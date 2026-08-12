"""개선안별 요약 — **독립 평가 결과 그대로** (14세션 5-1).

3단계의 첫 표는 2단계에서 켠 수단을 한 자리에 모은 것이다. **여기서 다시
계산하지 않는다.** 2단계 카드가 이미 낸 값을 옮길 뿐이고, 두 화면의 숫자가
어긋나면 어느 쪽을 믿어야 할지 알 수 없게 된다.

합계 행은 **「단순 합」** 이라고 명시한다. 수단을 함께 도입하면 서로 영향을 주므로
이 합이 최종 효과가 아니다 — 그 차이를 보이는 것이 3단계의 존재 이유이고,
:mod:`kwise.compare.combination` 이 조합을 통째로 재계산해서 낸다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from kwise import money
from kwise.measures import (
    Certainty,
    ContractAdjustment,
    DemandResponseResult,
    EssResult,
    MeasureKind,
    PowerFactorResult,
    SolarPoint,
    SurplusResult,
    TariffSwitchResult,
    measure_kind,
)
from kwise.report.notices import UNPRICED_REASONS

__all__ = [
    "SIMPLE_SUM_LABEL",
    "SIMPLE_SUM_NOTE",
    "StandaloneRow",
    "combinable_keys",
    "simple_sum_won",
    "standalone_frame",
    "standalone_rows",
]

SIMPLE_SUM_LABEL = "단순 합"
SIMPLE_SUM_NOTE = (
    "**단순 합입니다.** 수단을 함께 도입하면 서로 영향을 주므로 이것이 최종 "
    "효과가 아닙니다 — 합산효과에서 조합을 통째로 다시 계산합니다."
)

# 조합 재계산에 들어가는 수단. 나머지는 요금이 아니라 **별도 정산·수익**이라
# 조합 부하에 얹을 수 없다 (경제성DR 정산금·잉여 판매 수익).
_COMBINABLE: tuple[str, ...] = ("tariff_switch", "contract", "power_factor", "solar", "ess")


def combinable_keys() -> tuple[str, ...]:
    """합산효과가 다루는 수단. 나머지는 표에서 따로 적는다."""
    return _COMBINABLE


@dataclass(frozen=True)
class StandaloneRow:
    """개선안 하나의 독립 평가 결과.

    Attributes:
        reduction: 절감량 — 수단마다 단위가 다르다 (kW·kWh·%). **문자열이다.**
        annual_saving_won: 12개월 환산 절감액. 모르면 ``None`` 이고 사유가 붙는다.
        combinable: 합산효과 재계산에 들어가는가. 아니면 표에서 따로 적는다.
    """

    kind: MeasureKind
    reduction: str
    annual_saving_won: float | None
    investment_won: float | None
    payback_years: float | None
    certainty: Certainty
    saving_reason: str = ""
    investment_reason: str = ""
    notes: tuple[str, ...] = field(default=())

    @property
    def key(self) -> str:
        return self.kind.key

    @property
    def title(self) -> str:
        return self.kind.title

    @property
    def combinable(self) -> bool:
        return self.kind.key in _COMBINABLE


def standalone_rows(
    *,
    switch: TariffSwitchResult | None = None,
    contract: ContractAdjustment | None = None,
    demand_response: DemandResponseResult | None = None,
    power_factor: PowerFactorResult | None = None,
    solar: SolarPoint | None = None,
    solar_certainty: Certainty | None = None,
    solar_investment_reason: str = "",
    ess: EssResult | None = None,
    surplus: SurplusResult | None = None,
) -> tuple[StandaloneRow, ...]:
    """켠 수단을 **7장 순서 그대로** 한 줄씩. 계산하지 않고 옮기기만 한다."""
    rows: list[StandaloneRow] = []

    if switch is not None:
        rows.append(
            StandaloneRow(
                kind=measure_kind("tariff_switch"),
                reduction=f"요금제 {switch.current.selection.option} → "
                f"{switch.best.selection.option}",
                annual_saving_won=switch.annual_saving_won,
                investment_won=0.0,
                payback_years=0.0,
                certainty=switch.certainty,
            )
        )
    if contract is not None:
        rows.append(
            StandaloneRow(
                kind=measure_kind("contract"),
                reduction=f"{contract.reduction_kw:,.0f} kW 하향",
                annual_saving_won=contract.annual_saving_won,
                investment_won=0.0,
                payback_years=0.0 if contract.saving_won else None,
                certainty=contract.certainty,
                saving_reason=contract.saving_basis,
            )
        )
    if demand_response is not None:
        rows.append(
            StandaloneRow(
                kind=measure_kind("demand_response"),
                reduction=f"{demand_response.annual_reducible_kwh:,.0f} kWh 감축",
                annual_saving_won=demand_response.settlement_won,
                investment_won=0.0,
                payback_years=0.0 if demand_response.is_priced else None,
                certainty=demand_response.certainty,
                saving_reason=demand_response.settlement_label,
            )
        )
    if power_factor is not None:
        rows.append(
            StandaloneRow(
                kind=measure_kind("power_factor"),
                reduction=f"{power_factor.current_pct:,.1f}% → {power_factor.target_pct:,.1f}%",
                annual_saving_won=power_factor.annual_saving_won,
                investment_won=power_factor.investment_won,
                payback_years=power_factor.payback_years,
                certainty=power_factor.certainty,
            )
        )
    if solar is not None:
        rows.append(
            StandaloneRow(
                kind=measure_kind("solar"),
                reduction=f"{solar.capacity_kwp:,.0f} kWp",
                annual_saving_won=solar.annual_saving_won,
                investment_won=solar.investment_won,
                payback_years=solar.payback_years,
                certainty=solar_certainty or Certainty.MEDIUM,
                investment_reason=solar_investment_reason or UNPRICED_REASONS["pv_price"],
            )
        )
    if ess is not None:
        rows.append(
            StandaloneRow(
                kind=measure_kind("ess"),
                reduction=f"목표 {ess.excess.target_kw:,.0f} kW · "
                f"{ess.power_kw:,.0f} kW / {ess.capacity_kwh:,.0f} kWh",
                annual_saving_won=ess.annual_saving_won,
                investment_won=ess.investment_won,
                payback_years=ess.payback_years,
                certainty=ess.certainty,
            )
        )
    if surplus is not None:
        offset = surplus.scenario("상계거래")
        rows.append(
            StandaloneRow(
                kind=measure_kind("surplus"),
                reduction=f"{surplus.total_kwh:,.0f} kWh 잉여",
                annual_saving_won=offset.revenue_won,
                investment_won=0.0,
                payback_years=0.0 if offset.is_priced else None,
                certainty=Certainty.MEDIUM_LOW,
                saving_reason=offset.basis,
            )
        )
    return tuple(rows)


def simple_sum_won(rows: tuple[StandaloneRow, ...], *, combinable_only: bool = False) -> float:
    """**단순 합.** 모르는 금액은 0 으로 세지 않고 그냥 뺀다.

    Args:
        combinable_only: 합산효과와 견줄 때 켠다. 조합 재계산에 들어가지 않는
            수단(경제성DR·잉여)까지 더하면 같은 것을 비교하는 것이 아니다.
    """
    return sum(
        row.annual_saving_won or 0.0
        for row in rows
        if row.annual_saving_won is not None and (row.combinable or not combinable_only)
    )


def standalone_frame(rows: tuple[StandaloneRow, ...]) -> pd.DataFrame:
    """화면·산출물이 같이 쓰는 표. **합계 행에 「단순 합」이라고 적는다.**"""
    body: list[dict[str, object]] = [
        {
            "수단": row.title,
            "절감량": row.reduction,
            "연간 절감액": money.won(
                row.annual_saving_won,
                reason=row.saving_reason or UNPRICED_REASONS["contract"],
            ),
            "투자비": money.won(
                row.investment_won, reason=row.investment_reason or "미산출 — 단가 미입력"
            ),
            "회수기간": _payback(row),
            "확실성": str(row.certainty),
        }
        for row in rows
    ]
    if body:
        body.append(
            {
                "수단": SIMPLE_SUM_LABEL,
                "절감량": "—",
                "연간 절감액": money.won(simple_sum_won(rows), reason="—"),
                "투자비": money.won(sum(row.investment_won or 0.0 for row in rows), reason="—"),
                "회수기간": "—",
                "확실성": "—",
            }
        )
    return pd.DataFrame(body)


def _payback(row: StandaloneRow) -> str:
    if row.payback_years is None:
        return "미산출"
    if row.payback_years <= 0:
        return "즉시"
    return f"{row.payback_years:,.1f}년"
