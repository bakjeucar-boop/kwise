"""기상 데이터 사전 취득분 (요구사항서 7.5).

Open-Meteo 는 호출 제한이 있어 실패할 때가 있다. 실패해도 검토가 멈추지 않도록
**전국 격자의 최근 3개년치를 미리 받아 ``data\\weather\\`` 에 둔다.**

조회 순서는 :func:`kwise.pv.weather.load_weather` 가 정한다.

    ① ``PROJECT_CACHE`` 캐시    반복 호출을 여기서 막는다
    ② Open-Meteo API           성공분은 ①에 캐시한다
    ③ 사전 취득분 (이 모듈)     **폴백했다는 사실을 결과에 표시한다**
    ④ 둘 다 없으면 중단        0 으로 계산하거나 인접 격자로 대체하지 않는다

저장 단위는 **격자 셀 × 연도** 다. 좌표는
:data:`kwise.pv.region.GRID_RESOLUTION_DEG` 로 반올림하므로 229개 시군구가
135개 셀로 뭉친다 (9차 세션의 캐시 키 규약과 같다).

용량은 ``int16 + 스케일 팩터`` 로 줄인다 (:data:`SCALE_FACTORS`). 일사는 0.1 W/m²,
기온·풍속은 0.01 단위로 양자화되며 계산에 쓰기 충분하다.

**적설(``snowfall``)은 저장하지 않는다.** pvlib 경로에서 쓰지 않는 값이라 호출
비용만 늘린다. 스키마 호환을 위해 읽을 때 0 으로 채우고 그 사실을 여기 적어 둔다.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from kwise.pv.region import GRID_RESOLUTION_DEG, Region, grid_cell, load_regions
from kwise.pv.weather import (
    ARCHIVE_URL,
    WEATHER_COLUMNS,
    WeatherRequest,
    WeatherUnavailableError,
    normalize_hourly,
)

__all__ = [
    "ARCHIVE_COLUMNS",
    "ARCHIVE_TIMEZONE",
    "ATTRIBUTION",
    "DEFAULT_ARCHIVE_END",
    "DEFAULT_ARCHIVE_START",
    "INDEX_FILENAME",
    "SCALE_FACTORS",
    "ArchiveCell",
    "ArchiveEntry",
    "ArchiveStatus",
    "FetchTask",
    "Pacer",
    "RetryPolicy",
    "WeatherHttpError",
    "archive_covers",
    "archive_path",
    "archive_root",
    "archive_status",
    "decode_frame",
    "encode_frame",
    "fetch_cell_year",
    "fetch_json_with_retry",
    "grid_cells_for",
    "load_archive",
    "pending_tasks",
    "read_index",
    "rebuild_index",
    "request_json",
    "store_cell_year",
    "unavailable_message",
]

# 사전 취득 기본 범위. 직전 3개년이다 (설정으로 바꿀 수 있다).
DEFAULT_ARCHIVE_START = dt.date(2023, 1, 1)
DEFAULT_ARCHIVE_END = dt.date(2025, 12, 31)

ARCHIVE_TIMEZONE = "Asia/Seoul"
INDEX_FILENAME = "index.json"
INDEX_SCHEMA_VERSION = "1"

# 출처 표기. README 와 산출물에 그대로 싣는다 (요구사항서 7.5).
ATTRIBUTION = (
    "기상 자료: Open-Meteo (https://open-meteo.com/) ERA5 재분석, CC BY 4.0. "
    "Copernicus Climate Change Service 정보를 포함한다."
)

# int16 저장용 스케일. 값 × 스케일을 반올림해 담고 읽을 때 되나눈다.
#   일사   0.1 W/m²   (최대 1,400 W/m² → 14,000, int16 한계 32,767 안)
#   기온   0.01 °C    (−40 ~ 45 °C → ±4,500)
#   풍속   0.01 m/s   (최대 100 m/s → 10,000)
SCALE_FACTORS: dict[str, float] = {
    "ghi": 10.0,
    "dni": 10.0,
    "dhi": 10.0,
    "temp_air": 100.0,
    "wind_speed": 100.0,
}
ARCHIVE_COLUMNS: tuple[str, ...] = tuple(SCALE_FACTORS)

_INT16_MIN, _INT16_MAX = -32768, 32767
_OPEN_METEO_VARIABLES = (
    "shortwave_radiation",
    "direct_normal_irradiance",
    "diffuse_radiation",
    "temperature_2m",
    "wind_speed_10m",
)


class WeatherHttpError(WeatherUnavailableError):
    """HTTP 계층의 실패. 재시도 여부를 판단하려고 상태 코드를 들고 있다.

    ``status`` 가 ``None`` 이면 연결 자체가 실패한 것이다 (타임아웃·DNS·프록시).
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------- 경로


