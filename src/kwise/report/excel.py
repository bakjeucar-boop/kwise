"""Excel 출력 (요구사항서 10.3).

시트 구성은 요약 / 진단 / 월별 집계 / 15분 시계열 / 요금 계산 명세 / 수단별 결과 /
조합 비교 / 감도 여덟 장이다.

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

from kwise.compare import ComparisonResult
from kwise.diagnose import Diagnosis
from kwise.io import UsageData
from kwise.measures import (
    Certainty,
    ContractAdjustment,
    EssResult,
    SolarPoint,
    TariffSwitchResult,
)
from kwise.report.notices import (
    CONTRACT_CHANGE_WARNING,
    KNOWN_LIMITS,
    NOT_INCLUDED_NOTICE,
    UNPRICED_REASONS,
    format_won,
)
from kwise.tariff import BillingResult

__all__ = [
    "DEFAULT_OUTPUT_DIR",
    "NO_PV_SENSITIVITY_NOTE",
    "SHEET_ORDER",
    "ReportSections",
    "ReportWriteError",
    "build_sheets",
    "export_report",
    "measure_summary_frame",
    "no_pv_sensitivity_frame",
    "result_path",
    "strip_timezone",
    "write_workbook",
]

NO_PV_SENSITIVITY_NOTE = (
    "태양광이 없어 감도를 적용할 항목이 없습니다. 감도 계수는 PV 출력에만 적용하며, "
    "요금제 전환과 계약전력 조정은 확정 계산이라 감도를 쓰지 않습니다 (요구사항서 9.2)."
)

DEFAULT_OUTPUT_DIR = Path("output")
SHEET_ORDER: tuple[str, ...] = (
    "요약",
    "진단",
    "월별 집계",
    "15분 시계열",
    "요금 계산 명세",
    "수단별 결과",
    "조합 비교",
    "감도",
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
    include_timeseries: bool = True


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

    rows.append(("요금", "기본요금", f"{bill.total_base_won:,.0f} 원"))
    rows.append(("요금", "전력량요금", f"{bill.total_energy_won:,.0f} 원"))
    rows.append(("요금", "합계 (관측 기준)", f"{bill.total_won:,.0f} 원"))
    rows.append(("요금", "합계 (결측 보정 기준)", f"{bill.total_won_adjusted:,.0f} 원"))

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

    rows.append(("미포함 요금요소", "안내", NOT_INCLUDED_NOTICE))  # 5.1
    rows.append(("계약전력 변경 경고", "필수 안내", CONTRACT_CHANGE_WARNING))  # 9.4
    for number, limit in enumerate(KNOWN_LIMITS, start=1):  # 부록 D
        rows.append(("알려진 한계", f"{number}", limit))

    for note in bill.notes:
        rows.append(("계산 방침", "", note))
    for warning in bill.warnings:
        rows.append(("경고", "요금", warning))
    if diagnosis is not None:
        for warning in diagnosis.warnings:
            rows.append(("경고", "품질·진단", warning))
    if sections.comparison is not None:
        for warning in sections.comparison.warnings:
            rows.append(("경고", "조합", warning))
        for note in sections.comparison.notes:
            rows.append(("계산 방침", "조합", note))
    return rows


def measure_summary_frame(
    *,
    switch: TariffSwitchResult | None = None,
    contract: ContractAdjustment | None = None,
    ess: EssResult | None = None,
    solar: SolarPoint | None = None,
) -> pd.DataFrame:
    """수단별 결과 시트 (요구사항서 7장).

    **금액을 내지 못한 항목은 빈칸으로 두지 않고 사유를 적는다.**
    """
    rows: list[dict[str, object]] = []
    if switch is not None:
        rows.append(
            {
                "수단": f"선택요금 전환 ({switch.current.selection} → {switch.best.selection})",
                "투자비(원)": 0.0,
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
                "투자비(원)": 0.0,
                "절감액(원)": format_won(contract.saving_won, reason=UNPRICED_REASONS["contract"]),
                "12개월 환산(원)": format_won(
                    contract.annual_saving_won, reason=UNPRICED_REASONS["contract"]
                ),
                "회수기간": "즉시" if contract.saving_won else "—",
                "확실성": str(contract.certainty),
                "비고": f"하향 여지 {contract.reduction_kw:,.0f} kW. {contract.saving_basis}",
            }
        )
    if solar is not None:
        rows.append(
            {
                "수단": f"태양광 {solar.capacity_kwp:,.0f} kWp",
                "투자비(원)": solar.investment_won,
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
                "투자비(원)": ess.investment_won,
                "절감액(원)": format_won(ess.total_saving_won),
                "12개월 환산(원)": format_won(ess.annual_saving_won),
                "회수기간": (
                    f"{ess.payback_years:.1f}년"
                    if ess.payback_years is not None
                    else UNPRICED_REASONS["no_saving"]
                ),
                "확실성": str(ess.certainty),
                "비고": (
                    f"손익분기 단가 "
                    f"{format_won(ess.breakeven_unit_cost_won_per_kwh)} 원/kWh "
                    f"(회수 {ess.payback_target_years:.0f}년 기준). "
                    f"용량은 하루 최대 초과 에너지 "
                    f"{ess.excess.max_daily_excess_kwh:,.1f} kWh 기준"
                ),
            }
        )
    return pd.DataFrame(rows).set_index("수단")


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
        ("상위 100구간 주말 건수", f"{peak.weekend_slots}"),
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

    hours = peak.hour_counts[peak.hour_counts > 0]
    extra = pd.DataFrame(
        [(f"상위 100구간 {hour}시", f"{count}건") for hour, count in hours.items()],
        columns=["항목", "값"],
    ).set_index("항목")
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
    sheets["월별 집계"] = monthly[
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
    ]
    if sections.include_timeseries:
        sheets["15분 시계열"] = _timeseries_frame(sections.usage)
    sheets["요금 계산 명세"] = monthly[
        [
            "billing_demand_kw",
            "base_fee_factor",
            "base_won",
            "energy_won",
            "energy_won_adjusted",
            "total_won",
            "total_won_adjusted",
            "discount_won",
            "demand_confidence",
        ]
    ]
    if sections.measure_rows is not None:
        sheets["수단별 결과"] = sections.measure_rows
    if sections.comparison is not None:
        sheets["조합 비교"] = sections.comparison.frame()
    if sections.sensitivity is not None:
        sheets["감도"] = sections.sensitivity
    return {name: sheets[name] for name in SHEET_ORDER if name in sheets}


def export_report(
    sections: ReportSections,
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    prefix: str = "result",
    now: dt.datetime | None = None,
) -> Path:
    """시트를 만들어 ``output\\result_YYYYMMDD_HHMM.xlsx`` 로 저장한다."""
    return write_workbook(build_sheets(sections), result_path(output_dir, prefix=prefix, now=now))
