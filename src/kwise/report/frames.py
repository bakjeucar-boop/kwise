"""차트가 그릴 표 (요구사항서 10.2·10.5).

**화면(altair)과 보고서(matplotlib)가 같은 표를 본다.** 프레임을 각자 만들면
같은 이름의 차트가 서로 다른 수를 그리게 되고, 그 어긋남은 눈으로 잡히지 않는다.

여기 있는 것은 전부 순수 함수다. 그림 라이브러리를 import 하지 않는다 —
:mod:`kwise.ui.charts` 가 altair 로, :mod:`kwise.report.figures` 가 matplotlib 로
같은 프레임을 받아 그린다.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence

import pandas as pd

from kwise.compare import ComparisonResult, SensitivityRange
from kwise.diagnose import ChargeStructure, PeakProfile
from kwise.diagnose.dr import DrProfile
from kwise.io import UsageData, slot_start
from kwise.measures import (
    CapacityVerdict,
    DispatchResult,
    EssTargetCurve,
    PowerFactorResult,
    SolarCurve,
    SolarPoint,
    TariffSwitchResult,
    annualize,
)
from kwise.report.columns import option_label
from kwise.report.days import day_profile
from kwise.tariff import day_window, option_sort_key

__all__ = [
    "BAND_LABELS",
    "CAPACITY_COLUMNS",
    "CAPACITY_ROWS",
    "CAPACITY_WINDOW",
    "DAY_TYPE_LABELS",
    "MONTHLY_CHARGE_PARTS",
    "TARIFF_PARTS",
    "band_frame",
    "combination_frame",
    "daily_temperature_frame",
    "dr_daily_frame",
    "ess_day_frame",
    "ess_target_frame",
    "hourly_profile_frame",
    "monthly_charge_frame",
    "monthly_peak_frame",
    "power_factor_day_frame",
    "power_triangle_frame",
    "sensitivity_frame",
    "solar_annual_frame",
    "solar_capacity_table",
    "solar_curve_frame",
    "solar_day_frame",
    "surplus_daily_frame",
    "tariff_delta_frame",
    "tariff_option_frame",
    "tariff_option_long_frame",
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


def hourly_profile_frame(peak: PeakProfile, *, season: str | None = None) -> pd.DataFrame:
    """시각별 평균 부하. ``season`` 을 주면 그 계절만 (30세션 5-1).

    **없는 계절은 빈 표다.** 반년치 자료에는 여름이 없을 수 있는데, 없는 것을
    0 으로 그리면 "여름에 안 쓴다" 로 읽힌다.
    """
    profile = peak.hourly_profile
    if season is not None:
        wide = peak.hourly_profile_by_season
        if wide.empty or season not in wide.columns:
            return pd.DataFrame({"시각": [], "평균 부하(kW)": []})
        profile = wide[season].dropna()
    return pd.DataFrame(
        {
            "시각": [f"{int(hour):02d}시" for hour in profile.index],
            "평균 부하(kW)": profile.astype(float).to_numpy(),
        }
    )


#: 월별 요금 구성 막대의 쌓는 순서. **기본요금이 맨 아래다** — 사용량과 무관하게
#: 깔리는 몫이라 밑단에 있어야 그 위의 전력량요금이 무엇에 얹혀 있는지 읽힌다.
MONTHLY_CHARGE_PARTS: tuple[str, ...] = ("기본요금", "경부하", "중간부하", "최대부하")


def monthly_charge_frame(structure: ChargeStructure) -> pd.DataFrame:
    """월별 요금 구성 — 기본요금과 계시별 전력량요금 (27세션 3-2).

    **기본요금에 역률요금을 합쳐 적는다.** 역률요금은 기본요금의 ±% 조정이라
    따로 세우면 막대에 뜻 없는 실오라기가 하나 늘고, 요금 엔진의 12개월 환산
    (:meth:`~kwise.tariff.BillingResult.annualize`)도 이미 둘을 함께 묶는다.
    그래서 **네 조각의 합이 그달 청구액**(``total_won``)과 정확히 맞는다.

    **기본요금이 달마다 같은 값으로 이어지는 것이 정상이다** (27세션 3-2).
    요금적용전력이 직전 12개월 최대로 결정되므로 한 번 최대가 서면 그 뒤로는
    같은 값이 이어진다 — 그 사실을 보이려고 막대에 넣는다. 선으로 빼면 축이
    둘이 되고, 주석으로 내리면 「전력량요금만 있는 요금」 처럼 읽힌다.
    """
    monthly = structure.monthly
    rows: list[dict[str, object]] = []
    for month, row in monthly.iterrows():
        base = float(row["base_won"]) + float(row.get("power_factor_won", 0.0))
        values = {
            "기본요금": base,
            "경부하": float(row.get("light_won", 0.0)),
            "중간부하": float(row.get("mid_won", 0.0)),
            "최대부하": float(row.get("peak_won", 0.0)),
        }
        total = sum(values.values())
        rows.extend(
            {
                "월": str(month),
                "구분": part,
                "원": values[part],
                "합계(원)": total,
                # 쌓는 순서. 그림 쪽에서 다시 정하면 달마다 밑단이 흔들린다.
                "순서": order,
            }
            for order, part in enumerate(MONTHLY_CHARGE_PARTS)
        )
    return pd.DataFrame(rows, columns=["월", "구분", "원", "합계(원)", "순서"])


def band_frame(structure: ChargeStructure, *, season: str | None = None) -> pd.DataFrame:
    """계시별 사용량 구성. ``season`` 을 주면 그 계절만 (30세션 5-2).

    비중은 **그 계절 안에서 다시 잰다.** 전체 대비로 두면 세 계절의 막대가 각각
    3분의 1 길이로 그려져 무엇의 구성인지 알 수 없다.
    """
    kwh = structure.band_kwh
    if season is not None:
        wide = structure.band_season_kwh
        if wide.empty or season not in wide.index:
            return pd.DataFrame({"시간대": [], "사용량(kWh)": [], "비중": []})
        kwh = wide.loc[season]
    total = float(kwh.sum())
    return pd.DataFrame(
        {
            "시간대": [BAND_LABELS.get(str(band), str(band)) for band in kwh.index],
            "사용량(kWh)": kwh.astype(float).to_numpy(),
            "비중": [float(value) / total if total else 0.0 for value in kwh],
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


#: 용량 표에 세울 지점 수 (17세션 3-3). 스무 줄은 아무도 읽지 않고, 셋은 곡선의
#: 모양이 보이지 않는다.
CAPACITY_ROWS = 5


CAPACITY_COLUMNS: tuple[str, ...] = (
    "용량(kWp)",
    "필요 면적(m²)",
    "연간 발전량(kWh)",
    "자가소비율",
    "기본요금 절감(원)",
    "전력량요금 절감(원)",
    "투자비(원)",
    "회수기간(년)",
    "표식",
)


def solar_capacity_table(
    curve: SolarCurve,
    *,
    verdict: CapacityVerdict | None = None,
    surplus_points: Sequence[tuple[str, SolarPoint]] = (),
    gcr: float | None = None,
    area_per_kwp_m2: float | None = None,
    area_limit_m2: float | None = None,
    rows: int = CAPACITY_ROWS,
) -> pd.DataFrame:
    """**잉여 발생 지점을 가운데 둔 용량 비교** (17세션 3-3 · 31세션 4-1).

    26세션에 판단의 갈림길을 잉여로 옮겼는데, 표는 그대로 **선정 용량 아래에서만**
    지점을 골랐다 — 곡선이 0 부터 설치 가능 면적이 허용하는 용량까지만 돌기
    때문이다. 그래서 정작 「어디서부터 잉여가 생기나」 가 표에 없었다.

    다섯 지점을 세운다.

        선정 용량보다 작은 것 둘   곡선에서 고르게 뽑는다
        선정 용량                 카드가 머리에 낸 그 용량
        잉여가 처음 생기는 용량    :func:`~kwise.measures.surplus_free_capacity_kwp`
        잉여가 많이 생기는 용량    :func:`~kwise.measures.surplus_share_capacity_kwp`

    뒤의 둘은 **곡선 밖일 수 있다** — 그래서 ``surplus_points`` 로 따로 받는다
    (:func:`~kwise.measures.solar_point` 가 낸 점). 곡선에 얹지 않는 이유는
    최적 판정이 설치할 수 없는 용량을 고르면 안 되기 때문이다.

    **면적을 함께 낸다.** kWp 는 설비 규격이라 지붕을 보고 판단할 수 없다.
    ``area_limit_m2`` 를 주면 그 면적을 넘는 줄에 표식을 단다 — 값을 지우지는
    않는다. 「이만큼 지으면 이런 값인데 자리가 없다」 가 판단에 필요한 사실이다.

    금액과 발전량은 **12개월 환산**이다. 회수기간이 연 단위라 같은 축에 두려면
    기간값을 그대로 쓸 수 없다.
    """
    usable = [point for point in curve.points if point.capacity_kwp > 0]
    if not usable:
        return pd.DataFrame(columns=list(CAPACITY_COLUMNS))
    months = curve.base_fee_months
    limit = usable[-1]
    best = verdict.best if verdict is not None else None

    marks: dict[float, list[str]] = {limit.capacity_kwp: ["선정 용량"]}
    if best is not None and best.capacity_kwp != limit.capacity_kwp:
        marks.setdefault(best.capacity_kwp, []).append("최적")
    picked: dict[float, SolarPoint] = {limit.capacity_kwp: limit}
    if best is not None:
        picked.setdefault(best.capacity_kwp, best)

    # **선정 용량보다 작은 것 둘.** 곡선을 삼등분해 뽑는다 — 아래쪽이 어떻게
    # 생겼는지 보이기만 하면 되므로 촘촘할 이유가 없다.
    below = [point for point in usable if point.capacity_kwp < limit.capacity_kwp]
    for fraction in (1, 2):
        if not below:
            break
        target = limit.capacity_kwp * fraction / 3.0
        near = min(below, key=lambda point: abs(point.capacity_kwp - target))
        picked.setdefault(near.capacity_kwp, near)

    for label, point in surplus_points:
        picked.setdefault(point.capacity_kwp, point)
        marks.setdefault(point.capacity_kwp, []).append(label)

    ordered = sorted(picked.values(), key=lambda point: point.capacity_kwp)[: max(rows, len(marks))]

    def area(capacity_kwp: float) -> float | None:
        if gcr is None or area_per_kwp_m2 is None or gcr <= 0:
            return None
        return capacity_kwp * area_per_kwp_m2 / gcr

    def mark(point: SolarPoint) -> str:
        labels = list(marks.get(point.capacity_kwp, ()))
        needed = area(point.capacity_kwp)
        if area_limit_m2 is not None and needed is not None and needed > area_limit_m2 + 1e-6:
            labels.append("면적 초과")
        return " · ".join(labels)

    return pd.DataFrame(
        {
            "용량(kWp)": [point.capacity_kwp for point in ordered],
            "필요 면적(m²)": [area(point.capacity_kwp) for point in ordered],
            "연간 발전량(kWh)": [annualize(point.generation_kwh, months) for point in ordered],
            "자가소비율": [point.self_consumption_ratio for point in ordered],
            "기본요금 절감(원)": [annualize(point.base_saving_won, months) for point in ordered],
            "전력량요금 절감(원)": [
                annualize(point.energy_saving_won, months) for point in ordered
            ],
            "투자비(원)": [point.investment_won for point in ordered],
            "회수기간(년)": [point.payback_years for point in ordered],
            "표식": [mark(point) for point in ordered],
        }
    )


#: 회수기간 곡선에서 **최적의 몇 배까지 보일지** (26세션 1-2).
#:
#: 40~20,000 kWh 를 다 그리면 최적 둘레의 변화가 뭉개진다 — 사용자가 "3,700 kW
#: 로 낮추는 게 낫지 않나" 라고 물은 것이 그 구간을 못 읽어서였다. 최적 용량을
#: 가운데 두고 **작은 쪽 절반부터 큰 쪽 세 배까지** 남긴다. 왼쪽은 시장 최소
#: 규모에 눌려 금방 평평해지고, 오른쪽은 세 배쯤에서 회수기간이 두 배가 되어
#: 더 그려도 같은 말을 되풀이한다.
CAPACITY_WINDOW: tuple[float, float] = (0.5, 3.0)


def ess_target_frame(curve: EssTargetCurve) -> pd.DataFrame:
    """ESS 회수기간 곡선 — **정격 용량 축** (26세션 1-1).

    x 를 목표 요금적용전력에서 **정격 용량(kWh)** 으로 바꿨다. 사용자가 정하는
    것은 목표가 아니라 사야 할 배터리이고, 목표를 낮출수록 용량이 급증한다는
    사실이 축 자체로 읽혀야 하기 때문이다. 용량은 목표에 대해 단조 감소라
    U자 모양은 그대로다.

    보조 축을 두지 않는다 — 회수기간 한 줄만 남긴다.
    """
    frame = curve.frame()
    priced = frame[frame["회수기간(년)"].notna()].reset_index(drop=True)
    return _capacity_window(priced, curve)


def _capacity_window(frame: pd.DataFrame, curve: EssTargetCurve) -> pd.DataFrame:
    """최적 용량 둘레만 남긴다. 최적이 없으면 그대로 돌려준다."""
    if frame.empty or curve.best is None:
        return frame
    center = curve.best.nameplate_capacity_kwh
    if center <= 0:
        return frame
    low, high = CAPACITY_WINDOW
    capacity = frame["정격 용량(kWh)"]
    window = frame[(capacity >= center * low) & (capacity <= center * high)]
    return window.reset_index(drop=True) if len(window) >= 2 else frame


# **목표 선택 표를 없앴다** (26세션 1-3). 곡선 아래에 대표 지점 대여섯 줄을
# 두었는데, 그림 하나로 고르는 자리에 표까지 두니 읽을 것이 둘이 되었다.
# 최적 지점의 사양은 아래 지표 카드가 낸다 — 같은 값을 두 번 적지 않는다.


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


# ===================================================================== 15세션 · 2단계 그래프
#
# **하루 곡선을 쓰는 셋(역률·태양광·ESS)은 같은 대표일을 본다** (:mod:`kwise.report.days`).
# 카드마다 다른 날을 그리면 세 그림을 나란히 놓고도 견줄 수 없다.


DAY_TYPE_LABELS: dict[str, str] = {
    "weekday": "평일",
    "saturday": "토요일",
    "sunday": "일요일",
    "holiday": "공휴일",
}


#: 그룹 막대의 세 항목. **순서가 곧 읽는 순서다** (17세션 1-2).
TARIFF_PARTS: tuple[str, ...] = ("기본요금", "전력량요금", "합계")


def tariff_option_frame(switch: TariffSwitchResult) -> pd.DataFrame:
    """요금제별 기본요금·전력량요금·합계 (15세션 2-1 · 17세션 1-1).

    **제도 순서(Ⅰ·Ⅱ·Ⅲ)로 늘어놓는다.** 절감액 순으로 정렬하면 자료마다
    Ⅱ·Ⅲ·Ⅰ 처럼 뒤섞여 "왜 이 순서인가" 를 먼저 묻게 된다. 어느 쪽이 유리한지는
    표식과 차액 차트가 말한다.
    """
    current = switch.current.key
    best = switch.best.key
    ordered = sorted(switch.quotes, key=lambda quote: option_sort_key(quote.selection.option))
    rows: list[dict[str, object]] = []
    for quote in ordered:
        mark = "현행" if quote.key == current else ("최적" if quote.key == best else "")
        rows.append(
            {
                "요금제": option_label(quote.selection.option),
                "표식": mark,
                "기본요금(원)": quote.base_won,
                "전력량요금(원)": quote.energy_won,
                "합계(원)": quote.total_won,
                "현행 대비(원)": quote.total_won - switch.current.total_won,
            }
        )
    return pd.DataFrame(rows)


def tariff_option_long_frame(switch: TariffSwitchResult) -> pd.DataFrame:
    """**그룹 막대**용 긴 형식 (17세션 1-2).

    쌓지 않고 나란히 세운다. 누적으로 그리면 합계는 보이지만 **기본요금끼리·
    전력량요금끼리 견줄 수가 없다** — 선택요금은 그 둘을 맞바꾸는 제도라 정작
    봐야 할 것이 항목별 크기다.

    **상세를 모르는 요금제도 막대를 세운다.** 값이 없으면 합계 하나만 세우고
    그 사실을 적는다 — 빼 버리면 선택지가 조용히 사라진다.
    """
    rows: list[dict[str, object]] = []
    for _, row in tariff_option_frame(switch).iterrows():
        base, energy = row["기본요금(원)"], row["전력량요금(원)"]
        parts: tuple[tuple[str, float], ...] = (
            (
                ("기본요금", base),
                ("전력량요금", energy),
                ("합계", row["합계(원)"]),
            )
            if pd.notna(base) and pd.notna(energy)
            else (("합계", row["합계(원)"]),)
        )
        rows.extend(
            {"요금제": row["요금제"], "표식": row["표식"], "구분": name, "원": float(value)}
            for name, value in parts
        )
    return pd.DataFrame(rows)


def tariff_delta_frame(switch: TariffSwitchResult) -> pd.DataFrame:
    """**현행 대비 차액만** 그리는 표 (17세션 1-3).

    35억에서 5천만원이 줄어드는 것을 0 부터 시작하는 한 축에 그리면 막대 셋이
    같은 높이로 보인다. 차액만 떼어 0 을 기준으로 좌우(위아래)로 뻗게 하면
    같은 사실이 한눈에 읽힌다 — **이쪽이 축을 자르는 것보다 명확하다.**

    부호 규약은 「현행 대비」 다. 음수가 절감이다.
    """
    frame = tariff_option_frame(switch)
    return pd.DataFrame(
        {
            "요금제": frame["요금제"],
            "표식": frame["표식"],
            "현행 대비(원)": frame["현행 대비(원)"],
            "방향": [
                "절감" if value < 0 else ("증가" if value > 0 else "현행")
                for value in frame["현행 대비(원)"]
            ],
        }
    )


def dr_daily_frame(profile: DrProfile) -> pd.DataFrame:
    """연간 일별 운영시간대 평균 부하 (15세션 2-2).

    **기준선 근처로 내려온 평일이 감축 가능일이다.** 요일 갈래를 색으로 나누고
    저부하 평일에 표식을 찍으면 그 사실이 그림 하나로 읽힌다.
    """
    series = profile.daily_window_kw
    if series is None or not len(series):
        return pd.DataFrame(columns=["날짜", "구분", "운영시간대 평균(kW)", "저부하 평일"])
    eligible = set(profile.eligible_day_index)
    low = set(profile.low_load_days)
    rows: list[dict[str, object]] = []
    for day, value in series.items():
        stamp = pd.Timestamp(day)
        if stamp in eligible:
            kind = "weekday"
        elif stamp.weekday() == 5:
            kind = "saturday"
        elif stamp.weekday() == 6:
            kind = "sunday"
        else:
            kind = "holiday"
        rows.append(
            {
                "날짜": stamp.date(),
                "구분": DAY_TYPE_LABELS[kind],
                "운영시간대 평균(kW)": float(value),
                "저부하 평일": stamp in low,
            }
        )
    return pd.DataFrame(rows)


def power_triangle_frame(result: PowerFactorResult) -> pd.DataFrame:
    """전력삼각형 — 개선 전후 (15세션 2-3).

        유효전력 P = 1 (기준화)
        무효전력 Q = P × tan(acos(역률))
        피상전력 S = √(P² + Q²) = P ÷ 역률

    **유효전력을 1 로 두고 견준다.** 역률 개선 설비는 무효전력만 줄이므로 유효전력은
    그대로다 — 각이 좁아지는 것이 개선의 전부다.
    """
    rows: list[dict[str, object]] = []
    for label, pct in (("개선 전", result.current_pct), ("개선 후", result.target_pct)):
        ratio = max(min(pct / 100.0, 1.0), 1e-6)
        angle = math.degrees(math.acos(ratio))
        rows.append(
            {
                "구분": label,
                "역률(%)": pct,
                "유효전력": 1.0,
                "무효전력": math.tan(math.acos(ratio)),
                "피상전력": 1.0 / ratio,
                "각도(도)": angle,
            }
        )
    return pd.DataFrame(rows)


def power_factor_day_frame(
    usage: UsageData,
    day: dt.date,
    *,
    current_pct: float,
    target_pct: float,
) -> pd.DataFrame:
    """대표일의 15분 부하와 역률 추정치 (15세션 2-3).

    **역률은 시각마다 재지 않는다** — 무효전력 실측이 없다. 주간(08~22시)에는
    입력 역률을, 야간에는 진상 간주(추가 0)를 그려 **어느 구간이 요금 대상인지**를
    보인다. 곡선이 아니라 창을 보여 주는 그림이다.
    """
    interval = usage.meta.interval_minutes
    frame = day_profile(usage.kw, day, interval, name="부하(kW)")
    if frame.empty:
        return pd.DataFrame(columns=["시각", "부하(kW)", "구간", "역률(%)", "도입 후 역률(%)"])
    start, end = day_window()
    hours = pd.DatetimeIndex(frame["시각"]).hour
    daytime = (hours >= start) & (hours < end)
    frame["구간"] = ["주간 (지상 기준)" if flag else "야간 (진상 기준)" for flag in daytime]
    frame["역률(%)"] = [current_pct if flag else float("nan") for flag in daytime]
    frame["도입 후 역률(%)"] = [target_pct if flag else float("nan") for flag in daytime]
    return frame


def solar_day_frame(
    usage: UsageData,
    generation_kw: pd.Series,
    day: dt.date,
) -> pd.DataFrame:
    """대표일의 원부하·순부하·발전량 (15세션 2-4 ②).

    **피크가 얼마나 내려가는지**가 이 그림의 전부다. 세 선을 겹쳐 그린다.
    """
    interval = usage.meta.interval_minutes
    load = day_profile(usage.kw, day, interval, name="원부하(kW)")
    if load.empty:
        return pd.DataFrame(columns=["시각", "원부하(kW)", "발전량(kW)", "순부하(kW)"])
    aligned = generation_kw.reindex(pd.DatetimeIndex(usage.kw.index)).fillna(0.0)
    gen = day_profile(aligned, day, interval, name="발전량(kW)")
    load["발전량(kW)"] = gen["발전량(kW)"].to_numpy()
    load["순부하(kW)"] = (load["원부하(kW)"] - load["발전량(kW)"]).clip(lower=0.0)
    return load


#: 피크 확대 차트가 보일 앞뒤 시간 (17세션 3-5 · 23세션 6절).
#:
#: **화면(altair)과 보고서(png)가 같은 값을 쓴다.** 프레임 계층에 두는 이유는
#: 한쪽만 고치면 두 그림의 가로 범위가 갈라지기 때문이다.
PEAK_ZOOM_HOURS = 3


def peak_window(frame: pd.DataFrame, *, column: str = "원부하(kW)") -> pd.DataFrame:
    """피크 앞뒤 :data:`PEAK_ZOOM_HOURS` 시간만 남긴다. 비어 있으면 그대로.

    **관측이 하나도 없는 날도 있다** (25세션 1절). 결측이 온종일인 날을 「일일 곡선
    대표일」 로 고르면 여기서 ``ValueError: Encountered all NA values`` 가 나 화면이
    통째로 죽었다. 잘라 낼 피크가 없으므로 **빈 프레임**을 돌려준다 — 부르는 쪽
    셋이 모두 빈 프레임을 「그릴 것이 없다」 로 처리한다.
    """
    if frame.empty or not frame[column].notna().any():
        return frame.iloc[0:0]
    peak_at = pd.Timestamp(frame.loc[frame[column].idxmax(), "시각"])
    window = pd.Timedelta(hours=PEAK_ZOOM_HOURS)
    times = pd.DatetimeIndex(frame["시각"])
    return frame[(times >= peak_at - window) & (times <= peak_at + window)]


def solar_annual_frame(usage: UsageData, generation_kw: pd.Series) -> pd.DataFrame:
    """연간 일별 사용량·자가소비·잉여 (15세션 2-4 ①).

    **계통에서 받는 양이 줄어드는 모습**을 보인다. 자가소비분과 잉여분을 갈라
    적어야 "발전량을 다 쓰는가" 가 함께 읽힌다.
    """
    interval = usage.meta.interval_minutes
    hours = interval / 60.0
    load = usage.kw.dropna()
    index = pd.DatetimeIndex(load.index)
    gen = generation_kw.reindex(index).fillna(0.0)
    days = slot_start(index, interval).normalize()

    self_used = pd.concat([load, gen], axis=1).min(axis=1).clip(lower=0.0)
    surplus = (gen - load).clip(lower=0.0)
    grid = (load - gen).clip(lower=0.0)
    frame = pd.DataFrame(
        {
            "day": days,
            "사용량(kWh)": load.to_numpy(dtype=float) * hours,
            "계통 수전(kWh)": grid.to_numpy(dtype=float) * hours,
            "자가소비(kWh)": self_used.to_numpy(dtype=float) * hours,
            "잉여(kWh)": surplus.to_numpy(dtype=float) * hours,
            # **발전량은 자가소비 + 잉여다** (23세션 5절). 일별 발전량 그래프가
            # 이 열 하나만 그린다 — 수전량과 겹쳐 그리니 발전이 묻혔다.
            "발전량(kWh)": gen.to_numpy(dtype=float) * hours,
        }
    )
    daily = frame.groupby("day").sum().reset_index()
    daily["날짜"] = [pd.Timestamp(value).date() for value in daily["day"]]
    return daily[
        ["날짜", "사용량(kWh)", "계통 수전(kWh)", "자가소비(kWh)", "잉여(kWh)", "발전량(kWh)"]
    ]


def daily_temperature_frame(usage: UsageData, temperature: pd.Series) -> pd.DataFrame:
    """연간 일별 사용량과 일평균 기온 (30세션 4절).

    **관측이 있는 날만 낸다.** 결측일을 0 kWh 로 그리면 그날 기온이 무엇이든
    사용량이 바닥으로 떨어져, 기온과 사용량의 관계가 있지도 않은 곳에서 보인다.

    Args:
        temperature: 시간별 기온 (℃). 인덱스는 tz 유무를 가리지 않는다 —
            **여기서 벗겨 날짜로만 묶는다.** 부하 인덱스는 tz-naive 지방시다.
    """
    interval = usage.meta.interval_minutes
    load = usage.kw.dropna()
    index = pd.DatetimeIndex(load.index)
    days = slot_start(index, interval).normalize()
    daily_kwh = (
        pd.Series(load.to_numpy(dtype=float) * (interval / 60.0), index=days).groupby(level=0).sum()
    )

    stamps = pd.DatetimeIndex(temperature.index)
    if stamps.tz is not None:
        stamps = stamps.tz_localize(None)
    daily_temp = (
        pd.Series(temperature.to_numpy(dtype=float), index=stamps.normalize())
        .groupby(level=0)
        .mean()
    )

    frame = pd.DataFrame({"사용량(kWh)": daily_kwh, "일평균 기온(℃)": daily_temp}).dropna()
    frame.index.name = "day"
    frame = frame.reset_index()
    frame["날짜"] = [pd.Timestamp(value).date() for value in frame["day"]]
    return frame[["날짜", "사용량(kWh)", "일평균 기온(℃)"]]


def dispatch_schedule(frame: pd.DataFrame) -> tuple[str, str]:
    """대표일의 (충전 문구, 방전 문구) — **그림 대신 글로 낸다** (23세션 6절).

    17세션의 아래 칸(충전＋·방전−)은 위 칸과 종속이라 그림이 둘일 필요가 없었다.
    시각 범위와 양은 글이 더 정확하다 — 막대에서 시각을 눈으로 읽어 내야 했다.

    끊긴 구간이 여럿이면 **처음과 끝**으로 묶어 적는다. 15분 단위 구간을 다
    나열하면 그림보다 못한 목록이 된다.
    """

    def span(column: str) -> str:
        active = frame[frame[column] > 0]
        if active.empty:
            return ""
        times = pd.DatetimeIndex(active["시각"])
        return f"{times.min():%H:%M}–{times.max():%H:%M}"

    return span("충전(kW)"), span("방전(kW)")


def ess_day_frame(usage: UsageData, dispatch: DispatchResult, day: dt.date) -> pd.DataFrame:
    """대표일의 ESS 충·방전 구조 (15세션 2-5).

    충전은 ``+``, 방전은 ``−`` 로 부호를 갈라 **언제 담고 언제 쓰는지**를 보인다.

    **계시별 시간대 열을 걷어냈다** (26세션 2-1). 화면이 그것을 배경 띠로 깔았는데
    확대한 창이 한 시간대 안에 들어가 그림이 한 색으로 칠해졌고, 보고서 png 는
    애초에 쓰지 않았다 — 두 그림이 같아야 한다는 규약을 배경을 빼는 쪽으로 맞췄다.
    """
    interval = usage.meta.interval_minutes
    load = day_profile(usage.kw, day, interval, name="원부하(kW)")
    if load.empty:
        return pd.DataFrame(columns=["시각", "원부하(kW)", "순부하(kW)", "충전(kW)", "방전(kW)"])
    net = day_profile(dispatch.net_kw, day, interval, name="순부하(kW)")
    load["순부하(kW)"] = net["순부하(kW)"].to_numpy()
    delta = load["순부하(kW)"] - load["원부하(kW)"]
    load["충전(kW)"] = delta.clip(lower=0.0)
    load["방전(kW)"] = (-delta).clip(lower=0.0)
    load["목표(kW)"] = dispatch.target_kw
    return load


def surplus_daily_frame(usage: UsageData, surplus_kw: pd.Series) -> pd.DataFrame:
    """연간 일별 잉여량 (15세션 2-6).

    **주말·공휴일을 갈라 적는다** — 잉여가 거기 몰리면 자가소비가 어려운 구조다.
    """
    interval = usage.meta.interval_minutes
    hours = interval / 60.0
    index = pd.DatetimeIndex(usage.kw.index)
    aligned = surplus_kw.reindex(index).fillna(0.0)
    starts = slot_start(index, interval)
    frame = pd.DataFrame(
        {
            "day": starts.normalize(),
            "weekday": starts.weekday,
            "잉여(kWh)": aligned.to_numpy(dtype=float) * hours,
        }
    )
    daily = frame.groupby(["day", "weekday"], as_index=False)["잉여(kWh)"].sum()
    daily["날짜"] = [pd.Timestamp(value).date() for value in daily["day"]]
    daily["구분"] = [
        "토요일" if value == 5 else ("일요일" if value == 6 else "평일")
        for value in daily["weekday"]
    ]
    return daily[["날짜", "구분", "잉여(kWh)"]]
