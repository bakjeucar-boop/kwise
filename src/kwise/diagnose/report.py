"""진단 (요구사항서 6장).

**설비 정보 없이, 업로드와 계약 정보만으로 나오는 결과다.** PV·ESS 는 여기 들어오지
않는다 — 개선 수단은 5세션 measures 의 일이다. 계약 정보조차 없으면 부하 패턴과
피크 특성까지만 내고 금액은 비운다. 사용자가 파일만 올려도 결과가 나와야 한다.

선택요금 조합은 **순차 처리하고 요약(합계)만 남긴다.** 월별 명세를 들고 있는 것은
현행 조합 하나뿐이다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

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
    build_lines,
    judge_pv_potential,
    pv_basis_label,
)
from kwise.io import UsageData
from kwise.notices import Notice, block
from kwise.quality import (
    DEFAULT_OPERATING_HOURS,
    LoadPattern,
    QualityReport,
    check_quality,
    load_pattern,
    outage_slot_mask,
)
from kwise.tariff import (
    BillingOptions,
    TariffSelection,
    TariffTable,
    build_calendar,
    calculate_bill,
    classify_slots,
    default_demand_months,
    demand_eligible_mask,
    switchable_selections,
)

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
    margin_ratio: float | None = None,
    contract_floor_ratio: float | None = None,
    operating_hours: tuple[int, int] = DEFAULT_OPERATING_HOURS,
    dr_off_days: Iterable[DateLike] = (),
) -> Diagnosis:
    """업로드와 계약 정보만으로 진단한다.

    Args:
        contract: 계약 정보. None 이면 요금 관련 항목을 비우고 부하·피크만 낸다.
        margin_ratio: 권장 계약전력에 얹을 여유율. None 이면 판단값을 읽는다.
        contract_floor_ratio: 요금적용전력의 계약전력 대비 하한 비율.
            None 이면 요금표의 종별 속성(일반용(을) 30%)을 쓴다 (요구사항서 5.2 ③).
        operating_hours: **건물** 운영 시간대 ``(시작, 끝)``. 운영시간 외 부하 진단과
            DR 저부하일 판정에 쓴다. 경제성DR 의 **시장 운영 시간대**는 제도
            규정이라 이 값과 무관하다 (:func:`~kwise.diagnose.dr.dr_market_windows`).
        dr_off_days: 사용자가 「쉬는 날」 로 지목한 날짜 (29세션). **DR 판정에만
            쓴다** — 요금 계산의 공휴일은 법정 공휴일이므로 건드리지 않는다.
            근로자의 날(2025년까지)은 한전 요금에서 평일이 맞다.
    """
    report = quality if quality is not None else check_quality(usage)
    interval = usage.meta.interval_minutes
    opts = options if options is not None else BillingOptions()

    pattern = load_pattern(usage.kw, interval, operating_hours=operating_hours)

    # 요금적용전력은 중간·최대부하 시간대만 대상이다 (요구사항서 5.2 ①).
    contract_type = contract.selection.contract_type if contract else None
    type_rules = table.contract(contract_type) if contract_type else None
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
        interval,
        calendar,
        contract_type=contract_type,
        contract_kw=contract.contract_kw if contract else None,
        outage_mask=outage_slot_mask(index, report.outages),
        operating_hours=operating_hours,
        off_days=dr_off_days,
    )

    notices: list[Notice] = list(report.notices)

    if contract is None:
        summary = ImprovementSummary(
            current_selection=None,
            current_total_won=None,
            best_selection=None,
            best_total_won=None,
            tariff_switch_saving_won=None,
            contract_saving_won=None,
            contract_reduction_kw=None,
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
            summary=summary.__class__(**{**summary.__dict__, "lines": build_lines(summary)}),
            notices=tuple(notices),
        )

    current_bill = calculate_bill(usage, table, contract.selection, options=opts, quality=report)
    structure = charge_structure(usage, table, current_bill, options=opts)
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
        adequacy = assess_contract(
            usage.kw,
            contract_kw=contract.contract_kw,
            billing_demand_kw=peak.billing_demand_before_floor_kw,
            base_rate_won_per_kw=current_bill.base_rate_won_per_kw,
            base_fee_months=current_bill.base_fee_months,
            margin_ratio=margin_ratio,
            contract_floor_ratio=(
                contract_floor_ratio
                if contract_floor_ratio is not None
                else (type_rules.contract_floor_ratio if type_rules else None)
            ),
        )
        notices.extend(adequacy.notices)
    else:
        notices.append(
            block(
                "계약전력을 입력하면 계약 적정성을 진단합니다.",
                fact="diagnose.no_contract_kw",
            )
        )

    summary = ImprovementSummary(
        current_selection=contract.selection,
        current_total_won=current_bill.total_won,
        best_selection=best_selection,
        best_total_won=totals[best_key],
        tariff_switch_saving_won=switch_saving,
        contract_saving_won=adequacy.saving_won if adequacy else None,
        contract_reduction_kw=adequacy.reduction_kw if adequacy else None,
        pv_potential=potential,
        pv_midday_share=midday_share,
        pv_basis=pv_basis,
        period_label=current_bill.period_label,
    )
    summary = ImprovementSummary(**{**summary.__dict__, "lines": build_lines(summary)})

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


def _parse_selection(key: str) -> TariffSelection:
    contract_type, voltage, option = key.split("/")
    return TariffSelection(contract_type=contract_type, voltage=voltage, option=option)
