"""태양광 발전량 시뮬레이션 (요구사항서 7.4).

pvlib 기반. 어레이마다 :func:`pvlib.bifacial.infinite_sheds.get_irradiance` 로
경사면 일사를 구한다. 어레이 상호 음영은 **GCR 하나로** 표현한다 — 모델이
높이/열간격 비율에만 의존하므로 pitch 를 1 로 두고 ``height_to_pitch`` 를 높이로 넘긴다.

**시각** — 시간별 기상값이 대표하는 순시각(라벨 + 30분)에서 태양 위치를 계산한다.
결과 시리즈의 인덱스가 그 순시각이며, 부하 라벨에 붙이는 일은
:mod:`kwise.pv.alignment` 가 맡는다. 여기서 15분이 밀리면 피크 기여가 통째로 어긋난다.

메모리는 어레이 단위로만 쓴다. 용량 곡선·감도는 순차 처리한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
import pvlib
from pvlib.bifacial import infinite_sheds
from pvlib.location import Location
from pvlib.temperature import TEMPERATURE_MODEL_PARAMETERS

from kwise.pv.config import ArrayConfig, PvSystemConfig
from kwise.pv.weather import WEATHER_COLUMNS, WeatherData

__all__ = ["ArrayResult", "PvSimulation", "simulate", "simulate_array"]

_PITCH = 1.0  # infinite_sheds 는 높이/열간격 비율에만 의존한다


@dataclass(frozen=True, eq=False)
class ArrayResult:
    """어레이 하나의 결과. 인덱스는 tz-aware 순시각."""

    name: str
    capacity_kwp: float
    ac_kw: pd.Series
    poa_wm2: pd.Series

    @property
    def peak_ac_kw(self) -> float:
        return float(self.ac_kw.max()) if len(self.ac_kw) else 0.0


@dataclass(frozen=True, eq=False)
class PvSimulation:
    """설비 전체의 시뮬레이션 결과."""

    ac_kw: pd.Series
    arrays: tuple[ArrayResult, ...]
    config: PvSystemConfig
    weather_label: str
    hours_per_step: float = 1.0
    warnings: tuple[str, ...] = field(default=())

    @property
    def total_capacity_kwp(self) -> float:
        return self.config.total_capacity_kwp

    @property
    def energy_kwh(self) -> float:
        """시뮬레이션 구간의 발전량. 시간 간격을 곱해 적산한다."""
        return float(self.ac_kw.sum()) * self.hours_per_step

    @property
    def specific_yield_kwh_per_kwp(self) -> float | None:
        capacity = self.total_capacity_kwp
        return self.energy_kwh / capacity if capacity > 0 else None


def _check_weather(weather: WeatherData) -> None:
    missing = [column for column in WEATHER_COLUMNS if column not in weather.hourly.columns]
    if missing:
        raise ValueError(f"기상 데이터에 컬럼이 없습니다: {missing}")
    if weather.hourly.index.tz is None:
        raise ValueError("기상 데이터 인덱스는 tz-aware 여야 합니다 (지방시).")


def simulate_array(
    weather: WeatherData,
    config: PvSystemConfig,
    array: ArrayConfig,
) -> ArrayResult:
    """어레이 하나의 교류 출력을 낸다.

    용량 0 이면 계산하지 않고 0 시리즈를 돌려준다. PV 0 kWp 가 정확히 0 절감이
    되도록 하는 경로다 (요구사항서 11.3).
    """
    instants = weather.instants
    if array.capacity_kwp <= 0:
        zeros = pd.Series(0.0, index=instants, name=array.name)
        return ArrayResult(array.name, 0.0, zeros, zeros.copy())

    hourly = weather.hourly
    location = Location(
        latitude=config.latitude,
        longitude=config.longitude,
        tz=config.timezone,
        altitude=config.altitude_m,
    )
    solar = location.get_solarposition(instants)

    irradiance = infinite_sheds.get_irradiance(
        surface_tilt=array.tilt_deg,
        surface_azimuth=array.azimuth_deg,
        solar_zenith=solar["apparent_zenith"].to_numpy(),
        solar_azimuth=solar["azimuth"].to_numpy(),
        gcr=array.gcr,
        height=array.height_to_pitch * _PITCH,
        pitch=_PITCH,
        ghi=hourly["ghi"].to_numpy(),
        dhi=hourly["dhi"].to_numpy(),
        dni=hourly["dni"].to_numpy(),
        albedo=array.albedo,
        bifaciality=array.bifaciality,
    )
    poa = pd.Series(irradiance["poa_global"], index=instants).fillna(0.0).clip(lower=0.0)

    temp_params = TEMPERATURE_MODEL_PARAMETERS["sapm"][array.mounting.sapm_key]
    cell_temp = pvlib.temperature.sapm_cell(
        poa.to_numpy(),
        hourly["temp_air"].to_numpy(),
        hourly["wind_speed"].to_numpy(),
        temp_params["a"],
        temp_params["b"],
        temp_params["deltaT"],
    )

    dc_w = pvlib.pvsystem.pvwatts_dc(
        poa.to_numpy(),
        cell_temp,
        pdc0=array.capacity_kwp * 1000.0,
        gamma_pdc=array.gamma_pdc_per_c,
    )
    dc_after_losses = pd.Series(dc_w, index=instants).clip(lower=0.0) * (
        1.0 - array.system_loss_ratio
    )
    ac_w = pvlib.inverter.pvwatts(
        dc_after_losses.to_numpy(),
        pdc0=array.inverter_ac_kw * 1000.0,
    )
    ac_kw = pd.Series(ac_w, index=instants, name=array.name).fillna(0.0).clip(lower=0.0) / 1000.0
    return ArrayResult(
        name=array.name,
        capacity_kwp=array.capacity_kwp,
        ac_kw=ac_kw,
        poa_wm2=poa.rename(array.name),
    )


def simulate(weather: WeatherData, config: PvSystemConfig) -> PvSimulation:
    """다중 어레이를 순차로 돌려 합산한다.

    지붕과 벽면을 함께 올릴 수 있다. 벽면은 경사각 90° 로 두면 된다.
    """
    _check_weather(weather)
    instants = weather.instants
    results: list[ArrayResult] = []
    total = pd.Series(0.0, index=instants, name="pv_kw")
    for array in config.arrays:
        result = simulate_array(weather, config, array)
        total = total.add(result.ac_kw, fill_value=0.0)
        results.append(result)

    warnings: list[str] = []
    if not config.arrays:
        warnings.append("어레이가 없어 발전량이 0 입니다.")
    if config.total_capacity_kwp == 0:
        warnings.append("설치 용량이 0 kWp 입니다. 감도를 곱해도 결과는 0 입니다.")

    steps = weather.hourly.index.to_series().diff().dt.total_seconds().dropna()
    hours_per_step = float(steps.mode().iloc[0] / 3600.0) if len(steps) else 1.0

    return PvSimulation(
        ac_kw=total.rename("pv_kw"),
        arrays=tuple(results),
        config=config,
        weather_label=str(weather.label),
        hours_per_step=hours_per_step,
        warnings=tuple(warnings),
    )
