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
import math

import pandas as pd
import streamlit as st

from kwise import money
from kwise.diagnose import Diagnosis, default_margin_ratio, margin_range
from kwise.diagnose.dr import dr_event_hours, dr_max_events_per_day
from kwise.io import UsageData
from kwise.measures import (
    BASE_FEE_UNCHANGED,
    CURTAIL_SCENARIO,
    EXTERNAL_SCENARIO,
    OFFSET_SCENARIO,
    SHORTEST_PAYBACK,
    SolarPoint,
    SurplusResult,
    annualize,
    apply_generation,
    default_external_price_won_per_kwh,
    default_smp_price_won_per_kwh,
    default_target_pct,
    evaluate_demand_response,
    high_rate_discharge_hours,
    offset_settles_cash,
    power_factor_floor_pct,
    refine_targets,
    surplus_free_capacity_kwp,
    surplus_options,
    with_surplus_revenue,
)
from kwise.measures.demand_response import DemandResponseResult
from kwise.notices import Notice, tooltip
from kwise.pv import PvPresets, area_from_capacity_m2, capacity_preview, load_pv_presets
from kwise.quality import QualityReport
from kwise.report import CONTRACT_CHANGE_WARNING, frames
from kwise.report.days import RepresentativeDay, find_day, representative_days
from kwise.report.worksheet import (
    Worksheet,
    demand_response_worksheet,
    ess_worksheet,
    power_factor_worksheet,
    solar_worksheet,
    tariff_switch_worksheet,
)
from kwise.tariff import TariffTable
from kwise.ui import callout, charts
from kwise.ui import text as fmt
from kwise.ui.anchors import manual_tip
from kwise.ui.building import BuildingInfo
from kwise.ui.cache import (
    cached_azimuth_options,
    cached_contract_adjustment,
    cached_ess,
    cached_ess_optimum,
    cached_ess_targets,
    cached_power_factor,
    cached_solar,
    cached_surplus,
    cached_surplus_points,
    cached_tariff_switch,
    cached_unit_pv,
    rules_stamp,
    usage_token,
)
from kwise.ui.context import AnalysisContext
from kwise.ui.labels import measure_title, option_label, selection_label
from kwise.ui.notices import partition_facts, screen_notices, tooltip_text
from kwise.ui.pipeline import ContractForm, SolarInputs
from kwise.ui.progress import progress_panel
from kwise.ui.spec import MEASURES, MeasureSpec
from kwise.ui.state import (
    ess_pricing,
    get_solar_inputs,
    input_key,
    set_solar_inputs,
    surplus_prices,
    toggle_key,
)

__all__ = [
    "WEATHER_SOURCE_LABELS",
    "chosen_surplus_revenue",
    "dr_off_days",
    "render",
    "solar_surplus_scenario",
    "weather_source_label",
]


#: 기상 출처의 **표시 이름** (31세션 4-2).
#:
#: :class:`~kwise.pv.WeatherData` 의 ``source`` 는 셋(``network``·``cache``·
#: ``archive``)이지만 **사용자에게는 둘**이다 — 앞의 둘은 같은 Open-Meteo 자료이고
#: 우리가 그것을 파일로 들고 있느냐만 다르다. 저장 방식은 결과를 바꾸지 않으므로
#: 이름을 가르지 않는다. 반면 사전 취득분은 **망을 못 탔을 때 물러선 자리**라
#: 그 사실이 보여야 한다 (요구사항서 7.5 — 조용히 바꾸지 않는다).
WEATHER_SOURCE_LABELS: dict[str, str] = {
    "network": "Open-Meteo",
    "cache": "Open-Meteo",
    "archive": "아카이브(Open-Meteo)",
}


def weather_source_label(source: str) -> str:
    """``cache`` → ``Open-Meteo``. **모르는 값은 그대로 두지 않는다.**

    코드 식별자가 화면에 나가는 것을 막는 것이 이 함수의 목적이므로, 새 출처가
    생기면 옛 이름 대신 「기타」 로 적고 표에 더할 때까지 눈에 띄게 둔다.
    """
    return WEATHER_SOURCE_LABELS.get(source, "기타")


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
    # **머리말 캡션을 뺐다** (31세션 1-1). 「개선안 일곱을 차례로 놓았습니다」 는
    # 화면을 보면 아는 사실이었다. 달려 있던 `combination` 앵커는 제 자리인
    # 3단계 「조합 구성」 으로 옮겼다 — 조합 이야기를 하는 화면이 그쪽이다.
    #
    # **기준선을 한 번만 밝힌다.** 카드마다 되풀이하면 읽히지 않는다 (10.7).
    st.write(
        "각 개선안은 독립적으로 평가합니다. 카드의 절감액은 해당 수단만 적용했을 "
        "때의 효과이며, 기준은 현재 요금제와 사용량입니다. 여러 수단을 동시에 "
        "적용한 효과는 3단계 합산효과에서 별도로 산정합니다."
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
}


def _open_key(measure_key: str) -> str:
    return f"_kwise_opened_{measure_key}"


