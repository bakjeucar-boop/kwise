"""화면 차트 (요구사항서 10.2 — altair).

표를 그리는 자리가 아니라 **한눈에 판단이 서야 하는 자리에만** 쓴다. 진단에서는
둘이 필수다 (10.1).

    월별 최대수요        요금은 월별로 매겨지고 요금적용전력은 그 이력의 최대다
    상위 100구간 시각 분포  **태양광 기여 가능성을 즉시 보여 주는 지표다**

**프레임은 :mod:`kwise.report.frames` 에 있다.** 화면(altair)과 보고서
(matplotlib)가 같은 표를 봐야 같은 수를 그린다. 여기 있는 것은 altair 사양뿐이다.
"""

from __future__ import annotations

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
    TARIFF_PARTS,
    band_frame,
    combination_frame,
    dr_daily_frame,
    ess_day_frame,
    ess_target_frame,
    ess_target_table,
    hourly_profile_frame,
    monthly_peak_frame,
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
    top_hour_frame,
)

__all__ = [
    "BAND_LABELS",
    "CAPACITY_ROWS",
    "PEAK_ZOOM_HOURS",
    "TARIFF_PARTS",
    "band_chart",
    "band_frame",
    "combination_chart",
    "combination_frame",
    "dr_daily_chart",
    "dr_daily_frame",
    "ess_day_chart",
    "ess_day_frame",
    "ess_target_chart",
    "ess_target_frame",
    "ess_target_table",
    "hourly_profile_chart",
    "hourly_profile_frame",
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
#     ① 금액·전력 축은 **0 부터 시작하지 않는다.** 축 제목에 그 사실을 적는다
#     ② 절대값과 변화량은 **차트를 나눈다.** 한 축에 억과 백만을 같이 두지 않는다
#     ③ 두 선이 붙어 보이면 **그 사이를 색으로 채운다**

#: 값의 범위에 맞춰 축을 자른다. 축 제목에 "0 부터 시작하지 않습니다" 를 적는다.
_CUT_SCALE = alt.Scale(zero=False, nice=True)

#: 범례를 **우측 하단**에 둔다 (17세션 2절). 우측 상단이면 값이 커질 때
#: 라벨·표식을 가린다 — 값을 바꿔 보는 화면에서 그 가림이 반복된다.
_LEGEND_BOTTOM = alt.Legend(orient="bottom-right", direction="vertical", fillColor="white")


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
            color=alt.Color("구분:N", title=None),
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
            color=alt.Color("기준:N", title=None),
            tooltip=["시각", "기준", "구간 수"],
        )
        .properties(height=_HEIGHT)
    )


def hourly_profile_chart(peak: PeakProfile) -> alt.Chart:
    return (
        alt.Chart(hourly_profile_frame(peak))
        .mark_line(point=True)
        .encode(
            x=alt.X("시각:N", title="시각", sort=None),
            y=alt.Y("평균 부하(kW):Q", title="평균 부하 (kW)"),
            tooltip=["시각", alt.Tooltip("평균 부하(kW):Q", format=",.0f")],
        )
        .properties(height=_HEIGHT)
    )


