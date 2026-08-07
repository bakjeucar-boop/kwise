"""개선 수단 평가 (요구사항서 7장, 11.3, 부록 B).

수단마다 요금을 다시 계산한다. 빼기로 어림한 값이 아니라는 것을 여러 곳에서 확인한다.
"""

from __future__ import annotations

import inspect
import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest

from kwise.diagnose import Diagnosis
from kwise.io import UsageData, load_usage
from kwise.measures import (
    Certainty,
    ContractStatus,
    EssResult,
    NetLoad,
    SolarCurve,
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
    roof_capacity_limit_kwp,
    size_for_target,
    solar_curve,
    unit_generation_kw,
    with_load,
)
from kwise.pv import ArrayConfig, PvSystemConfig
from kwise.quality import QualityReport
from kwise.tariff import BillingResult, TariffSelection, TariffTable, calculate_bill
from tests._synthetic import clearsky_weather, write_month

CURRENT = TariffSelection("general_b", "high_a", "I")
BEST = TariffSelection("general_b", "high_a", "II")
INTERVAL = 15
CELL_COST_WON_PER_KWH = 400_000.0
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


def test_switch_details_only_for_the_two_baselines(sample_switch: TariffSwitchResult) -> None:
    """상세(기본/전력량 분해)는 현행·최적 둘만. 나머지는 합계만 든다."""
    priced = [quote for quote in sample_switch.quotes if quote.base_won is not None]
    assert {quote.selection for quote in priced} == {CURRENT, BEST}
    for quote in priced:
        assert quote.base_won is not None
        assert quote.energy_won is not None
        assert quote.base_won + quote.energy_won == pytest.approx(quote.total_won)


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
    assert any("하한 규정" in message for message in result.warnings)


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
    assert any("걸리지 않아" in note for note in result.notes)


def test_penalty_warning_is_always_returned(
    sample_usage: UsageData, sample_bill: BillingResult
) -> None:
    for ratio in (None, 1.0):
        result = evaluate_contract_adjustment(
            sample_usage, sample_bill, contract_kw=7_000.0, contract_floor_ratio=ratio
        )
        assert any("위약금" in message for message in result.warnings)
        assert any("12개월간 적용" in message for message in result.warnings)


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
        unit_cost_won_per_kwp=PV_COST_WON_PER_KWP,
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
        unit_cost_won_per_kwp=PV_COST_WON_PER_KWP,
        steps=1,
        baseline=sample_bill,
        quality=sample_report,
    )
    point = curve.points[-1]
    assert point.power_factor_after_pct < 92.0
    assert point.power_factor_extra_won > 0  # 92% 미만이므로 추가요금이다
    assert point.saving_after_power_factor_won < point.total_saving_won
    assert any("콘덴서" in message for message in curve.warnings)
    assert any("제41·43조" in message for message in curve.warnings)


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
    for factor in (0.70, 1.00, 1.20):
        switch_savings.add(
            evaluate_tariff_switch(sample_usage, tariff, CURRENT, quality=sample_report).saving_won
        )
        curve = solar_curve(
            sample_usage,
            tariff,
            CURRENT,
            sample_unit_pv,
            max_capacity_kwp=300.0,
            unit_cost_won_per_kwp=PV_COST_WON_PER_KWP,
            steps=1,
            sensitivity_factor=factor,
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
    result = sample_ess
    assert result.power_kw == pytest.approx(93.4, abs=0.1)
    assert result.capacity_kwh == pytest.approx(41.1, abs=0.5)
    assert result.investment_won == pytest.approx(result.capacity_kwh * CELL_COST_WON_PER_KWH)
    assert result.total_saving_won > 0
    assert result.payback_years is not None
    assert result.certainty is Certainty.MEDIUM_LOW


def test_breakeven_unit_cost_is_reversed_from_ten_years(sample_ess: EssResult) -> None:
    """회수기간 10년이 되는 단가를 역산한다. '경제성 없음' 도 의사결정 자료가 된다."""
    result = sample_ess
    expected = result.annual_saving_won * 10.0 / result.capacity_kwh
    assert result.breakeven_unit_cost_won_per_kwh == pytest.approx(expected)
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
        unit_cost_won_per_kwh=CELL_COST_WON_PER_KWH,
        charge_mask=mask,
        power_kw=50.0,
        capacity_kwh=50.0,
        baseline=sample_bill,
        quality=sample_report,
    )
    assert not result.dispatch.target_met
    assert result.dispatch.achieved_peak_kw > 5_000.0
    assert any("지키지 못한" in message for message in result.warnings)


