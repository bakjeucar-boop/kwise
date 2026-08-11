"""1단계 · 진단 (요구사항서 10.1·10.7).

**업로드 즉시, 설비 정보 없이** 나오는 화면이다. 묻는 것은 계약 정보 넷뿐이다.

순서는 **진단을 다 본 뒤 "그래서 무엇을 할 수 있나" 로 잇는다** (12세션).

    지표 카드 → 데이터 품질 → 부하 패턴 → 피크 특성 → 요금 구조
    → 계약전력 적정성 → **개선 여지 요약** → 2단계로

개선 여지 요약을 최상단에 두었더니 진단을 보기 전에 금액이 떠서 혼란스러웠다.
맨 아래, 다음 단계 단추 바로 위가 제자리다.

**차트를 표보다 앞에 둔다.** 월별 최대수요·상위 구간 시각 분포·시간대별
프로파일이 진단의 핵심인데 표 뒤에 있으면 눈에 들어오지 않는다.

안내는 심각도로 거른다 — **화면에는 차단과 주의만** (:mod:`kwise.ui.notices`).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from kwise.diagnose import Diagnosis
from kwise.io import UsageData
from kwise.quality import QualityReport
from kwise.report import localize
from kwise.tariff import TENTATIVE_BASE_FEE_BASIS_WARNING, TariffTable
from kwise.ui import charts
from kwise.ui import text as fmt
from kwise.ui.anchors import detail_suffix
from kwise.ui.cache import (
    cached_diagnosis,
    cached_quality,
    cached_usage,
    rules_stamp,
    usage_token,
)
from kwise.ui.labels import option_label, selection_label
from kwise.ui.nav import next_step_button
from kwise.ui.notices import Severity, partition, screen_notices
from kwise.ui.pipeline import (
    ContractForm,
    contract_type_choices,
    default_lagging_pct,
    guess_contract,
    option_choices,
    voltage_choices,
)
from kwise.ui.state import get_form, set_form, store_upload, upload

__all__ = ["render"]

_COLUMN_KEYS = ("diag_date_column", "diag_energy_column")


def render(table: TariffTable) -> None:
    st.header("📊 1단계 · 진단")
    st.caption(
        "파일만 올려도 결과가 나옵니다. 설비 정보는 묻지 않습니다. "
        + detail_suffix("improvement-summary")
    )

    uploaded = _upload_block()
    if uploaded is None:
        st.info("사용량 파일(csv·xls·xlsx)을 올려 주십시오. 한전 사이버지점 내려받기 형식입니다.")
        return

    usage = _load_with_columns(*uploaded)
    if usage is None:
        return

    form = _contract_block(table, usage)
    quality = cached_quality(usage, usage_token(usage), form.contract_kw if form else None)
    diagnosis = cached_diagnosis(
        usage,
        table,
        quality,
        usage_token(usage),
        form,
        rules_stamp(),
    )

    _headline_block(usage, diagnosis)
    _tentative_basis_block(table, form)
    _notice_block(quality, diagnosis)
    _quality_block(usage, quality, diagnosis)
    _pattern_block(diagnosis)
    _peak_block(diagnosis)
    _structure_block(diagnosis)
    _contract_adequacy_block(diagnosis)
    _summary_block(table, diagnosis)
    next_step_button("2단계 · 개선 수단", key="go_measures")


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
    columns[2].metric("부하율", fmt.ratio_pct(diagnosis.pattern.load_factor))
    # 1년치가 아닌 자료를 "연간" 이라 적으면 그 자체가 오독이다. 라벨을 기간에 맞춘다.
    span = meta.period_days or 0
    columns[3].metric("연간 사용량" if span >= 350 else "기간 사용량", fmt.mwh(meta.total_kwh))


# 결측 관련 문구는 「데이터 품질」 블록이 한 묶음으로 낸다. 위쪽 경고에서 뺀다.
MISSING_MARKERS = ("결측", "보간")


def _notice_block(quality: QualityReport, diagnosis: Diagnosis) -> None:
    """**차단과 주의만.** 참고 등급은 Excel 요약과 보고서 5장으로 간다 (10.7)."""
    _missing, rest = partition(
        screen_notices(quality.warnings, diagnosis.warnings), MISSING_MARKERS
    )
    for notice in rest:
        if notice.severity is Severity.BLOCK:
            st.error(notice.text)
        else:
            st.warning(notice.text)


# --------------------------------------------------------------------- 업로드·열 인식


def _upload_block() -> tuple[bytes, str] | None:
    uploaded = st.file_uploader(
        "사용량 데이터", type=["csv", "xls", "xlsx"], help="업로드 파일은 서버에 저장하지 않습니다."
    )
    if uploaded is not None:
        store_upload(uploaded.getvalue(), uploaded.name)
    return upload()


def _load_with_columns(data: bytes, filename: str) -> UsageData | None:
    """열 판정을 보여 주고 **드롭다운으로 고칠 수 있게** 한다 (10.1).

    자동 탐지는 언젠가 실패한다. 실패했을 때 화면에서 손쓸 수 없으면 파일을
    고쳐 다시 올리는 수밖에 없다.
    """
    date_override = st.session_state.get(_COLUMN_KEYS[0])
    energy_override = st.session_state.get(_COLUMN_KEYS[1])
    try:
        usage = cached_usage(
            data,
            filename,
            date_column=date_override,
            energy_column=energy_override,
        )
    except Exception as exc:
        st.error(f"파일을 읽지 못했습니다.\n\n{exc}")
        if (date_override or energy_override) and st.button("열 지정 되돌리기"):
            for key in _COLUMN_KEYS:
                st.session_state.pop(key, None)
            st.rerun()
        return None

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
        st.caption("자동 판정이 빗나갔으면 여기서 고칩니다. " + detail_suffix("column-detection"))
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
    return usage


# --------------------------------------------------------------------- 계약 정보


def _contract_block(table: TariffTable, usage: UsageData) -> ContractForm | None:
    """계약 정보 넷. **추정치를 제시하고 사용자가 확정한다** (3.2)."""
    saved = get_form()
    types = contract_type_choices(table)
    type_keys = [key for key, _label in types]
    type_labels = {key: label for key, label in types}

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
                help="데이터로는 추정할 수 없습니다. 청구서를 보고 고르십시오.",
            )
            guess = guess_contract(usage, contract_type)

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
            )
        with right:
            contract_kw = st.number_input(
                "계약전력 (kW)",
                min_value=0.0,
                value=(
                    float(saved.contract_kw) if saved and saved.contract_kw else guess.contract_kw
                ),
                step=1.0,
                help="청구서 기재값입니다. 계약 적정성 진단이 이 값을 전제로 합니다.",
            )
            options = option_choices(table, contract_type, voltage)
            default_option = saved.option if saved and saved.option in options else options[0]
            option = st.selectbox(
                "선택요금",
                options,
                index=options.index(default_option),
                format_func=option_label,
                help="현행 선택요금입니다. 다른 선택요금은 2단계에서 모두 다시 계산합니다.",
            )

        st.caption(
            f"추정 — 갑/을 {guess.tier_hint} · 관측 최대 {fmt.kw(guess.max_demand_kw)} · "
            f"연간 이용시간 {fmt.count(guess.utilization_hours, '시간')}. "
            + detail_suffix("contract-info")
        )
        st.caption(
            "계약전력은 **청구서 기재값**을 넣으십시오. 위 값은 관측 최대에 여유를 얹은 "
            "가늠이며, 계약 적정성 진단이 이 값을 전제로 합니다."
        )

        with st.expander("역률 (선택)", expanded=False):
            pf_left, pf_right = st.columns(2)
            with pf_left:
                lagging = st.number_input(
                    "주간 지상역률 (%)",
                    min_value=1.0,
                    max_value=100.0,
                    value=float(saved.lagging_pct) if saved else default_lagging_pct(),
                    step=0.1,
                    help="모르면 그대로 두십시오. 이 값에서 조정액이 0원입니다.",
                )
            with pf_right:
                known_leading = st.checkbox(
                    "야간 진상역률을 안다",
                    value=saved.leading_power_factor_pct is not None if saved else False,
                )
                leading = (
                    st.number_input(
                        "야간 진상역률 (%)", min_value=1.0, max_value=100.0, value=95.0, step=0.1
                    )
                    if known_leading
                    else None
                )
            st.caption(
                "모르면 지상으로 간주해 추가요금이 없습니다. "
                + detail_suffix("measure-power-factor")
            )

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
        st.warning(
            "계약 정보를 확정하기 전까지는 부하 패턴과 피크 특성만 나옵니다. "
            "금액은 계약 정보가 있어야 산출합니다."
        )
    return saved


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
    st.warning(
        f"**{contract_type.label}** — {TENTATIVE_BASE_FEE_BASIS_WARNING} "
        f"현재는 계약전력 {fmt.kw(form.contract_kw)} 기준으로 계산합니다. "
        + detail_suffix("measure-contract")
    )


# --------------------------------------------------------------------- 개선 여지 요약


def _summary_block(table: TariffTable, diagnosis: Diagnosis) -> None:
    """**진단을 다 본 뒤** "그래서 무엇을 할 수 있나" (6.5).

    화면에 코드 식별자를 내지 않는다 — 요금제 이름은 요금 데이터에서 가져온다.
    """
    st.subheader("개선 여지 — 투자 없이 가능한 절감액")
    summary = diagnosis.summary
    if summary.no_investment_saving_won is not None:
        saved_won = fmt.won_short(summary.no_investment_saving_won)
        st.success(f"투자 없이 **{saved_won}** 줄일 수 있습니다.")

    left, middle, right = st.columns(3)
    left.metric(
        "선택요금 전환",
        fmt.won_short(summary.tariff_switch_saving_won, reason="계약 정보 필요"),
    )
    middle.metric(
        "계약전력 조정",
        fmt.won_short(summary.contract_saving_won, reason="여유 없음"),
    )
    right.metric("태양광 기여 가능성", str(summary.pv_potential))

    if summary.best_selection is not None:
        best = selection_label(table, summary.best_selection)
        current = (
            selection_label(table, summary.current_selection)
            if summary.current_selection is not None
            else ""
        )
        if summary.best_selection != summary.current_selection:
            st.write(f"가장 유리한 요금제는 **{best}** 입니다. 현재는 {current} 입니다.")
        else:
            st.write(f"현재 요금제(**{current}**)가 이미 가장 유리합니다.")
    st.caption(
        f"상위 구간의 {summary.pv_midday_share:.0%}가 정오 시간대입니다. "
        f"산출 기간은 {summary.period_label or '—'} 입니다. " + detail_suffix("improvement-summary")
    )


# --------------------------------------------------------------------- 데이터 품질


def missing_lines(quality: QualityReport) -> tuple[str, ...]:
    """결측 안내 **한 묶음** (13세션).

    같은 사실이 세 군데서 세 번 나왔다 — 총 결측, 최장 연속, 월별 편중이 각각
    다른 문장으로 흩어져 있었다. 한 블록에 세 줄로 모은다. 세부는 확인사항으로.
    """
    lines = [
        f"결측 {fmt.count(quality.missing_slots, '구간')} / "
        f"{fmt.count(quality.expected_slots, '구간')} "
        f"({fmt.ratio_pct(quality.missing_ratio)}) · 보간하지 않고 계산에서 제외"
    ]
    gap = quality.longest_gap
    if gap is not None:
        lines.append(
            f"최장 연속 {fmt.count(gap.days, '일', decimals=2)} "
            f"({gap.start:%Y-%m-%d} {fmt.RANGE} {gap.end:%m-%d})"
        )
    for month in quality.flagged_months:
        lines.append(
            f"{month.month} 결측률 {fmt.ratio_pct(month.ratio)} → 해당 월 최대수요는 신뢰 제한"
        )
    return tuple(lines)


def _quality_block(usage: UsageData, quality: QualityReport, diagnosis: Diagnosis) -> None:
    """경고는 위쪽 :func:`_notice_block` 이 이미 냈다. 여기는 사실만 적는다."""
    st.subheader("데이터 품질")
    meta = usage.meta
    columns = st.columns(3)
    columns[0].metric("검침 간격", f"{meta.interval_minutes}분")
    columns[1].metric("결측", f"{fmt.ratio_pct(quality.missing_ratio)}")
    columns[2].metric("정전 추정", fmt.count(len(quality.outages), "건"))

    with st.container(border=True):
        for line in missing_lines(quality):
            st.write(line)
        # **결측 편중은 결과 해석을 바꾼다.** 발생 지점에 한 번만 적는다.
        if quality.skew.flagged:
            st.write(
                f"피크 시간대 편중 배수 {fmt.count(quality.skew.multiple, decimals=2)} → "
                "그 달의 최대수요가 실제보다 낮게 잡혔을 수 있음"
            )
    # 세부는 확인사항으로 내린다. 위쪽 경고 목록에서는 뺐다 (13세션).
    details, _rest = partition(
        screen_notices(quality.warnings, diagnosis.warnings), MISSING_MARKERS
    )
    if details:
        with st.expander(f"확인사항 {len(details)}건", expanded=False):
            for item in details:
                st.write(f"- {item.text}")
    st.caption(detail_suffix("data-quality"))


# --------------------------------------------------------------------- 부하 패턴


def _pattern_block(diagnosis: Diagnosis) -> None:
    st.subheader("부하 패턴")
    pattern = diagnosis.pattern
    columns = st.columns(4)
    columns[0].metric("부하율", fmt.ratio_pct(pattern.load_factor))
    columns[1].metric("기저부하 비율", fmt.ratio_pct(pattern.base_load_ratio))
    columns[2].metric("주말 부하 비율", fmt.ratio_pct(pattern.weekend_ratio))
    columns[3].metric("무인시간 부하 비중", fmt.ratio_pct(pattern.unattended_energy_share))
    st.caption(
        f"야간 {pattern.night_hours[0]}~{pattern.night_hours[1]}시 · "
        f"운영 {pattern.operating_hours[0]}~{pattern.operating_hours[1]}시 기준. "
        + detail_suffix("load-pattern")
    )


# --------------------------------------------------------------------- 피크 특성


def _peak_block(diagnosis: Diagnosis) -> None:
    """**차트가 먼저다.** 상위 구간 분포가 태양광 판단의 근거다 (6.2)."""
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
    )
    if split:
        st.caption(
            f"관측 최대 {fmt.kw(peak.peak_kw)} 중 경부하 시간대 구간은 요금적용전력 "
            f"대상에서 제외되어 {fmt.kw(peak.billing_demand_kw)} 가 적용됩니다. "
            + detail_suffix("billing-demand")
        )
    st.altair_chart(charts.monthly_peak_chart(peak, split=split), width="stretch")
    st.altair_chart(charts.top_hour_chart(peak, split=split), width="stretch")
    st.altair_chart(charts.hourly_profile_chart(peak), width="stretch")
    st.caption(
        "상위 구간의 시각 분포가 **태양광 기여 가능성을 즉시 보여 주는 지표**입니다. "
        + detail_suffix("peak-profile")
    )


# --------------------------------------------------------------------- 요금 구조


def _structure_block(diagnosis: Diagnosis) -> None:
    if diagnosis.structure is None:
        return
    st.subheader("현재 요금 구조")
    structure = diagnosis.structure
    columns = st.columns(3)
    columns[0].metric("기본요금", fmt.won_short(structure.base_won))
    columns[1].metric("전력량요금", fmt.won_short(structure.energy_won))
    columns[2].metric("기본요금 비중", fmt.ratio_pct(structure.base_share))
    st.altair_chart(charts.band_chart(structure), width="stretch")
    st.caption(
        "기본요금과 전력량요금만 계산합니다. 그 밖의 요금요소는 미포함이며 실제 절감액은 "
        "이보다 큽니다. " + detail_suffix("not-included")
    )
    with st.expander("월별 명세", expanded=False):
        # **열 이름을 한글로 낸다.** 번역표는 `kwise.report.columns` 한 곳에 있다.
        st.dataframe(localize(structure.monthly, index_name="월"), width="stretch")


# --------------------------------------------------------------------- 계약전력 적정성


def _contract_adequacy_block(diagnosis: Diagnosis) -> None:
    if diagnosis.contract is None:
        return
    st.subheader("계약전력 적정성")
    adequacy = diagnosis.contract
    columns = st.columns(4)
    columns[0].metric("계약전력", fmt.kw(adequacy.contract_kw))
    columns[1].metric("이용률", fmt.ratio_pct(adequacy.utilization))
    columns[2].metric("하향 여지", fmt.kw(adequacy.reduction_kw))
    columns[3].metric(
        "예상 절감액", fmt.won_short(adequacy.saving_won, reason=adequacy.saving_basis)
    )
    # 계약전력 변경 위험(9.4)은 위쪽 안내가 이미 냈다. 여기서 되풀이하지 않는다.
    st.caption(detail_suffix("contract-adequacy"))
