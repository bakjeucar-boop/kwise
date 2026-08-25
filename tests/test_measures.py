"""개선 수단 평가 (요구사항서 7장, 11.3, 부록 B).

수단마다 요금을 다시 계산한다. 빼기로 어림한 값이 아니라는 것을 여러 곳에서 확인한다.
"""

from __future__ import annotations

import inspect
import math
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from kwise.compare import CombinationSpec, evaluate_combination
from kwise.diagnose import Diagnosis
from kwise.io import UsageData, load_usage
from kwise.measures import (
    CURTAIL_SCENARIO,
    EXTERNAL_SCENARIO,
    OFFSET_SCENARIO,
    PV_UNPRICED_REASON,
    Certainty,
    ContractStatus,
    EssCostInput,
    EssResult,
    NetLoad,
    PvCostInput,
    SolarCurve,
    SolarPoint,
    SurplusResult,
    TariffSwitchResult,
    analyze_peak_excess,
    apply_generation,
    dispatch_peak_shaving,
    evaluate_contract_adjustment,
    evaluate_ess,
    evaluate_surplus,
    evaluate_tariff_switch,
    excess_table,
    light_band_mask,
    offset_carry_only_max_kw,
    offset_max_kw,
    offset_settles_cash,
    roof_capacity_limit_kwp,
    size_for_target,
    solar_curve,
    solar_point,
    surplus_options,
    unit_generation_kw,
    with_load,
    with_surplus_revenue,
)
from kwise.notices import texts
from kwise.pv import ArrayConfig, PvSystemConfig
from kwise.quality import QualityReport
from kwise.tariff import BillingResult, TariffSelection, TariffTable, calculate_bill
from tests._synthetic import clearsky_weather, write_month

CURRENT = TariffSelection("general_b", "high_a", "I")
BEST = TariffSelection("general_b", "high_a", "II")
INTERVAL = 15
ESS_COST_WON_PER_KW = 615_231.0  # 참고단가 LFP 2025 · 1h 방전 환산값
PV_COST_WON_PER_KWP = 1_200_000.0


# --------------------------------------------------------------------- 7.1 선택요금 전환


def test_switch_prices_every_option_from_the_data(
    sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable
) -> None:
    """선택지는 요금 데이터에서 생성한다. 하드코딩하지 않는다.

    **전압구분은 넘나들지 않는다.** 수전설비로 정해지므로 전환 대상이 아니다.
    """
    result = evaluate_tariff_switch(sample_usage, tariff, CURRENT, quality=sample_report)
    assert {quote.key for quote in result.quotes} == {
        f"general_b/high_a/{option}" for option in ("I", "II", "III")
    }
    assert not any("high_b" in quote.key for quote in result.quotes)
    assert result.certainty is Certainty.HIGH
    assert result.investment_won == 0.0


def test_switch_reports_both_baselines(sample_switch: TariffSwitchResult) -> None:
    """현행 유지 기준과 최적 전환 기준을 모두 낸다."""
    result = sample_switch
    assert result.current.selection == CURRENT
    assert result.best.selection == BEST
    assert result.switch_needed
    assert result.saving_won == pytest.approx(53_575_280.0, rel=1e-4)
    assert result.ranking[0].selection == BEST


def test_switch_details_cover_every_option(sample_switch: TariffSwitchResult) -> None:
    """**모든 선택요금이 기본/전력량으로 갈라진다** (17세션 1-4).

    현행·최적 둘만 상세를 내던 것은 계산을 아끼려는 최적화였는데, 화면이 나머지를
    「상세 미산출」 로 그렸다. 값이 없는 것이 아니라 쪼개지 않았을 뿐이다.
    """
    assert sample_switch.quotes
    for quote in sample_switch.quotes:
        assert quote.base_won is not None, quote.key
        assert quote.energy_won is not None, quote.key
        assert quote.base_won + quote.energy_won == pytest.approx(quote.total_won)
    assert {CURRENT, BEST} <= {quote.selection for quote in sample_switch.quotes}


def test_switch_reuses_precomputed_totals(
    sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable
) -> None:
    """4세션 diagnose 의 합계를 넘기면 다시 계산하지 않는다.

    일부러 틀린 합계를 넣어 그 값이 그대로 쓰이는 것으로 재사용을 확인한다.
    """
    totals = {
        "general_b/high_a/I": 1.0,
        "general_b/high_a/II": 2.0,
        "general_b/high_a/III": 3.0,
        "general_b/high_b/I": 0.5,  # 더 싸지만 갈아탈 수 없는 전압이다
    }
    result = evaluate_tariff_switch(
        sample_usage, tariff, CURRENT, quality=sample_report, option_totals=totals
    )
    assert [quote.total_won for quote in result.quotes] == [1.0, 2.0, 3.0]
    assert result.best.key == "general_b/high_a/I"  # 가짜 합계에서는 현행이 최저다


def test_diagnosis_totals_plug_straight_in(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_diagnosis: Diagnosis,
) -> None:
    reused = evaluate_tariff_switch(
        sample_usage,
        tariff,
        CURRENT,
        quality=sample_report,
        option_totals=sample_diagnosis.option_totals,
    )
    fresh = evaluate_tariff_switch(sample_usage, tariff, CURRENT, quality=sample_report)
    assert reused.saving_won == pytest.approx(fresh.saving_won)
    assert reused.best.selection == fresh.best.selection


def test_switch_rejects_a_selection_outside_the_table(
    sample_usage: UsageData, tariff: TariffTable
) -> None:
    with pytest.raises(ValueError, match="요금표에 없는 조합"):
        evaluate_tariff_switch(sample_usage, tariff, TariffSelection("general_b", "high_a", "IV"))


# --------------------------------------------------------------------- 7.2 계약전력 조정


def test_floor_ratio_comes_from_the_contract_type() -> None:
    """하한 30% 가 확인됐다 (요구사항서 5.2 ③). 종별 속성으로 관리한다.

    인자를 주지 않으면 요금표의 종별 값을 쓴다. 코드에 숫자를 박지 않는다.
    """
    parameter = inspect.signature(evaluate_contract_adjustment).parameters["contract_floor_ratio"]
    assert parameter.default is None  # None = 종별 속성을 따른다
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


def test_floor_ratio_defaults_to_the_contract_type(
    sample_usage: UsageData, sample_bill: BillingResult
) -> None:
    """인자를 주지 않으면 종별 하한(일반용(을) 30%)으로 산출한다."""
    result = evaluate_contract_adjustment(sample_usage, sample_bill, contract_kw=7_000.0)
    assert result.status is ContractStatus.CONFIRMED
    assert result.contract_floor_ratio == pytest.approx(0.30)
    assert result.reduction_kw == pytest.approx(1_177.0)
    assert result.suggested_contract_kw == 5_823.0
    assert result.is_over_contracted
    # 7,000 × 30% = 2,100 kW 로 요금적용전력 5,293 kW 에 못 미친다 → 절감 없음
    assert result.saving_won == pytest.approx(0.0)