def archive_root(root: Path | None = None) -> Path:
    """사전 취득분 뿌리.

    인자 → 환경변수 ``KWISE_WEATHER_DIR`` → ``<프로젝트>\\data\\weather`` 순이다.
    **다른 경로로 옮겨도 동작해야 한다** — 배포 시 별도 위치에 두는 경우가 있다.
    """
    if root is not None:
        return Path(root)
    override = os.environ.get("KWISE_WEATHER_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "data" / "weather"


def cell_key(cell: tuple[float, float]) -> str:
    """격자 셀의 문자열 키. 파일명과 색인에 같은 표기를 쓴다."""
    return f"{cell[0]:.4f}_{cell[1]:.4f}"


def archive_path(cell: tuple[float, float], year: int, *, root: Path | None = None) -> Path:
    """격자 셀·연도 하나의 parquet 경로."""
    return archive_root(root) / f"openmeteo_{cell_key(cell)}_{year}.parquet"


def index_path(root: Path | None = None) -> Path:
    return archive_root(root) / INDEX_FILENAME


# --------------------------------------------------------------------- 부호화


def encode_frame(hourly: pd.DataFrame) -> pd.DataFrame:
    """실수 시간별 기상을 ``int16`` 으로 담는다.

    스케일을 곱해 반올림하고 int16 범위로 자른다. 결측은 0 으로 둔다 —
    사전 취득분은 Open-Meteo 가 채운 완전한 시계열이라 결측이 나오지 않는다.
    """
    columns: dict[str, pd.Series] = {}
    for column, scale in SCALE_FACTORS.items():
        if column not in hourly.columns:
            raise ValueError(f"기상 자료에 {column} 열이 없습니다.")
        values = pd.to_numeric(hourly[column], errors="coerce").fillna(0.0) * scale
        columns[column] = values.round().clip(_INT16_MIN, _INT16_MAX).astype("int16")
    return pd.DataFrame(columns, index=hourly.index)


def decode_frame(stored: pd.DataFrame) -> pd.DataFrame:
    """``int16`` 저장분을 시뮬레이션이 쓰는 실수 열로 되돌린다.

    적설은 저장하지 않으므로 0 으로 채운다 (모듈 docstring 참조).
    """
    columns: dict[str, pd.Series] = {}
    for column, scale in SCALE_FACTORS.items():
        columns[column] = stored[column].astype("float64") / scale
    frame = pd.DataFrame(columns, index=stored.index)
    for column in WEATHER_COLUMNS:
        if column not in frame.columns:
            frame[column] = 0.0
    return frame[list(WEATHER_COLUMNS)]


# --------------------------------------------------------------------- 색인


@dataclass(frozen=True)
class ArchiveEntry:
    """격자 셀 한 해의 확보 현황."""

    latitude: float
    longitude: float
    year: int
    start: dt.date
    end: dt.date
    rows: int
    bytes: int
    filename: str

    @property
    def cell(self) -> tuple[float, float]:
        return (self.latitude, self.longitude)

    def covers(self, start: dt.date, end: dt.date) -> bool:
        """요청 구간을 이 연도분이 (부분이라도) 온전히 덮는지."""
        return self.start <= start and end <= self.end


@dataclass(frozen=True)
class ArchiveCell:
    """격자 셀 하나의 확보 현황. 8세션 UI 가 이 형태로 읽는다."""

    latitude: float
    longitude: float
    entries: tuple[ArchiveEntry, ...]

    @property
    def key(self) -> str:
        return cell_key((self.latitude, self.longitude))

    @property
    def years(self) -> tuple[int, ...]:
        return tuple(sorted(entry.year for entry in self.entries))

    @property
    def rows(self) -> int:
        return sum(entry.rows for entry in self.entries)

    @property
    def bytes(self) -> int:
        return sum(entry.bytes for entry in self.entries)

    @property
    def start(self) -> dt.date | None:
        return min((entry.start for entry in self.entries), default=None)

    @property
    def end(self) -> dt.date | None:
        return max((entry.end for entry in self.entries), default=None)

    def covers(self, start: dt.date, end: dt.date) -> bool:
        """연도 파일을 이어 붙여 ``start`` ~ ``end`` 를 빈틈없이 덮는지."""
        cursor = start
        for entry in sorted(self.entries, key=lambda item: item.start):
            if entry.start > cursor:
                return False
            if entry.end >= cursor:
                cursor = entry.end + dt.timedelta(days=1)
            if cursor > end:
                return True
        return cursor > end


@dataclass(frozen=True)
class ArchiveStatus:
    """확보 현황 전체 (요구사항서 7.5 — 현황 조회).

    8세션 UI 에서 "어느 지역·기간이 준비되어 있는가" 를 보여 주는 데 쓴다.
    """

    root: Path
    cells: tuple[ArchiveCell, ...]
    timezone: str = ARCHIVE_TIMEZONE
    attribution: str = ATTRIBUTION

    @property
    def cell_count(self) -> int:
        return len(self.cells)

    @property
    def years(self) -> tuple[int, ...]:
        return tuple(sorted({year for cell in self.cells for year in cell.years}))

    @property
    def bytes(self) -> int:
        return sum(cell.bytes for cell in self.cells)

    @property
    def start(self) -> dt.date | None:
        return min((cell.start for cell in self.cells if cell.start), default=None)

    @property
    def end(self) -> dt.date | None:
        return max((cell.end for cell in self.cells if cell.end), default=None)

    def find(self, latitude: float, longitude: float) -> ArchiveCell | None:
        """좌표가 속한 격자 셀의 현황. 없으면 ``None``."""
        key = cell_key(grid_cell(latitude, longitude))
        for cell in self.cells:
            if cell.key == key:
                return cell
        return None

    def covers(self, latitude: float, longitude: float, start: dt.date, end: dt.date) -> bool:
        cell = self.find(latitude, longitude)
        return cell is not None and cell.covers(start, end)

    @property
    def range_text(self) -> str:
        """``"2023-01 ~ 2025-12"`` 꼴. 안내 문구에 그대로 넣는다."""
        if self.start is None or self.end is None:
            return "없음"
        return f"{self.start:%Y-%m} ~ {self.end:%Y-%m}"

    def summary_text(self) -> str:
        if not self.cells:
            return f"사전 취득분이 없습니다 ({self.root})."
        return (
            f"격자 {self.cell_count}개 · {self.range_text} · "
            f"{self.bytes / 1_048_576:.1f} MB ({self.root})"
        )


def read_index(root: Path | None = None) -> dict[str, object]:
    """색인 파일을 읽는다. 없으면 빈 뼈대를 만든다."""
    target = index_path(root)
    if not target.is_file():
        return _empty_index()
    with target.open(encoding="utf-8") as stream:
        payload = json.load(stream)
    if not isinstance(payload, dict):
        raise WeatherUnavailableError(f"색인 파일 형식이 올바르지 않습니다: {target}")
    return payload


def write_index(payload: Mapping[str, object], root: Path | None = None) -> Path:
    target = index_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    return target


def _empty_index() -> dict[str, object]:
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "attribution": ATTRIBUTION,
        "timezone": ARCHIVE_TIMEZONE,
        "grid_resolution_deg": GRID_RESOLUTION_DEG,
        "columns": list(ARCHIVE_COLUMNS),
        "scale_factors": dict(SCALE_FACTORS),
        "note": (
            "격자 셀 × 연도 단위 parquet. 값은 int16 이며 scale_factors 로 되나눈다. "
            "적설은 계산에 쓰지 않아 저장하지 않는다."
        ),
        "cells": {},
    }


