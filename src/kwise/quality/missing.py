"""결측 분석 (요구사항서 4.1, 4.2).

두 가지 위험을 **모두** 산출한다.

편중
    결측이 평일 피크 시간대에 몰렸는지. 몰렸다면 관측된 최대수요가 실제보다 낮다.

연속 공백
    며칠짜리 공백은 모든 시간대가 균등하게 빠지므로 편중 배수가 1 근처가 되어
    음성으로 판정되지만, 그 달의 최대수요 판정 자체를 무의미하게 만든다.
    편중 배수만 보면 놓친다.

시각 귀속은 모두 :func:`kwise.io.slot_start` 를 거친다. 라벨이 구간 끝이라
``10:00`` 라벨은 09시대 사용량이다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from kwise.io import slot_start

__all__ = [
    "DEFAULT_PEAK_HOURS",
    "LONG_GAP_DAYS",
    "MONTHLY_MISSING_THRESHOLD",
    "PEAK_SKEW_THRESHOLD",
    "MissingGap",
    "MonthlyMissing",
    "PeakHourSkew",
    "find_missing_gaps",
    "longest_gap",
    "monthly_longest_gaps",
    "monthly_missing",
    "peak_hour_skew",
]

# 평일 10~16시. 최대수요가 이 구간에서 나는 건물이 많다.
DEFAULT_PEAK_HOURS: tuple[int, int] = (10, 16)
PEAK_SKEW_THRESHOLD = 1.5
MONTHLY_MISSING_THRESHOLD = 0.05
LONG_GAP_DAYS = 1.0


@dataclass(frozen=True)
class MissingGap:
    """연속 결측 구간. ``start``·``end`` 는 결측 슬롯의 라벨이다."""

    start: pd.Timestamp
    end: pd.Timestamp
    slots: int
    days: float

    @property
    def is_long(self) -> bool:
        """1일을 넘으면 그 달의 최대수요 판정이 흔들린다."""
        return self.days > LONG_GAP_DAYS


@dataclass(frozen=True)
class MonthlyMissing:
    """월별 결측률. 5% 초과 월은 최대수요를 '신뢰 제한' 으로 표시한다."""

    month: pd.Period
    expected_slots: int
    missing_slots: int
    ratio: float
    flagged: bool


@dataclass(frozen=True)
class PeakHourSkew:
    """결측의 피크 시간대 편중.

    ``multiple`` 이 1.5 를 넘으면 최대수요 과소평가 위험으로 본다.
    정전 슬롯을 뺀 값이 정본이고, 포함한 값은 비교용이다.
    """

    peak_hours: tuple[int, int]
    peak_missing: int
    peak_expected: int
    peak_ratio: float
    overall_missing: int
    overall_expected: int
    overall_ratio: float
    multiple: float
    threshold: float
    flagged: bool
    excluded_slots: int

    @property
    def label(self) -> str:
        return "최대수요 과소평가 위험" if self.flagged else "편중 없음"


def find_missing_gaps(kw: pd.Series, interval_minutes: int) -> tuple[MissingGap, ...]:
    """연속 결측 구간을 앞에서부터 찾는다."""
    flags = kw.isna()
    if not bool(flags.any()):
        return ()
    blocks = (flags != flags.shift()).cumsum()
    slot_days = interval_minutes / 1440.0
    gaps: list[MissingGap] = []
    for _, block in flags[flags].groupby(blocks[flags]):
        index = block.index
        slots = len(index)
        gaps.append(
            MissingGap(
                start=pd.Timestamp(index[0]),
                end=pd.Timestamp(index[-1]),
                slots=slots,
                days=slots * slot_days,
            )
        )
    return tuple(gaps)


def longest_gap(gaps: tuple[MissingGap, ...]) -> MissingGap | None:
    """가장 긴 연속 결측 구간. 없으면 None."""
    if not gaps:
        return None
    return max(gaps, key=lambda gap: gap.slots)


def monthly_longest_gaps(
    gaps: tuple[MissingGap, ...], interval_minutes: int
) -> dict[pd.Period, MissingGap]:
    """월별 최장 연속 결측 구간.

    **월 귀속은 구간 시작 시각으로 판정한다** — :func:`monthly_missing` 과 같은
    규약이다. 라벨이 구간 끝이라 ``02-01 00:00`` 은 1월의 마지막 구간이며, 라벨로
    나누면 그 구간만 2월로 넘어가 두 표의 달이 어긋난다.

    구간이 달을 넘어가면 **시작한 달에 센다.** 쪼개면 "최장 연속" 이라는 말이
    뜻을 잃는다 — 9일짜리 하나가 4일과 5일 둘로 보인다.
    """
    if not gaps:
        return {}
    starts = slot_start(pd.DatetimeIndex([gap.start for gap in gaps]), interval_minutes)
    longest: dict[pd.Period, MissingGap] = {}
    for gap, start in zip(gaps, starts, strict=True):
        month = pd.Period(start, freq="M")
        current = longest.get(month)
        if current is None or gap.slots > current.slots:
            longest[month] = gap
    return longest


def monthly_missing(
    kw: pd.Series,
    interval_minutes: int,
    *,
    threshold: float = MONTHLY_MISSING_THRESHOLD,
) -> tuple[MonthlyMissing, ...]:
    """월별 결측률을 개별 산출한다. 월 귀속은 구간 시작 시각으로 판정한다."""
    starts = slot_start(pd.DatetimeIndex(kw.index), interval_minutes)
    frame = pd.DataFrame(
        {"month": starts.to_period("M"), "missing": kw.isna().to_numpy()},
    )
    grouped = frame.groupby("month", observed=True)["missing"].agg(["size", "sum"])
    result: list[MonthlyMissing] = []
    for month, row in grouped.iterrows():
        expected = int(row["size"])
        missing = int(row["sum"])
        ratio = missing / expected if expected else 0.0
        result.append(
            MonthlyMissing(
                month=month,
                expected_slots=expected,
                missing_slots=missing,
                ratio=ratio,
                flagged=ratio > threshold,
            )
        )
    return tuple(result)


def peak_hour_skew(
    kw: pd.Series,
    interval_minutes: int,
    *,
    excluded: pd.Series | None = None,
    peak_hours: tuple[int, int] = DEFAULT_PEAK_HOURS,
    threshold: float = PEAK_SKEW_THRESHOLD,
) -> PeakHourSkew:
    """결측이 평일 피크 시간대에 몰렸는지 본다.

    Args:
        excluded: 분자·분모 **양쪽에서** 뺄 슬롯 마스크. 정전 슬롯을 넘긴다.
            한쪽만 빼면 비율이 왜곡된다 (요구사항서 4.1).
    """
    index = pd.DatetimeIndex(kw.index)
    starts = slot_start(index, interval_minutes)
    keep = pd.Series(True, index=index) if excluded is None else ~excluded.astype(bool)
    keep = keep.reindex(index, fill_value=True)

    in_peak = pd.Series(
        (starts.weekday < 5) & (starts.hour >= peak_hours[0]) & (starts.hour < peak_hours[1]),
        index=index,
    )
    missing = kw.isna()

    overall_expected = int(keep.sum())
    overall_missing = int((missing & keep).sum())
    peak_expected = int((in_peak & keep).sum())
    peak_missing = int((missing & in_peak & keep).sum())

    overall_ratio = overall_missing / overall_expected if overall_expected else 0.0
    peak_ratio = peak_missing / peak_expected if peak_expected else 0.0
    if overall_ratio > 0:
        multiple = peak_ratio / overall_ratio
    else:
        multiple = math.inf if peak_ratio > 0 else 0.0

    return PeakHourSkew(
        peak_hours=peak_hours,
        peak_missing=peak_missing,
        peak_expected=peak_expected,
        peak_ratio=peak_ratio,
        overall_missing=overall_missing,
        overall_expected=overall_expected,
        overall_ratio=overall_ratio,
        multiple=multiple,
        threshold=threshold,
        flagged=multiple > threshold,
        excluded_slots=int(len(index) - overall_expected),
    )
