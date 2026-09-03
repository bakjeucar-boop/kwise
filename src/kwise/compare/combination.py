"""조합 비교 (요구사항서 8장).

**조합의 절감액은 단순 합이 아니다.** 태양광이 사용량을 줄이면 최적 선택요금이
바뀌고, ESS 가 피크를 낮추면 기본요금 기반이 달라진다. 그래서 조합마다 부하를
처음부터 다시 만들어 요금을 한 번만 계산한다.

    부하 → apply_generation(PV) → dispatch_peak_shaving(ESS) → calculate_bill()

**확실성 등급은 계산에 남되 산출물에 적지 않는다** (53세션 1-4). 조합의 등급은
가장 낮은 구성 요소를 따른다 — :meth:`CombinationResult.certainty` 가 그 규칙이다.
요금제 전환만이면 '높음', 태양광이 끼면 '중간', ESS 가 끼면 '중간~낮음'이다.

ESS 충전이 새 피크를 만드는지도 확인한다. 경부하 시간대 충전이 기저부하 위에
얹히면 야간 피크가 생긴다. 목표를 넘으면 경고하고, ``charge_limit_kw`` 로
충전 전력을 제한할 수 있다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace

import pandas as pd

from kwise import money
from kwise.io import UsageData
from kwise.measures import (
    AppliedMeasure,
    Certainty,
    DispatchResult,
    annualize,
    apply_generation,
    dispatch_peak_shaving,
    light_band_mask,
    load_ess_cost_model,
    lowest_certainty,
    payback_years,
    size_for_target,
    with_load,
)
from kwise.measures.contract import ContractAdjustment, evaluate_contract_adjustment
from kwise.measures.ess import analyze_peak_excess
from kwise.measures.pv_cost import PV_UNPRICED_REASON, PvCostInput
from kwise.measures.solar import (
    power_factor_after_pct,
    power_factor_drop_warning,
    power_factor_floor_pct,
)
from kwise.notices import Notice, basis, block, info, prefixed, warn
from kwise.progress import ProgressReporter, record
from kwise.pv import sharpen
from kwise.quality import QualityReport
from kwise.tariff import (
    BillingOptions,
    BillingResult,
    TariffSelection,
    TariffTable,
    calculate_bill,
    deemed_lagging_pct,
)

__all__ = [
    "CombinationResult",
    "CombinationSpec",
    "ComparisonResult",
    "compare_combinations",
    "default_combinations",
    "evaluate_combination",
]


@dataclass(frozen=True)
class CombinationSpec:
    """조합 하나의 정의. 켠 수단만 값을 채운다."""

    name: str
    selection: TariffSelection
    pv_capacity_kwp: float = 0.0
    pv_unit_cost_won_per_kwp: float | None = None
    pv_total_investment_won: float | None = None
    sharpness: float = 1.0
    ess_target_kw: float | None = None
    ess_power_kw: float | None = None
    ess_capacity_kwh: float | None = None
    ess_unit_cost_won_per_kw: float | None = None
    ess_total_investment_won: float | None = None
    ess_fixed_won: float | None = None
    ess_per_kwh_won: float | None = None
    ess_charge_limit_kw: float | None = None
    ess_respect_target_when_charging: bool = True
    contract_kw: float | None = None
    contract_floor_ratio: float | None = None
    power_factor_pct: float | None = None
    """도입 후 지상역률 (7.4). **조합에 넣어야 상충이 보인다** — 역률 감액은
    기본요금에 비례하므로 태양광·ESS 가 기본요금을 낮추면 감액도 함께 준다."""
    power_factor_investment_won: float | None = None
    surplus_revenue_won: float | None = None
    """**고른** 잉여 처리의 수익 (관측 기간, 원) — 48세션.

    태양광을 켠 조합에만 붙는다. 14세션은 잉여 활용이 **독립 개선안**이라 조합
    부하에 얹을 수 없다며 합산효과에서 뺐는데, 41세션에 잉여가 태양광의 결과가
    되면서 그 전제가 사라졌다 — 용량이 정해지면 남는 양도 정해진다.

    **차익거래는 계속 뺀다.** 그쪽은 「그날 피크에 쓸 몫을 남기는 운전 규칙이
    없다」 는 이유가 살아 있다 (:mod:`kwise.measures.arbitrage`).
    """
    surplus_scenario: str = ""
    """고른 시나리오 이름. 추적성 문구가 쓴다."""

    @property
    def pv_cost(self) -> PvCostInput:
        """태양광 단가. 총액이 있으면 그것이 이긴다. 없으면 **미산출**이다."""
        if self.pv_total_investment_won is not None:
            return PvCostInput.of_total(self.pv_total_investment_won)
        if self.pv_unit_cost_won_per_kwp is not None:
            return PvCostInput.of_unit_cost(self.pv_unit_cost_won_per_kwp)
        return PvCostInput.unpriced()

    @property
    def has_pv(self) -> bool:
        return self.pv_capacity_kwp > 0

    @property
    def has_ess(self) -> bool:
        return self.ess_target_kw is not None

    @property
    def has_power_factor(self) -> bool:
        return self.power_factor_pct is not None

    @property
    def applied(self) -> tuple[AppliedMeasure, ...]:
        """적용된 수단 — **등록 키와 파라미터**다. 라벨은 조회로 얻는다 (13세션).

        표시 문자열을 키 자리에 담았다가 라벨 조회가 막혀 3단계 화면이 통째로
        죽은 적이 있다. 파라미터가 붙는 수단(계약전력·ESS·태양광)이라도 키는
        등록 키 그대로다.
        """
        items: list[AppliedMeasure] = []
        if self.pv_capacity_kwp > 0:
            items.append(AppliedMeasure("solar", (("capacity_kwp", self.pv_capacity_kwp),)))
        if self.ess_target_kw is not None:
            items.append(AppliedMeasure("ess", (("target_kw", self.ess_target_kw),)))
        if self.contract_kw is not None:
            items.append(AppliedMeasure("contract", (("contract_kw", self.contract_kw),)))
        if self.power_factor_pct is not None:
            items.append(
                AppliedMeasure("power_factor", (("power_factor_pct", self.power_factor_pct),))
            )
        return tuple(items)

    @property
    def measure_keys(self) -> tuple[str, ...]:
        """등록 키만. 필터·집계에 쓴다."""
        return tuple(item.key for item in self.applied)

    @property
    def measure_labels(self) -> tuple[str, ...]:
        return tuple(item.label for item in self.applied)

    def composition(self, baseline: TariffSelection | None = None) -> str:
        """조합에 **무엇이 들어갔는지 한 줄로** (39세션 3-3).

            선택요금 전환 + 역률 개선 97% + 태양광 240 kWp

        조합 이름(:attr:`name`)은 「+ ESS 목표 5,170 kW」 처럼 **직전 조합에 무엇을
        더했는가**를 적는다 — 표에서 차례로 읽을 때는 그것이 맞지만, 그 이름
        하나만 떼어 놓으면 앞의 수단들이 보이지 않는다.

        Args:
            baseline: 기준선의 선택요금. 이것과 다르면 요금제를 바꾼 조합이므로
                맨 앞에 「선택요금 전환」 을 세운다. 주지 않으면 세지 않는다.
        """
        parts: list[str] = []
        if baseline is not None and self.selection != baseline:
            parts.append("선택요금 전환")
        parts.extend(item.short_label for item in self.applied)
        return " + ".join(parts) if parts else "현행 유지"


@dataclass(frozen=True, eq=False)
class CombinationResult:
    """조합 하나의 평가. 시계열은 들고 있지 않는다 (디스패치 요약만 남긴다)."""

    spec: CombinationSpec
    bill: BillingResult
    saving_won: float
    annual_saving_won: float
    investment_won: float | None
    payback_years: float | None
    certainty: Certainty
    billing_demand_kw: float
    generation_kwh: float
    surplus_kwh: float
    self_consumption_ratio: float | None
    dispatch: DispatchResult | None = None
    surplus_revenue_won: float = 0.0
    """절감액에 더한 잉여 처리 수익 (관측 기간, 원) — 48세션. 안 골랐으면 0 이다."""
    contract_saving_won: float | None = None
    contract_adjustment: ContractAdjustment | None = None
    """조합 부하 기준의 계약전력 조정. **추가 하향 판정이 여기서 나온다** (14세션 5-2)."""
    notices: tuple[Notice, ...] = field(default=())

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def total_won(self) -> float:
        """조합 적용 후 실질 부담. **절감액과 더해서 기준선이 나와야 한다.**

        계약전력 조정과 잉여 수익은 요금 계산 밖에서 붙는 몫이라 여기서 뺀다 —
        빼지 않으면 표의 「요금」 과 「절감액」 이 기준선으로 되돌아가지 않는다.
        """
        return self.bill.total_won - (self.contract_saving_won or 0.0) - self.surplus_revenue_won


@dataclass(frozen=True, eq=False)
class ComparisonResult:
    """조합 비교 표 (요구사항서 8장)."""

    baseline: CombinationResult
    combinations: tuple[CombinationResult, ...]
    base_fee_months: float
    period_label: str
    notices: tuple[Notice, ...] = field(default=())

    def frame(self) -> pd.DataFrame:
        """조합 | 절감액 | 투자비 | 회수기간. **확실성 열은 없다** (53세션 1-4)."""
        rows = [
            {
                "조합": item.name,
                "수단": ", ".join(item.spec.measure_labels) or "—",
                "요금(원)": item.total_won,
                "절감액(원)": item.saving_won,
                "12개월 환산 절감액(원)": item.annual_saving_won,
                "투자비(원)": item.investment_won,
                "회수기간(년)": item.payback_years,
                "요금적용전력(kW)": item.billing_demand_kw,
            }
            for item in self.combinations
        ]
        return pd.DataFrame(rows).set_index("조합")

    def with_surplus_revenue(self, revenue_won: float | None, scenario: str) -> ComparisonResult:
        """고른 잉여 처리를 **이미 계산한 비교에 얹는다** (57세션).

        잉여 수익은 **요금 계산 밖에서 붙는 몫**이라 부하도 청구서도 바꾸지
        않는다 (:meth:`CombinationResult.total_won`). 그런데 조합 명세에 들어
        있어, 라디오를 누를 때마다 조합 여섯의 요금이 통째로 다시 돌았다 —
        **값이 이미 손에 있는데도** 2.3초를 썼고, 세션 기억이 여덟 칸뿐이라
        (:data:`~kwise.ui.memo._MAX_ENTRIES`) 새 항목이 태양광 곡선과 ESS
        정밀화를 밀어내 6.5초짜리 재계산까지 불렀다.

        **덧셈만 한다.** 어떤 수익에서 출발했든 같은 답이 나오도록 이미 얹혀
        있던 몫을 먼저 뺀다.
        """
        revenue = float(revenue_won or 0.0)
        combinations = tuple(
            _with_surplus(item, revenue, scenario, self.base_fee_months)
            for item in self.combinations
        )
        if combinations == self.combinations:
            return self
        return replace(
            self,
            baseline=combinations[0],
            combinations=combinations,
            notices=aggregate_notices(combinations),
        )

    @property
    def best(self) -> CombinationResult:
        """절감액이 가장 큰 조합. 투자비는 따로 본다."""
        return max(self.combinations, key=lambda item: item.saving_won)


def _combination_certainty(spec: CombinationSpec) -> Certainty:
    grades: list[Certainty] = [Certainty.HIGH]  # 요금제·계약은 확정 계산
    if spec.has_pv:
        grades.append(Certainty.MEDIUM)
    if spec.has_ess:
        grades.append(Certainty.MEDIUM_LOW)
    return lowest_certainty(grades)


def evaluate_combination(
    usage: UsageData,
    table: TariffTable,
    spec: CombinationSpec,
    *,
    baseline_bill: BillingResult,
    unit_pv_kw_per_kwp: pd.Series | None = None,
    charge_mask: pd.Series | None = None,
    quality: QualityReport | None = None,
    options: BillingOptions | None = None,
) -> CombinationResult:
    """수단을 차례로 물린 뒤 요금을 **한 번** 계산한다.

    Args:
        unit_pv_kw_per_kwp: 1 kWp 당 발전 프로파일. PV 를 켠 조합에 필요하다.
        baseline_bill: 절감액의 기준선.
    """
    opts = options if options is not None else BillingOptions()
    if spec.power_factor_pct is not None:
        # 역률은 **요금 옵션**이다. 부하를 바꾸지 않고 기본요금 조정액만 바꾼다.
        opts = replace(opts, power_factor_pct=spec.power_factor_pct)
    interval = usage.meta.interval_minutes
    notices: list[Notice] = []

    generated_kwh = 0.0
    surplus_kwh = 0.0
    self_consumption: float | None = None
    working = usage

    if spec.has_pv:
        if unit_pv_kw_per_kwp is None:
            raise ValueError(f"'{spec.name}' 은 태양광을 켰지만 단위 발전 프로파일이 없습니다.")
        # 첨예도 조정(9.2)은 단위 프로파일에 걸고 용량을 곱한다. sharpen 이
        # 양의 상수배에 동차라 순서를 바꿔도 결과가 같다.
        generation = (
            sharpen(
                unit_pv_kw_per_kwp.reindex(pd.DatetimeIndex(usage.kw.index)).fillna(0.0),
                spec.sharpness,
            )
            * spec.pv_capacity_kwp
        )
        net = apply_generation(usage, generation)
        working = net.usage
        generated_kwh = net.generated_kwh
        surplus_kwh = net.surplus_kwh
        self_consumption = net.self_consumption_ratio

        # ===== 태양광이 떨어뜨리는 역률 (78세션 2절 · 미해결 하나를 닫는다)
        #
        # **PV 는 무효전력을 만들지도 없애지도 않는다.** 유효전력만 상쇄하므로
        # 같은 무효전력에 대해 역률이 떨어진다 (:func:`power_factor_after_pct`).
        # 5세션에 그 함수가 섰는데 14세션이 조합을 지으며 잇지 않았다 — 뺀다는
        # 결정은 기록에 없다 (76세션 조사).
        #
        # **떨어진 뒤에서 개선이 시작한다** (77세션에 사람이 정했다 — 갈래 ㄴ).
        # 역률 수단을 켰으면 목표(97%)가 PV 전 값이고 **PV 가 그것을 끌어내린다**
        # — 목표에 못 미칠 수 있고 그것이 정상이다. 도구는 설비 크기를 모르므로
        # (투자비가 사용자 입력이다) 「악화분까지 끌어올린다」 로 두면 더 큰 설비를
        # 값 없이 가정하는 셈이 된다. 역률 수단을 껐으면 끌어올릴 주체가 아예 없다.
        #
        # **조합마다 다시 잰다.** 조합마다 PV 용량이 달라 악화분도 다르므로
        # 2단계 곡선의 한 점을 가져다 쓸 수 없다.
        #
        # **ESS 는 안 본다.** 원부하와 PV 발전량만 넘겨 2단계 카드와 같은 규칙을
        # 쓴다 — ESS 도 계량 유효전력을 줄이므로 같은 이유로 역률을 떨어뜨리지만,
        # 도구가 PCS 의 무효전력 거동을 모르고 여기서 범위를 넓히면 두 자리가
        # 다른 규칙을 쓰게 된다. **봤다는 사실만 남긴다.**
        before_pct = (
            opts.power_factor_pct if opts.power_factor_pct is not None else deemed_lagging_pct()
        )
        after_pct = power_factor_after_pct(
            usage.kw, generation, power_factor_pct=before_pct, interval_minutes=interval
        )
        opts = replace(opts, power_factor_pct=after_pct)
        if after_pct < power_factor_floor_pct():
            notices.append(
                warn(
                    power_factor_drop_warning(
                        capacity_kwp=spec.pv_capacity_kwp, after_pct=after_pct
                    ),
                    fact="solar.power_factor_drop",
                )
            )

    dispatch: DispatchResult | None = None
    if spec.ess_target_kw is not None:
        excess = analyze_peak_excess(working.kw, spec.ess_target_kw, interval)
        sized_power, sized_capacity = size_for_target(excess)
        power = spec.ess_power_kw if spec.ess_power_kw is not None else sized_power
        capacity = spec.ess_capacity_kwh if spec.ess_capacity_kwh is not None else sized_capacity
        mask = (
            charge_mask
            if charge_mask is not None
            else light_band_mask(usage, table, selection=spec.selection, options=opts)
        )
        dispatch = dispatch_peak_shaving(
            working.kw,
            target_kw=spec.ess_target_kw,
            power_kw=power,
            capacity_kwh=capacity,
            charge_mask=mask,
            interval_minutes=interval,
            charge_limit_kw=spec.ess_charge_limit_kw,
            respect_target_when_charging=spec.ess_respect_target_when_charging,
        )
        working = with_load(working, dispatch.net_kw, source_suffix=" + ESS")
        notices.append(
            basis(
                f"ESS {power:,.0f} kW / {capacity:,.0f} kWh — 하루 최대 초과 에너지 "
                f"{excess.max_daily_excess_kwh:,.1f} kWh 기준으로 잡았습니다. "
                f"부록 B 의 총 초과 에너지({excess.total_excess_kwh:,.1f} kWh)는 기간 합계라 "
                "용량 산정에 쓰지 않습니다.",
                fact="combination.ess_sizing",
            )
        )
        # **조합명을 문구에 심지 않는다** (20세션 4절). 앞말이 지문이 되어 아래
        # 경고 둘이 하나로 접혔다. 조합명은 :func:`compare_combinations` 가
        # 표시할 때 붙인다.
        if dispatch.charge_created_new_peak:
            notices.append(
                warn(
                    f"경부하 충전이 목표를 넘는 새 피크를 만들었습니다. "
                    f"충전 시간대 최대 {dispatch.charge_window_peak_kw:,.1f} kW > 목표 "
                    f"{spec.ess_target_kw:,.0f} kW. 충전 전력을 목표 아래로 제한해야 합니다.",
                    fact="ess.charge_new_peak",
                )
            )
        elif dispatch.charge_window_rise_kw > 0:
            notices.append(
                basis(
                    f"경부하 충전으로 충전 시간대 최대 부하가 "
                    f"{dispatch.charge_window_rise_kw:,.1f} kW 올랐습니다 "
                    f"(목표 {spec.ess_target_kw:,.0f} kW 이내).",
                    fact="combination.charge_window_rise",
                )
            )
        if not dispatch.target_met:
            notices.append(
                warn(
                    f"목표 {spec.ess_target_kw:,.0f} kW 를 지키지 못했습니다. "
                    f"달성 {dispatch.achieved_peak_kw:,.1f} kW, "
                    f"미달 {dispatch.unmet_kwh:,.1f} kWh.",
                    fact="ess.target_unmet",
                )
            )

    bill = calculate_bill(working, table, spec.selection, options=opts, quality=quality)

    contract_saving: float | None = None
    adjustment: ContractAdjustment | None = None
    if spec.contract_kw is not None:
        adjustment = evaluate_contract_adjustment(
            working,
            bill,
            contract_kw=spec.contract_kw,
            contract_floor_ratio=spec.contract_floor_ratio,
            # **종별을 넘는 후보까지 본다** (98세션). 조합도 수단마다 요금을
            # 다시 계산하는 자리이므로 여기서 빼면 조합만 옛 값을 낸다.
            table=table,
            options=opts,
        )
        contract_saving = adjustment.saving_won
        notices.extend(adjustment.notices)

    # 투자비를 **모르면 0 이 아니라 None 이다.** 0 으로 두면 회수기간이 0년으로
    # 나와 "즉시 회수" 로 읽힌다.
    investment: float | None = 0.0
    if spec.has_pv:
        pv_investment = spec.pv_cost.investment_won(spec.pv_capacity_kwp)
        if pv_investment is None:
            investment = None
            notices.append(block(PV_UNPRICED_REASON, fact="solar.unpriced"))
        elif investment is not None:
            investment += pv_investment
    if spec.power_factor_investment_won and investment is not None:
        investment += spec.power_factor_investment_won
    if dispatch is not None:
        # ESS 투자비는 **출력 × kW당 단가**다 (7.6). 방전시간은 단가에 이미
        # 반영되어 있으므로 용량을 다시 곱하지 않는다.
        if spec.ess_total_investment_won is not None:
            ess_investment: float | None = spec.ess_total_investment_won
        elif spec.ess_unit_cost_won_per_kw is not None:
            ess_investment = dispatch.power_kw * spec.ess_unit_cost_won_per_kw
        else:
            # **입력이 없으면 조달 사례 모델이 산정한다** (13세션·14세션 3-4).
            # 2단계 카드와 같은 계수를 써야 두 화면의 투자비가 어긋나지 않는다.
            model = load_ess_cost_model()
            if spec.ess_fixed_won is not None or spec.ess_per_kwh_won is not None:
                model = model.with_coefficients(
                    fixed_won=(
                        model.fixed_won if spec.ess_fixed_won is None else spec.ess_fixed_won
                    ),
                    per_kwh_won=(
                        model.per_kwh_won if spec.ess_per_kwh_won is None else spec.ess_per_kwh_won
                    ),
                )
            ess_investment = model.quote(dispatch.capacity_kwh).total_won
            notices.append(
                basis(
                    model.formula + " — 도입 사례 회귀로 ESS 투자비를 산정했습니다.",
                    fact="ess.cost_model_formula",
                )
            )
            # 출처는 참고 등급이다 — 화면에 없고 보고서 부록에 실린다 (50세션 4절).
            notices.append(
                info(f"ESS 투자비 계수 — {model.provenance}.", fact="ess.cost_model_source")
            )
        if investment is not None and ess_investment is not None:
            investment += ess_investment

    # **고른 잉여 처리를 더한다** (48세션). 태양광을 켠 조합에만 붙고, 아무것도
    # 고르지 않았으면 0 이다. 역송분은 요금 계산에서 이미 빠져 있어 겹치지 않는다.
    surplus_revenue = (spec.surplus_revenue_won or 0.0) if spec.has_pv else 0.0
    surplus_note = surplus_notice(surplus_revenue, spec.surplus_scenario)
    if surplus_note is not None:
        notices.append(surplus_note)
    saving = baseline_bill.total_won - bill.total_won + (contract_saving or 0.0) + surplus_revenue
    annual = annualize(saving, baseline_bill.base_fee_months)
    return CombinationResult(
        spec=spec,
        bill=bill,
        saving_won=saving,
        surplus_revenue_won=surplus_revenue,
        annual_saving_won=annual,
        investment_won=investment,
        payback_years=payback_years(investment, annual) if investment is not None else None,
        certainty=_combination_certainty(spec),
        billing_demand_kw=bill.billing_demand_kw,
        generation_kwh=generated_kwh,
        surplus_kwh=surplus_kwh,
        self_consumption_ratio=self_consumption,
        dispatch=dispatch,
        contract_saving_won=contract_saving,
        contract_adjustment=adjustment,
        notices=tuple(notices),
    )


def default_combinations(
    *,
    current_selection: TariffSelection,
    best_selection: TariffSelection,
    pv_capacity_kwp: float = 0.0,
    pv_unit_cost_won_per_kwp: float | None = None,
    pv_total_investment_won: float | None = None,
    ess_target_kw: float | None = None,
    ess_unit_cost_won_per_kw: float | None = None,
    contract_kw: float | None = None,
    contract_floor_ratio: float | None = None,
    sharpness: float = 1.0,
) -> tuple[CombinationSpec, ...]:
    """기본 조합 세트. 투자비 순으로 쌓는다.

    기준선 → 요금제만 → 요금제+태양광 → 요금제+태양광+ESS
    """
    common = {
        "pv_unit_cost_won_per_kwp": pv_unit_cost_won_per_kwp,
        "pv_total_investment_won": pv_total_investment_won,
        "ess_unit_cost_won_per_kw": ess_unit_cost_won_per_kw,
        "contract_kw": contract_kw,
        "contract_floor_ratio": contract_floor_ratio,
        "sharpness": sharpness,
    }
    specs = [
        CombinationSpec(name="기준선 (현행)", selection=current_selection, **common),  # type: ignore[arg-type]
        CombinationSpec(name="선택요금 전환", selection=best_selection, **common),  # type: ignore[arg-type]
    ]
    if pv_capacity_kwp > 0:
        specs.append(
            CombinationSpec(
                name=f"+ 태양광 {pv_capacity_kwp:,.0f} kWp",
                selection=best_selection,
                pv_capacity_kwp=pv_capacity_kwp,
                **common,  # type: ignore[arg-type]
            )
        )
    if ess_target_kw is not None:
        specs.append(
            CombinationSpec(
                name=f"+ ESS 목표 {ess_target_kw:,.0f} kW",
                selection=best_selection,
                pv_capacity_kwp=pv_capacity_kwp,
                ess_target_kw=ess_target_kw,
                **common,  # type: ignore[arg-type]
            )
        )
    return tuple(specs)


SURPLUS_REVENUE_FACT = "combination.surplus_revenue"


def surplus_notice(revenue_won: float, scenario: str) -> Notice | None:
    """잉여 수익을 더했다는 **근거 한 줄**. 0 이면 없다 (57세션에 뽑았다).

    :meth:`ComparisonResult.with_surplus_revenue` 가 같은 글을 다시 지어야 하므로
    한 자리에 둔다 — 46세션의 「같은 규칙이 두 자리에 있으면」 과 같은 줄기다.
    """
    if not revenue_won:
        return None
    return basis(
        f"잉여 {scenario} 수익 {money.won(revenue_won, reason='—')} 을 절감액에 "
        "더했습니다. 역송분은 요금 계산에서 빠져 있어 자가소비 절감액과 "
        "겹치지 않습니다.",
        fact=SURPLUS_REVENUE_FACT,
    )


def _with_surplus(
    result: CombinationResult, revenue_won: float, scenario: str, base_fee_months: float
) -> CombinationResult:
    """조합 하나에 잉여 수익을 다시 얹는다 (57세션). **태양광을 켠 조합만이다.**"""
    wanted = revenue_won if result.spec.has_pv else 0.0
    if wanted == result.surplus_revenue_won:
        return result
    saving = result.saving_won - result.surplus_revenue_won + wanted
    annual = annualize(saving, base_fee_months)
    kept = tuple(item for item in result.notices if item.fact != SURPLUS_REVENUE_FACT)
    note = surplus_notice(wanted, scenario)
    return replace(
        result,
        spec=replace(
            result.spec,
            surplus_revenue_won=wanted or None,
            surplus_scenario=scenario if wanted else "",
        ),
        saving_won=saving,
        annual_saving_won=annual,
        surplus_revenue_won=wanted,
        payback_years=(
            payback_years(result.investment_won, annual)
            if result.investment_won is not None
            else None
        ),
        notices=(*kept, note) if note is not None else kept,
    )


def aggregate_notices(results: Sequence[CombinationResult]) -> tuple[Notice, ...]:
    """조합 안내를 한 자리에 모은다 — **묶는 규칙은 여기 하나다** (57세션에 뽑았다).

    **조합명은 여기서 붙인다** (20세션 4절). 조합이 여럿이라 어느 조합의 말인지
    밝혀야 하는데, 문구에 심어 두면 그 앞말이 지문이 되어 같은 조합의 다른
    경고를 잡아먹는다. 판별자 ``c{번호}`` 는 조합마다 같은 사실을 따로 남기기
    위한 것이다.

    합산 금지 규칙은 **근거**다 — 표의 숫자가 어떻게 만들어졌는지 그 자체다.
    """
    return (
        *(
            item
            for index, result in enumerate(results)
            for item in prefixed(result.notices, result.name, tag=f"c{index}")
        ),
        basis(
            "조합의 절감액은 수단별 절감액의 단순 합이 아니라, 각 조합의 부하를 "
            "재구성하여 처음부터 다시 산출한 값입니다.",
            fact="combination.not_additive",
        ),
    )


def compare_combinations(
    usage: UsageData,
    table: TariffTable,
    specs: tuple[CombinationSpec, ...],
    *,
    baseline_bill: BillingResult | None = None,
    unit_pv_kw_per_kwp: pd.Series | None = None,
    quality: QualityReport | None = None,
    options: BillingOptions | None = None,
    progress: ProgressReporter | None = None,
) -> ComparisonResult:
    """조합을 순차로 평가한다. 첫 조합이 기준선이다.

    ``progress`` 는 **선택 인자다** (10.6). 조합마다 요금을 다시 계산하므로
    조합 수가 늘면 그대로 길어진다 — 몇 번째를 돌고 있는지 보여 준다.
    """
    report = record(progress)
    if not specs:
        raise ValueError("비교할 조합이 없습니다.")
    opts = options if options is not None else BillingOptions()
    base = (
        baseline_bill
        if baseline_bill is not None
        else calculate_bill(usage, table, specs[0].selection, options=opts, quality=quality)
    )
    # 충전 시간대는 조합마다 같으므로 한 번만 만든다.
    mask = light_band_mask(usage, table, selection=specs[0].selection, options=opts)

    results: list[CombinationResult] = []
    for index, spec in enumerate(specs):  # 순차 처리. 시계열은 조합 하나 분량만 살아 있다
        report.step(index + 1, f"{index + 1}/{len(specs)} {spec.name}")
        results.append(
            evaluate_combination(
                usage,
                table,
                spec,
                baseline_bill=base,
                unit_pv_kw_per_kwp=unit_pv_kw_per_kwp,
                charge_mask=mask,
                quality=quality,
                options=opts,
            )
        )

    # **조합명은 여기서 붙인다** (20세션 4절). 조합이 여럿이라 어느 조합의 말인지
    # 밝혀야 하는데, 문구에 심어 두면 그 앞말이 지문이 되어 같은 조합의 다른 경고를
    # 잡아먹는다. 판별자 ``c{번호}`` 는 조합마다 같은 사실을 따로 남기기 위한 것이다.
    # 합산 금지 규칙은 **근거**다 — 표의 숫자가 어떻게 만들어졌는지 그 자체다.
    return ComparisonResult(
        baseline=results[0],
        combinations=tuple(results),
        base_fee_months=base.base_fee_months,
        period_label=base.period_label,
        notices=aggregate_notices(results),
    )
