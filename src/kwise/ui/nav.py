"""화면 배치 (요구사항서 10.7 · 16세션 1절).

**세 화면을 탭으로 한 번에 그린다.** 옆단에서 하나를 골라 그 화면만 그리던
방식은 세 가지를 한꺼번에 망가뜨렸다.

    ① 3단계가 2단계 방문에 기댄다   그리지 않은 화면의 위젯은 세션에서 사라진다
    ② 내려받기가 결과를 지운다      rerun 때 2단계가 없으니 켠 수단도 없어진다
    ③ 오가며 값이 날아간다          같은 이유다

탭은 **한 번의 실행에서 셋을 모두 그린다.** 2단계 위젯이 항상 살아 있으므로
3단계와 산출물이 같은 값을 본다 — 배선이 아니라 구조가 지켜 준다.

    [ 1단계 · 진단 ] [ 2단계 · 개선 수단 ] [ 3단계 · 개선안 조합 ]

**진행할 수 없는 탭도 막지 않는다.** 눌러 보고 무엇이 없는지 읽는 편이,
눌리지 않는 이유를 짐작하는 편보다 낫다 — 탭 안에 안내만 둔다.

**기준 데이터는 단계가 아니라 설정이다.** 탭에 넣지 않고 옆단 하단의 별도
진입점으로 남긴다.
"""

from __future__ import annotations

import streamlit as st

__all__ = [
    "ANALYSIS_PAGE",
    "PAGES",
    "PAGE_KEY",
    "RULES_PAGE",
    "TABS",
    "TAB_KEY",
    "current_page",
    "go_to",
    "render_settings_entry",
]

PAGE_KEY = "nav_page"
ANALYSIS_PAGE = "분석"
RULES_PAGE = "기준 데이터"
PAGES: tuple[str, ...] = (ANALYSIS_PAGE, RULES_PAGE)

# 탭 이름. **번호를 남긴다** — 진단 → 수단 → 조합이 한 줄기라는 사실이 이름에서
# 읽혀야 한다. 3단계는 「비교」가 아니라 「개선안 조합」이다 (16세션 5절).
TAB_KEY = "nav_tab"
TABS: tuple[str, ...] = ("1단계 · 진단", "2단계 · 개선 수단", "3단계 · 개선안 조합")


def current_page() -> str:
    """지금 그릴 것 — 분석(탭 셋)인가 기준 데이터인가."""
    value = st.session_state.get(PAGE_KEY)
    return value if isinstance(value, str) and value in PAGES else ANALYSIS_PAGE


def go_to(page: str) -> None:
    """옆단 진입점이 쓰는 콜백. **위젯 키가 아닌 일반 세션 키**에 쓴다."""
    if page not in PAGES:
        raise KeyError(f"등록되지 않은 화면입니다: {page!r}")
    st.session_state[PAGE_KEY] = page


# 옆단 단추 모양은 **한 곳에 모은다.** 흩어 놓으면 고칠 때 하나를 빠뜨린다.
_STYLE = """
<style>
[data-testid="stSidebar"] div.stButton > button {
    width: 100%;
    justify-content: flex-start;
    text-align: left;
    border: none;
    background: transparent;
    padding: 0.30rem 0.55rem;
    font-weight: 400;
}
[data-testid="stSidebar"] div.stButton > button[kind="primary"] {
    background: rgba(120, 160, 220, 0.22);
    font-weight: 700;
}
</style>
"""


def render_settings_entry() -> str:
    """옆단 하단의 설정 진입점. 고른 화면 이름을 돌려준다.

    **번호 배지를 붙이지 않는다** — 붙이면 "네 번째 단계" 로 읽힌다.
    """
    st.sidebar.markdown(_STYLE, unsafe_allow_html=True)
    page = current_page()
    st.sidebar.caption("설정")
    st.sidebar.button(
        RULES_PAGE,
        key="nav_rules",
        type="primary" if page == RULES_PAGE else "secondary",
        width="stretch",
        on_click=go_to,
        args=(RULES_PAGE,),
    )
    st.sidebar.button(
        "분석으로 돌아가기",
        key="nav_analysis",
        type="primary" if page == ANALYSIS_PAGE else "secondary",
        width="stretch",
        on_click=go_to,
        args=(ANALYSIS_PAGE,),
    )
    return current_page()
