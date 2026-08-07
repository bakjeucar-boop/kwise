"""경제성DR 참여 여력 진단 (요구사항서 6.6).

근거는 ``data\\source\\전력시장운영규칙.pdf`` 제12장 「수요반응자원의 거래」다.

    신뢰성DR   수급 비상 시 **의무** 감축. 용량요금 + 실적금.      → 범위 밖
    경제성DR   하루 전 **자발적** 입찰. 실적금만. 설비 투자 불필요.  → 대상

**거래일 제약이 이 모듈의 핵심이다 (제12.4.2.1조 제1항 1호).**

    "관공서의 공휴일에 관한 규정"의 공휴일과 **토요일**을 제외한 평일의
    거래일에만 입찰할 수 있다.

일요일은 공휴일 규정에 포함되므로 자동으로 빠진다. 이 제약을 빼면 감축 가능량이
**30% 이상 과대평가**된다 (365일 중 대상일이 245일 안팎이다).

**요금 계량의 '평일' 과 정의가 다르다.** 요금 엔진은 토요일을 공휴일로 보지 않고
(최대부하 → 중간부하로 낮출 뿐) 일요일만 공휴일로 계량한다. DR 은 토·일·공휴일이
모두 똑같이 제외다. 그래서 :func:`dr_day_mask` 를 :mod:`kwise.tariff.tou` 와
**따로 둔다.** 같은 함수로 판정하면 두 규칙이 조용히 섞인다.

산정은 **보수적으로** 한다. 과대 산정은 감축 미달로 이어지고, 미달은 위약금이다
(별표26).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

import pandas as pd

from kwise.io import slot_start
from kwise.quality import LoadPattern
from kwise.rules import assumption, rule_value
from kwise.tariff import HolidayCalendar

__all__ = [
    "DrPotential",
    "DrProfile",
    "DrResourceType",
    "default_day_hours",
    "default_high_capacity_kw",
    "dr_day_mask",
    "dr_eligible_days",
    "dr_profile",
    "dr_reference_capacity_kw",
    "judge_resource_types",
    "low_load_percentile",
    "national_dr_max_contract_kw",
    "registration_percentile",
    "small_medium_dr_industrial_max_kw",
]

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


def default_day_hours() -> tuple[int, int]:
    """감축 여력을 재는 주간 창. **판단값이다** — 규칙이 정한 창이 아니다."""
    start, end = assumption("dr.day_hours")
    return (int(start), int(end))


def low_load_percentile() -> float:
    """무비용 감축 가능일 판정 문턱 (6.6). **등록 문턱과 다른 값이다.**"""
    return float(assumption("dr.low_load_percentile"))


def registration_percentile() -> float:
    """등록 권장 용량의 분위수.

    사업자와 계약할 때 등록하는 값이라 **어느 거래일에나 지킬 수 있어야 한다** —
    평균으로 등록하면 절반의 날에 미달하고 미달은 위약금이다(별표26).
    저부하일 식별의 값과 쓰임이 달라 항목을 따로 둔다.
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
    """경제성DR 참여 여력.

    Attributes:
        eligible_days: 거래 가능일 수 (토·일·공휴일 제외). **이후 모든 산출의 분모다.**
        registered_capacity_kw: **등록 권장값** — 사업자와 계약할 때 등록하는 용량.
            대상일 주간 부하의 하위 10%에서 기저부하를 뺀 값이라 어느 거래일에나
            지킬 수 있다. **연간 수익 추정에 이 값을 쓰면 안 된다.**
        daily_reducible_kw: 거래일별 감축 여력 (그날 주간 평균 − 기저부하).
            **연간 감축 가능량의 기준이다.**
        mean_reducible_kw: 대상일 주간 **평균** 부하 − 기저부하. 비교용이다.
        low_load_days: 무비용 감축 가능일. 일평균 부하가 대상일 하위 5% 이하이고
            정전 구간이 아닌 날.
    """

    eligible_days: int
    total_days: int
    excluded_days: int
    day_hours: tuple[int, int]

    day_mean_kw: float | None
    day_floor_kw: float | None
    base_load_kw: float | None
    base_load_ratio: float | None
    registered_capacity_kw: float
    mean_reducible_kw: float
    daily_reducible_kw: pd.Series

    resource_types: tuple[DrResourceType, ...]
    potential: DrPotential
    low_load_days: tuple[pd.Timestamp, ...] = field(default=())
    low_load_threshold_kw: float | None = None
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

    def annual_reducible_kwh(self, bid_hours_per_day: float) -> float:
        """연간 감축 가능량 — **거래일별 여력의 합**이다.

        등록 권장값 × 일수로 계산하면 부하가 많은 날의 여력을 통째로 버린다.
        경제성DR 은 하루 전 입찰이라 매일 다른 양을 입찰하기 때문이다.
        """
        if bid_hours_per_day <= 0:
            raise ValueError(f"입찰 지속시간은 양수여야 합니다: {bid_hours_per_day}")
        return float(self.daily_reducible_kw.sum()) * bid_hours_per_day

    def low_cost_reducible_kwh(self, bid_hours_per_day: float) -> float:
        """무비용 감축 가능일만 참여했을 때의 감축량. 그날의 여력을 쓴다."""
        if bid_hours_per_day <= 0:
            raise ValueError(f"입찰 지속시간은 양수여야 합니다: {bid_hours_per_day}")
        days = self.daily_reducible_kw.reindex(list(self.low_load_days)).dropna()
        return float(days.sum()) * bid_hours_per_day


