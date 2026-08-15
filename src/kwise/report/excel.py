"""Excel 출력 (요구사항서 10.3).

시트 구성은 요약 / 진단 / 월별 집계 / 15분 시계열 / 요금 계산 명세 / 수단별 결과 /
조합 비교 / 감도 / 감도 상세 아홉 장이다.

**저장 직전 tz-aware 컬럼을 반드시 해제한다.** pvlib 결과는 항상 tz-aware 이고,
openpyxl 은 tz 가 붙은 시각을 쓰지 못해 ValueError 를 낸다.

**파일명에 날짜·시각 접미사를 붙인다.** Excel 이 파일을 열고 있으면 덮어쓰기가
실패하기 때문이다. 그래도 실패하면 "Excel 에서 파일을 닫아 주세요" 를 안내한다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from kwise import money
from kwise.compare import (
    SCENARIO_NAME_CAVEAT,
    SENSITIVITY_NOTE,
    ComparisonResult,
    sensitivity_range_frame,
)
from kwise.diagnose import Diagnosis
from kwise.io import UsageData
from kwise.measures import (
    DR_ADVISORY,
    Certainty,
    ContractAdjustment,
    DemandResponseResult,
    EssResult,
    PowerFactorResult,
    SolarCurve,
    SolarPoint,
    TariffSwitchResult,
)
from kwise.notices import Notice, dedupe
from kwise.report.appendix import basis_data_frame, known_limits, worksheet_frame
from kwise.report.columns import localize
from kwise.report.notices import (
    CONTRACT_CHANGE_WARNING,
    DATA_SOURCES,
    KNOWN_LIMITS,
    NOT_INCLUDED_NOTICE,
    TRUNCATION_FOOTNOTE,
    UNPRICED_REASONS,
    format_won,
)
from kwise.report.worksheet import Worksheet
from kwise.tariff import BillingResult, TariffTable

__all__ = [
    "DEFAULT_OUTPUT_DIR",
    "MEASURE_SHEET_COLUMNS",
    "NO_PV_SENSITIVITY_NOTE",
    "SHEET_ORDER",
    "ReportSections",
    "ReportWriteError",
    "build_sheets",
    "export_report",
    "measure_summary_frame",
    "no_pv_sensitivity_frame",
    "result_path",
    "solar_curve_sheet",
    "strip_timezone",
    "truncate_money_columns",
    "write_workbook",
]

NO_PV_SENSITIVITY_NOTE = (
    "태양광이 없어 감도를 적용할 항목이 없습니다. 감도는 PV 출력의 첨예도에만 적용하며, "
    "요금제 전환·계약전력 조정·역률 개선은 확정 계산이라 감도를 쓰지 않습니다."
)

# 「수단별 결과」 시트의 열. 켠 수단이 없어도 이 구조는 유지한다.
MEASURE_SHEET_COLUMNS: tuple[str, ...] = (
    "수단",
    "투자비(원)",
    "절감액(원)",
    "12개월 환산(원)",
    "회수기간",
    "확실성",
    "비고",
)

DEFAULT_OUTPUT_DIR = Path("output")
SHEET_ORDER: tuple[str, ...] = (
    "요약",
    "진단",
    "월별 집계",
    "15분 시계열",
    "요금 계산 명세",
    "수단별 결과",
    "태양광 용량 곡선",
    "조합 비교",
    "감도",
    "감도 상세",
    # **부록 셋** (22세션 3절). Word 와 같은 것을 싣는다 — 만드는 자리도 하나다.
    "부록 A 산출 근거",
    "부록 B 기준 데이터",
    "부록 C 한계와 전제",
)
_CLOSE_EXCEL = "Excel 에서 파일을 닫아 주세요."


class ReportWriteError(RuntimeError):
    """Excel 파일을 쓰지 못했을 때 발생한다."""


def strip_timezone(frame: pd.DataFrame) -> pd.DataFrame:
    """tz-aware 컬럼과 인덱스의 tz 를 해제한다. Excel 은 tz 를 쓰지 못한다."""
    result = frame.copy()
    for column in result.columns:
        if isinstance(result[column].dtype, pd.DatetimeTZDtype):
            result[column] = result[column].dt.tz_localize(None)
    index = result.index
    if isinstance(index, pd.DatetimeIndex) and index.tz is not None:
        result.index = index.tz_localize(None)
    if isinstance(index, pd.PeriodIndex):
        result.index = index.astype(str)
    return result


def result_path(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    prefix: str = "result",
    now: dt.datetime | None = None,
) -> Path:
    """``result_YYYYMMDD_HHMM.xlsx``. 접미사가 없으면 덮어쓰기 충돌이 난다."""
    stamp = (now if now is not None else dt.datetime.now()).strftime("%Y%m%d_%H%M")
    return output_dir / f"{prefix}_{stamp}.xlsx"


def write_workbook(sheets: dict[str, pd.DataFrame], path: Path) -> Path:
    """시트를 하나의 통합문서로 쓴다. 시트마다 tz 를 해제한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            for name, frame in sheets.items():
                strip_timezone(frame).to_excel(writer, sheet_name=name[:31])
    except PermissionError as exc:
        raise ReportWriteError(
            f"'{path}' 에 쓰지 못했습니다. {_CLOSE_EXCEL} "
            "파일명에 날짜·시각 접미사가 붙으므로 닫은 뒤 다시 실행하면 됩니다."
        ) from exc
    except OSError as exc:
        raise ReportWriteError(f"'{path}' 저장에 실패했습니다: {exc}") from exc
    return path


