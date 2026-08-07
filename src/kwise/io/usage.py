"""사용량 데이터 로더 (요구사항서 3.1).

한전 사이버지점에서 내려받는 CSV·Excel 을 읽어 그리드에 정렬된 kW 시리즈와
메타데이터를 만든다.

라벨 규약 — **검침일 라벨은 구간의 끝이다.**
    하루가 ``00:15`` 로 시작해 ``24:00`` 으로 끝나 96행이 된다. ``24:00`` 은
    다음 날 ``00:00`` 으로 옮긴다. 계절·시간대·월 귀속은 라벨에서 한 구간을 뺀
    시각으로 판정해야 하며, 그 판정은 tariff 모듈이 맡는다. 여기서는 라벨을
    그대로 인덱스로 둔다.

그리드 이탈 행 (요구사항서 4.3)
    15분 그리드를 벗어난 부분 적산 행은 kW 수요 산정에서 **제외**하고
    kWh 사용량 합계에는 **포함**한다. 값을 버리지 않고 ``UsageData.off_grid``
    에 보존한다.
"""

from __future__ import annotations

import csv
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from io import BytesIO, StringIO
from pathlib import Path
from typing import ClassVar

import pandas as pd

from kwise.io.columns import (
    USAGE_DATE_COLUMN_CANDIDATES,
    USAGE_ENERGY_COLUMN_CANDIDATES,
    ColumnDetection,
    ColumnDetectionError,
    detect_usage_columns,
    match_usage_column,
    normalize_column_name,
)

__all__ = [
    "DEFAULT_ENCODINGS",
    "SUPPORTED_INTERVALS",
    "USAGE_DATE_COLUMN_CANDIDATES",
    "USAGE_ENERGY_COLUMN_CANDIDATES",
    "ColumnDetection",
    "ColumnDetectionError",
    "EnergySeries",
    "EnergyToDemandError",
    "GridKwhSeries",
    "OffGridEnergyError",
    "UsageData",
    "UsageLoadError",
    "UsageMeta",
    "count_hour24",
    "detect_grid_phase_seconds",
    "detect_interval_minutes",
    "detect_usage_columns",
    "load_usage",
    "load_usage_bytes",
    "match_usage_column",
    "normalize_column_name",
    "parse_usage_datetime",
    "parse_usage_energy",
    "slot_start",
]

# 인코딩 4종 순차 시도. 인코딩이 어긋나도 예외 없이 깨진 헤더가 나오는 경우가 있으므로
# 컬럼 매칭 성공까지 확인해야 그 인코딩을 채택한다.
DEFAULT_ENCODINGS: tuple[str, ...] = ("utf-8-sig", "cp949", "euc-kr", "utf-8")

SUPPORTED_INTERVALS: tuple[int, ...] = (15, 60)

_HOUR24_PATTERN = r"24:00(?::00)?$"
_CSV_SUFFIXES = frozenset({".csv", ".txt"})
_EXCEL_SUFFIXES = frozenset({".xls", ".xlsx", ".xlsm"})


class UsageLoadError(RuntimeError):
    """사용량 파일을 읽지 못했을 때 발생한다."""


@dataclass(frozen=True)
class UsageMeta:
    """로딩 결과 메타데이터. 품질 검사(2세션)와 산출물 추적성의 입력이 된다."""

    source_name: str
    encoding: str | None
    date_column: str
    energy_column: str
    columns: ColumnDetection

    interval_minutes: int
    grid_phase_seconds: int
    start: pd.Timestamp
    end: pd.Timestamp
    period_days: float

    raw_rows: int
    valid_rows: int
    off_grid_rows: int
    duplicate_rows: int
    invalid_datetime_rows: int
    invalid_energy_rows: int
    negative_energy_rows: int
    hour24_rows: int

    expected_rows: int
    missing_rows: int
    missing_ratio: float

    total_kwh: float
    off_grid_kwh: float
    max_demand_kw: float
    max_demand_at: pd.Timestamp
    mean_kw: float
    load_factor: float

    warnings: tuple[str, ...] = field(default=())

    @property
    def slot_hours(self) -> float:
        """한 구간의 시간 길이 (h)."""
        return self.interval_minutes / 60.0


