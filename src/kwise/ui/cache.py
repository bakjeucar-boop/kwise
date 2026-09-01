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
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

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
    EssCostModel,
    EssOptimum,
    EssResult,
    EssTargetCurve,
    PowerFactorResult,
    SolarCurve,
    SolarPoint,
    SurplusResult,
    TariffSwitchResult,
    apply_generation,
    ess_target_curve,
    evaluate_contract_adjustment,
    evaluate_ess,
    evaluate_power_factor,
    evaluate_surplus,
    evaluate_tariff_switch,
    load_ess_cost_model,
    refine_ess_target,
)
from kwise.progress import ProgressReporter
from kwise.quality import DEFAULT_OPERATING_HOURS, QualityReport
from kwise.rules import EditResult, RuleOrigin, assumptions, reload_rules, rules
from kwise.tariff import BillingOptions, BillingResult, TariffTable, load_tariff
from kwise.ui import pipeline
from kwise.ui.memo import clear_memo, session_memo
from kwise.ui.pipeline import AzimuthOption, ContractForm, SolarInputs

__all__ = [
    "apply_rule_edit",
    "cached_azimuth_options",
    "cached_baseline_bill",
    "cached_comparison",
    "cached_contract_adjustment",
    "cached_daily_temperature",
    "cached_diagnosis",
    "cached_ess",
    "cached_ess_optimum",
    "cached_ess_targets",
    "cached_power_factor",
    "cached_quality",
    "cached_sensitivity",
    "cached_solar",
    "cached_surplus",
    "cached_surplus_points",
    "cached_tariff",
    "cached_tariff_switch",
    "cached_unit_pv",
    "cached_usage",
    "clear_calc_cache",
    "code_stamp",
    "ess_cost_model",
    "form_token",
    "rules_stamp",
    "upload_digest",
    "usage_token",
]


# --------------------------------------------------------------------- 토큰


# ===================================================================== 34세션 3절 · 코드 지문
#
# **``st.cache_data`` 는 감싼 함수의 소스만 해시한다.** 그 함수가 부르는 다른
# 모듈이 바뀌어도 키가 그대로라, 앱이 떠 있는 채로 코드를 고치면 **소스는 새 값인데
# 화면은 옛 값**이 된다. Streamlit 은 스크립트를 다시 읽어도 캐시는 들고 있다.
#
# 33세션이 여기 걸렸다 — 조합 근거 문구를 고쳤는데 화면이 그대로였다. 문구가
# :class:`~kwise.compare.ComparisonResult` 안에 담겨 **캐시된 결과의 일부**로
# 돌아오기 때문이다. 기준 데이터에 같은 병이 있어 이 모듈 머리에 적어 둔 그것과
# 원인이 같다.
#
# **패키지 소스의 지문을 캐시 키에 함께 물린다.** 파일 하나만 바뀌어도 모든
# 캐시 키가 달라진다.
#
#     · 한 프로세스에서 **한 번만** 잰다 (`lru_cache`). 배포지에서는 소스가
#       바뀌지 않으므로 재실행마다 파일을 훑을 이유가 없다
#     · 크기와 수정 시각만 본다 — 내용을 읽으면 100여 파일을 매번 읽게 된다
def _source_fingerprint() -> str:
    """``src\\kwise`` 전체의 지문 (34세션 3절). 파일 하나만 바뀌어도 달라진다."""
    root = Path(__file__).resolve().parent.parent
    parts = [
        f"{path.relative_to(root).as_posix()}:{stat.st_size}:{stat.st_mtime_ns}"
        for path in sorted(root.rglob("*.py"))
        for stat in (path.stat(),)
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]


@lru_cache(maxsize=1)
def code_stamp() -> str:
    """코드 지문. **프로세스마다 한 번 잰다** (34세션 3절)."""
    try:
        return _source_fingerprint()
    except OSError:  # 소스를 읽을 수 없는 배포 형태 — 캐시 키만 못 물릴 뿐이다
        return ""


