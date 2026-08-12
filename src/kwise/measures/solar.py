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
from itertools import pairwise

import pandas as pd

from kwise import money
from kwise.io import UsageData, slot_start
from kwise.measures.base import Certainty, annualize, payback_years
from kwise.measures.netload import apply_generation
from kwise.measures.pv_cost import (
    PV_COST_BASIS_NOTE,
    PV_REFERENCE_NOTE,
    SCALE_ECONOMY_NOTE,
    PvCostInput,
)
from kwise.progress import ProgressReporter, record
from kwise.pv import PvSystemConfig, WeatherData, align_simulation, sharpen, simulate
from kwise.quality import QualityReport
from kwise.rules import assumption
from kwise.tariff import (
    BillingOptions,
    BillingResult,
    TariffSelection,
    TariffTable,
    calculate_bill,
    day_window,
    lagging_adjustment_ratio,
    lagging_standard_pct,
)

__all__ = [
    "DEFAULT_MODULE_DENSITY_KWP_PER_M2",
    "DEFAULT_STEPS",
    "DEFAULT_USABLE_RATIO",
    "CapacityVerdict",
    "SolarCurve",
    "SolarPoint",
    "capacity_verdict",
    "day_window_mask",
    "payback_tie_ratio",
    "power_factor_after_pct",
    "power_factor_floor_pct",
    "roof_capacity_limit_kwp",
    "solar_curve",
    "unit_generation_kw",
]

DEFAULT_USABLE_RATIO = 0.6  # 옥상 가용 비율 (요구사항서 3.3)
DEFAULT_MODULE_DENSITY_KWP_PER_M2 = 0.20
DEFAULT_STEPS = 20


def power_factor_floor_pct() -> float:
    """약관 제41조의 유지 의무이자 제43조의 요금 기준. 이 아래로 떨어지면 돈이 나간다."""
    return lagging_standard_pct()


# 약관 제41조의 유지 의무이자 제43조의 요금 기준. 이 아래로 떨어지면 돈이 나간다.


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
    start, end = day_window()
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
    investment_won: float | None
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
    cost: PvCostInput
    base_fee_months: float
    certainty: Certainty = Certainty.MEDIUM
    warnings: tuple[str, ...] = field(default=())
    notes: tuple[str, ...] = field(default=())

    def frame(self) -> pd.DataFrame:
        """표로 그릴 수 있는 형태."""
        return pd.DataFrame([point.__dict__ for point in self.points]).set_index("capacity_kwp")

    @property
    def is_priced(self) -> bool:
        return self.cost.is_priced

    @property
    def best_payback(self) -> SolarPoint | None:
        candidates = [point for point in self.points if point.payback_years is not None]
        if not candidates:
            return None
        return min(candidates, key=lambda point: point.payback_years or math.inf)

    def verdict(self) -> CapacityVerdict:
        """용량 판정 (15세션 1-3). **새로 계산하지 않는다** — 이미 훑은 점에서 고른다."""
        return capacity_verdict(self)


@dataclass(frozen=True)
class CapacityVerdict:
    """용량 한 줄 판정 (15세션 1-3).

    **표를 나열하지 않는다.** 면적이 정해지면 용량이 정해지므로, 잉여가 없고
    기본요금 절감이 포화하지 않는 한 곡선은 단조롭게 좋아지기만 한다. 그런
    경우 20단계 표는 아무것도 알려주지 않는다 — 한 줄이면 된다.

    Attributes:
        best: 고른 점. 회수기간이 있으면 그 최소점, 없으면 절감액 최대점이다.
        at_limit: 고른 점이 **면적 상한**인가. 상한이면 곡선을 감춘다.
        basis: 무엇으로 골랐는지 (``회수기간`` / ``절감액``).
        reason: 상한보다 작을 때 그 이유 (``잉여 발생`` / ``기본요금 절감 포화``).
    """

    best: SolarPoint | None
    limit: SolarPoint | None
    at_limit: bool
    basis: str
    reason: str = ""
    monotonic: bool = True
    """상한까지 단조롭게 좋아지는가. 아니면 곡선을 펼쳐 최소점을 보인다."""

    @property
    def show_curve(self) -> bool:
        """곡선을 펼칠지. **최적이 상한보다 작을 때만** 펼친다 (15세션 1-3)."""
        return self.best is not None and not self.at_limit

    def sentence(self) -> str:
        """화면·보고서가 같이 쓰는 한 줄."""
        if self.best is None or self.limit is None:
            return "용량 곡선을 산출하지 못했습니다."
        if self.at_limit:
            return (
                f"설치 가능 면적 전체({self.limit.capacity_kwp:,.0f} kWp)를 쓰는 것이 "
                f"{self.basis} 기준 가장 유리합니다."
            )
        tail = f" 그 이상은 {self.reason}." if self.reason else ""
        return f"{self.basis} 기준 최적은 {self.best.capacity_kwp:,.0f} kWp 입니다.{tail}"


