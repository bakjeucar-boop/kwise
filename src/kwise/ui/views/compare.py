"""3단계 · 비교 (요구사항서 8장·9.2·10.1).

**조합마다 요금을 다시 계산한다.** 수단별 절감액을 더하지 않는다 — 태양광이
사용량을 줄이면 최적 선택요금이 바뀌고 ESS 가 피크를 낮추면 기본요금 기반이 바뀐다.

감도는 **범위**로 낸다. 세 값을 나란히 놓으면 "어느 쪽이 좋은 값인가" 를 찾게
되는데 이 축에는 좋고 나쁨이 없다 (9.2). 원자료 3행은 근거표로 접는다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd
import streamlit as st

from kwise.compare import (
    CombinationSpec,
    ComparisonResult,
    SensitivityRange,
)
from kwise.diagnose import Diagnosis
from kwise.io import UsageData
from kwise.measures import (
    Certainty,
    ContractAdjustment,
    DemandResponseResult,
    EssResult,
    PowerFactorResult,
    SolarPoint,
    SurplusResult,
    TariffSwitchResult,
    default_target_pct,
    evaluate_demand_response,
)
from kwise.quality import QualityReport
from kwise.report import (
    DocumentSections,
    MeasureEntry,
    ReportSections,
    document_bytes,
    measure_entries,
    measure_summary_frame,
    no_pv_sensitivity_frame,
)
from kwise.tariff import BillingResult, TariffTable
from kwise.ui import charts
from kwise.ui import text as fmt
from kwise.ui.anchors import detail_suffix
from kwise.ui.artifacts import recall, remember
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
from kwise.ui.notices import screen_notices
from kwise.ui.pipeline import ContractForm, combination_specs
from kwise.ui.progress import progress_panel
from kwise.ui.session import build_report_bytes
from kwise.ui.spec import ReviewScope, measure, review_scope
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
    st.header("⚖ 3단계 · 비교")
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

    # 7단계. 조합마다 요금을 다시 계산하므로 조합 수가 늘면 그대로 길어진다.
    panel, runner = progress_panel("조합을 비교하는 중…")
    with panel, runner.running("compare", total_steps=len(specs)) as report:
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
            report,
        )

    # **지표 카드가 먼저, 차트, 표가 마지막이다** (12세션).
    best = comparison.best
    st.subheader(f"권장안 — {best.name}")
    columns = st.columns(4)
    columns[0].metric("12개월 환산 절감액", fmt.won_short(best.annual_saving_won))
    columns[1].metric("투자비", fmt.won_short(best.investment_won, reason="미산출 — 단가 미입력"))
    columns[2].metric(
        "회수기간", fmt.payback(best.payback_years, investment_won=best.investment_won)
    )
    columns[3].metric("확실성", str(best.certainty))

    st.altair_chart(charts.combination_chart(comparison), width="stretch")
    st.dataframe(_comparison_frame(comparison), hide_index=True, width="stretch")
    st.caption(
        "조합마다 요금을 다시 계산했습니다. 수단별 절감액의 합이 아닙니다. "
        + detail_suffix("certainty")
    )
    for notice in screen_notices(comparison.warnings):
        st.warning(notice.text)

    sensitivity_frame, sensitivity_ranges = _sensitivity_block(
        usage, table, baseline, unit_profile, quality, form, specs
    )
    results = _measure_results(
        usage, table, form, diagnosis, quality, baseline, enabled, unit_profile
    )
    _download_block(
        usage,
        baseline,
        diagnosis,
        comparison,
        sensitivity_frame,
        sensitivity_ranges,
        results,
        scope,
    )

    # 미포함 요금요소·알려진 한계는 **참고 등급**이다. 화면에서 빼고 Excel 요약
    # 시트와 보고서 5장에만 싣는다 (10.7).


def _measure_labels(keys: tuple[str, ...]) -> str:
    """``solar, ess`` 를 ``태양광, ESS`` 로. **화면에 코드 식별자를 내지 않는다** (10.7)."""
    return ", ".join(measure(key).label for key in keys) or "—"


def _comparison_frame(comparison: ComparisonResult) -> pd.DataFrame:
    """화면용 조합 표. 숫자는 문자열로 굳혀 세 자리 콤마를 보장한다."""
    rows = [
        {
            "조합": item.name,
            "수단": _measure_labels(item.spec.measures),
            "12개월 환산 절감액": fmt.won(item.annual_saving_won),
            "투자비": fmt.won(item.investment_won, reason="—"),
            "회수기간": fmt.payback(item.payback_years, investment_won=item.investment_won),
            "확실성": str(item.certainty),
        }
        for item in comparison.combinations
    ]
    return pd.DataFrame(rows)


def _options_key(form: ContractForm) -> str:
    return f"{form.contract_kw}|{form.lagging_pct}|{form.leading_power_factor_pct}"


@dataclass(frozen=True)
class _MeasureResults:
    """켠 수단의 계산 결과 한 벌.

    **Excel 시트와 Word 3장이 같은 것을 봐야 한다.** 각자 모으면 한쪽에만 든
    수단이 생기고, 두 산출물을 나란히 놓고서야 드러난다.
    """

    switch: TariffSwitchResult | None = None
    contract: ContractAdjustment | None = None
    demand_response: DemandResponseResult | None = None
    power_factor: PowerFactorResult | None = None
    solar: SolarPoint | None = None
    solar_certainty: Certainty | None = None
    solar_unpriced_reason: str = ""
    ess: EssResult | None = None
    surplus: SurplusResult | None = None

    def excel_frame(self) -> pd.DataFrame:
        return measure_summary_frame(
            switch=self.switch,
            contract=self.contract,
            demand_response=self.demand_response,
            power_factor=self.power_factor,
            ess=self.ess,
            solar=self.solar,
        )

    def entries(self) -> tuple[MeasureEntry, ...]:
        return measure_entries(
            switch=self.switch,
            contract=self.contract,
            demand_response=self.demand_response,
            power_factor=self.power_factor,
            solar=self.solar,
            solar_certainty=self.solar_certainty,
            solar_unpriced_reason=self.solar_unpriced_reason,
            ess=self.ess,
            surplus=self.surplus,
        )


def _measure_results(
    usage: UsageData,
    table: TariffTable,
    form: ContractForm,
    diagnosis: Diagnosis,
    quality: QualityReport,
    baseline: BillingResult,
    enabled: tuple[str, ...],
    unit_profile: pd.Series | None,
) -> _MeasureResults:
    """**켠 수단만 계산한다.** 2단계에서 이미 돌린 것들이라 캐시에 걸린다.

    하나도 켜지 않았으면 비어 있고, 무엇을 보지 않았는지는 「검토 범위」가 밝힌다.
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
    solar_certainty = None
    solar_reason = ""
    inputs = get_solar_inputs()
    if "solar" in enabled and inputs is not None and unit_profile is not None:
        curve = cached_solar(
            usage, table, unit_profile, baseline, quality, token, form, inputs, stamp
        )
        solar = curve.points[-1]
        solar_certainty = curve.certainty
        # 단가를 넣지 않았으면 **투자비 칸에 사유가 들어간다** (7.5).
        solar_reason = curve.cost.reason

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

    return _MeasureResults(
        switch=switch,
        contract=contract,
        demand_response=demand_response,
        power_factor=power_factor,
        solar=solar,
        solar_certainty=solar_certainty,
        solar_unpriced_reason=solar_reason,
        ess=ess,
    )