#: 7.1 카드 본문에서 내리는 사실 둘 (27세션 4-3·4-4).
#:
#:     결측 신뢰 제한   **1단계 「데이터 품질」 이 낸다.** 요금 엔진이 요금제마다
#:                     같은 사실을 다시 실어 보내지만, 그것으로 요금제를 고르지
#:                     않는다. 달 판별자가 붙어 사실 ID 중복 판정을 빠져나간다
#:     최적 요금제      **카드 본문이 같은 문장을 이미 쓴다.** 뒷문장을 뺀 뒤로는
#:                     둘이 완전히 같아졌다 — 산출물에는 그대로 실린다
_TARIFF_HIDDEN_FACTS: tuple[str, ...] = (
    "quality.month_missing_rate",
    "tariff_switch.best_selection",
)


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
    rerun 마다 다시 펼쳐진다. 접어 두고 싶으면 체크를 푼다 — 풀면 3단계 조합에서
    빠지므로, 「켜 두고 접기」 는 애초에 두 뜻이 섞인 상태였다.

    **고르는 것은 체크박스이고 이름이 곧 그 라벨이다** (27세션 1절). 슬라이드
    단추는 손가락으로 맞히기 어려웠다 — 표적이 스위치 하나뿐이었기 때문이다.
    이름을 라벨로 삼으면 제목 전체가 누를 자리가 되고, 「검토에 포함」 이라는
    줄도 하나 준다. **세션 키는 그대로다** (``measure_on_*``) — 16세션에 잡은
    키 충돌과 값 유실을 다시 열지 않는다.
    """
    icon = _ICONS[spec.key]
    # 번호와 이름을 **묶음 머리보다 크게** 둔다 (16세션 6-1). 카드가 일곱이라
    # 스크롤 중에 경계가 눈에 걸려야 하는데, 묶음 머리와 같은 크기로는 묻혔다.
    # 체크박스 라벨은 글꼴 크기를 받지 않으므로 **굵게**로 무게만 준다.
    title = measure_title(spec.title)
    enabled = st.checkbox(f"**{icon} {title}**", key=toggle_key(spec.key))
    opened_key = _open_key(spec.key)
    if not enabled:
        st.session_state[opened_key] = False
        return
    st.session_state[opened_key] = True
    with st.expander(f"{title} — 입력과 결과", expanded=True):
        st.caption(spec.headline, help=manual_tip(spec.anchor))
        handler = _HANDLERS[spec.key]
        handler(spec, usage, table, form, diagnosis, quality, baseline, day, building)


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
    # **확실성 등급은 어디에도 없다** (28세션 4절 · 53세션 1-4). 계산만 남는다.
    columns[2].metric("절감액", fmt.won_year(result.annual_saving_won))
    if result.switch_needed:
        st.write(
            f"가장 유리한 요금제는 **{selection_label(table, result.best.selection)}** 입니다."
        )
    else:
        st.write("현재 요금제가 이미 가장 유리합니다. 바꿀 이유가 없습니다.")
    # **① 차액이 먼저다** (17세션 1-3). 35억 위에서 5천만원이 움직이는 것을 절대
    # 금액 축에 그리면 막대 셋이 같은 높이로 보인다 — 변화만 떼어 먼저 보인다.
    st.altair_chart(charts.tariff_delta_chart(result), width="stretch")
    # **읽는 법은 툴팁 하나로 족하다** (27세션 4-2). 「0 보다 왼쪽이면 절감」 이
    # 물음표 안에 이미 있는데 바로 아래 캡션이 같은 말을 되풀이하고 있었다.
    # 절사 각주도 여기 두지 않는다 — 3단계 「개선안별 요약」 한 곳이다 (25세션 4-5).
    st.caption("현행 대비 차액", help=fmt.chart_tip("chart.tariff_delta"))
    # **② 그룹 막대** (17세션 1-2). 쌓으면 기본요금끼리·전력량요금끼리 견줄 수 없다.
    st.altair_chart(charts.tariff_option_chart(result), width="stretch")
    st.caption("요금제별 기본·전력량·합계", help=fmt.chart_tip("chart.tariff_option"))
    # 본문에서 내리는 사실 둘은 :data:`_TARIFF_HIDDEN_FACTS` 가 쥔다 (27세션 4-3·4-4).
    _notices(partition_facts(result.notices, _TARIFF_HIDDEN_FACTS)[1])
    _worksheet(tariff_switch_worksheet(result))


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
    # **여유가 없으면 입력칸을 감춘다** (13세션). 움직여도 0% 라 고장으로 보인다.
    peak = diagnosis.peak.billing_demand_kw
    headroom = (form.contract_kw - peak) / form.contract_kw if form.contract_kw else 0.0
    low, high = margin_range()
    if headroom <= low:
        # **여유율을 세션에 남긴다.** 입력칸을 감춘 경우에도 3단계가 같은 값을
        # 읽어야 2단계 카드와 숫자가 어긋나지 않는다 (14세션 5-1). 위젯이 이번
        # 실행에서 만들어지지 않았으므로 키를 직접 써도 된다.
        margin = default_margin_ratio()
        st.session_state[input_key("contract", "margin")] = margin
    else:
        _adequacy(diagnosis)
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
            help=fmt.tip("contract_margin"),
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
    if result.reduction_kw <= 0:
        # **지표는 되살리고 경고·근거는 계속 감춘다** (31세션 2절).
        #
        # 27세션에 이 자리를 한 줄로 줄였다 — 지표 넷이 모두 「0」 을 말하고 경고
        # 둘은 하지도 못할 하향을 조심하라는 말이었기 때문이다. 줄이고 보니 이
        # 카드만 큰 글자 숫자가 없어 **다른 개선안과 나란히 훑을 수가 없었다.**
        # 「할 일이 없다」 와 「값을 알 수 없다」 는 다르다.
        #
        # 그래서 **무엇을 보이느냐를 바꿨다.** 현행·권장·절감액(전부 0)이 아니라
        # **왜 여지가 없는지 말하는 넷**을 세운다 — 계약전력과 요금적용전력이
        # 얼마나 붙어 있는지가 그 이유다. 경고와 산출 근거는 그대로 감춘다.
        columns = st.columns(4)
        columns[0].metric("현재 계약전력", fmt.kw(result.contract_kw))
        columns[1].metric("요금적용전력", fmt.kw(peak))
        columns[2].metric(
            "여유",
            fmt.ratio_pct(headroom),
            help=fmt.markdown_safe(
                "(계약전력 − 요금적용전력) ÷ 계약전력.\n\n"
                "이 값이 확보할 여유율보다 작으면 낮출 자리가 없습니다."
            ),
        )
        columns[3].metric("하향 여지", fmt.kw(result.reduction_kw))
        st.write(
            f"현재 계약전력 {fmt.kw(result.contract_kw, decimals=0)} 는 요금적용전력 "
            f"{fmt.kw(peak)} 대비 여유가 {fmt.ratio_pct(headroom)} 입니다. "
            "하향 여지가 없습니다."
        )
        return
    columns = st.columns(4)
    columns[0].metric("현행 계약전력", fmt.kw(result.contract_kw))
    columns[1].metric("권장", fmt.kw(result.suggested_contract_kw))
    columns[2].metric("하향 여지", fmt.kw(result.reduction_kw))
    # **「0원」 이 아니라 「기본요금 변화없음」 이다** (48세션). 여지 8 kW 옆의 0원은
    # 계산이 덜 된 것처럼 읽힌다 — 둘은 다른 물음이고, 왜 안 바뀌는지는 아래
    # 근거(`contract.floor_not_binding`)가 이미 낸다. 문구를 새로 만들지 않았다.
    columns[3].metric(
        "절감액",
        fmt.won_year(
            result.annual_saving_won,
            reason=result.saving_basis,
            zero_reason=BASE_FEE_UNCHANGED if result.base_fee_unchanged else None,
        ),
    )
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
        help=(
            "요금적용전력 ÷ 계약전력.\n\n낮을수록 계약을 과하게 잡아 둔 것이라 하향 여지가 큽니다."
        ),
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
    # **넷을 한 줄에 둔다** (31세션 3-1). 27세션까지 3+1 로 갈려 있어 연간 감축
    # 가능량만 아래에 따로 섰다 — 다른 개선안은 모두 넷이 한 줄이라, 이 카드만
    # 훑는 눈이 한 번 더 멈췄다. **값이 아니라 자리가 문제였다.**
    #
    # **넷 다 툴팁을 단다** (31세션 3-2). 이름만으로는 무엇을 센 것인지 알 수
    # 없다 — 「거래 가능일」 이 평일 전부인지 참여할 수 있는 날인지, 「등록 권장
    # 용량」 이 평균인지 보수값인지가 갈린다.
    columns = st.columns(4)
    columns[0].metric(
        "거래 가능일",
        fmt.days(result.eligible_days),
        help=fmt.markdown_safe(
            "분석 기간의 평일 수입니다. 토·일·공휴일은 입찰할 수 없어 뺐습니다.\n\n"
            "이 가운데 감축할 여력이 있는 날만 아래 「저부하 평일」 로 셉니다."
        ),
    )
    columns[1].metric(
        "저부하 평일",
        fmt.days(result.low_load_days),
        help=fmt.markdown_safe(
            "거래 가능일 중 부하가 쉬는 날 수준까지 내려온 날 수입니다.\n\n"
            "**연간 참여 일수 제한이 없으므로 이 날 수가 실질 제약입니다** — "
            "감축할 여력이 있는 날이 몇 날이냐가 전부입니다."
        ),
    )
    columns[2].metric(
        "등록 권장 용량",
        # **소수점을 없앤다** (31세션 3-3). 저부하일 여력 분포의 분위수라 원래
        # 소수 자리에 뜻이 없고, 사업자와 계약할 때 적는 값도 정수다.
        fmt.kw(result.registered_capacity_kw, decimals=0),
        help=fmt.markdown_safe(
            "사업자 등록 시 제시할 보수적인 감축 가능 용량입니다.\n\n"
            "저부하일 여력 분포의 하위값이라 어느 참여일에나 지킬 수 있습니다 — "
            "평균으로 등록하면 절반의 날에 미달하고, 미달은 입찰 제한으로 이어집니다."
        ),
    )
    columns[3].metric(
        "연간 감축 가능량",
        fmt.kwh(result.annual_reducible_kwh),
        help=fmt.markdown_safe(
            "실제 참여 가능 시간과 일별 감축 여력을 반영한 연간 감축 잠재량입니다.\n\n"
            "저부하일마다 (그날 여력 × 그날 참여 가능 시간)을 더해 365일로 "
            "환산했습니다 — 등록 용량 × 시간이 아닙니다."
        ),
    )
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
    st.caption("일별 판정 시간대 평균 부하", help=fmt.chart_tip("chart.dr_daily"))
    # **어떤 날인지 보여 준다.** 창립기념일·워크숍처럼 사무실을 비우는 날일 가능성이
    # 높아, 목록을 보면 사용자가 스스로 맞는 날인지 판정할 수 있다 (14세션 4절).
    #
    # **판정에서 그치지 않고 뺄 수 있게 했다** (29세션). 목록 아래 고르는 칸이
    # 그것이고, 고른 날은 거래 가능일에서 빠져 감축량이 다시 계산된다. 앞의
    # 캡션(「사무실을 비우는 날일 가능성이 높습니다」)은 이 칸이 대신한다 —
    # 문구를 하나 더할 때는 뺄 것을 함께 정한다 (CLAUDE.md).
    #
    # **뺀 날이 있으면 목록이 비어도 접힘을 남긴다.** 남기지 않으면 되돌릴 위젯이
    # 사라져 선택이 영영 고정된다 — 16세션에 겪은 「그리지 않은 위젯」 의 반대편이다.
    off_days = dr_off_days()
    if result.low_load_days or off_days:
        with st.expander(f"저부하 평일 {result.low_load_days}일", expanded=False):
            if result.low_load_days:
                st.dataframe(result.low_load_day_table, hide_index=True, width="stretch")
            _off_day_picker(result, off_days)
    if not result.low_load_days:
        st.write("저부하 평일이 없어 감축 가능량을 0 으로 두었습니다.")
    if result.is_priced:
        st.metric("정산금", fmt.won_short(result.settlement_won))
    st.caption(
        "자원 유형 — " + (", ".join(str(item) for item in result.resource_types) or "판정 불가")
    )
    # **참여 안내는 근거다** (22세션 1절). 카드가 직접 그리던 것을 등급 체계로
    # 되돌렸다 — 하루 몇 회·몇 시간이라는 제도 설명이지 경고가 아니고, 확인사항
    # 넷 가운데 하나를 차지하고 있었다. 문자열 조각(``startswith``)으로 거르던
    # 자리도 함께 지웠다 (20세션에 폐기한 방식의 잔재였다).
    _notices(result.notices)
    _worksheet(demand_response_worksheet(result))


def _off_day_picker(result: DemandResponseResult, off_days: tuple[str, ...]) -> None:
    """**쉬는 날을 사람이 뺀다** (29세션).

    공휴일 라이브러리가 못 잡는 날이 있다 — 근로자의 날은 2026년부터 법정
    공휴일이고, 임시공휴일은 요금표 관행에 맞춰 계량에서 빼므로 거래일 판정에는
    평일로 남는다 (:data:`~kwise.diagnose.dr.LIBRARY_HOLIDAY_GAPS`). 앞으로 어떤
    날이 임시공휴일로 지정될지도 알 수 없으므로 **자동 판정을 늘리는 대신 목록을
    보여 주고 고르게 한다.**

    고른 날짜는 **위젯 키에 그대로 남고 1단계 진단이 그것을 읽는다** — DR
    프로파일을 처음부터 다시 만들어야 기준선·문턱·감축량이 함께 움직인다.

    **값은 위젯 키 하나에만 둔다.** 고른 것을 다른 열쇠로 옮겨 적으면 그 옮기는
    일이 이 카드(2단계)에서 일어나므로, 1단계가 읽는 시점에는 아직 옛 값이다 —
    한 번 더 눌러야 숫자가 바뀌는 화면이 된다. 라벨(요일)은 ``format_func`` 로
    그리고 **값 자체는 ISO 날짜**로 둔다.
    """
    frame = result.low_load_day_table
    # **이미 뺀 날도 선택지에 남긴다** — 빼고 나면 목록에서 사라지므로, 선택지가
    # 목록뿐이면 되돌릴 수가 없다.
    dates = sorted({str(value) for value in frame["날짜"]} | set(off_days))
    st.multiselect(
        "쉬는 날이어서 뺄 날짜",
        dates,
        format_func=lambda value: f"{value} ({_weekday_of(value)})",
        key=_OFF_DAY_WIDGET,
        help=(
            "근로자의 날이나 임시공휴일이 섞여 있을 수 있습니다. 고른 날은 거래 "
            "가능일에서 빼고 감축량을 다시 계산합니다."
        ),
    )


#: 요일 이름. 목록 표(:meth:`~kwise.diagnose.dr.DrProfile.low_load_day_table`)와
#: 같은 말을 쓴다 — 고르는 자리와 목록이 다른 말을 하면 같은 날인지 알 수 없다.
_WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")


def _weekday_of(value: str) -> str:
    return _WEEKDAYS[dt.date.fromisoformat(value).weekday()]


#: 고른 날짜가 담기는 위젯 키. ``measure_`` 로 시작하므로 화면을 갈아 끼워도
#: :func:`kwise.ui.state.carry_inputs` 가 지켜 준다 (16세션 0-1).
_OFF_DAY_WIDGET = input_key("demand_response", "off_days")


def dr_off_days() -> tuple[str, ...]:
    """1단계가 읽는 「쉬는 날」 (29세션).

    **화면 순서와 반대로 흐른다.** 고르는 자리는 2단계 카드인데 쓰는 자리는
    1단계 진단이다 — Streamlit 은 위젯 값을 세션에 먼저 넣고 스크립트를 위에서
    아래로 다시 돌리므로, 고른 그 실행에서 바로 반영된다. 대표일
    (:func:`kwise.ui.state.reference_day`)과 ESS 목표가 쓰는 것과 같은 방식이다.
    """
    value = st.session_state.get(_OFF_DAY_WIDGET) or ()
    return tuple(str(item) for item in value)


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
    columns[2].metric("절감액", fmt.won_year(result.annual_saving_won))
    columns[3].metric(
        "회수기간", fmt.payback(result.payback_years, investment_won=result.investment_won)
    )
    # **각이 좁아지는 모습**이 개선의 전부다 (15세션 2-3).
    triangle_col, day_col = st.columns(2)
    with triangle_col:
        st.altair_chart(charts.power_triangle_chart(result), width="stretch")
        st.caption("전력삼각형 — 개선 전후", help=fmt.chart_tip("chart.power_triangle"))
    with day_col:
        if day is not None:
            st.altair_chart(
                charts.power_factor_day_chart(
                    usage, day, current_pct=result.current_pct, target_pct=result.target_pct
                ),
                width="stretch",
            )
            st.caption(
                f"{day.title} · 15분 부하와 판정 창",
                help=fmt.chart_tip("chart.power_factor_day"),
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
    _worksheet(power_factor_worksheet(result))


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
            # 밀도 설명은 **기준 데이터에서 온다.** 툴팁도 마크다운을 해석하므로
            # 파일에서 온 글은 escape 해서 넣는다 (25세션 2절).
            help="\n\n".join(
                (
                    *(
                        fmt.markdown_safe(f"**{item.label}** — {item.tradeoff}")
                        for item in presets.densities
                    ),
                    manual_tip("pv-density"),
                )
            ),
        )

    st.caption(
        f"지역 — {region_key.replace('/', ' ')} (옆단 「건물 정보」 에서 고칩니다)",
        # **`weather-source` 앵커의 새 자리다** (31세션 4-2). 격자와 시군구 선택
        # 이야기라 지역을 적는 이 줄이 제자리이고, 출처 이름 옆에서는 「이 이름이
        # 무슨 뜻인가」 를 묻게 만들 뿐이었다.
        help=manual_tip("weather-source"),
    )

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
        help=(
            "견적 단가입니다. 넣지 않으면 투자비와 회수기간을 산출하지 않습니다.\n\n"
            + manual_tip("pv-cost")
        ),
    )

    # ---- 확장 패널 (접어 둔다)
    # 접혀 있다는 사실은 **보면 안다** (25세션 4-4). 라벨에 적지 않는다.
    with st.expander("상세", expanded=False):
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
    # **출처는 이름으로 적는다** (31세션 4-2). `cache`·`network`·`archive` 라는
    # 코드 식별자가 그대로 화면에 나가고 있었다. 툴팁은 뗐다 — 출처 이름 하나면
    # 되고, 격자·라이선스 이야기는 위 「지역」 캡션의 물음표에 있다.
    st.caption(f"기상 출처 — {weather_source_label(source)}")
    point = curve.points[-1]
    if stale:
        st.caption("**묵은 결과** — 지금 화면의 입력이 아니라 마지막 계산의 입력 기준입니다.")
    _overview(spec)
    months = curve.base_fee_months
    # **고른 잉여 처리를 절감액에 싣는다** (48세션). 41세션이 잉여를 태양광 안으로
    # 옮기면서 금액 합치기를 하지 않아, 화면에 **더해지지 않는 두 수**가 남아
    # 있었다 — 절감액 2,543만원과 잉여 수익 241만원. 회수기간은 앞의 것만 봤다.
    # 아무것도 고르지 않았으면 더하지 않는다 (`기본값 없음` 을 지킨다).
    surplus = _surplus_result(usage, table, form, unit_profile, point)
    scenario, revenue = chosen_surplus_revenue(surplus)
    point = with_surplus_revenue(
        point, revenue_won=revenue, scenario=scenario, base_fee_months=months
    )
    columns = st.columns(4)
    columns[0].metric("용량", fmt.kwp(point.capacity_kwp))
    # **발전량은 MWh 다** (26세션 3-3). kWh 는 백만 자리라 눈으로 읽히지 않는다.
    columns[1].metric("발전량", fmt.per_year(fmt.mwh(annualize(point.generation_kwh, months))))
    # **지표를 늘리지 않는다** (48세션). 자가소비분과 잉여 수익을 두 칸에 세우면
    # 어느 쪽이 결론인지 흐려진다 — 한 칸에 합쳐 내고 툴팁이 가른다.
    columns[2].metric(
        "절감액",
        fmt.won_year(point.annual_saving_won),
        help=_solar_saving_tip(point, months),
    )
    columns[3].metric(
        "회수기간", fmt.payback(point.payback_years, investment_won=point.investment_won)
    )
    # **투자비를 모르면 빈칸이나 0원이 아니라 사유다** (7.5).
    st.caption("투자비 — " + fmt.won(point.investment_won, reason=curve.cost.reason))

    # **두 조각으로 나눠 쓴다** (52세션 1-4). ① 무엇을 계산했나 ② 참고.
    #
    # 51세션까지는 「**용량 판정** — …」 뒤에 판정을 붙이고 그 아래 근거를 한 줄
    # 더 달았다. **두 자료에서 다 어긋났다** — 대형은 「회수기간 기준 가장
    # 유리」 가 거짓이었고(56 kWp 가 더 짧다), 소형은 참고값인 권장이 계산에
    # 쓰인 값처럼 읽혔다. 배지(**설치 가능 면적 상한**)가 문장 중간에 박혀
    # 읽기도 어려웠다.
    #
    # **라벨과 줄표를 뗐다.** 문장이 스스로 무엇인지 말하므로 「용량 판정 —」 이
    # 필요 없고, 그 줄표 때문에 본문이 빈 줄표로 시작하는 것처럼 보였다.
    verdict = curve.verdict()
    st.markdown(fmt.markdown_safe(verdict.headline(inputs.area_m2 or None)))
    reference = verdict.reference_note(surplus_free_capacity_kwp(usage, unit_profile))
    if reference:
        st.caption(fmt.markdown_safe(reference))

    # **판단의 갈림길은 잉여다** (26세션 3절). 회수기간 최소만으로는 정할 수 없다 —
    # 잉여를 내면 상계거래 계약과 역송 계량기가 따라오므로, 「잉여 없이 어디까지」 와
    # 「지금 용량이면 얼마나 남는가」 를 나란히 둔다.
    _surplus_verdict(surplus, unit_profile, usage, point, months, presets)
    _surplus_handling(usage, table, form, unit_profile, point, months, surplus)

    # **대표 지점을 표로** (17세션 3-3). 곡선 그래프는 26세션에 걷어냈다 —
    # 절감액 세 계열이 단조롭게 늘기만 해 읽을 것이 없었고, 판단은 위의 잉여
    # 숫자로 한다. 20단계 상세는 Excel 로 간다.
    #
    # **잉여 지점 둘을 표에 세운다** (31세션 4-1). 곡선은 설치 가능 면적이
    # 허용하는 용량까지만 도므로, 그것만 뽑으면 비교 용량이 전부 선정 용량보다
    # 작았다 — 정작 「어디서부터 잉여가 생기나」 가 표에 없었다.
    surplus_points = cached_surplus_points(
        usage,
        table,
        unit_profile,
        baseline,  # type: ignore[arg-type]
        quality,
        usage_token(usage),
        form,
        inputs,
        rules_stamp(),
    )
    # 이름을 `density` 로 두면 위쪽 라디오 선택값(문자열)을 덮는다.
    density_preset = presets.density(inputs.density_key or presets.default.key)
    st.markdown("**용량별 비교**")
    st.dataframe(
        _capacity_view(
            charts.solar_capacity_table(
                curve,
                verdict=verdict,
                surplus_points=surplus_points,
                gcr=density_preset.gcr,
                area_per_kwp_m2=presets.area_per_kwp_m2,
                area_limit_m2=inputs.area_m2 or None,
            )
        ),
        hide_index=True,
        width="stretch",
    )
    # **표식의 판정 근거를 표 바로 아래 적는다** (50세션). 회수기간 11.0년 줄을
    # 두고 11.1년 줄에 표식이 붙는데, 그 규칙을 모르면 표가 틀린 것으로 읽힌다.
    # 「용량 판정」 캡션에 있던 같은 뜻의 문장을 여기로 옮겼다 — 문구를 늘리지
    # 않으려면 한 자리를 비워야 하고, 판정 근거는 표식 옆에서 읽혀야 한다.
    # **각주는 규칙이 실제로 쓰일 때만 붙는다** (51세션 1절). 단가를 넣지 않으면
    # 절감액 최대를 그대로 고르므로 동률 폭이 쓰이지 않는데, 50세션은 조건 없이
    # 달아 표식이 「최대 절감액」 인 화면에서 각주만 「권장」 을 말하고 있었다.
    tie_note = verdict.tie_note
    st.caption(
        "잉여가 생기기 시작하는 용량과 많이 생기는 용량을 함께 세웠습니다 — "
        "설치 가능 면적을 넘는 줄은 「면적 초과」 로 적습니다. "
        + (tie_note + " " if tie_note else "")
        + "20단계 상세는 Excel 「태양광 용량 곡선」 시트에 있습니다."
    )

    generation = unit_profile * point.capacity_kwp
    # **일별 발전량 하나만 그린다** (23세션 5절). 사용량과 함께 그리니 60 MWh 대
    # 사용량에 3 MWh 대 발전량이 눌려 보이지 않았다.
    st.altair_chart(charts.solar_annual_chart(usage, generation), width="stretch")
    ratio = charts.solar_saving_ratio(usage, generation)
    st.caption(
        "날짜별 발전량"
        + (f" · 연간 사용량의 **{fmt.ratio_pct(ratio)}** 를 줄입니다" if ratio else ""),
        help=fmt.chart_tip("chart.solar_annual"),
    )
    if day is not None:
        # **하루 전체 곡선을 지웠다** (23세션 5-3). 스물넷을 다 그리면 저감 구간이
        # 손톱만 해져 확대본과 나란히 둘 이유가 없다.
        st.altair_chart(charts.solar_day_chart(usage, generation, day, zoom=True), width="stretch")
        st.caption(
            f"{day.title} · 피크 앞뒤 {charts.PEAK_ZOOM_HOURS}시간",
            help=fmt.chart_tip("chart.solar_day"),
        )
    _notices(curve.notices)
    _worksheet(solar_worksheet(curve, point))


def solar_surplus_scenario() -> str:
    """고른 잉여 처리 방식. **안 골랐으면 기준선인 출력제어다** (57세션).

    41세션은 「기본값 없음」 으로 두었다 — 상계와 외부 판매는 계약 상대도 정산
    절차도 다른 길이라 미리 골라 두면 그것이 권고로 읽힌다는 까닭이었다.
    **그런데 「고르지 않음」 도 하나의 답이었다** — 0원이 더해지므로 값으로는
    출력제어와 같은데, 화면은 무엇이 기준인지 말하지 않았다. 잉여가 나는
    자료에서 그 선택이 절감액을 바꾸므로, 사용자가 접힘을 못 보고 지나치면
    **어느 전제로 나온 숫자인지 모른 채** 결론을 읽게 된다.

    **아무 절차도 필요 없는 쪽이 기준선이다.** 상계거래는 계약 변경과 역송
    계량기가 따라오는데 그것을 전제로 절감액을 부풀리면 안 된다.

    ``dr_off_days`` 와 같은 방식이다 — 고르는 자리는 태양광 카드 안인데 쓰는
    자리가 그 위 지표와 3단계다. Streamlit 이 위젯 값을 세션에 먼저 넣고
    스크립트를 다시 돌리므로 고른 그 실행에서 바로 반영된다.
    """
    return str(st.session_state.get(input_key("solar", "surplus_use")) or CURTAIL_SCENARIO)


def chosen_surplus_revenue(result: SurplusResult | None) -> tuple[str, float | None]:
    """(고른 시나리오 이름, 관측 기간 수익). 안 골랐으면 ``("", None)``.

    금액을 못 내는 경우(단가 미입력)에는 이름만 돌려준다 — 지어낸 0원을 절감액에
    더하지 않는다.
    """
    scenario = solar_surplus_scenario()
    if not scenario or result is None:
        return "", None
    try:
        return scenario, result.scenario(scenario).revenue_won
    except KeyError:
        # 용량이 상계 상한을 넘어 선택지에서 빠진 경우다. 고른 적이 없는 것으로 본다.
        return "", None


def _solar_saving_tip(point: SolarPoint, months: float) -> str:
    """절감액에 **무엇이 들어 있는지** 한 줄 (48세션 · 57세션에 언제나 붙인다).

    41세션까지는 아무것도 고르지 않을 수 있어 「안 골랐으면 안 붙인다」 였다.
    57세션에 기준선(출력제어)이 기본이 되면서 **언제나 고른 것이 있다** — 그러면
    이 줄이 「지금 이 숫자에 무엇이 들어 있나」 에 늘 답한다.
    """
    if not point.surplus_scenario:  # pragma: no cover - 기본값이 있어 비지 않는다
        return ""
    self_consumed = money.won(annualize(point.self_consumption_saving_won, months), reason="—")
    added = money.won(annualize(point.surplus_revenue_won, months), reason="—")
    head = f"자가소비로 줄인 요금 {self_consumed} + 잉여 {point.surplus_scenario} {added}."
    if not point.surplus_revenue_won:
        return fmt.markdown_safe(head)
    return fmt.markdown_safe(
        head + '\n\n역송분은 요금 계산에서 빠져 있고, 상계 차감은 그 뒤에 남은 '
        "순부하 사용량을 한도로 잽니다 — 겹쳐 세지 않았습니다."
    )


def _surplus_result(
    usage: UsageData,
    table: TariffTable,
    form: ContractForm,
    unit_profile: pd.Series,
    point: SolarPoint,
) -> SurplusResult:
    """잉여 계산 한 벌. **세 자리가 같은 인자로 부른다** — 기억에 걸린다."""
    external, smp = surplus_prices()
    return cached_surplus(
        usage,
        table,
        unit_profile,
        usage_token(usage),
        form,
        point.capacity_kwp,
        external,
        rules_stamp(),
        smp,
    )


def _surplus_verdict(
    surplus: SurplusResult,
    unit_profile: pd.Series,
    usage: UsageData,
    point: SolarPoint,
    months: float,
    presets: PvPresets,
) -> None:
    """**잉여를 낼 것인가** — 태양광 규모를 가르는 물음 (26세션 3-2).

    셋을 나란히 낸다.

        지금 용량의 연간 잉여      MWh/년 · 발전량 대비 비중
        언제 남는가               평일 / 토·일·공휴일
        잉여 없이 지을 수 있는 최대  용량과 그때의 면적

    **41세션에 7.7 카드가 없어졌다.** 「남는 것으로 무엇을 하나」 도 이 카드로
    들어와 바로 아래 :func:`_surplus_handling` 이 낸다 — 잉여는 태양광을 얼마나
    크게 지을지에 따라 나오는 결과이지 따로 고르는 수단이 아니기 때문이다.
    """
    density = presets.density(
        (get_solar_inputs() or SolarInputs(region_key="")).density_key or presets.default.key
    )
    free_kwp = surplus_free_capacity_kwp(usage, unit_profile)
    free_area = area_from_capacity_m2(
        free_kwp, gcr=density.gcr, area_per_kwp_m2=presets.area_per_kwp_m2
    )
    holiday_kwh = surplus.weekend_kwh + surplus.holiday_kwh

    columns = st.columns(4)
    columns[0].metric(
        "연간 잉여",
        fmt.per_year(fmt.mwh(annualize(surplus.total_kwh, months))),
        f"발전량의 {fmt.ratio_pct(surplus.share_of_generation)}",
        delta_color="off",
    )
    columns[1].metric(
        "평일 잉여",
        fmt.per_year(fmt.mwh(annualize(surplus.weekday_kwh, months))),
        _share(surplus.weekday_kwh, surplus.total_kwh),
        delta_color="off",
    )
    columns[2].metric(
        "토·일·공휴일 잉여",
        fmt.per_year(fmt.mwh(annualize(holiday_kwh, months))),
        _share(holiday_kwh, surplus.total_kwh),
        delta_color="off",
    )
    columns[3].metric(
        "잉여 없는 최대 용량",
        fmt.kwp(free_kwp),
        f"면적 {fmt.count(free_area, ' m²', decimals=0)}",
        delta_color="off",
        help=fmt.tip("surplus_free"),
    )


def _surplus_handling(
    usage: UsageData,
    table: TariffTable,
    form: ContractForm,
    unit_profile: pd.Series,
    point: SolarPoint,
    months: float,
    surplus: SurplusResult,
) -> None:
    """남는 것으로 무엇을 하나 — **잉여가 있을 때만 묻는다** (41세션 2-2).

    잉여가 0 이면 아무것도 그리지 않는다. 위 지표가 이미 「잉여 없는 최대 용량」
    으로 「생기려면 얼마가 필요한가」 에 답하고 있으므로, 고를 것이 없는 자리에
    선택지를 세우면 없는 결정을 만든다.

    **잉여가 나면 펼쳐 둔다** (57세션). 41세션은 본문 예산을 이유로 접었는데,
    **접힘 안은 예산을 따로 세므로** 그 근거가 서지 않았다. 그리고 접어 두면
    사용자가 절을 못 보고 지나쳐 **무엇을 전제로 나온 절감액인지 모른 채**
    결론을 읽는다 — 잉여가 나는 자료에서 그 선택은 금액을 바꾼다.

    **27세션의 「카드는 접고 시작」 과 어긋나지 않는다.** 실제 규약은 16세션
    0-2 의 **「켠 카드는 펼쳐진 채로 남는다」** 이고 수단 카드 자체가 이미
    ``expanded=True`` 다. 여기는 카드가 아니라 **카드 안의 절**이므로, 켠 카드
    안에서 다시 접어 두는 쪽이 오히려 그 규약과 어긋난다.

    **기본은 출력제어다.** 아무 절차도 필요 없는 쪽이 기준선이다 —
    :func:`solar_surplus_scenario`.
    """
    if surplus.total_kwh <= 0:
        return
    options = surplus_options(point.capacity_kwp)
    with st.expander("잉여 처리", expanded=True):
        choice = st.radio(
            "남는 전기를 어떻게 하나",
            options,
            index=options.index(CURTAIL_SCENARIO),
            key=input_key("solar", "surplus_use"),
            horizontal=True,
            help=manual_tip("measure-surplus"),
        )
        if choice is None:  # pragma: no cover - 기본값이 있어 비지 않는다
            return
        # **단가는 위젯 키에만 둔다** (48세션). :func:`_surplus_result` 가 세션에서
        # 읽으므로 여기서 값을 받아 넘기지 않는다 — 옮겨 적으면 카드 위 절감액과
        # 이 접힘 안의 수익이 한 실행 어긋난다 (`_off_day_picker` 와 같은 이유).
        # **기본값은 기준 데이터에서 온다** (58세션). 비우면 「미산출」 로 돌아간다.
        if choice == EXTERNAL_SCENARIO:
            st.number_input(
                "잉여 판매 단가 (원/kWh) — 0 이면 미산출",
                min_value=0.0,
                value=default_external_price_won_per_kwh(),
                step=1.0,
                key=input_key("solar", "surplus_price"),
                help="우리가 파는 쪽의 단가입니다. 비우면 금액을 산출하지 않습니다.",
            )
        elif choice == OFFSET_SCENARIO and offset_settles_cash(point.capacity_kwp):
            st.number_input(
                "SMP 단가 (원/kWh) — 0 이면 미산출",
                min_value=0.0,
                value=default_smp_price_won_per_kwh(),
                step=1.0,
                key=input_key("solar", "smp_price"),
                help="당월 차감하고 남은 몫을 정산하는 단가입니다. 비우면 잔여량만 냅니다.",
            )
        result = _surplus_result(usage, table, form, unit_profile, point)
        scenario = result.scenario(choice)
        revenue = scenario.revenue_won
        columns = st.columns(4)
        columns[0].metric(
            "연간 수익",
            fmt.per_year(fmt.won_short(annualize(revenue, months)))
            if revenue is not None
            else fmt.DASH,
            # **위 절감액에 이미 들어 있다** (48세션). 고른 뒤에 보는 자리라
            # 여기서는 그 몫이 얼마인지만 낸다.
            delta_color="off",
        )
        # 금액을 못 내면 **사유를 적는다** — 빈칸이나 0원으로 두지 않는다 (7.5).
        st.caption(
            fmt.markdown_safe(scenario.basis)
            if revenue is not None
            else fmt.won(revenue, reason=scenario.basis)
        )
        # **어느 단가로 나온 금액인가** (58세션). 기본값이 생겨 「미산출」 이
        # 사라졌으므로 이 줄이 없으면 참고값이 확정값으로 읽힌다. 문구는
        # :meth:`SurplusResult.applied_price_note` 하나이고 PPT·Excel·Word 가
        # 같은 것을 쓴다.
        if result.applied_price_note:
            st.caption(fmt.markdown_safe(result.applied_price_note))
        # **잉여가 주말에 몰리는지**가 보여야 한다 (15세션 2-6). 7.7 카드가 지고
        # 있던 그림인데, 41세션에 카드를 없애면서 여기로 옮겼다 — 무엇을 팔지
        # 정하는 자리라 오히려 제자리다. 접힘 안이므로 본문 예산에 들지 않는다.
        surplus_kw = apply_generation(usage, unit_profile * point.capacity_kwp).surplus_kw
        st.altair_chart(charts.surplus_daily_chart(usage, surplus_kw), width="stretch")
        st.caption("날짜별 잉여", help=fmt.chart_tip("chart.surplus_daily"))
        _surplus_carry(result)
        # **적용 단가는 바로 위 캡션이 이미 냈다.** 툴팁에도 넣으면 같은 문장이
        # 한 절에 두 번 선다 — 20세션이 사실 ID 로 접기 시작한 그 병이다.
        _notices(tuple(item for item in result.notices if item.fact != "surplus.applied_price"))


def _surplus_carry(result: SurplusResult) -> None:
    """이월이 생긴 달만 낸다. **없으면 표시하지 않는다** (41세션 2-3).

    부하가 큰 건물에서는 거의 생기지 않는데, 늘 「이월 없음」 을 적으면 없는
    항목이 자리를 차지한다.
    """
    settlement = result.offset
    if settlement is None:
        return
    carried = settlement.carried
    if not carried:
        return
    st.dataframe(
        {
            "월": [item.month for item in carried],
            "차감": [fmt.kwh(item.deducted_kwh) for item in carried],
            "다음 달로 이월": [fmt.kwh(item.carried_out_kwh) for item in carried],
        },
        hide_index=True,
        width="stretch",
    )
    st.caption("그 달 그 계시 사용량을 넘어 차감하지 못한 몫입니다.")


def _share(part: float, total: float) -> str:
    """구성비. 총량이 0 이면 비율이 없다 — 0% 로 적으면 「없다」 가 「0이다」 가 된다."""
    return fmt.ratio_pct(part / total) if total > 0 else fmt.DASH


def _ess_spec_view(frame: pd.DataFrame) -> pd.DataFrame:
    """목표별 사양 표를 **사람이 읽는 문자열로** 굳힌다 (46세션).

    태양광 용량 표(:func:`_capacity_view`)와 같은 방식이다 — 두 표가 한 화면에
    있으므로 서식이 갈리면 다른 것을 재는 표처럼 보인다.
    """
    if frame.empty:
        return frame
    return pd.DataFrame(
        {
            # **목표는 범위다** (50세션 3-6). 격자를 쓰면 목표 여럿이 한 사양으로
            # 뭉치고, 그 범위가 곧 「이 목표 범위는 같은 설비로 덮인다」 는 사실이다.
            "목표": [f"{value} kW" for value in frame["목표 요금적용전력(kW)"]],
            "저감량": [fmt.kw(value, decimals=0) for value in frame["저감량(kW)"]],
            "출력": [fmt.kw(value, decimals=0) for value in frame["출력(kW)"]],
            "용량": [fmt.kwh(value) for value in frame["용량(kWh)"]],
            "방전시간": [fmt.hours(value) for value in frame["방전시간(h)"]],
            "투자비": [fmt.won_short(value, reason="—") for value in frame["투자비(원)"]],
            "연간 절감액": [fmt.won_year(value) for value in frame["연간 절감액(원)"]],
            "회수기간": [
                fmt.payback(years, investment_won=investment)
                for years, investment in zip(
                    frame["회수기간(년)"], frame["투자비(원)"], strict=True
                )
            ],
            "표식": frame["표식"],
        }
    )


def _self_consumption(ratio: float | None) -> str:
    """자가소비율 — **100.0% 는 잉여가 정확히 0 일 때만** (52세션 1-5).

    반올림하면 잉여가 조금 있는 줄도 100.0% 가 된다. 그 표에는 바로 아래에
    「잉여 시작」 표식이 붙어 있어 **표가 스스로 어긋나 보였다.** 내림으로 자르면
    「100.0% ⟺ 잉여 없음」 이 표 안에서 맞는다.
    """
    if ratio is None or pd.isna(ratio):
        return fmt.DASH
    value = float(ratio)
    return fmt.ratio_pct(value if value >= 1.0 else math.floor(value * 1_000.0) / 1_000.0)


def _capacity_view(frame: pd.DataFrame) -> pd.DataFrame:
    """용량 표를 **사람이 읽는 문자열로** 굳힌다 (17세션 3-3)."""
    if frame.empty:
        return frame
    return pd.DataFrame(
        {
            "용량(kWp)": [fmt.kwp(value) for value in frame["용량(kWp)"]],
            # **면적이 판단 기준이다** (31세션 4-1). kWp 는 설비 규격이라 지붕을
            # 보고 「지을 수 있나」 를 정할 수 없다.
            "필요 면적": [
                fmt.count(value, " m²", decimals=0) if pd.notna(value) else fmt.DASH
                for value in frame["필요 면적(m²)"]
            ],
            # 발전량은 MWh 로 낸다 (26세션 3-3). 12개월 환산값이라 /년 을 붙인다.
            "발전량": [fmt.per_year(fmt.mwh(value)) for value in frame["연간 발전량(kWh)"]],
            # **100.0% 는 잉여가 정확히 0 일 때만 나온다** (52세션 1-5). 곡선 격자
            # 한 칸 위에서 잉여가 수십 kWh 뿐이라(48 kWp 에서 33.7 kWh · 발전량의
            # 0.06%) 반올림하면 100.0% 로 찍혔다 — 그 줄 아래에 「잉여 시작」 이
            # 붙어 있어 표가 스스로 어긋나 보였다. **내림으로 자른다.**
            "자가소비율": [_self_consumption(value) for value in frame["자가소비율"]],
            # **절감 열은 하나다** (51세션 2절). 회수기간이 용량과 거의 무관해
            # 세 줄이 같은 값을 내는데(16세션) 줄을 가르는 것은 이 합계다 —
            # 두 수를 사람이 더하게 두면 세 배 차이가 한눈에 안 들어온다.
            "절감액": [fmt.won_year(value) for value in frame["절감액(원)"]],
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

    total_cost, pricing_path, fixed_won, per_kwh_won = _ess_cost_inputs()
    _overview(spec)

    curve = cached_ess_targets(
        usage,
        usage_token(usage),
        float(peak),
        base_fee,
        fixed_won,
        per_kwh_won,
        pricing_path,
        rules_stamp(),
    )
    best = curve.best
    if best is None:
        _caution("어떤 목표에서도 초과 구간이 없어 곡선을 그리지 못했습니다.")
        return

    # **목표는 곡선이 고르고 요금 재계산이 다시 고른다** (40세션 1절). 개략
    # 곡선은 목표마다 다른 크기로 어긋나 최소가 놓인 자리 자체를 옮긴다 —
    # 케이스 스터디에서 정격 +60%(C3)·+17%(C5) 짜리 배터리를 권하고 있었다.
    #
    # **한 점당 요금을 다시 계산하므로 21점에 약 8초다.** 진행을 보인다 —
    # 아무 말 없이 멈추면 사용자는 화면이 죽은 줄 안다 (13세션과 같은 이유).
    panel, runner = progress_panel("ESS 목표를 고르는 중…")
    with panel, runner.running("measures", total_steps=len(refine_targets(curve))) as report:
        optimum = cached_ess_optimum(
            usage,
            table,
            baseline,  # type: ignore[arg-type]
            quality,
            curve,
            usage_token(usage),
            form,
            fixed_won,
            per_kwh_won,
            pricing_path,
            rules_stamp(),
            report,
        )

    # **곡선을 표로 바꿨다** (46세션). 곡선은 개략 산정이라 값이 카드와 달라
    # 한 화면에 두 숫자(26.0년·30.8년)가 남아 있었다. 비율을 곱해 올리는 방법을
    # 자료 일곱 121점에서 재 봤더니 1.10~3.18 로 갈려 한 곱수로는 못 옮긴다.
    #
    # **표는 전부 카드 기준 참값이고 추가 계산이 없다** — 위 정밀화가 이미 잰
    # 점을 그대로 쓴다. 26세션이 없앤 「대표 지점 표」와 다르다: 그때는 곡선
    # 아래 표까지 두어 읽을 것이 둘이었고, 지금은 표 하나뿐이다.
    # **최소 규격에 못 미치면 표를 싣지 않는다** (50세션 3-3). 회수기간도 목표별
    # 사양도 낼 것이 없다 — 살 물건이 없기 때문이다. 대신 이 건물의 사실인
    # 필요 출력·용량·방전시간은 낸다. **확인되지 않은 판정을 적지 않는다** —
    # 제품을 못 찾은 것이지 회수되지 않는다고 확인한 것이 아니다.
    if optimum.below_minimum:
        st.session_state[input_key("ess", "target")] = 0.0
        columns = st.columns(3)
        columns[0].metric("필요 출력", fmt.kw(optimum.required_power_kw))
        columns[1].metric("필요 용량", fmt.kwh(optimum.required_capacity_kwh))
        columns[2].metric(
            "방전시간",
            fmt.hours(optimum.required_discharge_hours),
            help=fmt.tip("discharge_hours"),
        )
        _notices(optimum.notices)
        return

    # **잰 점이 없으면 표를 그리지 않는다** (56세션). 갑 종별처럼 훑기 전에
    # 끊는 자리가 생겼다 — 빈 표를 그리면 그 자리에서 터진다. 「왜 안 되는가」 는
    # 표가 아니라 결론 한 줄이 말한다 (`below_minimum` 과 같은 규약).
    if not optimum.points:
        st.session_state[input_key("ess", "target")] = 0.0
        _notices(optimum.notices)
        return

    st.dataframe(
        _ess_spec_view(
            frames.ess_spec_frame(optimum, baseline_demand_kw=curve.baseline_demand_kw)
        ),
        hide_index=True,
        width="stretch",
    )
    st.caption(frames.ESS_SPEC_CAPTION, help=fmt.tip("table.ess_spec"))

    # **고를 수 있는 목표가 없으면 목표를 제시하지 않는다** (48세션). 지금까지는
    # 「최적 230 kW · 154.5년」 처럼 적었는데, 그 사양은 kW당 배터리비가 10년
    # 절감액의 8.7배라 어떤 규모로도 회수되지 않는 자리였다. **표는 참고로
    # 남긴다** — 왜 안 되는지가 표에 있다.
    if not optimum.viable:
        st.session_state[input_key("ess", "target")] = 0.0
        _notices(optimum.notices)
        return

    # 목표는 정밀화가 정한다. 세션에는 남겨 3단계가 같은 값을 읽게 한다.
    target = optimum.target_kw
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
        pricing_path,
    )
    st.subheader(f"{SHORTEST_PAYBACK} 목표 {fmt.kw(target, decimals=0)} 기준")
    columns = st.columns(4)
    # **잘리지 않게 방전시간까지 한 칸에 담는다** (14세션 3-5).
    columns[0].metric(
        "출력 / 용량",
        f"{fmt.kw(result.power_kw, decimals=0)} / {fmt.kwh(result.capacity_kwh)}",
        # **근거·Excel 과 같은 자릿수로 낸다** (45세션). 0.8 과 0.97 이 한 화면에
        # 있으면 다른 값으로 읽힌다.
        f"방전 {fmt.hours(result.discharge_hours)}",
        help=fmt.tip("discharge_hours"),
    )
    # **무엇의 합인지가 아니라 왜 그렇게 나뉘는지를 밝힌다** (31세션 5-3 · 32세션 3절).
    # 31세션에 「기본요금 절감 + 전력량요금 절감」 이라 적었더니 다음 물음이 왔다 —
    # **"충전을 하는 ESS 에서 전력량요금 절감이 가능한가?"** 옮겨 담기(비싼 시간 →
    # 싼 시간)와 왕복효율 손실(총 kWh 증가)이 맞부딪히는 것이 답이다.
    #
    # **32세션의 「거의 상쇄됩니다」 는 틀린 말이었다** (33세션 4절). 손실이 더
    # 크면 전력량요금은 **늘어난다** — 그 자료에서 기본요금 비중이 100.4% 로
    # 나왔고, 문구가 그 경우를 설명하지 못했다. 부호로 문장을 가른다.
    #
    # **금액 둘을 지표로 세우지 않는다.** 바로 아래 「계산 근거」 표가 기본요금
    # 절감·전력량요금 절감을 원 단위로 이미 낸다 (:func:`~kwise.report.worksheet.ess_worksheet`).
    #
    # **12개월 환산분으로 적는다.** 카드가 내는 절감액이 환산값이라, 관측 기간
    # 금액을 나란히 두면 두 수가 더해지지 않는다. 환산비는 계산 쪽이 이미 낸
    # 두 값의 비(``annual_saving_won / total_saving_won``)라 여기서 새로 만들지 않는다.
    annual_factor = (
        result.annual_saving_won / result.total_saving_won if result.total_saving_won else 1.0
    )
    columns[1].metric(
        "절감액",
        fmt.won_year(result.annual_saving_won),
        help=fmt.markdown_safe(
            fmt.ess_saving_line(
                result.base_saving_won,
                result.energy_saving_won,
                result.energy_saving_won * annual_factor,
            )
            + " 경부하에 충전해 최대부하에 방전하니 비싼 시간의 사용량이 싼 시간으로 "
            "옮겨가지만, 왕복효율 손실만큼 총 사용량이 늘어 둘이 맞부딪힙니다.\n\n"
            "**충방전 차익거래는 들어 있지 않습니다** — 도구가 돌리지 않는 운전이라 "
            "확인사항에 잠재값으로 따로 적습니다."
        ),
    )
    columns[2].metric(
        "투자비",
        fmt.won_short(result.investment_won),
        # **어느 경로로 계산했는지 결과에 한 줄로 적는다** (50세션 3-5).
        # 경로가 셋이므로 금액만 내면 어느 단가로 나온 값인지 알 수 없다.
        f"단가 — {result.pricing_path}",
        delta_color="off",
    )
    columns[3].metric(
        "회수기간", fmt.payback(result.payback_years, investment_won=result.investment_won)
    )
    # **언제 담고 언제 쓰는지**를 2단 그림으로 보인다 (15세션 2-5 · 17세션 4-1).
    if day is not None:
        st.altair_chart(charts.ess_day_chart(usage, result.dispatch, day), width="stretch")
        st.caption(
            f"{day.title} · 피크 앞뒤 {charts.PEAK_ZOOM_HOURS}시간",
            help=fmt.chart_tip("chart.ess_day"),
        )
        # **충·방전은 그림이 아니라 글로 낸다** (23세션 6-2). 아래 칸 막대는 위
        # 칸과 종속이라 그림이 둘일 필요가 없었고, 시각을 막대에서 눈으로 읽어
        # 내야 했다. 언제 담아 언제 쓰는지는 글이 더 정확하다.
        frame = charts.ess_day_frame(usage, result.dispatch, day.date)
        slot_hours = usage.meta.interval_minutes / 60.0
        charged = float(frame["충전(kW)"].sum()) * slot_hours if len(frame) else 0.0
        discharged = float(frame["방전(kW)"].sum()) * slot_hours if len(frame) else 0.0
        cut = float(frame["원부하(kW)"].max() - frame["순부하(kW)"].max()) if len(frame) else 0.0
        charge_span, discharge_span = charts.dispatch_schedule(frame)
        st.write(
            f"**{day.title} 운전** — 경부하 {charge_span or fmt.DASH} 충전 "
            f"{fmt.kwh(charged)} · 최대부하 {discharge_span or fmt.DASH} 방전 "
            f"{fmt.kwh(discharged)} · 그날 저감 {fmt.kw(cut)}."
        )
    # **회수기간과 보증 수명을 견주는 문장은 여기 없다** (25세션 3-3 · A).
    # 계산 쪽이 같은 사실을 이미 낸다 — 넘으면 ``ess.payback_over_warranty`` 가
    # 주의로 뜬다. 등급이 달라(근거/주의) 중복 판정을 빠져나가던 자리였고,
    # 한 화면에 같은 수 둘이 나란히 있으면 어느 쪽이 결론인지 흐려진다.
    # 회수기간 자체는 위 지표 카드가 낸다.
    #
    # **고출력 셀 사양 경고는 화면에 남긴다** (10.2 예외).
    if 0 < result.discharge_hours < high_rate_discharge_hours():
        _caution(
            f"방전시간이 {fmt.hours(result.discharge_hours)} 로 짧습니다. 고출력 셀 "
            "사양이 되어 도입 사례보다 단가가 높아질 수 있습니다."
        )
    # **투자비 내역·계수·성립 조건은 근거 툴팁으로 간다** (17세션 4-2 · 19세션 1절).
    # 본문에는 투자비 합계·절감액·회수기간만 남긴다 — 위 지표 넷이 그것이다.
    # 20세션에 화면이 다시 쓰던 넉 줄을 지웠다. 계산 쪽이 같은 사실을 이미
    # 근거로 내고 있어(``ess.quote_breakdown`` · ``ess.cost_model_formula`` ·
    # ``ess.feasibility``) 사실 ID 로 견주니 전부 중복이었다.
    # **두 기준의 차이를 적던 확인사항을 뺐다** (46세션). 곡선이 없어져
    # 설명할 차이가 없다 — 표가 전부 카드 기준 참값이다.
    _notices((*result.notices, *optimum.notices))
    _worksheet(ess_worksheet(result))


def _ess_cost_inputs() -> tuple[float, str, float | None, float | None]:
    """단가 입력 — **카드에는 견적 총액만 남는다** (50세션 3-5 ③).

    49세션까지는 접힘 안에 고정비·용량단가·총액 셋이 있었다. **단가는 성격상
    설정이다** — 접어 두면 관심 있는 사람도 못 보고, 펼쳐 두면 모르는 사람에게
    어렵다. 두 계수와 kWh 구간 단가는 **「기준 데이터」 화면**으로 옮겼고, 여기
    남는 것은 견적을 받은 사람이 그 금액을 그대로 넣는 자리 하나다.

    Returns:
        ``(견적 총액, 단가 경로, 고정비, 용량단가)``. 뒤 셋은 기준 데이터
        화면에서 고른 값을 세션에서 읽은 것이다.
    """
    total = st.number_input(
        "견적 총액 직접 입력 (원) — 0 이면 기준 데이터의 단가로 산정",
        min_value=0.0,
        value=0.0,
        step=1_000_000.0,
        key=input_key("ess", "total_cost"),
        help=manual_tip("ess-cost-reference"),
    )
    path, fixed, per_kwh = ess_pricing()
    return float(total), path, fixed, per_kwh


# --------------------------------------------------------------------- 7.7


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
            help=tooltip_text(notices),
        )


def _worksheet(sheet: Worksheet) -> None:
    """계산 근거를 **접어 둔다** (22세션 2절).

    산식과 대입값을 나란히 둔 표 하나다. 툴팁의 근거 문구가 「무엇을 근거로」
    라면 이것은 「그래서 어떻게 그 숫자가 되었나」다 — 접힘 안이므로 본문
    예산에 들어가지 않는다.

    **표를 여기서 만들지 않는다.** :mod:`kwise.report.worksheet` 가 만든 것을
    그리기만 한다 — 보고서 부록 A 와 Excel 이 같은 표를 쓴다.
    """
    if not sheet:
        return
    with st.expander("계산 근거", expanded=False):
        st.dataframe(sheet.frame(), hide_index=True, width="stretch")


_HANDLERS = {
    "tariff_switch": _tariff_switch,
    "contract": _contract,
    "demand_response": _demand_response,
    "power_factor": _power_factor,
    "solar": _solar,
    "ess": _ess,
}
