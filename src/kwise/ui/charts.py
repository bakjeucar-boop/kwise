"""화면 차트 (요구사항서 10.2 — altair).

표를 그리는 자리가 아니라 **한눈에 판단이 서야 하는 자리에만** 쓴다. 진단에서는
둘이 필수다 (10.1).

    월별 최대수요        요금은 월별로 매겨지고 요금적용전력은 그 이력의 최대다
    상위 100구간 시각 분포  **태양광 기여 가능성을 즉시 보여 주는 지표다**

**프레임은 :mod:`kwise.report.frames` 에 있다.** 화면(altair)과 보고서
(matplotlib)가 같은 표를 봐야 같은 수를 그린다. 여기 있는 것은 altair 사양뿐이다.
"""

from __future__ import annotations

from collections.abc import Sequence

import altair as alt
import pandas as pd

from kwise import money
from kwise.compare import ComparisonResult, SensitivityRange
from kwise.diagnose import ChargeStructure, PeakProfile
from kwise.diagnose.dr import DrProfile
from kwise.io import UsageData
from kwise.measures import (
    CapacityVerdict,
    DispatchResult,
    EssTargetCurve,
    PowerFactorResult,
    SolarCurve,
    TariffSwitchResult,
)
from kwise.report.days import RepresentativeDay
from kwise.report.frames import (
    BAND_LABELS,
    CAPACITY_ROWS,
    MONTHLY_CHARGE_PARTS,
    PEAK_ZOOM_HOURS,
    TARIFF_PARTS,
    band_frame,
    combination_frame,
    daily_temperature_frame,
    dispatch_schedule,
    dr_daily_frame,
    ess_day_frame,
    ess_target_frame,
    hourly_profile_frame,
    monthly_charge_frame,
    monthly_peak_frame,
    peak_window,
    power_factor_day_frame,
    power_triangle_frame,
    sensitivity_frame,
    solar_annual_frame,
    solar_capacity_table,
    solar_curve_frame,
    solar_day_frame,
    surplus_daily_frame,
    tariff_delta_frame,
    tariff_option_frame,
    tariff_option_long_frame,
    temperature_mean_frame,
    top_hour_frame,
)

__all__ = [
    "BAND_LABELS",
    "CAPACITY_ROWS",
    "DATE_FORMAT",
    "DATE_LABEL_EXPR",
    "LEGEND",
    "LEGEND_BELOW",
    "MONTHLY_CHARGE_PARTS",
    "MONTH_BAR_STEP",
    "PEAK_ZOOM_HOURS",
    "TARIFF_PARTS",
    "TIME_FORMAT",
    "TIME_TOOLTIP_FORMAT",
    "band_donut_chart",
    "band_frame",
    "combination_chart",
    "combination_frame",
    "daily_temperature_chart",
    "daily_temperature_frame",
    "date_axis",
    "date_tooltip",
    "dispatch_schedule",
    "dr_daily_chart",
    "dr_daily_frame",
    "ess_day_chart",
    "ess_day_frame",
    "ess_target_chart",
    "ess_target_frame",
    "hourly_profile_chart",
    "hourly_profile_frame",
    "monthly_charge_chart",
    "monthly_charge_frame",
    "monthly_peak_chart",
    "monthly_peak_frame",
    "power_factor_day_chart",
    "power_factor_day_frame",
    "power_triangle_chart",
    "power_triangle_frame",
    "sensitivity_chart",
    "sensitivity_frame",
    "solar_annual_chart",
    "solar_annual_frame",
    "solar_capacity_table",
    "solar_curve_chart",
    "solar_curve_frame",
    "solar_day_chart",
    "solar_day_frame",
    "solar_saving_ratio",
    "surplus_daily_chart",
    "surplus_daily_frame",
    "tariff_delta_chart",
    "tariff_delta_frame",
    "tariff_option_chart",
    "tariff_option_frame",
    "tariff_option_long_frame",
    "temperature_mean_frame",
    "time_axis",
    "time_tooltip",
    "top_hour_chart",
    "top_hour_frame",
]

_HEIGHT = 260

# ===================================================================== 17세션 0절 · 스케일
#
# **차이가 큰 두 값을 한 축에 그리면 변화가 안 보인다.** 35억 위에서 5천만원이
# 움직이거나 5,000 kW 부하에 수백 kW 발전을 얹으면, 0 부터 시작하는 축에서는
# 두 값이 같은 자리에 겹친다. 규약 셋을 여기 모아 둔다.
#
#     ① 금액·전력 축은 **0 부터 시작하지 않는다** (문구는 적지 않는다 — 21세션 5절)
#     ② 절대값과 변화량은 **차트를 나눈다.** 한 축에 억과 백만을 같이 두지 않는다
#     ③ 두 선이 붙어 보이면 **그 사이를 색으로 채운다**

#: 값의 범위에 맞춰 축을 자른다. **축 제목에 그 사실을 적지 않는다** (21세션 5절)
#: — 눈금이 0 이 아닌 것은 축을 보면 안다.
#:
#: **막대는 자르지 않는다** (23세션 2절). 선·면은 두 값의 *간격*이 뜻이라 축을
#: 자르면 그 간격이 드러나지만, 막대는 *길이*가 곧 값이라 자르면 길이가 거짓말을
#: 한다. 개수를 세는 막대(상위 구간 수)와 부호를 가르는 막대(충전＋·방전−)는
#: 0 이 기준점 그 자체다.
_CUT_SCALE = alt.Scale(zero=False, nice=True)

# ===================================================================== 23세션 1절 · 범례
#
# **범례를 그림 바깥 오른쪽에 둔다.** 안쪽에 두면 값이 커질 때 선과 표식을 가리고,
# 흰 배경 상자를 깔아 두었더니 다크 모드에서 흰 바탕에 회색 글씨가 되어 읽히지
# 않았다 (17세션에 `fillColor="white"` 를 준 것이 원인이다).
#
#     ① 자리는 **바깥 오른쪽**. 그림이 그만큼 좁아지지만 가리는 것이 없다
#     ② **배경을 칠하지 않는다.** 테마 배경이 그대로 비쳐 두 모드에서 다 읽힌다
#     ③ 글자색을 고정하지 않는다 — 흰색·회색을 박아 두면 한쪽 모드가 깨진다
#
# **③ 을 글자 전부로 넓힌다** (35세션 1-2). 34세션의 도넛 부제(합계 사용량)가
# vega 기본색(검정)으로 나가 다크 모드에서 배경에 묻혔다. 규칙을 나눠 적는다.
#
#     · **중립색(흰·회·검)을 글자에 박지 않는다.** 한쪽 모드가 반드시 깨진다.
#       테마가 칠하게 두거나, 아예 Streamlit 텍스트로 뺀다
#     · **채색은 허용한다** — 그 글자가 어느 선의 것인지 색이 말해 주기 때문이다
#       (30세션 축 제목 규약). 다만 **두 바탕에서 다 읽히는 중간 명도**여야 한다.
#       35세션에 일 사용량 색을 `#08519c` → `#3182bd` 로 올린 것이 그 이유다 —
#       어두운 남색은 검은 바탕에서 선도 축 제목도 가라앉았다

#: 전 차트가 쓰는 범례. **기본은 이것 하나다** — 차트마다 달리 주면 또 갈라진다.
LEGEND = alt.Legend(
    orient="right",
    direction="vertical",
    offset=10,
    labelLimit=200,
    fillColor=None,
    strokeColor=None,
    padding=0,
)

