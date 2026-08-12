"""대표일 고르기 (15세션 2절).

**역률·태양광·ESS 가 모두 「하루 15분 곡선」을 쓴다.** 카드마다 다른 날을 그리면
세 그림을 나란히 놓고도 견줄 수 없다. 기준을 여기 한 곳에서 정한다.

    연간 최대수요일  기본. 피크가 어떻게 생겼는지가 세 수단의 공통 관심사다
    여름 대표일      여름철 평일 중 일평균이 중앙값에 가장 가까운 날
    겨울 대표일      겨울철 평일 중 같은 방식
    사용자 지정일    직접 고른 날

**대표일은 평균이 아니라 실제 하루다.** 평균 하루를 그리면 피크가 뭉개져
"이 시각에 방전한다" 가 보이지 않는다. 중앙값에 가장 가까운 **실재하는 날**을
고르는 이유다.

날짜 귀속은 :func:`kwise.io.slot_start` 를 따른다 — 라벨 ``03-05 00:00`` 은
04일 23:45~24:00 이라 4일에 속한다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd

from kwise.io import UsageData, slot_start

__all__ = [
    "MAX_DEMAND_KEY",
    "RepresentativeDay",
    "day_profile",
    "find_day",
    "representative_days",
]

MAX_DEMAND_KEY = "peak"
"""기본 대표일의 키. 연간 최대수요가 난 날이다."""

_SUMMER_MONTHS = (6, 7, 8)
_WINTER_MONTHS = (12, 1, 2)


@dataclass(frozen=True)
class RepresentativeDay:
    """대표일 하나."""

    key: str
    label: str
    date: dt.date
    reason: str

    @property
    def title(self) -> str:
        """차트 제목에 그대로 붙인다. **어느 날인지 반드시 보인다.**"""
        return f"{self.label} — {self.date:%Y-%m-%d}"


def _daily_frame(usage: UsageData) -> pd.DataFrame:
    """슬롯을 날짜로 묶은 표. 귀속은 구간 시작 시각 기준이다."""
    observed = usage.kw.dropna()
    starts = slot_start(pd.DatetimeIndex(observed.index), usage.meta.interval_minutes)
    return pd.DataFrame(
        {
            "kw": observed.to_numpy(dtype=float),
            "day": starts.normalize(),
            "month": starts.month,
            "weekday": starts.weekday,
        }
    )


def _typical_day(frame: pd.DataFrame, months: tuple[int, ...]) -> dt.date | None:
    """계절 평일 중 **일평균이 중앙값에 가장 가까운 실재하는 날**."""
    season = frame[frame["month"].isin(months) & (frame["weekday"] < 5)]
    if season.empty:
        return None
    means = season.groupby("day")["kw"].mean()
    if means.empty:
        return None
    target = float(means.median())
    return pd.Timestamp((means - target).abs().idxmin()).date()


def representative_days(usage: UsageData) -> tuple[RepresentativeDay, ...]:
    """고를 수 있는 대표일. **없는 계절은 목록에서 뺀다.**

    기간이 반 년이면 겨울이 없을 수 있다. 없는 날을 선택지로 두면 고른 뒤에
    빈 차트가 나온다.
    """
    frame = _daily_frame(usage)
    if frame.empty:
        return ()

    peak_day = pd.Timestamp(frame.loc[frame["kw"].idxmax(), "day"]).date()
    days = [
        RepresentativeDay(
            key=MAX_DEMAND_KEY,
            label="연간 최대수요일",
            date=peak_day,
            reason="기간 중 15분 최대수요가 난 날입니다.",
        )
    ]
    for key, label, months in (
        ("summer", "여름 대표일", _SUMMER_MONTHS),
        ("winter", "겨울 대표일", _WINTER_MONTHS),
    ):
        picked = _typical_day(frame, months)
        if picked is not None:
            days.append(
                RepresentativeDay(
                    key=key,
                    label=label,
                    date=picked,
                    reason=f"{label[:2]}철 평일 중 일평균이 중앙값에 가장 가까운 날입니다.",
                )
            )
    return tuple(days)


def find_day(usage: UsageData, key: str, *, custom: dt.date | None = None) -> RepresentativeDay:
    """키로 대표일을 찾는다. ``custom`` 이면 사용자가 고른 날이다."""
    if key == "custom" and custom is not None:
        return RepresentativeDay(
            key="custom", label="사용자 지정일", date=custom, reason="직접 고른 날입니다."
        )
    days = representative_days(usage)
    if not days:
        raise ValueError("관측치가 없어 대표일을 고를 수 없습니다.")
    for item in days:
        if item.key == key:
            return item
    return days[0]


def day_profile(
    series: pd.Series,
    day: dt.date,
    interval_minutes: int,
    *,
    name: str = "kw",
) -> pd.DataFrame:
    """하루치 15분 곡선. ``시각`` 열은 **구간 시작 시각**이다.

    라벨(구간 끝)을 x축에 쓰면 자정 슬롯이 다음 날로 넘어가 하루가 어긋난다.
    """
    index = pd.DatetimeIndex(series.index)
    starts = slot_start(index, interval_minutes)
    mask = starts.normalize() == pd.Timestamp(day)
    picked = series[mask]
    # **숫자가 아닌 계열도 받는다** — 계시별 시간대 라벨을 같은 방식으로 자른다.
    values = (
        picked.to_numpy(dtype=float) if pd.api.types.is_numeric_dtype(picked) else picked.to_numpy()
    )
    return pd.DataFrame({"시각": starts[mask], name: values})
