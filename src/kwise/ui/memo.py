"""세션 안에서만 사는 계산 기억 (요구사항서 10.2·10.6).

**진행을 보여 주는 계산은 ``st.cache_data`` 로 감쌀 수 없다.** 캐시된 함수 안에서
바깥에 만든 상자(``st.status``)에 글을 쓰면 Streamlit 이 그 호출을 기록했다가
다시 재생하려다 실패한다 (``CacheReplayClosureError``). 그렇다고 진행 표시를
포기하면 가장 오래 걸리는 구간이 통째로 말없이 멈춘다.

그래서 그 셋(태양광 곡선·조합 비교·감도)만 **세션 상태에 직접 기억한다.**

    st.cache_data   프로세스 전역. 동시 접속자가 공유한다
    session_memo    세션 하나. **다른 사용자와 섞이지 않는다**

부수 효과로 격리가 좋아진다 — 무거운 결과가 세션 밖으로 나가지 않는다.
:func:`kwise.ui.cache.clear_calc_cache` 가 이쪽도 함께 비운다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import streamlit as st

__all__ = ["MEMO_KEY", "clear_memo", "memo_size", "session_memo"]

MEMO_KEY = "_kwise_memo"
# 세션 하나가 들고 있을 결과 수. 부하 시계열이 딸려 오므로 무한정 쌓지 않는다.
_MAX_ENTRIES = 8


def _store() -> dict[str, Any]:
    store = st.session_state.get(MEMO_KEY)
    if not isinstance(store, dict):
        store = {}
        st.session_state[MEMO_KEY] = store
    return store


def session_memo[T](key: str, build: Callable[[], T]) -> T:
    """``key`` 로 기억한다. 없으면 ``build()`` 를 부른다.

    **``build`` 를 부르는 동안 진행 표시가 살아 있다** — 캐시 재생이 없으므로
    바깥 상자에 그대로 쓸 수 있다.
    """
    store = _store()
    if key in store:
        return store[key]
    value = build()
    if len(store) >= _MAX_ENTRIES:
        # 가장 오래된 것부터 버린다. 시계열이 딸려 오므로 메모리를 쥔다.
        store.pop(next(iter(store)))
    store[key] = value
    return value


def clear_memo() -> None:
    """세션 기억을 비운다. **기준 데이터를 고치면 함께 비운다.**"""
    st.session_state[MEMO_KEY] = {}


def memo_size() -> int:
    return len(_store())
