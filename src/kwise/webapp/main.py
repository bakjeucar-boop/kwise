"""창을 띄운다 — **웹서버를 켜지 않고, 디스크의 html 파일도 열지 않는다** (``shell`` 참고)."""

from __future__ import annotations

import webview

from .api import Api
from .shell import SHELL, use_renamed_templates


def main(*, debug: bool = False) -> None:
    use_renamed_templates()
    webview.create_window(
        "kWise",
        html=SHELL,
        js_api=Api(),
        width=1280,
        height=900,
        min_size=(960, 640),
    )
    # http_server=False — pywebview 가 로컬 HTTP 서버를 띄우지 않는다. 화면 틀은 글자로 넘긴다.
    webview.start(http_server=False, debug=debug, private_mode=True)
