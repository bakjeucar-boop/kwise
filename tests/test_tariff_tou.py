"""계절·시간대 분류 (요구사항서 5.3, 11.2).

경계값이 핵심이다. 라벨은 구간 끝이므로 ``15:00`` 슬롯은 ``14:45~15:00`` 사용량이고
중간부하다. 첫 최대부하 슬롯은 ``15:15`` 이다. 여기서 한 칸 밀리면 요금이 통째로 어긋난다.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Sequence
from typing import Any

import pandas as pd
import pytest

from kwise.tariff import (
    Band,
    DayType,
    TariffTable,
    build_calendar,
    classify_slots,
    default_tariff_dir,
    parse_tariff,
)
from tests._synthetic import make_labels, parse_label

INTERVAL = 15


def classify(
    labels: Sequence[str],
    tariff: TariffTable,
    *,
    sunday_is_holiday: bool = True,
    extra_holidays: Sequence[str] = (),
    excluded_holidays: Sequence[str] = (),
    contract_type: str | None = None,
) -> pd.DataFrame:
    index = pd.DatetimeIndex([parse_label(label) for label in labels])
    calendar = build_calendar(
        {stamp.year for stamp in index} | {stamp.year - 1 for stamp in index},
        sunday_is_holiday=sunday_is_holiday,
        extra_holidays=extra_holidays,
        excluded_holidays=excluded_holidays,
    )
    return classify_slots(index, INTERVAL, tariff, calendar, contract_type=contract_type)


def bands(labels: Sequence[str], tariff: TariffTable, **kwargs: Any) -> list[str]:
    return list(classify(labels, tariff, **kwargs)["band"])


# --------------------------------------------------------------------- 라벨 규약


def test_label_is_the_end_of_the_interval(tariff: TariffTable) -> None:
    """15:00 라벨은 14:45 에 시작한 구간이다. 그래서 중간부하다."""
    frame = classify(["2023-07-05 15:00", "2023-07-05 15:15"], tariff)
    assert list(frame["slot_start"]) == [
        pd.Timestamp("2023-07-05 14:45"),
        pd.Timestamp("2023-07-05 15:00"),
    ]
    assert list(frame["band"]) == [Band.MID, Band.PEAK]


# --------------------------------------------------------------------- 계절 경계


def test_season_boundary_at_june_first(tariff: TariffTable) -> None:
    """5/31 23시대는 봄·가을철, 6/1 0시부터 여름철.

    ``2023-06-01 00:00`` 라벨은 5월 31일 23:45 에 시작한 구간이므로 아직 봄·가을철이다.
    """
    frame = classify(["2023-05-31 23:45", "2023-06-01 00:00", "2023-06-01 00:15"], tariff)
    assert list(frame["season"]) == ["spring_fall", "spring_fall", "summer"]


def test_season_boundary_at_september_first(tariff: TariffTable) -> None:
    frame = classify(["2023-09-01 00:00", "2023-09-01 00:15"], tariff)
    assert list(frame["season"]) == ["summer", "spring_fall"]


def test_season_boundary_at_november_first(tariff: TariffTable) -> None:
    frame = classify(["2023-11-01 00:00", "2023-11-01 00:15"], tariff)
    assert list(frame["season"]) == ["spring_fall", "winter"]


# --------------------------------------------------------------------- 시간대 경계


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("2023-07-05 08:00", Band.LIGHT),  # 07:45 시작 — 아직 경부하
        ("2023-07-05 08:15", Band.MID),
        ("2023-07-05 15:00", Band.MID),  # 14:45 시작 — 아직 중간부하
        ("2023-07-05 15:15", Band.PEAK),  # 첫 최대부하 슬롯
        ("2023-07-05 21:00", Band.PEAK),
        ("2023-07-05 21:15", Band.MID),
        ("2023-07-05 22:00", Band.MID),
        ("2023-07-05 22:15", Band.LIGHT),
        ("2023-07-06 00:00", Band.LIGHT),  # 전날 23:45 시작
    ],
)
def test_summer_weekday_band_boundaries(tariff: TariffTable, label: str, expected: str) -> None:
    assert bands([label], tariff) == [expected]


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("2023-12-06 09:00", Band.MID),  # 08:45 시작
        ("2023-12-06 09:15", Band.PEAK),
        ("2023-12-06 12:00", Band.PEAK),  # 11:45 시작 — 겨울 12시 경계
        ("2023-12-06 12:15", Band.MID),
        ("2023-12-06 16:00", Band.MID),  # 15:45 시작
        ("2023-12-06 16:15", Band.PEAK),
        ("2023-12-06 19:00", Band.PEAK),
        ("2023-12-06 19:15", Band.MID),
        ("2023-12-06 22:15", Band.LIGHT),
    ],
)
def test_winter_weekday_band_boundaries(tariff: TariffTable, label: str, expected: str) -> None:
    assert bands([label], tariff) == [expected]


def test_spring_fall_uses_summer_shape(tariff: TariffTable) -> None:
    assert bands(["2023-04-05 15:15"], tariff) == [Band.PEAK]
    assert bands(["2023-04-05 12:00"], tariff) == [Band.MID]


# --------------------------------------------------------------------- 요일 규칙


def test_saturday_peak_becomes_mid(tariff: TariffTable) -> None:
    frame = classify(["2023-07-08 15:15", "2023-07-08 03:00", "2023-07-08 09:00"], tariff)
    assert list(frame["band"]) == [Band.MID, Band.LIGHT, Band.MID]
    assert list(frame["base_band"]) == [Band.PEAK, Band.LIGHT, Band.MID]
    assert set(frame["day_type"]) == {DayType.SATURDAY}


def test_sunday_is_counted_as_holiday(tariff: TariffTable) -> None:
    """holidays 라이브러리는 일요일을 담지 않는다. 직접 더해야 한다."""
    frame = classify(["2023-07-09 15:15", "2023-07-09 09:00"], tariff)
    assert list(frame["band"]) == [Band.LIGHT, Band.LIGHT]
    assert set(frame["day_type"]) == {DayType.HOLIDAY}
    assert bool(frame["is_holiday"].all())


def test_sunday_toggle_can_be_turned_off(tariff: TariffTable) -> None:
    frame = classify(["2023-07-09 15:15"], tariff, sunday_is_holiday=False)
    assert list(frame["band"]) == [Band.PEAK]
    assert list(frame["day_type"]) == [DayType.WEEKDAY]


def test_missing_sunday_rule_would_mismeter_52_days(tariff: TariffTable) -> None:
    """일요일을 빠뜨리면 연 52일이 최대부하로 잘못 계량된다.

    2023-07 은 일요일이 5일이고 하루 최대부하가 24슬롯이라 120슬롯 차이가 난다.
    """
    labels = [
        label
        for date in [f"2023-07-{day:02d}" for day in range(1, 32)]
        for label in make_labels(date)
    ]
    with_sunday = bands(labels, tariff)
    without_sunday = bands(labels, tariff, sunday_is_holiday=False)
    assert with_sunday.count(Band.PEAK) == 504  # 평일 21일 × 24슬롯
    assert without_sunday.count(Band.PEAK) == 624  # + 일요일 5일 × 24슬롯
    assert without_sunday.count(Band.PEAK) - with_sunday.count(Band.PEAK) == 120


def test_public_holiday_is_all_light(tariff: TariffTable) -> None:
    """2023-06-06 현충일 (화)."""
    frame = classify(["2023-06-06 15:15", "2023-06-06 09:00"], tariff)
    assert list(frame["band"]) == [Band.LIGHT, Band.LIGHT]
    assert list(frame["day_type"]) == [DayType.HOLIDAY, DayType.HOLIDAY]


def test_holiday_wins_over_saturday(tariff: TariffTable) -> None:
    """2023-09-30 은 토요일이면서 추석 다음날이다. 경부하로 계량한다."""
    frame = classify(["2023-09-30 15:15"], tariff)
    assert list(frame["band"]) == [Band.LIGHT]
    assert list(frame["day_type"]) == [DayType.HOLIDAY]


# --------------------------------------------------------------------- 임시공휴일


def test_temporary_holiday_is_excluded_by_default(tariff: TariffTable) -> None:
    """2023-10-02 임시공휴일은 기본적으로 평일로 계량한다."""
    frame = classify(["2023-10-02 15:15"], tariff)
    assert list(frame["band"]) == [Band.PEAK]
    assert list(frame["day_type"]) == [DayType.WEEKDAY]


def test_user_can_add_a_temporary_holiday(tariff: TariffTable) -> None:
    frame = classify(["2023-10-02 15:15"], tariff, extra_holidays=["2023-10-02"])
    assert list(frame["band"]) == [Band.LIGHT]
    assert list(frame["day_type"]) == [DayType.HOLIDAY]


def test_user_can_remove_a_holiday(tariff: TariffTable) -> None:
    """2023-10-03 개천절을 빼면 평일로 계량된다."""
    frame = classify(["2023-10-03 15:15"], tariff, excluded_holidays=["2023-10-03"])
    assert list(frame["band"]) == [Band.PEAK]


def test_calendar_records_what_it_dropped() -> None:
    calendar = build_calendar([2023])
    assert pd.Timestamp("2023-10-02").date() in calendar.excluded_temporary
    assert not calendar.is_holiday("2023-10-02")
    assert calendar.is_holiday("2023-10-03")
    assert calendar.is_holiday("2023-07-09")  # 일요일
    assert not calendar.is_holiday("2023-07-05")


def test_calendar_can_keep_temporary_holidays() -> None:
    calendar = build_calendar([2023], exclude_temporary=False)
    assert calendar.is_holiday("2023-10-02")


# --------------------------------------------------------------------- 특례 (5.6)


@pytest.fixture(scope="module")
def industrial_table() -> TariffTable:
    """산업용(을) 주말 할인 특례를 시험하기 위한 합성 요금표.

    PoC 요금 데이터에는 산업용이 없다 (부록 A.4). 단가는 일반용(을)을 빌리고
    특례 적용 여부만 본다. **배포되는 JSON 은 건드리지 않는다.**
    """
    path = default_tariff_dir() / "tariff_kr_20260601.json"
    with path.open(encoding="utf-8") as stream:
        payload: dict[str, Any] = json.load(stream)
    copied = copy.deepcopy(payload)
    industrial = copy.deepcopy(copied["contract_types"]["general_b"])
    industrial["label"] = "산업용전력(을)"
    copied["contract_types"]["industrial_b"] = industrial
    return parse_tariff(copied)


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("2023-04-15 11:00", 0.0),  # 10:45 시작 — 아직 아니다
        ("2023-04-15 11:15", 0.5),  # 11:00 시작
        ("2023-04-15 14:00", 0.5),  # 13:45 시작 — 마지막 슬롯
        ("2023-04-15 14:15", 0.0),  # 14:00 시작 — 끝났다
    ],
)
def test_industrial_weekend_discount_hours(
    industrial_table: TariffTable, label: str, expected: float
) -> None:
    """봄·가을철 토·일·공휴일 11~14시 전력량요금 50% 할인.

    PV 최성기와 겹치므로 미반영 시 PV 절감 효과가 과대평가된다.
    """
    frame = classify([label], industrial_table, contract_type="industrial_b")
    assert frame["discount_rate"].iloc[0] == pytest.approx(expected)


def test_industrial_discount_only_on_weekends_and_spring_fall(
    industrial_table: TariffTable,
) -> None:
    weekday = classify(["2023-04-14 12:00"], industrial_table, contract_type="industrial_b")
    summer_weekend = classify(["2023-07-15 12:00"], industrial_table, contract_type="industrial_b")
    holiday = classify(["2023-10-03 12:00"], industrial_table, contract_type="industrial_b")
    assert weekday["discount_rate"].iloc[0] == 0.0
    assert summer_weekend["discount_rate"].iloc[0] == 0.0
    assert holiday["discount_rate"].iloc[0] == pytest.approx(0.5)


def test_discount_does_not_apply_to_general_b(industrial_table: TariffTable) -> None:
    """특례는 applies_to 에 적힌 종별에만 붙는다."""
    frame = classify(["2023-04-15 12:00"], industrial_table, contract_type="general_b")
    assert frame["discount_rate"].iloc[0] == 0.0


# --------------------------------------------------------------------- 샘플 정합


def test_sample_classification_covers_every_slot(sample_usage: object, tariff: TariffTable) -> None:
    """실측 샘플 35,328 슬롯이 모두 분류된다."""
    usage = sample_usage
    index = pd.DatetimeIndex(usage.kw.index)  # type: ignore[attr-defined]
    calendar = build_calendar(range(2022, 2026))
    frame = classify_slots(index, 15, tariff, calendar)
    assert len(frame) == 35_328
    assert set(frame["band"]) <= {Band.LIGHT, Band.MID, Band.PEAK}
    assert not frame["band"].isna().any()
    assert set(frame["season"]) == {"summer", "spring_fall", "winter"}
    # 주말·공휴일에는 최대부하가 없어야 한다
    peak = frame[frame["band"] == Band.PEAK]
    assert set(peak["day_type"]) == {DayType.WEEKDAY}
