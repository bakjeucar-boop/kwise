"""검침일·전력량 열 판정 (요구사항서 3.1).

한전ON 다운로드 원본은 **계기번호 · 고객번호 · 검침일 · 순방향 유효전력량(KWH)**
네 열이다. 샘플로 쓰는 파일은 앞 두 열을 지운 것이다. 양식은 언제든 바뀔 수
있으므로 열 위치에 기대지 않고 세 단으로 찾아낸다.

    1단  헤더 행 탐색   상위 10행 중 날짜 계열 이름과 kWh 계열 이름이 함께 있는 행
    2단  이름 매칭      공백·괄호·대소문자·전각반각을 정규화한 뒤 후보 사전과 대조
    3단  내용 기반      이름으로 못 찾으면 **값**으로 판정한다

3단이 핵심이다. 판정 기준은 이렇다.

    시각 열   파싱 성공률 95% 이상 + 등간격성(연속 차이의 최빈값 점유율)
    값 열     수치형 + 음수 없음 + **고유값이 상수가 아닐 것**

고유값 조건이 고객번호를 걸러낸다. 고객번호는 전 행이 같은 상수라 고유값이
하나다. 계기번호는 ``55-282007100`` 같은 문자열이라 수치 변환 자체가 실패해
애초에 후보가 아니다 — 그래서 여기서는 쉼표만 걷어내고 :func:`pandas.to_numeric`
에 맡긴다. 로더 본체의 :func:`parse_usage_energy` 처럼 숫자 아닌 문자를 모두
지워 버리면 계기번호가 ``55282007100`` 이라는 멀쩡한 수로 둔갑한다.

판정 결과는 :class:`ColumnDetection` 으로 돌려준다. **어느 열을 무엇으로
인식했고 어느 단계에서 판정했는지**가 담기므로 8세션 UI 에서 사용자가 고쳐
넣을 수 있다.
"""

from __future__ import annotations

import re
import unicodedata
import warnings
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import pandas as pd

__all__ = [
    "CONTENT_DISTINCT_REFERENCE",
    "CONTENT_MIN_DISTINCT",
    "CONTENT_MIN_RATIO",
    "MAX_HEADER_SEARCH_ROWS",
    "USAGE_DATE_COLUMN_CANDIDATES",
    "USAGE_ENERGY_COLUMN_CANDIDATES",
    "ColumnCandidate",
    "ColumnDetection",
    "ColumnDetectionError",
    "detect_usage_columns",
    "find_header_row",
    "match_usage_column",
    "normalize_column_name",
    "score_date_column",
    "score_energy_column",
]

MAX_HEADER_SEARCH_ROWS = 10
CONTENT_MIN_RATIO = 0.95

# 고객번호(전 행 동일)를 값 열로 오인하지 않게 하는 문턱.
#
# **비율이 아니라 개수로 자른다.** 실측 샘플의 15분 kWh 는 34,358행에 고유값이
# 1,417개뿐이다 — 비율로 치면 4.1% 라, 5% 같은 비율 문턱을 두면 진짜 사용량 열이
# 잘려 나간다. 반면 고객번호·계기번호·계약전력은 파일 전체가 한 값이라 고유값이
# 1개다. 개수로 자르고 비율은 점수에만 반영한다.
CONTENT_MIN_DISTINCT = 3
CONTENT_DISTINCT_REFERENCE = 0.05  # 이 비율이면 만점. 실측 4.1% 가 0.82점이 된다.
# 이름 매칭 없이 값만으로 고른 경우, 1·2위 점수가 이만큼 안 벌어지면 경고한다.
AMBIGUOUS_MARGIN = 0.05
# 점수는 표본으로 매긴다. 34,000행 전부에 dateutil 을 돌릴 이유가 없다.
SCORE_SAMPLE_ROWS = 2_000

USAGE_DATE_COLUMN_CANDIDATES: tuple[str, ...] = (
    "검침일",
    "검침일시",
    "검침 날짜",
    "검침시간",
    "일시",
    "날짜",
    "날짜시간",
    "일자",
    "Meter Reading Date",
    "datetime",
    "timestamp",
    "date",
)
USAGE_ENERGY_COLUMN_CANDIDATES: tuple[str, ...] = (
    "순방향 유효전력량(KWH)",
    "순방향 유효전력량 (kWh)",
    "순방향유효전력량",
    "유효전력량",
    "전력사용량",
    "사용량",
    "전력량",
    "Forward Active Energy (kWh)",
    "kWh",
)

# 이름 매칭에서 시각 열이 전력량 열로 새는 것을 막는다. '검침일' 은 '일자' 와
# 겹치는 글자가 없지만, 부분 일치 단계에서는 짧은 후보가 엉뚱하게 걸리기 쉽다.
_MIN_PARTIAL_MATCH_LEN = 2


