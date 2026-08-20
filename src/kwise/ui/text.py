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
from kwise.money import ROUNDING_FOOTNOTE, TRUNCATION_FOOTNOTE
from kwise.report.notices import format_won

__all__ = [
    "CHART_TIPS",
    "DASH",
    "RANGE",
    "ROUNDING_FOOTNOTE",
    "TIPS",
    "TRUNCATION_FOOTNOTE",
    "chart_tip",
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
    "per_year",
    "period",
    "range_text",
    "ratio_pct",
    "tip",
    "won",
    "won_short",
    "won_year",
]

# 긴 설명은 **툴팁으로 보낸다** (15세션 1-2). 선택지·지표 옆에 길게 붙여 놓으면
# 고르는 순간에 읽히지 않고 화면만 길어진다. 한 곳에 모아 두어 같은 말이 두
# 화면에서 다르게 적히는 것을 막는다. 문단은 빈 줄로 나눈다 — Streamlit 의
# ``help=`` 는 마크다운을 해석한다.
#
# **그래서 툴팁도 escape 한다** (25세션 2절). 여기 있는 글을 ``help=`` 에 곧장
# 넣지 말고 :func:`tip` 으로 꺼낸다 — 굵게는 살리고 물결표만 막는다.
# **확실성 툴팁과 감도 툴팁을 지웠다** (28세션 4·5절). 화면에서 둘 다 뺐으므로
# 붙일 자리가 없다. 등급의 뜻과 감도의 정의는 Excel·보고서·매뉴얼에 남는다.
TIPS: dict[str, str] = {
    "discharge_hours": "\n\n".join(
        (
            "**방전시간** = 하루 최대 초과 에너지 ÷ 최대 초과 출력.",
            "**입력이 아니라 산출값입니다.** 목표를 정하면 데이터가 정합니다.",
            "짧을수록 kW당 단가가 싸지만, 0.5시간 미만은 2C 이상이라 정치형 LFP 의 "
            "통상 사양(0.5–1C 연속)을 넘는 고출력 셀입니다.",
        )
    ),
    "surplus_free": "\n\n".join(
        (
            "**잉여 없는 최대 용량** — 어느 15분 구간에서도 발전이 부하를 넘지 않는 "
            "가장 큰 용량입니다.",
            "여기까지는 전량 자가소비라 상계거래 계약도 역송 계량기도 필요 없습니다. "
            "더 지으면 남는 몫을 어떻게 할지(잉여 활용 카드)를 함께 정해야 합니다.",
        )
    ),
    "contract_margin": "\n\n".join(
        (
            "**확보할 여유율** — 향후 부하 증가와 예측 오차에 대비한 완충입니다.",
            "높이면 안전하지만 절감액이 줄고, 낮추면 절감액이 늘지만 초과 위험이 커집니다.",
            # 「한 번의 초과가 12개월간 적용됩니다」 를 뺐다 (25세션 3-3 · B).
            # 같은 카드 본문의 계약전력 변경 경고가 그 말을 이미 한다.
        )
    ),
}

