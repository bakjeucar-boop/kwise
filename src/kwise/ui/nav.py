"""화면 이동과 단계 진행 표시 (요구사항서 10.7 · 15세션 3절).

**라디오 버튼은 「고르는 것」으로 보인다.** 진단 → 수단 → 비교가 한 줄기라는
사실이 눈에 들어오지 않고, 지금 어디이며 무엇을 마쳤는지도 알 수 없다.
옆단을 **단계 진행 표시**로 바꾼다.

    ── 분석 ──────────────
      ① 진단          ✓ 완료
      ② 개선 수단     ● 현재
      ③ 조합 비교     ○ 대기
    ── 설정 ──────────────
      기준 데이터

**기준 데이터는 단계가 아니라 설정이다.** 구분선 아래 별도 묶음에 두고 번호
배지를 붙이지 않는다 — 번호를 붙이면 "네 번째 단계" 로 읽힌다.

**옆단과 단계 하단 단추가 같은 세션 키를 쓴다.** 두 벌을 두면 단추로 옮긴 뒤
옆단 표시가 어긋난다 (12세션에 겪었다). 여기서는 위젯 키가 아니라 **일반 세션
키**에 쓰므로 콜백 밖에서도 안전하다 — 라디오였을 때의 제약이 사라졌다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import streamlit as st

__all__ = [
    "PAGES",
    "PAGE_KEY",
    "SETTINGS_PAGES",
    "STEP_PAGES",
    "StepState",
    "current_page",
    "go_to",
    "next_step_button",
    "render_sidebar",
    "step_states",
]

PAGE_KEY = "nav_page"
PAGES: tuple[str, ...] = ("1단계 · 진단", "2단계 · 개선 수단", "3단계 · 비교", "기준 데이터")
STEP_PAGES: tuple[str, ...] = PAGES[:3]
SETTINGS_PAGES: tuple[str, ...] = PAGES[3:]

# 배지·짧은 이름. 옆단이 좁아 "1단계 · 진단" 을 그대로 쓰면 두 줄이 된다.
_BADGES: tuple[str, ...] = ("①", "②", "③")
_SHORT: dict[str, str] = {
    PAGES[0]: "진단",
    PAGES[1]: "개선 수단",
    PAGES[2]: "조합 비교",
}
_VISITED = "_kwise_visited"


class StepState(Enum):
    """단계 상태. 값이 화면 표기다."""

    DONE = "완료"
    CURRENT = "현재"
    WAITING = "대기"
    LOCKED = "진행 불가"

    def __str__(self) -> str:
        return self.value

    @property
    def mark(self) -> str:
        return {"완료": "✓", "현재": "●", "대기": "○", "진행 불가": "○"}[self.value]

    @property
    def clickable(self) -> bool:
        """**진행할 수 없는 단계는 누르지 못하게 둔다.** 눌러도 빈 화면이 나온다."""
        return self is not StepState.LOCKED


@dataclass(frozen=True)
class Step:
    """옆단 한 줄."""

    page: str
    badge: str
    label: str
    state: StepState

    @property
    def text(self) -> str:
        return f"{self.badge} {self.label}"


def current_page() -> str:
    value = st.session_state.get(PAGE_KEY)
    return value if isinstance(value, str) and value in PAGES else PAGES[0]


def go_to(page: str) -> None:
    """옆단 선택을 바꾼다. **단추와 옆단이 같은 키를 쓴다.**"""
    if page not in PAGES:
        raise KeyError(f"등록되지 않은 화면입니다: {page!r}")
    st.session_state[PAGE_KEY] = page
    visited = set(st.session_state.get(_VISITED, ()))
    visited.add(page)
    st.session_state[_VISITED] = tuple(visited)


def step_states(*, ready: bool, measures_on: bool) -> tuple[Step, ...]:
    """단계별 상태 (15세션 3절). **순수 판정** — Streamlit 세션만 읽는다.

    Args:
        ready: 데이터 업로드와 계약 정보 입력이 끝났는가. 1단계 완료 조건이자
            2·3단계 진입 조건이다.
        measures_on: 수단을 하나 이상 켰는가. 2단계 완료 조건이다.
    """
    page = current_page()
    visited = set(st.session_state.get(_VISITED, ()))
    done = {
        PAGES[0]: ready,
        PAGES[1]: measures_on,
        PAGES[2]: PAGES[2] in visited,
    }
    steps: list[Step] = []
    for index, name in enumerate(STEP_PAGES):
        if not ready and index > 0:
            state = StepState.LOCKED
        elif name == page:
            state = StepState.CURRENT
        elif done[name]:
            state = StepState.DONE
        else:
            state = StepState.WAITING
        steps.append(Step(page=name, badge=_BADGES[index], label=_SHORT[name], state=state))
    return tuple(steps)


# 옆단 스타일은 **한 곳에 모은다** (15세션 3절). 여기저기 흩어 놓으면 단계마다
# 다른 모양이 되고, 고칠 때 하나를 빠뜨린다.
_STYLE = """
<style>
[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] div.stButton > button {
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
[data-testid="stSidebar"] div.stButton > button:disabled {
    opacity: 0.42;
}
</style>
"""


def render_sidebar(*, ready: bool, measures_on: bool) -> str:
    """옆단을 그리고 고른 화면을 돌려준다 (15세션 3절)."""
    st.sidebar.markdown(_STYLE, unsafe_allow_html=True)
    page = current_page()
    st.session_state.setdefault(_VISITED, (page,))

    st.sidebar.caption("분석")
    for step in step_states(ready=ready, measures_on=measures_on):
        st.sidebar.button(
            f"{step.text}  ·  {step.state.mark} {step.state}",
            key=f"nav_{step.page}",
            type="primary" if step.state is StepState.CURRENT else "secondary",
            disabled=not step.state.clickable,
            width="stretch",
            on_click=go_to,
            args=(step.page,),
        )
    if not ready:
        st.sidebar.caption("1단계에서 파일을 올리고 계약 정보를 확정하면 열립니다.")

    st.sidebar.divider()
    # **기준 데이터는 단계가 아니다.** 번호 배지를 붙이지 않는다.
    st.sidebar.caption("설정")
    for name in SETTINGS_PAGES:
        st.sidebar.button(
            name,
            key=f"nav_{name}",
            type="primary" if name == page else "secondary",
            width="stretch",
            on_click=go_to,
            args=(name,),
        )
    return current_page()


def next_step_button(page: str, *, key: str, label: str | None = None) -> None:
    """단계 하단의 이동 단추. ``2단계 · 개선 수단으로 →`` 꼴.

    **옆단과 같은 세션 키를 쓴다** — 두 벌이면 단추로 옮긴 뒤 옆단 표시가 어긋난다.
    """
    st.divider()
    st.button(
        label or f"{page}으로 →",
        type="primary",
        key=key,
        width="stretch",
        on_click=go_to,
        args=(page,),
    )
