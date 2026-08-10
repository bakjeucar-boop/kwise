"""Streamlit 화면 (요구사항서 10.1·10.2). **계산 로직을 담지 않는다.**

    .venv\\Scripts\\streamlit.exe run src\\kwise\\ui\\app.py

모듈 갈래는 Streamlit 의존 여부로 가른다 — 앞엣것은 테스트가 그대로 닿는다.

    순수      anchors · text · spec · charts · rules_view · pipeline · session
    Streamlit  app · state · cache · views\\

``app`` 은 import 하는 것만으로 화면을 그리므로 여기서 끌어오지 않는다.
"""

from kwise.ui import anchors, charts, pipeline, rules_view, session, spec, text

__all__ = ["anchors", "charts", "pipeline", "rules_view", "session", "spec", "text"]
