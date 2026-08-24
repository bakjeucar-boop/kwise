"""개선 수단 공통 (요구사항서 7장, 8장).

수단은 **투자비 순으로** 배치한다. 투자 0원짜리가 먼저다.

절감액은 언제나 **재계산**이다. 수단을 적용한 부하로 요금을 처음부터 다시 산출해
기준선과 비교한다. 빼기로 어림하면 최적 선택요금이 바뀌는 것을 놓친다.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

__all__ = [
    "LARGEST_SAVING",
    "SHORTEST_PAYBACK",
    "Certainty",
    "annualize",
    "lowest_certainty",
    "payback_years",
]

SHORTEST_PAYBACK = "최단 회수기간"
"""곡선·표에서 고른 지점을 부르는 이름 (49세션). **「최적」 이라 부르지 않는다.**

「최적」 은 판단이 들어간 말이라 **「그것을 하라」 로 읽힌다.** 소형 사무빌딩에서
회수기간 578년짜리가 「최적 목표」 로 나왔다 — 성립 조건을 늘려 지우는 것이 아니라
이름을 고치는 것이 답이다. 「최단 회수기간」 은 사실만 말하므로 578년이어도 거짓이
아니고, 수명을 넘는다는 판단은 ``ess.payback_over_warranty`` 경고가 따로 한다.

**선택요금 전환의 「최적 요금제」 는 그대로 둔다** — 그쪽은 회수기간이 아니라
금액이 가장 적은 요금제이고, 투자가 없어 「최적」 이 사실과 어긋나지 않는다.
"""

LARGEST_SAVING = "최대 절감액"
"""설치 단가를 넣지 않아 **절감액**으로 고른 경우의 이름 (49세션).

태양광은 단가를 안 넣으면 회수기간을 못 내므로 절감액이 가장 큰 용량을 고른다.
그 자리에 「최단 회수기간」 을 적으면 **없는 사실을 적는 것**이라 갈라 둔다.
"""


class Certainty(StrEnum):
    """확실성 등급 (요구사항서 8장). 같은 표에서 이를 구분해 보여준다."""

    HIGH = "높음"  # 요금제 전환, 계약전력 조정 — 실측과 요금표만으로 확정
    MEDIUM = "중간"  # 태양광 — 발전량 예측 오차
    MEDIUM_LOW = "중간~낮음"  # ESS — 운전 전략과 열화에 좌우

    @property
    def rank(self) -> int:
        """높을수록 확실하다. 조합 등급을 고를 때 쓴다."""
        return {Certainty.MEDIUM_LOW: 0, Certainty.MEDIUM: 1, Certainty.HIGH: 2}[self]


def lowest_certainty(items: Iterable[Certainty]) -> Certainty:
    """조합의 확실성은 **가장 낮은 구성 요소**를 따른다.

    태양광과 ESS 를 함께 넣으면 조합 전체가 ESS 등급(중간~낮음)이 된다.
    비어 있으면 확정 계산만 있다는 뜻이므로 '높음'.
    """
    return min(items, key=lambda item: item.rank, default=Certainty.HIGH)


def annualize(amount_won: float, base_fee_months: float) -> float:
    """기간 금액을 12개월로 환산한다. '연간' 이라 부르지 않는다 (요구사항서 5.5)."""
    if base_fee_months <= 0:
        raise ValueError(f"기본요금 개월수가 0 이하입니다: {base_fee_months}")
    return amount_won * 12.0 / base_fee_months


def payback_years(investment_won: float, annual_saving_won: float) -> float | None:
    """단순 회수기간. 절감이 없으면 None."""
    if annual_saving_won <= 0:
        return None
    return investment_won / annual_saving_won
