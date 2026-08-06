"""태양광 (요구사항서 7.3, 5.7).

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

from kwise.io import UsageData
from kwise.measures.base import Certainty, annualize, payback_years
from kwise.measures.netload import apply_generation
from kwise.pv import PvSystemConfig, WeatherData, align_simulation, simulate
from kwise.quality import QualityReport
from kwise.tariff import (
    BillingOptions,
    BillingResult,
    TariffSelection,
    TariffTable,
    calculate_bill,
)

__all__ = [
    "DEFAULT_MODULE_DENSITY_KWP_PER_M2",
    "DEFAULT_STEPS",
    "DEFAULT_USABLE_RATIO",
    "POWER_FACTOR_FLOOR_PCT",
    "SolarCurve",
    "SolarPoint",
    "power_factor_after_pct",
    "roof_capacity_limit_kwp",
    "solar_curve",
    "unit_generation_kw",
]

DEFAULT_USABLE_RATIO = 0.6  # 옥상 가용 비율 (요구사항서 3.3)
DEFAULT_MODULE_DENSITY_KWP_PER_M2 = 0.20
DEFAULT_STEPS = 20
POWER_FACTOR_FLOOR_PCT = 90.0


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


def power_factor_after_pct(
    load_kw: pd.Series,
    generation_kw: pd.Series,
    *,
    power_factor_pct: float,
) -> float:
    """PV 도입 후 예상 역률 (요구사항서 5.7).

    무효전력은 그대로인데 PV 가 유효전력만 상쇄하므로 역률이 떨어진다.
    발전 시간대의 평균 유효전력으로 판정한다.
    """
    if not 0 < power_factor_pct <= 100:
        raise ValueError(f"역률은 0~100% 여야 합니다: {power_factor_pct}")
    generating = (generation_kw > 0) & load_kw.notna()
    if not bool(generating.any()):
        return power_factor_pct

    before = float(load_kw[generating].mean())
    after = float((load_kw - generation_kw).clip(lower=0.0)[generating].mean())
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
    sensitivity_factor: float
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
    sensitivity_factor: float = 1.0,
    power_factor_pct: float = 92.0,
    baseline: BillingResult | None = None,
    quality: QualityReport | None = None,
    options: BillingOptions | None = None,
) -> SolarCurve:
    """0 부터 상한까지 용량을 키우며 절감액을 재계산한다.

    Args:
        unit_kw_per_kwp: :func:`unit_generation_kw` 의 결과.
        max_capacity_kwp: 상한. 보통 :func:`roof_capacity_limit_kwp`.
        unit_cost_won_per_kwp: 설치 단가. 사용자 입력이며 기본값이 없다.
        sensitivity_factor: 감도 계수 (9.2). PV 출력에만 적용한다.
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
    unit = unit_kw_per_kwp.reindex(pd.DatetimeIndex(usage.kw.index)).fillna(0.0)

    points: list[SolarPoint] = []
    warnings: list[str] = []
    for step in range(steps + 1):  # 0 kWp 포함
        capacity = max_capacity_kwp * step / steps
        generation = unit * capacity * sensitivity_factor
        net = apply_generation(usage, generation)
        bill = calculate_bill(net.usage, table, selection, options=opts, quality=quality)

        investment = capacity * unit_cost_won_per_kwp
        saving = base_bill.total_won - bill.total_won
        annual_saving = annualize(saving, base_bill.base_fee_months)
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
                power_factor_after_pct=power_factor_after_pct(
                    usage.kw, generation, power_factor_pct=power_factor_pct
                ),
            )
        )

    largest = points[-1]
    if largest.power_factor_after_pct < POWER_FACTOR_FLOOR_PCT:
        warnings.append(
            f"PV {largest.capacity_kwp:,.0f} kWp 도입 시 예상 역률이 "
            f"{largest.power_factor_after_pct:.1f}% 로 지상 90% 를 밑돕니다. "
            "무효전력은 그대로인데 유효전력만 상쇄되기 때문입니다. "
            "콘덴서 용량 조정이 필요합니다 (요구사항서 5.7)."
        )
    notes = [
        "발전량 예측은 피크 발전량을 과소 산출하는 경향이 있어 결과가 보수적입니다 "
        "(요구사항서 9.1).",
        f"감도 계수 {sensitivity_factor:.2f} 를 PV 출력에 적용했습니다.",
        "용량마다 요금을 다시 계산했습니다. 절감액을 빼기로 어림하지 않았습니다.",
        "역률요금은 추정 역률 기반 참고 산출입니다. 무효전력 실측이 없습니다.",
    ]
    return SolarCurve(
        points=tuple(points),
        selection=selection,
        baseline_total_won=base_bill.total_won,
        baseline_base_won=base_bill.total_base_won,
        baseline_energy_won=base_bill.total_energy_won,
        sensitivity_factor=sensitivity_factor,
        max_capacity_kwp=max_capacity_kwp,
        unit_cost_won_per_kwp=unit_cost_won_per_kwp,
        base_fee_months=base_bill.base_fee_months,
        warnings=tuple(warnings),
        notes=tuple(notes),
    )
