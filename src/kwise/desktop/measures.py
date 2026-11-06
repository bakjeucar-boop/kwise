"""2단계 · 개선 수단 — 데스크톱 화면 (Streamlit ``ui\\views\\measures.py`` 의 자리).

**카드마다 공용 계산 함수(``kwise.measures.evaluate_*``)를 그대로 부른다.** 순서 · 이름 · 개요는
공용 ``kwise.ui.spec.MEASURES`` 가, 그래프는 ``kwise.ui.charts`` 가, 계산 근거 표는
``kwise.report.worksheet`` 가 쥔다. 여기 있는 것은 입력 받기와 결과 놓기뿐이다.

모든 카드는 독립 평가다 — 기준은 현재 요금제와 사용량이고, 카드를 켜고 꺼도 다른 카드의
숫자가 바뀌지 않는다. 켠 카드만 3단계 조합에 들어간다.

옮긴 카드: 1 선택요금 전환 · 2 계약전력 조정 · 3 경제성DR · 4 역률 개선 · 6 ESS.
남은 하나(5 태양광)는 다음 판이다 — 켜면 아직 없다고 적는다.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import pandas as pd
from nicegui import run, ui

from kwise import money
from kwise.diagnose import Diagnosis
from kwise.diagnose.dr import JUDGE_WINDOW, dr_event_hours, dr_max_events_per_day
from kwise.io import UsageData, interval_label
from kwise.measures import (
    MARGIN_FACT,
    NO_HEADROOM_LABEL,
    NO_SAVING,
    SHORTEST_PAYBACK,
    EssCostInput,
    default_target_pct,
    ess_target_curve,
    evaluate_contract_adjustment,
    evaluate_demand_response,
    evaluate_ess,
    evaluate_power_factor,
    evaluate_tariff_switch,
    high_rate_discharge_hours,
    load_ess_cost_model,
    power_factor_floor_pct,
    refine_ess_target,
)
from kwise.quality import QualityReport
from kwise.report import CONTRACT_CHANGE_WARNING, frames, notices
from kwise.report.days import RepresentativeDay, find_day, representative_days
from kwise.report.worksheet import (
    demand_response_worksheet,
    ess_worksheet,
    power_factor_worksheet,
    tariff_switch_worksheet,
)
from kwise.tariff import TariffTable, lagging_standard_pct
from kwise.ui import charts
from kwise.ui import text as fmt
from kwise.ui.labels import measure_title, option_label, selection_label
from kwise.ui.notices import partition_facts
from kwise.ui.pipeline import ContractForm
from kwise.ui.spec import DR_PRICED_HEADLINE, MEASURES, NO_HEADROOM_OVERVIEW, MeasureSpec

from . import vega
from . import widgets as w
from .building import BuildingInfo

#: 카드 지표의 절감액 이름 — 값이 12개월 환산이다 (Streamlit ``SAVING_LABEL`` 과 같다).
SAVING_LABEL = "12개월 환산 절감액"

#: 7.1 카드 본문에서 내리는 사실 — Streamlit ``_TARIFF_HIDDEN_FACTS`` 의 사본.
_TARIFF_HIDDEN_FACTS = (
    "quality.month_missing_rate",
    "tariff.billing_demand_rule",
    "tariff.school_exception_available",
    "tariff.tentative_base_fee_basis",
    "tariff_switch.best_selection",
)
#: 하한 판정 결론 — 본문이 직접 쓰므로 안내에서 뺀다.
_FLOOR_VERDICT = "contract.floor_not_binding"

#: 수단 머리에만 붙이는 표지 — Streamlit ``_ICONS`` 와 같다.
_ICONS = {
    "tariff_switch": "🔀",
    "contract": "📐",
    "demand_response": "📉",
    "power_factor": "🔌",
    "solar": "☀",
    "ess": "🔋",
}

_BASELINE = (
    "각 개선안은 독립적으로 평가합니다. 카드의 절감액은 해당 수단만 적용했을 "
    "때의 효과이며, 기준은 현재 요금제와 사용량입니다. 여러 수단을 동시에 "
    "적용한 효과는 3단계 합산효과에서 별도로 산정합니다."
)


@dataclass
class Context:
    """1단계가 넘기는 한 벌 — 이것이 있어야 2단계가 선다."""

    usage: UsageData
    table: TariffTable
    form: ContractForm
    diagnosis: Diagnosis
    quality: QualityReport
    building: BuildingInfo
    #: 1단계를 다시 계산하게 한다 — 경제성DR 「쉬는 날」 은 1단계 진단이 쓴다 (Streamlit 과 같다).
    rediagnose: Callable[[], Awaitable[None]]


@dataclass
class Choices:
    """2단계에서 사람이 고른 것 — 화면을 다시 지어도 남는다 (3단계가 읽는다)."""

    enabled: set[str] = field(default_factory=set)
    day_key: str = ""
    custom_day: dt.date | None = None
    pf_target: float | None = None
    pf_investment: float | None = None
    dr_priced: bool = False
    dr_price: float | None = None
    #: 쉬는 날이어서 뺄 날짜(ISO) — 1단계 진단이 읽는다.
    dr_off_days: tuple[str, ...] = ()
    ess_known: bool = False
    ess_total: float | None = None
    #: ESS 가 고른 목표 요금적용전력 — 3단계가 같은 값을 읽는다 (고를 것이 없으면 0).
    ess_target: float = 0.0

    @property
    def ess_quote(self) -> float | None:
        """「견적 총액을 안다」 를 체크했을 때만 — 0 은 미입력 (Streamlit ``ess_quote``)."""
        return (self.ess_total or None) if self.ess_known else None

    @property
    def dr_unit_price(self) -> float | None:
        """「정산 단가를 안다」 를 체크했을 때만 — 0 은 미입력 (Streamlit ``dr_unit_price``)."""
        return (self.dr_price or None) if self.dr_priced else None


Card = Callable[["Context", "Choices", RepresentativeDay | None], Awaitable[None]]


def _one_at_a_time(draw: Callable[[], Awaitable[None]]) -> Callable[[], Awaitable[None]]:
    """그리기를 한 번에 하나씩 — 비우고 계산을 기다리는 사이에 또 그리면 결과가 두 벌 쌓인다.

    체크와 Enter 처럼 두 번이 겹치면 뒤의 것이 앞의 것이 끝나기를 기다렸다가 다시 비우고 그린다.
    """
    lock = asyncio.Lock()

    async def serial() -> None:
        async with lock:
            await draw()

    return serial


def render(ctx: Context, choices: Choices) -> None:
    """2단계 화면 한 벌을 짓는다 — 1단계가 다시 계산될 때마다 새로 짓는다."""
    ui.label("2단계 · 개선 수단").classes("k-section")
    ui.label(_BASELINE).classes("k-note")
    day_box = ui.row().classes("w-full items-end gap-4")
    boxes: dict[str, ui.column] = {}

    def day() -> RepresentativeDay | None:
        days = representative_days(ctx.usage)
        if not days:
            return None
        key = choices.day_key or days[0].key
        return find_day(ctx.usage, key, custom=choices.custom_day)

    locks: dict[str, asyncio.Lock] = {}

    async def refresh(key: str) -> None:
        # 카드마다 한 번에 하나씩 — 켜기와 대표일 바꾸기가 겹치면 결과가 두 벌 쌓인다.
        async with locks.setdefault(key, asyncio.Lock()):
            box = boxes[key]
            box.clear()
            if key not in choices.enabled:
                return
            with box:
                await _CARDS.get(key, _not_yet)(ctx, choices, day())

    async def day_changed() -> None:
        # 하루 곡선을 쓰는 카드만 다시 — 역률 · 태양광 · ESS 가 같은 날을 본다.
        for key in ("power_factor", "solar", "ess"):
            await refresh(key)

    with day_box:
        _day_picker(ctx.usage, choices, day_changed)

    tier = ""
    for spec in MEASURES:
        if spec.tier != tier:
            tier = spec.tier
            ui.label(tier).classes("k-label mt-4")
        _toggle(spec, choices, refresh)
        boxes[spec.key] = ui.column().classes("w-full gap-3 pl-8")
    for key in sorted(choices.enabled):
        ui.timer(0, lambda key=key: refresh(key), once=True)


def _toggle(spec: MeasureSpec, choices: Choices, refresh: Callable[[str], Awaitable[None]]) -> None:
    """켜면 카드가 펼쳐지고 계산한다. 끄면 접히고 3단계 조합에서 빠진다 (Streamlit 과 같다)."""

    async def changed(event: object) -> None:
        on = bool(event.value)  # type: ignore[attr-defined]
        (choices.enabled.add if on else choices.enabled.discard)(spec.key)
        await refresh(spec.key)

    ui.checkbox(
        f"{_ICONS[spec.key]} {measure_title(spec.title)}",
        value=spec.key in choices.enabled,
        on_change=changed,
    ).classes("text-base font-semibold")


def _day_picker(
    usage: UsageData, choices: Choices, on_change: Callable[[], Awaitable[None]]
) -> None:
    """일일 곡선 대표일 — 역률 · 태양광 · ESS 의 하루 곡선이 모두 이 날을 쓴다."""
    days = representative_days(usage)
    if not days:
        return
    options = {d.key: d.title for d in days} | {"custom": "사용자 지정일"}
    picked = ui.select(options, value=choices.day_key or days[0].key, label="일일 곡선 대표일")
    picked.classes("min-w-[22rem]")
    with picked:
        w.tip(
            "역률·태양광·ESS 의 하루 곡선이 모두 이 날을 씁니다. "
            "기본은 기간 최대수요가 난 날입니다 — 피크가 어떻게 생겼는지가 "
            "세 수단의 공통 관심사이기 때문입니다."
        )
    start, end = usage.meta.start.date(), usage.meta.end.date()
    custom = ui.input("날짜", value=str(choices.custom_day or days[0].date)).props(
        f'type=date min="{start}" max="{end}"'
    )
    custom.set_visibility(picked.value == "custom")

    async def changed() -> None:
        choices.day_key = str(picked.value)
        custom.set_visibility(picked.value == "custom")
        try:
            choices.custom_day = dt.date.fromisoformat(str(custom.value))
        except ValueError:
            choices.custom_day = None
        await on_change()

    picked.on_value_change(changed)
    custom.on("change", changed)


async def _not_yet(ctx: Context, choices: Choices, day: RepresentativeDay | None) -> None:
    w.note("이 카드는 데스크톱 화면에 아직 없습니다. 지금은 Streamlit 화면에서 보십시오.")


def _headline(spec: MeasureSpec) -> None:
    w.caption(spec.headline)


# --------------------------------------------------------------------- 1 선택요금 전환


async def _tariff_switch(ctx: Context, choices: Choices, day: RepresentativeDay | None) -> None:
    spec = _spec("tariff_switch")
    _headline(spec)
    result = await run.io_bound(
        evaluate_tariff_switch,
        ctx.usage,
        ctx.table,
        ctx.form.selection,
        quality=ctx.quality,
        options=ctx.form.billing_options(),
        option_totals=dict(ctx.diagnosis.option_totals),
    )
    w.overview(spec.overview)
    w.metrics(
        [
            ("현행", option_label(result.current.selection.option)),
            ("가장 유리한 요금제", option_label(result.best.selection.option)),
            (SAVING_LABEL, fmt.won_short(result.annual_saving_won)),
        ]
    )
    if result.switch_needed:
        best = selection_label(ctx.table, result.best.selection)
        ui.markdown(f"가장 유리한 요금제는 **{best}** 입니다.")
    else:
        ui.label("현재 요금제가 이미 가장 유리합니다. 바꿀 이유가 없습니다.")
    vega.chart(charts.tariff_delta_chart(result))
    vega.chart(charts.tariff_option_chart(result))
    w.caption(
        notices.TARIFF_SWITCH_CAPTION,
        fmt.chart_tip("chart.tariff_delta") + "\n\n" + fmt.chart_tip("chart.tariff_option"),
    )
    w.notices(partition_facts(result.notices, _TARIFF_HIDDEN_FACTS)[1])
    w.worksheet(tariff_switch_worksheet(result))


# --------------------------------------------------------------------- 2 계약전력 조정


async def _contract(ctx: Context, choices: Choices, day: RepresentativeDay | None) -> None:
    spec = _spec("contract")
    _headline(spec)
    structure = ctx.diagnosis.structure
    if ctx.form.contract_kw is None or structure is None:
        w.overview(spec.overview)
        w.caution("계약전력을 입력해야 조정 여지를 봅니다 (1단계 계약 정보).")
        return
    baseline = structure.bill
    result = await run.io_bound(
        evaluate_contract_adjustment,
        ctx.usage,
        baseline,
        contract_kw=ctx.form.contract_kw,
        contract_floor_ratio=None,
        table=ctx.table,
        options=ctx.form.billing_options(),
    )
    w.overview(spec.overview)
    ratio = result.contract_floor_ratio
    cells: list[tuple[str, ...]] = [
        ("계약전력", fmt.kw(result.contract_kw)),
        (
            f"계약전력의 {fmt.ratio_pct(ratio, decimals=0)}" if ratio is not None else "하한",
            fmt.kw(result.floor_kw) if result.floor_kw is not None else "—",
        ),
        ("최대수요", notices.max_demand_text(result.demand_before_floor_kw)),
    ]
    if result.target_contract_kw is not None:
        cells.append(("목표 계약전력", fmt.kw(result.target_contract_kw, decimals=0)))
    cells.append(
        (
            SAVING_LABEL,
            NO_SAVING
            if result.no_saving and result.annual_saving_won == 0
            else fmt.won_short(result.annual_saving_won, reason=result.saving_basis),
        )
    )
    w.metrics(cells)
    verdict = next((item for item in result.notices if item.fact == _FLOOR_VERDICT), None)
    if verdict is not None:
        ui.markdown(fmt.markdown_safe(verdict.text))
    if result.reducible:
        w.caption("현재 부하 기준입니다. 다른 수단을 함께 켜면 3단계에서 다시 계산됩니다.")
        w.caution(CONTRACT_CHANGE_WARNING)
        period_note = notices.contract_period_note(baseline)
        if period_note:
            w.caution(period_note)
    w.notices(
        tuple(item for item in result.notices if item.fact not in (MARGIN_FACT, _FLOOR_VERDICT))
    )


# --------------------------------------------------------------------- 3 경제성DR

#: 요일 이름 — 저부하 평일 표와 같은 말 (Streamlit ``_WEEKDAYS``).
_WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")


async def _demand_response(ctx: Context, choices: Choices, day: RepresentativeDay | None) -> None:
    spec = _spec("demand_response")
    # 캡션은 그 벌의 단가로 참인 말만 한다 — 단가를 넣었으면 뒤 문장만 (Streamlit 과 같다).
    headline = ui.label().classes("k-note")

    def headline_text() -> None:
        headline.set_text(DR_PRICED_HEADLINE if choices.dr_unit_price else spec.headline)

    headline_text()
    dr = ctx.diagnosis.dr
    if dr is None:
        w.overview(spec.overview)
        w.caution("경제성DR 참여 여력을 산출하지 못했습니다.")
        return

    async def priced_changed(event: object) -> None:
        choices.dr_priced = bool(event.value)  # type: ignore[attr-defined]
        price.set_visibility(choices.dr_priced)
        await draw()

    async def price_changed() -> None:
        value = float(price.value) if price.value else None
        if value == choices.dr_price:  # Enter 뒤 칸을 떠날 때 또 그리면 막 연 접힘이 닫힌다
            return
        choices.dr_price = value
        await draw()

    with ui.row().classes("w-full items-center gap-4"):
        ui.checkbox(
            "정산 단가를 안다 (사업자 제시값)", value=choices.dr_priced, on_change=priced_changed
        )
        price = ui.number("정산 단가 (원/kWh)", value=choices.dr_price or 0, min=0, step=1)
        price.set_visibility(choices.dr_priced)
        price.on("blur", price_changed)
        price.on("keydown.enter", price_changed)
    body = ui.column().classes("w-full gap-3")

    async def draw() -> None:
        headline_text()
        body.clear()
        with body:
            _demand_response_result(ctx, choices, spec)

    await draw()


def _demand_response_result(ctx: Context, choices: Choices, spec: MeasureSpec) -> None:
    dr = ctx.diagnosis.dr
    assert dr is not None
    result = evaluate_demand_response(
        dr, unit_price_won_per_kwh=choices.dr_unit_price, jeju=ctx.building.jeju
    )
    w.overview(spec.overview)
    # 저부하 평일을 세지 않은 벌(주말에도 가동)은 0 이 아니라 「미산출」 이다.
    counted = not result.unassessed_reason
    w.metrics(
        [
            (
                "거래 가능일",
                fmt.days(result.eligible_days),
                "",
                "분석 기간의 평일 수입니다. 토·일·공휴일은 입찰할 수 없어 뺐습니다.\n\n"
                "이 가운데 감축할 여력이 있는 날만 아래 「저부하 평일」 로 셉니다.",
            ),
            (
                "저부하 평일",
                fmt.days(result.low_load_days),
                "",
                "거래 가능일 중 부하가 쉬는 날 수준까지 내려온 날 수입니다.\n\n"
                "**연간 참여 일수 제한이 없으므로 이 날 수가 실질 제약입니다** — "
                "감축할 여력이 있는 날이 몇 날이냐가 전부입니다.",
            ),
            (
                "등록 권장 용량",
                fmt.kw(result.registered_capacity_kw, decimals=0) if counted else notices.UNPRICED,
                "",
                "사업자 등록 시 제시할 보수적인 감축 가능 용량입니다.\n\n"
                "저부하일 여력 분포의 하위값이라 어느 참여일에나 지킬 수 있습니다 — "
                "평균으로 등록하면 절반의 날에 미달하고, 미달은 입찰 제한으로 이어집니다.",
            ),
            (
                "12개월 환산 감축 가능량",
                fmt.kwh(result.annual_reducible_kwh) if counted else notices.UNPRICED,
                "",
                "실제 참여 가능 시간과 일별 감축 여력을 반영한 12개월 환산 감축 잠재량입니다.\n\n"
                "저부하일마다 (그날 여력 × 그날 참여 가능 시간)을 더해 365일로 "
                "환산했습니다 — 등록 용량 × 시간이 아닙니다.",
            ),
        ]
    )
    hours_sum = fmt.hours(result.participation_hours, decimals=0) if counted else notices.UNPRICED
    w.caption(
        f"{JUDGE_WINDOW} {dr.window_label} · 하루 한도(가정) "
        f"{dr_max_events_per_day()}회 × 최대 {fmt.hours(dr_event_hours()[1], decimals=0)} "
        f"(하루 {fmt.hours(result.daily_hours_cap, decimals=0)}) · 참여 가능 시간 합 {hours_sum}"
    )
    if result.weekend_baseline_kw is not None and result.low_load_threshold_kw is not None:
        w.caption(
            f"저부하 판정 기준선은 주말·공휴일 {JUDGE_WINDOW} 평균 "
            f"{fmt.kw(result.weekend_baseline_kw)} 이고, 그 "
            f"{dr.low_load_multiple:.2g}배인 "
            f"{fmt.kw(result.low_load_threshold_kw)} 이하인 평일을 셌습니다."
        )
    vega.chart(charts.dr_daily_chart(dr))
    w.caption(
        f"일별 {JUDGE_WINDOW} 평균 부하",
        fmt.chart_tip("chart.dr_daily", reading=result.low_load_threshold_kw is not None),
    )
    # 뺀 날이 있으면 목록이 비어도 접힘을 남긴다 — 안 남기면 되돌릴 칸이 사라진다.
    if result.low_load_days or choices.dr_off_days:
        with ui.expansion(f"저부하 평일 {result.low_load_days}일").classes("w-full"):
            if result.low_load_days:
                w.table(result.low_load_day_table)
            _off_day_picker(ctx, choices, result.low_load_day_table)
    if not result.low_load_days and counted:
        ui.label("저부하 평일이 없어 감축 가능량을 0 으로 두었습니다.")
    if result.is_priced:
        w.metrics(
            [
                (
                    "12개월 환산 정산금",
                    NO_SAVING if result.no_reduction else fmt.won_short(result.settlement_won),
                )
            ]
        )
    w.caption(
        "자원 유형 — " + (", ".join(str(item) for item in result.resource_types) or "판정 불가")
    )
    w.notices(result.notices)
    w.worksheet(demand_response_worksheet(result))


def _off_day_picker(ctx: Context, choices: Choices, frame: object) -> None:
    """쉬는 날을 사람이 뺀다 — 고른 날은 거래 가능일에서 빠지고 1단계부터 다시 계산한다.

    이미 뺀 날도 선택지에 남긴다 — 빼고 나면 목록에서 사라져 되돌릴 수가 없다.
    """
    listed = {str(value) for value in frame["날짜"]}  # type: ignore[index]
    dates = sorted(listed | set(choices.dr_off_days))
    labels = {d: f"{d} ({_WEEKDAYS[dt.date.fromisoformat(d).weekday()]})" for d in dates}

    async def changed(event: object) -> None:
        choices.dr_off_days = tuple(sorted(event.value or ()))  # type: ignore[attr-defined]
        await ctx.rediagnose()

    with (
        ui.select(
            labels,
            value=list(choices.dr_off_days),
            label="쉬는 날이어서 뺄 날짜",
            multiple=True,
            on_change=changed,
        )
        .props("use-chips")
        .classes("w-full")
    ):
        w.tip(
            "근로자의 날이나 임시공휴일이 섞여 있을 수 있습니다. 고른 날은 거래 "
            "가능일에서 빼고 감축량을 다시 계산합니다."
        )


# --------------------------------------------------------------------- 4 역률 개선


async def _power_factor(ctx: Context, choices: Choices, day: RepresentativeDay | None) -> None:
    spec = _spec("power_factor")
    _headline(spec)
    structure = ctx.diagnosis.structure
    floor = power_factor_floor_pct()
    if choices.pf_target is None:
        choices.pf_target = default_target_pct()

    @_one_at_a_time
    async def draw() -> None:
        body.clear()
        with body:
            await _power_factor_result(ctx, choices, day, spec, structure)

    async def changed() -> None:
        new_target = float(target.value) if target.value is not None else None
        new_invest = float(invest.value) if invest.value else None
        if (new_target, new_invest) == (choices.pf_target, choices.pf_investment):
            return  # 값이 그대로면 다시 그리지 않는다 — Enter 뒤 칸을 떠날 때 두 번 돈다
        choices.pf_target, choices.pf_investment = new_target, new_invest
        await draw()

    with ui.grid(columns=2).classes("w-full gap-x-4"):
        target = ui.number(
            "목표 지상역률 (%)", value=choices.pf_target, min=float(floor), max=100.0, step=0.1
        )
        invest = ui.number(
            "역률 개선 투자비 (원)", value=choices.pf_investment or 0, min=0, step=100_000
        )
    for field_in in (target, invest):  # 칸을 떠날 때 · Enter 에 다시 계산
        field_in.on("blur", changed)
        field_in.on("keydown.enter", changed)
    body = ui.column().classes("w-full gap-3")
    await draw()


async def _power_factor_result(
    ctx: Context,
    choices: Choices,
    day: RepresentativeDay | None,
    spec: MeasureSpec,
    structure: object,
) -> None:
    baseline = structure.bill if structure is not None else None  # type: ignore[attr-defined]
    target = choices.pf_target if choices.pf_target is not None else default_target_pct()
    result = await run.io_bound(
        evaluate_power_factor,
        ctx.usage,
        ctx.table,
        ctx.form.selection,
        current_pct=ctx.form.lagging_pct,
        target_pct=target,
        investment_won=choices.pf_investment,  # 0 은 미입력이다
        baseline=baseline,
        quality=ctx.quality,
        options=ctx.form.billing_options(),
        billed=ctx.form.power_factor_pct is not None,
    )
    w.overview(NO_HEADROOM_OVERVIEW if result.no_headroom else spec.overview)
    w.metrics(
        [
            ("현재 역률", fmt.pct(result.current_pct)),
            ("목표 역률", NO_HEADROOM_LABEL if result.no_headroom else fmt.pct(result.target_pct)),
            (
                SAVING_LABEL,
                NO_SAVING if result.no_headroom else fmt.won_short(result.annual_saving_won),
            ),
            (
                "회수기간",
                NO_SAVING
                if result.no_headroom
                else fmt.payback(result.payback_years, investment_won=result.investment_won),
            ),
        ]
    )
    with ui.row().classes("w-full gap-4 no-wrap"):
        with ui.column().classes("flex-1 min-w-0"):
            vega.chart(charts.power_triangle_chart(result))
            w.caption(
                "전력삼각형" if result.no_headroom else "전력삼각형 — 개선 전후",
                fmt.chart_tip("chart.power_triangle"),
            )
        if day is not None:
            with ui.column().classes("flex-1 min-w-0"):
                vega.chart(
                    charts.power_factor_day_chart(
                        ctx.usage,
                        day,
                        current_pct=result.current_pct,
                        target_pct=result.target_pct,
                    )
                )
                interval = interval_label(ctx.usage.meta.interval_minutes)
                w.caption(
                    f"{day.title} · {interval} 부하와 판정 창",
                    fmt.chart_tip("chart.power_factor_day").replace("15분", interval),
                )
    standard = lagging_standard_pct()
    if result.current_pct < standard:
        w.caution(
            f"현재 지상역률이 기준 {standard:,.0f}% 에 미달합니다 "
            f"({fmt.pct(result.current_pct)}). 매 1%p 마다 기본요금이 추가됩니다."
        )
    w.notices(result.notices)
    w.worksheet(power_factor_worksheet(result))


# --------------------------------------------------------------------- 6 ESS


async def _ess(ctx: Context, choices: Choices, day: RepresentativeDay | None) -> None:
    """목표를 사람이 찍지 않는다 — 회수기간이 가장 짧은 목표를 공용 계산이 고른다.

    단가는 기준 데이터의 조달 사례 모델 그대로다(데스크톱에는 「기준 데이터」 화면이 없다).
    견적 총액을 알면 그 값이 이긴다 (Streamlit 과 같다).
    """
    spec = _spec("ess")
    _headline(spec)

    async def known_changed(event: object) -> None:
        choices.ess_known = bool(event.value)  # type: ignore[attr-defined]
        total.set_visibility(choices.ess_known)
        await draw()

    async def total_changed() -> None:
        value = float(total.value) if total.value else None
        if value == choices.ess_total:
            return
        choices.ess_total = value
        await draw()

    with ui.row().classes("w-full items-center gap-4"):
        ui.checkbox("견적 총액을 안다", value=choices.ess_known, on_change=known_changed)
        total = ui.number("견적 총액 (원)", value=choices.ess_total or 0, min=0, step=1_000_000)
        total.set_visibility(choices.ess_known)
        total.on("blur", total_changed)
        total.on("keydown.enter", total_changed)
    body = ui.column().classes("w-full gap-3")

    @_one_at_a_time
    async def draw() -> None:
        body.clear()
        with body:
            await _ess_result(ctx, choices, day, spec)

    await draw()


def _ess_spec_view(frame: pd.DataFrame) -> pd.DataFrame:
    """목표별 사양 표를 사람이 읽는 글로 — Streamlit ``_ess_spec_view`` 의 사본."""
    if frame.empty:
        return frame
    savings = frame["연간 절감액(원)"]
    in_man = bool((savings.abs() >= 10_000).any())

    def saving(value: float) -> str:
        # 만원 한 자리는 천 원 절사다 — 한 열은 한 단위로.
        if in_man and 0 < abs(value) < 10_000:
            return f"{money.truncate_won(value) / 10_000:,.1f}만원"
        return fmt.won_short(value)

    return pd.DataFrame(
        {
            "목표": [f"{value} kW" for value in frame["목표 요금적용전력(kW)"]],
            "저감량": [fmt.kw(value, decimals=0) for value in frame["저감량(kW)"]],
            "출력": [fmt.kw(value, decimals=0) for value in frame["출력(kW)"]],
            "용량": [fmt.kwh(value) for value in frame["용량(kWh)"]],
            "방전시간": [fmt.hours(value) for value in frame["방전시간(h)"]],
            "투자비": [fmt.won_short(value, reason="—") for value in frame["투자비(원)"]],
            "12개월 환산 절감액": [saving(value) for value in savings],
            "회수기간": [
                fmt.payback(years, investment_won=investment)
                for years, investment in zip(
                    frame["회수기간(년)"], frame["투자비(원)"], strict=True
                )
            ],
            "표식": frame["표식"],
        }
    )


async def _ess_result(
    ctx: Context, choices: Choices, day: RepresentativeDay | None, spec: MeasureSpec
) -> None:
    peak = ctx.diagnosis.peak.billing_demand_kw
    if peak <= 0:
        choices.ess_target = 0.0
        w.caution("요금적용전력을 산출하지 못해 ESS 목표를 훑을 수 없습니다.")
        return
    structure = ctx.diagnosis.structure
    baseline = structure.bill if structure is not None else None
    # 기본요금단가는 현행 요금제 기준 — 최적 요금제 단가를 쓰면 독립 평가가 깨진다.
    base_fee = float(ctx.table.rates(ctx.form.selection).base_won_per_kw)
    model = load_ess_cost_model()
    w.overview(spec.overview)

    curve = await run.io_bound(
        ess_target_curve,
        ctx.usage.kw,
        ctx.usage.meta.interval_minutes,
        baseline_demand_kw=float(peak),
        base_fee_won_per_kw=base_fee,
        model=model,
    )
    if curve.best is None:
        choices.ess_target = 0.0
        w.caution(notices.ESS_NO_EXCESS)
        return
    # 한 점당 요금을 다시 계산해 몇 초 걸린다 — 아무 말 없이 멈추지 않게 돈다고 적는다.
    with ui.row().classes("items-center gap-2") as wait:
        ui.spinner(size="sm")
        ui.label("ESS 목표를 고르는 중…").classes("k-note")
    try:
        optimum = await run.io_bound(
            refine_ess_target,
            ctx.usage,
            ctx.table,
            ctx.form.selection,
            curve=curve,
            baseline=baseline,
            quality=ctx.quality,
            options=ctx.form.billing_options(),
            model=model,
        )
    finally:
        wait.delete()

    if optimum.below_minimum:
        # 살 물건이 없다 — 이 건물의 사실(필요 출력 · 용량 · 방전시간)만 낸다.
        choices.ess_target = 0.0
        w.metrics(
            [
                ("필요 출력", fmt.kw(optimum.required_power_kw)),
                ("필요 용량", notices.ess_capacity_text(optimum.required_capacity_kwh)),
                (
                    "방전시간",
                    fmt.hours(optimum.required_discharge_hours),
                    "",
                    fmt.tip("discharge_hours"),
                ),
            ]
        )
        w.notices(optimum.notices)
        return
    if not optimum.points:
        choices.ess_target = 0.0
        w.notices(optimum.notices)
        return

    table = frames.ess_spec_frame(optimum, baseline_demand_kw=curve.baseline_demand_kw)
    w.table(_ess_spec_view(table))
    w.caption(frames.ess_spec_caption(table), fmt.tip("table.ess_spec"))
    if not optimum.viable:
        choices.ess_target = 0.0
        w.notices(optimum.notices)
        return

    target = optimum.target_kw
    choices.ess_target = target
    cost = (
        EssCostInput.of_total(choices.ess_quote)
        if choices.ess_quote is not None
        else EssCostInput.unpriced()
    )
    result = await run.io_bound(
        evaluate_ess,
        ctx.usage,
        ctx.table,
        ctx.form.selection,
        target_kw=target,
        cost=cost,
        model=model,
        baseline=baseline,
        quality=ctx.quality,
        options=ctx.form.billing_options(),
    )
    ui.label(f"{SHORTEST_PAYBACK} 목표 {fmt.kw(target, decimals=0)} 기준").classes(
        "text-base font-semibold mt-2"
    )
    annual_factor = (
        result.annual_saving_won / result.total_saving_won if result.total_saving_won else 1.0
    )
    w.metrics(
        [
            (
                "출력 / 용량",
                f"{fmt.kw(result.power_kw, decimals=0)} / {fmt.kwh(result.capacity_kwh)}",
                f"방전 {fmt.hours(result.discharge_hours)}",
                fmt.tip("discharge_hours"),
            ),
            (
                SAVING_LABEL,
                fmt.won_short(result.annual_saving_won),
                "",
                fmt.ess_saving_line(
                    result.base_saving_won,
                    result.energy_saving_won,
                    result.energy_saving_won * annual_factor,
                )
                + " 경부하에 충전해 최대부하에 방전하니 비싼 시간의 사용량이 싼 시간으로 "
                "옮겨가지만, 왕복효율 손실만큼 총 사용량이 늘어 둘이 맞부딪힙니다.",
            ),
            ("투자비", fmt.won_short(result.investment_won), f"단가 — {result.pricing_path}"),
            ("회수기간", fmt.payback(result.payback_years, investment_won=result.investment_won)),
        ]
    )
    if day is not None:
        vega.chart(charts.ess_day_chart(ctx.usage, result.dispatch, day))
        w.caption(
            f"{day.title} · 피크 앞뒤 {charts.PEAK_ZOOM_HOURS}시간", fmt.chart_tip("chart.ess_day")
        )
        frame = charts.ess_day_frame(ctx.usage, result.dispatch, day.date)
        slot_hours = ctx.usage.meta.interval_minutes / 60.0
        charged = float(frame["충전(kW)"].sum()) * slot_hours if len(frame) else 0.0
        discharged = float(frame["방전(kW)"].sum()) * slot_hours if len(frame) else 0.0
        cut = float(frame["원부하(kW)"].max() - frame["순부하(kW)"].max()) if len(frame) else 0.0
        charge_span, discharge_span = charts.dispatch_schedule(frame)
        ui.markdown(
            f"**{day.title} 운전** — 경부하 {charge_span or fmt.DASH} 충전 "
            f"{fmt.kwh(charged)} · 최대부하 {discharge_span or fmt.DASH} 방전 "
            f"{fmt.kwh(discharged)} · 그날 저감 {fmt.kw(cut)}."
        )
    if 0 < result.discharge_hours < high_rate_discharge_hours():
        w.caution(
            f"방전시간이 {fmt.hours(result.discharge_hours)} 로 짧습니다. 고출력 셀 "
            "사양이 되어 도입 사례보다 단가가 높아질 수 있습니다."
        )
    w.notices(notices.without_arbitrage((*result.notices, *optimum.notices)))
    w.worksheet(ess_worksheet(result))


def _spec(key: str) -> MeasureSpec:
    return next(spec for spec in MEASURES if spec.key == key)


_CARDS: dict[str, Card] = {
    "tariff_switch": _tariff_switch,
    "contract": _contract,
    "demand_response": _demand_response,
    "power_factor": _power_factor,
    "ess": _ess,
}
