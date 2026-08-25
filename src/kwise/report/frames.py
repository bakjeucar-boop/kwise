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
    AREA_EXCEEDED,
    MARGIN_SHORT,
    RECOMMENDED,
    SELECTED_CAPACITY,
    SHORTEST_PAYBACK,
    SPEC_TABLE_ROWS,
    TARGET_MISSED,
    TIED_PAYBACK,
    CapacityVerdict,
    ContractAdjustment,
    DispatchResult,
    EssCostModel,
    EssOptimum,
    EssOptimumPoint,
    PowerFactorResult,
    SolarCurve,
    SolarPoint,
    TariffSwitchResult,
    annualize,
    payback_text,
    payback_tie_ratio,
)
from kwise.report.columns import option_label
from kwise.report.days import day_profile
from kwise.report.notices import format_won
from kwise.tariff import day_window, option_sort_key

__all__ = [
    "BAND_FRAME_COLUMNS",
    "BAND_LABELS",
    "CAPACITY_COLUMNS",
    "CAPACITY_ROWS",
    "DAY_TYPE_LABELS",
    "ESS_SPEC_CAPTION",
    "NO_REDUCTION_CAPTION",
    "ESS_SPEC_HEADER",
    "ESS_SPEC_ROWS",
    "MONTHLY_CHARGE_PARTS",
    "TARIFF_PARTS",
    "band_frame",
    "capacity_band_frame",
    "combination_frame",
    "contract_headroom_frame",
    "daily_temperature_frame",
    "daily_usage_frame",
    "dr_daily_frame",
    "ess_day_frame",
    "ess_spec_caption",
    "ess_spec_frame",
    "ess_spec_groups",
    "ess_spec_rows",
    "ess_spec_targets",
    "hourly_profile_frame",
    "month_labels",
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
    "temperature_mean_frame",
    "top_hour_frame",
]

BAND_LABELS: dict[str, str] = {"light": "경부하", "mid": "중간부하", "peak": "최대부하"}


#: 달 축 라벨 (33세션 1절). **해는 바뀔 때만 적는다** — 열세 달짜리 자료에
#: 「4월」 이 둘 생기면 어느 쪽이 앞인지 알 수 없고, 달마다 해를 적으면 라벨이
#: 두 배가 되어 눕는다. 날짜 축의 :data:`~kwise.ui.charts.DATE_LABEL_EXPR` 와
#: 같은 규칙이다.
def month_labels(periods: Sequence[object]) -> list[str]:
    """``[2023-04, 2023-05, …]`` → ``["2023년 4월", "5월", …]`` (33세션 1절)."""
    labels: list[str] = []
    year: int | None = None
    for value in periods:
        stamp = pd.Period(str(value), freq="M")
        head = f"{stamp.year}년 " if stamp.year != year else ""
        labels.append(f"{head}{stamp.month}월")
        year = stamp.year
    return labels


def monthly_peak_frame(peak: PeakProfile) -> pd.DataFrame:
    """월별 최대수요와 요금적용전력 기준값.

    ``demand_basis_kw`` 는 **경부하를 뺀 대상 시간대의 최대**다 (5.2 ①).
    관측 최대와 나란히 두어야 "밤 피크는 요금적용전력이 아니다" 가 보인다.
    """
    frame = peak.monthly.reset_index()
    # **축에 적히는 이름은 한국식이다** (33세션 1절).
    frame["월"] = month_labels(frame["month"].tolist())
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


#: 계시별 사용량 구성 표의 열. **빈 표도 같은 열을 낸다** — 없는 계절에서
#: 열이 사라지면 그림 쪽이 KeyError 로 죽는다.
BAND_FRAME_COLUMNS: dict[str, list[object]] = {
    "시간대": [],
    "사용량(kWh)": [],
    "비중": [],
    "라벨": [],
    "순서": [],
}


