"""세션 상태 (요구사항서 10.1).

**원재료만 세션에 둔다.** 업로드 바이트·계약 정보·수단 입력이 전부이고, 계산
결과는 :mod:`kwise.ui.cache` 가 캐시에서 꺼낸다. 결과까지 세션에 쌓으면 기준
데이터를 고쳤을 때 어느 쪽이 옛 값인지 알 수 없게 된다.

업로드 바이트는 **메모리에만** 둔다 (10.2).
"""

from __future__ import annotations

import uuid

import streamlit as st

from kwise.ui.pipeline import ContractForm, SolarInputs
from kwise.ui.spec import MEASURES

__all__ = [
    "SESSION_ID",
    "clear_upload",
    "enabled_measures",
    "get_form",
    "get_solar_inputs",
    "input_key",
    "measure_float",
    "session_id",
    "set_form",
    "set_solar_inputs",
    "store_upload",
    "toggle_key",
    "upload",
]

SESSION_ID = "_kwise_session_id"
_UPLOAD = "upload_bytes"
_UPLOAD_NAME = "upload_name"
_FORM = "contract_form"
_SOLAR = "solar_inputs"


def session_id() -> str:
    """세션 하나를 가리키는 값. 임시 폴더 이름에 쓴다."""
    if SESSION_ID not in st.session_state:
        st.session_state[SESSION_ID] = uuid.uuid4().hex[:12]
    return str(st.session_state[SESSION_ID])


# --------------------------------------------------------------------- 업로드


def store_upload(data: bytes, filename: str) -> None:
    st.session_state[_UPLOAD] = data
    st.session_state[_UPLOAD_NAME] = filename


def upload() -> tuple[bytes, str] | None:
    data = st.session_state.get(_UPLOAD)
    name = st.session_state.get(_UPLOAD_NAME)
    if data is None or name is None:
        return None
    return data, str(name)


def clear_upload() -> None:
    for key in (_UPLOAD, _UPLOAD_NAME):
        st.session_state.pop(key, None)


# --------------------------------------------------------------------- 계약 정보


def set_form(form: ContractForm) -> None:
    st.session_state[_FORM] = form


def get_form() -> ContractForm | None:
    value = st.session_state.get(_FORM)
    return value if isinstance(value, ContractForm) else None


# --------------------------------------------------------------------- 개선 수단


def toggle_key(measure_key: str) -> str:
    return f"measure_on_{measure_key}"


def input_key(measure_key: str, field: str) -> str:
    """수단 입력 위젯의 상태 키.

    **2단계에서 넣은 값을 3단계가 읽어야 한다.** 위젯에 키를 주지 않으면 값이
    그 화면에만 남아, 조합 비교와 산출물이 목표 피크·단가를 모르게 된다.
    """
    return f"measure_{measure_key}_{field}"


def measure_float(measure_key: str, field: str) -> float | None:
    """수단 입력 하나를 실수로. 넣지 않았거나 0 이면 ``None`` 이다.

    0 을 그대로 넘기면 "단가 0원" 이 되어 회수기간이 0년으로 나온다 (7.5·7.6).
    """
    value = st.session_state.get(input_key(measure_key, field))
    if value is None:
        return None
    number = float(value)
    return number if number else None


def enabled_measures() -> tuple[str, ...]:
    """켠 수단. **7장 순서 그대로** 돌려준다."""
    return tuple(item.key for item in MEASURES if st.session_state.get(toggle_key(item.key)))


def set_solar_inputs(inputs: SolarInputs) -> None:
    st.session_state[_SOLAR] = inputs


def get_solar_inputs() -> SolarInputs | None:
    value = st.session_state.get(_SOLAR)
    return value if isinstance(value, SolarInputs) else None
