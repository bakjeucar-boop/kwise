"""웹서버 없는 데스크톱 화면 진입점.

띄우기: ``.venv\\Scripts\\python.exe kwise_webapp.py``
exe: ``.venv\\Scripts\\python.exe tools\\build_webapp.py`` → ``output\\webapp\\kwise\\kwise.exe``
"""

from __future__ import annotations

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


if __name__ == "__main__":
    # 캐시 · 로그는 사용자 폴더로 — exe 가 놓인 폴더는 쓰기 권한이 없을 수 있다.
    home = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "kwise"
    home.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("PROJECT_CACHE", str(home / "cache"))
    if sys.stdout is None or sys.stderr is None:  # 콘솔 없는 exe
        log = (home / "webapp.log").open("a", encoding="utf-8")
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
    try:
        from kwise.webapp.main import drm_check, main

        if "--drm-check" in sys.argv:  # 회사 PC 에서 html 이 잠기는지 보는 쪽 (``shell``)
            drm_check()
        else:
            main()
    except Exception:
        detail = traceback.format_exc()
        print(detail, file=sys.stderr, flush=True)
        _fatal(
            "kWise 를 시작하지 못했습니다.\n\n"
            f"{detail.strip().splitlines()[-1]}\n\n"
            f"자세한 기록: {home / 'webapp.log'}"
        )
        raise