# ===================================================================== 23세션 3절 · 그래프
#
# **그래프 툴팁은 형식이 다르다.** 21세션이 지표 툴팁을 「산식 한 줄 + 의미 한 줄」
# 로 못박았는데, 그림에는 나눌 산식이 없다. 대신 이 둘을 적는다.
#
#     ① 무엇을 그렸나 — 축과 계열이 무엇인지
#     ② 무엇을 읽나  — 어떤 모양이면 어떤 뜻인지
#
# 지표 툴팁과 섞이지 않게 열쇠를 ``chart.`` 로 시작한다.
CHART_TIPS: dict[str, str] = {
    "chart.monthly_peak": (
        "달마다 기록된 최대수요입니다. 붉은 점선이 요금적용전력이고, "
        "기본요금은 이 선으로 매겨집니다.\n\n"
        "한 달만 유난히 솟아 있으면 그 달의 몇십 분이 1년 기본요금을 끌어올린 것입니다."
    ),
    "chart.top_hour": (
        "연간 최대수요 상위 100개 구간이 하루 중 언제 발생했는지 보여줍니다.\n\n"
        "낮 시간에 몰려 있으면 태양광이 피크를 낮출 여지가 큽니다. "
        "밤에 몰려 있으면 태양광으로는 기본요금이 줄지 않습니다."
    ),
    "chart.hourly_profile": (
        "하루 24시간의 평균 부하 모양입니다.\n\n"
        "낮에 봉우리가 하나면 냉방·조업 부하가, 밤에도 높으면 상시 설비가 큰 건물입니다."
    ),
    "chart.daily_temperature": (
        "날마다 쓴 전기(왼쪽 축)와 그날의 평균 기온(오른쪽 축)을 겹쳐 놓은 그림입니다.\n\n"
        "**두 선이 함께 오르내리면 냉난방이 부하를 끌고 가는 건물**이고, 기온이 "
        "움직여도 사용량이 평평하면 조업 부하가 지배하는 건물입니다."
    ),
    "chart.monthly_charge": (
        "달마다 청구액이 기본요금과 계시별 전력량요금으로 어떻게 나뉘는지 쌓아 "
        "보여줍니다.\n\n"
        "밑단(기본요금)은 요금적용전력으로 매겨져 대개 같은 높이로 이어지고, 그 위 "
        "세 조각이 계절과 조업에 따라 움직입니다."
    ),
    "chart.band": (
        "계시별 시간대(경부하·중간부하·최대부하)에 사용량이 어떻게 나뉘는지 보여줍니다.\n\n"
        "최대부하 비중이 크면 단가가 낮은 시간대로 옮길 여지를 먼저 봅니다."
    ),
    "chart.tariff_option": (
        "요금제마다 기본요금과 전력량요금을 나란히 세운 그림입니다.\n\n"
        "**선택요금은 그 둘을 맞바꾸는 제도**입니다 — 기본요금이 오르는 대신 "
        "전력량요금이 내려갑니다. 합계가 낮은 쪽이 유리합니다."
    ),
    "chart.tariff_delta": (
        "현행 요금제 대비 얼마나 늘고 주는지를 요금제별로 보여줍니다.\n\n"
        "0 보다 왼쪽이면 절감, 오른쪽이면 증가입니다."
    ),
    "chart.dr_daily": (
        "하루 한 점씩, 그날 판정 시간대의 평균 부하입니다. 붉은 가로선이 "
        "주말·공휴일 평균(기준선)과 저부하 문턱입니다.\n\n"
        "문턱 아래로 내려온 평일(역삼각형)이 감축을 입찰할 수 있는 날입니다."
    ),
    "chart.power_triangle": (
        "유효전력을 1 로 두고 무효전력이 얼마나 붙어 있는지 그린 삼각형입니다.\n\n"
        "**각이 좁아질수록 역률이 좋습니다.** 개선 전후 두 삼각형의 각도 차가 곧 "
        "역률 개선 폭입니다."
    ),
    "chart.power_factor_day": (
        "대표일의 15분 부하이고, 주황 점이 역률을 판정하는 주간(08~22시) 구간입니다.\n\n"
        "이 구간의 부하가 클수록 역률 조정액이 커집니다 — 역률요금은 기본요금에 "
        "비례하기 때문입니다."
    ),
    "chart.solar_curve": (
        "설치 용량을 키우며 절감액이 어떻게 늘어나는지 훑은 곡선입니다. "
        "붉은 표식이 고른 최적 용량입니다.\n\n"
        "선이 눕기 시작하는 지점부터는 더 지어도 절감이 그만큼 늘지 않습니다."
    ),
    "chart.solar_annual": (
        "날짜별 태양광 발전량입니다.\n\n"
        "여름에 높고 겨울에 낮은 계절 굴곡이 보입니다 — 기본요금은 여름 피크에 "
        "매이므로 그 계절의 발전량이 특히 중요합니다."
    ),
    "chart.solar_day": (
        "대표일의 피크 앞뒤 시간대입니다. 파란 두 선은 원래 부하와 태양광 적용 후 "
        "부하이고, 그 사이 초록이 저감분입니다.\n\n"
        "**피크 시각에 초록이 두꺼워야** 기본요금이 줄어듭니다. 발전이 많아도 피크와 "
        "어긋나면 전력량요금만 줄어듭니다."
    ),
    "chart.ess_target": (
        "배터리를 얼마나 크게 지을 때 회수기간이 어떻게 달라지는지 그린 곡선이고, "
        "붉은 표식이 최소 지점입니다.\n\n"
        "**목표를 낮추면 저감량은 늘지만 필요 용량이 훨씬 빠르게 늘어 회수기간이 "
        "나빠집니다.** 최소 지점이 그 균형점입니다 — 더 큰 배터리가 더 나은 것이 "
        "아닙니다."
    ),
    "chart.ess_day": (
        "대표일의 피크 앞뒤 시간대입니다. 두 선 사이 초록이 ESS 로 깎은 몫이고, "
        "붉은 점선이 목표 요금적용전력입니다.\n\n"
        "**순부하 선이 목표선 아래로 눌려 있어야** 그 목표가 지켜진 것입니다."
    ),
    "chart.surplus_daily": (
        "날짜별 잉여 발전량이고, 색이 요일 갈래입니다.\n\n"
        "주말에 몰려 있으면 공장이 쉬는 날 남는 구조입니다 — 상계거래·외부 판매의 "
        "값어치가 그만큼 큽니다."
    ),
    "chart.combination": (
        "고른 수단을 하나씩 쌓아 가며 조합마다 요금을 다시 계산한 절감액입니다.\n\n"
        "막대가 위에서 아래로 길어지는 폭이 그 수단을 더해 얻는 몫입니다 — 폭이 "
        "좁아지면 앞의 수단과 겹치는 부분이 있다는 뜻입니다."
    ),
}


