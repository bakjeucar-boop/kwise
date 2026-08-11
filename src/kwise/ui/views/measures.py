"""2단계 · 개선 수단 (요구사항서 7장·10.1).

카드로 켜고 끈다. **투자비 순으로 배치하고 접힌 상태로 시작한다.** 순서는
:data:`kwise.ui.spec.MEASURES` 가 쥐고 있으며 바꾸지 않는다.

**태양광을 켜지 않아도 1단계와 투자 0원 수단은 그대로 동작한다.** 켜지 않은
수단은 3단계 조합 비교와 산출물에서 빠지고, 그 사실을 「검토 범위」가 밝힌다.
"""

from __future__ import annotations

import streamlit as st

from kwise.diagnose import Diagnosis
from kwise.io import UsageData
from kwise.measures import (
    ELIGIBILITY_NOTICE,
    analyze_peak_excess,
    default_target_pct,
    evaluate_demand_response,
    high_rate_discharge_hours,
    load_ess_cost_reference,
    power_factor_floor_pct,
    reference_table,
    required_discharge_hours,
)
from kwise.pv import capacity_preview, list_provinces, list_sigungu, load_pv_presets
from kwise.quality import QualityReport
from kwise.tariff import TariffTable
from kwise.ui import charts
from kwise.ui import text as fmt
from kwise.ui.anchors import detail_suffix
from kwise.ui.cache import (
    cached_contract_adjustment,
    cached_ess,
    cached_power_factor,
    cached_solar,
    cached_surplus,
    cached_tariff_switch,
    cached_unit_pv,
    rules_stamp,
    usage_token,
)
from kwise.ui.labels import option_label, selection_label
from kwise.ui.nav import next_step_button
from kwise.ui.notices import Severity, screen_notices
from kwise.ui.pipeline import ContractForm, SolarInputs
from kwise.ui.progress import progress_panel
from kwise.ui.spec import MEASURES, MeasureSpec
from kwise.ui.state import get_solar_inputs, input_key, set_solar_inputs, toggle_key

__all__ = ["render"]


def render(
    usage: UsageData,
    table: TariffTable,
    form: ContractForm,
    diagnosis: Diagnosis,
    quality: QualityReport,
) -> None:
    st.header("🛠 2단계 · 개선 수단")
    st.caption(
        "투자비 순으로 놓았습니다. 켠 수단만 3단계 비교와 산출물에 들어갑니다. "
        + detail_suffix("combination")
    )
    # **기준선을 한 번만 밝힌다.** 카드마다 되풀이하면 읽히지 않는다 (10.7).
    st.info(
        "이 절감액은 현재 요금제를 유지한다고 보고 계산했습니다. "
        "요금제를 함께 바꾸면 3단계 조합 비교에서 다시 계산됩니다."
    )
    baseline = diagnosis.structure.bill if diagnosis.structure is not None else None

    tier = ""
    for spec in MEASURES:
        if spec.tier != tier:
            tier = spec.tier
            st.subheader(tier)
        _card(spec, usage, table, form, diagnosis, quality, baseline)

    next_step_button("3단계 · 비교", key="go_compare")


# 수단 헤더에만 이모지를 둔다 (10.7). 카드가 일곱이라 접힌 목록에서 눈이 걸릴
# 표지가 하나쯤 필요하다. 본문·표·안내에는 쓰지 않는다.
_ICONS: dict[str, str] = {
    "tariff_switch": "🔀",
    "contract": "📐",
    "demand_response": "📉",
    "power_factor": "🔌",
    "solar": "☀",
    "ess": "🔋",
    "surplus": "♻",
}


