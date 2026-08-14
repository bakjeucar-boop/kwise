"""Streamlit Cloud 진입점.

**저장소 뿌리에 둔다.** 이 프로젝트는 src 레이아웃이라 ``src`` 를 경로에 넣어야
``kwise`` 를 찾는다. 로컬에서는 ``pip install -e .`` 로 해결하지만, Streamlit
Cloud 는 ``requirements.txt`` 만 설치하므로 여기서 직접 넣는다.

    streamlit run streamlit_app.py

화면은 :mod:`kwise.ui.app` 이 모듈 최상위에서 ``main()`` 을 부르므로 **import 가
곧 실행**이다. 여기서 다시 부르지 않는다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import kwise.ui.app  # noqa: E402,F401  — import 가 화면을 그린다
