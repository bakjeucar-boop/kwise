"""안내 표시 (15세션 4절).

**배경색 상자를 쓰지 않는다.** ``st.warning`` 은 노란 바탕에 진한 노란 글자라
확인사항을 펼치면 화면이 통째로 노래진다. 열 줄이 같은 색으로 쌓이면 무엇이
중요한지 오히려 알 수 없다.

    차단   ``st.error`` — **색을 남기는 유일한 등급.** 이대로면 결과를 못 쓴다
    주의   ⚠ 아이콘 + 굵은 글씨. 배경 없음
    참고   · 점 하나. 작은 글씨, 배경 없음

심각도는 **아이콘과 굵기로만** 가른다. 등급 판정은 :mod:`kwise.ui.notices` 가
하고 여기서는 그리기만 한다.
"""

from __future__ import annotations

import streamlit as st

from kwise.ui.notices import Notice, Severity
from kwise.ui.text import markdown_safe

__all__ = ["BLOCK_ICON", "CAUTION_ICON", "blocked", "caution", "note", "render_notice"]

CAUTION_ICON = "⚠"
BLOCK_ICON = "⛔"


def blocked(text: str) -> None:
    """차단 — **색을 남기는 유일한 등급이다.** 이대로면 결과를 쓸 수 없다."""
    st.error(markdown_safe(text))


def caution(text: str) -> None:
    """주의 — 결과 해석이 달라지는 것. 배경 없이 아이콘과 굵기로만 가른다.

    **문구가 이미 굵게를 품고 있으면 겹치지 않는다** (22세션). 바깥에서 한 번 더
    감싸면 별표가 넷이 이어져(``****``) 마크다운이 굵게를 열지 못하고 별표가
    그대로 화면에 나온다 — 「\\*\\*\\*\\*투자비는 0원이지만」 이 그랬다.
    발신처가 강조한 자리를 살리는 편이 낫다.
    """
    body = markdown_safe(text)
    st.markdown(f"{CAUTION_ICON} {body if '**' in body else f'**{body}**'}")


def note(text: str) -> None:
    """참고 — 전제·근거. 작은 글씨로 흘려 둔다."""
    st.caption(f"· {markdown_safe(text)}")


def render_notice(item: Notice) -> None:
    """등급에 맞게 그린다. **판정은 :mod:`kwise.ui.notices` 가 한다.**"""
    if item.severity is Severity.BLOCK:
        blocked(item.text)
    elif item.severity is Severity.WARN:
        caution(item.text)
    else:
        note(item.text)
