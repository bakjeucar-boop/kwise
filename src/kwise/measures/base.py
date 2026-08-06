"""개선 수단 공통 (요구사항서 7장, 8장).

수단은 **투자비 순으로** 배치한다. 투자 0원짜리가 먼저다.

절감액은 언제나 **재계산**이다. 수단을 적용한 부하로 요금을 처음부터 다시 산출해
기준선과 비교한다. 빼기로 어림하면 최적 선택요금이 바뀌는 것을 놓친다.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["Certainty", "annualize", "payback_years"]


class Certainty(StrEnum):
    """확실성 등급 (요구사항서 8장). 같은 표에서 이를 구분해 보여준다."""

    HIGH = "높음"  # 요금제 전환, 계약전력 조정 — 실측과 요금표만으로 확정
    MEDIUM = "중간"  # 태양광 — 발전량 예측 오차
    MEDIUM_LOW = "중간~낮음"  # ESS — 운전 전략과 열화에 좌우


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
