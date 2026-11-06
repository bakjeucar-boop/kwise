"""데스크톱 화면 진입점 (exe 도 이 파일로 묶는다). Streamlit 화면은 ``streamlit_app.py`` 그대로다.

띄우기: ``.venv\\Scripts\\python.exe kwise_desktop.py``
exe: ``.venv\\Scripts\\python.exe tools\\build_desktop.py`` → ``dist\\kwise\\kwise.exe``
"""

from __future__ import annotations

import multiprocessing
import os
import sys
from pathlib import Path

# src 레이아웃 — ``streamlit_app.py`` 와 같은 까닭으로 경로를 넣는다.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from kwise.desktop.app import main

if __name__ in {"__main__", "__mp_main__"}:
    # 창(pywebview)은 다른 프로세스로 뜬다 — exe 에서는 이 줄이 없으면 앱이 자기를 거듭 띄운다.
    multiprocessing.freeze_support()
    # 캐시는 사용자 폴더로 — exe 가 놓인 폴더는 쓰기 권한이 없을 수 있다. 정해 둔 값은 따른다.
    home = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "kwise"
    os.environ.setdefault("PROJECT_CACHE", str(home / "cache"))
    # 콘솔 없는 exe 는 출력 자리가 None 이라 로그를 쓰다 죽는다 — 파일로 받는다.
    if sys.stdout is None or sys.stderr is None:
        home.mkdir(parents=True, exist_ok=True)
        log = (home / "desktop.log").open("a", encoding="utf-8")
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
    main()