def archive_status(root: Path | None = None) -> ArchiveStatus:
    """확보 현황을 읽는다 (요구사항서 7.5 — 현황 조회 함수).

    색인이 없으면 빈 현황을 돌려준다. 파일이 있는데 색인만 없는 경우는
    :func:`rebuild_index` 로 되살린다.
    """
    base = archive_root(root)
    payload = read_index(base)
    raw_cells = payload.get("cells")
    cells: list[ArchiveCell] = []
    if isinstance(raw_cells, dict):
        for key, value in sorted(raw_cells.items()):
            if not isinstance(value, dict):
                continue
            latitude = float(value["latitude"])
            longitude = float(value["longitude"])
            years = value.get("years")
            entries: list[ArchiveEntry] = []
            if isinstance(years, dict):
                for year_key, item in sorted(years.items()):
                    if not isinstance(item, dict):
                        continue
                    entries.append(
                        ArchiveEntry(
                            latitude=latitude,
                            longitude=longitude,
                            year=int(year_key),
                            start=dt.date.fromisoformat(str(item["start"])),
                            end=dt.date.fromisoformat(str(item["end"])),
                            rows=int(item.get("rows", 0)),
                            bytes=int(item.get("bytes", 0)),
                            filename=str(item.get("file", "")),
                        )
                    )
            if entries:
                cells.append(
                    ArchiveCell(latitude=latitude, longitude=longitude, entries=tuple(entries))
                )
            _ = key
    return ArchiveStatus(
        root=base,
        cells=tuple(cells),
        timezone=str(payload.get("timezone", ARCHIVE_TIMEZONE)),
        attribution=str(payload.get("attribution", ATTRIBUTION)),
    )


