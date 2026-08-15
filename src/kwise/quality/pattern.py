"""부하 패턴 진단 (요구사항서 6.1).

설비 정보 없이 kW 시계열만으로 나오는 지표다. 순수 함수이며 Streamlit 을
import 하지 않는다. 4세션의 diagnose 모듈이 이 함수를 호출한다.

| 지표 | 의미 |
|---|---|
| 부하율 | 평균 ÷ 최대 |
| 기저부하 비율 | 야간 평균 ÷ 주간 평균 |
| 주말 부하 비율 | 주말 평균 ÷ 평일 평균 |
| 운영시간 외 부하 | 운영시간 밖 사용량의 비중 |

기저부하가 과다하면 상시 가동 설비 점검 여지를 시사한다.

시각·요일 귀속은 :func:`kwise.io.slot_start` 로 판정한다. 결측 슬롯은 계산에서
빠지며 보간하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from kwise.io import slot_start

__all__ = [
    "DEFAULT_NIGHT_HOURS",
    "DEFAULT_OPERATING_HOURS",
    "LoadPattern",
    "load_pattern",
]

# 경부하 시간대와 맞춘다 (22:00~08:00).
DEFAULT_NIGHT_HOURS: tuple[int, int] = (22, 8)
DEFAULT_OPERATING_HOURS: tuple[int, int] = (9, 18)


@dataclass(frozen=True)
class LoadPattern:
    """부하 패턴 지표. 비율은 분모가 0 이면 None 이다."""

    observed_slots: int
    mean_kw: float
    max_kw: float
    min_kw: float
    load_factor: float | None

    night_hours: tuple[int, int]
    night_mean_kw: float | None
    day_mean_kw: float | None
    base_load_ratio: float | None

    weekday_mean_kw: float | None
    weekend_mean_kw: float | None
    weekend_ratio: float | None

    operating_hours: tuple[int, int]
    operating_mean_kw: float | None
    off_hours_mean_kw: float | None
    off_hours_ratio: float | None
    off_hours_energy_share: float | None


def _mean(series: pd.Series) -> float | None:
    return float(series.mean()) if len(series) else None


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or not denominator:
        return None
    return numerator / denominator


def load_pattern(
    kw: pd.Series,
    interval_minutes: int,
    *,
    night_hours: tuple[int, int] = DEFAULT_NIGHT_HOURS,
    operating_hours: tuple[int, int] = DEFAULT_OPERATING_HOURS,
) -> LoadPattern:
    """부하 패턴 지표를 산출한다.

    Args:
        kw: 15분(또는 1시간) 평균 수요. 결측은 NaN 인 채로 넘긴다.
        interval_minutes: 검침 간격. 라벨을 구간 시작으로 되돌리는 데 쓴다.
        night_hours: 야간 구간 ``(시작, 끝)``. 자정을 넘는 구간을 허용한다.
        operating_hours: 운영시간 ``(시작, 끝)``. 평일 이 시간대 밖이 운영시간 외다.

    Returns:
        :class:`LoadPattern`. 관측치가 없으면 ValueError.
    """
    observed = kw.dropna()
    if observed.empty:
        raise ValueError("관측된 수요가 없어 부하 패턴을 산출할 수 없습니다.")

    starts = slot_start(pd.DatetimeIndex(observed.index), interval_minutes)
    hour = pd.Series(starts.hour, index=observed.index)
    weekday = pd.Series(starts.weekday, index=observed.index)

    night_start, night_end = night_hours
    if night_start > night_end:  # 22시~익일 8시처럼 자정을 넘는 구간
        is_night = (hour >= night_start) | (hour < night_end)
    else:
        is_night = (hour >= night_start) & (hour < night_end)
    is_weekend = weekday >= 5

    open_start, open_end = operating_hours
    is_operating = (hour >= open_start) & (hour < open_end) & ~is_weekend

    mean_kw = float(observed.mean())
    max_kw = float(observed.max())
    night_mean = _mean(observed[is_night])
    day_mean = _mean(observed[~is_night])
    weekday_mean = _mean(observed[~is_weekend])
    weekend_mean = _mean(observed[is_weekend])
    operating_mean = _mean(observed[is_operating])
    off_hours_mean = _mean(observed[~is_operating])

    slot_hours = interval_minutes / 60.0
    total_energy = float(observed.sum()) * slot_hours
    off_hours_energy = float(observed[~is_operating].sum()) * slot_hours

    return LoadPattern(
        observed_slots=len(observed),
        mean_kw=mean_kw,
        max_kw=max_kw,
        min_kw=float(observed.min()),
        load_factor=_ratio(mean_kw, max_kw),
        night_hours=night_hours,
        night_mean_kw=night_mean,
        day_mean_kw=day_mean,
        base_load_ratio=_ratio(night_mean, day_mean),
        weekday_mean_kw=weekday_mean,
        weekend_mean_kw=weekend_mean,
        weekend_ratio=_ratio(weekend_mean, weekday_mean),
        operating_hours=operating_hours,
        operating_mean_kw=operating_mean,
        off_hours_mean_kw=off_hours_mean,
        off_hours_ratio=_ratio(off_hours_mean, operating_mean),
        off_hours_energy_share=_ratio(off_hours_energy, total_energy),
    )