def test_unknown_floor_rule_reports_headroom_without_money(
    sample_usage: UsageData, tariff: TariffTable, sample_report: QualityReport
) -> None:
    """종별 하한 비율이 요금 데이터에 없으면 금액을 만들지 않는다."""
    import copy
    import json

    from kwise.tariff import default_tariff_dir, parse_tariff

    with (default_tariff_dir() / "tariff_kr_20260601.json").open(encoding="utf-8") as stream:
        payload = copy.deepcopy(json.load(stream))
    payload["contract_types"]["general_b"]["contract_floor_ratio"] = None
    unknown_table = parse_tariff(payload)
    bill = calculate_bill(sample_usage, unknown_table, CURRENT, quality=sample_report)

    result = evaluate_contract_adjustment(sample_usage, bill, contract_kw=7_000.0)
    assert result.status is ContractStatus.UNKNOWN
    assert result.saving_won is None
    assert result.annual_saving_won is None
    assert result.adjusted_base_won is None
    # 여지와 경고는 언제나 나온다
    assert result.reduction_kw == pytest.approx(1_177.0)
    assert result.is_over_contracted
    assert any("하한 규정" in message for message in texts(result.notices))


def test_confirmed_floor_rule_recalculates_the_base_fee(
    sample_usage: UsageData, sample_bill: BillingResult
) -> None:
    """월별 요금적용전력에 하한을 씌워 기본요금을 다시 합산한다."""
    result = evaluate_contract_adjustment(
        sample_usage, sample_bill, contract_kw=7_000.0, contract_floor_ratio=1.0
    )
    assert result.status is ContractStatus.CONFIRMED
    # 하한이 모든 달에 걸리므로 (7,000 − 5,823) × 7,220 원 × 12개월
    expected = (7_000.0 - 5_823.0) * 7_220.0 * 12.0
    assert result.saving_won == pytest.approx(expected)
    assert result.current_base_won == pytest.approx(7_000.0 * 7_220.0 * 12.0)
    assert result.adjusted_base_won == pytest.approx(5_823.0 * 7_220.0 * 12.0)
    assert "하한 100%" in result.saving_basis


def test_floor_below_the_demand_yields_no_saving(
    sample_usage: UsageData, sample_bill: BillingResult
) -> None:
    """하한이 요금적용전력에 걸리지 않으면 계약을 낮춰도 요금은 그대로다."""
    result = evaluate_contract_adjustment(
        sample_usage, sample_bill, contract_kw=7_000.0, contract_floor_ratio=0.3
    )
    assert result.saving_won == pytest.approx(0.0)
    assert any("걸리지 않아" in note for note in texts(result.notices))


def test_penalty_warning_is_always_returned(
    sample_usage: UsageData, sample_bill: BillingResult
) -> None:
    for ratio in (None, 1.0):
        result = evaluate_contract_adjustment(
            sample_usage, sample_bill, contract_kw=7_000.0, contract_floor_ratio=ratio
        )
        assert any("위약금" in message for message in texts(result.notices))
        assert any("12개월간 적용" in message for message in texts(result.notices))


def test_invalid_floor_ratio_raises(sample_usage: UsageData, sample_bill: BillingResult) -> None:
    with pytest.raises(ValueError, match="하한 비율"):
        evaluate_contract_adjustment(
            sample_usage, sample_bill, contract_kw=7_000.0, contract_floor_ratio=1.5
        )


# --------------------------------------------------------------------- 7.3 태양광


def test_roof_capacity_limit(tmp_path: Path) -> None:
    """가용 비율 60%, GCR 0.4, 0.2 kWp/m²."""
    assert roof_capacity_limit_kwp(20_000.0) == pytest.approx(960.0)
    assert roof_capacity_limit_kwp(0.0) == 0.0
    with pytest.raises(ValueError, match="음수"):
        roof_capacity_limit_kwp(-1.0)


def test_generation_scales_linearly_with_capacity(sample_usage: UsageData) -> None:
    """정격과 인버터 용량이 함께 커지므로 출력이 정확히 비례한다.

    그래서 용량 곡선은 시뮬레이션을 한 번만 돌리고 곱셈으로 단계를 만든다.
    """
    weather = clearsky_weather(start="2023-07-02", end="2023-07-04")
    index = pd.date_range("2023-07-03 00:15", periods=96, freq="15min")
    usage = with_load(sample_usage, sample_usage.kw)  # 인덱스만 빌린다
    small = PvSystemConfig(37.5, 127.0, arrays=(ArrayConfig.roof("지붕", 100.0),), altitude_m=50.0)
    large = small.scaled(700.0)
    unit_small = unit_generation_kw(usage, weather, small).reindex(index).fillna(0.0)
    unit_large = unit_generation_kw(usage, weather, large).reindex(index).fillna(0.0)
    pd.testing.assert_series_equal(unit_small, unit_large, rtol=1e-9)


def test_curve_starts_at_zero_and_covers_the_limit(sample_curve: SolarCurve) -> None:
    points = sample_curve.points
    assert len(points) == 5  # steps=4 → 0 포함 5점
    assert points[0].capacity_kwp == 0.0
    assert points[-1].capacity_kwp == pytest.approx(sample_curve.max_capacity_kwp)
    assert sample_curve.certainty is Certainty.MEDIUM


def test_zero_capacity_saves_exactly_nothing(sample_curve: SolarCurve) -> None:
    """PV 0 kWp 일 때 태양광 절감액이 정확히 0 이다 (요구사항서 11.3)."""
    zero = sample_curve.points[0]
    assert zero.generation_kwh == 0.0
    assert zero.surplus_kwh == 0.0
    assert zero.base_saving_won == 0.0
    assert zero.energy_saving_won == 0.0
    assert zero.total_saving_won == 0.0
    assert zero.investment_won == 0.0
    assert zero.payback_years is None
    assert zero.self_consumption_ratio is None


def test_energy_saving_increases_monotonically(sample_curve: SolarCurve) -> None:
    savings = [point.energy_saving_won for point in sample_curve.points]
    assert savings == sorted(savings)
    assert savings[-1] > savings[0]


def test_base_saving_is_monotonic(sample_curve: SolarCurve) -> None:
    """기본요금 절감은 단조 증가한 뒤 포화한다. 줄어들지는 않는다."""
    savings = [point.base_saving_won for point in sample_curve.points]
    assert savings == sorted(savings)


def test_generation_is_proportional_to_capacity(sample_curve: SolarCurve) -> None:
    points = sample_curve.points
    reference = points[-1]
    for point in points[1:]:
        expected = reference.generation_kwh * point.capacity_kwp / reference.capacity_kwp
        assert point.generation_kwh == pytest.approx(expected, rel=1e-9)


