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

from kwise.io import UsageData, slot_start
from kwise.measures.base import Certainty, annualize, payback_years
from kwise.measures.netload import with_load
from kwise.quality import QualityReport
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
    "DEFAULT_DOD",
    "DEFAULT_PAYBACK_TARGET_YEARS",
    "DEFAULT_ROUND_TRIP",
    "DispatchResult",
    "EssResult",
    "PeakExcess",
    "analyze_peak_excess",
    "dispatch_peak_shaving",
    "evaluate_ess",
    "excess_slots_by_day",
    "light_band_mask",
    "size_for_target",
]

DEFAULT_ROUND_TRIP = 0.88
DEFAULT_DOD = 0.90
DEFAULT_PAYBACK_TARGET_YEARS = 10.0


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


def size_for_target(
    excess: PeakExcess,
    *,
    dod: float = DEFAULT_DOD,
    round_trip: float = DEFAULT_ROUND_TRIP,
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
    energy = {
        "event": excess.max_event_excess_kwh,
        "daily": excess.max_daily_excess_kwh,
        "total": excess.total_excess_kwh,
    }
    if basis not in energy:
        raise ValueError(f"알 수 없는 용량 산정 기준입니다: {basis!r}")
    if not 0 < dod <= 1:
        raise ValueError(f"DoD 는 0 초과 1 이하여야 합니다: {dod}")
    discharge_efficiency = math.sqrt(round_trip)
    # 방전 손실을 감안해 계통에 내보낼 에너지를 배터리 저장량으로 환산한다.
    nameplate = energy[basis] / discharge_efficiency / dod
    return excess.max_excess_kw, nameplate


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


def dispatch_peak_shaving(
    kw: pd.Series,
    *,
    target_kw: float,
    power_kw: float,
    capacity_kwh: float,
    charge_mask: pd.Series,
    interval_minutes: int,
    round_trip: float = DEFAULT_ROUND_TRIP,
    dod: float = DEFAULT_DOD,
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
    """ESS 평가. 출력과 에너지를 분리해 담는다."""

    excess: PeakExcess
    dispatch: DispatchResult
    power_kw: float
    capacity_kwh: float
    unit_cost_won_per_kwh: float
    investment_won: float
    base_saving_won: float
    energy_saving_won: float
    total_saving_won: float
    annual_saving_won: float
    payback_years: float | None
    breakeven_unit_cost_won_per_kwh: float | None
    payback_target_years: float
    billing_demand_kw: float
    certainty: Certainty = Certainty.MEDIUM_LOW
    warnings: tuple[str, ...] = field(default=())
    notes: tuple[str, ...] = field(default=())


def evaluate_ess(
    usage: UsageData,
    table: TariffTable,
    selection: TariffSelection,
    *,
    target_kw: float,
    unit_cost_won_per_kwh: float,
    charge_mask: pd.Series | None = None,
    power_kw: float | None = None,
    capacity_kwh: float | None = None,
    sizing_basis: str = "daily",
    charge_limit_kw: float | None = None,
    respect_target_when_charging: bool = True,
    round_trip: float = DEFAULT_ROUND_TRIP,
    dod: float = DEFAULT_DOD,
    payback_target_years: float = DEFAULT_PAYBACK_TARGET_YEARS,
    baseline: BillingResult | None = None,
    quality: QualityReport | None = None,
    options: BillingOptions | None = None,
) -> EssResult:
    """목표 요금적용전력을 받아 사양·절감액·회수기간을 낸다.

    Args:
        unit_cost_won_per_kwh: 배터리 단가. 사용자 입력이며 기본값이 없다.
        power_kw, capacity_kwh: 주지 않으면 목표에서 역산한다.
        payback_target_years: 손익분기 단가를 역산할 회수기간 (기본 10년).
    """
    opts = options if options is not None else BillingOptions()
    interval = usage.meta.interval_minutes
    excess = analyze_peak_excess(usage.kw, target_kw, interval)

    sized_power, sized_capacity = size_for_target(
        excess, dod=dod, round_trip=round_trip, basis=sizing_basis
    )
    power = sized_power if power_kw is None else power_kw
    capacity = sized_capacity if capacity_kwh is None else capacity_kwh

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

    investment = capacity * unit_cost_won_per_kwh
    saving = base_bill.total_won - bill.total_won
    annual_saving = annualize(saving, base_bill.base_fee_months)
    breakeven = (
        annual_saving * payback_target_years / capacity
        if capacity > 0 and annual_saving > 0
        else None
    )

    warnings: list[str] = []
    if dispatch.charge_created_new_peak:
        warnings.append(
            f"경부하 충전이 목표를 넘는 새 피크를 만들었습니다 — 충전 시간대 최대 "
            f"{dispatch.charge_window_peak_kw:,.1f} kW > 목표 {target_kw:,.0f} kW. "
            "charge_limit_kw 로 충전 전력을 제한하십시오."
        )
    if not dispatch.target_met:
        warnings.append(
            f"목표 {target_kw:,.0f} kW 를 지키지 못한 에너지가 {dispatch.unmet_kwh:,.1f} kWh "
            f"있습니다. 실제 달성 피크는 {dispatch.achieved_peak_kw:,.1f} kW 입니다. "
            "출력이나 용량을 키우십시오."
        )
    if breakeven is not None and breakeven < unit_cost_won_per_kwh:
        warnings.append(
            f"손익분기 단가 {breakeven:,.0f} 원/kWh 가 입력 단가 "
            f"{unit_cost_won_per_kwh:,.0f} 원/kWh 보다 낮습니다. "
            f"{payback_target_years:.0f}년 회수는 성립하지 않습니다."
        )
    notes = [
        "규칙기반 피크컷 단일 전략입니다. 목표 초과분을 방전하고 경부하에 충전합니다. "
        "최적 운전과는 차이가 있습니다 (요구사항서 부록 D.8).",
        f"용량 산정 기준: {sizing_basis} "
        f"(하루 최대 {excess.max_daily_excess_kwh:,.1f} kWh, "
        f"연속 구간 최대 {excess.max_event_excess_kwh:,.1f} kWh, "
        f"기간 합계 {excess.total_excess_kwh:,.1f} kWh).",
        f"왕복효율 {round_trip:.0%}, DoD {dod:.0%} 를 적용했습니다.",
        "열화·수명은 반영하지 않았습니다.",
    ]
    return EssResult(
        excess=excess,
        dispatch=dispatch,
        power_kw=power,
        capacity_kwh=capacity,
        unit_cost_won_per_kwh=unit_cost_won_per_kwh,
        investment_won=investment,
        base_saving_won=base_bill.total_base_won - bill.total_base_won,
        energy_saving_won=base_bill.total_energy_won - bill.total_energy_won,
        total_saving_won=saving,
        annual_saving_won=annual_saving,
        payback_years=payback_years(investment, annual_saving),
        breakeven_unit_cost_won_per_kwh=breakeven,
        payback_target_years=payback_target_years,
        billing_demand_kw=bill.billing_demand_kw,
        warnings=tuple(warnings),
        notes=tuple(notes),
    )


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