def band_chart(structure: ChargeStructure) -> alt.Chart:
    return (
        alt.Chart(band_frame(structure))
        .mark_bar()
        .encode(
            x=alt.X("사용량(kWh):Q", title="사용량 (kWh)", stack="normalize"),
            color=alt.Color("시간대:N", title=None, sort=list(BAND_LABELS.values())),
            tooltip=[
                "시간대",
                alt.Tooltip("사용량(kWh):Q", format=",.0f"),
                alt.Tooltip("비중:Q", format=".1%"),
            ],
        )
        .properties(height=90)
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
            color=alt.Color("구분:N", title=None),
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


def ess_target_chart(curve: EssTargetCurve) -> alt.LayerChart:
    """ESS 회수기간 U곡선 (14세션 3-2).

    **최소 지점에 표식을 찍고 그 지점의 사양을 함께 적는다.** 슬라이더 대신 이
    곡선이 목표를 고르게 한다 — 사용자가 찍는 자리는 대개 틀린 자리다.

    필요 용량을 **보조 축**으로 함께 그린다. 오른쪽 팔이 왜 나빠지는지는 회수기간
    곡선만으로는 보이지 않는다 — 용량이 급증하는 것이 원인이다.
    """
    frame = ess_target_frame(curve)
    base = alt.Chart(frame).encode(
        x=alt.X(
            "목표 요금적용전력(kW):Q", title="목표 요금적용전력 (kW)", scale=alt.Scale(zero=False)
        )
    )
    tooltip = [
        alt.Tooltip("목표 요금적용전력(kW):Q", format=",.0f"),
        alt.Tooltip("저감량(kW):Q", format=",.0f"),
        alt.Tooltip("필요 출력(kW):Q", format=",.0f"),
        alt.Tooltip("필요 용량(kWh):Q", format=",.0f"),
        alt.Tooltip("방전시간(h):Q", format=",.2f"),
        alt.Tooltip("투자비(원):Q", format=",.0f"),
        alt.Tooltip("회수기간(년):Q", format=",.1f"),
    ]
    payback = base.mark_line(color="#08519c").encode(
        y=alt.Y("회수기간(년):Q", title="회수기간 (년)", scale=alt.Scale(zero=False)),
        tooltip=tooltip,
    )
    capacity = base.mark_line(color="#bdbdbd", strokeDash=[4, 3]).encode(
        y=alt.Y("필요 용량(kWh):Q", title="필요 용량 (kWh)", scale=alt.Scale(zero=False)),
        tooltip=tooltip,
    )
    layers: list[alt.Chart] = [capacity, payback]
    if curve.best is not None:
        best = pd.DataFrame(
            {
                "목표 요금적용전력(kW)": [curve.best.target_kw],
                "회수기간(년)": [curve.best.payback_years],
                "사양": [curve.best.spec_label],
            }
        )
        mark = alt.Chart(best)
        layers.append(
            mark.mark_point(size=140, filled=True, color="crimson").encode(
                x="목표 요금적용전력(kW):Q", y="회수기간(년):Q", tooltip=["사양"]
            )
        )
        layers.append(
            mark.mark_text(dy=-14, color="crimson", fontWeight="bold").encode(
                x="목표 요금적용전력(kW):Q", y="회수기간(년):Q", text="사양:N"
            )
        )
    return alt.layer(*layers).resolve_scale(y="independent").properties(height=320)


def combination_chart(comparison: ComparisonResult) -> alt.Chart:
    """조합별 절감액. **확실성으로 색을 나눈다** — 같은 표에서 등급이 보여야 한다 (8장)."""
    frame = combination_frame(comparison)
    return (
        alt.Chart(frame)
        .mark_bar()
        .encode(
            y=alt.Y("조합:N", title=None, sort=list(frame["조합"])),
            x=alt.X("절감액(원):Q", title="기간 절감액 (원)"),
            color=alt.Color("확실성:N", title="확실성"),
            tooltip=["조합", alt.Tooltip("절감액(원):Q", format=",.0f"), "확실성"],
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
_BAND_COLORS = alt.Scale(
    domain=["경부하", "중간부하", "최대부하"],
    range=["#eff3ff", "#fee6ce", "#fdd0a2"],
)


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
            y=alt.Y("원:Q", title="요금 (원) — 0 부터 시작하지 않습니다", scale=_CUT_SCALE),
            color=alt.Color(
                "구분:N",
                title=None,
                sort=list(TARIFF_PARTS),
                scale=alt.Scale(domain=list(TARIFF_PARTS), range=["#6baed6", "#fd8d3c", "#31a354"]),
                legend=_LEGEND_BOTTOM,
            ),
            tooltip=["요금제", "구분", alt.Tooltip("원:Q", format=",.0f")],
        )
        .properties(height=300)
    )


def tariff_delta_chart(switch: TariffSwitchResult) -> alt.LayerChart:
    """**현행 대비 차액만** 그리는 막대 (17세션 1-3).

    0 을 기준으로 좌우로 뻗는다 — 절감은 왼쪽, 증가는 오른쪽이다. 절대 금액을
    지우고 변화만 남기면 "얼마나 줄어드는가" 가 한눈에 읽힌다.
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
    bars = (
        alt.Chart(labelled)
        .mark_bar()
        .encode(
            y=alt.Y("요금제:N", title=None, sort=order),
            x=alt.X("현행 대비(원):Q", title="현행 대비 (원) — 왼쪽이 절감"),
            color=alt.Color(
                "방향:N",
                title=None,
                scale=alt.Scale(
                    domain=["절감", "현행", "증가"], range=["#31a354", "#bdbdbd", "#de2d26"]
                ),
                legend=_LEGEND_BOTTOM,
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
            x=alt.X("날짜:T", title="날짜"),
            y=alt.Y("운영시간대 평균(kW):Q", title="운영 시간대 평균 부하 (kW)"),
            color=alt.Color("구분:N", title=None, scale=_DAY_TYPE_COLORS),
            tooltip=[
                alt.Tooltip("날짜:T"),
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
                x="날짜:T",
                y="운영시간대 평균(kW):Q",
                tooltip=[
                    alt.Tooltip("날짜:T"),
                    alt.Tooltip("운영시간대 평균(kW):Q", format=",.0f"),
                ],
            )
        )
    return alt.layer(*layers).properties(height=300)


def power_triangle_chart(result: PowerFactorResult) -> alt.LayerChart:
    """전력삼각형 — 개선 전후 (15세션 2-3).

    **각이 좁아지는 모습**이 이 그림의 전부다. 유효전력을 1 로 두고 무효전력만
    줄어드는 것을 겹쳐 보인다.
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
            color=alt.Color("구분:N", title=None, legend=_LEGEND_BOTTOM),
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
            color=alt.Color("구분:N", title=None, legend=_LEGEND_BOTTOM),
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
            color=alt.Color("구분:N", title=None, legend=_LEGEND_BOTTOM),
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
            x=alt.X("시각:T", title=f"{day.title} · 15분 부하"),
            y=alt.Y("부하(kW):Q", title="부하 (kW)"),
            tooltip=[alt.Tooltip("시각:T"), alt.Tooltip("부하(kW):Q", format=",.0f"), "구간"],
        )
    )
    window = (
        alt.Chart(frame[frame["구간"].str.startswith("주간")])
        .mark_point(size=18, opacity=0.35, color="#fd8d3c")
        .encode(x="시각:T", y="부하(kW):Q", tooltip=["구간"])
    )
    return (load + window).properties(height=280).configure_legend(orient="bottom-right")


