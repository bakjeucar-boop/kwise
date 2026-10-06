"""경제성DR 참여 (요구사항서 7.3) — 투자 0원.

근거는 ``data\\source\\2024-02_전력시장운영규칙.pdf`` 제12장이다. 하루 전 자발적 입찰이라
설비 투자가 필요 없고, 편익은 **정산금 하나**다.

**기본요금 절감은 계산하지 않는다.** SMP 기준으로 산발적으로 입찰하므로 참여일이
기간 최대수요일과 겹칠 확률이 낮다. 겹친다는 보장 없이 기본요금 절감을 얹으면
없는 절감을 만들어내는 것이 된다.

**정산 단가에 기본값을 두지 않는다.** 전력거래소는 계획감축량을 지역별 SMP로 정산하고
(별표26 Ⅰ.1) 수요관리사업자 수수료가 별도라 우리가 만들 수 있는 값이 아니다.
단가가 없으면 감축량(kWh)만 내고 금액은 "단가 미입력"으로 표시한다.

**연간 참여 일수 제한은 없다 (14세션에 바로잡았다).** 13세션의 「연 60시간 한도」는
경제성DR 의 제약이 아니었다. 남는 제약은 하루 2회·총 8시간, 평일 09~20시(점심
제외), 그리고 **미이행 시 6개월 입찰 제한**이다.

감축 가능량은 :mod:`kwise.diagnose.dr` 이 데이터에서 찾은 **저부하 평일**에서 온다.
이 모듈은 그 값을 받아 정산금과 위약금 리스크로 옮길 뿐, 감축량을 다시 만들지
않는다 — 두 곳에서 만들면 어긋난다.

**투자비는 0원이지만 리스크는 0이 아니다.** 감축계획량을 채우지 못하면
실적위약금이 붙는다 (별표26 5.가). 육지와 제주의 가격이 갈린다 (S249 · 나-18).

    육지  실적위약금 = (감축계획량 − 실제감축량) × 계통한계가격 × 위약금계수
    제주  실적위약금 = (감축계획량 − 실제감축량) × Max(하루전에너지가격, 0) × 위약금계수

건물이 제주인지 모르면(일괄 생성 등) 육지 식이다.

확실성 등급은 **'중간'** 이다. 입찰 낙찰 여부와 참여일 수가 운영에 달렸다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from kwise.diagnose.dr import (
    BID_WINDOW,
    DrProfile,
    DrResourceType,
    dr_bid_restriction_months,
    dr_event_hours,
    dr_market_windows,
)
from kwise.measures.base import Certainty
from kwise.money import NO_SAVING
from kwise.notices import Notice, basis, block, info, warn
from kwise.rules import rule_value

__all__ = [
    "DR_ADVISORY",
    "UNPRICED_REASON",
    "DemandResponseResult",
    "evaluate_demand_response",
    "shortfall_penalty_won",
]


#: **표 한 칸에 들어갈 길이로 적는다** (28세션 1-4). 왜 만들지 않는지는 이
#: 함수가 내는 차단 안내(``dr.no_price``)와 보고서 3장 주의가 이미 말한다 —
#: 사유를 표 안에서 되풀이하면 표가 문단이 된다.
UNPRICED_REASON = "미산출 — 정산 단가 미입력"

DR_ADVISORY = (
    "경제성DR은 수요관리사업자를 통해서만 참여할 수 있습니다. "
    "정산 단가, 계약 조건, 위약금 조항은 사업자와 상담하여 확인하십시오."
)


def penalty_factor() -> float:
    """실적위약금 계수 (별표26 5.가 · PPCF)."""
    return float(rule_value("dr.penalty_factor"))


def _penalty_price_name(jeju: bool) -> str:
    """위약금 식의 가격 이름. 규칙 원문 글자다 (별표26 5.가)."""
    return "하루전에너지가격" if jeju else "계통한계가격"


def shortfall_penalty_won(
    planned_kw: float,
    actual_kw: float,
    hours: float,
    price_won_per_kwh: float,
    *,
    jeju: bool = False,
) -> float:
    """실적위약금 (전력시장운영규칙 별표26 5.가).

        육지  (감축계획량 − 실제감축량) × 계통한계가격 × 위약금계수
        제주  (감축계획량 − 실제감축량) × Max(하루전에너지가격, 0) × 위약금계수

    계획을 채웠거나 넘겼으면 0 이다. **음수 가격을 0 으로 보는 것은 제주 식뿐이다** —
    육지 식에는 원문에 ``Max`` 가 없다.
    """
    shortfall_kwh = max(0.0, planned_kw - actual_kw) * hours
    price = max(0.0, price_won_per_kwh) if jeju else price_won_per_kwh
    return shortfall_kwh * price * penalty_factor()


@dataclass(frozen=True, eq=False)
class DemandResponseResult:
    """경제성DR 참여 평가 (14세션에 산출 근거를 갈아치웠다).

    Attributes:
        registered_capacity_kw: **등록 권장값.** 저부하일 여력 분포의 하위값이라
            어느 참여일에나 지킬 수 있다.
        low_load_days: 저부하 평일 수. **이것이 실질 제약이다** — 연간 참여 일수
            제한은 없고, 감축할 여력이 있는 날이 몇 날이냐가 전부다.
        participation_hours: 저부하일에 참여할 수 있는 시간의 합 (하루 8시간 상한).
        annual_reducible_kwh: Σ(저부하일별 감축 여력 × 그날 참여 가능 시간)을
            365일로 환산한 값.
        settlement_won: 정산금. 단가가 없으면 None — 금액을 지어내지 않는다.
        penalty_price_won_per_kwh: 위약금 가격 — 육지는 계통한계가격 · 제주는
            하루전에너지가격 (별표26 5.가).
        penalty_per_shortfall_kw_won: 감축 미달 1 kW 당 위약금 (별표26). 리스크 크기다.
        bid_restriction_months: 미이행 제재 기간. 보수적 산정의 이유다.
    """

    registered_capacity_kw: float
    mean_reducible_kw: float
    eligible_days: int
    low_load_days: int
    weekend_baseline_kw: float | None
    low_load_threshold_kw: float | None
    normal_weekday_mean_kw: float | None
    participation_hours: float
    daily_hours_cap: float
    period_reducible_kwh: float
    annual_reducible_kwh: float
    low_load_day_table: pd.DataFrame

    unit_price_won_per_kwh: float | None
    settlement_won: float | None

    penalty_price_won_per_kwh: float | None
    penalty_per_shortfall_kw_won: float | None

    resource_types: tuple[DrResourceType, ...]
    bid_restriction_months: float
    participation_notice: str
    investment_won: float = 0.0
    certainty: Certainty = Certainty.MEDIUM
    notices: tuple[Notice, ...] = field(default=())
    unassessed_reason: str = ""
    """감축 가능량을 **산출하지 못한 까닭** (S272 결정 1 · :attr:`DrProfile.unassessed_reason`).

    있으면 감축 가능량 · 등록 권장 용량 · 참여 시간 · 정산금 칸은 0 이 아니라 「미산출」 이고
    이 글이 그 사유다. 수는 0 그대로 둔다 — 금액을 만드는 식은 건드리지 않는다."""

    @property
    def has_low_load_days(self) -> bool:
        """감축할 여력이 있는 날이 있는가. 없으면 감축 가능량이 0 이다."""
        return self.low_load_days > 0

    @property
    def no_reduction(self) -> bool:
        """**세어 보니 감축 가능량이 0 이다** (S276 결정 5) — 정산금 칸은 단가를 넣었든 안
        넣었든 「없음」 이다 (S205 · S237 ㄴ). 세지 않은 벌(주말에도 가동)은 「미산출」 이다."""
        return not self.unassessed_reason and self.annual_reducible_kwh <= 0

    @property
    def is_priced(self) -> bool:
        return self.settlement_won is not None

    @property
    def period_settlement_won(self) -> float | None:
        """기간 정산금 — 관측 기간 감축 가능량 × 단가 (S246 결정 1 의 식 · S256 고4 · 고7)."""
        if self.unit_price_won_per_kwh is None or self.unassessed_reason:
            return None
        return self.period_reducible_kwh * self.unit_price_won_per_kwh

    @property
    def settlement_label(self) -> str:
        """금액 또는 사유. **빈칸으로 두지 않는다.**"""
        if self.unassessed_reason:
            # 단가를 넣어도 금액이 안 선다 — 사유는 실제로 막은 것을 말한다 (S238 결정 3).
            return f"미산출 — {self.unassessed_reason}"
        if self.no_reduction:
            return NO_SAVING
        if self.settlement_won is None:
            return UNPRICED_REASON
        return f"{self.settlement_won:,.0f}"


def evaluate_demand_response(
    profile: DrProfile,
    *,
    unit_price_won_per_kwh: float | None = None,
    penalty_price_won_per_kwh: float | None = None,
    reduction_kw: float | None = None,
    jeju: bool = False,
) -> DemandResponseResult:
    """경제성DR 참여 편익과 위약금 리스크를 낸다.

    Args:
        profile: 6.6 진단 결과. **저부하 평일과 보수적 등록 용량을 여기서 받는다.**
        unit_price_won_per_kwh: 정산 단가. **기본값이 없다** — 없으면 금액을 내지 않는다.
        penalty_price_won_per_kwh: 위약금 가격 — 육지는 계통한계가격, 제주는
            하루전에너지가격. 위약금 리스크 산정용이며 없으면 리스크 금액을 내지 않는다.
        jeju: 건물이 제주인가 (화면 옆단 지역). **모르면 거짓 — 육지 식이다** (S249 · 나-18).
        reduction_kw: 감축계획량. 기본은 진단의 보수적 등록 가능 용량이다.
            **넣으면 감축 가능량이 그 비율로 다시 잡힌다** — 등록값을 바꾸면
            날마다 낼 수 있는 양도 바뀐다.
    """
    capacity = profile.registered_capacity_kw if reduction_kw is None else reduction_kw
    if capacity < 0:
        raise ValueError(f"감축계획량은 음수일 수 없습니다: {capacity}")

    # 감축 가능량은 **진단이 만든 값 하나**다. 등록값을 손으로 바꾸면 그 비율만큼
    # 함께 움직인다 — 여기서 다시 만들지 않는다.
    scale = (
        1.0
        if reduction_kw is None or profile.registered_capacity_kw <= 0
        else capacity / profile.registered_capacity_kw
    )
    annual_kwh = profile.annual_reducible_kwh * scale
    period_kwh = profile.period_reducible_kwh * scale

    # **주말에도 가동해 저부하 평일을 세지 않은 벌은 0 이 아니라 「미산출」 이다** (S272 결정 1).
    # 단가를 넣어도 정산금을 세우지 않는다 — 0 kWh × 단가 = 0원은 잰 값이 아니다.
    unassessed = profile.unassessed_reason
    settlement = (
        None
        if unit_price_won_per_kwh is None or unassessed
        else annual_kwh * unit_price_won_per_kwh
    )
    penalty_per_kw = (
        None
        if penalty_price_won_per_kwh is None
        else shortfall_penalty_won(
            1.0, 0.0, dr_event_hours()[1], penalty_price_won_per_kwh, jeju=jeju
        )
    )
    months = dr_bid_restriction_months()
    price_name = _penalty_price_name(jeju)
    price_term = f"Max({price_name}, 0)" if jeju else price_name

    # 감축 가능량이 0 이면 참여를 전제로 한 주의(위약금 · 정산 단가 · 참고 문턱)를 세우지
    # 않는다 — 그 벌에서 참인 말만 (S261 고침 1 · S207). 「저부하 평일이 없습니다」 는
    # 세어 본 0일의 벌에만 선다 (아래 · S274 결정 3).
    reducible = annual_kwh > 0
    notices: list[Notice] = [
        # **주의** — 위약·리스크. 결과를 그대로 받아들이면 안 되는 것들이다.
        *(
            (
                warn(
                    "**투자비는 0원이지만 리스크는 0이 아닙니다.** 감축계획량을 채우지 못하면 "
                    f"실적위약금 = (감축계획량 − 실제감축량) × {price_term} × "
                    f"위약금계수({penalty_factor():g}) 이 부과됩니다 (전력시장운영규칙 별표26).",
                    fact="dr.penalty_risk",
                ),
            )
            if reducible
            else ()
        ),
        # **근거** — 숫자가 어디서 나왔는가. 산식·모수·판정 창이다.
        basis(
            f"감축량은 거래 가능일 {profile.eligible_days}일 가운데 **저부하 평일 "
            f"{profile.low_load_days_count}일**만 세었습니다. 토·일·공휴일은 입찰할 수 "
            "없습니다 (제12.4.2.1조 제1항 1호).",
            fact="dr.low_days_counted",
        ),
        # 아래 둘은 **1단계 진단이 내는 것과 같은 사실**이다 (diagnose\dr.py).
        # 이 함수 끝에서 진단 안내를 이어 붙이므로 ID 가 같아야 한 번만 나온다.
        basis(
            f"**등록 권장 용량 {capacity:,.0f} kW** 는 저부하일 감축 여력 분포의 하위값"
            f"입니다. 사업자와 계약할 때 등록하는 값이며, 평균 기준 여력 "
            f"{profile.mean_reducible_kw:,.0f} kW 로 등록하면 절반의 날에 미달합니다.",
            fact="dr.registered_capacity",
        ),
        basis(
            # **「연간」 이 아니라 「12개월 환산」 이다** (S214). ``annual_kwh`` 는
            # 관측 기간 값을 365일로 늘려 잡은 값이라 곁의 ``period_reducible_kwh``
            # 와 이름이 갈려야 한다.
            f"**12개월 환산 감축 가능량 {annual_kwh:,.0f} kWh** = Σ(저부하일별 감축 여력 × 그날 "
            f"참여 가능 시간). 참여 가능 시간의 합은 "
            f"{profile.total_participation_hours:,.0f}시간이고 하루 상한은 "
            f"{profile.daily_hours_cap:,.0f}시간입니다.",
            fact="dr.annual_reducible",
        ),
        # **시간대와 한도는 카드 본문이 이미 낸다** (25세션 3-3 · D). 여기서는
        # 본문에 없는 사실 하나만 적는다 — 왜 창이 둘로 갈라져 있는가.
        # **창이 하나인 제주 입찰 창에서는 거짓이라 안 세운다** (S253 결정 2).
        *(
            (basis(f"점심시간(12–13시)은 {BID_WINDOW}에서 빠집니다.", fact="dr.window_rule"),)
            if len(dr_market_windows(jeju=jeju)) > 1
            else ()
        ),
        basis(
            "**기본요금 절감은 계산하지 않았습니다.** SMP 기준으로 산발적으로 입찰하므로 "
            # **「연중」 이 아니라 「기간」 이다** (S213) — 대표일 이름과 같은 낱말로
            # 모은다. 같은 것을 「연간」·「연중」 두 이름으로 부르고 있었다.
            "참여일이 기간 최대수요일과 겹칠 확률이 낮습니다. 편익은 정산금 하나로 봅니다.",
            fact="dr.no_base_fee_saving",
        ),
        # **참고** — 제도 설명. 화면에 없고 보고서 부록으로 간다.
        info(DR_ADVISORY, fact="dr.advisory"),
        # **참여 조건은 근거다** (22세션 1절). 하루 몇 회·어느 시간대·미이행 제재는
        # 감축 가능량이 왜 그 값인지 설명하는 제도 조건이지 경고가 아니다. 카드가
        # 이것을 주의로 직접 그리면서 확인사항 넷 가운데 하나를 차지하고 있었다.
        basis(profile.notice, fact="dr.participation"),
    ]
    # **차단은 정산 단가 하나다** (S256 결정 가 · 고1). 위약금 가격은 화면에 입력 칸이
    # 없어 「입력하지 않아」 가 입력을 전제한 문장이었다 — 빠진 입력에서 걷는다. 단가를
    # 넣은 판에는 가격이 무엇에 달렸는지만 주의(⚠)로 남긴다.
    basis_text = (
        f"정산 단가는 전력거래소가 지역별 SMP로 정산하는 몫과 사업자 수수료에, 위약금은 "
        f"{price_name}에 달려 있습니다 (전력시장운영규칙 별표26)."
    )
    if unassessed:
        # 0 을 값으로 적는 근거 둘과 「감축 가능량(kWh)만 참고」 는 이 벌에서 참이 아니다 —
        # 안 세운다 (S207 · S261 꼴). 까닭은 진단이 낸 그 한 줄이 말한다.
        notices = [
            item
            for item in notices
            if item.fact not in {"dr.registered_capacity", "dr.annual_reducible"}
        ]
    elif unit_price_won_per_kwh is None and reducible:
        # 감축이 0 이면 단가를 넣어도 금액이 「없음」 이라 세우지 않는다 (S276 결정 5).
        notices.append(
            block(
                "정산 단가를 입력하지 않아 금액을 산출하지 않았습니다. "
                f"감축 가능량(kWh)만 참고하십시오 — {basis_text}",
                fact="dr.no_price",
            )
        )
    elif penalty_price_won_per_kwh is None and reducible:
        notices.append(warn(basis_text, fact="dr.no_price"))
    if not profile.meets_reference_capacity and reducible:
        notices.append(
            warn(
                f"등록 권장 용량이 참고 문턱 100 kW 아래입니다 ({capacity:,.0f} kW). "
                "자원 단위 기준이라 다른 고객과 묶여 참여할 수 있으므로 사업자와 "
                "상담하십시오 (제12.4.2.1조 제1항 2호).",
                fact="dr.below_reference",
            )
        )
    if not profile.low_load_days_count and not unassessed:
        # **진단이 내는 것과 같은 사실이다.** 줄표 유무로 지문이 갈려 두 번
        # 나왔다 (20세션 2절 결함 ②). **세지 않은 벌(주말에도 가동)에는 안 세운다**
        # (S274 결정 3) — 「없습니다」 는 세어 본 0일의 말이다.
        notices.append(
            warn(
                "저부하 평일이 없습니다. 감축이 실제 운영 축소를 뜻하므로 "
                "생산·재실 영향과 함께 검토하십시오.",
                fact="dr.no_low_days",
            )
        )
    notices.extend(profile.notices)

    return DemandResponseResult(
        registered_capacity_kw=capacity,
        mean_reducible_kw=profile.mean_reducible_kw,
        eligible_days=profile.eligible_days,
        low_load_days=profile.low_load_days_count,
        weekend_baseline_kw=profile.weekend_baseline_kw,
        low_load_threshold_kw=profile.low_load_threshold_kw,
        normal_weekday_mean_kw=profile.normal_weekday_mean_kw,
        participation_hours=profile.total_participation_hours,
        daily_hours_cap=profile.daily_hours_cap,
        period_reducible_kwh=period_kwh,
        annual_reducible_kwh=annual_kwh,
        low_load_day_table=profile.low_load_day_table(),
        unit_price_won_per_kwh=unit_price_won_per_kwh,
        settlement_won=settlement,
        penalty_price_won_per_kwh=penalty_price_won_per_kwh,
        penalty_per_shortfall_kw_won=penalty_per_kw,
        resource_types=profile.resource_types,
        bid_restriction_months=months,
        participation_notice=profile.notice,
        notices=tuple(notices),
        unassessed_reason=unassessed,
    )