def _card(
    spec: MeasureSpec,
    usage: UsageData,
    table: TariffTable,
    form: ContractForm,
    diagnosis: Diagnosis,
    quality: QualityReport,
    baseline: object,
) -> None:
    """**모든 카드가 접힌 상태로 시작한다.**

    수단마다 펼침 상태가 다르면 어디까지 봤는지 알 수 없다. 켜는 것과 펴는 것을
    갈라 두어, 켜 두고 접어 놓아도 3단계 조합에는 그대로 들어간다.
    """
    icon = _ICONS[spec.key]
    enabled = st.toggle(f"{icon} {spec.title}", key=toggle_key(spec.key))
    if not enabled:
        return
    with st.expander(f"{icon} {spec.title} — 입력과 결과", expanded=False):
        st.caption(spec.headline + " " + detail_suffix(spec.anchor))
        handler = _HANDLERS[spec.key]
        handler(usage, table, form, diagnosis, quality, baseline)


# --------------------------------------------------------------------- 7.1


def _tariff_switch(
    usage: UsageData,
    table: TariffTable,
    form: ContractForm,
    diagnosis: Diagnosis,
    quality: QualityReport,
    baseline: object,
) -> None:
    result = cached_tariff_switch(
        usage,
        table,
        quality,
        dict(diagnosis.option_totals),
        usage_token(usage),
        form,
        rules_stamp(),
    )
    columns = st.columns(3)
    columns[0].metric("현행", option_label(result.current.selection.option))
    columns[1].metric("가장 유리한 요금제", option_label(result.best.selection.option))
    columns[2].metric(
        "절감액", fmt.won_short(result.saving_won), fmt.certainty_badge(result.certainty)
    )
    if result.switch_needed:
        st.write(
            f"가장 유리한 요금제는 **{selection_label(table, result.best.selection)}** 입니다."
        )
    else:
        st.write("현재 요금제가 이미 가장 유리합니다. 바꿀 이유가 없습니다.")
    st.dataframe(
        {
            "요금제": [option_label(quote.selection.option) for quote in result.ranking],
            "총 요금": [fmt.won(quote.total_won) for quote in result.ranking],
        },
        hide_index=True,
        width="stretch",
    )
    _notes(result.warnings, result.notes)


# --------------------------------------------------------------------- 7.2


def _contract(
    usage: UsageData,
    table: TariffTable,
    form: ContractForm,
    diagnosis: Diagnosis,
    quality: QualityReport,
    baseline: object,
) -> None:
    if form.contract_kw is None or baseline is None:
        st.warning("계약전력을 입력해야 조정 여지를 봅니다 (1단계 계약 정보).")
        return
    margin = st.slider(
        "확보할 여유율",
        min_value=0.0,
        max_value=0.3,
        value=0.1,
        step=0.01,
        format="%.0f%%",
        key=input_key("contract", "margin"),
    )
    result = cached_contract_adjustment(
        usage,
        baseline,  # type: ignore[arg-type]
        usage_token(usage),
        form.contract_kw,
        None,
        margin,
        rules_stamp(),
    )
    columns = st.columns(4)
    columns[0].metric("현행 계약전력", fmt.kw(result.contract_kw))
    columns[1].metric("권장", fmt.kw(result.suggested_contract_kw))
    columns[2].metric("하향 여지", fmt.kw(result.reduction_kw))
    columns[3].metric("절감액", fmt.won_short(result.saving_won, reason=result.saving_basis))
    # 계약전력 변경 위험(9.4)은 result.warnings 에 들어 있다. 확인사항에서 한 번만 낸다.
    _notes(result.warnings, result.notes)


# --------------------------------------------------------------------- 7.3


