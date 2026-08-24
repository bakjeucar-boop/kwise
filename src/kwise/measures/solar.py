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
from dataclasses import dataclass, field, replace
from itertools import pairwise

import pandas as pd

from kwise import money
from kwise.io import UsageData, slot_start
from kwise.measures.base import (
    LARGEST_SAVING,
    RECOMMENDED,
    Certainty,
    annualize,
    payback_years,
)
from kwise.measures.netload import apply_generation
from kwise.measures.pv_cost import (
    PV_COST_BASIS_NOTE,
    PV_REFERENCE_NOTE,
    SCALE_ECONOMY_NOTE,
    PvCostInput,
)
from kwise.notices import Notice, basis, block, info, warn
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
    "payback_tie_note",
    "payback_tie_ratio",
    "power_factor_after_pct",
    "power_factor_floor_pct",
    "roof_capacity_limit_kwp",
    "solar_curve",
    "solar_point",
    "surplus_free_capacity_kwp",
    "surplus_heavy_share",
    "surplus_share_capacity_kwp",
    "unit_generation_kw",
    "with_surplus_revenue",
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


def surplus_free_capacity_kwp(usage: UsageData, unit_generation_kw: pd.Series) -> float:
    """**잉여가 한 슬롯도 나지 않는 최대 용량** (kWp · 26세션 3-2).

    잉여는 ``발전 − 부하`` 의 양수분이고 (:func:`kwise.measures.apply_generation`),
    발전은 용량에 비례한다. 그러므로 어느 슬롯에서도 역송이 없으려면

        용량 × 단위발전(t) ≤ 부하(t)   (관측된 모든 t)

    이고, 상한은 **발전이 있는 슬롯의 ``부하 ÷ 단위발전`` 최솟값**이다. 훑지 않고
    닫힌 식으로 구한다 — 용량마다 요금을 다시 계산할 이유가 없는 값이다.

    잉여를 낼지 말지가 태양광 규모 결정의 갈림길이라 (상계거래 계약·역송 계량기가
    따라온다) 이 한 값이 판단을 가른다. 부하가 0 인 슬롯에 발전이 있으면 0 이다.
    """
    index = pd.DatetimeIndex(usage.kw.index)
    generation = unit_generation_kw.reindex(index).astype(float)
    load = usage.kw.astype(float)
    lit = generation > 0
    observed = load.notna() & lit
    if not bool(observed.any()):
        return 0.0
    headroom = (load[observed] / generation[observed]).min()
    return max(float(headroom), 0.0)


#: 잉여 비중을 이분법으로 찾을 때의 용량 상한 배수 (31세션 4-1). 잉여 비중은
#: 용량이 커질수록 1 에 가까워지므로 어떤 목표 비중이든 유한한 용량에서 만난다.
#: 「잉여 없는 최대」 의 이 배수까지 훑으면 실무 범위를 넉넉히 덮는다.
_SURPLUS_SEARCH_LIMIT = 64.0
_SURPLUS_SEARCH_ROUNDS = 40


def _surplus_share(load: pd.Series, generation: pd.Series, capacity_kwp: float) -> float:
    """용량 ``capacity_kwp`` 에서 잉여 ÷ 발전량.

    :func:`kwise.measures.apply_generation` 과 **같은 모집단**(부하가 관측된
    슬롯)을 본다 — 다른 슬롯을 세면 화면의 「연간 잉여」 비중과 어긋난다.
    """
    if capacity_kwp <= 0:
        return 0.0
    generated = float(generation.sum()) * capacity_kwp
    if generated <= 0:
        return 0.0
    surplus = float((generation * capacity_kwp - load).clip(lower=0.0).sum())
    return surplus / generated


