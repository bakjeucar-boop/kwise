"""계약전력 조정 (요구사항서 7.2, 6.4) — 투자 0원.

**판정은 「하한이 걸린 달」 이 갈린다** (83세션에 하한으로 옮기고 105세션에
달로 내렸다). 기본요금이 요금적용전력에 붙는 종별(을 · 갑Ⅱ)에서 요금적용전력은
**달마다** 이렇게 정해진다 (약관 제68조 제1항).

    그 달의 요금적용전력 = max(직전 12개월 최대수요, 계약전력 × 하한비율)

    걸린 달이 없다   어느 달도 하한이 그 달 수요를 못 넘는다. 계약전력을
                     낮춰도 **한 푼도 줄지 않는다**
    걸린 달이 있다   그 달들은 기준이 하한이다. 계약전력을 낮추면 하한이 함께
                     내려가 **그 달의 수요에 닿을 때까지** 줄어든다

**연간 최대 하나로 보지 않는다** (105세션 · ②-13). 굴림 12개월을 아직 못 채운
초기 달은 굴림최대가 작아 **그 달에만 하한이 걸린다** — 연간 최대만 보면 그
몫이 통째로 안 보이고, 같은 판에서 요금 엔진이 내는 「N개 월에 걸렸습니다」 와
어긋난다. 세는 함수는 :func:`kwise.tariff.demand.floor_bound_months` 하나다.

목표 계약전력은 **어느 달에도 하한이 안 걸리는 가장 큰 값**이다
(:func:`target_contract_kw`) — 약관에서 바로 나오는 수이고, 그 아래로 내려도
더 얻을 것이 없는 상한이다. **다만 관측 최대가 그보다 크면 목표는 그쪽이다**
(106세션 1절) — 그 아래는 기본요금이 더 줄어도 초과사용부가금 대상이라 권할 수
없다. 곧 목표는 두 수의 **큰 쪽**이다. **여유율을 곱하지 않는다** (83세션에 걷어냈다).
61세션이 갑Ⅰ/갑Ⅱ 를 가르며 기본요금 기준을 바로잡았는데 「요금적용전력 ×
(1+여유율)」 이라는 권장값이 따라오지 않았다 — 그 값은 기본요금이 **계약전력**에
붙는 자리(갑Ⅰ·교육용(갑) 저압)의 것이라 여기서는 전제가 서지 않는다.
여유율 10~30% 에는 붙은 근거도 없었다.

**후보가 하나 더 있다 — 종별 문턱 바로 아래다** (98세션에 세우고 99세션에
하한 갈래 밖으로 꺼냈다). 요금표가 문턱 아래 종별을 들고 있으면 거기로 넘어가
요금을 처음부터 다시 계산한다. **하한이 지는 판에도 이 후보가 선다** — 하한이
질 때 없는 것은 「같은 종별 안에서 낮출 이유」 이지 「넘어갈 자리」 가 아니다.

비율이 없는 종별은 :data:`ContractStatus.UNKNOWN` 을 돌려주고 금액을 비운다.

하한 비율을 받으면 **재계산**한다. 월별 요금적용전력에 하한을 씌워 기본요금을
다시 합산하고 두 계약전력을 비교한다. 부분 월 안분 계수도 그대로 반영된다.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

import pandas as pd

from kwise.io import UsageData
from kwise.measures.base import Certainty, annualize
from kwise.money import NO_SAVING
from kwise.notices import Notice, basis, block, warn
from kwise.tariff import (
    BillingOptions,
    BillingResult,
    TariffSelection,
    TariffTable,
    calculate_bill,
    floor_bound_months,
    list_selections,
    selection_label,
)
from kwise.tariff.schema import TariffDataError, threshold_text, within_type_threshold

__all__ = [
    "MARGIN_NOTICE",
    "NO_SAVING",
    "ContractAdjustment",
    "ContractStatus",
    "evaluate_contract_adjustment",
    "target_contract_kw",
]


def target_contract_kw(
    monthly_demand_kw: Mapping[Any, float],
    floor_ratio: float,
    step_kw: float = 1.0,
    *,
    observed_max_kw: float,
) -> float:
    """목표 계약전력 — **어느 달에도 하한이 안 걸리는 가장 큰 값**이다.

    **달별 하한 적용 전 수요를 받는다** (105세션 3절 · ②-13). 앞서는 연간
    최대 하나를 받아 ``최대수요 ÷ 하한비율`` 을 올렸는데, 요금적용전력은
    **달마다** ``max(직전 12개월 최대수요, 계약전력 × 30%)`` 라 굴림 창을 못
    채운 초기 달의 몫을 그 산식이 못 본다. 그 몫까지 얻으려면 하한을 **가장
    작은 달** 아래로 내려야 한다.

    두 수를 견주어 큰 쪽이다.

        포화   하한이 어느 달에도 안 걸리는 가장 큰 계약전력 — ``최소 달 ÷ 비율``
               을 **내림**한다. 올리면 그 달에 하한이 그대로 걸려 마지막 한 원을
               못 얻는다 (용인 을 400 kW 에서 375 는 169,437원, 374 는 175,311원)
        보전   **관측 최대 아래로는 안 내린다.** 초과사용부가금 대상이 되므로
               권고할 수 없다 — :func:`_crossed_quote` 가 문턱 후보에 거는 것과
               같은 선이다. **관측 최대를 따로 받는다** (106세션 1절)

    **달별 수요의 최대는 관측 최대가 아니다** (106세션 1절에 값으로 봤다).
    ``monthly_demand_kw`` 는 요금적용전력 산정 대상 수요라 **경부하 시간대와
    비대상월이 빠져 있다** — 105세션은 그 최대를 관측 최대로 알고 보전 갈래에
    먹였는데, 야간 피크형(C6)은 관측 최대 10,920.64 kW 인데 그 값이 2,801.00 kW
    다. 그대로 두면 목표가 **7,415 kW** 로 나와 실측이 **935구간·3,505.64 kW**
    를 넘는다 — 초과사용부가금을 막으라고 지은 갈래가 못 막았다.

    ``1e-9`` 를 더하고 내리는(그리고 빼고 올리는) 까닭은 **부동소수 부스러기**
    때문이다 — 계약 218 kW 의 하한이 ``218 * 0.3 = 65.39999999999999`` 로 잡히고
    그것을 다시 0.3 으로 나누면 ``217.99999999999997`` 이라 그대로 내리면
    217 이 된다. 화면·PPT·Excel 이 1 kW 어긋난 목표를 적는다.

    **산식이 한 자리에 있어야 한다** (83세션). **100세션에 이 자리로 옮겼다** —
    83세션은 1단계 적정성 쪽에 두고 둘이 함께 불렀는데, 이제 판정 자체가 이
    모듈 하나이므로 부르는 곳도 여기 하나다.
    """
    values = [float(value) for value in monthly_demand_kw.values()]
    saturating = math.floor(min(values) / floor_ratio / step_kw + 1e-9) * step_kw
    covering = math.ceil(observed_max_kw / step_kw - 1e-9) * step_kw
    return max(saturating, covering)


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
CONTRACT_AT_OBSERVED_MAX_NOTICE = (
    "관측 최대수요가 계약전력에 닿아 있어 계약전력을 더 낮출 자리가 없습니다."
)
"""**하한은 걸렸는데 낮출 자리가 없는 갈래** (108세션 2절).