class OffGridEnergyError(RuntimeError):
    """그리드 이탈분을 빠뜨린 채 총 사용량을 구하려 할 때 발생한다."""


class GridKwhSeries(pd.Series):  # type: ignore[misc]
    """그리드 정렬 사용량 시리즈. **총합을 직접 구하는 것을 막는다.**

    ``kwh_grid.sum()`` 은 그리드 이탈(부분 적산) 행의 kWh 를 빠뜨린다. 샘플에서
    그 차이는 43.2 kWh, 전체의 0.0002% 라 눈으로는 잡히지 않는다. 요금 엔진에서
    이 실수가 나면 발견이 매우 어려우므로 합계 경로 자체를 막고
    :attr:`UsageData.total_kwh` 또는 :meth:`UsageData.energy_kwh` 로 유도한다.

    슬라이스에도 ``off_grid_kwh`` 가 따라붙으므로 월별·시간대별 부분합 역시 막힌다.
    그리드 정렬분만 필요한 것이 확실하면 ``sum(grid_only=True)`` 로 뜻을 밝힌다.
    """

    _metadata: ClassVar[list[str]] = ["off_grid_kwh"]

    @property
    def _constructor(self) -> type[GridKwhSeries]:
        return GridKwhSeries

    def sum(self, *args: object, grid_only: bool = False, **kwargs: object) -> float:
        off_grid_kwh = float(getattr(self, "off_grid_kwh", 0.0) or 0.0)
        if off_grid_kwh and not grid_only:
            raise OffGridEnergyError(
                f"kwh_grid.sum() 은 그리드 이탈 {off_grid_kwh:,.2f} kWh 를 빠뜨립니다. "
                "총 사용량은 UsageData.total_kwh, 시계열 합계는 "
                "UsageData.energy_kwh() 를 쓰십시오 (요구사항서 4.3). "
                "그리드 정렬분만 필요하면 sum(grid_only=True) 로 밝히십시오."
            )
        return float(super().sum(*args, **kwargs))


class EnergyToDemandError(RuntimeError):
    """사용량 시계열을 수요(kW)로 환산하려 할 때 발생한다."""


class EnergySeries(pd.Series):  # type: ignore[misc]
    """요금 계산용 사용량 시리즈. **kW 환산을 막는다.**

    :meth:`UsageData.energy_kwh` 는 그리드 이탈 행의 kWh 를 그 행이 속한 구간에
    얹는다. 그 구간은 위상이 달라(14분 적산 등) 시간으로 나누면 수요가 왜곡된다.
    43.2 kWh ÷ 14분 = 185 kW 인데 15분 환산하면 172.8 kW 가 되는 식이다.
    수요가 필요하면 언제나 :attr:`UsageData.kw` 를 쓴다.

    막는 것은 kW 로 가는 두 경로뿐이다 — 구간 환산계수를 곱하거나
    구간 시간으로 나누는 것. 단가를 곱하는 등 정상적인 연산은 그대로 된다.
    """

    _metadata: ClassVar[list[str]] = ["kw_factor"]

    @property
    def _constructor(self) -> type[EnergySeries]:
        return EnergySeries

    def _guard(self, other: object, *, dividing: bool) -> None:
        factor = float(getattr(self, "kw_factor", 0.0) or 0.0)
        if factor <= 1.0 or not isinstance(other, int | float) or isinstance(other, bool):
            return  # 1시간 간격은 kWh 와 kW 가 같은 수라 환산이랄 것이 없다
        target = 1.0 / factor if dividing else factor
        if math.isclose(float(other), target, rel_tol=1e-9):
            raise EnergyToDemandError(
                f"energy_kwh() 를 kW 로 환산하지 마십시오 "
                f"({'÷ ' + format(target, 'g') if dividing else '× ' + format(target, 'g')}). "
                "그리드 이탈분이 얹힌 구간은 위상이 달라 수요가 왜곡됩니다. "
                "수요는 UsageData.kw 를 쓰십시오 (요구사항서 4.3)."
            )

    def to_kw(self) -> pd.Series:
        raise EnergyToDemandError(
            "energy_kwh() 는 kW 로 바꿀 수 없습니다. UsageData.kw 를 쓰십시오 (요구사항서 4.3)."
        )

    def __mul__(self, other: object) -> EnergySeries:
        self._guard(other, dividing=False)
        return super().__mul__(other)

    def __rmul__(self, other: object) -> EnergySeries:
        self._guard(other, dividing=False)
        return super().__rmul__(other)

    def __truediv__(self, other: object) -> EnergySeries:
        self._guard(other, dividing=True)
        return super().__truediv__(other)