#: **그림 안의 글자가 오른쪽으로 뻗는 차트만 아래에 둔다** (27세션 6절).
#:
#: 바깥 오른쪽 범례는 그림 오른쪽 **위**에서부터 쌓인다. 도형 옆에 설명을 직접
#: 적는 차트(전력삼각형)는 그 글자가 꼭짓점에서 오른쪽으로 뻗어 같은 자리를
#: 다툰다 — 규약이 적용되지 않은 것이 아니라, **적용된 규약이 이 그림에서만
#: 부딪힌다.** 위아래로 자리를 갈라 겹침을 없앤다.
#:
#: 나머지 셋(배경 없음·안쪽 금지·글자색 고정 금지)은 그대로다.
LEGEND_BELOW = alt.Legend(
    orient="bottom",
    direction="horizontal",
    offset=10,
    labelLimit=200,
    fillColor=None,
    strokeColor=None,
    padding=0,
)


def monthly_peak_chart(peak: PeakProfile, *, split: bool = True) -> alt.LayerChart:
    """월별 최대수요.

    Args:
        split: 관측 최대와 요금적용 대상 최대를 **따로 그릴지**. 둘이 같은 값이면
            막대 두 개가 겹쳐 보여 뜻이 없다 — 한 계열만 그린다 (13세션).
    """
    frame = monthly_peak_frame(peak)
    values = ["관측 최대(kW)", "요금적용 대상 최대(kW)"] if split else ["관측 최대(kW)"]
    long = frame.melt(
        id_vars=["월", "발생 시각"],
        value_vars=values,
        var_name="구분",
        value_name="kW",
    )
    bars = (
        alt.Chart(long)
        .mark_bar()
        .encode(
            x=alt.X("월:N", title=None, sort=None),
            y=alt.Y("kW:Q", title="최대수요 (kW)"),
            xOffset=alt.XOffset("구분:N"),
            color=alt.Color("구분:N", title=None, legend=LEGEND),
            tooltip=["월", "구분", alt.Tooltip("kW:Q", format=",.1f"), "발생 시각"],
        )
    )
    rule = (
        alt.Chart(pd.DataFrame({"요금적용전력": [peak.billing_demand_kw]}))
        .mark_rule(strokeDash=[6, 4], color="crimson")
        .encode(y="요금적용전력:Q", tooltip=[alt.Tooltip("요금적용전력:Q", format=",.1f")])
    )
    return (bars + rule).properties(height=_HEIGHT)


def top_hour_chart(peak: PeakProfile, *, split: bool = True) -> alt.Chart:
    """상위 구간의 시각 분포. 두 기준이 같으면 한 계열만 그린다 (13세션)."""
    frame = top_hour_frame(peak)
    if not split:
        frame = frame[["시각", "요금적용전력 대상"]]
    long = frame.melt(id_vars="시각", var_name="기준", value_name="구간 수")
    return (
        alt.Chart(long)
        .mark_bar()
        .encode(
            x=alt.X("시각:N", title=f"상위 {peak.top_n}구간 발생 시각 (검침 라벨)", sort=None),
            y=alt.Y("구간 수:Q", title="구간 수"),
            xOffset=alt.XOffset("기준:N"),
            color=alt.Color("기준:N", title=None, legend=LEGEND),
            tooltip=["시각", "기준", "구간 수"],
        )
        .properties(height=_HEIGHT)
    )


def hourly_profile_chart(peak: PeakProfile, *, season: str | None = None) -> alt.Chart:
    """시각별 평균 부하. ``season`` 을 주면 그 계절만 그린다 (30세션 5-1)."""
    return (
        alt.Chart(hourly_profile_frame(peak, season=season))
        .mark_line(point=True)
        .encode(
            x=alt.X("시각:N", title="시각", sort=None),
            y=alt.Y("평균 부하(kW):Q", title="평균 부하 (kW)", scale=_CUT_SCALE),
            tooltip=["시각", alt.Tooltip("평균 부하(kW):Q", format=",.0f")],
        )
        .properties(height=_HEIGHT)
    )


# ===================================================================== 33세션 1절 · 날짜 표기
#
# **vega 의 기본 로케일은 영어다.** 날짜 축을 그대로 두면 눈금에 ``May`` ·
# ``Apr 30`` 이 찍히고, 툴팁도 ``Apr 30, 2024`` 가 된다. vega-lite **spec 에는
# 로케일을 실을 수 없고** (vega-embed 옵션이라 streamlit 을 거쳐 넘길 길이 없다),
# 차트마다 ``format`` 을 적으면 다음에 새 차트가 또 영어로 나온다.
#
# **그래서 라벨 식을 한 벌만 만들고 날짜 축 전부가 그것을 쓴다.**
#
#     ① 눈금 단위에 따라 이름이 바뀐다 — 1월 1일은 「2024년」, 달의 첫날은
#        「5월」, 나머지는 「4월 30일」. 해가 바뀌는 자리가 축에 남아야
#        두 해에 걸친 자료에서 같은 「4월」 이 둘 생기지 않는다
#     ② 툴팁은 눈금과 달리 **언제나 온전한 날짜**를 적는다 (「2024년 4월 30일」)
#     ③ 하루 안을 그리는 축(시각)은 날짜가 아니라 **시:분**이다
#
# 새 차트를 만들 때는 :func:`date_axis` · :func:`date_tooltip` 을 쓴다.
# 시험이 ``:T`` 축에 이 규약이 붙었는지 훑는다.

#: 날짜 눈금 라벨. vega 표현식이라 눈금 단위마다 다른 이름을 낼 수 있다.
DATE_LABEL_EXPR = (
    "month(datum.value) === 0 && date(datum.value) === 1"
    " ? timeFormat(datum.value, '%Y년')"
    " : date(datum.value) === 1"
    " ? timeFormat(datum.value, '%-m월')"
    " : timeFormat(datum.value, '%-m월 %-d일')"
)

#: 날짜 툴팁. 눈금과 달리 **줄여 적지 않는다** — 짚어 본 그 날이 어느 해인지가
#: 툴팁에서까지 흐려지면 읽을 길이 없다.
DATE_FORMAT = "%Y년 %-m월 %-d일"

#: 하루 안을 그리는 축의 눈금·툴팁. 날짜는 그림 제목이 이미 적는다.
TIME_FORMAT = "%H:%M"

#: 하루 안 툴팁은 날짜를 함께 적는다 — 대표일이 바뀌면 그림 제목만 보고는
#: 어느 날의 곡선인지 헷갈린다.
TIME_TOOLTIP_FORMAT = "%-m월 %-d일 %H:%M"


def date_axis(**kwargs: object) -> alt.Axis:
    """날짜 축 눈금 (33세션 1절). ``labelExpr`` 를 여기서만 정한다."""
    return alt.Axis(labelExpr=DATE_LABEL_EXPR, **kwargs)  # type: ignore[arg-type]


def time_axis(**kwargs: object) -> alt.Axis:
    """하루 안 축 눈금 (33세션 1절)."""
    return alt.Axis(format=TIME_FORMAT, **kwargs)  # type: ignore[arg-type]


def date_tooltip(field: str = "날짜", **kwargs: object) -> alt.Tooltip:
    """날짜 툴팁 (33세션 1절)."""
    return alt.Tooltip(f"{field}:T", format=DATE_FORMAT, **kwargs)  # type: ignore[arg-type]


