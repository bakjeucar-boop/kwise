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
    solar_curve_frame,
    solar_day_frame,
    surplus_daily_frame,
    tariff_option_frame,
    tariff_option_long_frame,
    top_hour_frame,
)

__all__ = [
    "BAND_LABELS",
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
    "solar_curve_chart",
    "solar_curve_frame",
    "solar_day_chart",
    "solar_day_frame",
    "surplus_daily_chart",
    "surplus_daily_frame",
    "tariff_option_chart",
    "tariff_option_frame",
    "tariff_option_long_frame",
    "top_hour_chart",
    "top_hour_frame",
]

_HEIGHT = 260


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


def tariff_option_chart(switch: TariffSwitchResult) -> alt.LayerChart:
    """요금제별 기본·전력량 누적 막대 (15세션 2-1).

    **선택요금은 기본요금과 전력량요금을 맞바꾸는 제도다.** 합계만 보면 왜
    유리한지 알 수 없어 누적으로 그린다. 현행·최적에 표식을 찍고 막대 위에
    합계와 현행 대비 차액을 적는다.
    """
    long = tariff_option_long_frame(switch)
    wide = tariff_option_frame(switch)
    order = list(wide["요금제"])
    wide = wide.assign(
        요약=[
            f"{money.won_short(total, reason='—')}"
            + ("" if abs(delta) < 1 else f" ({money.won_short(delta, reason='—')})")
            + (f" · {mark}" if mark else "")
            for total, delta, mark in zip(
                wide["합계(원)"], wide["현행 대비(원)"], wide["표식"], strict=True
            )
        ]
    )
    bars = (
        alt.Chart(long)
        .mark_bar()
        .encode(
            x=alt.X("요금제:N", title=None, sort=order),
            y=alt.Y("원:Q", title="요금 (원)"),
            color=alt.Color("구분:N", title=None),
            tooltip=["요금제", "구분", alt.Tooltip("원:Q", format=",.0f")],
        )
    )
    labels = (
        alt.Chart(wide)
        .mark_text(dy=-8, fontWeight="bold")
        .encode(x=alt.X("요금제:N", sort=order), y=alt.Y("합계(원):Q"), text="요약:N")
    )
    return (bars + labels).properties(height=300)


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
            color=alt.Color("구분:N", title=None),
            order="순서:Q",
            tooltip=["구분"],
        )
    )
    labels = (
        alt.Chart(
            frame.assign(
                설명=[
                    f"{row['구분']} — 역률 {row['역률(%)']:.0f}% · {row['각도(도)']:.0f}°"
                    for _, row in frame.iterrows()
                ]
            )
        )
        .mark_text(dx=6, align="left")
        .encode(
            x="유효전력:Q", y="무효전력:Q", text="설명:N", color=alt.Color("구분:N", title=None)
        )
    )
    return (shape + labels).properties(height=280)


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
    return (load + window).properties(height=280)


def solar_annual_chart(usage: UsageData, generation_kw: pd.Series) -> alt.Chart:
    """연간 일별 계통 수전·자가소비·잉여 (15세션 2-4 ①).

    **계통에서 받는 양이 줄어드는 모습**을 보인다. 자가소비와 잉여를 갈라 쌓아
    발전량을 다 쓰는지도 함께 읽히게 한다.
    """
    frame = solar_annual_frame(usage, generation_kw)
    long = frame.melt(
        id_vars="날짜",
        value_vars=["계통 수전(kWh)", "자가소비(kWh)", "잉여(kWh)"],
        var_name="구분",
        value_name="kWh",
    )
    return (
        alt.Chart(long)
        .mark_area()
        .encode(
            x=alt.X("날짜:T", title="날짜"),
            y=alt.Y("kWh:Q", title="일별 전력량 (kWh)"),
            color=alt.Color(
                "구분:N",
                title=None,
                scale=alt.Scale(
                    domain=["계통 수전(kWh)", "자가소비(kWh)", "잉여(kWh)"],
                    range=["#9ecae1", "#31a354", "#fdd0a2"],
                ),
            ),
            tooltip=[alt.Tooltip("날짜:T"), "구분", alt.Tooltip("kWh:Q", format=",.0f")],
        )
        .properties(height=300)
    )