def rules_stamp() -> str:
    """기준 데이터 47항목 **과 코드**의 지문. 하나라도 바뀌면 달라진다.

    코드까지 무는 이유는 위 「코드 지문」 주석에 있다 — 값만 물면 앱이 떠 있는
    채로 코드를 고쳤을 때 화면이 옛 값을 낸다.
    """
    payload = {
        origin.filename: {key: ruleset[key].value for key in sorted(ruleset.item_keys())}
        for origin, ruleset in (
            (RuleOrigin.STATUTORY, rules()),
            (RuleOrigin.JUDGEMENT, assumptions()),
        )
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"{digest}.{code_stamp()}"


def upload_digest() -> str:
    """올린 파일 **내용**의 지문. 세션이 없으면 빈 문자열이다.

    ``st.cache_data`` 는 **프로세스 전역**이라 동시 접속자가 캐시를 공유한다
    (Streamlit Cloud 배포 시). 요약 정보(파일명·기간·총량)만으로 키를 만들면
    서로 다른 파일이 같은 키를 가질 여지가 남고, 그때 옆 사람의 결과가 나온다.
    내용 해시를 키에 물려 그 여지를 없앤다.
    """
    try:
        data = st.session_state.get("upload_bytes")
    except Exception:
        return ""
    if not isinstance(data, bytes):
        return ""
    return hashlib.sha256(data).hexdigest()[:16]


def usage_token(usage: UsageData) -> str:
    """부하 데이터의 지문. **올린 파일 내용까지 물린다** (동시 사용 대비)."""
    meta = usage.meta
    text = "|".join(
        str(part)
        for part in (
            upload_digest(),
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
    """계산 캐시를 통째로 비운다. **기준 데이터 편집 뒤에 반드시 부른다.**

    캐시가 두 갈래(전역 ``st.cache_data`` + 세션 기억)이므로 **둘 다** 비운다.
    한쪽만 비우면 화면 절반은 새 값, 절반은 옛 값이 된다.
    """
    reload_rules()
    st.cache_data.clear()
    clear_memo()


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
    operating_hours: tuple[int, int] = DEFAULT_OPERATING_HOURS,
    dr_off_days: tuple[str, ...] = (),
) -> Diagnosis:
    """``dr_off_days`` 는 **캐시 열쇠에 들어가야 한다** (29세션).

    사용자가 쉬는 날을 고르면 DR 감축량이 다시 계산되는데, 열쇠에 없으면 옛
    결과가 그대로 돌아온다 — 화면은 고쳤는데 숫자가 안 바뀌는 자리가 된다.
    """
    return pipeline.diagnose_usage(
        _usage,
        _table,
        form,
        quality=_quality,
        operating_hours=operating_hours,
        dr_off_days=dr_off_days,
    )


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


@st.cache_data(show_spinner="기온 자료를 읽는 중…")
def cached_daily_temperature(
    _usage: UsageData, token: str, region_key: str
) -> tuple[pd.Series, str] | None:
    """부하 패턴이 곁들이는 기온 (30세션 4절). **못 받으면 ``None`` 이다.**

    캐시 열쇠는 자료와 지역뿐이다 — 기상은 요금 기준 데이터와 무관하므로
    ``rules_stamp()`` 를 물리지 않는다.
    """
    return pipeline.daily_temperature(_usage, region_key)


@st.cache_data(show_spinner="기상 자료로 발전 프로파일을 만드는 중…")
def cached_unit_pv(
    _usage: UsageData, token: str, inputs: SolarInputs, stamp: str
) -> tuple[pd.Series, str]:
    return pipeline.unit_pv_profile(_usage, inputs)


@st.cache_data(show_spinner="여덟 방위를 비교하는 중…")
def cached_azimuth_options(
    _usage: UsageData,
    token: str,
    region_key: str,
    latitude: float | None,
    longitude: float | None,
    tilt_deg: float,
    gcr: float,
    system_loss_ratio: float,
    altitude_m: float,
    stamp: str,
) -> tuple[AzimuthOption, ...]:
    """방위별 상대 발전량 (15세션 1-1).

    **캐시 키에 면적·밀도 라벨을 넣지 않는다.** 용량 1 kWp 로 고정해 비율만
    내므로 면적이 바뀌어도 값이 같다 — 넣으면 면적을 만질 때마다 여덟 번씩
    다시 돈다.
    """
    inputs = SolarInputs(
        region_key=region_key,
        latitude=latitude,
        longitude=longitude,
        system_loss_ratio=system_loss_ratio,
        altitude_m=altitude_m,
    )
    return pipeline.azimuth_options(_usage, inputs, tilt_deg=tilt_deg, gcr=gcr)


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
    _progress: ProgressReporter | None = None,
) -> SolarCurve:
    """**세션 기억을 쓴다** — ``st.cache_data`` 는 진행 표시와 함께 못 쓴다.

    기억에 있으면 본문이 돌지 않아 ``step()`` 도 불리지 않는다. 그것이 맞다 —
    즉시 끝난 단계에 진행을 그릴 이유가 없다.
    """
    key = f"solar|{token}|{form_token(form)}|{inputs}|{stamp}"
    return session_memo(
        key,
        lambda: pipeline.solar_result(
            _usage,
            _table,
            form,
            inputs,
            _unit,
            baseline=_baseline,
            quality=_quality,
            progress=_progress,
        ),
    )


def cached_surplus_points(
    _usage: UsageData,
    _table: TariffTable,
    _unit: pd.Series,
    _baseline: BillingResult | None,
    _quality: QualityReport | None,
    token: str,
    form: ContractForm,
    inputs: SolarInputs,
    stamp: str,
) -> tuple[tuple[str, SolarPoint], ...]:
    """용량 비교 표의 잉여 지점 둘 (31세션 4-1). **요금 재계산 두 번이다.**

    ``cached_solar`` 와 같은 세션 기억을 쓴다 — 태양광 카드는 입력을 만질 때마다
    다시 그려지므로, 캐시하지 않으면 두 번의 재계산이 매 실행에 붙는다.
    """
    key = f"surplus_points|{token}|{form_token(form)}|{inputs}|{stamp}"
    return session_memo(
        key,
        lambda: pipeline.surplus_capacity_points(
            _usage,
            _table,
            form,
            _unit,
            cost=inputs.cost(),
            baseline=_baseline,
            quality=_quality,
        ),
    )


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
    _progress: ProgressReporter | None = None,
) -> ComparisonResult:
    # **잉여 수익을 열쇠에서 뺀다** (57세션). 그 몫은 요금 계산 **밖에서** 붙는
    # 덧셈이라 부하도 청구서도 바꾸지 않는데, 조합 명세에 들어 있어 라디오를
    # 누를 때마다 조합 여섯의 요금이 통째로 다시 돌았다 — 값이 이미 손에 있는데
    # 2.3초를 썼다. 게다가 기억이 여덟 칸뿐이라(:mod:`kwise.ui.memo`) 새 항목이
    # **태양광 곡선과 ESS 정밀화를 밀어내** 6.5초짜리 재계산까지 불렀다.
    revenue = next(
        (spec.surplus_revenue_won for spec in specs if spec.has_pv and spec.surplus_revenue_won),
        None,
    )
    scenario = next((spec.surplus_scenario for spec in specs if spec.surplus_scenario), "")
    stripped = tuple(replace(spec, surplus_revenue_won=None, surplus_scenario="") for spec in specs)
    key = f"compare|{token}|{stripped}|{options_key}|{stamp}"
    base = session_memo(
        key,
        lambda: compare_combinations(
            _usage,
            _table,
            stripped,
            baseline_bill=_baseline,
            unit_pv_kw_per_kwp=_unit,
            quality=_quality,
            options=_options,
            progress=_progress,
        ),
    )
    return base.with_surplus_revenue(revenue, scenario)


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
    form: ContractForm,
    contract_kw: float,
    contract_floor_ratio: float | None,
    stamp: str,
) -> ContractAdjustment:
    """**계약종별이 열쇠에 있어야 한다** (59세션 8절).

    하한 비율은 종별 속성이라 :class:`BillingResult` 가 들고 오는데, 그 인자는
    ``_bill`` 이라 열쇠에서 빠진다. 다른 캐시 함수는 모두 ``form`` 을 열쇠에
    두는데 여기만 없어, **한 세션에서 계약종별을 바꾸면 앞 종별의 결과가 그대로
    다시 나왔다** — 일반용(을) 로 한 번 계산한 뒤 일반용(갑)Ⅰ 로 바꾸니
    「하한 규정 미확인 — 미산출」 이어야 할 자리에 「없음 — 하한 30% 적용」 이
    섰다. 덱 여섯을 한 프로세스에서 뽑다가 드러났다.
    """
    return evaluate_contract_adjustment(
        _usage,
        _bill,
        contract_kw=contract_kw,
        contract_floor_ratio=contract_floor_ratio,
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


def ess_cost_model(
    fixed_won: float | None = None,
    per_kwh_won: float | None = None,
    pricing_path: str | None = None,
) -> EssCostModel:
    """조달 사례 모델. **두 계수와 단가 경로를 갈아 끼울 수 있다** (14세션 3-4 · 50세션).

    경로는 둘이다 — 2항식(기본)과 kWh 구간 단가. **기준 데이터 화면에서 고른다**
    (50세션 3-5). 견적 총액은 세 번째 경로인데 그쪽은 모델을 거치지 않는다.
    """
    model = load_ess_cost_model()
    if pricing_path is not None and pricing_path != model.pricing_path:
        model = model.with_pricing_path(pricing_path)
    if fixed_won is None and per_kwh_won is None:
        return model
    return model.with_coefficients(
        fixed_won=model.fixed_won if fixed_won is None else fixed_won,
        per_kwh_won=model.per_kwh_won if per_kwh_won is None else per_kwh_won,
    )


@st.cache_data(show_spinner="ESS 목표를 훑는 중…")
def cached_ess_targets(
    _usage: UsageData,
    token: str,
    baseline_demand_kw: float,
    base_fee_won_per_kw: float,
    fixed_won: float | None,
    per_kwh_won: float | None,
    pricing_path: str,
    stamp: str,
) -> EssTargetCurve:
    """목표별 회수기간 곡선. **기본요금단가는 현행 요금제 기준으로 받는다.**"""
    return ess_target_curve(
        _usage.kw,
        _usage.meta.interval_minutes,
        baseline_demand_kw=baseline_demand_kw,
        base_fee_won_per_kw=base_fee_won_per_kw,
        model=ess_cost_model(fixed_won, per_kwh_won, pricing_path),
    )


def cached_ess_optimum(
    _usage: UsageData,
    _table: TariffTable,
    _baseline: BillingResult | None,
    _quality: QualityReport | None,
    _curve: EssTargetCurve,
    token: str,
    form: ContractForm,
    fixed_won: float | None,
    per_kwh_won: float | None,
    pricing_path: str,
    stamp: str,
    _progress: ProgressReporter | None = None,
) -> EssOptimum:
    """개략 곡선이 고른 목표를 **카드 기준으로 다시 고른다** (40세션).

    **세션 기억을 쓴다** — ``st.cache_data`` 는 진행 표시와 함께 못 쓴다
    (:func:`cached_solar` 와 같은 이유). 기억에 있으면 본문이 돌지 않아
    ``step()`` 도 불리지 않는다.

    한 점당 요금을 다시 계산하므로 **21점에 약 8초**다. 입력이 바뀌지 않으면
    다시 돌지 않는다.
    """
    key = f"ess_optimum|{token}|{form_token(form)}|{fixed_won}|{per_kwh_won}|{pricing_path}|{stamp}"
    return session_memo(
        key,
        lambda: refine_ess_target(
            _usage,
            _table,
            form.selection,
            curve=_curve,
            baseline=_baseline,
            quality=_quality,
            options=form.billing_options(),
            model=ess_cost_model(fixed_won, per_kwh_won, pricing_path),
            progress=_progress,
        ),
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
    total_investment_won: float | None,
    stamp: str,
    fixed_won: float | None = None,
    per_kwh_won: float | None = None,
    pricing_path: str | None = None,
) -> EssResult:
    # 견적 총액을 넣었으면 그쪽이 이긴다. 아니면 **조달 사례 모델**이 산정한다.
    # 0 원으로 때우지 않는다 — 0 원이면 회수기간이 "즉시 회수" 로 읽힌다.
    cost = (
        EssCostInput.of_total(total_investment_won)
        if total_investment_won is not None
        else EssCostInput.unpriced()
    )
    return evaluate_ess(
        _usage,
        _table,
        form.selection,
        target_kw=target_kw,
        cost=cost,
        model=ess_cost_model(fixed_won, per_kwh_won, pricing_path),
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
    smp_price_won_per_kwh: float | None = None,
) -> SurplusResult:
    net = apply_generation(_usage, _unit * capacity_kwp)
    return evaluate_surplus(
        _usage,
        _table,
        form.selection,
        net.surplus_kw,
        generation_kwh=net.generated_kwh,
        # **차감 한도는 자가소비를 뺀 뒤의 부하다** (41세션 2-3). 원부하로 재면
        # 태양광이 이미 지운 사용량까지 상계 여지로 세게 된다.
        net_usage=net.usage,
        capacity_kwp=capacity_kwp,
        external_price_won_per_kwh=external_price_won_per_kwh,
        smp_price_won_per_kwh=smp_price_won_per_kwh,
        options=form.billing_options(),
    )


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
    _progress: ProgressReporter | None = None,
) -> tuple[pd.DataFrame, tuple[SensitivityRange, ...]]:
    def build() -> tuple[pd.DataFrame, tuple[SensitivityRange, ...]]:
        frame = sensitivity_comparison(
            _usage,
            _table,
            spec,
            baseline_bill=_baseline,
            unit_pv_kw_per_kwp=_unit,
            quality=_quality,
            options=_options,
            progress=_progress,
        )
        return frame, sensitivity_ranges(frame)

    return session_memo(f"sensitivity|{token}|{spec}|{options_key}|{stamp}", build)