def time_tooltip(field: str = "시각", **kwargs: object) -> alt.Tooltip:
    """하루 안 툴팁 (33세션 1절)."""
    return alt.Tooltip(f"{field}:T", format=TIME_TOOLTIP_FORMAT, **kwargs)  # type: ignore[arg-type]


# ===================================================================== 30세션 4절 · 두 축
#
# **축이 둘이면 어느 선이 어느 축인지 라벨이 말해야 한다** (17세션 0절의 연장).
# 17세션은 축이 다른 값을 한 축에 얹지 말라고 했고, 그래서 여태 두 축짜리 그림을
# 두지 않았다. 기온은 예외다 — 사용량과 **함께 봐야** 관계가 보이는데 단위가
# 아예 다르다. 그림을 나누면 눈이 두 그림을 오가며 날짜를 맞춰야 한다.
#
#     ① 축 제목에 단위를 적고, **범례 이름에 어느 쪽 축인지 적는다**
#     ② 축 제목 색을 선 색과 맞춘다 — 라벨을 못 읽어도 색으로 이어진다
#     ③ 둘 다 0 에서 시작하지 않는다. 기온은 음수가 나오고, 사용량은 변화가 뜻이다
_LOAD_COLOR = "#3182bd"
_TEMP_COLOR = "#d95f0e"
_LOAD_SERIES = "일 사용량 (왼쪽 축)"
_TEMP_SERIES = "일평균 기온 (오른쪽 축)"

# ===================================================================== 33세션 2절 · 층과 축
#
# **한 층 안에서 축은 하나로 합쳐진다.** 32세션에 기준선·라벨을 기온 선과 같은
# 층에 묶으면서 그 둘에 ``axis=None`` 을 주었더니, vega 가 층의 y 축을 합칠 때
# **null 을 이겨 오른쪽 눈금이 통째로 사라졌다** — 기온 곡선은 그려지는데
# 범위와 단위(℃)를 읽을 수 없는 그림이 됐다.
#
#     세 층이 **똑같은 축 정의**를 쓰게 한다. 합칠 것이 하나뿐이면 다툴 일이 없다.
#
# 필드 이름은 층마다 다르다 (곡선은 일별 값, 기준선은 평균). 축 제목은 하나이므로
# 합쳐도 어긋나지 않는다.
_TEMP_AXIS = alt.Axis(orient="right", titleColor=_TEMP_COLOR)


def _temp_y(field: str) -> alt.Y:
    """기온 축 하나. **층 셋이 이것을 그대로 쓴다** (33세션 2절)."""
    return alt.Y(
        field,
        type="quantitative",
        title="일평균 기온 (℃)",
        scale=_CUT_SCALE,
        axis=_TEMP_AXIS,
    )


def daily_temperature_chart(usage: UsageData, temperature: pd.Series) -> alt.LayerChart:
    """연간 일별 사용량과 일평균 기온 (30세션 4절).

    **냉난방이 부하의 얼마를 차지하는지**를 눈으로 재는 그림이다. 여름·겨울에
    두 선이 함께 솟으면 냉난방 부하가 크고, 기온이 오르내려도 사용량이 평평하면
    공정 부하다 — 태양광·ESS 판단이 갈리는 자리다.
    """
    frame = daily_temperature_frame(usage, temperature)
    frame = frame.assign(계열=_LOAD_SERIES, 기온계열=_TEMP_SERIES)
    scale = alt.Scale(domain=[_LOAD_SERIES, _TEMP_SERIES], range=[_LOAD_COLOR, _TEMP_COLOR])
    base = alt.Chart(frame).encode(x=alt.X("날짜:T", title=None, axis=date_axis()))
    load = base.mark_line(strokeWidth=1).encode(
        y=alt.Y(
            "사용량(kWh):Q",
            title="일 사용량 (kWh)",
            scale=_CUT_SCALE,
            axis=alt.Axis(titleColor=_LOAD_COLOR),
        ),
        color=alt.Color("계열:N", title=None, scale=scale, legend=LEGEND),
        tooltip=[
            date_tooltip(),
            alt.Tooltip("사용량(kWh):Q", format=",.0f"),
            alt.Tooltip("일평균 기온(℃):Q", format=",.1f"),
        ],
    )
    temp = base.mark_line(strokeWidth=1).encode(
        y=_temp_y("일평균 기온(℃)"),
        color=alt.Color("기온계열:N", title=None, scale=scale, legend=LEGEND),
        tooltip=[
            date_tooltip(),
            alt.Tooltip("사용량(kWh):Q", format=",.0f"),
            alt.Tooltip("일평균 기온(℃):Q", format=",.1f"),
        ],
    )
    # ===================================== 32세션 1절 · 평균 기준선
    #
    # **여름 고온·겨울 저온이 부하를 미는 크기는 평균과의 거리로 읽힌다.** 선이
    # 없으면 곡선이 어느 쪽으로 얼마나 벗어난 것인지 눈금을 세어야 했다.
    #
    #     ① 값을 **선 위 라벨**로 적는다 — 범례를 늘리지 않는다 (23세션 1절)
    #     ② 이름은 관측 기간이 정한다 (「연평균」 / 「기간 평균」)
    #     ③ 기온 축에 얹으므로 **기온 층 안에서 축을 나눠 쓴다** — 바깥 층에
    #        따로 두면 ``resolve_scale(y="independent")`` 가 이 선에도 제 축을
    #        만들어 주어 곡선과 다른 높이에 그린다
    mean_frame = temperature_mean_frame(frame)
    mean_base = alt.Chart(mean_frame)
    mean_rule = mean_base.mark_rule(
        color=_TEMP_COLOR, strokeDash=[6, 4], strokeWidth=1, opacity=0.7
    ).encode(y=_temp_y("평균 기온(℃)"))
    mean_text = mean_base.mark_text(
        align="left", baseline="bottom", dx=4, dy=-3, color=_TEMP_COLOR, fontSize=11
    ).encode(
        x=alt.X("날짜:T", title=None, axis=date_axis()),
        y=_temp_y("평균 기온(℃)"),
        text=alt.Text("기준선:N"),
    )
    # 기온·기준선·라벨을 한 층으로 묶어 축 하나를 함께 쓴다.
    right = alt.layer(temp, mean_rule, mean_text)
    return alt.layer(load, right).resolve_scale(y="independent").properties(height=300)


# 계시별 시간대 색 — **1단계의 두 그림이 같은 색을 쓴다** (27세션 3-2). 파랑이
# 짙어질수록 단가가 높은 시간대다. 기본요금은 사용량과 무관한 몫이라 회색으로
# 갈라 둔다.
_BAND_RANGE: dict[str, str] = {"경부하": "#c6dbef", "중간부하": "#6baed6", "최대부하": "#08519c"}
_BASE_FEE_COLOR = "#737373"


def _band_scale(*, with_base_fee: bool = False) -> alt.Scale:
    colors = ({"기본요금": _BASE_FEE_COLOR} | _BAND_RANGE) if with_base_fee else _BAND_RANGE
    return alt.Scale(domain=list(colors), range=list(colors.values()))


