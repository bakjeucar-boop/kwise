"""3단계 · 개선안 조합 — 데스크톱 화면 (Streamlit ``ui\\views\\compare.py`` 의 자리).

    ① 검토 범위 · 개선안별 요약   2단계 카드 값을 같은 공용 함수로 다시 모은다
    ② 조합 구성 · 합산효과        체크한 조합을 **부하부터 다시 만들어** 한 번 계산한다
    ③ 내려받기                   Excel · PPT — 데스크톱이라 「다운로드」 폴더에 바로 저장한다

계산은 공용 함수(``kwise.measures`` · ``kwise.compare`` · ``kwise.ui.pipeline``)와 산출물
조립(``kwise.report``)이 한다. 여기 있는 것은 입력 받기와 결과 놓기뿐이다. 수단별 절감액을
더하지 않는다 — 조합마다 요금을 다시 계산한다 (규약).
"""

from __future__ import annotations

import math
import os
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
from nicegui import run, ui

from kwise import money
from kwise.compare import CombinationResult, CombinationSpec, ComparisonResult, compare_combinations
from kwise.compare.sensitivity import sensitivity_comparison
from kwise.diagnose.dr import DrProfile
from kwise.io import UsageData, interval_label
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
    ess_target_curve,
    evaluate_contract_adjustment,
    evaluate_demand_response,
    evaluate_ess,
    evaluate_power_factor,
    evaluate_tariff_switch,
    load_ess_cost_model,
    payback_years,
    refine_ess_target,
    surplus_free_capacity_kwp,
    with_load,
    with_surplus_revenue,
)
from kwise.notices import Notice, dedupe_key, dedupe_keys, prefixed, tooltip
from kwise.report import (
    SIMPLE_SUM_NOTE,
    DocumentSections,
    MeasureEntry,
    ReportSections,
    StandaloneRow,
    SurplusPage,
    measure_entries,
    measure_summary_frame,
    no_pv_sensitivity_frame,
    simple_sum_won,
    slides_bytes,
    standalone_frame,
    standalone_rows,
    surplus_page,
)
from kwise.report.days import RepresentativeDay, find_day, representative_days
from kwise.report.excel import sensitivity_items
from kwise.report.notices import (
    SCHOOL_HIGH_VOLTAGE_NOTICE,
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
from kwise.ui.pipeline import combination_specs, daily_temperature, solar_result, unit_pv_profile
from kwise.ui.session import build_report_bytes
from kwise.ui.spec import MEASURES, ReviewScope, measure, review_scope

from . import widgets as w
from .solar import STALE_INPUT, _chosen_revenue, _surplus

if TYPE_CHECKING:
    from .measures import Choices, Context

#: 산출물을 두는 자리 — 데스크톱은 사용자 PC 라 「다운로드」 폴더에 바로 저장한다.
DOWNLOADS = Path.home() / "Downloads"

#: 이 화면을 연 동안의 이름 — Excel 을 잠깐 쓰는 임시 폴더 이름에 쓴다 (공용 ``session_dir``).
_SESSION = uuid.uuid4().hex


# ===================================================================== 수단 결과 한 벌
# Streamlit ``compare._MeasureResults`` 의 사본 — 그 모듈이 streamlit 을 끌어와 부를 수 없다.
# 산출물 조립 함수(``kwise.report``)에 넘기는 인자가 같아야 Excel · PPT 가 같은 것을 본다.


@dataclass(frozen=True)
class _Results:
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
    #: 조합이 쓰는 것 — 1 kWp 발전 프로파일 · 태양광 용량 · ESS 목표.
    unit_profile: pd.Series | None = None
    capacity_kwp: float = 0.0
    ess_target: float | None = None
    solar_error: str = ""

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


def _enabled(choices: Choices) -> tuple[str, ...]:
    """켠 수단 — 7장 번호 순 (조합 이름이 실행마다 달라지지 않게)."""
    return tuple(spec.key for spec in MEASURES if spec.key in choices.enabled)


def _day(ctx: Context, choices: Choices) -> RepresentativeDay | None:
    days = representative_days(ctx.usage)
    if not days:
        return None
    return find_day(ctx.usage, choices.day_key or days[0].key, custom=choices.custom_day)


def _measure_results(ctx: Context, choices: Choices) -> _Results:
    """켠 수단만 계산한다 — 2단계 카드와 같은 공용 함수 · 같은 인자 (일꾼 스레드에서 돈다)."""
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
    enabled = _enabled(choices)

    switch = None
    if "tariff_switch" in enabled:
        switch = evaluate_tariff_switch(
            usage,
            table,
            form.selection,
            quality=quality,
            options=form.billing_options(),
            option_totals=dict(diagnosis.option_totals),
        )
    contract = None
    if "contract" in enabled and form.contract_kw is not None:
        contract = evaluate_contract_adjustment(
            usage,
            baseline,
            contract_kw=form.contract_kw,
            contract_floor_ratio=None,
            table=table,
            options=form.billing_options(),
        )
    demand_response = None
    if "demand_response" in enabled and diagnosis.dr is not None:
        demand_response = evaluate_demand_response(
            diagnosis.dr, unit_price_won_per_kwh=choices.dr_unit_price, jeju=ctx.building.jeju
        )
    power_factor = None
    if "power_factor" in enabled:
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
    surplus = None
    inputs = choices.solar.ran
    if "solar" in enabled and inputs is not None:
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
        # 2단계 카드와 같은 값 — 고른 잉여 처리를 여기서도 싣는다.
        surplus_point = _surplus(
            usage, table, form, unit_profile, point.capacity_kwp, choices.solar
        )
        scenario, revenue = _chosen_revenue(choices.solar, surplus_point)
        solar = with_surplus_revenue(
            point, revenue_won=revenue, scenario=scenario, base_fee_months=curve.base_fee_months
        )
        solar_curve, solar_certainty, solar_reason = curve, curve.certainty, curve.cost.reason
        if capacity > 0:
            surplus = _surplus(usage, table, form, unit_profile, capacity, choices.solar)

    ess = ess_curve = ess_optimum = None
    ess_target = None
    if "ess" in enabled and diagnosis.peak.billing_demand_kw > 0:
        model = load_ess_cost_model()
        ess_curve = ess_target_curve(
            usage.kw,
            usage.meta.interval_minutes,
            baseline_demand_kw=float(diagnosis.peak.billing_demand_kw),
            base_fee_won_per_kw=float(table.rates(form.selection).base_won_per_kw),
            model=model,
        )
        if ess_curve.best is not None:
            ess_optimum = refine_ess_target(
                usage,
                table,
                form.selection,
                curve=ess_curve,
                baseline=baseline,
                quality=quality,
                options=form.billing_options(),
                model=model,
            )
            if ess_optimum.viable:
                ess_target = choices.ess_target or ess_optimum.target_kw
        if ess_target:
            quote = choices.ess_quote
            ess = evaluate_ess(
                usage,
                table,
                form.selection,
                target_kw=ess_target,
                cost=EssCostInput.of_total(quote) if quote is not None else EssCostInput.unpriced(),
                model=model,
                baseline=baseline,
                quality=quality,
                options=form.billing_options(),
            )

    generation = unit_profile * solar.capacity_kwp if solar and unit_profile is not None else None
    surplus_kw = (
        apply_generation(usage, unit_profile * capacity).surplus_kw
        if surplus is not None and unit_profile is not None
        else None
    )
    free_kwp = area = None
    if unit_profile is not None and inputs is not None:
        free_kwp = surplus_free_capacity_kwp(usage, unit_profile)
        area = inputs.area_m2 or None
    return _Results(
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
        ess_optimum=ess_optimum,
        surplus=surplus,
        surplus_free_kwp=free_kwp,
        solar_area_m2=area,
        base_fee_months=baseline.base_fee_months,
        bill=baseline,
        usage=usage,
        day=_day(ctx, choices),
        dr_profile=diagnosis.dr,
        solar_generation_kw=generation,
        surplus_kw=surplus_kw,
        unit_profile=unit_profile,
        capacity_kwp=capacity if unit_profile is not None else 0.0,
        ess_target=ess_target,
        solar_error=solar_error,
    )


def _specs(
    ctx: Context, choices: Choices, results: _Results, enabled: tuple[str, ...]
) -> tuple[CombinationSpec, ...]:
    """조합 명세 — 공용 ``combination_specs`` 에 2단계 값을 그대로 넘긴다."""
    form = ctx.form
    inputs = choices.solar.ran
    scenario, revenue = ("", None)
    if results.unit_profile is not None and "solar" in enabled and results.capacity_kwp > 0:
        scenario, revenue = _chosen_revenue(
            choices.solar,
            _surplus(
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
        enabled=enabled,
        pv_capacity_kwp=results.capacity_kwp if "solar" in enabled else 0.0,
        pv_unit_cost_won_per_kwp=inputs.unit_cost_won_per_kwp if inputs else None,
        pv_total_investment_won=inputs.total_investment_won if inputs else None,
        ess_target_kw=results.ess_target if "ess" in enabled else None,
        ess_total_investment_won=choices.ess_quote,
        power_factor_pct=(
            (choices.pf_target or default_target_pct()) if "power_factor" in enabled else None
        ),
        power_factor_investment_won=choices.pf_investment,
        surplus_revenue_won=revenue,
        surplus_scenario=scenario,
    )


def _compare(
    ctx: Context, results: _Results, specs: tuple[CombinationSpec, ...]
) -> ComparisonResult:
    """조합 비교 — Streamlit ``cached_comparison`` 과 같은 부름(잉여 수익은 뒤에 얹는다)."""
    revenue = next(
        (spec.surplus_revenue_won for spec in specs if spec.has_pv and spec.surplus_revenue_won),
        None,
    )
    scenario = next((spec.surplus_scenario for spec in specs if spec.surplus_scenario), "")
    stripped = tuple(replace(spec, surplus_revenue_won=None, surplus_scenario="") for spec in specs)
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


def _sensitivity(
    ctx: Context, results: _Results, specs: tuple[CombinationSpec, ...]
) -> pd.DataFrame:
    """감도 — 화면에는 없고 Excel · PPT 에만 간다 (Streamlit 과 같다)."""
    pv_specs = [spec for spec in specs if spec.has_pv]
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


# ===================================================================== 화면


async def render(ctx: Context, choices: Choices) -> None:
    """3단계 한 벌 — 탭을 열 때 짓는다 (켠 수단을 다시 모으는 데 몇 초 걸린다)."""
    ui.label("3단계 · 개선안 조합").classes("k-section")
    reviewed = _enabled(choices)
    scope = review_scope(reviewed)
    with ui.card().classes("k-card w-full gap-1"):
        ui.markdown("**검토 범위**")
        ui.label("검토함 — " + (", ".join(map(measure_title, scope.reviewed_labels)) or "없음"))
        ui.label("미검토 — " + (", ".join(map(measure_title, scope.skipped_labels)) or "없음"))
        w.caption("미검토는 '효과가 없다' 가 아니라 '보지 않았다' 입니다.")
    if ctx.diagnosis.structure is None:
        w.caution("계약 정보를 확정해야 조합을 비교합니다 (1단계).")
        return

    with ui.row().classes("items-center gap-2") as wait:
        ui.spinner(size="sm")
        ui.label("켠 개선안을 모으는 중…").classes("k-note")
    try:
        results = await run.io_bound(_measure_results, ctx, choices)
    finally:
        wait.delete()
    if results.solar_error:
        w.blocked(results.solar_error)

    rows = results.standalone()
    _standalone_block(rows)
    stale = _stale_lines(ctx, choices)

    async def compute() -> None:
        choices.combo_computed = tuple(
            key for key in reviewed if choices.combo_checked.get(key, True)
        )
        await draw()

    async def draw() -> None:
        combo_box.clear()
        with combo_box:
            await _combination(ctx, choices, results, rows, scope, reviewed)

    _combination_picker(choices, reviewed, stale, compute, draw)
    combo_box = ui.column().classes("w-full gap-3")
    await draw()


def _stale_lines(ctx: Context, choices: Choices) -> tuple[str, ...]:
    """막힘 줄 — 태양광을 계산한 뒤 입력을 바꿨으면 조합도 산출물도 못 만든다."""
    from kwise.pv import load_pv_presets

    from .solar import _inputs  # 같은 판정 — 카드의 「입력이 변경되었습니다」 와 한 자리

    form = choices.solar
    if "solar" not in choices.enabled or form.ran is None or not ctx.building.region_key:
        return ()
    if form.ran == _inputs(form, ctx.building.region_key, load_pv_presets()):
        return ()
    return (f"{measure_title(measure('solar').title)} — {STALE_INPUT}",)


def _standalone_block(rows: tuple[StandaloneRow, ...]) -> None:
    ui.label("개선안별 요약").classes("text-base font-semibold mt-2")
    if not rows:
        w.note("2단계에서 수단을 하나도 켜지 않았습니다.")
        return
    frame = standalone_frame(rows)
    if not frame.empty:
        frame = frame.assign(**{"수단": [measure_title(str(value)) for value in frame["수단"]]})
    w.table(frame)
    ui.markdown(
        fmt.markdown_safe(
            "각 줄은 **그 수단만 도입했을 때**의 값입니다 (현재 요금제·현재 사용량 기준). "
            + SIMPLE_SUM_NOTE
            + " "
            + fmt.ROUNDING_FOOTNOTE
        )
    ).classes("k-note")


def _combination_picker(
    choices: Choices,
    reviewed: tuple[str, ...],
    stale: tuple[str, ...],
    compute: Callable[[], Awaitable[None]],
    redraw: Callable[[], Awaitable[None]],
) -> None:
    """조합은 사용자가 짠다 — 기본은 2단계에서 켠 수단 전부. 다 고르고 한 번 누른다."""
    ui.label("조합 구성").classes("text-base font-semibold mt-2")
    if not reviewed:
        w.note("2단계에서 개선안을 하나도 켜지 않아 조합할 것이 없습니다.")
        return

    async def toggled(key: str, value: bool) -> None:
        choices.combo_checked[key] = value
        await redraw()  # 묵은 결과 흐림만 바뀐다 — 「합산효과 계산」 전에는 다시 비교하지 않는다

    with ui.row().classes("w-full gap-6"):
        for key in reviewed:
            ui.checkbox(
                measure(key).label,
                value=choices.combo_checked.get(key, True),
                on_change=lambda e, key=key: toggled(key, bool(e.value)),
            )
    w.caption(
        "체크한 개선안만 합산효과에 넣습니다. 체크를 풀어도 위의 개선안별 요약에는 "
        "남습니다 — 뺀 것이 얼마짜리였는지 보여야 뺄지 말지 정할 수 있습니다."
    )
    for line in stale:
        w.caution(line)
    button = ui.button("합산효과 계산", on_click=compute).props("unelevated no-caps")
    if stale:
        button.disable()


async def _combination(
    ctx: Context,
    choices: Choices,
    results: _Results,
    rows: tuple[StandaloneRow, ...],
    scope: ReviewScope,
    reviewed: tuple[str, ...],
) -> None:
    computed = choices.combo_computed
    if computed is None:
        if reviewed:
            w.note("개선안을 고르고 「합산효과 계산」 을 누르십시오.")
        _downloads(ctx, choices, results, None, no_pv_sensitivity_frame(), scope)
        return
    picked = tuple(key for key in reviewed if choices.combo_checked.get(key, True))
    stale = computed != picked
    enabled = tuple(key for key in computed if key in reviewed)
    specs = _specs(ctx, choices, results, enabled)
    if len(specs) == 1:
        w.note("요금에 영향을 주는 개선안을 하나 이상 고르면 합산효과를 계산합니다.")
        _downloads(ctx, choices, results, None, no_pv_sensitivity_frame(), scope)
        return

    with ui.row().classes("items-center gap-2") as wait:
        ui.spinner(size="sm")
        ui.label("조합을 비교하는 중…").classes("k-note")
    try:
        comparison = await run.io_bound(_compare, ctx, results, specs)
    finally:
        wait.delete()
    dr_won = None
    if "demand_response" in enabled:
        if choices.dr_unit_price is not None and ctx.diagnosis.dr is not None:
            # 원 부하로 잰 2단계 카드 값 — 조합 부하로 다시 재면 발전량이 DR 로 또 세어진다.
            dr_won = evaluate_demand_response(
                ctx.diagnosis.dr, unit_price_won_per_kwh=choices.dr_unit_price
            ).settlement_won
        comparison = comparison.with_demand_response(ctx.diagnosis.dr, choices.dr_unit_price)

    if stale:
        w.caution("선택이 변경되었습니다 — 다시 계산하십시오.")
    with ui.column().classes("w-full gap-3" + (" k-stale" if stale else "")):
        _combined_block(
            ctx, comparison, rows, results.contract, enabled, results.peer_savings(), dr_won
        )
    seen = results.shown_facts()
    fresh = tuple(item for item in comparison.notices if dedupe_key(item, base=True) not in seen)
    heads = _measure_notice_heads(comparison)
    for notice in screen_notices(tuple(heads.get(item, item) for item in fresh)):
        w.render_notice(notice)
    grounds = tooltip(fresh)
    if grounds:
        w.caption(f"산출 근거 {len(grounds)}건", tooltip_text(fresh))

    with ui.row().classes("items-center gap-2") as wait:
        ui.spinner(size="sm")
        ui.label("감도를 훑는 중…").classes("k-note")
    try:
        sensitivity = await run.io_bound(_sensitivity, ctx, results, specs)
    finally:
        wait.delete()
    _downloads(ctx, choices, results, comparison, sensitivity, scope)


def _measure_notice_heads(comparison: ComparisonResult) -> dict[Notice, Notice]:
    """조합 이름 머리를 붙인 안내 → 머리 없는 안내 — Streamlit 과 같은 잣대."""
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


def _combined_block(
    ctx: Context,
    comparison: ComparisonResult,
    rows: tuple[StandaloneRow, ...],
    contract: ContractAdjustment | None,
    chosen: tuple[str, ...],
    peers: Peers,
    dr_won: float | None,
) -> None:
    """합산효과 — 단순 합과의 차이를 반드시 보인다 (Streamlit ``_combined_block`` 과 같은 셈)."""
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

    ui.label("합산효과").classes("text-base font-semibold mt-2")
    payback = (
        payback_years(combined.investment_won, actual)
        if combined.investment_won is not None
        else None
    )
    # 2개씩 두 줄 — 한 줄 넷이면 억 단위 금액이 잘렸다 (Streamlit 과 같다).
    w.metrics([("단순 합", fmt.won_year(simple)), ("합산효과", fmt.won_year(actual))])
    w.metrics(
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
    w.caption("이 조합을 모두 도입했을 때의 12개월 환산 절감액입니다.")
    excluded = [row for row in rows if not row.combinable]
    if excluded:
        settled = bool(dr_won)
        w.caption(
            ("조합 부하에 넣지 않은 수단 — " if settled else "합산효과에 넣지 않은 수단 — ")
            + ", ".join(measure_title(row.title) for row in excluded)
            + ". 요금이 아니라 별도 정산·수익이라 "
            + ("합산효과에 DR 정산금으로 더합니다." if settled else "조합 부하에 얹을 수 없습니다.")
        )
    dropped = [row for row in rows if row.combinable and row.key not in set(chosen)]
    if dropped:
        w.caption(
            "조합에서 뺀 개선안 — " + ", ".join(measure_title(row.title) for row in dropped) + "."
        )
    if comparison.ess_below_minimum_note:
        w.caption(comparison.ess_below_minimum_note)
    reasons = (
        _interaction_reasons(comparison, combined, picked)
        if money.gap_won(combined_shown, simple_shown)
        else []
    )
    extra_won = _contract_headroom(ctx, combined, contract)
    sheet = combination_worksheet(
        simple_won=simple_shown,
        combined_won=combined_shown,
        reasons=tuple(reasons),
        contract_extra_won=extra_won,
    )
    w.worksheet(sheet)


def _interaction_reasons(
    comparison: ComparisonResult, combined: CombinationResult, rows: tuple[StandaloneRow, ...]
) -> list[str]:
    """실제로 발생한 상호작용만 적는다 — Streamlit ``_interaction_reasons`` 의 사본."""
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
    """계약전력 추가 하향 — 조합 부하로 잰다 (Streamlit ``_contract_headroom`` 과 같은 부름)."""
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
        ui.label("계약전력 추가 하향 — 이 조합에서도 하한에 걸리지 않아 낮출 이유가 없습니다.")
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
    ui.markdown(fmt.markdown_safe(text))
    return extra if extra > 0 else None


# ===================================================================== 내려받기


def _downloads(
    ctx: Context,
    choices: Choices,
    results: _Results,
    comparison: ComparisonResult | None,
    sensitivity: pd.DataFrame,
    scope: ReviewScope,
) -> None:
    """Excel(분석자용) · PPT(의사결정자용) — 만든 파일은 「다운로드」 폴더에 저장한다."""
    ui.label("내려받기").classes("text-base font-semibold mt-2")
    structure = ctx.diagnosis.structure
    assert structure is not None
    baseline = structure.bill
    blocked = _stale_lines(ctx, choices)
    billed = ctx.form.power_factor_pct is not None
    building = ctx.building

    async def make(label: str, build: Callable[[], tuple[bytes, str]], status: ui.column) -> None:
        status.clear()
        with status, ui.row().classes("items-center gap-2") as wait:
            ui.spinner(size="sm")
            ui.label(f"{label} 를 만드는 중…").classes("k-note")
        try:
            payload, filename = await run.io_bound(build)
            DOWNLOADS.mkdir(parents=True, exist_ok=True)
            path = DOWNLOADS / filename
            path.write_bytes(payload)
        except Exception as exc:
            status.clear()
            with status:
                w.blocked(f"{label} — 만들지 못했습니다. {exc}")
            return
        finally:
            if not wait.is_deleted:
                wait.delete()
        status.clear()
        with status, ui.row().classes("items-center gap-3"):
            ui.label(f"저장했습니다 — {path} · {fmt.count(len(payload) / 1024, 'KB')}").classes(
                "k-note"
            )
            ui.button("폴더 열기", on_click=lambda: os.startfile(DOWNLOADS)).props("flat no-caps")

    with ui.tabs().props("align=left no-caps") as tabs:
        excel_tab = ui.tab("Excel — 분석자용")
        deck_tab = ui.tab("PPT 보고서 — 의사결정자용")
    with ui.tab_panels(tabs, value=excel_tab).classes("w-full bg-transparent"):
        with ui.tab_panel(excel_tab).classes("gap-2 p-0 pt-2"):
            w.caption("부록 셋을 포함한 통합문서입니다. 파일명에 날짜·시각이 붙습니다.")
            timeseries = ui.checkbox(
                f"{interval_label(ctx.usage.meta.interval_minutes)} 시계열 시트 포함",
                value=choices.include_timeseries,
                on_change=lambda e: setattr(choices, "include_timeseries", bool(e.value)),
            )
            for line in blocked:
                w.caution(line)

            def excel() -> tuple[bytes, str]:
                sections = ReportSections(
                    usage=ctx.usage,
                    bill=baseline,
                    diagnosis=ctx.diagnosis,
                    comparison=comparison,
                    sensitivity=sensitivity,
                    measure_rows=results.excel_frame(),
                    solar_curve=results.solar_curve,
                    include_timeseries=bool(timeseries.value),
                    worksheets=results.worksheets(),
                    tariff_table=ctx.table,
                    ess_cases=load_ess_cost_model().case_table(),
                    measure_notices=results.notice_groups(),
                    solar=results.solar,
                    peer_savings=results.peer_savings(),
                    floor_area_m2=building.floor_area_m2,
                    power_factor_billed=billed,
                )
                return build_report_bytes(sections, session_id=_SESSION)

            excel_row = ui.row()
            excel_status = ui.column().classes("w-full")
            with excel_row:
                excel_button = ui.button(
                    "Excel 만들기", on_click=lambda: make("Excel", excel, excel_status)
                ).props("unelevated no-caps")
            if blocked:
                excel_button.disable()
        with ui.tab_panel(deck_tab).classes("gap-2 p-0 pt-2"):
            w.caption("한 장에 한 가지만 담은 슬라이드입니다. 켠 수단만 한 장씩 들어갑니다.")
            name = building.title
            ui.markdown(f"표지 이름 — **{name}** (옆단 「건물 정보」 에서 고칩니다)").classes(
                "k-note"
            )
            for line in blocked:
                w.caution(line)

            def deck() -> tuple[bytes, str]:
                loaded = daily_temperature(ctx.usage, building.region_key)
                document = DocumentSections(
                    usage=ctx.usage,
                    bill=baseline,
                    diagnosis=ctx.diagnosis,
                    comparison=comparison,
                    sensitivity=sensitivity_items(
                        sensitivity, comparison, results.peer_savings(), results.solar
                    ),
                    measures=results.entries(),
                    surplus=results.surplus_page(),
                    worksheets=results.worksheets(),
                    tariff_table=ctx.table,
                    ess_cases=load_ess_cost_model().case_table(),
                    temperature=loaded[0] if loaded is not None else None,
                    building_name=name,
                    reviewed_labels=scope.reviewed_labels,
                    skipped_labels=scope.skipped_labels,
                    peer_savings=results.peer_savings(),
                    school_notice=SCHOOL_HIGH_VOLTAGE_NOTICE if building.education else "",
                    power_factor_billed=billed,
                    dr_period_won=(
                        results.demand_response.period_settlement_won
                        if results.demand_response is not None
                        else None
                    ),
                )
                return slides_bytes(document)

            deck_row = ui.row()
            deck_status = ui.column().classes("w-full")
            with deck_row:
                deck_button = ui.button(
                    "PPT 보고서 만들기", on_click=lambda: make("PPT 보고서", deck, deck_status)
                ).props("unelevated no-caps")
            if blocked:
                deck_button.disable()