def rebuild_index(root: Path | None = None) -> ArchiveStatus:
    """parquet 파일을 훑어 색인을 다시 만든다. 색인을 잃었을 때 쓴다."""
    base = archive_root(root)
    payload = _empty_index()
    cells: dict[str, dict[str, object]] = {}
    for path in sorted(base.glob("openmeteo_*.parquet")):
        stem = path.stem[len("openmeteo_") :]
        latitude_text, longitude_text, year_text = stem.rsplit("_", 2)
        frame = pd.read_parquet(path)
        entry = cells.setdefault(
            f"{latitude_text}_{longitude_text}",
            {
                "latitude": float(latitude_text),
                "longitude": float(longitude_text),
                "years": {},
            },
        )
        years = entry["years"]
        assert isinstance(years, dict)
        years[year_text] = {
            "file": path.name,
            "rows": len(frame),
            "bytes": int(path.stat().st_size),
            "start": frame.index.min().date().isoformat(),
            "end": frame.index.max().date().isoformat(),
        }
    payload["cells"] = cells
    write_index(payload, base)
    return archive_status(base)


# --------------------------------------------------------------------- 저장


def store_cell_year(
    hourly: pd.DataFrame,
    cell: tuple[float, float],
    year: int,
    *,
    root: Path | None = None,
) -> ArchiveEntry:
    """한 셀·한 해분을 저장하고 색인을 갱신한다."""
    if hourly.empty:
        raise WeatherUnavailableError(f"저장할 자료가 비어 있습니다: {cell} {year}")
    base = archive_root(root)
    path = archive_path(cell, year, root=base)
    path.parent.mkdir(parents=True, exist_ok=True)
    encode_frame(hourly).to_parquet(path, compression="zstd", compression_level=9)

    index = pd.DatetimeIndex(hourly.index)
    entry = ArchiveEntry(
        latitude=cell[0],
        longitude=cell[1],
        year=year,
        start=index.min().date(),
        end=index.max().date(),
        rows=len(hourly),
        bytes=int(path.stat().st_size),
        filename=path.name,
    )

    payload = read_index(base)
    raw_cells = payload.setdefault("cells", {})
    assert isinstance(raw_cells, dict)
    cell_entry = raw_cells.setdefault(
        cell_key(cell), {"latitude": cell[0], "longitude": cell[1], "years": {}}
    )
    years = cell_entry.setdefault("years", {})
    years[str(year)] = {
        "file": entry.filename,
        "rows": entry.rows,
        "bytes": entry.bytes,
        "start": entry.start.isoformat(),
        "end": entry.end.isoformat(),
    }
    write_index(payload, base)
    return entry


