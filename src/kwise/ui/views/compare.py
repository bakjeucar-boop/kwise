"""3단계 · 비교 (요구사항서 8장·9.2·10.1).

**조합마다 요금을 다시 계산한다.** 수단별 절감액을 더하지 않는다 — 태양광이
사용량을 줄이면 최적 선택요금이 바뀌고 ESS 가 피크를 낮추면 기본요금 기반이 바뀐다.

감도는 **범위**로 낸다. 세 값을 나란히 놓으면 "어느 쪽이 좋은 값인가" 를 찾게
되는데 이 축에는 좋고 나쁨이 없다 (9.2). 원자료 3행은 근거표로 접는다.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from kwise.compare import (
    SCENARIO_NAME_CAVEAT,
    SENSITIVITY_NOTE,
    CombinationSpec,
    ComparisonResult,
)
from kwise.diagnose import Diagnosis
from kwise.io import UsageData
from kwise.measures import default_target_pct, evaluate_demand_response
from kwise.quality import QualityReport
from kwise.report import ReportSections, measure_summary_frame, no_pv_sensitivity_frame
from kwise.report.notices import KNOWN_LIMITS, NOT_INCLUDED_NOTICE
from kwise.tariff import BillingResult, TariffTable
from kwise.ui import charts
from kwise.ui import text as fmt
from kwise.ui.anchors import detail_suffix
from kwise.ui.cache import (
    cached_comparison,
    cached_contract_adjustment,
    cached_ess,
    cached_power_factor,
    cached_sensitivity,
    cached_solar,
    cached_tariff_switch,
    cached_unit_pv,
    rules_stamp,
    usage_token,
)
from kwise.ui.pipeline import ContractForm, combination_specs
from kwise.ui.session import build_report_bytes
from kwise.ui.spec import review_scope
from kwise.ui.state import (
    enabled_measures,
    get_solar_inputs,
    input_key,
    measure_float,
    session_id,
)

__all__ = ["render"]


def render(
    usage: UsageData,
    table: TariffTable,
    form: ContractForm,
    diagnosis: Diagnosis,
    quality: QualityReport,
) -> None:
    st.header("3단계 · 비교")
    enabled = enabled_measures()
    scope = review_scope(enabled)

    # ---- 검토 범위. **빠진 것을 조용히 빼지 않는다.**
    with st.container(border=True):
        st.markdown("**검토 범위**")
        st.write("검토함 — " + (", ".join(scope.reviewed_labels) or "없음"))
        st.write("미검토 — " + (", ".join(scope.skipped_labels) or "없음"))
        st.caption("미검토는 '효과가 없다' 가 아니라 '보지 않았다' 입니다.")

    if diagnosis.structure is None:
        st.warning("계약 정보를 확정해야 조합을 비교합니다 (1단계).")
        return
    baseline = diagnosis.structure.bill

    unit_profile = None
    inputs = get_solar_inputs()
    capacity = 0.0
    if "solar" in enabled and inputs is not None:
        capacity = inputs.resolved_capacity_kwp()
        try:
            unit_profile, _source = cached_unit_pv(usage, usage_token(usage), inputs, rules_stamp())
        except Exception as exc:
            st.error(f"기상 자료를 얻지 못해 태양광을 조합에서 뺐습니다.\n\n{exc}")
            capacity = 0.0

    # 2단계에서 넣은 값을 그대로 읽는다 (위젯 키로 세션에 남는다).
    ess_target = measure_float("ess", "target") if "ess" in enabled else None
    specs = combination_specs(
        form=form,
        best_selection=(diagnosis.summary.best_selection or form.selection),
        enabled=enabled,
        pv_capacity_kwp=capacity if unit_profile is not None else 0.0,
        pv_unit_cost_won_per_kwp=inputs.unit_cost_won_per_kwp if inputs else None,
        pv_total_investment_won=inputs.total_investment_won if inputs else None,
        ess_target_kw=ess_target,
        ess_unit_cost_won_per_kw=measure_float("ess", "unit_cost"),
        ess_total_investment_won=measure_float("ess", "total_cost"),
    )
    if len(specs) == 1:
        st.info("2단계에서 수단을 하나 이상 켜면 조합을 비교합니다.")
        return

    comparison = cached_comparison(
        usage,
        table,
        baseline,
        unit_profile,
        quality,
        usage_token(usage),
        specs,
        _options_key(form),
        rules_stamp(),
        form.billing_options(),
    )

    frame = comparison.frame()
    st.dataframe(frame, width="stretch")
    st.altair_chart(charts.combination_chart(comparison), width="stretch")
    st.caption(
        "조합마다 요금을 다시 계산했습니다. 수단별 절감액의 합이 아닙니다. "
        + detail_suffix("certainty")
    )

    best = comparison.best
    columns = st.columns(4)
    columns[0].metric("최선 조합", best.name)
    columns[1].metric("절감액", fmt.won_short(best.saving_won))
    columns[2].metric("투자비", fmt.won_short(best.investment_won, reason="미산출 — 단가 미입력"))
    columns[3].metric(
        "회수기간",
        fmt.payback(best.payback_years, investment_won=best.investment_won),
        fmt.certainty_badge(best.certainty),
    )
    for message in comparison.warnings:
        st.warning(message)

    sensitivity_frame = _sensitivity_block(
        usage, table, baseline, unit_profile, quality, form, specs
    )
    measure_rows = _measure_rows(
        usage, table, form, diagnosis, quality, baseline, enabled, unit_profile
    )
    _download_block(usage, baseline, diagnosis, comparison, sensitivity_frame, measure_rows)

    with st.expander("미포함 요금요소와 알려진 한계", expanded=False):
        st.write(NOT_INCLUDED_NOTICE)
        for limit in KNOWN_LIMITS:
            st.write(f"- {limit}")
        st.caption(detail_suffix("known-limits"))


def _options_key(form: ContractForm) -> str:
    return f"{form.contract_kw}|{form.lagging_pct}|{form.leading_power_factor_pct}"


def _measure_rows(
    usage: UsageData,
    table: TariffTable,
    form: ContractForm,
    diagnosis: Diagnosis,
    quality: QualityReport,
    baseline: BillingResult,
    enabled: tuple[str, ...],
    unit_profile: pd.Series | None,
) -> pd.DataFrame:
    """산출물의 「수단별 결과」 시트.

    **켠 수단만 담는다.** 2단계에서 이미 계산한 것들이라 캐시에 걸린다. 하나도
    켜지 않았으면 빈 표가 나가고, 무엇을 보지 않았는지는 「검토 범위」가 밝힌다.
    """
    token = usage_token(usage)
    stamp = rules_stamp()

    switch = None
    if "tariff_switch" in enabled:
        switch = cached_tariff_switch(
            usage, table, quality, dict(diagnosis.option_totals), token, form, stamp
        )

    contract = None
    if "contract" in enabled and form.contract_kw is not None:
        contract = cached_contract_adjustment(
            usage,
            baseline,
            token,
            form.contract_kw,
            None,
            float(st.session_state.get(input_key("contract", "margin"), 0.1)),
            stamp,
        )

    demand_response = None
    if "demand_response" in enabled and diagnosis.dr is not None:
        demand_response = evaluate_demand_response(
            diagnosis.dr,
            unit_price_won_per_kwh=measure_float("demand_response", "unit_price"),
        )

    power_factor = None
    if "power_factor" in enabled:
        power_factor = cached_power_factor(
            usage,
            table,
            baseline,
            quality,
            token,
            form,
            measure_float("power_factor", "target") or default_target_pct(),
            measure_float("power_factor", "investment") or 0.0,
            stamp,
        )

    solar = None
    inputs = get_solar_inputs()
    if "solar" in enabled and inputs is not None and unit_profile is not None:
        solar = cached_solar(
            usage, table, unit_profile, baseline, quality, token, form, inputs, stamp
        ).points[-1]

    ess = None
    ess_target = measure_float("ess", "target")
    if "ess" in enabled and ess_target is not None:
        unit_cost = measure_float("ess", "unit_cost")
        total_cost = measure_float("ess", "total_cost")
        if unit_cost is not None or total_cost is not None:
            ess = cached_ess(
                usage,
                table,
                baseline,
                quality,
                token,
                form,
                ess_target,
                unit_cost,
                total_cost,
                stamp,
            )

    return measure_summary_frame(
        switch=switch,
        contract=contract,
        demand_response=demand_response,
        power_factor=power_factor,
        ess=ess,
        solar=solar,
    )


def _sensitivity_block(
    usage: UsageData,
    table: TariffTable,
    baseline: BillingResult,
    unit_profile: pd.Series | None,
    quality: QualityReport,
    form: ContractForm,
    specs: tuple[CombinationSpec, ...],
) -> pd.DataFrame:
    st.subheader("감도")
    pv_specs = [spec for spec in specs if spec.has_pv]
    if not pv_specs or unit_profile is None:
        st.info(
            "태양광이 없어 감도를 적용할 항목이 없습니다. 감도는 PV 출력의 첨예도에만 "
            "적용하며 요금제 전환·계약전력 조정·역률 개선은 확정 계산입니다."
        )
        return no_pv_sensitivity_frame()

    frame, ranges = cached_sensitivity(
        usage,
        table,
        baseline,
        unit_profile,
        quality,
        usage_token(usage),
        pv_specs[-1],
        _options_key(form),
        rules_stamp(),
        form.billing_options(),
    )
    for item in ranges:
        if item.base is not None:
            st.write(f"- {item.text()}")
    st.altair_chart(charts.sensitivity_chart(ranges), width="stretch")
    st.caption(SCENARIO_NAME_CAVEAT + " " + detail_suffix("sensitivity"))
    with st.expander("근거 — 시나리오 3행 원자료", expanded=False):
        st.dataframe(frame, width="stretch")
        st.caption(SENSITIVITY_NOTE)
    return frame


def _download_block(
    usage: UsageData,
    baseline: BillingResult,
    diagnosis: Diagnosis,
    comparison: ComparisonResult,
    sensitivity: pd.DataFrame,
    measure_rows: pd.DataFrame,
) -> None:
    st.subheader("내려받기")
    st.caption(
        "아홉 시트 통합문서입니다. 파일명에 날짜·시각이 붙습니다. " + detail_suffix("excel-report")
    )
    include_timeseries = st.checkbox("15분 시계열 시트 포함", value=True)
    if not st.button("Excel 만들기", type="primary"):
        return
    sections = ReportSections(
        usage=usage,
        bill=baseline,
        diagnosis=diagnosis,
        comparison=comparison,
        sensitivity=sensitivity,
        measure_rows=measure_rows,
        include_timeseries=include_timeseries,
    )
    try:
        payload, filename = build_report_bytes(sections, session_id=session_id())
    except Exception as exc:
        st.error(f"Excel 을 만들지 못했습니다.\n\n{exc}")
        return
    st.download_button(
        "내려받기",
        data=payload,
        file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    st.caption("서버에는 남기지 않습니다 — 만든 즉시 지웠습니다.")
