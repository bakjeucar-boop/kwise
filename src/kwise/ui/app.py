"""kWise 화면 (요구사항서 10.1·10.2).

    .venv\\Scripts\\streamlit.exe run src\\kwise\\ui\\app.py

화면은 셋 + 기준 데이터 관리다. **순서를 요구사항서가 정하므로** Streamlit 의
``pages\\`` 자동 배치를 쓰지 않고 옆단에서 직접 고른다.

    1단계 · 진단      업로드 즉시, 설비 정보 없이
    2단계 · 개선 수단  카드로 선택. 투자비 순
    3단계 · 비교      조합·감도·검토 범위·내려받기
    기준 데이터        근거를 값 옆에 두고 고친다

**계산은 여기 없다.** :mod:`kwise.ui.pipeline` 이 순수 함수를 어떻게 부를지 정하고
:mod:`kwise.ui.cache` 가 캐싱한다.
"""

from __future__ import annotations

import streamlit as st

from kwise import __version__
from kwise.diagnose import Diagnosis
from kwise.io import UsageData
from kwise.quality import QualityReport
from kwise.tariff import TariffTable
from kwise.ui.cache import (
    cached_diagnosis,
    cached_quality,
    cached_tariff,
    cached_usage,
    rules_stamp,
    usage_token,
)
from kwise.ui.nav import PAGE_KEY, PAGES
from kwise.ui.pipeline import ContractForm
from kwise.ui.session import purge_stale
from kwise.ui.state import get_form, upload
from kwise.ui.views import compare, diagnose, measures, rules_admin

__all__ = ["main"]

_PURGED = "_kwise_purged"


def main() -> None:
    st.set_page_config(page_title="kWise — 전력 비용 진단", page_icon="⚡", layout="wide")

    # 지난 실행이 남긴 임시 폴더를 한 번만 쓸어낸다 (10.2).
    if _PURGED not in st.session_state:
        purge_stale()
        st.session_state[_PURGED] = True

    st.sidebar.title("kWise")
    st.sidebar.caption(f"버전 {__version__} · 대한민국 전용")
    # **단계 하단 이동 단추와 같은 세션 키를 쓴다** — 두 벌이면 표시가 어긋난다.
    page = st.sidebar.radio("화면", PAGES, key=PAGE_KEY, label_visibility="collapsed")
    st.sidebar.divider()
    st.sidebar.caption("기본요금과 전력량요금만 계산합니다. 인증·신고용 산출물이 아닙니다.")

    rules_admin.render_alerts()

    if page == PAGES[3]:
        rules_admin.render()
        return

    # 요금표 검증 상태는 **참고 등급**이다. 사이드바에 띄우지 않고 기준 데이터
    # 화면과 산출물에만 둔다 (10.7).
    table = cached_tariff(rules_stamp())

    if page == PAGES[0]:
        diagnose.render(table)
        return

    context = _context(table)
    if context is None:
        st.warning("1단계에서 파일을 올리고 계약 정보를 확정해 주십시오.")
        return
    usage, quality, form, diagnosis = context

    if page == PAGES[1]:
        measures.render(usage, table, form, diagnosis, quality)
    else:
        compare.render(usage, table, form, diagnosis, quality)


def _context(
    table: TariffTable,
) -> tuple[UsageData, QualityReport, ContractForm, Diagnosis] | None:
    """2·3단계가 쓰는 값을 캐시에서 다시 꺼낸다. 세션에는 원재료만 있다."""
    uploaded = upload()
    form = get_form()
    if uploaded is None or form is None:
        return None
    data, filename = uploaded
    usage = cached_usage(
        data,
        filename,
        date_column=st.session_state.get("diag_date_column"),
        energy_column=st.session_state.get("diag_energy_column"),
    )
    token = usage_token(usage)
    quality = cached_quality(usage, token, form.contract_kw)
    diagnosis = cached_diagnosis(usage, table, quality, token, form, rules_stamp())
    return usage, quality, form, diagnosis


main()
