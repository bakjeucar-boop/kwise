"""잉여 활용 (요구사항서 7.6).

**자격요건은 판정하지 않는다.** 제도별 자격요건과 행정 절차는 별도 확인 사항이며,
여기서는 금액만 참고로 제시한다. 예상 행정 부담을 한 줄로 병기한다.

    시나리오 A  버림(자가소비만)              0원
    시나리오 B  상계거래                      그 시각의 전력량요금 단가로 상계
    시나리오 C  외부 신재생에너지 구매 연계   사용자가 넣은 외부 단가

상계거래 금액은 요금표에서 계산한다 — 잉여가 난 슬롯의 계절·시간대 단가를 그대로
쓴다. 단가를 지어내지 않는다. 외부 구매는 단가를 모르므로 입력이 없으면 비운다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from kwise.io import UsageData
from kwise.tariff import (
    BillingOptions,
    TariffSelection,
    TariffTable,
    build_calendar,
    classify_slots,
)

__all__ = [
    "ELIGIBILITY_NOTICE",
    "SurplusResult",
    "SurplusScenario",
    "evaluate_surplus",
]

ELIGIBILITY_NOTICE = (
    "제도별 자격요건과 행정 절차는 별도 확인이 필요합니다. 본 도구는 자격을 "
    "판정하지 않으며 금액만 참고로 제시합니다."
)
_ADMIN_NOTES = {
    "버림": "행정 부담 없음. 잉여를 계통에 내보내지 않고 버린다.",
    "상계거래": "계약 변경, 역송 계량기, 월별 정산 관리가 필요하다.",
    "외부 신재생에너지 구매 연계": "구매자 발굴·계약, 정산 대행, 계량·인증 관리가 필요하다.",
}


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
    notes: tuple[str, ...] = field(default=())

    @property
    def weekend_share(self) -> float | None:
        """주말·공휴일 집중도. 높으면 자가소비가 어려운 구조다."""
        if self.total_kwh <= 0:
            return None
        return self.holiday_kwh / self.total_kwh

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
    external_price_won_per_kwh: float | None = None,
    options: BillingOptions | None = None,
) -> SurplusResult:
    """잉여량·시간대 분포와 활용 시나리오 3종을 낸다.

    Args:
        surplus_kw: 역송된 출력. :func:`kwise.measures.apply_generation` 의 결과.
        external_price_won_per_kwh: 외부 신재생에너지 구매 단가. 없으면 금액을 비운다.
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

    # 상계거래 — 잉여가 난 슬롯의 계절·시간대 단가로 상계한다.
    unit_prices = pd.Series(
        [
            rates.rate(str(season), str(band))
            for season, band in zip(slots["season"], slots["band"], strict=True)
        ],
        index=index,
    )
    offset_won = float((energy * unit_prices).sum())

    hour_distribution = energy.groupby(index.hour).sum()
    hour_distribution.index.name = "hour"
    hour_distribution.name = "kwh"

    day_type = slots["day_type"].to_numpy()
    weekend_mask = day_type == "saturday"
    holiday_mask = day_type == "holiday"
    weekday_kwh = float(energy[~(weekend_mask | holiday_mask)].sum())

    scenarios = (
        SurplusScenario("버림", 0.0, "자가소비만 한다. 잉여는 버린다.", _ADMIN_NOTES["버림"]),
        SurplusScenario(
            "상계거래",
            offset_won,
            f"잉여가 난 슬롯의 전력량요금 단가로 상계 ({table.effective_date} 시행 요금표).",
            _ADMIN_NOTES["상계거래"],
        ),
        SurplusScenario(
            "외부 신재생에너지 구매 연계",
            total_kwh * external_price_won_per_kwh
            if external_price_won_per_kwh is not None
            else None,
            (
                f"외부 단가 {external_price_won_per_kwh:,.1f} 원/kWh 적용"
                if external_price_won_per_kwh is not None
                else "외부 단가를 입력하면 산출합니다. 단가를 지어내지 않습니다."
            ),
            _ADMIN_NOTES["외부 신재생에너지 구매 연계"],
        ),
    )
    return SurplusResult(
        total_kwh=total_kwh,
        generation_kwh=generation_kwh,
        share_of_generation=total_kwh / generation_kwh if generation_kwh > 0 else None,
        hour_distribution=hour_distribution,
        weekday_kwh=weekday_kwh,
        weekend_kwh=float(energy[weekend_mask].sum()),
        holiday_kwh=float(energy[holiday_mask].sum()),
        scenarios=scenarios,
        notes=(ELIGIBILITY_NOTICE,),
    )
