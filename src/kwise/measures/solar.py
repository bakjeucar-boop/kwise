"""태양광 (요구사항서 7.5, 5.7).

**용량 곡선이 핵심이다.** "태양광을 얼마나 넣어야 하나" 가 실제로 가장 많이 받는
질문이다. 0 부터 옥상 가용면적 상한까지 20단계로 자가소비율·절감액·잉여·회수기간을
낸다.

용량마다 시뮬레이션을 다시 돌리지 않는다. pvwatts 직류 출력과 인버터 모델이 모두
정격에 **선형**이므로(정격과 인버터 용량이 함께 커진다) 1 kWp 프로파일을 한 번
구해 곱한다. 용량 단계가 20개라도 태양 위치 계산은 한 번뿐이다.

절감액은 재계산이다. 용량마다 순부하를 만들어 요금을 처음부터 다시 산출한다.
단계는 순차 처리하고 요약(:class:`SolarPoint`)만 남긴다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from kwise.io import UsageData, slot_start
from kwise.measures.base import Certainty, annualize, payback_years
from kwise.measures.netload import apply_generation
from kwise.pv import PvSystemConfig, WeatherData, align_simulation, sharpen, simulate
from kwise.quality import QualityReport
from kwise.tariff import (
    DAY_WINDOW,
    LAGGING_STANDARD_PCT,
    BillingOptions,
    BillingResult,
    TariffSelection,
    TariffTable,
    calculate_bill,
    lagging_adjustment_ratio,
)

__all__ = [
    "DEFAULT_MODULE_DENSITY_KWP_PER_M2",
    "DEFAULT_STEPS",
    "DEFAULT_USABLE_RATIO",
    "POWER_FACTOR_FLOOR_PCT",
    "SolarCurve",
    "SolarPoint",
    "day_window_mask",
    "power_factor_after_pct",
    "roof_capacity_limit_kwp",
    "solar_curve",
    "unit_generation_kw",
]

DEFAULT_USABLE_RATIO = 0.6  # 옥상 가용 비율 (요구사항서 3.3)
DEFAULT_MODULE_DENSITY_KWP_PER_M2 = 0.20
DEFAULT_STEPS = 20
# 약관 제41조의 유지 의무이자 제43조의 요금 기준. 이 아래로 떨어지면 돈이 나간다.
POWER_FACTOR_FLOOR_PCT = LAGGING_STANDARD_PCT


def roof_capacity_limit_kwp(
    roof_area_m2: float,
    *,
    usable_ratio: float = DEFAULT_USABLE_RATIO,
    gcr: float = 0.4,
    module_density_kwp_per_m2: float = DEFAULT_MODULE_DENSITY_KWP_PER_M2,
) -> float:
    """옥상 면적에서 설치 상한 용량을 낸다.

    가용면적 × GCR 이 모듈 면적이고, 거기에 모듈 밀도를 곱한다.
    """
    if roof_area_m2 < 0:
        raise ValueError(f"옥상 면적은 음수일 수 없습니다: {roof_area_m2}")
    return roof_area_m2 * usable_ratio * gcr * module_density_kwp_per_m2


def unit_generation_kw(
    usage: UsageData,
    weather: WeatherData,
    config: PvSystemConfig,
) -> pd.Series:
    """1 kWp 당 발전 출력을 부하 라벨에 정렬해 낸다.

    설비 구성(어레이 비율·방위·경사)은 그대로 두고 크기만 1 kWp 로 맞춘 뒤
    한 번만 시뮬레이션한다. 이후 용량은 곱셈으로 얻는다.
    """
    capacity = config.total_capacity_kwp
    if capacity <= 0:
        raise ValueError("용량이 0 인 구성으로는 단위 프로파일을 만들 수 없습니다.")
    simulation = simulate(weather, config)
    aligned = align_simulation(
        simulation, pd.DatetimeIndex(usage.kw.index), usage.meta.interval_minutes
    )
    return (aligned.kw / capacity).rename("pv_kw_per_kwp")


def day_window_mask(index: pd.DatetimeIndex, interval_minutes: int) -> pd.Series:
    """역률요금 주간 창(08~22시) 마스크 — **구간 시작 시각 기준** (제43조 ②).

    라벨은 구간 끝이므로 ``08:00`` 슬롯은 07:45~08:00 이라 야간이다.
    첫 주간 슬롯은 ``08:15`` 다.
    """
    hours = slot_start(pd.DatetimeIndex(index), interval_minutes).hour
    start, end = DAY_WINDOW
    return pd.Series((hours >= start) & (hours < end), index=index, name="day_window")


def power_factor_after_pct(
    load_kw: pd.Series,
    generation_kw: pd.Series,
    *,
    power_factor_pct: float,
    interval_minutes: int = 15,
) -> float:
    """PV 도입 후 예상 **주간 지상역률** (요구사항서 5.7, 약관 제43조 ②).

    무효전력은 그대로인데 PV 가 유효전력만 상쇄하므로 역률이 떨어진다.

    **판정 창은 08~22시다.** 약관이 그 시간대의 지상역률로 요금을 매기기 때문이다.
    발전 시간대(대략 06~19시)로 재면 요금 규칙과 창이 어긋난다. 창 안의 평균
    유효전력으로 판정하며, 무효전력은 도입 전 유효전력과 기준 역률에서 역산한
    값을 그대로 유지한다 (PV 는 무효전력을 만들지도 없애지도 않는다).
    """
    if not 0 < power_factor_pct <= 100:
        raise ValueError(f"역률은 0~100% 여야 합니다: {power_factor_pct}")
    window = day_window_mask(pd.DatetimeIndex(load_kw.index), interval_minutes)
    observed = window & load_kw.notna()
    if not bool(observed.any()):
        return power_factor_pct

    before = float(load_kw[observed].mean())
    after = float((load_kw - generation_kw).clip(lower=0.0)[observed].mean())
    if before <= 0:
        return power_factor_pct

    ratio = power_factor_pct / 100.0
    reactive = before * math.tan(math.acos(ratio))
    if after <= 0:
        return 0.0
    return 100.0 * after / math.hypot(after, reactive)


@dataclass(frozen=True)
class SolarPoint:
    """용량 곡선의 한 점. 시계열은 들고 있지 않는다."""

    capacity_kwp: float
    generation_kwh: float
    self_consumed_kwh: float
    surplus_kwh: float
    self_consumption_ratio: float | None
    billing_demand_kw: float
    base_saving_won: float
    energy_saving_won: float
    total_saving_won: float
    annual_saving_won: float
    investment_won: float
    payback_years: float | None
    power_factor_after_pct: float
    power_factor_extra_won: float
    """도입 후 역률로 늘어나는 역률요금 (원). 감액이면 음수다 (약관 제43조)."""

    @property
    def saving_after_power_factor_won(self) -> float:
        """역률 악화분을 뺀 절감액. **이것이 실제로 남는 돈이다.**"""
        return self.total_saving_won - self.power_factor_extra_won

    @property
    def surplus_ratio(self) -> float | None:
        if self.generation_kwh <= 0:
            return None
        return self.surplus_kwh / self.generation_kwh


@dataclass(frozen=True, eq=False)
class SolarCurve:
    """용량 곡선 전체."""

    points: tuple[SolarPoint, ...]
    selection: TariffSelection
    baseline_total_won: float
    baseline_base_won: float
    baseline_energy_won: float
    sharpness: float
    max_capacity_kwp: float
    unit_cost_won_per_kwp: float
    base_fee_months: float
    certainty: Certainty = Certainty.MEDIUM
    warnings: tuple[str, ...] = field(default=())
    notes: tuple[str, ...] = field(default=())

    def frame(self) -> pd.DataFrame:
        """표로 그릴 수 있는 형태."""
        return pd.DataFrame([point.__dict__ for point in self.points]).set_index("capacity_kwp")

    @property
    def best_payback(self) -> SolarPoint | None:
        candidates = [point for point in self.points if point.payback_years is not None]
        if not candidates:
            return None
        return min(candidates, key=lambda point: point.payback_years or math.inf)


def solar_curve(
    usage: UsageData,
    table: TariffTable,
    selection: TariffSelection,
    unit_kw_per_kwp: pd.Series,
    *,
    max_capacity_kwp: float,
    unit_cost_won_per_kwp: float,
    steps: int = DEFAULT_STEPS,
    sharpness: float = 1.0,
    power_factor_pct: float = LAGGING_STANDARD_PCT,
    baseline: BillingResult | None = None,
    quality: QualityReport | None = None,
    options: BillingOptions | None = None,
) -> SolarCurve:
    """0 부터 상한까지 용량을 키우며 절감액을 재계산한다.

    Args:
        unit_kw_per_kwp: :func:`unit_generation_kw` 의 결과.
        max_capacity_kwp: 상한. 보통 :func:`roof_capacity_limit_kwp`.
        unit_cost_won_per_kwp: 설치 단가. 사용자 입력이며 기본값이 없다.
        sharpness: 감도 첨예도 계수 (9.2). PV 출력에만 적용한다. 일별 총량을
            보존하고 곡선의 뾰족한 정도만 바꾼다.
    """
    if steps < 1:
        raise ValueError(f"단계 수는 1 이상이어야 합니다: {steps}")
    if max_capacity_kwp < 0:
        raise ValueError(f"상한 용량은 음수일 수 없습니다: {max_capacity_kwp}")

    opts = options if options is not None else BillingOptions()
    base_bill = (
        baseline
        if baseline is not None
        else calculate_bill(usage, table, selection, options=opts, quality=quality)
    )
    # 첨예도 조정을 **단위 프로파일에 한 번만** 건다. sharpen 은 양의 상수배에
    # 대해 동차이므로(평균·편차·클램프·재정규화가 모두 비례) 용량을 곱한 뒤
    # 조정한 것과 결과가 같다. 20단계마다 다시 계산할 이유가 없다.
    unit = sharpen(unit_kw_per_kwp.reindex(pd.DatetimeIndex(usage.kw.index)).fillna(0.0), sharpness)

    points: list[SolarPoint] = []
    warnings: list[str] = []
    for step in range(steps + 1):  # 0 kWp 포함
        capacity = max_capacity_kwp * step / steps
        generation = unit * capacity
        net = apply_generation(usage, generation)
        bill = calculate_bill(net.usage, table, selection, options=opts, quality=quality)

        investment = capacity * unit_cost_won_per_kwp
        saving = base_bill.total_won - bill.total_won
        annual_saving = annualize(saving, base_bill.base_fee_months)

        # 역률 악화분 (약관 제43조). 기준 역률 대비 조정 비율의 차이를
        # 도입 후 기본요금에 곱한다. 92% 미만이면 양수(추가)다.
        after_pct = power_factor_after_pct(
            usage.kw,
            generation,
            power_factor_pct=power_factor_pct,
            interval_minutes=usage.meta.interval_minutes,
        )
        extra_won = bill.total_base_won * (
            lagging_adjustment_ratio(after_pct) - lagging_adjustment_ratio(power_factor_pct)
        )
        points.append(
            SolarPoint(
                capacity_kwp=capacity,
                generation_kwh=net.generated_kwh,
                self_consumed_kwh=net.self_consumed_kwh,
                surplus_kwh=net.surplus_kwh,
                self_consumption_ratio=net.self_consumption_ratio,
                billing_demand_kw=bill.billing_demand_kw,
                base_saving_won=base_bill.total_base_won - bill.total_base_won,
                energy_saving_won=base_bill.total_energy_won - bill.total_energy_won,
                total_saving_won=saving,
                annual_saving_won=annual_saving,
                investment_won=investment,
                payback_years=payback_years(investment, annual_saving),
                power_factor_after_pct=after_pct,
                power_factor_extra_won=extra_won,
            )
        )

    largest = points[-1]
    if largest.power_factor_after_pct < POWER_FACTOR_FLOOR_PCT:
        warnings.append(
            f"PV {largest.capacity_kwp:,.0f} kWp 도입 시 예상 주간(08~22시) 지상역률이 "
            f"{largest.power_factor_after_pct:.1f}% 로 기준 "
            f"{POWER_FACTOR_FLOOR_PCT:.0f}% 를 밑돕니다. 무효전력은 그대로인데 "
            "유효전력만 상쇄되기 때문입니다. 역률요금이 "
            f"{largest.power_factor_extra_won:,.0f} 원 늘어 절감액이 "
            f"{largest.total_saving_won:,.0f} → "
            f"{largest.saving_after_power_factor_won:,.0f} 원이 됩니다. "
            "콘덴서 용량 조정이 필요합니다 (기본공급약관 제41·43조, 요구사항서 5.7)."
        )
    notes = [
        "발전량 예측은 피크 발전량을 과소 산출하는 경향이 있어 결과가 보수적입니다 "
        "(요구사항서 9.1).",
        f"감도 첨예도 계수 s={sharpness:.2f} 를 PV 출력에 적용했습니다. "
        "일별 총 발전량은 보존되고 피크만 달라집니다 (요구사항서 9.2).",
        "용량마다 요금을 다시 계산했습니다. 절감액을 빼기로 어림하지 않았습니다.",
        f"역률 판정 창은 08~22시(구간 시작 기준)이며 기준은 지상 "
        f"{LAGGING_STANDARD_PCT:.0f}% 입니다 (기본공급약관 제43조 ②). "
        f"도입 전 추정 역률 {power_factor_pct:.1f}% 에서 시작합니다.",
        "역률요금은 추정 역률 기반 참고 산출입니다. 무효전력 실측이 없습니다 "
        "(약관 제42조는 30분 누적 계량을 요구하는데 우리 데이터는 15분이고 "
        "무효전력이 없습니다).",
    ]
    return SolarCurve(
        points=tuple(points),
        selection=selection,
        baseline_total_won=base_bill.total_won,
        baseline_base_won=base_bill.total_base_won,
        baseline_energy_won=base_bill.total_energy_won,
        sharpness=sharpness,
        max_capacity_kwp=max_capacity_kwp,
        unit_cost_won_per_kwp=unit_cost_won_per_kwp,
        base_fee_months=base_bill.base_fee_months,
        warnings=tuple(warnings),
        notes=tuple(notes),
    )
