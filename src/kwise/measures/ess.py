"""ESS (요구사항서 7.6).

**출력(kW)과 에너지(kWh)를 분리해 보여주는 것이 핵심이다.** 목표를 조금만 낮추면
필요 에너지가 급감하는 경우가 많고, 그 사실이 배터리 사양을 완전히 바꾼다.

디스패치는 규칙기반 피크컷 단일 전략이다. 목표 초과분을 방전하고 경부하 시간대에
충전한다. MILP 최적화는 범위 밖이다.

용량 산정 주의 — 부록 B 의 '총 초과 에너지' 는 **기간 전체 합계**다. 배터리 용량은
충전 기회가 야간뿐이라는 전제에서 **하루 최대 초과 에너지**로 잡는다. 샘플의
5,200 kW 목표에서 기간 합계는 91 kWh, 연속 구간 최대는 23 kWh, 하루 최대는 35 kWh 다.
연속 구간 최대로 잡으면 같은 날 초과가 두 번 이상일 때 목표를 놓친다. 세 값을 모두 낸다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from kwise import money
from kwise.io import UsageData, slot_start
from kwise.measures.arbitrage import (
    ArbitrageValue,
    arbitrage_value,
    c_rate,
    default_cycles_per_day,
)
from kwise.measures.base import Certainty, annualize, payback_years
from kwise.measures.ess_cost import (
    EssCostInput,
    EssCostModel,
    EssCostReference,
    EssQuote,
    Feasibility,
    load_ess_cost_model,
    load_ess_cost_reference,
)
from kwise.measures.netload import with_load
from kwise.notices import Notice, basis, info, warn
from kwise.quality import QualityReport
from kwise.rules import assumption
from kwise.tariff import (
    BillingOptions,
    BillingResult,
    TariffSelection,
    TariffTable,
    build_calendar,
    calculate_bill,
    classify_slots,
)

__all__ = [
    "U_SHAPE_REASON",
    "DispatchResult",
    "EssResult",
    "EssTargetCurve",
    "EssTargetPoint",
    "PeakExcess",
    "analyze_peak_excess",
    "band_labels",
    "default_dod",
    "default_payback_target_years",
    "default_round_trip",
    "default_target_search_ratio",
    "default_target_step_kw",
    "dispatch_peak_shaving",
    "ess_payback_curve",
    "ess_target_curve",
    "evaluate_ess",
    "excess_slots_by_day",
    "high_rate_discharge_hours",
    "light_band_mask",
    "nameplate_capacity_kwh",
    "required_discharge_hours",
    "size_for_target",
]

# 값은 ``datassumptions.json`` 에 있다 (요구사항서 12장). 판단값이다.


def default_round_trip() -> float:
    return float(assumption("ess.round_trip"))


def default_dod() -> float:
    return float(assumption("ess.dod"))


def default_payback_target_years() -> float:
    return float(assumption("ess.payback_target_years"))


def high_rate_discharge_hours() -> float:
    """이 아래는 정치형 셀의 통상 연속 방전(0.5~1C)을 넘어선다. 고출력 셀 사양이다."""
    return float(assumption("ess.high_rate_discharge_hours"))


def default_target_step_kw() -> float:
    """최적 목표를 훑는 격자 (14세션 3-1). **곡선이 격자에 민감하다.**"""
    return float(assumption("ess.target_step_kw"))


def default_target_search_ratio() -> float:
    """탐색 하한 비율. 현행 요금적용전력의 이 배까지 내려가며 훑는다."""
    return float(assumption("ess.target_search_ratio"))


# 회수기간 곡선에서 보여 줄 방전시간. 짧을수록 경제성이 좋다는 것을 보이는 표다.
DEFAULT_CURVE_DISCHARGE_HOURS: tuple[float, ...] = (0.5, 1.0, 2.0, 4.0)


@dataclass(frozen=True)
class PeakExcess:
    """목표 피크 초과 분석.

    Attributes:
        max_excess_kw: 필요한 배터리 **출력**.
        total_excess_kwh: 기간 전체 초과 에너지 (부록 B 규약).
        max_event_excess_kwh: 연속 초과 구간 하나의 최대 에너지. **용량 산정 기준**.
        max_daily_excess_kwh: 하루 최대 초과 에너지.
    """

    target_kw: float
    slots: int
    hours: float
    max_excess_kw: float
    total_excess_kwh: float
    max_event_excess_kwh: float
    max_daily_excess_kwh: float
    events: int


def analyze_peak_excess(kw: pd.Series, target_kw: float, interval_minutes: int) -> PeakExcess:
    """목표 요금적용전력을 넘는 구간을 센다. 결측 슬롯은 제외한다."""
    if target_kw < 0:
        raise ValueError(f"목표 피크는 음수일 수 없습니다: {target_kw}")
    observed = kw.dropna()
    slot_hours = interval_minutes / 60.0
    excess = (observed - target_kw).clip(lower=0.0)
    over = excess[excess > 0]
    if over.empty:
        return PeakExcess(target_kw, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0)

    energy = over * slot_hours
    flags = excess > 0
    blocks = (flags != flags.shift()).cumsum()[flags]
    event_energy = energy.groupby(blocks).sum()
    daily_energy = energy.groupby(pd.DatetimeIndex(over.index).date).sum()

    return PeakExcess(
        target_kw=target_kw,
        slots=len(over),
        hours=float(len(over) * slot_hours),
        max_excess_kw=float(over.max()),
        total_excess_kwh=float(energy.sum()),
        max_event_excess_kwh=float(event_energy.max()),
        max_daily_excess_kwh=float(daily_energy.max()),
        events=int(event_energy.size),
    )


def nameplate_capacity_kwh(delivered_kwh: float, *, round_trip: float, dod: float) -> float:
    """계통에 내보낼 에너지를 배터리 **정격 용량**으로 환산한다 (18세션 1절).

        정격 용량 = 내보낼 에너지 ÷ √왕복효율 ÷ DoD

    **화면에 내는 용량은 언제나 이 값이다.** 환산 전 값(하루 최대 초과 에너지)은
    목표를 고르는 곡선 안에서만 쓴다 — 두 값을 같은 화면에 두면 사용자에게는
    그냥 불일치로 보인다. 한 곳에 두어 :func:`size_for_target` 과
    :func:`ess_target_curve` 가 같은 식을 쓰게 한다.
    """
    if not 0 < dod <= 1:
        raise ValueError(f"DoD 는 0 초과 1 이하여야 합니다: {dod}")
    if round_trip <= 0:
        raise ValueError(f"왕복효율은 양수여야 합니다: {round_trip}")
    return delivered_kwh / math.sqrt(round_trip) / dod


def size_for_target(
    excess: PeakExcess,
    *,
    dod: float | None = None,
    round_trip: float | None = None,
    basis: str = "daily",
) -> tuple[float, float]:
    """목표에서 필요한 (출력 kW, 정격 용량 kWh) 를 낸다.

    기본은 **하루 최대**다. 충전 기회가 경부하 시간대(야간)뿐이라 하루치를 한 번에
    담아야 한다. 연속 구간 최대로 잡으면 같은 날 초과가 두 번 이상일 때 배터리가
    비어 목표를 놓친다 — 샘플 5,200 kW 목표에서 연속 최대는 23 kWh 지만
    하루 최대는 35 kWh 다.

    Args:
        basis: ``"daily"`` 하루 최대 (기본) / ``"event"`` 연속 초과 구간 최대 /
            ``"total"`` 기간 합계 (부록 B 규약. 배터리 사양으로는 과대).
    """
    round_trip = default_round_trip() if round_trip is None else round_trip
    dod = default_dod() if dod is None else dod
    energy = {
        "event": excess.max_event_excess_kwh,
        "daily": excess.max_daily_excess_kwh,
        "total": excess.total_excess_kwh,
    }
    if basis not in energy:
        raise ValueError(f"알 수 없는 용량 산정 기준입니다: {basis!r}")
    # 방전 손실을 감안해 계통에 내보낼 에너지를 배터리 저장량으로 환산한다.
    nameplate = nameplate_capacity_kwh(energy[basis], round_trip=round_trip, dod=dod)
    return excess.max_excess_kw, nameplate


def required_discharge_hours(excess: PeakExcess) -> float:
    """**방전시간은 산출값이다. 강제하지 않는다** (요구사항서 7.6).

        방전시간 = 하루 최대 초과 에너지 ÷ 최대 초과 출력

    목표 피크를 정하면 데이터에서 결정된다. 짧을수록 kW당 단가가 싸므로
    경제성이 좋다 — 조달이 가능하다면 짧은 쪽이 맞다. 다만 0.5h 미만은
    2C 이상이라 정치형 셀의 통상 사양(0.5~1C 연속)을 넘는다.
    """
    if excess.max_excess_kw <= 0:
        return 0.0
    return excess.max_daily_excess_kwh / excess.max_excess_kw


U_SHAPE_REASON = (
    "왼쪽은 최소 규모 {minimum:,.0f} kWh 를 다 못 쓰는 구간이고, 오른쪽은 용량이 "
    "급증해 투자비가 늘어나는 구간입니다."
)
"""회수기간 곡선이 U자인 이유 (14세션 3-2).

