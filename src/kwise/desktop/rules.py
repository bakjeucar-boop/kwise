"""「기준 데이터」 화면 — 데스크톱 (Streamlit ``ui\\views\\rules_admin.py`` 의 자리).

**값을 고치는 것은 공용 ``kwise.rules`` 의 함수다** (``set_value`` · ``confirm`` · ``restore_*``).
이 화면은 그것을 부르기만 하고, 저장에 성공하면 기준 데이터를 다시 읽어 계산을 새로 돌린다
(Streamlit ``apply_rule_edit`` 이 캐시를 비우는 자리). 고치는 파일은 Streamlit 과 같다 —
저장소에서 띄우면 저장소 ``data\\``, exe 면 exe 옆에 복사된 ``data\\`` 다.

ESS 단가 경로 · 두 계수는 Streamlit 처럼 **이 화면을 연 동안만** 쓴다(기준 데이터 파일에
쓰지 않는다) — 값은 ``Choices`` 에 둔다.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import pandas as pd
from nicegui import ui

from kwise.measures import PRICING_BANDS, PRICING_FORMULA, EssCostModel, load_ess_cost_model
from kwise.pv.archive import archive_status
from kwise.report.frames import capacity_band_frame
from kwise.rules import (
    EditResult,
    RuleOrigin,
    assumptions,
    confirm,
    describe_items,
    diff_from_defaults,
    expiry_warnings,
    read_history,
    reload_rules,
    restore_defaults,
    restore_item,
    restore_previous,
    rules,
    set_value,
)
from kwise.tariff import load_tariff
from kwise.ui.rules_view import (
    RuleRow,
    build_rows,
    count_rows,
    diff_frame,
    header_text,
    weather_panel,
)
from kwise.ui.text import bullet_list

from . import widgets as w

if TYPE_CHECKING:
    from .measures import Choices


def ess_model(choices: Choices) -> EssCostModel:
    """고른 단가 경로와 두 계수로 조달 사례 모델 — Streamlit ``ess_cost_model`` 과 같은 셈."""
    model = load_ess_cost_model()
    if choices.ess_path and choices.ess_path != model.pricing_path:
        model = model.with_pricing_path(choices.ess_path)
    if choices.ess_fixed is None and choices.ess_per_kwh is None:
        return model
    return model.with_coefficients(
        fixed_won=model.fixed_won if choices.ess_fixed is None else choices.ess_fixed,
        per_kwh_won=model.per_kwh_won if choices.ess_per_kwh is None else choices.ess_per_kwh,
    )


def alerts() -> None:
    """모든 화면 위에 거는 알림 — 손상 복구는 조용히 넘기지 않고, 만료 경고도 함께 본다."""
    for ruleset in (rules(), assumptions()):
        if ruleset.recovered_from:
            w.blocked(
                f"기준 데이터 **{ruleset.origin.filename}** 를 읽지 못해 "
                f"`{ruleset.recovered_from}` 에서 복구했습니다. "
                "값이 최신인지 「기준 데이터」 화면에서 확인하십시오."
            )
    warnings = expiry_warnings()
    if warnings:
        with ui.expansion(f"⚠ 기준 데이터 확인 필요 {len(warnings)}건").classes("w-full"):
            ui.markdown(bullet_list(item.message() for item in warnings))


def render(choices: Choices, on_saved: Callable[[], Awaitable[None]]) -> None:
    """기준 데이터 한 벌. 저장에 성공하면 ``on_saved`` 가 계산을 새로 돌린다."""
    box = ui.column().classes("w-full gap-3")

    def redraw() -> None:
        box.clear()
        with box:
            _body(choices, saved)

    async def saved(result: EditResult) -> None:
        if result.ok:
            reload_rules()  # Streamlit ``clear_calc_cache`` 의 첫 줄과 같다
            redraw()
            ui.notify(f"✓ {result.message}", type="positive")
            await on_saved()
            return
        with box:
            w.blocked(result.message)
            ui.markdown(bullet_list(str(issue) for issue in result.issues))

    redraw()


def _body(choices: Choices, saved: Callable[[EditResult], Awaitable[None]]) -> None:
    w.caption(
        "법령 유래 값과 우리 판단값을 갈라 둡니다. 법령 항목은 함부로 고치지 말고, "
        "판단값은 조정하라고 있는 것입니다."
    )
    warnings = expiry_warnings(include_weather=False)
    if warnings:
        w.caption(f"확인이 필요한 항목 {len(warnings)}건")
    rows = build_rows(describe_items(), warnings)
    ui.label(header_text(count_rows(rows))).classes("text-base font-semibold")
    _tariff_block()
    _ess_cost_block(choices)
    _restore_block(saved)
    ui.separator()
    for row in rows:
        _row(row, saved)
    ui.separator()
    _weather_block()
    _history_block()


def _tariff_block() -> None:
    table = load_tariff()
    with ui.card().classes("k-card w-full gap-1"):
        ui.markdown(f"**요금표** — {table.source or '출처 미기재'} ({table.effective_date} 시행)")
        w.caption(
            "실제 청구서와 대조해 확인했습니다."
            if table.verified
            else "아직 실제 청구서와 대조하지 않았습니다. 단가는 공표 자료 그대로이며, "
            "청구서로 한 번 맞춰 보면 확신이 올라갑니다."
        )


def _ess_cost_block(choices: Choices) -> None:
    """ESS 투자비 단가 — 2항식 · kWh 구간 단가 (견적 총액은 ESS 카드에 있다)."""
    model = load_ess_cost_model()
    with ui.card().classes("k-card w-full gap-2"):
        ui.markdown(f"**ESS 투자비 단가** — {model.formula}")
        w.caption(model.provenance)
        ui.label("단가 경로").classes("k-label")
        path = ui.radio(
            [PRICING_FORMULA, PRICING_BANDS], value=choices.ess_path or PRICING_FORMULA
        ).props("inline")
        with ui.grid(columns=2).classes("w-full gap-x-6"):
            fixed = ui.number(
                "고정비 (원) — PCS·PMS·컨테이너·소방·공조·UPS·변압기",
                value=choices.ess_fixed if choices.ess_fixed is not None else model.fixed_won,
                min=0,
                step=1_000_000,
            )
            per_kwh = ui.number(
                "용량단가 (원/kWh) — 배터리",
                value=(
                    choices.ess_per_kwh if choices.ess_per_kwh is not None else model.per_kwh_won
                ),
                min=0,
                step=10_000,
            )
        detail = ui.column().classes("w-full gap-1")

        def show() -> None:
            detail.clear()
            with detail:
                f = float(fixed.value or 0)
                k = float(per_kwh.value or 0)
                if f != model.fixed_won or k != model.per_kwh_won:
                    w.caution("계수 조정됨 — 자동 산출값이 아닙니다.")
                w.table(capacity_band_frame(model.with_coefficients(fixed_won=f, per_kwh_won=k)))
                if path.value == PRICING_BANDS:
                    w.caption("구간 단가로 산정합니다. 구간 안에서는 kWh당 단가가 일정합니다.")

        def changed(*_: object) -> None:
            # 출고값과 같으면 「안 만졌다」 — Streamlit 이 0 · 미입력을 기본값으로 읽는 것과 같다.
            choices.ess_path = str(path.value)
            f, k = float(fixed.value or 0), float(per_kwh.value or 0)
            choices.ess_fixed = None if f == model.fixed_won else (f or None)
            choices.ess_per_kwh = None if k == model.per_kwh_won else (k or None)
            choices.ess_target = 0.0  # 단가가 바뀌면 ESS 목표를 다시 고른다
            show()

        path.on_value_change(changed)
        for typed in (fixed, per_kwh):
            typed.on("blur", changed)
            typed.on("keydown.enter", changed)
        show()


def _row(row: RuleRow, saved: Callable[[EditResult], Awaitable[None]]) -> None:
    badge = "🟥 법령" if row.is_statutory else "🟦 판단"
    flags = []
    if row.needs_check:
        flags.append("⚠ 확인 필요")
    if row.changed:
        flags.append("● 기본값과 다름")
    with ui.card().classes("k-card w-full"), ui.row().classes("w-full no-wrap gap-6"):
        with ui.column().classes("flex-[3] min-w-0 gap-1"):
            ui.markdown(f"**{badge}** · {row.view.label}  \n`{row.key}`")
            if flags:
                w.caption(" · ".join(flags))
            w.caption(row.source_text, _source_tip(row))
            w.caption(f"확인일 — {row.verified_text}")
            if row.view.note:
                w.caption(f"비고 — {row.view.note}")
        with ui.column().classes("flex-[2] min-w-0 gap-1"):
            _editor(row, saved)


def _editor(row: RuleRow, saved: Callable[[EditResult], Awaitable[None]]) -> None:
    """값 편집 — 형을 지켜 되돌려 준다 (정수 항목을 실수로 저장하지 않는다)."""
    value = row.view.value
    field: ui.element | None = None
    if isinstance(value, bool):
        field = ui.checkbox("값", value=value)
    elif isinstance(value, int):
        field = ui.number("값", value=value, step=1, format="%d")
    elif isinstance(value, float):
        field = ui.number("값", value=value)
    elif isinstance(value, str):
        field = ui.input("값", value=value)
    else:
        # 목록 · 사전은 화면에서 고치지 않는다 — 엑셀 왕복 경로를 쓴다.
        ui.code(repr(value)).classes("w-full")
        w.caption("구조가 있는 값은 tools\\export_rules_xlsx.py 로 고칩니다.")

    def stored() -> bool | int | float | str:
        current = field.value  # type: ignore[union-attr]
        if isinstance(value, bool):
            return bool(current)
        if isinstance(value, int):
            return int(current)
        if isinstance(value, float):
            return float(current)
        return str(current)

    with ui.row().classes("gap-2"):
        if field is not None:
            ui.button("저장", on_click=lambda: saved(set_value(row.key, stored()))).props(
                "unelevated no-caps dense"
            )
        with ui.button("확인함", on_click=lambda: saved(confirm(row.key))).props(
            "flat no-caps dense"
        ):
            w.tip("값은 그대로 두고 확인일만 갱신합니다.")
        if row.changed:
            with ui.button("되돌리기", on_click=lambda: saved(restore_item(row.key))).props(
                "flat no-caps dense"
            ):
                w.tip(f"기본값 {row.view.default_value!r} 로 되돌립니다.")
    if row.changed:
        w.caption(f"기본값 — {row.view.default_value!r}")


def _restore_block(saved: Callable[[EditResult], Awaitable[None]]) -> None:
    """원복 세 경로 — 출고 복원은 차이 목록을 먼저 보이고 확인받는다."""
    w.h2("원복")
    w.caption(
        "직전 상태 · 출고 상태 · 항목별 셋을 모두 씁니다. 항목별 되돌리기는 각 줄에 있습니다."
    )

    def ask(origin: RuleOrigin) -> None:
        preview.clear()
        result = restore_defaults(origin, confirmed=False)  # 실행하지 않고 차이만 돌려준다
        diffs = diff_from_defaults(origin)
        with preview:
            w.caution(result.message)
            if diffs:
                w.table(diff_frame(diffs))
            with ui.row().classes("gap-2"):

                async def go() -> None:
                    preview.clear()
                    await saved(restore_defaults(origin, confirmed=True))

                ui.button("확인했습니다 — 기본값으로 되돌립니다", on_click=go).props(
                    "unelevated no-caps"
                )
                ui.button("취소", on_click=preview.clear).props("flat no-caps")

    for origin in (RuleOrigin.STATUTORY, RuleOrigin.JUDGEMENT):
        with ui.row().classes("w-full items-center gap-3"):
            ui.markdown(f"**{origin}** (`{origin.filename}`)").classes("flex-1")
            ui.button(
                "직전 상태로", on_click=lambda origin=origin: saved(restore_previous(origin))
            ).props("flat no-caps dense")
            ui.button("출고 상태로", on_click=lambda origin=origin: ask(origin)).props(
                "flat no-caps dense"
            )
    preview = ui.column().classes("w-full gap-2")


def _weather_block() -> None:
    w.h2("기상 데이터 현황")
    panel = weather_panel(archive_status())
    w.metrics(
        [
            ("확보 격자", f"{panel.cell_count}개"),
            ("연도", panel.year_text),
            ("용량", f"{panel.megabytes:,.1f} MB"),
            ("최종 취득", panel.fetched_text),
        ]
    )
    w.caption(
        "필요한 격자만 받아 두는 구조라 일부만 있어도 정상입니다. 없는 격자는 "
        f"조회 시점에 Open-Meteo 에서 받습니다. 저장 위치 — {panel.root}."
    )


def _history_block() -> None:
    with ui.expansion("변경 이력").classes("w-full"):
        records = read_history()
        if not records:
            ui.label("아직 변경이 없습니다.")
            return
        w.table(
            pd.DataFrame(
                [
                    {
                        "시각": record.changed_at,
                        "파일": record.file,
                        "항목": record.key,
                        "이전": str(record.before),
                        "이후": str(record.after),
                        "동작": record.action,
                        "비고": record.note,
                    }
                    for record in reversed(records)
                ]
            )
        )


def _source_tip(row: RuleRow) -> str:
    """근거 풀이 — 조문 번호와 시행일, 확인처 이름 (주소는 버린다 · Streamlit 과 같다)."""
    parts = [row.source_text]
    link = row.link
    index = link.find("http")
    where = (link[:index] if index >= 0 else link).strip().rstrip("—·-").strip()
    if where:
        parts.append(f"확인처 — {where}")
    if row.view.note:
        parts.append(row.view.note)
    return " · ".join(parts)