def tip(key: str) -> str:
    """지표·입력 툴팁. **escape 해서 낸다** (25세션 2절).

    ``help=`` 는 마크다운을 해석하므로 ``08~22시`` 가 한 줄에 둘 있으면 그 사이가
    취소선이 된다. 굵게는 살아야 하므로 물결표만 막는다.
    """
    try:
        return markdown_safe(TIPS[key])
    except KeyError as exc:  # pragma: no cover - 개발 중 오타를 잡는 자리다
        raise KeyError(f"등록되지 않은 툴팁입니다: {key!r}") from exc


def chart_tip(key: str) -> str:
    """그래프 툴팁. **없는 열쇠는 곧바로 드러낸다** — 조용히 빈 물음표를 두지 않는다."""
    try:
        return markdown_safe(CHART_TIPS[key])
    except KeyError as exc:  # pragma: no cover - 개발 중 오타를 잡는 자리다
        raise KeyError(f"등록되지 않은 그래프 툴팁입니다: {key!r}") from exc


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


#: 기간 단위 꼬리표. **12개월 환산값에만 붙인다** (26세션 2-3).
PER_YEAR = "/년"


def per_year(text: str) -> str:
    """``1,940.8 MWh`` → ``1,940.8 MWh/년``. **12개월 환산값에 단위를 붙인다.**

    지표 카드에 ``896만원`` 만 있으면 한 달인지 한 해인지 알 수 없다. 라벨에
    「연간」 을 붙이는 대신 값에 단위를 붙인다 — 라벨 자리는 좁고, 옮겨 적을 때
    단위가 값을 따라가야 한다.

    **값이 없어 사유가 들어온 자리에는 쓰지 않는다** — 금액은 :func:`won_year`
    가 그 갈림을 대신 판단한다.
    """
    return f"{text.strip()}{PER_YEAR}" if text.strip() not in ("", DASH) else text


def won_year(value: float | None, *, reason: str | None = None) -> str:
    """12개월 환산 금액. ``896만원/년``.

    **값이 없으면 사유이고 꼬리표를 붙이지 않는다** — ``미산출 — 단가 미입력/년``
    은 말이 되지 않는다 (7.5 의 빈칸·0원 금지 규약과 같은 자리다).
    """
    if value is None:
        return won_short(value, reason=reason)
    return per_year(won_short(value))


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


# **확실성 뱃지를 지웠다** (28세션 4절). 화면에서 등급을 빼기로 했으므로 이
# 껍데기도 함께 없앤다 — 남겨 두면 다음에 누군가 다시 붙인다. 등급 이름의
# 물결표를 범위 기호로 바꾸던 일(25세션 2절)도 화면에 등급이 없으니 끝났다.
