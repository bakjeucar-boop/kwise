"""2단계 5 태양광 카드 — 데스크톱 화면 (Streamlit ``ui\\views\\measures.py`` 의 ``_solar`` 자리).

계산은 공용 ``kwise.ui.pipeline``(발전 프로파일 · 용량 곡선 · 잉여 지점 · 방위별 비율)과
``kwise.measures``(잉여 · 잉여 수익 싣기)가 한다. 여기 있는 것은 입력 받기와 결과 놓기뿐이다.

**「태양광 계산」 단추를 눌러야 돈다** — 값 하나만 바꿔도 몇 초 도는 구간이라 (Streamlit 과 같다).
계산 뒤 입력을 바꾸면 결과를 지우지 않고 「입력이 변경되었습니다」 를 띄운다.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd
from nicegui import run, ui

from kwise.io import UsageData, interval_label
from kwise.measures import (
    CURTAIL_SCENARIO,
    EXTERNAL_SCENARIO,
    OFFSET_SCENARIO,
    SolarPoint,
    SurplusResult,
    annualize,
    apply_generation,
    default_external_price_won_per_kwh,
    default_smp_price_won_per_kwh,
    evaluate_surplus,
    offset_settles_cash,
    surplus_free_capacity_kwp,
    surplus_options,
    with_surplus_revenue,
)
from kwise.pv import PvPresets, area_from_capacity_m2, capacity_preview, load_pv_presets
from kwise.report import narrative, notices
from kwise.report.days import RepresentativeDay
from kwise.report.worksheet import solar_worksheet
from kwise.tariff import TariffTable
from kwise.ui import charts
from kwise.ui import text as fmt
from kwise.ui.pipeline import (
    AzimuthOption,
    ContractForm,
    SolarInputs,
    azimuth_options,
    solar_result,
    surplus_capacity_points,
    unit_pv_profile,
)
from kwise.ui.spec import MeasureSpec

from . import vega
from . import widgets as w
from .building import weather_source_label

if TYPE_CHECKING:
    from .measures import Choices, Context

#: 묵은 결과 경고 — Streamlit ``STALE_INPUT`` 과 같은 글.
STALE_INPUT = "입력이 변경되었습니다 — 다시 계산하십시오."
SAVING_LABEL = "12개월 환산 절감액"


@dataclass
class SolarForm:
    """태양광 카드의 입력칸 값 — 2단계를 다시 지어도 남는다."""

    area: float = 1000.0
    density: str = ""
    azimuth: str = ""
    unit_cost: float = 0.0
    capacity: float = 0.0
    azimuth_override: float = 0.0
    loss: float = 0.14
    total_known: bool = False
    total: float = 0.0
    wall_area: float = 0.0
    wall_azimuth: str = ""
    #: 남는 전기 처리 — 기본은 아무 절차도 필요 없는 출력제어다 (Streamlit 과 같다).
    surplus_use: str = CURTAIL_SCENARIO
    #: 단가 둘 — ``None`` 이면 아직 손대지 않아 기준 데이터 기본값, 0 이면 미산출.
    surplus_price: float | None = None
    smp_price: float | None = None
    #: 마지막으로 계산한 입력 — 없으면 아직 안 돌았다. 3단계가 같은 입력을 읽는다.
    ran: SolarInputs | None = None

    def prices(self) -> tuple[float | None, float | None]:
        """(외부 판매, 상계 SMP) — 안 만졌으면 기본값, 0 은 미산출 (Streamlit 과 같다)."""
        external = (
            default_external_price_won_per_kwh()
            if self.surplus_price is None
            else self.surplus_price
        )
        smp = default_smp_price_won_per_kwh() if self.smp_price is None else self.smp_price
        return (external or None), (smp or None)


def _inputs(form: SolarForm, region_key: str, presets: PvPresets) -> SolarInputs:
    density = form.density or presets.default.key
    azimuth = presets.azimuth(form.azimuth or presets.default_azimuth).azimuth_deg
    wall = (
        presets.azimuth(form.wall_azimuth or presets.default_azimuth).azimuth_deg
        if form.wall_area > 0
        else None
    )
    return SolarInputs(
        region_key=region_key,
        area_m2=form.area,
        density_key=density,
        capacity_kwp=form.capacity or None,
        azimuth_deg=form.azimuth_override or azimuth,
        system_loss_ratio=form.loss,
        wall_area_m2=form.wall_area,
        wall_azimuth_deg=wall,
        unit_cost_won_per_kwp=form.unit_cost or None,
        total_investment_won=(form.total or None) if form.total_known else None,
    )


async def card(
    ctx: Context, choices: Choices, day: RepresentativeDay | None, spec: MeasureSpec
) -> None:
    w.caption(spec.headline)
    form: SolarForm = choices.solar
    presets = load_pv_presets()
    region_key = ctx.building.region_key
    if not region_key:
        w.caution("옆단에서 지역(시도·시군구)을 고르십시오. 기상 격자를 정하는 값입니다.")
        return
    lock = asyncio.Lock()

    def now() -> SolarInputs:
        return _inputs(form, region_key, presets)

    def check_stale() -> None:
        # 경고는 단추 바로 아래, 옛 결과는 흐리게 — 남아 있되 지금 입력의 값이 아니라는 표시다.
        stale_box.clear()
        stale = form.ran is not None and form.ran != now()
        if stale:
            with stale_box:
                w.caution(f"{STALE_INPUT} 아래는 이전 결과입니다.")
        if stale:
            result_box.classes(add="k-dim")
        else:
            result_box.classes(remove="k-dim")

    # ---- 기본 입력 — 면적과 설치 밀도 (환산 용량은 라벨에, 상충 관계는 풀이에)
    with ui.grid(columns=2).classes("w-full gap-x-6"):
        area_in = ui.number("설치 가능 면적 (m²)", value=form.area, min=0, step=50)
        density_box = ui.column().classes("gap-0")

    def density_options() -> dict[str, str]:
        by_key = {p.key: c for p, c in capacity_preview(float(area_in.value or 0), presets)}
        return {
            item.key: f"{item.label} — {fmt.kwp(by_key.get(item.key, 0.0))}"
            for item in presets.densities
        }

    with density_box:
        ui.label(presets.density_label).classes("k-label")
        density_in = ui.radio(density_options(), value=form.density or presets.default.key).props(
            "inline"
        )
        with density_in:
            w.tip("\n\n".join(f"**{item.label}** — {item.tradeoff}" for item in presets.densities))
    w.caption(f"지역 — {region_key.replace('/', ' ')} (옆단 「건물 정보」 에서 고칩니다)")

    azimuth_box = ui.column().classes("w-full gap-1")
    unit_in = ui.number("설치 단가 (원/kWp)", value=form.unit_cost, min=0, step=10_000)
    with unit_in:
        w.tip("견적 단가입니다. 넣지 않으면 투자비와 회수기간을 산출하지 않습니다.")

    with ui.expansion("상세").classes("w-full"):
        with ui.grid(columns=2).classes("w-full gap-x-6"):
            capacity_in = ui.number(
                "설치 용량 직접 입력 (kWp) — 0 이면 면적 환산", value=form.capacity, min=0, step=10
            )
            loss_in = ui.number(
                "시스템 손실", value=form.loss, min=0, max=0.4, step=0.01, format="%.2f"
            )
            az_override_in = ui.number(
                "방위각 직접 입력 (도) — 0 이면 위 8방위 선택",
                value=form.azimuth_override,
                min=0,
                max=360,
                step=5,
            )
            with az_override_in:
                w.tip("8방위 격자(45도)에 없는 각도를 쓸 때만 넣습니다.")
            with ui.column().classes("gap-0"):
                total_known_in = ui.checkbox("총 투자비를 안다", value=form.total_known)
                total_in = ui.number("총 투자비 (원)", value=form.total, min=0, step=1_000_000)
                total_in.set_visibility(form.total_known)
        ui.markdown("**벽면 어레이** — 지붕과 방위를 따로 고릅니다. 0 이면 지붕 한 벌입니다.")
        wall_in = ui.number("벽면 설치 가능 면적 (m²)", value=form.wall_area, min=0, step=50)
        wall_box = ui.column().classes("w-full gap-1")

    def azimuth_labels(tilt: float, gcr: float) -> dict[str, str]:
        """계산해 둔 지역 · 경사와 같을 때만 상대 발전량을 라벨에 얹는다 (Streamlit 과 같다)."""
        labels = {item.key: item.label for item in presets.azimuths}
        ran = form.ran
        if ran is None or ran.region_key != region_key:
            return labels
        if abs(presets.density(ran.density_key or presets.default.key).tilt_deg - tilt) > 1e-9:
            return labels
        computed = _azimuth_memo.get((region_key, tilt, gcr))
        return {item.key: item.label for item in computed} if computed else labels

    def azimuth_picker(box: ui.column, field: str, tilt: float, gcr: float) -> None:
        box.clear()
        labels = azimuth_labels(tilt, gcr)
        with box:
            ui.label("방위").classes("k-label")
            picked = ui.radio(labels, value=getattr(form, field) or presets.default_azimuth).props(
                "inline"
            )
            with picked:
                w.tip(
                    f"경사 {fmt.count(tilt, '°')} 기준으로 여덟 방위를 각각 계산한 상대 "
                    "발전량입니다. 45도 간격이라 최대 오차는 22.5도이고, 그 차이는 발전량 "
                    f"1{fmt.RANGE}2% 로 예측 오차 안에 묻힙니다."
                )
            if labels == {item.key: item.label for item in presets.azimuths}:
                w.note(
                    "방위별 상대 발전량은 「태양광 계산」 을 누른 뒤 라벨에 붙습니다 — "
                    "지역·경사각을 바꿀 때마다 여덟 방위를 다시 돌리지 않습니다."
                )
            preset = presets.azimuth(str(picked.value))
            if preset.is_northward:
                w.caution(preset.caution)

            def chose(event: object) -> None:
                setattr(form, field, str(event.value))  # type: ignore[attr-defined]
                azimuth_picker(box, field, tilt, gcr)
                check_stale()

            picked.on_value_change(chose)

    def pickers() -> None:
        density = presets.density(form.density or presets.default.key)
        azimuth_picker(azimuth_box, "azimuth", density.tilt_deg, density.gcr)
        wall_box.clear()
        if form.wall_area > 0:
            azimuth_picker(wall_box, "wall_azimuth", 90.0, 1.0)

    def read_fields() -> None:
        form.area = float(area_in.value or 0)
        form.density = str(density_in.value or "")
        form.unit_cost = float(unit_in.value or 0)
        form.capacity = float(capacity_in.value or 0)
        form.azimuth_override = float(az_override_in.value or 0)
        form.loss = float(loss_in.value if loss_in.value is not None else 0.14)
        form.total_known = bool(total_known_in.value)
        form.total = float(total_in.value or 0)
        form.wall_area = float(wall_in.value or 0)

    def field_changed(*_: object) -> None:
        before_wall = form.wall_area > 0
        before_density = form.density
        read_fields()
        density_in.set_options(density_options(), value=density_in.value)
        total_in.set_visibility(form.total_known)
        if before_density != form.density or before_wall != (form.wall_area > 0):
            pickers()
        check_stale()

    density_in.on_value_change(field_changed)
    total_known_in.on_value_change(field_changed)
    for typed in (area_in, unit_in, capacity_in, loss_in, az_override_in, total_in, wall_in):
        typed.on("blur", field_changed)
        typed.on("keydown.enter", field_changed)
    pickers()

    async def calculate() -> None:
        read_fields()
        inputs = now()
        if inputs.resolved_capacity_kwp(presets) <= 0:
            result_box.clear()
            with result_box:
                w.caution("면적 또는 용량을 넣어야 계산합니다.")
            return
        form.ran = inputs
        check_stale()
        await draw()

    async def draw() -> None:
        async with lock:
            result_box.clear()
            if form.ran is None:
                with result_box:
                    w.note("면적·설치 밀도·지역·단가를 넣고 「태양광 계산」 을 누르십시오.")
                return
            button.props("loading")
            try:
                with result_box:
                    await _result(ctx, choices, day, spec, presets, form, result_box)
                pickers()  # 계산 뒤 방위 라벨에 상대 발전량이 붙는다
            finally:
                button.props(remove="loading")

    button = ui.button("태양광 계산", on_click=calculate).props("unelevated no-caps")
    stale_box = ui.column().classes("w-full")
    result_box = ui.column().classes("w-full gap-3")
    check_stale()
    await draw()


#: 방위별 상대 발전량 — (지역, 경사, GCR) 마다 한 번만 돈다 (여덟 방위 시뮬레이션).
_azimuth_memo: dict[tuple[str, float, float], tuple[AzimuthOption, ...]] = {}


def forget() -> None:
    """기준 데이터를 고친 뒤 방위 비율을 다시 낸다."""
    _azimuth_memo.clear()


async def _result(
    ctx: Context,
    choices: Choices,
    day: RepresentativeDay | None,
    spec: MeasureSpec,
    presets: PvPresets,
    form: SolarForm,
    box: ui.column,
) -> None:
    inputs = form.ran
    assert inputs is not None
    usage = ctx.usage
    structure = ctx.diagnosis.structure
    baseline = structure.bill if structure is not None else None
    with ui.row().classes("items-center gap-2") as wait:
        ui.spinner(size="sm")
        ui.label("태양광을 계산하는 중…").classes("k-note")
    try:
        try:
            unit_profile, source = await run.io_bound(unit_pv_profile, usage, inputs)
        except Exception as exc:
            wait.delete()
            w.blocked(f"기상 자료를 얻지 못해 계산하지 않았습니다. {exc}")
            return
        curve = await run.io_bound(
            solar_result,
            usage,
            ctx.table,
            ctx.form,
            inputs,
            unit_profile,
            baseline=baseline,
            quality=ctx.quality,
        )
        density = presets.density(inputs.density_key or presets.default.key)
        key = (inputs.region_key, float(density.tilt_deg), float(density.gcr))
        if key not in _azimuth_memo:
            # 방위 비율은 라벨일 뿐 — 못 내면 이름만 둔다.
            with contextlib.suppress(Exception):
                _azimuth_memo[key] = await run.io_bound(
                    azimuth_options,
                    usage,
                    SolarInputs(region_key=inputs.region_key, system_loss_ratio=0.14),
                    tilt_deg=float(density.tilt_deg),
                    gcr=float(density.gcr),
                )
        point = curve.points[-1]
        months = curve.base_fee_months
        surplus = await run.io_bound(
            _surplus, usage, ctx.table, ctx.form, unit_profile, point.capacity_kwp, form
        )
        surplus_points = await run.io_bound(
            surplus_capacity_points,
            usage,
            ctx.table,
            ctx.form,
            unit_profile,
            cost=inputs.cost(),
            baseline=baseline,
            quality=ctx.quality,
        )
    finally:
        if not wait.is_deleted:
            wait.delete()

    w.caption(f"기상 출처 — {weather_source_label(source)}")
    w.overview(spec.overview)
    scenario, revenue = _chosen_revenue(form, surplus)
    point = with_surplus_revenue(
        point, revenue_won=revenue, scenario=scenario, base_fee_months=months
    )
    w.metrics(
        [
            ("용량", fmt.kwp(point.capacity_kwp)),
            ("발전량", fmt.per_year(fmt.mwh(annualize(point.generation_kwh, months)))),
            (SAVING_LABEL, fmt.won_short(point.annual_saving_won), "", _saving_tip(point, months)),
            ("회수기간", fmt.payback(point.payback_years, investment_won=point.investment_won)),
        ]
    )
    w.caption("투자비 — " + fmt.won(point.investment_won, reason=curve.cost.reason))
    verdict = curve.verdict()
    ui.markdown(fmt.markdown_safe(verdict.headline(inputs.area_m2 or None)))
    reference = verdict.reference_note(surplus_free_capacity_kwp(usage, unit_profile))
    if reference:
        ui.markdown(fmt.markdown_safe(reference)).classes("k-note")

    _surplus_verdict(usage, surplus, unit_profile, months, presets, inputs)

    async def redraw_card() -> None:
        # 잉여 처리를 바꾸면 위 절감액도 바뀐다 — 카드 결과를 다시 그린다.
        box.clear()
        with box:
            await _result(ctx, choices, day, spec, presets, form, box)

    _surplus_handling(
        usage, ctx.table, ctx.form, unit_profile, point, months, surplus, form, redraw_card
    )

    w.h3("용량별 비교")
    w.table(
        _capacity_view(
            charts.solar_capacity_table(
                curve,
                verdict=verdict,
                surplus_points=surplus_points,
                gcr=density.gcr,
                area_per_kwp_m2=presets.area_per_kwp_m2,
                area_limit_m2=inputs.area_m2 or None,
            )
        )
    )
    tie_note = verdict.tie_note
    w.caption(
        "잉여가 생기기 시작하는 용량과 많이 생기는 용량을 함께 세웠습니다 — "
        "설치 가능 면적을 넘는 줄은 「면적 초과」 로 적습니다. "
        + (tie_note + " " if tie_note else "")
        + "20단계 상세는 Excel 「태양광 용량 곡선」 시트에 있습니다."
    )
    generation = unit_profile * point.capacity_kwp
    vega.chart(charts.solar_annual_chart(usage, generation))
    ratio = charts.solar_saving_ratio(usage, generation)
    w.caption(
        "날짜별 발전량" + (f" · 기간 사용량의 {fmt.ratio_pct(ratio)} 를 줄입니다" if ratio else ""),
        fmt.chart_tip(
            "chart.solar_annual",
            reading=structure is None or notices.covers_summer_and_winter(structure.bill),
        ),
    )
    if day is not None:
        vega.chart(charts.solar_day_chart(usage, generation, day, zoom=True))
        w.caption(
            f"{day.title} · 피크 앞뒤 {charts.PEAK_ZOOM_HOURS}시간",
            fmt.chart_tip("chart.solar_day"),
        )
    w.notices(curve.notices)
    w.worksheet(solar_worksheet(curve, point, power_factor_pct=ctx.form.lagging_pct))


def _surplus(
    usage: UsageData,
    table: TariffTable,
    form: ContractForm,
    unit_profile: pd.Series,
    capacity_kwp: float,
    solar: SolarForm,
) -> SurplusResult:
    """잉여 한 벌 — Streamlit ``cached_surplus`` 의 본문과 같은 부름."""
    external, smp = solar.prices()
    net = apply_generation(usage, unit_profile * capacity_kwp)
    return evaluate_surplus(
        usage,
        table,
        form.selection,
        net.surplus_kw,
        generation_kwh=net.generated_kwh,
        net_usage=net.usage,  # 차감 한도는 자가소비를 뺀 뒤의 부하다
        capacity_kwp=capacity_kwp,
        external_price_won_per_kwh=external,
        smp_price_won_per_kwh=smp,
        options=form.billing_options(),
    )


def _chosen_revenue(form: SolarForm, result: SurplusResult) -> tuple[str, float | None]:
    """(고른 처리 이름, 관측 기간 수익) — Streamlit ``chosen_surplus_revenue`` 와 같다."""
    scenario = form.surplus_use or CURTAIL_SCENARIO
    try:
        return scenario, result.scenario(scenario).revenue_won
    except KeyError:  # 용량이 상계 상한을 넘어 선택지에서 빠졌다 — 고른 적이 없는 것으로 본다
        return "", None


def _saving_tip(point: SolarPoint, months: float) -> str:
    """절감액에 무엇이 들어 있는지 — 문구는 공용 ``narrative`` 가 쥔다."""
    if not point.surplus_scenario:
        return ""
    head = (
        narrative.solar_saving_breakdown(
            self_consumption_won=annualize(point.self_consumption_saving_won, months),
            surplus_scenario=point.surplus_scenario,
            surplus_revenue_won=annualize(point.surplus_revenue_won, months),
        ).replace("절감액 = ", "")
        + "."
    )
    if not point.surplus_revenue_won:
        return head
    return (
        head + "\n\n역송분은 요금 계산에서 빠져 있고, 상계 차감은 그 뒤에 남은 "
        "순부하 사용량을 한도로 잽니다 — 겹쳐 세지 않았습니다."
    )


def _share(part: float, total: float) -> str:
    return fmt.ratio_pct(part / total) if total > 0 else fmt.DASH


def _surplus_verdict(
    usage: UsageData,
    surplus: SurplusResult,
    unit_profile: pd.Series,
    months: float,
    presets: PvPresets,
    inputs: SolarInputs,
) -> None:
    """잉여를 낼 것인가 — 12개월 환산 잉여 · 평일 · 휴일 · 잉여 없는 최대 용량."""
    density = presets.density(inputs.density_key or presets.default.key)
    free_kwp = surplus_free_capacity_kwp(usage, unit_profile)
    free_area = area_from_capacity_m2(
        free_kwp, gcr=density.gcr, area_per_kwp_m2=presets.area_per_kwp_m2
    )
    holiday_kwh = surplus.weekend_kwh + surplus.holiday_kwh
    total_text, weekday_text, holiday_text = notices.surplus_split_kwh(
        annualize(surplus.total_kwh, months),
        annualize(surplus.weekday_kwh, months),
        annualize(holiday_kwh, months),
    )
    w.metrics(
        [
            (
                "12개월 환산 잉여",
                total_text,
                f"발전량의 {fmt.ratio_pct(surplus.share_of_generation)}",
            ),
            (
                "평일 잉여",
                fmt.per_year(weekday_text),
                _share(surplus.weekday_kwh, surplus.total_kwh),
            ),
            (
                "토·일·공휴일 잉여",
                fmt.per_year(holiday_text),
                _share(holiday_kwh, surplus.total_kwh),
            ),
            (
                "잉여 없는 최대 용량",
                fmt.kwp(free_kwp),
                f"면적 {fmt.count(free_area, ' m²', decimals=0)}",
                fmt.tip("surplus_free").replace(
                    "15분", interval_label(usage.meta.interval_minutes)
                ),
            ),
        ]
    )


def _surplus_handling(
    usage: UsageData,
    table: TariffTable,
    form: ContractForm,
    unit_profile: pd.Series,
    point: SolarPoint,
    months: float,
    surplus: SurplusResult,
    solar: SolarForm,
    redraw: object,
) -> None:
    """남는 것으로 무엇을 하나 — 잉여가 있을 때만 묻고, 펼쳐 둔다 (Streamlit 과 같다)."""
    if surplus.total_kwh <= 0:
        return
    options = surplus_options(point.capacity_kwp)
    choice = solar.surplus_use if solar.surplus_use in options else CURTAIL_SCENARIO

    async def chose(event: object) -> None:
        solar.surplus_use = str(event.value)  # type: ignore[attr-defined]
        await redraw()  # type: ignore[operator]

    async def price_set(field: str, value: float | None) -> None:
        number = float(value) if value is not None else 0.0
        if getattr(solar, field) == number:
            return
        setattr(solar, field, number)
        await redraw()  # type: ignore[operator]

    with ui.expansion("잉여 처리", value=True).classes("w-full"):
        ui.label("남는 전기를 어떻게 하나").classes("k-label")
        ui.radio(list(options), value=choice, on_change=chose).props("inline")
        external, smp = solar.prices()
        if choice == EXTERNAL_SCENARIO:
            price = ui.number(
                "잉여 판매 단가 (원/kWh) — 0 이면 미산출", value=external or 0, min=0, step=1
            )
            with price:
                w.tip("우리가 파는 쪽의 단가입니다. 비우면 금액을 산출하지 않습니다.")
            price.on("keydown.enter", lambda: price_set("surplus_price", price.value))
            price.on("blur", lambda: price_set("surplus_price", price.value))
        elif (
            choice == OFFSET_SCENARIO
            and offset_settles_cash(point.capacity_kwp)
            and surplus.offset is not None
            and surplus.offset.remaining_kwh > 0
        ):
            smp_in = ui.number("SMP 단가 (원/kWh) — 0 이면 미산출", value=smp or 0, min=0, step=1)
            with smp_in:
                w.tip("당월 차감하고 남은 몫을 정산하는 단가입니다. 비우면 잔여량만 냅니다.")
            smp_in.on("keydown.enter", lambda: price_set("smp_price", smp_in.value))
            smp_in.on("blur", lambda: price_set("smp_price", smp_in.value))
        scenario = surplus.scenario(choice)
        revenue = scenario.revenue_won
        w.metrics(
            [
                (
                    "12개월 환산 수익",
                    fmt.won_short(annualize(revenue, months)) if revenue is not None else fmt.DASH,
                )
            ]
        )
        w.caption(
            fmt.markdown_safe(scenario.basis)
            if revenue is not None
            else fmt.won(revenue, reason=scenario.basis)
        )
        if note := surplus.chosen_price_note(choice):
            w.caption(note)
        surplus_kw = apply_generation(usage, unit_profile * point.capacity_kwp).surplus_kw
        vega.chart(charts.surplus_daily_chart(usage, surplus_kw))
        w.caption("날짜별 잉여", fmt.chart_tip("chart.surplus_daily"))
        settlement = surplus.offset
        if settlement is not None and settlement.carried:
            carried = settlement.carried
            w.table(
                pd.DataFrame(
                    {
                        "월": [str(item.month) for item in carried],
                        "차감": [fmt.kwh(item.deducted_kwh) for item in carried],
                        "다음 달로 이월": [fmt.kwh(item.carried_out_kwh) for item in carried],
                    }
                )
            )
            w.caption("그 달 그 계시 사용량을 넘어 차감하지 못한 몫입니다.")
        w.notices(tuple(item for item in surplus.notices if item.fact != "surplus.applied_price"))


def _self_consumption(ratio: float | None) -> str:
    """자가소비율 — 100.0% 는 잉여가 정확히 0 일 때만 (내림으로 자른다)."""
    if ratio is None or pd.isna(ratio):
        return fmt.DASH
    value = float(ratio)
    return fmt.ratio_pct(value if value >= 1.0 else math.floor(value * 1_000.0) / 1_000.0)


def _capacity_view(frame: pd.DataFrame) -> pd.DataFrame:
    """용량 표를 사람이 읽는 글로 — Streamlit ``_capacity_view`` 의 사본."""
    if frame.empty:
        return frame
    return pd.DataFrame(
        {
            "용량(kWp)": [fmt.kwp(value) for value in frame["용량(kWp)"]],
            "필요 면적": [
                fmt.count(value, " m²", decimals=0) if pd.notna(value) else fmt.DASH
                for value in frame["필요 면적(m²)"]
            ],
            "발전량": [fmt.per_year(fmt.mwh(value)) for value in frame["연간 발전량(kWh)"]],
            "자가소비율": [_self_consumption(value) for value in frame["자가소비율"]],
            "12개월 환산 자가소비 절감액": [
                fmt.won_short(value) for value in frame["자가소비 절감액(원)"]
            ],
            "투자비": [
                fmt.won_short(value, reason="미산출 — 단가 미입력") for value in frame["투자비(원)"]
            ],
            "회수기간": [
                fmt.payback(years, investment_won=investment)
                for years, investment in zip(
                    frame["회수기간(년)"], frame["투자비(원)"], strict=True
                )
            ],
            "표식": frame["표식"],
        }
    )


__all__ = ["STALE_INPUT", "SolarForm", "card"]