def solar_day_chart(
    usage: UsageData, generation_kw: pd.Series, day: RepresentativeDay
) -> alt.LayerChart | alt.FacetChart:
    """대표일의 원부하·순부하·발전량 (15세션 2-4 ②).

    **피크가 얼마나 내려가는지**가 전부다. 원부하 피크 시각에 표식을 찍고
    저감량을 적는다.
    """
    frame = solar_day_frame(usage, generation_kw, day.date)
    long = frame.melt(
        id_vars="시각",
        value_vars=["원부하(kW)", "순부하(kW)", "발전량(kW)"],
        var_name="구분",
        value_name="kW",
    )
    lines = (
        alt.Chart(long)
        .mark_line()
        .encode(
            x=alt.X("시각:T", title=f"{day.title} · 15분"),
            y=alt.Y("kW:Q", title="출력 (kW)"),
            color=alt.Color(
                "구분:N",
                title=None,
                scale=alt.Scale(
                    domain=["원부하(kW)", "순부하(kW)", "발전량(kW)"],
                    range=["#9ecae1", "#08519c", "#f16913"],
                ),
            ),
            tooltip=[alt.Tooltip("시각:T"), "구분", alt.Tooltip("kW:Q", format=",.0f")],
        )
    )
    layers: list[alt.Chart] = [lines]
    if len(frame):
        peak = frame.loc[frame["원부하(kW)"].idxmax()]
        cut = float(peak["원부하(kW)"]) - float(peak["순부하(kW)"])
        mark = pd.DataFrame(
            [
                {
                    "시각": peak["시각"],
                    "kW": float(peak["원부하(kW)"]),
                    "설명": f"피크 −{cut:,.0f} kW",
                }
            ]
        )
        layers.append(
            alt.Chart(mark)
            .mark_point(size=150, filled=True, color="crimson")
            .encode(x="시각:T", y="kW:Q", tooltip=["설명"])
        )
        layers.append(
            alt.Chart(mark)
            .mark_text(dy=-14, color="crimson", fontWeight="bold")
            .encode(x="시각:T", y="kW:Q", text="설명:N")
        )
    return alt.layer(*layers).properties(height=300)


def ess_day_chart(
    usage: UsageData,
    dispatch: DispatchResult,
    day: RepresentativeDay,
    *,
    bands: pd.Series | None = None,
) -> alt.LayerChart | alt.FacetChart:
    """대표일의 ESS 충·방전 구조 (15세션 2-5).

    계시별 시간대를 배경 띠로 깔아 **왜 그 시각에 담고 쓰는지**를 보인다.
    충전은 위로, 방전은 아래로 부호를 갈라 영역으로 그린다.
    """
    frame = ess_day_frame(usage, dispatch, day.date, bands=bands)
    layers: list[alt.Chart] = []
    if "시간대" in frame.columns:
        layers.append(
            alt.Chart(frame)
            .mark_rect(opacity=0.5)
            .encode(
                x=alt.X("시각:T", title=f"{day.title} · 15분"),
                color=alt.Color("시간대:N", title="계시별 시간대", scale=_BAND_COLORS),
                tooltip=["시간대"],
            )
        )
    flows = frame.melt(
        id_vars="시각", value_vars=["충전(kW)", "방전(kW)"], var_name="구분", value_name="kW"
    )
    layers.append(
        alt.Chart(flows[flows["kW"] > 0])
        .mark_bar(opacity=0.85)
        .encode(
            x=alt.X("시각:T", title=f"{day.title} · 15분"),
            y=alt.Y("kW:Q", title="출력 (kW)"),
            color=alt.Color(
                "구분:N",
                title=None,
                scale=alt.Scale(domain=["충전(kW)", "방전(kW)"], range=["#6baed6", "#e6550d"]),
            ),
            tooltip=[alt.Tooltip("시각:T"), "구분", alt.Tooltip("kW:Q", format=",.0f")],
        )
    )
    long = frame.melt(
        id_vars="시각", value_vars=["원부하(kW)", "순부하(kW)"], var_name="구분", value_name="kW"
    )
    layers.append(
        alt.Chart(long)
        .mark_line()
        .encode(
            x="시각:T",
            y=alt.Y("kW:Q", title="출력 (kW)"),
            color=alt.Color(
                "구분:N",
                title=None,
                scale=alt.Scale(domain=["원부하(kW)", "순부하(kW)"], range=["#9ecae1", "#08519c"]),
            ),
            tooltip=[alt.Tooltip("시각:T"), "구분", alt.Tooltip("kW:Q", format=",.0f")],
        )
    )
    layers.append(
        alt.Chart(pd.DataFrame({"목표(kW)": [dispatch.target_kw]}))
        .mark_rule(strokeDash=[6, 4], color="crimson")
        .encode(y="목표(kW):Q", tooltip=[alt.Tooltip("목표(kW):Q", format=",.0f")])
    )
    return alt.layer(*layers).resolve_scale(y="shared").properties(height=320)


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
