"""부하와 발전의 시각 정렬 (요구사항서 3.1 라벨 규약, 11.2).

여기가 이 모듈에서 가장 조용히 틀리는 곳이다. 두 시계열의 시각 뜻이 다르다.

    부하   검침 라벨이 **구간 끝**이다. ``10:15`` 는 ``10:00~10:15`` 평균 수요.
    발전   pvlib 결과는 그 시각의 **순시** 출력이다.

그래서 부하 라벨 L 에 붙일 발전량은 구간 ``[L − Δ, L)`` 의 평균 출력이고,
그 대표값으로 **구간 중앙** ``L − Δ/2`` 의 순시 출력을 쓴다. 이 반 칸을 빠뜨리면
전체가 7.5분, 라벨을 그대로 쓰면 15분이 밀린다. 정오 최대 발전이 남중 시각에서
벗어나거나 일출 전 구간에 발전량이 생기는 식으로 드러난다.

시간별 시뮬 결과를 15분으로 옮기는 일도 여기서 한다. 선형 보간이며 기상
자료가 없는 구간은 0 으로 두고 몇 슬롯이 비었는지 센다.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from pvlib.location import Location

from kwise.pv.simulate import PvSimulation

__all__ = [
    "AlignedGeneration",
    "align_simulation",
    "align_to_load",
    "daylight_mask",
    "slot_midpoints",
]


@dataclass(frozen=True, eq=False)
class AlignedGeneration:
    """부하 라벨에 맞춰진 발전 시계열.

    Attributes:
        kw: 구간 평균 출력. 인덱스는 부하와 같은 tz-naive 구간 끝 라벨이다.
        interval_minutes: 구간 길이.
        uncovered_slots: 기상 자료가 없어 0 으로 채운 슬롯 수.
    """

    kw: pd.Series
    interval_minutes: int
    uncovered_slots: int = 0

    @property
    def slot_hours(self) -> float:
        return self.interval_minutes / 60.0

    @property
    def kwh(self) -> pd.Series:
        """구간별 발전량. 평균 출력 × 구간 시간이다."""
        return (self.kw * self.slot_hours).rename("pv_kwh")

    @property
    def total_kwh(self) -> float:
        return float(self.kwh.sum())

    @property
    def peak_kw(self) -> float:
        return float(self.kw.max()) if len(self.kw) else 0.0


def slot_midpoints(load_index: pd.DatetimeIndex, interval_minutes: int) -> pd.DatetimeIndex:
    """부하 라벨(구간 끝)에 대응하는 구간 중앙 시각.

    ``10:15`` 라벨의 구간은 ``10:00~10:15`` 이므로 중앙은 ``10:07:30`` 이다.
    """
    return pd.DatetimeIndex(load_index - pd.Timedelta(minutes=interval_minutes / 2))


def daylight_mask(
    load_index: pd.DatetimeIndex,
    interval_minutes: int,
    *,
    latitude: float,
    longitude: float,
    timezone: str,
    altitude_m: float = 0.0,
) -> pd.Series:
    """구간 중앙에서 해가 떠 있는 슬롯만 참인 마스크.

    시간별 값을 15분으로 선형 보간하면 일몰 직후 슬롯까지 발전량이 새어 나온다.
    일몰 전 마지막 시간값과 일몰 후 첫 시간값 사이를 직선으로 이었기 때문이다.
    태양 고도로 잘라내야 일출·일몰 시각과 대조가 성립한다.
    """
    midpoints = slot_midpoints(load_index, interval_minutes)
    localized = midpoints.tz_localize(timezone, ambiguous=True, nonexistent="shift_forward")
    location = Location(latitude=latitude, longitude=longitude, tz=timezone, altitude=altitude_m)
    elevation = location.get_solarposition(localized)["apparent_elevation"]
    return pd.Series(
        elevation.to_numpy() > 0.0, index=pd.DatetimeIndex(load_index), name="daylight"
    )


def align_to_load(
    ac_kw: pd.Series,
    load_index: pd.DatetimeIndex,
    interval_minutes: int,
    *,
    daylight: pd.Series | None = None,
) -> AlignedGeneration:
    """순시 출력 시계열을 부하 라벨에 맞춰 구간 평균 출력으로 바꾼다.

    Args:
        ac_kw: 시뮬레이션 결과. tz-aware(지방시) 또는 tz-naive 순시각 인덱스.
        load_index: 부하 라벨. tz-naive 구간 끝.
        interval_minutes: 부하 구간 길이.
        daylight: 낮 슬롯 마스크. 주면 밤 슬롯을 0 으로 만든다.

    Returns:
        :class:`AlignedGeneration`. 인덱스는 ``load_index`` 그대로다.
    """
    if interval_minutes <= 0:
        raise ValueError(f"구간 길이는 양수여야 합니다: {interval_minutes}")
    labels = pd.DatetimeIndex(load_index)
    if labels.tz is not None:
        raise ValueError("부하 인덱스는 tz-naive 여야 합니다. 로더가 그렇게 돌려줍니다.")

    source = ac_kw.sort_index()
    if isinstance(source.index, pd.DatetimeIndex) and source.index.tz is not None:
        # 지방시로 이미 계산했으므로 tz 만 떼면 벽시계 시각이 그대로 남는다.
        source = source.tz_localize(None)

    midpoints = slot_midpoints(labels, interval_minutes)
    combined = source.reindex(source.index.union(midpoints))
    interpolated = combined.interpolate(method="time", limit_area="inside")
    aligned = interpolated.reindex(midpoints)

    uncovered = int(aligned.isna().sum())
    values = aligned.to_numpy(dtype=float, copy=True)
    kw = pd.Series(values, index=labels, name="pv_kw").fillna(0.0)
    if daylight is not None:
        kw = kw.where(daylight.reindex(labels, fill_value=False).to_numpy(dtype=bool), 0.0)
    return AlignedGeneration(
        kw=kw,
        interval_minutes=interval_minutes,
        uncovered_slots=uncovered,
    )


def align_simulation(
    simulation: PvSimulation,
    load_index: pd.DatetimeIndex,
    interval_minutes: int,
    *,
    clip_to_daylight: bool = True,
) -> AlignedGeneration:
    """시뮬레이션 결과를 부하 라벨에 붙인다. 설비 위치로 밤 슬롯을 잘라낸다.

    부하와 발전을 함께 다루는 쪽(measures, diagnose)은 이 함수를 쓴다.
    """
    config = simulation.config
    mask = (
        daylight_mask(
            load_index,
            interval_minutes,
            latitude=config.latitude,
            longitude=config.longitude,
            timezone=config.timezone,
            altitude_m=config.altitude_m,
        )
        if clip_to_daylight
        else None
    )
    return align_to_load(simulation.ac_kw, load_index, interval_minutes, daylight=mask)
