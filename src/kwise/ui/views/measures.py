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

import datetime as dt

import pandas as pd
import streamlit as st

from kwise.diagnose import Diagnosis, default_margin_ratio, margin_range
from kwise.diagnose.dr import dr_event_hours, dr_max_events_per_day
from kwise.io import UsageData
from kwise.measures import (
    ELIGIBILITY_NOTICE,
    apply_generation,
    band_labels,
    default_target_pct,
    evaluate_demand_response,
    high_rate_discharge_hours,
    load_ess_cost_model,
    power_factor_floor_pct,
)
from kwise.notices import Notice, basis, tooltip
from kwise.pv import PvPresets, capacity_preview, load_pv_presets
from kwise.quality import QualityReport
from kwise.report import CONTRACT_CHANGE_WARNING
from kwise.report.columns import localize
from kwise.report.days import RepresentativeDay, find_day, representative_days
from kwise.tariff import TariffTable
from kwise.ui import callout, charts
from kwise.ui import text as fmt
from kwise.ui.anchors import manual_tip
from kwise.ui.building import BuildingInfo
from kwise.ui.cache import (
    cached_azimuth_options,
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
from kwise.ui.context import AnalysisContext
from kwise.ui.labels import option_label, selection_label
from kwise.ui.notices import screen_notices, tooltip_text
from kwise.ui.pipeline import ContractForm, SolarInputs
from kwise.ui.progress import progress_panel
from kwise.ui.spec import MEASURES, MeasureSpec
from kwise.ui.state import get_solar_inputs, input_key, set_solar_inputs, toggle_key

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
    st.header("🛠 2단계 · 개선 수단")
    st.caption("7.1부터 7.7까지 차례로 놓았습니다.", help=manual_tip("combination"))
    # **기준선을 한 번만 밝힌다.** 카드마다 되풀이하면 읽히지 않는다 (10.7).
    st.write(
        "각 개선안은 **따로따로** 평가합니다. 카드의 절감액은 「지금 이 수단만 "
        "도입하면 얼마」이며, 기준은 언제나 현재 요금제와 현재 사용량입니다. "
        "수단을 함께 켰을 때의 최종 효과는 3단계 합산효과에서 다시 계산합니다."
    )
    baseline = diagnosis.structure.bill if diagnosis.structure is not None else None
    day = _reference_day(usage)

    tier = ""
    for spec in MEASURES:
        if spec.tier != tier:
            tier = spec.tier
            st.subheader(tier)
        _card(spec, usage, table, form, diagnosis, quality, baseline, day, building)


# --------------------------------------------------------------------- 대표일


def _reference_day(usage: UsageData) -> RepresentativeDay | None:
    """**세 곡선 차트가 같은 날을 본다** (15세션 2절).

    역률·태양광·ESS 가 모두 하루 15분 곡선을 쓴다. 카드마다 다른 날을 그리면
    세 그림을 나란히 놓고도 견줄 수 없으므로 위젯 하나로 묶는다.
    """
    days = representative_days(usage)
    if not days:
        return None
    keys = [item.key for item in days] + ["custom"]
    labels = {item.key: item.title for item in days} | {"custom": "사용자 지정일"}
    left, right = st.columns([2, 1])
    with left:
        picked = st.selectbox(
            "일일 곡선 대표일",
            keys,
            format_func=lambda key: labels[key],
            key=input_key("common", "ref_day"),
            help=(
                "역률·태양광·ESS 의 하루 곡선이 모두 이 날을 씁니다. "
                "기본은 연간 최대수요가 난 날입니다 — 피크가 어떻게 생겼는지가 "
                "세 수단의 공통 관심사이기 때문입니다."
            ),
        )
    custom: dt.date | None = None
    if picked == "custom":
        with right:
            custom = st.date_input(
                "날짜",
                value=days[0].date,
                min_value=usage.meta.start.date(),
                max_value=usage.meta.end.date(),
                key=input_key("common", "ref_day_custom"),
            )
    # **세션에 남긴 값을 읽는 것은 :func:`kwise.ui.state.reference_day` 하나다.**
    # 3단계 보고서 차트가 같은 날을 보게 하려면 읽는 곳이 하나여야 한다.
    return find_day(usage, str(picked), custom=custom)


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


def _open_key(measure_key: str) -> str:
    return f"_kwise_opened_{measure_key}"


def _card(
    spec: MeasureSpec,
    usage: UsageData,
    table: TariffTable,
    form: ContractForm,
    diagnosis: Diagnosis,
    quality: QualityReport,
    baseline: object,
    day: RepresentativeDay | None,
    building: BuildingInfo | None,
) -> None:
    """**켜지 않은 카드는 접혀 있고, 켠 카드는 펼쳐진 채로 남는다** (16세션 0-2).

    ± 단추 하나를 눌러도 rerun 이 일어나는데, 펼침 여부를 「방금 켰는가」로
    판정하니 그 rerun 에서 카드가 도로 접혔다. 값을 세 번 조정하려면 세 번 다시
    펴야 했다. **열림 상태를 세션에 담고 ``expanded=`` 에 그 값을 넣는다.**

    Streamlit 은 사용자가 손으로 접은 것을 알려 주지 않으므로, 켜 둔 카드는
    rerun 마다 다시 펼쳐진다. 접어 두고 싶으면 토글을 끈다 — 끄면 3단계 조합에서
    빠지므로, 「켜 두고 접기」 는 애초에 두 뜻이 섞인 상태였다.
    """
    icon = _ICONS[spec.key]
    # 절 번호와 이름을 **묶음 머리보다 크게** 둔다 (16세션 6-1). 카드가 일곱이라
    # 스크롤 중에 경계가 눈에 걸려야 하는데, 묶음 머리와 같은 크기로는 묻혔다.
    st.markdown(
        f"<div style='font-size:1.5rem;font-weight:600;padding-top:0.6rem'>"
        f"{icon} {spec.title}</div>",
        unsafe_allow_html=True,
    )
    enabled = st.toggle("검토에 포함", key=toggle_key(spec.key))
    opened_key = _open_key(spec.key)
    if not enabled:
        st.session_state[opened_key] = False
        return
    st.session_state[opened_key] = True
    with st.expander(f"{spec.title} — 입력과 결과", expanded=True):
        st.caption(spec.headline, help=manual_tip(spec.anchor))
        handler = _HANDLERS[spec.key]
        handler(spec, usage, table, form, diagnosis, quality, baseline, day, building)


def _band_series(usage: UsageData, table: TariffTable, form: ContractForm) -> pd.Series | None:
    """계시별 시간대 라벨. **왜 그 시각에 충·방전하는지**를 배경으로 깔 때 쓴다."""
    try:
        return band_labels(usage, table, selection=form.selection, options=form.billing_options())
    except Exception:
        return None


def _caution(text: str) -> None:
    """주의 — **배경 없이** 아이콘과 굵기로만 (15세션 4절)."""
    callout.caution(text)


def _hint(text: str) -> None:
    """참고 — 무엇을 해야 하는지 알려 주는 안내. 배경 없이 작은 글씨."""
    callout.note(text)


def _blocked(text: str) -> None:
    """차단 — 이대로면 결과를 쓸 수 없다. **색을 남기는 유일한 등급이다.**"""
    callout.blocked(text)


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
    day: RepresentativeDay | None,
    building: BuildingInfo | None,
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
        "절감액",
        fmt.won_short(result.saving_won),
        fmt.certainty_badge(result.certainty),
        help=fmt.TIPS["certainty"],
    )
    if result.switch_needed:
        st.write(
            f"가장 유리한 요금제는 **{selection_label(table, result.best.selection)}** 입니다."
        )
    else:
        st.write("현재 요금제가 이미 가장 유리합니다. 바꿀 이유가 없습니다.")
    # **① 차액이 먼저다** (17세션 1-3). 35억 위에서 5천만원이 움직이는 것을 절대
    # 금액 축에 그리면 막대 셋이 같은 높이로 보인다 — 변화만 떼어 먼저 보인다.
    st.altair_chart(charts.tariff_delta_chart(result), width="stretch")
    st.caption(
        "현행을 0 으로 두고 좌우로 뻗은 막대입니다. 왼쪽(초록)이 절감입니다. "
        + fmt.TRUNCATION_FOOTNOTE
    )
    # **② 그룹 막대** (17세션 1-2). 쌓으면 기본요금끼리·전력량요금끼리 견줄 수 없다.
    st.altair_chart(charts.tariff_option_chart(result), width="stretch")
    st.caption(
        "요금제마다 기본요금·전력량요금·합계를 나란히 세웠습니다. **선택요금은 그 둘을 "
        "맞바꾸는 제도**입니다 — 기본요금이 오르는 대신 전력량요금이 내려갑니다. "
        "세로축은 0 부터 시작하지 않습니다."
    )
    _notices(result.notices)