def _day_window(starts: pd.DatetimeIndex, day_hours: tuple[int, int]) -> pd.Series:
    start, end = day_hours
    hours = starts.hour
    inside = (hours >= start) & (hours < end) if start <= end else (hours >= start) | (hours < end)
    return pd.Series(inside, index=starts)


def dr_profile(
    kw: pd.Series,
    interval_minutes: int,
    calendar: HolidayCalendar,
    *,
    pattern: LoadPattern,
    contract_type: str | None = None,
    contract_kw: float | None = None,
    outage_mask: pd.Series | None = None,
    day_hours: tuple[int, int] | None = None,
    low_load_quantile: float | None = None,
    registration_quantile: float | None = None,
    high_capacity_kw: float | None = None,
) -> DrProfile:
    """경제성DR 참여 여력을 진단한다 (요구사항서 6.6).

    Args:
        pattern: 6.1 부하 패턴. **기저부하 비율을 여기서 가져다 쓴다.**
            다시 계산하지 않는다.
        outage_mask: 정전 슬롯 마스크. 무비용 감축 가능일에서 정전일을 뺀다.
        day_hours: 감축 여력을 재는 주간 창.
        low_load_quantile: 무비용 감축 가능일 문턱 (기본은 assumptions.json).
        registration_quantile: 등록 권장 용량 분위수 (기본은 assumptions.json).
            **저부하일 문턱과 쓰임이 다른 값이다.**
    """
    observed = kw.dropna()
    if observed.empty:
        raise ValueError("관측된 수요가 없어 DR 참여 여력을 산출할 수 없습니다.")

    # 기본값은 파일에서 온다 (요구사항서 12장). 코드에 두지 않는다.
    day_hours = default_day_hours() if day_hours is None else day_hours
    low_quantile = low_load_percentile() if low_load_quantile is None else low_load_quantile
    registration = (
        registration_percentile() if registration_quantile is None else registration_quantile
    )
    high_capacity_kw = default_high_capacity_kw() if high_capacity_kw is None else high_capacity_kw
    reference_kw = dr_reference_capacity_kw()

    index = pd.DatetimeIndex(observed.index)
    starts = slot_start(index, interval_minutes)
    eligible_days = dr_eligible_days(index, interval_minutes, calendar)
    all_days = pd.DatetimeIndex(pd.Series(starts.normalize()).unique())
    day_of = pd.Series(starts.normalize(), index=observed.index)
    is_dr_day = day_of.isin(set(eligible_days))
    in_window = pd.Series(_day_window(starts, day_hours).to_numpy(), index=observed.index)

    warnings: list[str] = []
    notes: list[str] = [
        "경제성DR 은 '관공서의 공휴일에 관한 규정'의 공휴일과 토요일을 제외한 "
        f"평일에만 입찰할 수 있습니다 (전력시장운영규칙 제12.4.2.1조 제1항 1호). "
        f"기간 {len(all_days)}일 중 거래 가능일은 {len(eligible_days)}일입니다.",
        "요금 계량의 평일 판정과 다릅니다. 요금은 토요일을 중간부하로 낮출 뿐 "
        "공휴일로 보지 않지만, DR 은 토·일·공휴일이 모두 제외입니다.",
    ]

    selected = observed[is_dr_day & in_window]
    day_mean = float(selected.mean()) if len(selected) else None
    # 등록 권장값의 바닥. 어느 거래일에나 지킬 수 있어야 한다 (별표26 위약금).
    day_floor = float(selected.quantile(registration)) if len(selected) else None

    ratio = pattern.base_load_ratio  # 6.1 의 값을 재사용한다. 다시 계산하지 않는다
    base_kw: float | None = None
    registered = 0.0
    mean_reducible = 0.0
    daily_reducible = pd.Series(dtype=float)
    if day_mean is not None and ratio is not None:
        base_kw = day_mean * ratio
        mean_reducible = max(0.0, day_mean - base_kw)
        registered = max(0.0, (day_floor or 0.0) - base_kw)

        # **연간 감축 가능량은 등록값 × 일수가 아니다.** 경제성DR 은 하루 전
        # 입찰이라 매일 다른 양을 입찰한다. 등록값(하위 10%)으로 곱하면 부하가
        # 많은 날의 여력을 통째로 버려 연간 수익이 크게 과소평가된다.
        #   연간 감축 가능량 = Σ (거래일별 주간 평균 부하 − 기저부하)
        per_day_mean = selected.groupby(day_of[selected.index]).mean()
        daily_reducible = (per_day_mean - base_kw).clip(lower=0.0)
        notes.append(
            f"감축 여력 = 대상일 주간({day_hours[0]}~{day_hours[1]}시) 부하 − 기저부하. "
            f"기저부하는 6.1 의 기저부하 비율 {ratio:.1%} 를 그대로 썼습니다 "
            f"({base_kw:,.0f} kW)."
        )
        notes.append(
            f"**두 값은 쓰임이 다릅니다.** 등록 권장 용량 {registered:,.0f} kW 는 "
            f"하위 {registration:.0%} 기준으로, 사업자와 계약할 때 등록하는 "
            "값입니다 — 평균으로 등록하면 절반의 날에 미달해 위약금이 납니다. "
            f"반면 연간 감축 가능량은 **거래일별 여력을 합산**합니다 "
            f"(일평균 {float(daily_reducible.mean()):,.0f} kW). 하루 전 입찰이라 "
            "매일 다른 양을 입찰하므로, 등록값으로 곱하면 부하가 많은 날의 여력을 "
            "버려 연간 수익이 과소평가됩니다."
        )
    else:
        warnings.append(
            "대상일 주간 관측치나 기저부하 비율이 없어 등록 가능 용량을 산출하지 못했습니다."
        )

    if registered < reference_kw:
        warnings.append(
            f"등록 권장 용량 {registered:,.0f} kW 가 참고 문턱 "
            f"{reference_kw:,.0f} kW (0.1 MW-h) 아래입니다. "
            "이 문턱은 수요관리사업자가 **묶은 자원 단위** 기준이라 개별 고객에게 "
            "그대로 적용되지 않습니다 (제12.4.2.1조 제1항 2호). 다른 고객과 묶여 "
            "참여할 수 있으므로 사업자와 상담하십시오."
        )

    # 무비용 감축 가능일 — 기준선은 **반드시 대상일만으로** 계산한다.
    # 주말이 섞이면 기준선이 내려가 평일 저부하일이 걸리지 않는다.
    daily_mean = observed[is_dr_day].groupby(day_of[is_dr_day]).mean()
    low_threshold: float | None = None
    low_days: tuple[pd.Timestamp, ...] = ()
    if len(daily_mean):
        low_threshold = float(daily_mean.quantile(low_quantile))
        candidates = daily_mean[daily_mean <= low_threshold].index
        outage_days: set[pd.Timestamp] = set()
        if outage_mask is not None:
            flagged = outage_mask.reindex(observed.index).fillna(False).astype(bool)
            outage_days = set(day_of[flagged])
        low_days = tuple(day for day in candidates if day not in outage_days)
        notes.append(
            f"무비용 감축 가능일 {len(low_days)}일 — 대상일 일평균 부하가 하위 "
            f"{low_quantile:.0%}({low_threshold:,.0f} kW) 이하이고 정전 구간이 "
            "아닌 날입니다. **기준선은 거래 가능일만으로 계산했습니다** — 주말이 "
            "섞이면 기준선이 내려가 평일 저부하일이 걸리지 않습니다."
        )

    resource_types = judge_resource_types(contract_type, contract_kw)
    if contract_kw is None:
        warnings.append(
            "계약전력이 없어 국민DR·중소형DR 해당 여부를 확정하지 못했습니다 "
            "(제12.1.1조). 표준DR 은 계약종별 제한이 없습니다."
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
        day_hours=day_hours,
        day_mean_kw=day_mean,
        day_floor_kw=day_floor,
        base_load_kw=base_kw,
        base_load_ratio=ratio,
        registered_capacity_kw=registered,
        mean_reducible_kw=mean_reducible,
        daily_reducible_kw=daily_reducible,
        resource_types=resource_types,
        potential=potential,
        low_load_days=low_days,
        low_load_threshold_kw=low_threshold,
        warnings=tuple(warnings),
        notes=tuple(notes),
    )


def resource_type_labels(types: Sequence[DrResourceType]) -> str:
    """산출물에 그대로 쓸 한 줄."""
    return ", ".join(str(item) for item in types) if types else "해당 없음"
