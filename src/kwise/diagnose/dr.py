"""경제성DR 참여 여력 진단 (요구사항서 6.6).

근거는 ``data\\source\\전력시장운영규칙.pdf`` 제12장 「수요반응자원의 거래」다.

    신뢰성DR   수급 비상 시 **의무** 감축. 용량요금 + 실적금.      → 범위 밖
    경제성DR   하루 전 **자발적** 입찰. 실적금만. 설비 투자 불필요.  → 대상

**연간 참여 일수 제한은 없다** (14세션에 바로잡았다). 13세션에 넣었던 「연 60시간
한도」는 전력거래소 확인 결과 경제성DR 의 제약이 아니다. 기회가 있을 때마다
참여할 수 있다.

남는 제약은 넷이다.

    하루 한도    최대 2회, 1회 1~4시간 — 하루 8시간
    참여 요일    평일만. 토·일·공휴일 제외 (제12.4.2.1조 제1항 1호)
    운영 시간대  09:00~20:00, 점심(12:00~13:00) 제외
    미이행 제재  **6개월 입찰 제한.** 과대 산정의 대가가 크다

**따라서 실질 제약은 「감축할 여력이 있는 날이 며칠이냐」 하나다.** 그 날을
데이터에서 찾는다.

    ① 기준선     주말·공휴일의 운영 시간대 평균 부하. 건물이 사실상 비어 있는 수준
    ② 저부하 평일 대상일 중 그 시간대 평균이 기준선의 일정 배수 이하인 날
    ③ 감축 여력   평일 정상 평균 − 그날 실제 부하
    ④ 참여 시간   하루 8시간을 상한으로 하되 저부하가 실제로 지속되는 시간까지
    ⑤ 감축 가능량 Σ(저부하일별 감축 여력 × 그날 참여 가능 시간)

**보수적으로 잡는다.** 미이행이 6개월 입찰 제한이라 과대 산정의 대가가 크다.
등록 권장 용량은 저부하일 여력 분포의 **하위값**이다 — 평균으로 등록하면 절반의
날에 미달한다.

**거래일 제약이 이 모듈의 두 번째 축이다 (제12.4.2.1조 제1항 1호).**

    "관공서의 공휴일에 관한 규정"의 공휴일과 **토요일**을 제외한 평일의
    거래일에만 입찰할 수 있다.

일요일은 공휴일 규정에 포함되므로 자동으로 빠진다. 이 제약을 빼면 감축 가능량이
**30% 이상 과대평가**된다 (365일 중 대상일이 245일 안팎이다).

**요금 계량의 '평일' 과 정의가 다르다.** 요금 엔진은 토요일을 공휴일로 보지 않고
(최대부하 → 중간부하로 낮출 뿐) 일요일만 공휴일로 계량한다. DR 은 토·일·공휴일이
모두 똑같이 제외다. 그래서 :func:`dr_day_mask` 를 :mod:`kwise.tariff.tou` 와
**따로 둔다.** 같은 함수로 판정하면 두 규칙이 조용히 섞인다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

import pandas as pd

from kwise.io import slot_start
from kwise.rules import assumption, rule_value
from kwise.tariff import HolidayCalendar

__all__ = [
    "PARTICIPATION_NOTICE",
    "DrPotential",
    "DrProfile",
    "DrResourceType",
    "default_high_capacity_kw",
    "dr_bid_restriction_months",
    "dr_daily_hours_cap",
    "dr_day_mask",
    "dr_eligible_days",
    "dr_event_hours",
    "dr_max_events_per_day",
    "dr_operating_windows",
    "dr_profile",
    "dr_reference_capacity_kw",
    "judge_resource_types",
    "low_load_multiple",
    "national_dr_max_contract_kw",
    "registration_percentile",
    "resource_type_labels",
    "small_medium_dr_industrial_max_kw",
]

PARTICIPATION_NOTICE = (
    "연간 참여 일수 제한은 없으나 하루 최대 2회(총 {daily:,.0f}시간)이며 평일 "
    "{window}에만 가능합니다. 낙찰 후 감축을 이행하지 못하면 {months:,.0f}개월 "
    "입찰 제한을 받을 수 있으므로 감축 가능량은 보수적으로 산정했습니다. "
    "실제 참여는 수요관리사업자와 상담해 결정하십시오."
)
"""화면·산출물이 같이 쓰는 안내 (14세션 4절). 점심시간은 운영 시간대에서 빠진다."""

# 값은 파일에 있다 (요구사항서 12장). 법령 유래는 ``rules_kr.json``,
# 우리 판단값은 ``assumptions.json`` 이다 — **섞지 않는다.**


def dr_reference_capacity_kw() -> float:
    """제12.4.2.1조 제1항 2호 — 거래시간별 감축가능용량 0.1 MW-h.

    **수요관리사업자가 묶은 자원 단위 기준이라 개별 고객에게 그대로 적용되지
    않는다.** 참고 문턱으로만 표시한다.
    """
    return float(rule_value("dr.reference_capacity_kw"))


def national_dr_max_contract_kw() -> float:
    """제12.1.1조 — 국민DR 계약전력 상한."""
    return float(rule_value("dr.national_max_contract_kw"))


def small_medium_dr_industrial_max_kw() -> float:
    """제12.1.1조 — 중소형DR 산업체 계약전력 상한."""
    return float(rule_value("dr.small_medium_industrial_max_kw"))


def dr_operating_windows() -> tuple[tuple[int, int], ...]:
    """경제성DR 운영 시간대. **평일 09~12시·13~20시** (점심 제외).

    감축 여력은 이 구간의 부하로만 잰다. 하루 전체 평균으로 재면 참여할 수 없는
    시간대의 부하까지 여력으로 세어 과대 산출된다 (13세션).
    """
    windows = rule_value("dr.operating_hours")
    return tuple((int(start), int(end)) for start, end in windows)


def dr_max_events_per_day() -> int:
    """하루 최대 발령 횟수."""
    return int(rule_value("dr.max_events_per_day"))


def dr_event_hours() -> tuple[float, float]:
    """1회 지속시간 범위 (시간)."""
    low, high = rule_value("dr.event_hours")
    return (float(low), float(high))


def dr_daily_hours_cap() -> float:
    """하루 상한 시간 = 최대 발령 횟수 × 1회 최대 지속시간.

    **연간 한도는 없다** (14세션). 남는 상한은 이 하루 한도뿐이다.
    """
    return dr_max_events_per_day() * dr_event_hours()[1]


def dr_bid_restriction_months() -> float:
    """미이행 시 입찰 제한 기간 (개월).

    **이 제재가 보수적 산정의 근거다.** 과대 산정으로 감축을 이행하지 못하면
    반년 동안 참여 자체가 막힌다.
    """
    return float(rule_value("dr.bid_restriction_months"))


def low_load_multiple() -> float:
    """저부하 평일 판정 배수 (6.6).

    주말·공휴일 기준선의 이 배수 이하이면 「사무실을 비운 날」로 본다 —
    창립기념일·워크숍처럼 감축 여력이 실제로 있는 날이다.
    """
    return float(assumption("dr.low_load_multiple"))


def registration_percentile() -> float:
    """등록 권장 용량의 분위수.

    사업자와 계약할 때 등록하는 값이라 **어느 참여일에나 지킬 수 있어야 한다** —
    평균으로 등록하면 절반의 날에 미달하고 미달은 6개월 입찰 제한이다.
    """
    return float(assumption("dr.registration_percentile"))


def default_high_capacity_kw() -> float:
    """적합성 등급의 상단. 규칙이 정하는 값이 아니라 본 도구의 구간이다."""
    return float(assumption("dr.high_capacity_kw"))


class DrResourceType(StrEnum):
    """수요반응자원 유형 (전력시장운영규칙 제12.1.1조)."""

    STANDARD = "표준DR"
    SMALL_MEDIUM = "중소형DR"
    NATIONAL = "국민DR"


class DrPotential(StrEnum):
    """경제성DR 적합성."""

    HIGH = "높음"
    MEDIUM = "보통"
    LOW = "낮음"


# --------------------------------------------------------------------- 거래일


def dr_eligible_days(
    index: pd.DatetimeIndex,
    interval_minutes: int,
    calendar: HolidayCalendar,
) -> pd.DatetimeIndex:
    """경제성DR 거래 가능일 (제12.4.2.1조 제1항 1호).

    **토요일·일요일·공휴일을 모두 뺀다.** 일요일은 "관공서의 공휴일에 관한 규정"에
    포함되므로 ``calendar`` 가 알아서 걸러 주지만, **토요일은 요금 규칙에서
    공휴일이 아니므로 여기서 따로 뺀다.**

    귀속은 :func:`kwise.io.slot_start` 로 판정한 구간 시작 시각의 날짜다.
    라벨 ``2024-03-05 00:00`` 은 04일 23:45~24:00 이므로 4일에 속한다.
    """
    starts = slot_start(pd.DatetimeIndex(index), interval_minutes)
    days = pd.DatetimeIndex(pd.Series(starts.normalize()).unique()).sort_values()
    keep = [
        day
        for day in days
        if day.weekday() < 5 and not calendar.is_holiday(day)  # 토(5)·일(6) 제외
    ]
    return pd.DatetimeIndex(keep, name="dr_day")


def dr_day_mask(
    index: pd.DatetimeIndex,
    interval_minutes: int,
    calendar: HolidayCalendar,
) -> pd.Series:
    """거래 가능일에 속한 슬롯 마스크.

    **요금 계량의 평일 판정과 다르다.** 요금은 토요일을 최대부하 → 중간부하로
    낮출 뿐 공휴일로 보지 않지만, DR 은 토요일도 통째로 제외다.
    """
    starts = slot_start(pd.DatetimeIndex(index), interval_minutes)
    eligible = set(dr_eligible_days(index, interval_minutes, calendar))
    return pd.Series([day in eligible for day in starts.normalize()], index=index, name="dr_day")


# --------------------------------------------------------------------- 자원 유형


def judge_resource_types(
    contract_type: str | None,
    contract_kw: float | None,
) -> tuple[DrResourceType, ...]:
    """참여 가능한 자원 유형 (제12.1.1조).

        표준DR    계약종별 제한 없음
        중소형DR  일반용·주택용·농사용·교육용. **산업용은 2 MW 이하**
        국민DR    계약전력 200 kW 이하, 주택용, 집합건물 개별세대

    이미 입력받는 계약종별·계약전력만으로 판정한다. 새로 묻지 않는다.
    """
    types: list[DrResourceType] = [DrResourceType.STANDARD]  # 제한 없음

    if contract_type is None:
        # 종별을 모르면 중소형DR 여부를 단정할 수 없다. 표준DR 만 확실하다.
        pass
    elif contract_type.startswith("industrial"):
        if contract_kw is not None and contract_kw <= small_medium_dr_industrial_max_kw():
            types.append(DrResourceType.SMALL_MEDIUM)
    else:  # 일반용·교육용 (주택용·농사용은 본 도구 범위 밖)
        types.append(DrResourceType.SMALL_MEDIUM)

    if contract_kw is not None and contract_kw <= national_dr_max_contract_kw():
        types.append(DrResourceType.NATIONAL)
    return tuple(types)


# --------------------------------------------------------------------- 진단


@dataclass(frozen=True, eq=False)
class DrProfile:
    """경제성DR 참여 여력 (14세션에 산출 방식을 갈아치웠다).

    Attributes:
        eligible_days: 거래 가능일 수 (토·일·공휴일 제외).
        weekend_baseline_kw: **① 기준선.** 주말·공휴일 운영 시간대 평균 부하.
            건물이 사실상 비어 있을 때의 수준이다.
        low_load_threshold_kw: 기준선 × :func:`low_load_multiple`.
        low_load_days: **② 저부하 평일.** 대상일 중 운영 시간대 평균이 문턱 이하인 날.
        normal_weekday_mean_kw: 저부하일을 뺀 **평일 정상 평균**. ③ 의 기준이다.
        daily_reducible_kw: **③ 저부하일별 감축 여력** (평일 정상 평균 − 실제 부하).
        daily_hours: **④ 저부하일별 참여 가능 시간.** 하루 한도로 자른다.
        registered_capacity_kw: **등록 권장값.** 저부하일 여력 분포의 하위값이라
            어느 참여일에나 지킬 수 있다. 평균으로 등록하면 절반의 날에 미달한다.
        period_reducible_kwh: **⑤ 관측 기간의 감축 가능량** Σ(여력 × 시간).
        annual_reducible_kwh: 관측 기간을 365일로 환산한 값. 기간이 1년이 아닐 수
            있어 **환산 사실을 노트에 적는다.**
    """

    eligible_days: int
    total_days: int
    excluded_days: int
    windows: tuple[tuple[int, int], ...]

    weekend_days: int
    weekend_baseline_kw: float | None
    low_load_multiple: float
    low_load_threshold_kw: float | None
    weekday_mean_kw: float | None
    normal_weekday_mean_kw: float | None

    low_load_days: tuple[pd.Timestamp, ...]
    daily_reducible_kw: pd.Series
    daily_hours: pd.Series
    daily_hours_cap: float

    registered_capacity_kw: float
    mean_reducible_kw: float
    period_reducible_kwh: float
    annual_reducible_kwh: float

    resource_types: tuple[DrResourceType, ...]
    potential: DrPotential
    bid_restriction_months: float
    warnings: tuple[str, ...] = field(default=())
    notes: tuple[str, ...] = field(default=())

    @property
    def eligible_day_ratio(self) -> float:
        """전체 일수 대비 거래 가능일 비율. 보통 0.67 근처다."""
        return self.eligible_days / self.total_days if self.total_days else 0.0

    @property
    def meets_reference_capacity(self) -> bool:
        """참고 문턱(100 kW)을 넘는가. 자원 단위 기준이라 확정 판정이 아니다."""
        return self.registered_capacity_kw >= dr_reference_capacity_kw()

    @property
    def low_load_days_count(self) -> int:
        return len(self.low_load_days)

    @property
    def total_participation_hours(self) -> float:
        """저부하일에 실제로 참여할 수 있는 시간의 합."""
        return float(self.daily_hours.sum()) if len(self.daily_hours) else 0.0

    @property
    def window_label(self) -> str:
        return _window_label(self.windows)

    @property
    def notice(self) -> str:
        """화면·산출물이 같이 쓰는 안내 한 문단."""
        return PARTICIPATION_NOTICE.format(
            daily=self.daily_hours_cap,
            window=self.window_label,
            months=self.bid_restriction_months,
        )

    def low_load_day_table(self) -> pd.DataFrame:
        """저부하 평일 목록. **어떤 날인지 보여 준다** (14세션 4절).

        창립기념일·워크숍처럼 사무실을 비우는 날일 가능성이 높아, 목록을 보면
        사용자가 스스로 맞는 날인지 판정할 수 있다.
        """
        days = list(self.low_load_days)
        return pd.DataFrame(
            {
                "날짜": [f"{day:%Y-%m-%d}" for day in days],
                "요일": [_WEEKDAYS[day.weekday()] for day in days],
                "감축 여력(kW)": [float(self.daily_reducible_kw.get(day, 0.0)) for day in days],
                "참여 가능 시간(h)": [float(self.daily_hours.get(day, 0.0)) for day in days],
            }
        )


_WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")


def _window_label(windows: tuple[tuple[int, int], ...]) -> str:
    """``09~12시 · 13~20시``. 화면에 실릴 때는 물결표를 escape 한다."""
    return " · ".join(f"{start:02d}~{end:02d}시" for start, end in windows)


def _operating_window(starts: pd.DatetimeIndex, windows: tuple[tuple[int, int], ...]) -> pd.Series:
    """운영 시간대 마스크. **구간이 둘 이상**이다 (점심이 빠진다)."""
    hours = starts.hour
    inside = pd.Series(False, index=range(len(starts)))
    for start, end in windows:
        band = (
            (hours >= start) & (hours < end) if start <= end else (hours >= start) | (hours < end)
        )
        inside = inside | pd.Series(band, index=range(len(starts)))
    return pd.Series(inside.to_numpy(), index=starts)


def _empty_profile(
    *,
    eligible_days: int,
    total_days: int,
    windows: tuple[tuple[int, int], ...],
    weekend_days: int,
    weekend_baseline_kw: float | None,
    multiple: float,
    threshold: float | None,
    weekday_mean: float | None,
    resource_types: tuple[DrResourceType, ...],
    warnings: list[str],
    notes: list[str],
) -> DrProfile:
    """저부하 평일이 없을 때. **0 을 내되 이유를 적는다.**"""
    return DrProfile(
        eligible_days=eligible_days,
        total_days=total_days,
        excluded_days=total_days - eligible_days,
        windows=windows,
        weekend_days=weekend_days,
        weekend_baseline_kw=weekend_baseline_kw,
        low_load_multiple=multiple,
        low_load_threshold_kw=threshold,
        weekday_mean_kw=weekday_mean,
        normal_weekday_mean_kw=weekday_mean,
        low_load_days=(),
        daily_reducible_kw=pd.Series(dtype=float),
        daily_hours=pd.Series(dtype=float),
        daily_hours_cap=dr_daily_hours_cap(),
        registered_capacity_kw=0.0,
        mean_reducible_kw=0.0,
        period_reducible_kwh=0.0,
        annual_reducible_kwh=0.0,
        resource_types=resource_types,
        potential=DrPotential.LOW,
        bid_restriction_months=dr_bid_restriction_months(),
        warnings=tuple(warnings),
        notes=tuple(notes),
    )


def dr_profile(
    kw: pd.Series,
    interval_minutes: int,
    calendar: HolidayCalendar,
    *,
    contract_type: str | None = None,
    contract_kw: float | None = None,
    outage_mask: pd.Series | None = None,
    windows: tuple[tuple[int, int], ...] | None = None,
    low_load_ratio: float | None = None,
    registration_quantile: float | None = None,
    high_capacity_kw: float | None = None,
) -> DrProfile:
    """경제성DR 참여 여력을 진단한다 (요구사항서 6.6 · 14세션 4절).

    Args:
        outage_mask: 정전 슬롯 마스크. 저부하 평일에서 정전일을 뺀다 — 정전은
            감축 여력이 아니다.
        windows: 감축 여력을 재는 운영 시간대. 기본은 규칙 값(09~12·13~20시).
        low_load_ratio: 저부하 평일 판정 배수 (기본은 assumptions.json 의 1.2).
        registration_quantile: 등록 권장 용량 분위수. **보수적으로 하위값을 쓴다.**
    """
    observed = kw.dropna()
    if observed.empty:
        raise ValueError("관측된 수요가 없어 DR 참여 여력을 산출할 수 없습니다.")

    # 기본값은 파일에서 온다 (요구사항서 12장). 코드에 두지 않는다.
    windows = dr_operating_windows() if windows is None else windows
    multiple = low_load_multiple() if low_load_ratio is None else low_load_ratio
    registration = (
        registration_percentile() if registration_quantile is None else registration_quantile
    )
    high_capacity_kw = default_high_capacity_kw() if high_capacity_kw is None else high_capacity_kw
    reference_kw = dr_reference_capacity_kw()
    daily_cap = dr_daily_hours_cap()
    slot_hours = interval_minutes / 60.0

    index = pd.DatetimeIndex(observed.index)
    starts = slot_start(index, interval_minutes)
    eligible_days = dr_eligible_days(index, interval_minutes, calendar)
    all_days = pd.DatetimeIndex(pd.Series(starts.normalize()).unique())
    day_of = pd.Series(starts.normalize(), index=observed.index)
    is_dr_day = day_of.isin(set(eligible_days))
    in_window = pd.Series(_operating_window(starts, windows).to_numpy(), index=observed.index)

    resource_types = judge_resource_types(contract_type, contract_kw)
    warnings: list[str] = []
    notes: list[str] = [
        "경제성DR 은 '관공서의 공휴일에 관한 규정'의 공휴일과 토요일을 제외한 "
        f"평일에만 입찰할 수 있습니다 (전력시장운영규칙 제12.4.2.1조 제1항 1호). "
        f"기간 {len(all_days)}일 중 거래 가능일은 {len(eligible_days)}일입니다.",
        "요금 계량의 평일 판정과 다릅니다. 요금은 토요일을 중간부하로 낮출 뿐 "
        "공휴일로 보지 않지만, DR 은 토·일·공휴일이 모두 제외입니다.",
        "**연간 참여 일수 제한은 없습니다** (14세션에 바로잡았습니다). 남는 제약은 "
        f"하루 {dr_max_events_per_day()}회 × 최대 {dr_event_hours()[1]:,.0f}시간"
        f"(하루 {daily_cap:,.0f}시간)과 운영 시간대"
        f"({_window_label(windows)}) 뿐이므로, 실질 제약은 「감축할 여력이 있는 날이 "
        "며칠이냐」 하나입니다.",
    ]
    if contract_kw is None:
        warnings.append(
            "계약전력이 없어 국민DR·중소형DR 해당 여부를 확정하지 못했습니다 "
            "(제12.1.1조). 표준DR 은 계약종별 제한이 없습니다."
        )

    # ① 기준선 — **주말·공휴일**의 운영 시간대 평균. 건물이 사실상 비어 있는 수준이다.
    off_day = ~is_dr_day
    weekend_slots = observed[off_day & in_window]
    weekend_days = int(day_of[off_day].nunique())
    weekday_slots = observed[is_dr_day & in_window]
    weekday_mean = float(weekday_slots.mean()) if len(weekday_slots) else None

    if not len(weekend_slots) or not len(weekday_slots):
        warnings.append(
            "주말·공휴일 또는 평일의 운영 시간대 관측치가 없어 저부하 평일을 "
            "찾지 못했습니다. 감축 가능량을 산출하지 않습니다."
        )
        return _empty_profile(
            eligible_days=len(eligible_days),
            total_days=len(all_days),
            windows=windows,
            weekend_days=weekend_days,
            weekend_baseline_kw=float(weekend_slots.mean()) if len(weekend_slots) else None,
            multiple=multiple,
            threshold=None,
            weekday_mean=weekday_mean,
            resource_types=resource_types,
            warnings=warnings,
            notes=notes,
        )

    baseline_kw = float(weekend_slots.mean())
    threshold = baseline_kw * multiple
    notes.append(
        f"기준선 {baseline_kw:,.0f} kW 는 주말·공휴일 {weekend_days}일의 운영 시간대 "
        f"평균 부하입니다. 건물이 사실상 비어 있을 때의 수준이며, 저부하 평일 문턱은 "
        f"그 {multiple:.2g}배인 {threshold:,.0f} kW 입니다."
    )

    # ② 저부하 평일 — 대상일 중 운영 시간대 평균이 문턱 이하인 날. 정전일은 뺀다.
    day_mean = weekday_slots.groupby(day_of[weekday_slots.index]).mean()
    outage_days: set[pd.Timestamp] = set()
    if outage_mask is not None:
        flagged = outage_mask.reindex(observed.index).fillna(False).astype(bool)
        outage_days = set(day_of[flagged])
    low_days = tuple(day for day in day_mean[day_mean <= threshold].index if day not in outage_days)
    normal_days = [day for day in day_mean.index if day not in low_days]
    normal_mean = float(day_mean[normal_days].mean()) if normal_days else weekday_mean

    if not low_days or normal_mean is None:
        warnings.append(
            f"저부하 평일이 없습니다 — 대상일 {len(day_mean)}일 가운데 운영 시간대 "
            f"평균이 문턱 {threshold:,.0f} kW 이하인 날이 없습니다. 감축은 실제 운영 "
            "축소를 뜻하므로 생산·재실 영향과 함께 검토하십시오."
        )
        return _empty_profile(
            eligible_days=len(eligible_days),
            total_days=len(all_days),
            windows=windows,
            weekend_days=weekend_days,
            weekend_baseline_kw=baseline_kw,
            multiple=multiple,
            threshold=threshold,
            weekday_mean=weekday_mean,
            resource_types=resource_types,
            warnings=warnings,
            notes=notes,
        )

    # ③④ 저부하일별 감축 여력과 참여 가능 시간.
    #
    # 여력은 **참여할 슬롯의 부하**로 잰다 — 하루 평균으로 재면 참여하지 않는
    # 시간대의 높은 부하가 여력을 깎는다. 시간은 저부하가 실제로 지속되는 만큼만
    # 세고 하루 한도로 자른다.
    low_index = pd.Index(low_days)
    window_frame = pd.DataFrame(
        {"kw": weekday_slots, "day": day_of[weekday_slots.index]},
    )
    picked = window_frame[
        window_frame["day"].isin(set(low_days)) & (window_frame["kw"] <= threshold)
    ]
    slot_counts = picked.groupby("day")["kw"].size().reindex(low_index, fill_value=0)
    picked_mean = picked.groupby("day")["kw"].mean().reindex(low_index)

    hours = (slot_counts.astype(float) * slot_hours).clip(upper=daily_cap)
    reducible = (normal_mean - picked_mean).clip(lower=0.0).fillna(0.0)
    hours.index.name = "day"
    reducible.index.name = "day"

    period_kwh = float((reducible * hours).sum())
    annual_kwh = period_kwh * 365.0 / len(all_days) if len(all_days) else 0.0

    # **보수적으로.** 등록값은 하위 분위수다 — 미이행이 6개월 입찰 제한이다.
    positive = reducible[reducible > 0]
    registered = float(positive.quantile(registration)) if len(positive) else 0.0
    mean_reducible = float(reducible.mean())

    notes.append(
        f"저부하 평일 {len(low_days)}일을 찾았습니다. 감축 여력은 평일 정상 평균 "
        f"{normal_mean:,.0f} kW 에서 그날 실제 부하를 뺀 값이고, 참여 시간은 저부하가 "
        f"지속되는 시간을 하루 한도 {daily_cap:,.0f}시간으로 자른 값입니다."
    )
    notes.append(
        f"**감축 가능량 {annual_kwh:,.0f} kWh/년** = Σ(저부하일별 감축 여력 × 그날 "
        f"참여 가능 시간). 관측 기간 {len(all_days)}일의 합 {period_kwh:,.0f} kWh 를 "
        "365일로 환산했습니다."
    )
    notes.append(
        f"**등록 권장 용량 {registered:,.0f} kW** 는 저부하일 여력 분포의 하위 "
        f"{registration:.0%} 입니다. 사업자와 계약할 때 등록하는 값이며, 평균 "
        f"{mean_reducible:,.0f} kW 로 등록하면 절반의 날에 미달합니다 — 미달은 "
        f"{dr_bid_restriction_months():,.0f}개월 입찰 제한입니다."
    )

    if registered < reference_kw:
        warnings.append(
            f"등록 권장 용량 {registered:,.0f} kW 가 참고 문턱 "
            f"{reference_kw:,.0f} kW (0.1 MW-h) 아래입니다. "
            "이 문턱은 수요관리사업자가 **묶은 자원 단위** 기준이라 개별 고객에게 "
            "그대로 적용되지 않습니다 (제12.4.2.1조 제1항 2호). 다른 고객과 묶여 "
            "참여할 수 있으므로 사업자와 상담하십시오."
        )

    if registered >= high_capacity_kw:
        potential = DrPotential.HIGH
    elif registered >= reference_kw:
        potential = DrPotential.MEDIUM
    else:
        potential = DrPotential.LOW

    return DrProfile(
        eligible_days=len(eligible_days),
        total_days=len(all_days),
        excluded_days=len(all_days) - len(eligible_days),
        windows=windows,
        weekend_days=weekend_days,
        weekend_baseline_kw=baseline_kw,
        low_load_multiple=multiple,
        low_load_threshold_kw=threshold,
        weekday_mean_kw=weekday_mean,
        normal_weekday_mean_kw=normal_mean,
        low_load_days=tuple(low_days),
        daily_reducible_kw=reducible,
        daily_hours=hours,
        daily_hours_cap=daily_cap,
        registered_capacity_kw=registered,
        mean_reducible_kw=mean_reducible,
        period_reducible_kwh=period_kwh,
        annual_reducible_kwh=annual_kwh,
        resource_types=resource_types,
        potential=potential,
        bid_restriction_months=dr_bid_restriction_months(),
        warnings=tuple(warnings),
        notes=tuple(notes),
    )


def resource_type_labels(types: Sequence[DrResourceType]) -> str:
    """산출물에 그대로 쓸 한 줄."""
    return ", ".join(str(item) for item in types) if types else "해당 없음"
