"""공용 Altair 차트를 NiceGUI 화면에 그린다 — Vega 스크립트는 ``static\\`` 에 넣어 둔 것을 쓴다.

``ui.altair`` 를 쓰지 않는 까닭: 그 요소는 ``anywidget`` 이 따로 필요하고 Vega 를
esm.sh 에서 받아 와 **오프라인 exe 에서 그래프가 안 뜬다.**
"""

from __future__ import annotations

import json
from pathlib import Path

import altair as alt
from nicegui import app, ui

STATIC = Path(__file__).parent / "static"

#: Streamlit 은 차트에 제 테마를 덧씌운다. 다 흉내 내지 않고 글꼴과 글자색만 맞춘다 —
#: 회색 글자는 밝은 배경과 어두운 배경 양쪽에서 읽힌다.
_CONFIG = {
    "background": None,
    "font": "system-ui, sans-serif",
    "view": {"stroke": None},
    "axis": {
        "labelColor": "#8e8e93",
        "titleColor": "#8e8e93",
        "domain": False,
        "gridColor": "#8e8e9333",
        "tickColor": "#8e8e9333",
    },
    "legend": {"labelColor": "#8e8e93", "titleColor": "#8e8e93"},
    "title": {"color": "#8e8e93"},
    "header": {"labelColor": "#8e8e93", "titleColor": "#8e8e93"},
}


def _iso(value: object) -> str:
    """Altair 가 남긴 날짜(``datetime.date``)를 Vega 가 읽는 ISO 글로 — Streamlit 이 하는 일."""
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    raise TypeError(f"{type(value).__name__} 를 JSON 으로 못 바꿉니다")


def setup() -> None:
    """앱 시작 때 한 번 — 정적 파일 경로와 스크립트 태그를 붙인다."""
    app.add_static_files("/vega", STATIC)
    ui.add_head_html(
        "".join(
            f'<script src="/vega/{name}"></script>'
            for name in ("vega.min.js", "vega-lite.min.js", "vega-embed.min.js")
        ),
        shared=True,
    )


def chart(spec: alt.TopLevelMixin) -> ui.element:
    """차트 한 장. 폭은 담는 칸을 따른다 (Streamlit 의 ``width="stretch"`` 와 같은 뜻)."""
    data = spec.to_dict()
    # 층·이어붙이기 차트는 폭 "container" 를 못 받는다 — 단일 차트에만 준다.
    if "width" not in data and not any(k in data for k in ("hconcat", "vconcat", "concat")):
        data["width"] = "container"
    data["config"] = {**_CONFIG, **data.get("config", {})}
    box = ui.element("div").classes("w-full")
    # 칸이 화면에 붙기 전에 스크립트가 먼저 돌 수 있다 — 붙을 때까지 기다렸다 그린다.
    ui.run_javascript(
        f"(function draw() {{ const el = document.getElementById('{box.html_id}');"
        " if (!el) return setTimeout(draw, 30);"
        f" vegaEmbed(el, {json.dumps(data, ensure_ascii=False, default=_iso)},"
        " {actions: false, renderer: 'svg'}).catch(e => console.error('vega', e)); })()"
    )
    return box