@dataclass(frozen=True, eq=False)
class UsageData:
    """로더의 반환값.

    Attributes:
        kw: tz-naive DatetimeIndex 의 15분(또는 1시간) 평균 수요. 결측은 NaN.
            그리드 이탈 행은 포함하지 않는다.
        kwh_grid: 같은 인덱스의 **그리드 정렬분** 사용량. 이탈 행이 빠져 있으므로
            총합은 :attr:`total_kwh` 나 :meth:`energy_kwh` 로 구한다.
        off_grid: 그리드를 벗어난 행 (``timestamp``, ``kwh``). 값까지 보존한다.
        meta: 메타데이터.
    """

    kw: pd.Series
    kwh_grid: GridKwhSeries
    off_grid: pd.DataFrame
    meta: UsageMeta

    @property
    def total_kwh(self) -> float:
        """그리드 이탈 행을 포함한 총 사용량. 요금 계산의 기준이다."""
        return self.meta.total_kwh

    def energy_kwh(self, *, include_off_grid: bool = True) -> EnergySeries:
        """요금 계산용 사용량 시계열.

        그리드 이탈 행의 kWh 를 그 행이 속한 구간의 라벨에 얹어 돌려준다.
        부분 적산 행은 애초에 그 구간의 일부를 잰 값이므로 귀속이 맞다.
        합계는 :attr:`total_kwh` 와 같다.

        **kW 로 환산할 수 없다.** 이탈분이 얹힌 구간은 위상이 달라 수요가 왜곡되므로
        :class:`EnergySeries` 가 환산 경로를 막는다 (요구사항서 4.3).
        """
        energy = EnergySeries(
            self.kwh_grid.to_numpy(dtype=float),
            index=self.kwh_grid.index.copy(),
            name="kwh",
        )
        energy.kw_factor = 60.0 / self.meta.interval_minutes
        if not include_off_grid or self.off_grid.empty:
            return energy

        interval = pd.Timedelta(minutes=self.meta.interval_minutes)
        # 라벨은 구간 끝이다. 이탈 행은 자기를 품는 구간의 라벨로 올림한다.
        labels = self.off_grid["timestamp"].dt.ceil(interval)
        extra = self.off_grid.groupby(labels)["kwh"].sum()
        extra = extra[extra.index.isin(energy.index)]
        if extra.empty:
            return energy
        base = energy.reindex(extra.index).fillna(0.0)
        energy.loc[extra.index] = base + extra
        return energy

    def __getattr__(self, name: str) -> object:
        # 1세션의 이름을 그대로 쓰다가 이탈분을 빠뜨리는 것을 막는다.
        if name == "kwh":
            raise AttributeError(
                "UsageData.kwh 는 kwh_grid 로 바뀌었습니다. 총 사용량은 total_kwh, "
                "요금 계산용 시계열은 energy_kwh() 를 쓰십시오 (요구사항서 4.3)."
            )
        raise AttributeError(name)


# --------------------------------------------------------------------- 파싱


def _hour24_mask(series: pd.Series) -> pd.Series:
    raw = series.astype(str).str.strip()
    return raw.str.contains(_HOUR24_PATTERN, regex=True, na=False)


def count_hour24(series: pd.Series) -> int:
    """``24:00`` 표기 행 수. 문자열 컬럼이 아니면 0."""
    if pd.api.types.is_datetime64_any_dtype(series) or pd.api.types.is_numeric_dtype(series):
        return 0
    return int(_hour24_mask(series).sum())


