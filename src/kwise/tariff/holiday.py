"""공휴일 달력 (요구사항서 5.3).

**일요일도 공휴일로 계량한다.** ``holidays`` 라이브러리는 일요일을 담지 않는데,
관공서 공휴일 규정에는 일요일이 포함된다. 빠뜨리면 연 52일이 최대부하로 잘못
계량되어 요금이 과대 산출된다. 청구서를 확보하면 이 항목을 가장 먼저 대조한다.

임시공휴일은 기본적으로 뺀다 (요금표 적용 관행). 사용자가 날짜를 더하거나
뺄 수 있게 ``extra_holidays``·``excluded_holidays`` 를 둔다.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass, field

import holidays
import pandas as pd

__all__ = [
    "DEFAULT_COUNTRY",
    "TEMPORARY_HOLIDAY_MARKER",
    "HolidayCalendar",
    "build_calendar",
]

DEFAULT_COUNTRY = "KR"
# holidays 라이브러리가 임시공휴일에 붙이는 이름. 예: 2023-10-02 '임시공휴일'
TEMPORARY_HOLIDAY_MARKER = "임시"

# pd.Timestamp 은 datetime.date 의 하위형이라 dt.date 로 받아진다.
type DateLike = dt.date | str


def _as_date(value: DateLike) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return pd.Timestamp(value).date()


@dataclass(frozen=True)
class HolidayCalendar:
    """공휴일 판정기.

    ``dates`` 에는 일요일이 들어 있지 않다. 일요일은 :attr:`sunday_is_holiday`
    토글로 판정 시점에 더한다. 52~53개 날짜를 미리 담지 않기 위함이다.
    """

    dates: frozenset[dt.date]
    sunday_is_holiday: bool = True
    added: tuple[dt.date, ...] = field(default=())
    removed: tuple[dt.date, ...] = field(default=())
    excluded_temporary: tuple[dt.date, ...] = field(default=())

    def is_holiday(self, day: DateLike) -> bool:
        """공휴일인가. ``sunday_is_holiday`` 가 켜져 있으면 일요일도 참이다."""
        date = _as_date(day)
        if date in self.dates:
            return True
        return self.sunday_is_holiday and date.weekday() == 6

    def holiday_index(self) -> pd.DatetimeIndex:
        """일요일을 뺀 공휴일 목록. 시계열 분류에서 ``isin`` 으로 쓴다."""
        return pd.DatetimeIndex(sorted(pd.Timestamp(date) for date in self.dates))


def build_calendar(
    years: Iterable[int],
    *,
    sunday_is_holiday: bool = True,
    exclude_temporary: bool = True,
    extra_holidays: Iterable[DateLike] = (),
    excluded_holidays: Iterable[DateLike] = (),
    country: str = DEFAULT_COUNTRY,
) -> HolidayCalendar:
    """공휴일 달력을 만든다.

    Args:
        years: 대상 연도. 보통 데이터 기간의 연도들이다.
        sunday_is_holiday: 일요일을 공휴일로 계량할지. **기본은 켬.**
        exclude_temporary: 임시공휴일을 뺄지 (요금 데이터 ``day_rules`` 와 맞춘다).
        extra_holidays: 사용자가 더할 날짜. 임시공휴일을 되살릴 때 쓴다.
        excluded_holidays: 사용자가 뺄 날짜.
    """
    year_list = sorted({int(year) for year in years})
    source = holidays.country_holidays(country, years=year_list)

    dates: set[dt.date] = set()
    temporary: list[dt.date] = []
    for date, name in source.items():
        if exclude_temporary and TEMPORARY_HOLIDAY_MARKER in str(name):
            temporary.append(date)
            continue
        dates.add(date)

    added = tuple(sorted(_as_date(value) for value in extra_holidays))
    removed = tuple(sorted(_as_date(value) for value in excluded_holidays))
    dates.update(added)
    dates.difference_update(removed)

    return HolidayCalendar(
        dates=frozenset(dates),
        sunday_is_holiday=sunday_is_holiday,
        added=added,
        removed=removed,
        excluded_temporary=tuple(sorted(temporary)),
    )