def _demand_response(
    usage: UsageData,
    table: TariffTable,
    form: ContractForm,
    diagnosis: Diagnosis,
    quality: QualityReport,
    baseline: object,
) -> None:
    if diagnosis.dr is None:
        st.warning("경제성DR 참여 여력을 산출하지 못했습니다.")
        return
    left, right = st.columns(2)
    with left:
        priced = st.checkbox("정산 단가를 안다 (사업자 제시값)", value=False)
        unit_price = (
            st.number_input(
                "정산 단가 (원/kWh)",
                min_value=0.0,
                value=0.0,
                step=1.0,
                key=input_key("demand_response", "unit_price"),
            )
            if priced
            else None
        )
    with right:
        # **예상 참여일에 기본값을 두지 않는다.** 지어내면 그 값으로 금액이 난다.
        known_days = st.checkbox("예상 참여일 수를 안다", value=False)
        expected_days = (
            st.number_input(
                "예상 참여일 (일)",
                min_value=1,
                max_value=int(diagnosis.dr.eligible_days),
                value=int(diagnosis.dr.eligible_days),
                step=1,
                key=input_key("demand_response", "days"),
            )
            if known_days
            else None
        )
    result = evaluate_demand_response(diagnosis.dr, unit_price_won_per_kwh=unit_price or None)

    # **라벨이 '최대'다.** 거래 가능일 전체에 참여한다고 본 이론적 최대치다.
    share = (expected_days / result.eligible_days) if expected_days else 1.0
    columns = st.columns(3)
    columns[0].metric("거래 가능일", fmt.days(result.eligible_days))
    columns[1].metric("등록 권장 용량", fmt.kw(result.registered_capacity_kw))
    columns[2].metric(
        "예상 감축량" if expected_days else "연간 최대 가능량",
        fmt.kwh(result.annual_reducible_kwh * share),
    )
    if expected_days:
        st.caption(
            f"참여 {fmt.days(expected_days)} 기준입니다. 거래 가능일 전체"
            f"({fmt.days(result.eligible_days)}) 기준 최대 가능량은 "
            f"{fmt.kwh(result.annual_reducible_kwh)} 입니다."
        )
    st.warning(
        "거래 가능일 전체에 참여한다고 가정한 최대치입니다. 실제 참여일은 시장 상황에 "
        "따라 크게 줄어듭니다. **수요관리사업자와 상담해 예상 참여일을 확인하십시오.**"
    )
    if result.is_priced:
        st.metric("정산금", fmt.won_short((result.settlement_won or 0.0) * share))
    st.caption(
        "자원 유형 — " + (", ".join(str(item) for item in result.resource_types) or "판정 불가")
    )
    _notes(result.warnings, result.notes)


# --------------------------------------------------------------------- 7.4


def _power_factor(
    usage: UsageData,
    table: TariffTable,
    form: ContractForm,
    diagnosis: Diagnosis,
    quality: QualityReport,
    baseline: object,
) -> None:
    floor = power_factor_floor_pct()
    left, right = st.columns(2)
    with left:
        target = st.number_input(
            "도입 후 지상역률 (%)",
            min_value=float(floor),
            max_value=100.0,
            value=default_target_pct(),
            step=0.1,
            key=input_key("power_factor", "target"),
        )
    with right:
        investment = st.number_input(
            "콘덴서·APFR 투자비 (원)",
            min_value=0.0,
            value=0.0,
            step=100_000.0,
            key=input_key("power_factor", "investment"),
        )
    result = cached_power_factor(
        usage,
        table,
        baseline,  # type: ignore[arg-type]
        quality,
        usage_token(usage),
        form,
        target,
        investment,
        rules_stamp(),
    )
    columns = st.columns(4)
    columns[0].metric("현재 역률", fmt.pct(result.current_pct))
    columns[1].metric("도입 후", fmt.pct(result.target_pct))
    columns[2].metric("절감액", fmt.won_short(result.saving_won))
    columns[3].metric(
        "회수기간", fmt.payback(result.payback_years, investment_won=result.investment_won)
    )
    # **92% 미달 경고는 화면에 남긴다** — 결과 해석을 바꾼다 (10.2 예외).
    from kwise.tariff import lagging_standard_pct

    standard = lagging_standard_pct()
    if result.current_pct < standard:
        st.warning(
            f"현재 지상역률이 기준 {standard:,.0f}% 에 미달합니다 "
            f"({fmt.pct(result.current_pct)}). 매 1%p 마다 기본요금이 추가됩니다."
        )
    _notes(result.warnings, result.notes)


