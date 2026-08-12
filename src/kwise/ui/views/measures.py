"""2단계 · 개선 수단 (요구사항서 7장·10.1).

카드로 켜고 끈다. **7장 번호 순(7.1~7.7)으로 배치하고 접힌 상태로 시작한다.**
순서는 :data:`kwise.ui.spec.MEASURES` 가 쥐고 있으며 바꾸지 않는다.

**모든 개선안은 독립 평가다** (14세션 2절).

    기준선   언제나 **현행 요금제·현행 사용량**이다. 최적 요금제로 바꾼 뒤의
             단가나 다른 수단 적용 후의 부하를 쓰지 않는다.
    뜻       각 카드의 절감액은 "지금 이 수단만 도입하면 얼마" 다.
    불변     어떤 수단을 켜고 끄든 다른 카드의 숫자가 바뀌지 않는다.
    비활성   **다른 카드 때문에 비활성이 되는 카드는 없다.** 태양광을 켜지 않아도
             잉여 활용 카드는 열려 있고 잉여가 0 이라는 사실만 적는다.

상호작용(요금제가 바뀐다·기본요금 기반이 달라진다)은 **3단계 합산효과**에서만
다룬다. 켜지 않은 수단은 3단계 조합 비교와 산출물에서 빠지고, 그 사실을
「검토 범위」가 밝힌다.
"""

from __future__ import annotations

import streamlit as st

