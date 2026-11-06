"""2단계 · 개선 수단 — 카드마다 결과를 화면 조각(``blocks``)으로 적는다.

Streamlit 2단계(``kwise.ui.views.measures``)와 같은 공용 함수 · 같은 인자 · 같은 글이다.
카드의 입력칸은 화면(Vue)이 쥐고, 여기는 그 값을 받아 계산하고 결과를 적는다 — 그래서 함수는
동기이고 한 번에 한 카드 결과 한 벌을 돌려준다(다리가 따로 실에서 부른다).
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from typing import Any

import pandas as pd

from kwise import money
from kwise.diagnose import Diagnosis
from kwise.diagnose.dr import JUDGE_WINDOW, dr_event_hours, dr_max_events_per_day
from kwise.io import UsageData, interval_label
from kwise.measures import (
    CURTAIL_SCENARIO,
    EXTERNAL_SCENARIO,
    MARGIN_FACT,
    NO_HEADROOM_LABEL,
    NO_SAVING,
    OFFSET_SCENARIO,
    SHORTEST_PAYBACK,
    EssCostInput,
    EssCostModel,
    EssOptimum,
    EssTargetCurve,
    SolarPoint,
    SurplusResult,
    annualize,
    apply_generation,
    default_target_pct,
    ess_target_curve,
    evaluate_contract_adjustment,
    evaluate_demand_response,
    evaluate_ess,
    evaluate_power_factor,
    evaluate_surplus,
    evaluate_tariff_switch,
    high_rate_discharge_hours,
    load_ess_cost_model,
    offset_settles_cash,
    power_factor_floor_pct,
    refine_ess_target,
    surplus_free_capacity_kwp,
    surplus_options,
    with_surplus_revenue,
)
from kwise.pv import PvPresets, area_from_capacity_m2, capacity_preview, load_pv_presets
from kwise.quality import QualityReport
from kwise.report import CONTRACT_CHANGE_WARNING, frames, narrative, notices
from kwise.report.days import RepresentativeDay, find_day, representative_days
from kwise.report.worksheet import (
    demand_response_worksheet,
    ess_worksheet,
    power_factor_worksheet,
    solar_worksheet,
    tariff_switch_worksheet,
)
from kwise.tariff import TariffTable, lagging_standard_pct
from kwise.ui import charts
from kwise.ui import text as fmt
from kwise.ui.labels import measure_title, option_label, selection_label
from kwise.ui.notices import partition_facts
from kwise.ui.pipeline import (
    AzimuthOption,
    ContractForm,
    SolarInputs,
    azimuth_options,
    solar_result,
    surplus_capacity_points,
    unit_pv_profile,
)
from kwise.ui.spec import DR_PRICED_HEADLINE, MEASURES, NO_HEADROOM_OVERVIEW, MeasureSpec

from . import blocks as b
from .building import BuildingInfo, weather_source_label
from .state import Choices, SolarForm

SAVING_LABEL = "12개월 환산 절감액"
STALE_INPUT = "입력이 변경되었습니다 — 다시 계산하십시오."
#: 수단 머리에만 붙이는 표지 — Streamlit ``_ICONS`` 와 같다.
ICONS = {
    "tariff_switch": "🔀",
    "contract": "📐",
    "demand_response": "📉",
    "power_factor": "🔌",
    "solar": "☀",
    "ess": "🔋",
}
BASELINE = (
    "각 개선안은 독립적으로 평가합니다. 카드의 절감액은 해당 수단만 적용했을 "
    "때의 효과이며, 기준은 현재 요금제와 사용량입니다. 여러 수단을 동시에 "
    "적용한 효과는 3단계 합산효과에서 별도로 산정합니다."
)
_TARIFF_HIDDEN_FACTS = (
    "quality.month_missing_rate",
    "tariff.billing_demand_rule",
    "tariff.school_exception_available",
    "tariff.tentative_base_fee_basis",
    "tariff_switch.best_selection",
)
_FLOOR_VERDICT = "contract.floor_not_binding"
_WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")


@dataclass
class Context:
    """1단계가 넘기는 한 벌 — 이것이 있어야 2 · 3단계가 선다."""

    usage: UsageData
    table: TariffTable
    form: ContractForm
    diagnosis: Diagnosis
    quality: QualityReport
    building: BuildingInfo


def spec_of(key: str) -> MeasureSpec:
    return next(spec for spec in MEASURES if spec.key == key)


def frame(ctx: Context, choices: Choices) -> dict[str, Any]:
    """2단계 머리 — 기준선 문장 · 대표일 고르기 · 카드 목록(묶음 순)."""
    days = representative_days(ctx.usage)
    return {
        "baseline": BASELINE,
        "days": [{"value": d.key, "label": d.title} for d in days]
        + ([{"value": "custom", "label": "사용자 지정일"}] if days else []),
        "day": choices.day_key or (days[0].key if days else ""),
        "range": [str(ctx.usage.meta.start.date()), str(ctx.usage.meta.end.date())],
        "custom": str(choices.custom_day or (days[0].date if days else "")),
        "cards": [
            {
                "key": spec.key,
                "title": f"{ICONS[spec.key]} {measure_title(spec.title)}",
                "tier": spec.tier,
            }
            for spec in MEASURES
        ],
        "enabled": sorted(choices.enabled),
    }


def day_of(ctx: Context, choices: Choices) -> RepresentativeDay | None:
    days = representative_days(ctx.usage)
    if not days:
        return None
    return find_day(ctx.usage, choices.day_key or days[0].key, custom=choices.custom_day)


def card(ctx: Context, choices: Choices, key: str) -> dict[str, Any]:
    """카드 한 장 — ``{"blocks": [...], ...카드 입력칸이 쓸 값}``."""
    with b.collect() as page:
        extra = _CARDS[key](ctx, choices, day_of(ctx, choices)) or {}
    return {"blocks": page, **extra}


# --------------------------------------------------------------------- 1 선택요금 전환


def _tariff_switch(ctx: Context, choices: Choices, day: RepresentativeDay | None) -> None:
    spec = spec_of("tariff_switch")
    b.caption(spec.headline)
    result = evaluate_tariff_switch(
        ctx.usage,
        ctx.table,
        ctx.form.selection,
        quality=ctx.quality,
        options=ctx.form.billing_options(),
        option_totals=dict(ctx.diagnosis.option_totals),
    )
    b.overview(spec.overview)
    b.metrics(
        [
            ("현행", option_label(result.current.selection.option)),
            ("가장 유리한 요금제", option_label(result.best.selection.option)),
            (SAVING_LABEL, fmt.won_short(result.annual_saving_won)),
        ]
    )
    if result.switch_needed:
        b.text(
            f"가장 유리한 요금제는 **{selection_label(ctx.table, result.best.selection)}** 입니다."
        )
    else:
        b.text("현재 요금제가 이미 가장 유리합니다. 바꿀 이유가 없습니다.")
    b.chart(charts.tariff_delta_chart(result))
    b.chart(charts.tariff_option_chart(result))
    b.caption(
        notices.TARIFF_SWITCH_CAPTION,
        fmt.chart_tip("chart.tariff_delta") + "\n\n" + fmt.chart_tip("chart.tariff_option"),
    )
    b.notices(partition_facts(result.notices, _TARIFF_HIDDEN_FACTS)[1])
    b.worksheet(tariff_switch_worksheet(result))


# --------------------------------------------------------------------- 2 계약전력 조정


def _contract(ctx: Context, choices: Choices, day: RepresentativeDay | None) -> None:
    spec = spec_of("contract")
    b.caption(spec.headline)
    structure = ctx.diagnosis.structure
    if ctx.form.contract_kw is None or structure is None:
        b.overview(spec.overview)
        b.caution("계약전력을 입력해야 조정 여지를 봅니다 (1단계 계약 정보).")
        return
    baseline = structure.bill
    result = evaluate_contract_adjustment(
        ctx.usage,
        baseline,
        contract_kw=ctx.form.contract_kw,
        contract_floor_ratio=None,
        table=ctx.table,
        options=ctx.form.billing_options(),
    )
    b.overview(spec.overview)
    ratio = result.contract_floor_ratio
    cells: list[tuple[str, ...]] = [
        ("계약전력", fmt.kw(result.contract_kw)),
        (
            f"계약전력의 {fmt.ratio_pct(ratio, decimals=0)}" if ratio is not None else "하한",
            fmt.kw(result.floor_kw) if result.floor_kw is not None else "—",
        ),
        ("최대수요", notices.max_demand_text(result.demand_before_floor_kw)),
    ]
    if result.target_contract_kw is not None:
        cells.append(("목표 계약전력", fmt.kw(result.target_contract_kw, decimals=0)))
    cells.append(
        (
            SAVING_LABEL,
            NO_SAVING
            if result.no_saving and result.annual_saving_won == 0
            else fmt.won_short(result.annual_saving_won, reason=result.saving_basis),
        )
    )
    b.metrics(cells)
    verdict = next((item for item in result.notices if item.fact == _FLOOR_VERDICT), None)
    if verdict is not None:
        b.text(verdict.text)
    if result.reducible:
        b.caption("현재 부하 기준입니다. 다른 수단을 함께 켜면 3단계에서 다시 계산됩니다.")
        b.caution(CONTRACT_CHANGE_WARNING)
        period_note = notices.contract_period_note(baseline)
        if period_note:
            b.caution(period_note)
    b.notices(
        tuple(item for item in result.notices if item.fact not in (MARGIN_FACT, _FLOOR_VERDICT))
    )


# --------------------------------------------------------------------- 3 경제성DR


def _demand_response(
    ctx: Context, choices: Choices, day: RepresentativeDay | None
) -> dict[str, Any]:
    spec = spec_of("demand_response")
    # 캡션은 그 벌의 단가로 참인 말만 한다 — 단가를 넣었으면 뒤 문장만 (Streamlit 과 같다).
    b.caption(DR_PRICED_HEADLINE if choices.dr_unit_price else spec.headline)
    dr = ctx.diagnosis.dr
    if dr is None:
        b.overview(spec.overview)
        b.caution("경제성DR 참여 여력을 산출하지 못했습니다.")
        return {"off_day_options": []}
    result = evaluate_demand_response(
        dr, unit_price_won_per_kwh=choices.dr_unit_price, jeju=ctx.building.jeju
    )
    b.overview(spec.overview)
    counted = not result.unassessed_reason
    b.metrics(
        [
            (
                "거래 가능일",
                fmt.days(result.eligible_days),
                "",
                "분석 기간의 평일 수입니다. 토·일·공휴일은 입찰할 수 없어 뺐습니다.\n\n"
                "이 가운데 감축할 여력이 있는 날만 아래 「저부하 평일」 로 셉니다.",
            ),
            (
                "저부하 평일",
                fmt.days(result.low_load_days),
                "",
                "거래 가능일 중 부하가 쉬는 날 수준까지 내려온 날 수입니다.\n\n"
                "**연간 참여 일수 제한이 없으므로 이 날 수가 실질 제약입니다** — "
                "감축할 여력이 있는 날이 몇 날이냐가 전부입니다.",
            ),
            (
                "등록 권장 용량",
                fmt.kw(result.registered_capacity_kw, decimals=0) if counted else notices.UNPRICED,
                "",
                "사업자 등록 시 제시할 보수적인 감축 가능 용량입니다.\n\n"
                "저부하일 여력 분포의 하위값이라 어느 참여일에나 지킬 수 있습니다 — "
                "평균으로 등록하면 절반의 날에 미달하고, 미달은 입찰 제한으로 이어집니다.",
            ),
            (
                "12개월 환산 감축 가능량",
                fmt.kwh(result.annual_reducible_kwh) if counted else notices.UNPRICED,
                "",
                "실제 참여 가능 시간과 일별 감축 여력을 반영한 12개월 환산 감축 잠재량입니다.\n\n"
                "저부하일마다 (그날 여력 × 그날 참여 가능 시간)을 더해 365일로 "
                "환산했습니다 — 등록 용량 × 시간이 아닙니다.",
            ),
        ]
    )
    hours_sum = fmt.hours(result.participation_hours, decimals=0) if counted else notices.UNPRICED
    b.caption(
        f"{JUDGE_WINDOW} {dr.window_label} · 하루 한도(가정) "
        f"{dr_max_events_per_day()}회 × 최대 {fmt.hours(dr_event_hours()[1], decimals=0)} "
        f"(하루 {fmt.hours(result.daily_hours_cap, decimals=0)}) · 참여 가능 시간 합 {hours_sum}"
    )
    if result.weekend_baseline_kw is not None and result.low_load_threshold_kw is not None:
        b.caption(
            f"저부하 판정 기준선은 주말·공휴일 {JUDGE_WINDOW} 평균 "
            f"{fmt.kw(result.weekend_baseline_kw)} 이고, 그 "
            f"{dr.low_load_multiple:.2g}배인 "
            f"{fmt.kw(result.low_load_threshold_kw)} 이하인 평일을 셌습니다."
        )
    b.chart(charts.dr_daily_chart(dr))
    b.caption(
        f"일별 {JUDGE_WINDOW} 평균 부하",
        fmt.chart_tip("chart.dr_daily", reading=result.low_load_threshold_kw is not None),
    )
    # 뺀 날이 있으면 목록이 비어도 고르는 칸을 남긴다 — 안 남기면 되돌릴 칸이 사라진다.
    dates: list[str] = []
    if result.low_load_days or choices.dr_off_days:
        with b.expansion(f"저부하 평일 {result.low_load_days}일"):
            if result.low_load_days:
                b.table(result.low_load_day_table)
        listed = {str(value) for value in result.low_load_day_table["날짜"]}
        dates = sorted(listed | set(choices.dr_off_days))
    if not result.low_load_days and counted:
        b.text("저부하 평일이 없어 감축 가능량을 0 으로 두었습니다.")
    if result.is_priced:
        b.metrics(
            [
                (
                    "12개월 환산 정산금",
                    NO_SAVING if result.no_reduction else fmt.won_short(result.settlement_won),
                )
            ]
        )
    b.caption(
        "자원 유형 — " + (", ".join(str(item) for item in result.resource_types) or "판정 불가")
    )
    b.notices(result.notices)
    b.worksheet(demand_response_worksheet(result))
    return {
        "off_day_options": [
            {"value": d, "label": f"{d} ({_WEEKDAYS[dt.date.fromisoformat(d).weekday()]})"}
            for d in dates
        ]
    }


# --------------------------------------------------------------------- 4 역률 개선


def _power_factor(ctx: Context, choices: Choices, day: RepresentativeDay | None) -> dict[str, Any]:
    spec = spec_of("power_factor")
    b.caption(spec.headline)
    structure = ctx.diagnosis.structure
    baseline = structure.bill if structure is not None else None
    target = choices.pf_target if choices.pf_target is not None else default_target_pct()
    result = evaluate_power_factor(
        ctx.usage,
        ctx.table,
        ctx.form.selection,
        current_pct=ctx.form.lagging_pct,
        target_pct=target,
        investment_won=choices.pf_investment,
        baseline=baseline,
        quality=ctx.quality,
        options=ctx.form.billing_options(),
        billed=ctx.form.power_factor_pct is not None,
    )
    b.overview(NO_HEADROOM_OVERVIEW if result.no_headroom else spec.overview)
    b.metrics(
        [
            ("현재 역률", fmt.pct(result.current_pct)),
            ("목표 역률", NO_HEADROOM_LABEL if result.no_headroom else fmt.pct(result.target_pct)),
            (
                SAVING_LABEL,
                NO_SAVING if result.no_headroom else fmt.won_short(result.annual_saving_won),
            ),
            (
                "회수기간",
                NO_SAVING
                if result.no_headroom
                else fmt.payback(result.payback_years, investment_won=result.investment_won),
            ),
        ]
    )
    with b.columns(2) as cols:
        with cols.column(0):
            b.chart(charts.power_triangle_chart(result))
            b.caption(
                "전력삼각형" if result.no_headroom else "전력삼각형 — 개선 전후",
                fmt.chart_tip("chart.power_triangle"),
            )
        if day is not None:
            with cols.column(1):
                b.chart(
                    charts.power_factor_day_chart(
                        ctx.usage, day, current_pct=result.current_pct, target_pct=result.target_pct
                    )
                )
                interval = interval_label(ctx.usage.meta.interval_minutes)
                b.caption(
                    f"{day.title} · {interval} 부하와 판정 창",
                    fmt.chart_tip("chart.power_factor_day").replace("15분", interval),
                )
    standard = lagging_standard_pct()
    if result.current_pct < standard:
        b.caution(
            f"현재 지상역률이 기준 {standard:,.0f}% 에 미달합니다 "
            f"({fmt.pct(result.current_pct)}). 매 1%p 마다 기본요금이 추가됩니다."
        )
    b.notices(result.notices)
    b.worksheet(power_factor_worksheet(result))
    return {"floor": power_factor_floor_pct(), "target": target}


# --------------------------------------------------------------------- 6 ESS


def ess_model(choices: Choices) -> EssCostModel:
    """고른 단가 경로와 두 계수로 조달 사례 모델 — Streamlit ``ess_cost_model`` 과 같은 셈."""
    model = load_ess_cost_model()
    if choices.ess_path and choices.ess_path != model.pricing_path:
        model = model.with_pricing_path(choices.ess_path)
    if choices.ess_fixed is None and choices.ess_per_kwh is None:
        return model
    return model.with_coefficients(
        fixed_won=model.fixed_won if choices.ess_fixed is None else choices.ess_fixed,
        per_kwh_won=model.per_kwh_won if choices.ess_per_kwh is None else choices.ess_per_kwh,
    )


#: ESS 목표 훑기 결과 — 자료 · 계약 · 단가 모델로 열쇠를 건다(견적만 바꾸면 다시 안 돈다).
_ESS_MEMO: dict[tuple[object, ...], tuple[EssTargetCurve, EssOptimum | None]] = {}
#: 방위별 상대 발전량 — (지역, 경사, GCR) 마다 한 번.
_AZIMUTH_MEMO: dict[tuple[str, float, float], tuple[AzimuthOption, ...]] = {}


def forget() -> None:
    """기준 데이터를 고친 뒤 — 화면 쪽 기억을 비운다 (Streamlit ``clear_calc_cache`` 자리)."""
    _ESS_MEMO.clear()
    _AZIMUTH_MEMO.clear()


def ess_optimum(ctx: Context, choices: Choices) -> tuple[EssTargetCurve | None, EssOptimum | None]:
    """목표 곡선과 정밀화 — 2단계 카드와 3단계가 같은 기억을 본다."""
    peak = ctx.diagnosis.peak.billing_demand_kw
    if peak <= 0:
        return None, None
    model = ess_model(choices)
    key = (
        id(ctx.usage),
        ctx.usage.meta.total_kwh,
        repr(ctx.form),
        model.pricing_path,
        model.fixed_won,
        model.per_kwh_won,
    )
    if key not in _ESS_MEMO:
        structure = ctx.diagnosis.structure
        curve = ess_target_curve(
            ctx.usage.kw,
            ctx.usage.meta.interval_minutes,
            baseline_demand_kw=float(peak),
            base_fee_won_per_kw=float(ctx.table.rates(ctx.form.selection).base_won_per_kw),
            model=model,
        )
        optimum = None
        if curve.best is not None:
            optimum = refine_ess_target(
                ctx.usage,
                ctx.table,
                ctx.form.selection,
                curve=curve,
                baseline=structure.bill if structure is not None else None,
                quality=ctx.quality,
                options=ctx.form.billing_options(),
                model=model,
            )
        if len(_ESS_MEMO) >= 8:  # ponytail: 작은 기억 — 넘치면 통째로 비운다
            _ESS_MEMO.clear()
        _ESS_MEMO[key] = (curve, optimum)
    return _ESS_MEMO[key]


def _ess_spec_view(frame_: pd.DataFrame) -> pd.DataFrame:
    if frame_.empty:
        return frame_
    savings = frame_["연간 절감액(원)"]
    in_man = bool((savings.abs() >= 10_000).any())

    def saving(value: float) -> str:
        if in_man and 0 < abs(value) < 10_000:
            return f"{money.truncate_won(value) / 10_000:,.1f}만원"
        return fmt.won_short(value)

    return pd.DataFrame(
        {
            "목표": [f"{value} kW" for value in frame_["목표 요금적용전력(kW)"]],
            "저감량": [fmt.kw(value, decimals=0) for value in frame_["저감량(kW)"]],
            "출력": [fmt.kw(value, decimals=0) for value in frame_["출력(kW)"]],
            "용량": [fmt.kwh(value) for value in frame_["용량(kWh)"]],
            "방전시간": [fmt.hours(value) for value in frame_["방전시간(h)"]],
            "투자비": [fmt.won_short(value, reason="—") for value in frame_["투자비(원)"]],
            "12개월 환산 절감액": [saving(value) for value in savings],
            "회수기간": [
                fmt.payback(years, investment_won=investment)
                for years, investment in zip(
                    frame_["회수기간(년)"], frame_["투자비(원)"], strict=True
                )
            ],
            "표식": frame_["표식"],
        }
    )


def _ess(ctx: Context, choices: Choices, day: RepresentativeDay | None) -> None:
    spec = spec_of("ess")
    b.caption(spec.headline)
    b.overview(spec.overview)
    curve, optimum = ess_optimum(ctx, choices)
    if curve is None:
        choices.ess_target = 0.0
        b.caution("요금적용전력을 산출하지 못해 ESS 목표를 훑을 수 없습니다.")
        return
    if curve.best is None or optimum is None:
        choices.ess_target = 0.0
        b.caution(notices.ESS_NO_EXCESS)
        return
    if optimum.below_minimum:
        choices.ess_target = 0.0
        b.metrics(
            [
                ("필요 출력", fmt.kw(optimum.required_power_kw)),
                ("필요 용량", notices.ess_capacity_text(optimum.required_capacity_kwh)),
                (
                    "방전시간",
                    fmt.hours(optimum.required_discharge_hours),
                    "",
                    fmt.tip("discharge_hours"),
                ),
            ]
        )
        b.notices(optimum.notices)
        return
    if not optimum.points:
        choices.ess_target = 0.0
        b.notices(optimum.notices)
        return
    table = frames.ess_spec_frame(optimum, baseline_demand_kw=curve.baseline_demand_kw)
    b.table(_ess_spec_view(table))
    b.caption(frames.ess_spec_caption(table), fmt.tip("table.ess_spec"))
    if not optimum.viable:
        choices.ess_target = 0.0
        b.notices(optimum.notices)
        return
    target = optimum.target_kw
    choices.ess_target = target
    structure = ctx.diagnosis.structure
    quote = choices.ess_quote
    result = evaluate_ess(
        ctx.usage,
        ctx.table,
        ctx.form.selection,
        target_kw=target,
        cost=EssCostInput.of_total(quote) if quote is not None else EssCostInput.unpriced(),
        model=ess_model(choices),
        baseline=structure.bill if structure is not None else None,
        quality=ctx.quality,
        options=ctx.form.billing_options(),
    )
    b.h3(f"{SHORTEST_PAYBACK} 목표 {fmt.kw(target, decimals=0)} 기준")
    annual_factor = (
        result.annual_saving_won / result.total_saving_won if result.total_saving_won else 1.0
    )
    b.metrics(
        [
            (
                "출력 / 용량",
                # 수와 단위 사이는 줄바꿈 없는 빈칸 — 꺾여도 「/」 에서만 꺾인다.
                f"{fmt.kw(result.power_kw, decimals=0)} / {fmt.kwh(result.capacity_kwh)}".replace(
                    " k", " k"
                ),
                f"방전 {fmt.hours(result.discharge_hours)}",
                fmt.tip("discharge_hours"),
            ),
            (
                SAVING_LABEL,
                fmt.won_short(result.annual_saving_won),
                "",
                fmt.ess_saving_line(
                    result.base_saving_won,
                    result.energy_saving_won,
                    result.energy_saving_won * annual_factor,
                )
                + " 경부하에 충전해 최대부하에 방전하니 비싼 시간의 사용량이 싼 시간으로 "
                "옮겨가지만, 왕복효율 손실만큼 총 사용량이 늘어 둘이 맞부딪힙니다.",
            ),
            ("투자비", fmt.won_short(result.investment_won), f"단가 — {result.pricing_path}"),
            ("회수기간", fmt.payback(result.payback_years, investment_won=result.investment_won)),
        ]
    )
    if day is not None:
        b.chart(charts.ess_day_chart(ctx.usage, result.dispatch, day))
        b.caption(
            f"{day.title} · 피크 앞뒤 {charts.PEAK_ZOOM_HOURS}시간", fmt.chart_tip("chart.ess_day")
        )
        frame_ = charts.ess_day_frame(ctx.usage, result.dispatch, day.date)
        slot_hours = ctx.usage.meta.interval_minutes / 60.0
        charged = float(frame_["충전(kW)"].sum()) * slot_hours if len(frame_) else 0.0
        discharged = float(frame_["방전(kW)"].sum()) * slot_hours if len(frame_) else 0.0
        cut = float(frame_["원부하(kW)"].max() - frame_["순부하(kW)"].max()) if len(frame_) else 0.0
        charge_span, discharge_span = charts.dispatch_schedule(frame_)
        b.text(
            f"**{day.title} 운전** — 경부하 {charge_span or fmt.DASH} 충전 "
            f"{fmt.kwh(charged)} · 최대부하 {discharge_span or fmt.DASH} 방전 "
            f"{fmt.kwh(discharged)} · 그날 저감 {fmt.kw(cut)}."
        )
    if 0 < result.discharge_hours < high_rate_discharge_hours():
        b.caution(
            f"방전시간이 {fmt.hours(result.discharge_hours)} 로 짧습니다. 고출력 셀 "
            "사양이 되어 도입 사례보다 단가가 높아질 수 있습니다."
        )
    b.notices(notices.without_arbitrage((*result.notices, *optimum.notices)))
    b.worksheet(ess_worksheet(result))


# --------------------------------------------------------------------- 5 태양광


def solar_options(ctx: Context, choices: Choices) -> dict[str, Any]:
    """태양광 입력칸의 선택지 — 밀도(환산 용량 라벨) · 8방위(계산 뒤 상대 발전량)."""
    presets = load_pv_presets()
    form = choices.solar
    by_key = {p.key: c for p, c in capacity_preview(float(form.area or 0), presets)}
    density = presets.density(form.density or presets.default.key)
    return {
        "density_label": presets.density_label,
        "densities": [
            {"value": d.key, "label": f"{d.label} — {fmt.kwp(by_key.get(d.key, 0.0))}"}
            for d in presets.densities
        ],
        "density_tip": "\n\n".join(f"**{d.label}** — {d.tradeoff}" for d in presets.densities),
        "default_density": presets.default.key,
        "azimuths": _azimuth_labels(ctx, choices, presets, density.tilt_deg, density.gcr),
        "wall_azimuths": _azimuth_labels(ctx, choices, presets, 90.0, 1.0),
        "default_azimuth": presets.default_azimuth,
        "tilt": density.tilt_deg,
        "region": ctx.building.region_key,
        "stale": form.stale,
        "ran": form.ran_form is not None,
        "northward": {a.key: a.caution for a in presets.azimuths if a.is_northward},
    }


def _azimuth_labels(
    ctx: Context, choices: Choices, presets: PvPresets, tilt: float, gcr: float
) -> list[dict[str, str]]:
    """계산해 둔 지역 · 경사와 같을 때만 상대 발전량을 라벨에 얹는다 (Streamlit 과 같다)."""
    labels = {a.key: a.label for a in presets.azimuths}
    ran = choices.solar.ran_form
    region = ctx.building.region_key
    if ran is not None and region:
        ran_density = presets.density(str(ran.get("density") or presets.default.key))
        if abs(ran_density.tilt_deg - tilt) < 1e-9:
            computed = _AZIMUTH_MEMO.get((region, float(tilt), float(gcr)))
            if computed:
                labels = {a.key: a.label for a in computed}
    return [{"value": key, "label": label} for key, label in labels.items()]


def surplus(
    usage: UsageData,
    table: TariffTable,
    form: ContractForm,
    unit_profile: pd.Series,
    capacity_kwp: float,
    solar: SolarForm,
) -> SurplusResult:
    """잉여 한 벌 — Streamlit ``cached_surplus`` 의 본문과 같은 부름."""
    external, smp = solar.prices()
    net = apply_generation(usage, unit_profile * capacity_kwp)
    return evaluate_surplus(
        usage,
        table,
        form.selection,
        net.surplus_kw,
        generation_kwh=net.generated_kwh,
        net_usage=net.usage,
        capacity_kwp=capacity_kwp,
        external_price_won_per_kwh=external,
        smp_price_won_per_kwh=smp,
        options=form.billing_options(),
    )


def chosen_revenue(form: SolarForm, result: SurplusResult) -> tuple[str, float | None]:
    """(고른 처리 이름, 관측 기간 수익) — Streamlit ``chosen_surplus_revenue`` 와 같다."""
    scenario = form.surplus_use or CURTAIL_SCENARIO
    try:
        return scenario, result.scenario(scenario).revenue_won
    except KeyError:
        return "", None


def _solar(ctx: Context, choices: Choices, day: RepresentativeDay | None) -> dict[str, Any]:
    spec = spec_of("solar")
    b.caption(spec.headline)
    form = choices.solar
    presets = load_pv_presets()
    region = ctx.building.region_key
    if not region:
        b.caution("옆단에서 지역(시도·시군구)을 고르십시오. 기상 격자를 정하는 값입니다.")
        return {"solar": None}
    if form.ran_form is None:
        b.note("면적·설치 밀도·지역·단가를 넣고 「태양광 계산」 을 누르십시오.")
        return {"solar": solar_options(ctx, choices)}
    inputs = form.inputs(region, presets, ran=True)
    if inputs.resolved_capacity_kwp(presets) <= 0:
        b.caution("면적 또는 용량을 넣어야 계산합니다.")
        return {"solar": solar_options(ctx, choices)}
    if form.stale:
        b.caution(f"{STALE_INPUT} 아래는 이전 결과입니다.")
    with b.dim(form.stale):
        _solar_result(ctx, choices, day, spec, presets, inputs)
    return {"solar": solar_options(ctx, choices)}


def _solar_result(
    ctx: Context,
    choices: Choices,
    day: RepresentativeDay | None,
    spec: MeasureSpec,
    presets: PvPresets,
    inputs: SolarInputs,
) -> None:
    usage = ctx.usage
    form = choices.solar
    structure = ctx.diagnosis.structure
    baseline = structure.bill if structure is not None else None
    try:
        unit_profile, source = unit_pv_profile(usage, inputs)
    except Exception as exc:
        b.blocked(f"기상 자료를 얻지 못해 계산하지 않았습니다. {exc}")
        return
    curve = solar_result(
        usage, ctx.table, ctx.form, inputs, unit_profile, baseline=baseline, quality=ctx.quality
    )
    density = presets.density(inputs.density_key or presets.default.key)
    key = (inputs.region_key, float(density.tilt_deg), float(density.gcr))
    if key not in _AZIMUTH_MEMO:
        try:
            _AZIMUTH_MEMO[key] = azimuth_options(
                usage,
                SolarInputs(region_key=inputs.region_key, system_loss_ratio=0.14),
                tilt_deg=float(density.tilt_deg),
                gcr=float(density.gcr),
            )
        except Exception:  # 방위 비율은 라벨일 뿐 — 못 내면 이름만 둔다
            _AZIMUTH_MEMO[key] = ()
    point = curve.points[-1]
    months = curve.base_fee_months
    result = surplus(usage, ctx.table, ctx.form, unit_profile, point.capacity_kwp, form)
    points = surplus_capacity_points(
        usage,
        ctx.table,
        ctx.form,
        unit_profile,
        cost=inputs.cost(),
        baseline=baseline,
        quality=ctx.quality,
    )
    b.caption(f"기상 출처 — {weather_source_label(source)}")
    b.overview(spec.overview)
    scenario, revenue = chosen_revenue(form, result)
    point = with_surplus_revenue(
        point, revenue_won=revenue, scenario=scenario, base_fee_months=months
    )
    b.metrics(
        [
            ("용량", fmt.kwp(point.capacity_kwp)),
            ("발전량", fmt.per_year(fmt.mwh(annualize(point.generation_kwh, months)))),
            (SAVING_LABEL, fmt.won_short(point.annual_saving_won), "", _saving_tip(point, months)),
            ("회수기간", fmt.payback(point.payback_years, investment_won=point.investment_won)),
        ]
    )
    b.caption("투자비 — " + fmt.won(point.investment_won, reason=curve.cost.reason))
    verdict = curve.verdict()
    b.text(verdict.headline(inputs.area_m2 or None))
    reference = verdict.reference_note(surplus_free_capacity_kwp(usage, unit_profile))
    if reference:
        b.caption(reference)
    _surplus_verdict(usage, result, unit_profile, months, presets, inputs)
    _surplus_handling(usage, unit_profile, point, months, result, form)
    b.h3("용량별 비교")
    b.table(
        _capacity_view(
            charts.solar_capacity_table(
                curve,
                verdict=verdict,
                surplus_points=points,
                gcr=density.gcr,
                area_per_kwp_m2=presets.area_per_kwp_m2,
                area_limit_m2=inputs.area_m2 or None,
            )
        )
    )
    tie_note = verdict.tie_note
    b.caption(
        "잉여가 생기기 시작하는 용량과 많이 생기는 용량을 함께 세웠습니다 — "
        "설치 가능 면적을 넘는 줄은 「면적 초과」 로 적습니다. "
        + (tie_note + " " if tie_note else "")
        + "20단계 상세는 Excel 「태양광 용량 곡선」 시트에 있습니다."
    )
    generation = unit_profile * point.capacity_kwp
    b.chart(charts.solar_annual_chart(usage, generation))
    ratio = charts.solar_saving_ratio(usage, generation)
    b.caption(
        "날짜별 발전량" + (f" · 기간 사용량의 {fmt.ratio_pct(ratio)} 를 줄입니다" if ratio else ""),
        fmt.chart_tip(
            "chart.solar_annual",
            reading=structure is None or notices.covers_summer_and_winter(structure.bill),
        ),
    )
    if day is not None:
        b.chart(charts.solar_day_chart(usage, generation, day, zoom=True))
        b.caption(
            f"{day.title} · 피크 앞뒤 {charts.PEAK_ZOOM_HOURS}시간",
            fmt.chart_tip("chart.solar_day"),
        )
    b.notices(curve.notices)
    b.worksheet(solar_worksheet(curve, point, power_factor_pct=ctx.form.lagging_pct))


def _saving_tip(point: SolarPoint, months: float) -> str:
    if not point.surplus_scenario:
        return ""
    head = (
        narrative.solar_saving_breakdown(
            self_consumption_won=annualize(point.self_consumption_saving_won, months),
            surplus_scenario=point.surplus_scenario,
            surplus_revenue_won=annualize(point.surplus_revenue_won, months),
        ).replace("절감액 = ", "")
        + "."
    )
    if not point.surplus_revenue_won:
        return head
    return (
        head + "\n\n역송분은 요금 계산에서 빠져 있고, 상계 차감은 그 뒤에 남은 "
        "순부하 사용량을 한도로 잽니다 — 겹쳐 세지 않았습니다."
    )


def _share(part: float, total: float) -> str:
    return fmt.ratio_pct(part / total) if total > 0 else fmt.DASH


def _surplus_verdict(
    usage: UsageData,
    result: SurplusResult,
    unit_profile: pd.Series,
    months: float,
    presets: PvPresets,
    inputs: SolarInputs,
) -> None:
    density = presets.density(inputs.density_key or presets.default.key)
    free_kwp = surplus_free_capacity_kwp(usage, unit_profile)
    free_area = area_from_capacity_m2(
        free_kwp, gcr=density.gcr, area_per_kwp_m2=presets.area_per_kwp_m2
    )
    holiday_kwh = result.weekend_kwh + result.holiday_kwh
    total_text, weekday_text, holiday_text = notices.surplus_split_kwh(
        annualize(result.total_kwh, months),
        annualize(result.weekday_kwh, months),
        annualize(holiday_kwh, months),
    )
    b.metrics(
        [
            (
                "12개월 환산 잉여",
                total_text,
                f"발전량의 {fmt.ratio_pct(result.share_of_generation)}",
            ),
            ("평일 잉여", fmt.per_year(weekday_text), _share(result.weekday_kwh, result.total_kwh)),
            (
                "토·일·공휴일 잉여",
                fmt.per_year(holiday_text),
                _share(holiday_kwh, result.total_kwh),
            ),
            (
                "잉여 없는 최대 용량",
                fmt.kwp(free_kwp),
                f"면적 {fmt.count(free_area, ' m²', decimals=0)}",
                fmt.tip("surplus_free").replace(
                    "15분", interval_label(usage.meta.interval_minutes)
                ),
            ),
        ]
    )


def _surplus_handling(
    usage: UsageData,
    unit_profile: pd.Series,
    point: SolarPoint,
    months: float,
    result: SurplusResult,
    form: SolarForm,
) -> None:
    """남는 것으로 무엇을 하나 — 고르는 칸은 화면(Vue)이 그리고, 여기는 그 결과를 적는다."""
    if result.total_kwh <= 0:
        return
    options = surplus_options(point.capacity_kwp)
    choice = form.surplus_use if form.surplus_use in options else CURTAIL_SCENARIO
    _add_surplus_picker(options, choice, result, point, form)
    scenario = result.scenario(choice)
    revenue = scenario.revenue_won
    b.metrics(
        [
            (
                "12개월 환산 수익",
                fmt.won_short(annualize(revenue, months)) if revenue is not None else fmt.DASH,
            )
        ]
    )
    b.caption(scenario.basis if revenue is not None else fmt.won(revenue, reason=scenario.basis))
    if note := result.chosen_price_note(choice):
        b.caption(note)
    surplus_kw = apply_generation(usage, unit_profile * point.capacity_kwp).surplus_kw
    b.chart(charts.surplus_daily_chart(usage, surplus_kw))
    b.caption("날짜별 잉여", fmt.chart_tip("chart.surplus_daily"))
    settlement = result.offset
    if settlement is not None and settlement.carried:
        b.table(
            pd.DataFrame(
                {
                    "월": [str(item.month) for item in settlement.carried],
                    "차감": [fmt.kwh(item.deducted_kwh) for item in settlement.carried],
                    "다음 달로 이월": [
                        fmt.kwh(item.carried_out_kwh) for item in settlement.carried
                    ],
                }
            )
        )
        b.caption("그 달 그 계시 사용량을 넘어 차감하지 못한 몫입니다.")
    b.notices(tuple(item for item in result.notices if item.fact != "surplus.applied_price"))


def _add_surplus_picker(
    options: tuple[str, ...],
    choice: str,
    result: SurplusResult,
    point: SolarPoint,
    form: SolarForm,
) -> None:
    """잉여 처리 고르기 — 화면이 라디오 · 단가 칸으로 그린다(``surplus_picker`` 조각)."""
    external, smp = form.prices()
    price_field = ""
    if choice == EXTERNAL_SCENARIO:
        price_field = "surplus_price"
    elif (
        choice == OFFSET_SCENARIO
        and offset_settles_cash(point.capacity_kwp)
        and result.offset is not None
        and result.offset.remaining_kwh > 0
    ):
        price_field = "smp_price"
    b.raw(
        {
            "type": "surplus_picker",
            "options": list(options),
            "value": choice,
            "price_field": price_field,
            "price": (external if price_field == "surplus_price" else smp) or 0,
            "price_label": (
                "잉여 판매 단가 (원/kWh) — 0 이면 미산출"
                if price_field == "surplus_price"
                else "SMP 단가 (원/kWh) — 0 이면 미산출"
            ),
            "price_tip": (
                "우리가 파는 쪽의 단가입니다. 비우면 금액을 산출하지 않습니다."
                if price_field == "surplus_price"
                else "당월 차감하고 남은 몫을 정산하는 단가입니다. 비우면 잔여량만 냅니다."
            ),
        }
    )


def _self_consumption(ratio: float | None) -> str:
    if ratio is None or pd.isna(ratio):
        return fmt.DASH
    value = float(ratio)
    return fmt.ratio_pct(value if value >= 1.0 else math.floor(value * 1_000.0) / 1_000.0)


def _capacity_view(frame_: pd.DataFrame) -> pd.DataFrame:
    if frame_.empty:
        return frame_
    return pd.DataFrame(
        {
            "용량(kWp)": [fmt.kwp(value) for value in frame_["용량(kWp)"]],
            "필요 면적": [
                fmt.count(value, " m²", decimals=0) if pd.notna(value) else fmt.DASH
                for value in frame_["필요 면적(m²)"]
            ],
            "발전량": [fmt.per_year(fmt.mwh(value)) for value in frame_["연간 발전량(kWh)"]],
            "자가소비율": [_self_consumption(value) for value in frame_["자가소비율"]],
            "12개월 환산 자가소비 절감액": [
                fmt.won_short(value) for value in frame_["자가소비 절감액(원)"]
            ],
            "투자비": [
                fmt.won_short(value, reason="미산출 — 단가 미입력")
                for value in frame_["투자비(원)"]
            ],
            "회수기간": [
                fmt.payback(years, investment_won=investment)
                for years, investment in zip(
                    frame_["회수기간(년)"], frame_["투자비(원)"], strict=True
                )
            ],
            "표식": frame_["표식"],
        }
    )


_CARDS = {
    "tariff_switch": _tariff_switch,
    "contract": _contract,
    "demand_response": _demand_response,
    "power_factor": _power_factor,
    "solar": _solar,
    "ess": _ess,
}
