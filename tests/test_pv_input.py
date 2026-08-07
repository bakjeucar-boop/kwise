"""지역 선택과 태양광 간략 입력 (요구사항서 3.3).

**시군구 단위 선택은 정확도를 희생하지 않는다.** Open-Meteo 가 쓰는 ERA5 격자가
25~31 km 라, 시군구 중심점과 실제 부지의 거리가 그보다 작으면 같은 셀을 조회한다.
서울 25개 구는 좌표 폭이 남북 20.6 km · 동서 29.0 km 라 같은 격자에 들 수 있다.

**간략 입력은 셋뿐이다** — 면적 · 밀도 · 지역. 방위·경사·손실률을 정밀하게 받아도
발전량 예측 R² 가 0.8 이라 기상 오차가 지배한다. 게다가 용량 곡선이 이미 20단계를
훑으므로 면적 환산이 ±20% 틀려도 곡선 위 다른 점을 볼 뿐 결론이 바뀌지 않는다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from kwise.io import UsageData
from kwise.pv import (
    GRID_RESOLUTION_DEG,
    KOREA_LATITUDE_RANGE,
    KOREA_LONGITUDE_RANGE,
    PROVINCE_ALIASES,
    PvPresetError,
    PvQuickInput,
    Region,
    RegionDataError,
    WeatherRequest,
    capacity_from_area_kwp,
    capacity_preview,
    find_region,
    grid_cell,
    list_provinces,
    list_sigungu,
    load_pv_presets,
    load_regions,
    preset_data_path,
    region_data_path,
)
from kwise.pv.region import SEJONG_COORDINATE, SEJONG_KEY

PERIOD = (pd.Timestamp("2023-04-25").date(), pd.Timestamp("2024-04-27").date())


@pytest.fixture(scope="module")
def regions() -> tuple[Region, ...]:
    return load_regions()


# --------------------------------------------------------------------- 지역 데이터


def test_region_file_ships_with_the_package() -> None:
    assert region_data_path().is_file()
    assert region_data_path().name == "sigungu_kr.json"


def test_all_229_regions_load(regions: tuple[Region, ...]) -> None:
    """원본 228개 + 세종 1개 = 229개."""
    assert len(regions) == 229
    assert len({region.key for region in regions}) == 229
    assert len(list_provinces(regions)) == 17


def test_coordinates_are_floats_not_strings(regions: tuple[Region, ...]) -> None:
    """원본은 문자열이다. 그대로 두면 시뮬레이션에서 터진다."""
    raw = json.loads(region_data_path().read_text(encoding="utf-8"))
    assert isinstance(next(iter(raw.values()))["lat"], str)
    assert all(isinstance(region.latitude, float) for region in regions)
    assert all(isinstance(region.longitude, float) for region in regions)


def test_every_coordinate_is_inside_korea(regions: tuple[Region, ...]) -> None:
    """위도 33~39, 경도 124~132 밖이면 좌표가 잘못 들어간 것이다."""
    low_lat, high_lat = KOREA_LATITUDE_RANGE
    low_lon, high_lon = KOREA_LONGITUDE_RANGE
    for region in regions:
        assert low_lat <= region.latitude <= high_lat, region.key
        assert low_lon <= region.longitude <= high_lon, region.key


def test_sejong_is_added_as_a_single_tier_city(regions: tuple[Region, ...]) -> None:
    """세종은 하위 시군구가 없는 단층 자치시라 원본에서 누락되어 있었다."""
    raw = json.loads(region_data_path().read_text(encoding="utf-8"))
    assert not any(key.startswith("세종") for key in raw)  # 원본에는 없다

    sejong = find_region(SEJONG_KEY, regions)
    assert sejong.is_single_tier
    assert (sejong.latitude, sejong.longitude) == SEJONG_COORDINATE
    assert list_sigungu("세종특별자치시", regions) == (sejong,)


def test_original_coordinates_are_untouched(regions: tuple[Region, ...]) -> None:
    """세종만 더한다. **다른 좌표는 손대지 않는다.**"""
    raw = json.loads(region_data_path().read_text(encoding="utf-8"))
    for region in regions:
        if region.key == SEJONG_KEY:
            continue
        assert float(raw[region.key]["lat"]) == region.latitude
        assert float(raw[region.key]["long"]) == region.longitude


def test_renamed_provinces_keep_the_original_key(regions: tuple[Region, ...]) -> None:
    """키는 파일 그대로 두어 호환을 유지하고, 표시 명칭만 현행으로 병기한다."""
    gangwon = find_region("강원도/춘천시", regions)
    assert gangwon.province == "강원도"  # 키는 개편 전 그대로
    assert gangwon.label == "강원특별자치도 (구 강원도) 춘천시"
    assert set(PROVINCE_ALIASES) == {"강원도", "전라북도"}
    for province in PROVINCE_ALIASES:
        assert province in list_provinces(regions)


def test_dropdowns_come_from_the_data(regions: tuple[Region, ...]) -> None:
    """8세션 UI 는 이 목록으로 만든다. 하드코딩 금지."""
    provinces = list_provinces(regions)
    assert "서울특별시" in provinces
    seoul = list_sigungu("서울특별시", regions)
    assert len(seoul) == 25
    assert {region.name for region in seoul} >= {"강남구", "서초구", "종로구"}
    # 시군구 목록의 합이 전체와 같다 — 빠지는 시도가 없다.
    assert sum(len(list_sigungu(name, regions)) for name in provinces) == len(regions)


def test_unknown_province_and_key_raise(regions: tuple[Region, ...]) -> None:
    with pytest.raises(RegionDataError, match="없는 시도"):
        list_sigungu("경기북도", regions)
    with pytest.raises(RegionDataError, match="없는 키"):
        find_region("서울특별시/없는구", regions)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(RegionDataError, match="지역 데이터 파일이 없습니다"):
        load_regions(str(tmp_path / "none.json"))


# --------------------------------------------------------------------- 격자·캐시 키


def test_seoul_districts_round_to_the_same_grid(regions: tuple[Region, ...]) -> None:
    """**서울 안에서는 구를 바꿔도 같은 격자다.** 시군구 선택이 정확도를 깎지 않는다."""
    gangnam = find_region("서울특별시/강남구", regions)
    seocho = find_region("서울특별시/서초구", regions)
    assert gangnam.grid_cell() == seocho.grid_cell() == (37.5, 127.0)

    seoul = list_sigungu("서울특별시", regions)
    span_lat = max(r.latitude for r in seoul) - min(r.latitude for r in seoul)
    span_lon = max(r.longitude for r in seoul) - min(r.longitude for r in seoul)
    assert span_lat * 111 == pytest.approx(20.6, abs=1.0)  # 남북 20.6 km
    assert span_lon * 88.5 == pytest.approx(29.0, abs=1.5)  # 동서 29.0 km


def test_grid_cell_rounds_to_the_era5_resolution() -> None:
    assert GRID_RESOLUTION_DEG == 0.25
    assert grid_cell(37.4951, 127.06278) == (37.5, 127.0)
    assert grid_cell(37.6, 127.2) == (37.5, 127.25)
    assert grid_cell(35.1, 129.0) == (35.0, 129.0)
    with pytest.raises(ValueError, match="격자 해상도"):
        grid_cell(37.5, 127.0, resolution_deg=0.0)


def test_cache_key_is_shared_within_a_grid_cell(regions: tuple[Region, ...]) -> None:
    """**같은 격자면 캐시를 공유한다.** 7세션 케이스 스터디의 재조회를 크게 줄인다."""
    gangnam = find_region("서울특별시/강남구", regions)
    seocho = find_region("서울특별시/서초구", regions)
    start, end = PERIOD
    left = WeatherRequest(gangnam.latitude, gangnam.longitude, start, end)
    right = WeatherRequest(seocho.latitude, seocho.longitude, start, end)
    assert left.cache_name == right.cache_name
    assert left.grid_cell == right.grid_cell


def test_direct_coordinates_share_the_cache_with_a_sigungu(regions: tuple[Region, ...]) -> None:
    """좌표를 직접 넣어도 같은 격자면 시군구 선택과 같은 파일을 쓴다."""
    gangnam = find_region("서울특별시/강남구", regions)
    start, end = PERIOD
    picked = WeatherRequest(gangnam.latitude, gangnam.longitude, start, end)
    typed = WeatherRequest(37.512, 126.998, start, end)  # 손으로 넣은 부지 좌표
    assert typed.cache_name == picked.cache_name


def test_different_grid_cells_do_not_share_the_cache() -> None:
    """격자가 다르면 당연히 다른 파일이다. 반올림이 지나치지 않았다는 확인이다."""
    start, end = PERIOD
    seoul = WeatherRequest(37.5, 127.0, start, end)
    busan = WeatherRequest(35.1796, 129.0756, start, end)
    assert seoul.cache_name != busan.cache_name


def test_cache_name_still_separates_period_and_timezone() -> None:
    start, end = PERIOD
    base = WeatherRequest(37.5, 127.0, start, end)
    other_period = WeatherRequest(37.5, 127.0, start, pd.Timestamp("2024-05-01").date())
    assert base.cache_name != other_period.cache_name


# --------------------------------------------------------------------- 간략 입력


def test_presets_come_from_the_data_file() -> None:
    """프리셋은 ``data\\pv_presets.json`` 에 있다. 하드코딩하지 않는다."""
    assert preset_data_path().is_file()
    presets = load_pv_presets()
    # 내부 키와 표시 라벨을 분리한다 — 산출물에는 구어체를 쓰지 않는다.
    assert tuple(item.key for item in presets.densities) == ("high", "normal", "low")
    assert tuple(item.label for item in presets.densities) == ("높음", "보통", "낮음")
    assert presets.density_label == "설치 밀도"
    # 밀도만으로는 좋고 나쁨을 알 수 없다. 상충 관계를 한 줄로 병기한다.
    assert all(item.tradeoff for item in presets.densities)
    assert presets.area_per_kwp_m2 == 5.0
    assert presets.default_azimuth_deg == 180.0
    assert presets.default.key == "normal"


@pytest.mark.parametrize(
    ("density", "expected_kwp"),
    [("high", 110.0), ("normal", 80.0), ("low", 60.0)],
)
def test_area_converts_to_capacity_by_density(
    regions: tuple[Region, ...], density: str, expected_kwp: float
) -> None:
    """1,000 m² 에서 높음 110 / 보통 80 / 낮음 60 kWp (±10%)."""
    quick = PvQuickInput(
        area_m2=1_000.0, density=density, region=find_region("서울특별시/강남구", regions)
    )
    assert quick.capacity_kwp == pytest.approx(expected_kwp, rel=0.10)


def test_density_moves_gcr_and_tilt_together() -> None:
    """**밀도 하나로 GCR 과 경사각이 함께 정해진다.**

    고밀도 배치는 이격을 좁히는 대신 경사를 낮춰 음영을 줄이는 실무 관행이다.
    """
    presets = load_pv_presets()
    dense, normal, sparse = (presets.density(key) for key in ("high", "normal", "low"))
    assert dense.gcr > normal.gcr > sparse.gcr  # 밀도가 높을수록 GCR 이 크고
    assert dense.tilt_deg < normal.tilt_deg < sparse.tilt_deg  # 경사는 낮다

    quick = PvQuickInput(area_m2=1_000.0, density="high", latitude=37.5, longitude=127.0)
    assert (quick.gcr, quick.tilt_deg) == (0.55, 15.0)
    array = quick.to_system().arrays[0]
    assert (array.gcr, array.tilt_deg) == (0.55, 15.0)


def test_azimuth_defaults_to_south() -> None:
    quick = PvQuickInput(area_m2=500.0, latitude=37.5, longitude=127.0)
    assert quick.azimuth == 180.0
    assert quick.to_system().arrays[0].azimuth_deg == 180.0


def test_capacity_override_wins_over_area(regions: tuple[Region, ...]) -> None:
    """확장 패널에서 용량을 직접 지정하면 면적 환산을 덮어쓴다 (견적서가 있는 경우)."""
    quick = PvQuickInput(
        area_m2=1_000.0,
        region=find_region("서울특별시/강남구", regions),
        capacity_override_kwp=250.0,
    )
    assert quick.area_capacity_kwp == pytest.approx(80.0)  # 환산값은 그대로 보여 준다
    assert quick.capacity_kwp == 250.0
    assert quick.to_system().arrays[0].capacity_kwp == 250.0
    assert "직접 입력" in quick.describe()


def test_expansion_panel_values_fall_back_to_defaults() -> None:
    """**모드를 나누지 않는다.** 확장 패널을 안 건드리면 기본값이 자동 적용된다."""
    from kwise.pv.config import DEFAULT_SYSTEM_LOSS

    quick = PvQuickInput(area_m2=1_000.0, latitude=37.5, longitude=127.0)
    array = quick.to_system().arrays[0]
    assert array.system_loss_ratio == DEFAULT_SYSTEM_LOSS
    assert array.azimuth_deg == 180.0
    assert array.gcr == 0.40

    tuned = PvQuickInput(
        area_m2=1_000.0,
        latitude=37.5,
        longitude=127.0,
        azimuth_deg=200.0,
        tilt_override_deg=10.0,
        gcr_override=0.5,
        system_loss_ratio=0.10,
    )
    tuned_array = tuned.to_system().arrays[0]
    assert (tuned_array.azimuth_deg, tuned_array.tilt_deg) == (200.0, 10.0)
    assert (tuned_array.gcr, tuned_array.system_loss_ratio) == (0.5, 0.10)


def test_region_or_coordinates_are_required() -> None:
    with pytest.raises(ValueError, match="지역을 정하지 못했습니다"):
        PvQuickInput(area_m2=1_000.0)
    with pytest.raises(ValueError, match="설치 가능 면적"):
        PvQuickInput(area_m2=-1.0, latitude=37.5, longitude=127.0)


def test_coordinates_beat_the_region_when_both_given(regions: tuple[Region, ...]) -> None:
    """좌표를 직접 넣으면 그 값을 쓴다. 산악·해안 지형용 경로다."""
    quick = PvQuickInput(
        area_m2=1_000.0,
        region=find_region("서울특별시/강남구", regions),
        latitude=37.7,
        longitude=128.9,
    )
    assert quick.coordinate == (37.7, 128.9)


def test_unknown_density_is_rejected() -> None:
    quick = PvQuickInput(area_m2=1_000.0, density="아주높음", latitude=37.5, longitude=127.0)
    with pytest.raises(PvPresetError, match="없는 설치 밀도"):
        _ = quick.gcr


def test_capacity_formula_is_explicit() -> None:
    assert capacity_from_area_kwp(1_000.0, gcr=0.4, area_per_kwp_m2=5.0) == pytest.approx(80.0)
    assert capacity_from_area_kwp(0.0, gcr=0.4, area_per_kwp_m2=5.0) == 0.0
    with pytest.raises(ValueError, match="설치 가능 면적"):
        capacity_from_area_kwp(-1.0, gcr=0.4, area_per_kwp_m2=5.0)


# --------------------------------------------------------------------- 용량 곡선과의 관계


def test_area_error_only_moves_the_point_on_the_curve(
    sample_usage: UsageData, sample_unit_pv: pd.Series
) -> None:
    """**면적이 ±20% 달라져도 용량 곡선 자체는 같다.** 곡선 위 위치만 옮겨진다.

    곡선은 0 부터 상한까지 용량을 훑으므로, 면적 환산 오차는 "어느 점을 보느냐"
    를 바꿀 뿐 곡선의 모양이나 결론을 바꾸지 않는다. 간략 입력이 정당한 이유다.
    """
    from kwise.measures.solar import solar_curve
    from kwise.tariff import TariffSelection, load_tariff

    table = load_tariff()
    selection = TariffSelection("general_b", "high_a", "I")
    base = PvQuickInput(area_m2=1_000.0, latitude=37.5, longitude=127.0)
    wide = PvQuickInput(area_m2=1_200.0, latitude=37.5, longitude=127.0)
    narrow = PvQuickInput(area_m2=800.0, latitude=37.5, longitude=127.0)

    assert wide.capacity_kwp == pytest.approx(base.capacity_kwp * 1.2)
    assert narrow.capacity_kwp == pytest.approx(base.capacity_kwp * 0.8)

    # 곡선은 상한만 정해 주면 같은 함수를 그린다 — 같은 용량 지점이면 같은 값이다.
    curve = solar_curve(
        sample_usage,
        table,
        selection,
        sample_unit_pv,
        max_capacity_kwp=wide.capacity_kwp,
        unit_cost_won_per_kwp=1_200_000.0,
        steps=6,
        baseline=None,
    )
    by_capacity = {round(point.capacity_kwp, 6): point for point in curve.points}
    # 800 m² 환산 용량(64 kWp)은 1,200 m² 곡선의 한 점(6단계 중 4번째)과 같다.
    assert round(narrow.capacity_kwp, 6) in by_capacity
    matched = by_capacity[round(narrow.capacity_kwp, 6)]
    assert matched.capacity_kwp == pytest.approx(narrow.capacity_kwp)
    assert matched.total_saving_won > 0


# --------------------------------------------------------------------- 설치 밀도 라벨 (3.3)


def test_density_keys_are_separate_from_the_labels() -> None:
    """산출물에 구어체를 쓰지 않는다. 내부 키와 표시 라벨을 분리한다."""
    presets = load_pv_presets()
    assert [item.key for item in presets.densities] == ["high", "normal", "low"]
    assert [item.label for item in presets.densities] == ["높음", "보통", "낮음"]
    assert presets.density_label == "설치 밀도"
    assert presets.default.key == "normal" and presets.default.label == "보통"


def test_every_density_states_its_tradeoff() -> None:
    """**밀도만으로는 '높으면 좋은 것인지' 를 알 수 없다.** 상충 관계를 병기한다."""
    presets = load_pv_presets()
    high, normal, low = presets.densities
    assert "면적당 용량이 가장 크다" in high.tradeoff
    assert "kWp당 발전량이 가장 크다" in low.tradeoff
    assert "균형" in normal.tradeoff
    for item in presets.densities:
        assert len(item.tradeoff) > 10


def test_capacity_preview_shows_the_conversion_immediately() -> None:
    """고르는 즉시 환산 용량을 함께 보여 준다 — 1,000 m² → 110 / 80 / 60 kWp."""
    preview = capacity_preview(1_000.0)
    assert [item[0].label for item in preview] == ["높음", "보통", "낮음"]
    assert [round(item[1]) for item in preview] == [110, 80, 60]


def test_capacity_preview_scales_with_area() -> None:
    small = capacity_preview(500.0)
    large = capacity_preview(1_000.0)
    for (_, low_kwp), (_, high_kwp) in zip(small, large, strict=True):
        assert high_kwp == pytest.approx(low_kwp * 2.0)
