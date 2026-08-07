"""기상 데이터 취득과 캐시 (요구사항서 7.4).

Open-Meteo 에서 시간별 일사·기온·풍속을 받아 ``PROJECT_CACHE`` 경로에 parquet 으로
캐시한다. 같은 좌표·기간을 다시 물으면 네트워크를 타지 않는다.

**시각 규약** — Open-Meteo 의 시간별 라벨은 구간의 **시작**이다. 값이 대표하는
순시각은 라벨 + 30분으로 본다 (:class:`WeatherLabel`). 실측 발전량과 대조했을 때
발전 곡선이 통째로 30분 밀려 보이면 이 규약부터 의심한다.

프록시 뒤에서 Python HTTPS 가 막히는 사례가 있어 ``requests`` 실패 시
PowerShell 폴백을 둔다 (reference\\mg_weather_openmeteo.py 에서 검증된 경로).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd

__all__ = [
    "ARCHIVE_URL",
    "FORECAST_URL",
    "WEATHER_COLUMNS",
    "WeatherData",
    "WeatherLabel",
    "WeatherRequest",
    "WeatherUnavailableError",
    "cache_root",
    "fetch_open_meteo",
    "load_weather",
    "weather_cache_path",
]

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# 시뮬레이션이 쓰는 표준 컬럼. 단위는 SI 로 맞춘다 (풍속 m/s).
WEATHER_COLUMNS: tuple[str, ...] = (
    "ghi",
    "dni",
    "dhi",
    "temp_air",
    "wind_speed",
    "snowfall",
)
_OPEN_METEO_HOURLY = (
    "temperature_2m",
    "wind_speed_10m",
    "shortwave_radiation",
    "direct_normal_irradiance",
    "diffuse_radiation",
    "snowfall",
)
_COLUMN_MAP = {
    "shortwave_radiation": "ghi",
    "direct_normal_irradiance": "dni",
    "diffuse_radiation": "dhi",
    "temperature_2m": "temp_air",
    "wind_speed_10m": "wind_speed",
    "snowfall": "snowfall",
}
_KMH_TO_MS = 1 / 3.6


class WeatherUnavailableError(RuntimeError):
    """기상 데이터를 캐시에서도 네트워크에서도 얻지 못했을 때 발생한다."""


class WeatherLabel(StrEnum):
    """시간별 값의 라벨 규약. 대표 순시각을 정하는 데 쓴다."""

    INTERVAL_START = "interval_start"  # Open-Meteo 기본. 대표 순시각 = 라벨 + 30분
    INTERVAL_END = "interval_end"  # 대표 순시각 = 라벨 − 30분
    INSTANT = "instant"  # 라벨이 곧 순시각

    def offset(self, hours: float = 1.0) -> pd.Timedelta:
        if self is WeatherLabel.INTERVAL_START:
            return pd.Timedelta(hours=hours / 2)
        if self is WeatherLabel.INTERVAL_END:
            return -pd.Timedelta(hours=hours / 2)
        return pd.Timedelta(0)


@dataclass(frozen=True)
class WeatherRequest:
    """좌표와 기간. 캐시 키가 된다."""

    latitude: float
    longitude: float
    start: dt.date
    end: dt.date
    timezone: str = "Asia/Seoul"

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"종료일이 시작일보다 빠릅니다: {self.start} ~ {self.end}")

    @property
    def cache_name(self) -> str:
        return (
            f"openmeteo_{self.latitude:.4f}_{self.longitude:.4f}"
            f"_{self.start:%Y%m%d}_{self.end:%Y%m%d}_{self.timezone.replace('/', '-')}.parquet"
        )

    @classmethod
    def for_index(
        cls,
        index: pd.DatetimeIndex,
        latitude: float,
        longitude: float,
        *,
        timezone: str = "Asia/Seoul",
        pad_days: int = 1,
    ) -> WeatherRequest:
        """부하 시계열이 덮는 기간을 앞뒤로 조금 넓혀 요청한다.

        보간 경계에서 값이 비지 않도록 하루씩 여유를 둔다.
        """
        pad = pd.Timedelta(days=pad_days)
        return cls(
            latitude=latitude,
            longitude=longitude,
            start=(index.min() - pad).date(),
            end=(index.max() + pad).date(),
            timezone=timezone,
        )


@dataclass(frozen=True, eq=False)
class WeatherData:
    """시간별 기상. 인덱스는 tz-aware 지방시이며 라벨 규약은 ``label`` 이 말한다."""

    hourly: pd.DataFrame
    request: WeatherRequest
    label: WeatherLabel = WeatherLabel.INTERVAL_START
    source: str = "cache"
    path: Path | None = None

    @property
    def instants(self) -> pd.DatetimeIndex:
        """각 시간 값이 대표하는 순시각. 태양 위치를 이 시각에서 계산한다."""
        return pd.DatetimeIndex(self.hourly.index + self.label.offset())


# --------------------------------------------------------------------- 캐시


def cache_root() -> Path:
    """캐시 뿌리. ``PROJECT_CACHE`` 환경변수, 없으면 ``.\\cache``."""
    override = os.environ.get("PROJECT_CACHE")
    return Path(override) if override else Path("cache")


def weather_cache_path(request: WeatherRequest, *, root: Path | None = None) -> Path:
    base = root if root is not None else cache_root()
    return base / "weather" / request.cache_name


# --------------------------------------------------------------------- 취득


def _open_meteo_params(request: WeatherRequest) -> dict[str, str]:
    return {
        "latitude": f"{request.latitude}",
        "longitude": f"{request.longitude}",
        "start_date": f"{request.start:%Y-%m-%d}",
        "end_date": f"{request.end:%Y-%m-%d}",
        "hourly": ",".join(_OPEN_METEO_HOURLY),
        "timezone": request.timezone,
    }


def _get_json(url: str, params: dict[str, str], timeout: float) -> Mapping[str, object]:
    """requests 로 먼저, 막히면 PowerShell 로 받는다.

    프록시 환경에서 Python HTTPS 만 막히는 사례가 있다.
    """
    errors: list[str] = []
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - requests 는 필수 의존성이다
        raise WeatherUnavailableError("requests 가 없습니다.") from exc

    for trust_env in (False, True):
        session = requests.Session()
        session.trust_env = trust_env
        try:
            response = session.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return dict(response.json())
        except Exception as exc:
            errors.append(f"{'proxy' if trust_env else 'direct'}: {exc}")

    if sys.platform == "win32":
        try:
            return _get_json_via_powershell(url, params, timeout)
        except Exception as exc:
            errors.append(f"powershell: {exc}")

    raise WeatherUnavailableError("Open-Meteo 연결에 실패했습니다. " + " | ".join(errors))


def _get_json_via_powershell(
    url: str, params: dict[str, str], timeout: float
) -> Mapping[str, object]:
    full_url = f"{url}?{urlencode(params)}".replace("'", "''")
    command = (
        "$ProgressPreference='SilentlyContinue'; "
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
        f"(Invoke-WebRequest -UseBasicParsing -Uri '{full_url}' "
        f"-TimeoutSec {int(timeout)}).Content"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout + 10,
        check=False,
    )
    if completed.returncode != 0:
        raise WeatherUnavailableError(
            (completed.stderr or completed.stdout or "PowerShell 요청 실패").strip()
        )
    return dict(json.loads(completed.stdout))


def normalize_hourly(payload: Mapping[str, object], timezone: str) -> pd.DataFrame:
    """Open-Meteo 응답을 표준 컬럼으로 바꾼다. 풍속은 m/s 로 환산한다."""
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict) or "time" not in hourly:
        raise WeatherUnavailableError("Open-Meteo 응답에 hourly 자료가 없습니다.")
    frame = pd.DataFrame(hourly)
    index = pd.to_datetime(frame["time"]).dt.tz_localize(timezone)
    frame = frame.drop(columns=["time"]).set_index(pd.DatetimeIndex(index))
    frame.index.name = "time"
    frame = frame.rename(columns=_COLUMN_MAP)
    for column in WEATHER_COLUMNS:
        if column not in frame.columns:
            frame[column] = 0.0
    frame["wind_speed"] = pd.to_numeric(frame["wind_speed"], errors="coerce") * _KMH_TO_MS
    for column in WEATHER_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame[list(WEATHER_COLUMNS)].astype(float).fillna(0.0).sort_index()


def fetch_open_meteo(request: WeatherRequest, *, timeout: float = 30.0) -> pd.DataFrame:
    """실측(archive) 우선, 오늘 이후 구간은 예보로 채운다."""
    today = pd.Timestamp.now(tz=request.timezone).date()
    parts: list[pd.DataFrame] = []

    archive_end = min(request.end, today - dt.timedelta(days=1))
    if request.start <= archive_end:
        params = _open_meteo_params(request) | {"end_date": f"{archive_end:%Y-%m-%d}"}
        parts.append(normalize_hourly(_get_json(ARCHIVE_URL, params, timeout), request.timezone))

    forecast_start = max(request.start, today)
    if forecast_start <= request.end:
        params = _open_meteo_params(request) | {"start_date": f"{forecast_start:%Y-%m-%d}"}
        parts.append(normalize_hourly(_get_json(FORECAST_URL, params, timeout), request.timezone))

    if not parts:
        raise WeatherUnavailableError(f"요청 기간이 비어 있습니다: {request}")
    merged = pd.concat(parts).sort_index()
    return merged[~merged.index.duplicated(keep="last")]


# --------------------------------------------------------------------- 진입점


def load_weather(
    request: WeatherRequest,
    *,
    fetch: Callable[[WeatherRequest], pd.DataFrame] | None = None,
    cache_dir: Path | None = None,
    use_cache: bool = True,
    refresh: bool = False,
    label: WeatherLabel = WeatherLabel.INTERVAL_START,
) -> WeatherData:
    """캐시 우선으로 기상 데이터를 얻는다.

    Args:
        fetch: 취득 함수. 기본은 Open-Meteo. 테스트는 여기에 합성 생성기를 넣어
            네트워크를 타지 않는다.
        refresh: True 면 캐시를 무시하고 다시 받는다.
    """
    path = weather_cache_path(request, root=cache_dir)
    if use_cache and not refresh and path.is_file():
        hourly = pd.read_parquet(path)
        return WeatherData(hourly=hourly, request=request, label=label, source="cache", path=path)

    fetcher = fetch if fetch is not None else fetch_open_meteo
    hourly = fetcher(request)
    if hourly.empty:
        raise WeatherUnavailableError(f"받은 기상 자료가 비어 있습니다: {request}")

    stored: Path | None = None
    if use_cache:
        path.parent.mkdir(parents=True, exist_ok=True)
        hourly.to_parquet(path)
        stored = path
    return WeatherData(hourly=hourly, request=request, label=label, source="network", path=stored)