def band_frame(structure: ChargeStructure, *, season: str | None = None) -> pd.DataFrame:
    """계시별 사용량 구성. ``season`` 을 주면 그 계절만 (30세션 5-2).

    비중은 **그 계절 안에서 다시 잰다.** 전체 대비로 두면 세 계절의 막대가 각각
    3분의 1 길이로 그려져 무엇의 구성인지 알 수 없다.
    """
    kwh = structure.band_kwh
    if season is not None:
        wide = structure.band_season_kwh
        if wide.empty or season not in wide.index:
            return pd.DataFrame(BAND_FRAME_COLUMNS)
        kwh = wide.loc[season]
    total = float(kwh.sum())
    names = [BAND_LABELS.get(str(band), str(band)) for band in kwh.index]
    shares = [float(value) / total if total else 0.0 for value in kwh]
    return pd.DataFrame(
        {
            "시간대": names,
            "사용량(kWh)": kwh.astype(float).to_numpy(),
            "비중": shares,
            # **이름과 비중을 한 조각에 적는다** (34세션 1절). 원이 넷이라 범례를
            # 달면 같은 이름이 네 번 실린다.
            "라벨": [
                f"{name} {share * 100:.0f}%" for name, share in zip(names, shares, strict=True)
            ],
            # 조각 순서. 자료 순서에 맡기면 계절마다 색이 돈다.
            "순서": list(range(len(names))),
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
    "절감액(원)",
    "투자비(원)",
    "회수기간(년)",
    "표식",
)
"""**절감 열은 하나다** (51세션 2절).

50세션까지는 「기본요금 절감」·「전력량요금 절감」 둘로 갈라 두었다. 그런데 이
표에서 줄을 **가르는 것은 합계**다 — 회수기간이 용량과 거의 무관해 세 줄이 같은
값을 내는데(16세션), 절감액은 세 배 차이 난다. 두 수를 사람이 더하게 두면 그
차이가 한눈에 안 들어온다.

**가른 값이 사라진 것은 아니다** — 카드의 절감액 툴팁·계산 근거 표·Excel
「태양광 용량 곡선」 시트가 그대로 낸다. 열이 하나 줄어 표가 가벼워졌다.
"""


def _capacity_rows(
    picked: dict[float, SolarPoint], marks: dict[float, list[str]], rows: int
) -> list[SolarPoint]:
    """표에 세울 줄을 고른다 — **표식이 붙은 줄은 절대 버리지 않는다** (51세션 4절).

    50세션까지는 지점을 오름차순으로 늘어놓고 앞에서 ``rows`` 개만 잘랐다.
    권장 용량이 면적 상한과 다르면 지점이 여섯이 되는데, **오름차순이라 가장 큰
    것 — 곧 「선정 용량」 — 이 버려졌다.** 카드가 낸 용량이 비교 표에 없는 것이라
    매뉴얼의 「면적 상한과 「권장」 줄은 항상 들어간다」 와도 어긋났다.

    버릴 것은 **표식 없는 줄**뿐이고, 그중에서도 **옆 줄과 가장 가까운 것**부터
    버린다 — 곡선 모양을 가장 적게 해친다. 소형 자료에서 56 kWp 가 40.2 kWh 에서
    16 kWp 밖에 안 떨어져 먼저 빠지고, 104 kWp 가 남아 아래쪽 모양을 지킨다.
    """
    ordered = sorted(picked.values(), key=lambda point: point.capacity_kwp)
    limit_rows = max(rows, len(marks))
    keep = list(ordered)
    while len(keep) > limit_rows:
        loose = [
            index
            for index, point in enumerate(keep)
            if not marks.get(point.capacity_kwp)
        ]
        if not loose:
            break  # 표식뿐이면 자르지 않는다 — 표식이 곧 세울 이유다

        def nearest_gap(index: int) -> float:
            here = keep[index].capacity_kwp
            gaps = [
                abs(keep[other].capacity_kwp - here)
                for other in (index - 1, index + 1)
                if 0 <= other < len(keep)
            ]
            return min(gaps) if gaps else float("inf")

        keep.pop(min(loose, key=nearest_gap))
    return keep


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

    marks: dict[float, list[str]] = {limit.capacity_kwp: [SELECTED_CAPACITY]}
    if best is not None:
        # **「최적」 도 「최단 회수기간」 도 아니다** (49·50세션). 태양광은 동률
        # 처리를 거쳐 고르므로 「권장」 이다 — 판정 근거는 표 아래 한 줄이 적는다
        # (:func:`~kwise.measures.payback_tie_note`).
        #
        # **선정 용량과 같아도 찍는다** (51세션 1절). 50세션은 둘이 다를 때만
        # 찍었는데, 대형 자료는 권장이 곧 면적 상한이라 **각주가 말하는 「권장」 이
        # 표에 없었다.** 둘은 다른 사실이다 — 「지을 수 있는 가장 큰 것」 과
        # 「권하는 것」 이 같다는 것도 적을 값어치가 있다.
        marks.setdefault(best.capacity_kwp, []).append(
            verdict.pick_label if verdict is not None else RECOMMENDED
        )
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

    # **회수기간이 사실상 같은 줄을 묶는다** (51세션 2절). kWp당 단가면 투자비와
    # 절감액이 함께 용량에 비례해 회수기간이 용량과 거의 무관해진다 (16세션) —
    # 대형 자료에서 56·104·160 kWp 가 모두 6.2~6.3년이다. 표가 그대로 보이면
    # 「어느 것을 골라도 같다」 로 읽히는데 **절감액은 세 배 차이 난다.**
    #
    # **폭은 고른 자리가 아니라 곡선의 최소에서 잰다** — `capacity_verdict` 와
    # 똑같은 식이라야 한다. 고른 자리에서 재면 밴드가 한 번 더 넓어져, 규칙이
    # 동률로 보지 않은 줄에 「동률」 이 붙는다 (소형에서 56 kWp 가 그랬다).
    priced = [point.payback_years for point in usable if point.payback_years is not None]
    if best is not None and priced:
        ceiling = min(priced) * (1.0 + payback_tie_ratio())
        for point in picked.values():
            years = point.payback_years
            if years is None or point.capacity_kwp == best.capacity_kwp:
                continue
            if years <= ceiling:
                marks.setdefault(point.capacity_kwp, []).append(TIED_PAYBACK)

    ordered = _capacity_rows(picked, marks, rows)

    def area(capacity_kwp: float) -> float | None:
        if gcr is None or area_per_kwp_m2 is None or gcr <= 0:
            return None
        return capacity_kwp * area_per_kwp_m2 / gcr

    def mark(point: SolarPoint) -> str:
        labels = list(marks.get(point.capacity_kwp, ()))
        needed = area(point.capacity_kwp)
        if area_limit_m2 is not None and needed is not None and needed > area_limit_m2 + 1e-6:
            labels.append(AREA_EXCEEDED)
        return " · ".join(labels)

    return pd.DataFrame(
        {
            "용량(kWp)": [point.capacity_kwp for point in ordered],
            "필요 면적(m²)": [area(point.capacity_kwp) for point in ordered],
            "연간 발전량(kWh)": [annualize(point.generation_kwh, months) for point in ordered],
            "자가소비율": [point.self_consumption_ratio for point in ordered],
            # **합계 하나로 낸다** (51세션 2절). 줄을 가르는 것이 이 값이다.
            "절감액(원)": [annualize(point.total_saving_won, months) for point in ordered],
            "투자비(원)": [point.investment_won for point in ordered],
            "회수기간(년)": [point.payback_years for point in ordered],
            "표식": [mark(point) for point in ordered],
        }
    )


#: 회수기간 곡선에서 **최적의 몇 배까지 보일지** (26세션 1-2).
#:
#: 목표별 사양 표에 세울 줄 수 (46세션). **대여섯 줄이다** — 21점을 다 세우면
#: 읽을 것이 너무 많고, 셋이면 「목표를 낮추면 나빠진다」 가 안 읽힌다.
#:
#: **값은 :data:`~kwise.measures.ess.SPEC_TABLE_ROWS` 하나다** (48세션). 성립하는
#: 목표가 없을 때 정밀화가 재는 참고 지점 수와 같아야 표에 빈 줄이 생기지 않는다.
ESS_SPEC_ROWS = SPEC_TABLE_ROWS


def contract_headroom_frame(contract: ContractAdjustment) -> pd.DataFrame:
    """계약전력과 요금적용전력의 **틈** (53세션 6-3).

    한 줄짜리 표다 — 그림 하나를 그리는 데 필요한 값만 든다. **여기서 다시
    계산하지 않는다**: 화면 카드가 쓰는 것과 같은 셋(계약전력·요금적용전력·
    권장)에 그 차이를 더할 뿐이다.

    ``여유(kW)`` 는 음수일 수 있다 — 요금적용전력이 계약전력을 넘은 자료다
    (초과 위약). 그림이 그 사실을 감추지 않게 그대로 둔다.
    """
    return pd.DataFrame(
        {
            "구분": ["계약전력"],
            "요금적용전력(kW)": [contract.billing_demand_kw],
            "여유(kW)": [contract.contract_kw - contract.billing_demand_kw],
            "계약전력(kW)": [contract.contract_kw],
            "권장 계약전력(kW)": [contract.suggested_contract_kw],
        }
    )


def ess_spec_groups(
    points: Sequence[EssOptimumPoint],
) -> tuple[tuple[EssOptimumPoint, tuple[float, float]], ...]:
    """**같은 설비로 덮이는 목표를 묶는다** (50세션 3-6).

    격자를 쓰면 목표 여럿이 한 사양으로 뭉친다. 그것이 정보다 — 「이 목표 범위는
    같은 설비로 덮인다」. 대형 샘플에서 21점이 15사양으로 줄었다.

    **대표는 그 사양이 버티는 가장 깊은 목표다.** 같은 설비를 샀으면 그것이
    견디는 가장 낮은 목표로 돌리는 것이 맞다 — 저감량·절감액이 가장 크고
    회수기간이 가장 짧은 줄이 자동으로 대표가 된다. 굳이 얕게 돌릴 까닭이 없다.

    Returns:
        ``(대표 점, (목표 하한, 목표 상한))`` 을 목표 내림차순으로.
        **최소 규격에 못 미치는 점은 뺀다** (50세션 3-3) — 살 물건이 없다.
    """
    usable = [point for point in points if not point.below_min_power]
    groups: dict[tuple[float, float], list[EssOptimumPoint]] = {}
    for point in usable:
        groups.setdefault(point.spec_key, []).append(point)
    rows = [
        (
            min(members, key=lambda item: item.target_kw),
            (
                min(item.target_kw for item in members),
                max(item.target_kw for item in members),
            ),
        )
        for members in groups.values()
    ]
    return tuple(sorted(rows, key=lambda row: -row[0].target_kw))


def ess_spec_targets(points: Sequence[EssOptimumPoint], best_target_kw: float) -> tuple[int, ...]:
    """표에 세울 **사양**의 자리 번호 — 고른 자리를 가운데 두고 양쪽으로 벌린다.

    창의 양 끝과 고른 자리는 반드시 넣는다. 끝을 빼면 「목표를 낮추면 회수가
    나빠진다」 가 안 보이고, 고른 자리를 빼면 표식을 찍을 줄이 없다. 남은 자리는
    **가장 넓은 틈부터** 반으로 갈라 채운다 — 한쪽에만 몰리지 않는다.

    **50세션부터 자리 번호는 사양 묶음의 것이다** (:func:`ess_spec_groups`).
    """
    ordered = list(range(len(points)))
    if not ordered:
        return ()
    best = next(
        (rank for rank, i in enumerate(ordered) if points[i].target_kw == best_target_kw), 0
    )
    chosen = sorted({0, best, len(ordered) - 1})
    while len(chosen) < min(ESS_SPEC_ROWS, len(ordered)):
        gaps = [(chosen[i + 1] - chosen[i], i) for i in range(len(chosen) - 1)]
        width, at = max(gaps)
        if width < 2:
            break
        chosen = sorted({*chosen, chosen[at] + width // 2})
    return tuple(ordered[rank] for rank in chosen)


def ess_spec_frame(optimum: EssOptimum, *, baseline_demand_kw: float) -> pd.DataFrame:
    """목표별 사양 표 — **곡선을 대신한다** (46세션).

    23~45세션은 회수기간 곡선을 그렸다. 곡선은 개략 산정이라 값이 카드와 달랐고
    (샘플 26.0년 대 30.8년), 한 화면에 두 숫자가 남았다. 비율을 곱해 올리는
    방법을 재 봤더니 자료마다 1.10~3.18 로 갈려 한 곱수로는 못 옮긴다.

    **표는 전부 카드 기준 참값이다.** 정밀화가 이미 잰 점을 그대로 쓰므로 추가
    계산이 없다. 열 구성과 「표식」 규약은 **태양광 용량 표와 같다.**

    26세션이 없앤 「대표 지점 표」와 다르다 — 그때는 곡선 **아래** 표까지 두어
    읽을 것이 둘이었다. 지금은 표 하나뿐이다.

    **뭉친 줄을 합친다** (50세션 3-6). 격자를 쓰면 목표 여럿이 한 사양으로 묶이고,
    그 범위가 곧 정보다 — 「이 목표 범위는 같은 설비로 덮인다」.

    **「최소 규모」 표식이 없어졌다** (50세션 3-4). 다섯 줄에 모두 붙어 구별하는
    힘이 없었다 — 격자와 최소 규격을 쓰면 살 수 있는 최소 구성이 자연히 하한이
    되므로 그 표식이 필요 없다.
    """
    groups = ess_spec_groups(optimum.points)
    picks = ess_spec_targets([point for point, _ in groups], optimum.target_kw)
    rows = [groups[i] for i in picks]

    def mark(point: EssOptimumPoint) -> str:
        # **성립하는 목표가 없으면 표식을 찍지 않는다** (48세션).
        # **「최적」 이라 부르지 않는다** (49세션) — 578년짜리도 최단 회수기간이다.
        #
        # **회수기간이 없는 줄에는 붙이지 않는다** (54세션). 절감액이 0 이하면
        # 회수기간 칸이 「—」 인데 표식은 「최단 회수기간」 을 말해 앞뒤가 안
        # 맞았다. ``optimum.viable`` 만 보고 찍고 있었고, 그 깃발이 잘못 서는
        # 갈래가 있었다 (:func:`~kwise.measures.ess.refine_ess_target`).
        # **깃발이 바로잡힌 뒤에도 이 조건은 남긴다** — 다른 자료에서 또 날 수
        # 있고, 표식은 제 줄의 값과 어긋나지 않아야 한다.
        chosen = (
            optimum.viable
            and point.target_kw == optimum.target_kw
            and point.annual_saving_won > 0
            and point.payback_years is not None
        )
        labels = [SHORTEST_PAYBACK] if chosen else []
        # **표가 사실과 달라지는 자리다** (48세션). 목표를 못 지킨 줄에 목표만
        # 적어 두면 「210 kW · 저감 55 kW」 라 읽히는데 실제 요금적용전력은
        # 264 kW 다. 실제 값을 표식에 적어 그 어긋남을 표 안에서 닫는다.
        if not point.target_met:
            labels.append(f"{TARGET_MISSED} (실제 {point.achieved_demand_kw:,.0f} kW)")
        elif not point.viable:
            labels.append(MARGIN_SHORT)
        return " · ".join(labels)

    def reduction(point: EssOptimumPoint) -> float:
        """실제로 내려간 요금적용전력 (54세션).

        **목표에서 빼면 안 된다.** 목표를 못 지킨 줄에서 「저감량 836 kW」 와
        「목표 미달 (실제 2,801 kW)」 가 한 줄에 나란히 섰다 — 실제 요금적용전력은
        한 kW 도 안 내려갔는데 저감량이 836 kW 라고 적혀 있었다.

        목표를 지킨 줄에서는 달성값이 곧 목표라 **값이 달라지지 않는다.**
        재지 못한 점(출력 0)은 달성값이 없으므로 목표로 적는다.
        """
        achieved = point.achieved_demand_kw
        if achieved <= 0:
            return max(0.0, baseline_demand_kw - point.target_kw)
        return max(0.0, baseline_demand_kw - achieved)

    def span(bounds: tuple[float, float]) -> str:
        low, high = bounds
        return f"{low:,.0f}" if abs(high - low) < 0.5 else f"{low:,.0f}~{high:,.0f}"

    return pd.DataFrame(
        {
            "목표 요금적용전력(kW)": [span(bounds) for _, bounds in rows],
            "저감량(kW)": [reduction(point) for point, _ in rows],
            "출력(kW)": [point.grid_power_kw for point, _ in rows],
            "용량(kWh)": [point.grid_capacity_kwh for point, _ in rows],
            "방전시간(h)": [point.discharge_hours for point, _ in rows],
            "투자비(원)": [point.investment_won for point, _ in rows],
            "연간 절감액(원)": [point.annual_saving_won for point, _ in rows],
            "회수기간(년)": [point.payback_years for point, _ in rows],
            "표식": [mark(point) for point, _ in rows],
        }
    )


ESS_SPEC_HEADER: tuple[str, ...] = (
    "목표",
    "저감량",
    "출력",
    "용량",
    "방전시간",
    "투자비",
    "연간 절감액",
    "회수기간",
    "표식",
)
"""목표별 사양 표의 머리글 (46세션). **화면·PPT·Word 가 같은 열을 쓴다.**"""


def ess_spec_rows(frame: pd.DataFrame) -> tuple[tuple[str, ...], ...]:
    """사양 표를 **사람이 읽는 문자열로** 굳힌다 — 머리글이 첫 줄이다.

    **한 곳에서 만든다** (46세션). 화면·PPT·Word 가 각자 서식을 잡으면 같은 표가
    산출물마다 다르게 읽힌다 — 17세션 3-3 이 태양광 용량 표에서 겪은 일이다.
    """
    rows: list[tuple[str, ...]] = [ESS_SPEC_HEADER]
    for _, row in frame.iterrows():
        years = row["회수기간(년)"]
        rows.append(
            (
                f"{row['목표 요금적용전력(kW)']} kW",
                f"{row['저감량(kW)']:,.0f} kW",
                f"{row['출력(kW)']:,.0f} kW",
                f"{row['용량(kWh)']:,.0f} kWh",
                f"{row['방전시간(h)']:,.2f}h",
                format_won(float(row["투자비(원)"]), reason="—"),
                format_won(float(row["연간 절감액(원)"]), reason="—"),
                # **표시 상한을 넘으면 「>50년」 이다** (50세션 3-7).
                # 500년·3,000년 같은 값은 근거로 읽히지 않는다.
                payback_text(None if years is None or years != years else float(years)),
                str(row["표식"]),
            )
        )
    return tuple(rows)


#: 목표별 사양 표 위 한 줄 (46세션). 곡선 캡션이 하던 일이다 — 왜 U자인지를
#: 적는다. **곡선이 없어졌으므로 「개략」 을 밝힐 필요도 없어졌다.**
ESS_SPEC_CAPTION = "목표를 낮추면 저감량은 늘지만 필요 용량이 더 빠르게 늘어 회수기간이 나빠집니다."

NO_REDUCTION_CAPTION = (
    "저감량이 전 줄 0 kW 입니다 — 목표를 지켜도 요금적용전력이 내려가지 않아 "
    "기본요금이 그대로입니다. 남는 것은 충·방전 단가차뿐입니다."
)
"""저감량이 전 줄 0 인 표의 캡션 (59세션 3절).

**:data:`ESS_SPEC_CAPTION` 이 이 자료에서 거짓이다.** 「목표를 낮추면 저감량은
늘지만」 이라 적어 두었는데 표는 0 kW 가 다섯 줄이다 — 계약전력이 과다해
요금적용전력이 **하한(계약전력의 30%)에 걸려 있으면** 피크를 아무리 깎아도
기준 전력이 안 내려간다. 대형 자료에 계약전력 20,000 kW 를 주면 하한 6,000 kW
가 관측 최대 5,293 kW 보다 커서 그 자리가 된다.

**줄을 뭉치거나 빼지 않는다.** 0 은 계산이 못 낸 값이 아니라 **이 자료의
사실**이고, 다섯 줄이 나란히 0 인 것 자체가 「규모를 키워도 소용없다」 는
근거다. 틀린 것은 그 위에 붙은 한 줄이었다 — 한 줄을 다른 한 줄로 바꾼다.
"""


def ess_spec_caption(frame: pd.DataFrame) -> str:
    """사양 표 위 한 줄. **표가 말하는 것과 어긋나지 않게 고른다** (59세션 3절)."""
    column = frame["저감량(kW)"] if "저감량(kW)" in frame else None
    if column is not None and len(column) and not (column > 0).any():
        return NO_REDUCTION_CAPTION
    return ESS_SPEC_CAPTION


def capacity_band_frame(model: EssCostModel) -> pd.DataFrame:
    """kWh 구간 단가 표 (50세션 3-5 ②). **기준 데이터 화면이 그린다.**

    기본값은 2항식을 구간 중앙에서 환산한 값이다 — ``tools\fit_ess_cost.py`` 가
    계수에서 다시 채운다. **비활성 구간도 목록에 남긴다**: 규격 격자의 최소
    배터리 미만이라 조달되지 않지만, 향후 상업용 소용량 제품이 나오면 살릴
    자리다. 지우면 그런 구간이 있었다는 사실까지 사라진다.
    """
    rows = [
        {
            "구간": band.label,
            "단가": (
                format_won(band.won_per_kwh, reason="—") + "/kWh"
                if band.won_per_kwh is not None
                else "—"
            ),
            "쓰임": "쓴다" if band.active else "비활성 — 최소 규격 미만",
            "2항식 환산 기준": f"{band.midpoint_kwh:,.0f} kWh",
        }
        for band in model.capacity_bands
    ]
    return pd.DataFrame(rows)


# **26세션이 없앴던 표가 46세션에 돌아왔다.** 그때는 곡선 **아래** 대표 지점
# 표까지 두어 읽을 것이 둘이었다 — 지금은 곡선이 없고 표 하나뿐이다.
# 곡선을 남길 수 없었던 까닭은 값이 개략이라 카드와 갈라졌기 때문이다.


def combination_frame(comparison: ComparisonResult) -> pd.DataFrame:
    """조합별 절감액·투자비.

    **투자비를 모르면 ``None`` 이다.** 0 으로 채우면 막대가 바닥에 붙어
    "공짜" 로 읽힌다 (7.5).

    **확실성 열을 뺐다** (53세션 1-4). 28세션에 그림에서 색을 걷어낸 뒤로 아무도
    읽지 않던 열이고, 산출물에서도 등급을 빼기로 했다.
    """
    return pd.DataFrame(
        {
            "조합": [item.name for item in comparison.combinations],
            "절감액(원)": [item.saving_won for item in comparison.combinations],
            "투자비(원)": [item.investment_won for item in comparison.combinations],
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


def _daily_kwh(usage: UsageData) -> pd.Series:
    """일별 사용량 (kWh). **관측이 있는 날만** — 결측일을 0 으로 만들지 않는다.

    날짜 귀속은 :func:`~kwise.io.slot_start` 를 거친다. 라벨(구간 끝)로 묶으면
    자정 구간이 다음 날로 넘어가 하루가 15분씩 밀린다.
    """
    interval = usage.meta.interval_minutes
    load = usage.kw.dropna()
    index = pd.DatetimeIndex(load.index)
    days = slot_start(index, interval).normalize()
    return (
        pd.Series(load.to_numpy(dtype=float) * (interval / 60.0), index=days).groupby(level=0).sum()
    )


def daily_usage_frame(usage: UsageData) -> pd.DataFrame:
    """연간 일별 사용량 (36세션 2절 · PPT 「전력사용현황」).

    기온을 곁들인 :func:`daily_temperature_frame` 과 **같은 일별 합계**를 쓴다.
    지역을 고르지 않았거나 기상 자료가 없어도 이 그림은 나와야 한다 — 슬라이드
    한 장이 통째로 비면 「사용량이 없다」 로 읽힌다.
    """
    daily = _daily_kwh(usage)
    return pd.DataFrame(
        {
            "날짜": [pd.Timestamp(value).date() for value in daily.index],
            "사용량(kWh)": daily.astype(float).to_numpy(),
        }
    )


def daily_temperature_frame(usage: UsageData, temperature: pd.Series) -> pd.DataFrame:
    """연간 일별 사용량과 일평균 기온 (30세션 4절).

    **관측이 있는 날만 낸다.** 결측일을 0 kWh 로 그리면 그날 기온이 무엇이든
    사용량이 바닥으로 떨어져, 기온과 사용량의 관계가 있지도 않은 곳에서 보인다.

    Args:
        temperature: 시간별 기온 (℃). 인덱스는 tz 유무를 가리지 않는다 —
            **여기서 벗겨 날짜로만 묶는다.** 부하 인덱스는 tz-naive 지방시다.
    """
    daily_kwh = _daily_kwh(usage)

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


#: 기준선 이름이 갈리는 지점 — **관측이 1년에 못 미치면 「연평균」이 아니다** (32세션 1절).
#: 반년치 자료의 평균을 「연평균」 이라 적으면 그 해의 평년값처럼 읽힌다.
_FULL_YEAR_DAYS = 365


def temperature_mean_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """기온 곡선에 그을 기준선 한 줄 — 값과 이름 (32세션 1절).

    **여름 고온·겨울 저온이 부하를 얼마나 밀어 올리는지는 평균과의 거리로 읽힌다.**
    선이 없으면 곡선의 오르내림이 어느 쪽으로 얼마나 벗어난 것인지 눈금을 세어야 한다.

    이름은 관측 기간이 정한다 — 1년(365일) 이상이면 「연평균」, 못 미치면
    **「기간 평균」** 이다.

    Args:
        frame: :func:`daily_temperature_frame` 이 낸 표.

    Returns:
        한 줄짜리 표. ``날짜`` 는 라벨을 놓을 자리(관측 첫날)이고,
        ``평균 기온(℃)`` 이 값, ``기준선`` 이 화면에 적히는 문구다.
    """
    temps = frame["일평균 기온(℃)"].astype(float)
    mean = float(temps.mean())
    days = pd.DatetimeIndex(pd.to_datetime(frame["날짜"]))
    span = int((days.max() - days.min()).days) + 1 if len(days) else 0
    name = "연평균" if span >= _FULL_YEAR_DAYS else "기간 평균"
    return pd.DataFrame(
        {
            "날짜": [days.min().date() if len(days) else None],
            "평균 기온(℃)": [mean],
            "기준선": [f"{name} {mean:,.1f}℃"],
        }
    )


def dispatch_schedule(frame: pd.DataFrame) -> tuple[str, str]:
    """대표일의 (충전 문구, 방전 문구) — **그림 대신 글로 낸다** (23세션 6절).

    17세션의 아래 칸(충전＋·방전−)은 위 칸과 종속이라 그림이 둘일 필요가 없었다.
    시각 범위와 양은 글이 더 정확하다 — 막대에서 시각을 눈으로 읽어 내야 했다.

    끊긴 구간이 여럿이면 **처음과 끝**으로 묶어 적는다. 15분 단위 구간을 다
    나열하면 그림보다 못한 목록이 된다.
    """

    # **``시각`` 은 구간 **시작**이다** (:func:`day_profile`). 그대로 min–max 를
    # 적으면 오른쪽 끝이 한 구간 짧고, **한 구간만 돌면 「22:00–22:00」** 처럼
    # 길이가 0 인 구간이 된다 — 35 kWh 를 충전했다면서 시작과 끝이 같았다
    # (54세션). 오른쪽 끝에 한 구간을 더해 실제로 덮은 구간을 적는다.
    times = pd.DatetimeIndex(frame["시각"])
    step = (times[1] - times[0]) if len(times) > 1 else pd.Timedelta(minutes=15)

    def span(column: str) -> str:
        active = frame[frame[column] > 0]
        if active.empty:
            return ""
        marks = pd.DatetimeIndex(active["시각"])
        return f"{marks.min():%H:%M}–{marks.max() + step:%H:%M}"

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
