"""금액 표기 (14세션).

**원화 표기는 전부 여기를 지난다.** 화면·Excel·Word 가 각자 ``f"{value:,.0f}원"``
을 쓰면 같은 값이 세 군데에서 다르게 보이고, 표기 규칙을 바꿀 때 한 곳을 빠뜨린다.

    표시   천 원 단위로 **절사**한다.  1,234,567원 → 1,234,000원
    계산   **원 단위를 그대로 유지한다.** 절사해서 계산하면 합계가 어긋난다.

절사는 **표시 직전에 한 번만** 한다. :func:`truncate_won` 을 계산 중간에 끼워
넣지 마라 — 항목을 절사해 더하면 합계가 항목 수만큼 어긋난다.

항목을 각각 절사하고 합계는 원값을 절사해 내므로 **항목 합과 합계 표시가
1천 원 내외 다를 수 있다.** 표 아래에 :data:`TRUNCATION_FOOTNOTE` 를 단다.
만원 표기를 쓰는 표는 :data:`ROUNDING_FOOTNOTE` 다 — **각주가 표기 방식을
그대로 적어야 한다** (28세션).

억·만원 표기(:func:`won_short`)도 같은 곳에서 관리한다. 이쪽은 만원 자리에서
반올림하므로 천 원 절사보다 이미 굵다 — 따로 절사하지 않는다.
"""

from __future__ import annotations

import math

__all__ = [
    "NO_SAVING",
    "ROUNDING_FOOTNOTE",
    "TRUNCATION_FOOTNOTE",
    "TRUNCATION_UNIT_WON",
    "truncate_won",
    "won",
    "won_plain",
    "won_short",
]

NO_SAVING = "없음"
"""금액 칸의 **셋째 값** (83세션). 「줄 것이 없다」 는 결론이다.

    없음   따져 보니 줄어들 몫 자체가 없다 (계약전력 조정의 하한 미적용)
    0      계산해서 0원이 나왔다 (선택요금 전환의 「현행이 최적」)
    —      산출하지 못했다 (단가 미입력 · 하한 비율 미확인)

**셋이 다른 것을 뜻하므로 서로 대신 쓰지 않는다.** 여기 두는 까닭은 화면·
Excel·PPT 가 같은 자리에 같은 말을 적어야 하기 때문이다 — 금액 표기와 같은 문.
"""

TRUNCATION_UNIT_WON = 1_000
"""표시 절사 단위. 천 원."""

TRUNCATION_FOOTNOTE = "금액은 천 원 단위로 절사 표시되어 항목 합과 차이가 날 수 있습니다."

#: 만원 표기(:func:`won_short`)를 쓰는 표의 각주 (28세션 1-3). 절사가 아니라
#: **반올림**이라 같은 말을 쓸 수 없다 — 각주가 표기 방식을 잘못 적으면 항목 합이
#: 어긋나 보일 때 읽는 사람이 엉뚱한 자리를 의심한다.
ROUNDING_FOOTNOTE = "금액은 만원 단위로 반올림 표시되어 항목 합과 차이가 날 수 있습니다."

_EOK = 100_000_000
_MAN = 10_000


def truncate_won(value: float) -> float:
    """천 원 단위 절사. **표시 직전에만 부른다.**

    음수는 0 쪽으로 자른다 — ``-1,234,567`` 은 ``-1,234,000`` 이다. 내림으로
    처리하면 손실 항목만 한 단위 더 커 보인다.
    """
    return float(math.trunc(value / TRUNCATION_UNIT_WON) * TRUNCATION_UNIT_WON)


def won_plain(value: float | None, *, reason: str) -> str:
    """단위 없는 금액. **열 이름이 ``(원)`` 을 달고 있는 Excel 칸에 쓴다.**

    ``None`` 이면 빈칸이나 0 이 아니라 사유다 — 0원은 "공짜" 로 읽힌다.
    """
    if value is None:
        return reason
    return f"{truncate_won(value):,.0f}"


def won(value: float | None, *, reason: str) -> str:
    """``1,234,000원``. 표·본문·산출물에서 값을 대조할 수 있는 표기다."""
    if value is None:
        return reason
    return f"{truncate_won(value):,.0f}원"


def won_short(value: float | None, *, reason: str) -> str:
    """``1억 2,340만원``. 지표 카드처럼 자리가 좁은 곳에 쓴다.

    ``1.23억원`` 보다 자릿수가 그대로 읽힌다 — 억 단위 소수는 만원 자리를 감춘다.
    **버리지 않고 반올림한다** — 31,518,402원은 3,151만원이 아니라 3,152만원이다.
    만원 자리 반올림이 천 원 절사보다 굵으므로 여기서 다시 절사하지 않는다.
    """
    if value is None:
        return reason
    sign = "-" if value < 0 else ""
    size = round(abs(value))
    if size < _MAN:
        return f"{sign}{truncate_won(size):,.0f}원"
    total_man = round(size / _MAN)
    eok, man = divmod(total_man, _MAN)
    if eok == 0:
        return f"{sign}{man:,}만원"
    return f"{sign}{eok:,}억원" if man == 0 else f"{sign}{eok:,}억 {man:,}만원"
