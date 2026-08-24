"""개선안별 요약 — **독립 평가 결과 그대로** (14세션 5-1).

3단계의 첫 표는 2단계에서 켠 수단을 한 자리에 모은 것이다. **여기서 다시
계산하지 않는다.** 2단계 카드가 이미 낸 값을 옮길 뿐이고, 두 화면의 숫자가
어긋나면 어느 쪽을 믿어야 할지 알 수 없게 된다.

합계 행은 **「단순 합」** 이라고 명시한다. 수단을 함께 도입하면 서로 영향을 주므로
이 합이 최종 효과가 아니다 — 그 차이를 보이는 것이 3단계의 존재 이유이고,
:mod:`kwise.compare.combination` 이 조합을 통째로 재계산해서 낸다.

**이 표는 화면 전용이다.** Excel 은 :func:`kwise.report.excel.measure_summary_frame`,
Word 는 :func:`kwise.report.document.measure_entries` 가 따로 만든다.

**모든 금액과 수량은 12개월 환산이다** (28세션 3절). 한 칸이라도 기간 값이면
같은 열에서 기준이 갈라지는데, 표에는 그 사실을 적을 자리가 없다 — 잉여 상계
수익이 그랬다. 환산은 :func:`standalone_rows` 가 ``base_fee_months`` 로 한다.

**7.7 잉여 활용 줄은 41세션에 빠졌다.** 잉여는 개선안이 아니라 태양광의 결과라
개선안이 여섯이 됐다 (:mod:`kwise.measures.catalog`). ``surplus`` 인자는 남겨
두되 표에는 줄을 세우지 않는다 — 부르는 쪽을 한꺼번에 고치지 않아도 되게 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from kwise import money
from kwise.measures import (
    BASE_FEE_UNCHANGED,
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
    payback_text,
)
from kwise.report.columns import option_label
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
        reduction: **개선 방안** — 무엇을 얼마나 하는지 (28세션 1절). 수단마다
            단위가 다르므로(kW·kWh·kWp·%) 문자열이고, **끝에 동사를 붙인다** —
            「80 kWp」 만으로는 그것을 짓자는 것인지 줄이자는 것인지 알 수 없다.
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
    zero_reason: str = ""
    """**0 이 결론인 줄**에 0원 대신 적을 말 (48세션).

    계약전력 조정의 「기본요금 변화없음」 이 그것이다 — 2단계 카드가 그렇게 적는데
    3단계 요약만 「0원」 이라 적으면 두 화면이 다른 말을 한다.
    """
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
    base_fee_months: float | None = None,
) -> tuple[StandaloneRow, ...]:
    """켠 수단을 **7장 순서 그대로** 한 줄씩. 계산하지 않고 옮기기만 한다.

    Args:
        surplus: **41세션부터 쓰이지 않는다.** 잉여는 개선안이 아니라 태양광의
            결과라 표에서 줄이 빠졌다. 부르는 쪽이 그대로 넘겨도 되게 남겨 둔다.
        base_fee_months: 기간을 12개월로 환산하는 데 쓴다 (28세션 3절). 기본값을
            두지 않는 것은 ``contract_floor_ratio`` 와 같은 이유다 (5세션) —
            지어낸 가정으로 만든 금액을 누군가 근거로 쓴다.
    """
    del surplus, base_fee_months  # 41세션에 잉여 줄이 빠지면서 함께 쓰이지 않는다
    rows: list[StandaloneRow] = []

    if switch is not None:
        current, best = switch.current.selection.option, switch.best.selection.option
        rows.append(
            StandaloneRow(
                kind=measure_kind("tariff_switch"),
                reduction=(
                    f"{option_label(current)} 유지"
                    if current == best
                    else f"{option_label(current)} → {option_label(best)} 전환"
                ),
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
                zero_reason=BASE_FEE_UNCHANGED if contract.base_fee_unchanged else "",
            )
        )
    if demand_response is not None:
        rows.append(
            StandaloneRow(
                kind=measure_kind("demand_response"),
                reduction=f"{demand_response.annual_reducible_kwh:,.0f} kWh 입찰",
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
                reduction=(
                    f"{power_factor.current_pct:,.1f}% → {power_factor.target_pct:,.1f}% 개선"
                ),
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
                reduction=f"{solar.capacity_kwp:,.0f} kWp 설치",
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
                reduction=f"{ess.power_kw:,.0f} kW / {ess.capacity_kwh:,.0f} kWh 설치 "
                f"(목표 {ess.excess.target_kw:,.0f} kW)",
                annual_saving_won=ess.annual_saving_won,
                investment_won=ess.investment_won,
                payback_years=ess.payback_years,
                certainty=ess.certainty,
            )
        )
    # **7.7 잉여 활용을 41세션에 뺐다.** 개선안이 아니라 태양광의 결과다 —
    # 상계 수익은 태양광 카드 안에서 낸다 (:mod:`kwise.measures.surplus`).
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
    """3단계 화면의 요약 표. **합계 행에 「단순 합」이라고 적는다.**

    **금액은 만원 단위다** (28세션 1-3). 원 단위 아홉 자리가 여섯 줄 늘어서면
    자릿수를 세어 가며 읽어야 한다 — 여기는 대조하는 자리가 아니라 크기를
    견주는 자리다. 원 단위 대조는 Excel 「수단별 결과」 가 맡는다.

    **열 이름은 「개선 방안」이다** (28세션 1-1). 「절감량」 이라고 적어 두고
    용량(kWp)·요금제 이름처럼 절감량이 아닌 것을 담고 있었다.

    **확실성 열은 없다** (28세션 4절). 무엇에 대한 등급인지가 이름에 없어
    「높음」 이 어느 정도인지 읽는 사람이 가늠할 수 없었고, 잉여가 0 이라
    수익 0원이 확정인 줄에도 「중간~낮음」 이 붙었다. 등급 자체는 데이터와
    Excel·Word 에 그대로 남는다 — 없앤 것은 화면 표기다.
    """
    body: list[dict[str, object]] = [
        {
            "수단": row.title,
            "개선 방안": row.reduction,
            "연간 절감액": (
                row.zero_reason
                if row.zero_reason and not row.annual_saving_won
                else money.won_short(
                    row.annual_saving_won,
                    reason=row.saving_reason or UNPRICED_REASONS["contract"],
                )
            ),
            "투자비": money.won_short(
                row.investment_won, reason=row.investment_reason or "미산출 — 단가 미입력"
            ),
            "회수기간": _payback(row),
        }
        for row in rows
    ]
    if body:
        body.append(
            {
                "수단": SIMPLE_SUM_LABEL,
                "개선 방안": "—",
                "연간 절감액": money.won_short(simple_sum_won(rows), reason="—"),
                "투자비": money.won_short(
                    sum(row.investment_won or 0.0 for row in rows), reason="—"
                ),
                "회수기간": "—",
            }
        )
    return pd.DataFrame(body)


def _payback(row: StandaloneRow) -> str:
    """**표시 상한을 넘으면 「>50년」 이다** (50세션 3-7)."""
    if row.payback_years is None:
        return "미산출"
    if row.payback_years <= 0:
        return "즉시"
    return payback_text(row.payback_years)