def surplus_share_capacity_kwp(
    usage: UsageData,
    unit_generation_kw: pd.Series,
    *,
    share: float,
) -> float | None:
    """잉여가 **발전량의 ``share``** 에 이르는 용량 (kWp · 31세션 4-1).

    「잉여가 많이 생기는 용량」 을 눈대중이 아니라 한 값으로 못박는 자리다.
    :func:`surplus_free_capacity_kwp` 가 「어디서부터 남기 시작하나」 를 답하고
    이쪽이 「어디부터 많이 남나」 를 답한다.

    잉여 비중은 용량에 대해 **단조 증가**한다 — 용량을 키우면 슬롯마다 역송이
    늘거나 그대로이고 발전량은 비례로만 늘기 때문이다. 그래서 이분법으로 찾는다.
    요금을 다시 계산하지 않으므로 값싸다.

    Returns:
        해당 용량. 발전이 없거나(``None``) 찾지 못하면 ``None``.
    """
    if not 0 < share < 1:
        raise ValueError(f"잉여 비중은 0 과 1 사이여야 합니다: {share}")
    index = pd.DatetimeIndex(usage.kw.index)
    generation = unit_generation_kw.reindex(index).fillna(0.0).astype(float)
    load = usage.kw.astype(float)
    observed = load.notna()
    load = load[observed]
    generation = generation[observed]
    if float(generation.sum()) <= 0:
        return None

    low = surplus_free_capacity_kwp(usage, unit_generation_kw)
    high = max(low, 1.0) * _SURPLUS_SEARCH_LIMIT
    if _surplus_share(load, generation, high) < share:
        # 상한까지 키워도 목표 비중에 못 미친다 — **없는 값을 지어내지 않는다.**
        return None
    for _ in range(_SURPLUS_SEARCH_ROUNDS):
        middle = (low + high) / 2.0
        if _surplus_share(load, generation, middle) < share:
            low = middle
        else:
            high = middle
    return high


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
    surplus_revenue_won: float = 0.0
    """**고른** 잉여 처리의 수익 (관측 기간, 원) — 48세션.

    :func:`with_surplus_revenue` 로만 채워진다. 곡선 계산 자체는 자가소비분만
    보므로 기본값이 0 이다 — 잉여를 무엇으로 할지는 사용자가 고르는 것이지
    계산이 정하는 것이 아니다.
    """
    surplus_scenario: str = ""
    """고른 시나리오 이름. 비어 있으면 **아직 고르지 않았다.**"""

    @property
    def saving_after_power_factor_won(self) -> float:
        """역률 악화분을 뺀 절감액. **이것이 실제로 남는 돈이다.**"""
        return self.total_saving_won - self.power_factor_extra_won

    @property
    def self_consumption_saving_won(self) -> float:
        """자가소비분만의 절감액 (관측 기간). 잉여 수익을 더하기 **전** 값이다."""
        return self.total_saving_won - self.surplus_revenue_won

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
    notices: tuple[Notice, ...] = field(default=())

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


#: 최적이 상한보다 작은 이유 — ID → (짧은 라벨, 문장 조각).
#:
#: **ID 로 판정하고 문구는 여기서 찾는다** (22세션). 문구 조각을 ``in`` 으로
#: 훑어 라벨을 되찾던 자리라, 문장을 다듬으면 라벨이 조용히 빈칸이 됐다 —
#: 20세션에 안내에서 걷어낸 방식이 여기 남아 있었다.
LIMITERS: dict[str, tuple[str, str]] = {
    "surplus": ("잉여 발생", "잉여가 발생해 절감 효율이 떨어집니다"),
    "base_saturated": (
        "기본요금 절감 포화",
        "기본요금 절감이 포화해 추가 용량이 전력량요금만 줄입니다",
    ),
}


