"""검침일·전력량 열 판정 (요구사항서 3.1).

한전ON 원본은 **계기번호 · 고객번호 · 검침일 · 순방향 유효전력량(KWH)** 네 열이다.
샘플로 쓰는 파일은 앞 두 열을 지운 것이라, 원본 양식이 그대로 올라오는 경로가
지금까지 회귀로 고정되어 있지 않았다. 여기서 세 단(헤더 행 탐색 → 이름 매칭 →
내용 기반)을 모두 못 박는다.

핵심은 **고객번호가 값 열로 오인되지 않는 것**이다. 고객번호는 전 행이 같은
상수이고, 계기번호는 문자열이라 수치 변환 자체가 실패한다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from kwise.io import (
    ColumnDetectionError,
    UsageData,
    UsageLoadError,
    detect_usage_columns,
    find_header_row,
    load_usage,
    normalize_column_name,
    score_date_column,
    score_energy_column,
)
from tests._synthetic import KEPCO_HEADER, kepco_month_rows, write_kepco_file

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "사용량조회_양식.xlsx"
OPAQUE_HEADER = ("AAA", "BBB", "CCC", "DDD")


@pytest.fixture(scope="module")
def month_rows() -> list[tuple[str, float]]:
    """2024-03 한 달치 (라벨, kWh). 2,976 슬롯."""
    return kepco_month_rows(2024, 3)


# --------------------------------------------------------------------- 실제 양식


def test_fixture_is_the_four_column_kepco_form() -> None:
    """픽스처가 실제로 한전ON 4열 양식인지부터 확인한다."""
    raw = pd.read_excel(FIXTURE, header=None)
    assert tuple(str(value) for value in raw.iloc[0]) == KEPCO_HEADER
    assert len(raw) == 2  # 헤더 + 데이터 1행


def test_four_column_form_is_detected_by_name() -> None:
    """1·2단으로 끝난다. 계기번호·고객번호를 건드리지 않는다."""
    detection = detect_usage_columns(pd.read_excel(FIXTURE, header=None))
    assert detection.header_row == 0
    assert detection.date_column == "검침일"
    assert detection.energy_column == "순방향 유효전력량(KWH)"
    assert detection.strategy == "name"
    assert detection.warnings == ()
    assert "검침일" in detection.describe()


def test_four_column_form_loads_through_the_xlsx_path() -> None:
    """``.xlsx`` 경로 회귀. 지금까지 CSV 만 고정되어 있었다.

    양식 파일은 데이터가 한 줄이라 간격을 자동 인식할 수 없다. 간격을 주면 읽힌다.
    """
    usage = load_usage(FIXTURE, interval_minutes=15)
    assert usage.meta.encoding is None  # 엑셀은 인코딩 개념이 없다
    assert usage.meta.date_column == "검침일"
    assert usage.meta.energy_column == "순방향 유효전력량(KWH)"
    assert usage.meta.raw_rows == 1
    assert float(usage.kw.iloc[0]) == pytest.approx(321.84 * 4)  # 15분 kWh → kW


def test_one_row_form_says_why_it_cannot_guess_the_interval() -> None:
    with pytest.raises(UsageLoadError, match="간격을 판정할 수 없습니다"):
        load_usage(FIXTURE)


# --------------------------------------------------------------------- 1단 헤더 행


def test_title_row_is_skipped(tmp_path: Path, month_rows: list[tuple[str, float]]) -> None:
    """한전ON 화면 저장본은 위에 제목 행이 붙는다. 2행이 헤더다."""
    path = write_kepco_file(tmp_path / "titled.xlsx", month_rows, title="한전ON 사용량 조회 결과")
    usage = load_usage(path)
    assert usage.meta.columns.header_row == 1
    assert usage.meta.columns.strategy == "name"
    assert usage.meta.date_column == "검침일"
    assert usage.meta.expected_rows == 2_976
    assert any("2행을 헤더로" in message for message in usage.meta.warnings)


def test_title_row_is_skipped_in_csv_too(
    tmp_path: Path, month_rows: list[tuple[str, float]]
) -> None:
    """제목 행은 열 수가 달라 그대로 읽으면 파서가 깨진다."""
    path = write_kepco_file(tmp_path / "titled.csv", month_rows, title="한전ON 사용량 조회")
    usage = load_usage(path)
    assert usage.meta.columns.header_row == 1
    assert usage.meta.expected_rows == 2_976


def test_find_header_row_reports_none_when_no_name_matches() -> None:
    raw = pd.DataFrame([["AAA", "BBB"], ["1", "2"]])
    assert find_header_row(raw) is None


def test_header_is_searched_only_in_the_first_rows() -> None:
    """상위 10행까지만 본다. 그 아래는 데이터로 본다."""
    filler = [["x", "y", "z", "w"]] * 12
    raw = pd.DataFrame([*filler, list(KEPCO_HEADER)])
    assert find_header_row(raw) is None
    assert find_header_row(raw, max_rows=20) == 12


# --------------------------------------------------------------------- 2단 이름 매칭


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("순방향 유효전력량(KWH)", "순방향유효전력량 [kwh]"),
        ("검침일", " 검침일 "),
        ("kWh", "ｋＷｈ"),  # 전각도 접는다
        ("Meter Reading Date", "meter_reading_date"),
    ],
)
def test_column_names_are_normalized(left: str, right: str) -> None:
    assert normalize_column_name(left) == normalize_column_name(right)


def test_english_headers_are_matched(tmp_path: Path, month_rows: list[tuple[str, float]]) -> None:
    path = write_kepco_file(
        tmp_path / "en.xlsx",
        month_rows,
        header=("Meter No", "Customer No", "Meter Reading Date", "Forward Active Energy (kWh)"),
    )
    usage = load_usage(path)
    assert usage.meta.columns.strategy == "name"
    assert usage.meta.date_column == "Meter Reading Date"
    assert usage.meta.energy_column == "Forward Active Energy (kWh)"


# --------------------------------------------------------------------- 3단 내용 기반


def test_unreadable_headers_fall_back_to_content(
    tmp_path: Path, month_rows: list[tuple[str, float]]
) -> None:
    """이름을 전부 알아볼 수 없어도 값으로 찾아낸다."""
    path = write_kepco_file(tmp_path / "opaque.xlsx", month_rows, header=OPAQUE_HEADER)
    usage = load_usage(path)
    detection = usage.meta.columns
    assert detection.strategy == "content"
    assert detection.date_column == "CCC"
    assert detection.energy_column == "DDD"
    assert usage.meta.expected_rows == 2_976
    assert any("값 패턴으로 판정" in message for message in usage.meta.warnings)


def test_content_fallback_works_with_a_title_row(
    tmp_path: Path, month_rows: list[tuple[str, float]]
) -> None:
    """제목 행 + 알아볼 수 없는 헤더. 열이 꽉 찬 행만 헤더 후보다."""
    path = write_kepco_file(
        tmp_path / "opaque_titled.xlsx", month_rows, header=OPAQUE_HEADER, title="제목 줄"
    )
    usage = load_usage(path)
    assert usage.meta.columns.header_row == 1
    assert usage.meta.columns.date_column == "CCC"
    assert usage.meta.columns.energy_column == "DDD"


def test_customer_number_is_never_taken_for_the_energy_column(
    tmp_path: Path, month_rows: list[tuple[str, float]]
) -> None:
    """**고유값 조건이 고객번호를 걸러낸다.** 전 행이 같은 상수이기 때문이다."""
    path = write_kepco_file(tmp_path / "opaque.xlsx", month_rows, header=OPAQUE_HEADER)
    detection = detect_usage_columns(pd.read_excel(path, header=None))
    chosen = {item.column for item in detection.energy_candidates}
    assert detection.energy_column == "DDD"
    assert "BBB" not in chosen  # 고객번호 — 상수라 후보에도 오르지 않는다
    assert "AAA" not in chosen  # 계기번호 — 문자열이라 수치 변환이 실패한다


def test_constant_column_is_rejected_with_a_reason() -> None:
    constant = pd.Series([196_705_100] * 500, name="고객번호")
    candidate = score_energy_column(constant)
    assert candidate.score == 0.0
    assert "상수 열" in candidate.reason


def test_meter_number_is_not_coerced_into_a_number() -> None:
    """``55-282007100`` 에서 기호를 지우면 멀쩡한 수가 된다. 지우지 않는다."""
    meters = pd.Series(["55-282007100"] * 500, name="계기번호")
    assert score_energy_column(meters).score == 0.0
    assert "수치 변환" in score_energy_column(meters).reason


def test_low_distinct_ratio_is_not_rejected() -> None:
    """실측 샘플의 kWh 고유값 비율은 4.1% 다. 비율로 자르면 진짜 값 열이 잘린다."""
    raw = pd.read_csv(
        Path(__file__).resolve().parent.parent / "input" / "사용량조회_20240429.csv",
        encoding="utf-8-sig",
    )
    series = raw["순방향 유효전력량(KWH)"]
    ratio = series.nunique() / len(series)
    assert ratio < 0.05  # 5% 문턱이었다면 잘렸을 열이다
    assert score_energy_column(series).score > 0


def test_energy_column_with_negatives_is_rejected() -> None:
    values = pd.Series([1.0, -2.0, 3.0, 4.0, 5.0], name="정산")
    assert score_energy_column(values).score == 0.0
    assert "음수" in score_energy_column(values).reason


def test_regular_timestamps_beat_irregular_ones() -> None:
    """등간격성이 시각 열을 가른다. kWh 도 Excel 일련번호로는 파싱되기 때문이다."""
    labels = pd.Series(
        pd.date_range("2024-03-01 00:15", periods=200, freq="15min").astype(str), name="검침"
    )
    kwh = pd.Series([100.0 + index * 0.37 for index in range(200)], name="사용량")
    assert score_date_column(labels).score > score_date_column(kwh).score


def test_detection_fails_loudly_on_a_file_without_either_column(tmp_path: Path) -> None:
    frame = pd.DataFrame({"이름": ["가", "나"], "메모": ["x", "y"]})
    with pytest.raises(ColumnDetectionError, match="판독하지 못했습니다"):
        detect_usage_columns(pd.DataFrame([frame.columns.tolist(), *frame.to_numpy().tolist()]))


def test_empty_frame_is_rejected() -> None:
    with pytest.raises(ColumnDetectionError, match="비어 있습니다"):
        detect_usage_columns(pd.DataFrame())


# --------------------------------------------------------------------- 판정 결과 노출


def test_detection_is_carried_in_the_metadata(sample_usage: UsageData) -> None:
    """8세션 UI 가 사용자에게 보여 주고 고칠 수 있어야 한다."""
    detection = sample_usage.meta.columns
    assert detection.date_strategy == "name"
    assert detection.energy_strategy == "name"
    assert detection.header_row == 0
    assert detection.columns == ("검침일", "순방향 유효전력량(KWH)")
    assert detection.date_column == sample_usage.meta.date_column


def test_two_column_sample_still_matches_appendix_b(sample_usage: UsageData) -> None:
    """**기존 샘플(2열 CSV)이 부록 B 와 여전히 일치한다.** 강화가 회귀를 깨지 않았다."""
    meta = sample_usage.meta
    assert meta.encoding == "utf-8-sig"
    assert (meta.raw_rows, meta.valid_rows, meta.off_grid_rows) == (34_358, 34_356, 2)
    assert (meta.expected_rows, meta.missing_rows) == (35_328, 972)
    assert meta.hour24_rows == 357
    assert meta.max_demand_kw == pytest.approx(5_293.44)
    assert meta.max_demand_at == pd.Timestamp("2023-07-03 09:30")
    assert meta.total_kwh / 1_000 == pytest.approx(22_284.8, abs=0.1)
    assert meta.load_factor == pytest.approx(0.490, abs=0.001)
