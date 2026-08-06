"""조합 비교 (요구사항서 8장, 9.2).

조합의 절감액은 수단별 절감액의 합이 아니다. 그 사실을 숫자로 확인한다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from kwise.compare import (
    CombinationSpec,
    ComparisonResult,
    compare_combinations,
    default_combinations,
    evaluate_combination,
    sensitivity_comparison,
)
from kwise.io import UsageData
from kwise.measures import Certainty, dispatch_peak_shaving, lowest_certainty
from kwise.quality import QualityReport
from kwise.tariff import BillingResult, TariffSelection, TariffTable

CURRENT = TariffSelection("general_b", "high_a", "I")
BEST = TariffSelection("general_b", "high_a", "II")
PV_KWP = 500.0
PV_COST = 1_200_000.0
ESS_TARGET = 5_000.0
ESS_COST = 400_000.0


# --------------------------------------------------------------------- 재계산


def test_combination_saving_is_not_the_sum_of_measures(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
    sample_unit_pv: pd.Series,
) -> None:
    """요금제 전환 절감 + 태양광 절감 ≠ 둘을 함께 넣은 절감.

    태양광이 사용량을 줄이면 선택요금별 유불리가 달라지기 때문이다.
    조합마다 요금을 다시 계산해야 이 차이가 잡힌다.
    """
    kwargs = {
        "baseline_bill": sample_bill,
        "unit_pv_kw_per_kwp": sample_unit_pv,
        "quality": sample_report,
    }
    switch_only = evaluate_combination(
        sample_usage, tariff, CombinationSpec("요금제만", BEST), **kwargs
    )
    pv_only = evaluate_combination(
        sample_usage,
        tariff,
        CombinationSpec("태양광만", CURRENT, pv_capacity_kwp=PV_KWP),
        **kwargs,
    )
    both = evaluate_combination(
        sample_usage,
        tariff,
        CombinationSpec("요금제+태양광", BEST, pv_capacity_kwp=PV_KWP),
        **kwargs,
    )

    naive_sum = switch_only.saving_won + pv_only.saving_won
    assert both.saving_won != pytest.approx(naive_sum, rel=1e-6)
    # 단순 합보다 작다 — 태양광이 사용량을 줄이면 선택요금 전환의 이득이 줄어든다
    assert both.saving_won < naive_sum
    assert abs(naive_sum - both.saving_won) > 1_000_000


def test_baseline_has_no_saving(sample_comparison: ComparisonResult) -> None:
    baseline = sample_comparison.baseline
    assert baseline.saving_won == pytest.approx(0.0)
    assert baseline.investment_won == 0.0
    assert baseline.payback_years is None


def test_pv_reduces_the_billing_demand(sample_comparison: ComparisonResult) -> None:
    demands = [item.billing_demand_kw for item in sample_comparison.combinations]
    assert demands[0] == pytest.approx(5_293.44)  # 기준선
    assert demands[-1] == pytest.approx(ESS_TARGET)  # ESS 가 목표까지 깎는다
    assert demands[2] < demands[0]  # 태양광만으로도 조금 내려간다


def test_measures_are_applied_in_series(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
    sample_unit_pv: pd.Series,
) -> None:
    """PV → ESS 순서로 물린다. ESS 는 PV 를 뺀 부하에서 목표를 잡는다."""
    result = evaluate_combination(
        sample_usage,
        tariff,
        CombinationSpec("PV+ESS", BEST, pv_capacity_kwp=PV_KWP, ess_target_kw=ESS_TARGET),
        baseline_bill=sample_bill,
        unit_pv_kw_per_kwp=sample_unit_pv,
        quality=sample_report,
    )
    assert result.dispatch is not None
    assert result.generation_kwh > 0
    assert result.dispatch.target_met
    assert result.billing_demand_kw == pytest.approx(ESS_TARGET)
    # PV 가 먼저 깎았으므로 ESS 가 감당할 초과 에너지가 줄어든다
    pv_free = evaluate_combination(
        sample_usage,
        tariff,
        CombinationSpec("ESS만", BEST, ess_target_kw=ESS_TARGET),
        baseline_bill=sample_bill,
        quality=sample_report,
    )
    assert pv_free.dispatch is not None
    assert result.dispatch.discharged_kwh < pv_free.dispatch.discharged_kwh


def test_pv_capacity_without_profile_raises(
    sample_usage: UsageData, tariff: TariffTable, sample_bill: BillingResult
) -> None:
    with pytest.raises(ValueError, match="단위 발전 프로파일"):
        evaluate_combination(
            sample_usage,
            tariff,
            CombinationSpec("태양광", BEST, pv_capacity_kwp=100.0),
            baseline_bill=sample_bill,
        )


# --------------------------------------------------------------------- 확실성


def test_lowest_certainty_wins() -> None:
    assert lowest_certainty([Certainty.HIGH]) is Certainty.HIGH
    assert lowest_certainty([Certainty.HIGH, Certainty.MEDIUM]) is Certainty.MEDIUM
    assert (
        lowest_certainty([Certainty.HIGH, Certainty.MEDIUM, Certainty.MEDIUM_LOW])
        is Certainty.MEDIUM_LOW
    )
    assert lowest_certainty([]) is Certainty.HIGH  # 확정 계산만 있다


def test_combination_certainty_follows_the_lowest_component(
    sample_comparison: ComparisonResult,
) -> None:
    """조합의 등급은 가장 낮은 구성 요소를 따른다."""
    grades = {item.name: item.certainty for item in sample_comparison.combinations}
    assert grades["기준선 (현행)"] is Certainty.HIGH
    assert grades["선택요금 전환"] is Certainty.HIGH
    assert grades[f"+ 태양광 {PV_KWP:,.0f} kWp"] is Certainty.MEDIUM
    assert grades[f"+ ESS 목표 {ESS_TARGET:,.0f} kW"] is Certainty.MEDIUM_LOW


def test_certainty_reaches_the_comparison_table(sample_comparison: ComparisonResult) -> None:
    frame = sample_comparison.frame()
    assert list(frame["확실성"]) == ["높음", "높음", "중간", "중간~낮음"]


# --------------------------------------------------------------------- ESS 야간 피크


def synthetic_load() -> pd.Series:
    """야간 기저 400 kW, 정오 스파이크 1,000 kW 인 사흘치."""
    index = pd.date_range("2024-03-01 00:15", periods=96 * 3, freq="15min")
    load = pd.Series(400.0, index=index)
    load.loc["2024-03-01 12:00":"2024-03-01 13:00"] = 1_000.0
    return load


def night_mask(index: pd.DatetimeIndex) -> pd.Series:
    return pd.Series(index.hour.isin([*range(22, 24), *range(0, 8)]), index=index)


def test_charging_can_create_a_new_night_peak() -> None:
    """경부하 충전이 기저부하에 얹히면 야간 피크가 생긴다. 그 사실을 잡아낸다."""
    load = synthetic_load()
    naive = dispatch_peak_shaving(
        load,
        target_kw=500.0,
        power_kw=2_000.0,
        capacity_kwh=5_000.0,
        charge_mask=night_mask(pd.DatetimeIndex(load.index)),
        interval_minutes=15,
        respect_target_when_charging=False,  # 목표를 무시하고 출력껏 충전한다
    )
    assert naive.charge_created_new_peak
    assert naive.charge_window_peak_kw == pytest.approx(2_400.0)
    assert naive.charge_window_rise_kw == pytest.approx(2_000.0)


def test_default_dispatch_never_creates_a_new_peak() -> None:
    load = synthetic_load()
    safe = dispatch_peak_shaving(
        load,
        target_kw=500.0,
        power_kw=2_000.0,
        capacity_kwh=5_000.0,
        charge_mask=night_mask(pd.DatetimeIndex(load.index)),
        interval_minutes=15,
    )
    assert not safe.charge_created_new_peak
    assert safe.charge_window_peak_kw == pytest.approx(500.0)


def test_charge_limit_suppresses_the_night_peak() -> None:
    load = synthetic_load()
    limited = dispatch_peak_shaving(
        load,
        target_kw=500.0,
        power_kw=2_000.0,
        capacity_kwh=5_000.0,
        charge_mask=night_mask(pd.DatetimeIndex(load.index)),
        interval_minutes=15,
        respect_target_when_charging=False,
        charge_limit_kw=50.0,
    )
    assert not limited.charge_created_new_peak
    assert limited.charge_window_peak_kw == pytest.approx(450.0)


def test_comparison_warns_when_charging_creates_a_peak(
    synthetic_usage: UsageData, tariff: TariffTable
) -> None:
    """조합 비교가 야간 피크를 경고로 올린다."""
    spec = CombinationSpec(
        "ESS 무제한 충전",
        CURRENT,
        ess_target_kw=450.0,
        ess_power_kw=2_000.0,
        ess_capacity_kwh=5_000.0,
        ess_respect_target_when_charging=False,
    )
    result = compare_combinations(
        synthetic_usage, tariff, (CombinationSpec("기준선", CURRENT), spec)
    )
    assert any("새 피크" in message for message in result.warnings)
    assert any("ess_charge_limit_kw" in message for message in result.warnings)


def test_charge_limit_removes_the_warning(synthetic_usage: UsageData, tariff: TariffTable) -> None:
    spec = CombinationSpec(
        "ESS 충전 제한",
        CURRENT,
        ess_target_kw=450.0,
        ess_power_kw=2_000.0,
        ess_capacity_kwh=5_000.0,
        ess_respect_target_when_charging=False,
        ess_charge_limit_kw=20.0,
    )
    result = compare_combinations(
        synthetic_usage, tariff, (CombinationSpec("기준선", CURRENT), spec)
    )
    assert not any("새 피크" in message for message in result.warnings)


# --------------------------------------------------------------------- 기본 세트·표


def test_default_set_is_ordered_by_investment() -> None:
    specs = default_combinations(
        current_selection=CURRENT,
        best_selection=BEST,
        pv_capacity_kwp=PV_KWP,
        ess_target_kw=ESS_TARGET,
    )
    assert [spec.name for spec in specs] == [
        "기준선 (현행)",
        "선택요금 전환",
        f"+ 태양광 {PV_KWP:,.0f} kWp",
        f"+ ESS 목표 {ESS_TARGET:,.0f} kW",
    ]
    assert specs[0].selection == CURRENT
    assert all(spec.selection == BEST for spec in specs[1:])
    assert specs[-1].has_pv and specs[-1].has_ess


def test_default_set_skips_measures_that_are_off() -> None:
    specs = default_combinations(current_selection=CURRENT, best_selection=BEST)
    assert len(specs) == 2
    assert not any(spec.has_pv or spec.has_ess for spec in specs)


def test_comparison_frame_has_the_required_columns(
    sample_comparison: ComparisonResult,
) -> None:
    """요구사항서 8장 표 — 조합 | 절감액 | 투자비 | 회수기간 | 확실성."""
    frame = sample_comparison.frame()
    for column in ("절감액(원)", "투자비(원)", "회수기간(년)", "확실성"):
        assert column in frame.columns
    assert frame.index.name == "조합"
    assert len(frame) == 4
    assert any("합이 아닙니다" in note for note in sample_comparison.notes)


def test_empty_specs_raise(sample_usage: UsageData, tariff: TariffTable) -> None:
    with pytest.raises(ValueError, match="비교할 조합이 없습니다"):
        compare_combinations(sample_usage, tariff, ())


# --------------------------------------------------------------------- 감도 (9.2)


def test_sensitivity_moves_pv_but_not_the_tariff_switch(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
    sample_unit_pv: pd.Series,
) -> None:
    """감도는 PV 출력에만 붙는다. 요금제 전환만인 조합은 세 시나리오가 같다."""
    with_pv = sensitivity_comparison(
        sample_usage,
        tariff,
        CombinationSpec("PV", BEST, pv_capacity_kwp=PV_KWP),
        baseline_bill=sample_bill,
        unit_pv_kw_per_kwp=sample_unit_pv,
        quality=sample_report,
    )
    assert list(with_pv.index) == ["보수", "기준", "낙관"]
    assert list(with_pv["계수"]) == [0.70, 1.00, 1.20]
    assert with_pv["발전량(kWh)"].nunique() == 3
    assert with_pv["절감액(원)"].nunique() == 3
    assert with_pv["절감액(원)"].is_monotonic_increasing

    without_pv = sensitivity_comparison(
        sample_usage,
        tariff,
        CombinationSpec("요금제만", BEST),
        baseline_bill=sample_bill,
        quality=sample_report,
    )
    assert without_pv["절감액(원)"].nunique() == 1  # 확정 계산이라 감도와 무관하다
    assert set(without_pv["발전량(kWh)"]) == {0.0}
