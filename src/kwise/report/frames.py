"""차트가 그릴 표 (요구사항서 10.2·10.5).

**화면(altair)과 보고서(matplotlib)가 같은 표를 본다.** 프레임을 각자 만들면
같은 이름의 차트가 서로 다른 수를 그리게 되고, 그 어긋남은 눈으로 잡히지 않는다.

여기 있는 것은 전부 순수 함수다. 그림 라이브러리를 import 하지 않는다 —
:mod:`kwise.ui.charts` 가 altair 로, :mod:`kwise.report.figures` 가 matplotlib 로
같은 프레임을 받아 그린다.
"""

from __future__ import annotations

import pandas as pd

from kwise.compare import ComparisonResult, SensitivityRange
from kwise.diagnose import ChargeStructure, PeakProfile
from kwise.measures import EssTargetCurve, SolarCurve

__all__ = [
    "BAND_LABELS",
    "band_frame",
    "combination_frame",
    "ess_target_frame",
    "ess_target_table",
    "hourly_profile_frame",
    "monthly_peak_frame",
    "sensitivity_frame",
    "solar_curve_frame",
    "top_hour_frame",
]

BAND_LABELS: dict[str, str] = {"light": "경부하", "mid": "중간부하", "peak": "최대부하"}


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


def hourly_profile_frame(peak: PeakProfile) -> pd.DataFrame:
    profile = peak.hourly_profile
    return pd.DataFrame(
        {
            "시각": [f"{int(hour):02d}시" for hour in profile.index],
            "평균 부하(kW)": profile.astype(float).to_numpy(),
        }
    )


def band_frame(structure: ChargeStructure) -> pd.DataFrame:
    share = structure.band_share
    return pd.DataFrame(
        {
            "시간대": [BAND_LABELS.get(str(band), str(band)) for band in structure.band_kwh.index],
            "사용량(kWh)": structure.band_kwh.astype(float).to_numpy(),
            "비중": [float(share.get(band, 0.0)) for band in structure.band_kwh.index],
        }
    )


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


def ess_target_frame(curve: EssTargetCurve) -> pd.DataFrame:
    """ESS 회수기간 U곡선 (14세션 3-2).

    **필요 용량을 함께 낸다.** 회수기간만 그리면 "목표를 조금만 낮춰도 용량이
    급증한다" 가 보이지 않는다 — 그것이 곡선의 오른쪽 팔을 만드는 힘이다.
    """
    frame = curve.frame()
    return frame[frame["회수기간(년)"].notna()].reset_index(drop=True)


def ess_target_table(curve: EssTargetCurve) -> pd.DataFrame:
    """곡선 아래에 두는 대표 지점 표. **최소 지점이 가운데 온다.**"""
    return pd.DataFrame(
        {
            "목표(kW)": [item.target_kw for item in curve.highlights()],
            "저감량(kW)": [item.reduction_kw for item in curve.highlights()],
            "필요 출력(kW)": [item.power_kw for item in curve.highlights()],
            "필요 용량(kWh)": [item.required_capacity_kwh for item in curve.highlights()],
            "방전시간(h)": [item.discharge_hours for item in curve.highlights()],
            "투자비(원)": [item.investment_won for item in curve.highlights()],
            "연간 절감액(원)": [item.annual_saving_won for item in curve.highlights()],
            "회수기간(년)": [item.payback_years for item in curve.highlights()],
        }
    )


def combination_frame(comparison: ComparisonResult) -> pd.DataFrame:
    """조합별 절감액·투자비·확실성.

    **투자비를 모르면 ``None`` 이다.** 0 으로 채우면 막대가 바닥에 붙어
    "공짜" 로 읽힌다 (7.5).
    """
    return pd.DataFrame(
        {
            "조합": [item.name for item in comparison.combinations],
            "절감액(원)": [item.saving_won for item in comparison.combinations],
            "투자비(원)": [item.investment_won for item in comparison.combinations],
            "확실성": [str(item.certainty) for item in comparison.combinations],
        }
    )


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