# ===================================================================== 32세션 2절 · 막대 두께
#
# **막대 폭을 그림에 못박는다.** 폭을 적지 않으면 vega-lite 가 칸 하나를 기본
# 20px 로 잡고, 그 85%(streamlit 테마)인 **17px** 짜리 실오라기가 열두 개
# 그려진다 — 누적 막대는 조각이 넷이라 이 폭에서는 경부하 조각이 선으로 보인다.
#
#     ① 칸을 **60px** 로 잡는다 (기본 20px 의 세 배). 막대는 그 85% 인 51px
#     ② 막대 사이 간격도 함께 세 배가 된다 (3px → 9px) — 비율을 손대지 않는다
#     ③ 달이 많으면 칸을 줄여 **가로 스크롤을 만들지 않는다** (아래 상한)
#
# 화면 폭에 맞춰 늘어나는 경로에서는 vega 가 이 값을 화면 폭으로 덮어쓴다.
# 그때는 칸이 더 넓어지므로 이 상수가 **하한**으로 작동한다.
MONTH_BAR_STEP = 60

#: 그림 하나가 차지할 가로 상한 (px). 자료가 여러 해면 달 수가 늘어 60px × N 이
#: 화면을 넘는다 — 넘기느니 칸을 줄인다.
_MONTH_CHART_MAX_WIDTH = 1_440

#: 칸의 하한. 여기까지 줄여도 안 들어가면 그때는 스크롤이 낫다.
_MONTH_BAR_MIN_STEP = 20


def _month_step(months: int) -> int:
    """달 수에 맞춘 칸 폭 (32세션 2절). 열두 달이면 그대로 :data:`MONTH_BAR_STEP`."""
    if months <= 0:
        return MONTH_BAR_STEP
    fitted = _MONTH_CHART_MAX_WIDTH // months
    return int(max(_MONTH_BAR_MIN_STEP, min(MONTH_BAR_STEP, fitted)))


def monthly_charge_chart(structure: ChargeStructure) -> alt.Chart:
    """월별 요금 구성 — 기본요금 + 계시별 전력량요금 **누적 막대** (27세션 3-2).

    한 달치 청구액이 어떻게 나뉘는지, 그리고 **달마다 무엇이 달라지는지**를 한
    그림에서 본다. 기본요금이 같은 높이로 이어지는 것 자체가 요금적용전력
    12개월 규칙의 모습이다.

    **막대는 자르지 않는다** (17세션 0절 · 23세션 2절). 길이가 곧 금액이다.

    **칸 폭을 못박는다** (32세션 2절). 위 「막대 두께」 주석 참조.
    """
    frame = monthly_charge_frame(structure)
    step = _month_step(int(frame["월"].nunique()))
    return (
        alt.Chart(frame)
        .mark_bar()
        .encode(
            x=alt.X("월:N", title=None, sort=None),
            y=alt.Y("원:Q", title="요금 (원)", stack="zero"),
            color=alt.Color(
                "구분:N",
                title=None,
                sort=list(MONTHLY_CHARGE_PARTS),
                scale=_band_scale(with_base_fee=True),
                legend=LEGEND,
            ),
            # **쌓는 순서를 색 순서에 묶는다.** 주지 않으면 달마다 순서가 달라져
            # 밑단이 흔들린다.
            order=alt.Order("순서:Q", sort="ascending"),
            tooltip=[
                "월",
                "구분",
                alt.Tooltip("원:Q", format=",.0f"),
                alt.Tooltip("합계(원):Q", format=",.0f"),
            ],
        )
        .properties(width=alt.Step(step), height=300)
    )


# ===================================================================== 33세션 3절 · 원 넷
#
# **비중을 견주는 그림은 원이 낫다** (33세션 → 34세션에 자리를 옮겼다).
# 33세션은 이것을 **월별 요금 구성**에 붙였는데 지시가 잘못 전달된 것이었고,
# 34세션에 제자리인 **계시별 사용량 구성**으로 옮겼다. 요금 구성은 달마다의
# 높이를 비교하는 그림이라 막대가 맞다 (위 「막대 두께」).
#
#     ① 갈래는 계절 탭과 같다 — 전체 · 봄·가을 · 여름 · 겨울. **탭이 아니라
#        한 줄에 넷**이다. 갈아 끼우면 계절을 나란히 볼 수 없다
#     ② 조각마다 **이름과 비중을 함께 적는다.** 원이 넷이라 범례를 달면 같은
#        이름 넷이 네 번 실린다 — 조각 위 라벨이면 한 번이다
#     ③ 합계는 그림 제목이 적는다 (원 아래 자리는 라벨이 쓴다)
#
# **17세션 축 규약은 막대에 대한 것이다.** 원에는 축이 없어 「자르지 않는다」 가
# 성립하지 않는다 — 규약을 고칠 일이 아니라 **해당 없음**이다. 대신 각이 곧
# 비중이라 ``theta`` 를 쌓아 같은 뜻을 지킨다.

# ===================================================================== 35세션 1절 · 도넛 세 가지
#
# **제목을 vega 에 두지 않는다.** 34세션에 `TitleParams` 로 계절 이름과 합계를
# 얹었더니 화면에서 **제목 윗부분이 잘렸다** — 층(layer) 차트의 제목은 높이
# 계산에 들어가지 않는 경우가 있다. 그리고 부제는 vega 기본색(검정)이라
# **다크 모드에서 배경에 묻혔다.**
#
#     ① 제목·합계는 **Streamlit 텍스트로 그림 위에 둔다.** 잘리지 않고,
#        글자색도 테마를 따른다 (23세션 3항의 연장)
#     ② 도넛을 줄이고 라벨을 더 밖으로 민다 — 좁은 칸에서 라벨이 조각을 파고들었다
#
# 넷이 한 줄에 들어가는 것은 그대로다.

#: 도넛의 안·바깥 반지름 (px). 안을 비우면 조각의 각을 견주기 쉽다.
#:
#: **넷이 한 줄이라 칸 하나가 좁다** (전체 폭의 4분의 1). 34세션의 78px 은 그
#: 칸에서 라벨이 놓일 자리를 남기지 않았다.
_DONUT_INNER = 34
_DONUT_OUTER = 62

#: 조각 라벨을 놓을 반지름. **바깥보다 24px 밖**이라 글자가 조각을 파고들지 않는다.
_DONUT_LABEL_RADIUS = _DONUT_OUTER + 24

#: 도넛 하나의 높이. 라벨이 바깥으로 뻗으므로 반지름의 두 배보다 넉넉해야 한다.
#: 제목이 그림 밖으로 나갔으므로 34세션(230)보다 낮아도 된다.
_DONUT_HEIGHT = 200


def _donut(
    frame: pd.DataFrame,
    *,
    value: str,
    group: str,
    order: Sequence[str],
    scale: alt.Scale,
    tooltip: Sequence[alt.Tooltip | str],
) -> alt.LayerChart | alt.FacetChart:
    """도넛 한 장. **비중을 견주는 그림은 전부 이것을 쓴다** (33세션 3절).

    **제목을 담지 않는다** (35세션 1절). 계절 이름과 합계는 부르는 쪽이
    그림 **위에 Streamlit 텍스트로** 적는다 — vega 제목은 잘리고 색이 고정된다.

    Args:
        value: 각을 정하는 열.
        group: 조각을 가르는 열. ``라벨`` 열이 그 이름과 비중을 함께 적는다.
        order: 조각 순서. 주지 않으면 자료 순서가 달라질 때 색이 돈다.
    """
    base = alt.Chart(frame).encode(
        theta=alt.Theta(f"{value}:Q", stack=True),
        order=alt.Order("순서:Q", sort="ascending"),
        color=alt.Color(
            f"{group}:N",
            title=None,
            sort=list(order),
            scale=scale,
            # **범례를 달지 않는다** — 조각마다 이름이 적혀 있다.
            legend=None,
        ),
        tooltip=list(tooltip),
    )
    arc = base.mark_arc(innerRadius=_DONUT_INNER, outerRadius=_DONUT_OUTER)
    labels = base.mark_text(radius=_DONUT_LABEL_RADIUS, fontSize=11).encode(text=alt.Text("라벨:N"))
    return alt.layer(arc, labels).properties(height=_DONUT_HEIGHT)


