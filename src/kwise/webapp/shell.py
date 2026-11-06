"""화면 틀과 화면 파일 — **디스크의 html 파일을 창이 열지 않는다** (2026-11-06 · 회사 문서 DRM).

회사 PC 는 html · txt · 오피스 파일을 자동으로 잠근다. 창이 ``index.html`` 을 파일로 열면 잠긴
내용을 받아 빈 화면이 된다. 그래서 아주 짧은 틀(:data:`SHELL`)만 글자로 창에 넘기고, 스타일 ·
스크립트는 그 틀이 다리(``js_api``)로 파이썬에게 받아 붙인다 — 파이썬이 읽으므로 이름이 무엇이든
상관없고, 웹서버도 없다. 한 번에 다 글자로 넘기지 않는 까닭: 창이 받는 글자 크기에 한도(약 2MB)가
있고 화면 파일을 다 합치면 그보다 크다.

PPT · Word 를 지을 때 라이브러리가 읽는 빈 서식(``default.pptx`` · ``default.docx``)도 잠긴다 — exe
빌드가 그 둘을 ``.bin`` 이름으로 바꿔 넣고, :func:`use_renamed_templates` 가 그 자리를 읽게 한다.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path
from types import ModuleType
from typing import Any

STATIC = Path(__file__).parent / "static"

#: 붙이는 차례가 곧 불러오는 차례다 — Vue 가 Quasar 보다, 앱이 맨 뒤.
STYLES = ("vendor/material-icons.css", "vendor/quasar.prod.css", "app.css")
SCRIPTS = (
    "vendor/vue.global.prod.js",
    "vendor/quasar.umd.prod.js",
    "vendor/quasar.ko-KR.umd.prod.js",
    "vendor/vega.min.js",
    "vendor/vega-lite.min.js",
    "vendor/vega-embed.min.js",
    "app.js",
)

#: 창에 넘기는 틀. 다리가 서면(``pywebviewready``) 화면 파일을 받아 차례대로 붙인다.
SHELL = """<!doctype html>
<html lang="ko">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>kWise</title></head>
<body>
<div id="app"></div>
<script>
window.addEventListener("pywebviewready", async () => {
  const assets = await window.pywebview.api.shell_assets();
  for (const css of assets.styles) {
    const tag = document.createElement("style");
    tag.textContent = css;
    document.head.appendChild(tag);
  }
  for (const js of assets.scripts) {
    const tag = document.createElement("script");
    tag.textContent = js;
    document.body.appendChild(tag);
  }
});
</script>
</body>
</html>"""

#: CSS 안의 글꼴 자리 — 틀은 파일 주소가 없으므로 글꼴을 글자(data URI)로 박아 넘긴다.
_FONT_URL = re.compile(r"url\('\./(fonts/[^']+\.woff2)'\)")


def _inline_fonts(css: str, folder: Path) -> str:
    def data_uri(match: re.Match[str]) -> str:
        raw = (folder / match.group(1)).read_bytes()
        return f"url(data:font/woff2;base64,{base64.b64encode(raw).decode('ascii')})"

    return _FONT_URL.sub(data_uri, css)


def assets() -> dict[str, list[str]]:
    """스타일 · 스크립트의 글자 — 틀이 이것을 받아 붙인다."""
    # 끝에 파일 이름표를 붙인다 — 글자로 붙인 스크립트 · 스타일도 개발자 도구가 오류를
    # 「app.js 몇째 줄」 로 보인다(화면 동작은 그대로).
    styles = []
    for name in STYLES:
        path = STATIC / name
        css = _inline_fonts(path.read_text(encoding="utf-8"), path.parent)
        styles.append(f"{css}\n/*# sourceURL={name} */")
    scripts = [
        f"{(STATIC / name).read_text(encoding='utf-8')}\n//# sourceURL={name}" for name in SCRIPTS
    ]
    return {"styles": styles, "scripts": scripts}


def _redirect(module: ModuleType, attr: str, renamed: str) -> None:
    original: Any = getattr(module, attr)
    path = Path(original())
    moved = path.with_name(renamed)
    if not path.exists() and moved.exists():
        setattr(module, attr, lambda: str(moved))


#: exe 빌드가 서식 파일에 붙이는 이름 — 오피스 확장자가 아니어서 DRM 이 잠그지 않는다.
PPTX_TEMPLATE = "default_pptx.bin"
DOCX_TEMPLATE = "default_docx.bin"


def use_renamed_templates() -> None:
    """빌드가 서식 이름을 바꿨으면 라이브러리가 그 자리를 읽게 한다 (저장소에서는 그대로)."""
    import docx.api
    import pptx.api

    _redirect(pptx.api, "_default_pptx_path", PPTX_TEMPLATE)
    _redirect(docx.api, "_default_docx_path", DOCX_TEMPLATE)