def test_solar_saving_is_recalculated_not_subtracted(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
    sample_unit_pv: pd.Series,
) -> None:
    """곡선의 절감액이 순부하로 다시 계산한 요금과 정확히 맞는지 본다."""
    point = solar_curve(
        sample_usage,
        tariff,
        CURRENT,
        sample_unit_pv,
        max_capacity_kwp=500.0,
        cost=PvCostInput.of_unit_cost(PV_COST_WON_PER_KWP),
        steps=1,
        baseline=sample_bill,
        quality=sample_report,
    ).points[-1]

    net = apply_generation(sample_usage, sample_unit_pv * 500.0)
    recomputed = calculate_bill(net.usage, tariff, CURRENT, quality=sample_report)
    assert point.total_saving_won == pytest.approx(sample_bill.total_won - recomputed.total_won)
    assert point.generation_kwh == pytest.approx(net.generated_kwh)


def test_power_factor_falls_and_warns(sample_curve: SolarCurve) -> None:
    """무효전력은 그대로인데 유효전력만 상쇄되어 역률이 떨어진다 (5.7)."""
    factors = [point.power_factor_after_pct for point in sample_curve.points]
    assert factors[0] == pytest.approx(92.0)  # 도입 전 추정 역률 (약관 제42조 간주값)
    assert factors == sorted(factors, reverse=True)
    assert factors[-1] < 92.0
    # 0 kWp 는 역률이 그대로이므로 추가요금도 0 이다.
    assert sample_curve.points[0].power_factor_extra_won == pytest.approx(0.0, abs=1.0)
    assert sample_curve.points[-1].power_factor_extra_won > 0


def test_power_factor_warning_appears_below_the_standard(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
    sample_unit_pv: pd.Series,
) -> None:
    """기준은 지상 **92%** 다 (기본공급약관 제41·43조). 90% 가 아니다.

    경고에 예상 추가 역률요금과 그만큼 깎인 절감액을 함께 적는다.
    """
    curve = solar_curve(
        sample_usage,
        tariff,
        CURRENT,
        sample_unit_pv,
        max_capacity_kwp=960.0,
        cost=PvCostInput.of_unit_cost(PV_COST_WON_PER_KWP),
        steps=1,
        baseline=sample_bill,
        quality=sample_report,
    )
    point = curve.points[-1]
    assert point.power_factor_after_pct < 92.0
    assert point.power_factor_extra_won > 0  # 92% 미만이므로 추가요금이다
    assert point.saving_after_power_factor_won < point.total_saving_won
    assert any("역률 개선 설비" in message for message in texts(curve.notices))
    assert any("제41·43조" in message for message in texts(curve.notices))


def test_tariff_switch_saving_ignores_sensitivity_while_solar_does_not(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
    sample_unit_pv: pd.Series,
) -> None:
    """요금제 전환은 확정 계산이라 감도와 무관하다. 태양광은 감도에 따라 달라진다."""
    switch_savings: set[float] = set()
    solar_savings: set[float] = set()
    for sharpness in (0.85, 1.00, 1.25):
        switch_savings.add(
            evaluate_tariff_switch(sample_usage, tariff, CURRENT, quality=sample_report).saving_won
        )
        curve = solar_curve(
            sample_usage,
            tariff,
            CURRENT,
            sample_unit_pv,
            max_capacity_kwp=300.0,
            cost=PvCostInput.of_unit_cost(PV_COST_WON_PER_KWP),
            steps=1,
            sharpness=sharpness,
            baseline=sample_bill,
            quality=sample_report,
        )
        solar_savings.add(curve.points[-1].total_saving_won)
    assert len(switch_savings) == 1
    assert len(solar_savings) == 3


# --------------------------------------------------------------------- 7.4 ESS


def test_appendix_b_excess_table(sample_usage: UsageData) -> None:
    """부록 B 목표 피크별 초과 분석 — 회귀로 고정한다."""
    expected = {
        5_200.0: (13, 3.25, 93.4, 91.0),
        5_000.0: (496, 124.0, 293.4, 9_743.0),
        4_800.0: (1_225, 306.25, 493.4, 52_964.0),
        4_500.0: (2_160, 540.0, 793.4, 178_289.0),
    }
    table = excess_table(sample_usage.kw, tuple(expected), INTERVAL)
    for target, (slots, hours, power, energy) in expected.items():
        row = table.loc[target]
        assert row["slots"] == slots, target
        assert row["hours"] == pytest.approx(hours), target
        assert row["max_excess_kw"] == pytest.approx(power, abs=0.1), target
        assert row["total_excess_kwh"] == pytest.approx(energy, abs=1.0), target


def test_power_and_energy_are_reported_separately(sample_usage: UsageData) -> None:
    """목표를 조금 낮추면 필요 에너지가 급증한다. 출력은 그만큼 늘지 않는다."""
    high = analyze_peak_excess(sample_usage.kw, 5_200.0, INTERVAL)
    low = analyze_peak_excess(sample_usage.kw, 5_000.0, INTERVAL)
    assert low.max_excess_kw / high.max_excess_kw == pytest.approx(3.14, abs=0.05)
    assert low.total_excess_kwh / high.total_excess_kwh > 100
    # 용량 산정 기준 세 가지가 모두 다르다
    assert high.max_event_excess_kwh < high.max_daily_excess_kwh < high.total_excess_kwh


def test_no_excess_above_the_peak(sample_usage: UsageData) -> None:
    excess = analyze_peak_excess(sample_usage.kw, 6_000.0, INTERVAL)
    assert excess.slots == 0
    assert excess.total_excess_kwh == 0.0
    assert excess.events == 0


def test_sizing_uses_daily_energy_by_default(sample_usage: UsageData) -> None:
    excess = analyze_peak_excess(sample_usage.kw, 5_200.0, INTERVAL)
    power, capacity = size_for_target(excess)
    assert power == pytest.approx(excess.max_excess_kw)
    # 하루 최대 에너지를 방전 효율과 DoD 로 되돌린 값
    assert capacity == pytest.approx(excess.max_daily_excess_kwh / math.sqrt(0.88) / 0.90)
    _, event_based = size_for_target(excess, basis="event")
    assert event_based < capacity
    with pytest.raises(ValueError, match="용량 산정 기준"):
        size_for_target(excess, basis="hourly")


def test_dispatch_conserves_energy(sample_ess: EssResult) -> None:
    """soc_end − soc_start = 충전 × η − 방전 ÷ η. 항등식이 정확히 성립한다."""
    dispatch = sample_ess.dispatch
    efficiency = math.sqrt(dispatch.round_trip)
    stored = dispatch.soc_end_kwh - dispatch.soc_start_kwh
    flows = dispatch.charged_kwh * efficiency - dispatch.discharged_kwh / efficiency
    assert stored == pytest.approx(flows, abs=1e-9)
    assert dispatch.charged_kwh > 0
    assert dispatch.discharged_kwh > 0


