"""경제성DR 참여 (요구사항서 7.3) — 투자 0원.

근거는 ``data\\source\\전력시장운영규칙.pdf`` 제12장이다. 하루 전 자발적 입찰이라
설비 투자가 필요 없고, 편익은 **정산금 하나**다.

**기본요금 절감은 계산하지 않는다.** SMP 기준으로 산발적으로 입찰하므로 참여일이
연중 최대수요일과 겹칠 확률이 낮다. 겹친다는 보장 없이 기본요금 절감을 얹으면
없는 절감을 만들어내는 것이 된다.

**정산 단가에 기본값을 두지 않는다.** 전력거래소가 매월 순편익가격(입찰 최소가격)을
공지하고 수요관리사업자 수수료가 별도라 우리가 만들 수 있는 값이 아니다.
단가가 없으면 감축량(kWh)만 내고 금액은 "단가 미입력"으로 표시한다.

**등록 권장 용량과 연간 감축 가능량은 쓰임이 다르다.**

    registered_capacity_kw   하위 10% 기준 — 사업자와 계약할 때 등록하는 값
    annual_reducible_kwh     **거래일별 여력의 합** — 연간 수익 추정 기준

경제성DR 은 하루 전 입찰이라 매일 다른 양을 입찰한다. 등록값 × 일수로 계산하면
부하가 많은 날의 여력을 통째로 버려 연간 수익이 크게 과소평가된다.

**투자비는 0원이지만 리스크는 0이 아니다.** 감축계획량을 채우지 못하면
실적위약금이 붙는다 (별표26).

    실적위약금 = (감축계획량 − 실제감축량) × Max(하루전에너지가격, 0)

확실성 등급은 **'중간'** 이다. 입찰 낙찰 여부와 참여일 수가 운영에 달렸다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kwise.diagnose.dr import DrProfile, DrResourceType
from kwise.measures.base import Certainty

__all__ = [
    "DEFAULT_BID_HOURS_PER_DAY",
    "DR_ADVISORY",
    "UNPRICED_REASON",
    "DemandResponseResult",
    "evaluate_demand_response",
    "shortfall_penalty_won",
]

# 입찰 지속시간. 규칙이 고정하지 않으므로 사용자가 바꿀 수 있게 둔다.
DEFAULT_BID_HOURS_PER_DAY = 1.0

UNPRICED_REASON = (
    "미산출 — 정산 단가 미입력. 전력거래소가 매월 공지하는 순편익가격(입찰 "
    "최소가격)과 수요관리사업자 수수료에 따라 달라지므로 본 도구가 만들지 않습니다."
)

DR_ADVISORY = (
    "경제성DR은 수요관리사업자를 통해서만 참여할 수 있습니다. "
    "정산 단가, 계약 조건, 위약금 조항은 사업자와 상담하여 확인하십시오."
)


def shortfall_penalty_won(
    planned_kw: float,
    actual_kw: float,
    hours: float,
    day_ahead_price_won_per_kwh: float,
) -> float:
    """실적위약금 (전력시장운영규칙 별표26).

        (감축계획량 − 실제감축량) × Max(하루전에너지가격, 0)

    계획을 채웠거나 넘겼으면 0 이다. 가격이 음수면 0 으로 본다.
    """
    shortfall_kwh = max(0.0, planned_kw - actual_kw) * hours
    return shortfall_kwh * max(0.0, day_ahead_price_won_per_kwh)


@dataclass(frozen=True, eq=False)
class DemandResponseResult:
    """경제성DR 참여 평가.

    Attributes:
        registered_capacity_kw: **등록 권장값.** 사업자와 계약할 때 등록하는 용량이며
            하위 10% 기준이라 어느 거래일에나 지킬 수 있다.
        annual_reducible_kwh: **거래일별 여력의 합.** 연간 수익 추정은 이 값으로 한다.
            등록값 × 일수가 아니다 — 하루 전 입찰이라 매일 다른 양을 입찰한다.
        flat_reduction_kwh: 등록값 × 일수. **비교용으로만 둔다.** 이 값을 수익
            추정에 쓰면 부하가 많은 날의 여력을 버려 크게 과소평가된다.
        low_cost_reduction_kwh: 무비용 감축 가능일에만 참여했을 때의 감축량.
        settlement_won: 정산금. 단가가 없으면 None — 금액을 지어내지 않는다.
        penalty_per_shortfall_kw_won: 감축 미달 1 kW 당 위약금 (별표26). 리스크 크기다.
    """

    registered_capacity_kw: float
    mean_reducible_kw: float
    eligible_days: int
    low_load_days: int
    bid_hours_per_day: float
    annual_reducible_kwh: float
    flat_reduction_kwh: float
    low_cost_reduction_kwh: float

    unit_price_won_per_kwh: float | None
    settlement_won: float | None
    low_cost_settlement_won: float | None

    day_ahead_price_won_per_kwh: float | None
    penalty_per_shortfall_kw_won: float | None

    resource_types: tuple[DrResourceType, ...]
    investment_won: float = 0.0
    certainty: Certainty = Certainty.MEDIUM
    warnings: tuple[str, ...] = field(default=())
    notes: tuple[str, ...] = field(default=())

    @property
    def is_priced(self) -> bool:
        return self.settlement_won is not None

    @property
    def settlement_label(self) -> str:
        """금액 또는 사유. **빈칸으로 두지 않는다.**"""
        if self.settlement_won is None:
            return UNPRICED_REASON
        return f"{self.settlement_won:,.0f}"


def evaluate_demand_response(
    profile: DrProfile,
    *,
    unit_price_won_per_kwh: float | None = None,
    day_ahead_price_won_per_kwh: float | None = None,
    reduction_kw: float | None = None,
    bid_hours_per_day: float = DEFAULT_BID_HOURS_PER_DAY,
) -> DemandResponseResult:
    """경제성DR 참여 편익과 위약금 리스크를 낸다.

    Args:
        profile: 6.6 진단 결과. **거래 가능일 수와 보수적 등록 용량을 여기서 받는다.**
        unit_price_won_per_kwh: 정산 단가. **기본값이 없다** — 없으면 금액을 내지 않는다.
        day_ahead_price_won_per_kwh: 하루전에너지가격. 위약금 리스크 산정용이며
            없으면 리스크 금액을 내지 않는다.
        reduction_kw: 감축계획량. 기본은 진단의 보수적 등록 가능 용량이다.
        bid_hours_per_day: 입찰 지속시간. 규칙이 고정하지 않는다.
    """
    if bid_hours_per_day <= 0:
        raise ValueError(f"입찰 지속시간은 양수여야 합니다: {bid_hours_per_day}")
    capacity = profile.registered_capacity_kw if reduction_kw is None else reduction_kw
    if capacity < 0:
        raise ValueError(f"감축계획량은 음수일 수 없습니다: {capacity}")

    # 감축량은 **거래 가능일 기준으로만** 낸다 (제12.4.2.1조 제1항 1호).
    # **연간 감축 가능량은 거래일별 여력의 합이다.** 등록값 × 일수가 아니다.
    annual_kwh = profile.annual_reducible_kwh(bid_hours_per_day)
    flat_kwh = capacity * bid_hours_per_day * profile.eligible_days
    low_cost_kwh = profile.low_cost_reducible_kwh(bid_hours_per_day)

    settlement = None if unit_price_won_per_kwh is None else annual_kwh * unit_price_won_per_kwh
    low_cost_settlement = (
        None if unit_price_won_per_kwh is None else low_cost_kwh * unit_price_won_per_kwh
    )
    penalty_per_kw = (
        None
        if day_ahead_price_won_per_kwh is None
        else shortfall_penalty_won(1.0, 0.0, bid_hours_per_day, day_ahead_price_won_per_kwh)
    )

    notes = [
        DR_ADVISORY,
        f"감축량은 거래 가능일 {profile.eligible_days}일 기준입니다. 토·일·공휴일은 "
        "입찰할 수 없습니다 (제12.4.2.1조 제1항 1호).",
        f"**등록 권장 용량 {capacity:,.0f} kW** 는 대상일 주간 부하 하위 10% − "
        "기저부하입니다. 사업자와 계약할 때 등록하는 값이며, 평균 기준 여력 "
        f"{profile.mean_reducible_kw:,.0f} kW 로 등록하면 절반의 날에 미달합니다.",
        f"**연간 감축 가능량 {annual_kwh:,.0f} kWh 는 거래일별 여력을 합산한 값입니다.** "
        f"등록값 × 일수로 계산하면 {flat_kwh:,.0f} kWh 로 "
        f"{(1 - flat_kwh / annual_kwh) if annual_kwh else 0:.0%} 과소평가됩니다 — "
        "하루 전 입찰이라 매일 다른 양을 입찰하므로 부하가 많은 날의 여력을 "
        "버릴 이유가 없습니다.",
        "**기본요금 절감은 계산하지 않았습니다.** SMP 기준으로 산발적으로 입찰하므로 "
        "참여일이 연중 최대수요일과 겹칠 확률이 낮습니다. 편익은 정산금 하나로 봅니다.",
        f"입찰 지속시간을 {bid_hours_per_day:.1f}시간으로 두었습니다. 규칙이 고정하는 "
        "값이 아니므로 사업자와 협의한 값으로 바꾸십시오.",
    ]
    warnings: list[str] = [
        "**투자비는 0원이지만 리스크는 0이 아닙니다.** 감축계획량을 채우지 못하면 "
        "실적위약금 = (감축계획량 − 실제감축량) × Max(하루전에너지가격, 0) 이 "
        "부과됩니다 (별표26).",
    ]
    if unit_price_won_per_kwh is None:
        warnings.append(
            "정산 단가를 입력하지 않아 금액을 산출하지 않았습니다. 감축 가능량(kWh)만 "
            "참고하십시오. 단가는 전력거래소 월별 순편익가격과 사업자 수수료에 "
            "달려 있습니다."
        )
    if day_ahead_price_won_per_kwh is None:
        warnings.append(
            "하루전에너지가격을 입력하지 않아 위약금 리스크 금액을 산출하지 않았습니다 (별표26)."
        )
    if not profile.meets_reference_capacity:
        warnings.append(
            f"등록 가능 용량이 참고 문턱 100 kW 아래입니다 ({capacity:,.0f} kW). "
            "자원 단위 기준이라 다른 고객과 묶여 참여할 수 있으므로 사업자와 "
            "상담하십시오 (제12.4.2.1조 제1항 2호)."
        )
    if len(profile.low_load_days) == 0:
        warnings.append(
            "무비용 감축 가능일이 없습니다. 감축이 실제 운영 축소를 뜻하므로 "
            "생산·재실 영향과 함께 검토하십시오."
        )

    return DemandResponseResult(
        registered_capacity_kw=capacity,
        mean_reducible_kw=profile.mean_reducible_kw,
        eligible_days=profile.eligible_days,
        low_load_days=len(profile.low_load_days),
        bid_hours_per_day=bid_hours_per_day,
        annual_reducible_kwh=annual_kwh,
        flat_reduction_kwh=flat_kwh,
        low_cost_reduction_kwh=low_cost_kwh,
        unit_price_won_per_kwh=unit_price_won_per_kwh,
        settlement_won=settlement,
        low_cost_settlement_won=low_cost_settlement,
        day_ahead_price_won_per_kwh=day_ahead_price_won_per_kwh,
        penalty_per_shortfall_kw_won=penalty_per_kw,
        resource_types=profile.resource_types,
        warnings=tuple(warnings),
        notes=tuple(notes),
    )
