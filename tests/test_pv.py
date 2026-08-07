"""태양광 계산과 시각 정렬 (요구사항서 7.5, 9.2, 11.2).

시각 정렬이 이 모듈에서 가장 조용히 틀리는 곳이다. 부하는 구간 끝 라벨,
시뮬레이션은 순시값이라 반 칸(7.5분)을 보정해야 한다. 아래 무게중심 시험은
그 반 칸이 빠지면 정확히 7.5분 어긋나는 것을 잡아낸다.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pvlib
import pytest

from kwise.pv import (
    ArrayConfig,
    PvSystemConfig,
    SharpnessFactors,
    WeatherData,
    WeatherLabel,
    WeatherRequest,
    WeatherUnavailableError,
    align_simulation,
    align_to_load,
    cache_root,
    iter_scenarios,
    load_sharpness_factors,
    load_weather,
    sharpen,
    simulate,
    slot_midpoints,
    summarize_scenarios,
    weather_cache_path,
)
from kwise.pv.weather import normalize_hourly
from tests._synthetic import clearsky_weather

LAT, LON, TZ = 37.5, 127.0, "Asia/Seoul"
ALTITUDE = 50.0
DAY = "2023-07-03"
INTERVAL = 15


def roof_system(capacity_kwp: float = 1_000.0, **kwargs: float) -> PvSystemConfig:
    return PvSystemConfig(
        latitude=LAT,
        longitude=LON,
        arrays=(ArrayConfig.roof("지붕", capacity_kwp, **kwargs),),
        altitude_m=ALTITUDE,
        timezone=TZ,
    )


def load_index(day: str = DAY) -> pd.DatetimeIndex:
    """하루치 부하 라벨. 00:15 로 시작해 다음 날 00:00 으로 끝난다."""
    start = pd.Timestamp(day) + pd.Timedelta(minutes=INTERVAL)
    return pd.date_range(start, periods=96, freq=f"{INTERVAL}min")


def sun_events(day: str = DAY) -> pd.Series:
    times = pd.DatetimeIndex([f"{day} 12:00"]).tz_localize(TZ)
    events = pvlib.solarposition.sun_rise_set_transit_spa(times, LAT, LON).iloc[0]
    return events.map(lambda stamp: pd.Timestamp(stamp).tz_localize(None))


def centroid(series: pd.Series, *, offset_minutes: float) -> pd.Timestamp:
    """발전 무게중심. 맑은 날 남향 어레이라면 남중 시각과 같아야 한다."""
    reference = pd.Timestamp(series.index[0]).normalize()
    shifted = pd.DatetimeIndex(series.index) - pd.Timedelta(minutes=offset_minutes)
    seconds = (shifted - reference).total_seconds().to_numpy()
    weighted = float(np.average(seconds, weights=series.to_numpy()))
    return reference + pd.Timedelta(seconds=weighted)


@pytest.fixture(scope="module")
def weather() -> WeatherData:
    return clearsky_weather(latitude=LAT, longitude=LON, timezone=TZ, altitude_m=ALTITUDE)


@pytest.fixture(scope="module")
def aligned_day(weather: WeatherData) -> pd.Series:
    simulation = simulate(weather, roof_system())
    return align_simulation(simulation, load_index(), INTERVAL).kw


@pytest.fixture(scope="module")
def aligned_days(weather: WeatherData) -> pd.Series:
    """사흘치. 일별 총량 보존을 하루가 아니라 날짜 경계를 넘겨 확인한다."""
    simulation = simulate(weather, roof_system())
    start = pd.Timestamp("2023-07-02") + pd.Timedelta(minutes=INTERVAL)
    index = pd.date_range(start, periods=96 * 3, freq=f"{INTERVAL}min")
    return align_simulation(simulation, index, INTERVAL).kw


# --------------------------------------------------------------------- 시각 정렬


def test_slot_midpoints_sit_half_an_interval_before_the_label() -> None:
    """라벨 10:15 의 구간은 10:00~10:15 이므로 중앙은 10:07:30 이다."""
    labels = pd.DatetimeIndex(["2023-07-03 10:15", "2023-07-03 10:30"])
    assert list(slot_midpoints(labels, 15)) == [
        pd.Timestamp("2023-07-03 10:07:30"),
        pd.Timestamp("2023-07-03 10:22:30"),
    ]


def test_generation_centroid_matches_solar_noon(aligned_day: pd.Series) -> None:
    """맑은 날 남향 어레이의 발전 무게중심은 남중 시각과 같다.

    구간 중앙 보정이 맞으면 오차가 1분 미만이다.
    """
    generating = aligned_day[aligned_day > 0]
    noon = sun_events()["transit"]
    error = (centroid(generating, offset_minutes=INTERVAL / 2) - noon).total_seconds()
    assert abs(error) < 60.0


def test_missing_half_slot_shift_is_detected(aligned_day: pd.Series) -> None:
    """라벨을 그대로 시각으로 쓰면 정확히 7.5분 밀린다. 위 시험이 이것을 잡는다."""
    generating = aligned_day[aligned_day > 0]
    noon = sun_events()["transit"]
    error = (centroid(generating, offset_minutes=0) - noon).total_seconds()
    assert error == pytest.approx(INTERVAL / 2 * 60, abs=5.0)


def test_full_slot_shift_is_detected(weather: WeatherData) -> None:
    """한 슬롯(15분) 밀린 정렬은 무게중심이 15분 어긋나 걸린다."""
    simulation = simulate(weather, roof_system())
    labels = load_index()
    correct = align_simulation(simulation, labels, INTERVAL).kw
    shifted = pd.Series(correct.to_numpy(), index=labels + pd.Timedelta(minutes=INTERVAL))
    noon = sun_events()["transit"]
    generating = shifted[shifted > 0]
    error = (centroid(generating, offset_minutes=INTERVAL / 2) - noon).total_seconds()
    assert error == pytest.approx(INTERVAL * 60, abs=5.0)


def test_no_generation_outside_daylight(aligned_day: pd.Series) -> None:
    """일출 전·일몰 후 구간에는 발전량이 없다.

    시간별 값을 15분으로 선형 보간하면 일몰 뒤로 새어 나가므로 태양 고도로 잘라낸다.
    """
    events = sun_events()
    generating = aligned_day[aligned_day > 0]
    first, last = generating.index[0], generating.index[-1]
    assert first - pd.Timedelta(minutes=INTERVAL) >= events["sunrise"].floor("15min")
    assert last <= events["sunset"].ceil("15min")
    # 자정 전후는 확실히 0
    assert aligned_day.loc[f"{DAY} 03:00"] == 0.0
    assert aligned_day.loc[f"{DAY} 23:00"] == 0.0


def test_peak_slot_brackets_solar_noon(aligned_day: pd.Series) -> None:
    """최대 발전 슬롯의 구간이 남중 시각을 품는다."""
    peak_label = pd.Timestamp(aligned_day.idxmax())
    noon = sun_events()["transit"]
    assert peak_label - pd.Timedelta(minutes=INTERVAL) <= noon <= peak_label


def test_hourly_is_interpolated_to_quarter_hour(aligned_day: pd.Series) -> None:
    assert len(aligned_day) == 96
    assert aligned_day.index.freqstr == "15min"
    # 시간별 원자료보다 촘촘하므로 인접 15분 값이 서로 다르다
    midday = aligned_day.loc[f"{DAY} 11:00" : f"{DAY} 13:00"]
    assert midday.nunique() == len(midday)


def test_align_rejects_tz_aware_load_index(weather: WeatherData) -> None:
    simulation = simulate(weather, roof_system())
    labels = load_index().tz_localize(TZ)
    with pytest.raises(ValueError, match="tz-naive"):
        align_to_load(simulation.ac_kw, labels, INTERVAL)


def test_slots_outside_weather_range_are_zero_and_counted(weather: WeatherData) -> None:
    simulation = simulate(weather, roof_system())
    labels = pd.date_range("2023-07-10 00:15", periods=96, freq="15min")  # 기상 자료 밖
    aligned = align_to_load(simulation.ac_kw, labels, INTERVAL)
    assert aligned.uncovered_slots == 96
    assert float(aligned.kw.sum()) == 0.0


def test_energy_is_power_times_slot_hours(aligned_day: pd.Series) -> None:
    simulation_kw = aligned_day
    energy = simulation_kw * (INTERVAL / 60.0)
    assert float(energy.sum()) == pytest.approx(float(simulation_kw.sum()) * 0.25)


# --------------------------------------------------------------------- 시뮬레이션


def test_multiple_arrays_are_summed(weather: WeatherData) -> None:
    """지붕 + 벽면. 합계는 어레이별 합과 같다."""
    config = PvSystemConfig(
        latitude=LAT,
        longitude=LON,
        arrays=(ArrayConfig.roof("지붕", 800.0), ArrayConfig.wall("벽면", 200.0)),
        altitude_m=ALTITUDE,
        timezone=TZ,
    )
    simulation = simulate(weather, config)
    assert [array.name for array in simulation.arrays] == ["지붕", "벽면"]
    total = sum(array.ac_kw for array in simulation.arrays)
    pd.testing.assert_series_equal(simulation.ac_kw, total.rename("pv_kw"), check_names=False)
    assert simulation.total_capacity_kwp == 1_000.0


def test_wall_array_is_vertical_and_weaker(weather: WeatherData) -> None:
    """벽면은 경사각 90°. 같은 용량이면 여름 남향 지붕보다 발전이 적다."""
    wall = ArrayConfig.wall("벽면", 500.0)
    assert wall.tilt_deg == 90.0
    assert wall.is_wall

    roof_energy = simulate(weather, roof_system(500.0)).energy_kwh
    wall_energy = simulate(
        weather,
        PvSystemConfig(LAT, LON, arrays=(wall,), altitude_m=ALTITUDE, timezone=TZ),
    ).energy_kwh
    assert 0 < wall_energy < roof_energy


def test_denser_rows_lose_output_to_mutual_shading(weather: WeatherData) -> None:
    """GCR 하나로 어레이 상호 음영을 표현한다. 촘촘할수록 kWp 당 발전이 준다."""
    sparse = simulate(weather, roof_system(gcr=0.2)).energy_kwh
    dense = simulate(weather, roof_system(gcr=0.9)).energy_kwh
    assert dense < sparse


def test_zero_capacity_produces_zero(weather: WeatherData) -> None:
    simulation = simulate(weather, roof_system(0.0))
    assert simulation.total_capacity_kwp == 0.0
    assert float(simulation.ac_kw.abs().max()) == 0.0
    assert simulation.energy_kwh == 0.0
    assert simulation.specific_yield_kwh_per_kwp is None
    assert any("0 kWp" in message for message in simulation.warnings)


def test_specific_yield_is_reasonable(weather: WeatherData) -> None:
    """맑은 여름 사흘. kWp 당 하루 4~7 kWh 범위에 든다."""
    simulation = simulate(weather, roof_system())
    per_day = (simulation.specific_yield_kwh_per_kwp or 0.0) / 3
    assert 4.0 < per_day < 7.0


def test_system_loss_reduces_output_proportionally(weather: WeatherData) -> None:
    lossless = simulate(weather, roof_system(system_loss_ratio=0.0)).energy_kwh
    default = simulate(weather, roof_system(system_loss_ratio=0.14)).energy_kwh
    assert default < lossless
    assert default / lossless == pytest.approx(0.86, abs=0.02)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"capacity_kwp": -1.0}, "음수"),
        ({"capacity_kwp": 10.0, "tilt_deg": 120.0}, "경사각"),
        ({"capacity_kwp": 10.0, "gcr": 0.0}, "GCR"),
        ({"capacity_kwp": 10.0, "system_loss_ratio": 1.0}, "시스템 손실"),
    ],
)
def test_invalid_array_config_raises(kwargs: dict[str, float], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        ArrayConfig(name="test", **kwargs)  # type: ignore[arg-type]


def test_duplicate_array_names_raise() -> None:
    with pytest.raises(ValueError, match="겹칩니다"):
        PvSystemConfig(
            LAT,
            LON,
            arrays=(ArrayConfig.roof("A", 10.0), ArrayConfig.roof("A", 20.0)),
        )


def test_scaled_config_keeps_array_ratio() -> None:
    config = PvSystemConfig(
        LAT,
        LON,
        arrays=(ArrayConfig.roof("지붕", 800.0), ArrayConfig.wall("벽면", 200.0)),
    )
    scaled = config.scaled(500.0)
    assert scaled.total_capacity_kwp == pytest.approx(500.0)
    assert [array.capacity_kwp for array in scaled.arrays] == pytest.approx([400.0, 100.0])


# --------------------------------------------------------------------- 기상 캐시


def fake_fetch(request: WeatherRequest) -> pd.DataFrame:
    index = pd.date_range(f"{request.start} 00:00", periods=24, freq="1h", tz=request.timezone)
    return pd.DataFrame(
        {
            "ghi": 100.0,
            "dni": 200.0,
            "dhi": 50.0,
            "temp_air": 20.0,
            "wind_speed": 1.5,
            "snowfall": 0.0,
        },
        index=index,
    )


def test_weather_is_cached_as_parquet(tmp_path: Path) -> None:
    """두 번째 호출은 네트워크를 타지 않는다. 캐시가 없으면 fetcher 가 폭발한다."""
    request = WeatherRequest(
        LAT, LON, pd.Timestamp("2023-07-03").date(), pd.Timestamp("2023-07-03").date(), TZ
    )
    first = load_weather(request, fetch=fake_fetch, cache_dir=tmp_path)
    assert first.source == "network"
    assert first.path is not None
    assert first.path.suffix == ".parquet"
    assert first.path.is_file()

    def explode(_: WeatherRequest) -> pd.DataFrame:
        raise AssertionError("캐시가 있으면 다시 받으면 안 된다")

    second = load_weather(request, fetch=explode, cache_dir=tmp_path)
    assert second.source == "cache"
    # parquet 왕복에서 index.freq 만 사라진다. 값과 tz 는 그대로다.
    pd.testing.assert_frame_equal(first.hourly, second.hourly, check_freq=False)
    assert second.hourly.index.tz is not None  # tz 가 parquet 왕복에서 살아남는다


def test_refresh_bypasses_the_cache(tmp_path: Path) -> None:
    request = WeatherRequest(
        LAT, LON, pd.Timestamp("2023-07-03").date(), pd.Timestamp("2023-07-03").date(), TZ
    )
    load_weather(request, fetch=fake_fetch, cache_dir=tmp_path)
    refreshed = load_weather(request, fetch=fake_fetch, cache_dir=tmp_path, refresh=True)
    assert refreshed.source == "network"


def test_cache_root_follows_project_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PROJECT_CACHE", str(tmp_path / "somewhere"))
    assert cache_root() == tmp_path / "somewhere"
    monkeypatch.delenv("PROJECT_CACHE")
    assert cache_root() == Path("cache")


def test_cache_path_is_built_with_pathlib() -> None:
    request = WeatherRequest(
        LAT, LON, pd.Timestamp("2023-07-03").date(), pd.Timestamp("2023-07-05").date(), TZ
    )
    path = weather_cache_path(request, root=Path("cache"))
    assert isinstance(path, Path)
    assert path.parent.name == "weather"
    assert path.name.startswith("openmeteo_37.5000_127.0000_20230703_20230705")
    assert path.suffix == ".parquet"


def test_empty_response_raises(tmp_path: Path) -> None:
    request = WeatherRequest(
        LAT, LON, pd.Timestamp("2023-07-03").date(), pd.Timestamp("2023-07-03").date(), TZ
    )
    with pytest.raises(WeatherUnavailableError, match="비어 있습니다"):
        load_weather(request, fetch=lambda _: pd.DataFrame(), cache_dir=tmp_path)


def test_backwards_date_range_raises() -> None:
    with pytest.raises(ValueError, match="종료일"):
        WeatherRequest(
            LAT, LON, pd.Timestamp("2023-07-05").date(), pd.Timestamp("2023-07-03").date(), TZ
        )


def test_normalize_converts_wind_speed_to_metres_per_second() -> None:
    payload = {
        "hourly": {
            "time": ["2023-07-03T00:00", "2023-07-03T01:00"],
            "temperature_2m": [20.0, 21.0],
            "wind_speed_10m": [36.0, 18.0],  # km/h
            "shortwave_radiation": [0.0, 10.0],
            "direct_normal_irradiance": [0.0, 5.0],
            "diffuse_radiation": [0.0, 5.0],
            "snowfall": [0.0, 0.0],
        }
    }
    frame = normalize_hourly(payload, TZ)
    assert list(frame.columns) == ["ghi", "dni", "dhi", "temp_air", "wind_speed", "snowfall"]
    assert frame["wind_speed"].tolist() == pytest.approx([10.0, 5.0])
    assert frame.index.tz is not None


def test_weather_label_offsets() -> None:
    """Open-Meteo 라벨은 구간 시작이다. 대표 순시각은 30분 뒤다."""
    assert WeatherLabel.INTERVAL_START.offset() == pd.Timedelta(minutes=30)
    assert WeatherLabel.INTERVAL_END.offset() == pd.Timedelta(minutes=-30)
    assert WeatherLabel.INSTANT.offset() == pd.Timedelta(0)


def test_weather_instants_shift_by_half_an_hour(weather: WeatherData) -> None:
    assert (weather.instants - weather.hourly.index).unique().tolist() == [pd.Timedelta(minutes=30)]


def test_request_for_index_pads_the_period() -> None:
    index = pd.date_range("2023-07-03 00:15", periods=96, freq="15min")
    request = WeatherRequest.for_index(index, LAT, LON, timezone=TZ)
    assert request.start == pd.Timestamp("2023-07-02").date()
    assert request.end == pd.Timestamp("2023-07-05").date()


# --------------------------------------------------------------------- 감도 (9.2)
#
# 감도는 **첨예도**만 조정한다. 9.1 이 말하는 편향이 "총량 오차"가 아니라
# "프로파일 평탄화"이기 때문이다. 총량이 맞는데 일률 계수를 곱하면 총량을 ±30%
# 흔들게 되고 전력량요금 절감액 밴드와 회수기간 밴드가 함께 부풀려진다.


def test_default_sharpness_is_085_100_125() -> None:
    assert load_sharpness_factors().items() == (("평탄형", 0.85), ("기준", 1.00), ("첨예형", 1.25))


def test_labels_are_separate_from_the_internal_keys() -> None:
    """**이 축은 낙관·비관이 아니다.** 이름이 프로파일 모양을 가리켜야 한다.

    내부 키는 그대로 두고 표시 라벨만 분리한다 — 설정 파일과 케이스 정의가
    키를 쓰고 있다.
    """
    from kwise.pv import SCENARIO_KEYS

    assert SCENARIO_KEYS == ("conservative", "base", "optimistic")
    assert load_sharpness_factors().keyed_items() == (
        ("conservative", "평탄형", 0.85),
        ("base", "기준", 1.00),
        ("optimistic", "첨예형", 1.25),
    )
    assert load_sharpness_factors().base_label == "기준"


def test_sharpness_comes_from_the_assumptions_file() -> None:
    """s 값과 라벨은 코드가 아니라 ``data\assumptions.json`` 에 있다 (12장)."""
    from kwise.rules import assumption, assumptions

    for key in (
        "sensitivity.sharpness.conservative",
        "sensitivity.sharpness.base",
        "sensitivity.sharpness.optimistic",
        "sensitivity.labels",
    ):
        assert key in assumptions()
    assert assumption("sensitivity.sharpness.conservative") == 0.85
    assert assumption("sensitivity.labels") == ["평탄형", "기준", "첨예형"]


def test_sharpness_factors_have_no_code_defaults() -> None:
    """**데이터클래스에 기본값이 없다.** 남아 있으면 파일을 고쳐도 반영되지 않는다."""
    with pytest.raises(TypeError):
        SharpnessFactors()  # type: ignore[call-arg]


def test_sharpness_one_returns_the_original_exactly(aligned_day: pd.Series) -> None:
    """s=1.0 이면 원본 그대로다. 부동소수 오차조차 만들지 않는다."""
    adjusted = sharpen(aligned_day, 1.0)
    pd.testing.assert_series_equal(adjusted, aligned_day)
    assert adjusted is aligned_day


def test_daily_total_is_preserved(aligned_days: pd.Series) -> None:
    """세 시나리오의 **일별** 총 발전량이 서로 ±1% 이내여야 한다.

    총량 보존이 이 방식의 전부다. 이것이 깨지면 전력량요금 절감액이 흔들린다.
    """
    day = pd.DatetimeIndex(aligned_days.index).normalize().to_numpy()
    base_daily = aligned_days.groupby(day).sum()
    for _, sharpness, adjusted in iter_scenarios(aligned_days):
        daily = adjusted.groupby(day).sum()
        generating = base_daily > 0
        ratio = (daily[generating] / base_daily[generating]).abs()
        assert float((ratio - 1.0).abs().max()) < 0.01, sharpness


def test_daily_total_is_preserved_to_machine_precision(aligned_day: pd.Series) -> None:
    """±1% 는 요구 사항이고 실제로는 재정규화 덕에 부동소수 수준으로 같다."""
    for _, _, adjusted in iter_scenarios(aligned_day):
        assert float(adjusted.sum()) == pytest.approx(float(aligned_day.sum()), rel=1e-12)


def test_noon_peak_widens_with_sharpness(aligned_day: pd.Series) -> None:
    """정오 최대 출력은 보수 < 기준 < 낙관 이어야 한다."""
    noon = [
        float(adjusted.loc[f"{DAY} 11:00" : f"{DAY} 13:00"].max())
        for _, _, adjusted in iter_scenarios(aligned_day)
    ]
    assert noon[0] < noon[1] < noon[2]


def test_night_stays_zero(aligned_day: pd.Series) -> None:
    """평균을 축으로 회전시켜도 밤에 발전이 생기지 않는다.

    일평균을 야간 0 까지 포함해 잡으면 밤의 편차가 전체를 지배해 재정규화 후
    정오 출력이 오히려 낮아진다. 발전 슬롯 기준 평균을 쓰는 이유다.
    """
    for _, _, adjusted in iter_scenarios(aligned_day):
        assert adjusted.loc[f"{DAY} 03:00"] == 0.0
        assert adjusted.loc[f"{DAY} 23:00"] == 0.0
        assert float(adjusted.min()) >= 0.0


def test_sharpen_is_homogeneous(aligned_day: pd.Series) -> None:
    """양의 상수배에 동차다 — 단위 프로파일에 한 번만 걸어도 된다.

    용량 곡선이 20단계를 훑을 때 sharpen 을 매번 다시 걸지 않는 근거다.
    """
    scaled = sharpen(aligned_day * 250.0, 1.25)
    once = sharpen(aligned_day, 1.25) * 250.0
    pd.testing.assert_series_equal(scaled, once, rtol=1e-12)


def test_zero_capacity_makes_every_scenario_identical(weather: WeatherData) -> None:
    """PV 0 kWp 면 감도 3종 결과가 정확히 같다 (요구사항서 11.3)."""
    simulation = simulate(weather, roof_system(0.0))
    aligned = align_simulation(simulation, load_index(), INTERVAL)
    summaries = summarize_scenarios(aligned.kw, INTERVAL)
    assert {item.total_kwh for item in summaries} == {0.0}
    assert {item.peak_kw for item in summaries} == {0.0}


def test_scenarios_are_produced_one_at_a_time(aligned_day: pd.Series) -> None:
    """시나리오는 순차 처리한다. 세 시계열을 동시에 들고 있지 않는다."""
    iterator = iter_scenarios(aligned_day)
    name, sharpness, series = next(iterator)
    assert (name, sharpness) == ("평탄형", 0.85)
    assert float(series.max()) < float(aligned_day.max())
    assert len(list(iterator)) == 2


def test_summary_totals_are_equal_but_peaks_differ(aligned_day: pd.Series) -> None:
    summaries = summarize_scenarios(aligned_day, INTERVAL)
    assert [item.name for item in summaries] == ["평탄형", "기준", "첨예형"]
    totals = [item.total_kwh for item in summaries]
    assert totals[0] == pytest.approx(totals[1], rel=1e-12)
    assert totals[2] == pytest.approx(totals[1], rel=1e-12)
    assert summaries[0].peak_kw < summaries[1].peak_kw < summaries[2].peak_kw


def test_custom_factors_are_allowed(aligned_day: pd.Series) -> None:
    factors = SharpnessFactors(
        conservative=0.8, base=1.0, optimistic=1.1, labels=("평탄", "기준", "첨예")
    )
    summaries = summarize_scenarios(aligned_day, INTERVAL, factors)
    assert [item.sharpness for item in summaries] == [0.8, 1.0, 1.1]


def test_empty_series_survives() -> None:
    empty = pd.Series(dtype="float64", index=pd.DatetimeIndex([]))
    assert sharpen(empty, 1.25).empty


@pytest.mark.parametrize(
    "kwargs",
    [
        {"conservative": -0.1, "base": 1.0, "optimistic": 1.25},
        {"conservative": 1.2, "base": 1.0, "optimistic": 0.9},
    ],
)
def test_invalid_factors_raise(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError, match="첨예도 계수"):
        SharpnessFactors(labels=("평탄형", "기준", "첨예형"), **kwargs)
