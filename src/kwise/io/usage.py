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

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

import pandas as pd

__all__ = [
    "DEFAULT_ENCODINGS",
    "SUPPORTED_INTERVALS",
    "USAGE_DATE_COLUMN_CANDIDATES",
    "USAGE_ENERGY_COLUMN_CANDIDATES",
    "UsageData",
    "UsageLoadError",
    "UsageMeta",
    "count_hour24",
    "detect_grid_phase_seconds",
    "detect_interval_minutes",
    "load_usage",
    "load_usage_bytes",
    "match_usage_column",
    "parse_usage_datetime",
    "parse_usage_energy",
]

# 인코딩 4종 순차 시도. 인코딩이 어긋나도 예외 없이 깨진 헤더가 나오는 경우가 있으므로
# 컬럼 매칭 성공까지 확인해야 그 인코딩을 채택한다.
DEFAULT_ENCODINGS: tuple[str, ...] = ("utf-8-sig", "cp949", "euc-kr", "utf-8")

SUPPORTED_INTERVALS: tuple[int, ...] = (15, 60)

USAGE_DATE_COLUMN_CANDIDATES: tuple[str, ...] = (
    "Meter Reading Date",
    "검침일",
    "검침일시",
    "검침 날짜",
    "검침시간",
    "일시",
    "날짜시간",
)
USAGE_ENERGY_COLUMN_CANDIDATES: tuple[str, ...] = (
    "Forward Active Energy (kWh)",
    "순방향 유효전력량(KWH)",
    "순방향 유효전력량 (kWh)",
    "순방향유효전력량",
    "유효전력량",
    "전력사용량",
    "사용량",
    "kWh",
)

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


@dataclass(frozen=True, eq=False)
class UsageData:
    """로더의 반환값.

    Attributes:
        kw: tz-naive DatetimeIndex 의 15분(또는 1시간) 평균 수요. 결측은 NaN.
            그리드 이탈 행은 포함하지 않는다.
        kwh: 같은 인덱스의 사용량. 그리드 이탈 행은 포함하지 않는다.
        off_grid: 그리드를 벗어난 행 (``timestamp``, ``kwh``). 값까지 보존한다.
        meta: 메타데이터.
    """

    kw: pd.Series
    kwh: pd.Series
    off_grid: pd.DataFrame
    meta: UsageMeta

    @property
    def total_kwh(self) -> float:
        """그리드 이탈 행을 포함한 총 사용량. 요금 계산의 기준이다."""
        return self.meta.total_kwh


# --------------------------------------------------------------------- 컬럼 매칭


def normalize_column_name(value: object) -> str:
    """컬럼명 비교용 정규화. 공백·기호를 지우고 소문자로 만든다."""
    text = str(value).strip().lower()
    text = re.sub(r"[\s_\-./\\()\[\]{}]", "", text)
    return text.replace("㎾h", "kwh")


def match_usage_column(columns: Iterable[object], candidates: Sequence[str]) -> str | None:
    """후보 목록으로 컬럼을 유연 매칭한다. 정확 일치 → 부분 일치 순.

    reference\\streamlit_app.py 에서 실측 검증된 로직이다.
    """
    normalized_columns = {normalize_column_name(col): col for col in columns}
    for candidate in candidates:
        key = normalize_column_name(candidate)
        if key in normalized_columns:
            return str(normalized_columns[key])
    for candidate in candidates:
        key = normalize_column_name(candidate)
        if not key:
            continue
        for norm_col, original_col in normalized_columns.items():
            if key in norm_col or norm_col in key:
                return str(original_col)
    return None


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


# --------------------------------------------------------------------- 파일 읽기


def _match_columns(frame: pd.DataFrame) -> tuple[str, str] | None:
    date_col = match_usage_column(frame.columns, USAGE_DATE_COLUMN_CANDIDATES)
    energy_col = match_usage_column(frame.columns, USAGE_ENERGY_COLUMN_CANDIDATES)
    if date_col is None or energy_col is None:
        return None
    return date_col, energy_col


def _read_frame(
    raw: bytes, suffix: str, encodings: Sequence[str]
) -> tuple[pd.DataFrame, str | None, str, str]:
    """파일 바이트를 DataFrame 으로 읽고 컬럼까지 확정한다.

    CSV 는 인코딩을 순차 시도하되, **컬럼 매칭에 성공해야 그 인코딩을 채택한다.**
    인코딩이 어긋나도 예외 없이 깨진 헤더가 나오는 경우가 있기 때문이다.
    """
    if suffix in _CSV_SUFFIXES:
        attempts: list[str] = []
        for encoding in encodings:
            try:
                frame = pd.read_csv(BytesIO(raw), encoding=encoding)
            except (UnicodeDecodeError, ValueError, pd.errors.ParserError) as exc:
                attempts.append(f"{encoding}: {type(exc).__name__}")
                continue
            matched = _match_columns(frame)
            if matched is None:
                attempts.append(f"{encoding}: 컬럼 매칭 실패 {list(frame.columns)}")
                continue
            return frame, encoding, matched[0], matched[1]
        raise UsageLoadError(
            "CSV 를 읽지 못했습니다. 인코딩 또는 컬럼명을 확인해 주세요.\n  "
            + "\n  ".join(attempts)
        )

    if suffix in _EXCEL_SUFFIXES:
        try:
            frame = pd.read_excel(BytesIO(raw), sheet_name=0)
        except ImportError as exc:  # openpyxl / xlrd 미설치
            raise UsageLoadError(
                "엑셀 파일을 읽는 데 필요한 라이브러리가 없습니다. openpyxl 설치를 확인해 주세요."
            ) from exc
        matched = _match_columns(frame)
        if matched is None:
            raise UsageLoadError(
                f"검침일 또는 전력량 컬럼을 판독하지 못했습니다: {list(frame.columns)}"
            )
        return frame, None, matched[0], matched[1]

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
    frame, encoding, date_col, energy_col = _read_frame(data, suffix, encodings)
    frame = frame.dropna(how="all").dropna(axis=1, how="all")

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
    kwh_series = grouped.reindex(index)
    kw_series = kwh_series * (60.0 / interval)
    kw_series.name = "kw"
    kwh_series.name = "kwh"

    expected_rows = len(index)
    valid_rows = len(grouped)
    missing_rows = int(kwh_series.isna().sum())
    missing_ratio = missing_rows / expected_rows if expected_rows else 0.0

    off_grid_kwh = float(off_grid["kwh"].sum())
    total_kwh = float(kwh_series.sum()) + off_grid_kwh  # 그리드 이탈 행의 kWh 도 더한다
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
    return UsageData(kw=kw_series, kwh=kwh_series, off_grid=off_grid, meta=meta)


def _build_warnings(
    *,
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
    messages: list[str] = []
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