def band_donut_chart(
    structure: ChargeStructure, *, season: str | None = None
) -> alt.LayerChart | alt.FacetChart:
    """계시별 사용량 구성 **도넛** (33세션 3절 · 34세션 1절).

    한 계절의 사용량이 **경부하·중간부하·최대부하**로 어떻게 갈리는지 낸다.
    계절을 나란히 놓으면 여름·겨울에 최대부하 조각이 두꺼워지는 것이 그대로
    보인다 — 그 조각을 옮기거나 깎는 수단의 값어치가 거기서 갈린다.

    **비중은 그 계절 안에서 다시 잰다** (30세션 5-2). 전체 대비로 두면 세 계절의
    원이 각각 3분의 1 만 칠해져 무엇의 구성인지 알 수 없다.

    **계절 이름과 합계는 그림 위에 있다** (35세션 1절). 부르는 쪽이 적는다.

    Args:
        season: ``None`` 이면 전 기간.
    """
    return _donut(
        band_frame(structure, season=season),
        value="사용량(kWh)",
        group="시간대",
        order=list(BAND_LABELS.values()),
        scale=_band_scale(),
        tooltip=[
            "시간대",
            alt.Tooltip("사용량(kWh):Q", format=",.0f"),
            alt.Tooltip("비중:Q", format=".1%"),
        ],
    )


def solar_curve_chart(
    curve: SolarCurve, *, verdict: CapacityVerdict | None = None
) -> alt.LayerChart | alt.FacetChart:
    """용량별 절감액 곡선. **최적 지점에 표식을 찍는다** (15세션 1-3).

    기본은 이 곡선을 감춘다 — 곡선이 단조롭게 좋아지기만 하면 한 줄 판정으로
    충분하다. 최적이 면적 상한보다 작을 때만 펼쳐 최소점을 보인다.
    """
    long = solar_curve_frame(curve).melt(
        id_vars="용량(kWp)",
        value_vars=["기본요금 절감(원)", "전력량요금 절감(원)", "총 절감액(원)"],
        var_name="구분",
        value_name="원",
    )
    lines = (
        alt.Chart(long)
        .mark_line(point=True)
        .encode(
            x=alt.X("용량(kWp):Q", title="설치 용량 (kWp)"),
            y=alt.Y("원:Q", title="절감액 (원)"),
            color=alt.Color("구분:N", title=None, legend=LEGEND),
            tooltip=[
                alt.Tooltip("용량(kWp):Q", format=",.0f"),
                "구분",
                alt.Tooltip("원:Q", format=",.0f"),
            ],
        )
    )
    layers: list[alt.Chart] = [lines]
    if verdict is not None and verdict.best is not None:
        best = verdict.best
        mark = pd.DataFrame(
            {
                "용량(kWp)": [best.capacity_kwp],
                "원": [best.total_saving_won],
                "설명": [f"{verdict.basis} 최적 {best.capacity_kwp:,.0f} kWp"],
            }
        )
        layers.append(
            alt.Chart(mark)
            .mark_point(size=170, filled=True, color="crimson")
            .encode(x="용량(kWp):Q", y="원:Q", tooltip=["설명"])
        )
        layers.append(
            alt.Chart(mark)
            .mark_text(dy=-14, color="crimson", fontWeight="bold")
            .encode(x="용량(kWp):Q", y="원:Q", text="설명:N")
        )
    return alt.layer(*layers).properties(height=300)


def ess_target_chart(curve: EssTargetCurve) -> alt.LayerChart | alt.FacetChart:
    """ESS 회수기간 곡선 — **축 하나·선 하나** (26세션 1절).

    23세션까지는 한 그림에 축이 셋이었다 (좌 회수기간·우 정격 용량, 그리고 눈금이
    두 벌). 범례가 없어 무엇이 무엇인지 알 수 없었고, x 가 3,700~20,000 kW 라
    최적 둘레가 뭉개졌다. **정보를 덜어내는 것이 이 그림의 목적이다.**

        x   ESS 정격 용량 (kWh) — 사용자가 실제로 사야 할 것
        y   회수기간 (년)
        표식 최소 지점 하나

    보조 축도 범례도 두지 않는다. 범위는 :data:`~kwise.report.frames.CAPACITY_WINDOW`
    가 최적 둘레로 좁힌다. 회수기간은 기본요금 절감만 본 개략치이므로 **고르는
    지표**로만 읽는다 — 결론 금액은 카드가 낸다 (18세션 1절).
    """
    frame = ess_target_frame(curve)
    line = (
        alt.Chart(frame)
        .mark_line(color="#08519c", strokeWidth=2)
        .encode(
            x=alt.X("정격 용량(kWh):Q", title="ESS 정격 용량 (kWh)", scale=alt.Scale(zero=False)),
            y=alt.Y("회수기간(년):Q", title="회수기간 (년)", scale=alt.Scale(zero=False)),
            tooltip=[
                alt.Tooltip("정격 용량(kWh):Q", format=",.0f"),
                alt.Tooltip("목표 요금적용전력(kW):Q", format=",.0f"),
                alt.Tooltip("저감량(kW):Q", format=",.0f"),
                alt.Tooltip("회수기간(년):Q", format=",.1f"),
            ],
        )
    )
    layers: list[alt.Chart] = [line]
    if curve.best is not None:
        best = pd.DataFrame(
            {
                "정격 용량(kWh)": [curve.best.nameplate_capacity_kwh],
                "회수기간(년)": [curve.best.payback_years],
                "사양": [curve.best.spec_label],
            }
        )
        layers.append(
            alt.Chart(best)
            .mark_point(size=140, filled=True, color="crimson")
            .encode(x="정격 용량(kWh):Q", y="회수기간(년):Q", tooltip=["사양"])
        )
    return alt.layer(*layers).properties(height=300)


def combination_chart(comparison: ComparisonResult) -> alt.Chart:
    """조합별 절감액.

    **확실성으로 색을 나누던 것을 걷어냈다** (28세션 4절). 등급이 무엇에 대한
    것인지 이름에 없어 색이 무엇을 가리키는지도 알 수 없었다. 이 그림이 말하는
    것은 하나다 — **수단을 쌓을수록 절감액이 어떻게 늘어나는가.** 등급은
    Excel·Word 에 남는다.
    """
    frame = combination_frame(comparison)
    return (
        alt.Chart(frame)
        .mark_bar(color="#08519c")
        .encode(
            y=alt.Y("조합:N", title=None, sort=list(frame["조합"])),
            x=alt.X("절감액(원):Q", title="기간 절감액 (원)"),
            tooltip=["조합", alt.Tooltip("절감액(원):Q", format=",.0f")],
        )
        .properties(height=max(_HEIGHT, 44 * len(frame)))
    )


