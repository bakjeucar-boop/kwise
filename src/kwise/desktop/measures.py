"""2단계 · 개선 수단 — 데스크톱 화면 (Streamlit ``ui\\views\\measures.py`` 의 자리).

**카드마다 공용 계산 함수(``kwise.measures.evaluate_*``)를 그대로 부른다.** 순서 · 이름 · 개요는
공용 ``kwise.ui.spec.MEASURES`` 가, 그래프는 ``kwise.ui.charts`` 가, 계산 근거 표는
``kwise.report.worksheet`` 가 쥔다. 여기 있는 것은 입력 받기와 결과 놓기뿐이다.

모든 카드는 독립 평가다 — 기준은 현재 요금제와 사용량이고, 카드를 켜고 꺼도 다른 카드의
숫자가 바뀌지 않는다. 켠 카드만 3단계 조합에 들어간다.

옮긴 카드: 1 선택요금 전환 · 2 계약전력 조정 · 4 역률 개선. 나머지 셋(경제성DR · 태양광 ·
ESS)은 다음 판이다 — 켜면 아직 없다고 적는다.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from nicegui import run, ui

from kwise.diagnose import Diagnosis
from kwise.io import UsageData, interval_label
from kwise.measures import (
    MARGIN_FACT,
    NO_HEADROOM_LABEL,
    NO_SAVING,
    default_target_pct,
    evaluate_contract_adjustment,
    evaluate_power_factor,
    evaluate_tariff_switch,
    power_factor_floor_pct,
)
from kwise.quality import QualityReport
from kwise.report import CONTRACT_CHANGE_WARNING, notices
from kwise.report.days import RepresentativeDay, find_day, representative_days
from kwise.report.worksheet import power_factor_worksheet, tariff_switch_worksheet
from kwise.tariff import TariffTable, lagging_standard_pct
from kwise.ui import charts
from kwise.ui import text as fmt
from kwise.ui.labels import measure_title, option_label, selection_label
from kwise.ui.notices import partition_facts
from kwise.ui.pipeline import ContractForm
from kwise.ui.spec import MEASURES, NO_HEADROOM_OVERVIEW, MeasureSpec

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


@dataclass
class Choices:
    """2단계에서 사람이 고른 것 — 화면을 다시 지어도 남는다 (3단계가 읽는다)."""

    enabled: set[str] = field(default_factory=set)
    day_key: str = ""
    custom_day: dt.date | None = None
    pf_target: float | None = None
    pf_investment: float | None = None


Card = Callable[["Context", "Choices", RepresentativeDay | None], Awaitable[None]]


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

    async def refresh(key: str) -> None:
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


# --------------------------------------------------------------------- 4 역률 개선


async def _power_factor(ctx: Context, choices: Choices, day: RepresentativeDay | None) -> None:
    spec = _spec("power_factor")
    _headline(spec)
    structure = ctx.diagnosis.structure
    floor = power_factor_floor_pct()
    if choices.pf_target is None:
        choices.pf_target = default_target_pct()

    async def draw() -> None:
        body.clear()
        with body:
            await _power_factor_result(ctx, choices, day, spec, structure)

    async def changed() -> None:
        choices.pf_target = float(target.value) if target.value is not None else None
        choices.pf_investment = float(invest.value) if invest.value else None
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


def _spec(key: str) -> MeasureSpec:
    return next(spec for spec in MEASURES if spec.key == key)


_CARDS: dict[str, Card] = {
    "tariff_switch": _tariff_switch,
    "contract": _contract,
    "power_factor": _power_factor,
}
