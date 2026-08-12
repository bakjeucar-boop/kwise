"""화면 표기 (요구사항서 10.2·10.7).

**금액·전력·기간을 한 곳에서 찍는다.** 화면마다 자릿수를 달리 쓰면 같은 값이
다르게 보인다.

**모든 숫자에 세 자리 콤마를 넣는다.** 금액·전력·에너지 전부다. 여기 있는
함수만 쓰면 저절로 지켜진다 — 화면에서 ``f"{value:,.0f}"`` 를 직접 쓰지 않는다.

금액 표기를 둘로 나눠 쓴다.

    화면 지표 카드   :func:`won_short` — ``1억 2,340만원``. 한눈에 크기를 본다
    표·본문·산출물   :func:`won` — ``123,400,000원``. 대조할 수 있다

**규칙 자체는 :mod:`kwise.money` 한 곳에 있다** (14세션). 여기 있는 것은 화면용
껍데기다 — 화면·Excel·Word 가 같은 절사(천 원 단위)를 쓴다.

모르는 값은 **빈칸이나 0 으로 두지 않는다.** 0원은 "공짜" 로, 회수기간 0년은
"즉시 회수" 로 읽힌다. 사유를 적는 것이 규약이다 (요구사항서 7.5).
"""

from __future__ import annotations

import re

from kwise import money
from kwise.measures import Certainty
from kwise.money import TRUNCATION_FOOTNOTE
from kwise.report.notices import format_won

__all__ = [
    "DASH",
    "RANGE",
    "TIPS",
    "TRUNCATION_FOOTNOTE",
    "certainty_badge",
    "count",
    "days",
    "hours",
    "kw",
    "kwh",
    "kwp",
    "markdown_safe",
    "money_range",
    "months",
    "mwh",
    "payback",
    "pct",
    "period",
    "range_text",
    "ratio_pct",
    "won",
    "won_short",
]

# 긴 설명은 **툴팁으로 보낸다** (15세션 1-2). 선택지·지표 옆에 길게 붙여 놓으면
# 고르는 순간에 읽히지 않고 화면만 길어진다. 한 곳에 모아 두어 같은 말이 두
# 화면에서 다르게 적히는 것을 막는다. 문단은 빈 줄로 나눈다 — Streamlit 의
# ``help=`` 는 마크다운을 해석한다.
TIPS: dict[str, str] = {
    "certainty": "\n\n".join(
        (
            "**확실성** — 결과를 얼마나 믿을 수 있는지의 등급입니다.",
            "**높음** 요금표와 약관만으로 확정됩니다 (요금제 전환·계약전력·역률).",
            "**중간** 발전량 예측이 끼어 있습니다 (태양광).",
            "**중간~낮음** 운전 전략과 열화에 좌우됩니다 (ESS·잉여 활용).",
            "조합의 등급은 **가장 낮은 구성 요소**를 따릅니다.",
        )
    ),
    "sensitivity": "\n\n".join(
        (
            "**감도 범위** — 태양광 발전 프로파일의 불확실성을 반영한 폭입니다.",
            "흔드는 축은 총량이 아니라 **정오의 첨예도**입니다. 재분석 기상 자료가 "
            "피크 발전량을 평탄하게 내는 경향이 있어, 총량은 맞아도 피크 절감이 "
            "달라집니다.",
            "이 축에는 좋고 나쁨이 없습니다 — 세 값 중 하나를 고르는 것이 아닙니다.",
        )
    ),
    "discharge_hours": "\n\n".join(
        (
            "**방전시간** = 하루 최대 초과 에너지 ÷ 최대 초과 출력.",
            "**입력이 아니라 산출값입니다.** 목표를 정하면 데이터가 정합니다.",
            "짧을수록 kW당 단가가 싸지만, 0.5시간 미만은 2C 이상이라 정치형 LFP 의 "
            "통상 사양(0.5–1C 연속)을 넘는 고출력 셀입니다.",
        )
    ),
    "contract_margin": "\n\n".join(
        (
            "**확보할 여유율** — 향후 부하 증가와 예측 오차에 대비한 완충입니다.",
            "높이면 안전하지만 절감액이 줄고, 낮추면 절감액이 늘지만 초과 위험이 커집니다.",
            "**한 번의 초과가 12개월간 적용됩니다.** 기본요금은 직전 12개월 중 "
            "최대수요로 결정되기 때문입니다.",
        )
    ),
}

DASH = "—"
RANGE = "–"
"""범위 기호. **물결표를 쓰지 않는다** — 한 줄에 둘이 들어가면 Streamlit 이
그 사이를 ~~취소선~~ 으로 그린다 (13세션). 계산 모듈이 내는 물결표는
:func:`markdown_safe` 가 렌더 직전에 escape 한다."""