def solar_saving_ratio(usage: UsageData, generation_kw: pd.Series) -> float | None:
    """자가소비로 줄어든 **계통 수전 비율**. 화면 문구가 쓴다 (17세션 3-4)."""
    frame = solar_annual_frame(usage, generation_kw)
    total = float(frame["사용량(kWh)"].sum())
    if total <= 0:
        return None
    return float(frame["자가소비(kWh)"].sum()) / total


def solar_annual_chart(usage: UsageData, generation_kw: pd.Series) -> alt.LayerChart:
    """연간 일별 — **주인공은 사용량이 줄어드는 모습이다** (17세션 3-4).

    셋을 쌓아 올리던 그림은 발전량(자가소비·잉여)에 색이 들어가 **그쪽이 주인공**
    처럼 보였다. 정작 봐야 할 것은 「계통에서 받는 양이 얼마나 줄었는가」 다.

        원래 사용량 선  ─────────────
                        ▓▓▓ 절감분 (자가소비)
        순사용량 영역   ░░░░░░░░░░░░░

    두 선 사이를 색으로 채우면 그 간격이 곧 절감이다. 잉여는 **자가소비로 쓰지
    못하고 남은 몫**이라 별개 선으로 얇게만 얹는다.
    """
    frame = solar_annual_frame(usage, generation_kw)
    saved = (
        alt.Chart(frame.assign(구분="절감분 (자가소비)"))
        .mark_area(opacity=0.85)
        .encode(
            x=alt.X("날짜:T", title="날짜"),
            y=alt.Y("계통 수전(kWh):Q", title="일별 전력량 (kWh)"),
            y2=alt.Y2("사용량(kWh)"),
            color=alt.Color(
                "구분:N",
                title=None,
                scale=alt.Scale(domain=["절감분 (자가소비)"], range=["#31a354"]),
                legend=_LEGEND_BOTTOM,
            ),
            tooltip=[
                alt.Tooltip("날짜:T"),
                alt.Tooltip("사용량(kWh):Q", format=",.0f"),
                alt.Tooltip("계통 수전(kWh):Q", format=",.0f"),
                alt.Tooltip("자가소비(kWh):Q", format=",.0f"),
            ],
        )
    )
    grid = (
        alt.Chart(frame)
        .mark_area(opacity=0.55, color="#9ecae1")
        .encode(x="날짜:T", y=alt.Y("계통 수전(kWh):Q"))
    )
    demand = (
        alt.Chart(frame)
        .mark_line(color="#08519c", strokeWidth=1.2)
        .encode(x="날짜:T", y=alt.Y("사용량(kWh):Q", title="일별 전력량 (kWh)"))
    )
    surplus = (
        alt.Chart(frame)
        .mark_line(color="#f16913", strokeWidth=1.0, strokeDash=[4, 3])
        .encode(x="날짜:T", y=alt.Y("잉여(kWh):Q"))
    )
    return (grid + saved + demand + surplus).properties(height=300)


