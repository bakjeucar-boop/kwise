"""계약전력 조정 (요구사항서 7.2, 6.4) — 투자 0원.

**판정은 하한 하나로 갈린다** (83세션). 기본요금이 요금적용전력에 붙는 종별
(을 · 갑Ⅱ)에서 요금적용전력은 이렇게 정해진다 (약관 제68조 제1항).

    요금적용전력 = max(직전 12개월 최대수요, 계약전력 × 하한비율)

    하한이 진다   하한 ≤ 최대수요 → 기준이 최대수요다. 계약전력을 낮춰도
                  요금적용전력이 그대로라 **한 푼도 줄지 않는다**
    하한이 이긴다  하한 > 최대수요 → 기준이 하한이다. 계약전력을 낮추면
                  하한이 함께 내려가 **최대수요에 닿을 때까지** 줄어든다

그래서 목표 계약전력은 **최대수요 ÷ 하한비율** 하나다 — 약관에서 바로 나오는
수이고, 그 아래로 내려도 더 얻을 것이 없는 상한이다. **여유율을 곱하지 않는다**
(83세션에 걷어냈다). 61세션이 갑Ⅰ/갑Ⅱ 를 가르며 기본요금 기준을 바로잡았는데
「요금적용전력 × (1+여유율)」 이라는 권장값이 따라오지 않았다 — 그 값은 기본요금이
**계약전력**에 붙는 자리(갑Ⅰ·교육용(갑) 저압)의 것이라 여기서는 전제가 서지 않는다.
여유율 10~30% 에는 붙은 근거도 없었다.

비율이 없는 종별은 :data:`ContractStatus.UNKNOWN` 을 돌려주고 금액을 비운다.

하한 비율을 받으면 **재계산**한다. 월별 요금적용전력에 하한을 씌워 기본요금을
다시 합산하고 두 계약전력을 비교한다. 부분 월 안분 계수도 그대로 반영된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import pandas as pd

from kwise.diagnose.contract import target_contract_kw
from kwise.io import UsageData
from kwise.measures.base import Certainty, annualize
from kwise.money import NO_SAVING
from kwise.notices import Notice, basis, block, warn
from kwise.tariff import BillingResult

__all__ = [
    "MARGIN_NOTICE",
    "NO_SAVING",
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
    "요금적용전력 하한 비율이 요금 데이터에 없어 절감액을 산출하지 않습니다. "
    "기본요금이 계약전력에 붙는 종별이면 저압·고압 전제부터 청구서로 확인하십시오."
)
FLOOR_NOT_BINDING_NOTICE = (
    "하한이 요금적용전력에 걸리지 않아 계약전력을 낮춰도 기본요금이 줄지 않습니다."
)
"""**하한이 지는 갈래의 결론.** 3단계·PPT 가 이미 이렇게 적고 있던 문장이다 —
2단계 개요가 거꾸로 적고 있어 83세션에 이 문장으로 맞췄다.
"""


class ContractStatus(StrEnum):
    """절감액 산출 가능 여부."""

    CONFIRMED = "산출"
    UNKNOWN = "미확인"


@dataclass(frozen=True, eq=False)
class ContractAdjustment:
    """계약전력 조정 평가.

    Attributes:
        billing_demand_kw: 요금적용전력 — 하한이 이기면 하한 값이다.
        demand_before_floor_kw: 직전 12개월 최대수요. **하한 판정의 상대다.**
        floor_kw: 계약전력 × 하한비율. 비율을 모르면 None.
        target_contract_kw: 목표 계약전력 (최대수요 ÷ 하한비율).
            **하한이 이길 때만 값이 있다** — 질 때는 낮출 이유가 없다.
        saving_won: :attr:`status` 가 ``CONFIRMED`` 일 때만 값이 있다.
    """

    status: ContractStatus
    contract_kw: float
    billing_demand_kw: float
    demand_before_floor_kw: float
    max_demand_kw: float
    over_contract_slots: int
    contract_floor_ratio: float | None
    floor_kw: float | None
    target_contract_kw: float | None
    current_base_won: float
    adjusted_base_won: float | None
    saving_won: float | None
    annual_saving_won: float | None
    saving_basis: str
    certainty: Certainty = Certainty.HIGH
    investment_won: float = 0.0
    notices: tuple[Notice, ...] = field(default=())

    @property
    def floor_binding(self) -> bool:
        """**하한이 이기는가.** 참일 때만 낮출 이유가 있다."""
        return self.target_contract_kw is not None

    @property
    def no_saving(self) -> bool:
        """**하한이 안 걸려 줄 것이 없는가.**

        참이면 절감액 자리에 0원 대신 :data:`NO_SAVING` 을 적는다. 하한 비율을
        모르는 경우(``UNKNOWN``)는 여기 들지 않는다 — 그쪽은 「미산출」 이다.
        """
        return self.status is ContractStatus.CONFIRMED and not self.floor_binding


def _demand_column(monthly: pd.DataFrame) -> str:
    """하한 적용 **전** 열. 이미 씌워진 하한을 다시 씌우면 효과가 사라진다."""
    return (
        "demand_before_floor_kw"
        if "demand_before_floor_kw" in monthly.columns
        else "billing_demand_kw"
    )


def _base_fee_won(bill: BillingResult, floor_kw: float) -> float:
    """월별 요금적용전력에 하한을 씌워 기본요금을 다시 합산한다."""
    monthly = bill.monthly
    demand = monthly[_demand_column(monthly)].clip(lower=floor_kw)
    return float((demand * bill.base_rate_won_per_kw * monthly["base_fee_factor"]).sum())


def evaluate_contract_adjustment(
    usage: UsageData,
    bill: BillingResult,
    *,
    contract_kw: float,
    contract_floor_ratio: float | None = None,
    step_kw: float = 1.0,
) -> ContractAdjustment:
    """하한 판정과, 하한이 이기는 경우의 목표 계약전력·절감액을 낸다.

    Args:
        contract_floor_ratio: 요금적용전력의 계약전력 대비 하한 비율.
            None 이면 요금표의 종별 속성(제68조 ①의 30%)을 쓴다. 종별
            속성마저 비어 있으면 '미확인' 을 돌려주고 금액을 만들지 않는다.
        step_kw: 계약전력 조정 단위.
    """
    if contract_kw <= 0:
        raise ValueError(f"계약전력은 양수여야 합니다: {contract_kw}")

    ratio = contract_floor_ratio if contract_floor_ratio is not None else bill.contract_floor_ratio
    observed = usage.kw.dropna()
    max_demand = float(observed.max()) if len(observed) else 0.0
    billing_demand = float(bill.billing_demand_kw)
    before_floor = float(bill.monthly[_demand_column(bill.monthly)].max())
    over_slots = int((observed > contract_kw).sum())

    notices: list[Notice] = []
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
            demand_before_floor_kw=before_floor,
            max_demand_kw=max_demand,
            over_contract_slots=over_slots,
            contract_floor_ratio=None,
            floor_kw=None,
            target_contract_kw=None,
            current_base_won=bill.total_base_won,
            adjusted_base_won=None,
            saving_won=None,
            annual_saving_won=None,
            saving_basis="하한 비율 없음 — 금액 미산출",
            notices=tuple(notices),
        )

    if not 0.0 < ratio <= 1.0:
        raise ValueError(f"하한 비율은 0 초과 1 이하여야 합니다: {ratio}")

    floor_kw = contract_kw * ratio
    # **판정은 이 한 줄이다.** 하한이 최대수요를 넘어야 낮출 이유가 생긴다.
    # 목표 산식은 1단계 적정성과 **같은 함수**를 쓴다 — 각자 올리면 같은
    # 자료에서 두 값이 나온다.
    target = (
        min(contract_kw, target_contract_kw(before_floor, ratio, step_kw))
        if floor_kw > before_floor
        else None
    )

    # 하한 적용 전 값으로 되돌린 뒤 두 계약전력에서 각각 다시 씌운다.
    current_base = _base_fee_won(bill, floor_kw)
    adjusted_base = _base_fee_won(bill, target * ratio) if target is not None else current_base
    saving = current_base - adjusted_base
    if target is None:
        basis_text = f"요금적용전력 하한 {ratio:.0%} 미적용 — 최대수요가 기준"
        notices.append(basis(FLOOR_NOT_BINDING_NOTICE, fact="contract.floor_not_binding"))
    else:
        basis_text = (
            f"요금적용전력 하한 {ratio:.0%} 적용, "
            f"월별 기본요금을 {bill.base_fee_months:.2f}개월분으로 재계산"
        )
        # **낮출 자리가 있을 때만 낸다.** 낮출 이유가 없는 갈래에서 「하향은
        # 되돌리기 어렵다」 를 읽히면 하지도 못할 일을 조심하라는 말이 된다.
        notices += [
            warn(MARGIN_NOTICE, fact="contract.margin"),
            warn(_PENALTY_NOTICE, fact="contract.penalty"),
            basis(basis_text, fact="contract.saving_basis"),
            basis(
                "전력량요금은 계약전력과 무관하므로 변하지 않습니다.",
                fact="contract.energy_unchanged",
            ),
        ]
    return ContractAdjustment(
        status=ContractStatus.CONFIRMED,
        contract_kw=contract_kw,
        billing_demand_kw=billing_demand,
        demand_before_floor_kw=before_floor,
        max_demand_kw=max_demand,
        over_contract_slots=over_slots,
        contract_floor_ratio=ratio,
        floor_kw=floor_kw,
        target_contract_kw=target,
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