# --------------------------------------------------------------------- 시트


@dataclass(frozen=True, eq=False)
class ReportSections:
    """산출물에 담을 조각. 없는 것은 비워 둔다."""

    usage: UsageData
    bill: BillingResult
    diagnosis: Diagnosis | None = None
    comparison: ComparisonResult | None = None
    sensitivity: pd.DataFrame | None = None
    measure_rows: pd.DataFrame | None = None
    solar_curve: SolarCurve | None = None
    """태양광 용량 곡선. **20단계 상세는 화면이 아니라 여기로 보낸다** (15세션 1-3)."""
    include_timeseries: bool = True
    worksheets: tuple[Worksheet, ...] = ()
    """계산 근거 표 (22세션 2절). 화면 카드가 접어 둔 것과 **같은 표**다."""
    tariff_table: TariffTable | None = None
    ess_cases: pd.DataFrame | None = None
    measure_notices: tuple[tuple[Notice, ...], ...] = ()
    """수단이 낸 안내 원본. 부록 C 가 참고 등급을 골라 쓴다."""


def _summary_rows(sections: ReportSections) -> list[tuple[str, str, str]]:
    bill = sections.bill
    rows: list[tuple[str, str, str]] = []

    for line in bill.traceability():  # 요구사항서 5.8
        label, _, value = line.partition(": ")
        rows.append(("적용 근거", label, value or line))

    usage = sections.usage
    rows.extend(
        [
            ("데이터", "원본 파일", usage.meta.source_name),
            ("데이터", "기간", bill.period_label),
            (
                "데이터",
                "총 사용량",
                f"{usage.total_kwh / 1000:,.1f} MWh (그리드 이탈 "
                f"{usage.meta.off_grid_kwh:,.2f} kWh 포함)",
            ),
            ("데이터", "최대수요", f"{usage.meta.max_demand_kw:,.1f} kW"),
            (
                "데이터",
                "결측",
                f"{usage.meta.missing_rows:,}슬롯 ({usage.meta.missing_ratio:.1%}) — 미보간",
            ),
        ]
    )

    rows.append(("요금", "기본요금", f"{format_won(bill.total_base_won)} 원"))
    charge = bill.power_factor
    rows.append(
        (
            "요금",
            "역률요금",
            f"{format_won(bill.total_power_factor_won)} 원 "
            f"({'감액' if charge.is_rebate else '추가'}, 기본요금의 "
            f"{charge.total_ratio:+.1%}, 주간 지상 {charge.lagging_pct:.1f}%)",
        )
    )
    rows.append(("요금", "전력량요금", f"{format_won(bill.total_energy_won)} 원"))
    rows.append(("요금", "합계 (관측 기준)", f"{format_won(bill.total_won)} 원"))
    rows.append(("요금", "합계 (결측 보정 기준)", f"{format_won(bill.total_won_adjusted)} 원"))
    # 항목을 각각 절사하므로 항목 합과 합계 표시가 어긋날 수 있다 (14세션).
    rows.append(("요금", "표기 안내", TRUNCATION_FOOTNOTE))

    diagnosis = sections.diagnosis
    if diagnosis is not None:
        summary = diagnosis.summary
        switch_won = format_won(summary.tariff_switch_saving_won, reason="미산출 — 계약 정보 없음")
        rows.append(("개선 여지", "선택요금 전환", f"{switch_won} 원 (투자 불필요)"))
        rows.append(
            (
                "개선 여지",
                "계약전력 조정",
                format_won(summary.contract_saving_won, reason=UNPRICED_REASONS["contract"]),
            )
        )
        rows.append(
            (
                "개선 여지",
                "태양광 피크 기여",
                f"{summary.pv_potential} (상위 구간의 {summary.pv_midday_share:.0%}가 정오 시간대)",
            )
        )
        if summary.pv_basis:
            # 어느 모집단으로 판정했는지 밝힌다. 부록 B 원값과 섞이지 않게 한다.
            rows.append(("개선 여지", "태양광 판정 모집단", summary.pv_basis))

    rows.append(("미포함 요금요소", "안내", NOT_INCLUDED_NOTICE))  # 5.1
    rows.append(("계약전력 변경 경고", "필수 안내", CONTRACT_CHANGE_WARNING))  # 9.4
    for number, limit in enumerate(KNOWN_LIMITS, start=1):  # 부록 D
        rows.append(("알려진 한계", f"{number}", limit))
    for source in DATA_SOURCES:  # 출처 표기 (7.5)
        rows.append(("데이터 출처", "", source))
    if sections.sensitivity is not None:  # 9.2
        rows.append(("감도", "방식", SENSITIVITY_NOTE))
        rows.append(("감도", "이름 주의", SCENARIO_NAME_CAVEAT))

    # **등급을 열에 적는다** (19세션 1절). 화면에서 뺀 근거·참고가 여기 남는다 —
    # 어느 등급이라 화면에 없었는지까지 보여야 사용자가 찾을 수 있다.
    groups = [("요금", bill.notices)]
    if diagnosis is not None:
        groups.append(("품질·진단", diagnosis.notices))
    if sections.comparison is not None:
        groups.append(("조합", sections.comparison.notices))
    for label, notices in groups:
        for item in dedupe(notices):
            rows.append((f"안내 · {item.severity}", label, item.text))
    return rows