def _sensitivity_block(
    usage: UsageData,
    table: TariffTable,
    baseline: BillingResult,
    unit_profile: pd.Series | None,
    quality: QualityReport,
    form: ContractForm,
    specs: tuple[CombinationSpec, ...],
) -> tuple[pd.DataFrame, tuple[SensitivityRange, ...]]:
    st.subheader("감도")
    pv_specs = [spec for spec in specs if spec.has_pv]
    if not pv_specs or unit_profile is None:
        st.info(
            "태양광이 없어 감도를 적용할 항목이 없습니다. 감도는 PV 출력의 첨예도에만 "
            "적용하며 요금제 전환·계약전력 조정·역률 개선은 확정 계산입니다."
        )
        return no_pv_sensitivity_frame(), ()

    panel, runner = progress_panel("감도를 훑는 중…")
    with panel, runner.running("compare", total_steps=3) as report:
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
            report,
        )
    # **기준값 옆에 범위만 붙인다** (9.2·12세션).
    #
    # 감도 차트와 3행 원자료 표를 화면에서 뺐다. 계산 표의 숫자와 감도 숫자가
    # 나란히 놓이면 어느 것이 결과인지 알 수 없고, 그래프는 뜻이 읽히지 않는다.
    # 근거가 필요한 사람은 Excel 「감도 상세」 시트를 본다.
    for item in ranges:
        if item.base is None:
            continue
        st.write(
            f"- **{item.metric}** {fmt.money_range(item.base, item.low, item.high)}"
            if item.unit == "원"
            else f"- **{item.metric}** {item.text()}"
        )
    st.caption(
        "태양광 발전 프로파일의 불확실성을 반영한 범위입니다. " + detail_suffix("sensitivity")
    )
    return frame, ranges