# --------------------------------------------------------------------- 조회


def archive_covers(request: WeatherRequest, *, root: Path | None = None) -> bool:
    """이 요청을 사전 취득분만으로 채울 수 있는가."""
    status = archive_status(root)
    return status.covers(request.latitude, request.longitude, request.start, request.end)


def load_archive(request: WeatherRequest, *, root: Path | None = None) -> pd.DataFrame:
    """사전 취득분에서 요청 구간을 잘라 낸다.

    없으면 :class:`WeatherUnavailableError` 를 던진다. **인접 격자로 대체하지
    않는다** — 다른 지점의 일사로 계산한 발전량은 근거가 없다.
    """
    base = archive_root(root)
    cell = request.grid_cell
    frames: list[pd.DataFrame] = []
    for year in range(request.start.year, request.end.year + 1):
        path = archive_path(cell, year, root=base)
        if not path.is_file():
            raise WeatherUnavailableError(
                f"사전 취득분에 {year}년 격자 "
                f"({cell[0]:.2f}, {cell[1]:.2f}) 자료가 없습니다: {path}"
            )
        frames.append(decode_frame(pd.read_parquet(path)))

    merged = pd.concat(frames).sort_index()
    merged = merged[~merged.index.duplicated(keep="last")]
    index = pd.DatetimeIndex(merged.index)
    if index.tz is None:
        index = index.tz_localize(request.timezone)
    elif str(index.tz) != request.timezone:
        index = index.tz_convert(request.timezone)
    merged.index = index

    start = pd.Timestamp(request.start, tz=request.timezone)
    end = pd.Timestamp(request.end, tz=request.timezone) + pd.Timedelta(days=1)
    sliced = merged.loc[(merged.index >= start) & (merged.index < end)]
    if sliced.empty:
        raise WeatherUnavailableError(
            f"사전 취득분에 요청 구간이 없습니다: {request.start} ~ {request.end}"
        )
    first, last = sliced.index.min(), sliced.index.max()
    if first > start or last < end - pd.Timedelta(hours=1):
        raise WeatherUnavailableError(
            "사전 취득분이 요청 구간을 온전히 덮지 못합니다: "
            f"요청 {request.start} ~ {request.end}, 확보 {first.date()} ~ {last.date()}"
        )
    return sliced


def unavailable_message(request: WeatherRequest, *, root: Path | None = None) -> str:
    """API 도 사전 취득분도 없을 때의 안내 (요구사항서 7.5).

    **예외를 삼키고 0 으로 계산하지 않는다.** 무엇이 없는지, 무엇을 하면 되는지
    적어서 중단한다.
    """
    status = archive_status(root)
    span = f"{request.start:%Y-%m} ~ {request.end:%Y-%m}"
    cell = request.grid_cell
    detail = ""
    if status.find(request.latitude, request.longitude) is None and status.cells:
        detail = f" 해당 격자({cell[0]:.2f}, {cell[1]:.2f})의 사전 취득분이 없습니다."
    return (
        f"Open-Meteo 접속에 실패했고, 요청하신 기간({span})이 "
        f"사전 취득 범위({status.range_text}) 밖입니다.{detail} "
        "네트워크를 확인하시거나 tools\\fetch_weather.py 로 해당 기간을 먼저 받으십시오."
    )


