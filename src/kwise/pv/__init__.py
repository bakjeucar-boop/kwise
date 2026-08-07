"""태양광 계산과 기상 취득 (요구사항서 7.5, 9.2).

    load_weather()   Open-Meteo 시간별 기상. PROJECT_CACHE 에 parquet 캐시
    simulate()       다중 어레이 발전 시뮬레이션 (GCR 단일 파라미터)
    align_to_load()  순시 출력 → 부하 라벨(구간 끝) 기준 구간 평균 출력
    summarize_scenarios()  감도 3종 (0.70 / 1.00 / 1.20)

시각 정렬이 핵심이다. 부하는 구간 끝 라벨, 시뮬은 순시값이므로
:mod:`kwise.pv.alignment` 를 반드시 거친다.
"""

from kwise.pv.alignment import (
    AlignedGeneration,
    align_simulation,
    align_to_load,
    daylight_mask,
    slot_midpoints,
)
from kwise.pv.config import (
    DEFAULT_ALBEDO,
    DEFAULT_AZIMUTH_DEG,
    DEFAULT_DC_AC_RATIO,
    DEFAULT_GCR,
    DEFAULT_SYSTEM_LOSS,
    DEFAULT_TILT_DEG,
    WALL_TILT_DEG,
    ArrayConfig,
    Mounting,
    PvSystemConfig,
)
from kwise.pv.layout import (
    PRESET_FILENAME,
    DensityPreset,
    PvPresetError,
    PvPresets,
    PvQuickInput,
    capacity_from_area_kwp,
    load_pv_presets,
    preset_data_path,
)
from kwise.pv.region import (
    GRID_RESOLUTION_DEG,
    KOREA_LATITUDE_RANGE,
    KOREA_LONGITUDE_RANGE,
    PROVINCE_ALIASES,
    Region,
    RegionDataError,
    find_region,
    grid_cell,
    list_provinces,
    list_sigungu,
    load_regions,
    region_data_path,
)
from kwise.pv.sensitivity import (
    DEFAULT_FACTORS,
    ScenarioSummary,
    SensitivityFactors,
    iter_scenarios,
    summarize_scenarios,
)
from kwise.pv.simulate import ArrayResult, PvSimulation, simulate, simulate_array
from kwise.pv.weather import (
    WEATHER_COLUMNS,
    WeatherData,
    WeatherLabel,
    WeatherRequest,
    WeatherUnavailableError,
    cache_root,
    fetch_open_meteo,
    load_weather,
    weather_cache_path,
)

__all__ = [
    "DEFAULT_ALBEDO",
    "DEFAULT_AZIMUTH_DEG",
    "DEFAULT_DC_AC_RATIO",
    "DEFAULT_FACTORS",
    "DEFAULT_GCR",
    "DEFAULT_SYSTEM_LOSS",
    "DEFAULT_TILT_DEG",
    "GRID_RESOLUTION_DEG",
    "KOREA_LATITUDE_RANGE",
    "KOREA_LONGITUDE_RANGE",
    "PRESET_FILENAME",
    "PROVINCE_ALIASES",
    "WALL_TILT_DEG",
    "WEATHER_COLUMNS",
    "AlignedGeneration",
    "ArrayConfig",
    "ArrayResult",
    "DensityPreset",
    "Mounting",
    "PvPresetError",
    "PvPresets",
    "PvQuickInput",
    "PvSimulation",
    "PvSystemConfig",
    "Region",
    "RegionDataError",
    "ScenarioSummary",
    "SensitivityFactors",
    "WeatherData",
    "WeatherLabel",
    "WeatherRequest",
    "WeatherUnavailableError",
    "align_simulation",
    "align_to_load",
    "cache_root",
    "capacity_from_area_kwp",
    "daylight_mask",
    "fetch_open_meteo",
    "find_region",
    "grid_cell",
    "iter_scenarios",
    "list_provinces",
    "list_sigungu",
    "load_pv_presets",
    "load_regions",
    "load_weather",
    "preset_data_path",
    "region_data_path",
    "simulate",
    "simulate_array",
    "slot_midpoints",
    "summarize_scenarios",
    "weather_cache_path",
]