@dataclass(frozen=True)
class CapacityVerdict:
    """용량 한 줄 판정 (15세션 1-3).

    **``best`` 는 화면에서 「권장」(또는 「최대 절감액」)으로 부른다**
    (50세션 — :attr:`pick_label`). 코드 식별자는 그대로 둔다.

    **표를 나열하지 않는다.** 면적이 정해지면 용량이 정해지므로, 잉여가 없고
    기본요금 절감이 포화하지 않는 한 곡선은 단조롭게 좋아지기만 한다. 그런
    경우 20단계 표는 아무것도 알려주지 않는다 — 한 줄이면 된다.

    Attributes:
        best: 고른 점. 회수기간이 있으면 그 최소점, 없으면 절감액 최대점이다.
        at_limit: 고른 점이 **면적 상한**인가. 상한이면 곡선을 감춘다.
        basis: 무엇으로 골랐는지 (``회수기간`` / ``절감액``).
        limiter_key: 상한보다 작을 때 그 이유의 **ID** (``surplus`` /
            ``base_saturated``). 문구가 아니라 ID 다 — 22세션까지 문구 조각을
            ``in`` 으로 훑어 라벨을 되찾고 있었고, 문구를 다듬으면 조용히
            어긋나는 방식이었다.
    """

    best: SolarPoint | None
    limit: SolarPoint | None
    at_limit: bool
    basis: str
    limiter_key: str = ""
    monotonic: bool = True
    """상한까지 단조롭게 좋아지는가. 아니면 곡선을 펼쳐 최소점을 보인다."""

    @property
    def show_curve(self) -> bool:
        """곡선을 펼칠지. **고른 자리가 상한보다 작을 때만** 펼친다 (15세션 1-3)."""
        return self.best is not None and not self.at_limit

    @property
    def pick_label(self) -> str:
        """고른 자리를 부르는 이름. **「최적」 도 「최단 회수기간」 도 아니다.**

        49세션이 「최적」 을 「최단 회수기간」 으로 좁혔는데, 그 이름이 **16세션의
        동률 처리와 어긋났다** (50세션) — 11.0년짜리를 두고 11.1년짜리에
        「최단 회수기간」 이 붙었다. 계산이 아니라 이름이 틀린 것이다.

        단가를 넣었으면 동률 처리를 거치므로 :data:`~kwise.measures.base.RECOMMENDED`,
        안 넣었으면 절감액 최대를 그대로 고르므로
        :data:`~kwise.measures.base.LARGEST_SAVING` 이다 — 뒤쪽은 동률 처리가
        없어 이름이 사실과 어긋나지 않는다.
        """
        return RECOMMENDED if self.basis == "회수기간" else LARGEST_SAVING

    @property
    def tie_note(self) -> str:
        """**동률 처리를 실제로 거쳤을 때만** 판정 근거를 낸다 (51세션 1절).

        단가를 넣지 않으면 절감액 최대를 그대로 고르므로 동률 폭이 쓰이지 않는다.
        그런데 50세션은 각주를 조건 없이 달아, 표에는 「최대 절감액」 이 붙은 화면에
        **각주만 「권장」 을 말하고** 있었다 — 표식이 「선정 용량」 에 먹히던 것과
        같은 어긋남이다. 없는 규칙을 설명하지 않는다.
        """
        return payback_tie_note() if self.basis == "회수기간" else ""

    def sentence(self) -> str:
        """화면·보고서가 같이 쓰는 한 줄."""
        if self.best is None or self.limit is None:
            return "용량 곡선을 산출하지 못했습니다."
        if self.at_limit:
            return (
                f"설치 가능 면적 전체({self.limit.capacity_kwp:,.0f} kWp)를 쓰는 것이 "
                f"{self.basis} 기준 가장 유리합니다."
            )
        tail = f" 그 이상은 {LIMITERS[self.limiter_key][1]}." if self.limiter_key else ""
        return f"{self.pick_label} 용량은 {self.best.capacity_kwp:,.0f} kWp 입니다.{tail}"

    @property
    def limiter(self) -> str:
        """**무엇이 그 용량에서 멈춰 세웠는가** (17세션 3-2).

        셋 중 하나다 — 더 지을 자리가 없거나, 더 지어도 남아돌거나, 더 지어도
        기본요금이 더는 줄지 않거나.
        """
        if self.best is None:
            return ""
        if self.at_limit:
            return "설치 가능 면적 상한"
        return LIMITERS[self.limiter_key][0] if self.limiter_key else ""

    def basis_sentence(self) -> str:
        """**어떻게 골랐는지** 한 줄 (17세션 3-2).

        숫자만 내면 "왜 하필 그 용량인가" 를 알 수 없다. 고른 규칙과, 그 규칙을
        어디서 멈추게 한 것이 무엇인지를 함께 적는다.
        """
        if self.best is None:
            return ""
        if self.basis == "회수기간":
            # **「가장 짧은」 이라 적지 않는다** (50세션). 동률 처리를 거치므로
            # 고른 자리가 최소점이 아닐 수 있다 — 11.0년을 두고 11.1년을 고른다.
            # 동률 규칙은 표식이 붙는 자리 바로 아래로 옮겼다
            # (:func:`payback_tie_note`). 판정 근거는 표식 옆에서 읽혀야 하고,
            # 문구를 늘리지 않으려면 한 자리를 비워야 한다.
            head = "회수기간을 기준으로 고른 용량입니다."
        else:
            head = "설치 단가를 넣지 않아 **절감액이 가장 큰** 용량을 골랐습니다."
        limiter = self.limiter
        # **「최적을 정한 것은」 이 아니다** (49세션). 고른 자리를 최적이라 부르지
        # 않으므로 무엇이 거기서 멈춰 세웠는지로 적는다.
        return f"{head} 그 용량에서 멈춘 것은 **{limiter}** 입니다." if limiter else head


