"""세션 상태 (요구사항서 10.1).

**원재료만 세션에 둔다.** 업로드 바이트·계약 정보·수단 입력이 전부이고, 계산
결과는 :mod:`kwise.ui.cache` 가 캐시에서 꺼낸다. 결과까지 세션에 쌓으면 기준
데이터를 고쳤을 때 어느 쪽이 옛 값인지 알 수 없게 된다.

업로드 바이트는 **메모리에만** 둔다 (10.2).
"""

from __future__ import annotations

import uuid

import streamlit as st

from kwise.io import UsageData
from kwise.report.days import MAX_DEMAND_KEY, RepresentativeDay, find_day
from kwise.ui.pipeline import ContractForm, SolarInputs
from kwise.ui.spec import MEASURES

__all__ = [
    "SESSION_ID",
    "carry_inputs",
    "clear_upload",
    "enabled_measures",
    "get_combination_pick",
    "get_form",
    "get_solar_inputs",
    "input_key",
    "measure_float",
    "reference_day",
    "session_id",
    "set_combination_pick",
    "set_form",
    "set_solar_inputs",
    "store_upload",
    "toggle_key",
    "upload",
]

SESSION_ID = "_kwise_session_id"
_CARRY = "_kwise_carried_inputs"
#: 화면을 갈아 끼울 때 지켜야 할 위젯 키의 머리글자.
_CARRY_PREFIXES = ("measure_", "diag_", "combo_pick_")
_UPLOAD = "upload_bytes"
_UPLOAD_NAME = "upload_name"
_FORM = "contract_form"
_SOLAR = "solar_inputs"
_COMBO = "combination_pick"


def session_id() -> str:
    """세션 하나를 가리키는 값. 임시 폴더 이름에 쓴다."""
    if SESSION_ID not in st.session_state:
        st.session_state[SESSION_ID] = uuid.uuid4().hex[:12]
    return str(st.session_state[SESSION_ID])


def carry_inputs() -> None:
    """화면이 갈릴 때 **위젯 값을 지킨다** (16세션 0-1).

    Streamlit 은 **이번 실행에서 그리지 않은 위젯의 상태를 버린다.** 세 화면을
    탭으로 묶어 늘 함께 그리므로 탭 사이에서는 문제가 없지만, 기준 데이터 화면은
    분석 화면을 통째로 갈아 끼운다 — 다녀오면 켠 수단도 넣은 값도 초깃값으로
    돌아갔다.

    그래서 **매 실행에 그림자 사본을 뜨고, 없어진 키를 되돌려 놓는다.** 위젯을
    만들기 전에 세션 키에 쓰면 위젯이 그 값을 안고 태어난다.

        분석 화면   값이 있다 → 사본을 갱신한다
        기준 데이터  위젯을 그리지 않는다 → 실행 끝에 버려진다
        돌아오면    사본에서 되돌려 놓는다

    이 함수는 **화면을 그리기 전에** 부른다.
    """
    shadow = st.session_state.get(_CARRY)
    if not isinstance(shadow, dict):
        shadow = {}
    for key, value in shadow.items():
        if key not in st.session_state:
            st.session_state[key] = value
    shadow.update(
        {
            key: st.session_state[key]
            for key in list(st.session_state.keys())
            if isinstance(key, str) and key.startswith(_CARRY_PREFIXES)
        }
    )
    st.session_state[_CARRY] = shadow


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


def reference_day(usage: UsageData) -> RepresentativeDay | None:
    """**곡선 차트가 보는 대표일** (15세션 2절).

    2단계가 위젯으로 고른 값을 세션에서 그대로 읽는다 — 3단계 보고서 차트도
    같은 날을 봐야 화면과 문서가 어긋나지 않는다. 위젯을 그리지 않으므로
    어느 화면에서 불러도 안전하다.
    """
    key = st.session_state.get(input_key("common", "ref_day")) or MAX_DEMAND_KEY
    custom = st.session_state.get(input_key("common", "ref_day_custom"))
    try:
        return find_day(usage, str(key), custom=custom)
    except ValueError:  # 관측치가 없으면 대표일도 없다
        return None


def enabled_measures() -> tuple[str, ...]:
    """켠 수단. **7장 순서 그대로** 돌려준다."""
    return tuple(item.key for item in MEASURES if st.session_state.get(toggle_key(item.key)))


def set_combination_pick(picked: tuple[str, ...]) -> None:
    """「합산효과 계산」 을 누른 그 순간의 선택 (33세션 5절)."""
    st.session_state[_COMBO] = tuple(picked)


def get_combination_pick() -> tuple[str, ...] | None:
    """마지막으로 계산한 선택. 한 번도 누르지 않았으면 ``None``."""
    value = st.session_state.get(_COMBO)
    return tuple(str(item) for item in value) if isinstance(value, tuple) else None


def set_solar_inputs(inputs: SolarInputs) -> None:
    st.session_state[_SOLAR] = inputs


def get_solar_inputs() -> SolarInputs | None:
    value = st.session_state.get(_SOLAR)
    return value if isinstance(value, SolarInputs) else None