def test_dispatch_meets_the_target(sample_ess: EssResult) -> None:
    dispatch = sample_ess.dispatch
    assert dispatch.target_met
    assert dispatch.unmet_kwh == pytest.approx(0.0, abs=1e-9)
    assert dispatch.achieved_peak_kw == pytest.approx(5_200.0, abs=0.01)


def test_dispatch_charges_only_in_the_light_band(
    sample_usage: UsageData, tariff: TariffTable
) -> None:
    mask = light_band_mask(sample_usage, tariff, selection=CURRENT)
    dispatch = dispatch_peak_shaving(
        sample_usage.kw,
        target_kw=5_200.0,
        power_kw=100.0,
        capacity_kwh=50.0,
        charge_mask=mask,
        interval_minutes=INTERVAL,
    )
    charging = dispatch.net_kw > sample_usage.kw + 1e-9
    assert bool(charging.any())
    assert bool(mask[charging.fillna(False)].all())


def test_dispatch_never_charges_above_the_target(
    sample_usage: UsageData, tariff: TariffTable
) -> None:
    """충전이 새 피크를 만들면 의미가 없다."""
    mask = light_band_mask(sample_usage, tariff, selection=CURRENT)
    dispatch = dispatch_peak_shaving(
        sample_usage.kw,
        target_kw=4_000.0,
        power_kw=2_000.0,
        capacity_kwh=20_000.0,
        charge_mask=mask,
        interval_minutes=INTERVAL,
    )
    charging = dispatch.net_kw > sample_usage.kw + 1e-9
    assert float(dispatch.net_kw[charging.fillna(False)].max()) <= 4_000.0 + 1e-6


def test_missing_slots_are_left_alone(sample_usage: UsageData, tariff: TariffTable) -> None:
    mask = light_band_mask(sample_usage, tariff, selection=CURRENT)
    dispatch = dispatch_peak_shaving(
        sample_usage.kw,
        target_kw=5_000.0,
        power_kw=300.0,
        capacity_kwh=1_500.0,
        charge_mask=mask,
        interval_minutes=INTERVAL,
    )
    assert int(dispatch.net_kw.isna().sum()) == int(sample_usage.kw.isna().sum())


def test_ess_economics(sample_ess: EssResult) -> None:
    """**50세션부터 사양은 규격 격자 위의 값이다** (3-2).

    필요 93.4 kW / 41.1 kWh 는 기성품에 없다. 살 수 있는 것은 100 kW / 50 kWh 이고
    투자비도 그 출력으로 낸다 — 더 산 만큼 회수기간이 길어지는데 그것이 정직한
    방향이다. 필요 사양은 ``required_*`` 에 그대로 남는다.
    """
    result = sample_ess
    assert result.power_kw == 100.0
    assert result.capacity_kwh == 50.0
    assert result.required_power_kw == pytest.approx(93.4, abs=0.1)
    assert result.required_capacity_kwh == pytest.approx(41.1, abs=0.5)
    # 투자비 = **출력 × kW당 단가**. 방전시간은 단가에 들어 있어 다시 곱하지 않는다.
    assert result.investment_won == pytest.approx(result.power_kw * ESS_COST_WON_PER_KW)
    assert result.total_saving_won > 0
    assert result.payback_years is not None
    assert result.certainty is Certainty.MEDIUM_LOW


def test_breakeven_unit_cost_is_reversed_from_ten_years(sample_ess: EssResult) -> None:
    """회수기간 10년이 되는 단가를 역산한다. '경제성 없음' 도 의사결정 자료가 된다."""
    result = sample_ess
    expected = result.annual_saving_won * 10.0 / result.power_kw
    assert result.breakeven_unit_cost_won_per_kw == pytest.approx(expected)
    assert result.payback_target_years == 10.0


def test_ess_saving_comes_mostly_from_the_base_fee(sample_ess: EssResult) -> None:
    """피크컷의 절감은 기본요금이 대부분이다.

    전력량요금도 조금 준다. 최대·중간부하에 방전하고 경부하에 충전하므로
    단가 차이가 왕복손실(12%)보다 크기 때문이다. 이 부호는 요금표에 달려 있다.
    """
    result = sample_ess
    assert result.base_saving_won > 0
    assert result.base_saving_won > abs(result.energy_saving_won) * 100
    assert result.total_saving_won == pytest.approx(
        result.base_saving_won + result.energy_saving_won
    )


def test_undersized_battery_warns(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
) -> None:
    mask = light_band_mask(sample_usage, tariff, selection=CURRENT)
    result = evaluate_ess(
        sample_usage,
        tariff,
        CURRENT,
        target_kw=5_000.0,
        cost=EssCostInput.of_unit_cost(ESS_COST_WON_PER_KW),
        charge_mask=mask,
        power_kw=50.0,
        capacity_kwh=50.0,
        baseline=sample_bill,
        quality=sample_report,
    )
    assert not result.dispatch.target_met
    assert result.dispatch.achieved_peak_kw > 5_000.0
    assert any("지키지 못한" in message for message in texts(result.notices))


# ------------------------------------------------------------------ 잉여 (태양광의 결과)
#
# **41세션에 개선안에서 뺐다.** 잉여는 태양광을 얼마나 크게 지을지에 따라 나오는
# 결과이지 따로 고르는 수단이 아니다 — 계산 모듈은 그대로 남아 태양광 카드가 부른다.


@dataclass(frozen=True, eq=False)
class SurplusCase:
    """부하보다 큰 PV. 잉여가 실제로 생기는 경우."""

    usage: UsageData
    net: NetLoad
    unit: pd.Series
    capacity_kwp: float = 1_000.0


@pytest.fixture(scope="module")
def surplus_case(tmp_path_factory: pytest.TempPathFactory) -> SurplusCase:
    path = write_month(tmp_path_factory.mktemp("surplus") / "small.csv", 2023, 7, kwh=25.0)
    usage = load_usage(path)  # 100 kW 균일 부하
    weather = clearsky_weather(start="2023-06-30", end="2023-08-01")
    config = PvSystemConfig(
        37.5, 127.0, arrays=(ArrayConfig.roof("지붕", 1_000.0),), altitude_m=50.0
    )
    unit = unit_generation_kw(usage, weather, config)
    return SurplusCase(usage=usage, net=apply_generation(usage, unit * 1_000.0), unit=unit)


