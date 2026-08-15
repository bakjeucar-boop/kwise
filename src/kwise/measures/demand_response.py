"""경제성DR 참여 (요구사항서 7.3) — 투자 0원.

근거는 ``data\\source\\전력시장운영규칙.pdf`` 제12장이다. 하루 전 자발적 입찰이라
설비 투자가 필요 없고, 편익은 **정산금 하나**다.

**기본요금 절감은 계산하지 않는다.** SMP 기준으로 산발적으로 입찰하므로 참여일이
연중 최대수요일과 겹칠 확률이 낮다. 겹친다는 보장 없이 기본요금 절감을 얹으면
없는 절감을 만들어내는 것이 된다.

**정산 단가에 기본값을 두지 않는다.** 전력거래소가 매월 순편익가격(입찰 최소가격)을
공지하고 수요관리사업자 수수료가 별도라 우리가 만들 수 있는 값이 아니다.
단가가 없으면 감축량(kWh)만 내고 금액은 "단가 미입력"으로 표시한다.

**연간 참여 일수 제한은 없다 (14세션에 바로잡았다).** 13세션의 「연 60시간 한도」는
경제성DR 의 제약이 아니었다. 남는 제약은 하루 2회·총 8시간, 평일 09~20시(점심
제외), 그리고 **미이행 시 6개월 입찰 제한**이다.

감축 가능량은 :mod:`kwise.diagnose.dr` 이 데이터에서 찾은 **저부하 평일**에서 온다.
이 모듈은 그 값을 받아 정산금과 위약금 리스크로 옮길 뿐, 감축량을 다시 만들지
않는다 — 두 곳에서 만들면 어긋난다.

**투자비는 0원이지만 리스크는 0이 아니다.** 감축계획량을 채우지 못하면
실적위약금이 붙는다 (별표26).

    실적위약금 = (감축계획량 − 실제감축량) × Max(하루전에너지가격, 0)

확실성 등급은 **'중간'** 이다. 입찰 낙찰 여부와 참여일 수가 운영에 달렸다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from kwise.diagnose.dr import (
    DrProfile,
    DrResourceType,
    dr_bid_restriction_months,
    dr_event_hours,
)
from kwise.measures.base import Certainty
from kwise.notices import Notice, basis, block, info, warn

__all__ = [
    "DR_ADVISORY",
    "UNPRICED_REASON",
    "DemandResponseResult",
    "evaluate_demand_response",
    "shortfall_penalty_won",
]


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

    day_ahead_price_won_per_kwh: float | None
    penalty_per_shortfall_kw_won: float | None

    resource_types: tuple[DrResourceType, ...]
    bid_restriction_months: float
    participation_notice: str
    investment_won: float = 0.0
    certainty: Certainty = Certainty.MEDIUM
    notices: tuple[Notice, ...] = field(default=())

    @property
    def has_low_load_days(self) -> bool:
        """감축할 여력이 있는 날이 있는가. 없으면 감축 가능량이 0 이다."""
        return self.low_load_days > 0

    @property
    def is_priced(self) -> bool:
        return self.settlement_won is not None

    @property
    def settlement_label(self) -> str:
        """금액 또는 사유. **빈칸으로 두지 않는다.**"""
        if self.settlement_won is None:
            return UNPRICED_REASON
        return f"{self.settlement_won:,.0f}"


def _object_particle(word: str) -> str:
    """목적격 조사 ``을``/``를``. **받침이 있으면 을이다.**

    문구를 조립하면 조사가 어긋난다 — 「위약금 리스크을」 이 그랬다. 한글 음절은
    ``(코드 - 0xAC00) % 28`` 이 0 이 아니면 종성이 있다.
    """
    last = word.strip()[-1:] if word.strip() else ""
    if not last or not ("가" <= last <= "힣"):
        return "를"
    return "을" if (ord(last) - 0xAC00) % 28 else "를"


def evaluate_demand_response(
    profile: DrProfile,
    *,
    unit_price_won_per_kwh: float | None = None,
    day_ahead_price_won_per_kwh: float | None = None,
    reduction_kw: float | None = None,
) -> DemandResponseResult:
    """경제성DR 참여 편익과 위약금 리스크를 낸다.

    Args:
        profile: 6.6 진단 결과. **저부하 평일과 보수적 등록 용량을 여기서 받는다.**
        unit_price_won_per_kwh: 정산 단가. **기본값이 없다** — 없으면 금액을 내지 않는다.
        day_ahead_price_won_per_kwh: 하루전에너지가격. 위약금 리스크 산정용이며
            없으면 리스크 금액을 내지 않는다.
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

    settlement = None if unit_price_won_per_kwh is None else annual_kwh * unit_price_won_per_kwh
    penalty_per_kw = (
        None
        if day_ahead_price_won_per_kwh is None
        else shortfall_penalty_won(1.0, 0.0, dr_event_hours()[1], day_ahead_price_won_per_kwh)
    )
    months = dr_bid_restriction_months()

    notices: list[Notice] = [
        # **주의** — 위약·리스크. 결과를 그대로 받아들이면 안 되는 것들이다.
        warn(
            "**투자비는 0원이지만 리스크는 0이 아닙니다.** 감축계획량을 채우지 못하면 "
            "실적위약금 = (감축계획량 − 실제감축량) × Max(하루전에너지가격, 0) 이 "
            "부과됩니다 (전력시장운영규칙 별표26).",
            fact="dr.penalty_risk",
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
            f"**연간 감축 가능량 {annual_kwh:,.0f} kWh** = Σ(저부하일별 감축 여력 × 그날 "
            f"참여 가능 시간). 참여 가능 시간의 합은 "
            f"{profile.total_participation_hours:,.0f}시간이고 하루 상한은 "
            f"{profile.daily_hours_cap:,.0f}시간입니다.",
            fact="dr.annual_reducible",
        ),
        # **시간대와 한도는 카드 본문이 이미 낸다** (24세션 3-3 · D). 여기서는
        # 본문에 없는 사실 하나만 적는다 — 왜 창이 둘로 갈라져 있는가.
        basis("점심시간(12–13시)은 운영 시간대에서 빠집니다.", fact="dr.window_rule"),
        basis(
            "**기본요금 절감은 계산하지 않았습니다.** SMP 기준으로 산발적으로 입찰하므로 "
            "참여일이 연중 최대수요일과 겹칠 확률이 낮습니다. 편익은 정산금 하나로 봅니다.",
            fact="dr.no_base_fee_saving",
        ),
        # **참고** — 제도 설명. 화면에 없고 보고서 부록으로 간다.
        info(DR_ADVISORY, fact="dr.advisory"),
        # **참여 조건은 근거다** (22세션 1절). 하루 몇 회·어느 시간대·미이행 제재는
        # 감축 가능량이 왜 그 값인지 설명하는 제도 조건이지 경고가 아니다. 카드가
        # 이것을 주의로 직접 그리면서 확인사항 넷 가운데 하나를 차지하고 있었다.
        basis(profile.notice, fact="dr.participation"),
    ]
    # **차단은 한 줄이다** (22세션 1절). 단가 둘이 다 없으면 같은 말을 두 번 하는
    # 셈이라 확인사항 예산을 둘이나 먹었다. 어느 쪽이 비었는지는 문구가 밝힌다.
    missing = tuple(
        (name, outcome)
        for name, outcome, value in (
            ("정산 단가", "금액", unit_price_won_per_kwh),
            ("하루전에너지가격", "위약금 리스크", day_ahead_price_won_per_kwh),
        )
        if value is None
    )
    if missing:
        inputs = " · ".join(name for name, _ in missing)
        outcomes = "과 ".join(outcome for _, outcome in missing)
        notices.append(
            block(
                f"{inputs}{_object_particle(inputs)} 입력하지 않아 "
                f"{outcomes}{_object_particle(outcomes)} 산출하지 않았습니다. "
                "감축 가능량(kWh)만 참고하십시오 — 정산 단가는 전력거래소 월별 "
                "순편익가격과 사업자 수수료에, 위약금은 하루전에너지가격에 달려 "
                "있습니다 (전력시장운영규칙 별표26).",
                fact="dr.no_price",
            )
        )
    if not profile.meets_reference_capacity:
        notices.append(
            warn(
                f"등록 가능 용량이 참고 문턱 100 kW 아래입니다 ({capacity:,.0f} kW). "
                "자원 단위 기준이라 다른 고객과 묶여 참여할 수 있으므로 사업자와 "
                "상담하십시오 (제12.4.2.1조 제1항 2호).",
                fact="dr.below_reference",
            )
        )
    if not profile.low_load_days_count:
        # **진단이 내는 것과 같은 사실이다.** 줄표 유무로 지문이 갈려 두 번
        # 나왔다 (20세션 2절 결함 ②).
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
        day_ahead_price_won_per_kwh=day_ahead_price_won_per_kwh,
        penalty_per_shortfall_kw_won=penalty_per_kw,
        resource_types=profile.resource_types,
        bid_restriction_months=months,
        participation_notice=profile.notice,
        notices=tuple(notices),
    )
