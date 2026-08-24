"""개선 수단 공통 (요구사항서 7장, 8장).

수단은 **투자비 순으로** 배치한다. 투자 0원짜리가 먼저다.

절감액은 언제나 **재계산**이다. 수단을 적용한 부하로 요금을 처음부터 다시 산출해
기준선과 비교한다. 빼기로 어림하면 최적 선택요금이 바뀌는 것을 놓친다.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

__all__ = [
    "AREA_EXCEEDED",
    "LARGEST_SAVING",
    "RECOMMENDED",
    "SELECTED_CAPACITY",
    "SHORTEST_PAYBACK",
    "SURPLUS_HEAVY",
    "SURPLUS_ONSET",
    "TIED_PAYBACK",
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

RECOMMENDED = "권장"
"""**동률 처리를 거쳐** 고른 자리의 이름 (50세션).

49세션이 「최적」 을 「최단 회수기간」 으로 좁혔는데, 태양광은 16세션의 동률
처리(``pv.payback_tie_ratio``)를 거쳐 고른다 — 회수기간 차이가 예측 오차 안이면
절감액이 큰 쪽을 고른다. 그래서 **11.0년짜리를 두고 11.1년짜리에 「최단
회수기간」 이 붙는 일**이 생겼다. 이름이 규칙과 어긋난 것이다.

「권장」 은 규칙을 드러내지 않으면서 사실과도 어긋나지 않는다. **판정 근거는
:func:`~kwise.measures.solar.payback_tie_note` 가 표 아래 한 줄로 적는다** —
출처가 아니라 결과를 읽는 데 필요한 설명이라 화면에 남긴다.

**ESS 에는 쓰지 않는다.** 그쪽은 동률 처리 없이 최소를 그대로 고르므로
:data:`SHORTEST_PAYBACK` 이 맞다. 같은 말이 두 규칙을 가리키면 안 된다.
"""


# ===================================================================== 표식 어휘
#
# **표식 이름은 전부 여기 있다** (51세션). 50세션이 「최적 → 권장」 을 고치면서
# 이름 셋을 한 자리에 뒀는데, 표에 붙는 **나머지 표식은 문자열로 흩어져** 있었다
# (`report.frames` 둘 · `ui.pipeline` 둘). 그래서 「권장」 이 「선정 용량」 에
# 먹히는 것을 아무도 못 잡았다 — 각주는 「권장」 을 말하는데 표에는 없었다.
#
# **각주와 표가 같은 상수를 읽는다.** 이름을 바꾸면 둘이 함께 바뀐다.

SELECTED_CAPACITY = "선정 용량"
"""**카드가 머리에 낸 그 용량** — 설치 가능 면적이 허용하는 상한이다.

:data:`RECOMMENDED` 와 **다른 사실이다.** 「지을 수 있는 가장 큰 것」 과 「권하는
것」 은 대개 다르고, 같을 때는 **둘 다 적는다** (51세션). 50세션까지는 둘이
같으면 이쪽만 찍어 「권장」 이 화면에서 사라졌다.
"""

TIED_PAYBACK = "동률"
"""회수기간이 :data:`RECOMMENDED` 와 **사실상 같은** 줄 (51세션 2절).

kWp당 단가면 투자비와 절감액이 함께 용량에 비례해 **회수기간이 용량과 거의
무관해진다** (16세션). 표가 그것을 그대로 보이면 「어느 것을 골라도 같다」 로
읽히는데, 실제로는 절감액이 세 배 차이 난다.

**표식으로 묶어 각주와 잇는다** — 「이 줄들은 회수기간이 같고, 그중 절감액이
큰 것을 권한다」 가 표 안에서 읽힌다.
"""

AREA_EXCEEDED = "면적 초과"
"""설치 가능 면적을 넘는 줄. **값을 지우지는 않는다** — 「이만큼 지으면 이런
값인데 자리가 없다」 가 판단에 필요한 사실이다."""

SURPLUS_ONSET = "잉여 시작"
"""잉여가 처음 생기는 용량 (31세션 4-1)."""

SURPLUS_HEAVY = "잉여 다량"
"""잉여가 발전량의 :func:`~kwise.measures.solar.surplus_heavy_share` 를 넘는 용량."""


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
