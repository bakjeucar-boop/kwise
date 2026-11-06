"""화면 조각 — 지표 카드 · 캡션 · 주의 · 안내 · 표 · 계산 근거. 1·2·3단계가 함께 쓴다.

Streamlit 쪽 ``st.metric`` · ``st.caption(help=)`` · ``callout`` · ``tables.show`` 자리다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

import pandas as pd
from nicegui import ui

from kwise.notices import Notice, tooltip
from kwise.report.columns import display_frame
from kwise.report.worksheet import Worksheet
from kwise.ui import text as fmt
from kwise.ui.notices import Severity, screen_notices, tooltip_text

CSS = """
:root { --k-muted: #6e6e73; }
.body--dark { --k-muted: #8e8e93; }
body { font-family: system-ui, sans-serif; line-height: 1.5; }
.k-title { font-size: 1.75rem; font-weight: 700; letter-spacing: -0.02em; line-height: 1.1; }
/* 제목 세 층 — 단계(크고 굵게) · 절(왼쪽 색 막대) · 소절(굵게). 크기와 무게로 가른다. */
.k-h1 { font-size: 1.6rem; font-weight: 700; letter-spacing: -0.02em; line-height: 1.15;
        margin: 0.25rem 0 0.25rem; }
.k-section, .k-h2 { font-size: 1.2rem; font-weight: 650; letter-spacing: -0.01em;
        margin-top: 1.75rem; padding-left: 0.6rem; border-left: 4px solid var(--q-primary);
        line-height: 1.3; }
.k-h3 { font-size: 1rem; font-weight: 650; margin-top: 0.75rem; }
/* 위 막대 — 파일 메뉴와 단계 탭. 늘 보인다. */
.k-bar { background: var(--k-bar-bg); color: inherit;
         border-bottom: 1px solid rgba(142, 142, 147, 0.3); }
:root { --k-bar-bg: #ffffff; }
.body--dark { --k-bar-bg: #1d1d1f; }
.k-steps .q-tab { min-height: 3rem; padding: 0 1.25rem; }
.k-steps .q-tab__label { font-size: 0.95rem; font-weight: 600; }
.k-steps .q-tab--inactive { opacity: 0.6; }
.k-next { margin-top: 2rem; }
.k-label { font-size: 0.8rem; color: var(--k-muted); letter-spacing: 0.01em; }
.k-value { font-size: 1.6rem; font-weight: 650; letter-spacing: -0.02em; line-height: 1.15; }
.k-sub { font-size: 0.8rem; color: var(--k-muted); }
.k-note { font-size: 0.85rem; color: var(--k-muted); }
.k-card { border: 1px solid rgba(142, 142, 147, 0.3); border-radius: 12px; box-shadow: none; }
.k-stale { opacity: 0.45; pointer-events: none; }
.k-dim { opacity: 0.6; }
.k-caution p { margin: 0; font-size: 0.9rem; }
.k-overview { border-left: 3px solid rgba(142, 142, 147, 0.45); padding-left: 0.75rem; }
.k-overview p { margin: 0; }
.k-tip { white-space: pre-line; max-width: 22rem; font-size: 0.8rem; }
.k-info { color: var(--k-muted); min-height: 1.4rem; min-width: 1.4rem; }
.k-info:focus-visible { outline: 2px solid currentColor; outline-offset: 1px; }
.q-btn:active { transform: scale(0.97); transition: transform 100ms ease-out; }
@media (prefers-reduced-motion: reduce) { .q-btn:active { transform: none; } }
"""

#: ⓘ 단추의 이름 (화면 읽기용).
INFO_LABEL = "풀이"

#: 풀이는 옆에 띄운다 — 아래에 띄우면 바로 밑 입력칸을 덮는다.
TIP_SIDE = 'anchor="center right" self="center left" :offset="[8, 0]"'


def tip(text: str) -> None:
    """바로 앞 요소에 마우스를 올리면 뜨는 풀이 (Streamlit ``help=`` 자리 · 마크다운)."""
    with ui.tooltip().classes("k-tip").props(TIP_SIDE):
        ui.markdown(fmt.markdown_safe(text))


def h1(text: str) -> None:
    ui.label(text).classes("k-h1")


def h2(text: str) -> None:
    ui.label(text).classes("k-h2")


def h3(text: str) -> None:
    ui.label(text).classes("k-h3")


def next_step(
    label: str, go: Callable[[], object], *, enabled: bool = True, hint: str = ""
) -> None:
    """단계 맨 아래의 「다음 단계」 단추 — 위로 올라가 탭을 누르지 않아도 된다."""
    with ui.row().classes("w-full justify-end items-center gap-3 k-next"):
        if hint and not enabled:
            ui.label(hint).classes("k-note")
        button = ui.button(f"{label}  →", on_click=go).props("unelevated no-caps size=md")
        if not enabled:
            button.disable()


def info(text: str) -> None:
    """ⓘ — 마우스를 올리면 풀이가 뜨고, Tab 으로 닿아 Enter(또는 누름)로도 연다.

    풀이를 글자에만 걸면 키보드로는 못 연다 — 단추로 세워 초점을 받게 한다.
    """
    button = ui.button("ⓘ").props("flat round dense size=xs").classes("k-info")
    # 화면 읽기 프로그램이 읽는 이름 — 코드 식별자와 한 문자열에 섞지 않는다 (화면 문구 잣대).
    button.props["aria-label"] = INFO_LABEL
    with button:
        tip(text)
        with ui.menu().classes("k-tip p-3"):
            ui.markdown(fmt.markdown_safe(text))


def metric(label: str, value: str, sub: str = "", help_text: str = "") -> None:
    with ui.card().tight().classes("k-card p-4 min-w-[10rem] flex-1"):
        with ui.row().classes("items-center gap-0 no-wrap"):
            ui.label(label).classes("k-label")
            if help_text:
                info(help_text)
        ui.label(value).classes("k-value")
        if sub:
            ui.label(sub).classes("k-sub")


def metrics(cells: list[tuple[str, ...]]) -> None:
    """지표 카드 한 줄. 칸마다 (이름, 값[, 작은 글[, 풀이]])."""
    with ui.row().classes("w-full gap-3 flex-wrap"):
        for cell in cells:
            metric(*cell)


def caption(text: str, help_text: str = "") -> None:
    if not help_text:
        ui.label(text).classes("k-note")
        return
    with ui.row().classes("items-center gap-0"):
        ui.label(text).classes("k-note")
        info(help_text)


def caution(text: str) -> None:
    """주의 — Streamlit ``callout.caution`` 과 같다: 배경 없이 ⚠ 와 굵기로만."""
    body = fmt.markdown_safe(text)
    ui.markdown(f"⚠ {body if '**' in body else f'**{body}**'}").classes("k-caution")


def blocked(text: str) -> None:
    """차단 — 색을 남기는 유일한 등급."""
    ui.markdown(f"⛔ {fmt.markdown_safe(text)}").classes("k-caution text-negative")


def note(text: str) -> None:
    """참고 — 작은 글씨로 흘려 둔다."""
    ui.markdown(f"· {fmt.markdown_safe(text)}").classes("k-caution k-note")


def render_notice(item: Notice) -> None:
    """등급대로 그린다 — 등급 판정은 공용 ``kwise.ui.notices`` 가 한다."""
    if item.severity is Severity.BLOCK:
        blocked(item.text)
    elif item.severity is Severity.WARN:
        caution(item.text)
    else:
        note(item.text)


def notice_block(shown: Iterable[Notice], grounds_from: tuple[Notice, ...]) -> None:
    """본문에는 차단 · 주의만, 근거는 「산출 근거 N건 ⓘ」 하나로 접는다."""
    for item in shown:
        render_notice(item)
    grounds = tooltip(grounds_from)
    if grounds:
        caption(f"산출 근거 {len(grounds)}건", tooltip_text(grounds_from))


def notices(items: tuple[Notice, ...]) -> None:
    """2단계 카드의 안내 (Streamlit ``measures._notices``)."""
    notice_block(screen_notices(items), items)


def overview(text: str) -> None:
    """무엇을 어떻게 개선하는지 두세 줄 — Streamlit 의 인용 꼴(``> ``) 자리."""
    ui.markdown(fmt.markdown_safe(text)).classes("k-overview k-note")


def table(
    frame: pd.DataFrame, *, width: str = "w-full", tips: dict[str, str] | None = None
) -> None:
    """표 하나 — Excel · Streamlit 이 지나는 출구(``display_frame``)를 같이 지난다."""

    def cell(value: object) -> str:
        # 자릿수는 ``display_frame`` 이 접었다 — 여기서는 세 자리 쉼표만 (Streamlit 표와 같다).
        if isinstance(value, float):
            return f"{value:,.0f}" if value.is_integer() else f"{value:,}"
        return "" if pd.isna(value) else str(value)

    folded = display_frame(frame)
    shown = folded.astype(object).map(cell)
    grid = ui.table.from_pandas(shown).props("flat dense bordered").classes(width)
    # 숫자 열은 오른쪽, 글자 열은 왼쪽 — Streamlit 표와 같다 (글로 바꾸기 전의 열 형으로 가른다).
    numeric = {str(name) for name in folded.columns if pd.api.types.is_numeric_dtype(folded[name])}
    for spec in grid.columns:
        spec["align"] = "right" if spec["name"] in numeric else "left"
    grid.update()
    for column, text in (tips or {}).items():  # 열 머리 풀이 (Streamlit 의 열 ``help``)
        grid.add_slot(
            f"header-cell-{column}",
            f'<q-th :props="props">{{{{ props.col.label }}}} ⓘ<q-tooltip>{text}</q-tooltip></q-th>',
        )


def worksheet(sheet: Worksheet) -> None:
    """계산 근거 — 접어 둔다. 표는 공용 ``kwise.report.worksheet`` 가 만든다."""
    if not sheet:
        return
    with ui.expansion("계산 근거").classes("w-full"):
        table(sheet.frame())