# --------------------------------------------------------------------- 7.5


def _solar(
    usage: UsageData,
    table: TariffTable,
    form: ContractForm,
    diagnosis: Diagnosis,
    quality: QualityReport,
    baseline: object,
) -> None:
    presets = load_pv_presets()
    saved = get_solar_inputs()

    # ---- 기본 입력 셋 (3.3). **3열로 나눈다** — 셋뿐이므로 한 줄에 들어간다.
    area_col, density_col, region_col = st.columns(3)
    with area_col:
        area = st.number_input(
            "설치 가능 면적 (m²)",
            min_value=0.0,
            value=float(saved.area_m2) if saved else 1000.0,
            step=50.0,
        )
    density_keys = [item.key for item in presets.densities]
    density_labels = {item.key: item.label for item in presets.densities}
    default_density = (
        saved.density_key if saved and saved.density_key in density_keys else presets.default.key
    )
    with density_col:
        density = st.radio(
            presets.density_label,
            density_keys,
            index=density_keys.index(default_density),
            format_func=lambda key: density_labels[key],
            horizontal=True,
        )
    # **선택 즉시 환산 용량과 상충 관계를 함께 보여 준다** (3.3).
    for preset, capacity in capacity_preview(area, presets):
        marker = "**" if preset.key == density else ""
        st.caption(f"{marker}{preset.label} — {fmt.kwp(capacity)}{marker} · {preset.tradeoff}")

    provinces = list_provinces()
    default_region = saved.region_key if saved else None
    province_default = default_region.split("/", 1)[0] if default_region else provinces[0]
    with region_col:
        province = st.selectbox(
            "시도",
            provinces,
            index=provinces.index(province_default) if province_default in provinces else 0,
        )
        regions = list_sigungu(province)
        region_keys = [item.key for item in regions]
        region_labels = {item.key: item.name for item in regions}
        region_default = default_region if default_region in region_keys else region_keys[0]
        region_key = st.selectbox(
            "시군구",
            region_keys,
            index=region_keys.index(region_default),
            format_func=lambda key: region_labels[key],
            help="기상 격자가 25~31 km 라 같은 격자면 결과가 같습니다.",
        )

    unit_cost = st.number_input(
        "설치 단가 (원/kWp)",
        min_value=0.0,
        value=float(saved.unit_cost_won_per_kwp or 0.0) if saved else 0.0,
        step=10_000.0,
        help="견적 단가입니다. 넣지 않으면 투자비와 회수기간을 산출하지 않습니다.",
    )

    # ---- 확장 패널 (접어 둔다)
    with st.expander("상세 (접어 둠)", expanded=False):
        detail_left, detail_right = st.columns(2)
        with detail_left:
            capacity_override = st.number_input(
                "설치 용량 직접 입력 (kWp) — 0 이면 면적 환산",
                min_value=0.0,
                value=0.0,
                step=10.0,
            )
            azimuth = st.number_input(
                "방위각 (도)",
                min_value=0.0,
                max_value=360.0,
                value=presets.default_azimuth_deg,
                step=5.0,
            )
        with detail_right:
            loss = st.slider("시스템 손실", min_value=0.0, max_value=0.4, value=0.14, step=0.01)
            total_cost = st.number_input(
                "총 투자비 직접 입력 (원) — 0 이면 단가 사용",
                min_value=0.0,
                value=0.0,
                step=1_000_000.0,
            )

    inputs = SolarInputs(
        region_key=region_key,
        area_m2=area,
        density_key=density,
        capacity_kwp=capacity_override or None,
        azimuth_deg=azimuth,
        system_loss_ratio=loss,
        unit_cost_won_per_kwp=unit_cost or None,
        total_investment_won=total_cost or None,
    )
    set_solar_inputs(inputs)

    if inputs.resolved_capacity_kwp(presets) <= 0:
        st.warning("면적 또는 용량을 넣어야 계산합니다.")
        return

    # 4·5단계 — **파이프라인에서 가장 오래 걸리는 구간이다** (실측 43%).
    # 아무 말 없이 몇 초를 멈추면 사용자는 화면이 죽은 줄 안다.
    panel, runner = progress_panel("태양광을 계산하는 중…")
    with panel:
        with runner.running("weather"):
            try:
                unit_profile, source = cached_unit_pv(
                    usage, usage_token(usage), inputs, rules_stamp()
                )
            except Exception as exc:
                st.error(f"기상 자료를 얻지 못해 계산하지 않았습니다.\n\n{exc}")
                return
        if source == "cache":
            runner.skip("weather", "캐시 적중")

        with runner.running("solar", total_steps=inputs.steps) as report:
            curve = cached_solar(
                usage,
                table,
                unit_profile,
                baseline,  # type: ignore[arg-type]
                quality,
                usage_token(usage),
                form,
                inputs,
                rules_stamp(),
                report,
            )
    st.caption(f"기상 출처 — {source}. " + detail_suffix("weather-source"))
    point = curve.points[-1]
    columns = st.columns(4)
    columns[0].metric("용량", fmt.kwp(point.capacity_kwp))
    columns[1].metric("발전량", fmt.kwh(point.generation_kwh))
    columns[2].metric("절감액", fmt.won_short(point.total_saving_won))
    columns[3].metric(
        "회수기간",
        fmt.payback(point.payback_years, investment_won=point.investment_won),
        fmt.certainty_badge(curve.certainty),
    )
    # **투자비를 모르면 빈칸이나 0원이 아니라 사유다** (7.5).
    st.caption("투자비 — " + fmt.won(point.investment_won, reason=curve.cost.reason))
    st.altair_chart(charts.solar_curve_chart(curve), width="stretch")
    _notes(curve.warnings, curve.notes)


