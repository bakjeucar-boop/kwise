"""화면 셋 + 기준 데이터 관리 (요구사항서 10.1).

Streamlit 의 ``pages\\`` 자동 인식과 겹치지 않도록 ``views\\`` 로 둔다 —
화면 순서를 요구사항서가 정하므로 파일 이름순 자동 배치를 쓰지 않는다.
"""

from kwise.ui.views import compare, diagnose, measures, rules_admin

__all__ = ["compare", "diagnose", "measures", "rules_admin"]