def won(value: float | None, *, reason: str | None = None) -> str:
    """원 단위 금액. **천 원 단위로 절사해 보인다** (14세션).

    ``None`` 이면 **사유**를 낸다 (빈칸·0원 금지).
    """
    return money.won(value, reason=format_won(None) if reason is None else reason)


def won_short(value: float | None, *, reason: str | None = None) -> str:
    """억·만원으로 줄인 금액. 지표 카드처럼 자리가 좁은 곳에 쓴다.

    ``1억 2,340만원`` 꼴이다. ``1.23억원`` 보다 자릿수가 그대로 읽힌다 —
    억 단위 소수는 만원 자리를 감춘다.
    """
    return money.won_short(value, reason=format_won(None) if reason is None else reason)


def money_range(base: float | None, low: float | None, high: float | None) -> str:
    """``3,152만원 (2,897 – 3,266만원)`` — 감도 범위를 기준값 옆에 붙인다 (9.2).

    **3열로 나열하지 않는다.** 세 값을 나란히 놓으면 "어느 쪽이 좋은 값인가" 를
    찾게 되는데 이 축에는 좋고 나쁨이 없다.
    """
    if base is None:
        return format_won(None)
    text = won_short(base)
    if low is None or high is None:
        return text
    first, second = won_short(low), won_short(high)
    for unit in ("만원", "억원", "원"):
        if first.endswith(unit) and second.endswith(unit):
            first = first[: -len(unit)]
            break
    return f"{text} ({first} {RANGE} {second})"


def kw(value: float | None, *, decimals: int = 1) -> str:
    return DASH if value is None else f"{value:,.{decimals}f} kW"


def kwp(value: float | None, *, decimals: int = 0) -> str:
    return DASH if value is None else f"{value:,.{decimals}f} kWp"


def count(value: float | None, unit: str = "", *, decimals: int = 0) -> str:
    """단위가 붙는 일반 수. **세 자리 콤마가 들어간다.**"""
    if value is None:
        return DASH
    return f"{value:,.{decimals}f}{unit}"


def days(value: float | None) -> str:
    return DASH if value is None else f"{value:,.0f}일"


def hours(value: float | None, *, decimals: int = 2) -> str:
    return DASH if value is None else f"{value:,.{decimals}f}시간"


def kwh(value: float | None, *, decimals: int = 0) -> str:
    return DASH if value is None else f"{value:,.{decimals}f} kWh"


def mwh(value: float | None, *, decimals: int = 1) -> str:
    return DASH if value is None else f"{value / 1000.0:,.{decimals}f} MWh"


def pct(value: float | None, *, decimals: int = 1) -> str:
    """이미 백분율인 값."""
    return DASH if value is None else f"{value:,.{decimals}f}%"


def ratio_pct(value: float | None, *, decimals: int = 1) -> str:
    """0~1 비율을 백분율로."""
    return DASH if value is None else f"{value * 100.0:,.{decimals}f}%"


def months(value: float | None, *, decimals: int = 1) -> str:
    return DASH if value is None else f"{value:,.{decimals}f}개월"


def period(start: object, end: object, span_days: float | None = None) -> str:
    """``2023-04-25 – 2024-04-27 (369일)`` — **한 줄에 들어가는 길이**로 맞춘다."""
    text = f"{start:%Y-%m-%d} {RANGE} {end:%Y-%m-%d}"
    return text if span_days is None else f"{text} ({span_days:,.0f}일)"


def range_text(base: str, low: str, high: str) -> str:
    """``3,152만원 (2,897 – 3,266만원)`` — 감도 범위를 기준값 옆에 붙인다 (9.2)."""
    return f"{base} ({low} {RANGE} {high})"


def markdown_safe(value: str) -> str:
    """계산 모듈이 낸 문구를 화면에 그대로 실을 수 있게 한다 (13세션).

    ``야간 22~8시 · 운영 9~18시`` 처럼 **물결표가 한 줄에 둘** 있으면 Streamlit 이
    그 사이를 취소선으로 그린다. 계산 모듈의 문구는 Excel·보고서로도 가므로
    거기서 고치지 않고 **화면에 실을 때 escape** 한다.
    """
    return re.sub(r"(?<!\\)~", r"\\~", value)


def payback(years: float | None, *, investment_won: float | None = None) -> str:
    """회수기간. **투자비를 모르면 0년이 아니라 사유**다.

    투자비가 0원인 무투자 수단만 '즉시' 로 적는다.
    """
    if years is None:
        if investment_won is None:
            return "미산출 — 투자비 미입력"
        return DASH
    if years <= 0:
        return "즉시"
    return f"{years:,.1f}년"


def certainty_badge(certainty: Certainty | str) -> str:
    """확실성 등급. **등급만 낸다** — 근거는 매뉴얼로 보낸다 (10.2)."""
    return f"확실성 {certainty}"