# --------------------------------------------------------------------- 7.6


def _ess(
    usage: UsageData,
    table: TariffTable,
    form: ContractForm,
    diagnosis: Diagnosis,
    quality: QualityReport,
    baseline: object,
) -> None:
    peak = diagnosis.peak.billing_demand_kw
    target = st.number_input(
        "목표 요금적용전력 (kW)",
        min_value=0.0,
        max_value=float(peak),
        value=float(round(peak * 0.9)),
        step=10.0,
        help="출력·용량·방전시간이 여기서 역산됩니다.",
        key=input_key("ess", "target"),
    )
    if target <= 0 or target >= peak:
        st.warning("현재 요금적용전력보다 낮은 목표를 넣어야 계산합니다.")
        return

    excess = analyze_peak_excess(usage.kw, target, usage.meta.interval_minutes)
    hours = required_discharge_hours(excess)
    reference = load_ess_cost_reference()
    # 화면에는 **방전시간과 환산단가 한 줄** (10.2 적용 예).
    st.caption(
        f"산출된 방전시간 {fmt.hours(hours)} · 참고단가 표에서 해당 행을 강조합니다. "
        + detail_suffix("ess-cost-reference")
    )
    # 표는 접어 둔다. 단가를 넣기 전에 볼 참고자료이지 결과가 아니다 (10.7).
    with st.expander("참고단가 표", expanded=False):
        st.dataframe(reference_table(reference, highlight_hours=hours), width="stretch")
    # **고출력 셀 사양 경고는 화면에 남긴다** (10.2 예외).
    if hours < high_rate_discharge_hours():
        st.warning(
            f"방전시간이 {fmt.hours(hours)} 로 짧습니다. 고출력 셀 사양이 되어 "
            "참고단가보다 단가가 높아집니다."
        )

    cost_left, cost_right = st.columns(2)
    with cost_left:
        unit_cost = st.number_input(
            "단가 (원/kW)",
            min_value=0.0,
            value=0.0,
            step=10_000.0,
            key=input_key("ess", "unit_cost"),
        )
    with cost_right:
        total_cost = st.number_input(
            "총 투자비 직접 입력 (원) — 0 이면 단가 사용",
            min_value=0.0,
            value=0.0,
            step=1_000_000.0,
            key=input_key("ess", "total_cost"),
        )
    if not unit_cost and not total_cost:
        st.info(
            "단가나 총 투자비를 넣으면 회수기간을 산출합니다. 참고단가는 자동 적용하지 않습니다."
        )
        return

    result = cached_ess(
        usage,
        table,
        baseline,  # type: ignore[arg-type]
        quality,
        usage_token(usage),
        form,
        target,
        unit_cost or None,
        total_cost or None,
        rules_stamp(),
    )
    columns = st.columns(4)
    columns[0].metric(
        "출력 / 용량", f"{fmt.kw(result.power_kw, decimals=0)} / {fmt.kwh(result.capacity_kwh)}"
    )
    columns[1].metric("절감액", fmt.won_short(result.total_saving_won))
    columns[2].metric("투자비", fmt.won_short(result.investment_won))
    columns[3].metric(
        "회수기간",
        fmt.payback(result.payback_years, investment_won=result.investment_won),
        fmt.certainty_badge(result.certainty),
    )
    if result.reference_verdict:
        st.caption(result.reference_verdict)
    _notes(result.warnings, result.notes)


