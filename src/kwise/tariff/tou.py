"""계절·시간대 분류기 (요구사항서 5.3).

**귀속은 구간 시작 시각으로 판정한다.** 검침일 라벨은 구간 끝이므로
``15:00`` 슬롯은 ``14:45~15:00`` 사용량이고 중간부하다. 첫 최대부하 슬롯은
``15:15`` 이다. :func:`kwise.io.slot_start` 를 반드시 거친다.

요일 규칙
    토요일   최대부하 → 중간부하로 계량
    일요일   전량 경부하로 계량 (``holidays`` 가 일요일을 담지 않으므로 직접 더한다)
    공휴일   전량 경부하로 계량
"""

from __future__ import annotations

from enum import StrEnum

import numpy as np
import pandas as pd

from kwise.io import slot_start
from kwise.tariff.holiday import HolidayCalendar
from kwise.tariff.schema import DEFAULT_REGION_GROUP, TariffDataError, TariffTable

__all__ = [
    "Band",
    "DayType",
    "classify_slots",
]


class Band(StrEnum):
    """시간대."""

    LIGHT = "light"
    MID = "mid"
    PEAK = "peak"


class DayType(StrEnum):
    """요일 구분. 일요일은 공휴일로 접힌다 (토글이 켜져 있을 때)."""

    WEEKDAY = "weekday"
    SATURDAY = "saturday"
    HOLIDAY = "holiday"


def _apply_day_rule(bands: np.ndarray, mask: np.ndarray, rule: str, target: str) -> None:
    """요일 규칙 한 줄을 적용한다. 알 수 없는 규칙은 조용히 넘기지 않는다."""
    if not mask.any():
        return
    if rule == "peak_to_mid":
        bands[mask & (bands == Band.PEAK.value)] = Band.MID.value
    elif rule == "all_to_light":
        bands[mask] = Band.LIGHT.value
    elif rule == "none":
        return
    else:
        raise TariffDataError(f"알 수 없는 요일 규칙입니다: day_rules.{target} = {rule!r}")


def _discount_rates(
    table: TariffTable,
    *,
    contract_type: str | None,
    seasons: np.ndarray,
    hours: np.ndarray,
    is_saturday: np.ndarray,
    is_sunday: np.ndarray,
    is_public_holiday: np.ndarray,
) -> np.ndarray:
    """특례 할인율 (요구사항서 5.6).

    산업용(을) 봄·가을철 주말 할인처럼 PV 최성기와 겹치는 특례가 있다.
    미반영 시 PV 절감 효과가 과대평가된다.
    """
    rates = np.zeros(len(hours), dtype=float)
    if contract_type is None:
        return rates
    day_flags = {
        "saturday": is_saturday,
        "sunday": is_sunday,
        "holiday": is_public_holiday,
        "weekday": ~(is_saturday | is_sunday | is_public_holiday),
    }
    for rule in table.special_rules.values():
        if contract_type not in rule.applies_to:
            continue
        season_mask = np.isin(seasons, np.asarray(rule.seasons, dtype=object))
        day_mask = np.zeros(len(hours), dtype=bool)
        for token in rule.days:
            if token not in day_flags:
                raise TariffDataError(f"알 수 없는 요일 토큰입니다: {token!r}")
            day_mask |= day_flags[token]
        hour_mask = np.zeros(len(hours), dtype=bool)
        for start, end in rule.hours:
            hour_mask |= (hours >= start) & (hours < end)
        rates[season_mask & day_mask & hour_mask] = rule.discount_rate
    return rates


def classify_slots(
    index: pd.DatetimeIndex,
    interval_minutes: int,
    table: TariffTable,
    calendar: HolidayCalendar,
    *,
    contract_type: str | None = None,
    region_group: str = DEFAULT_REGION_GROUP,
) -> pd.DataFrame:
    """슬롯마다 계절·시간대·요일 구분을 붙인다.

    Args:
        index: 검침 라벨 (구간 끝).
        interval_minutes: 검침 간격.
        contract_type: 특례 적용 판정용. None 이면 할인 없음.

    Returns:
        라벨을 인덱스로 하는 DataFrame.
        ``slot_start``, ``month``, ``season``, ``base_band``, ``band``,
        ``day_type``, ``is_holiday``, ``discount_rate``.
    """
    starts = slot_start(pd.DatetimeIndex(index), interval_minutes)
    hours = np.asarray(starts.hour, dtype=int)
    weekday = np.asarray(starts.weekday, dtype=int)
    months = np.asarray(starts.month, dtype=int)

    seasons = np.asarray([table.season_of(month) for month in months], dtype=object)
    base_bands = np.empty(len(index), dtype=object)
    for season in np.unique(seasons):
        mask = seasons == season
        lookup = table.hour_bands[region_group][str(season)]
        if any(band is None for band in lookup):
            missing = [hour for hour, band in enumerate(lookup) if band is None]
            raise TariffDataError(
                f"{region_group}/{season} 의 시간대 정의에 공백이 있습니다: {missing}"
            )
        base_bands[mask] = np.asarray(lookup, dtype=object)[hours[mask]]

    is_public_holiday = np.asarray(starts.normalize().isin(calendar.holiday_index()), dtype=bool)
    is_saturday = weekday == 5
    is_sunday = weekday == 6
    is_holiday = is_public_holiday | (is_sunday if calendar.sunday_is_holiday else False)

    day_types = np.full(len(index), DayType.WEEKDAY.value, dtype=object)
    day_types[is_saturday] = DayType.SATURDAY.value
    day_types[is_holiday] = DayType.HOLIDAY.value

    bands = base_bands.copy()
    # 공휴일 규칙을 나중에 적용해 토요일이면서 공휴일인 날이 경부하로 가게 한다.
    _apply_day_rule(bands, is_saturday & ~is_holiday, table.day_rules.saturday, "saturday")
    _apply_day_rule(bands, is_sunday & calendar.sunday_is_holiday, table.day_rules.sunday, "sunday")
    _apply_day_rule(bands, is_public_holiday, table.day_rules.holiday, "holiday")

    discount = _discount_rates(
        table,
        contract_type=contract_type,
        seasons=seasons,
        hours=hours,
        is_saturday=is_saturday,
        is_sunday=is_sunday,
        is_public_holiday=is_public_holiday,
    )

    return pd.DataFrame(
        {
            "slot_start": starts,
            "month": starts.to_period("M"),
            "season": seasons,
            "base_band": base_bands,
            "band": bands,
            "day_type": day_types,
            "is_holiday": is_holiday,
            "discount_rate": discount,
        },
        index=pd.DatetimeIndex(index),
    )