class ColumnDetectionError(ValueError):
    """검침일·전력량 열을 찾지 못했을 때 발생한다."""


@dataclass(frozen=True)
class ColumnCandidate:
    """열 하나의 후보 평가. UI 에서 '왜 이 열인가' 를 보여줄 때 쓴다."""

    column: str
    score: float
    reason: str


@dataclass(frozen=True)
class ColumnDetection:
    """열 판정 결과.

    Attributes:
        header_row: 헤더로 삼은 행 번호 (0부터). 제목 행이 있으면 0 이 아니다.
        date_strategy / energy_strategy: ``"name"`` 이면 이름으로, ``"content"``
            면 값으로 판정했다.
        date_candidates / energy_candidates: 점수 순 후보. 3단에서만 채워진다.
        warnings: 후보가 여럿이거나 확신이 낮을 때의 안내.
    """

    date_column: str
    energy_column: str
    header_row: int
    date_strategy: str
    energy_strategy: str
    columns: tuple[str, ...] = field(default=())
    date_candidates: tuple[ColumnCandidate, ...] = field(default=())
    energy_candidates: tuple[ColumnCandidate, ...] = field(default=())
    warnings: tuple[str, ...] = field(default=())

    @property
    def strategy(self) -> str:
        """두 열을 어떻게 찾았는지 한 낱말로. ``name`` / ``content`` / ``mixed``."""
        if self.date_strategy == self.energy_strategy:
            return self.date_strategy
        return "mixed"

    def describe(self) -> str:
        """산출물·UI 에 그대로 쓸 한 줄."""
        how = {"name": "열 이름", "content": "값 패턴", "mixed": "열 이름+값 패턴"}[self.strategy]
        row = "" if self.header_row == 0 else f" (헤더 {self.header_row + 1}행)"
        return f"검침일='{self.date_column}', 전력량='{self.energy_column}' — {how} 으로 판정{row}"


# --------------------------------------------------------------------- 2단 이름 매칭


def normalize_column_name(value: object) -> str:
    """컬럼명 비교용 정규화.

    전각을 반각으로 접고(NFKC), 공백·괄호·기호를 지우고 소문자로 만든다.
    ``순방향 유효전력량(KWH)`` 와 ``순방향유효전력량 [kWh]`` 가 같아진다.
    """
    text = unicodedata.normalize("NFKC", str(value)).strip().lower()
    text = re.sub(r"[\s_\-./\\()\[\]{}]", "", text)
    return text.replace("㎾h", "kwh").replace("㎿h", "mwh")


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
        if len(key) < _MIN_PARTIAL_MATCH_LEN:
            continue
        for norm_col, original_col in normalized_columns.items():
            if key in norm_col or norm_col in key:
                return str(original_col)
    return None


def _match_by_name(columns: Sequence[object]) -> tuple[str, str] | None:
    date_col = match_usage_column(columns, USAGE_DATE_COLUMN_CANDIDATES)
    energy_col = match_usage_column(columns, USAGE_ENERGY_COLUMN_CANDIDATES)
    if date_col is None or energy_col is None or date_col == energy_col:
        return None
    return date_col, energy_col


# --------------------------------------------------------------------- 1단 헤더 행


def find_header_row(raw: pd.DataFrame, *, max_rows: int = MAX_HEADER_SEARCH_ROWS) -> int | None:
    """이름 매칭이 성립하는 첫 행을 헤더로 잡는다. 없으면 None.

    헤더가 1행이라는 보장이 없다. 한전ON 화면 저장본은 위에 제목 행이 붙는다.
    """
    for row in range(min(max_rows, len(raw))):
        values = list(raw.iloc[row])
        if _match_by_name(values) is not None:
            return row
    return None


# --------------------------------------------------------------------- 3단 내용 기반


def _numeric(series: pd.Series) -> pd.Series:
    """쉼표만 걷어내고 수치 변환한다.

    **숫자 아닌 문자를 지우지 않는다.** 지우면 계기번호 ``55-282007100`` 이
    ``55282007100`` 이라는 멀쩡한 수가 되어 값 열 후보로 올라온다.
    """
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")
    if pd.api.types.is_datetime64_any_dtype(series):
        return pd.Series(float("nan"), index=series.index)
    cleaned = series.astype(str).str.strip().str.replace(",", "", regex=False)
    return pd.to_numeric(cleaned, errors="coerce")