def measure_summary_frame(
    *,
    switch: TariffSwitchResult | None = None,
    contract: ContractAdjustment | None = None,
    demand_response: DemandResponseResult | None = None,
    power_factor: PowerFactorResult | None = None,
    ess: EssResult | None = None,
    solar: SolarPoint | None = None,
) -> pd.DataFrame:
    """수단별 결과 시트 (요구사항서 7장).

    **7장 번호 순(7.1~7.7)으로 배치한다** — 선택요금 → 계약전력 → 경제성DR →
    역률 → 태양광 → ESS.

    각 행은 **독립 평가**다 (14세션 2절). 현행 요금제·현행 사용량을 기준선으로
    "이 수단만 도입하면 얼마" 를 낸 값이며, 조합 상호작용은 3단계 합산효과에서만
    다룬다.

    **금액을 내지 못한 항목은 빈칸으로 두지 않고 사유를 적는다.**
    """
    rows: list[dict[str, object]] = []
    if switch is not None:
        rows.append(
            {
                "수단": f"선택요금 전환 ({switch.current.selection} → {switch.best.selection})",
                "투자비(원)": format_won(0.0),
                "절감액(원)": format_won(switch.saving_won),
                "12개월 환산(원)": format_won(switch.annual_saving_won),
                "회수기간": "즉시",
                "확실성": str(switch.certainty),
                "비고": "설비 도입과 무관합니다. 감도를 적용하지 않습니다.",
            }
        )
    if contract is not None:
        rows.append(
            {
                "수단": (
                    f"계약전력 조정 ({contract.contract_kw:,.0f} → "
                    f"{contract.suggested_contract_kw:,.0f} kW)"
                ),
                "투자비(원)": format_won(0.0),
                "절감액(원)": format_won(contract.saving_won, reason=UNPRICED_REASONS["contract"]),
                "12개월 환산(원)": format_won(
                    contract.annual_saving_won, reason=UNPRICED_REASONS["contract"]
                ),
                "회수기간": "즉시" if contract.saving_won else "—",
                "확실성": str(contract.certainty),
                "비고": f"하향 여지 {contract.reduction_kw:,.0f} kW. {contract.saving_basis}",
            }
        )
    if demand_response is not None:
        rows.append(
            {
                "수단": f"경제성DR (등록 {demand_response.registered_capacity_kw:,.0f} kW)",
                "투자비(원)": format_won(0.0),
                "절감액(원)": demand_response.settlement_label,
                "12개월 환산(원)": demand_response.settlement_label,
                "회수기간": "즉시" if demand_response.is_priced else UNPRICED_REASONS["no_saving"],
                "확실성": str(demand_response.certainty),
                "비고": (
                    f"거래 가능일 {demand_response.eligible_days}일 중 저부하 평일 "
                    f"{demand_response.low_load_days}일. 연간 감축 가능량 "
                    f"{demand_response.annual_reducible_kwh:,.0f} kWh "
                    f"= Σ(저부하일별 감축 여력 × 참여 가능 시간, 합 "
                    f"{demand_response.participation_hours:,.0f}시간, 하루 상한 "
                    f"{demand_response.daily_hours_cap:,.0f}시간). "
                    f"{demand_response.participation_notice} "
                    "투자비는 0원이지만 감축 미달 시 실적위약금이 있습니다 "
                    "(전력시장운영규칙 별표26). " + DR_ADVISORY
                ),
            }
        )
    if power_factor is not None:
        rows.append(
            {
                "수단": (
                    f"역률 개선 ({power_factor.current_pct:.1f} → {power_factor.target_pct:.1f}%)"
                ),
                "투자비(원)": format_won(power_factor.investment_won),
                "절감액(원)": format_won(power_factor.saving_won),
                "12개월 환산(원)": format_won(power_factor.annual_saving_won),
                "회수기간": (
                    "즉시"
                    if power_factor.payback_years == 0
                    else f"{power_factor.payback_years:.1f}년"
                    if power_factor.payback_years is not None
                    else UNPRICED_REASONS["no_saving"]
                ),
                "확실성": str(power_factor.certainty),
                "비고": (
                    f"주간(08~22시) 지상역률 기준 92%, 매 1%당 기본요금의 0.2% "
                    f"(한전 기본공급약관 제43조). 현재 역률요금 "
                    f"{format_won(power_factor.current_charge_won)} 원 → "
                    f"{format_won(power_factor.target_charge_won)} 원. "
                    "야간 진상 95% 조항에 걸리지 않도록 시간대별 투입을 제어하십시오."
                ),
            }
        )
    if solar is not None:
        rows.append(
            {
                "수단": f"태양광 {solar.capacity_kwp:,.0f} kWp",
                "투자비(원)": format_won(solar.investment_won, reason=UNPRICED_REASONS["pv_price"]),
                "절감액(원)": format_won(solar.total_saving_won),
                "12개월 환산(원)": format_won(solar.annual_saving_won),
                "회수기간": (
                    f"{solar.payback_years:.1f}년"
                    if solar.payback_years is not None
                    else UNPRICED_REASONS["no_saving"]
                ),
                "확실성": str(Certainty.MEDIUM),
                "비고": (
                    f"자가소비율 {solar.self_consumption_ratio:.0%}, "
                    f"도입 후 역률 {solar.power_factor_after_pct:.1f}%"
                    if solar.self_consumption_ratio is not None
                    else "발전량 0"
                ),
            }
        )
    if ess is not None:
        rows.append(
            {
                "수단": (
                    f"ESS 목표 {ess.excess.target_kw:,.0f} kW "
                    f"({ess.power_kw:,.0f} kW / {ess.capacity_kwh:,.0f} kWh)"
                ),
                "투자비(원)": format_won(ess.investment_won),
                "절감액(원)": format_won(ess.total_saving_won),
                "12개월 환산(원)": format_won(ess.annual_saving_won),
                "회수기간": (
                    f"{ess.payback_years:.1f}년"
                    if ess.payback_years is not None
                    else UNPRICED_REASONS["no_saving"]
                ),
                "확실성": str(ess.certainty),
                "비고": (
                    f"방전시간 {ess.discharge_hours:.2f}h ({ess.c_rate:.1f}C, 산출값) · "
                    f"손익분기 단가 "
                    f"{format_won(ess.breakeven_unit_cost_won_per_kw)} 원/kW "
                    f"(회수 {ess.payback_target_years:.0f}년 기준) · "
                    + (
                        f"{ess.outlook_label} 단가 기준 {ess.outlook_payback_years:,.1f}년"
                        if ess.outlook_payback_years is not None
                        else "전망 단가 회수기간 미산출"
                    )
                ),
            }
        )
        if ess.arbitrage is not None:
            # 차익거래는 **별도 줄**이다. 피크저감 절감액에 더하면 이중 계산이 된다.
            arbitrage = ess.arbitrage
            rows.append(
                {
                    "수단": "└ 차익거래 잠재 (경부하 충전 → 최대부하 방전)",
                    "투자비(원)": format_won(None, reason="—"),
                    "절감액(원)": UNPRICED_REASONS["arbitrage_not_summed"],
                    "12개월 환산(원)": format_won(arbitrage.annual_won),
                    "회수기간": (
                        f"단독 {arbitrage.standalone_payback_years:,.1f}년"
                        if arbitrage.standalone_payback_years is not None
                        else UNPRICED_REASONS["no_saving"]
                    ),
                    "확실성": str(ess.certainty),
                    "비고": (
                        f"연 {arbitrage.won_per_kwh_year:,.0f} 원/kWh · "
                        f"평일 {arbitrage.cycles_per_day:g} 사이클 · 계시별 단가는 요금표에서 "
                        "가져왔습니다. "
                        + (
                            "배터리 수명(10~15년)을 넘어 단독으로는 성립하지 않습니다."
                            if arbitrage.outlives_battery
                            else "배터리 수명 안에 들어옵니다."
                        )
                    ),
                }
            )
    if not rows:
        # 켠 수단이 하나도 없을 수 있다 (진단만 보는 경우). **열 구조는 유지한다** —
        # 빈 DataFrame 에 set_index 를 걸면 KeyError 로 산출물 생성이 통째로 멈춘다.
        return pd.DataFrame(columns=MEASURE_SHEET_COLUMNS).set_index("수단")
    return pd.DataFrame(rows).set_index("수단")