# --------------------------------------------------------------------- 7.2


def _contract(
    spec: MeasureSpec,
    usage: UsageData,
    table: TariffTable,
    form: ContractForm,
    diagnosis: Diagnosis,
    quality: QualityReport,
    baseline: object,
    day: RepresentativeDay | None,
    building: BuildingInfo | None,
) -> None:
    if form.contract_kw is None or baseline is None:
        _overview(spec)
        _caution("계약전력을 입력해야 조정 여지를 봅니다 (1단계 계약 정보).")
        return
    _adequacy(diagnosis)
    # **여유가 없으면 입력칸을 감춘다** (13세션). 움직여도 0% 라 고장으로 보인다.
    peak = diagnosis.peak.billing_demand_kw
    headroom = (form.contract_kw - peak) / form.contract_kw if form.contract_kw else 0.0
    low, high = margin_range()
    if headroom <= low:
        st.write(
            f"현재 계약전력 {fmt.kw(form.contract_kw, decimals=0)} 는 요금적용전력 "
            f"{fmt.kw(peak)} 대비 여유가 {fmt.ratio_pct(headroom)} 입니다. "
            "하향 여지가 없습니다."
        )
        # **여유율을 세션에 남긴다.** 입력칸을 감춘 경우에도 3단계가 같은 값을
        # 읽어야 2단계 카드와 숫자가 어긋나지 않는다 (14세션 5-1). 위젯이 이번
        # 실행에서 만들어지지 않았으므로 키를 직접 써도 된다.
        margin = default_margin_ratio()
        st.session_state[input_key("contract", "margin")] = margin
    else:
        # **슬라이더가 아니라 수치 입력이다** (16세션 0-2). 슬라이더는 끄는 동안
        # 실행이 이어져 화면이 계속 다시 그려진다 — ± 한 번에 한 번만 돈다.
        margin = st.number_input(
            "확보할 여유율",
            min_value=0.0,
            max_value=0.3,
            value=default_margin_ratio(),
            step=0.01,
            format="%.2f",
            key=input_key("contract", "margin"),
            help=fmt.TIPS["contract_margin"],
        )
        st.caption(
            f"권장 {fmt.ratio_pct(low, decimals=0)}–{fmt.ratio_pct(high, decimals=0)}.",
            help=manual_tip("contract-adequacy"),
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
    # **계약전력 변경 경고는 이 카드에 둔다** (16세션 3절). 1단계에 있을 때는
    # 계약전력을 바꿀 생각을 하기 전에 읽혀 지나쳤다 — 바꾸자고 제안하는 자리가
    # 이 경고의 제자리다. **문구는 산출물과 같은 원문 그대로다.**
    _caution(CONTRACT_CHANGE_WARNING)
    _notices(tuple(item for item in result.notices if item.text != CONTRACT_CHANGE_WARNING))


def _adequacy(diagnosis: Diagnosis) -> None:
    """계약전력 적정성 — **1단계에서 이리로 옮겼다** (16세션 3절).

    같은 금액이 1단계 「계약전력 적정성」과 이 카드에 다른 이름으로 있었다.
    둘을 나란히 놓고 보면 어느 쪽을 믿어야 할지 알 수 없으므로, **바꿀지 말지를
    정하는 자리** 하나에 모은다.
    """
    adequacy = diagnosis.contract
    if adequacy is None:
        return
    st.markdown("**적정성**")
    columns = st.columns(3)
    columns[0].metric("계약전력", fmt.kw(adequacy.contract_kw))
    columns[1].metric(
        "이용률",
        fmt.ratio_pct(adequacy.utilization),
        help="요금적용전력 ÷ 계약전력. 낮으면 계약을 과하게 잡아 둔 것이다.",
    )
    columns[2].metric("하향 여지", fmt.kw(adequacy.reduction_kw))


# --------------------------------------------------------------------- 7.3


def _demand_response(
    spec: MeasureSpec,
    usage: UsageData,
    table: TariffTable,
    form: ContractForm,
    diagnosis: Diagnosis,
    quality: QualityReport,
    baseline: object,
    day: RepresentativeDay | None,
    building: BuildingInfo | None,
) -> None:
    if diagnosis.dr is None:
        _overview(spec)
        _caution("경제성DR 참여 여력을 산출하지 못했습니다.")
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
    # **기준선 근처로 내려온 평일이 감축 가능일이다** (15세션 2-2). 요일 갈래를
    # 색으로 나누고 저부하 평일에 표식을 찍으면 그 사실이 그림 하나로 읽힌다.
    st.altair_chart(charts.dr_daily_chart(diagnosis.dr), width="stretch")
    st.caption(
        "점 하나가 하루입니다. 붉은 가로선이 주말·공휴일 평균(기준선)과 저부하 문턱이고, "
        "역삼각형이 감축 가능일입니다."
    )
    # **어떤 날인지 보여 준다.** 창립기념일·워크숍처럼 사무실을 비우는 날일 가능성이
    # 높아, 목록을 보면 사용자가 스스로 맞는 날인지 판정할 수 있다 (14세션 4절).
    if result.low_load_days:
        with st.expander(f"저부하 평일 {result.low_load_days}일", expanded=False):
            st.dataframe(result.low_load_day_table, hide_index=True, width="stretch")
            st.caption("사무실을 비우는 날(창립기념일·워크숍 등)일 가능성이 높습니다.")
    else:
        st.write("저부하 평일이 없어 감축 가능량을 0 으로 두었습니다.")
    _caution(result.participation_notice)
    if result.is_priced:
        st.metric("정산금", fmt.won_short(result.settlement_won))
    st.caption(
        "자원 유형 — " + (", ".join(str(item) for item in result.resource_types) or "판정 불가")
    )
    # 참여 안내는 바로 위에 냈다. 확인사항에서 한 번 더 내지 않는다 (10.7).
    _notices(tuple(item for item in result.notices if not item.text.startswith("낙찰 후 감축을")))


# --------------------------------------------------------------------- 7.4


def _power_factor(
    spec: MeasureSpec,
    usage: UsageData,
    table: TariffTable,
    form: ContractForm,
    diagnosis: Diagnosis,
    quality: QualityReport,
    baseline: object,
    day: RepresentativeDay | None,
    building: BuildingInfo | None,
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
            "역률 개선 투자비 (원)",
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
    # **각이 좁아지는 모습**이 개선의 전부다 (15세션 2-3).
    triangle_col, day_col = st.columns(2)
    with triangle_col:
        st.altair_chart(charts.power_triangle_chart(result), width="stretch")
        st.caption("각이 좁아질수록 역률이 좋아집니다. 역률 개선 설비는 무효전력(세로)만 줄입니다.")
    with day_col:
        if day is not None:
            st.altair_chart(
                charts.power_factor_day_chart(
                    usage, day, current_pct=result.current_pct, target_pct=result.target_pct
                ),
                width="stretch",
            )
            st.caption(
                f"{day.title} · 주황 표식이 역률요금 판정 창(주간 08–22시)입니다. "
                "야간은 진상 기준입니다."
            )
    # **92% 미달 경고는 화면에 남긴다** — 결과 해석을 바꾼다 (10.2 예외).
    from kwise.tariff import lagging_standard_pct

    standard = lagging_standard_pct()
    if result.current_pct < standard:
        _caution(
            f"현재 지상역률이 기준 {standard:,.0f}% 에 미달합니다 "
            f"({fmt.pct(result.current_pct)}). 매 1%p 마다 기본요금이 추가됩니다."
        )
    _notices(result.notices)


# --------------------------------------------------------------------- 7.5


def _solar(
    spec: MeasureSpec,
    usage: UsageData,
    table: TariffTable,
    form: ContractForm,
    diagnosis: Diagnosis,
    quality: QualityReport,
    baseline: object,
    day: RepresentativeDay | None,
    building: BuildingInfo | None,
) -> None:
    presets = load_pv_presets()
    saved = get_solar_inputs()
    # **지역은 옆단에서 온다** (16세션 2절). 건물이 어디 있는지는 태양광만의
    # 물음이 아니고, 카드 안에 두면 태양광을 켜야만 고칠 수 있었다.
    region_key = building.region_key if building is not None else ""
    if not region_key and saved is not None:
        region_key = saved.region_key
    if not region_key:
        _caution("옆단에서 지역(시도·시군구)을 고르십시오. 기상 격자를 정하는 값입니다.")
        return

    # ---- 기본 입력 둘 (3.3). **2열로 나눈다** — 지역이 옆단으로 올라가 둘만 남았다.
    area_col, density_col = st.columns(2)
    with area_col:
        area = st.number_input(
            "설치 가능 면적 (m²)",
            min_value=0.0,
            value=float(saved.area_m2) if saved else 1000.0,
            step=50.0,
        )
    density_keys = [item.key for item in presets.densities]
    # **환산 용량은 라벨에, 상충 관계는 툴팁에** (15세션 1-2). 선택지와 설명이
    # 화면상 떨어져 있으면 고르는 순간에 읽히지 않는다.
    capacity_by_key = {preset.key: capacity for preset, capacity in capacity_preview(area, presets)}
    density_labels = {
        item.key: f"{item.label} — {fmt.kwp(capacity_by_key.get(item.key, 0.0))}"
        for item in presets.densities
    }
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
            help="\n\n".join(f"**{item.label}** — {item.tradeoff}" for item in presets.densities),
        )

    st.caption(f"지역 — {region_key.replace('/', ' ')} (옆단 「건물 정보」 에서 고칩니다)")

    # ---- 방위 (15세션 1-1). **각도가 아니라 8방위로 고른다.**
    tilt = presets.density(density).tilt_deg
    azimuth = _azimuth_picker(
        usage,
        region_key,
        tilt_deg=tilt,
        gcr=presets.density(density).gcr,
        field="azimuth",
        calculated=saved,
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
            azimuth_override = st.number_input(
                "방위각 직접 입력 (도) — 0 이면 위 8방위 선택",
                min_value=0.0,
                max_value=360.0,
                value=0.0,
                step=5.0,
                help="8방위 격자(45도)에 없는 각도를 쓸 때만 넣습니다.",
            )
        with detail_right:
            # 슬라이더 대신 수치 입력 (16세션 0-2) — 끄는 동안 실행이 이어지지 않는다.
            loss = st.number_input(
                "시스템 손실",
                min_value=0.0,
                max_value=0.4,
                value=0.14,
                step=0.01,
                format="%.2f",
                key=input_key("solar", "system_loss"),
            )
            total_cost = st.number_input(
                "총 투자비 직접 입력 (원) — 0 이면 단가 사용",
                min_value=0.0,
                value=0.0,
                step=1_000_000.0,
            )
        # **다중 어레이** (15세션 1-1). 벽면은 경사 90° 라 방위 영향이 훨씬 크다.
        st.markdown("**벽면 어레이** — 지붕과 방위를 따로 고릅니다. 0 이면 지붕 한 벌입니다.")
        wall_area = st.number_input(
            "벽면 설치 가능 면적 (m²)",
            min_value=0.0,
            value=float(saved.wall_area_m2) if saved else 0.0,
            step=50.0,
        )
        wall_azimuth = (
            _azimuth_picker(
                usage,
                region_key,
                tilt_deg=90.0,
                gcr=1.0,
                field="wall_azimuth",
                calculated=saved,
            )
            if wall_area > 0
            else None
        )

    inputs = SolarInputs(
        region_key=region_key,
        area_m2=area,
        density_key=density,
        capacity_kwp=capacity_override or None,
        azimuth_deg=azimuth_override or azimuth,
        system_loss_ratio=loss,
        wall_area_m2=wall_area,
        wall_azimuth_deg=wall_azimuth,
        unit_cost_won_per_kwp=unit_cost or None,
        total_investment_won=total_cost or None,
    )
    if inputs.resolved_capacity_kwp(presets) <= 0:
        _caution("면적 또는 용량을 넣어야 계산합니다.")
        return

    # **계산 버튼을 둔다** (13세션). 값 하나만 바꿔도 다시 도는 구간이라
    # (파이프라인의 절반이 여기다) 입력하는 동안 화면이 계속 멈췄다.
    if st.button("태양광 계산", type="primary", key="solar_run"):
        set_solar_inputs(inputs)
        st.rerun()
    saved_run = get_solar_inputs()
    if saved_run is None:
        _hint("면적·설치 밀도·지역·단가를 넣고 「태양광 계산」 을 누르십시오.")
        return
    stale = saved_run != inputs
    if stale:
        _caution("입력이 변경되었습니다 — 다시 계산하십시오. 아래는 이전 결과입니다.")
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
                _blocked(f"기상 자료를 얻지 못해 계산하지 않았습니다. {exc}")
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
    st.caption(f"기상 출처 — {source}.", help=manual_tip("weather-source"))
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
        help=fmt.TIPS["certainty"],
    )
    # **투자비를 모르면 빈칸이나 0원이 아니라 사유다** (7.5).
    st.caption("투자비 — " + fmt.won(point.investment_won, reason=curve.cost.reason))

    # **한 줄 판정에 근거를 붙인다** (17세션 3-2). 숫자만 내면 "왜 하필 그
    # 용량인가" 를 알 수 없다.
    verdict = curve.verdict()
    st.markdown(f"**용량 판정** — {verdict.sentence()}")
    st.caption(fmt.markdown_safe(verdict.basis_sentence()))

    # **대표 지점을 표로** (17세션 3-3). 스무 줄은 아무도 읽지 않고, 곡선만으로는
    # 용량을 키울 때 무엇이 어떻게 변하는지 수로 확인할 수 없다.
    st.markdown("**용량별 비교**")
    st.dataframe(
        _capacity_view(charts.solar_capacity_table(curve, verdict=verdict)),
        hide_index=True,
        width="stretch",
    )
    st.caption(
        f"의미 있는 지점 {charts.CAPACITY_ROWS}개만 골랐습니다 — 면적 상한과 최적은 항상 "
        "들어갑니다. 금액과 발전량은 12개월 환산입니다. "
        "20단계 상세는 Excel 「태양광 용량 곡선」 시트에 있습니다."
    )
    with st.expander("용량 곡선 (접어 둠)", expanded=False):
        st.altair_chart(charts.solar_curve_chart(curve, verdict=verdict), width="stretch")

    generation = unit_profile * point.capacity_kwp
    st.altair_chart(charts.solar_annual_chart(usage, generation), width="stretch")
    ratio = charts.solar_saving_ratio(usage, generation)
    st.caption(
        "**주인공은 계통에서 받는 양이 줄어드는 모습**입니다. 위 선이 원래 사용량, "
        "그 아래 초록이 자가소비로 줄어든 몫, 연파랑이 그래도 계통에서 받는 양입니다. "
        + (f"연간 사용량의 **{fmt.ratio_pct(ratio)}** 를 줄입니다. " if ratio else "")
        + "주황 점선(잉여)이 크면 자가소비로 다 쓰지 못하는 구조입니다."
    )
    if day is not None:
        st.altair_chart(charts.solar_day_chart(usage, generation, day), width="stretch")
        st.caption(
            f"{day.title} · 두 선 사이 초록이 저감분입니다. **세로축은 0 부터 시작하지 "
            "않습니다** — 5,000 kW 대 부하에 수백 kW 를 얹으면 0 부터 그린 축에서는 "
            "두 선이 붙어 보입니다."
        )
        st.altair_chart(charts.solar_day_chart(usage, generation, day, zoom=True), width="stretch")
        st.caption(f"피크 앞뒤 {charts.PEAK_ZOOM_HOURS}시간만 확대한 그림입니다.")
    _notices(curve.notices)


def _capacity_view(frame: pd.DataFrame) -> pd.DataFrame:
    """용량 표를 **사람이 읽는 문자열로** 굳힌다 (17세션 3-3)."""
    if frame.empty:
        return frame
    return pd.DataFrame(
        {
            "용량(kWp)": [fmt.kwp(value) for value in frame["용량(kWp)"]],
            "연간 발전량": [fmt.kwh(value) for value in frame["연간 발전량(kWh)"]],
            "자가소비율": [fmt.ratio_pct(value) for value in frame["자가소비율"]],
            "기본요금 절감": [fmt.won_short(value) for value in frame["기본요금 절감(원)"]],
            "전력량요금 절감": [fmt.won_short(value) for value in frame["전력량요금 절감(원)"]],
            "투자비": [
                fmt.won_short(value, reason="미산출 — 단가 미입력") for value in frame["투자비(원)"]
            ],
            "회수기간": [
                fmt.payback(years, investment_won=investment)
                for years, investment in zip(
                    frame["회수기간(년)"], frame["투자비(원)"], strict=True
                )
            ],
            "표식": frame["표식"],
        }
    )


def _azimuth_picker(
    usage: UsageData,
    region_key: str,
    *,
    tilt_deg: float,
    gcr: float,
    field: str,
    calculated: SolarInputs | None = None,
) -> float:
    """8방위 선택 (15세션 1-1). **상대 발전량을 라벨에 병기한다.**

    비율은 하드코딩하지 않고 **지역·경사각으로 여덟 방위를 계산**해 낸다 —
    경사가 낮으면 방위 영향이 줄어들기 때문이다 (밀도 '높음' 은 경사 15°).

    **계산해 둔 것이 있을 때만 비율을 낸다** (17세션 3-1). 여덟 방위를 돌리는 데
    0.85초가 걸리는데, 살아 있는 위젯 값으로 매기면 **옆단에서 시군구를 바꾸는
    것만으로 그 0.85초가 돈다.** 태양광은 「계산」 단추를 눌러야 도는 카드이고,
    그 규약은 라벨에도 적용된다 — 마지막 계산의 지역·경사각과 지금 고른 값이
    같을 때만 비율을 얹고, 아니면 방위 이름만 보인다.
    """
    presets = load_pv_presets()
    matches = (
        calculated is not None
        and calculated.region_key == region_key
        and abs(_tilt_of(calculated, presets) - float(tilt_deg)) < 1e-9
    )
    options: dict[str, str] = {item.key: item.label for item in presets.azimuths}
    if matches:
        try:
            computed = cached_azimuth_options(
                usage,
                usage_token(usage),
                region_key,
                None,
                None,
                float(tilt_deg),
                float(gcr),
                0.14,
                50.0,
                rules_stamp(),
            )
            options = {item.key: item.label for item in computed}
        except Exception:
            options = {item.key: item.label for item in presets.azimuths}

    keys = [item.key for item in presets.azimuths]
    picked = st.radio(
        "방위",
        keys,
        index=keys.index(presets.default_azimuth),
        format_func=lambda key: options.get(key, key),
        horizontal=True,
        key=input_key("solar", field),
        help=(
            f"경사 {fmt.count(tilt_deg, '°')} 기준으로 여덟 방위를 각각 계산한 상대 "
            "발전량입니다. 45도 간격이라 최대 오차는 22.5도이고, 그 차이는 발전량 "
            f"1{fmt.RANGE}2% 로 예측 오차 안에 묻힙니다."
        ),
    )
    if not matches:
        _hint(
            "방위별 상대 발전량은 「태양광 계산」 을 누른 뒤 라벨에 붙습니다 — "
            "지역·경사각을 바꿀 때마다 여덟 방위를 다시 돌리지 않습니다."
        )
    preset = presets.azimuth(str(picked))
    if preset.is_northward:
        _caution(preset.caution)
    return preset.azimuth_deg


def _tilt_of(inputs: SolarInputs, presets: PvPresets) -> float:
    """계산해 둔 입력의 경사각. 밀도 프리셋이 정한다."""
    try:
        return float(presets.density(inputs.density_key).tilt_deg)
    except Exception:
        return float("nan")


# --------------------------------------------------------------------- 7.6


def _ess(
    spec: MeasureSpec,
    usage: UsageData,
    table: TariffTable,
    form: ContractForm,
    diagnosis: Diagnosis,
    quality: QualityReport,
    baseline: object,
    day: RepresentativeDay | None,
    building: BuildingInfo | None,
) -> None:
    """**목표 슬라이더가 없다** (14세션 3-2).

    목표를 사용자가 찍게 두면 대개 틀린 자리를 찍는다 — 피크의 90%에서는 용량이
    4,000 kWh 를 넘고, 조금만 올리면 방전시간이 0.4시간까지 줄어 고출력 셀이 된다.
    회수기간 곡선을 그리고 **최소 지점을 기본값으로** 삼는다.
    """
    peak = diagnosis.peak.billing_demand_kw
    if peak <= 0:
        _caution("요금적용전력을 산출하지 못해 ESS 목표를 훑을 수 없습니다.")
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
        _caution("어떤 목표에서도 초과 구간이 없어 곡선을 그리지 못했습니다.")
        return
    model = ess_cost_model(fixed_won, per_kwh_won)

    st.markdown("**목표 선택 곡선** — 어느 목표가 유리한지만 본다")
    st.altair_chart(charts.ess_target_chart(curve), width="stretch")
    st.caption(
        f"가장 유리한 목표는 **{fmt.kw(best.target_kw, decimals=0)}** 입니다 — "
        f"{fmt.markdown_safe(curve.u_shape_reason)} **물리적 최적이 아니라 조달 규격의 "
        "산물입니다.** 회색 점선은 정격 용량(kWh)이며, 목표를 조금만 낮춰도 급증하는 "
        "것이 오른쪽 팔을 만듭니다."
    )
    # **표에 돈에 관한 숫자를 두지 않는다** (18세션 1절). 표는 사양만 싣고,
    # 절감액·투자비·회수기간은 아래 카드 하나가 낸다. 두 기준이 각각 옳아도
    # 같은 목표에서 다른 회수기간이 두 개 보이면 사용자에게는 그냥 불일치다.
    st.dataframe(localize(charts.ess_target_table(curve)), hide_index=True, width="stretch")
    st.caption(
        "표의 출력·정격 용량·방전시간은 **아래 결과와 같은 값**입니다. "
        "절감액·투자비·회수기간은 요금을 다시 계산해야 나오므로 아래에서만 냅니다."
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
        help=fmt.TIPS["discharge_hours"],
    )
    columns[1].metric("절감액", fmt.won_short(result.total_saving_won))
    columns[2].metric("투자비", fmt.won_short(result.investment_won))
    columns[3].metric(
        "회수기간",
        fmt.payback(result.payback_years, investment_won=result.investment_won),
        fmt.certainty_badge(result.certainty),
        help=fmt.TIPS["certainty"],
    )
    # **언제 담고 언제 쓰는지**를 2단 그림으로 보인다 (15세션 2-5 · 17세션 4-1).
    if day is not None:
        st.altair_chart(
            charts.ess_day_chart(
                usage, result.dispatch, day, bands=_band_series(usage, table, form)
            ),
            width="stretch",
        )
        frame = charts.ess_day_frame(usage, result.dispatch, day.date)
        slot_hours = usage.meta.interval_minutes / 60.0
        charged = float(frame["충전(kW)"].sum()) * slot_hours if len(frame) else 0.0
        discharged = float(frame["방전(kW)"].sum()) * slot_hours if len(frame) else 0.0
        cut = float(frame["원부하(kW)"].max() - frame["순부하(kW)"].max()) if len(frame) else 0.0
        st.caption(
            f"{day.title} · 위 칸이 부하(축은 0 부터 시작하지 않습니다), 아래 칸이 "
            "충전(+)·방전(−)입니다. 배경 띠가 계시별 시간대예요 — 경부하에 담아 "
            f"최대부하에 씁니다. **그날 저감 {fmt.kw(cut)} · 충전 {fmt.kwh(charged)} · "
            f"방전 {fmt.kwh(discharged)}.**"
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
        _caution(
            f"방전시간이 {fmt.hours(result.discharge_hours)} 로 짧습니다. 고출력 셀 "
            "사양이 되어 조달 사례보다 단가가 높아질 수 있습니다."
        )
    # **투자비 내역·계수·성립 조건은 확인사항으로 내린다** (17세션 4-2·4-3).
    # 본문에는 투자비 합계·절감액·회수기간만 남긴다 — 위 지표 넷이 그것이다.
    _notices((*result.notices, _ess_basis_note(base_fee), *_ess_details(result, model)))


def _ess_basis_note(base_fee_won_per_kw: float) -> Notice:
    """두 기준의 차이 **한 줄** (18세션 1절).

    곡선은 기본요금 절감만 본 개략치로 목표를 고르고, 결론 숫자는 요금을 다시
    계산한 이 카드가 낸다. 차이를 지우지는 않되 **결론 옆에 두지 않는다** —
    한 줄이면 되는 사실이라 확인사항으로 내린다.
    """
    return basis(
        "목표 선택 곡선은 기본요금 절감만 본 개략치입니다 (현행 요금제 기본요금단가 "
        f"{fmt.count(base_fee_won_per_kw, ' 원/kW')} × 12개월). 위 결과는 그 목표에서 "
        "요금을 다시 계산하고 왕복효율·DoD 를 반영한 값이라 회수기간이 더 깁니다."
    )


def _ess_details(result: object, model: object) -> tuple[Notice, ...]:
    """ESS 투자비 내역·계수·성립 조건 (17세션 4-2).

    **본문에서 확인사항으로 내렸다.** 넉 줄이 결과 아래에 붙어 있으면 정작
    읽어야 할 투자비 합계·절감액·회수기간이 그 사이에 묻힌다. 지운 것이 아니라
    자리를 옮긴 것이다 — 근거는 접힌 상자 안에 그대로 있다.

    조달 사례 표도 화면에서 뺐다 (4-3). 계수를 낸 원자료이므로 데이터와 재적합
    스크립트에는 남아 있고, 여기 계수 한 줄이 그것을 갈음한다.
    """
    lines: list[str] = []
    quote = getattr(result, "quote", None)
    if quote is not None:
        lines.append(
            f"투자비 내역 — 설비 {fmt.won(quote.equipment_won)} + 전기공사 "
            f"{fmt.won(quote.electrical_won)} = {fmt.won(quote.total_won)}"
        )
        if quote.applied_kwh > quote.capacity_kwh:
            lines.append(
                f"산출 용량 {fmt.kwh(quote.capacity_kwh, decimals=1)} — 시장 최소 "
                f"{fmt.kwh(getattr(model, 'market_minimum_kwh', 0.0))} 기준으로 산정했습니다."
            )
        lines.append(
            f"전기공사는 옥외 기준 {fmt.won(quote.electrical_low_won)} – "
            f"{fmt.won(quote.electrical_high_won)} 구간의 대표값입니다."
        )
        lines.append(str(getattr(model, "formula", "")))
    feasibility = getattr(result, "feasibility", None)
    if feasibility is not None:
        lines.append(f"성립 조건 — {feasibility.message()}")
    # **같은 사실을 두 번 적지 않는다.** ESS 는 경고·메모가 스물이 넘어, 옮겨 온
    # 줄이 그 안의 문장과 겹치면 확인사항이 같은 말을 되풀이한다. 앞의 라벨
    # (``성립 조건 — ``)을 떼고 알맹이로 견준다.
    already = " \n".join(item.text for item in getattr(result, "notices", ()))
    return tuple(basis(line) for line in lines if line and line.split(" — ")[-1] not in already)


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
            _caution("계수 조정됨 — 조달 사례 회귀값이 아닙니다.")
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
    day: RepresentativeDay | None,
    building: BuildingInfo | None,
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
        _blocked(f"기상 자료를 얻지 못했습니다. {exc}")
        return
    result = cached_surplus(
        usage, table, unit_profile, usage_token(usage), form, capacity, price or None, rules_stamp()
    )
    columns = st.columns(3)
    columns[0].metric("잉여 전력량", fmt.kwh(result.total_kwh))
    columns[1].metric("발전량 대비", fmt.ratio_pct(result.share_of_generation))
    columns[2].metric("주말 비중", fmt.ratio_pct(result.weekend_share))
    # **잉여가 주말에 몰리는지**가 보여야 한다 (15세션 2-6).
    if result.total_kwh > 0:
        surplus_kw = apply_generation(usage, unit_profile * capacity).surplus_kw
        st.altair_chart(charts.surplus_daily_chart(usage, surplus_kw), width="stretch")
        st.caption("막대 하나가 하루입니다. 주말·공휴일에 몰리면 자가소비가 어려운 구조입니다.")
    else:
        st.write("잉여가 0 이라 그릴 것이 없습니다 — 발전량을 모두 자가소비합니다.")
    st.dataframe(
        {
            "시나리오": [item.name for item in result.scenarios],
            "수익": [fmt.won(item.revenue_won, reason=item.basis) for item in result.scenarios],
            "행정 부담": [item.admin_burden for item in result.scenarios],
        },
        hide_index=True,
        width="stretch",
    )
    _hint(ELIGIBILITY_NOTICE)
    _notices(result.notices)


# --------------------------------------------------------------------- 공통


def _notices(notices: tuple[Notice, ...]) -> None:
    """등급대로 자리를 나눈다 (19세션 1절).

        차단·주의  카드 본문. 아이콘을 달고 그대로 보인다
        근거      **툴팁 하나로 접는다** — 매번 볼 것은 아니지만 결과를
                  신뢰할지 판단할 때 필요하다
        참고      화면에 없다. 보고서 부록으로 간다

    17세션에 ESS 확인사항이 스물둘이었다. 세어 보니 그 대부분이 참고가 아니라
    **근거**였다 — 산식·출처·계수. 접힌 상자 하나에 스물둘을 쌓으면 정작
    위험한 두 건이 묻히므로, 근거는 툴팁으로 내리고 본문에는 차단·주의만 남긴다.

    **배경색 상자를 쓰지 않는다** (15세션 4절). 차단만 색을 남기고 나머지는
    아이콘으로 구분한다.
    """
    for item in screen_notices(notices):
        callout.render_notice(item)
    grounds = tooltip(notices)
    if grounds:
        st.caption(
            f"산출 근거 {len(grounds)}건",
            help=tooltip_text(notices, header="**이 숫자가 어디서 나왔나**"),
        )


_HANDLERS = {
    "tariff_switch": _tariff_switch,
    "contract": _contract,
    "demand_response": _demand_response,
    "power_factor": _power_factor,
    "solar": _solar,
    "ess": _ess,
    "surplus": _surplus,
}
