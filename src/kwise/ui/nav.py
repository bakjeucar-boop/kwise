"""화면 이동 (요구사항서 10.7).

**옆단 라디오만으로는 흐름이 보이지 않는다.** 단계 하단에 다음 단계로 가는
단추를 두어 진단 → 수단 → 비교가 한 줄기로 읽히게 한다.

옆단 라디오와 **같은 세션 키**를 쓴다. 두 벌을 두면 단추로 옮긴 뒤 옆단 표시가
어긋난다.
"""

from __future__ import annotations

import streamlit as st

__all__ = ["PAGES", "PAGE_KEY", "current_page", "go_to", "next_step_button"]

PAGE_KEY = "nav_page"
PAGES: tuple[str, ...] = ("1단계 · 진단", "2단계 · 개선 수단", "3단계 · 비교", "기준 데이터")


def current_page() -> str:
    value = st.session_state.get(PAGE_KEY)
    return value if isinstance(value, str) and value in PAGES else PAGES[0]


def go_to(page: str) -> None:
    """옆단 선택을 바꾼다.

    **콜백에서만 부른다.** 라디오가 그려진 뒤에 같은 키를 건드리면 Streamlit 이
    ``cannot be modified after the widget ... was instantiated`` 로 막는다.
    콜백은 다음 실행 전에 돌아 그 제약을 받지 않는다.
    """
    if page not in PAGES:
        raise KeyError(f"등록되지 않은 화면입니다: {page!r}")
    st.session_state[PAGE_KEY] = page


def next_step_button(page: str, *, key: str, label: str | None = None) -> None:
    """단계 하단의 이동 단추. ``2단계 · 개선 수단으로 →`` 꼴."""
    st.divider()
    st.button(
        label or f"{page}으로 →",
        type="primary",
        key=key,
        width="stretch",
        on_click=go_to,
        args=(page,),
    )
