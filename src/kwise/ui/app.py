"""kWise 화면 (요구사항서 10.1·10.2).

    .venv\\Scripts\\streamlit.exe run streamlit_app.py

**세 화면을 탭으로 한 번에 그린다** (16세션 1절).

    옆단          건물 공통 정보 — 건물명·용도·지역·연면적·준공연도
    [1단계 · 진단]        계약 정보와 업로드. 설비 정보는 묻지 않는다
    [2단계 · 개선 수단]    카드로 켜고 끈다. 7장 번호 순
    [3단계 · 개선안 조합]  체크박스로 조합을 짜고 합산효과를 본다
    옆단 하단      기준 데이터 — **단계가 아니라 설정이다**

옆단에서 하나를 골라 그 화면만 그리던 방식은 **그리지 않은 화면의 위젯 값을
잃었다.** 3단계가 2단계 방문에 기대고, 내려받기 rerun 이 켠 수단을 지웠다.
탭은 셋을 함께 그리므로 그런 일이 구조적으로 생기지 않는다.

**계산은 여기 없다.** :mod:`kwise.ui.pipeline` 이 순수 함수를 어떻게 부를지 정하고
:mod:`kwise.ui.cache` 가 캐싱한다.
"""

from __future__ import annotations

import streamlit as st

from kwise import __version__
from kwise.ui import building as building_view
from kwise.ui import callout
from kwise.ui.cache import cached_tariff, rules_stamp
from kwise.ui.nav import RULES_PAGE, TABS, render_settings_entry
from kwise.ui.session import purge_stale
from kwise.ui.state import carry_inputs
from kwise.ui.views import compare, diagnose, measures, rules_admin

__all__ = ["main"]

_PURGED = "_kwise_purged"

_NOT_READY = (
    "「1단계 · 진단」 탭에서 사용량 파일을 올리고 계약 정보를 확정하면 여기에 결과가 나옵니다."
)


def main() -> None:
    st.set_page_config(page_title="kWise — 전력 비용 진단", page_icon="⚡", layout="wide")

    # 지난 실행이 남긴 임시 폴더를 한 번만 쓸어낸다 (10.2).
    if _PURGED not in st.session_state:
        purge_stale()
        st.session_state[_PURGED] = True

    # **기준 데이터 화면을 다녀와도 켠 수단과 넣은 값이 남아야 한다** (16세션 0-1).
    # 그 화면은 분석 화면을 통째로 갈아 끼우므로 위젯 상태가 버려진다.
    carry_inputs()

    st.sidebar.title("kWise")
    st.sidebar.caption(f"버전 {__version__} · 대한민국 전용")
    # **옆단은 건물 이야기다** (16세션 2절). 계약 정보는 건물이 아니라 계약이라
    # 1단계 탭으로 내렸다.
    info = building_view.render_sidebar()
    st.sidebar.divider()
    page = render_settings_entry()
    st.sidebar.divider()
    # 앞절(계산 범위)은 1단계 「현재 요금 구조」 가 이미 밝힌다 (25세션 3-3 · I).
    st.sidebar.caption("인증·신고용 산출물이 아닙니다.")

    rules_admin.render_alerts()

    if page == RULES_PAGE:
        rules_admin.render()
        return

    # 요금표 검증 상태는 **참고 등급**이다. 옆단에 띄우지 않고 기준 데이터
    # 화면과 산출물에만 둔다 (10.7).
    table = cached_tariff(rules_stamp())

    diagnose_tab, measures_tab, combine_tab = st.tabs(TABS)
    with diagnose_tab:
        context = diagnose.render(table, info)
    with measures_tab:
        # **진행할 수 없어도 탭을 막지 않는다** (16세션 1절). 눌러 보고 무엇이
        # 없는지 읽는 편이, 눌리지 않는 이유를 짐작하는 편보다 낫다.
        if context is None:
            st.header("🛠 2단계 · 개선 수단")
            callout.note(_NOT_READY)
        else:
            measures.render(context, table, info)
    with combine_tab:
        if context is None:
            st.header("⚖ 3단계 · 개선안 조합")
            callout.note(_NOT_READY)
        else:
            compare.render(context, table, info)


# **import 로 화면을 그리지 않는다.** Streamlit 은 조작할 때마다 진입점 파일을
# 처음부터 다시 실행하는데, 파이썬은 이미 불러온 모듈을 다시 실행하지 않는다.
# 최상위에서 그리면 두 번째 실행부터 아무것도 그려지지 않아 화면이 빈다
# (2026-08-14 배포지에서 겪었다 — 로컬은 이 파일을 직접 돌려서 안 났다).
#
#     streamlit run streamlit_app.py      그쪽이 main() 을 매 실행에 부른다 — **이것만 쓴다**
#     streamlit run src\kwise\ui\app.py   이 아래가 매 실행에 돈다. 배포지와 경로가
#                                         달라 위 결함이 로컬에서 안 보인다
#
# 아래 진입점은 **급할 때의 뒷문**으로만 남긴다. 문서는 모두 뿌리 진입점을 가리킨다
# (2026-08-15 에 통일했다).
if __name__ == "__main__":
    main()
