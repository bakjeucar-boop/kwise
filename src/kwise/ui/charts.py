"""차트 (요구사항서 10.2 — altair).

표를 그리는 자리가 아니라 **한눈에 판단이 서야 하는 자리에만** 쓴다. 진단에서는
둘이 필수다 (10.1).

    월별 최대수요        요금은 월별로 매겨지고 요금적용전력은 그 이력의 최대다
    상위 100구간 시각 분포  **태양광 기여 가능성을 즉시 보여 주는 지표다**

프레임을 만드는 함수와 차트를 만드는 함수를 갈라 둔다. 앞엣것은 테스트가 닿고,
뒤엣것은 altair 사양일 뿐이다.
"""

from __future__ import annotations

import altair as alt
import pandas as pd

from kwise.compare import ComparisonResult, SensitivityRange
from kwise.diagnose import ChargeStructure, PeakProfile
from kwise.measures import SolarCurve

__all__ = [
    "BAND_LABELS",
    "band_chart",
    "band_frame",
    "combination_chart",
    "combination_frame",
    "hourly_profile_chart",
    "hourly_profile_frame",
    "monthly_peak_chart",
    "monthly_peak_frame",
    "sensitivity_chart",
    "sensitivity_frame",
    "solar_curve_chart",
    "solar_curve_frame",
    "top_hour_chart",
    "top_hour_frame",
]

BAND_LABELS: dict[str, str] = {"light": "경부하", "mid": "중간부하", "peak": "최대부하"}

_HEIGHT = 260


# --------------------------------------------------------------------- 월별 최대수요


def monthly_peak_frame(peak: PeakProfile) -> pd.DataFrame:
    """월별 최대수요와 요금적용전력 기준값.

    ``demand_basis_kw`` 는 **경부하를 뺀 대상 시간대의 최대**다 (5.2 ①).
    관측 최대와 나란히 두어야 "밤 피크는 요금적용전력이 아니다" 가 보인다.
    """
    frame = peak.monthly.reset_index()
    frame["월"] = frame["month"].astype(str)
    return pd.DataFrame(
        {
            "월": frame["월"],
            "관측 최대(kW)": frame["max_demand_kw"].astype(float),
            "요금적용 대상 최대(kW)": frame["demand_basis_kw"].astype(float),
            "발생 시각": frame["max_demand_at"].astype(str),
        }
    )


def monthly_peak_chart(peak: PeakProfile) -> alt.LayerChart:
    frame = monthly_peak_frame(peak)
    long = frame.melt(
        id_vars=["월", "발생 시각"],
        value_vars=["관측 최대(kW)", "요금적용 대상 최대(kW)"],
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


# --------------------------------------------------------------------- 상위 구간 시각 분포


def top_hour_frame(peak: PeakProfile) -> pd.DataFrame:
    """상위 구간의 시각 분포를 **두 벌** 낸다 (6세션 결정).

    전 슬롯 기준은 부록 B 대조용 원값이고, 요금적용전력 대상 기준이 판정용이다.
    한 필드에 섞으면 어느 기준인지 알 수 없게 된다.
    """
    hours = range(24)
    return pd.DataFrame(
        {
            "시각": [f"{hour:02d}시" for hour in hours],
            "전 슬롯": [int(peak.hour_counts.get(hour, 0)) for hour in hours],
            "요금적용전력 대상": [int(peak.demand_hour_counts.get(hour, 0)) for hour in hours],
        }
    )


def top_hour_chart(peak: PeakProfile) -> alt.Chart:
    long = top_hour_frame(peak).melt(id_vars="시각", var_name="기준", value_name="구간 수")
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


# --------------------------------------------------------------------- 부하 프로파일


def hourly_profile_frame(peak: PeakProfile) -> pd.DataFrame:
    profile = peak.hourly_profile
    return pd.DataFrame(
        {
            "시각": [f"{int(hour):02d}시" for hour in profile.index],
            "평균 부하(kW)": profile.astype(float).to_numpy(),
        }
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


# --------------------------------------------------------------------- 요금 구조


def band_frame(structure: ChargeStructure) -> pd.DataFrame:
    share = structure.band_share
    return pd.DataFrame(
        {
            "시간대": [BAND_LABELS.get(str(band), str(band)) for band in structure.band_kwh.index],
            "사용량(kWh)": structure.band_kwh.astype(float).to_numpy(),
            "비중": [float(share.get(band, 0.0)) for band in structure.band_kwh.index],
        }
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


# --------------------------------------------------------------------- 태양광 용량 곡선


def solar_curve_frame(curve: SolarCurve) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "용량(kWp)": [point.capacity_kwp for point in curve.points],
            "기본요금 절감(원)": [point.base_saving_won for point in curve.points],
            "전력량요금 절감(원)": [point.energy_saving_won for point in curve.points],
            "총 절감액(원)": [point.total_saving_won for point in curve.points],
            "요금적용전력(kW)": [point.billing_demand_kw for point in curve.points],
        }
    )


def solar_curve_chart(curve: SolarCurve) -> alt.Chart:
    long = solar_curve_frame(curve).melt(
        id_vars="용량(kWp)",
        value_vars=["기본요금 절감(원)", "전력량요금 절감(원)", "총 절감액(원)"],
        var_name="구분",
        value_name="원",
    )
    return (
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
        .properties(height=300)
    )


# --------------------------------------------------------------------- 조합 비교


def combination_frame(comparison: ComparisonResult) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "조합": [item.name for item in comparison.combinations],
            "절감액(원)": [item.saving_won for item in comparison.combinations],
            "확실성": [str(item.certainty) for item in comparison.combinations],
        }
    )


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


# --------------------------------------------------------------------- 감도


def sensitivity_frame(ranges: tuple[SensitivityRange, ...]) -> pd.DataFrame:
    """감도는 **범위**다. 3열 나열을 하지 않는다 (9.2)."""
    rows = [
        {
            "지표": item.metric,
            "기준값": item.base,
            "하한": item.low,
            "상한": item.high,
            "범위": item.text(),
        }
        for item in ranges
        if item.base is not None
    ]
    return pd.DataFrame(rows)


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
