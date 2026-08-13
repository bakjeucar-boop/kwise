"""품질 검사 집계 (요구사항서 4.1).

업로드 직후 요약을 표시한다. **조용히 넘어가지 않는다.**
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from kwise.io import UsageData
from kwise.notices import Notice, basis, warn
from kwise.quality.missing import (
    DEFAULT_PEAK_HOURS,
    MONTHLY_MISSING_THRESHOLD,
    PEAK_SKEW_THRESHOLD,
    MissingGap,
    MonthlyMissing,
    PeakHourSkew,
    find_missing_gaps,
    longest_gap,
    monthly_missing,
    peak_hour_skew,
)
from kwise.quality.outage import OutageEvent, detect_outages, outage_slot_mask

__all__ = [
    "DEFAULT_LOW_LOAD_KW",
    "MISSING_RATIO_THRESHOLD",
    "IntervalConsistency",
    "OutlierSummary",
    "QualityReport",
    "check_quality",
]

MISSING_RATIO_THRESHOLD = 0.03
DEFAULT_LOW_LOAD_KW = 100.0
_SPIKE_RATIO = 0.30  # 최대수요 대비 인접 슬롯 변화폭


@dataclass(frozen=True)
class OutlierSummary:
    """이상치 (요구사항서 4.1). 값을 고치지 않고 건수만 알린다."""

    zero_kw_slots: int
    low_load_kw: float
    low_load_slots: tuple[pd.Timestamp, ...]
    contract_kw: float | None
    over_contract_slots: int
    over_contract_max_kw: float | None
    spike_threshold_kw: float
    spike_slots: int

    @property
    def low_load_count(self) -> int:
        return len(self.low_load_slots)


@dataclass(frozen=True)
class IntervalConsistency:
    """간격 일관성과 처리 방침 (요구사항서 4.1, 4.3)."""

    interval_minutes: int
    grid_phase_seconds: int
    duplicate_rows: int
    partial_metering_rows: int
    partial_metering_kwh: float
    invalid_datetime_rows: int
    invalid_energy_rows: int
    negative_energy_rows: int

    @property
    def uniform(self) -> bool:
        return self.duplicate_rows == 0 and self.partial_metering_rows == 0


@dataclass(frozen=True)
class QualityReport:
    """품질 검사 결과. UI·Excel 요약과 2차 계산의 입력이 된다."""

    source_name: str
    start: pd.Timestamp
    end: pd.Timestamp
    period_days: float
    interval_minutes: int

    expected_slots: int
    observed_slots: int
    missing_slots: int
    missing_ratio: float

    gaps: tuple[MissingGap, ...]
    longest_gap: MissingGap | None
    monthly: tuple[MonthlyMissing, ...]

    outages: tuple[OutageEvent, ...]
    skew: PeakHourSkew
    skew_including_outages: PeakHourSkew

    outliers: OutlierSummary
    consistency: IntervalConsistency

    notices: tuple[Notice, ...] = field(default=())

    @property
    def flagged_months(self) -> tuple[MonthlyMissing, ...]:
        """결측률이 임계를 넘은 월. 최대수요를 '신뢰 제한' 으로 표시한다."""
        return tuple(month for month in self.monthly if month.flagged)

    @property
    def has_full_year(self) -> bool:
        return self.period_days >= 365.0


def _outliers(
    usage: UsageData,
    *,
    contract_kw: float | None,
    low_load_kw: float,
    spike_threshold_kw: float | None,
) -> OutlierSummary:
    observed = usage.kw.dropna()
    low = observed[observed < low_load_kw]
    threshold = (
        spike_threshold_kw
        if spike_threshold_kw is not None
        else usage.meta.max_demand_kw * _SPIKE_RATIO
    )
    # 결측을 사이에 두고 벌어진 차이는 급변이 아니다. 관측된 이웃끼리만 본다.
    jumps = observed.diff().abs()
    over_contract = observed[observed > contract_kw] if contract_kw else observed.iloc[:0]

    return OutlierSummary(
        zero_kw_slots=int((observed == 0).sum()),
        low_load_kw=low_load_kw,
        low_load_slots=tuple(pd.Timestamp(stamp) for stamp in low.index),
        contract_kw=contract_kw,
        over_contract_slots=len(over_contract),
        over_contract_max_kw=float(over_contract.max()) if len(over_contract) else None,
        spike_threshold_kw=threshold,
        spike_slots=int((jumps > threshold).sum()),
    )


def check_quality(
    usage: UsageData,
    *,
    contract_kw: float | None = None,
    low_load_kw: float = DEFAULT_LOW_LOAD_KW,
    spike_threshold_kw: float | None = None,
    peak_hours: tuple[int, int] = DEFAULT_PEAK_HOURS,
    skew_threshold: float = PEAK_SKEW_THRESHOLD,
    monthly_threshold: float = MONTHLY_MISSING_THRESHOLD,
    outage_low_load_kw: float | None = None,
    min_outage_evidence: int = 2,
) -> QualityReport:
    """업로드 직후의 품질 검사 (요구사항서 4.1).

    편중 판정은 정전 슬롯을 분자·분모 양쪽에서 뺀 값이 정본이고,
    포함한 값은 ``skew_including_outages`` 로 병기한다.
    """
    meta = usage.meta
    interval = meta.interval_minutes
    index = pd.DatetimeIndex(usage.kw.index)

    gaps = find_missing_gaps(usage.kw, interval)
    outages = detect_outages(
        usage,
        gaps,
        low_load_kw=outage_low_load_kw,
        min_evidence=min_outage_evidence,
    )
    outage_mask = outage_slot_mask(index, outages)

    skew = peak_hour_skew(
        usage.kw,
        interval,
        excluded=outage_mask,
        peak_hours=peak_hours,
        threshold=skew_threshold,
    )
    skew_all = peak_hour_skew(
        usage.kw,
        interval,
        peak_hours=peak_hours,
        threshold=skew_threshold,
    )

    monthly = monthly_missing(usage.kw, interval, threshold=monthly_threshold)
    outliers = _outliers(
        usage,
        contract_kw=contract_kw,
        low_load_kw=low_load_kw,
        spike_threshold_kw=spike_threshold_kw,
    )
    consistency = IntervalConsistency(
        interval_minutes=interval,
        grid_phase_seconds=meta.grid_phase_seconds,
        duplicate_rows=meta.duplicate_rows,
        partial_metering_rows=meta.off_grid_rows,
        partial_metering_kwh=meta.off_grid_kwh,
        invalid_datetime_rows=meta.invalid_datetime_rows,
        invalid_energy_rows=meta.invalid_energy_rows,
        negative_energy_rows=meta.negative_energy_rows,
    )
    report = QualityReport(
        source_name=meta.source_name,
        start=meta.start,
        end=meta.end,
        period_days=meta.period_days,
        interval_minutes=interval,
        expected_slots=meta.expected_rows,
        observed_slots=meta.valid_rows,
        missing_slots=meta.missing_rows,
        missing_ratio=meta.missing_ratio,
        gaps=gaps,
        longest_gap=longest_gap(gaps),
        monthly=monthly,
        outages=outages,
        skew=skew,
        skew_including_outages=skew_all,
        outliers=outliers,
        consistency=consistency,
    )
    return _with_warnings(report)


def _with_warnings(report: QualityReport) -> QualityReport:
    """경고 문구를 붙인 사본을 만든다."""
    messages: list[Notice] = []

    if not report.has_full_year:
        # **주의** — 연간 환산 결과의 신뢰도가 달라진다.
        messages.append(
            warn(
                f"기간이 {report.period_days:.0f}일로 12개월 미만입니다. "
                "연간 환산 결과에 경고를 붙여야 합니다."
            )
        )
    if report.missing_ratio > MISSING_RATIO_THRESHOLD:
        messages.append(
            warn(
                f"결측률 {report.missing_ratio:.1%} — 임계 {MISSING_RATIO_THRESHOLD:.0%} 초과. "
                "보간하지 않으며 결측 구간은 계산에서 제외합니다."
            )
        )
    if report.skew.flagged:
        messages.append(
            warn(
                f"최대수요 과소평가 위험 — 평일 "
                f"{report.skew.peak_hours[0]}~{report.skew.peak_hours[1]}시 결측률이 "
                f"전체의 {report.skew.multiple:.2f}배입니다."
            )
        )
    gap = report.longest_gap
    if gap is not None and gap.is_long:
        messages.append(
            warn(
                f"최장 연속 결측 {gap.days:.2f}일 ({gap.slots:,}슬롯, {gap.start} ~ {gap.end}). "
                "이 구간이 든 달은 최대수요 판정 자체가 무의미할 수 있습니다."
            )
        )
    for month in report.flagged_months:
        messages.append(
            warn(
                f"{month.month} 결측률 {month.ratio:.1%} — 최대수요를 '신뢰 제한' 으로 표시합니다."
            )
        )
    # 아래는 전부 **근거**다 — 무엇을 어떻게 처리했는지 적는 관측 기록이며,
    # 결과 해석을 바꾸지는 않는다 (18세션 인벤토리의 6·7번).
    for outage in report.outages:
        messages.append(
            basis(
                f"정전 추정 {outage.start} ~ {outage.end} "
                f"({outage.duration_hours:.2f}시간, 흔적 {outage.decisive_evidence}종). "
                "편중 판정에서 제외했습니다."
            )
        )
    outliers = report.outliers
    if outliers.zero_kw_slots:
        messages.append(basis(f"0 kW 구간 {outliers.zero_kw_slots:,}건."))
    if outliers.low_load_count:
        messages.append(
            basis(
                f"{outliers.low_load_kw:,.0f} kW 미만 구간 {outliers.low_load_count:,}건 "
                f"(첫 시각 {outliers.low_load_slots[0]})."
            )
        )
    if outliers.over_contract_slots:
        # **주의** — 초과사용부가금 대상이다.
        messages.append(
            warn(
                f"계약전력 {outliers.contract_kw:,.0f} kW 초과 {outliers.over_contract_slots:,}건, "
                f"최대 {outliers.over_contract_max_kw:,.1f} kW."
            )
        )
    if outliers.spike_slots:
        messages.append(
            basis(
                f"인접 슬롯 급변 {outliers.spike_slots:,}건 "
                f"(임계 {outliers.spike_threshold_kw:,.0f} kW)."
            )
        )
    consistency = report.consistency
    if consistency.partial_metering_rows:
        messages.append(
            basis(
                f"부분 계량 {consistency.partial_metering_rows}건 "
                f"({consistency.partial_metering_kwh:,.2f} kWh) — '결측' 이 아니라 "
                "별도 분류입니다. kW 산정에서 빼고 kWh 합계에는 넣었습니다."
            )
        )
    if consistency.duplicate_rows:
        messages.append(basis(f"중복 시각 {consistency.duplicate_rows:,}건을 합산했습니다."))

    return QualityReport(
        source_name=report.source_name,
        start=report.start,
        end=report.end,
        period_days=report.period_days,
        interval_minutes=report.interval_minutes,
        expected_slots=report.expected_slots,
        observed_slots=report.observed_slots,
        missing_slots=report.missing_slots,
        missing_ratio=report.missing_ratio,
        gaps=report.gaps,
        longest_gap=report.longest_gap,
        monthly=report.monthly,
        outages=report.outages,
        skew=report.skew,
        skew_including_outages=report.skew_including_outages,
        outliers=report.outliers,
        consistency=report.consistency,
        notices=tuple(messages),
    )
