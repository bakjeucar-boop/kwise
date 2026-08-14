"""Streamlit Cloud 진입점.

**저장소 뿌리에 둔다.** 이 프로젝트는 src 레이아웃이라 ``src`` 를 경로에 넣어야
``kwise`` 를 찾는다. 로컬에서는 ``pip install -e .`` 로 해결하지만, Streamlit
Cloud 는 ``requirements.txt`` 만 설치하므로 여기서 직접 넣는다.

    streamlit run streamlit_app.py

**매 실행에 ``main()`` 을 부른다.** Streamlit 은 조작할 때마다 이 파일을 처음부터
다시 실행하는데, ``import`` 는 두 번째부터 아무 일도 하지 않는다 — 파이썬이 이미
불러온 모듈을 다시 실행하지 않기 때문이다. 그리는 일을 import 에 맡기면 첫 화면만
나오고 그 뒤로는 빈 화면이 된다 (2026-08-14).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

# 경로를 넣은 뒤에야 찾을 수 있어 import 가 파일 위쪽이 아니다.
from kwise.ui.app import main

main()