#: 피크 확대 차트가 보일 앞뒤 시간 (17세션 3-5).
PEAK_ZOOM_HOURS = 3


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
        peak_at = pd.Timestamp(frame.loc[frame["원부하(kW)"].idxmax(), "시각"])
        window = pd.Timedelta(hours=PEAK_ZOOM_HOURS)
        times = pd.DatetimeIndex(frame["시각"])
        frame = frame[(times >= peak_at - window) & (times <= peak_at + window)]
        title = f"{day.title} · 피크 앞뒤 {PEAK_ZOOM_HOURS}시간"
    if frame.empty:
        blank = alt.Chart(pd.DataFrame({"시각": [], "kW": []})).mark_line()
        return alt.layer(blank, blank).properties(height=280)

    band = (
        alt.Chart(frame.assign(구분="저감분"))
        .mark_area(opacity=0.75)
        .encode(
            x=alt.X("시각:T", title=title),
            y=alt.Y("순부하(kW):Q", title="출력 (kW) — 0 부터 시작하지 않습니다", scale=_CUT_SCALE),
            y2=alt.Y2("원부하(kW)"),
            color=alt.Color(
                "구분:N",
                title=None,
                scale=alt.Scale(domain=["저감분"], range=["#31a354"]),
                legend=_LEGEND_BOTTOM,
            ),
            tooltip=[
                alt.Tooltip("시각:T"),
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
            x="시각:T",
            y=alt.Y("kW:Q", title="출력 (kW) — 0 부터 시작하지 않습니다", scale=_CUT_SCALE),
            color=alt.Color(
                "구분:N",
                title=None,
                scale=alt.Scale(domain=["원부하(kW)", "순부하(kW)"], range=["#9ecae1", "#08519c"]),
                legend=_LEGEND_BOTTOM,
            ),
            tooltip=[alt.Tooltip("시각:T"), "구분", alt.Tooltip("kW:Q", format=",.0f")],
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
        .encode(x="시각:T", y="kW:Q", y2=alt.Y2("아래"))
    )
    caps = (
        alt.Chart(mark)
        .mark_point(shape="triangle-down", size=90, filled=True, color="crimson")
        .encode(x="시각:T", y="아래:Q", tooltip=["설명"])
    )
    label = (
        alt.Chart(mark)
        .mark_text(dy=-12, color="crimson", fontWeight="bold")
        .encode(x="시각:T", y="kW:Q", text="설명:N")
    )
    return (band + lines + arrow + caps + label).properties(height=300)


def ess_day_chart(
    usage: UsageData,
    dispatch: DispatchResult,
    day: RepresentativeDay,
    *,
    bands: pd.Series | None = None,
) -> alt.VConcatChart:
    """대표일의 ESS **2단 그림** (17세션 4-1).

    한 축에 겹쳐 그렸더니 **온통 하얬다.** 부하가 5,000 kW 대인데 충·방전은
    100 kW 대라, 공유 축에서 막대가 선 굵기만큼도 서지 않았다.

        위 칸   원부하 · 순부하 · 목표선  — **축을 0 부터 시작하지 않는다**
        아래 칸  충전(+) · 방전(−)        — 제 스케일을 가진 별도 패널

    계시별 시간대 띠는 **옅게** 깔아 배경으로 둔다 — 왜 그 시각에 담고 쓰는지가
    거기서 읽힌다.
    """
    frame = ess_day_frame(usage, dispatch, day.date, bands=bands)
    title = f"{day.title} · 15분"
    if frame.empty:
        empty = alt.Chart(pd.DataFrame({"시각": [], "kW": []})).mark_line().properties(height=200)
        return alt.vconcat(empty, empty)

    upper: list[alt.Chart] = []
    if "시간대" in frame.columns:
        upper.append(
            alt.Chart(frame)
            .mark_rect(opacity=0.18)
            .encode(
                x=alt.X("시각:T", title=None),
                color=alt.Color(
                    "시간대:N", title="계시별 시간대", scale=_BAND_COLORS, legend=_LEGEND_BOTTOM
                ),
                tooltip=["시간대"],
            )
        )
    load = frame.melt(
        id_vars="시각", value_vars=["원부하(kW)", "순부하(kW)"], var_name="구분", value_name="kW"
    )
    upper.append(
        alt.Chart(frame.assign(구분="저감분"))
        .mark_area(opacity=0.7, color="#31a354")
        .encode(
            x=alt.X("시각:T", title=None),
            y=alt.Y("순부하(kW):Q", title="부하 (kW) — 0 부터 시작하지 않습니다", scale=_CUT_SCALE),
            y2=alt.Y2("원부하(kW)"),
        )
    )
    upper.append(
        alt.Chart(load)
        .mark_line(strokeWidth=1.8)
        .encode(
            x=alt.X("시각:T", title=None),
            y=alt.Y("kW:Q", title="부하 (kW) — 0 부터 시작하지 않습니다", scale=_CUT_SCALE),
            color=alt.Color(
                "구분:N",
                title=None,
                scale=alt.Scale(domain=["원부하(kW)", "순부하(kW)"], range=["#9ecae1", "#08519c"]),
                legend=_LEGEND_BOTTOM,
            ),
            tooltip=[alt.Tooltip("시각:T"), "구분", alt.Tooltip("kW:Q", format=",.0f")],
        )
    )
    upper.append(
        alt.Chart(pd.DataFrame({"목표(kW)": [dispatch.target_kw]}))
        .mark_rule(strokeDash=[6, 4], color="crimson", strokeWidth=1.6)
        .encode(y="목표(kW):Q", tooltip=[alt.Tooltip("목표(kW):Q", format=",.0f")])
    )

    # 아래 칸 — **충전은 위로, 방전은 아래로.** 부호를 갈라야 언제 담고 언제
    # 쓰는지가 한눈에 들어온다.
    flows = pd.concat(
        [
            frame[["시각"]].assign(구분="충전", kW=frame["충전(kW)"]),
            frame[["시각"]].assign(구분="방전", kW=-frame["방전(kW)"]),
        ]
    )
    lower = (
        alt.Chart(flows[flows["kW"] != 0.0])
        .mark_bar(opacity=0.95)
        .encode(
            x=alt.X("시각:T", title=title),
            y=alt.Y("kW:Q", title="충전(+) · 방전(−) (kW)"),
            color=alt.Color(
                "구분:N",
                title=None,
                scale=alt.Scale(domain=["충전", "방전"], range=["#3182bd", "#e6550d"]),
                legend=_LEGEND_BOTTOM,
            ),
            tooltip=[alt.Tooltip("시각:T"), "구분", alt.Tooltip("kW:Q", format=",.0f")],
        )
        .properties(height=140)
    )
    return alt.vconcat(alt.layer(*upper).properties(height=240), lower, spacing=6).resolve_scale(
        color="independent"
    )


def surplus_daily_chart(usage: UsageData, surplus_kw: pd.Series) -> alt.Chart:
    """연간 일별 잉여량 (15세션 2-6). **주말에 몰리는지**가 보여야 한다."""
    frame = surplus_daily_frame(usage, surplus_kw)
    return (
        alt.Chart(frame)
        .mark_bar()
        .encode(
            x=alt.X("날짜:T", title="날짜"),
            y=alt.Y("잉여(kWh):Q", title="일별 잉여 (kWh)"),
            color=alt.Color("구분:N", title=None, scale=_DAY_TYPE_COLORS),
            tooltip=[alt.Tooltip("날짜:T"), "구분", alt.Tooltip("잉여(kWh):Q", format=",.0f")],
        )
        .properties(height=280)
    )
