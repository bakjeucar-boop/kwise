"""창을 띄운다 — **웹서버를 켜지 않는다** (``http_server=False`` · 화면은 ``file://`` 로 연다)."""

from __future__ import annotations

from pathlib import Path

import webview

from .api import Api

STATIC = Path(__file__).parent / "static"


def main(*, debug: bool = False) -> None:
    webview.create_window(
        "kWise",
        url=(STATIC / "index.html").as_uri(),
        js_api=Api(),
        width=1280,
        height=900,
        min_size=(960, 640),
    )
    # http_server=False — pywebview 가 로컬 HTTP 서버를 띄우지 않고 파일을 그대로 연다.
    webview.start(http_server=False, debug=debug, private_mode=True)