# --------------------------------------------------------------------- 7.5 잉여 활용


@dataclass(frozen=True, eq=False)
class SurplusCase:
    """부하보다 큰 PV. 잉여가 실제로 생기는 경우."""

    usage: UsageData
    net: NetLoad


@pytest.fixture(scope="module")
def surplus_case(tmp_path_factory: pytest.TempPathFactory) -> SurplusCase:
    path = write_month(tmp_path_factory.mktemp("surplus") / "small.csv", 2023, 7, kwh=25.0)
    usage = load_usage(path)  # 100 kW 균일 부하
    weather = clearsky_weather(start="2023-06-30", end="2023-08-01")
    config = PvSystemConfig(
        37.5, 127.0, arrays=(ArrayConfig.roof("지붕", 1_000.0),), altitude_m=50.0
    )
    unit = unit_generation_kw(usage, weather, config)
    return SurplusCase(usage=usage, net=apply_generation(usage, unit * 1_000.0))


def test_surplus_is_split_from_self_consumption(surplus_case: SurplusCase) -> None:
    net = surplus_case.net
    assert net.generated_kwh > 0
    assert net.surplus_kwh > 0
    assert net.self_consumed_kwh == pytest.approx(net.generated_kwh - net.surplus_kwh)
    assert 0 < (net.self_consumption_ratio or 0) < 1
    assert float(net.usage.kw.min()) >= 0.0  # 계통 사용량은 음수가 될 수 없다


def test_surplus_scenarios(surplus_case: SurplusCase, tariff: TariffTable) -> None:
    usage, net = surplus_case.usage, surplus_case.net
    result = evaluate_surplus(
        usage,
        tariff,
        CURRENT,
        net.surplus_kw,
        generation_kwh=net.generated_kwh,
        external_price_won_per_kwh=90.0,
    )
    assert result.total_kwh == pytest.approx(net.surplus_kwh)
    assert result.scenario("버림").revenue_won == 0.0
    offset = result.scenario("상계거래")
    assert offset.revenue_won is not None
    assert offset.revenue_won > 0
    # 상계 단가는 요금표에서 나온다. 경부하(92.8)~최대부하(227.8) 사이여야 한다
    assert 92.8 <= offset.revenue_won / result.total_kwh <= 227.8
    external = result.scenario("외부 신재생에너지 구매 연계")
    assert external.revenue_won == pytest.approx(result.total_kwh * 90.0)


def test_external_price_is_not_invented(surplus_case: SurplusCase, tariff: TariffTable) -> None:
    usage, net = surplus_case.usage, surplus_case.net
    result = evaluate_surplus(
        usage, tariff, CURRENT, net.surplus_kw, generation_kwh=net.generated_kwh
    )
    external = result.scenario("외부 신재생에너지 구매 연계")
    assert external.revenue_won is None
    assert not external.is_priced
    assert "지어내지 않습니다" in external.basis


def test_eligibility_is_not_judged(surplus_case: SurplusCase, tariff: TariffTable) -> None:
    """자격요건은 판정하지 않는다. 금액만 제시하고 확인 필요를 명시한다."""
    usage, net = surplus_case.usage, surplus_case.net
    result = evaluate_surplus(
        usage, tariff, CURRENT, net.surplus_kw, generation_kwh=net.generated_kwh
    )
    assert any("자격요건" in note for note in result.notes)
    assert all(scenario.admin_burden for scenario in result.scenarios)


def test_surplus_hour_distribution_is_midday(
    surplus_case: SurplusCase, tariff: TariffTable
) -> None:
    usage, net = surplus_case.usage, surplus_case.net
    result = evaluate_surplus(
        usage, tariff, CURRENT, net.surplus_kw, generation_kwh=net.generated_kwh
    )
    distribution = result.hour_distribution
    assert int(distribution.idxmax()) in range(11, 15)
    assert distribution.loc[3] == 0.0  # 새벽에는 잉여가 없다
    assert result.share_of_generation is not None
    assert result.weekend_share is not None


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
