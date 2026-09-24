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
from collections.abc import Iterable, Sequence

__all__ = [
    "AXIS_UNITS",
    "NO_SAVING",
    "ROUNDING_FOOTNOTE",
    "TRUNCATION_FOOTNOTE",
    "TRUNCATION_UNIT_WON",
    "annual_won",
    "axis_unit",
    "balance_won",
    "delta_amount",
    "gap_won",
    "on_axis",
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


def balance_won(
    parts: Sequence[float],
    total: float,
    signs: Sequence[int] | None = None,
    fixed: Sequence[float | None] | None = None,
) -> list[float]:
    """**단수 차이 조정** — 적힌 줄의 셈이 적힌 합계와 맞게 줄을 천 원씩 올린다 (S232).

    줄마다 절사하면 버린 끝자리가 쌓여 적힌 줄의 합이 합계와 1,000원 넘게
    어긋난다(S231 · 덱 10 ~ 18벌). **합계는 원값을 절사한 그대로 둔다** — 셈의
    결과이기 때문이다. 대신 **잘린 나머지가 가장 큰 줄부터** 잘린 쪽으로 1,000원씩
    올려 ``Σ 부호 × 줄 = 합계`` 가 적힌 수끼리 선다(사람이 정했다 · S232 ㄱ).
    :func:`kwise.report.frames.whole_percents` 와 같은 최대잔여법이다.

    **계산값은 안 바꾼다.** 원값에서 셈이 안 서는 줄(다른 몫이 합계에 든 표)은
    손대지 않고 줄마다 절사만 한다 — 거기서 맞추면 없는 몫을 줄에 싣는다.

    Args:
        parts: 줄의 원값.
        total: 셈의 결과(원값). ``Σ signs × parts`` 여야 조정한다.
        signs: 줄마다 +1 · −1. 차를 적는 표(``현재 − 조정 후``)가 −1 을 쓴다.
        fixed: 줄마다 **이미 다른 표에서 선 표기 값** — 있으면 그 값을 쓰고 안 올린다
            (S233 ㄱ · 사실마다 한 자리). 계약 표의 현재 기본요금은 청구 표의 기본요금이다.
    """
    signs = signs if signs is not None else [1] * len(parts)
    fixed = fixed if fixed is not None else [None] * len(parts)
    shown = [
        truncate_won(value) if preset is None else preset
        for value, preset in zip(parts, fixed, strict=True)
    ]
    if abs(sum(sign * value for sign, value in zip(signs, parts, strict=True)) - total) >= 1:
        return shown
    gap = round(
        (truncate_won(total) - sum(sign * value for sign, value in zip(signs, shown, strict=True)))
        / TRUNCATION_UNIT_WON
    )
    step = 1 if gap > 0 else -1
    # 올릴 수 있는 줄 — 0 에서 먼 쪽(잘린 쪽)으로 움직여 셈이 gap 쪽으로 가는 줄
    movable = [
        index
        for index, (sign, value, cut) in enumerate(zip(signs, parts, shown, strict=True))
        if fixed[index] is None and value != cut and sign * math.copysign(1, value) == step
    ]
    movable.sort(key=lambda index: abs(parts[index] - shown[index]), reverse=True)
    for index in movable[: abs(gap)]:
        shown[index] += math.copysign(TRUNCATION_UNIT_WON, parts[index])
    return shown


def gap_won(minuend: float, subtrahend: float) -> float:
    """**두 합계의 차는 적힌 두 합계의 차다** (S233 ㄴ · 웹 대화창 판단).

    선택요금 「현행 합계 − 최적 합계」 처럼 두 값이 다 셈의 결과이면 올릴 줄이
    없다(S232 가 A 밖으로 뺐다). 차를 원값에서 따로 절사하면 적힌 두 합계의 차와
    1,000원 어긋난다(덱 8벌) — 차를 **적힌 두 값에서** 낸다. 같은 차가 서는 자리는
    다 이것을 지난다.
    """
    return truncate_won(minuend) - truncate_won(subtrahend)


def annual_won(
    annual: float | None, period: float | None, period_shown: float | None
) -> float | None:
    """12개월 환산의 표기 값 — **기간 값과 같은 값이면 기간 값의 글자를 쓴다** (S233 ㄱ).

    12개월 자료는 환산이 기간 값 그대로다. 기간 값을 :func:`gap_won` 으로 적고
    환산을 제 원값으로 적으면 같은 수가 한 줄에 「53,580,000원 (12개월 환산
    53,579,000원)」 으로 선다. 다른 값(12개월 미만 자료)은 제 값 그대로다. 「같은
    값」 은 1원 안이다 — ``x × 12 ÷ 12`` 가 부동소수로 마지막 자리에서 갈릴 수 있다.
    """
    if annual is not None and period is not None and abs(annual - period) < 1:
        return period_shown
    return annual


#: 금액 **축**이 쓰는 단위와 그 나눔수 (S162 2절).
AXIS_UNITS: dict[str, float] = {"만원": _MAN, "억원": _EOK}


def axis_unit(values: Iterable[float | None]) -> str:
    """금액 축의 단위 — **자료의 크기가 고른다** (S156 4-3 을 넓혔다).

    억원 고정은 대형 사업장의 단위다. ``small-ind-a1`` 은 달마다 200만원대라
    눈금이 「0.0000 ~ 0.0200 억원」 이 되어 **넷째 자리를 세어야 금액을 읽는다.**
    가장 큰 값이 1억을 넘으면 억원, 아니면 만원이다. **값은 안 갈린다.**

    ``monthly_charge_png`` 하나가 제 자리에서 하던 셈을 여기로 옮겼다 —
    같은 판단을 그림마다 새로 적으면 한 덱 안에서 갈린다.
    """
    highest = max(
        (abs(float(value)) for value in values if value is not None and value == value),
        default=0.0,
    )
    return "억원" if highest >= _EOK else "만원"


def on_axis(value: float, unit: str) -> str:
    """축 단위로 접은 금액 — ``5.20억원`` · ``1,082만원``.

    **막대에 붙는 값 라벨은 축과 같은 자로 읽혀야 한다** (S162 2-2). 축은 원
    눈금인데 라벨만 만원이면 한 그림에서 두 자를 읽게 된다.

    자리가 좁은 카드에는 :func:`won_short` 를 쓴다 — 그쪽은 축이 없어 「1억
    2,340만원」 처럼 두 단위를 함께 적을 수 있다.
    """
    folded = value / AXIS_UNITS[unit]
    return f"{folded:,.2f}{unit}" if abs(folded) < 10 else f"{folded:,.0f}{unit}"


def delta_amount(value: float, mark: str, unit: str) -> str:
    """차액 막대의 **금액 자리** — 표식이 「현행」 이면 비운다 (S163 1-2).

    현행 요금제는 차액이 0 이라 금액 자리에도 「현행」 을 적고 있었다. 어느
    요금제가 현행인지 말하는 것은 **표식** 쪽이라 한 그림에 「현행」 이 두 번
    섰다 — 화면은 라벨이 「현행 · 현행」, PPT 는 x 눈금 이름 밑에 두 줄이었다.

    **화면과 PPT 가 함께 지난다.** 두 자리에 따로 적으면 또 갈린다.
    """
    return "" if mark == "현행" else on_axis(value, unit)


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