def solar_curve_sheet(curve: SolarCurve) -> pd.DataFrame:
    """태양광 20단계 상세 (15세션 1-3).

    화면은 **한 줄 판정**만 내고 이 표를 여기로 보낸다. 최적 지점에 표식을 남겨
    화면의 판정과 대조할 수 있게 한다.
    """
    verdict = curve.verdict()
    best = verdict.best.capacity_kwp if verdict.best is not None else None
    rows = [
        {
            "용량(kWp)": point.capacity_kwp,
            "발전량(kWh)": point.generation_kwh,
            "자가소비(kWh)": point.self_consumed_kwh,
            "잉여(kWh)": point.surplus_kwh,
            "자가소비율": point.self_consumption_ratio,
            "요금적용전력(kW)": point.billing_demand_kw,
            "기본요금 절감(원)": point.base_saving_won,
            "전력량요금 절감(원)": point.energy_saving_won,
            "총 절감액(원)": point.total_saving_won,
            "12개월 환산(원)": point.annual_saving_won,
            "투자비(원)": point.investment_won,
            "회수기간(년)": point.payback_years,
            "도입 후 역률(%)": point.power_factor_after_pct,
            "판정": (
                "◀ " + verdict.basis + " 최적"
                if best is not None and abs(point.capacity_kwp - best) < 1e-9
                else ""
            ),
        }
        for point in curve.points
    ]
    return pd.DataFrame(rows).set_index("용량(kWp)")


