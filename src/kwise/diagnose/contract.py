"""계약 정보와 계약전력 적정성 (요구사항서 3.2, 6.4).

계약전력 대비 최대수요가 낮으면 과계약이다. **PV 와 무관하게 즉시 돈이 나오는
항목이다.** 다만 하향은 되돌리기 어렵고 초과 시 위약금이 있으므로 여유 확보
권고를 반드시 함께 낸다.

**절감액은 하한 규정으로 결정된다.** 일반용(을)의 기본요금은 요금적용전력 기반이므로,
계약전력을 낮춰도 요금적용전력이 그대로면 요금은 그대로다. 줄어드는 경우는
요금적용전력이 계약전력의 일정 비율(일반용(을) 30%, 교육용 등 15% 특례) 아래로
못 내려가는 **하한 규정**에 걸려 있을 때뿐이다 (요구사항서 5.2 ③).
비율을 받지 못하면 금액을 만들어내지 않고 하향 여지(kW)만 낸다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from kwise.notices import Notice, basis, block, warn
from kwise.rules import assumption, rule_value
from kwise.tariff import TariffSelection

__all__ = [
    "ContractAdequacy",
    "ContractInfo",
    "assess_contract",
    "deemed_power_factor_pct",
    "default_margin_ratio",
]


def deemed_power_factor_pct() -> float:
    """역률을 모를 때 쓰는 간주 지상역률 (약관 제42조).

    **모듈 상수로 붙잡지 않는다.** import 시점에 고정하면 기준 데이터를 고쳐도
    그 프로세스에서는 옛 값으로 계산된다 (8세션 준비 결정).
    """
    return float(rule_value("power_factor.deemed_lagging_pct"))


def default_margin_ratio() -> float:
    """계약전력 권장 여유율. 판단값이다 (``assumptions.json``)."""
    return float(assumption("contract.margin_ratio"))


def margin_range() -> tuple[float, float]:
    """권장 여유율 범위. **근거는 기준 데이터의 note 에 있다** (13세션).

    하한은 월 최대수요의 통상 진폭, 상한은 그 이상 확보하면 절감이 사라지는
    지점이다. 화면 슬라이더의 권장 구간 표시에 쓴다.
    """
    low, high = assumption("contract.margin_range")
    return (float(low), float(high))


_MARGIN_NOTICE = (
    "기본요금은 직전 12개월 중 최대수요로 결정됩니다. 계약전력을 하향할 경우, "
    "예측 오차와 기상 변동을 고려하여 충분한 여유를 확보하십시오. "
    "한 번의 초과가 12개월간 적용됩니다."
)
_FLOOR_UNKNOWN = (
    "요금적용전력의 계약전력 대비 하한 규정을 확인하지 못해 절감액을 산출하지 "
    "않았습니다. 하향 여지(kW)만 참고하십시오. 한전 기본공급약관 확인 후 "
    "contract_floor_ratio 로 넘기면 재계산합니다."
)


@dataclass(frozen=True)
class ContractInfo:
    """계약 정보 (요구사항서 3.2). 진단 단계에서 필요한 유일한 입력이다.

    설비 정보는 이 단계에서 묻지 않는다.
    """

    selection: TariffSelection
    contract_kw: float | None = None
    power_factor_pct: float = field(default_factory=deemed_power_factor_pct)
    """주지 않으면 약관 제42조의 간주값. **생성 시점에 파일에서 읽는다.**"""

    def __post_init__(self) -> None:
        if self.contract_kw is not None and self.contract_kw <= 0:
            raise ValueError(f"계약전력은 양수여야 합니다: {self.contract_kw}")
        if not 0 < self.power_factor_pct <= 100:
            raise ValueError(f"역률은 0~100% 여야 합니다: {self.power_factor_pct}")


@dataclass(frozen=True)
class ContractAdequacy:
    """계약전력 적정성.

    Attributes:
        utilization: 최대수요 ÷ 계약전력. 낮으면 과계약이다.
        suggested_contract_kw: 여유율을 얹은 권장 계약전력.
        saving_won: 하한 규정을 아는 경우에만 값이 있다. 모르면 None.
    """

    contract_kw: float
    billing_demand_kw: float
    max_demand_kw: float
    utilization: float
    headroom_kw: float
    over_contract_slots: int
    margin_ratio: float
    suggested_contract_kw: float
    reduction_kw: float
    saving_won: float | None
    saving_basis: str
    notices: tuple[Notice, ...] = field(default=())

    @property
    def is_over_contracted(self) -> bool:
        return self.reduction_kw > 0


def assess_contract(
    kw: pd.Series,
    *,
    contract_kw: float,
    billing_demand_kw: float,
    base_rate_won_per_kw: float,
    base_fee_months: float,
    margin_ratio: float | None = None,
    contract_floor_ratio: float | None = None,
    step_kw: float = 1.0,
) -> ContractAdequacy:
    """계약전력 적정성을 본다.

    Args:
        billing_demand_kw: 요금적용전력 (12개월 규칙 적용값).
        margin_ratio: 권장 계약전력에 얹을 여유율. None 이면 판단값을 읽는다.
        contract_floor_ratio: 요금적용전력의 계약전력 대비 하한 비율.
            None 이면 절감액을 산출하지 않는다.
        step_kw: 계약전력 조정 단위.
    """
    if contract_kw <= 0:
        raise ValueError(f"계약전력은 양수여야 합니다: {contract_kw}")
    margin_ratio = default_margin_ratio() if margin_ratio is None else margin_ratio

    observed = kw.dropna()
    max_demand = float(observed.max()) if len(observed) else 0.0
    over_slots = int((observed > contract_kw).sum())

    target = billing_demand_kw * (1.0 + margin_ratio)
    suggested = min(contract_kw, math.ceil(target / step_kw) * step_kw)
    reduction = max(0.0, contract_kw - suggested)

    notices: list[Notice] = [warn(_MARGIN_NOTICE)]
    if over_slots:
        notices.append(
            warn(
                f"계약전력 {contract_kw:,.0f} kW 를 넘은 구간이 {over_slots:,}건 있습니다. "
                "하향 대상이 아니라 상향·초과 위약 검토 대상입니다."
            )
        )

    saving: float | None = None
    if contract_floor_ratio is None:
        basis_text = "하한 규정 미확인 — 미산출"
        # **차단** — 금액을 만들지 않는다.
        notices.append(block(_FLOOR_UNKNOWN))
    else:
        # 재계산한다. 두 계약전력 각각에서 요금적용전력을 다시 구해 기본요금을 낸다.
        current_demand = max(billing_demand_kw, contract_kw * contract_floor_ratio)
        target_demand = max(billing_demand_kw, suggested * contract_floor_ratio)
        saving = (current_demand - target_demand) * base_rate_won_per_kw * base_fee_months
        basis_text = (
            f"요금적용전력 하한 {contract_floor_ratio:.0%} 가정, "
            f"기본요금 {base_fee_months:.2f}개월분 기준"
        )
        notices.append(basis(basis_text))

    return ContractAdequacy(
        contract_kw=contract_kw,
        billing_demand_kw=billing_demand_kw,
        max_demand_kw=max_demand,
        utilization=billing_demand_kw / contract_kw,
        headroom_kw=contract_kw - billing_demand_kw,
        over_contract_slots=over_slots,
        margin_ratio=margin_ratio,
        suggested_contract_kw=suggested,
        reduction_kw=reduction,
        saving_won=saving,
        saving_basis=basis_text,
        notices=tuple(notices),
    )