def sensitivity_chart(ranges: tuple[SensitivityRange, ...]) -> alt.LayerChart:
    frame = sensitivity_frame(ranges)
    base = alt.Chart(frame).encode(y=alt.Y("지표:N", title=None, sort=list(frame["지표"])))
    span = base.mark_rule(size=6, color="#9ecae1").encode(
        x=alt.X("하한:Q", title="범위"), x2="상한:Q", tooltip=["지표", "범위"]
    )
    point = base.mark_point(size=90, filled=True, color="#08519c").encode(
        x="기준값:Q", tooltip=["지표", "범위"]
    )
    return (span + point).properties(height=max(120, 38 * len(frame)))


# ===================================================================== 15세션 · 2단계 그래프


_DAY_TYPE_COLORS = alt.Scale(
    domain=["평일", "토요일", "일요일", "공휴일"],
    range=["#08519c", "#6baed6", "#fd8d3c", "#d94801"],
)
# 계시별 시간대 **배경 띠 색은 없앴다** (26세션 2-1). ESS 하루 그림이 유일한
# 쓰임이었는데, 확대한 창이 한 시간대 안에 들어가 그림 전체가 주황 한 색이 되고
# 범례에는 셋이 남아 그림에 없는 것을 가리켰다.


def tariff_option_chart(switch: TariffSwitchResult) -> alt.Chart:
    """요금제별 기본·전력량·합계 **그룹 막대** (17세션 1-2).

    쌓지 않고 나란히 세운다. 누적은 합계만 보이고 **기본요금끼리·전력량요금끼리
    견줄 수가 없는데**, 선택요금은 바로 그 둘을 맞바꾸는 제도다.

    **축을 0 부터 시작하지 않는다** (17세션 0절). 35억 위에서 5천만원이 움직이는
    것을 0 부터 그리면 막대 셋이 같은 높이로 보인다. 얼마나 줄어드는지는
    :func:`tariff_delta_chart` 가 따로 낸다.
    """
    long = tariff_option_long_frame(switch)
    order = list(tariff_option_frame(switch)["요금제"])
    return (
        alt.Chart(long)
        .mark_bar()
        .encode(
            x=alt.X("요금제:N", title=None, sort=order),
            xOffset=alt.XOffset("구분:N", sort=list(TARIFF_PARTS)),
            y=alt.Y("원:Q", title="요금 (원)", scale=_CUT_SCALE),
            color=alt.Color(
                "구분:N",
                title=None,
                sort=list(TARIFF_PARTS),
                scale=alt.Scale(domain=list(TARIFF_PARTS), range=["#6baed6", "#fd8d3c", "#31a354"]),
                legend=LEGEND,
            ),
            tooltip=["요금제", "구분", alt.Tooltip("원:Q", format=",.0f")],
        )
        .properties(height=300)
    )


def tariff_delta_chart(switch: TariffSwitchResult) -> alt.LayerChart:
    """**현행 대비 차액만** 그리는 막대 (17세션 1-3).

    0 을 기준으로 좌우로 뻗는다 — 절감은 왼쪽, 증가는 오른쪽이다. 절대 금액을
    지우고 변화만 남기면 "얼마나 줄어드는가" 가 한눈에 읽힌다.

    **오른쪽에 글자 자리를 비워 둔다** (27세션 4-1). 막대 끝 글자는 왼쪽 정렬로
    오른쪽으로 뻗는데, 갈아탈 요금제가 모두 절감이면 0 이 그림의 오른쪽 끝이라
    「현행」 글자가 그림 밖으로 나가 바깥 오른쪽 범례 위에 겹쳤다. 축 범위를
    글자 폭만큼 넓혀 **그림을 왼쪽으로 줄인다** — 글자가 그림 안에 남는다.
    """
    frame = tariff_delta_frame(switch)
    labelled = frame.assign(
        설명=[
            (money.won_short(value, reason="—") if abs(value) >= 1 else "현행")
            + (f" · {mark}" if mark else "")
            for value, mark in zip(frame["현행 대비(원)"], frame["표식"], strict=True)
        ]
    )
    order = list(frame["요금제"])
    values = [*(float(value) for value in frame["현행 대비(원)"]), 0.0]
    low, high = min(values), max(values)
    span = (high - low) or 1.0
    # 왼쪽은 막대만, 오른쪽은 **막대 + 글자**가 들어간다.
    domain = [low - span * 0.05, high + span * 0.45]
    bars = (
        alt.Chart(labelled)
        .mark_bar()
        .encode(
            y=alt.Y("요금제:N", title=None, sort=order),
            x=alt.X(
                "현행 대비(원):Q",
                title="현행 대비 (원) — 왼쪽이 절감",
                scale=alt.Scale(domain=domain, nice=False),
            ),
            color=alt.Color(
                "방향:N",
                title=None,
                scale=alt.Scale(
                    domain=["절감", "현행", "증가"], range=["#31a354", "#bdbdbd", "#de2d26"]
                ),
                legend=LEGEND,
            ),
            tooltip=["요금제", alt.Tooltip("현행 대비(원):Q", format=",.0f")],
        )
    )
    labels = (
        alt.Chart(labelled)
        .mark_text(align="left", dx=6, fontWeight="bold")
        .encode(y=alt.Y("요금제:N", sort=order), x="현행 대비(원):Q", text="설명:N")
    )
    zero = alt.Chart(pd.DataFrame({"기준": [0.0]})).mark_rule(color="#525252").encode(x="기준:Q")
    return (zero + bars + labels).properties(height=140)


def dr_daily_chart(profile: DrProfile) -> alt.LayerChart | alt.FacetChart:
    """연간 일별 운영시간대 평균 부하 (15세션 2-2).

    **기준선 근처로 내려온 평일이 감축 가능일이다.** 주말·공휴일 평균을 가로선으로
    깔고 저부하 평일에 표식을 찍으면 그 사실이 그림 하나로 읽힌다.
    """
    frame = dr_daily_frame(profile)
    points = (
        alt.Chart(frame)
        .mark_circle(size=26, opacity=0.75)
        .encode(
            x=alt.X("날짜:T", title="날짜", axis=date_axis()),
            y=alt.Y("운영시간대 평균(kW):Q", title="운영 시간대 평균 부하 (kW)", scale=_CUT_SCALE),
            color=alt.Color("구분:N", title=None, scale=_DAY_TYPE_COLORS, legend=LEGEND),
            tooltip=[
                date_tooltip(),
                "구분",
                alt.Tooltip("운영시간대 평균(kW):Q", format=",.0f"),
                "저부하 평일",
            ],
        )
    )
    layers: list[alt.Chart] = [points]
    rules = pd.DataFrame(
        [
            {"값": value, "이름": name}
            for value, name in (
                (profile.weekend_baseline_kw, "주말·공휴일 평균"),
                (profile.low_load_threshold_kw, "저부하 문턱"),
            )
            if value is not None
        ]
    )
    if not rules.empty:
        layers.append(
            alt.Chart(rules)
            .mark_rule(strokeDash=[6, 4], color="crimson")
            .encode(y="값:Q", tooltip=["이름", alt.Tooltip("값:Q", format=",.0f")])
        )
    marked = frame[frame["저부하 평일"]] if len(frame) else frame
    if len(marked):
        layers.append(
            alt.Chart(marked)
            .mark_point(size=170, shape="triangle-down", filled=True, color="crimson")
            .encode(
                x=alt.X("날짜:T", axis=date_axis()),
                y="운영시간대 평균(kW):Q",
                tooltip=[
                    date_tooltip(),
                    alt.Tooltip("운영시간대 평균(kW):Q", format=",.0f"),
                ],
            )
        )
    return alt.layer(*layers).properties(height=300)