from kwise.diagnose import Diagnosis, default_margin_ratio, margin_range
from kwise.diagnose.dr import dr_event_hours, dr_max_events_per_day
from kwise.io import UsageData
from kwise.measures import (
    ELIGIBILITY_NOTICE,
    default_target_pct,
    evaluate_demand_response,
    high_rate_discharge_hours,
    load_ess_cost_model,
    power_factor_floor_pct,
)
from kwise.pv import capacity_preview, list_provinces, list_sigungu, load_pv_presets
from kwise.quality import QualityReport
from kwise.report.columns import localize
from kwise.tariff import TariffTable
from kwise.ui import charts
from kwise.ui import text as fmt
from kwise.ui.anchors import detail_suffix
from kwise.ui.cache import (
    cached_contract_adjustment,
    cached_ess,
    cached_ess_targets,
    cached_power_factor,
    cached_solar,
    cached_surplus,
    cached_tariff_switch,
    cached_unit_pv,
    ess_cost_model,
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
    st.caption("7.1부터 7.7까지 차례로 놓았습니다. " + detail_suffix("combination"))
    # **기준선을 한 번만 밝힌다.** 카드마다 되풀이하면 읽히지 않는다 (10.7).
    st.write(
        "각 개선안은 **따로따로** 평가합니다. 카드의 절감액은 「지금 이 수단만 "
        "도입하면 얼마」이며, 기준은 언제나 현재 요금제와 현재 사용량입니다. "
        "수단을 함께 켰을 때의 최종 효과는 3단계 합산효과에서 다시 계산합니다."
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
    # 절 번호와 이름을 **묶음 머리(투자 0원)와 같은 크기**로 둔다. 굵게 하지 않는다 —
    # 토글 라벨 크기로는 카드 경계가 보이지 않았다 (13세션).
    st.markdown(
        f"<div style='font-size:1.15rem;padding-top:0.4rem'>{icon} {spec.title}</div>",
        unsafe_allow_html=True,
    )
    enabled = st.toggle("검토에 포함", key=toggle_key(spec.key))
    opened_key = f"_kwise_opened_{spec.key}"
    just_enabled = enabled and not bool(st.session_state.get(opened_key))
    st.session_state[opened_key] = enabled
    if not enabled:
        return
    # **켜면 펼친다.** 켠 뒤 다시 펼치게 하면 두 번 눌러야 결과가 보인다 (13세션).
    with st.expander(f"{spec.title} — 입력과 결과", expanded=just_enabled):
        st.caption(spec.headline + " " + detail_suffix(spec.anchor))
        handler = _HANDLERS[spec.key]
        handler(spec, usage, table, form, diagnosis, quality, baseline)


def _overview(spec: MeasureSpec) -> None:
    """무엇을 어떻게 개선하는지 두세 줄 (14세션 2-2).

    **입력 아래, 결과 숫자 위에 놓는다.** 결과를 읽기 직전에 무엇을 보고 있는지
    한 번 짚어 주는 자리다 — 카드 맨 위에 두면 입력을 채우는 동안 스크롤 밖으로
    밀려나 읽히지 않는다.
    """
    st.markdown(f"> {spec.overview}")


# --------------------------------------------------------------------- 7.1


def _tariff_switch(
    spec: MeasureSpec,
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
    _overview(spec)
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
    spec: MeasureSpec,
    usage: UsageData,
    table: TariffTable,
    form: ContractForm,
    diagnosis: Diagnosis,
    quality: QualityReport,
    baseline: object,
) -> None:
    if form.contract_kw is None or baseline is None:
        _overview(spec)
        st.warning("계약전력을 입력해야 조정 여지를 봅니다 (1단계 계약 정보).")
        return
    # **여유가 없으면 슬라이더를 감춘다** (13세션). 움직여도 0% 라 고장으로 보인다.
    peak = diagnosis.peak.billing_demand_kw
    headroom = (form.contract_kw - peak) / form.contract_kw if form.contract_kw else 0.0
    low, high = margin_range()
    if headroom <= low:
        st.write(
            f"현재 계약전력 {fmt.kw(form.contract_kw, decimals=0)} 는 요금적용전력 "
            f"{fmt.kw(peak)} 대비 여유가 {fmt.ratio_pct(headroom)} 입니다. "
            "하향 여지가 없습니다."
        )
        # **여유율을 세션에 남긴다.** 슬라이더를 감춘 경우에도 3단계가 같은 값을
        # 읽어야 2단계 카드와 숫자가 어긋나지 않는다 (14세션 5-1). 위젯이 이번
        # 실행에서 만들어지지 않았으므로 키를 직접 써도 된다.
        margin = default_margin_ratio()
        st.session_state[input_key("contract", "margin")] = margin
    else:
        margin = st.slider(
            "확보할 여유율",
            min_value=0.0,
            max_value=0.3,
            value=default_margin_ratio(),
            step=0.01,
            format="%.0f%%",
            key=input_key("contract", "margin"),
        )
        st.caption(
            "확보할 여유율은 향후 부하 증가와 예측 오차에 대비한 완충입니다. "
            "높이면 안전하지만 절감액이 줄고, 낮추면 절감액이 늘지만 초과 시 "
            "위약금과 12개월 기본요금 상승 위험이 커집니다. 권장 "
            f"{fmt.ratio_pct(low, decimals=0)}–{fmt.ratio_pct(high, decimals=0)}. "
            + detail_suffix("contract-adequacy")
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
    _overview(spec)
    columns = st.columns(4)
    columns[0].metric("현행 계약전력", fmt.kw(result.contract_kw))
    columns[1].metric("권장", fmt.kw(result.suggested_contract_kw))
    columns[2].metric("하향 여지", fmt.kw(result.reduction_kw))
    columns[3].metric("절감액", fmt.won_short(result.saving_won, reason=result.saving_basis))
    # **이 숫자는 현재 부하 기준이다** (14세션 2-4). 다른 수단을 켰다고 바뀌지
    # 않으며, 조합 기준의 추가 하향 여지는 3단계에서 따로 낸다.
    st.caption(
        "현재 부하 기준의 하향 여지입니다. 다른 수단을 함께 켜면 3단계 합산효과에서 "
        "추가 하향 여지가 계산됩니다."
    )
    # 계약전력 변경 위험(9.4)은 result.warnings 에 들어 있다. 확인사항에서 한 번만 낸다.
    _notes(result.warnings, result.notes)


# --------------------------------------------------------------------- 7.3


def _demand_response(
    spec: MeasureSpec,
    usage: UsageData,
    table: TariffTable,
    form: ContractForm,
    diagnosis: Diagnosis,
    quality: QualityReport,
    baseline: object,
) -> None:
    if diagnosis.dr is None:
        _overview(spec)
        st.warning("경제성DR 참여 여력을 산출하지 못했습니다.")
        return
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
    result = evaluate_demand_response(diagnosis.dr, unit_price_won_per_kwh=unit_price or None)

    _overview(spec)
    # **연간 한도가 없으므로 실질 제약은 저부하 평일 수 하나다** (14세션 4절).
    columns = st.columns(3)
    columns[0].metric("거래 가능일", fmt.days(result.eligible_days))
    columns[1].metric("저부하 평일", fmt.days(result.low_load_days))
    columns[2].metric("등록 권장 용량", fmt.kw(result.registered_capacity_kw))
    st.metric("연간 감축 가능량", fmt.kwh(result.annual_reducible_kwh))
    st.caption(
        f"운영 시간대 {fmt.markdown_safe(diagnosis.dr.window_label)} · 하루 한도 "
        f"{dr_max_events_per_day()}회 × 최대 {fmt.hours(dr_event_hours()[1], decimals=0)} "
        f"(하루 {fmt.hours(result.daily_hours_cap, decimals=0)}) · 참여 가능 시간 합 "
        f"{fmt.hours(result.participation_hours, decimals=0)}"
    )
    if result.weekend_baseline_kw is not None and result.low_load_threshold_kw is not None:
        st.caption(
            f"저부하 판정 기준선은 주말·공휴일 운영 시간대 평균 "
            f"{fmt.kw(result.weekend_baseline_kw)} 이고, 그 "
            f"{diagnosis.dr.low_load_multiple:.2g}배인 "
            f"{fmt.kw(result.low_load_threshold_kw)} 이하인 평일을 셌습니다."
        )
    # **어떤 날인지 보여 준다.** 창립기념일·워크숍처럼 사무실을 비우는 날일 가능성이
    # 높아, 목록을 보면 사용자가 스스로 맞는 날인지 판정할 수 있다 (14세션 4절).
    if result.low_load_days:
        with st.expander(f"저부하 평일 {result.low_load_days}일", expanded=False):
            st.dataframe(result.low_load_day_table, hide_index=True, width="stretch")
            st.caption("사무실을 비우는 날(창립기념일·워크숍 등)일 가능성이 높습니다.")
    else:
        st.write("저부하 평일이 없어 감축 가능량을 0 으로 두었습니다.")
    st.warning(fmt.markdown_safe(result.participation_notice))
    if result.is_priced:
        st.metric("정산금", fmt.won_short(result.settlement_won))
    st.caption(
        "자원 유형 — " + (", ".join(str(item) for item in result.resource_types) or "판정 불가")
    )
    # 참여 안내는 바로 위에 냈다. 확인사항에서 한 번 더 내지 않는다 (10.7).
    _notes(
        tuple(item for item in result.warnings if not item.startswith("낙찰 후 감축을")),
        result.notes,
    )


# --------------------------------------------------------------------- 7.4


def _power_factor(
    spec: MeasureSpec,
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
    _overview(spec)
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
    spec: MeasureSpec,
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
            help="기상 격자가 25–31 km 라 같은 격자면 결과가 같습니다.",
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
    if inputs.resolved_capacity_kwp(presets) <= 0:
        st.warning("면적 또는 용량을 넣어야 계산합니다.")
        return

    # **계산 버튼을 둔다** (13세션). 값 하나만 바꿔도 다시 도는 구간이라
    # (파이프라인의 절반이 여기다) 입력하는 동안 화면이 계속 멈췄다.
    if st.button("태양광 계산", type="primary", key="solar_run"):
        set_solar_inputs(inputs)
        st.rerun()
    saved_run = get_solar_inputs()
    if saved_run is None:
        st.info("면적·설치 밀도·지역·단가를 넣고 「태양광 계산」 을 누르십시오.")
        return
    stale = saved_run != inputs
    if stale:
        st.warning("입력이 변경되었습니다 — 다시 계산하십시오. 아래는 이전 결과입니다.")
    inputs = saved_run

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
    if stale:
        st.caption("**묵은 결과** — 지금 화면의 입력이 아니라 마지막 계산의 입력 기준입니다.")
    _overview(spec)
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
    spec: MeasureSpec,
    usage: UsageData,
    table: TariffTable,
    form: ContractForm,
    diagnosis: Diagnosis,
    quality: QualityReport,
    baseline: object,
) -> None:
    """**목표 슬라이더가 없다** (14세션 3-2).

    목표를 사용자가 찍게 두면 대개 틀린 자리를 찍는다 — 피크의 90%에서는 용량이
    4,000 kWh 를 넘고, 조금만 올리면 방전시간이 0.4시간까지 줄어 고출력 셀이 된다.
    회수기간 곡선을 그리고 **최소 지점을 기본값으로** 삼는다.
    """
    peak = diagnosis.peak.billing_demand_kw
    if peak <= 0:
        st.warning("요금적용전력을 산출하지 못해 ESS 목표를 훑을 수 없습니다.")
        return
    # **기본요금단가는 현행 요금제 기준이다** (14세션 2절). 최적 요금제로 바꾼 뒤의
    # 단가를 쓰면 ESS 절감액이 선택요금 전환에 딸려 움직여 독립 평가가 깨진다.
    base_fee = float(table.rates(form.selection).base_won_per_kw)

    total_cost, fixed_won, per_kwh_won = _ess_cost_inputs()
    _overview(spec)

    curve = cached_ess_targets(
        usage,
        usage_token(usage),
        float(peak),
        base_fee,
        fixed_won,
        per_kwh_won,
        rules_stamp(),
    )
    best = curve.best
    if best is None:
        st.warning("어떤 목표에서도 초과 구간이 없어 곡선을 그리지 못했습니다.")
        return
    model = ess_cost_model(fixed_won, per_kwh_won)

    st.altair_chart(charts.ess_target_chart(curve), width="stretch")
    st.caption(
        f"회수기간이 가장 짧은 목표는 **{fmt.kw(best.target_kw, decimals=0)}** 입니다 — "
        f"{fmt.markdown_safe(curve.u_shape_reason)} **물리적 최적이 아니라 조달 규격의 "
        "산물입니다.** 회색 점선은 필요 용량(kWh)이며, 목표를 조금만 낮춰도 급증하는 "
        "것이 오른쪽 팔을 만듭니다."
    )
    st.dataframe(localize(charts.ess_target_table(curve)), hide_index=True, width="stretch")
    st.caption(
        "위 곡선과 표는 **기본요금 절감만 본 개략치**입니다 (현행 요금제 기본요금단가 "
        f"{fmt.count(base_fee, ' 원/kW')} × 12개월). 아래 결과는 고른 목표에서 요금을 "
        "다시 계산하고 왕복효율·DoD 를 반영한 값입니다."
    )

    # 목표는 곡선이 정한다. 세션에는 남겨 3단계가 같은 값을 읽게 한다.
    target = best.target_kw
    st.session_state[input_key("ess", "target")] = target

    result = cached_ess(
        usage,
        table,
        baseline,  # type: ignore[arg-type]
        quality,
        usage_token(usage),
        form,
        target,
        total_cost or None,
        rules_stamp(),
        fixed_won,
        per_kwh_won,
    )
    st.subheader(f"최적 목표 {fmt.kw(target, decimals=0)} 기준")
    columns = st.columns(4)
    # **잘리지 않게 방전시간까지 한 칸에 담는다** (14세션 3-5).
    columns[0].metric(
        "출력 / 용량",
        f"{fmt.kw(result.power_kw, decimals=0)} / {fmt.kwh(result.capacity_kwh)}",
        f"방전 {fmt.hours(result.discharge_hours, decimals=1)}",
    )
    columns[1].metric("절감액", fmt.won_short(result.total_saving_won))
    columns[2].metric("투자비", fmt.won_short(result.investment_won))
    columns[3].metric(
        "회수기간",
        fmt.payback(result.payback_years, investment_won=result.investment_won),
        fmt.certainty_badge(result.certainty),
    )
    # **판정 문장을 쓰지 않는다** (14세션 3-3). 사실만 적고 판단은 사용자가 한다.
    if result.payback_years is not None:
        st.write(
            f"최적 목표 {fmt.kw(target, decimals=0)} 기준 회수기간 "
            f"{fmt.payback(result.payback_years)} — 배터리 보증 수명 "
            f"{fmt.count(result.payback_target_years, '년')} 과 견주십시오."
        )
    # **고출력 셀 사양 경고는 화면에 남긴다** (10.2 예외).
    if 0 < result.discharge_hours < high_rate_discharge_hours():
        st.warning(
            f"방전시간이 {fmt.hours(result.discharge_hours)} 로 짧습니다. 고출력 셀 "
            "사양이 되어 조달 사례보다 단가가 높아질 수 있습니다."
        )
    # **투자비는 설비와 전기공사를 나눠 적는다** (13세션). 합계만 보이면 실내·옥외
    # 차이가 어디서 오는지 알 수 없다.
    quote = result.quote
    if quote is not None:
        st.write(
            f"설비 **{fmt.won(quote.equipment_won)}** + 전기공사 "
            f"**{fmt.won(quote.electrical_won)}** = **{fmt.won(quote.total_won)}**"
        )
        if quote.applied_kwh > quote.capacity_kwh:
            st.caption(
                f"산출 용량 {fmt.kwh(quote.capacity_kwh, decimals=1)} — 시장 최소 "
                f"{fmt.kwh(model.market_minimum_kwh)} 기준으로 산정했습니다."
            )
        st.caption(
            f"전기공사는 옥외 기준 {fmt.won(quote.electrical_low_won)} – "
            f"{fmt.won(quote.electrical_high_won)} 구간의 대표값입니다. "
            f"{fmt.markdown_safe(model.formula)}"
        )
    # 성립 조건은 **두 값을 나란히 놓는 데서 그친다.** 단정하지 않는다.
    feasibility = result.feasibility
    if feasibility is not None:
        st.markdown(f"**성립 조건** — {fmt.markdown_safe(feasibility.message())}")
        grid = st.columns(3)
        grid[0].metric("kW당 배터리비", fmt.won(feasibility.battery_won_per_kw))
        grid[1].metric(
            f"{feasibility.target_years:,.0f}년 절감/kW", fmt.won(feasibility.saving_won_per_kw)
        )
        grid[2].metric("마진/kW", fmt.won(feasibility.margin_won_per_kw))
    with st.expander("조달 사례", expanded=False):
        st.dataframe(model.case_table(), hide_index=True, width="stretch")
        st.caption(
            "회귀에는 옥외 컨테이너형 관급 설비 네 건만 썼습니다. 실내형·이동형·"
            "카탈로그가는 설치 조건이 달라 같은 선에 놓을 수 없습니다."
        )
    _notes(result.warnings, result.notes)


def _ess_cost_inputs() -> tuple[float, float | None, float | None]:
    """단가 입력 — **2계수 방식이다** (14세션 3-4).

    kW당 단가로는 표현할 수 없다. 같은 100 kW 인데 용량이 156.4 kWh 면 2.35억,
    400 kWh 면 4.43억이다 — kW 가 설명 변수가 아니다. 기본은 자동 산출이고,
    확장 패널에서 **고정비와 용량단가 둘만** 조정한다.
    """
    model = load_ess_cost_model()
    with st.expander("단가 조정 (접어 둠)", expanded=False):
        st.caption(
            f"기본값은 자동 산출입니다 — {model.coefficient_source}. "
            "단가가 변하면 아래 두 값만 갱신하면 됩니다."
        )
        left, right = st.columns(2)
        with left:
            fixed = st.number_input(
                "고정비 (원) — PCS·PMS·컨테이너·소방·공조·UPS·변압기",
                min_value=0.0,
                value=float(model.fixed_won),
                step=1_000_000.0,
                key=input_key("ess", "fixed_cost"),
            )
        with right:
            per_kwh = st.number_input(
                "용량단가 (원/kWh) — 배터리",
                min_value=0.0,
                value=float(model.per_kwh_won),
                step=10_000.0,
                key=input_key("ess", "per_kwh_cost"),
            )
        total = st.number_input(
            "견적 총액 직접 입력 (원) — 0 이면 위 계수로 산정",
            min_value=0.0,
            value=0.0,
            step=1_000_000.0,
            key=input_key("ess", "total_cost"),
        )
        if fixed != model.fixed_won or per_kwh != model.per_kwh_won:
            st.warning("계수 조정됨 — 조달 사례 회귀값이 아닙니다.")
    return float(total), float(fixed), float(per_kwh)


# --------------------------------------------------------------------- 7.7


def _surplus(
    spec: MeasureSpec,
    usage: UsageData,
    table: TariffTable,
    form: ContractForm,
    diagnosis: Diagnosis,
    quality: QualityReport,
    baseline: object,
) -> None:
    """**다른 카드 때문에 비활성이 되지 않는다** (14세션 2-3).

    태양광이 없으면 잉여가 0 인 것이지 이 수단을 검토할 수 없는 것이 아니다.
    카드를 잠그면 "쓸 수 없는 수단" 으로 읽히므로, 열어 두고 **잉여가 0 이라는
    사실만** 적는다.
    """
    price = st.number_input(
        "잉여 판매 단가 (원/kWh) — 0 이면 미산출",
        min_value=0.0,
        value=0.0,
        step=1.0,
        key=input_key("surplus", "price"),
        help="우리가 파는 쪽의 단가입니다. 넣지 않으면 외부 판매 금액을 산출하지 않습니다.",
    )
    _overview(spec)

    inputs = get_solar_inputs()
    presets = load_pv_presets()
    capacity = inputs.resolved_capacity_kwp(presets) if inputs is not None else 0.0
    if inputs is None or capacity <= 0:
        columns = st.columns(3)
        columns[0].metric("잉여 전력량", fmt.kwh(0.0))
        columns[1].metric("발전량 대비", fmt.DASH)
        columns[2].metric("주말 비중", fmt.DASH)
        st.write("태양광을 켜지 않아 잉여가 0 입니다.")
        st.caption(
            "7.5 태양광을 켜고 계산하면 잉여량과 활용 시나리오가 여기에 나옵니다. "
            + fmt.markdown_safe(ELIGIBILITY_NOTICE)
        )
        return

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
    st.info(fmt.markdown_safe(ELIGIBILITY_NOTICE))
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