# --------------------------------------------------------------------- 취득


@dataclass(frozen=True)
class RetryPolicy:
    """호출 제한 대응 (요구사항서 7.5).

    429·5xx 와 연결 실패는 지수 백오프로 다시 부른다. 그 밖의 4xx 는 다시 불러도
    같은 답이 오므로 즉시 실패로 남긴다.
    """

    max_attempts: int = 5
    backoff_base_sec: float = 2.0
    backoff_max_sec: float = 120.0
    retry_statuses: frozenset[int] = frozenset({408, 425, 429, 500, 502, 503, 504})
    retry_on_connection_error: bool = True

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError(f"최대 시도 횟수는 1 이상이어야 합니다: {self.max_attempts}")
        if self.backoff_base_sec < 0:
            raise ValueError(f"백오프 기준은 음수일 수 없습니다: {self.backoff_base_sec}")

    def should_retry(self, error: WeatherHttpError) -> bool:
        if error.status is None:
            return self.retry_on_connection_error
        return error.status in self.retry_statuses

    def delay(self, attempt: int) -> float:
        """``attempt`` 번째 시도가 실패한 뒤 기다릴 초. 1 부터 센다."""
        return min(self.backoff_base_sec * (2 ** (attempt - 1)), self.backoff_max_sec)


DEFAULT_RETRY = RetryPolicy()

Transport = Callable[[str, dict[str, str], float], Mapping[str, object]]


def request_json(url: str, params: dict[str, str], timeout: float) -> Mapping[str, object]:
    """상태 코드를 남기는 HTTP GET. 재시도 판정에 필요하다."""
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - requests 는 필수 의존성이다
        raise WeatherHttpError("requests 가 없습니다.") from exc
    try:
        response = requests.get(url, params=params, timeout=timeout)
    except Exception as exc:
        raise WeatherHttpError(f"연결 실패: {exc}") from exc
    if response.status_code >= 400:
        body = response.text[:200].replace("\n", " ")
        raise WeatherHttpError(f"HTTP {response.status_code}: {body}", status=response.status_code)
    try:
        return dict(response.json())
    except Exception as exc:
        raise WeatherHttpError(f"응답을 JSON 으로 읽지 못했습니다: {exc}") from exc


def fetch_json_with_retry(
    url: str,
    params: dict[str, str],
    *,
    policy: RetryPolicy = DEFAULT_RETRY,
    transport: Transport = request_json,
    sleep: Callable[[float], None] = time.sleep,
    timeout: float = 120.0,
    on_retry: Callable[[int, float, WeatherHttpError], None] | None = None,
) -> Mapping[str, object]:
    """지수 백오프로 다시 부른다. 최대 횟수를 넘기면 마지막 오류를 던진다."""
    last: WeatherHttpError | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return transport(url, params, timeout)
        except WeatherHttpError as exc:
            last = exc
            if attempt >= policy.max_attempts or not policy.should_retry(exc):
                raise
            delay = policy.delay(attempt)
            if on_retry is not None:
                on_retry(attempt, delay, exc)
            sleep(delay)
    raise last if last is not None else WeatherHttpError("취득에 실패했습니다.")


@dataclass
class Pacer:
    """호출 간 최소 간격을 지킨다 (요구사항서 7.5).

    Open-Meteo 무료 사용은 분·시간 단위 상한이 있다. 간격을 두는 것이 429 를
    맞고 백오프하는 것보다 전체적으로 빠르다.
    """

    min_interval_sec: float = 1.0
    sleep: Callable[[float], None] = field(default=time.sleep)
    clock: Callable[[], float] = field(default=time.monotonic)
    _last: float | None = field(default=None, init=False, repr=False)

    def wait(self) -> float:
        """필요한 만큼 쉬고 실제로 쉰 시간을 돌려준다."""
        now = self.clock()
        waited = 0.0
        if self._last is not None:
            remaining = self.min_interval_sec - (now - self._last)
            if remaining > 0:
                self.sleep(remaining)
                waited = remaining
                now = self.clock()
        self._last = now
        return waited