def power_triangle_chart(result: PowerFactorResult) -> alt.LayerChart:
    """전력삼각형 — 개선 전후 (15세션 2-3).

    **각이 좁아지는 모습**이 이 그림의 전부다. 유효전력을 1 로 두고 무효전력만
    줄어드는 것을 겹쳐 보인다.

    **범례만 아래에 둔다** (27세션 6절). 꼭짓점 옆의 설명 글자가 오른쪽으로
    뻗어 바깥 오른쪽 범례와 같은 자리를 다툰다 — :data:`LEGEND_BELOW` 참조.
    """
    frame = power_triangle_frame(result)
    lines = pd.DataFrame(
        [
            {"구분": row["구분"], "순서": order, "유효전력": x, "무효전력": y}
            for _, row in frame.iterrows()
            for order, (x, y) in enumerate(
                ((0.0, 0.0), (row["유효전력"], 0.0), (row["유효전력"], row["무효전력"]), (0.0, 0.0))
            )
        ]
    )
    shape = (
        alt.Chart(lines)
        .mark_line(point=False)
        .encode(
            x=alt.X("유효전력:Q", title="유효전력 (기준 1)"),
            y=alt.Y("무효전력:Q", title="무효전력"),
            color=alt.Color("구분:N", title=None, legend=LEGEND_BELOW),
            order="순서:Q",
            tooltip=["구분"],
        )
    )
    # **각도와 역률을 도형 옆에 직접 적는다** (17세션 2절). 범례에 기대면 값을
    # 바꿔 보는 동안 어느 선이 어느 쪽인지 매번 다시 찾아야 한다.
    marked = frame.assign(
        설명=[
            f"{row['구분']} — 역률 {row['역률(%)']:.1f}% · {row['각도(도)']:.1f}°"
            for _, row in frame.iterrows()
        ],
        각도라벨=[f"{row['각도(도)']:.1f}°" for _, row in frame.iterrows()],
        각도y=[row["무효전력"] * 0.28 for _, row in frame.iterrows()],
    )
    labels = (
        alt.Chart(marked)
        .mark_text(dx=6, align="left", fontWeight="bold")
        .encode(
            x="유효전력:Q",
            y="무효전력:Q",
            text="설명:N",
            color=alt.Color("구분:N", title=None, legend=LEGEND_BELOW),
        )
    )
    # 원점 쪽 각도 표기 — **각이 좁아지는 모습**이 이 그림의 전부다.
    angles = (
        alt.Chart(marked)
        .mark_text(dx=4, align="left", fontSize=11)
        .encode(
            x=alt.value(46),
            y=alt.Y("각도y:Q"),
            text="각도라벨:N",
            color=alt.Color("구분:N", title=None, legend=LEGEND_BELOW),
        )
    )
    return (shape + labels + angles).properties(height=280)


def power_factor_day_chart(
    usage: UsageData, day: RepresentativeDay, *, current_pct: float, target_pct: float
) -> alt.LayerChart:
    """대표일 부하와 역률 판정 창 (15세션 2-3)."""
    frame = power_factor_day_frame(usage, day.date, current_pct=current_pct, target_pct=target_pct)
    load = (
        alt.Chart(frame)
        .mark_line(color="#08519c")
        .encode(
            x=alt.X("시각:T", title=f"{day.title} · 15분 부하", axis=time_axis()),
            y=alt.Y("부하(kW):Q", title="부하 (kW)", scale=_CUT_SCALE),
            tooltip=[time_tooltip(), alt.Tooltip("부하(kW):Q", format=",.0f"), "구간"],
        )
    )
    window = (
        alt.Chart(frame[frame["구간"].str.startswith("주간")])
        .mark_point(size=18, opacity=0.35, color="#fd8d3c")
        .encode(x=alt.X("시각:T", axis=time_axis()), y="부하(kW):Q", tooltip=["구간"])
    )
    return (load + window).properties(height=280)


def solar_saving_ratio(usage: UsageData, generation_kw: pd.Series) -> float | None:
    """자가소비로 줄어든 **계통 수전 비율**. 화면 문구가 쓴다 (17세션 3-4)."""
    frame = solar_annual_frame(usage, generation_kw)
    total = float(frame["사용량(kWh)"].sum())
    if total <= 0:
        return None
    return float(frame["자가소비(kWh)"].sum()) / total


def solar_annual_chart(usage: UsageData, generation_kw: pd.Series) -> alt.Chart:
    """연간 **일별 발전량** (23세션 5절).

    17세션에는 사용량·계통 수전·자가소비·잉여 넷을 한 그림에 얹고 「사용량이
    줄어드는 모습」을 주인공으로 삼았다. 그런데 사용량이 일 60 MWh 대인데
    발전량은 3 MWh 대라, 같은 축에서 **발전량이 바닥에 눌려 보이지 않았다.**
    호버도 날짜와 수전량을 내놓아 정작 발전량을 읽을 수 없었다.

    **한 그림은 한 가지만 말한다.** 여기서는 일별 발전량이고, 사용량이 얼마나
    줄었는지는 카드의 절감액과 대표일 곡선이 낸다.
    """
    # **쓰는 열만 싣는다.** altair 는 프레임을 통째로 사양에 박아 넣는다 — 안 그리는
    # 열까지 실으면 페이로드만 커지고, 그 열이 화면에 없다는 것을 확인할 길도 없다.
    frame = solar_annual_frame(usage, generation_kw)[["날짜", "발전량(kWh)"]]
    return (
        alt.Chart(frame)
        .mark_area(opacity=0.85, color="#31a354", line={"color": "#238b45"})
        .encode(
            x=alt.X("날짜:T", title="날짜", axis=date_axis()),
            y=alt.Y("발전량(kWh):Q", title="일별 발전량 (kWh)"),
            tooltip=[
                date_tooltip(),
                alt.Tooltip("발전량(kWh):Q", format=",.0f", title="발전량(kWh)"),
            ],
        )
        .properties(height=300)
    )