**물리적 최적이 아니라 조달 규격의 산물이다.** 시장 최소 규모가 없으면 목표를
올릴수록 회수기간이 계속 좋아지기만 한다.
"""


@dataclass(frozen=True)
class EssTargetPoint:
    """목표 하나에서 나오는 사양·투자비·회수기간 (14세션 3-1).

    Attributes:
        reduction_kw: 저감량 = 현행 요금적용전력 − 목표. **절감액의 근거다.**
        power_kw: 필요 출력 = 목표 초과분의 최대값.
        required_capacity_kwh: 필요 용량 = 하루치 초과 에너지 합의 연중 최대값.
            **내보낼 에너지다.** 곡선 내부(투자비·회수기간) 에서만 쓰고 화면에
            내지 않는다 — 화면 용량은 ``nameplate_capacity_kwh`` 다 (18세션 1절).
        nameplate_capacity_kwh: 정격 용량 = 필요 용량 ÷ √왕복효율 ÷ DoD.
            :func:`evaluate_ess` 가 내는 카드 용량과 **같은 값이다.**
        billed_capacity_kwh: 과금 용량 = ``max(필요 용량, 시장 최소 규모)``.
        at_market_minimum: 최소 규모에 걸렸는가. 걸린 구간이 U자의 왼쪽 팔이다.
    """

    target_kw: float
    reduction_kw: float
    power_kw: float
    required_capacity_kwh: float
    nameplate_capacity_kwh: float
    billed_capacity_kwh: float
    discharge_hours: float
    equipment_won: float
    electrical_won: float
    investment_won: float
    annual_saving_won: float
    payback_years: float | None
    at_market_minimum: bool

    @property
    def spec_label(self) -> str:
        """``5,170 kW · 저감 123 kW · 123 kW / 120 kWh`` — 표식에 붙인다.

        **회수기간을 적지 않는다** (18세션 1절). 곡선의 회수기간은 기본요금만 본
        개략치라 카드의 결론과 다르다. 표식은 이미 그 축 위에 찍혀 있으므로
        숫자를 한 번 더 적으면 카드와 어긋난 값이 화면에 남는다.
        """
        return (
            f"{self.target_kw:,.0f} kW · 저감 {self.reduction_kw:,.0f} kW · "
            f"{self.power_kw:,.0f} kW / {self.nameplate_capacity_kwh:,.0f} kWh"
        )


@dataclass(frozen=True)
class EssTargetCurve:
    """목표별 회수기간 곡선. **U자다** (14세션 3-2).

    목표를 낮출수록 저감량이 커져 절감액이 늘지만 필요 용량이 급증해 투자비가 더
    빨리 오른다. 반대로 목표를 높이면 필요 용량이 시장 최소 규모 아래로 내려가
    투자비가 더 줄지 않는데 절감액만 준다. 그 사이에 최소 지점이 생긴다.

    **절감액은 기본요금만 본 개략치다.** 목표를 고르는 데 쓰고, 고른 뒤의 금액은
    :func:`evaluate_ess` 가 요금을 다시 계산해 낸다.
    """

    points: tuple[EssTargetPoint, ...]
    best: EssTargetPoint | None
    baseline_demand_kw: float
    observed_peak_kw: float
    base_fee_won_per_kw: float
    market_minimum_kwh: float
    step_kw: float
    round_trip: float
    dod: float

    @property
    def u_shape_reason(self) -> str:
        return U_SHAPE_REASON.format(minimum=self.market_minimum_kwh)

    def frame(self) -> pd.DataFrame:
        """곡선 전체. 차트가 그대로 그린다."""
        return pd.DataFrame(
            {
                "목표 요금적용전력(kW)": [item.target_kw for item in self.points],
                "저감량(kW)": [item.reduction_kw for item in self.points],
                "필요 출력(kW)": [item.power_kw for item in self.points],
                "필요 용량(kWh)": [item.required_capacity_kwh for item in self.points],
                # 화면에 내는 용량은 **정격**이다. 위 「필요 용량」 은 내보낼
                # 에너지라 카드와 다르다 — 곡선 내부 계산에만 쓴다 (18세션 1절).
                "정격 용량(kWh)": [item.nameplate_capacity_kwh for item in self.points],
                "과금 용량(kWh)": [item.billed_capacity_kwh for item in self.points],
                "방전시간(h)": [item.discharge_hours for item in self.points],
                "투자비(원)": [item.investment_won for item in self.points],
                "연간 절감액(원)": [item.annual_saving_won for item in self.points],
                "회수기간(년)": [item.payback_years for item in self.points],
                "최소 규모 적용": [item.at_market_minimum for item in self.points],
            }
        )

    # 최소 지점에서 몇 격자 떨어진 곳을 보일지. **바깥으로 갈수록 성기게** 잡는다 —
    # 최소 지점 바로 옆의 변화가 가장 크고, 멀어지면 단조롭게 나빠질 뿐이다.
    _HIGHLIGHT_OFFSETS: tuple[int, ...] = (3, 1, 0, -2, -7)

    def highlights(self) -> tuple[EssTargetPoint, ...]:
        """최소 지점 둘레의 대표 지점 대여섯 개 (14세션 3-2).

        곡선만으로는 "목표를 조금만 낮춰도 용량이 급증한다" 가 숫자로 읽히지
        않는다. **최소 지점을 가운데 두고** 양쪽을 보인다.
        """
        priced = [item for item in self.points if item.payback_years is not None]
        if not priced or self.best is None:
            return ()
        center = priced.index(self.best)
        picked = sorted(
            {min(max(center - offset, 0), len(priced) - 1) for offset in self._HIGHLIGHT_OFFSETS}
        )
        return tuple(priced[index] for index in picked)


def _daily_excess(
    values: pd.Series, day_codes: pd.Series, target_kw: float, slot_hours: float
) -> tuple[float, float]:
    """목표 하나의 (필요 출력 kW, 필요 용량 kWh).

    필요 용량은 **하루치 초과 에너지 합의 연중 최대값**이다. 충전 기회가 야간뿐이라
    하루치를 한 번에 담아야 한다.
    """
    excess = (values - target_kw).clip(lower=0.0)
    power = float(excess.max())
    if power <= 0:
        return 0.0, 0.0
    daily = (excess * slot_hours).groupby(day_codes).sum()
    return power, float(daily.max())


def ess_target_curve(
    kw: pd.Series,
    interval_minutes: int,
    *,
    baseline_demand_kw: float,
    base_fee_won_per_kw: float,
    model: EssCostModel | None = None,
    step_kw: float | None = None,
    search_ratio: float | None = None,
    indoor: bool = False,
    round_trip: float | None = None,
    dod: float | None = None,
) -> EssTargetCurve:
    """목표를 훑어 회수기간 최소 지점을 찾는다 (14세션 3-1).

    ::

        필요 출력 = 초과분 최대값 (kW)
        필요 용량 = 하루치 초과 에너지 합의 연중 최대값 (kWh)
        정격 용량 = 필요 용량 ÷ √왕복효율 ÷ DoD          ← 화면에 내는 용량
        과금 용량 = max(필요 용량, 시장 최소 규모)
        투자비   = 고정비 + 용량단가 × 과금 용량 + 전기공사
        절감액   = 저감량(kW) × 기본요금단가 × 12
        회수기간 = 투자비 ÷ 절감액

    **기본요금단가는 현행 요금제 기준이다** (14세션 2절). 최적 요금제로 바꾼 뒤의
    단가를 쓰면 ESS 절감액이 선택요금 전환에 딸려 움직여 독립 평가가 깨진다.

    **투자비·절감액·회수기간은 목표를 고르는 데만 쓴다** (18세션 1절). 절감액이
    기본요금만 본 개략치라 :func:`evaluate_ess` 의 결론과 다르다. 화면에 내는
    사양은 출력·정격 용량·방전시간 셋이며, 이 셋은 카드와 값이 같다.

    Args:
        baseline_demand_kw: 현행 요금적용전력. 저감량의 기준이다.
        step_kw: 탐색 격자. **10 kW 이하로 둔다** — 50 kW 간격이면 최소 지점을
            50 kW 옆에서 놓친다 (샘플에서 26.7년 vs 24.6년).
        search_ratio: 탐색 하한 비율. 기본은 현행 요금적용전력의 0.7배까지.
        round_trip, dod: 정격 용량 환산 계수. **투자비 산정에는 쓰지 않는다** —
            쓰면 최소 지점이 옮겨 가 카드 값이 통째로 달라진다.
    """
    if baseline_demand_kw <= 0:
        raise ValueError(f"현행 요금적용전력은 양수여야 합니다: {baseline_demand_kw}")
    cost_model = model if model is not None else load_ess_cost_model()
    step = default_target_step_kw() if step_kw is None else step_kw
    ratio = default_target_search_ratio() if search_ratio is None else search_ratio
    round_trip = default_round_trip() if round_trip is None else round_trip
    dod = default_dod() if dod is None else dod
    if step <= 0:
        raise ValueError(f"탐색 격자는 양수여야 합니다: {step}")
    if not 0 < ratio < 1:
        raise ValueError(f"탐색 하한 비율은 0 초과 1 미만이어야 합니다: {ratio}")

    observed = kw.dropna()
    if observed.empty:
        raise ValueError("관측된 수요가 없어 ESS 목표를 훑을 수 없습니다.")
    slot_hours = interval_minutes / 60.0
    day_codes = pd.Series(
        slot_start(pd.DatetimeIndex(observed.index), interval_minutes).normalize(),
        index=observed.index,
    )

    # **격자를 눈금에 맞춘다.** 현행값에서 그냥 빼 내려가면 5,283.44 kW 같은 목표가
    # 나와 읽히지 않고, 다른 건물과 견줄 수도 없다.
    lowest = baseline_demand_kw * ratio
    first = math.floor(baseline_demand_kw / step) * step
    if first >= baseline_demand_kw:
        first -= step
    count = math.floor((first - lowest) / step) + 1
    targets = [first - step * index for index in range(max(count, 0))]

    points: list[EssTargetPoint] = []
    for target in targets:
        power, capacity = _daily_excess(observed, day_codes, target, slot_hours)
        if power <= 0:
            continue
        billed = cost_model.billed_capacity_kwh(capacity)
        equipment = cost_model.equipment_won(billed)
        electrical = cost_model.electrical_won(billed, indoor=indoor)
        investment = equipment + electrical
        reduction = max(0.0, baseline_demand_kw - target)
        annual = reduction * base_fee_won_per_kw * 12.0
        points.append(
            EssTargetPoint(
                target_kw=target,
                reduction_kw=reduction,
                power_kw=power,
                required_capacity_kwh=capacity,
                nameplate_capacity_kwh=nameplate_capacity_kwh(
                    capacity, round_trip=round_trip, dod=dod
                ),
                billed_capacity_kwh=billed,
                discharge_hours=capacity / power if power > 0 else 0.0,
                equipment_won=equipment,
                electrical_won=electrical,
                investment_won=investment,
                annual_saving_won=annual,
                payback_years=payback_years(investment, annual),
                at_market_minimum=capacity < cost_model.market_minimum_kwh,
            )
        )

    priced = [item for item in points if item.payback_years is not None]
    best = min(priced, key=lambda item: item.payback_years or math.inf) if priced else None
    return EssTargetCurve(
        points=tuple(points),
        best=best,
        baseline_demand_kw=baseline_demand_kw,
        observed_peak_kw=float(observed.max()),
        base_fee_won_per_kw=base_fee_won_per_kw,
        market_minimum_kwh=cost_model.market_minimum_kwh,
        step_kw=step,
        round_trip=round_trip,
        dod=dod,
    )


@dataclass(frozen=True, eq=False)
class DispatchResult:
    """규칙기반 디스패치 결과.

    에너지 보존 항등식이 성립한다.
    ``soc_end − soc_start = charged_kwh × η충 − discharged_kwh ÷ η방``
    """

    net_kw: pd.Series
    soc_kwh: pd.Series
    charged_kwh: float
    discharged_kwh: float
    unmet_kwh: float
    soc_start_kwh: float
    soc_end_kwh: float
    achieved_peak_kw: float
    charge_window_peak_kw: float
    charge_window_peak_before_kw: float
    target_kw: float
    power_kw: float
    capacity_kwh: float
    round_trip: float
    dod: float

    @property
    def usable_kwh(self) -> float:
        return self.capacity_kwh * self.dod

    @property
    def cycles(self) -> float:
        if self.usable_kwh <= 0:
            return 0.0
        return self.discharged_kwh / self.usable_kwh

    @property
    def target_met(self) -> bool:
        return self.unmet_kwh <= 1e-9

    @property
    def charge_created_new_peak(self) -> bool:
        """충전이 목표를 넘는 새 피크를 만들었는가.

        경부하 충전이 기저부하에 얹히면 야간에 새 피크가 생길 수 있다.
        ``respect_target_when_charging`` 을 켜 두면 일어나지 않지만, 결과에서
        반드시 확인한다 — 조합 비교가 이 값을 본다.
        """
        return self.charge_window_peak_kw > self.target_kw + 1e-6

    @property
    def charge_window_rise_kw(self) -> float:
        """충전으로 올라간 충전 시간대 최대 부하."""
        return self.charge_window_peak_kw - self.charge_window_peak_before_kw


def light_band_mask(
    usage: UsageData,
    table: TariffTable,
    *,
    selection: TariffSelection | None = None,
    options: BillingOptions | None = None,
) -> pd.Series:
    """경부하 시간대 마스크. 충전 시간대를 요금표에서 가져온다."""
    opts = options if options is not None else BillingOptions()
    index = pd.DatetimeIndex(usage.kw.index)
    calendar = build_calendar(
        range(index[0].year - 1, index[-1].year + 2),
        sunday_is_holiday=opts.sunday_is_holiday,
        exclude_temporary=(
            table.day_rules.exclude_temporary_holiday
            if opts.exclude_temporary_holiday is None
            else opts.exclude_temporary_holiday
        ),
        extra_holidays=opts.extra_holidays,
        excluded_holidays=opts.excluded_holidays,
    )
    slots = classify_slots(
        index,
        usage.meta.interval_minutes,
        table,
        calendar,
        contract_type=selection.contract_type if selection else None,
        region_group=opts.region_group,
    )
    return pd.Series(slots["band"].to_numpy() == "light", index=index, name="charge_window")


def band_labels(
    usage: UsageData,
    table: TariffTable,
    *,
    selection: TariffSelection | None = None,
    options: BillingOptions | None = None,
) -> pd.Series:
    """계시별 시간대 이름 (``light``/``mid``/``peak``) — **표시용** (15세션 2-5).

    ESS 하루 곡선의 배경 띠가 쓴다. 왜 그 시각에 담고 쓰는지는 시간대를 함께
    보여야 읽힌다. :func:`light_band_mask` 와 **같은 분류를 쓴다** — 따로 계산하면
    그림의 띠와 실제 충전 창이 어긋날 수 있다.
    """
    opts = options if options is not None else BillingOptions()
    index = pd.DatetimeIndex(usage.kw.index)
    calendar = build_calendar(
        range(index[0].year - 1, index[-1].year + 2),
        sunday_is_holiday=opts.sunday_is_holiday,
        exclude_temporary=(
            table.day_rules.exclude_temporary_holiday
            if opts.exclude_temporary_holiday is None
            else opts.exclude_temporary_holiday
        ),
        extra_holidays=opts.extra_holidays,
        excluded_holidays=opts.excluded_holidays,
    )
    slots = classify_slots(
        index,
        usage.meta.interval_minutes,
        table,
        calendar,
        contract_type=selection.contract_type if selection else None,
        region_group=opts.region_group,
    )
    return pd.Series(slots["band"].to_numpy(), index=index, name="band")


def dispatch_peak_shaving(
    kw: pd.Series,
    *,
    target_kw: float,
    power_kw: float,
    capacity_kwh: float,
    charge_mask: pd.Series,
    interval_minutes: int,
    round_trip: float | None = None,
    dod: float | None = None,
    initial_soc_ratio: float = 1.0,
    charge_limit_kw: float | None = None,
    respect_target_when_charging: bool = True,
) -> DispatchResult:
    """목표 초과분 방전, 경부하 충전. 규칙기반 단일 전략이다.

    Args:
        charge_limit_kw: 충전 전력 상한. 야간 피크를 억제하고 싶을 때 쓴다.
        respect_target_when_charging: 충전을 목표 이하로 묶을지. **기본은 켬.**
            끄면 경부하 시간대에 출력껏 충전하므로 기저부하 위에 새 피크가 생긴다.
            그 실패 양상을 재현하려는 경우에만 끈다.
    """
    round_trip = default_round_trip() if round_trip is None else round_trip
    dod = default_dod() if dod is None else dod
    if power_kw < 0 or capacity_kwh < 0:
        raise ValueError("출력과 용량은 음수일 수 없습니다.")
    if not 0 < round_trip <= 1:
        raise ValueError(f"왕복효율은 0 초과 1 이하여야 합니다: {round_trip}")

    slot_hours = interval_minutes / 60.0
    usable = capacity_kwh * dod
    efficiency = math.sqrt(round_trip)  # 충·방전에 반씩 나눠 적용한다
    soc = usable * min(max(initial_soc_ratio, 0.0), 1.0)
    soc_start = soc

    index = pd.DatetimeIndex(kw.index)
    charge_window = charge_mask.reindex(index, fill_value=False).to_numpy(dtype=bool)
    loads = kw.to_numpy(dtype=float)

    net = loads.copy()
    soc_track = [0.0] * len(loads)
    charged = 0.0
    discharged = 0.0
    unmet = 0.0

    for position, load in enumerate(loads):
        if not math.isnan(load):
            excess = load - target_kw
            if excess > 0:
                deliverable = min(excess, power_kw, soc * efficiency / slot_hours)
                deliverable = max(deliverable, 0.0)
                energy = deliverable * slot_hours
                soc -= energy / efficiency
                discharged += energy
                unmet += (excess - deliverable) * slot_hours
                net[position] = load - deliverable
            elif charge_window[position] and soc < usable:
                intake = min(power_kw, (usable - soc) / efficiency / slot_hours)
                if respect_target_when_charging:
                    intake = min(intake, max(0.0, target_kw - load))
                if charge_limit_kw is not None:
                    intake = min(intake, charge_limit_kw)
                intake = max(intake, 0.0)
                energy = intake * slot_hours
                soc += energy * efficiency
                charged += energy
                net[position] = load + intake
        soc_track[position] = soc

    net_series = pd.Series(net, index=index, name="kw")
    observed = net_series.dropna()
    in_window = pd.Series(charge_window, index=index)
    windowed_after = net_series[in_window].dropna()
    windowed_before = kw[in_window].dropna()
    return DispatchResult(
        net_kw=net_series,
        soc_kwh=pd.Series(soc_track, index=index, name="soc_kwh"),
        charged_kwh=charged,
        discharged_kwh=discharged,
        unmet_kwh=unmet,
        soc_start_kwh=soc_start,
        soc_end_kwh=soc,
        achieved_peak_kw=float(observed.max()) if len(observed) else 0.0,
        charge_window_peak_kw=float(windowed_after.max()) if len(windowed_after) else 0.0,
        charge_window_peak_before_kw=(
            float(windowed_before.max()) if len(windowed_before) else 0.0
        ),
        target_kw=target_kw,
        power_kw=power_kw,
        capacity_kwh=capacity_kwh,
        round_trip=round_trip,
        dod=dod,
    )


@dataclass(frozen=True, eq=False)
class EssResult:
    """ESS 평가. 출력과 에너지를 분리해 담는다.

    Attributes:
        discharge_hours: **산출된** 방전시간 (하루 최대 초과 에너지 ÷ 최대 초과 출력).
            강제 입력이 아니다.
        breakeven_unit_cost_won_per_kw: 목표 회수기간을 맞추는 kW당 단가.
            입력 단가와 같은 단위라 그대로 견줄 수 있다.
        arbitrage: 차익거래 **잠재** 수익. 절감액에 더하지 않았다 (이중 계산 방지).
        payback_years: **피크저감 절감액만**으로 낸 회수기간. 기본값이자 보수적인 값이다.
        payback_with_arbitrage_years: 차익거래 잠재 수익까지 더한 회수기간. **상한**이다.
        outlook_payback_years: 2030년 전망 단가 기준 회수기간.
            "지금은 안 됨" 을 "언제쯤 되는가" 로 바꾼다.
        quote: 조달 사례 모델 견적 — **설비비와 전기공사비를 나눠** 담는다 (13세션).
        feasibility: **성립 조건.** 회수기간만으로는 "왜 안 되는가" 를 알 수 없다.
    """

    excess: PeakExcess
    dispatch: DispatchResult
    power_kw: float
    capacity_kwh: float
    discharge_hours: float
    cost: EssCostInput
    investment_won: float
    base_saving_won: float
    energy_saving_won: float
    total_saving_won: float
    annual_saving_won: float
    payback_years: float | None
    breakeven_unit_cost_won_per_kw: float | None
    payback_target_years: float
    billing_demand_kw: float
    arbitrage: ArbitrageValue | None = None
    payback_with_arbitrage_years: float | None = None
    outlook_payback_years: float | None = None
    outlook_payback_with_arbitrage_years: float | None = None
    outlook_label: str = ""
    quote: EssQuote | None = None
    feasibility: Feasibility | None = None
    certainty: Certainty = Certainty.MEDIUM_LOW
    notices: tuple[Notice, ...] = field(default=())

    @property
    def c_rate(self) -> float:
        """방전 C-rate. 0.5h 는 2C 다."""
        return c_rate(self.discharge_hours)

    @property
    def unit_cost_won_per_kw(self) -> float | None:
        return self.cost.unit_cost_won_per_kw


def _base_fee_won_per_kw(table: TariffTable, selection: TariffSelection) -> float:
    """기본요금 단가. **요금표에서 가져온다** — 성립 조건에 하드코딩하지 않는다."""
    return float(table.rates(selection).base_won_per_kw)


def evaluate_ess(
    usage: UsageData,
    table: TariffTable,
    selection: TariffSelection,
    *,
    target_kw: float,
    cost: EssCostInput,
    charge_mask: pd.Series | None = None,
    power_kw: float | None = None,
    capacity_kwh: float | None = None,
    sizing_basis: str = "daily",
    charge_limit_kw: float | None = None,
    respect_target_when_charging: bool = True,
    round_trip: float | None = None,
    dod: float | None = None,
    payback_target_years: float | None = None,
    cycles_per_day: float | None = None,
    reference: EssCostReference | None = None,
    model: EssCostModel | None = None,
    indoor: bool = False,
    baseline: BillingResult | None = None,
    quality: QualityReport | None = None,
    options: BillingOptions | None = None,
) -> EssResult:
    """목표 요금적용전력을 받아 사양·절감액·회수기간을 낸다.

    Args:
        cost: 단가 입력. **kW당 단가 하나** 또는 **총액**이다 (:class:`EssCostInput`).
            방전시간은 kW당 단가에 이미 들어 있으므로 이중으로 곱하지 않는다.
        power_kw, capacity_kwh: 주지 않으면 목표에서 역산한다.
        payback_target_years: 손익분기 단가를 역산할 회수기간 (기본 10년).
        cycles_per_day: 차익거래 평일 사이클 수 (기본 1).
        model: 조달 사례 투자비 모델. 단가·총액을 넣지 않으면 **이 모델로 산정**한다.
        indoor: 실내 설치. 전기공사비가 줄어든다 (근거 1건이라 참고값).
    """
    round_trip = default_round_trip() if round_trip is None else round_trip
    dod = default_dod() if dod is None else dod
    payback_target_years = (
        default_payback_target_years() if payback_target_years is None else payback_target_years
    )
    cycles_per_day = default_cycles_per_day() if cycles_per_day is None else cycles_per_day
    opts = options if options is not None else BillingOptions()
    interval = usage.meta.interval_minutes
    excess = analyze_peak_excess(usage.kw, target_kw, interval)

    sized_power, sized_capacity = size_for_target(
        excess, dod=dod, round_trip=round_trip, basis=sizing_basis
    )
    power = sized_power if power_kw is None else power_kw
    capacity = sized_capacity if capacity_kwh is None else capacity_kwh
    # 방전시간은 데이터가 정한다. 사양으로 강제하지 않는다 (7.6).
    discharge_hours = required_discharge_hours(excess)

    mask = (
        charge_mask
        if charge_mask is not None
        else light_band_mask(usage, table, selection=selection, options=opts)
    )
    dispatch = dispatch_peak_shaving(
        usage.kw,
        target_kw=target_kw,
        power_kw=power,
        capacity_kwh=capacity,
        charge_mask=mask,
        interval_minutes=interval,
        round_trip=round_trip,
        dod=dod,
        charge_limit_kw=charge_limit_kw,
        respect_target_when_charging=respect_target_when_charging,
    )

    base_bill = (
        baseline
        if baseline is not None
        else calculate_bill(usage, table, selection, options=opts, quality=quality)
    )
    after = with_load(usage, dispatch.net_kw, source_suffix=" + ESS")
    bill = calculate_bill(after, table, selection, options=opts, quality=quality)

    # **투자비는 조달 사례 모델이 기본이다** (13세션). 설비비와 전기공사비를 나눠
    # 산정하고, 사용자가 단가나 총액을 넣었으면 그쪽이 이긴다.
    cost_model = model if model is not None else load_ess_cost_model()
    quote = cost_model.quote(capacity, indoor=indoor)
    if cost.is_unpriced:
        cost = EssCostInput.of_total(
            quote.total_won,
            source=(
                f"조달 사례 모델 — 설비 {money.won(quote.equipment_won, reason='—')} "
                f"+ 전기공사 {money.won(quote.electrical_won, reason='—')}"
            ),
        )
    investment = cost.investment_won(power)
    saving = base_bill.total_won - bill.total_won
    annual_saving = annualize(saving, base_bill.base_fee_months)
    breakeven = (
        annual_saving * payback_target_years / power if power > 0 and annual_saving > 0 else None
    )

    # 차익거래 — 계시별 단가는 요금표에서 온다. 절감액에 더하지 않는다.
    ref = reference if reference is not None else load_ess_cost_reference()
    arbitrage = arbitrage_value(
        usage,
        table,
        selection,
        usable_kwh=capacity * dod,
        round_trip=round_trip,
        base_fee_months=base_bill.base_fee_months,
        cycles_per_day=cycles_per_day,
        capex_energy_won_per_kwh=ref.default.capex_energy_won_per_kwh,
        quality=quality,
        options=opts,
    )

    # 2030년 전망 단가 기준 회수기간 — "언제쯤 성립하는가" 에 답한다.
    outlook_investment = power * ref.outlook.unit_cost_won_per_kw(discharge_hours)
    outlook_payback = payback_years(outlook_investment, annual_saving)
    # 차익거래를 더한 **상한**. 기본값은 피크저감만 쓴 보수적인 값이다.
    with_arbitrage = annual_saving + arbitrage.annual_won
    payback_with_arbitrage = payback_years(investment, with_arbitrage)
    outlook_payback_with_arbitrage = payback_years(outlook_investment, with_arbitrage)

    # 이미 실현된 차익거래가 얼마나 겹치는지. 겹침이 크면 상한 값이 과대평가다.
    assumed_cycles_kwh = capacity * dod * arbitrage.period_days * cycles_per_day
    overlap = dispatch.discharged_kwh / assumed_cycles_kwh if assumed_cycles_kwh > 0 else 0.0

    notices: list[Notice] = []
    if dispatch.charge_created_new_peak:
        notices.append(
            warn(
                f"경부하 충전이 목표를 넘는 새 피크를 만들었습니다 — 충전 시간대 최대 "
                f"{dispatch.charge_window_peak_kw:,.1f} kW > 목표 {target_kw:,.0f} kW. "
                "charge_limit_kw 로 충전 전력을 제한하십시오.",
                fact="ess.charge_new_peak",
            )
        )
    if not dispatch.target_met:
        notices.append(
            warn(
                f"목표 {target_kw:,.0f} kW 를 지키지 못한 에너지가 {dispatch.unmet_kwh:,.1f} kWh "
                f"있습니다. 실제 달성 피크는 {dispatch.achieved_peak_kw:,.1f} kW 입니다. "
                "출력이나 용량을 키우십시오.",
                fact="ess.target_unmet",
            )
        )
    if 0 < discharge_hours < high_rate_discharge_hours():
        notices.append(
            warn(
                f"산출 사양이 {c_rate(discharge_hours):.1f}C 방전에 해당합니다 "
                f"(방전시간 {discharge_hours:.2f}h). 정치형 LFP 는 통상 0.5~1C 연속이므로 "
                "고출력 셀 사양이며, 조달 사례보다 비쌀 수 있습니다.",
                fact="ess.high_c_rate",
            )
        )
    if (
        breakeven is not None
        and cost.unit_cost_won_per_kw is not None
        and breakeven < cost.unit_cost_won_per_kw
    ):
        notices.append(
            warn(
                f"손익분기 단가 {breakeven:,.0f} 원/kW 가 입력 단가 "
                f"{cost.unit_cost_won_per_kw:,.0f} 원/kW 보다 낮습니다 "
                f"({payback_target_years:.0f}년 회수 기준).",
                fact="ess.breakeven_below_unit_cost",
            )
        )
    # **성립 조건** — 고정 문구 대신 계산에서 판정이 나온다 (13세션).
    rated_hours = capacity / power if power > 0 else 0.0
    feasibility = cost_model.feasibility(
        discharge_hours=rated_hours,
        base_fee_won_per_kw=_base_fee_won_per_kw(table, selection),
        target_years=payback_target_years,
        actual_reduction_kw=base_bill.billing_demand_kw - bill.billing_demand_kw,
        quote=quote,
    )
    if not feasibility.feasible:
        # 아래 근거 목록의 성립 조건과 **같은 사실**이다. 성립하지 않을 때만
        # 주의로 먼저 나오고, 지문 대신 ID 로 접히므로 먼저 나온 주의가 남는다.
        notices.append(warn(feasibility.message(), fact="ess.feasibility"))
    realized_payback = payback_years(investment, annual_saving)
    if realized_payback is not None and realized_payback > payback_target_years:
        # **판정하지 않는다** (14세션 3-3). 두 수를 나란히 놓고 판단은 사용자에게 둔다.
        notices.append(
            warn(
                f"회수기간 {realized_payback:,.1f}년 — 배터리 보증 수명 "
                f"{payback_target_years:,.0f}년을 초과합니다.",
                fact="ess.payback_over_warranty",
            )
        )

    # **근거** — 사양·투자비·회수기간이 어느 산식과 어느 계수에서 나왔는지.
    # 17세션까지 이것들이 전부 확인사항에 쌓여 스물둘이 됐다. 툴팁으로 내린다.
    notices += [
        basis(
            f"용량 산정 기준: {sizing_basis} "
            f"(하루 최대 {excess.max_daily_excess_kwh:,.1f} kWh, "
            f"연속 구간 최대 {excess.max_event_excess_kwh:,.1f} kWh, "
            f"기간 합계 {excess.total_excess_kwh:,.1f} kWh).",
            fact="ess.sizing_basis",
        ),
        basis(
            f"방전시간 {discharge_hours:.2f}h ({c_rate(discharge_hours):.1f}C) 는 "
            "하루 최대 초과 에너지 ÷ 최대 초과 출력으로 **산출한 값**입니다. "
            "짧을수록 kW당 단가가 싸므로, 조달이 가능하다면 짧은 쪽이 맞습니다.",
            fact="ess.discharge_hours",
        ),
        basis(
            f"투자비는 출력 {power:,.1f} kW × kW당 단가로 냈습니다 (출처: {cost.source}). "
            "방전시간은 단가에 이미 반영되어 있어 다시 곱하지 않았습니다.",
            fact="ess.investment_basis",
        ),
        basis(cost_model.formula + " — 조달 사례 회귀입니다.", fact="ess.cost_model_formula"),
        basis(
            f"투자비 = 설비 {money.won(quote.equipment_won, reason='—')} + 전기공사 "
            f"{money.won(quote.electrical_won, reason='—')} (옥외 기준 "
            f"{money.won(quote.electrical_low_won, reason='—')}~"
            f"{money.won(quote.electrical_high_won, reason='—')} 구간의 대표값) "
            f"= {money.won(quote.total_won, reason='—')}.",
            fact="ess.quote_breakdown",
        ),
        basis(feasibility.message(), fact="ess.feasibility"),
        basis(
            f"왕복효율 {round_trip:.0%}, DoD {dod:.0%} 를 적용했습니다.",
            fact="ess.efficiency_coefficients",
        ),
        # **참고** — 전략·한계. 숫자를 만들지 않는다.
        info(
            "규칙기반 피크컷 단일 전략입니다. 목표 초과분을 방전하고 경부하에 충전합니다. "
            "최적 운전과는 차이가 있습니다 (요구사항서 부록 D.9).",
            fact="ess.strategy_limit",
        ),
        info(
            "**수익 구조** — 피크저감 수익은 출력(kW)에 비례해 용량을 늘려도 늘지 않고, "
            "차익거래 수익은 용량(kWh)에 비례하며, 투자비도 용량에 크게 비례합니다. "
            "따라서 용량을 늘릴수록 회수기간이 나빠집니다.",
            fact="ess.revenue_structure",
        ),
        info("열화·수명은 반영하지 않았습니다.", fact="ess.degradation_excluded"),
    ]
    if outlook_payback is not None:
        notices.append(
            basis(
                f"현재 단가 기준 {payback_years(investment, annual_saving):,.1f}년 / "
                f"{ref.outlook.label} 단가 기준 {outlook_payback:,.1f}년입니다 "
                "(피크저감 절감액만 반영).",
                fact="ess.outlook_payback",
            )
        )
    if payback_with_arbitrage is not None:
        notices.append(
            basis(
                f"차익거래 잠재 수익까지 더하면 현재 단가 {payback_with_arbitrage:,.1f}년 / "
                f"{ref.outlook.label} 단가 "
                f"{outlook_payback_with_arbitrage:,.1f}년입니다. **이쪽이 상한입니다** — "
                f"피크컷 디스패치가 이미 가정 사이클의 {overlap:.0%} 를 돌리고 있어 그만큼은 "
                "절감액에 이미 들어 있습니다.",
                fact="ess.payback_with_arbitrage",
            )
        )
    notices.extend(quote.notices)
    notices.extend(arbitrage.notices)

    return EssResult(
        excess=excess,
        dispatch=dispatch,
        power_kw=power,
        capacity_kwh=capacity,
        discharge_hours=discharge_hours,
        cost=cost,
        investment_won=investment,
        base_saving_won=base_bill.total_base_won - bill.total_base_won,
        energy_saving_won=base_bill.total_energy_won - bill.total_energy_won,
        total_saving_won=saving,
        annual_saving_won=annual_saving,
        payback_years=payback_years(investment, annual_saving),
        breakeven_unit_cost_won_per_kw=breakeven,
        payback_target_years=payback_target_years,
        billing_demand_kw=bill.billing_demand_kw,
        arbitrage=arbitrage,
        payback_with_arbitrage_years=payback_with_arbitrage,
        outlook_payback_years=outlook_payback,
        outlook_payback_with_arbitrage_years=outlook_payback_with_arbitrage,
        outlook_label=ref.outlook.label,
        quote=quote,
        feasibility=feasibility,
        notices=tuple(notices),
    )


def ess_payback_curve(
    usage: UsageData,
    table: TariffTable,
    selection: TariffSelection,
    *,
    target_kw: float,
    discharge_hours: tuple[float, ...] = DEFAULT_CURVE_DISCHARGE_HOURS,
    technology: str | None = None,
    reference: EssCostReference | None = None,
    charge_mask: pd.Series | None = None,
    round_trip: float | None = None,
    dod: float | None = None,
    cycles_per_day: float | None = None,
    baseline: BillingResult | None = None,
    quality: QualityReport | None = None,
    options: BillingOptions | None = None,
) -> pd.DataFrame:
    """방전시간별 회수기간. **용량을 늘릴수록 나빠진다는 것을 보이는 표다.**

    출력은 목표에서 정해져 고정이고 용량만 ``출력 × 방전시간`` 으로 키운다.
    피크저감 수익은 출력에 붙어 그대로인데 투자비는 용량을 따라 오르므로
    회수기간이 단조증가한다.

    차익거래 잠재 수익을 더한 열을 함께 낸다 — 이쪽이 **상한**이다. 기본 열은
    순부하 재계산 절감액만 쓴 값이라 이중 계산이 없다.
    """
    round_trip = default_round_trip() if round_trip is None else round_trip
    dod = default_dod() if dod is None else dod
    cycles_per_day = default_cycles_per_day() if cycles_per_day is None else cycles_per_day
    opts = options if options is not None else BillingOptions()
    interval = usage.meta.interval_minutes
    excess = analyze_peak_excess(usage.kw, target_kw, interval)
    power, _ = size_for_target(excess, dod=dod, round_trip=round_trip)
    ref = reference if reference is not None else load_ess_cost_reference()
    item = ref.default if technology is None else ref.technology(technology)

    base_bill = (
        baseline
        if baseline is not None
        else calculate_bill(usage, table, selection, options=opts, quality=quality)
    )
    mask = (
        charge_mask
        if charge_mask is not None
        else light_band_mask(usage, table, selection=selection, options=opts)
    )

    rows: list[dict[str, object]] = []
    for hours in discharge_hours:
        capacity = power * hours
        dispatch = dispatch_peak_shaving(
            usage.kw,
            target_kw=target_kw,
            power_kw=power,
            capacity_kwh=capacity,
            charge_mask=mask,
            interval_minutes=interval,
            round_trip=round_trip,
            dod=dod,
        )
        after = with_load(usage, dispatch.net_kw, source_suffix=" + ESS")
        bill = calculate_bill(after, table, selection, options=opts, quality=quality)
        annual = annualize(base_bill.total_won - bill.total_won, base_bill.base_fee_months)
        investment = power * item.unit_cost_won_per_kw(hours)
        arbitrage = arbitrage_value(
            usage,
            table,
            selection,
            usable_kwh=capacity * dod,
            round_trip=round_trip,
            base_fee_months=base_bill.base_fee_months,
            cycles_per_day=cycles_per_day,
            options=opts,
        )
        rows.append(
            {
                "방전시간(h)": hours,
                "용량(kWh)": capacity,
                "kW당 단가(원)": item.unit_cost_won_per_kw(hours),
                "투자비(원)": investment,
                "12개월 환산 절감액(원)": annual,
                "회수기간(피크저감만, 년)": payback_years(investment, annual),
                "차익거래 잠재(원/년)": arbitrage.annual_won,
                "회수기간(차익거래 포함, 년)": payback_years(
                    investment, annual + arbitrage.annual_won
                ),
                "목표 달성": dispatch.target_met,
            }
        )
    return pd.DataFrame(rows).set_index("방전시간(h)")


def excess_table(kw: pd.Series, targets: tuple[float, ...], interval_minutes: int) -> pd.DataFrame:
    """목표 피크별 초과 분석 표 (부록 B 형식)."""
    rows = [analyze_peak_excess(kw, target, interval_minutes).__dict__ for target in targets]
    return pd.DataFrame(rows).set_index("target_kw")


def excess_slots_by_day(kw: pd.Series, target_kw: float, interval_minutes: int) -> pd.Series:
    """날짜별 초과 슬롯 수. 하루에 몇 번 방전하는지 가늠할 때 쓴다."""
    observed = kw.dropna()
    over = observed[observed > target_kw]
    starts = slot_start(pd.DatetimeIndex(over.index), interval_minutes)
    return over.groupby(starts.date).size()