def no_pv_sensitivity_frame() -> pd.DataFrame:
    """태양광이 없는 케이스의 감도 시트. 빈 시트 대신 사유를 적는다."""
    return pd.DataFrame([{"시나리오": "—", "내용": NO_PV_SENSITIVITY_NOTE}]).set_index("시나리오")


def _diagnosis_frame(diagnosis: Diagnosis) -> pd.DataFrame:
    pattern = diagnosis.pattern
    peak = diagnosis.peak
    rows: list[tuple[str, str]] = [
        ("부하율", f"{pattern.load_factor:.1%}" if pattern.load_factor else "—"),
        ("평균 수요", f"{pattern.mean_kw:,.1f} kW"),
        ("최대 수요", f"{pattern.max_kw:,.1f} kW"),
        (
            "기저부하 비율 (야간÷주간)",
            f"{pattern.base_load_ratio:.1%}" if pattern.base_load_ratio else "—",
        ),
        (
            "주말 부하 비율",
            f"{pattern.weekend_ratio:.1%}" if pattern.weekend_ratio else "—",
        ),
        (
            "무인시간 부하 비율",
            f"{pattern.unattended_ratio:.1%}" if pattern.unattended_ratio else "—",
        ),
        ("요금적용전력", f"{peak.billing_demand_kw:,.1f} kW"),
        (f"상위 {peak.top_n}구간 주말 건수 (전 슬롯)", f"{peak.weekend_slots}"),
        (
            f"상위 {peak.top_n}구간 주말 건수 (요금적용전력 대상)",
            f"{peak.demand_weekend_slots}",
        ),
        ("관측 슬롯", f"{peak.observed_slots:,}"),
        ("요금적용전력 대상 슬롯", f"{peak.demand_eligible_slots:,}"),
    ]
    if diagnosis.contract is not None:
        contract = diagnosis.contract
        rows.extend(
            [
                ("계약전력", f"{contract.contract_kw:,.0f} kW"),
                ("계약 대비 여유율", f"{contract.utilization:.1%}"),
                ("하향 여지", f"{contract.reduction_kw:,.0f} kW"),
                (
                    "계약전력 조정 절감액",
                    format_won(contract.saving_won, reason=UNPRICED_REASONS["contract"]),
                ),
            ]
        )
    if diagnosis.dr is not None:  # 6.6 경제성DR
        dr = diagnosis.dr
        rows.extend(
            [
                ("DR 거래 가능일", f"{dr.eligible_days}일 / 전체 {dr.total_days}일"),
                ("DR 제외일 (토·일·공휴일)", f"{dr.excluded_days}일"),
                (
                    "DR 등록 권장 용량 (저부하일 여력 하위값)",
                    f"{dr.registered_capacity_kw:,.0f} kW",
                ),
                ("DR 평균 기준 여력", f"{dr.mean_reducible_kw:,.0f} kW"),
                ("DR 운영 시간대", dr.window_label),
                (
                    "DR 저부하 판정 기준선 (주말·공휴일 평균 × 배수)",
                    f"{dr.weekend_baseline_kw:,.0f} kW × {dr.low_load_multiple:.2g} = "
                    f"{dr.low_load_threshold_kw:,.0f} kW"
                    if dr.weekend_baseline_kw is not None and dr.low_load_threshold_kw is not None
                    else "산출 보류 — 주말·공휴일 관측치 없음",
                ),
                ("DR 저부하 평일", f"{dr.low_load_days_count}일"),
                (
                    f"DR 연간 감축 가능량 (참여 {dr.total_participation_hours:,.0f}시간, "
                    f"하루 상한 {dr.daily_hours_cap:,.0f}시간)",
                    f"{dr.annual_reducible_kwh:,.0f} kWh",
                ),
                ("DR 참여 안내", dr.notice),
                ("DR 자원 유형", ", ".join(str(item) for item in dr.resource_types)),
                ("DR 적합성", str(dr.potential)),
            ]
        )
    if diagnosis.structure is not None:
        structure = diagnosis.structure
        rows.extend(
            [
                ("기본요금 비중", f"{structure.base_share:.1%}"),
                ("전력량요금 비중", f"{structure.energy_share:.1%}"),
                *(
                    (f"{band} 사용량 비중", f"{share:.1%}")
                    for band, share in structure.band_share.items()
                ),
                *(
                    (f"{season} 사용량 비중", f"{share:.1%}")
                    for season, share in structure.season_share.items()
                ),
            ]
        )
    frame = pd.DataFrame(rows, columns=["항목", "값"]).set_index("항목")

    # 시각 분포는 두 모집단을 나란히 싣되 라벨로 구분한다.
    # 전 슬롯 값이 부록 B 의 원값이고, 대상 슬롯 값이 태양광 등급의 근거다.
    distribution: list[tuple[str, str]] = [
        (f"상위 {peak.top_n}구간 {hour}시 (전 슬롯)", f"{count}건")
        for hour, count in peak.hour_counts.items()
        if count > 0
    ]
    distribution.extend(
        (f"상위 {peak.top_n}구간 {hour}시 (요금적용전력 대상)", f"{count}건")
        for hour, count in peak.demand_hour_counts.items()
        if count > 0
    )
    extra = pd.DataFrame(distribution, columns=["항목", "값"]).set_index("항목")
    return pd.concat([frame, extra])