def _limiting_reason(curve: SolarCurve, best: SolarPoint, limit: SolarPoint) -> str:
    """최적이 상한보다 작은 이유. **판별해서 적는다** — 둘 중 무엇인지 알 수 있다."""
    beyond = [point for point in curve.points if point.capacity_kwp > best.capacity_kwp]
    if not beyond:
        return ""
    gained_surplus = any(point.surplus_kwh > best.surplus_kwh * 1.05 + 1.0 for point in beyond) or (
        best.surplus_kwh <= 0 < limit.surplus_kwh
    )
    if gained_surplus:
        return "잉여가 발생해 절감 효율이 떨어집니다"
    base_saturated = limit.base_saving_won <= best.base_saving_won * 1.001
    if base_saturated:
        return "기본요금 절감이 포화해 추가 용량이 전력량요금만 줄입니다"
    return ""


def payback_tie_ratio() -> float:
    """회수기간이 **사실상 같다**고 볼 폭 (16세션 0-4). 기준 데이터에서 읽는다."""
    return float(assumption("pv.payback_tie_ratio"))


def capacity_verdict(curve: SolarCurve) -> CapacityVerdict:
    """이미 산출된 20단계 결과에서 최적 용량을 **고른다** (15세션 1-3).

    **산식을 새로 만들지 않는다.** 회수기간이 있으면 그 최소점을, 단가를 넣지
    않아 회수기간이 없으면 절감액(역률 악화분을 뺀 값) 최대점을 고른다.

    **회수기간이 평평하면 절감액으로 가른다** (16세션 0-4). 단가가 kWp 당이면
    투자비와 절감액이 모두 용량에 거의 비례해 회수기간이 용량과 무관해진다 —
    실측 사례에서 8 kWp 가 8.060년, 상한 160 kWp 가 8.114년이었다. 그대로 최소점을
    고르면 **상한의 1/20 을 최적이라 답한다.** 0.7% 차이는 발전량 예측 오차
    (R² 0.8) 안에 묻히므로 뜻이 없다.

        최소 회수기간의 :func:`payback_tie_ratio` 안에 드는 점들 가운데
        절감액이 가장 큰 것을 고른다.

    잉여가 생겨 회수기간이 **실제로** 꺾이면 꺾인 뒤의 점들이 이 폭을 벗어나므로
    최소점이 그대로 남는다 — U곡선 판정은 달라지지 않는다.
    """
    usable = [point for point in curve.points if point.capacity_kwp > 0]
    if not usable:
        return CapacityVerdict(best=None, limit=None, at_limit=False, basis="회수기간")
    limit = max(usable, key=lambda point: point.capacity_kwp)

    priced = [point for point in usable if point.payback_years is not None]
    if priced:
        basis = "회수기간"
        shortest = min(point.payback_years or math.inf for point in priced)
        ceiling = shortest * (1.0 + payback_tie_ratio())
        tied = [point for point in priced if (point.payback_years or math.inf) <= ceiling]
        best = max(tied, key=lambda point: point.saving_after_power_factor_won)
        ordered = [point.payback_years or math.inf for point in usable]
        monotonic = all(later <= earlier + 1e-9 for earlier, later in pairwise(ordered))
    else:
        basis = "절감액"
        best = max(usable, key=lambda point: point.saving_after_power_factor_won)
        ordered = [point.saving_after_power_factor_won for point in usable]
        monotonic = all(later >= earlier - 1e-9 for earlier, later in pairwise(ordered))

    at_limit = abs(best.capacity_kwp - limit.capacity_kwp) < 1e-9
    return CapacityVerdict(
        best=best,
        limit=limit,
        at_limit=at_limit,
        basis=basis,
        reason="" if at_limit else _limiting_reason(curve, best, limit),
        monotonic=monotonic,
    )