def parse_usage_datetime(series: pd.Series) -> pd.Series:
    """검침일 컬럼을 tz-naive datetime 으로 파싱한다.

    ``24:00`` 은 다음 날 ``00:00`` 으로 옮긴다. 숫자형은 Excel 일련번호로 본다.
    reference\\streamlit_app.py 에서 실측 검증된 로직이다.
    """
    if pd.api.types.is_datetime64_any_dtype(series):
        parsed = pd.to_datetime(series, errors="coerce")
        return _drop_timezone(parsed)
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_datetime(series, unit="D", origin="1899-12-30", errors="coerce")

    raw = series.astype(str).str.strip()
    mask24 = raw.str.contains(_HOUR24_PATTERN, regex=True, na=False)
    normalized = raw.str.replace(_HOUR24_PATTERN, "00:00", regex=True)
    parsed = pd.to_datetime(normalized, errors="coerce")
    parsed = _drop_timezone(parsed)
    return parsed + pd.to_timedelta(mask24.astype(int), unit="D")


def _drop_timezone(series: pd.Series) -> pd.Series:
    """tz-aware 면 tz 를 해제한다. Excel 출력과 인덱스 비교를 위해 항상 tz-naive 로 둔다."""
    if isinstance(series.dtype, pd.DatetimeTZDtype):
        return series.dt.tz_localize(None)
    return series


def parse_usage_energy(series: pd.Series) -> pd.Series:
    """전력량 컬럼을 실수로 파싱한다. 천단위 쉼표와 단위 문자를 걷어낸다."""
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")
    cleaned = (
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace(r"[^0-9.\-]", "", regex=True)
    )
    return pd.to_numeric(cleaned, errors="coerce")


# --------------------------------------------------------------------- 그리드 판정


def detect_interval_minutes(timestamps: pd.Series) -> int:
    """연속 검침 간격의 최빈값으로 15분·1시간을 판정한다.

    결측 구간 때문에 간격이 벌어진 행이 섞여도 최빈값은 영향을 받지 않는다.
    지원하지 않는 값이 나오면 가장 가까운 지원 간격으로 스냅한다.
    """
    ordered = timestamps.dropna().sort_values()
    diffs = ordered.diff().dropna()
    minutes = (diffs.dt.total_seconds() / 60.0).round().astype("int64")
    minutes = minutes[minutes > 0]
    if minutes.empty:
        raise UsageLoadError("검침 시각이 하나뿐이라 간격을 판정할 수 없습니다.")
    modal = int(minutes.mode().iloc[0])
    if modal in SUPPORTED_INTERVALS:
        return modal
    return min(SUPPORTED_INTERVALS, key=lambda candidate: abs(candidate - modal))


def detect_grid_phase_seconds(timestamps: pd.Series, interval_minutes: int) -> int:
    """그리드 위상을 최빈값으로 판정한다.

    첫 행이 비정규 시각이어도 전체가 어긋나지 않게 하려는 것이다.
    반환값은 하루 시작으로부터의 초 단위 잔여(0 이면 정시 정렬).
    """
    ordered = timestamps.dropna()
    if ordered.empty:
        raise UsageLoadError("유효한 검침 시각이 없습니다.")
    second_of_day = (ordered.dt.hour * 3600 + ordered.dt.minute * 60 + ordered.dt.second).astype(
        "int64"
    )
    phase = second_of_day % (interval_minutes * 60)
    return int(phase.mode().iloc[0])


def slot_start(index: pd.DatetimeIndex, interval_minutes: int) -> pd.DatetimeIndex:
    """라벨(구간 끝)에서 구간 시작 시각을 만든다.

    계절·시간대·월·요일 귀속은 모두 이 시각으로 판정해야 한다. ``15:00`` 라벨은
    ``14:45~15:00`` 사용량이므로 중간부하이고, 첫 최대부하 슬롯은 ``15:15`` 이다.
    quality·tariff·pv 가 모두 이 함수를 써야 15분 어긋남이 생기지 않는다.
    """
    return index - pd.Timedelta(minutes=interval_minutes)


# --------------------------------------------------------------------- 파일 읽기