def solar_day_chart(
    usage: UsageData,
    generation_kw: pd.Series,
    day: RepresentativeDay,
    *,
    zoom: bool = False,
) -> alt.LayerChart | alt.FacetChart:
    """대표일의 원부하·순부하와 **그 사이 저감분** (17세션 3-5).

    5,000 kW 부하에 수백 kW 발전을 얹으면 0 부터 시작하는 축에서 두 선이 붙어
    보인다. 셋을 바꿨다.

        ① **축을 0 부터 시작하지 않는다** — 두 선의 간격이 벌어진다
        ② **선 사이를 색으로 채운다** — 그 면적이 곧 저감이다
        ③ 발전량 선을 뺐다 — 축이 다른 값을 같은 축에 얹으면 스케일이 다시 뭉갠다

    Args:
        zoom: 피크 시각 앞뒤 :data:`PEAK_ZOOM_HOURS` 시간만 그린다. 하루 전체는
            간격이 좁아 보이므로 확대본을 곁들인다.
    """
    frame = solar_day_frame(usage, generation_kw, day.date)
    title = f"{day.title} · 15분"
    if len(frame) and zoom:
        frame = peak_window(frame)
        title = f"{day.title} · 피크 앞뒤 {PEAK_ZOOM_HOURS}시간"
    if frame.empty:
        blank = alt.Chart(pd.DataFrame({"시각": [], "kW": []})).mark_line()
        return alt.layer(blank, blank).properties(height=280)

    band = (
        alt.Chart(frame.assign(구분="저감분"))
        .mark_area(opacity=0.75)
        .encode(
            x=alt.X("시각:T", title=title, axis=time_axis()),
            y=alt.Y("순부하(kW):Q", title="출력 (kW)", scale=_CUT_SCALE),
            y2=alt.Y2("원부하(kW)"),
            color=alt.Color(
                "구분:N",
                title=None,
                scale=alt.Scale(domain=["저감분"], range=["#31a354"]),
                legend=LEGEND,
            ),
            tooltip=[
                time_tooltip(),
                alt.Tooltip("원부하(kW):Q", format=",.0f"),
                alt.Tooltip("순부하(kW):Q", format=",.0f"),
                alt.Tooltip("발전량(kW):Q", format=",.0f"),
            ],
        )
    )
    long = frame.melt(
        id_vars="시각", value_vars=["원부하(kW)", "순부하(kW)"], var_name="구분", value_name="kW"
    )
    lines = (
        alt.Chart(long)
        .mark_line(strokeWidth=1.8)
        .encode(
            x=alt.X("시각:T", axis=time_axis()),
            y=alt.Y("kW:Q", title="출력 (kW)", scale=_CUT_SCALE),
            color=alt.Color(
                "구분:N",
                title=None,
                scale=alt.Scale(domain=["원부하(kW)", "순부하(kW)"], range=["#9ecae1", "#08519c"]),
                legend=LEGEND,
            ),
            tooltip=[time_tooltip(), "구분", alt.Tooltip("kW:Q", format=",.0f")],
        )
    )
    peak = frame.loc[frame["원부하(kW)"].idxmax()]
    cut = float(peak["원부하(kW)"]) - float(peak["순부하(kW)"])
    mark = pd.DataFrame(
        [
            {
                "시각": peak["시각"],
                "kW": float(peak["원부하(kW)"]),
                "아래": float(peak["순부하(kW)"]),
                "설명": f"피크 −{cut:,.0f} kW",
            }
        ]
    )
    # **화살표로 간격을 직접 가리킨다** — 숫자만 띄우면 어느 간격인지 헷갈린다.
    arrow = (
        alt.Chart(mark)
        .mark_rule(color="crimson", strokeWidth=2)
        .encode(x=alt.X("시각:T", axis=time_axis()), y="kW:Q", y2=alt.Y2("아래"))
    )
    caps = (
        alt.Chart(mark)
        .mark_point(shape="triangle-down", size=90, filled=True, color="crimson")
        .encode(x=alt.X("시각:T", axis=time_axis()), y="아래:Q", tooltip=["설명"])
    )
    label = (
        alt.Chart(mark)
        .mark_text(dy=-12, color="crimson", fontWeight="bold")
        .encode(x=alt.X("시각:T", axis=time_axis()), y="kW:Q", text="설명:N")
    )
    return (band + lines + arrow + caps + label).properties(height=300)


def ess_day_chart(
    usage: UsageData,
    dispatch: DispatchResult,
    day: RepresentativeDay,
    *,
    zoom: bool = True,
) -> alt.LayerChart | alt.FacetChart:
    """대표일의 ESS — **피크 앞뒤만 확대한 한 칸** (23세션 6절).

    17세션에는 2단이었다. 아래 칸(충전＋·방전−)을 떼어 **문구로 내린다** — 위
    칸과 종속이라 언제 담고 언제 쓰는지는 순부하 곡선과 운전 문구에 이미 있고,
    그림이 둘이면 어느 쪽을 봐야 할지 알 수 없다.

    하루 스물넷을 다 그리면 저감 구간이 손톱만 해진다. 태양광과 같이 **피크
    앞뒤 :data:`PEAK_ZOOM_HOURS` 시간만** 확대한다.

        원부하 · 순부하 · 목표선 — 축을 0 부터 시작하지 않는다

    **계시별 시간대 배경 띠를 걷어냈다** (26세션 2-1·2-2). 확대한 창이 대개 한
    시간대 안에 들어가 그림 전체가 주황 한 색으로 칠해졌고, 범례에는 셋(경부하·
    중간부하·최대부하)이 남아 그림에 없는 것을 가리켰다. 언제 담아 언제 쓰는지는
    아래 운전 문구가 시각으로 정확히 적는다.
    """
    frame = ess_day_frame(usage, dispatch, day.date)
    title = f"{day.title} · 15분"
    if len(frame) and zoom:
        frame = peak_window(frame)
        title = f"{day.title} · 피크 앞뒤 {PEAK_ZOOM_HOURS}시간"
    if frame.empty:
        blank = alt.Chart(pd.DataFrame({"시각": [], "kW": []})).mark_line()
        return alt.layer(blank, blank).properties(height=300)

    layers: list[alt.Chart] = []
    layers.append(
        alt.Chart(frame.assign(구분="저감분"))
        .mark_area(opacity=0.7, color="#31a354")
        .encode(
            x=alt.X("시각:T", title=title, axis=time_axis()),
            y=alt.Y("순부하(kW):Q", title="부하 (kW)", scale=_CUT_SCALE),
            y2=alt.Y2("원부하(kW)"),
        )
    )
    load = frame.melt(
        id_vars="시각", value_vars=["원부하(kW)", "순부하(kW)"], var_name="구분", value_name="kW"
    )
    layers.append(
        alt.Chart(load)
        .mark_line(strokeWidth=1.8)
        .encode(
            x=alt.X("시각:T", title=title, axis=time_axis()),
            y=alt.Y("kW:Q", title="부하 (kW)", scale=_CUT_SCALE),
            color=alt.Color(
                "구분:N",
                title=None,
                scale=alt.Scale(domain=["원부하(kW)", "순부하(kW)"], range=["#9ecae1", "#08519c"]),
                legend=LEGEND,
            ),
            tooltip=[time_tooltip(), "구분", alt.Tooltip("kW:Q", format=",.0f")],
        )
    )
    layers.append(
        alt.Chart(pd.DataFrame({"목표(kW)": [dispatch.target_kw]}))
        .mark_rule(strokeDash=[6, 4], color="crimson", strokeWidth=1.6)
        .encode(y="목표(kW):Q", tooltip=[alt.Tooltip("목표(kW):Q", format=",.0f")])
    )
    return alt.layer(*layers).properties(height=300)


def surplus_daily_chart(usage: UsageData, surplus_kw: pd.Series) -> alt.Chart:
    """연간 일별 잉여량 (15세션 2-6). **주말에 몰리는지**가 보여야 한다."""
    frame = surplus_daily_frame(usage, surplus_kw)
    return (
        alt.Chart(frame)
        .mark_bar()
        .encode(
            x=alt.X("날짜:T", title="날짜", axis=date_axis()),
            y=alt.Y("잉여(kWh):Q", title="일별 잉여 (kWh)"),
            color=alt.Color("구분:N", title=None, scale=_DAY_TYPE_COLORS, legend=LEGEND),
            tooltip=[date_tooltip(), "구분", alt.Tooltip("잉여(kWh):Q", format=",.0f")],
        )
        .properties(height=280)
    )
