"""계산 캐싱 (요구사항서 10.2).

계산 결과는 ``st.cache_data`` 로 캐싱한다. 무거운 값(부하 시계열·요금표)은
**밑줄로 시작하는 인자**로 넘겨 해시에서 뺀다. 대신 그 값을 대표하는 **토큰**을
함께 넘겨 캐시 키로 삼는다 — Streamlit 규약이다.

**기준 데이터를 고치면 캐시를 비운다.** 지난 세션이 심어 둔
:func:`kwise.rules.reload_rules` 는 rules 쪽 lru_cache 만 비우므로, 그것만으로는
**파일은 바뀌었는데 화면은 옛 값**이 된다. 그래서 두 겹으로 막는다.

    ① :func:`apply_rule_edit` 가 편집 직후 ``st.cache_data.clear()`` 를 부른다
    ② 모든 캐시 키에 :func:`rules_stamp` 를 물린다 — ①을 빠뜨려도 값이 바뀌면
       키가 달라져 다시 계산한다

①만 두면 다른 경로(엑셀 왕복·손편집)로 파일이 바뀐 것을 놓친다.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

import pandas as pd
import streamlit as st

from kwise.compare import (
    ComparisonResult,
    SensitivityRange,
    compare_combinations,
    sensitivity_ranges,
)
from kwise.compare.combination import CombinationSpec
from kwise.compare.sensitivity import sensitivity_comparison
from kwise.diagnose import Diagnosis
from kwise.io import UsageData, load_usage_bytes
from kwise.measures import (
    ContractAdjustment,
    EssCostInput,
    EssResult,
    PowerFactorResult,
    SolarCurve,
    SurplusResult,
    TariffSwitchResult,
    apply_generation,
    evaluate_contract_adjustment,
    evaluate_ess,
    evaluate_power_factor,
    evaluate_surplus,
    evaluate_tariff_switch,
)
from kwise.quality import QualityReport
from kwise.rules import EditResult, RuleOrigin, assumptions, reload_rules, rules
from kwise.tariff import BillingOptions, BillingResult, TariffTable, load_tariff
from kwise.ui import pipeline
from kwise.ui.pipeline import ContractForm, SolarInputs

__all__ = [
    "apply_rule_edit",
    "cached_baseline_bill",
    "cached_comparison",
    "cached_contract_adjustment",
    "cached_diagnosis",
    "cached_ess",
    "cached_power_factor",
    "cached_quality",
    "cached_sensitivity",
    "cached_solar",
    "cached_surplus",
    "cached_tariff",
    "cached_tariff_switch",
    "cached_unit_pv",
    "cached_usage",
    "clear_calc_cache",
    "form_token",
    "rules_stamp",
    "usage_token",
]


# --------------------------------------------------------------------- 토큰


def rules_stamp() -> str:
    """기준 데이터 47항목의 지문. 값이 하나라도 바뀌면 달라진다."""
    payload = {
        origin.filename: {key: ruleset[key].value for key in sorted(ruleset.item_keys())}
        for origin, ruleset in (
            (RuleOrigin.STATUTORY, rules()),
            (RuleOrigin.JUDGEMENT, assumptions()),
        )
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def usage_token(usage: UsageData) -> str:
    """부하 데이터의 지문. 파일·열 판정·간격이 같으면 같은 값이다."""
    meta = usage.meta
    text = "|".join(
        str(part)
        for part in (
            meta.source_name,
            meta.date_column,
            meta.energy_column,
            meta.interval_minutes,
            meta.start,
            meta.end,
            meta.expected_rows,
            meta.total_kwh,
        )
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def form_token(form: ContractForm) -> str:
    return "|".join(
        str(part)
        for part in (
            form.contract_type,
            form.voltage,
            form.option,
            form.contract_kw,
            form.lagging_pct,
            form.leading_power_factor_pct,
            form.sunday_is_holiday,
        )
    )


# --------------------------------------------------------------------- 캐시 비움


def clear_calc_cache() -> None:
    """계산 캐시를 통째로 비운다. **기준 데이터 편집 뒤에 반드시 부른다.**"""
    reload_rules()
    st.cache_data.clear()


def apply_rule_edit(result: EditResult) -> EditResult:
    """편집 결과를 받아 성공했을 때만 캐시를 비운다.

    실패는 **저장되지 않았다**는 뜻이므로 캐시를 건드릴 이유가 없다.
    """
    if result.ok:
        clear_calc_cache()
    return result


# --------------------------------------------------------------------- 캐시된 계산


@st.cache_data(show_spinner="요금표를 읽는 중…")
def cached_tariff(_stamp: str = "") -> TariffTable:
    return load_tariff()


@st.cache_data(show_spinner="사용량 파일을 읽는 중…")
def cached_usage(
    data: bytes,
    filename: str,
    *,
    date_column: str | None = None,
    energy_column: str | None = None,
    interval_minutes: int | None = None,
) -> UsageData:
    """업로드 바이트를 읽는다. **바이트 자체가 캐시 키**라 같은 파일은 한 번만 읽는다."""
    return load_usage_bytes(
        data,
        filename,
        date_column=date_column,
        energy_column=energy_column,
        interval_minutes=interval_minutes,
    )


@st.cache_data(show_spinner="데이터 품질을 보는 중…")
def cached_quality(_usage: UsageData, token: str, contract_kw: float | None) -> QualityReport:
    return pipeline.load_quality(_usage, contract_kw=contract_kw)


@st.cache_data(show_spinner="진단하는 중…")
def cached_diagnosis(
    _usage: UsageData,
    _table: TariffTable,
    _quality: QualityReport | None,
    token: str,
    form: ContractForm | None,
    stamp: str,
) -> Diagnosis:
    return pipeline.diagnose_usage(_usage, _table, form, quality=_quality)


@st.cache_data(show_spinner="현행 요금을 계산하는 중…")
def cached_baseline_bill(
    _usage: UsageData,
    _table: TariffTable,
    _quality: QualityReport | None,
    token: str,
    form: ContractForm,
    stamp: str,
) -> BillingResult:
    return pipeline.baseline_bill(_usage, _table, form, quality=_quality)


@st.cache_data(show_spinner="기상 자료로 발전 프로파일을 만드는 중…")
def cached_unit_pv(
    _usage: UsageData, token: str, inputs: SolarInputs, stamp: str
) -> tuple[pd.Series, str]:
    return pipeline.unit_pv_profile(_usage, inputs)


@st.cache_data(show_spinner="태양광 용량 곡선을 훑는 중…")
def cached_solar(
    _usage: UsageData,
    _table: TariffTable,
    _unit: pd.Series,
    _baseline: BillingResult | None,
    _quality: QualityReport | None,
    token: str,
    form: ContractForm,
    inputs: SolarInputs,
    stamp: str,
) -> SolarCurve:
    return pipeline.solar_result(
        _usage, _table, form, inputs, _unit, baseline=_baseline, quality=_quality
    )


@st.cache_data(show_spinner="조합마다 요금을 다시 계산하는 중…")
def cached_comparison(
    _usage: UsageData,
    _table: TariffTable,
    _baseline: BillingResult,
    _unit: pd.Series | None,
    _quality: QualityReport | None,
    token: str,
    specs: tuple[CombinationSpec, ...],
    options_key: str,
    stamp: str,
    _options: BillingOptions | None = None,
) -> ComparisonResult:
    return compare_combinations(
        _usage,
        _table,
        specs,
        baseline_bill=_baseline,
        unit_pv_kw_per_kwp=_unit,
        quality=_quality,
        options=_options,
    )


@st.cache_data(show_spinner="선택요금을 모두 다시 계산하는 중…")
def cached_tariff_switch(
    _usage: UsageData,
    _table: TariffTable,
    _quality: QualityReport | None,
    _option_totals: Mapping[str, float] | None,
    token: str,
    form: ContractForm,
    stamp: str,
) -> TariffSwitchResult:
    return evaluate_tariff_switch(
        _usage,
        _table,
        form.selection,
        quality=_quality,
        options=form.billing_options(),
        option_totals=_option_totals,
    )


@st.cache_data(show_spinner="계약전력 조정을 보는 중…")
def cached_contract_adjustment(
    _usage: UsageData,
    _bill: BillingResult,
    token: str,
    contract_kw: float,
    contract_floor_ratio: float | None,
    margin_ratio: float,
    stamp: str,
) -> ContractAdjustment:
    return evaluate_contract_adjustment(
        _usage,
        _bill,
        contract_kw=contract_kw,
        contract_floor_ratio=contract_floor_ratio,
        margin_ratio=margin_ratio,
    )


@st.cache_data(show_spinner="역률 두 값으로 요금을 다시 계산하는 중…")
def cached_power_factor(
    _usage: UsageData,
    _table: TariffTable,
    _baseline: BillingResult | None,
    _quality: QualityReport | None,
    token: str,
    form: ContractForm,
    target_pct: float,
    investment_won: float,
    stamp: str,
) -> PowerFactorResult:
    return evaluate_power_factor(
        _usage,
        _table,
        form.selection,
        current_pct=form.lagging_pct,
        target_pct=target_pct,
        investment_won=investment_won,
        baseline=_baseline,
        quality=_quality,
        options=form.billing_options(),
    )


@st.cache_data(show_spinner="ESS 디스패치를 돌리는 중…")
def cached_ess(
    _usage: UsageData,
    _table: TariffTable,
    _baseline: BillingResult | None,
    _quality: QualityReport | None,
    token: str,
    form: ContractForm,
    target_kw: float,
    unit_cost_won_per_kw: float | None,
    total_investment_won: float | None,
    stamp: str,
) -> EssResult:
    if total_investment_won is not None:
        cost = EssCostInput.of_total(total_investment_won)
    elif unit_cost_won_per_kw is not None:
        cost = EssCostInput.of_unit_cost(unit_cost_won_per_kw)
    else:
        # **0 원으로 때우지 않는다.** 회수기간이 0년이 되어 "즉시 회수" 로 읽힌다.
        raise ValueError("ESS 단가(원/kW) 또는 총 투자비를 넣어야 계산합니다 (요구사항서 7.6).")
    return evaluate_ess(
        _usage,
        _table,
        form.selection,
        target_kw=target_kw,
        cost=cost,
        baseline=_baseline,
        quality=_quality,
        options=form.billing_options(),
    )


@st.cache_data(show_spinner="잉여 전력을 보는 중…")
def cached_surplus(
    _usage: UsageData,
    _table: TariffTable,
    _unit: pd.Series,
    token: str,
    form: ContractForm,
    capacity_kwp: float,
    external_price_won_per_kwh: float | None,
    stamp: str,
) -> SurplusResult:
    net = apply_generation(_usage, _unit * capacity_kwp)
    return evaluate_surplus(
        _usage,
        _table,
        form.selection,
        net.surplus_kw,
        generation_kwh=net.generated_kwh,
        external_price_won_per_kwh=external_price_won_per_kwh,
        options=form.billing_options(),
    )


@st.cache_data(show_spinner="감도를 훑는 중… (시나리오 3종)")
def cached_sensitivity(
    _usage: UsageData,
    _table: TariffTable,
    _baseline: BillingResult,
    _unit: pd.Series,
    _quality: QualityReport | None,
    token: str,
    spec: CombinationSpec,
    options_key: str,
    stamp: str,
    _options: BillingOptions | None = None,
) -> tuple[pd.DataFrame, tuple[SensitivityRange, ...]]:
    frame = sensitivity_comparison(
        _usage,
        _table,
        spec,
        baseline_bill=_baseline,
        unit_pv_kw_per_kwp=_unit,
        quality=_quality,
        options=_options,
    )
    return frame, sensitivity_ranges(frame)