def _csv_column_count(data: bytes, encoding: str, *, sample_lines: int = 40) -> int:
    """앞부분을 훑어 열 개수의 최대값을 센다.

    제목 행은 한 칸짜리라 뒤 행들과 열 수가 다르다. 그대로 읽으면 pandas 가
    ``ParserError`` 를 낸다. 열 이름을 넉넉히 미리 주어 들쭉날쭉한 행을 견딘다.
    """
    text = data.decode(encoding)
    reader = csv.reader(StringIO(text))
    widths = [len(row) for _, row in zip(range(sample_lines), reader, strict=False)]
    return max(widths) if widths else 1


def _read_raw(data: bytes, suffix: str, encoding: str | None) -> pd.DataFrame:
    """헤더 없이 통째로 읽는다. 헤더 행 탐색(1단)의 입력이다."""
    if suffix in _CSV_SUFFIXES:
        assert encoding is not None
        width = _csv_column_count(data, encoding)
        return pd.read_csv(
            BytesIO(data), encoding=encoding, header=None, dtype=str, names=range(width)
        )
    return pd.read_excel(BytesIO(data), sheet_name=0, header=None)


def _read_body(data: bytes, suffix: str, encoding: str | None, header_row: int) -> pd.DataFrame:
    """헤더 행을 확정한 뒤 다시 읽는다. 이래야 dtype 추론이 정상으로 돌아온다.

    CSV 는 ``skiprows`` 로 제목 행을 **파서에 닿기 전에** 버린다. ``header=N`` 은
    앞 행도 토큰화하므로 열 수가 다른 제목 행에서 걸린다.
    """
    if suffix in _CSV_SUFFIXES:
        return pd.read_csv(BytesIO(data), encoding=encoding, skiprows=header_row, header=0)
    return pd.read_excel(BytesIO(data), sheet_name=0, header=header_row)


def _detect_csv(data: bytes, encodings: Sequence[str]) -> tuple[str, ColumnDetection]:
    """CSV 인코딩과 열 판정을 함께 확정한다.

    **1차는 이름 매칭만 허용한다.** 인코딩이 어긋나도 예외 없이 깨진 헤더가
    나오는 경우가 있는데, 내용 기반 판정은 헤더가 깨져도 성공해 버려서
    잘못된 인코딩이 채택된다. 이름으로 아무 인코딩도 통과하지 못했을 때만
    2차로 내용 기반을 연다 (그때는 인코딩 첫 후보를 쓴다).
    """
    attempts: list[str] = []
    frames: list[tuple[str, pd.DataFrame]] = []
    for encoding in encodings:
        try:
            raw = _read_raw(data, ".csv", encoding)
        except (UnicodeDecodeError, ValueError, LookupError, pd.errors.ParserError) as exc:
            attempts.append(f"{encoding}: {type(exc).__name__}")
            continue
        frames.append((encoding, raw))
        try:
            return encoding, detect_usage_columns(raw, allow_content_fallback=False)
        except ColumnDetectionError as exc:
            attempts.append(f"{encoding}: {exc}")

    for encoding, raw in frames:  # 2차 — 값으로 판정한다
        try:
            return encoding, detect_usage_columns(raw)
        except ColumnDetectionError:
            continue
    raise UsageLoadError(
        "CSV 를 읽지 못했습니다. 인코딩 또는 컬럼명을 확인해 주세요.\n  " + "\n  ".join(attempts)
    )


def _read_frame(
    data: bytes, suffix: str, encodings: Sequence[str]
) -> tuple[pd.DataFrame, str | None, ColumnDetection]:
    """파일 바이트를 DataFrame 으로 읽고 헤더 행·두 열까지 확정한다."""
    if suffix in _CSV_SUFFIXES:
        encoding, detection = _detect_csv(data, encodings)
        return _read_body(data, suffix, encoding, detection.header_row), encoding, detection

    if suffix in _EXCEL_SUFFIXES:
        try:
            raw = _read_raw(data, suffix, None)
        except ImportError as exc:  # openpyxl / xlrd 미설치
            raise UsageLoadError(
                "엑셀 파일을 읽는 데 필요한 라이브러리가 없습니다. openpyxl 설치를 확인해 주세요."
            ) from exc
        try:
            detection = detect_usage_columns(raw)
        except ColumnDetectionError as exc:
            raise UsageLoadError(str(exc)) from exc
        return _read_body(data, suffix, None, detection.header_row), None, detection

    raise UsageLoadError(
        f"지원하지 않는 파일 형식입니다: '{suffix}'. csv, xls, xlsx 를 올려 주세요."
    )


