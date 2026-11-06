"""3단계 · 개선안 조합 — 결과를 화면 조각(``blocks``)으로 적는다.

NiceGUI 판(``kwise.desktop.compare``)과 같은 공용 함수 · 같은 인자 · 같은 셈이다. 조합마다
요금을 다시 계산한다 — 수단별 절감액을 더하지 않는다(규약). Excel · PPT 를 지을 재료도 여기서 낸다.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, replace

import pandas as pd

from kwise import money
from kwise.compare import CombinationResult, CombinationSpec, ComparisonResult, compare_combinations
from kwise.compare.sensitivity import sensitivity_comparison
from kwise.diagnose.dr import DrProfile
from kwise.io import UsageData
from kwise.measures import (
    Certainty,
    ContractAdjustment,
    DemandResponseResult,
    EssCostInput,
    EssOptimum,
    EssResult,
    EssTargetCurve,
    PowerFactorResult,
    SolarCurve,
    SolarPoint,
    SurplusResult,
    TariffSwitchResult,
    apply_generation,
    default_target_pct,
    evaluate_contract_adjustment,
    evaluate_demand_response,
    evaluate_ess,
    evaluate_power_factor,
    evaluate_tariff_switch,
    payback_years,
    surplus_free_capacity_kwp,
    with_load,
    with_surplus_revenue,
)
from kwise.notices import Notice, dedupe_key, dedupe_keys, prefixed, tooltip
from kwise.pv import load_pv_presets
from kwise.report import (
    SIMPLE_SUM_NOTE,
    MeasureEntry,
    StandaloneRow,
    SurplusPage,
    measure_entries,
    measure_summary_frame,
    no_pv_sensitivity_frame,
    simple_sum_won,
    standalone_frame,
    standalone_rows,
    surplus_page,
)
from kwise.report.days import RepresentativeDay
from kwise.report.notices import (
    Peers,
    combination_saving,
    covers_summer_and_winter,
    settled_saving_shown,
    standalone_savings,
)
from kwise.report.worksheet import (
    Worksheet,
    combination_worksheet,
    contract_worksheet,
    demand_response_worksheet,
    ess_worksheet,
    power_factor_worksheet,
    solar_worksheet,
    tariff_switch_worksheet,
)
from kwise.tariff import BillingResult
from kwise.ui import text as fmt
from kwise.ui.labels import measure_title, option_label
from kwise.ui.notices import screen_notices, tooltip_text
from kwise.ui.pipeline import SolarInputs, combination_specs, solar_result, unit_pv_profile
from kwise.ui.spec import MEASURES, ReviewScope, measure, review_scope

from . import blocks as b
from .stage2 import STALE_INPUT, Context, chosen_revenue, day_of, ess_model, ess_optimum, surplus
from .state import Choices

SELECTION_STALE = "선택이 변경되었습니다 — 다시 계산하십시오."


@dataclass(frozen=True)
class Results:
    """켠 수단의 계산 결과 한 벌 — Excel 시트 · PPT 장이 같은 것을 본다.

    NiceGUI 판 ``compare._Results`` (곧 Streamlit ``_MeasureResults``) 의 사본.
    """

    switch: TariffSwitchResult | None = None
    contract: ContractAdjustment | None = None
    demand_response: DemandResponseResult | None = None
    power_factor: PowerFactorResult | None = None
    solar: SolarPoint | None = None
    solar_curve: SolarCurve | None = None
    solar_certainty: Certainty | None = None
    solar_unpriced_reason: str = ""
    ess: EssResult | None = None
    ess_curve: EssTargetCurve | None = None
    ess_optimum: EssOptimum | None = None
    surplus_free_kwp: float | None = None
    solar_area_m2: float | None = None
    surplus: SurplusResult | None = None
    base_fee_months: float = 0.0
    bill: BillingResult | None = None
    usage: UsageData | None = None
    day: RepresentativeDay | None = None
    dr_profile: DrProfile | None = None
    solar_generation_kw: pd.Series | None = None
    surplus_kw: pd.Series | None = None
    unit_profile: pd.Series | None = None
    capacity_kwp: float = 0.0
    ess_target: float | None = None
    solar_error: str = ""
    solar_inputs: SolarInputs | None = None

    def excel_frame(self) -> pd.DataFrame:
        return measure_summary_frame(
            switch=self.switch,
            contract=self.contract,
            demand_response=self.demand_response,
            power_factor=self.power_factor,
            ess=self.ess,
            ess_optimum=self.ess_optimum,
            ess_curve=self.ess_curve,
            solar=self.solar,
            surplus=self.surplus,
            base_fee_months=self.base_fee_months,
        )

    def peer_savings(self) -> Peers:
        return standalone_savings(
            switch=self.switch,
            contract=self.contract,
            power_factor=self.power_factor,
            solar=self.solar,
            ess=self.ess,
        )

    def shown_facts(self) -> frozenset[str]:
        sources = (
            self.switch,
            self.contract,
            self.demand_response,
            self.power_factor,
            self.solar_curve,
            self.ess,
            self.surplus,
        )
        return dedupe_keys(
            *(getattr(source, "notices", ()) for source in sources if source is not None),
            base=True,
        )

    def standalone(self) -> tuple[StandaloneRow, ...]:
        return standalone_rows(
            switch=self.switch,
            contract=self.contract,
            demand_response=self.demand_response,
            power_factor=self.power_factor,
            solar=self.solar,
            solar_certainty=self.solar_certainty,
            solar_investment_reason=self.solar_unpriced_reason,
            ess=self.ess,
            ess_optimum=self.ess_optimum,
            ess_curve=self.ess_curve,
            surplus=self.surplus,
            base_fee_months=self.base_fee_months or None,
        )

    def worksheets(self) -> tuple[Worksheet, ...]:
        sheets: list[Worksheet] = []
        if self.switch is not None:
            sheets.append(tariff_switch_worksheet(self.switch))
        if self.contract is not None:
            sheets.append(contract_worksheet(self.contract, self.bill))
        if self.demand_response is not None:
            sheets.append(demand_response_worksheet(self.demand_response))
        if self.power_factor is not None:
            sheets.append(power_factor_worksheet(self.power_factor))
        if self.solar_curve is not None:
            sheets.append(
                solar_worksheet(
                    self.solar_curve,
                    self.solar,
                    power_factor_pct=(
                        self.bill.power_factor.lagging_pct if self.bill is not None else None
                    ),
                )
            )
        if self.ess is not None:
            sheets.append(ess_worksheet(self.ess))
        return tuple(sheet for sheet in sheets if sheet)

    def notice_groups(self) -> tuple[tuple[Notice, ...], ...]:
        return tuple(entry.notices for entry in self.entries())

    def surplus_page(self) -> SurplusPage | None:
        return surplus_page(
            self.surplus,
            capacity_kwp=self.solar.capacity_kwp if self.solar is not None else 0.0,
            surplus_free_kwp=self.surplus_free_kwp,
            chosen_scenario=self.solar.surplus_scenario if self.solar is not None else "",
            usage=self.usage,
            surplus_kw=self.surplus_kw,
        )

    def entries(self) -> tuple[MeasureEntry, ...]:
        return measure_entries(
            switch=self.switch,
            contract=self.contract,
            demand_response=self.demand_response,
            power_factor=self.power_factor,
            solar=self.solar,
            solar_certainty=self.solar_certainty,
            solar_unpriced_reason=self.solar_unpriced_reason,
            solar_notices=self.solar_curve.notices if self.solar_curve is not None else (),
            ess=self.ess,
            ess_curve=self.ess_curve,
            ess_optimum=self.ess_optimum,
            surplus=self.surplus,
            usage=self.usage,
            day=self.day,
            dr_profile=self.dr_profile,
            solar_generation_kw=self.solar_generation_kw,
            surplus_kw=self.surplus_kw,
            surplus_free_kwp=self.surplus_free_kwp,
            area_m2=self.solar_area_m2,
            base_fee_months=self.base_fee_months or None,
            covers_summer_winter=self.bill is None or covers_summer_and_winter(self.bill),
        )


def enabled(choices: Choices) -> tuple[str, ...]:
    """켠 수단 — 7장 번호 순 (조합 이름이 실행마다 달라지지 않게)."""
    return tuple(spec.key for spec in MEASURES if spec.key in choices.enabled)


def stale_lines(ctx: Context, choices: Choices) -> tuple[str, ...]:
    """막힘 줄 — 태양광을 계산한 뒤 입력을 바꿨으면 조합도 산출물도 못 만든다."""
    if "solar" in choices.enabled and choices.solar.stale and ctx.building.region_key:
        return (f"{measure_title(measure('solar').title)} — {STALE_INPUT}",)
    return ()


def measure_results(ctx: Context, choices: Choices) -> Results:
    """켠 수단만 계산한다 — 2단계 카드와 같은 공용 함수 · 같은 인자."""
    usage, table, form, diagnosis, quality = (
        ctx.usage,
        ctx.table,
        ctx.form,
        ctx.diagnosis,
        ctx.quality,
    )
    structure = diagnosis.structure
    assert structure is not None
    baseline = structure.bill
    keys = enabled(choices)

    switch = None
    if "tariff_switch" in keys:
        switch = evaluate_tariff_switch(
            usage,
            table,
            form.selection,
            quality=quality,
            options=form.billing_options(),
            option_totals=dict(diagnosis.option_totals),
        )
    contract = None
    if "contract" in keys and form.contract_kw is not None:
        contract = evaluate_contract_adjustment(
            usage,
            baseline,
            contract_kw=form.contract_kw,
            contract_floor_ratio=None,
            table=table,
            options=form.billing_options(),
        )
    demand_response = None
    if "demand_response" in keys and diagnosis.dr is not None:
        demand_response = evaluate_demand_response(
            diagnosis.dr, unit_price_won_per_kwh=choices.dr_unit_price, jeju=ctx.building.jeju
        )
    power_factor = None
    if "power_factor" in keys:
        power_factor = evaluate_power_factor(
            usage,
            table,
            form.selection,
            current_pct=form.lagging_pct,
            target_pct=choices.pf_target or default_target_pct(),
            investment_won=choices.pf_investment,
            baseline=baseline,
            quality=quality,
            options=form.billing_options(),
            billed=form.power_factor_pct is not None,
        )

    solar = solar_curve = solar_certainty = None
    solar_reason = solar_error = ""
    unit_profile = None
    capacity = 0.0
    surplus_result = None
    inputs = None
    if "solar" in keys and choices.solar.ran_form is not None and ctx.building.region_key:
        inputs = choices.solar.inputs(ctx.building.region_key, load_pv_presets(), ran=True)
        capacity = inputs.resolved_capacity_kwp()
        try:
            unit_profile, _source = unit_pv_profile(usage, inputs)
        except Exception as exc:
            solar_error = f"기상 자료를 얻지 못해 태양광을 조합에서 뺐습니다. {exc}"
            capacity = 0.0
    if unit_profile is not None and inputs is not None:
        curve = solar_result(
            usage, table, form, inputs, unit_profile, baseline=baseline, quality=quality
        )
        point = curve.points[-1]
        point_surplus = surplus(usage, table, form, unit_profile, point.capacity_kwp, choices.solar)
        scenario, revenue = chosen_revenue(choices.solar, point_surplus)
        solar = with_surplus_revenue(
            point, revenue_won=revenue, scenario=scenario, base_fee_months=curve.base_fee_months
        )
        solar_curve, solar_certainty, solar_reason = curve, curve.certainty, curve.cost.reason
        if capacity > 0:
            surplus_result = surplus(usage, table, form, unit_profile, capacity, choices.solar)

    ess = ess_curve = optimum = None
    ess_target = None
    if "ess" in keys:
        ess_curve, optimum = ess_optimum(ctx, choices)
        if optimum is not None and optimum.viable:
            ess_target = choices.ess_target or optimum.target_kw
        if ess_target:
            quote = choices.ess_quote
            ess = evaluate_ess(
                usage,
                table,
                form.selection,
                target_kw=ess_target,
                cost=EssCostInput.of_total(quote) if quote is not None else EssCostInput.unpriced(),
                model=ess_model(choices),
                baseline=baseline,
                quality=quality,
                options=form.billing_options(),
            )

    generation = unit_profile * solar.capacity_kwp if solar and unit_profile is not None else None
    surplus_kw = (
        apply_generation(usage, unit_profile * capacity).surplus_kw
        if surplus_result is not None and unit_profile is not None
        else None
    )
    free_kwp = area = None
    if unit_profile is not None and inputs is not None:
        free_kwp = surplus_free_capacity_kwp(usage, unit_profile)
        area = inputs.area_m2 or None
    return Results(
        switch=switch,
        contract=contract,
        demand_response=demand_response,
        power_factor=power_factor,
        solar=solar,
        solar_curve=solar_curve,
        solar_certainty=solar_certainty,
        solar_unpriced_reason=solar_reason,
        ess=ess,
        ess_curve=ess_curve,
        ess_optimum=optimum,
        surplus=surplus_result,
        surplus_free_kwp=free_kwp,
        solar_area_m2=area,
        base_fee_months=baseline.base_fee_months,
        bill=baseline,
        usage=usage,
        day=day_of(ctx, choices),
        dr_profile=diagnosis.dr,
        solar_generation_kw=generation,
        surplus_kw=surplus_kw,
        unit_profile=unit_profile,
        capacity_kwp=capacity if unit_profile is not None else 0.0,
        ess_target=ess_target,
        solar_error=solar_error,
        solar_inputs=inputs,
    )


def specs(
    ctx: Context, choices: Choices, results: Results, keys: tuple[str, ...]
) -> tuple[CombinationSpec, ...]:
    """조합 명세 — 공용 ``combination_specs`` 에 2단계 값을 그대로 넘긴다."""
    form = ctx.form
    inputs = results.solar_inputs
    scenario, revenue = ("", None)
    if results.unit_profile is not None and "solar" in keys and results.capacity_kwp > 0:
        scenario, revenue = chosen_revenue(
            choices.solar,
            surplus(
                ctx.usage,
                ctx.table,
                form,
                results.unit_profile,
                results.capacity_kwp,
                choices.solar,
            ),
        )
    return combination_specs(
        form=form,
        best_selection=(ctx.diagnosis.summary.best_selection or form.selection),
        enabled=keys,
        pv_capacity_kwp=results.capacity_kwp if "solar" in keys else 0.0,
        pv_unit_cost_won_per_kwp=inputs.unit_cost_won_per_kwp if inputs else None,
        pv_total_investment_won=inputs.total_investment_won if inputs else None,
        ess_target_kw=results.ess_target if "ess" in keys else None,
        ess_total_investment_won=choices.ess_quote,
        ess_fixed_won=choices.ess_fixed,
        ess_per_kwh_won=choices.ess_per_kwh,
        power_factor_pct=(
            (choices.pf_target or default_target_pct()) if "power_factor" in keys else None
        ),
        power_factor_investment_won=choices.pf_investment,
        surplus_revenue_won=revenue,
        surplus_scenario=scenario,
    )


def compare(ctx: Context, results: Results, combo: tuple[CombinationSpec, ...]) -> ComparisonResult:
    """조합 비교 — Streamlit ``cached_comparison`` 과 같은 부름(잉여 수익은 뒤에 얹는다)."""
    revenue = next(
        (spec.surplus_revenue_won for spec in combo if spec.has_pv and spec.surplus_revenue_won),
        None,
    )
    scenario = next((spec.surplus_scenario for spec in combo if spec.surplus_scenario), "")
    stripped = tuple(replace(spec, surplus_revenue_won=None, surplus_scenario="") for spec in combo)
    structure = ctx.diagnosis.structure
    assert structure is not None
    base = compare_combinations(
        ctx.usage,
        ctx.table,
        stripped,
        baseline_bill=structure.bill,
        unit_pv_kw_per_kwp=results.unit_profile,
        quality=ctx.quality,
        options=ctx.form.billing_options(),
    )
    return base.with_surplus_revenue(revenue, scenario)


def sensitivity(ctx: Context, results: Results, combo: tuple[CombinationSpec, ...]) -> pd.DataFrame:
    """감도 — 화면에는 없고 Excel · PPT 에만 간다 (Streamlit 과 같다)."""
    pv_specs = [spec for spec in combo if spec.has_pv]
    structure = ctx.diagnosis.structure
    if not pv_specs or results.unit_profile is None or structure is None:
        return no_pv_sensitivity_frame()
    return sensitivity_comparison(
        ctx.usage,
        ctx.table,
        pv_specs[-1],
        baseline_bill=structure.bill,
        unit_pv_kw_per_kwp=results.unit_profile,
        quality=ctx.quality,
        options=ctx.form.billing_options(),
    )


# --------------------------------------------------------------------- 화면


def scope_blocks(choices: Choices) -> tuple[ReviewScope, list[b.Block]]:
    scope = review_scope(enabled(choices))
    with b.collect() as page, b.card():
        b.text("**검토 범위**")
        b.text("검토함 — " + (", ".join(map(measure_title, scope.reviewed_labels)) or "없음"))
        b.text("미검토 — " + (", ".join(map(measure_title, scope.skipped_labels)) or "없음"))
        b.caption("미검토는 '효과가 없다' 가 아니라 '보지 않았다' 입니다.")
    return scope, page


def standalone_blocks(rows: tuple[StandaloneRow, ...], solar_error: str) -> list[b.Block]:
    with b.collect() as page:
        if solar_error:
            b.blocked(solar_error)
        b.h2("개선안별 요약")
        if not rows:
            b.note("2단계에서 수단을 하나도 켜지 않았습니다.")
        else:
            frame = standalone_frame(rows)
            if not frame.empty:
                frame = frame.assign(
                    **{"수단": [measure_title(str(value)) for value in frame["수단"]]}
                )
            b.table(frame)
            b.text(
                "각 줄은 **그 수단만 도입했을 때**의 값입니다 (현재 요금제·현재 사용량 기준). "
                + SIMPLE_SUM_NOTE
                + " "
                + fmt.ROUNDING_FOOTNOTE
            )
    return page


def combined_blocks(
    ctx: Context,
    choices: Choices,
    results: Results,
    rows: tuple[StandaloneRow, ...],
    keys: tuple[str, ...],
    comparison: ComparisonResult,
    *,
    selection_stale: bool,
) -> list[b.Block]:
    """합산효과 — 단순 합과의 차이를 반드시 보인다. 묵었으면 흐리게."""
    dr_won = None
    if "demand_response" in keys:
        if choices.dr_unit_price is not None and ctx.diagnosis.dr is not None:
            dr_won = evaluate_demand_response(
                ctx.diagnosis.dr, unit_price_won_per_kwh=choices.dr_unit_price
            ).settlement_won
        comparison = comparison.with_demand_response(ctx.diagnosis.dr, choices.dr_unit_price)
    stale = selection_stale or bool(stale_lines(ctx, choices))
    with b.collect() as page:
        if selection_stale:
            b.caution(SELECTION_STALE)
        with b.dim(stale):
            _combined(ctx, comparison, rows, results.contract, keys, results.peer_savings(), dr_won)
        seen = results.shown_facts()
        fresh = tuple(
            item for item in comparison.notices if dedupe_key(item, base=True) not in seen
        )
        heads = _measure_notice_heads(comparison)
        for notice in screen_notices(tuple(heads.get(item, item) for item in fresh)):
            b.render_notice(notice)
        grounds = tooltip(fresh)
        if grounds:
            b.caption(f"산출 근거 {len(grounds)}건", tooltip_text(fresh))
    return page


def _measure_notice_heads(comparison: ComparisonResult) -> dict[Notice, Notice]:
    origin = {
        shown: item
        for index, result in enumerate(comparison.combinations)
        for item, shown in zip(
            result.notices, prefixed(result.notices, result.row_name, tag=f"c{index}"), strict=True
        )
    }
    wording = Counter(fact for fact, _text in {(o.fact_base, o.text) for o in origin.values()})
    return {
        shown: item
        for shown, item in origin.items()
        if not item.fact_base.startswith("combination.") and wording[item.fact_base] == 1
    }


def _combined(
    ctx: Context,
    comparison: ComparisonResult,
    rows: tuple[StandaloneRow, ...],
    contract: ContractAdjustment | None,
    chosen: tuple[str, ...],
    peers: Peers,
    dr_won: float | None,
) -> None:
    combined = comparison.combinations[-1]
    picked = tuple(row for row in rows if row.key in set(chosen))
    simple = simple_sum_won(picked)
    actual = combined.annual_saving_won + (dr_won or 0.0)
    ratio = (actual - simple) / simple if simple else None
    shown = money.same_won(
        actual, combined.saving_won, combination_saving(comparison, combined, peers)
    )
    combined_shown = actual if shown is None else shown
    if dr_won is not None and combined.dr_annual_won is not None:
        combined_shown = settled_saving_shown(comparison, combined, peers, annual=True)
    simple_same = money.same_won(simple, actual, combined_shown)
    simple_shown = simple if simple_same is None else simple_same

    b.h2("합산효과")
    payback = (
        payback_years(combined.investment_won, actual)
        if combined.investment_won is not None
        else None
    )
    b.metrics([("단순 합", fmt.won_year(simple)), ("합산효과", fmt.won_year(actual))])
    b.metrics(
        [
            (
                "차이",
                fmt.won_year(money.gap_won(combined_shown, simple_shown)),
                fmt.ratio_pct(ratio) if ratio is not None else fmt.DASH,
            ),
            (
                "회수기간",
                fmt.payback(payback, investment_won=combined.investment_won),
                f"투자비 {fmt.won_short(combined.investment_won, reason='미산출')}",
            ),
        ]
    )
    b.caption("이 조합을 모두 도입했을 때의 12개월 환산 절감액입니다.")
    excluded = [row for row in rows if not row.combinable]
    if excluded:
        settled = bool(dr_won)
        b.caption(
            ("조합 부하에 넣지 않은 수단 — " if settled else "합산효과에 넣지 않은 수단 — ")
            + ", ".join(measure_title(row.title) for row in excluded)
            + ". 요금이 아니라 별도 정산·수익이라 "
            + ("합산효과에 DR 정산금으로 더합니다." if settled else "조합 부하에 얹을 수 없습니다.")
        )
    dropped = [row for row in rows if row.combinable and row.key not in set(chosen)]
    if dropped:
        b.caption(
            "조합에서 뺀 개선안 — " + ", ".join(measure_title(row.title) for row in dropped) + "."
        )
    if comparison.ess_below_minimum_note:
        b.caption(comparison.ess_below_minimum_note)
    reasons = (
        _interaction_reasons(comparison, combined, picked)
        if money.gap_won(combined_shown, simple_shown)
        else []
    )
    extra_won = _contract_headroom(ctx, combined, contract)
    b.worksheet(
        combination_worksheet(
            simple_won=simple_shown,
            combined_won=combined_shown,
            reasons=tuple(reasons),
            contract_extra_won=extra_won,
        )
    )


def _interaction_reasons(
    comparison: ComparisonResult, combined: CombinationResult, rows: tuple[StandaloneRow, ...]
) -> list[str]:
    reasons: list[str] = []
    keys = set(combined.spec.measure_keys) | {
        row.key for row in rows if row.combinable and row.annual_saving_won is not None
    }
    baseline = comparison.baseline
    if combined.selection != baseline.selection:
        reasons.append(
            "**요금제가 바뀌면 다른 수단의 기준 단가가 바뀝니다.** 조합은 "
            f"{option_label(combined.selection.option)} 로 계산했으므로, 현행 "
            "요금제를 기준으로 낸 2단계 값과 계약전력·역률·ESS 절감액이 다릅니다."
        )
    moved = not math.isclose(combined.bill.mean_base_demand_kw, baseline.bill.mean_base_demand_kw)
    if ("ess" in keys or "solar" in keys) and moved:
        reasons.append(
            "**기본요금 기반이 달라집니다.** 요금적용전력이 "
            f"{fmt.kw(baseline.billing_demand_kw)} 에서 "
            f"{fmt.kw(combined.billing_demand_kw)} 로 내려갔고, 그 위에서 남은 수단의 "
            "절감액이 다시 매겨집니다."
        )
    if (
        "power_factor" in keys
        and not combined.power_factor_no_headroom
        and ("solar" in keys or "ess" in keys)
        and moved
    ):
        reasons.append(
            "**역률 감액은 기본요금에 비례합니다.** 조합은 바뀐 기본요금 위에서 "
            "역률요금을 다시 매깁니다."
        )
    return reasons


def _contract_headroom(
    ctx: Context, combined: CombinationResult, standalone: ContractAdjustment | None
) -> float | None:
    form = ctx.form
    if form.contract_kw is None:
        return None
    usage = ctx.usage
    load = usage if combined.load_kw is None else with_load(usage, combined.load_kw)
    options = replace(
        form.billing_options(), power_factor_pct=combined.bill.power_factor.lagging_pct
    )
    adjustment = evaluate_contract_adjustment(
        load,
        combined.bill,
        contract_kw=form.contract_kw,
        table=ctx.table,
        options=options,
        observed_max_kw=usage.observed_max_kw,
    )
    already = (standalone.annual_saving_won or 0.0) if standalone is not None else 0.0
    extra = (adjustment.annual_saving_won or 0.0) - already
    if adjustment.target_contract_kw is None:
        b.text("계약전력 추가 하향 — 이 조합에서도 하한에 걸리지 않아 낮출 이유가 없습니다.")
        return None
    text = (
        f"**계약전력 추가 하향** — 이 조합이면 "
        f"**{fmt.kw(adjustment.target_contract_kw, decimals=0)}** 까지 "
        f"낮출 수 있습니다 (현행 {fmt.kw(form.contract_kw, decimals=0)})."
    )
    if standalone is not None and standalone.target_contract_kw is not None:
        text += (
            f" 현재 부하만 볼 때의 목표는 "
            f"{fmt.kw(standalone.target_contract_kw, decimals=0)} 였습니다."
        )
    if adjustment.annual_saving_won is None:
        text += f" 금액은 {adjustment.saving_basis}."
    elif extra > 0:
        text += f" 조합 시 추가 절감 **{fmt.won_short(extra)}/년**."
    b.text(text)
    return extra if extra > 0 else None
