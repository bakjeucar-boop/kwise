"""계약 정보와 계약전력 적정성 (요구사항서 3.2, 6.4).

계약전력 대비 최대수요가 낮으면 과계약이다. **PV 와 무관하게 즉시 돈이 나오는
항목이다.** 다만 하향은 되돌리기 어렵고 초과 시 위약금이 있으므로 여유 확보
권고를 반드시 함께 낸다.

**절감액은 하한 규정으로 결정된다.** 기본요금이 요금적용전력에 붙는 종별
(을 · 갑Ⅱ)은 계약전력을 낮춰도 요금적용전력이 그대로면 요금이 그대로다.
줄어드는 경우는 요금적용전력이 계약전력의 일정 비율(30%, 교육용(을) 15% 특례)
아래로 못 내려가는 **하한 규정**에 걸려 있을 때뿐이다 (약관 제68조 제1항).
비율을 받지 못하면 금액을 만들어내지 않는다.

**목표 계약전력은 최대수요 ÷ 하한비율이다** (83세션). 여유율을 얹은 「권장
계약전력」 은 걷어냈다 — 근거가 붙어 있지 않았고, 기본요금이 계약전력에 붙는
종별의 산식이라 여기서는 전제가 서지 않는다. 자세한 사정은
:mod:`kwise.measures.contract` 의 머리글에 적었다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from kwise.notices import Notice, block, warn
from kwise.rules import rule_value
from kwise.tariff import TariffSelection

__all__ = [
    "ContractAdequacy",
    "ContractInfo",
    "assess_contract",
    "deemed_power_factor_pct",
]


def deemed_power_factor_pct() -> float:
    """역률을 모를 때 쓰는 간주 지상역률 (약관 제42조).

    **모듈 상수로 붙잡지 않는다.** import 시점에 고정하면 기준 데이터를 고쳐도
    그 프로세스에서는 옛 값으로 계산된다 (8세션 준비 결정).
    """
    return float(rule_value("power_factor.deemed_lagging_pct"))


_MARGIN_NOTICE = (
    "기본요금은 직전 12개월 중 최대수요로 결정됩니다. 계약전력을 하향할 경우, "
    "예측 오차와 기상 변동을 고려하여 충분한 여유를 확보하십시오. "
    "한 번의 초과가 12개월간 적용됩니다."
)
_FLOOR_UNKNOWN = (
    "요금적용전력 하한 비율이 요금 데이터에 없어 절감액을 산출하지 않았습니다. "
    "기본요금이 계약전력에 붙는 종별이면 저압·고압 전제부터 청구서로 "
    "확인하십시오."
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
        billing_demand_kw: 직전 12개월 최대수요 (하한 적용 **전**).
        utilization: 최대수요 ÷ 계약전력. 낮으면 과계약이다.
        floor_kw: 계약전력 × 하한비율. 비율을 모르면 None.
        target_contract_kw: 목표 계약전력 (최대수요 ÷ 하한비율).
            **하한이 이길 때만 값이 있다.**
        saving_won: 하한 규정을 아는 경우에만 값이 있다. 모르면 None.
    """

    contract_kw: float
    billing_demand_kw: float
    max_demand_kw: float
    utilization: float
    headroom_kw: float
    over_contract_slots: int
    contract_floor_ratio: float | None
    floor_kw: float | None
    target_contract_kw: float | None
    saving_won: float | None
    saving_basis: str
    notices: tuple[Notice, ...] = field(default=())

    @property
    def floor_binding(self) -> bool:
        """**하한이 이기는가.** 참일 때만 낮출 이유가 있다."""
        return self.target_contract_kw is not None


def assess_contract(
    kw: pd.Series,
    *,
    contract_kw: float,
    billing_demand_kw: float,
    base_rate_won_per_kw: float,
    base_fee_months: float,
    contract_floor_ratio: float | None = None,
    step_kw: float = 1.0,
) -> ContractAdequacy:
    """계약전력 적정성을 본다.

    Args:
        billing_demand_kw: 직전 12개월 최대수요 (하한 적용 **전** 값).
        contract_floor_ratio: 요금적용전력의 계약전력 대비 하한 비율.
            None 이면 절감액을 산출하지 않는다.
        step_kw: 계약전력 조정 단위.
    """
    if contract_kw <= 0:
        raise ValueError(f"계약전력은 양수여야 합니다: {contract_kw}")

    observed = kw.dropna()
    max_demand = float(observed.max()) if len(observed) else 0.0
    over_slots = int((observed > contract_kw).sum())

    floor_kw = contract_kw * contract_floor_ratio if contract_floor_ratio is not None else None
    # **판정은 이 한 줄이다** (83세션). 하한이 최대수요를 넘어야 낮출 이유가 있다.
    target = (
        min(
            contract_kw,
            math.ceil(billing_demand_kw / contract_floor_ratio / step_kw) * step_kw,
        )
        if floor_kw is not None and contract_floor_ratio and floor_kw > billing_demand_kw
        else None
    )

    notices: list[Notice] = []
    if target is not None:
        notices.append(warn(_MARGIN_NOTICE, fact="contract.margin"))
    if over_slots:
        # **개선 수단 쪽과 같은 사실이다** (measures\contract.py). 문구가 세 글자
        # 다른 탓에 지문으로는 안 잡혀 화면에 두 번 나왔다 (20세션 2절).
        notices.append(
            warn(
                f"계약전력 {contract_kw:,.0f} kW 를 넘은 구간이 {over_slots:,}건 있습니다. "
                "하향 대상이 아니라 상향·초과 위약 검토 대상입니다.",
                fact="contract.over_limit",
            )
        )

    saving: float | None = None
    if contract_floor_ratio is None or floor_kw is None:
        basis_text = "하한 비율 없음 — 미산출"
        # **차단** — 금액을 만들지 않는다.
        notices.append(block(_FLOOR_UNKNOWN, fact="contract.floor_unknown"))
    elif target is None:
        saving = 0.0
        basis_text = f"요금적용전력 하한 {contract_floor_ratio:.0%} 미적용 — 최대수요가 기준"
    else:
        # 재계산한다. 두 계약전력 각각에서 요금적용전력을 다시 구해 기본요금을 낸다.
        target_demand = max(billing_demand_kw, target * contract_floor_ratio)
        saving = (floor_kw - target_demand) * base_rate_won_per_kw * base_fee_months
        basis_text = (
            f"요금적용전력 하한 {contract_floor_ratio:.0%} 가정, "
            f"기본요금 {base_fee_months:.2f}개월분 기준"
        )
        # **안내로 내지 않는다** (25세션 3-3 · K). 2단계 7.2 카드가 같은 사실을
        # 더 자세히 낸다 (``contract.saving_basis`` — 하한과 개월수가 같은 값이다).
        # 적정성은 16세션에 7.2 로 옮겼으므로 근거도 그쪽 하나면 된다. 다만
        # 금액 옆에 붙는 사유 문자열로는 그대로 쓴다.

    return ContractAdequacy(
        contract_kw=contract_kw,
        billing_demand_kw=billing_demand_kw,
        max_demand_kw=max_demand,
        utilization=billing_demand_kw / contract_kw,
        headroom_kw=contract_kw - billing_demand_kw,
        over_contract_slots=over_slots,
        contract_floor_ratio=contract_floor_ratio,
        floor_kw=floor_kw,
        target_contract_kw=target,
        saving_won=saving,
        saving_basis=basis_text,
        notices=tuple(notices),
    )
