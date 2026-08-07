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
from kwise.measures.arbitrage import (
    ArbitrageValue,
    arbitrage_value,
    c_rate,
    default_cycles_per_day,
)
from kwise.measures.base import Certainty, annualize, payback_years
from kwise.measures.ess_cost import EssCostInput, EssCostReference, load_ess_cost_reference
from kwise.measures.netload import with_load
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
    "DispatchResult",
    "EssResult",
    "PeakExcess",
    "analyze_peak_excess",
    "default_dod",
    "default_payback_target_years",
    "default_round_trip",
    "dispatch_peak_shaving",
    "ess_payback_curve",
    "evaluate_ess",
    "excess_slots_by_day",
    "high_rate_discharge_hours",
    "light_band_mask",
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
    if not 0 < dod <= 1:
        raise ValueError(f"DoD 는 0 초과 1 이하여야 합니다: {dod}")
    discharge_efficiency = math.sqrt(round_trip)
    # 방전 손실을 감안해 계통에 내보낼 에너지를 배터리 저장량으로 환산한다.
    nameplate = energy[basis] / discharge_efficiency / dod
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
    reference_verdict: str = ""
    certainty: Certainty = Certainty.MEDIUM_LOW
    warnings: tuple[str, ...] = field(default=())
    notes: tuple[str, ...] = field(default=())

    @property
    def c_rate(self) -> float:
        """방전 C-rate. 0.5h 는 2C 다."""
        return c_rate(self.discharge_hours)

    @property
    def unit_cost_won_per_kw(self) -> float | None:
        return self.cost.unit_cost_won_per_kw


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

    # 투자비 = 출력 × kW당 단가. 총액을 직접 넣었으면 그 값을 그대로 쓴다.
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
    if 0 < discharge_hours < high_rate_discharge_hours():
        warnings.append(
            f"산출 사양이 {c_rate(discharge_hours):.1f}C 방전에 해당합니다 "
            f"(방전시간 {discharge_hours:.2f}h). 정치형 LFP 는 통상 0.5~1C 연속이므로 "
            "고출력 셀 사양이며, 참고단가보다 비쌀 수 있습니다."
        )
    if (
        breakeven is not None
        and cost.unit_cost_won_per_kw is not None
        and breakeven < cost.unit_cost_won_per_kw
    ):
        warnings.append(
            f"손익분기 단가 {breakeven:,.0f} 원/kW 가 입력 단가 "
            f"{cost.unit_cost_won_per_kw:,.0f} 원/kW 보다 낮습니다. "
            f"{payback_target_years:.0f}년 회수는 성립하지 않습니다."
        )
    verdict = ref.verdict(breakeven, discharge_hours)

    notes = [
        "규칙기반 피크컷 단일 전략입니다. 목표 초과분을 방전하고 경부하에 충전합니다. "
        "최적 운전과는 차이가 있습니다 (요구사항서 부록 D.9).",
        f"용량 산정 기준: {sizing_basis} "
        f"(하루 최대 {excess.max_daily_excess_kwh:,.1f} kWh, "
        f"연속 구간 최대 {excess.max_event_excess_kwh:,.1f} kWh, "
        f"기간 합계 {excess.total_excess_kwh:,.1f} kWh).",
        f"방전시간 {discharge_hours:.2f}h ({c_rate(discharge_hours):.1f}C) 는 "
        "하루 최대 초과 에너지 ÷ 최대 초과 출력으로 **산출한 값**입니다. "
        "짧을수록 kW당 단가가 싸므로, 조달이 가능하다면 짧은 쪽이 맞습니다.",
        f"투자비는 출력 {power:,.1f} kW × kW당 단가로 냈습니다 (출처: {cost.source}). "
        "방전시간은 단가에 이미 반영되어 있어 다시 곱하지 않았습니다.",
        "**수익 구조** — 피크저감 수익은 출력(kW)에 비례해 용량을 늘려도 늘지 않고, "
        "차익거래 수익은 용량(kWh)에 비례하며, 투자비도 용량에 크게 비례합니다. "
        "따라서 용량을 늘릴수록 회수기간이 나빠집니다.",
        f"참고단가 출처: {ref.citation}. {ref.lower_bound_note}",
        verdict,
        f"왕복효율 {round_trip:.0%}, DoD {dod:.0%} 를 적용했습니다.",
        "열화·수명은 반영하지 않았습니다.",
    ]
    if outlook_payback is not None:
        notes.append(
            f"현재 단가 기준 {payback_years(investment, annual_saving):,.1f}년 / "
            f"{ref.outlook.label} 단가 기준 {outlook_payback:,.1f}년입니다 "
            "(피크저감 절감액만 반영)."
        )
    if payback_with_arbitrage is not None:
        notes.append(
            f"차익거래 잠재 수익까지 더하면 현재 단가 {payback_with_arbitrage:,.1f}년 / "
            f"{ref.outlook.label} 단가 "
            f"{outlook_payback_with_arbitrage:,.1f}년입니다. **이쪽이 상한입니다** — "
            f"피크컷 디스패치가 이미 가정 사이클의 {overlap:.0%} 를 돌리고 있어 그만큼은 "
            "절감액에 이미 들어 있습니다."
        )
    notes.extend(arbitrage.notes)

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
        reference_verdict=verdict,
        warnings=tuple(warnings),
        notes=tuple(notes),
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
