"""3단계 · 개선안 조합 (요구사항서 8장·9.2·10.1).

**조합은 사용자가 짠다** (16세션 5절). 개선안마다 체크박스를 두고, 기본은
2단계에서 켠 수단 전부다. 미리 정해 둔 조합 세트를 나란히 놓고 고르게 하던
방식은 **묻지 않은 것을 보여 주고 물은 것을 보여 주지 않았다** — 쓰는 사람은
"태양광은 빼고 나머지" 처럼 자기 조합을 알고 있는데 목록에 없었다.

    ① 개선안별 요약   2단계 카드 값 그대로
    ② 합산효과       체크한 조합을 **부하부터 다시 만들어** 한 번 계산
    ③ 내려받기       Excel · Word

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
    CombinationResult,
    CombinationSpec,
    ComparisonResult,
    SensitivityRange,
)
from kwise.diagnose import Diagnosis, default_margin_ratio
from kwise.diagnose.dr import DrProfile
from kwise.io import UsageData
from kwise.measures import (
    Certainty,
    ContractAdjustment,
    DemandResponseResult,
    EssResult,
    PowerFactorResult,
    SolarCurve,
    SolarPoint,
    SurplusResult,
    TariffSwitchResult,
    apply_generation,
    band_labels,
    default_target_pct,
    evaluate_contract_adjustment,
    evaluate_demand_response,
)
from kwise.notices import tooltip
from kwise.quality import QualityReport
from kwise.report import (
    SIMPLE_SUM_NOTE,
    DocumentSections,
    MeasureEntry,
    ReportSections,
    StandaloneRow,
    document_bytes,
    measure_entries,
    measure_summary_frame,
    no_pv_sensitivity_frame,
    simple_sum_won,
    standalone_frame,
    standalone_rows,
)
from kwise.report.days import RepresentativeDay
from kwise.tariff import BillingResult, TariffTable
from kwise.ui import callout
from kwise.ui import text as fmt
from kwise.ui.anchors import manual_tip
from kwise.ui.artifacts import recall, remember
from kwise.ui.building import NAME_MISSING, BuildingInfo
from kwise.ui.cache import (
    cached_comparison,
    cached_contract_adjustment,
    cached_ess,
    cached_ess_targets,
    cached_power_factor,
    cached_sensitivity,
    cached_solar,
    cached_surplus,
    cached_tariff_switch,
    cached_unit_pv,
    rules_stamp,
    usage_token,
)
from kwise.ui.context import AnalysisContext
from kwise.ui.labels import option_label
from kwise.ui.notices import screen_notices, tooltip_text
from kwise.ui.pipeline import ContractForm, combination_specs
from kwise.ui.progress import progress_panel
from kwise.ui.session import build_report_bytes
from kwise.ui.spec import ReviewScope, measure, review_scope
from kwise.ui.state import (
    enabled_measures,
    get_solar_inputs,
    input_key,
    measure_float,
    reference_day,
    session_id,
)

__all__ = ["render"]


def render(
    context: AnalysisContext,
    table: TariffTable,
    building: BuildingInfo | None = None,
) -> None:
    usage, form, diagnosis, quality = (
        context.usage,
        context.form,
        context.diagnosis,
        context.quality,
    )
    st.header("⚖ 3단계 · 개선안 조합")
    reviewed = enabled_measures()
    scope = review_scope(reviewed)

    # ---- 검토 범위. **빠진 것을 조용히 빼지 않는다.**
    with st.container(border=True):
        st.markdown("**검토 범위**")
        st.write("검토함 — " + (", ".join(scope.reviewed_labels) or "없음"))
        st.write("미검토 — " + (", ".join(scope.skipped_labels) or "없음"))
        st.caption("미검토는 '효과가 없다' 가 아니라 '보지 않았다' 입니다.")

    if diagnosis.structure is None:
        callout.caution("계약 정보를 확정해야 조합을 비교합니다 (1단계).")
        return
    baseline = diagnosis.structure.bill

    unit_profile = None
    inputs = get_solar_inputs()
    capacity = 0.0
    if "solar" in reviewed and inputs is not None:
        capacity = inputs.resolved_capacity_kwp()
        try:
            unit_profile, _source = cached_unit_pv(usage, usage_token(usage), inputs, rules_stamp())
        except Exception as exc:
            callout.blocked(f"기상 자료를 얻지 못해 태양광을 조합에서 뺐습니다. {exc}")
            capacity = 0.0

    # **① 개선안별 요약이 먼저다** (14세션 5-1). 2단계에서 이미 계산한 값을 옮긴다.
    # **조합에서 뺀 수단도 여기 남는다** — 뺀 것이 얼마짜리였는지 보여야 뺄지 말지
    # 정할 수 있다 (16세션 5절).
    results = _measure_results(
        usage, table, form, diagnosis, quality, baseline, reviewed, unit_profile
    )
    rows = results.standalone()
    _standalone_block(rows)

    # **조합은 사용자가 짠다** (16세션 5절). 기본은 2단계에서 켠 수단 전부다.
    enabled = _combination_picker(reviewed)

    # 2단계에서 넣은 값을 그대로 읽는다 (위젯 키로 세션에 남는다).
    ess_target = _ess_target(usage, table, form, diagnosis) if "ess" in enabled else None
    specs = combination_specs(
        form=form,
        best_selection=(diagnosis.summary.best_selection or form.selection),
        enabled=enabled,
        pv_capacity_kwp=capacity if unit_profile is not None and "solar" in enabled else 0.0,
        pv_unit_cost_won_per_kwp=inputs.unit_cost_won_per_kwp if inputs else None,
        pv_total_investment_won=inputs.total_investment_won if inputs else None,
        ess_target_kw=ess_target,
        ess_total_investment_won=measure_float("ess", "total_cost"),
        ess_fixed_won=measure_float("ess", "fixed_cost"),
        ess_per_kwh_won=measure_float("ess", "per_kwh_cost"),
        power_factor_pct=(
            measure_float("power_factor", "target") or default_target_pct()
            if "power_factor" in enabled
            else None
        ),
        power_factor_investment_won=measure_float("power_factor", "investment"),
    )

    if len(specs) == 1:
        callout.note("요금에 영향을 주는 개선안을 하나 이상 고르면 합산효과를 계산합니다.")
        _download_block(
            usage,
            baseline,
            diagnosis,
            None,
            no_pv_sensitivity_frame(),
            (),
            results,
            scope,
            building,
        )
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

    # **② 합산효과 — 단순 합과의 차이가 3단계의 존재 이유다** (14세션 5-2).
    _combined_block(usage, form, comparison, rows, results.contract, enabled)
    # **2단계 카드가 이미 낸 경고는 여기서 되풀이하지 않는다** (16세션 3절).
    # 세 화면이 한 번에 그려지므로 같은 문장이 두 번 뜬다 — 조합 자체의 경고만 남긴다.
    seen = results.warnings()
    fresh = tuple(item for item in comparison.notices if item.text not in seen)
    for notice in screen_notices(fresh):
        callout.render_notice(notice)
    # **근거는 툴팁 하나로** (19세션 1절). 조합 표의 숫자가 어떻게 만들어졌는지는
    # 매번 볼 것은 아니지만, 표를 믿을지 판단할 때 필요하다.
    grounds = tooltip(fresh)
    if grounds:
        st.caption(
            f"산출 근거 {len(grounds)}건",
            help=tooltip_text(fresh, header="**이 숫자가 어디서 나왔나**"),
        )

    sensitivity_frame, sensitivity_ranges = _sensitivity_block(
        usage, table, baseline, unit_profile, quality, form, specs
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
        building,
    )

    # 미포함 요금요소·알려진 한계는 **참고 등급**이다. 화면에서 빼고 Excel 요약
    # 시트와 보고서 5장에만 싣는다 (10.7).


_PICK_PREFIX = "combo_pick_"


def _pick_key(measure_key: str) -> str:
    return f"{_PICK_PREFIX}{measure_key}"


def _combination_picker(reviewed: tuple[str, ...]) -> tuple[str, ...]:
    """**조합을 사용자가 짠다** (16세션 5절).

    기본은 2단계에서 켠 수단 전부다. 체크를 풀면 그 수단을 뺀 조합으로 합산효과를
    다시 계산한다 — 조합마다 요금을 다시 계산하지만 캐시에 걸리므로 체크 한 번에
    다시 도는 것은 바뀐 조합 하나뿐이다.

    **7장 번호 순을 지킨다** — :func:`kwise.ui.state.enabled_measures` 가 그 순서로
    준다. 순서가 바뀌면 조합 이름이 실행마다 달라진다.
    """
    st.subheader("조합 구성")
    if not reviewed:
        callout.note("2단계에서 개선안을 하나도 켜지 않아 조합할 것이 없습니다.")
        return ()
    columns = st.columns(min(len(reviewed), 4))
    picked: list[str] = []
    for index, key in enumerate(reviewed):
        spec = measure(key)
        with columns[index % len(columns)]:
            # **기본이 참이다.** 2단계에서 켠 것을 3단계에서 다시 켜게 하면
            # 같은 판단을 두 번 시킨다.
            if st.checkbox(spec.label, value=True, key=_pick_key(key)):
                picked.append(key)
    st.caption(
        "체크한 개선안만 합산효과에 넣습니다. 체크를 풀어도 위의 개선안별 요약에는 "
        "남습니다 — 뺀 것이 얼마짜리였는지 보여야 뺄지 말지 정할 수 있습니다. "
        "경제성DR·잉여 활용은 요금이 아니라 별도 정산이라 합산효과에 들어가지 않습니다."
    )
    return tuple(picked)


def _standalone_block(rows: tuple[StandaloneRow, ...]) -> None:
    """**① 개선안별 요약** — 2단계 카드의 값을 그대로 옮긴다 (14세션 5-1).

    여기서 다시 계산하지 않는다. 두 화면의 숫자가 어긋나면 어느 쪽을 믿어야 할지
    알 수 없게 된다.
    """
    st.subheader("개선안별 요약")
    if not rows:
        callout.note("2단계에서 수단을 하나도 켜지 않았습니다.")
        return
    st.dataframe(standalone_frame(rows), hide_index=True, width="stretch")
    st.caption(
        "각 줄은 **그 수단만 도입했을 때**의 값입니다 (현재 요금제·현재 사용량 기준). "
        + SIMPLE_SUM_NOTE
        + " "
        + fmt.TRUNCATION_FOOTNOTE
    )


def _combined_block(
    usage: UsageData,
    form: ContractForm,
    comparison: ComparisonResult,
    rows: tuple[StandaloneRow, ...],
    contract: ContractAdjustment | None,
    chosen: tuple[str, ...],
) -> None:
    """**② 합산효과** — 단순 합과의 차이를 반드시 보인다 (14세션 5-2).

    조합의 마지막 항목이 **고른 수단을 모두 물린 것**이다. 부하를 처음부터 다시
    만들어 (``apply_generation`` → ``dispatch_peak_shaving``) 요금을 **한 번**
    계산한다.

    **단순 합도 고른 것만 더한다** (16세션 5절). 조합에서 뺀 수단을 단순 합에
    넣으면 차이가 상호작용이 아니라 「뺀 만큼」이 되어 뜻이 달라진다.
    """
    combined = comparison.combinations[-1]
    picked = tuple(row for row in rows if row.key in set(chosen))
    simple = simple_sum_won(picked, combinable_only=True)
    actual = combined.annual_saving_won
    gap = actual - simple
    ratio = gap / simple if simple else None

    st.subheader("합산효과")
    columns = st.columns(3)
    columns[0].metric("단순 합", fmt.won_short(simple))
    columns[1].metric("합산효과", fmt.won_short(actual))
    columns[2].metric(
        "차이",
        fmt.won_short(gap),
        fmt.ratio_pct(ratio) if ratio is not None else fmt.DASH,
    )
    st.caption(
        f"**{combined.name}** 를 함께 도입했을 때의 12개월 환산 절감액입니다. "
        "부하를 처음부터 다시 만들어 요금을 한 번 계산했습니다. " + fmt.TRUNCATION_FOOTNOTE
    )
    excluded = [row for row in rows if not row.combinable]
    if excluded:
        st.caption(
            "합산효과에 넣지 않은 수단 — "
            + ", ".join(row.title for row in excluded)
            + ". 요금이 아니라 별도 정산·수익이라 조합 부하에 얹을 수 없습니다."
        )
    dropped = [row for row in rows if row.combinable and row.key not in set(chosen)]
    if dropped:
        st.caption("조합에서 뺀 개선안 — " + ", ".join(row.title for row in dropped) + ".")

    reasons = _interaction_reasons(comparison, combined, picked)
    if reasons:
        st.markdown("**차이가 생기는 이유**")
        for reason in reasons:
            st.markdown(f"- {fmt.markdown_safe(reason)}")

    _contract_headroom(usage, form, combined, contract)


def _interaction_reasons(
    comparison: ComparisonResult,
    combined: CombinationResult,
    rows: tuple[StandaloneRow, ...],
) -> list[str]:
    """**실제로 발생한 상호작용만 적는다** (14세션 5-2). 해당 없으면 쓰지 않는다."""
    reasons: list[str] = []
    keys = set(combined.spec.measure_keys) | {
        row.key for row in rows if row.combinable and row.annual_saving_won is not None
    }
    baseline = comparison.baseline
    if combined.spec.selection != baseline.spec.selection:
        reasons.append(
            "**요금제가 바뀌면 다른 수단의 기준 단가가 바뀝니다.** 조합은 "
            f"{option_label(combined.spec.selection.option)} 로 계산했으므로, 현행 "
            "요금제를 기준으로 낸 2단계 값과 계약전력·역률·ESS 절감액이 다릅니다."
        )
    if "ess" in keys or "solar" in keys:
        reasons.append(
            "**기본요금 기반이 달라집니다.** 요금적용전력이 "
            f"{fmt.kw(baseline.billing_demand_kw)} 에서 "
            f"{fmt.kw(combined.billing_demand_kw)} 로 내려갔고, 그 위에서 남은 수단의 "
            "절감액이 다시 매겨집니다."
        )
    if "power_factor" in keys and ("solar" in keys or "ess" in keys):
        reasons.append(
            "**역률 감액은 기본요금에 비례합니다.** 태양광·ESS 가 기본요금을 낮추면 "
            "역률 감액도 함께 줄어 단순 합보다 작아집니다."
        )
    if "contract" in keys and ("solar" in keys or "ess" in keys):
        reasons.append(
            "**계약전력을 더 낮출 수 있습니다.** 태양광·ESS 로 요금적용전력이 "
            "내려가면 계약전력 하향 여지가 커집니다 (아래 참조)."
        )
    return reasons


def _contract_headroom(
    usage: UsageData,
    form: ContractForm,
    combined: CombinationResult,
    standalone: ContractAdjustment | None,
) -> None:
    """**계약전력 추가 하향 여지** — 조합 기준으로 여기서 낸다 (14세션 5-2).

    2단계 7.2 카드는 **현재 부하** 기준이라 다른 수단을 켜도 값이 바뀌지 않는다.
    조합 부하에서 얼마나 더 낮출 수 있는지는 이 자리에서만 계산한다.
    """
    if form.contract_kw is None:
        return
    margin = float(st.session_state.get(input_key("contract", "margin"), default_margin_ratio()))
    adjustment = evaluate_contract_adjustment(
        usage,
        combined.bill,
        contract_kw=form.contract_kw,
        margin_ratio=margin,
    )
    already = (standalone.annual_saving_won or 0.0) if standalone is not None else 0.0
    extra = (adjustment.annual_saving_won or 0.0) - already

    st.markdown("**계약전력 추가 하향 여지**")
    if adjustment.reduction_kw <= 0:
        st.write("이 조합에서도 계약전력을 더 낮출 여지가 없습니다.")
        return
    text = (
        f"이 조합이면 계약전력을 **{fmt.kw(adjustment.suggested_contract_kw, decimals=0)}** 로 "
        f"낮출 수 있습니다 (현행 {fmt.kw(form.contract_kw, decimals=0)}, 여유율 "
        f"{fmt.ratio_pct(margin, decimals=0)} 반영)."
    )
    if standalone is not None:
        text += (
            f" 현재 부하만 볼 때의 권장값은 "
            f"{fmt.kw(standalone.suggested_contract_kw, decimals=0)} 였습니다."
        )
    if adjustment.annual_saving_won is None:
        text += f" 금액은 {adjustment.saving_basis}."
    elif extra > 0:
        text += f" 추가 절감 **{fmt.won_short(extra)}/년**."
    else:
        text += (
            " 다만 기본요금이 요금적용전력 기준이라 계약전력을 낮춰도 요금은 "
            "줄지 않습니다 — 하한 규정에 걸리지 않습니다."
        )
    st.write(text)


def _ess_target(
    usage: UsageData, table: TariffTable, form: ContractForm, diagnosis: Diagnosis
) -> float | None:
    """ESS 목표 요금적용전력. **2단계를 거치지 않아도 같은 값이 나와야 한다** (15세션).

    2단계 카드가 세션에 남긴 값을 먼저 읽고, 없으면 곡선의 최소 지점을 **같은
    함수로** 다시 찾는다. 옆단에서 3단계로 바로 뛰면 세션이 비어 ESS 행이 통째로
    빠지던 자리다 — 통합 시험이 잡았다.
    """
    saved = measure_float("ess", "target")
    if saved is not None:
        return saved
    peak = diagnosis.peak.billing_demand_kw
    if peak <= 0:
        return None
    curve = cached_ess_targets(
        usage,
        usage_token(usage),
        float(peak),
        float(table.rates(form.selection).base_won_per_kw),
        measure_float("ess", "fixed_cost"),
        measure_float("ess", "per_kwh_cost"),
        rules_stamp(),
    )
    return curve.best.target_kw if curve.best is not None else None


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
    solar_curve: SolarCurve | None = None
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

    def warnings(self) -> frozenset[str]:
        """2단계 카드가 이미 낸 경고 (16세션 3절).

        탭 구조라 2·3단계가 함께 그려진다. 조합 경고에 같은 문장이 들어 있으면
        화면에 두 번 뜨므로, 3단계는 이 집합을 빼고 낸다.
        """
        sources = (
            self.switch,
            self.contract,
            self.demand_response,
            self.power_factor,
            self.solar_curve,
            self.ess,
            self.surplus,
        )
        return frozenset(
            item.text
            for source in sources
            if source is not None
            for item in getattr(source, "notices", ())
        )

    def standalone(self) -> tuple[StandaloneRow, ...]:
        """**① 개선안별 요약** — 2단계 카드 값을 그대로 옮긴다 (14세션 5-1)."""
        return standalone_rows(
            switch=self.switch,
            contract=self.contract,
            demand_response=self.demand_response,
            power_factor=self.power_factor,
            solar=self.solar,
            solar_certainty=self.solar_certainty,
            solar_investment_reason=self.solar_unpriced_reason,
            ess=self.ess,
            surplus=self.surplus,
        )

    # 차트 재료 (15세션 2절). **화면과 같은 프레임을 보고서에도 넘긴다.**
    usage: UsageData | None = None
    day: RepresentativeDay | None = None
    dr_profile: DrProfile | None = None
    solar_generation_kw: pd.Series | None = None
    ess_bands: pd.Series | None = None
    surplus_kw: pd.Series | None = None

    def entries(self) -> tuple[MeasureEntry, ...]:
        return measure_entries(
            switch=self.switch,
            contract=self.contract,
            demand_response=self.demand_response,
            power_factor=self.power_factor,
            solar=self.solar,
            solar_certainty=self.solar_certainty,
            solar_unpriced_reason=self.solar_unpriced_reason,
            solar_notices=self.solar_curve.notices if self.solar_curve is not None else (),
            ess=self.ess,
            surplus=self.surplus,
            usage=self.usage,
            day=self.day,
            dr_profile=self.dr_profile,
            solar_generation_kw=self.solar_generation_kw,
            ess_bands=self.ess_bands,
            surplus_kw=self.surplus_kw,
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
            # **2단계와 같은 여유율을 읽는다.** 슬라이더를 감춘 경우에도 2단계가
            # 세션에 남겨 두므로 두 화면의 값이 어긋나지 않는다 (14세션 5-1).
            float(st.session_state.get(input_key("contract", "margin"), default_margin_ratio())),
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
    solar_curve = None
    solar_certainty = None
    solar_reason = ""
    inputs = get_solar_inputs()
    if "solar" in enabled and inputs is not None and unit_profile is not None:
        curve = cached_solar(
            usage, table, unit_profile, baseline, quality, token, form, inputs, stamp
        )
        solar = curve.points[-1]
        solar_curve = curve
        solar_certainty = curve.certainty
        # 단가를 넣지 않았으면 **투자비 칸에 사유가 들어간다** (7.5).
        solar_reason = curve.cost.reason

    ess = None
    ess_target = _ess_target(usage, table, form, diagnosis)
    if "ess" in enabled and ess_target is not None:
        # **2단계와 같은 인자로 부른다** — 캐시에 걸려 같은 값이 나온다 (14세션 5-1).
        ess = cached_ess(
            usage,
            table,
            baseline,
            quality,
            token,
            form,
            ess_target,
            measure_float("ess", "total_cost"),
            stamp,
            measure_float("ess", "fixed_cost"),
            measure_float("ess", "per_kwh_cost"),
        )

    # 7.7 — **태양광이 없으면 잉여도 없다.** 카드는 그 사실만 적고 열려 있으므로
    # (14세션 2-3) 여기서도 계산할 것이 없다.
    surplus = None
    if "surplus" in enabled and inputs is not None and unit_profile is not None:
        capacity = inputs.resolved_capacity_kwp()
        if capacity > 0:
            surplus = cached_surplus(
                usage,
                table,
                unit_profile,
                token,
                form,
                capacity,
                measure_float("surplus", "price"),
                stamp,
            )

    # 보고서 차트 재료. **화면과 같은 대표일·같은 프로파일을 넘긴다** (15세션 2절).
    day = reference_day(usage)
    generation = None
    if solar is not None and unit_profile is not None:
        generation = unit_profile * solar.capacity_kwp
    bands = None
    if ess is not None:
        try:
            bands = band_labels(
                usage, table, selection=form.selection, options=form.billing_options()
            )
        except Exception:
            bands = None
    surplus_kw = None
    if surplus is not None and unit_profile is not None and inputs is not None:
        surplus_kw = apply_generation(
            usage, unit_profile * inputs.resolved_capacity_kwp()
        ).surplus_kw

    return _MeasureResults(
        switch=switch,
        contract=contract,
        demand_response=demand_response,
        power_factor=power_factor,
        solar=solar,
        solar_curve=solar_curve,
        solar_certainty=solar_certainty,
        solar_unpriced_reason=solar_reason,
        ess=ess,
        surplus=surplus,
        usage=usage,
        day=day,
        dr_profile=diagnosis.dr,
        solar_generation_kw=generation,
        ess_bands=bands,
        surplus_kw=surplus_kw,
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
    st.subheader("감도", help=fmt.TIPS["sensitivity"])
    pv_specs = [spec for spec in specs if spec.has_pv]
    if not pv_specs or unit_profile is None:
        callout.note(
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
            else f"- **{item.metric}** {fmt.markdown_safe(item.text())}"
        )
    st.caption(
        "태양광 발전 프로파일의 불확실성을 반영한 범위입니다.",
        help=manual_tip("sensitivity"),
    )
    return frame, ranges


def _download_block(
    usage: UsageData,
    baseline: BillingResult,
    diagnosis: Diagnosis,
    comparison: ComparisonResult | None,
    sensitivity: pd.DataFrame,
    sensitivity_ranges: tuple[SensitivityRange, ...],
    results: _MeasureResults,
    scope: ReviewScope,
    building: BuildingInfo | None,
) -> None:
    """산출물 둘 — 분석자용 Excel 과 의사결정자용 Word (10.3·10.5)."""
    st.subheader("내려받기")
    count = 0 if comparison is None else len(comparison.combinations)
    token = f"{usage_token(usage)}|{rules_stamp()}|{count}"
    excel_tab, word_tab = st.tabs(["Excel — 분석자용", "Word 보고서 — 의사결정자용"])

    with excel_tab:
        st.caption(
            "아홉 시트 통합문서입니다. 파일명에 날짜·시각이 붙습니다.",
            help=manual_tip("excel-report"),
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
                solar_curve=results.solar_curve,
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
        # **건물명은 옆단에서 온다** (16세션 2절). 같은 것을 두 곳에서 물으면
        # 두 값이 갈리고, 어느 쪽이 표지에 실릴지 알 수 없다.
        name = building.title if building is not None else NAME_MISSING
        st.caption(f"표지 이름 — **{name}** (옆단 「건물 정보」 에서 고칩니다)")
        word_token = f"{token}|{name}"
        if st.button("Word 보고서 만들기", type="primary", key="build_word"):
            document = DocumentSections(
                usage=usage,
                bill=baseline,
                diagnosis=diagnosis,
                comparison=comparison,
                sensitivity=sensitivity_ranges,
                measures=results.entries(),
                building_name=name,
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
        callout.blocked(f"{label} — 만들지 못했습니다. {exc}")
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
