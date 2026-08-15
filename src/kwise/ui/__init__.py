"""Streamlit 화면 (요구사항서 10.1·10.2). **계산 로직을 담지 않는다.**

    .venv\\Scripts\\streamlit.exe run streamlit_app.py

모듈 갈래는 Streamlit 의존 여부로 가른다 — 앞엣것은 테스트가 그대로 닿는다.

    순수      anchors · text · spec · charts · rules_view · pipeline · session
    Streamlit  app · state · cache · views\\

``app`` 을 여기서 끌어오지 않는 것은 **Streamlit 을 딸려 들이지 않기 위해서다.**
import 만으로 화면을 그리지는 않는다 — 그리는 일은 ``app.main()`` 안에 있고,
뿌리 진입점이 매 실행에 그것을 부른다 (2026-08-14 배포 결함).
"""

from kwise.ui import anchors, charts, pipeline, rules_view, session, spec, text

__all__ = ["anchors", "charts", "pipeline", "rules_view", "session", "spec", "text"]
