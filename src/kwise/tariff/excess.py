"""초과사용부가금 (기본공급약관 제67조의3 ③ · 109세션).

**금액을 만드는 자리는 여기 하나다.** 108세션까지 이 제도는 **글로만** 있었다 —
엔진이 「초과사용부가금 대상입니다」 라고 안내하면서 그 몫을 청구 총액에
한 원도 싣지 않았다. 부르는 쪽마다 세면 같은 자료에서 두 값이 나온다(83세션).

    초과전력 = 그 달 최대수요전력 − 계약전력
    초과비율 = 초과전력 ÷ 계약전력
    부가금   = 초과전력 × 해당 계약종별 **기본요금 단가** × 배수

**요금적용전력이 아니라 그 달 최대수요전력이다.** 12개월 창도, 30% 하한도,
대상월도 여기에는 없다. **경부하도 센다** — 별표3 단서 2호의 경부하 제외는
요금적용전력의 것이고 제67조의3 에는 그 단서가 없다.

**곱하는 것은 기본요금 단가(원/kW)이지 그 달 기본요금이 아니다.**

**첫 초과 달은 예고뿐이다** (제4항). 세칙 제48조의2 ② 1. 이 같은 것을 3년 창으로
다시 적는데, 분석 기간이 한 해라 그 창은 언제나 열려 있다 — 곧 같은 결과다.
그래서 **「초과한 달」 과 「청구되는 달」 이 하나 어긋난다.** 이름을 갈라 둔다.

**이 표는 제68조 제1항 고객의 것이다.** 계약전력 기준 고객(제68조 ②)은
제67조의3 **제1항**이라 구간이 다르다 — 초과횟수 기준이거나 450 kWh/kW
기준이고, 세칙에 예외 목록(가압상수도·무선기지국·현장조사 등)이 붙어 **우리가
판정할 수 없는 갈래**가 있다. 그래서 그 갈래는 **산출하지 않는다.**
:func:`~kwise.tariff.engine.calculate_bill` 이 `base_fee_basis` 로 가른다.

**값은 ``data\\rules_kr.json`` 에 있다. 모듈 상수로 붙잡지 않는다.**
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

import pandas as pd

from kwise.rules import rule_value

__all__ = [
    "ExcessCharge",
    "ExcessMonth",
    "excess_charges",
    "excess_grace_months",
    "excess_multiplier",
    "excess_tiers",
]

# 초과비율의 부스러기를 터는 자리 수. **20%·30% 경계에서 한 구간이 갈린다** —
# 12,000/10,000 − 1 이 0.19999999999999996 으로 떨어지면 배수가 200% 가 아니라
# 150% 가 된다. 나눗셈이 낳는 오차는 1e-15 수준이라 9자리면 넉넉하고, 실제로
# 뜻이 있는 자리(0.1 kW 단위 계량)는 훨씬 위에 있다.
_RATIO_DIGITS = 9


def excess_tiers() -> tuple[tuple[float, float], ...]:
    """(초과비율 하한, 기본요금 단가 배수) 구간표. 하한이 올라가는 차례다."""
    return tuple(
        (float(floor), float(multiplier))
        for floor, multiplier in rule_value("excess_charge.ratio_tiers")
    )


def excess_grace_months() -> int:
    """청구 전에 예고만 하는 달 수 (제4항). 지금은 1 이다."""
    return int(rule_value("excess_charge.grace_months"))


def excess_multiplier(ratio: float) -> float:
    """초과비율에 걸리는 배수. **판정 직전에 부스러기를 턴다.**"""
    rounded = round(ratio, _RATIO_DIGITS)
    multiplier = 0.0
    for floor, value in excess_tiers():
        if rounded >= floor:
            multiplier = value
        else:
            break
    return multiplier


@dataclass(frozen=True)
class ExcessMonth:
    """초과한 달 하나. **예고 달도 여기 있다** (금액이 0 일 뿐이다)."""

    month: pd.Period
    max_demand_kw: float
    excess_kw: float
    excess_ratio: float
    multiplier: float
    won: float
    charged: bool
    """청구되는가. 첫 초과 달은 ``False`` 이고 그 달의 ``won`` 은 0 이다."""


@dataclass(frozen=True)
class ExcessCharge:
    """한 계산의 초과사용부가금 전부."""

    months: tuple[ExcessMonth, ...] = field(default=())
    """**초과한 달** 전부. 예고 달을 포함한다."""
    applicable: bool = False
    """이 계산에 제67조의3 ③ 이 걸리는가.

    ``False`` 는 **「0원」 이 아니라 「산출하지 않았다」** 이다 — 계약전력 기준
    고객(제68조 ②)이거나 계약전력을 모르는 경우다. 둘을 섞으면 산출물이
    「부가금 없음」 을 두 뜻으로 말한다 (미해결 「산출물 열에서 0 이 세 뜻을
    갖는다」 와 같은 병이다).
    """

    @property
    def total_won(self) -> float:
        return sum(item.won for item in self.months)

    @property
    def exceeded_months(self) -> tuple[pd.Period, ...]:
        """**초과한** 달. 예고 달을 포함한다."""
        return tuple(item.month for item in self.months)

    @property
    def charged_months(self) -> tuple[pd.Period, ...]:
        """**청구되는** 달. 첫 초과 달이 빠져 있다."""
        return tuple(item.month for item in self.months if item.charged)

    def won_of(self, month: pd.Period) -> float:
        """그 달에 청구되는 부가금. 초과하지 않은 달은 0 이다."""
        for item in self.months:
            if item.month == month:
                return item.won
        return 0.0


def excess_charges(
    monthly_max_kw: Mapping[pd.Period, float],
    *,
    contract_kw: float | None,
    base_rate_won_per_kw: float,
) -> ExcessCharge:
    """제67조의3 ③ 의 초과사용부가금.

    Args:
        monthly_max_kw: 달 → **그 달 관측 최대수요전력** (경부하 포함).
            **보간하지 않는다** — 결측 구간이 있는 달은 남은 자료의 최대로
            판정하므로 그 달의 부가금은 과소 산출될 수 있다. 이 도구는 결측을
            메우지 않는다는 규약이 여기에도 그대로 걸린다.
        contract_kw: 계약전력. ``None`` 이면 산출하지 않는다.
        base_rate_won_per_kw: 해당 계약종별 기본요금 단가 (원/kW).

    부분 월에는 안분 계수를 곱하지 않는다 — 조문에 일할 규정이 없고, 초과는
    안분되는 요금이 아니라 **그 달에 일어났거나 아닌** 사실이다. 다만 부분 월은
    자료가 그 달의 일부뿐이라 관측 최대가 그 달 진짜 최대보다 낮을 수 있다.
    """
    if contract_kw is None or contract_kw <= 0:
        return ExcessCharge()

    grace = excess_grace_months()
    months: list[ExcessMonth] = []
    seen = 0
    for month in sorted(monthly_max_kw):
        peak = float(monthly_max_kw[month])
        if math.isnan(peak) or peak <= contract_kw:
            continue
        seen += 1
        excess_kw = peak - contract_kw
        ratio = excess_kw / contract_kw
        multiplier = excess_multiplier(ratio)
        charged = seen > grace
        months.append(
            ExcessMonth(
                month=month,
                max_demand_kw=peak,
                excess_kw=excess_kw,
                excess_ratio=ratio,
                multiplier=multiplier,
                won=excess_kw * base_rate_won_per_kw * multiplier if charged else 0.0,
                charged=charged,
            )
        )
    return ExcessCharge(months=tuple(months), applicable=True)