**판정 줄은 여전히 하나다** — 이 문장과 :data:`FLOOR_NOT_BINDING_NOTICE` 는
서로 배타적이고 사실 ID 도 같다. 화면·PPT 가 그 ID 로 판정 줄을 뜨므로
갈래를 나눠도 읽는 쪽이 늘지 않는다.
"""
TYPE_THRESHOLD_FACT = "contract.crosses_type_threshold"
"""**목표 계약전력이 종별 경계 밖일 때의 사실 ID** (96세션).

**98세션에 단가를 갈아 끼웠다** — 요금표가 문턱 아래 종별을 들고 있으면
그 종별로 요금을 처음부터 다시 계산하고 싼 쪽을 권한다. 그래서 이 사실은
이제 「지금 단가로 낸 값이다」 가 아니라 **「종별이 바뀐다」** 를 말한다.
"""
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
        target_contract_kw: 목표 계약전력. **낮출 자리가 있을 때만 값이 있다** —
            하한이 걸린 달이 있으면 **어느 달에도 안 걸리는 가장 큰 값**이고,
            한 달도 안 걸려도 문턱 아래 종별로 넘어갈 수 있으면 문턱 바로
            아래다 (99세션).
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
    floor_bound_months: tuple[Any, ...] = ()
    """**하한이 실제로 걸린 달** (105세션 3절 · ②-13). 요금 안내가 세는 것과
    같은 값이다 — :func:`kwise.tariff.demand.floor_bound_months` 하나가 센다."""
    crossed_selection: TariffSelection | None = None
    """종별 문턱을 넘어 권하는 조합 (98세션). 안 넘으면 ``None``."""
    crossed_label: str | None = None
    """넘어간 종별의 이름. 예 ``"일반용전력(갑)Ⅱ"``."""
    crossed_total_won: float | None = None
    """넘어간 종별에서 **처음부터 다시 계산한** 총 요금."""
    current_total_won: float | None = None
    """현행 종별의 총 요금. :attr:`crossed_total_won` 과 짝으로만 채운다."""
    certainty: Certainty = Certainty.HIGH
    investment_won: float = 0.0
    notices: tuple[Notice, ...] = field(default=())

    @property
    def crosses_type(self) -> bool:
        """**권고가 종별을 넘는가.** 참이면 절감액이 총액 차이다."""
        return self.crossed_selection is not None

    @property
    def floor_binding(self) -> bool:
        """**하한이 이기는가.** 계약전력 × 하한비율이 **연간** 최대수요를 넘는가.

        **판정이 아니다** (105세션 · ②-13). 이 값이 거짓이어도 굴림 창을 못
        채운 초기 달에는 하한이 걸릴 수 있다 — 「낮출 자리가 있는가」 는
        :attr:`reducible` 이 말한다. 글자 그대로 「하한 > 연간 최대수요」 이고,
        **하한이 모든 달에 걸린다**는 뜻으로만 쓴다.
        """
        return self.floor_kw is not None and self.floor_kw > self.demand_before_floor_kw

    @property
    def reducible(self) -> bool:
        """**낮출 자리가 있는가.** 하한이 걸린 달이 있거나, 문턱 아래 종별로 넘어갈 수 있다."""
        return self.target_contract_kw is not None

    @property
    def no_saving(self) -> bool:
        """**낮출 자리가 없어 줄 것이 없는가.**

        참이면 절감액 자리에 0원 대신 :data:`NO_SAVING` 을 적는다. 하한 비율을
        모르는 경우(``UNKNOWN``)는 여기 들지 않는다 — 그쪽은 「미산출」 이다.
        """
        return self.status is ContractStatus.CONFIRMED and not self.reducible


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


@dataclass(frozen=True)
class _CrossedQuote:
    """문턱 아래 종별에서 **처음부터 다시 계산한** 요금 한 벌 (98세션)."""

    selection: TariffSelection
    label: str
    """넘어간 **종별**의 이름. 안내가 이것으로 「종별이 바뀐다」 를 말한다."""
    selection_text: str
    """``일반용전력(갑)Ⅱ 고압A 선택Ⅱ`` — 산출 근거에 그대로 적는다."""
    contract_kw: float
    bill: BillingResult
    current_bill: BillingResult
    """현행 종별을 **같은 옵션·같은 계약전력**으로 다시 계산한 것.

    ``bill`` 을 그대로 쓰지 않는다 — 부르는 쪽이 계약전력 없이 계산한 기준선을
    넘겨줄 수 있고(:mod:`kwise.report.batch`), 그러면 한쪽만 하한이 걸려
    총액 차이가 종별이 아니라 하한 때문에 갈린다.
    """

    @property
    def saving_won(self) -> float:
        return self.current_bill.total_won - self.bill.total_won


def _crossed_quote(
    usage: UsageData,
    bill: BillingResult,
    table: TariffTable,
    options: BillingOptions,
    *,
    contract_kw: float,
    target: float | None,
    floor_before: float,
    step_kw: float,
) -> _CrossedQuote | None:
    """문턱 아래로 내려간 종별의 가장 싼 조합. 못 넘으면 ``None``.

    **절감액을 빼서 만들지 않는다.** 종별이 바뀌면 기본요금 단가·전력량요금
    단가·선택요금 후보·부칙 경과조치가 함께 바뀌므로 요금을 처음부터 다시
    계산하고 그 종별 안에서 선택요금을 다시 고른다.

    Args:
        target: 같은 종별 안의 목표 계약전력. **하한이 지면 ``None`` 이고**
            그때는 문턱 바로 아래가 유일한 후보다 (99세션).
    """
    contract = table.contract(bill.selection.contract_type)
    below = contract.below_threshold_key
    threshold = contract.threshold_kw
    if below is None or threshold is None or contract.threshold_direction != "above":
        return None

    # 넘어가는 계약전력은 **문턱 바로 아래**다. 같은 종별 목표가 이미 그보다
    # 낮으면 그 값을 그대로 쓴다 — 더 내려도 얻을 것이 없다. 하한이 지면
    # 같은 종별 목표가 없으므로 문턱 바로 아래 하나가 후보다 (99세션).
    below_threshold = threshold - step_kw
    candidate = below_threshold if target is None else min(target, below_threshold)
    # **낮추는 권고만 한다.** 문턱이 지금 계약전력 위면 넘어갈 자리가 아니다.
    if candidate >= contract_kw:
        return None
    # **최대수요 아래로 내리는 권고를 하지 않는다.** 초과사용부가금 대상이 된다.
    if candidate < floor_before:
        return None

    if below not in table.contract_types:
        # 요금 데이터가 가리키는 종별이 없다. **기본값을 두지 않고 멈춘다.**
        raise TariffDataError(f"{contract.key} 의 문턱 아래 종별이 요금표에 없습니다: {below!r}")
    crossed = table.contract(below)
    if bill.selection.voltage not in crossed.voltages:
        # 전압이 새 종별에 없으면 넘어갈 수 없다 (산업용(을) 고압C 가 그렇다).
        return None

    opts = replace(options, contract_kw=candidate)
    quotes = [
        (calculate_bill(usage, table, selection, options=opts), selection)
        for selection in list_selections(
            table, contract_types=[below], voltages=[bill.selection.voltage]
        )
    ]
    if not quotes:
        return None
    best_bill, best_selection = min(quotes, key=lambda pair: pair[0].total_won)
    return _CrossedQuote(
        selection=best_selection,
        label=crossed.label,
        selection_text=selection_label(table, best_selection),
        contract_kw=candidate,
        bill=best_bill,
        current_bill=calculate_bill(
            usage, table, bill.selection, options=replace(options, contract_kw=contract_kw)
        ),
    )


def evaluate_contract_adjustment(
    usage: UsageData,
    bill: BillingResult,
    *,
    contract_kw: float,
    contract_floor_ratio: float | None = None,
    step_kw: float = 1.0,
    table: TariffTable | None = None,
    options: BillingOptions | None = None,
) -> ContractAdjustment:
    """하한 판정과, 하한이 이기는 경우의 목표 계약전력·절감액을 낸다.

    Args:
        contract_floor_ratio: 요금적용전력의 계약전력 대비 하한 비율.
            None 이면 요금표의 종별 속성(제68조 ①의 30%)을 쓴다. 종별
            속성마저 비어 있으면 '미확인' 을 돌려주고 금액을 만들지 않는다.
        step_kw: 계약전력 조정 단위.
        table: 요금 데이터. 주면 목표가 종별 문턱 아래로 갈 수 있는지 보고,
            갈 수 있으면 **넘어간 종별의 단가로 요금을 처음부터 다시 계산해**
            싼 쪽을 권한다 (98세션). 안 주면 지금 종별 안에서만 본다.
        options: ``bill`` 을 계산할 때 쓴 것과 **같은** 요금 옵션.
            ``table`` 과 짝이다 — 다른 옵션으로 다시 계산하면 총액 차이가
            종별이 아니라 옵션 때문에 갈린다.

    Raises:
        ValueError: ``table`` 만 주고 ``options`` 를 안 줬을 때.
    """
    if contract_kw <= 0:
        raise ValueError(f"계약전력은 양수여야 합니다: {contract_kw}")
    if table is not None and options is None:
        raise ValueError("table 을 주면 options 도 함께 줘야 합니다 (같은 옵션으로 다시 계산한다).")

    ratio = contract_floor_ratio if contract_floor_ratio is not None else bill.contract_floor_ratio
    observed = usage.kw.dropna()
    max_demand = float(observed.max()) if len(observed) else 0.0
    billing_demand = float(bill.billing_demand_kw)
    monthly_demand: dict[Any, float] = {
        month: float(value) for month, value in bill.monthly[_demand_column(bill.monthly)].items()
    }
    before_floor = max(monthly_demand.values())
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
    # **판정은 「걸린 달」 이 읽는다** (105세션 3절 · ②-13). 앞서는
    # ``floor_kw > before_floor`` 한 줄이라 **연간 최대**만 봤는데, 요금
    # 엔진은 같은 판에서 달별로 세어 「N개 월에 걸렸습니다」 를 내고 있었다 —
    # 한 판 안에서 두 문장이 어긋났다. **세는 함수를 하나로 두어** 어긋날 수
    # 없게 한다. 청구 결과에도 같은 값이 실려 있다
    # (:attr:`BillingResult.floor_bound_months`) — 여기서 다시 부르는 까닭은
    # 이 함수가 **청구서와 다른 계약전력·비율**로도 불리기 때문이다.
    bound = floor_bound_months(monthly_demand, floor_kw)
    # **현행보다 낮을 때만 목표다** (108세션 2절). 앞서는 ``min(contract_kw, …)``
    # 이라 목표가 현행 위로 올라가면 **현행으로 눌려** 「10,921 → 10,921 kW 로
    # 낮추면 줄어듭니다」 가 절감액 0원과 함께 나갔다 — 고객에게 나가는 거짓
    # 문장이다. 그 자리는 **관측 최대가 계약전력에 닿은 판**이다(보전 갈래가
    # 현행을 붙든다). 하한이 걸린 것은 사실이지만 **낮출 자리는 없다.**
    #
    # **뿌리가 여기 하나다** — 화면 셋·Word 둘·PPT·Excel 둘·계산 근거 시트가
    # 전부 이 값과 :attr:`ContractAdjustment.reducible` 을 읽는다.
    candidate = (
        target_contract_kw(monthly_demand, ratio, step_kw, observed_max_kw=max_demand)
        if bound
        else None
    )
    target = candidate if candidate is not None and candidate < contract_kw else None

    # 하한 적용 전 값으로 되돌린 뒤 두 계약전력에서 각각 다시 씌운다.
    current_base = _base_fee_won(bill, floor_kw)
    adjusted_base = _base_fee_won(bill, target * ratio) if target is not None else current_base
    saving = current_base - adjusted_base
    basis_text = (
        f"요금적용전력 하한 {ratio:.0%} 적용, "
        f"월별 기본요금을 {bill.base_fee_months:.2f}개월분으로 재계산"
        if target is not None
        else f"요금적용전력 하한 {ratio:.0%} 미적용 — 최대수요가 기준"
    )

    # **문턱 아래 종별을 후보로 놓는다** (98세션). 같은 종별 안의 목표는
    # 기본요금만 줄이는데, 문턱을 넘으면 전력량요금 단가까지 함께 바뀐다.
    # **하한 갈래 안에 두지 않는다** (99세션). 하한이 지는 판에서도 문턱 아래
    # 종별로 넘어갈 수 있고, 갈래를 둘로 두면 같은 자료에서 두 값이 나온다.
    crossed: _CrossedQuote | None = None
    if table is not None and options is not None:
        quote = _crossed_quote(
            usage,
            bill,
            table,
            options,
            contract_kw=contract_kw,
            target=target,
            floor_before=before_floor,
            step_kw=step_kw,
        )
        if quote is not None and quote.saving_won > saving:
            crossed = quote
            target = quote.contract_kw
            adjusted_base = quote.bill.total_base_won
            saving = quote.saving_won
            basis_text = (
                f"{quote.selection_text} 로 종별을 바꿔 요금 전체를 다시 계산 "
                "(기본요금·전력량요금 단가가 함께 바뀐다)"
            )

    if target is None:
        # **낮출 자리가 아예 없다. 까닭이 둘이다** (108세션 2절) — 하한이 어느
        # 달에도 안 걸리거나, 걸렸어도 관측 최대가 계약전력에 닿아 보전이
        # 현행을 붙든다. **사실 ID 는 하나로 둔다** — 판정 줄을 뜨는 쪽
        # (화면·PPT)이 그 ID 로 찾는다.
        notices.append(
            basis(
                FLOOR_NOT_BINDING_NOTICE if not bound else CONTRACT_AT_OBSERVED_MAX_NOTICE,
                fact="contract.floor_not_binding",
            )
        )
    else:
        # **낮출 자리가 있을 때만 낸다.** 낮출 이유가 없는 갈래에서 「하향은
        # 되돌리기 어렵다」 를 읽히면 하지도 못할 일을 조심하라는 말이 된다.
        notices += [
            warn(MARGIN_NOTICE, fact="contract.margin"),
            warn(_PENALTY_NOTICE, fact="contract.penalty"),
            basis(basis_text, fact="contract.saving_basis"),
        ]
        if crossed is None:
            # **넘는 판에서는 내지 않는다** (98세션). 종별이 바뀌면 전력량요금
            # 단가도 바뀌므로 이 문장이 사실과 어긋난다.
            notices.append(
                basis(
                    "전력량요금은 계약전력과 무관하므로 변하지 않습니다.",
                    fact="contract.energy_unchanged",
                )
            )
        # **목표가 종별 경계를 넘는가** (96세션). 방향은 요금 데이터가 정한다 —
        # 갑Ⅰ·갑Ⅱ 는 300 kW 미만, 을은 300 kW 이상, 교육용은 1,000 kW 다.
        # **말할 것은 「계약전력이 줄었다」 가 아니라 「종별이 바뀐다」 다** (98세션).
        threshold = bill.threshold_kw
        if crossed is not None:
            notices.append(
                warn(
                    f"목표 {target:,.0f} kW 는 지금 계약종별의 범위 밖입니다. "
                    f"그렇게 낮추면 계약종별이 {crossed.label} 로 바뀝니다 — "
                    "절감액은 바뀐 종별의 요금으로 다시 계산한 값입니다.",
                    fact=TYPE_THRESHOLD_FACT,
                )
            )
        elif threshold is not None and not within_type_threshold(
            target, threshold, bill.threshold_direction
        ):
            # 넘는데 갈아 끼울 종별을 요금 데이터가 안 들고 있는 자리다.
            notices.append(
                warn(
                    f"{bill.contract_label} 은 계약전력 "
                    f"{threshold_text(threshold, bill.threshold_direction)} "
                    f"종별인데 목표 계약전력이 {target:,.0f} kW 입니다. "
                    "종별이 바뀌는 자리이므로 절감액은 지금 종별 단가로 낸 값입니다.",
                    fact=TYPE_THRESHOLD_FACT,
                )
            )
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
        floor_bound_months=bound,
        crossed_selection=crossed.selection if crossed is not None else None,
        crossed_label=crossed.label if crossed is not None else None,
        crossed_total_won=crossed.bill.total_won if crossed is not None else None,
        current_total_won=crossed.current_bill.total_won if crossed is not None else None,
        notices=tuple(notices),
    )


def contract_demand_series(
    bill: BillingResult, contract_kw: float, floor_ratio: float
) -> pd.Series:
    """월별 요금적용전력에 하한을 씌운 결과. 명세에 붙일 수 있다."""
    return bill.monthly["billing_demand_kw"].clip(lower=contract_kw * floor_ratio)