# --------------------------------------------------------------------- 7.7


def _surplus(
    usage: UsageData,
    table: TariffTable,
    form: ContractForm,
    diagnosis: Diagnosis,
    quality: QualityReport,
    baseline: object,
) -> None:
    inputs = get_solar_inputs()
    if inputs is None:
        st.warning("태양광을 먼저 켜야 잉여 전력이 나옵니다 (7.5).")
        return
    presets = load_pv_presets()
    capacity = inputs.resolved_capacity_kwp(presets)
    if capacity <= 0:
        st.warning("태양광 용량이 0 입니다.")
        return
    price = st.number_input(
        "외부 구매 단가 (원/kWh) — 0 이면 미산출", min_value=0.0, value=0.0, step=1.0
    )
    try:
        unit_profile, _source = cached_unit_pv(usage, usage_token(usage), inputs, rules_stamp())
    except Exception as exc:
        st.error(f"기상 자료를 얻지 못했습니다.\n\n{exc}")
        return
    result = cached_surplus(
        usage, table, unit_profile, usage_token(usage), form, capacity, price or None, rules_stamp()
    )
    columns = st.columns(3)
    columns[0].metric("잉여 전력량", fmt.kwh(result.total_kwh))
    columns[1].metric("발전량 대비", fmt.ratio_pct(result.share_of_generation))
    columns[2].metric("주말 비중", fmt.ratio_pct(result.weekend_share))
    st.dataframe(
        {
            "시나리오": [item.name for item in result.scenarios],
            "수익": [fmt.won(item.revenue_won, reason=item.basis) for item in result.scenarios],
            "행정 부담": [item.admin_burden for item in result.scenarios],
        },
        hide_index=True,
        width="stretch",
    )
    st.info(ELIGIBILITY_NOTICE)
    _notes((), result.notes)


# --------------------------------------------------------------------- 공통


def _notes(warnings: tuple[str, ...], notes: tuple[str, ...]) -> None:
    """**확인사항 하나로 합친다** (10.7).

    노란 상자 넷과 계산 메모가 따로 뜨던 구성은 무엇이 중요한지 가린다.
    주의 등급만 모아 접힌 상자 하나에 넣고, 참고 등급은 산출물로 보낸다.
    """
    items = screen_notices(warnings, notes)
    if not items:
        return
    with st.expander(f"확인사항 {len(items)}건", expanded=False):
        for item in items:
            st.warning(item.text) if item.severity is Severity.WARN else st.error(item.text)


_HANDLERS = {
    "tariff_switch": _tariff_switch,
    "contract": _contract,
    "demand_response": _demand_response,
    "power_factor": _power_factor,
    "solar": _solar,
    "ess": _ess,
    "surplus": _surplus,
}
