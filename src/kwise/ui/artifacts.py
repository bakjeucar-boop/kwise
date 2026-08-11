"""산출물 바이트 보관 (요구사항서 10.2).

**``st.download_button`` 은 눌리는 순간 rerun 을 일으킨다.** 그 rerun 에서는
바로 앞의 "만들기" 단추가 눌리지 않은 상태이므로, 만들기 분기 안에서
내려받기 단추를 그리면 **단추 자체가 사라지고 파일도 받아지지 않는다.**
화면의 계산 결과까지 다시 그려지며 날아간 것처럼 보인다 (12세션에서 잡았다).

    잘못된 순서   [만들기] 클릭 → 바이트 생성 → 내려받기 단추 → 클릭 → rerun → 사라짐
    바른 순서     [만들기] 클릭 → 바이트를 **세션에 담는다** → rerun 뒤에도 단추가 남는다

그래서 바이트를 세션 상태에 담아 두고 내려받기 단추는 **참조만** 한다.
입력이 바뀌면(토큰이 달라지면) 담아 둔 것을 버린다 — 옛 결과를 내려받게 두면
화면 숫자와 파일 내용이 어긋난다.
"""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

__all__ = ["ARTIFACT_KEY", "Artifact", "clear_artifacts", "recall", "remember"]

ARTIFACT_KEY = "_kwise_artifacts"


@dataclass(frozen=True)
class Artifact:
    """만들어 둔 산출물 하나."""

    payload: bytes
    filename: str
    token: str
    """만들 때의 입력 지문. 달라지면 버린다."""

    @property
    def kilobytes(self) -> float:
        return len(self.payload) / 1024


def _store() -> dict[str, Artifact]:
    store = st.session_state.get(ARTIFACT_KEY)
    if not isinstance(store, dict):
        store = {}
        st.session_state[ARTIFACT_KEY] = store
    return store


def remember(key: str, payload: bytes, filename: str, *, token: str) -> Artifact:
    """만든 산출물을 담아 둔다."""
    artifact = Artifact(payload=payload, filename=filename, token=token)
    _store()[key] = artifact
    return artifact


def recall(key: str, *, token: str) -> Artifact | None:
    """담아 둔 산출물. **입력이 바뀌었으면 버리고 ``None``** 을 돌려준다."""
    artifact = _store().get(key)
    if artifact is None:
        return None
    if artifact.token != token:
        _store().pop(key, None)
        return None
    return artifact


def clear_artifacts() -> None:
    """전부 버린다. 기준 데이터를 고쳤을 때처럼 결과가 통째로 바뀌는 경우다."""
    st.session_state[ARTIFACT_KEY] = {}
