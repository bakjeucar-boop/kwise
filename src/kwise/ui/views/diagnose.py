"""1단계 · 진단 (요구사항서 10.1).

**업로드 즉시, 설비 정보 없이** 나오는 화면이다. 묻는 것은 계약 정보 넷뿐이다.

화면 순서를 뒤집지 않는다 — **개선 여지 요약이 최상단**이다. 이것이 사용자가
처음 보는 숫자이고, 태양광부터 묻는 구조가 가리는 바로 그 값이다 (6.5).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from kwise.diagnose import Diagnosis
from kwise.io import UsageData
from kwise.quality import QualityReport
from kwise.tariff import TariffTable
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
    st.header("1단계 · 진단")
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

    _summary_block(diagnosis)
    _quality_block(usage, quality)
    _pattern_block(diagnosis)
    _peak_block(diagnosis)
    _structure_block(diagnosis)
    _contract_adequacy_block(diagnosis)
    _warning_block(diagnosis)


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

        contract_kw = st.number_input(
            "계약전력 (kW)",
            min_value=0.0,
            value=float(saved.contract_kw) if saved and saved.contract_kw else guess.contract_kw,
            step=1.0,
            help="청구서 기재값입니다. 계약 적정성 진단이 이 값을 전제로 합니다.",
        )

        options = option_choices(table, contract_type, voltage)
        default_option = saved.option if saved and saved.option in options else options[0]
        option = st.selectbox(
            "선택요금",
            options,
            index=options.index(default_option),
            help="현행 선택요금입니다. 다른 선택요금은 2단계에서 모두 다시 계산합니다.",
        )

        st.caption(
            f"추정 — 갑/을 {guess.tier_hint} · 관측 최대 {fmt.kw(guess.max_demand_kw)} · "
            f"연간 이용시간 {guess.utilization_hours:,.0f}시간. " + detail_suffix("contract-info")
        )
        for note in guess.notes:
            st.caption(f"· {note}")

        with st.expander("역률 (선택)", expanded=False):
            lagging = st.number_input(
                "주간 지상역률 (%)",
                min_value=1.0,
                max_value=100.0,
                value=float(saved.lagging_pct) if saved else default_lagging_pct(),
                step=0.1,
                help="약관 제42조의 무효전력계 미설치 간주값입니다. 이 값에서 조정액이 0원입니다.",
            )
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
                "모르면 지상으로 간주해 추가요금 0원입니다 (제43조 ② 2호 나목). "
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
            "금액은 계약 정보가 있어야 산출합니다 (요구사항서 6장)."
        )
    return saved


# --------------------------------------------------------------------- 개선 여지 요약


def _summary_block(diagnosis: Diagnosis) -> None:
    """**최상단.** 투자 없이 가능한 절감액이 첫 숫자다 (6.5)."""
    st.subheader("개선 여지 — 투자 없이 가능한 절감액")
    summary = diagnosis.summary
    left, middle, right = st.columns(3)
    left.metric(
        "선택요금 전환",
        fmt.won_short(summary.tariff_switch_saving_won, reason="계약 정보 필요"),
        help="같은 종별·전압의 다른 선택요금을 모두 다시 계산한 값입니다.",
    )
    middle.metric(
        "계약전력 조정",
        fmt.won_short(summary.contract_saving_won, reason="미산출"),
        help="하한 규정에 걸려 있을 때만 금액이 납니다.",
    )
    right.metric(
        "태양광 기여 가능성",
        str(summary.pv_potential),
        help=f"판정 모집단 — {summary.pv_basis}",
    )
    if summary.no_investment_saving_won is not None:
        st.success(
            f"투자 없이 **{fmt.won(summary.no_investment_saving_won)}** "
            f"({summary.period_label or '기간'} 기준)"
        )
    for line in summary.lines:
        st.caption(line)


# --------------------------------------------------------------------- 데이터 품질


def _quality_block(usage: UsageData, quality: QualityReport) -> None:
    st.subheader("데이터 품질")
    meta = usage.meta
    columns = st.columns(4)
    columns[0].metric("기간", f"{meta.start:%Y-%m-%d} ~ {meta.end:%Y-%m-%d}")
    columns[1].metric("검침 간격", f"{meta.interval_minutes}분")
    columns[2].metric("결측률", fmt.ratio_pct(quality.missing_ratio))
    columns[3].metric("총 사용량", fmt.mwh(meta.total_kwh))

    if quality.outages:
        st.caption(f"정전 추정 {len(quality.outages)}건 — 편중 판정에서 제외했습니다.")
    if quality.longest_gap is not None:
        gap = quality.longest_gap
        st.caption(
            f"최장 연속 결측 {gap.slots}슬롯 "
            f"({gap.start:%Y-%m-%d %H:%M} ~ {gap.end:%Y-%m-%d %H:%M})"
        )
    st.caption(
        "결측은 보간하지 않습니다. 계산에서 제외하고 그 사실을 표시합니다. "
        + detail_suffix("data-quality")
    )

    # **결측 편중은 경고다. 매뉴얼로 보내지 않는다** — 결과 해석을 바꾼다.
    if quality.skew.flagged:
        st.warning(
            f"결측이 피크 시간대에 몰려 있습니다 (편중 배수 {quality.skew.multiple:,.2f}). "
            "그 달의 최대수요가 실제보다 낮게 잡혔을 수 있습니다."
        )
    if quality.flagged_months:
        st.warning(
            "결측률이 높은 달 — "
            + ", ".join(f"{item.month} {item.ratio:.1%}" for item in quality.flagged_months)
            + ". 최대수요를 '신뢰 제한' 으로 봅니다."
        )
    with st.expander("품질 경고 전체", expanded=False):
        for message in quality.warnings:
            st.write(f"- {message}")


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
    st.subheader("피크 특성")
    peak = diagnosis.peak
    columns = st.columns(3)
    columns[0].metric("관측 최대수요", fmt.kw(peak.peak_kw))
    columns[1].metric("요금적용전력", fmt.kw(peak.billing_demand_kw))
    columns[2].metric(
        "상위 구간 주말 비중",
        fmt.ratio_pct(peak.weekend_slots / peak.top_n if peak.top_n else None),
    )
    if peak.billing_demand_kw < peak.peak_kw * 0.99:
        st.info(
            f"관측 최대({fmt.kw(peak.peak_kw)})보다 요금적용전력이 낮습니다 — "
            "경부하 시간대의 피크는 요금적용전력이 되지 않습니다 (약관 5.2 ①). "
            + detail_suffix("billing-demand")
        )
    st.altair_chart(charts.monthly_peak_chart(peak), width="stretch")
    st.altair_chart(charts.top_hour_chart(peak), width="stretch")
    st.caption(
        "상위 구간의 시각 분포가 **태양광 기여 가능성을 즉시 보여 주는 지표**입니다. "
        + detail_suffix("peak-profile")
    )
    with st.expander("시간대별 평균 부하", expanded=False):
        st.altair_chart(charts.hourly_profile_chart(peak), width="stretch")


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
        st.dataframe(structure.monthly, width="stretch")


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
    # **계약전력 변경 위험은 화면에 남긴다** (9.4). 매뉴얼로 보내면 읽지 않는다.
    st.warning(
        "기본요금은 직전 12개월 중 최대수요로 결정됩니다. 계약전력을 하향할 경우, "
        "예측 오차와 기상 변동을 고려하여 충분한 여유를 확보하십시오. "
        "한 번의 초과가 12개월간 적용됩니다."
    )
    st.caption(detail_suffix("contract-adequacy"))


def _warning_block(diagnosis: Diagnosis) -> None:
    if not diagnosis.warnings:
        return
    with st.expander(f"진단 경고 {len(diagnosis.warnings)}건", expanded=False):
        for message in diagnosis.warnings:
            st.write(f"- {message}")