def _timeseries_frame(usage: UsageData) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "kw": usage.kw,
            "kwh": usage.energy_kwh(),
            "결측": usage.kw.isna(),
        }
    )


def build_sheets(sections: ReportSections) -> dict[str, pd.DataFrame]:
    """시트 사전을 만든다. 순서는 :data:`SHEET_ORDER` 를 따른다."""
    summary = pd.DataFrame(_summary_rows(sections), columns=["구분", "항목", "내용"]).set_index(
        "구분"
    )

    monthly = sections.bill.monthly.copy()
    monthly.index = monthly.index.astype(str)

    sheets: dict[str, pd.DataFrame] = {"요약": summary}
    if sections.diagnosis is not None:
        sheets["진단"] = _diagnosis_frame(sections.diagnosis)
    sheets["월별 집계"] = localize(
        monthly[
            [
                "season",
                "covered_days",
                "is_partial",
                "max_demand_kw",
                "billing_demand_kw",
                "light_kwh",
                "mid_kwh",
                "peak_kwh",
                "total_kwh",
                "missing_ratio",
                "demand_confidence",
            ]
        ],
        index_name="월",
    )
    if sections.include_timeseries:
        sheets["15분 시계열"] = _timeseries_frame(sections.usage)
    # **화면에서 뺀 중간값이 여기 있다** (21세션 3-2). 월별 명세 화면은 결론
    # 하나(요금적용전력)만 내고, 그 값이 어떻게 나왔는지는 이 시트가 맡는다.
    sheets["요금 계산 명세"] = localize(
        monthly[
            [
                "days_in_month",
                "max_demand_at",
                "demand_basis_kw",
                "demand_before_floor_kw",
                "billing_demand_kw",
                "base_demand_kw",
                "base_fee_factor",
                "base_won",
                "power_factor_won",
                "energy_won",
                "energy_won_adjusted",
                "total_won",
                "total_won_adjusted",
                "discount_won",
                "demand_confidence",
            ]
        ],
        index_name="월",
    )
    if sections.measure_rows is not None:
        sheets["수단별 결과"] = sections.measure_rows
    if sections.solar_curve is not None:
        # **20단계 상세를 여기 싣는다** (15세션 1-3). 화면은 한 줄 판정만 낸다 —
        # 곡선이 단조롭게 좋아지기만 하면 표가 아무것도 알려주지 않기 때문이다.
        sheets["태양광 용량 곡선"] = solar_curve_sheet(sections.solar_curve)
    if sections.comparison is not None:
        sheets["조합 비교"] = sections.comparison.frame()
    # **부록 셋** — Word 와 같은 재료를 쓴다 (22세션 3절).
    if sections.worksheets:
        sheets["부록 A 산출 근거"] = worksheet_frame(sections.worksheets)
    sheets["부록 B 기준 데이터"] = basis_data_frame(sections.tariff_table)
    groups: list[tuple[Notice, ...]] = [sections.bill.notices, *sections.measure_notices]
    if sections.diagnosis is not None:
        groups.append(sections.diagnosis.notices)
    if sections.comparison is not None:
        groups.append(sections.comparison.notices)
    sheets["부록 C 한계와 전제"] = pd.DataFrame({"항목": list(known_limits(*groups))})
    if sections.sensitivity is not None:
        # **범위로 보여 준다.** 3열 나열은 근거표(감도 상세)로 내린다 (9.2).
        if "첨예도 s" in sections.sensitivity.columns:
            sheets["감도"] = sensitivity_range_frame(sections.sensitivity)
            sheets["감도 상세"] = sections.sensitivity
        else:
            sheets["감도"] = sections.sensitivity
    return {name: truncate_money_columns(sheets[name]) for name in SHEET_ORDER if name in sheets}


def truncate_money_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """``(원)`` 으로 끝나는 수치 열을 **천 원 단위로 절사한다** (14세션 1절).

    **내보낼 때만 자른다.** 계산 프레임은 원 단위 그대로다 — 절사한 값으로
    계산하면 합계가 어긋나고, 회귀 시험이 물고 있는 값이 흔들린다.
    """
    money_columns = [
        name
        for name in frame.columns
        if str(name).endswith("(원)") and pd.api.types.is_numeric_dtype(frame[name])
    ]
    if not money_columns:
        return frame
    trimmed = frame.copy()
    for name in money_columns:
        trimmed[name] = trimmed[name].map(
            lambda value: value if pd.isna(value) else money.truncate_won(float(value))
        )
    return trimmed


def export_report(
    sections: ReportSections,
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    prefix: str = "result",
    now: dt.datetime | None = None,
) -> Path:
    """시트를 만들어 ``output\\result_YYYYMMDD_HHMM.xlsx`` 로 저장한다."""
    return write_workbook(build_sheets(sections), result_path(output_dir, prefix=prefix, now=now))
