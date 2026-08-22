"""잉여 활용 (요구사항서 7.7). **태양광의 결과다** (41세션 2절).

41세션에 **개선안에서 뺐다.** 잉여는 태양광을 얼마나 크게 지을지에 따라 나오는
결과이지 따로 고르는 수단이 아니다 — 7.7 카드를 지우고 처리 선택을 태양광 카드
안으로 옮겼다. 개선안은 여섯이 된다. 계산 모듈은 여기 그대로 남는다.

    상계거래(한전)  그 슬롯의 실제 계시로 그 달 사용량에서 차감
    외부 판매       사용자가 넣은 잉여 판매 단가
    버리기          0원

**자격요건은 판정하지 않는다.** 제도별 자격요건과 행정 절차는 별도 확인 사항이며,
여기서는 금액만 참고로 제시한다.

상계거래
--------

**낮 시간을 일괄로 중간부하에 넣지 않는다.** 여름·봄가을 15~21시와 겨울 09~12시는
최대부하이고 태양광이 그 시간에도 발전한다. 요금 엔진이 이미 슬롯마다 계시를
판정하므로 그것을 그대로 쓴다 — 요일 규칙(토요일 최대→중간, 일요일·공휴일 전량
경부하)도 엔진 것이다.

차감은 **그 달 그 계시 사용량까지만** 한다. 넘으면 0 에서 멈추고 남은 몫을 **같은
계시로 다음 달에 이월**한다. 부하가 큰 건물에서는 거의 생기지 않는다.

설비 용량으로 구간을 가른다 (:func:`offset_carry_only_max_kw` ·
:func:`offset_max_kw`).

    10 kW 이하            이월만 되고 현금 정산이 없다
    10 kW 초과~1,000 kW   당월 차감 뒤 기간 말 잔여를 SMP 로 정산
    1,000 kW 초과         상계거래를 선택지에서 뺀다

규정은 모듈 정격과 인버터 용량 중 작은 값이 기준이지만 **초기 검토 도구이므로
인버터를 입력받지 않고** 설정된 kWp 로 판정한다.

**기본요금은 바뀌지 않는다.** 잉여는 부하가 낮은 시각에 나므로 요금적용전력과
무관하고, 태양광이 피크를 낮춘 효과는 이미 태양광 계산에 들어 있다. 여기서는
**전력량요금만** 다시 계산한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from kwise.io import UsageData
from kwise.notices import Notice, basis, info
from kwise.rules import assumption
from kwise.tariff import (
    BANDS,
    BillingOptions,
    TariffSelection,
    TariffTable,
    build_calendar,
    classify_slots,
)

__all__ = [
    "DISCARD_SCENARIO",
    "ELIGIBILITY_NOTICE",
    "EXTERNAL_SCENARIO",
    "OFFSET_SCENARIO",
    "OffsetMonth",
    "OffsetSettlement",
    "SurplusResult",
    "SurplusScenario",
    "evaluate_surplus",
    "offset_carry_only_max_kw",
    "offset_max_kw",
    "offset_settles_cash",
    "surplus_options",
]

ELIGIBILITY_NOTICE = (
    "제도별 자격요건과 행정 절차는 별도 확인이 필요합니다. 본 도구는 자격을 "
    "판정하지 않으며 금액만 참고로 제시합니다."
)
#: 시나리오 이름. **파는 곳으로 적는다** (27세션 7-3).
OFFSET_SCENARIO = "상계거래(한전)"
EXTERNAL_SCENARIO = "외부 판매"
#: **41세션에 되살렸다.** 27세션은 「언제나 0원인 줄」 이라 표에서 뺐는데, 41세션에
#: 표가 아니라 **고르는 자리**가 되면서 뜻이 달라졌다 — 「아무것도 하지 않는다」 를
#: 고를 수 없으면 셋 중 하나를 강요하게 된다.
DISCARD_SCENARIO = "버리기"

_ADMIN_NOTES = {
    OFFSET_SCENARIO: "계약 변경, 역송 계량기, 월별 정산 관리가 필요하다.",
    EXTERNAL_SCENARIO: "구매자 발굴·계약, 정산 대행, 계량·인증 관리가 필요하다.",
    DISCARD_SCENARIO: "없다.",
}


def offset_carry_only_max_kw() -> float:
    """이 용량 이하는 이월만 되고 현금 정산이 없다."""
    return float(assumption("surplus.offset.carry_only_max_kw"))


def offset_max_kw() -> float:
    """이 용량을 넘으면 상계거래를 선택지에서 뺀다."""
    return float(assumption("surplus.offset.max_kw"))


def offset_settles_cash(capacity_kwp: float) -> bool:
    """기간 말 잔여를 SMP 로 정산하는 구간인가."""
    return offset_carry_only_max_kw() < capacity_kwp <= offset_max_kw()


def surplus_options(capacity_kwp: float) -> tuple[str, ...]:
    """고를 수 있는 처리 방식. **기본값은 정하지 않는다** (41세션 2-2).

    ``capacity_kwp`` 가 :func:`offset_max_kw` 를 넘으면 상계거래가 빠진다 — 그
    위는 전력시장 직접 거래 영역이라 이 도구가 다루는 구조가 아니다.
    """
    if capacity_kwp > offset_max_kw():
        return (EXTERNAL_SCENARIO, DISCARD_SCENARIO)
    return (OFFSET_SCENARIO, EXTERNAL_SCENARIO, DISCARD_SCENARIO)


@dataclass(frozen=True)
class OffsetMonth:
    """상계 한 달. **이월이 생긴 달만 화면에 낸다** (41세션 2-3)."""

    month: str
    surplus_kwh: float
    """그 달에 난 잉여 (앞 달 이월분을 뺀 값)."""
    carried_in_kwh: float
    deducted_kwh: float
    deducted_won: float
    carried_out_kwh: float

    @property
    def has_carry(self) -> bool:
        return self.carried_out_kwh > 0


@dataclass(frozen=True)
class OffsetSettlement:
    """상계거래 정산 결과.

    **기본요금은 들어 있지 않다.** 잉여는 부하가 낮은 시각에 나므로 요금적용전력과
    무관하고, 태양광이 피크를 낮춘 효과는 이미 태양광 계산에 있다 — 여기 금액은
    전부 전력량요금 차감분이다.
    """

    months: tuple[OffsetMonth, ...]
    deducted_kwh: float
    deducted_won: float
    remaining_kwh: float
    """기간 말 잔여. 이월로도 소진하지 못한 몫이다."""
    settles_cash: bool
    """기간 말 잔여를 SMP 로 정산하는 구간인가 (10 kW 초과~1,000 kW)."""
    smp_price_won_per_kwh: float | None
    smp_won: float | None

    @property
    def carried(self) -> tuple[OffsetMonth, ...]:
        """이월이 생긴 달만. **없으면 빈 값이다 — 표시하지 않는다.**"""
        return tuple(item for item in self.months if item.has_carry)

    @property
    def is_priced(self) -> bool:
        """금액을 낼 수 있는가. SMP 정산 대상인데 단가가 없으면 낼 수 없다."""
        return not (self.settles_cash and self.remaining_kwh > 0 and self.smp_won is None)

    @property
    def revenue_won(self) -> float | None:
        if not self.is_priced:
            return None
        return self.deducted_won + (self.smp_won or 0.0)


def _offset_settlement(
    surplus_by_band: dict[pd.Period, dict[str, float]],
    usage_by_band: dict[pd.Period, dict[str, float]],
    price_by_band: dict[pd.Period, dict[str, float]],
    *,
    capacity_kwp: float,
    smp_price_won_per_kwh: float | None,
) -> OffsetSettlement:
    """당월 차감 → 같은 계시로 이월 → 기간 말 SMP 정산.

    **계시는 요금 엔진이 판정한 것을 그대로 쓴다** (41세션 2-3). 낮 시간을 일괄로
    중간부하에 넣지 않는다 — 여름·봄가을 15~21시와 겨울 09~12시는 최대부하이고
    태양광이 그 시간에도 발전한다.

    차감은 **그 달 그 계시 사용량까지만** 한다. 넘으면 0 에서 멈추고 남은 몫을
    같은 계시로 다음 달에 넘긴다.
    """
    carry: dict[str, float] = dict.fromkeys(BANDS, 0.0)
    rows: list[OffsetMonth] = []
    total_kwh = total_won = 0.0
    for month in sorted(set(surplus_by_band) | set(usage_by_band)):
        produced = surplus_by_band.get(month, {})
        available_room = usage_by_band.get(month, {})
        prices = price_by_band.get(month, {})
        month_surplus = month_carried_in = month_deducted = month_won = 0.0
        for band in BANDS:
            carried_in = carry[band]
            fresh = float(produced.get(band, 0.0))
            available = carried_in + fresh
            # **0 에서 멈춘다** — 음수 사용량은 없다.
            room = max(0.0, float(available_room.get(band, 0.0)))
            deducted = min(available, room)
            carry[band] = available - deducted
            month_surplus += fresh
            month_carried_in += carried_in
            month_deducted += deducted
            month_won += deducted * float(prices.get(band, 0.0))
        total_kwh += month_deducted
        total_won += month_won
        rows.append(
            OffsetMonth(
                month=str(month),
                surplus_kwh=month_surplus,
                carried_in_kwh=month_carried_in,
                deducted_kwh=month_deducted,
                deducted_won=month_won,
                carried_out_kwh=sum(carry.values()),
            )
        )

    remaining = sum(carry.values())
    settles_cash = offset_settles_cash(capacity_kwp)
    # **단가는 하나로 적용하고 기간 길이를 구분하지 않는다** (41세션 2-3).
    smp_won = (
        remaining * smp_price_won_per_kwh
        if settles_cash and smp_price_won_per_kwh is not None
        else None
    )
    return OffsetSettlement(
        months=tuple(rows),
        deducted_kwh=total_kwh,
        deducted_won=total_won,
        remaining_kwh=remaining,
        settles_cash=settles_cash,
        smp_price_won_per_kwh=smp_price_won_per_kwh if settles_cash else None,
        smp_won=smp_won,
    )


@dataclass(frozen=True)
class SurplusScenario:
    """잉여 활용 시나리오 하나."""

    name: str
    revenue_won: float | None
    basis: str
    admin_burden: str

    @property
    def is_priced(self) -> bool:
        return self.revenue_won is not None


@dataclass(frozen=True, eq=False)
class SurplusResult:
    """잉여 발생량과 활용 시나리오."""

    total_kwh: float
    generation_kwh: float
    share_of_generation: float | None
    hour_distribution: pd.Series
    weekday_kwh: float
    weekend_kwh: float
    holiday_kwh: float
    scenarios: tuple[SurplusScenario, ...]
    offset: OffsetSettlement | None = None
    """상계 정산. **상계를 쓸 수 없는 구간(1,000 kW 초과)이면 ``None`` 이다.**"""
    notices: tuple[Notice, ...] = field(default=())

    @property
    def off_day_share(self) -> float | None:
        """**토·일·공휴일 집중도.** 높으면 자가소비가 어려운 구조다.

        26세션에 태양광 카드가 같은 지표를 「토·일·공휴일 잉여」 로 냈으므로
        이름을 맞춘다 (27세션 7-1). 옛 이름은 ``weekend_share`` 였는데 값에
        토요일이 빠져 있었다 — ``holiday_kwh`` 만 세고 있었고, 그러면서 이름은
        「주말」 이었다. **이름과 값을 함께 고친다.**
        """
        if self.total_kwh <= 0:
            return None
        return (self.weekend_kwh + self.holiday_kwh) / self.total_kwh

    def scenario(self, name: str) -> SurplusScenario:
        for item in self.scenarios:
            if item.name == name:
                return item
        raise KeyError(f"없는 시나리오입니다: {name!r}")


def evaluate_surplus(
    usage: UsageData,
    table: TariffTable,
    selection: TariffSelection,
    surplus_kw: pd.Series,
    *,
    generation_kwh: float,
    net_usage: UsageData | None = None,
    capacity_kwp: float = 0.0,
    external_price_won_per_kwh: float | None = None,
    smp_price_won_per_kwh: float | None = None,
    options: BillingOptions | None = None,
) -> SurplusResult:
    """잉여량·시간대 분포와 활용 시나리오 셋을 낸다.

    Args:
        surplus_kw: 역송된 출력. :func:`kwise.measures.apply_generation` 의 결과.
        net_usage: 태양광 자가소비를 뺀 뒤의 부하. 상계 차감의 **한도**를 여기서
            읽는다 — 없으면 ``usage`` 를 쓴다 (차감 여지를 크게 잡는 쪽이다).
        capacity_kwp: 설비 용량. 상계 구간 판정에 쓴다 (:func:`surplus_options`).
        external_price_won_per_kwh: 잉여 판매 단가. 없으면 금액을 비운다.
        smp_price_won_per_kwh: 상계 잔여의 정산 단가. 없으면 잔여 kWh 만 낸다.
    """
    opts = options if options is not None else BillingOptions()
    index = pd.DatetimeIndex(usage.kw.index)
    interval = usage.meta.interval_minutes
    slot_hours = interval / 60.0

    surplus = surplus_kw.reindex(index).fillna(0.0).astype(float)
    energy = surplus * slot_hours
    total_kwh = float(energy.sum())

    calendar = build_calendar(
        range(index[0].year - 1, index[-1].year + 2),
        sunday_is_holiday=opts.sunday_is_holiday,
        exclude_temporary=(
            table.day_rules.exclude_temporary_holiday
            if opts.exclude_temporary_holiday is None
            else opts.exclude_temporary_holiday
        ),
        extra_holidays=opts.extra_holidays,
        excluded_holidays=opts.excluded_holidays,
    )
    slots = classify_slots(
        index,
        interval,
        table,
        calendar,
        contract_type=selection.contract_type,
        region_group=opts.region_group,
    )
    rates = table.rates(selection)

    # 상계거래 — **잉여가 난 슬롯의 실제 계시**로 그 달 사용량에서 차감한다.
    # 낮 시간을 일괄로 중간부하에 넣지 않는다 (41세션 2-3).
    months = slots["month"].to_numpy()
    bands = slots["band"].to_numpy()
    seasons = slots["season"].to_numpy()
    discounts = slots["discount_rate"].to_numpy()

    surplus_by_band: dict[pd.Period, dict[str, float]] = {}
    for month, band, kwh in zip(months, bands, energy.to_numpy(dtype=float), strict=True):
        if kwh <= 0:
            continue
        surplus_by_band.setdefault(month, dict.fromkeys(BANDS, 0.0))[str(band)] += float(kwh)

    # 차감 한도와 그 계시의 **실효 단가**. 할인 특례(산업용(을) 봄·가을 주말)가 같은
    # 시간대 안에서도 갈리므로 금액을 함께 쌓아 나눈다 — 요금 엔진과 같은 방식이다.
    net = net_usage if net_usage is not None else usage
    net_energy = net.energy_kwh().reindex(index).fillna(0.0).to_numpy(dtype=float)
    usage_by_band: dict[pd.Period, dict[str, float]] = {}
    won_by_band: dict[pd.Period, dict[str, float]] = {}
    for month, season, band, discount, kwh in zip(
        months, seasons, bands, discounts, net_energy, strict=True
    ):
        value = 0.0 if pd.isna(kwh) else float(kwh)
        room = usage_by_band.setdefault(month, dict.fromkeys(BANDS, 0.0))
        money = won_by_band.setdefault(month, dict.fromkeys(BANDS, 0.0))
        room[str(band)] += value
        money[str(band)] += value * rates.rate(str(season), str(band)) * (1.0 - float(discount))
    price_by_band = {
        month: {
            band: (won_by_band[month][band] / kwh if kwh > 0 else 0.0)
            for band, kwh in room.items()
        }
        for month, room in usage_by_band.items()
    }

    # **상계를 쓸 수 없는 구간이면 정산도 내지 않는다.** 값이 남아 있으면 쓰이지
    # 않을 금액이 결과에 실려 다니고, 언젠가 누군가 그것을 근거로 쓴다.
    settlement = (
        _offset_settlement(
            surplus_by_band,
            usage_by_band,
            price_by_band,
            capacity_kwp=capacity_kwp,
            smp_price_won_per_kwh=smp_price_won_per_kwh,
        )
        if OFFSET_SCENARIO in surplus_options(capacity_kwp)
        else None
    )

    hour_distribution = energy.groupby(index.hour).sum()
    hour_distribution.index.name = "hour"
    hour_distribution.name = "kwh"

    day_type = slots["day_type"].to_numpy()
    weekend_mask = day_type == "saturday"
    holiday_mask = day_type == "holiday"
    weekday_kwh = float(energy[~(weekend_mask | holiday_mask)].sum())

    offered = surplus_options(capacity_kwp)
    scenarios: list[SurplusScenario] = []
    if settlement is not None:
        scenarios.append(
            SurplusScenario(
                OFFSET_SCENARIO,
                settlement.revenue_won,
                _offset_basis(settlement, table),
                _ADMIN_NOTES[OFFSET_SCENARIO],
            )
        )
    scenarios.append(
        SurplusScenario(
            EXTERNAL_SCENARIO,
            total_kwh * external_price_won_per_kwh
            if external_price_won_per_kwh is not None
            else None,
            # **입력 이름 그대로 적는다** (27세션 7-2). 입력칸은 「잉여 판매 단가」
            # 인데 표에서는 「외부 단가」 라고 불러 두 값인 줄 알게 했다.
            (
                f"잉여 판매 단가 {external_price_won_per_kwh:,.1f} 원/kWh 적용"
                if external_price_won_per_kwh is not None
                else "잉여 판매 단가를 입력하면 산출합니다. 단가를 지어내지 않습니다."
            ),
            _ADMIN_NOTES[EXTERNAL_SCENARIO],
        )
    )
    scenarios.append(
        SurplusScenario(
            DISCARD_SCENARIO,
            0.0,
            "역송하지 않고 버립니다.",
            _ADMIN_NOTES[DISCARD_SCENARIO],
        )
    )

    notices: list[Notice] = [info(ELIGIBILITY_NOTICE, fact="surplus.eligibility")]
    if total_kwh > 0:
        # **한 줄이다** (41세션 2-3). 구간을 가르는 사정은 화면에 길게 쓰지 않는다.
        notices.append(
            basis(
                f"상계 구간은 설정된 {capacity_kwp:,.0f} kWp 로 판정했습니다 — 규정은 "
                "모듈 정격과 인버터 용량 중 작은 값이 기준이지만 초기 검토 도구이므로 "
                "인버터를 입력받지 않습니다.",
                fact="surplus.offset_band",
            )
        )
        if OFFSET_SCENARIO in offered:
            notices.append(
                basis(
                    "상계 차감은 잉여가 난 슬롯의 계시 그대로 그 달 사용량에서 빼고, "
                    "그 달 그 계시 사용량을 넘으면 0 에서 멈춰 같은 계시로 다음 달에 "
                    "이월합니다. **기본요금은 바뀌지 않습니다** — 전력량요금만 다시 "
                    "계산했습니다.",
                    fact="surplus.offset_basis",
                )
            )
    return SurplusResult(
        total_kwh=total_kwh,
        generation_kwh=generation_kwh,
        share_of_generation=total_kwh / generation_kwh if generation_kwh > 0 else None,
        hour_distribution=hour_distribution,
        weekday_kwh=weekday_kwh,
        weekend_kwh=float(energy[weekend_mask].sum()),
        holiday_kwh=float(energy[holiday_mask].sum()),
        scenarios=tuple(scenarios),
        offset=settlement,
        notices=tuple(notices),
    )


def _offset_basis(settlement: OffsetSettlement, table: TariffTable) -> str:
    """상계 금액의 근거 한 줄.

    **정산 시점이나 기간에 관한 단서를 달지 않는다** (41세션 2-3). 단가는 하나로
    적용하고 기간 길이를 구분하지 않는다.
    """
    head = (
        f"당월 차감 {settlement.deducted_kwh:,.0f} kWh — 잉여가 난 슬롯의 계시 단가로 "
        f"상계 ({table.effective_date} 시행 요금표)."
    )
    if settlement.remaining_kwh <= 0:
        return head
    if not settlement.settles_cash:
        return (
            f"{head} 잔여 {settlement.remaining_kwh:,.0f} kWh 는 이월만 되고 현금 정산이 "
            f"없습니다 ({offset_carry_only_max_kw():,.0f} kWp 이하)."
        )
    if settlement.smp_won is None:
        return f"{head} 잔여 {settlement.remaining_kwh:,.0f} kWh — SMP 단가 미입력"
    return (
        f"{head} 잔여 {settlement.remaining_kwh:,.0f} kWh 를 SMP "
        f"{settlement.smp_price_won_per_kwh:,.1f} 원/kWh 로 정산."
    )