def solar_curve(
    usage: UsageData,
    table: TariffTable,
    selection: TariffSelection,
    unit_kw_per_kwp: pd.Series,
    *,
    max_capacity_kwp: float,
    cost: PvCostInput | None = None,
    steps: int = DEFAULT_STEPS,
    sharpness: float = 1.0,
    power_factor_pct: float | None = None,
    baseline: BillingResult | None = None,
    quality: QualityReport | None = None,
    options: BillingOptions | None = None,
    progress: ProgressReporter | None = None,
) -> SolarCurve:
    """0 부터 상한까지 용량을 키우며 절감액을 재계산한다.

    Args:
        unit_kw_per_kwp: :func:`unit_generation_kw` 의 결과.
        max_capacity_kwp: 상한. 보통 :func:`roof_capacity_limit_kwp`.
        cost: 설치 단가. **kWp당 단가 또는 총액**이다 (:class:`PvCostInput`).
            주지 않으면 투자비와 회수기간을 만들지 않고 사유를 남긴다 —
            태양광은 인용할 참고단가가 없어 기본값을 지어내지 않는다.
        sharpness: 감도 첨예도 계수 (9.2). PV 출력에만 적용한다. 일별 총량을
            보존하고 곡선의 뾰족한 정도만 바꾼다.
        progress: 진행 보고자 (10.6). **선택 인자다** — 주지 않아도 그대로 돈다.
            이 함수가 파이프라인에서 가장 오래 걸리는 구간이라(실측 43%) 여기서
            진행이 보이지 않으면 화면이 멈춘 것처럼 읽힌다.
    """
    report = record(progress)
    if steps < 1:
        raise ValueError(f"단계 수는 1 이상이어야 합니다: {steps}")
    if max_capacity_kwp < 0:
        raise ValueError(f"상한 용량은 음수일 수 없습니다: {max_capacity_kwp}")

    pricing = cost if cost is not None else PvCostInput.unpriced()
    # 기본 역률은 약관 제42조의 간주값이다 (rules_kr.json). 코드에 두지 않는다.
    power_factor_pct = lagging_standard_pct() if power_factor_pct is None else power_factor_pct
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
        report.step(step, f"용량 곡선 {step}/{steps} — {capacity:,.0f} kWp")
        generation = unit * capacity
        net = apply_generation(usage, generation)
        bill = calculate_bill(net.usage, table, selection, options=opts, quality=quality)

        investment = pricing.investment_won(capacity)
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
                payback_years=(
                    payback_years(investment, annual_saving) if investment is not None else None
                ),
                power_factor_after_pct=after_pct,
                power_factor_extra_won=extra_won,
            )
        )

    largest = points[-1]
    if largest.power_factor_after_pct < power_factor_floor_pct():
        warnings.append(
            f"PV {largest.capacity_kwp:,.0f} kWp 도입 시 예상 주간(08~22시) 지상역률이 "
            f"{largest.power_factor_after_pct:.1f}% 로 기준 "
            f"{power_factor_floor_pct():.0f}% 를 밑돕니다. 무효전력은 그대로인데 "
            "유효전력만 상쇄되기 때문입니다. 역률요금이 "
            f"{money.won(largest.power_factor_extra_won, reason='—')} 늘어 절감액이 "
            f"{money.won(largest.total_saving_won, reason='—')} → "
            f"{money.won(largest.saving_after_power_factor_won, reason='—')} 이 됩니다. "
            "역률 개선 설비 용량 조정이 필요합니다 (기본공급약관 제41·43조, 요구사항서 5.7)."
        )
    notes = [
        "발전량 예측은 피크 발전량을 과소 산출하는 경향이 있어 결과가 보수적입니다 "
        "(요구사항서 9.1).",
        f"감도 첨예도 계수 s={sharpness:.2f} 를 PV 출력에 적용했습니다. "
        "일별 총 발전량은 보존되고 피크만 달라집니다 (요구사항서 9.2).",
        "용량마다 요금을 다시 계산했습니다. 절감액을 빼기로 어림하지 않았습니다.",
        PV_COST_BASIS_NOTE,
        f"역률 판정 창은 08~22시(구간 시작 기준)이며 기준은 지상 "
        f"{lagging_standard_pct():.0f}% 입니다 (기본공급약관 제43조 ②). "
        f"도입 전 추정 역률 {power_factor_pct:.1f}% 에서 시작합니다.",
        "역률요금은 추정 역률 기반 참고 산출입니다. 무효전력 실측이 없습니다 "
        "(약관 제42조는 30분 누적 계량을 요구하는데 우리 데이터는 15분이고 "
        "무효전력이 없습니다).",
    ]
    if pricing.unit_cost_won_per_kwp is not None:
        notes.append(SCALE_ECONOMY_NOTE)
        notes.append(
            f"투자비는 용량(kWp) × {pricing.unit_cost_won_per_kwp:,.0f} 원/kWp 로 냈습니다 "
            f"(출처: {pricing.source})."
        )
    elif pricing.total_won is not None:
        notes.append(
            f"투자비를 총액 {money.won(pricing.total_won, reason='—')} 으로 고정했습니다 "
            f"(출처: {pricing.source})."
        )
        warnings.append(
            "총액을 직접 넣으면 **용량 곡선의 모든 점에 같은 총액**이 적용됩니다. "
            "견적은 특정 용량에 대한 것이므로 그 용량 근처에서만 회수기간을 읽으십시오. "
            "곡선 전체를 보려면 kWp당 단가를 넣으십시오."
        )
    else:
        notes.append(PV_REFERENCE_NOTE)
        notes.append(
            "설치 단가를 넣지 않아 투자비와 회수기간을 산출하지 않았습니다. 절감액만 유효합니다."
        )

    return SolarCurve(
        points=tuple(points),
        selection=selection,
        baseline_total_won=base_bill.total_won,
        baseline_base_won=base_bill.total_base_won,
        baseline_energy_won=base_bill.total_energy_won,
        sharpness=sharpness,
        max_capacity_kwp=max_capacity_kwp,
        cost=pricing,
        base_fee_months=base_bill.base_fee_months,
        warnings=tuple(warnings),
        notes=tuple(notes),
    )