def _archive_params(
    cell: tuple[float, float],
    start: dt.date,
    end: dt.date,
    timezone: str,
) -> dict[str, str]:
    return {
        "latitude": f"{cell[0]}",
        "longitude": f"{cell[1]}",
        "start_date": f"{start:%Y-%m-%d}",
        "end_date": f"{end:%Y-%m-%d}",
        "hourly": ",".join(_OPEN_METEO_VARIABLES),
        "timezone": timezone,
    }


def fetch_cell_year(
    cell: tuple[float, float],
    year: int,
    *,
    start: dt.date | None = None,
    end: dt.date | None = None,
    timezone: str = ARCHIVE_TIMEZONE,
    policy: RetryPolicy = DEFAULT_RETRY,
    transport: Transport = request_json,
    sleep: Callable[[float], None] = time.sleep,
    timeout: float = 120.0,
    on_retry: Callable[[int, float, WeatherHttpError], None] | None = None,
) -> pd.DataFrame:
    """격자 셀 한 해분을 받는다. 저장 열(:data:`ARCHIVE_COLUMNS`)만 남긴다."""
    first = max(start, dt.date(year, 1, 1)) if start else dt.date(year, 1, 1)
    last = min(end, dt.date(year, 12, 31)) if end else dt.date(year, 12, 31)
    if last < first:
        raise ValueError(f"{year}년에 걸치는 구간이 없습니다: {start} ~ {end}")
    payload = fetch_json_with_retry(
        ARCHIVE_URL,
        _archive_params(cell, first, last, timezone),
        policy=policy,
        transport=transport,
        sleep=sleep,
        timeout=timeout,
        on_retry=on_retry,
    )
    frame = normalize_hourly(payload, timezone)
    return frame[list(ARCHIVE_COLUMNS)]


# --------------------------------------------------------------------- 작업 목록


def grid_cells_for(regions: Iterable[Region]) -> tuple[tuple[float, float], ...]:
    """시군구 목록을 격자 셀로 접는다. **중복이 여기서 사라진다.**

    229개 시군구가 0.25° 격자에서 135개 셀이 된다. 서울 25개 구는 4셀이다.
    """
    seen: dict[str, tuple[float, float]] = {}
    for region in regions:
        cell = region.grid_cell()
        seen.setdefault(cell_key(cell), cell)
    return tuple(seen[key] for key in sorted(seen))


def national_cells() -> tuple[tuple[float, float], ...]:
    """전국 격자 셀."""
    return grid_cells_for(load_regions())


@dataclass(frozen=True)
class FetchTask:
    """받아야 할 셀·연도 하나."""

    cell: tuple[float, float]
    year: int
    start: dt.date
    end: dt.date

    @property
    def key(self) -> str:
        return f"{cell_key(self.cell)}_{self.year}"


def pending_tasks(
    cells: Sequence[tuple[float, float]],
    start: dt.date,
    end: dt.date,
    *,
    root: Path | None = None,
    refresh: bool = False,
) -> tuple[tuple[FetchTask, ...], tuple[FetchTask, ...]]:
    """받을 것과 건너뛸 것을 가른다. **증분 갱신의 근거다.**

    Returns:
        (받을 작업, 이미 확보되어 건너뛸 작업)
    """
    if end < start:
        raise ValueError(f"종료일이 시작일보다 빠릅니다: {start} ~ {end}")
    status = archive_status(root)
    todo: list[FetchTask] = []
    done: list[FetchTask] = []
    for cell in cells:
        stored = status.find(*cell)
        for year in range(start.year, end.year + 1):
            first = max(start, dt.date(year, 1, 1))
            last = min(end, dt.date(year, 12, 31))
            task = FetchTask(cell=cell, year=year, start=first, end=last)
            entry = None
            if stored is not None:
                entry = next((item for item in stored.entries if item.year == year), None)
            if not refresh and entry is not None and entry.covers(first, last):
                done.append(task)
            else:
                todo.append(task)
    return tuple(todo), tuple(done)
