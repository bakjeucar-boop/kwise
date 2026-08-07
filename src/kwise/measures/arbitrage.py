"""ESS 차익거래 (요구사항서 7.6).

경부하에 충전해 최대부하에 방전하는 **단가차익**이다.

    수익/kWh·년 = Σ_계절 (최대부하단가 − 경부하단가 ÷ 왕복효율) × 계절별 평일수

**계시별 단가는 요금 엔진에서 자동으로 가져온다. 사용자 입력이 아니다.**
평일 수도 요금 달력에서 센다 — 최대부하 시간대가 실제로 존재하는 날만 세면
토요일(중간부하까지)과 일요일·공휴일(전량 경부하)이 자동으로 빠진다.

**수익 구조를 이해하는 것이 이 모듈의 목적이다.**

    피크저감 수익  ∝ 출력(kW)    용량을 늘려도 늘지 않는다
    차익거래 수익  ∝ 용량(kWh)
    투자비         ∝ 용량(kWh)   (kW당 단가 = CAPEX_Power + CAPEX_Energy × 방전시간)

그래서 **용량을 늘릴수록 회수기간이 나빠진다.** 그리고 차익거래만으로는 배터리
값을 뽑지 못한다 — 샘플에서 연 19,656원/kWh 이므로 CAPEX_Energy 369,936원/kWh
대비 18.8년이고, 배터리 수명(10~15년)을 넘는다. **"왜 안 되는가"에 대한 답이
이 숫자다.**

차익거래 수익을 피크저감 절감액에 **더하지 않는다.** 피크컷 디스패치가 이미
일부를 실현하고 있어 그대로 더하면 이중 계산이 된다 (:mod:`kwise.measures.ess`
의 절감액은 순부하 재계산 결과라 실현분이 들어 있다). 여기서 내는 값은 **매 평일
한 사이클을 온전히 돌렸을 때의 잠재값**이며 별도 줄로 표시한다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from kwise.io import UsageData
from kwise.quality import QualityReport
from kwise.rules import assumption
from kwise.tariff import (
    BillingOptions,
    TariffSelection,
    TariffTable,
    build_calendar,
    classify_slots,
)

__all__ = [
    "ArbitrageValue",
    "SeasonSpread",
    "arbitrage_value",
    "c_rate",
    "default_cycles_per_day",
    "peak_days_by_season",
]


def default_cycles_per_day() -> float:
    """평일 1사이클. 피크컷용 배터리를 매 평일 한 번 돌린다는 가정이다 (판단값)."""
    return float(assumption("ess.cycles_per_day"))


@dataclass(frozen=True)
class SeasonSpread:
    """계절 하나의 단가차익."""

    season: str
    peak_won_per_kwh: float
    light_won_per_kwh: float
    days: int
    round_trip: float

    @property
    def spread_won_per_kwh(self) -> float:
        """왕복효율을 반영한 1 kWh 방전당 차익. 충전은 효율만큼 더 사야 한다."""
        return self.peak_won_per_kwh - self.light_won_per_kwh / self.round_trip

    @property
    def won_per_kwh_period(self) -> float:
        return self.spread_won_per_kwh * self.days


@dataclass(frozen=True)
class ArbitrageValue:
    """차익거래 잠재 수익.

    Attributes:
        won_per_kwh_year: **12개월 환산** 1 kWh 당 수익. 이 숫자가 핵심이다.
        annual_won: 가용 용량 전체의 12개월 환산 수익.
        standalone_payback_years: 차익거래 **단독** 회수기간.
            CAPEX 의 에너지 성분(원/kWh)만으로 나눈다 — 차익거래는 용량이 만드는
            수익이므로 출력 성분을 부담시키지 않는 것이 가장 유리한 가정이다.
            그 유리한 가정에서도 성립하지 않는다는 것이 요점이다.
    """

    spreads: tuple[SeasonSpread, ...]
    won_per_kwh_period: float
    won_per_kwh_year: float
    usable_kwh: float
    annual_won: float
    cycles_per_day: float
    round_trip: float
    period_days: int
    capex_energy_won_per_kwh: float | None = None
    standalone_payback_years: float | None = None
    battery_life_years: tuple[float, float] = (10.0, 15.0)
    notes: tuple[str, ...] = field(default=())

    @property
    def outlives_battery(self) -> bool:
        """단독 회수기간이 배터리 수명을 넘는가. 넘으면 단독으로는 성립하지 않는다."""
        if self.standalone_payback_years is None:
            return True
        return self.standalone_payback_years > self.battery_life_years[1]

    def frame(self) -> pd.DataFrame:
        """계절별 내역. 어느 계절이 얼마를 벌어 주는지 보여 준다."""
        return pd.DataFrame(
            [
                {
                    "계절": item.season,
                    "최대부하 단가(원/kWh)": item.peak_won_per_kwh,
                    "경부하 단가(원/kWh)": item.light_won_per_kwh,
                    "차익(원/kWh)": item.spread_won_per_kwh,
                    "평일수": item.days,
                    "기간 수익(원/kWh)": item.won_per_kwh_period,
                }
                for item in self.spreads
            ]
        ).set_index("계절")


def peak_days_by_season(
    usage: UsageData,
    table: TariffTable,
    *,
    selection: TariffSelection | None = None,
    options: BillingOptions | None = None,
) -> dict[str, int]:
    """계절별 **최대부하가 존재하는 날** 수를 센다.

    토요일은 최대부하가 중간부하로 계량되고 일요일·공휴일은 전량 경부하이므로
    이 방식이면 자동으로 빠진다. DR 거래일(6.6)과는 다른 규칙이라 따로 센다 —
    DR 은 토요일도 제외지만 여기서는 토요일에 중간부하 차익이 남는다.
    """
    opts = options if options is not None else BillingOptions()
    index = pd.DatetimeIndex(usage.kw.index)
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
        usage.meta.interval_minutes,
        table,
        calendar,
        contract_type=selection.contract_type if selection else None,
        region_group=opts.region_group,
    )
    peak = slots[slots["band"] == "peak"]
    if peak.empty:
        return {}
    days = pd.DatetimeIndex(peak["slot_start"]).normalize()
    counted = pd.DataFrame({"season": peak["season"].to_numpy(), "day": days})
    return {
        str(season): int(group["day"].nunique())
        for season, group in counted.groupby("season", sort=True)
    }


def arbitrage_value(
    usage: UsageData,
    table: TariffTable,
    selection: TariffSelection,
    *,
    usable_kwh: float,
    round_trip: float,
    base_fee_months: float,
    cycles_per_day: float | None = None,
    capex_energy_won_per_kwh: float | None = None,
    quality: QualityReport | None = None,
    options: BillingOptions | None = None,
) -> ArbitrageValue:
    """경부하 충전 → 최대부하 방전의 단가차익을 낸다.

    Args:
        usable_kwh: 가용 용량 (정격 × DoD).
        capex_energy_won_per_kwh: 단독 회수기간을 낼 CAPEX 에너지 성분.
            주지 않으면 회수기간을 내지 않는다 — 지어낸 단가로 금액을 만들지 않는다.
        cycles_per_day: 평일 사이클 수. 기본 1.
    """
    if not 0 < round_trip <= 1:
        raise ValueError(f"왕복효율은 0 초과 1 이하여야 합니다: {round_trip}")
    cycles_per_day = default_cycles_per_day() if cycles_per_day is None else cycles_per_day
    if cycles_per_day < 0:
        raise ValueError(f"사이클 수는 음수일 수 없습니다: {cycles_per_day}")
    _ = quality  # 결측은 단가·달력 계산에 영향을 주지 않는다

    rates = table.rates(selection)
    days = peak_days_by_season(usage, table, selection=selection, options=options)
    spreads = tuple(
        SeasonSpread(
            season=season,
            peak_won_per_kwh=rates.rate(season, "peak"),
            light_won_per_kwh=rates.rate(season, "light"),
            days=count,
            round_trip=round_trip,
        )
        for season, count in sorted(days.items())
    )

    per_kwh_period = sum(item.won_per_kwh_period for item in spreads) * cycles_per_day
    per_kwh_year = per_kwh_period * 12.0 / base_fee_months if base_fee_months > 0 else 0.0
    annual = per_kwh_year * usable_kwh

    payback: float | None = None
    if capex_energy_won_per_kwh is not None and per_kwh_year > 0:
        payback = capex_energy_won_per_kwh / per_kwh_year

    notes = [
        "계시별 단가는 요금표에서 가져왔습니다. 사용자 입력이 아닙니다.",
        f"최대부하가 존재하는 날만 셌습니다 (계절별 {days}). 토요일은 최대부하가 "
        "중간부하로 계량되고 일요일·공휴일은 전량 경부하라 자동으로 빠집니다.",
        f"평일 {cycles_per_day:g} 사이클, 왕복효율 {round_trip:.0%} 가정입니다.",
        "**피크저감 절감액에 더하지 않았습니다.** 피크컷 디스패치가 이미 일부를 "
        "실현하고 있어 그대로 더하면 이중 계산이 됩니다. 이 값은 매 평일 한 사이클을 "
        "온전히 돌렸을 때의 잠재값입니다.",
    ]
    if payback is not None:
        life_low, life_high = 10.0, 15.0
        verdict = (
            f"배터리 수명({life_low:.0f}~{life_high:.0f}년)을 넘어 **단독으로는 "
            "성립하지 않습니다.**"
            if payback > life_high
            else "배터리 수명 안에 들어옵니다."
        )
        notes.append(
            f"차익거래 단독 회수기간 {payback:,.1f}년 — 연 {per_kwh_year:,.0f}원/kWh 로 "
            f"CAPEX 에너지 성분 {capex_energy_won_per_kwh:,.0f}원/kWh 를 회수합니다. "
            f"{verdict}"
        )

    return ArbitrageValue(
        spreads=spreads,
        won_per_kwh_period=per_kwh_period,
        won_per_kwh_year=per_kwh_year,
        usable_kwh=usable_kwh,
        annual_won=annual,
        cycles_per_day=cycles_per_day,
        round_trip=round_trip,
        period_days=sum(days.values()),
        capex_energy_won_per_kwh=capex_energy_won_per_kwh,
        standalone_payback_years=payback,
        notes=tuple(notes),
    )


def c_rate(discharge_hours: float) -> float:
    """방전시간 → C-rate. 0.5h 는 2C 다."""
    if discharge_hours <= 0:
        return math.inf
    return 1.0 / discharge_hours
