"""계약전력 조정 (요구사항서 7.2, 6.4) — 투자 0원.

4세션에서 보류한 절감액을 여기서 채운다. 다만 **임의 가정으로 금액을 만들지 않는다.**

기본요금이 요금적용전력에 붙는 종별(을 · 갑Ⅱ)은 계약전력을 낮춰도 요금적용전력이
그대로면 요금이 한 푼도 줄지 않는다. 줄어드는 경우는 요금적용전력이 계약전력의
일정 비율 아래로 내려가지 못하는 **하한 규정**(약관 제68조 제1항의 30%)에 걸려
있을 때뿐이다. 비율이 없는 종별 — 곧 기본요금이 **계약전력**에 붙는 종별 — 은
:data:`ContractStatus.UNKNOWN` 을 돌려주고 금액을 비운다. 그쪽은 계산식 자체가
달라(계약전력 × 단가) 이 함수의 전제가 서지 않는다.
하향 여지(kW)와 위약금 경고는 언제나 낸다.

하한 비율을 받으면 **재계산**한다. 월별 요금적용전력에 하한을 씌워 기본요금을
다시 합산하고 두 계약전력을 비교한다. 부분 월 안분 계수도 그대로 반영된다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

import pandas as pd

from kwise.diagnose import default_margin_ratio
from kwise.io import UsageData
from kwise.measures.base import Certainty, annualize
from kwise.notices import Notice, basis, block, warn
from kwise.tariff import BillingResult

__all__ = [
    "BASE_FEE_UNCHANGED",
    "MARGIN_NOTICE",
    "ContractAdjustment",
    "ContractStatus",
    "evaluate_contract_adjustment",
]

BASE_FEE_UNCHANGED = "기본요금 변화없음"
"""절감액 자리에 **0원 대신** 적는 결론 (48세션).

「하향 여지 8 kW」 옆에 「0원」 이 서면 계산이 덜 된 것처럼 읽힌다. 둘은 다른
물음이다 — 여지는 「낮출 수 있는가」 이고 절감액은 「낮추면 돈이 주는가」 다.
요금적용전력이 하한(계약전력의 30%)보다 훨씬 크면 계약전력을 낮춰도 기본요금
기준이 그대로라 한 푼도 줄지 않는다 (약관 제68조 제1항).
**여지를 0 으로 내리지 않는다** — 여지는 사실이고, 이 문구가 그 사실의 값을 말한다.
"""

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
    "요금적용전력 하한 비율이 요금 데이터에 없어 절감액을 산출하지 않습니다. "
    "기본요금이 계약전력에 붙는 종별이면 저압·고압 전제부터 청구서로 확인하십시오."
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
    notices: tuple[Notice, ...] = field(default=())

    @property
    def is_over_contracted(self) -> bool:
        return self.reduction_kw > 0

    @property
    def base_fee_unchanged(self) -> bool:
        """**하향 여지는 있는데 기본요금이 안 바뀌는가** (48세션).

        참이면 절감액 자리에 0원 대신 :data:`BASE_FEE_UNCHANGED` 를 적는다.
        하한 규정을 모르는 경우(``UNKNOWN``)는 여기 들지 않는다 — 그쪽은
        「변화없음」 이 아니라 「미산출」 이다.
        """
        return (
            self.status is ContractStatus.CONFIRMED
            and self.is_over_contracted
            and not self.saving_won
        )


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
    margin_ratio: float | None = None,
    step_kw: float = 1.0,
) -> ContractAdjustment:
    """계약전력을 낮출 여지와, 하한 규정을 아는 경우의 절감액을 낸다.

    Args:
        contract_floor_ratio: 요금적용전력의 계약전력 대비 하한 비율.
            None 이면 요금표의 종별 속성(30%, 교육용(을) 15%)을 쓴다. 종별
            속성마저 비어 있으면 '미확인' 을 돌려주고 금액을 만들지 않는다.
        margin_ratio: 권장 계약전력에 얹을 여유율. None 이면 판단값을 읽는다.
    """
    if contract_kw <= 0:
        raise ValueError(f"계약전력은 양수여야 합니다: {contract_kw}")
    margin_ratio = default_margin_ratio() if margin_ratio is None else margin_ratio

    ratio = contract_floor_ratio if contract_floor_ratio is not None else bill.contract_floor_ratio
    observed = usage.kw.dropna()
    max_demand = float(observed.max()) if len(observed) else 0.0
    billing_demand = float(bill.billing_demand_kw)
    over_slots = int((observed > contract_kw).sum())

    suggested = min(contract_kw, math.ceil(billing_demand * (1 + margin_ratio) / step_kw) * step_kw)
    reduction = max(0.0, contract_kw - suggested)

    # 둘 다 **주의**다. 하향은 되돌리기 어렵고 한 번의 초과가 12개월을 지배한다.
    notices = [
        warn(MARGIN_NOTICE, fact="contract.margin"),
        warn(_PENALTY_NOTICE, fact="contract.penalty"),
    ]
    if over_slots:
        # 1단계 진단이 내는 것과 **같은 사실**이다 (diagnose\contract.py).
        notices.append(
            warn(
                f"계약전력 {contract_kw:,.0f} kW 를 넘은 구간이 {over_slots:,}건 있습니다. "
                "하향이 아니라 상향·초과 위약 검토 대상입니다.",
                fact="contract.over_limit",
            )
        )

    if ratio is None:
        # **차단이다.** 하한 비율이 없는 종별은 기본요금이 계약전력에 붙는 쪽이고
        # (제68조 제2항), 그쪽은 아래 재계산의 전제 자체가 서지 않는다.
        notices.append(block(_UNKNOWN_NOTICE, fact="contract.floor_unknown"))
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
            saving_basis="하한 비율 없음 — 금액 미산출",
            notices=tuple(notices),
        )

    if not 0.0 <= ratio <= 1.0:
        raise ValueError(f"하한 비율은 0~1 이어야 합니다: {ratio}")

    # 하한 적용 전 값으로 되돌린 뒤 두 계약전력에서 각각 다시 씌운다.
    current_base = _base_fee_won(bill, contract_kw * ratio)
    adjusted_base = _base_fee_won(bill, suggested * ratio)
    saving = current_base - adjusted_base
    basis_text = (
        f"요금적용전력 하한 {ratio:.0%} 적용, "
        f"월별 기본요금을 {bill.base_fee_months:.2f}개월분으로 재계산"
    )
    notices += [
        basis(basis_text, fact="contract.saving_basis"),
        basis(
            "전력량요금은 계약전력과 무관하므로 변하지 않습니다.",
            fact="contract.energy_unchanged",
        ),
    ]
    if saving <= 0:
        notices.append(
            basis(
                "하한이 요금적용전력에 걸리지 않아 계약전력을 낮춰도 기본요금이 줄지 않습니다.",
                fact="contract.floor_not_binding",
            )
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
        saving_basis=basis_text,
        notices=tuple(notices),
    )


def contract_demand_series(
    bill: BillingResult, contract_kw: float, floor_ratio: float
) -> pd.Series:
    """월별 요금적용전력에 하한을 씌운 결과. 명세에 붙일 수 있다."""
    return bill.monthly["billing_demand_kw"].clip(lower=contract_kw * floor_ratio)
