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
    TariffSwitchResult,
    annualize,
)
from kwise.report.columns import option_label
from kwise.report.days import day_profile
from kwise.tariff import day_window, option_sort_key

__all__ = [
    "BAND_LABELS",
    "CAPACITY_ROWS",
    "DAY_TYPE_LABELS",
    "TARIFF_PARTS",
    "band_frame",
    "combination_frame",
    "dr_daily_frame",
    "ess_day_frame",
    "ess_target_frame",
    "ess_target_table",
    "hourly_profile_frame",
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


#: 용량 표에 세울 지점 수 (17세션 3-3). 스무 줄은 아무도 읽지 않고, 셋은 곡선의
#: 모양이 보이지 않는다.
CAPACITY_ROWS = 5


def solar_capacity_table(
    curve: SolarCurve, *, verdict: CapacityVerdict | None = None, rows: int = CAPACITY_ROWS
) -> pd.DataFrame:
    """**대표 용량 몇 개를 표로** (17세션 3-3).

    20단계 곡선은 Excel 로 보내고 화면에는 의미 있는 지점만 남긴다. **상한과
    최적은 반드시 넣고** 나머지는 고르게 흩어 뽑는다 — 그래야 "용량을 키우면
    무엇이 어떻게 변하는가" 가 표 하나로 읽힌다.

    금액과 발전량은 **12개월 환산**이다. 회수기간이 연 단위라 같은 축에 두려면
    기간값을 그대로 쓸 수 없다.
    """
    usable = [point for point in curve.points if point.capacity_kwp > 0]
    if not usable:
        return pd.DataFrame(
            columns=[
                "용량(kWp)",
                "연간 발전량(kWh)",
                "자가소비율",
                "기본요금 절감(원)",
                "전력량요금 절감(원)",
                "투자비(원)",
                "회수기간(년)",
                "표식",
            ]
        )
    months = curve.base_fee_months
    limit = usable[-1]
    best = verdict.best if verdict is not None else None
    keep = {limit.capacity_kwp}
    if best is not None:
        keep.add(best.capacity_kwp)
    # 남은 자리를 고르게 채운다. 이미 든 것은 건너뛴다.
    span = max(rows - len(keep), 0)
    if span:
        step = max(len(usable) // (span + 1), 1)
        for index in range(step, len(usable), step):
            if len(keep) >= rows:
                break
            keep.add(usable[index - 1].capacity_kwp)
    picked = sorted(
        (point for point in usable if point.capacity_kwp in keep),
        key=lambda point: point.capacity_kwp,
    )
    marks = {limit.capacity_kwp: "면적 상한"}
    if best is not None:
        marks[best.capacity_kwp] = (
            "최적 · 면적 상한" if best.capacity_kwp == limit.capacity_kwp else "최적"
        )
    return pd.DataFrame(
        {
            "용량(kWp)": [point.capacity_kwp for point in picked],
            "연간 발전량(kWh)": [annualize(point.generation_kwh, months) for point in picked],
            "자가소비율": [point.self_consumption_ratio for point in picked],
            "기본요금 절감(원)": [annualize(point.base_saving_won, months) for point in picked],
            "전력량요금 절감(원)": [annualize(point.energy_saving_won, months) for point in picked],
            "투자비(원)": [point.investment_won for point in picked],
            "회수기간(년)": [point.payback_years for point in picked],
            "표식": [marks.get(point.capacity_kwp, "") for point in picked],
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
    """곡선 아래에 두는 대표 지점 표. **최소 지점이 가운데 온다.**

    **사양만 싣는다** (18세션 1절). 투자비·절감액·회수기간을 함께 실었더니 최소
    지점의 값이 카드의 결론과 어긋났다 — 곡선은 기본요금 절감만 본 개략치이고
    카드는 요금을 다시 계산한 값이라, 같은 목표에서 24.6년과 30.8년이 한 화면에
    나왔다. 계산은 각각 옳아도 사용자에게는 그냥 불일치다. 돈에 관한 숫자는
    카드 하나만 내고, 표에는 **카드와 값이 같은** 출력·정격 용량·방전시간을 둔다.
    """
    picked = curve.highlights()
    return pd.DataFrame(
        {
            "목표(kW)": [item.target_kw for item in picked],
            "저감량(kW)": [item.reduction_kw for item in picked],
            "필요 출력(kW)": [item.power_kw for item in picked],
            "정격 용량(kWh)": [item.nameplate_capacity_kwh for item in picked],
            "방전시간(h)": [item.discharge_hours for item in picked],
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
    """피크 앞뒤 :data:`PEAK_ZOOM_HOURS` 시간만 남긴다. 비어 있으면 그대로."""
    if frame.empty:
        return frame
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


def ess_day_frame(
    usage: UsageData,
    dispatch: DispatchResult,
    day: dt.date,
    *,
    bands: pd.Series | None = None,
) -> pd.DataFrame:
    """대표일의 ESS 충·방전 구조 (15세션 2-5).

    충전은 ``+``, 방전은 ``−`` 로 부호를 갈라 **언제 담고 언제 쓰는지**를 보인다.
    계시별 시간대를 함께 넣어 왜 그 시각인지가 배경으로 읽히게 한다.
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
    if bands is not None:
        picked = day_profile(bands.astype(object), day, interval, name="band")
        load["시간대"] = [BAND_LABELS.get(str(value), str(value)) for value in picked["band"]]
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
