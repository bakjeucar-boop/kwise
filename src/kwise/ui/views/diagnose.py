"""1단계 · 진단 (요구사항서 10.1·10.7).

**업로드 즉시, 설비 정보 없이** 나오는 화면이다. 묻는 것은 계약 정보 넷과
역률뿐이다.

순서는 **넣는 것을 먼저, 읽는 것을 나중에** 로 잡는다 (16세션 3절).

    ① 계약 정보 → ② 업로드 → ③ 역률
    → 지표 카드 → ④ 데이터 품질 → ⑤ 부하 패턴 → ⑥ 피크 특성 → ⑦ 요금 구조

계약 정보를 업로드 위에 둔 것은 **묻는 것이 넷뿐이고 파일과 무관하기 때문**이다.
파일을 먼저 올리게 하면 계약 정보를 넣기 전에 금액 없는 결과부터 읽게 된다.
**넷 다 청구서 기재값이다** — 관측치로 가늠해 미리 채우지 않는다 (21세션 1절).

**계약전력 적정성과 개선 여지 요약은 여기 없다** (16세션 3절). 적정성은
2단계 7.2 카드가 같은 값을 더 자세히 내고, 개선 여지는 7.1·7.2 와 겹쳤다 —
같은 금액이 두 화면에 다른 이름으로 있으면 어느 쪽을 믿어야 할지 알 수 없다.

**차트를 표보다 앞에 둔다.** 월별 최대수요·상위 구간 시각 분포·시간대별
프로파일이 진단의 핵심인데 표 뒤에 있으면 눈에 들어오지 않는다.

안내는 심각도로 거른다 — **화면에는 차단과 주의만** (:mod:`kwise.ui.notices`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace

import altair as alt
import pandas as pd
import streamlit as st

from kwise.diagnose import ChargeStructure, Diagnosis
from kwise.io import UsageData
from kwise.notices import tooltip
from kwise.quality import (
    DEFAULT_OPERATING_HOURS,
    LoadPattern,
    QualityReport,
    monthly_longest_gaps,
)
from kwise.report import localize, narrative
from kwise.tariff import TENTATIVE_BASE_FEE_BASIS_WARNING, TariffTable
from kwise.tariff.labels import SEASON_LABELS
from kwise.ui import callout, charts
from kwise.ui import text as fmt
from kwise.ui.anchors import manual_tip
from kwise.ui.building import BuildingInfo, intensity_kwh_per_m2, narrow_contract_types
from kwise.ui.cache import (
    cached_daily_temperature,
    cached_diagnosis,
    cached_quality,
    cached_usage,
    rules_stamp,
    usage_token,
)
from kwise.ui.context import AnalysisContext
from kwise.ui.labels import option_label
from kwise.ui.notices import partition_facts, screen_notices, tooltip_text
from kwise.ui.pipeline import (
    ContractForm,
    contract_type_choices,
    default_lagging_pct,
    option_choices,
    voltage_choices,
)
from kwise.ui.state import get_form, set_form, store_upload, upload
from kwise.ui.views.measures import dr_off_days, weather_source_label

__all__ = ["render"]

_COLUMN_KEYS = ("diag_date_column", "diag_energy_column")
_PF_LAGGING = "diag_pf_lagging"
_PF_KNOWN = "diag_pf_leading_known"
_PF_LEADING = "diag_pf_leading"


def render(table: TariffTable, building: BuildingInfo | None = None) -> AnalysisContext | None:
    """1단계를 그리고 **2·3단계가 쓸 한 벌을 돌려준다** (16세션 1절).

    계약 정보를 확정하지 않았거나 파일을 읽지 못하면 ``None`` 이고, 2·3단계 탭은
    안내만 낸다 — 탭 자체를 막지는 않는다.
    """
    st.header("📊 1단계 · 진단")
    st.caption(
        "파일만 올려도 결과가 나옵니다. 설비 정보는 묻지 않습니다.",
        help=manual_tip("improvement-summary"),
    )

    # **화면에 그리기 전에 세션에서 먼저 읽는다.** 계약 정보가 업로드 위에 오지만
    # 아래 블록들이 파일을 쓰므로, 순서와 의존이 어긋나지 않게 한다.
    stored = upload()
    usage, load_error = _load(stored)

    # ---- ① 계약 정보
    form = _contract_block(table, building)
    _tentative_basis_block(table, form)

    # ---- ② 업로드
    _upload_block()
    if stored is None:
        callout.note(
            "사용량 파일(csv·xls·xlsx)을 올려 주십시오. 한전 사이버지점 내려받기 형식입니다."
        )
        return None
    if usage is None:
        _load_failure(load_error)
        return None
    _column_block(usage)

    # ---- ③ 역률
    _power_factor_block(form)

    quality = cached_quality(usage, usage_token(usage), form.contract_kw if form else None)
    # **운영 시간대는 옆단에서 온다** (21세션 4절). 운영시간 외 부하 판정과 DR 저부하일
    # 판정이 이 값을 쓴다 — 캐시 열쇠에도 들어가야 값이 바뀌면 다시 계산한다.
    hours = building.operating_hours if building else DEFAULT_OPERATING_HOURS
    # **경제성DR 의 「쉬는 날」 은 2단계 카드에서 고른다** (29세션). 고르는 자리와
    # 쓰는 자리가 뒤바뀌어 보이지만, 세 화면이 한 실행에서 위에서 아래로 그려지므로
    # 직전 실행의 선택을 읽으면 된다 — 대표일·ESS 목표와 같은 방식이다.
    # **DR 판정에만 쓴다.** 요금 계산의 공휴일은 법정 공휴일 그대로다.
    diagnosis = cached_diagnosis(
        usage,
        table,
        quality,
        usage_token(usage),
        form,
        rules_stamp(),
        hours,
        dr_off_days(),
    )

    _headline_block(usage, diagnosis)
    _notice_block(quality, diagnosis)
    _quality_block(usage, quality)  # ④
    _pattern_block(diagnosis, usage, building)  # ⑤
    _peak_block(diagnosis)  # ⑥
    _structure_block(usage, diagnosis, building)  # ⑦

    if form is None:
        return None
    return AnalysisContext(usage=usage, quality=quality, form=form, diagnosis=diagnosis)


# --------------------------------------------------------------------- 지표·안내


def _headline_block(usage: UsageData, diagnosis: Diagnosis) -> None:
    """**지표 카드 넷.** 숫자와 단위만 둔다 — 설명은 카드 밖이다."""
    meta = usage.meta
    # 기간은 **값이 아니라 delta 자리**에 둔다. 지표 값 글꼴이 커서 한 줄에 들어가지
    # 않고 `2023-04-25 – 2024-` 까지만 보였다 (13세션). delta 는 글씨가 작다.
    columns = st.columns([1.2, 1, 1, 1])
    columns[0].metric(
        "분석 기간",
        fmt.days(meta.period_days),
        fmt.period(meta.start, meta.end),
        delta_color="off",
    )
    columns[1].metric("최대수요", fmt.kw(meta.max_demand_kw))
    # **부하 패턴 절과 같은 문구를 쓴다** (30세션 1-1). 같은 지표가 두 자리에 있는데
    # 설명이 두 벌이면 어느 쪽이 정의인지 알 수 없다 — 문구를 낳는 자리를 하나로 둔다.
    columns[2].metric(
        "부하율",
        fmt.ratio_pct(diagnosis.pattern.load_factor),
        help=_pattern_formulas(diagnosis.pattern)["load_factor"],
    )
    # 1년치가 아닌 자료를 "연간" 이라 적으면 그 자체가 오독이다. 라벨을 기간에 맞춘다.
    span = meta.period_days or 0
    columns[3].metric("연간 사용량" if span >= 350 else "기간 사용량", fmt.mwh(meta.total_kwh))


#: 결측 관련 문구는 「데이터 품질」 블록이 한 묶음으로 낸다. 위쪽 경고에서 뺀다.
#: **20세션에 사실 ID 로 바꿨다** — 18세션까지는 「결측」·「보간」 이라는 부분
#: 문자열로 걸렀는데, 문구가 한 글자만 바뀌어도 새고 엉뚱한 문장까지 걸어 갔다.
#: 앞의 셋이 :func:`missing_lines` 의 세 줄이고, 편중은 이 블록이 따로 한 줄
#: 적는다.
MISSING_FACTS = (
    "quality.missing_total",
    "quality.longest_gap",
    "quality.month_missing_rate",
    "quality.peak_skew",
    # **버린 행도 같은 자리다** (31세션 0-2). 결측과 나란히 읽어야 「빠진 값이
    # 얼마나 되나」 가 한 번에 잡힌다 — 위쪽 확인사항에 두면 결측률과 떨어진다.
    "quality.dropped_rows",
)

#: 「데이터 품질」 절이 **문구 그대로** 다시 그리는 사실 (31세션 0-2).
#: :func:`missing_lines` 는 문구를 새로 짓지만 이쪽은 안내의 글을 그대로 낸다 —
#: 건수만 적는 한 줄이라 두 벌로 적을 이유가 없다.
DROPPED_ROWS_FACT = "quality.dropped_rows"

#: 계약전력 변경 경고는 **2단계 7.2 카드**가 낸다 (16세션 3절). 바꾸자고
#: 제안하는 자리가 그 경고의 제자리다 — 여기서 미리 읽으면 무엇을 조심하라는
#: 말인지 알 수 없어 그냥 지나친다. 두 곳에 두면 같은 문장이 화면에 두 번 뜬다.
CONTRACT_FACTS = (
    "contract.margin",
    "contract.penalty",
    "contract.over_limit",
    "contract.floor_unknown",
    "contract.saving_basis",
    "contract.energy_unchanged",
    "contract.floor_not_binding",
    "quality.over_contract",
    "tariff.floor_no_contract",
    "tariff.contract_type_threshold",
    "diagnose.no_contract_kw",
)


def _notice_block(quality: QualityReport, diagnosis: Diagnosis) -> None:
    """등급대로 자리를 나눈다 (19세션 1절).

    본문에는 **차단과 주의만** 남긴다. 근거는 아래 툴팁 하나로 접고, 참고는
    화면에 내지 않는다 — 보고서 부록으로 간다.
    """
    merged = (*quality.notices, *diagnosis.notices)
    _missing, rest = partition_facts(screen_notices(merged), MISSING_FACTS)
    _contract, rest = partition_facts(rest, CONTRACT_FACTS)
    # **배경색 상자를 쓰지 않는다** (15세션 4절). 차단만 색을 남긴다.
    for notice in rest:
        callout.render_notice(notice)
    grounds = tooltip(merged)
    if grounds:
        st.caption(
            f"산출 근거 {len(grounds)}건",
            help=tooltip_text(merged),
        )


# --------------------------------------------------------------------- 업로드·열 인식


def _upload_block() -> None:
    """업로드 위젯. **새 파일이 들어오면 즉시 다시 그린다.**

    파일이 바뀌면 아래 블록이 전부 다시 계산되므로, 받은 그 실행에서 멈추면
    화면이 한 박자 늦는다.
    """
    uploaded = st.file_uploader(
        "사용량 데이터", type=["csv", "xls", "xlsx"], help="업로드 파일은 서버에 저장하지 않습니다."
    )
    if uploaded is None:
        return
    data = uploaded.getvalue()
    stored = upload()
    if stored is not None and stored[0] == data and stored[1] == uploaded.name:
        return
    store_upload(data, uploaded.name)
    st.rerun()


def _load(stored: tuple[bytes, str] | None) -> tuple[UsageData | None, str]:
    """세션에 담긴 바이트를 읽는다. **화면을 그리기 전에 부른다.**

    실패 사유는 문자열로 돌려 두었다가 업로드 블록 자리에서 낸다 — 계약 정보
    위에 파일 오류가 뜨면 어느 입력이 잘못됐는지 헷갈린다.
    """
    if stored is None:
        return None, ""
    try:
        return cached_usage(
            stored[0],
            stored[1],
            date_column=st.session_state.get(_COLUMN_KEYS[0]),
            energy_column=st.session_state.get(_COLUMN_KEYS[1]),
        ), ""
    except Exception as exc:
        return None, str(exc)


def _load_failure(reason: str) -> None:
    callout.blocked(f"파일을 읽지 못했습니다. {reason}")
    overridden = any(st.session_state.get(key) for key in _COLUMN_KEYS)
    if overridden and st.button("열 지정 되돌리기"):
        for key in _COLUMN_KEYS:
            st.session_state.pop(key, None)
        st.rerun()


def _column_block(usage: UsageData) -> None:
    """열 판정을 보여 주고 **드롭다운으로 고칠 수 있게** 한다 (10.1).

    자동 탐지는 언젠가 실패한다. 실패했을 때 화면에서 손쓸 수 없으면 파일을
    고쳐 다시 올리는 수밖에 없다.
    """
    detection = usage.meta.columns
    with st.expander(f"열 인식 결과 — {detection.describe()}", expanded=False):
        columns = list(detection.columns) or [detection.date_column, detection.energy_column]
        left, right = st.columns(2)
        with left:
            st.selectbox(
                "검침일 열",
                columns,
                index=columns.index(detection.date_column),
                key=_COLUMN_KEYS[0],
            )
        with right:
            st.selectbox(
                "전력량 열",
                columns,
                index=columns.index(detection.energy_column),
                key=_COLUMN_KEYS[1],
            )
        st.caption("자동 판정이 빗나갔으면 여기서 고칩니다.", help=manual_tip("column-detection"))
        if detection.date_candidates or detection.energy_candidates:
            st.dataframe(
                pd.DataFrame(
                    [
                        {"역할": role, "열": item.column, "점수": item.score, "근거": item.reason}
                        for role, ranked in (
                            ("검침일", detection.date_candidates),
                            ("전력량", detection.energy_candidates),
                        )
                        for item in ranked
                    ]
                ),
                hide_index=True,
                width="stretch",
            )


# --------------------------------------------------------------------- 계약 정보


def _contract_block(table: TariffTable, building: BuildingInfo | None) -> ContractForm | None:
    """계약 정보 넷. **청구서 기재값을 받는다** (3.2 · 21세션 1절).

    **용도를 골랐으면 계약종별 후보를 좁힌다** (16세션 2절). 좁히기이지 판정이
    아니므로 좁힐 수 없으면 전 종별을 보인다 — 고를 것이 사라지면 입력을 못 한다.
    """
    saved = get_form()
    types = contract_type_choices(table)
    narrowed = narrow_contract_types(types, building.use_key if building else "")
    type_keys = [key for key, _label in narrowed]
    type_labels = {key: label for key, label in narrowed}

    with st.expander("계약 정보 (4)", expanded=saved is None):
        # **2열로 나눈다.** 넷을 세로로 쌓으면 스크롤이 생겨 한눈에 안 들어온다.
        left, right = st.columns(2)
        with left:
            default_type = saved.contract_type if saved else type_keys[0]
            contract_type = st.selectbox(
                "계약종별",
                type_keys,
                index=type_keys.index(default_type) if default_type in type_keys else 0,
                format_func=lambda key: type_labels[key],
                help=(
                    "청구서 기재값입니다. 옆단에서 용도를 고르면 후보가 좁아집니다.\n\n"
                    + manual_tip("contract-info")
                ),
            )

            voltages = voltage_choices(table, contract_type)
            voltage_keys = [key for key, _label in voltages]
            voltage_labels = {key: label for key, label in voltages}
            default_voltage = (
                saved.voltage if saved and saved.voltage in voltage_keys else voltage_keys[0]
            )
            voltage = st.selectbox(
                "전압구분",
                voltage_keys,
                index=voltage_keys.index(default_voltage),
                format_func=lambda key: voltage_labels[key],
                help="청구서 기재값입니다.",
            )
        with right:
            # **추정치를 미리 채우지 않는다** (21세션 1절). 계약 정보는 청구서
            # 기재값을 전제로 받는 값이라, 관측 최대에 여유를 얹은 가늠을 넣어
            # 두면 그것이 확정값처럼 읽히고 계약 적정성 진단이 그 값에 끌려간다.
            contract_kw = st.number_input(
                "계약전력 (kW)",
                min_value=0.0,
                value=float(saved.contract_kw) if saved and saved.contract_kw else None,
                step=1.0,
                placeholder="청구서 기재값",
                help="청구서 기재값입니다. 계약 적정성 진단(2단계 계약전력 조정)이 "
                "이 값을 전제로 합니다.",
            )
            options = option_choices(table, contract_type, voltage)
            default_option = saved.option if saved and saved.option in options else options[0]
            option = st.selectbox(
                "선택요금",
                options,
                index=options.index(default_option),
                format_func=option_label,
                # 뒷문장(「다른 선택요금은 2단계에서 다시 계산합니다」)은 7.1 카드
                # 개요와 같은 말이라 뺐다 (25세션 3-3 · J).
                help="청구서에 적힌 현행 선택요금입니다.",
            )

        lagging, leading = _saved_power_factor()
        form = ContractForm(
            contract_type=contract_type,
            voltage=voltage,
            option=option,
            contract_kw=contract_kw or None,
            power_factor_pct=lagging,
            leading_power_factor_pct=leading,
        )
        if st.button("계약 정보 확정", type="primary"):
            set_form(form)
            st.rerun()

    if saved is None:
        callout.caution(
            "계약 정보를 확정하기 전까지는 부하 패턴과 피크 특성만 나옵니다. "
            "금액은 계약 정보가 있어야 산출합니다."
        )
    return saved


# --------------------------------------------------------------------- ③ 역률


def _saved_power_factor() -> tuple[float, float | None]:
    """역률 입력의 **현재 값**. 위젯은 아래 블록이 그리고 값은 세션에 남는다.

    계약 정보 블록이 역률 블록보다 위에 있으므로 위젯 객체를 참조할 수 없다.
    탭 구조라 두 블록이 매 실행에 함께 그려지고, 확정 단추를 누른 실행에서는
    세션에 이미 새 값이 들어와 있다.
    """
    lagging = st.session_state.get(_PF_LAGGING)
    known = bool(st.session_state.get(_PF_KNOWN))
    leading = st.session_state.get(_PF_LEADING) if known else None
    return (
        float(lagging) if lagging is not None else default_lagging_pct(),
        float(leading) if leading is not None else None,
    )


def _power_factor_block(form: ContractForm | None) -> None:
    """역률 (선택) — **계약 정보에서 떼어 따로 둔다** (16세션 3절).

    계약종별·전압·계약전력·선택요금은 청구서를 보고 한 번에 넣는 것이고,
    역률은 아는 사람만 넣는 별개의 값이다. 접힌 하위 확장 패널 안에 두었더니
    있는 줄도 모르고 지나갔다.
    """
    saved = get_form()
    with st.expander("역률 (선택)", expanded=False):
        left, right = st.columns(2)
        with left:
            st.number_input(
                "주간 지상역률 (%)",
                min_value=1.0,
                max_value=100.0,
                value=float(saved.lagging_pct) if saved else default_lagging_pct(),
                step=0.1,
                key=_PF_LAGGING,
                help="모르면 그대로 두십시오. 이 값에서 조정액이 0원입니다.",
            )
        with right:
            known = st.checkbox(
                "야간 진상역률을 안다",
                value=saved.leading_power_factor_pct is not None if saved else False,
                key=_PF_KNOWN,
            )
            if known:
                st.number_input(
                    "야간 진상역률 (%)",
                    min_value=1.0,
                    max_value=100.0,
                    value=float(saved.leading_power_factor_pct or 95.0) if saved else 95.0,
                    step=0.1,
                    key=_PF_LEADING,
                )
        st.caption(
            "모르면 지상으로 간주해 추가요금이 없습니다.",
            help=manual_tip("measure-power-factor"),
        )

        lagging, leading = _saved_power_factor()
        if form is None:
            return
        changed = lagging != form.lagging_pct or leading != form.leading_power_factor_pct
        if changed and st.button("역률 반영", type="primary", key="apply_power_factor"):
            set_form(replace(form, power_factor_pct=lagging, leading_power_factor_pct=leading))
            st.rerun()
        if changed:
            callout.note("바꾼 역률은 「역률 반영」 을 눌러야 계산에 들어갑니다.")


# --------------------------------------------------------------------- 갑 종별 잠정 경고


def _tentative_basis_block(table: TariffTable, form: ContractForm | None) -> None:
    """갑 종별 기본요금 기준이 잠정임을 **접지 않고** 알린다 (미해결 — 약관 제38조).

    PoC 범위(일반용(을))에서는 이 경로를 타지 않아 샘플에 영향이 없다. 그래서
    갑 종별을 실제로 쓸 때 조용히 틀리기 쉽다 — 화면 위쪽에 남긴다.
    """
    if form is None:
        return
    contract_type = table.contract(form.contract_type)
    if not contract_type.base_fee_on_contract:
        return
    callout.caution(
        f"**{contract_type.label}** — {TENTATIVE_BASE_FEE_BASIS_WARNING} "
        f"현재는 계약전력 {fmt.kw(form.contract_kw)} 기준으로 계산합니다."
    )


# --------------------------------------------------------------------- 데이터 품질


#: 결측 안내는 **두 줄을 넘지 않는다** (30세션 2절). 16세션의 세 줄에서 하나
#: 줄였다 — 최장 연속과 결측률 높은 달은 「결측 구간」 접힘의 표가 달마다 낸다.
MISSING_LINE_LIMIT = 2


def missing_lines(quality: QualityReport) -> tuple[str, ...]:
    """결측 안내 **두 줄** (13세션 · 16세션 3절 · 30세션 2절).

    같은 사실이 세 군데서 세 번 나왔다 — 13세션에 한 블록으로 모으고, 16세션에
    세 줄로 고정했다. **30세션에 둘로 줄인다.**

        ① 전체 결측 구간 수와 비율, 그리고 미보간 원칙
        ② 결측이 있는 달들의 구간 수와 비율 (있을 때만)

    16세션의 ②최장 연속과 ③결측률 높은 달을 뺀 것은 지워서가 아니라 **옮겨서**다.
    둘 다 달마다 다른 값인데 한 줄로 뭉뚱그리면 어느 달을 조심하라는 것인지 알 수
    없었다 — :func:`missing_month_frame` 의 표가 달마다 낸다. 두 자리에 두면 같은
    사실을 두 번 말하는 것이므로 여기서는 뺀다.

    ②의 분모는 **그 달들의 구간 수**다. 전체 대비로 두면 ①과 같은 수가 되어 줄이
    하나 더 늘 뿐이다.
    """
    lines = [
        f"결측 {fmt.count(quality.missing_slots, '구간')} / "
        f"{fmt.count(quality.expected_slots, '구간')} "
        f"({fmt.ratio_pct(quality.missing_ratio)}) · 보간하지 않고 계산에서 제외"
    ]
    months = tuple(month for month in quality.monthly if month.missing_slots)
    if months:
        missing = sum(month.missing_slots for month in months)
        expected = sum(month.expected_slots for month in months)
        lines.append(
            f"결측이 있는 달 {fmt.count(len(months), '개')} — "
            f"{fmt.count(missing, '구간')} / {fmt.count(expected, '구간')} "
            f"({fmt.ratio_pct(missing / expected if expected else None)})"
        )
    return tuple(lines[:MISSING_LINE_LIMIT])


def dropped_row_lines(quality: QualityReport) -> tuple[str, ...]:
    """버린 행 안내 — **있을 때만 한 줄** (31세션 0-2).

    **문구를 새로 짓지 않는다.** 품질 검사가 낸 안내(:data:`DROPPED_ROWS_FACT`)를
    그대로 낸다 — 건수만 적는 한 줄이라 화면과 보고서가 다른 말을 할 이유가 없고,
    두 벌로 적으면 한쪽만 고쳐진다.
    """
    return tuple(
        fmt.markdown_safe(notice.text)
        for notice in quality.notices
        if notice.fact == DROPPED_ROWS_FACT
    )


def missing_month_frame(quality: QualityReport) -> pd.DataFrame:
    """월별 결측 구간 표 (30세션 2절).

    **결측이 있는 달만 낸다.** 열두 줄 중 열 줄이 0 이면 표가 아니라 여백이다.

    「최장 연속 구간」 은 구간 수와 일수를 함께 적는다 — 구간 수만으로는 15분짜리
    하나인지 며칠인지 알 수 없고(정전 지표와 같은 병이다), 일수만으로는 옆 열의
    「결측 구간 수」 와 이어지지 않는다.
    """
    longest = monthly_longest_gaps(quality.gaps, quality.interval_minutes)
    rows = [
        {
            "월": str(month.month),
            "결측 구간 수": int(month.missing_slots),
            "비율": fmt.ratio_pct(month.ratio),
            "최장 연속 구간": (
                f"{fmt.count(gap.slots, '구간')} ({fmt.count(gap.days, '일', decimals=2)})"
                if (gap := longest.get(month.month)) is not None
                else fmt.DASH
            ),
        }
        for month in quality.monthly
        if month.missing_slots
    ]
    return pd.DataFrame(rows, columns=["월", "결측 구간 수", "비율", "최장 연속 구간"])


def _quality_block(usage: UsageData, quality: QualityReport) -> None:
    """경고는 위쪽 :func:`_notice_block` 이 이미 냈다. 여기는 사실만 적는다."""
    st.subheader("데이터 품질")
    meta = usage.meta
    columns = st.columns(3)
    columns[0].metric(
        "검침 간격", f"{meta.interval_minutes}분", help=manual_tip("label-convention")
    )
    columns[1].metric(
        "결측",
        f"{fmt.ratio_pct(quality.missing_ratio)}",
        help=fmt.markdown_safe(
            "데이터가 없는 구간 ÷ 전체 구간.\n\n"
            "높을수록 덜 본 자료입니다 — 그 달의 최대수요를 그대로 믿기 어렵습니다."
        ),
    )
    # **건수만으로는 규모를 알 수 없다** (30세션 1-3). 15분 하나가 1건이고 며칠이
    # 통째로 빈 것도 1건이라, 지속 시간을 delta 자리에 함께 낸다.
    outage_hours = sum(event.duration_hours for event in quality.outages)
    columns[2].metric(
        "정전 추정",
        fmt.count(len(quality.outages), "건"),
        fmt.count(outage_hours, "시간", decimals=1) if quality.outages else None,
        delta_color="off",
        help=fmt.markdown_safe(
            "연속 결측 중 정전 흔적(그리드 이탈 행·복전 후 저부하)이 둘 다 있는 "
            "구간입니다.\n\n"
            "아래 작은 숫자가 합친 지속 시간입니다. 정전 중에는 피크가 날 수 없어 "
            "편중 판정에서 뺍니다."
        ),
    )

    with st.container(border=True):
        for line in missing_lines(quality):
            st.write(line)
        # **결측 편중은 결과 해석을 바꾼다.** 발생 지점에 한 번만 적는다.
        if quality.skew.flagged:
            st.write(
                f"피크 시간대 편중 배수 {fmt.count(quality.skew.multiple, decimals=2)} → "
                "그 달의 최대수요가 실제보다 낮게 잡혔을 수 있음"
            )
        # **버린 행은 결측 옆에서 읽어야 한다** (31세션 0-2). 해당 행이 없으면
        # 줄 자체가 없다 — 한전 내려받기 형식에서는 대개 0 이라, 늘 떠 있으면
        # 「0건」 이 화면 한 줄을 영영 차지한다.
        for line in dropped_row_lines(quality):
            st.write(line)
    # **달마다 다른 값은 표로 낸다** (30세션 2절). 본문 두 줄은 전체와 「결측이
    # 있는 달」 이라는 덩어리까지만 말하고, 어느 달이 얼마나 비었는지는 여기서 본다.
    frame = missing_month_frame(quality)
    if not frame.empty:
        with st.expander("결측 구간", expanded=False):
            st.dataframe(frame, hide_index=True, width="stretch")
    # **확인사항을 달지 않는다** (18세션 2절). 13세션에 위쪽 경고에서 여기 확인사항
    # 으로 내렸고, 16세션에 :func:`missing_lines` 가 같은 사실을 정리했다. 두 조치가
    # 겹쳐 **같은 사실이 다섯 번** 나왔다 — 본문 줄 + 확인사항 두 건이 최장 연속
    # 결측과 월별 결측률을 되풀이한다. 본문 줄만 남긴다.
    #
    # 부분 문자열로 걸러 왔던 자리는 **20세션에 사실 ID 로 갈음했다**
    # (:data:`MISSING_FACTS`). 문구가 바뀌어도 새지 않는다.
    st.caption("결측은 보간하지 않습니다.", help=manual_tip("data-quality"))


# --------------------------------------------------------------------- 부하 패턴


def _pattern_formulas(pattern: object) -> dict[str, str]:
    """지표 넷의 툴팁 — **산식 한 줄 + 의미 한 줄** (16세션 3절 · 21세션 2절).

    "부하율 42%" 만 보이면 무엇을 무엇으로 나눈 값인지 알 수 없어, 높은 것이
    좋은지 나쁜지조차 판단할 수 없다. 산식만 적어도 마찬가지다 — **그래서
    어떻다는 것인지**가 없으면 읽고도 할 일이 없다. 두 줄을 규약으로 둔다.
    시간대 경계는 자료마다 다르므로
    :class:`~kwise.quality.pattern.LoadPattern` 이 쓴 값을 그대로 적는다.

    **문구는 :mod:`kwise.report.narrative` 가 쥔다** (39세션 1-2). PPT 가 같은
    산식을 슬라이드 아래 작은 글씨로 까므로, 두 벌로 적으면 한쪽만 고쳐진다.
    """
    table = narrative.terms(pattern if isinstance(pattern, LoadPattern) else None)
    # **툴팁도 escape 한다** (25세션 2절). ``help=`` 는 마크다운을 해석한다.
    return {
        key: fmt.markdown_safe(table[key].tooltip)
        for key in ("load_factor", "base_load_ratio", "weekend_ratio", "off_hours_energy_share")
    }


def _pattern_block(diagnosis: Diagnosis, usage: UsageData, building: BuildingInfo | None) -> None:
    st.subheader("부하 패턴")
    pattern = diagnosis.pattern
    tips = _pattern_formulas(pattern)
    columns = st.columns(4)
    columns[0].metric("부하율", fmt.ratio_pct(pattern.load_factor), help=tips["load_factor"])
    columns[1].metric(
        "기저부하 비율", fmt.ratio_pct(pattern.base_load_ratio), help=tips["base_load_ratio"]
    )
    columns[2].metric(
        "주말 부하 비율", fmt.ratio_pct(pattern.weekend_ratio), help=tips["weekend_ratio"]
    )
    columns[3].metric(
        "운영시간 외 부하 비중",
        fmt.ratio_pct(pattern.off_hours_energy_share),
        help=tips["off_hours_energy_share"],
    )
    st.caption(
        # **물결표를 쓰지 않는다** — 한 줄에 둘이 들어가면 그 사이가 취소선이 된다
        # (13세션). 통합 시험이 이 자리를 잡았다 (15세션).
        f"야간 {pattern.night_hours[0]}{fmt.RANGE}{pattern.night_hours[1]}시 · "
        f"운영 {pattern.operating_hours[0]}{fmt.RANGE}{pattern.operating_hours[1]}시 기준.",
        help=manual_tip("load-pattern"),
    )
    _temperature_chart(usage, building)


def _temperature_chart(usage: UsageData, building: BuildingInfo | None) -> None:
    """일별 사용량과 일평균 기온을 겹쳐 그린다 (30세션 4절).

    **지역이 없거나 기상 자료가 없으면 그림을 감춘다.** 옆단 지역은 선택 입력이고,
    고른 격자·기간을 사전 취득분이 덮지 못할 수도 있다. 기온 없이 사용량만 그리면
    이 자리에서 아무 말도 하지 못한다 — 월별 최대수요·시간대별 프로파일이 이미
    사용량의 모양을 낸다. 그래서 **빈 축을 남기지 않고 사유 한 줄로 갈음한다.**

    기상은 **태양광이 쓰는 것과 같은 지역·기간·격자**다
    (:func:`kwise.ui.pipeline.daily_temperature`).
    """
    region_key = building.region_key if building else ""
    loaded = cached_daily_temperature(usage, usage_token(usage), region_key)
    if loaded is None or loaded[0].empty:
        st.caption("옆단에서 지역을 고르면 일평균 기온을 함께 그립니다.")
        return
    temperature, source = loaded
    st.altair_chart(charts.daily_temperature_chart(usage, temperature), width="stretch")
    # **출처를 태양광 카드와 같은 이름으로 적는다** (31세션 4-2). 같은 자료를
    # 쓰는데 한쪽만 출처를 적으면 두 그림이 다른 기상을 본다고 읽힌다.
    st.caption(
        f"일별 사용량과 일평균 기온 · {weather_source_label(source)}",
        help=fmt.chart_tip("chart.daily_temperature"),
    )


# --------------------------------------------------------------------- 계절 갈래


#: 계절 갈래. **요금표의 계절 정의를 그대로 쓴다** (30세션 5절) — 여름 6~8월 ·
#: 봄가을 3~5·9~10월 · 겨울 11~2월이 ``data\\tariff_*.json`` 의
#: ``season_definition`` 이고, 화면이 달을 다시 나누면 두 벌이 된다. 이름도
#: :data:`~kwise.tariff.labels.SEASON_LABELS` 를 그대로 쓴다.
SEASON_CHOICES: tuple[tuple[str, str | None], ...] = (
    ("전체", None),
    *((label, key) for key, label in SEASON_LABELS.items()),
)


def season_choices(available: Iterable[object]) -> tuple[tuple[str, str | None], ...]:
    """자료에 있는 계절만 갈래로 남긴다.

    **없는 계절 갈래를 두지 않는다.** 반년치 자료에 여름 탭을 만들면 빈 그림이
    나오고, 빈 그림은 「여름에 안 쓴다」 로 읽힌다. 계절이 하나도 갈리지 않으면
    「전체」 하나만 남고, 부르는 쪽이 갈래를 아예 그리지 않는다.
    """
    keys = set(map(str, available))
    return tuple(item for item in SEASON_CHOICES if item[1] is None or item[1] in keys)


def _season_chart(
    draw: Callable[[str | None], alt.Chart],
    available: Iterable[object],
    caption: str,
    tip_key: str,
) -> None:
    """계절 갈래를 탭으로 전환해 그린다. **넷을 한꺼번에 그리지 않는다.**

    캡션은 탭 **밖**에 한 번만 둔다 — 안에 넣으면 갈래 수만큼 같은 글이 늘어난다.
    """
    choices = season_choices(available)
    if len(choices) <= 1:
        st.altair_chart(draw(None), width="stretch")
    else:
        tabs = st.tabs([label for label, _key in choices])
        for tab, (_label, key) in zip(tabs, choices, strict=True):
            with tab:
                st.altair_chart(draw(key), width="stretch")
    st.caption(caption, help=fmt.chart_tip(tip_key))


# --------------------------------------------------------------------- 피크 특성


def _peak_block(diagnosis: Diagnosis) -> None:
    """**차트가 먼저다.** 상위 구간 분포가 태양광 판단의 근거다 (6.2)."""
    # **제목에 툴팁을 달지 않는다** (30세션 1-4). 절 제목은 자리 이름이지 지표가
    # 아니라 물음표가 붙을 자리가 아니다. 매뉴얼 앵커는 아래 상위 구간 지표로 옮겼다.
    st.subheader("피크 특성")
    peak = diagnosis.peak
    # **같은 값이면 한 줄로 접는다** (13세션). 연간 최대가 중간·최대부하 시간대에
    # 있으면 관측 최대와 요금적용 대상 최대가 같은 값이다 — 두 칸을 나란히 두면
    # 둘이 다른 개념인 줄 알고 차이를 찾게 된다. 야간 피크형에서만 갈린다.
    split = peak.billing_demand_kw < peak.peak_kw * 0.99
    columns = st.columns(3)
    if split:
        columns[0].metric("관측 최대수요", fmt.kw(peak.peak_kw))
        columns[1].metric("요금적용전력", fmt.kw(peak.billing_demand_kw))
    else:
        columns[0].metric("최대수요 = 요금적용전력", fmt.kw(peak.peak_kw))
        columns[1].metric("상위 구간 정오 비중", fmt.ratio_pct(diagnosis.summary.pv_midday_share))
    columns[2].metric(
        "상위 구간 주말 비중",
        fmt.ratio_pct(peak.weekend_slots / peak.top_n if peak.top_n else None),
        help=manual_tip("peak-profile"),
    )
    if split:
        st.caption(
            f"관측 최대 {fmt.kw(peak.peak_kw)} 중 경부하 시간대 구간은 요금적용전력 "
            f"대상에서 제외되어 {fmt.kw(peak.billing_demand_kw)} 가 적용됩니다.",
            help=manual_tip("billing-demand"),
        )
    st.altair_chart(charts.monthly_peak_chart(peak, split=split), width="stretch")
    st.caption("월별 최대수요", help=fmt.chart_tip("chart.monthly_peak"))
    # **시간대별 프로파일을 상위 구간 분포보다 먼저 둔다** (23세션 4절). 하루가
    # 어떻게 생겼는지를 본 뒤에 「그 중 가장 높은 100구간은 언제였나」 를 읽어야
    # 순서가 맞다 — 상위 구간부터 보면 무엇에 견주는 분포인지 알 수 없다.
    # **계절로 갈라 본다** (30세션 5-1). 여름 낮 봉우리와 겨울 아침 봉우리가 한
    # 그림에서 평균으로 뭉개지면 어느 계절을 겨냥해야 할지 알 수 없다.
    _season_chart(
        lambda season: charts.hourly_profile_chart(peak, season=season),
        peak.hourly_profile_by_season.columns,
        "시간대별 평균 부하",
        "chart.hourly_profile",
    )
    st.altair_chart(charts.top_hour_chart(peak, split=split), width="stretch")
    st.caption(f"상위 {peak.top_n}구간 발생 시각", help=fmt.chart_tip("chart.top_hour"))


# --------------------------------------------------------------------- 요금 구조


def _band_donuts(structure: ChargeStructure) -> None:
    """계시별 사용량 구성 **원 넷을 한 줄에** (34세션 1절).

    갈래는 계절 탭과 같다 (:func:`season_choices`). **탭이 아니라 한 줄이다** —
    갈아 끼우면 계절을 나란히 볼 수 없는데, 이 그림에서 볼 것이 바로 계절 사이의
    차이다. **없는 계절은 그리지 않는다.**

    **계절 이름과 합계는 그림 위에 Streamlit 텍스트로 둔다** (35세션 1-1·1-2).
    vega 제목으로 두었더니 층 차트의 높이 계산에 들어가지 않아 **윗부분이 잘렸고**,
    부제는 vega 기본색(검정)이라 다크 모드에서 배경에 묻혔다. 캡션으로 빼면 둘 다
    없어진다 — 글자색이 테마를 따르고, 잘릴 자리도 없다.

    **캡션이지 본문이 아니다.** 넷을 본문으로 적으면 이 자리의 본문 예산
    (3줄, ``screen_budget``)을 그림 이름 넷이 통째로 먹는다.
    """
    choices = season_choices(structure.band_season_kwh.index)
    columns = st.columns(len(choices))
    for column, (label, key) in zip(columns, choices, strict=True):
        total = float(charts.band_frame(structure, season=key)["사용량(kWh)"].sum())
        with column:
            st.caption(f"**{label}** · {fmt.mwh(total)}")
            st.altair_chart(charts.band_donut_chart(structure, season=key), width="stretch")
    st.caption("계시별 사용량 구성", help=fmt.chart_tip("chart.band"))


def _structure_block(usage: UsageData, diagnosis: Diagnosis, building: BuildingInfo | None) -> None:
    if diagnosis.structure is None:
        return
    st.subheader("현재 요금 구조")
    structure = diagnosis.structure
    # **합계를 함께 낸다** (27세션 3-1). 둘만 있으면 읽는 사람이 더해야 했고,
    # 그 합이 청구액인지도 알 수 없었다.
    #
    # **역률요금은 기본요금에 합쳐 적는다.** 기본요금의 ±% 조정이라 따로 세울
    # 값이 아니고, 요금 엔진의 12개월 환산도 둘을 함께 묶는다
    # (:meth:`~kwise.tariff.BillingResult.annualize`). 그래야 **기본 + 전력량 =
    # 합계**가 화면에서 그대로 성립한다 — 샘플은 역률 조정이 0원이라 값이 같다.
    base_won = structure.base_won + structure.bill.total_power_factor_won
    total_won = structure.total_won
    columns = st.columns(4)
    columns[0].metric("기본요금", fmt.won_short(base_won))
    columns[1].metric("전력량요금", fmt.won_short(structure.energy_won))
    columns[2].metric("합계", fmt.won_short(total_won))
    columns[3].metric(
        "기본요금 비중",
        fmt.ratio_pct(base_won / total_won if total_won else None),
        help=manual_tip("charge-structure"),
    )
    # **달마다 무엇이 달라지는지가 여기서 읽힌다** (27세션 3-2). 밑단(기본요금)이
    # 같은 높이로 이어지고 그 위 세 조각만 계절따라 움직이는 것이 이 요금제의
    # 모습이다. 33세션에 원으로 바꿨다가 **34세션에 되돌렸다** — 달의 높이를
    # 비교하는 그림은 막대가 맞다.
    st.altair_chart(charts.monthly_charge_chart(structure), width="stretch")
    st.caption("월별 요금 구성", help=fmt.chart_tip("chart.monthly_charge"))
    # **계절로 갈라 본다** (30세션 5-2 · 34세션 1절). 여름·겨울은 중간부하와
    # 최대부하의 경계가 아예 다른 시각이라, 셋을 합친 구성만 보면 어느 계절의
    # 최대부하를 옮겨야 하는지 알 수 없다. **탭에서 원 넷으로 바꿨다** — 여기서
    # 볼 것이 계절 사이의 차이인데 탭은 하나씩만 보여 준다.
    _band_donuts(structure)
    _intensity_line(usage, building)
    st.caption(
        "기본요금과 전력량요금만 계산합니다. 그 밖의 요금요소는 미포함이며 실제 절감액은 "
        "이보다 큽니다.",
        help=manual_tip("not-included"),
    )
    with st.expander("월별 명세", expanded=False):
        # **열 이름과 값을 한글로 낸다.** 번역표는 `kwise.report.columns` 한 곳에 있다.
        _monthly_table(structure.monthly)


#: 월별 명세에서 **화면에 낼 열** (21세션 3-2).
#:
#: 요금적용전력을 내는 데 쓰는 중간값이 넷이다 — 요금적용 대상 최대, 하한 적용
#: 전 수요, 기본요금 기준전력, 기본요금 일할 계수. 서로 값이 거의 같아 표에
#: 나란히 놓이면 무엇이 결론인지 알 수 없다. **결론 하나(요금적용전력)만 두고
#: 나머지는 Excel 「요금 계산 명세」 로 보낸다** — 지우는 것이 아니다.
SCREEN_MONTHLY_COLUMNS: tuple[str, ...] = (
    "season",  # 계절 — 여름·겨울 단가가 다르다
    "covered_days",  # 계량 일수 — 부분 월의 근거
    "is_partial",
    "missing_ratio",  # 결측률과 신뢰도 — 그 달 숫자를 믿을지 판단한다
    "demand_confidence",
    "max_demand_kw",  # 관측 최대수요 — 사용자가 아는 값
    "billing_demand_kw",  # 요금적용전력 — 기본요금이 왜 그 값인지
    "total_kwh",
    "base_won",
    "energy_won",
    "total_won",
)


def _monthly_table(monthly: pd.DataFrame) -> None:
    """월별 명세 — **사용자가 확인할 열만.**

    ``is_partial`` 은 체크만 보여서는 뜻을 알 수 없어 열 도움말을 붙인다
    (21세션 3-3).
    """
    columns = [name for name in SCREEN_MONTHLY_COLUMNS if name in monthly.columns]
    st.dataframe(
        localize(monthly[columns], index_name="월"),
        width="stretch",
        column_config={
            "부분 월": st.column_config.CheckboxColumn(
                "부분 월", help="검침 기간이 한 달에 못 미치는 달입니다."
            ),
        },
    )
    st.caption("계산에 쓴 중간값(하한 적용 전 수요·기본요금 기준전력 등)은 Excel 에 있습니다.")


def _intensity_line(usage: UsageData, building: BuildingInfo | None) -> None:
    """연면적 원단위 한 줄 (16세션 2절).

    **없으면 줄 자체가 없다.** 그리고 **국내 평균과 견주지 않는다** — 용도·기후·
    가동시간이 다른 건물의 평균은 이 건물의 판단 기준이 되지 못한다. 같은 건물의
    작년과 견주는 용도로만 쓴다.
    """
    intensity = intensity_kwh_per_m2(usage.meta.total_kwh, building)
    if intensity is None or building is None or building.floor_area_m2 is None:
        return
    span = usage.meta.period_days or 0
    label = "연간" if span >= 350 else "기간"
    st.write(
        f"{label} 원단위 **{fmt.count(intensity, 'kWh/m²', decimals=1)}** "
        f"(연면적 {fmt.count(building.floor_area_m2, 'm²', decimals=0)} 기준)"
    )