def _download_block(
    usage: UsageData,
    baseline: BillingResult,
    diagnosis: Diagnosis,
    comparison: ComparisonResult,
    sensitivity: pd.DataFrame,
    sensitivity_ranges: tuple[SensitivityRange, ...],
    results: _MeasureResults,
    scope: ReviewScope,
) -> None:
    """산출물 둘 — 분석자용 Excel 과 의사결정자용 Word (10.3·10.5)."""
    st.subheader("내려받기")
    token = f"{usage_token(usage)}|{rules_stamp()}|{len(comparison.combinations)}"
    excel_tab, word_tab = st.tabs(["Excel — 분석자용", "Word 보고서 — 의사결정자용"])

    with excel_tab:
        st.caption(
            "아홉 시트 통합문서입니다. 파일명에 날짜·시각이 붙습니다. "
            + detail_suffix("excel-report")
        )
        include_timeseries = st.checkbox("15분 시계열 시트 포함", value=True)
        excel_token = f"{token}|{include_timeseries}"
        if st.button("Excel 만들기", type="primary", key="build_excel"):
            sections = ReportSections(
                usage=usage,
                bill=baseline,
                diagnosis=diagnosis,
                comparison=comparison,
                sensitivity=sensitivity,
                measure_rows=results.excel_frame(),
                include_timeseries=include_timeseries,
            )
            _build(
                lambda: build_report_bytes(sections, session_id=session_id()),
                slot="excel",
                label="Excel",
                token=excel_token,
            )
        _offer(
            slot="excel",
            token=excel_token,
            label="Excel 내려받기",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="dl_excel",
        )

    with word_tab:
        st.caption(
            "결론부터 쓴 다섯 장짜리 보고서입니다. **표는 Word 표 객체라** 제안서에 "
            "그대로 복사해 쓸 수 있습니다."
        )
        building = st.text_input(
            "건물명", value=usage.meta.source_name, help="표지와 본문에 들어갑니다."
        )
        word_token = f"{token}|{building}"
        if st.button("Word 보고서 만들기", type="primary", key="build_word"):
            document = DocumentSections(
                usage=usage,
                bill=baseline,
                diagnosis=diagnosis,
                comparison=comparison,
                sensitivity=sensitivity_ranges,
                measures=results.entries(),
                building_name=building,
                reviewed_labels=scope.reviewed_labels,
                skipped_labels=scope.skipped_labels,
            )
            _build(
                lambda: document_bytes(document),
                slot="word",
                label="Word 보고서",
                token=word_token,
            )
        _offer(
            slot="word",
            token=word_token,
            label="Word 내려받기",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            key="dl_word",
        )


def _build(make: Callable[[], tuple[bytes, str]], *, slot: str, label: str, token: str) -> None:
    """만들어 **세션에 담는다.** 내려받기 단추는 담긴 것을 참조만 한다.

    바이트를 담지 않고 만들기 분기 안에서 단추를 그리면, 그 단추를 누른 rerun 에서
    만들기 분기가 다시 타지 않아 **단추도 화면 결과도 사라진다** (12세션).
    """
    try:
        payload, filename = make()
    except Exception as exc:
        st.error(f"{label} — 만들지 못했습니다.\n\n{exc}")
        return
    remember(slot, payload, filename, token=token)


def _offer(*, slot: str, token: str, label: str, mime: str, key: str) -> None:
    """담아 둔 바이트를 내려받게 한다. **서버에 남기지 않는다** (10.2)."""
    artifact = recall(slot, token=token)
    if artifact is None:
        return
    st.download_button(
        label, data=artifact.payload, file_name=artifact.filename, mime=mime, key=key
    )
    st.caption(
        f"{artifact.filename} · {fmt.count(artifact.kilobytes, 'KB')} — 서버에는 남기지 않습니다."
    )