def _limiting_reason(curve: SolarCurve, best: SolarPoint, limit: SolarPoint) -> str:
    """고른 자리가 상한보다 작은 이유의 **ID**. 둘 중 무엇인지 판별해서 적는다."""
    beyond = [point for point in curve.points if point.capacity_kwp > best.capacity_kwp]
    if not beyond:
        return ""
    gained_surplus = any(point.surplus_kwh > best.surplus_kwh * 1.05 + 1.0 for point in beyond) or (
        best.surplus_kwh <= 0 < limit.surplus_kwh
    )
    if gained_surplus:
        return "surplus"
    base_saturated = limit.base_saving_won <= best.base_saving_won * 1.001
    if base_saturated:
        return "base_saturated"
    return ""


def payback_tie_ratio() -> float:
    """회수기간이 **사실상 같다**고 볼 폭 (16세션 0-4). 기준 데이터에서 읽는다."""
    return float(assumption("pv.payback_tie_ratio"))


def payback_tie_note() -> str:
    """「권장」 표식의 **판정 근거** 한 줄 (50세션).

    **출처 표기가 아니다.** 「도입 사례 4건 기준」 같은 것은 신뢰의 문제라
    매뉴얼로 보내지만, 이 문장이 없으면 11.0년 줄을 두고 11.1년 줄에 표식이
    붙은 표를 **틀린 표로 읽는다.** 결과를 읽는 데 드는 설명이므로 화면에 남긴다.

    **비율은 기준 데이터에서 읽는다** — 숫자를 문장에 박지 않는다.
    """
    return (
        f"회수기간 차이가 {payback_tie_ratio():.0%} 안이면 절감액이 큰 쪽을 "
        f"「{RECOMMENDED}」 으로 적습니다."
    )


def surplus_heavy_share() -> float:
    """잉여가 **많다**고 볼 발전량 대비 비중 (31세션 4-1). 기준 데이터에서 읽는다."""
    return float(assumption("pv.surplus_heavy_share"))


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
        limiter_key="" if at_limit else _limiting_reason(curve, best, limit),
        monotonic=monotonic,
    )


