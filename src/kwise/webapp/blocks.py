"""화면 조각을 **사전으로 적는다** — 화면(Vue)이 그 목록을 그대로 그린다.

NiceGUI 판의 ``widgets`` 와 같은 이름 · 같은 뜻의 함수들이다. 다른 것은 그리지 않고 적는다는 것
하나다 — 그래서 결과를 놓는 파이썬 코드를 거의 그대로 옮겨 쓸 수 있다.

    with collect() as page:      # 이 안에서 부른 조각이 page 에 쌓인다
        h2("피크 특성")
        metrics([("최대수요", "265 kW")])
        chart(charts.monthly_peak_chart(peak))
    return page                  # list[dict] — 다리를 건너 화면으로 간다

접힘 · 칸 나누기는 ``with expansion(...)`` · ``with columns(2) as cols`` 로 안을 채운다.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterable, Iterator
from contextvars import ContextVar
from typing import Any

import altair as alt
import pandas as pd

from kwise.notices import Notice, tooltip
from kwise.report.columns import display_frame
from kwise.report.worksheet import Worksheet
from kwise.ui import text as fmt
from kwise.ui.notices import Severity, screen_notices, tooltip_text

Block = dict[str, Any]

_CURRENT: ContextVar[list[Block] | None] = ContextVar("kwise_blocks", default=None)


@contextlib.contextmanager
def collect() -> Iterator[list[Block]]:
    """이 안에서 부른 조각을 한 목록에 모은다."""
    page: list[Block] = []
    token = _CURRENT.set(page)
    try:
        yield page
    finally:
        _CURRENT.reset(token)


def _add(block: Block) -> Block:
    page = _CURRENT.get()
    if page is None:
        raise RuntimeError("collect() 밖에서 화면 조각을 적었습니다")
    page.append(block)
    return block


@contextlib.contextmanager
def _inside(block: Block, key: str = "children") -> Iterator[None]:
    """조각 하나 안쪽에 쌓는다 — 접힘 · 카드 · 칸."""
    inner: list[Block] = []
    block[key] = inner
    token = _CURRENT.set(inner)
    try:
        yield
    finally:
        _CURRENT.reset(token)


# --------------------------------------------------------------------- 글


def h1(text: str) -> None:
    _add({"type": "h1", "text": text})


def h2(text: str) -> None:
    _add({"type": "h2", "text": text})


def h3(text: str) -> None:
    _add({"type": "h3", "text": text})


def text(body: str) -> None:
    """본문 한 줄 — 굵게(``**``)를 그린다."""
    _add({"type": "md", "text": fmt.markdown_safe(body)})


def caption(body: str, help_text: str = "") -> None:
    _add(
        {"type": "caption", "text": body, "help": fmt.markdown_safe(help_text) if help_text else ""}
    )


def caution(body: str) -> None:
    """주의 — 배경 없이 ⚠ 와 굵기로만 (Streamlit ``callout.caution`` 과 같다)."""
    safe = fmt.markdown_safe(body)
    _add({"type": "caution", "text": safe if "**" in safe else f"**{safe}**"})


def blocked(body: str) -> None:
    """차단 — 색을 남기는 유일한 등급."""
    _add({"type": "blocked", "text": fmt.markdown_safe(body)})


def note(body: str) -> None:
    """참고 — 작은 글씨로 흘려 둔다."""
    _add({"type": "note", "text": fmt.markdown_safe(body)})


def overview(body: str) -> None:
    """무엇을 어떻게 개선하는지 — 왼쪽 막대가 선 두세 줄."""
    _add({"type": "overview", "text": fmt.markdown_safe(body)})


# --------------------------------------------------------------------- 안내 · 지표


def render_notice(item: Notice) -> None:
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
    notice_block(screen_notices(items), items)


def metrics(cells: list[tuple[str, ...]]) -> None:
    """지표 카드 한 줄. 칸마다 (이름, 값[, 작은 글[, 풀이]])."""
    items = []
    for cell in cells:
        label, value, *rest = cell
        sub = rest[0] if rest else ""
        help_text = rest[1] if len(rest) > 1 else ""
        items.append(
            {
                "label": label,
                "value": value,
                "sub": sub,
                "help": fmt.markdown_safe(help_text) if help_text else "",
            }
        )
    _add({"type": "metrics", "items": items})


# --------------------------------------------------------------------- 표 · 그림


def _cell(value: object) -> str:
    # 자릿수는 ``display_frame`` 이 접었다 — 여기서는 세 자리 쉼표만 (Streamlit 표와 같다).
    if isinstance(value, float):
        return f"{value:,.0f}" if value.is_integer() else f"{value:,}"
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value)


def table(
    frame: pd.DataFrame, *, tips: dict[str, str] | None = None, compact: bool = False
) -> None:
    """표 하나 — Excel · Streamlit 이 지나는 출구(``display_frame``)를 같이 지난다."""
    folded = display_frame(frame)
    numeric = {str(n) for n in folded.columns if pd.api.types.is_numeric_dtype(folded[n])}
    columns = [
        {
            "name": str(name),
            "label": str(name),
            "field": str(name),
            "align": "right" if str(name) in numeric else "left",
            "help": (tips or {}).get(str(name), ""),
        }
        for name in folded.columns
    ]
    rows = [
        {str(name): _cell(value) for name, value in row.items()}
        for row in folded.astype(object).to_dict(orient="records")
    ]
    _add({"type": "table", "columns": columns, "rows": rows, "compact": compact})


def _iso(value: object) -> str:
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def spec(chart: alt.TopLevelMixin) -> dict[str, Any]:
    """Altair 차트를 화면이 그릴 Vega-Lite 사양으로 — 폭은 담는 칸을 따른다."""
    data: dict[str, Any] = chart.to_dict()
    if "width" not in data and not any(k in data for k in ("hconcat", "vconcat", "concat")):
        data["width"] = "container"
    # 날짜 같은 값은 글로 — 다리가 JSON 으로 못 바꾸면 그림이 통째로 안 온다.
    return json.loads(json.dumps(data, ensure_ascii=False, default=_iso))


def chart(altair_chart: alt.TopLevelMixin) -> None:
    _add({"type": "chart", "spec": spec(altair_chart)})


def chart_choices(options: list[tuple[str, alt.TopLevelMixin]], caption_text: str = "") -> None:
    """갈래를 눌러 바꾸는 그림 — 계절 갈래 같은 것. 갈래가 하나면 단추 없이 그림만."""
    _add(
        {
            "type": "chart_choices",
            "options": [{"label": label, "spec": spec(c)} for label, c in options],
            "caption": caption_text,
        }
    )


def worksheet(sheet: Worksheet) -> None:
    """계산 근거 — 접어 둔다. 표는 공용 ``kwise.report.worksheet`` 가 만든다."""
    if not sheet:
        return
    with expansion("계산 근거"):
        table(sheet.frame())


# --------------------------------------------------------------------- 묶음


@contextlib.contextmanager
def expansion(title: str, *, opened: bool = False) -> Iterator[None]:
    block = _add({"type": "expansion", "title": title, "opened": opened})
    with _inside(block):
        yield


@contextlib.contextmanager
def card() -> Iterator[None]:
    block = _add({"type": "card"})
    with _inside(block):
        yield


@contextlib.contextmanager
def dim(on: bool) -> Iterator[None]:
    """흐린 묶음 — 묵은 결과. ``on`` 이 거짓이면 그냥 묶음이다."""
    block = _add({"type": "group", "dim": on})
    with _inside(block):
        yield


class _Columns:
    def __init__(self, block: Block, count: int) -> None:
        self._block = block
        self._block["columns"] = [[] for _ in range(count)]

    @contextlib.contextmanager
    def column(self, index: int) -> Iterator[None]:
        token = _CURRENT.set(self._block["columns"][index])
        try:
            yield
        finally:
            _CURRENT.reset(token)


@contextlib.contextmanager
def columns(count: int) -> Iterator[_Columns]:
    block = _add({"type": "columns"})
    yield _Columns(block, count)
