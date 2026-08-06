"""계약전력 조정 (요구사항서 7.2, 6.4) — 투자 0원.

4세션에서 보류한 절감액을 여기서 채운다. 다만 **임의 가정으로 금액을 만들지 않는다.**

일반용(을)의 기본요금은 요금적용전력 기반이다. 계약전력을 낮춰도 요금적용전력이
그대로면 요금은 한 푼도 줄지 않는다. 줄어드는 경우는 요금적용전력이 계약전력의
일정 비율 아래로 내려가지 못하는 **하한 규정**에 걸려 있을 때뿐이다.
그 규정을 확인하기 전에는 :data:`ContractStatus.UNKNOWN` 을 돌려주고 금액을 비운다.
하향 여지(kW)와 위약금 경고는 언제나 낸다.

하한 비율을 받으면 **재계산**한다. 월별 요금적용전력에 하한을 씌워 기본요금을
다시 합산하고 두 계약전력을 비교한다. 부분 월 안분 계수도 그대로 반영된다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

import pandas as pd

from kwise.diagnose import DEFAULT_MARGIN_RATIO
from kwise.io import UsageData
from kwise.measures.base import Certainty, annualize
from kwise.tariff import BillingResult

__all__ = [
    "MARGIN_NOTICE",
    "ContractAdjustment",
    "ContractStatus",
    "evaluate_contract_adjustment",
]

MARGIN_NOTICE = (
    "기본요금은 직전 12개월 중 최대수요로 결정됩니다. 계약전력을 하향할 경우, "
    "예측 오차와 기상 변동을 고려하여 충분한 여유를 확보하십시오. "
    "한 번의 초과가 12개월간 적용됩니다."
)
_PENALTY_NOTICE = (
    "계약전력 하향은 되돌리기 어렵고 초과 시 위약금이 발생합니다. "
    "하향 폭은 운영 계획(증설·용도 변경)을 확인한 뒤 정하십시오."
)
_UNKNOWN_NOTICE = (
    "요금적용전력의 계약전력 대비 하한 규정을 확인하지 못했습니다. "
    "한전 기본공급약관 확인 전에는 절감액을 산출하지 않습니다. "
    "확인되면 contract_floor_ratio 로 넘기십시오."
)


class ContractStatus(StrEnum):
    """절감액 산출 가능 여부."""

    CONFIRMED = "산출"
    UNKNOWN = "미확인"


@dataclass(frozen=True, eq=False)
class ContractAdjustment:
    """계약전력 조정 평가.

    Attributes:
        saving_won: :attr:`status` 가 ``CONFIRMED`` 일 때만 값이 있다.
        reduction_kw: 하향 여지. 하한 규정을 몰라도 언제나 나온다.
    """

    status: ContractStatus
    contract_kw: float
    billing_demand_kw: float
    max_demand_kw: float
    utilization: float
    headroom_kw: float
    over_contract_slots: int
    margin_ratio: float
    suggested_contract_kw: float
    reduction_kw: float
    contract_floor_ratio: float | None
    current_base_won: float
    adjusted_base_won: float | None
    saving_won: float | None
    annual_saving_won: float | None
    saving_basis: str
    certainty: Certainty = Certainty.HIGH
    investment_won: float = 0.0
    warnings: tuple[str, ...] = field(default=())
    notes: tuple[str, ...] = field(default=())

    @property
    def is_over_contracted(self) -> bool:
        return self.reduction_kw > 0


def _base_fee_won(bill: BillingResult, floor_kw: float) -> float:
    """월별 요금적용전력에 하한을 씌워 기본요금을 다시 합산한다.

    하한 적용 **전** 값에서 출발한다. 이미 씌워진 하한을 다시 씌우면 계약전력을
    낮춘 효과가 사라진다.
    """
    monthly = bill.monthly
    column = (
        "demand_before_floor_kw"
        if "demand_before_floor_kw" in monthly.columns
        else "billing_demand_kw"
    )
    demand = monthly[column].clip(lower=floor_kw)
    return float((demand * bill.base_rate_won_per_kw * monthly["base_fee_factor"]).sum())


def evaluate_contract_adjustment(
    usage: UsageData,
    bill: BillingResult,
    *,
    contract_kw: float,
    contract_floor_ratio: float | None = None,
    margin_ratio: float = DEFAULT_MARGIN_RATIO,
    step_kw: float = 1.0,
) -> ContractAdjustment:
    """계약전력을 낮출 여지와, 하한 규정을 아는 경우의 절감액을 낸다.

    Args:
        contract_floor_ratio: 요금적용전력의 계약전력 대비 하한 비율.
            None 이면 요금표의 종별 속성(일반용(을) 30%)을 쓴다. 종별 속성마저
            비어 있으면 '미확인' 을 돌려주고 금액을 만들지 않는다.
        margin_ratio: 권장 계약전력에 얹을 여유율.
    """
    if contract_kw <= 0:
        raise ValueError(f"계약전력은 양수여야 합니다: {contract_kw}")

    ratio = contract_floor_ratio if contract_floor_ratio is not None else bill.contract_floor_ratio
    observed = usage.kw.dropna()
    max_demand = float(observed.max()) if len(observed) else 0.0
    billing_demand = float(bill.billing_demand_kw)
    over_slots = int((observed > contract_kw).sum())

    suggested = min(contract_kw, math.ceil(billing_demand * (1 + margin_ratio) / step_kw) * step_kw)
    reduction = max(0.0, contract_kw - suggested)

    warnings = [MARGIN_NOTICE, _PENALTY_NOTICE]
    if over_slots:
        warnings.append(
            f"계약전력 {contract_kw:,.0f} kW 를 넘은 구간이 {over_slots:,}건 있습니다. "
            "하향이 아니라 상향·초과 위약 검토 대상입니다."
        )

    if ratio is None:
        warnings.append(_UNKNOWN_NOTICE)
        return ContractAdjustment(
            status=ContractStatus.UNKNOWN,
            contract_kw=contract_kw,
            billing_demand_kw=billing_demand,
            max_demand_kw=max_demand,
            utilization=billing_demand / contract_kw,
            headroom_kw=contract_kw - billing_demand,
            over_contract_slots=over_slots,
            margin_ratio=margin_ratio,
            suggested_contract_kw=suggested,
            reduction_kw=reduction,
            contract_floor_ratio=None,
            current_base_won=bill.total_base_won,
            adjusted_base_won=None,
            saving_won=None,
            annual_saving_won=None,
            saving_basis="하한 규정 미확인 — 금액 미산출",
            warnings=tuple(warnings),
            notes=(_UNKNOWN_NOTICE,),
        )

    if not 0.0 <= ratio <= 1.0:
        raise ValueError(f"하한 비율은 0~1 이어야 합니다: {ratio}")

    # 하한 적용 전 값으로 되돌린 뒤 두 계약전력에서 각각 다시 씌운다.
    current_base = _base_fee_won(bill, contract_kw * ratio)
    adjusted_base = _base_fee_won(bill, suggested * ratio)
    saving = current_base - adjusted_base
    basis = (
        f"요금적용전력 하한 {ratio:.0%} 적용, "
        f"월별 기본요금을 {bill.base_fee_months:.2f}개월분으로 재계산"
    )
    notes = [
        basis,
        "전력량요금은 계약전력과 무관하므로 변하지 않습니다.",
    ]
    if saving <= 0:
        notes.append(
            "하한이 요금적용전력에 걸리지 않아 계약전력을 낮춰도 기본요금이 줄지 않습니다."
        )
    return ContractAdjustment(
        status=ContractStatus.CONFIRMED,
        contract_kw=contract_kw,
        billing_demand_kw=billing_demand,
        max_demand_kw=max_demand,
        utilization=billing_demand / contract_kw,
        headroom_kw=contract_kw - billing_demand,
        over_contract_slots=over_slots,
        margin_ratio=margin_ratio,
        suggested_contract_kw=suggested,
        reduction_kw=reduction,
        contract_floor_ratio=ratio,
        current_base_won=current_base,
        adjusted_base_won=adjusted_base,
        saving_won=saving,
        annual_saving_won=annualize(saving, bill.base_fee_months),
        saving_basis=basis,
        warnings=tuple(warnings),
        notes=tuple(notes),
    )


def contract_demand_series(
    bill: BillingResult, contract_kw: float, floor_ratio: float
) -> pd.Series:
    """월별 요금적용전력에 하한을 씌운 결과. 명세에 붙일 수 있다."""
    return bill.monthly["billing_demand_kw"].clip(lower=contract_kw * floor_ratio)
