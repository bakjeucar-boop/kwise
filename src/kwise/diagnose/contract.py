"""계약 정보와 계약전력 적정성 (요구사항서 3.2, 6.4).

계약전력 대비 최대수요가 낮으면 과계약이다. **PV 와 무관하게 즉시 돈이 나오는
항목이다.** 다만 하향은 되돌리기 어렵고 초과 시 위약금이 있으므로 여유 확보
권고를 반드시 함께 낸다.

**절감액은 하한 규정으로 결정된다.** 기본요금이 요금적용전력에 붙는 종별
(을 · 갑Ⅱ)은 계약전력을 낮춰도 요금적용전력이 그대로면 요금이 그대로다.
줄어드는 경우는 요금적용전력이 계약전력의 30% 아래로 못 내려가는 **하한 규정**에
걸려 있을 때뿐이다 (약관 제68조 제1항). **비율은 종별로 갈리지 않는다** (90세션) —
15% 는 세칙 별표4 8. 의 초·중·고교·유치원 **신청** 특례이지 종별 속성이 아니다.
비율을 받지 못하면 금액을 만들어내지 않는다.

**목표 계약전력은 어느 달에도 하한이 안 걸리는 가장 큰 값이다** (83세션에
하한으로 옮기고 105세션에 달로 내렸다). 여유율을 얹은 「권장
계약전력」 은 걷어냈다 — 근거가 붙어 있지 않았고, 기본요금이 계약전력에 붙는
종별의 산식이라 여기서는 전제가 서지 않는다. 자세한 사정은
:mod:`kwise.measures.contract` 의 머리글에 적었다.

**판정은 여기서 하지 않는다** (100세션). 98·99세션이 「문턱 아래 종별로
넘어가면 요금 전체가 준다」 를 :mod:`kwise.measures.contract` 에 세웠는데
이 자리는 여전히 하한 한 줄만 보고 있었다 — 그래서 2단계가 「299 kW 로
낮춰라」 하는 판에서 1단계는 **「적정합니다」** 라고 적었다. 한 산출물 안에서
두 장이 반대로 말한 것이다. 이제 적정성은 **조정 판정을 받아** 이용률·여유만
얹는다. 같은 사실을 두 번 세지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from kwise.notices import Notice, block, warn
from kwise.rules import rule_value
from kwise.tariff import TariffSelection, round_kw

if TYPE_CHECKING:  # 실행 시점에 들이면 measures → diagnose 와 맞물려 돈다.
    from kwise.measures.contract import ContractAdjustment

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
        # 제7조 ① — 계약전력의 계산단위는 1kW 다 (S118 ⑳). **요금 쪽 입구
        # (:class:`~kwise.tariff.engine.BillingOptions`)와 같은 값이어야 한다** —
        # 화면·PPT 는 이쪽을, 요금은 저쪽을 적으므로 한쪽만 접으면 한 산출물
        # 안에서 계약전력이 두 값으로 적힌다.
        if self.contract_kw is not None:
            object.__setattr__(self, "contract_kw", round_kw(self.contract_kw))
        if not 0 < self.power_factor_pct <= 100:
            raise ValueError(f"역률은 0~100% 여야 합니다: {self.power_factor_pct}")


@dataclass(frozen=True)
class ContractAdequacy:
    """계약전력 적정성 — **조정 판정에 이용률·여유를 얹은 것**이다 (100세션).

    판정(낮출 자리가 있는가 · 목표는 얼마인가 · 그러면 얼마인가)은
    :class:`~kwise.measures.contract.ContractAdjustment` 하나가 쥔다. 이 자리가
    따로 세면 같은 자료에서 두 값이 나온다 — 실제로 그랬다.

    Attributes:
        adjustment: 계약전력 조정 판정. **여기서 파생한다.**
        billing_demand_kw: 직전 12개월 최대수요 (하한 적용 **전**).
            이용률과 여유는 1단계가 본 이 값으로 낸다.
    """

    adjustment: ContractAdjustment
    billing_demand_kw: float
    notices: tuple[Notice, ...] = field(default=())

    # ---- 판정은 조정 쪽 하나가 쥔다. 여기서는 그대로 읽기만 한다.
    @property
    def contract_kw(self) -> float:
        return self.adjustment.contract_kw

    @property
    def max_demand_kw(self) -> float:
        return self.adjustment.max_demand_kw

    @property
    def over_contract_slots(self) -> int:
        return self.adjustment.over_contract_slots

    @property
    def contract_floor_ratio(self) -> float | None:
        return self.adjustment.contract_floor_ratio

    @property
    def floor_kw(self) -> float | None:
        return self.adjustment.floor_kw

    @property
    def target_contract_kw(self) -> float | None:
        return self.adjustment.target_contract_kw

    @property
    def saving_won(self) -> float | None:
        return self.adjustment.saving_won

    @property
    def saving_basis(self) -> str:
        return self.adjustment.saving_basis

    @property
    def crossed_label(self) -> str | None:
        """넘어가는 종별의 이름. 안 넘으면 None."""
        return self.adjustment.crossed_label

    # ---- 1단계가 스스로 내는 것 둘.
    @property
    def utilization(self) -> float:
        """최대수요 ÷ 계약전력. 낮으면 과계약이다."""
        return self.billing_demand_kw / self.contract_kw

    @property
    def headroom_kw(self) -> float:
        return self.contract_kw - self.billing_demand_kw

    @property
    def floor_binding(self) -> bool:
        """**하한이 이기는가.** 글자 그대로다 (99세션이 조정 쪽에서 갈랐다).

        「낮출 자리가 있다」 와 **다른 사실이다** — 그쪽은 :attr:`reducible` 이다.
        **판정이 아니다** (105세션) — 연간 최대만 보므로 초기 달에만 걸리는
        판에서는 거짓이면서도 낮출 자리가 있다.
        """
        return self.adjustment.floor_binding

    @property
    def reducible(self) -> bool:
        """**낮출 자리가 있는가.** 하한이 걸린 달이 있거나, 문턱 아래 종별로 넘어갈 수 있다."""
        return self.adjustment.reducible


def assess_contract(
    adjustment: ContractAdjustment,
    *,
    billing_demand_kw: float,
) -> ContractAdequacy:
    """계약전력 적정성을 본다. **판정은 만들지 않고 받는다** (100세션).

    Args:
        adjustment: 계약전력 조정 판정 (:mod:`kwise.measures.contract`).
        billing_demand_kw: 직전 12개월 최대수요 (하한 적용 **전** 값).
            이용률과 여유는 1단계가 본 이 값으로 낸다.
    """
    notices: list[Notice] = []
    if adjustment.reducible:
        notices.append(warn(_MARGIN_NOTICE, fact="contract.margin"))
    if adjustment.over_contract_slots:
        # **개선 수단 쪽과 같은 사실이다** (measures\contract.py). 문구가 세 글자
        # 다른 탓에 지문으로는 안 잡혀 화면에 두 번 나왔다 (20세션 2절).
        notices.append(
            warn(
                f"계약전력 {adjustment.contract_kw:,.0f} kW 를 넘은 구간이 "
                f"{adjustment.over_contract_slots:,}건 있습니다. "
                "하향 대상이 아니라 상향·초과 위약 검토 대상입니다.",
                fact="contract.over_limit",
            )
        )
    if adjustment.contract_floor_ratio is None:
        # **차단** — 금액을 만들지 않는다.
        notices.append(block(_FLOOR_UNKNOWN, fact="contract.floor_unknown"))

    return ContractAdequacy(
        adjustment=adjustment,
        billing_demand_kw=billing_demand_kw,
        notices=tuple(notices),
    )
