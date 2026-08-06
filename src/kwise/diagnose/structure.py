"""현재 요금 구조 (요구사항서 6.3).

3세션의 요금 엔진을 호출만 한다. 요금 계산을 여기서 다시 만들지 않는다.
시간대·계절 귀속도 tariff 의 분류기를 그대로 쓴다 (구간 시작 시각 기준).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from kwise.io import UsageData
from kwise.tariff import (
    BANDS,
    BillingOptions,
    BillingResult,
    TariffSelection,
    TariffTable,
    build_calendar,
    classify_slots,
)

__all__ = ["ChargeStructure", "charge_structure"]


@dataclass(frozen=True, eq=False)
class ChargeStructure:
    """요금 구조.

    Attributes:
        band_kwh: 경/중간/최대부하 사용량. 요일 규칙이 적용된 뒤의 계량 기준이다.
        season_kwh: 계절별 사용량.
        base_share: 기본요금 비중. 이 값이 크면 피크 저감의 여지가 크다.
    """

    bill: BillingResult
    band_kwh: pd.Series
    season_kwh: pd.Series
    base_won: float
    energy_won: float
    total_won: float
    base_share: float
    energy_share: float

    @property
    def monthly(self) -> pd.DataFrame:
        return self.bill.monthly

    @property
    def selection(self) -> TariffSelection:
        return self.bill.selection

    @property
    def band_share(self) -> pd.Series:
        total = float(self.band_kwh.sum())
        return (self.band_kwh / total).rename("share") if total else self.band_kwh * 0.0

    @property
    def season_share(self) -> pd.Series:
        total = float(self.season_kwh.sum())
        return (self.season_kwh / total).rename("share") if total else self.season_kwh * 0.0


def charge_structure(
    usage: UsageData,
    table: TariffTable,
    bill: BillingResult,
    *,
    options: BillingOptions | None = None,
) -> ChargeStructure:
    """월별 명세에서 비중을 뽑고, 계절별 사용량을 덧붙인다.

    시간대별 사용량은 이미 월별 명세에 있으므로 합치기만 한다. 계절별은
    분류기를 한 번 더 돌려 구한다 — 전력량은 ``energy_kwh()`` 를 쓴다.
    """
    opts = options if options is not None else BillingOptions()
    monthly = bill.monthly
    band_kwh = pd.Series({band: float(monthly[f"{band}_kwh"].sum()) for band in BANDS}, name="kwh")
    band_kwh.index.name = "band"

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
        contract_type=bill.selection.contract_type,
        region_group=opts.region_group,
    )
    energy = usage.energy_kwh()
    season_kwh = (
        pd.Series(energy.to_numpy(dtype=float), index=index)
        .groupby(slots["season"].to_numpy(), observed=True)
        .sum()
        .rename("kwh")
    )
    season_kwh.index.name = "season"

    total = bill.total_won
    return ChargeStructure(
        bill=bill,
        band_kwh=band_kwh,
        season_kwh=season_kwh,
        base_won=bill.total_base_won,
        energy_won=bill.total_energy_won,
        total_won=total,
        base_share=bill.total_base_won / total if total else 0.0,
        energy_share=bill.total_energy_won / total if total else 0.0,
    )
