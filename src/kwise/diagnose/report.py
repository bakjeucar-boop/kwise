"""진단 (요구사항서 6장).

**설비 정보 없이, 업로드와 계약 정보만으로 나오는 결과다.** PV·ESS 는 여기 들어오지
않는다 — 개선 수단은 5세션 measures 의 일이다. 계약 정보조차 없으면 부하 패턴과
피크 특성까지만 내고 금액은 비운다. 사용자가 파일만 올려도 결과가 나와야 한다.

선택요금 조합은 **순차 처리하고 요약(합계)만 남긴다.** 월별 명세를 들고 있는 것은
현행 조합 하나뿐이다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace

import pandas as pd

from kwise.diagnose.contract import (
    ContractAdequacy,
    ContractInfo,
    assess_contract,
)
from kwise.diagnose.dr import DateLike, DrProfile, dr_profile
from kwise.diagnose.peak import DEFAULT_TOP_N, PeakProfile, peak_profile
from kwise.diagnose.structure import ChargeStructure, charge_structure
from kwise.diagnose.summary import (
    ImprovementSummary,
    judge_pv_potential,
    pv_basis_label,
)
from kwise.io import HOURLY_INTERVAL_WARNING_ON_CONTRACT, UsageData

# **1단계가 2단계 판정을 받아 온다** (100세션). 묶음(``kwise.measures``)이 아니라
# 이 한 모듈만 들인다 — 묶음을 들이면 ``demand_response`` 를 지나 ``diagnose``
# 로 되돌아와 맞물린다.
from kwise.measures.contract import evaluate_contract_adjustment
from kwise.notices import Notice, block
from kwise.quality import (
    DEFAULT_OPERATING_HOURS,
    LoadPattern,
    QualityReport,
    check_quality,
    load_pattern,
    outage_slot_mask,
)
from kwise.quality.checks import HOURLY_INTERVAL_FACT
from kwise.tariff import (
    BillingOptions,
    TariffSelection,
    TariffTable,
    build_calendar,
    calculate_bill,
    classify_slots,
    default_demand_months,
    demand_eligible_mask,
    pending_option_notices,
    switchable_selections,
)
from kwise.tariff.engine import short_period_warning

__all__ = ["Diagnosis", "diagnose"]


@dataclass(frozen=True, eq=False)
class Diagnosis:
    """진단 결과 한 벌. UI 1단계가 이 객체 하나로 그려진다."""

    quality: QualityReport
    pattern: LoadPattern
    peak: PeakProfile
    summary: ImprovementSummary
    dr: DrProfile | None = None
    structure: ChargeStructure | None = None
    contract: ContractAdequacy | None = None
    option_totals: Mapping[str, float] = field(default_factory=dict)
    notices: tuple[Notice, ...] = field(default=())

    @property
    def has_charges(self) -> bool:
        """계약 정보가 있어 요금까지 산출했는지."""
        return self.structure is not None


def diagnose(
    usage: UsageData,
    table: TariffTable,
    contract: ContractInfo | None = None,
    *,
    quality: QualityReport | None = None,
    options: BillingOptions | None = None,
    top_n: int = DEFAULT_TOP_N,
    contract_floor_ratio: float | None = None,
    operating_hours: tuple[int, int] = DEFAULT_OPERATING_HOURS,
    dr_off_days: Iterable[DateLike] = (),
    jeju: bool = False,
) -> Diagnosis:
    """업로드와 계약 정보만으로 진단한다.

    Args:
        contract: 계약 정보. None 이면 요금 관련 항목을 비우고 부하·피크만 낸다.
        contract_floor_ratio: 요금적용전력의 계약전력 대비 하한 비율.
            None 이면 요금표의 종별 속성(일반용(을) 30%)을 쓴다 (요구사항서 5.2 ③).
        operating_hours: **건물** 운영 시간대 ``(시작, 끝)``. 운영시간 외 부하 진단과
            DR 저부하일 판정에 쓴다. 경제성DR 의 **시장 운영 시간대**는 제도
            규정이라 이 값과 무관하다 (:func:`~kwise.diagnose.dr.dr_market_windows`).
        dr_off_days: 사용자가 「쉬는 날」 로 지목한 날짜 (29세션). **DR 판정에만
            쓴다** — 요금 계산의 공휴일은 법정 공휴일이므로 건드리지 않는다.
            근로자의 날(2025년까지)은 한전 요금에서 평일이 맞다.
        jeju: 건물이 제주인가 — DR 입찰 창만 가른다 (S253 결정 2). 모르면 육지 창.
    """
    report = quality if quality is not None else check_quality(usage)
    interval = usage.meta.interval_minutes
    opts = options if options is not None else BillingOptions()

    # 요금적용전력은 중간·최대부하 시간대만 대상이다 (요구사항서 5.2 ①).
    contract_type = contract.selection.contract_type if contract else None
    type_rules = table.contract(contract_type) if contract_type else None
    # **계약전력 기준 건물은 1시간 자료라도 기본요금이 낮게 서지 않는다** (S269 결정 3).
    # 품질 검사는 종별을 모른다 — 계약 정보를 받은 이 자리에서 그 주의의 글자만 간다.
    # 화면 「데이터 품질」 · PPT 3장 · Excel 요약이 다 이 품질 결과를 읽는다.
    if (
        type_rules is not None
        and contract is not None
        and type_rules.base_fee_on_contract_at(contract.selection.voltage)
    ):
        report = replace(
            report,
            notices=tuple(
                replace(item, text=HOURLY_INTERVAL_WARNING_ON_CONTRACT)
                if item.fact == HOURLY_INTERVAL_FACT
                else item
                for item in report.notices
            ),
        )
    index = pd.DatetimeIndex(usage.kw.index)
    calendar = build_calendar(
        range(index[0].year - 1, index[-1].year + 2),
        sunday_is_holiday=opts.sunday_is_holiday,
        exclude_temporary=(
            table.day_rules.exclude_temporary_holiday
            if opts.exclude_temporary_holiday is None
            else opts.exclude_temporary_holiday
        ),
        extra_holidays=opts.extra_holidays,
        excluded_holidays=opts.excluded_holidays,
    )
    slots = classify_slots(
        index,
        interval,
        table,
        calendar,
        contract_type=contract_type,
        region_group=opts.region_group,
    )
    eligible = demand_eligible_mask(
        slots["band"],
        demand_bands=type_rules.demand_bands if type_rules else ("mid", "peak"),
    )
    peak = peak_profile(
        usage.kw,
        interval,
        top_n=top_n,
        prior_peaks=opts.prior_peaks,
        demand_eligible=eligible,
        demand_months=type_rules.demand_months if type_rules else default_demand_months(),
        contract_kw=contract.contract_kw if contract else None,
        contract_floor_ratio=type_rules.contract_floor_ratio if type_rules else None,
        # **계절 구분은 요금표에서 온다** (30세션 5절). 화면이 계절별 프로파일을
        # 나눠 그리는데, 여기서 달로 다시 나누면 요금표와 두 벌이 된다.
        seasons=slots["season"],
    )
    # 등급은 요금적용전력 대상 슬롯만 놓고 매긴다. 판정 모집단을 산출물에 적는다.
    potential, midday_share = judge_pv_potential(peak)
    pv_basis = pv_basis_label(peak)

    # 6.6 경제성DR 참여 여력. 거래일 판정은 요금 계량의 평일과 **다르다** —
    # DR 은 토·일·공휴일이 모두 제외다 (전력시장운영규칙 제12.4.2.1조 제1항 1호).
    dr = dr_profile(
        usage.kw,
        interval_minutes=interval,
        calendar=calendar,
        contract_type=contract_type,
        contract_kw=contract.contract_kw if contract else None,
        outage_mask=outage_slot_mask(index, report.outages),
        operating_hours=operating_hours,
        off_days=dr_off_days,
        jeju=jeju,
    )
    # **주말에도 가동하는 건물은 운영시간을 모든 날에 적용한다** (S272 결정 2). 판정은 DR
    # 진단의 그 갈래 하나다 — 주말을 통째로 「운영시간 외」 로 세면 24시간 공장의 주말 몫이
    # 그 이름으로 선다. 갈래가 안 서면 평일만 적용하던 그대로다.
    pattern = load_pattern(
        usage.kw,
        interval,
        operating_hours=operating_hours,
        operating_every_day=bool(dr.unassessed_reason),
    )

    if contract is None:
        # **12개월 미만 주의는 요금표를 아는 이 자리에서 완성한다** (S271 결정 1). 품질 검사는
        # 머리 문장만 낸다 — 기간과 계절 일수를 달고, 계약 정보가 없어 방향은 적지 않는다.
        report = _retext(
            report,
            SHORT_PERIOD_FACT,
            short_period_warning(
                slots["season"], interval, table, start=usage.meta.start, end=usage.meta.end
            ),
        )
        notices: list[Notice] = list(report.notices)
        summary = ImprovementSummary(
            current_selection=None,
            current_total_won=None,
            best_selection=None,
            best_total_won=None,
            tariff_switch_saving_won=None,
            contract_saving_won=None,
            pv_potential=potential,
            pv_midday_share=midday_share,
            pv_basis=pv_basis,
        )
        # **차단** — 계약 정보가 없으면 요금이 나오지 않는다.
        notices.append(
            block(
                "계약 정보가 없어 요금 구조와 절감액을 산출하지 않았습니다. "
                "계약종별·전압구분·선택요금·계약전력을 입력하면 나옵니다.",
                fact="diagnose.no_contract",
            )
        )
        return Diagnosis(
            quality=report,
            pattern=pattern,
            peak=peak,
            dr=dr,
            summary=summary,
            notices=tuple(notices),
        )

    current_bill = calculate_bill(usage, table, contract.selection, options=opts, quality=report)
    structure = charge_structure(usage, table, current_bill, options=opts)
    # 12개월 미만 주의는 청구 결과가 낸 글자 하나로 맞춘다 (S271 결정 1) — 화면 1단계가 읽는
    # 품질 쪽 안내와 PPT · Excel · Word 가 읽는 청구 쪽 안내가 한 글자다.
    billed = next(
        (item.text for item in current_bill.notices if item.fact == SHORT_PERIOD_FACT), ""
    )
    if billed:
        report = _retext(report, SHORT_PERIOD_FACT, billed)
    notices = list(report.notices)
    notices.extend(current_bill.notices)

    # 조합을 순차로 돌며 합계만 남긴다. 월별 명세는 현행 조합만 들고 있는다.
    # **현행 계약종별·전압구분 안에서만 비교한다.** 종별은 용도로, 전압은 수전설비로
    # 정해지므로 요금제 전환으로 바꿀 수 있는 것이 아니다 (요구사항서 7.1).
    totals: dict[str, float] = {str(contract.selection): current_bill.total_won}
    for selection in switchable_selections(table, contract.selection):
        key = str(selection)
        if key in totals:
            continue
        totals[key] = calculate_bill(
            usage, table, selection, options=opts, quality=report
        ).total_won

    best_key = min(totals, key=lambda key: totals[key])
    best_selection = _parse_selection(best_key)
    switch_saving = current_bill.total_won - totals[best_key]

    adequacy: ContractAdequacy | None = None
    if contract.contract_kw is not None:
        # **판정은 한 자리에서 한다** (100세션). 1단계가 하한만 보고 따로 세던
        # 것을 걷어내고 조정 쪽 판정을 받아 온다 — 그래야 2단계가 「299 kW 로
        # 낮춰라」 하는 판에서 1단계가 「적정합니다」 라고 적지 않는다.
        # **설비 정보는 여전히 안 묻는다** — 사용량·요금표·계약 정보뿐이고
        # 셋 다 이 자리에 이미 있다.
        adjustment = evaluate_contract_adjustment(
            usage,
            current_bill,
            contract_kw=contract.contract_kw,
            contract_floor_ratio=(
                contract_floor_ratio
                if contract_floor_ratio is not None
                else (type_rules.contract_floor_ratio if type_rules else None)
            ),
            table=table,
            options=opts,
        )
        adequacy = assess_contract(
            adjustment, billing_demand_kw=peak.billing_demand_before_floor_kw
        )
        notices.extend(adequacy.notices)
    else:
        notices.append(
            block(
                "계약전력을 입력하면 계약 적정성을 진단합니다.",
                fact="diagnose.no_contract_kw",
            )
        )

    # 현행과 권고 둘 다 산출물에 등장한다. 아직 못 쓰는 요금제면 오차를 알린다.
    notices.extend(
        pending_option_notices(
            table,
            (contract.selection, best_selection),
            period_start=current_bill.period_start.date(),
        )
    )

    summary = ImprovementSummary(
        current_selection=contract.selection,
        current_total_won=current_bill.total_won,
        best_selection=best_selection,
        best_total_won=totals[best_key],
        tariff_switch_saving_won=switch_saving,
        contract_saving_won=adequacy.saving_won if adequacy else None,
        pv_potential=potential,
        pv_midday_share=midday_share,
        pv_basis=pv_basis,
        period_label=current_bill.period_label,
    )

    return Diagnosis(
        quality=report,
        pattern=pattern,
        peak=peak,
        dr=dr,
        summary=summary,
        structure=structure,
        contract=adequacy,
        option_totals=totals,
        notices=tuple(notices),
    )


#: 12개월 미만 주의의 사실 ID — 품질 검사와 요금 엔진이 같은 ID 로 낸다.
SHORT_PERIOD_FACT = "quality.short_period"


def _retext(report: QualityReport, fact: str, text: str) -> QualityReport:
    """품질 결과에서 그 사실의 안내 글자만 간 사본 (S271 결정 1). 그 안내가 없으면 그대로다."""
    return replace(
        report,
        notices=tuple(
            replace(item, text=text) if item.fact == fact else item for item in report.notices
        ),
    )


def _parse_selection(key: str) -> TariffSelection:
    contract_type, voltage, option = key.split("/")
    return TariffSelection(contract_type=contract_type, voltage=voltage, option=option)
