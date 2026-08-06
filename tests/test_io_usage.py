"""사용량 로더 단위테스트 (요구사항서 3.1, 4.3, 부록 B)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from kwise.io import (
    EnergyToDemandError,
    OffGridEnergyError,
    UsageData,
    UsageLoadError,
    detect_grid_phase_seconds,
    detect_interval_minutes,
    load_usage,
    match_usage_column,
    parse_usage_datetime,
    parse_usage_energy,
    slot_start,
)
from tests._synthetic import HEADER, make_labels, one_day, write_csv

# --------------------------------------------------------------------- 24:00 파싱


def test_parse_usage_datetime_moves_24h_to_next_day() -> None:
    series = pd.Series(
        [
            "2023-04-25 23:45",
            "2023-04-25 24:00",
            "2023-04-26 00:15",
            "2023-04-26 24:00:00",
        ]
    )
    parsed = parse_usage_datetime(series)
    assert list(parsed) == [
        pd.Timestamp("2023-04-25 23:45"),
        pd.Timestamp("2023-04-26 00:00"),
        pd.Timestamp("2023-04-26 00:15"),
        pd.Timestamp("2023-04-27 00:00"),
    ]


def test_parse_usage_datetime_is_tz_naive() -> None:
    series = pd.Series(["2023-04-25 24:00"])
    assert parse_usage_datetime(series).dt.tz is None


def test_parse_usage_datetime_accepts_excel_serial() -> None:
    # 45041.0 = 2023-04-25 (Excel 1900 체계, origin 1899-12-30)
    parsed = parse_usage_datetime(pd.Series([45041.0]))
    assert parsed.iloc[0] == pd.Timestamp("2023-04-25")


def test_parse_usage_energy_strips_separators() -> None:
    parsed = parse_usage_energy(pd.Series(["1,234.56", "321.84 kWh", "abc"]))
    assert parsed.iloc[0] == pytest.approx(1234.56)
    assert parsed.iloc[1] == pytest.approx(321.84)
    assert pd.isna(parsed.iloc[2])


def test_hour24_rows_counted_in_meta(tmp_path: Path) -> None:
    # 3일치 = 24:00 표기 3건
    rows = [
        (label, 100.0)
        for date in ("2024-01-01", "2024-01-02", "2024-01-03")
        for label in make_labels(date, 15)
    ]
    usage = load_usage(write_csv(tmp_path / "u.csv", rows))
    assert usage.meta.hour24_rows == 3


# --------------------------------------------------------------------- 인코딩


@pytest.mark.parametrize("encoding", ["utf-8-sig", "cp949", "euc-kr", "utf-8"])
def test_loads_every_supported_encoding(tmp_path: Path, encoding: str) -> None:
    path = one_day(tmp_path / f"{encoding}.csv", encoding=encoding)
    usage = load_usage(path)
    assert usage.meta.valid_rows == 96
    assert usage.meta.date_column == HEADER[0]
    assert usage.meta.energy_column == HEADER[1]
    assert usage.kw.max() == pytest.approx(400.0)


def test_cp949_file_is_not_read_as_utf8(tmp_path: Path) -> None:
    path = one_day(tmp_path / "cp949.csv", encoding="cp949")
    # utf-8-sig 는 한글 cp949 바이트에서 디코딩 실패 → cp949 로 넘어간다
    assert load_usage(path).meta.encoding == "cp949"


def test_encoding_is_adopted_only_after_column_match(tmp_path: Path) -> None:
    """인코딩이 어긋나도 예외 없이 깨진 헤더가 나오는 경우가 있다.

    latin-1 은 어떤 바이트도 디코딩에 성공하므로 헤더가 깨진 채 통과한다.
    컬럼 매칭까지 확인해야 이 인코딩을 버리고 cp949 를 채택할 수 있다.
    """
    path = one_day(tmp_path / "cp949.csv", encoding="cp949")
    usage = load_usage(path, encodings=("latin-1", "cp949"))
    assert usage.meta.encoding == "cp949"
    assert usage.meta.valid_rows == 96


def test_unreadable_columns_raise(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("가나다,라마바\n2024-01-01 00:15,1\n", encoding="utf-8-sig")
    with pytest.raises(UsageLoadError, match="컬럼"):
        load_usage(path)


def test_unsupported_suffix_raises(tmp_path: Path) -> None:
    path = tmp_path / "u.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(UsageLoadError, match="지원하지 않는"):
        load_usage(path)


# --------------------------------------------------------------------- 컬럼 매칭


@pytest.mark.parametrize(
    "column",
    ["검침일", "검침일시", "Meter Reading Date", "meter_reading_date", " 검침일 "],
)
def test_match_usage_column_is_flexible(column: str) -> None:
    from kwise.io import USAGE_DATE_COLUMN_CANDIDATES

    assert match_usage_column([column, "값"], USAGE_DATE_COLUMN_CANDIDATES) == column


def test_english_header_file(tmp_path: Path) -> None:
    rows = [(label, 100.0) for label in make_labels("2024-01-01", 15)]
    path = write_csv(
        tmp_path / "en.csv",
        rows,
        encoding="utf-8",
        header=("Meter Reading Date", "Forward Active Energy (kWh)"),
    )
    usage = load_usage(path)
    assert usage.meta.date_column == "Meter Reading Date"
    assert usage.meta.valid_rows == 96


# --------------------------------------------------------------------- 간격·위상


def test_detects_15min_interval(tmp_path: Path) -> None:
    usage = load_usage(one_day(tmp_path / "q.csv", interval=15))
    assert usage.meta.interval_minutes == 15
    assert usage.meta.expected_rows == 96
    assert usage.kw.iloc[0] == pytest.approx(400.0)  # 100 kWh / 0.25 h


def test_detects_hourly_interval_and_warns(tmp_path: Path) -> None:
    usage = load_usage(one_day(tmp_path / "h.csv", interval=60, kwh=100.0))
    assert usage.meta.interval_minutes == 60
    assert usage.meta.expected_rows == 24
    assert usage.kw.iloc[0] == pytest.approx(100.0)  # 100 kWh / 1 h
    assert any("기본요금 판정에 한계" in message for message in usage.meta.warnings)


def test_interval_detection_survives_gaps() -> None:
    stamps = pd.date_range("2024-01-01 00:15", periods=96, freq="15min").to_series()
    gapped = pd.concat([stamps.iloc[:20], stamps.iloc[60:]])
    assert detect_interval_minutes(gapped) == 15


def test_grid_phase_uses_mode_not_first_row() -> None:
    """첫 행이 비정규 시각이어도 전체 위상이 어긋나지 않아야 한다."""
    stamps = pd.Series(
        [
            pd.Timestamp("2024-01-01 00:07"),
            *pd.date_range("2024-01-01 00:15", periods=95, freq="15min"),
        ]
    )
    assert detect_grid_phase_seconds(stamps, 15) == 0


def test_single_row_cannot_detect_interval(tmp_path: Path) -> None:
    path = write_csv(tmp_path / "one.csv", [("2024-01-01 00:15", 10.0)])
    with pytest.raises(UsageLoadError, match="간격"):
        load_usage(path)


# --------------------------------------------------------------------- 그리드 이탈


def test_off_grid_rows_excluded_from_kw_but_kept_in_kwh(tmp_path: Path) -> None:
    """그리드 이탈 2건은 kW 산정에서 빠지고 kWh 합계에는 들어간다 (요구사항서 4.3)."""
    rows = [(label, 100.0) for label in make_labels("2024-01-01", 15)]
    rows.append(("2024-01-01 19:29", 43.20))  # 14분 부분 적산
    rows.append(("2024-01-01 03:51", 0.72))
    usage = load_usage(write_csv(tmp_path / "off.csv", rows))

    meta = usage.meta
    assert meta.raw_rows == 98
    assert meta.valid_rows == 96
    assert meta.off_grid_rows == 2
    assert meta.expected_rows == 96
    assert meta.missing_rows == 0

    # kW 시리즈에는 이탈 행의 시각이 아예 없다
    assert pd.Timestamp("2024-01-01 19:29") not in usage.kw.index
    assert usage.kw.max() == pytest.approx(400.0)  # 43.2 kWh × 4 = 172.8 kW 가 섞이지 않는다
    assert usage.kw.min() == pytest.approx(400.0)

    # kWh 합계에는 포함된다
    assert meta.off_grid_kwh == pytest.approx(43.92)
    assert meta.total_kwh == pytest.approx(96 * 100.0 + 43.92)
    assert usage.total_kwh == pytest.approx(9643.92)

    # 값까지 보존한다
    assert list(usage.off_grid["kwh"]) == pytest.approx([0.72, 43.20])
    assert any("그리드 이탈" in message for message in meta.warnings)


def test_missing_slots_are_not_interpolated(tmp_path: Path) -> None:
    labels = make_labels("2024-01-01", 15)
    del labels[10:14]  # 4슬롯 결측
    usage = load_usage(write_csv(tmp_path / "m.csv", [(label, 100.0) for label in labels]))
    assert usage.meta.expected_rows == 96
    assert usage.meta.valid_rows == 92
    assert usage.meta.missing_rows == 4
    assert usage.meta.missing_ratio == pytest.approx(4 / 96)
    assert usage.kw.isna().sum() == 4  # 보간하지 않는다


def test_duplicate_timestamps_are_summed(tmp_path: Path) -> None:
    rows = [(label, 100.0) for label in make_labels("2024-01-01", 15)]
    rows.append(("2024-01-01 12:00", 50.0))
    usage = load_usage(write_csv(tmp_path / "d.csv", rows))
    assert usage.meta.duplicate_rows == 1
    assert usage.kwh_grid.loc[pd.Timestamp("2024-01-01 12:00")] == pytest.approx(150.0)


# --------------------------------------------------------------------- 실측 샘플 (부록 B)


@pytest.fixture(scope="module")
def sample(sample_usage_path: Path) -> UsageData:
    return load_usage(sample_usage_path)


def test_sample_shape_matches_appendix_b(sample: UsageData) -> None:
    m = sample.meta
    assert m.encoding == "utf-8-sig"
    assert m.date_column == "검침일"
    assert m.energy_column == "순방향 유효전력량(KWH)"
    assert m.interval_minutes == 15
    assert m.grid_phase_seconds == 0
    assert m.start == pd.Timestamp("2023-04-25 00:15")
    assert m.end == pd.Timestamp("2024-04-27 00:00")
    assert m.raw_rows == 34_358
    assert m.valid_rows == 34_356
    assert m.off_grid_rows == 2
    assert m.duplicate_rows == 0
    assert m.hour24_rows == 357
    assert m.expected_rows == 35_328
    assert m.missing_rows == 972
    assert m.missing_ratio == pytest.approx(0.028, abs=0.001)


def test_sample_statistics_match_appendix_b(sample: UsageData) -> None:
    m = sample.meta
    assert m.max_demand_kw == pytest.approx(5_293.4, abs=0.1)
    assert m.mean_kw == pytest.approx(2_594.6, abs=0.1)
    assert m.load_factor == pytest.approx(0.490, abs=0.001)
    # 연간 사용량 22,285 MWh — 그리드 이탈 행의 kWh 를 포함한 값이다
    assert m.total_kwh / 1_000_000 == pytest.approx(22.285, abs=0.001)


def test_sample_peak_timestamp(sample: UsageData) -> None:
    assert sample.meta.max_demand_at == pd.Timestamp("2023-07-03 09:30")
    assert sample.meta.max_demand_at.day_name() == "Monday"


def test_sample_off_grid_rows(sample: UsageData) -> None:
    """부록 B 의 정전 기록 2건. 값까지 보존한다."""
    off = sample.off_grid
    assert list(off["timestamp"]) == [
        pd.Timestamp("2024-04-06 19:29"),
        pd.Timestamp("2024-04-07 03:51"),
    ]
    assert list(off["kwh"]) == pytest.approx([43.20, 0.00])
    assert sample.meta.off_grid_kwh == pytest.approx(43.20)


def test_sample_kw_index_is_tz_naive_and_complete(sample: UsageData) -> None:
    index = sample.kw.index
    assert index.tz is None
    assert isinstance(index, pd.DatetimeIndex)
    assert index.freqstr == "15min"
    assert len(index) == 35_328


def test_sample_low_load_slot(sample: UsageData) -> None:
    """부록 B — 100 kW 미만 1건, 0 kW 구간 없음."""
    observed = sample.kw.dropna()
    assert (observed == 0).sum() == 0
    low = observed[observed < 100]
    assert len(low) == 1
    assert low.index[0] == pd.Timestamp("2024-04-07 06:00")
    assert low.iloc[0] == pytest.approx(2.88)


# --------------------------------------------------------------------- total_kwh 강제 장치


def off_grid_usage(tmp_path: Path) -> UsageData:
    """그리드 이탈 2건이 있는 하루치 데이터."""
    rows = [(label, 100.0) for label in make_labels("2024-01-01", 15)]
    rows.append(("2024-01-01 19:29", 43.20))
    rows.append(("2024-01-01 03:51", 0.72))
    return load_usage(write_csv(tmp_path / "guard.csv", rows))


def test_kwh_grid_sum_is_blocked_when_off_grid_exists(tmp_path: Path) -> None:
    """kwh_grid.sum() 은 이탈분을 빠뜨리므로 막는다. 43.92 kWh 는 눈에 띄지 않는다."""
    usage = off_grid_usage(tmp_path)
    with pytest.raises(OffGridEnergyError, match="total_kwh"):
        usage.kwh_grid.sum()


def test_kwh_grid_partial_sum_is_blocked_too(tmp_path: Path) -> None:
    """슬라이스에도 장치가 따라붙는다. 월별·시간대별 부분합이 더 위험하다."""
    usage = off_grid_usage(tmp_path)
    with pytest.raises(OffGridEnergyError):
        usage.kwh_grid.loc["2024-01-01 12:00":"2024-01-01 20:00"].sum()


def test_grid_only_sum_is_allowed_when_declared(tmp_path: Path) -> None:
    usage = off_grid_usage(tmp_path)
    assert usage.kwh_grid.sum(grid_only=True) == pytest.approx(9600.0)


def test_sum_is_not_blocked_without_off_grid_rows(tmp_path: Path) -> None:
    """이탈 행이 없으면 합계가 정확하므로 막지 않는다. 거짓 경보를 만들지 않는다."""
    usage = load_usage(one_day(tmp_path / "clean.csv"))
    assert usage.kwh_grid.sum() == pytest.approx(9600.0)
    assert usage.total_kwh == pytest.approx(9600.0)


def test_old_kwh_attribute_points_to_replacement(tmp_path: Path) -> None:
    usage = off_grid_usage(tmp_path)
    with pytest.raises(AttributeError, match="kwh_grid"):
        _ = usage.kwh


def test_energy_kwh_sums_to_total(tmp_path: Path) -> None:
    """energy_kwh() 는 이탈분을 그 구간 라벨에 얹는다. 합계가 total_kwh 와 같다."""
    usage = off_grid_usage(tmp_path)
    energy = usage.energy_kwh()
    assert energy.sum() == pytest.approx(usage.total_kwh)
    # 19:29 → 19:30 구간, 03:51 → 04:00 구간
    assert energy.loc[pd.Timestamp("2024-01-01 19:30")] == pytest.approx(143.20)
    assert energy.loc[pd.Timestamp("2024-01-01 04:00")] == pytest.approx(100.72)
    assert usage.kw.loc[pd.Timestamp("2024-01-01 19:30")] == pytest.approx(400.0)  # kW 는 그대로


def test_energy_kwh_can_exclude_off_grid(tmp_path: Path) -> None:
    usage = off_grid_usage(tmp_path)
    assert usage.energy_kwh(include_off_grid=False).sum() == pytest.approx(9600.0)


def test_sample_energy_kwh_matches_total(sample: UsageData) -> None:
    """실측 샘플 — 이탈 행이 결측 슬롯에 얹혀도 총합이 어긋나지 않는다."""
    energy = sample.energy_kwh()
    assert energy.sum() == pytest.approx(sample.total_kwh)
    # 2024-04-06 19:29 의 43.2 kWh 는 19:30 슬롯(결측)에 얹힌다
    assert energy.loc[pd.Timestamp("2024-04-06 19:30")] == pytest.approx(43.20)
    assert pd.isna(sample.kw.loc[pd.Timestamp("2024-04-06 19:30")])


def test_slot_start_applies_label_convention() -> None:
    """라벨은 구간 끝이다. 15:00 라벨은 14:45 에 시작한 구간이다."""
    index = pd.DatetimeIndex(["2023-07-03 15:00", "2023-07-03 15:15"])
    starts = slot_start(index, 15)
    assert list(starts) == [pd.Timestamp("2023-07-03 14:45"), pd.Timestamp("2023-07-03 15:00")]


# --------------------------------------------------------------------- kW 환산 차단


def test_energy_kwh_cannot_be_multiplied_into_kw(tmp_path: Path) -> None:
    """energy_kwh() × 4 는 kW 가 아니다. 이탈분이 얹힌 구간은 위상이 다르다."""
    usage = off_grid_usage(tmp_path)
    energy = usage.energy_kwh()
    with pytest.raises(EnergyToDemandError, match=r"UsageData\.kw"):
        _ = energy * 4
    with pytest.raises(EnergyToDemandError):
        _ = 4 * energy
    with pytest.raises(EnergyToDemandError):
        _ = energy / 0.25
    with pytest.raises(EnergyToDemandError):
        energy.to_kw()


def test_energy_kwh_still_supports_normal_arithmetic(tmp_path: Path) -> None:
    """막는 것은 kW 환산뿐이다. 단가를 곱하는 계산은 그대로 된다."""
    usage = off_grid_usage(tmp_path)
    energy = usage.energy_kwh()
    assert float((energy * 145.7).sum()) == pytest.approx(usage.total_kwh * 145.7)
    assert float((energy * 2).sum()) == pytest.approx(usage.total_kwh * 2)
    assert float(energy.sum()) == pytest.approx(usage.total_kwh)


def test_energy_kwh_guard_survives_slicing(tmp_path: Path) -> None:
    usage = off_grid_usage(tmp_path)
    window = usage.energy_kwh().loc["2024-01-01 12:00":"2024-01-01 20:00"]
    with pytest.raises(EnergyToDemandError):
        _ = window * 4


def test_sample_energy_kwh_blocks_kw_conversion(sample: UsageData) -> None:
    with pytest.raises(EnergyToDemandError, match=r"요구사항서 4\.3"):
        _ = sample.energy_kwh() * 4
