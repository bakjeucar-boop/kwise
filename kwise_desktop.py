"""데스크톱 화면 진입점 (exe 도 이 파일로 묶는다). Streamlit 화면은 ``streamlit_app.py`` 그대로다.

띄우기: ``.venv\\Scripts\\python.exe kwise_desktop.py``
exe: ``.venv\\Scripts\\python.exe tools\\build_desktop.py`` → ``output\\desktop\\kwise\\kwise.exe``

**창 안쪽은 이 PC 안(127.0.0.1)의 작은 서버에 붙는다** — NiceGUI 가 그렇게 돈다. 회사 망에서
막히는 자리를 줄이려고 둘을 해 둔다: 창(WebView2)은 프록시를 타지 않게 하고, 시작하다 터지면
조용히 닫히지 않고 사유를 창으로 띄운다(로그는 ``%LOCALAPPDATA%\\kwise\\desktop.log``).
"""

from __future__ import annotations

import multiprocessing
import os
import sys
import traceback
from pathlib import Path

# src 레이아웃 — ``streamlit_app.py`` 와 같은 까닭으로 경로를 넣는다.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


def _fatal(message: str) -> None:
    """콘솔 없는 exe 가 시작하다 터지면 사유를 창으로 — 아무 말 없이 닫히면 원인을 모른다."""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "kWise — 시작하지 못했습니다", 0x10)
    except Exception:  # 창을 못 띄워도 로그에는 남았다
        pass


if __name__ in {"__main__", "__mp_main__"}:
    # 창(pywebview)은 다른 프로세스로 뜬다 — exe 에서는 이 줄이 없으면 앱이 자기를 거듭 띄운다.
    multiprocessing.freeze_support()
    # 캐시는 사용자 폴더로 — exe 가 놓인 폴더는 쓰기 권한이 없을 수 있다. 정해 둔 값은 따른다.
    home = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "kwise"
    home.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("PROJECT_CACHE", str(home / "cache"))
    # 창 안쪽 브라우저(WebView2)는 이 PC 안의 서버만 본다 — 회사 프록시가 127.0.0.1 까지
    # 가로채면 빈 창이 된다. 창만 프록시를 끈다(기상 자료 받기는 파이썬 쪽이라 그대로다).
    os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--no-proxy-server")
    for name in ("NO_PROXY", "no_proxy"):
        current = os.environ.get(name, "")
        if "127.0.0.1" not in current:
            os.environ[name] = ",".join(filter(None, (current, "127.0.0.1", "localhost")))
    # 콘솔 없는 exe 는 출력 자리가 None 이라 로그를 쓰다 죽는다 — 파일로 받는다.
    if sys.stdout is None or sys.stderr is None:
        log = (home / "desktop.log").open("a", encoding="utf-8")
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
    try:
        from kwise.desktop.app import main

        main()
    except SystemExit:
        raise
    except Exception:
        detail = traceback.format_exc()
        print(detail, file=sys.stderr, flush=True)
        if __name__ == "__main__":
            _fatal(
                "kWise 를 시작하지 못했습니다.\n\n"
                f"{detail.strip().splitlines()[-1]}\n\n"
                f"자세한 기록: {home / 'desktop.log'}"
            )
        raise