def _regularity(parsed: pd.Series) -> float:
    """연속 차이의 최빈값 점유율. 15분 그리드면 1.0 에 가깝다."""
    ordered = parsed.dropna().sort_values()
    if len(ordered) < 3:
        return 0.0
    diffs = ordered.diff().dropna()
    diffs = diffs[diffs > pd.Timedelta(0)]
    if diffs.empty:
        return 0.0
    return float((diffs == diffs.mode().iloc[0]).mean())


def score_date_column(series: pd.Series) -> ColumnCandidate:
    """시각 열 점수 — 파싱 성공률 + 등간격성.

    수치형은 Excel 일련번호로도 읽히므로 전력량 열이 날짜로 둔갑할 수 있다.
    등간격성이 이를 갈라내고, 수치형에는 감점을 두어 안전 여유를 더한다.
    """
    from kwise.io.usage import parse_usage_datetime

    series = series.head(SCORE_SAMPLE_ROWS)
    total = len(series)
    if total == 0:
        return ColumnCandidate(str(series.name), 0.0, "빈 열")
    with warnings.catch_warnings():
        # 후보를 훑는 단계다. 형식을 못 맞춘다는 안내는 여기서 낼 것이 아니다.
        warnings.simplefilter("ignore", UserWarning)
        parsed = parse_usage_datetime(series)
    ratio = float(parsed.notna().mean())
    if ratio < CONTENT_MIN_RATIO or int(parsed.notna().sum()) < 2:
        return ColumnCandidate(str(series.name), 0.0, f"시각 파싱 성공률 {ratio:.0%}")
    regularity = _regularity(parsed)
    penalty = 0.3 if pd.api.types.is_numeric_dtype(series) else 0.0
    score = ratio + regularity - penalty
    reason = f"파싱 {ratio:.0%}, 등간격 {regularity:.0%}"
    if penalty:
        reason += ", 수치형 감점"  # Excel 일련번호로 읽힌 것이라 확신이 낮다
    return ColumnCandidate(str(series.name), score, reason)


def score_energy_column(series: pd.Series) -> ColumnCandidate:
    """값 열 점수 — 수치형 + 음수 없음 + 고유값 비율.

    **고유값 비율이 고객번호를 걸러낸다.** 고객번호는 전 행이 같은 상수다.
    """
    series = series.head(SCORE_SAMPLE_ROWS)
    total = len(series)
    if total == 0:
        return ColumnCandidate(str(series.name), 0.0, "빈 열")
    values = _numeric(series)
    ratio = float(values.notna().mean())
    if ratio < CONTENT_MIN_RATIO:
        return ColumnCandidate(str(series.name), 0.0, f"수치 변환 성공률 {ratio:.0%}")
    observed = values.dropna()
    if (observed < 0).any():
        return ColumnCandidate(str(series.name), 0.0, "음수가 있습니다")
    distinct = int(observed.nunique())
    distinct_ratio = distinct / len(observed) if len(observed) else 0.0
    if distinct < CONTENT_MIN_DISTINCT:
        return ColumnCandidate(
            str(series.name),
            0.0,
            f"고유값 {distinct}개 — 고객번호·계약전력 같은 상수 열로 보입니다",
        )
    fractional = float((observed % 1 != 0).mean())
    score = ratio + min(distinct_ratio / CONTENT_DISTINCT_REFERENCE, 1.0) + 0.2 * fractional
    return ColumnCandidate(
        str(series.name),
        score,
        f"수치 {ratio:.0%}, 고유값 {distinct_ratio:.0%}, 소수 {fractional:.0%}",
    )


def _header_names(values: Iterable[object]) -> list[str]:
    """헤더 행을 열 이름으로. 빈 칸과 중복에 자리 번호를 붙여 유일하게 만든다.

    제목 행을 헤더로 삼으면 뒤 칸이 전부 NaN 이라 이름이 겹친다. 겹친 채로 두면
    ``frame[name]`` 이 Series 가 아니라 DataFrame 을 돌려주어 판정이 깨진다.
    """
    names: list[str] = []
    seen: set[str] = set()
    for position, value in enumerate(values, start=1):
        text = "" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value)
        text = text.strip()
        if not text or text.lower() == "nan":
            text = f"열{position}"
        if text in seen:
            text = f"{text}_{position}"
        seen.add(text)
        names.append(text)
    return names


def _full_width_rows(raw: pd.DataFrame, limit: int) -> list[int]:
    """헤더가 될 수 있는 행 — 열이 꽉 찬 행만.

    제목 행은 한 칸만 차 있다. 헤더로 삼으면 나머지 열 이름이 비어 버리고,
    진짜 헤더가 데이터 첫 줄로 섞여 들어간다. 애초에 후보에서 뺀다.
    """
    head = raw.head(limit)
    widths = head.notna().sum(axis=1)
    if widths.empty:
        return []
    full = int(widths.max())
    return [position for position, width in enumerate(widths) if int(width) == full]