def _evaluate_point(
    usage: UsageData,
    table: TariffTable,
    selection: TariffSelection,
    unit: pd.Series,
    capacity_kwp: float,
    *,
    pricing: PvCostInput,
    base_bill: BillingResult,
    quality: QualityReport | None,
    options: BillingOptions,
    power_factor_pct: float,
) -> SolarPoint:
    """용량 하나에서 **요금을 다시 계산해** 한 점을 낸다.

    ``unit`` 은 **첨예도를 이미 먹인** 단위 프로파일이다 — 곡선이 한 번만 먹이고
    돌려쓰므로 여기서 또 건드리면 두 번 적용된다.
    """
    generation = unit * capacity_kwp
    net = apply_generation(usage, generation)
    bill = calculate_bill(net.usage, table, selection, options=options, quality=quality)

    investment = pricing.investment_won(capacity_kwp)
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
    return SolarPoint(
        capacity_kwp=capacity_kwp,
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


def solar_point(
    usage: UsageData,
    table: TariffTable,
    selection: TariffSelection,
    unit_kw_per_kwp: pd.Series,
    capacity_kwp: float,
    *,
    cost: PvCostInput | None = None,
    sharpness: float = 1.0,
    power_factor_pct: float | None = None,
    baseline: BillingResult | None = None,
    quality: QualityReport | None = None,
    options: BillingOptions | None = None,
) -> SolarPoint:
    """**곡선 밖의 용량 한 점** (31세션 4-1).

    :func:`solar_curve` 는 0 부터 설치 가능 면적이 허용하는 용량까지만 훑는다 —
    그래서 「잉여가 처음 생기는 용량」 이 그 위에 있으면 곡선 어디에도 없다.
    화면의 용량 비교가 **선정 용량보다 작은 것들만** 늘어놓던 까닭이 이것이다.

    **곡선을 늘리지 않고 점을 따로 낸다.** 곡선에 얹으면 최적 용량 판정
    (:func:`capacity_verdict`)이 설치할 수 없는 용량을 고를 수 있다 — 면적 상한을
    상한으로 둔 이유가 사라진다. 표에 한 줄 더하려고 판정을 흔들지 않는다.
    """
    pricing = cost if cost is not None else PvCostInput.unpriced()
    power_factor_pct = lagging_standard_pct() if power_factor_pct is None else power_factor_pct
    opts = options if options is not None else BillingOptions()
    base_bill = (
        baseline
        if baseline is not None
        else calculate_bill(usage, table, selection, options=opts, quality=quality)
    )
    unit = sharpen(unit_kw_per_kwp.reindex(pd.DatetimeIndex(usage.kw.index)).fillna(0.0), sharpness)
    return _evaluate_point(
        usage,
        table,
        selection,
        unit,
        capacity_kwp,
        pricing=pricing,
        base_bill=base_bill,
        quality=quality,
        options=opts,
        power_factor_pct=power_factor_pct,
    )


def with_surplus_revenue(
    point: SolarPoint,
    *,
    revenue_won: float | None,
    scenario: str,
    base_fee_months: float,
    cost: PvCostInput | None = None,
) -> SolarPoint:
    """**고른** 잉여 처리의 수익을 절감액·회수기간에 더한다 (48세션).

    41세션에 잉여 활용을 개선안에서 빼고 태양광 카드 안으로 옮겼는데, 금액을
    합치는 일을 하지 않았다. 그래서 화면에 **더해지지 않는 두 수**가 남았다 —
    소형 사무빌딩 자료에서 절감액 2,543만원과 잉여 수익 241만원이다. 회수기간
    12.6년은 앞의 것만 본 값이었다.

    **이중 계상이 아니다.** :func:`~kwise.measures.apply_generation` 이 순부하를
    0 에서 자른다 — 역송분은 요금 계산에서 아예 빠져 있고, 상계 차감은 그 뒤에
    남은 **순부하** 사용량을 한도로 계산한다. 겹치는 몫이 없다.

    Args:
        revenue_won: 고른 시나리오의 수익 (**관측 기간** 값). ``None`` 이면
            금액을 못 낸 것이므로 더하지 않고 시나리오 이름만 남긴다.
        scenario: 고른 시나리오 이름. 비면 아직 고르지 않은 것이라 그대로 둔다.
        cost: 회수기간을 다시 내는 데 쓸 단가. 없으면 원래 투자비를 쓴다.
    """
    if not scenario:
        return point
    added = float(revenue_won or 0.0)
    total = point.total_saving_won + added
    annual = annualize(total, base_fee_months)
    investment = (
        cost.investment_won(point.capacity_kwp) if cost is not None else point.investment_won
    )
    return replace(
        point,
        total_saving_won=total,
        annual_saving_won=annual,
        payback_years=(payback_years(investment, annual) if investment is not None else None),
        surplus_revenue_won=added,
        surplus_scenario=scenario,
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
    notices: list[Notice] = []
    for step in range(steps + 1):  # 0 kWp 포함
        capacity = max_capacity_kwp * step / steps
        report.step(step, f"용량 곡선 {step}/{steps} — {capacity:,.0f} kWp")
        points.append(
            _evaluate_point(
                usage,
                table,
                selection,
                unit,
                capacity,
                pricing=pricing,
                base_bill=base_bill,
                quality=quality,
                options=opts,
                power_factor_pct=power_factor_pct,
            )
        )

    largest = points[-1]
    if largest.power_factor_after_pct < power_factor_floor_pct():
        notices.append(
            warn(
                f"PV {largest.capacity_kwp:,.0f} kWp 도입 시 예상 주간(08~22시) 지상역률이 "
                f"{largest.power_factor_after_pct:.1f}% 로 기준 "
                f"{power_factor_floor_pct():.0f}% 를 밑돕니다. 무효전력은 그대로인데 "
                "유효전력만 상쇄되기 때문입니다. 역률요금이 "
                f"{money.won(largest.power_factor_extra_won, reason='—')} 늘어 절감액이 "
                f"{money.won(largest.total_saving_won, reason='—')} → "
                f"{money.won(largest.saving_after_power_factor_won, reason='—')} 이 됩니다. "
                "역률 개선 설비 용량 조정이 필요합니다 "
                "(한전 기본공급약관 제41·43조).",
                fact="solar.power_factor_drop",
            )
        )
    notices += [
        # **근거** — 절감액·역률 판정이 어느 창·어느 계수에서 나왔는가.
        basis(
            f"감도 첨예도 계수 {sharpness:.2f} 를 PV 출력에 적용했습니다. "
            "일별 총 발전량은 보존되고 피크만 달라집니다.",
            fact="solar.sharpness",
        ),
        basis(
            "용량마다 요금을 다시 계산했습니다. 절감액을 빼기로 어림하지 않았습니다.",
            fact="solar.recalculated",
        ),
        basis(
            f"역률 판정 창은 08~22시(구간 시작 기준)이며 기준은 지상 "
            f"{lagging_standard_pct():.0f}% 입니다 (한전 기본공급약관 제43조 ②). "
            f"도입 전 추정 역률 {power_factor_pct:.1f}% 에서 시작합니다.",
            fact="solar.power_factor_window",
        ),
        # **참고** — 모델의 한계. 전제이지 산식이 아니다.
        info(
            "발전량 예측은 피크 발전량을 과소 산출하는 경향이 있어 결과가 보수적입니다.",
            fact="solar.conservative_model",
        ),
        info(PV_COST_BASIS_NOTE, fact="solar.cost_basis"),
        info(
            "역률요금은 추정 역률 기반 참고 산출입니다. 무효전력 실측이 없습니다 "
            "(한전 기본공급약관 제42조는 30분 누적 계량을 요구하는데 우리 데이터는 15분이고 "
            "무효전력이 없습니다).",
            fact="solar.power_factor_estimated",
        ),
    ]
    if pricing.unit_cost_won_per_kwp is not None:
        notices.append(info(SCALE_ECONOMY_NOTE, fact="solar.scale_economy"))
        notices.append(
            basis(
                f"투자비는 용량(kWp) × {pricing.unit_cost_won_per_kwp:,.0f} 원/kWp 로 냈습니다 "
                f"(출처: {pricing.source}).",
                fact="solar.investment_unit_cost",
            )
        )
    elif pricing.total_won is not None:
        notices.append(
            basis(
                f"투자비를 총액 {money.won(pricing.total_won, reason='—')} 으로 고정했습니다 "
                f"(출처: {pricing.source}).",
                fact="solar.investment_total",
            )
        )
        notices.append(
            warn(
                "총액을 직접 넣으면 **용량 곡선의 모든 점에 같은 총액**이 적용됩니다. "
                "견적은 특정 용량에 대한 것이므로 그 용량 근처에서만 회수기간을 읽으십시오. "
                "곡선 전체를 보려면 kWp당 단가를 넣으십시오.",
                fact="solar.total_cost_curve_caveat",
            )
        )
    else:
        notices.append(info(PV_REFERENCE_NOTE, fact="solar.cost_reference_missing"))
        # **차단** — 투자비와 회수기간이 나오지 않는다.
        notices.append(
            block(
                "설치 단가를 넣지 않아 투자비와 회수기간을 산출하지 않았습니다. "
                "절감액만 유효합니다.",
                fact="solar.unpriced",
            )
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
        notices=tuple(notices),
    )
