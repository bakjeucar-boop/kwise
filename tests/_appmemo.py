"""화면 시험이 ESS 정밀화를 나눠 쓰는 장치 (43세션).

``refine_ess_target`` 은 한 점당 요금을 다시 계산해 **한 번에 약 11초**다
(40세션). 실제 앱은 세션 기억(:mod:`kwise.ui.memo`)에 담아 두어 사용자가 한 번만
낸다. 그런데 ``AppTest`` 는 띄울 때마다 세션이 새로 나므로, 시험은 같은 값을
띄울 때마다 다시 냈다 — ``test_integration`` 31회, ``test_ui_screen`` 45회.

**``ess_optimum`` 만 나눠 쓴다.** ``refine_ess_target`` 은 기상을 받지 않으므로
``real_weather`` 표식이 붙은 시험과 섞여도 값이 갈라지지 않는다.
``solar``·``compare`` 는 열쇠에 기상이 안 들어가 있어 **나눠 쓰면 안 된다** —
격리된 기상과 사전 취득분이 같은 열쇠를 쓰게 된다.

기억은 계산 결과일 뿐이라 시험 사이에 값이 새지 않는다. 씨를 뿌리지 않아도
각 시험은 그대로 통과한다 — 다만 느리다.
"""

from __future__ import annotations

from typing import Any

from streamlit.testing.v1 import AppTest

from kwise.ui.memo import MEMO_KEY

__all__ = ["harvest_ess_memo", "seed_ess_memo"]

_PREFIX = "ess_optimum|"
_shared: dict[str, Any] = {}


def seed_ess_memo(running: AppTest) -> AppTest:
    """앞선 시험이 낸 ESS 정밀화 결과를 심는다. ``run()`` 전에 부른다."""
    if _shared:
        running.session_state[MEMO_KEY] = dict(_shared)
    return running


def harvest_ess_memo(finished: AppTest) -> AppTest:
    """이번 시험이 새로 낸 것을 거둬 둔다. ``run()`` 뒤에 부른다."""
    try:
        store = finished.session_state[MEMO_KEY]
    except (KeyError, AttributeError):
        return finished
    for key, value in dict(store).items():
        if key.startswith(_PREFIX):
            _shared[key] = value
    return finished