def _rank(candidates: Iterable[ColumnCandidate]) -> tuple[ColumnCandidate, ...]:
    return tuple(
        sorted(
            (item for item in candidates if item.score > 0),
            key=lambda item: item.score,
            reverse=True,
        )
    )


def _detect_by_content(
    frame: pd.DataFrame,
) -> tuple[str, str, tuple[ColumnCandidate, ...], tuple[ColumnCandidate, ...]] | None:
    """값만으로 두 열을 고른다. 시각 열을 먼저 정하고 값 열에서 제외한다."""
    dates = _rank(score_date_column(frame[column]) for column in frame.columns)
    if not dates:
        return None
    date_column = dates[0].column
    energies = _rank(
        score_energy_column(frame[column]) for column in frame.columns if column != date_column
    )
    if not energies:
        return None
    return date_column, energies[0].column, dates, energies


# --------------------------------------------------------------------- 판정 본체


def detect_usage_columns(
    raw: pd.DataFrame,
    *,
    max_header_rows: int = MAX_HEADER_SEARCH_ROWS,
    allow_content_fallback: bool = True,
) -> ColumnDetection:
    """헤더 없이 읽은 원본에서 헤더 행과 두 열을 판정한다.

    Args:
        raw: ``header=None`` 으로 읽은 프레임. 헤더 문자열도 한 행으로 들어 있다.
        allow_content_fallback: 3단(내용 기반)을 쓸지. CSV 인코딩 시도에서는
            1차로 끈다 — 인코딩이 깨져도 숫자는 멀쩡해 잘못된 인코딩이 채택될 수 있다.

    Raises:
        ColumnDetectionError: 세 단으로도 찾지 못했을 때.
    """
    if raw.empty:
        raise ColumnDetectionError("파일이 비어 있습니다.")

    header_row = find_header_row(raw, max_rows=max_header_rows)
    if header_row is not None:
        columns = [str(value) for value in raw.iloc[header_row]]
        matched = _match_by_name(list(raw.iloc[header_row]))
        assert matched is not None  # find_header_row 가 보장한다
        date_column, energy_column = matched
        return ColumnDetection(
            date_column=date_column,
            energy_column=energy_column,
            header_row=header_row,
            date_strategy="name",
            energy_strategy="name",
            columns=tuple(columns),
            warnings=(
                ()
                if header_row == 0
                else (f"{header_row}개 행을 건너뛰고 {header_row + 1}행을 헤더로 삼았습니다.",)
            ),
        )

    if not allow_content_fallback:
        raise ColumnDetectionError(
            f"열 이름으로 검침일·전력량을 찾지 못했습니다: {list(raw.iloc[0])}"
        )

    # 3단 — 이름을 못 알아봤다. 열이 꽉 찬 행만 헤더 후보로 두고 값으로 판정한다.
    for row in _full_width_rows(raw, min(max_header_rows, len(raw))):
        body = raw.iloc[row + 1 :].reset_index(drop=True)
        if body.empty:
            break
        body.columns = pd.Index(_header_names(raw.iloc[row]))
        body = body.dropna(axis=1, how="all")
        found = _detect_by_content(body)
        if found is None:
            continue
        date_column, energy_column, dates, energies = found
        warnings = [
            "열 이름을 알아보지 못해 값 패턴으로 판정했습니다. "
            f"검침일='{date_column}' ({dates[0].reason}), "
            f"전력량='{energy_column}' ({energies[0].reason}). "
            "다르면 직접 지정해 주십시오.",
        ]
        if row:
            warnings.append(f"{row}개 행을 건너뛰고 {row + 1}행을 헤더로 삼았습니다.")
        for role, ranked in (("검침일", dates), ("전력량", energies)):
            if len(ranked) > 1 and ranked[0].score - ranked[1].score < AMBIGUOUS_MARGIN:
                warnings.append(
                    f"{role} 후보가 우열을 가리기 어렵습니다: "
                    f"'{ranked[0].column}' ({ranked[0].score:.2f}) vs "
                    f"'{ranked[1].column}' ({ranked[1].score:.2f})."
                )
        return ColumnDetection(
            date_column=date_column,
            energy_column=energy_column,
            header_row=row,
            date_strategy="content",
            energy_strategy="content",
            columns=tuple(_header_names(raw.iloc[row])),
            date_candidates=dates,
            energy_candidates=energies,
            warnings=tuple(warnings),
        )

    raise ColumnDetectionError(
        "검침일 또는 전력량 열을 판독하지 못했습니다. "
        f"열 이름: {list(raw.iloc[0])}. "
        "검침일과 사용량(kWh) 두 열이 있는 파일인지 확인해 주십시오."
    )
