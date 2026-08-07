"""지역 선택 (요구사항서 3.3).

**시군구 단위 선택은 정확도를 희생하지 않는다.**

Open-Meteo 가 쓰는 ERA5 격자는 25~31 km 다. 시군구 중심점과 실제 부지의 거리가
격자보다 작으면 **같은 셀을 조회**하므로 결과가 같다. 서울 25개 구의 좌표 폭이
남북 20.6 km · 동서 29.0 km 이므로 서울 안에서는 구를 바꿔도 같은 격자가 나올 수
있다. 그래서 기본 입력은 시도 → 시군구 2단으로 충분하다.

두 경로를 모두 지원한다.

    시군구 선택 (기본)     :func:`load_regions` 로 만든 목록에서 고른다
    위경도 직접 입력 (옵션) 산악·해안 지형이나 넓은 시군구용

**드롭다운은 이 데이터에서 생성한다. 하드코딩하지 않는다** (8세션 UI).

캐시 키는 :data:`GRID_RESOLUTION_DEG` 로 반올림한다. ERA5 해상도가 그 정도이므로
손실 없이 적중률이 오른다 — 시군구 선택이든 좌표 직접 입력이든 **같은 격자면
캐시를 공유한다.**
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

__all__ = [
    "GRID_RESOLUTION_DEG",
    "KOREA_LATITUDE_RANGE",
    "KOREA_LONGITUDE_RANGE",
    "PROVINCE_ALIASES",
    "REGION_FILENAME",
    "Region",
    "RegionDataError",
    "find_region",
    "grid_cell",
    "list_provinces",
    "list_sigungu",
    "load_regions",
    "region_data_path",
]

REGION_FILENAME = "sigungu_kr.json"

# ERA5 재분석 격자 해상도. 0.25° 는 위도 약 27.8 km, 위도 37°에서 경도 약 22.2 km 다.
# 시군구 중심점의 오차가 이보다 작으므로 반올림해도 다른 셀을 조회하지 않는다.
GRID_RESOLUTION_DEG = 0.25

# 좌표 유효성 범위. 대한민국 육상 영역을 넉넉히 감싼다
# (마라도 33.1°N ~ 강원 고성 38.6°N, 백령도 124.6°E ~ 독도 131.9°E).
KOREA_LATITUDE_RANGE: tuple[float, float] = (33.0, 39.0)
KOREA_LONGITUDE_RANGE: tuple[float, float] = (124.0, 132.0)

# 원본 파일의 시도 명칭이 개편 전 기준이다. **키는 파일 그대로 두어 호환을 유지하고**
# 표시 명칭에만 현행 명칭을 병기한다.
PROVINCE_ALIASES: dict[str, str] = {
    "강원도": "강원특별자치도 (구 강원도)",
    "전라북도": "전북특별자치도 (구 전라북도)",
}

# 세종특별자치시는 하위 시군구가 없는 **단층 자치시**라 원본에서 누락되어 있었다.
# 좌표는 세종특별자치시청 (정부세종청사 인근, 세종특별자치시 한누리대로 2130).
# 원본의 다른 좌표는 손대지 않는다.
SEJONG_KEY = "세종특별자치시/세종특별자치시"
SEJONG_COORDINATE: tuple[float, float] = (36.592369, 127.292199)


class RegionDataError(ValueError):
    """지역 데이터를 읽지 못했을 때 발생한다."""


@dataclass(frozen=True)
class Region:
    """시군구 하나.

    Attributes:
        key: 원본 파일의 키 (``"서울특별시/강남구"``). **호환을 위해 바꾸지 않는다.**
        province: 시도. 파일 그대로의 명칭이다.
        name: 시군구.
        label: 표시 명칭. 개편된 시도는 현행 명칭을 병기한다.
    """

    key: str
    province: str
    name: str
    latitude: float
    longitude: float

    @property
    def label(self) -> str:
        return f"{PROVINCE_ALIASES.get(self.province, self.province)} {self.name}"

    @property
    def is_single_tier(self) -> bool:
        """세종처럼 시도와 시군구가 같은 단층 자치시인가."""
        return self.province == self.name

    def grid_cell(self, resolution_deg: float = GRID_RESOLUTION_DEG) -> tuple[float, float]:
        """이 지점이 속한 기상 격자 중심."""
        return grid_cell(self.latitude, self.longitude, resolution_deg=resolution_deg)


def grid_cell(
    latitude: float,
    longitude: float,
    *,
    resolution_deg: float = GRID_RESOLUTION_DEG,
) -> tuple[float, float]:
    """좌표를 기상 격자 단위로 반올림한다.

    **캐시 키를 여기서 만든다.** ERA5 해상도가 25~31 km 라 이보다 가까운 두 지점은
    같은 셀을 조회한다. 반올림하면 손실 없이 캐시 적중률이 오른다 — 강남구와
    서초구가 같은 파일을 쓰고, 좌표를 직접 넣은 사용자도 그 파일을 공유한다.
    """
    if resolution_deg <= 0:
        raise ValueError(f"격자 해상도는 양수여야 합니다: {resolution_deg}")
    return (
        round(round(latitude / resolution_deg) * resolution_deg, 6),
        round(round(longitude / resolution_deg) * resolution_deg, 6),
    )


def region_data_path() -> Path:
    """지역 데이터 파일. 환경변수 ``KWISE_TARIFF_DIR`` 를 요금표와 함께 쓴다."""
    override = os.environ.get("KWISE_TARIFF_DIR")
    base = Path(override) if override else Path(__file__).resolve().parents[3] / "data"
    return base / REGION_FILENAME


@lru_cache(maxsize=1)
def load_regions(path: str | None = None) -> tuple[Region, ...]:
    """시군구 목록을 읽는다. 시도·시군구 순으로 정렬한다.

    좌표가 문자열이므로 ``float`` 로 바꾼다. 세종특별자치시는 원본에 없어 여기서
    더한다 (:data:`SEJONG_COORDINATE`). **다른 좌표는 손대지 않는다.**
    """
    target = Path(path) if path is not None else region_data_path()
    if not target.is_file():
        raise RegionDataError(f"지역 데이터 파일이 없습니다: {target}")
    with target.open(encoding="utf-8") as stream:
        payload = json.load(stream)

    entries: dict[str, tuple[float, float]] = {}
    for key, value in payload.items():
        try:
            latitude = float(value["lat"])
            longitude = float(value["long"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RegionDataError(f"좌표를 읽지 못했습니다: {key} → {value!r}") from exc
        entries[str(key)] = (latitude, longitude)
    entries.setdefault(SEJONG_KEY, SEJONG_COORDINATE)  # 단층 자치시라 원본에 없다

    regions: list[Region] = []
    for key, (latitude, longitude) in entries.items():
        province, _, name = key.partition("/")
        if not province or not name:
            raise RegionDataError(f"'시도/시군구' 꼴이 아닙니다: {key!r}")
        regions.append(
            Region(
                key=key,
                province=province,
                name=name,
                latitude=latitude,
                longitude=longitude,
            )
        )
    return tuple(sorted(regions, key=lambda item: (item.province, item.name)))


def list_provinces(regions: tuple[Region, ...] | None = None) -> tuple[str, ...]:
    """시도 목록. 드롭다운 1단이다."""
    items = regions if regions is not None else load_regions()
    seen: list[str] = []
    for region in items:
        if region.province not in seen:
            seen.append(region.province)
    return tuple(seen)


def list_sigungu(province: str, regions: tuple[Region, ...] | None = None) -> tuple[Region, ...]:
    """한 시도의 시군구 목록. 드롭다운 2단이다."""
    items = regions if regions is not None else load_regions()
    found = tuple(region for region in items if region.province == province)
    if not found:
        raise RegionDataError(
            f"지역 데이터에 없는 시도입니다: {province!r} "
            f"(가능: {', '.join(list_provinces(items))})"
        )
    return found


def find_region(key: str, regions: tuple[Region, ...] | None = None) -> Region:
    """``"서울특별시/강남구"`` 로 찾는다."""
    items = regions if regions is not None else load_regions()
    for region in items:
        if region.key == key:
            return region
    raise RegionDataError(f"지역 데이터에 없는 키입니다: {key!r}")