def _surplus(case: SurplusCase, tariff: TariffTable, **kwargs: object) -> SurplusResult:
    """``evaluate_surplus`` 를 케이스 인자로 부른다 — 시험마다 되풀이하지 않는다."""
    return evaluate_surplus(
        case.usage,
        tariff,
        CURRENT,
        case.net.surplus_kw,
        generation_kwh=case.net.generated_kwh,
        net_usage=case.net.usage,
        capacity_kwp=kwargs.pop("capacity_kwp", case.capacity_kwp),  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


def test_surplus_is_split_from_self_consumption(surplus_case: SurplusCase) -> None:
    net = surplus_case.net
    assert net.generated_kwh > 0
    assert net.surplus_kwh > 0
    assert net.self_consumed_kwh == pytest.approx(net.generated_kwh - net.surplus_kwh)
    assert 0 < (net.self_consumption_ratio or 0) < 1
    assert float(net.usage.kw.min()) >= 0.0  # 계통 사용량은 음수가 될 수 없다


def test_surplus_scenarios(surplus_case: SurplusCase, tariff: TariffTable) -> None:
    result = _surplus(
        surplus_case, tariff, external_price_won_per_kwh=90.0, smp_price_won_per_kwh=130.0
    )
    assert result.total_kwh == pytest.approx(surplus_case.net.surplus_kwh)
    # **셋이다** (41세션 2-2 · 56세션에 「버리기」 를 「출력제어」 로).
    # 27세션은 언제나 0원인 줄이라 표에서 뺐는데, 41세션에 표가 아니라
    # **고르는 자리**가 되면서 뜻이 달라졌다 — 「아무것도 하지 않는다」 를 고를
    # 수 없으면 셋 중 하나를 강요하게 된다.
    assert [item.name for item in result.scenarios] == [
        OFFSET_SCENARIO,
        EXTERNAL_SCENARIO,
        CURTAIL_SCENARIO,
    ]
    offset = result.scenario(OFFSET_SCENARIO)
    assert offset.revenue_won is not None
    assert offset.revenue_won > 0
    # 상계 단가는 요금표에서 나온다. 경부하(92.8)~최대부하(227.8) 사이여야 한다
    settlement = result.offset
    assert settlement is not None
    assert settlement.deducted_kwh > 0
    assert 92.8 <= settlement.deducted_won / settlement.deducted_kwh <= 227.8
    external = result.scenario(EXTERNAL_SCENARIO)
    assert external.revenue_won == pytest.approx(result.total_kwh * 90.0)
    assert result.scenario(CURTAIL_SCENARIO).revenue_won == 0.0


def test_external_price_is_not_invented(surplus_case: SurplusCase, tariff: TariffTable) -> None:
    result = _surplus(surplus_case, tariff)
    external = result.scenario(EXTERNAL_SCENARIO)
    assert external.revenue_won is None
    assert not external.is_priced
    assert "지어내지 않습니다" in external.basis
    # **입력칸과 같은 이름으로 적는다** (27세션 7-2).
    assert "잉여 판매 단가" in external.basis


def test_eligibility_is_not_judged(surplus_case: SurplusCase, tariff: TariffTable) -> None:
    """자격요건은 판정하지 않는다. 금액만 제시하고 확인 필요를 명시한다."""
    result = _surplus(surplus_case, tariff)
    assert any("자격요건" in note for note in texts(result.notices))
    assert all(scenario.admin_burden for scenario in result.scenarios)


def test_offset_deducts_by_the_real_tou_band(
    tmp_path_factory: pytest.TempPathFactory, tariff: TariffTable
) -> None:
    """**낮 시간을 일괄로 중간부하에 넣지 않는다** (41세션 2-3).

    여름 15~21시는 **최대부하**이고 태양광이 그 시간에도 발전한다. 그 창의 앞쪽
    두 시간에만 발전하는 합성 자료와 09~11시(중간부하)에만 발전하는 자료를
    나란히 넣으면, 차감 실효 단가가 최대부하 쪽에서 더 높아야 한다 — 낮을
    일괄로 중간부하에 넣었다면 둘이 같은 값이 된다.

    **창을 다 덮지 않는 것이 요령이다.** 발전이 부하를 넘는 슬롯은 사용량이 0 이
    되어 차감할 자리가 없어진다 — 같은 계시의 남은 시간이 그 자리를 준다.

    토요일(최대→중간)·일요일(전량 경부하)은 요금 엔진 규칙대로 섞이므로 실효
    단가는 최대부하 단가보다 낮게 나온다. **그래도 중간부하 단가는 넘는다.**
    """
    path = write_month(tmp_path_factory.mktemp("band") / "flat.csv", 2023, 7, kwh=25.0)
    usage = load_usage(path)  # 100 kW 균일 부하
    index = pd.DatetimeIndex(usage.kw.index)
    hour = index.hour + index.minute / 60.0
    rates = tariff.rates(CURRENT)
    mid_rate = rates.rate("summer", "mid")
    peak_rate = rates.rate("summer", "peak")
    assert mid_rate < peak_rate

    def effective(window: pd.Series) -> float:
        # 구간 끝 라벨이라 15:15 슬롯이 15:00~15:15 를 뜻한다.
        generation = pd.Series(np.where(window, 150.0, 0.0), index=index, name="kw")
        net = apply_generation(usage, generation)
        assert net.surplus_kwh > 0
        result = evaluate_surplus(
            usage,
            tariff,
            CURRENT,
            net.surplus_kw,
            generation_kwh=net.generated_kwh,
            net_usage=net.usage,
            capacity_kwp=500.0,
        )
        settlement = result.offset
        assert settlement is not None
        assert settlement.deducted_kwh > 0
        return settlement.deducted_won / settlement.deducted_kwh

    peak_window = effective((hour > 15.0) & (hour <= 17.0))
    mid_window = effective((hour > 9.0) & (hour <= 11.0))
    assert peak_window > mid_window, (peak_window, mid_window)
    assert peak_window > mid_rate, (peak_window, mid_rate)
    assert mid_window <= mid_rate, (mid_window, mid_rate)


def test_offset_never_goes_negative_and_carries_in_the_same_band(
    surplus_case: SurplusCase, tariff: TariffTable
) -> None:
    """차감은 그 달 그 계시 사용량까지만. 넘으면 0 에서 멈추고 이월한다."""
    result = _surplus(surplus_case, tariff, smp_price_won_per_kwh=130.0)
    settlement = result.offset
    assert settlement is not None
    # 100 kW 균일 부하에 1,000 kWp — 다 차감할 수 없다.
    assert settlement.deducted_kwh < result.total_kwh
    assert settlement.remaining_kwh > 0
    assert settlement.deducted_kwh + settlement.remaining_kwh == pytest.approx(result.total_kwh)
    for month in settlement.months:
        assert month.deducted_kwh >= 0.0
        assert month.carried_out_kwh >= 0.0


def test_carry_is_not_shown_when_there_is_none(
    tmp_path_factory: pytest.TempPathFactory, tariff: TariffTable
) -> None:
    """**이월이 없으면 표시하지 않는다** (41세션 2-3).

    부하가 큰 건물에서는 거의 안 생긴다 — 잉여가 그 달 사용량에 다 잠기기
    때문이다. 늘 「이월 없음」 을 적으면 없는 항목이 자리를 차지한다.
    """
    path = write_month(tmp_path_factory.mktemp("nocarry") / "big.csv", 2023, 7, kwh=2_500.0)
    usage = load_usage(path)  # 10,000 kW 균일 부하
    weather = clearsky_weather(start="2023-06-30", end="2023-08-01")
    config = PvSystemConfig(
        37.5, 127.0, arrays=(ArrayConfig.roof("지붕", 1_000.0),), altitude_m=50.0
    )
    unit = unit_generation_kw(usage, weather, config)
    net = apply_generation(usage, unit * 1_000.0)
    # 부하가 커서 잉여 자체가 없다 — 그래도 이월은 빈 값이어야 한다.
    result = evaluate_surplus(
        usage,
        tariff,
        CURRENT,
        net.surplus_kw,
        generation_kwh=net.generated_kwh,
        net_usage=net.usage,
        capacity_kwp=1_000.0,
    )
    settlement = result.offset
    assert settlement is not None
    assert settlement.carried == ()
    assert settlement.remaining_kwh == pytest.approx(0.0)


def test_offset_is_dropped_above_the_cap(surplus_case: SurplusCase, tariff: TariffTable) -> None:
    """**1,000 kW 를 넘으면 상계거래가 목록에 없다** (41세션 2-3)."""
    cap = offset_max_kw()
    assert surplus_options(cap) == (OFFSET_SCENARIO, EXTERNAL_SCENARIO, CURTAIL_SCENARIO)
    assert surplus_options(cap + 1.0) == (EXTERNAL_SCENARIO, CURTAIL_SCENARIO)

    result = _surplus(surplus_case, tariff, capacity_kwp=cap + 1.0)
    assert OFFSET_SCENARIO not in [item.name for item in result.scenarios]
    with pytest.raises(KeyError):
        result.scenario(OFFSET_SCENARIO)


def test_small_systems_carry_only(surplus_case: SurplusCase, tariff: TariffTable) -> None:
    """**10 kW 이하는 이월만 되고 현금 정산이 없다** (41세션 2-3)."""
    small = offset_carry_only_max_kw()
    assert not offset_settles_cash(small)
    assert offset_settles_cash(small + 1.0)

    result = _surplus(surplus_case, tariff, capacity_kwp=small, smp_price_won_per_kwh=130.0)
    settlement = result.offset
    assert settlement is not None
    assert not settlement.settles_cash
    assert settlement.smp_won is None
    assert settlement.smp_price_won_per_kwh is None  # 넣어도 쓰지 않는다
    # 금액은 나온다 — 차감분은 확정이고 잔여만 정산이 없을 뿐이다.
    assert settlement.revenue_won == pytest.approx(settlement.deducted_won)
    assert "현금 정산이 없습니다" in result.scenario(OFFSET_SCENARIO).basis


def test_smp_price_is_not_invented(surplus_case: SurplusCase, tariff: TariffTable) -> None:
    """**미입력이면 잔여 kWh 만 내고 금액은 미산출** (41세션 2-3)."""
    result = _surplus(surplus_case, tariff)
    settlement = result.offset
    assert settlement is not None
    assert settlement.settles_cash
    assert settlement.remaining_kwh > 0
    assert settlement.smp_won is None
    assert not settlement.is_priced
    offset = result.scenario(OFFSET_SCENARIO)
    assert offset.revenue_won is None
    assert not offset.is_priced
    assert "SMP 단가 미입력" in offset.basis
    assert f"{settlement.remaining_kwh:,.0f} kWh" in offset.basis


def test_offset_says_nothing_about_when_it_settles(
    surplus_case: SurplusCase, tariff: TariffTable
) -> None:
    """**정산 시점이나 기간에 관한 단서를 달지 않는다** (41세션 2-3).

    「해당 연도 평균 SMP」·「13개월째도 같은 단가」 같은 문구를 어디에도 두지
    않는다. 단가는 하나로 적용하고 기간 길이를 구분하지 않는다.
    """
    import re

    from kwise.measures import surplus as module

    said: list[str] = [module.__doc__ or ""]
    for smp in (None, 130.0):
        result = _surplus(surplus_case, tariff, smp_price_won_per_kwh=smp)
        said.extend(item.basis for item in result.scenarios)
        said.extend(item.text for item in result.notices)
    banned = re.compile(
        r"해당\s*연도|연도\s*평균|평균\s*SMP|\d+\s*개월째|말일|정산\s*시점|정산\s*주기"
        r"|익월|매월\s*말|연\s*단위|기간\s*말\s*시점"
    )
    for line in said:
        assert not banned.search(line), line


def test_offset_does_not_touch_the_base_fee(surplus_case: SurplusCase, tariff: TariffTable) -> None:
    """**기본요금은 바뀌지 않는다** (41세션 2-3).

    잉여는 부하가 낮은 시각에 나므로 요금적용전력과 무관하고, 태양광이 피크를
    낮춘 효과는 이미 태양광 계산에 들어 있다 — 여기 금액은 전부 전력량요금이다.
    """
    from kwise.tariff import calculate_bill

    case = surplus_case
    after = calculate_bill(case.net.usage, tariff, CURRENT)
    result = _surplus(case, tariff, smp_price_won_per_kwh=130.0)
    settlement = result.offset
    assert settlement is not None
    # 차감액이 태양광 적용 후 전력량요금을 넘지 않는다 — 넘으면 기본요금까지
    # 먹은 것이다.
    assert 0 < settlement.deducted_won <= after.total_energy_won
    assert any("기본요금은 바뀌지 않습니다" in note for note in texts(result.notices))


def test_surplus_hour_distribution_is_midday(
    surplus_case: SurplusCase, tariff: TariffTable
) -> None:
    result = _surplus(surplus_case, tariff)
    distribution = result.hour_distribution
    assert int(distribution.idxmax()) in range(11, 15)
    assert distribution.loc[3] == 0.0  # 새벽에는 잉여가 없다
    assert result.share_of_generation is not None
    # **토·일·공휴일을 함께 센다** (27세션 7-1). 옛 ``weekend_share`` 는 이름에
    # 주말을 달고 토요일을 빼먹고 있었다.
    assert result.off_day_share is not None
    assert result.off_day_share == pytest.approx(
        (result.weekend_kwh + result.holiday_kwh) / result.total_kwh
    )


# --------------------------------------------------------------------- 순부하 만들기


def test_missing_slots_stay_missing(sample_usage: UsageData, sample_unit_pv: pd.Series) -> None:
    """결측 구간은 자가소비를 판정할 수 없다. 결측인 채로 둔다."""
    net = apply_generation(sample_usage, sample_unit_pv * 500.0)
    assert int(net.usage.kw.isna().sum()) == int(sample_usage.kw.isna().sum())
    assert int(net.surplus_kw.isna().sum()) == int(sample_usage.kw.isna().sum())


def test_net_usage_keeps_off_grid_energy(
    sample_usage: UsageData, sample_unit_pv: pd.Series
) -> None:
    """그리드 이탈분은 건드리지 않는다. 총 사용량 규약이 유지된다."""
    net = apply_generation(sample_usage, sample_unit_pv * 500.0)
    assert net.usage.meta.off_grid_kwh == sample_usage.meta.off_grid_kwh
    assert net.usage.energy_kwh().sum() == pytest.approx(net.usage.total_kwh)
    assert net.usage.total_kwh < sample_usage.total_kwh


def test_with_load_recomputes_the_metadata(sample_usage: UsageData) -> None:
    halved = with_load(sample_usage, sample_usage.kw / 2, source_suffix=" (반)")
    assert halved.meta.max_demand_kw == pytest.approx(sample_usage.meta.max_demand_kw / 2)
    assert halved.meta.mean_kw == pytest.approx(sample_usage.meta.mean_kw / 2)
    assert halved.meta.load_factor == pytest.approx(sample_usage.meta.load_factor)
    assert halved.meta.source_name.endswith(" (반)")
    # 결측·이탈 정보는 그대로다
    assert halved.meta.missing_rows == sample_usage.meta.missing_rows


# --------------------------------------------------------------------- 7.5 태양광 단가


def test_investment_is_capacity_times_unit_cost(sample_curve: SolarCurve) -> None:
    """**투자비 = 설치 용량(kWp) × 입력단가(원/kWp).** ESS 와 같은 규약이다."""
    for point in sample_curve.points:
        assert point.investment_won == pytest.approx(point.capacity_kwp * PV_COST_WON_PER_KWP)
    assert sample_curve.is_priced


def test_total_investment_path(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
    sample_unit_pv: pd.Series,
) -> None:
    """견적서를 받았으면 총액을 그대로 쓴다. 곡선 전체에 같은 값이 붙는 것을 경고한다."""
    curve = solar_curve(
        sample_usage,
        tariff,
        CURRENT,
        sample_unit_pv,
        max_capacity_kwp=300.0,
        cost=PvCostInput.of_total(400_000_000.0),
        steps=2,
        baseline=sample_bill,
        quality=sample_report,
    )
    assert {point.investment_won for point in curve.points} == {400_000_000.0}
    assert any("같은 총액" in message for message in texts(curve.notices))


def test_missing_unit_cost_returns_a_reason_not_zero(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
    sample_unit_pv: pd.Series,
) -> None:
    """**참고단가를 만들지 않는다.** 단가가 없으면 0원이 아니라 사유다."""
    curve = solar_curve(
        sample_usage,
        tariff,
        CURRENT,
        sample_unit_pv,
        max_capacity_kwp=300.0,
        steps=2,
        baseline=sample_bill,
        quality=sample_report,
    )
    assert not curve.is_priced
    assert all(point.investment_won is None for point in curve.points)
    assert all(point.payback_years is None for point in curve.points)
    assert curve.best_payback is None
    # 절감액은 유효하다 — 단가를 몰라도 요금 계산은 확정된다.
    assert curve.points[-1].total_saving_won > 0
    assert any("참고값은 제공하지 않습니다" in note for note in texts(curve.notices))
    assert PvCostInput.unpriced().reason == PV_UNPRICED_REASON


def test_cost_basis_and_scale_economy_are_stated(sample_curve: SolarCurve) -> None:
    """kWp 가 DC 정격임을, 그리고 규모의 경제를 반영하지 않았음을 밝힌다."""
    notes = "\n".join(texts(sample_curve.notices))
    assert "모듈 직류(DC) 정격" in notes
    assert "인버터 용량(kW-ac)과 다릅니다" in notes
    assert "부대비용" in notes
    assert "규모의 경제는 반영하지 않았습니다" in notes


def test_investment_is_linear_in_capacity(sample_curve: SolarCurve) -> None:
    """단일 단가 가정이므로 투자비는 용량에 **정확히 선형**이다.

    이 선형성이 곧 규모의 경제 미반영의 정체다. 주석과 짝을 이룬다.
    """
    priced = [
        point
        for point in sample_curve.points
        if point.investment_won is not None and point.capacity_kwp > 0
    ]
    ratios = {round((point.investment_won or 0.0) / point.capacity_kwp, 6) for point in priced}
    assert len(ratios) == 1


def test_both_cost_paths_cannot_be_given() -> None:
    with pytest.raises(ValueError, match="함께 줄 수 없습니다"):
        PvCostInput(unit_cost_won_per_kwp=1_000_000.0, total_won=5_000_000.0)


# ===================================================================== 용량 판정 (16세션 0-4)


def _point(capacity: float, *, saving: float, investment: float, payback: float) -> SolarPoint:
    """판정만 시험하는 최소 점. 시계열은 필요 없다."""
    return SolarPoint(
        capacity_kwp=capacity,
        generation_kwh=capacity * 1_200.0,
        self_consumed_kwh=capacity * 1_200.0,
        surplus_kwh=0.0,
        self_consumption_ratio=1.0,
        billing_demand_kw=5_000.0,
        base_saving_won=saving * 0.2,
        energy_saving_won=saving * 0.8,
        total_saving_won=saving,
        annual_saving_won=saving,
        investment_won=investment,
        payback_years=payback,
        power_factor_after_pct=92.0,
        power_factor_extra_won=0.0,
    )


def _curve(points: tuple[SolarPoint, ...]) -> SolarCurve:
    return SolarCurve(
        points=points,
        selection=TariffSelection("general_b", "high_a", "I"),
        baseline_total_won=0.0,
        baseline_base_won=0.0,
        baseline_energy_won=0.0,
        sharpness=1.0,
        max_capacity_kwp=points[-1].capacity_kwp,
        cost=PvCostInput(unit_cost_won_per_kwp=1_500_000.0),
        base_fee_months=12.0,
    )


def test_평평한_회수기간에서는_상한을_고른다() -> None:
    """**첫 단계를 최적이라 답하던 자리다** (16세션 0-4).

    kWp 당 단가면 투자비와 절감액이 함께 비례해 회수기간이 거의 같아진다.
    실측에서 8 kWp 8.060년 · 160 kWp 8.114년이었고, 최소점을 그대로 고르니
    **상한의 1/20** 이 최적으로 나왔다.
    """
    points = tuple(
        _point(
            capacity,
            saving=1_488_896.0 * capacity / 8.0,
            investment=1_500_000.0 * capacity,
            payback=8.060 + 0.054 * (capacity - 8.0) / 152.0,
        )
        for capacity in (8.0, 40.0, 80.0, 120.0, 160.0)
    )
    verdict = _curve(points).verdict()
    assert verdict.basis == "회수기간"
    assert verdict.best is not None
    assert verdict.best.capacity_kwp == 160.0
    assert verdict.at_limit
    assert not verdict.show_curve


def test_회수기간이_실제로_꺾이면_최소점을_고른다() -> None:
    """잉여가 생기면 회수기간이 동률 폭을 벗어난다 — U곡선 판정은 그대로다."""
    paybacks = {8.0: 9.0, 40.0: 7.5, 80.0: 7.0, 120.0: 9.5, 160.0: 14.0}
    points = tuple(
        _point(
            capacity,
            saving=1_500_000.0 * capacity / paybacks[capacity] / 10.0,
            investment=1_500_000.0 * capacity,
            payback=paybacks[capacity],
        )
        for capacity in paybacks
    )
    verdict = _curve(points).verdict()
    assert verdict.best is not None
    assert verdict.best.capacity_kwp == 80.0
    assert not verdict.at_limit
    assert verdict.show_curve


def test_동률_폭은_기준_데이터에서_온다() -> None:
    """**코드에 기본값을 두지 않는다** (요구사항서 12장)."""
    from kwise.measures.solar import payback_tie_ratio

    assert 0.0 < payback_tie_ratio() < 0.5


# ===================================================================== 48세션 · 잉여 합산
#
# **41세션이 자리만 옮기고 금액을 합치지 않았다.** 잉여 활용(7.7)을 개선안에서
# 빼고 태양광 카드 안으로 넣었는데, 태양광의 절감액·회수기간은 자가소비분만
# 보고 있었다 — 화면에 **더해지지 않는 두 수**가 남았다.
#
# 14세션의 「경제성DR·잉여 활용은 합산효과에 넣지 않는다」 는 잉여가 **독립
# 개선안**이던 시절의 결정이다. 41세션에 전제가 사라졌다. **차익거래는 계속
# 뺀다** — 그쪽은 「그날 피크에 쓸 몫을 남기는 운전 규칙이 없다」 가 살아 있다.


def test_잉여_수익은_고른_경우에만_더한다(surplus_case: SurplusCase, tariff: TariffTable) -> None:
    """**기본값 없음을 지킨다** (41세션 2-2). 안 고르면 아무것도 더하지 않는다."""
    point = solar_point(
        surplus_case.usage,
        tariff,
        CURRENT,
        surplus_case.unit,
        surplus_case.capacity_kwp,
        cost=PvCostInput.of_unit_cost(2_000_000.0),
    )
    assert point.surplus_scenario == ""
    assert point.surplus_revenue_won == 0.0
    # 고르지 않은 상태 — 시나리오 이름이 비면 그대로 돌려준다.
    untouched = with_surplus_revenue(point, revenue_won=1_000.0, scenario="", base_fee_months=1.0)
    assert untouched is point


def test_잉여_수익이_절감액과_회수기간에_실린다(
    surplus_case: SurplusCase, tariff: TariffTable
) -> None:
    """**두 수가 하나가 되어야 한다** (48세션).

    소형 사무빌딩 자료에서 절감액 2,543만원과 잉여 수익 241만원이 따로 놀았고,
    회수기간 12.6년은 앞의 것만 본 값이었다. 더하면 11.5년이다.
    """
    point = solar_point(
        surplus_case.usage,
        tariff,
        CURRENT,
        surplus_case.unit,
        surplus_case.capacity_kwp,
        cost=PvCostInput.of_unit_cost(2_000_000.0),
    )
    assert point.payback_years is not None
    combined = with_surplus_revenue(
        point, revenue_won=1_000_000.0, scenario=OFFSET_SCENARIO, base_fee_months=1.0
    )
    assert combined.surplus_scenario == OFFSET_SCENARIO
    assert combined.surplus_revenue_won == 1_000_000.0
    assert combined.total_saving_won == pytest.approx(point.total_saving_won + 1_000_000.0)
    # **자가소비분은 그대로 꺼낼 수 있다** — 툴팁이 이 값으로 가른다.
    assert combined.self_consumption_saving_won == pytest.approx(point.total_saving_won)
    # 더한 만큼 회수기간이 짧아진다.
    assert combined.payback_years is not None
    assert combined.payback_years < point.payback_years
    assert combined.investment_won == point.investment_won


def test_금액을_못_내면_더하지_않는다(surplus_case: SurplusCase, tariff: TariffTable) -> None:
    """단가를 넣지 않은 외부 판매다. **지어낸 0원을 절감액에 넣지 않는다.**"""
    point = solar_point(
        surplus_case.usage,
        tariff,
        CURRENT,
        surplus_case.unit,
        surplus_case.capacity_kwp,
        cost=PvCostInput.of_unit_cost(2_000_000.0),
    )
    combined = with_surplus_revenue(
        point, revenue_won=None, scenario=EXTERNAL_SCENARIO, base_fee_months=1.0
    )
    assert combined.surplus_revenue_won == 0.0
    assert combined.total_saving_won == point.total_saving_won
    # 이름은 남는다 — 「고르지 않음」 과 「골랐지만 금액을 못 냄」 은 다르다.
    assert combined.surplus_scenario == EXTERNAL_SCENARIO


def test_합산효과에_잉여를_더한다(surplus_case: SurplusCase, tariff: TariffTable) -> None:
    """**14세션의 결정을 뒤집는다** (48세션). 전제가 41세션에 사라졌다."""
    baseline = calculate_bill(surplus_case.usage, tariff, CURRENT)
    spec = CombinationSpec(
        name="태양광",
        selection=CURRENT,
        pv_capacity_kwp=surplus_case.capacity_kwp,
        pv_unit_cost_won_per_kwp=2_000_000.0,
    )
    plain = evaluate_combination(
        surplus_case.usage,
        tariff,
        spec,
        baseline_bill=baseline,
        unit_pv_kw_per_kwp=surplus_case.unit,
    )
    with_revenue = evaluate_combination(
        surplus_case.usage,
        tariff,
        replace(spec, surplus_revenue_won=1_000_000.0, surplus_scenario=OFFSET_SCENARIO),
        baseline_bill=baseline,
        unit_pv_kw_per_kwp=surplus_case.unit,
    )
    assert plain.surplus_revenue_won == 0.0
    assert with_revenue.surplus_revenue_won == 1_000_000.0
    assert with_revenue.saving_won == pytest.approx(plain.saving_won + 1_000_000.0)
    # **표의 「요금」 과 「절감액」 이 기준선으로 되돌아가야 한다.**
    assert baseline.total_won - with_revenue.total_won == pytest.approx(with_revenue.saving_won)
    facts = {notice.fact for notice in with_revenue.notices}
    assert "combination.surplus_revenue" in facts


def test_태양광이_없으면_잉여도_더하지_않는다(
    surplus_case: SurplusCase, tariff: TariffTable
) -> None:
    """**잉여는 태양광의 결과다** (41세션 2절). 켜지 않은 수단의 수익은 없다."""
    baseline = calculate_bill(surplus_case.usage, tariff, CURRENT)
    result = evaluate_combination(
        surplus_case.usage,
        tariff,
        CombinationSpec(
            name="현행",
            selection=CURRENT,
            surplus_revenue_won=1_000_000.0,
            surplus_scenario=OFFSET_SCENARIO,
        ),
        baseline_bill=baseline,
    )
    assert result.surplus_revenue_won == 0.0
    assert result.saving_won == pytest.approx(0.0)