# --------------------------------------------------------------------- 로더 본체


def load_usage(
    path: str | Path,
    *,
    encodings: Sequence[str] = DEFAULT_ENCODINGS,
    interval_minutes: int | None = None,
) -> UsageData:
    """경로에서 사용량 데이터를 읽는다.

    Args:
        path: csv·xls·xlsx 경로.
        encodings: CSV 인코딩 시도 순서.
        interval_minutes: 강제 지정할 검침 간격. None 이면 자동 인식.
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise UsageLoadError(f"파일이 없습니다: {file_path}")
    return load_usage_bytes(
        file_path.read_bytes(),
        file_path.name,
        encodings=encodings,
        interval_minutes=interval_minutes,
    )


def load_usage_bytes(
    data: bytes,
    filename: str,
    *,
    encodings: Sequence[str] = DEFAULT_ENCODINGS,
    interval_minutes: int | None = None,
) -> UsageData:
    """업로드된 바이트에서 사용량 데이터를 읽는다. Streamlit 업로더용."""
    suffix = Path(filename).suffix.lower()
    frame, encoding, detection = _read_frame(data, suffix, encodings)
    date_col, energy_col = detection.date_column, detection.energy_column
    frame = frame.dropna(how="all").dropna(axis=1, how="all")
    for column in (date_col, energy_col):
        if column not in frame.columns:
            raise UsageLoadError(
                f"판정한 열 '{column}' 이 본문에 없습니다 ({detection.describe()})."
            )

    raw_rows = len(frame)
    hour24_rows = count_hour24(frame[date_col])

    parsed = pd.DataFrame(
        {
            "timestamp": parse_usage_datetime(frame[date_col]),
            "kwh": parse_usage_energy(frame[energy_col]),
        }
    )
    invalid_datetime_rows = int(parsed["timestamp"].isna().sum())
    invalid_energy_rows = int(parsed["kwh"].isna().sum())
    parsed = parsed.dropna(subset=["timestamp", "kwh"])

    negative_energy_rows = int((parsed["kwh"] < 0).sum())
    parsed = parsed[parsed["kwh"] >= 0]
    if parsed.empty:
        raise UsageLoadError("유효한 검침 데이터가 없습니다.")

    parsed = parsed.sort_values("timestamp", kind="stable")
    interval = interval_minutes or detect_interval_minutes(parsed["timestamp"])
    phase = detect_grid_phase_seconds(parsed["timestamp"], interval)

    second_of_day = (
        parsed["timestamp"].dt.hour * 3600
        + parsed["timestamp"].dt.minute * 60
        + parsed["timestamp"].dt.second
    ).astype("int64")
    on_grid_mask = (second_of_day % (interval * 60)) == phase

    on_grid = parsed[on_grid_mask]
    off_grid = parsed[~on_grid_mask].reset_index(drop=True)
    if on_grid.empty:
        raise UsageLoadError("그리드에 정렬된 검침 행이 없습니다.")

    duplicate_rows = int(on_grid.duplicated(subset=["timestamp"]).sum())
    grouped = on_grid.groupby("timestamp", as_index=True)["kwh"].sum().sort_index()

    index = pd.date_range(grouped.index.min(), grouped.index.max(), freq=f"{interval}min")
    index.name = "timestamp"
    aligned = grouped.reindex(index)
    kw_series = pd.Series(aligned.to_numpy(dtype=float) * (60.0 / interval), index=index, name="kw")

    expected_rows = len(index)
    valid_rows = len(grouped)
    missing_rows = int(aligned.isna().sum())
    missing_ratio = missing_rows / expected_rows if expected_rows else 0.0

    off_grid_kwh = float(off_grid["kwh"].sum())
    total_kwh = float(aligned.sum()) + off_grid_kwh  # 그리드 이탈 행의 kWh 도 더한다

    # 총합 경로를 막은 시리즈로 감싼다. 이탈분이 있으면 .sum() 이 예외를 던진다.
    kwh_series = GridKwhSeries(aligned.to_numpy(dtype=float), index=index, name="kwh")
    kwh_series.off_grid_kwh = off_grid_kwh
    max_demand_kw = float(kw_series.max())
    max_demand_at = pd.Timestamp(kw_series.idxmax())
    mean_kw = float(kw_series.mean())
    load_factor = mean_kw / max_demand_kw if max_demand_kw else 0.0

    start = pd.Timestamp(index[0])
    end = pd.Timestamp(index[-1])
    period_days = (end - start).total_seconds() / 86400.0

    meta = UsageMeta(
        source_name=filename,
        encoding=encoding,
        date_column=date_col,
        energy_column=energy_col,
        columns=detection,
        interval_minutes=interval,
        grid_phase_seconds=phase,
        start=start,
        end=end,
        period_days=period_days,
        raw_rows=raw_rows,
        valid_rows=valid_rows,
        off_grid_rows=len(off_grid),
        duplicate_rows=duplicate_rows,
        invalid_datetime_rows=invalid_datetime_rows,
        invalid_energy_rows=invalid_energy_rows,
        negative_energy_rows=negative_energy_rows,
        hour24_rows=hour24_rows,
        expected_rows=expected_rows,
        missing_rows=missing_rows,
        missing_ratio=missing_ratio,
        total_kwh=total_kwh,
        off_grid_kwh=off_grid_kwh,
        max_demand_kw=max_demand_kw,
        max_demand_at=max_demand_at,
        mean_kw=mean_kw,
        load_factor=load_factor,
        warnings=_build_warnings(
            detection=detection,
            interval=interval,
            missing_rows=missing_rows,
            missing_ratio=missing_ratio,
            period_days=period_days,
            off_grid=off_grid,
            duplicate_rows=duplicate_rows,
            invalid_datetime_rows=invalid_datetime_rows,
            invalid_energy_rows=invalid_energy_rows,
            negative_energy_rows=negative_energy_rows,
        ),
    )
    return UsageData(kw=kw_series, kwh_grid=kwh_series, off_grid=off_grid, meta=meta)


def _build_warnings(
    *,
    detection: ColumnDetection,
    interval: int,
    missing_rows: int,
    missing_ratio: float,
    period_days: float,
    off_grid: pd.DataFrame,
    duplicate_rows: int,
    invalid_datetime_rows: int,
    invalid_energy_rows: int,
    negative_energy_rows: int,
) -> tuple[str, ...]:
    """업로드 직후 표시할 경고. 조용히 넘어가지 않는다 (요구사항서 4장)."""
    messages: list[str] = list(detection.warnings)  # 열 판정에 확신이 없으면 먼저 알린다
    if interval != 15:
        messages.append(
            f"{interval}분 간격 데이터입니다. 15분 최대수요를 직접 관측할 수 없어 "
            "기본요금 판정에 한계가 있습니다."
        )
    if period_days < 365:
        messages.append(
            f"기간이 {period_days:.0f}일로 12개월 미만입니다. 연간 환산 시 주의가 필요합니다."
        )
    if missing_rows:
        level = "경고" if missing_ratio > 0.03 else "참고"
        messages.append(
            f"[{level}] 결측 {missing_rows:,}개 ({missing_ratio:.1%}). "
            "보간하지 않으며 결측 구간은 계산에서 제외합니다."
        )
    if not off_grid.empty:
        messages.append(
            f"그리드 이탈(부분 적산) {len(off_grid)}건 — kW 수요 산정에서 제외하고 "
            f"kWh 합계에는 포함했습니다 ({off_grid['kwh'].sum():,.2f} kWh)."
        )
    if duplicate_rows:
        messages.append(f"중복 시각 {duplicate_rows:,}건을 합산했습니다.")
    if invalid_datetime_rows:
        messages.append(f"검침일을 읽지 못한 행 {invalid_datetime_rows:,}건을 제외했습니다.")
    if invalid_energy_rows:
        messages.append(f"전력량을 읽지 못한 행 {invalid_energy_rows:,}건을 제외했습니다.")
    if negative_energy_rows:
        messages.append(f"음수 전력량 {negative_energy_rows:,}건을 제외했습니다.")
    return tuple(messages)
