"""조합 비교 (요구사항서 8장, 9.2).

조합의 절감액은 수단별 절감액의 합이 아니다. 그 사실을 숫자로 확인한다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from kwise.compare import (
    SCENARIO_NAME_CAVEAT,
    CombinationSpec,
    ComparisonResult,
    compare_combinations,
    default_combinations,
    evaluate_combination,
    sensitivity_comparison,
    sensitivity_range_frame,
    sensitivity_ranges,
)
from kwise.io import UsageData
from kwise.measures import Certainty, dispatch_peak_shaving, lowest_certainty
from kwise.measures.solar import power_factor_after_pct, power_factor_floor_pct
from kwise.notices import texts
from kwise.quality import QualityReport
from kwise.tariff import BillingResult, TariffSelection, TariffTable, deemed_lagging_pct

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


def _after_pct(usage: UsageData, unit: pd.Series, capacity: float, start: float) -> float:
    """그 용량에서 PV 가 떨어뜨린 주간 지상역률."""
    generation = unit.reindex(pd.DatetimeIndex(usage.kw.index)).fillna(0.0) * capacity
    return power_factor_after_pct(
        usage.kw,
        generation,
        power_factor_pct=start,
        interval_minutes=usage.meta.interval_minutes,
    )


def test_조합이_태양광이_떨어뜨린_역률로_요금을_다시_계산한다(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
    sample_unit_pv: pd.Series,
) -> None:
    """**PV 는 유효전력만 상쇄하므로 역률이 떨어진다** (78세션 2절).

    5세션이 :func:`power_factor_after_pct` 를 세웠는데 14세션이 조합을 지으며
    잇지 않았다 — 조합 요금은 ``BillingOptions.power_factor_pct`` 하나만 봤고
    **뺀다는 결정은 기록에 없다** (76세션 조사). 대형 을 자료에서 조합 절감액이
    2,095,077원/년(0.64%) 부풀어 있었다.

    **떨어진 뒤에서 개선이 시작한다** (77세션에 사람이 정했다 — 갈래 ㄴ).
    목표 97% 는 PV 전 값이고 PV 가 그것을 끌어내린다. 도구가 설비 크기를
    모르므로(투자비가 사용자 입력이다) 「악화분까지 끌어올린다」 로 두면
    **더 큰 설비를 값 없이** 가정하는 셈이 된다.

    **이 시험은 조합이 실제로 쓴 역률을 본다** — 「돌아간다」 가 아니다.
    59세션이 `test_slides.py` 에 박아 둔 못
    (`test_합산효과는_태양광_역률_영향을_반영하지_않는다`)이 **그때의 상태**를
    소스 글자로 기록한 것이라, 78세션에 사실이 뒤집히면서 지우고 이리로 옮겼다.
    """
    kwargs = {
        "baseline_bill": sample_bill,
        "unit_pv_kw_per_kwp": sample_unit_pv,
        "quality": sample_report,
    }
    # ① 역률 수단을 끈 조합 — 끌어올릴 주체가 없다. 갈래가 아예 없는 자리다.
    off = evaluate_combination(
        sample_usage, tariff, CombinationSpec("태양광", CURRENT, pv_capacity_kwp=PV_KWP), **kwargs
    )
    start_off = deemed_lagging_pct()
    after_off = _after_pct(sample_usage, sample_unit_pv, PV_KWP, start_off)
    assert after_off < start_off, "PV 를 넣었는데 역률이 안 떨어졌습니다."
    assert off.bill.power_factor.lagging_pct == pytest.approx(after_off)

    # ② 역률 수단을 켠 조합 — 목표에서 시작해 PV 가 끌어내린다 (ㄴ).
    on = evaluate_combination(
        sample_usage,
        tariff,
        CombinationSpec("역률+태양광", CURRENT, pv_capacity_kwp=PV_KWP, power_factor_pct=97.0),
        **kwargs,
    )
    after_on = _after_pct(sample_usage, sample_unit_pv, PV_KWP, 97.0)
    assert 97.0 > after_on > after_off, "목표에서 떨어져 시작하는 것이 아닙니다."
    assert on.bill.power_factor.lagging_pct == pytest.approx(after_on)
    # **목표에 못 미쳐도 개선은 개선이다** — 켠 쪽이 그래도 돈이 된다.
    assert on.saving_won > off.saving_won


def test_역률이_기준_아래로_떨어지면_조합이_말한다(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
    sample_unit_pv: pd.Series,
) -> None:
    """**뜨지 않는 경고는 없는 경고와 같다** (78세션 1절 · 46세션 가장자리 경고).

    기준(92%) 아래로 내려가면 **추가요금이 실제로 붙는 구간**인데 조합이 그
    사실을 말하지 않았다 — 결함 유형 ①·② 가 겹친 자리다.

    **문구는 2단계 태양광 카드가 쓰던 것을 그대로 쓴다**
    (:func:`~kwise.measures.solar.power_factor_drop_warning`). 어휘가 두 벌이면
    한쪽만 고쳐진다 (결함 유형 ③).
    """
    kwargs = {
        "baseline_bill": sample_bill,
        "unit_pv_kw_per_kwp": sample_unit_pv,
        "quality": sample_report,
    }

    def facts(capacity: float, target: float | None = None) -> list[str]:
        result = evaluate_combination(
            sample_usage,
            tariff,
            CombinationSpec(
                "태양광", CURRENT, pv_capacity_kwp=capacity, power_factor_pct=target
            ),
            **kwargs,
        )
        return [item.text for item in result.notices if item.fact == "solar.power_factor_drop"]

    # ① 기준 아래로 내려가면 말한다.
    below = facts(PV_KWP)
    assert _after_pct(sample_usage, sample_unit_pv, PV_KWP, deemed_lagging_pct()) < (
        power_factor_floor_pct()
    )
    assert len(below) == 1, below
    assert "밑돕니다" in below[0] and "역률 개선 설비" in below[0]
    # **금액은 조합 쪽에 적지 않는다** — 이미 떨어진 역률로 요금을 냈으므로
    # 견줄 앞값이 없다. 2단계 카드만 조정 전후를 나란히 놓는다 (31세션).
    assert "절감액이" not in below[0]

    # ② 태양광이 없으면 뜰 일이 없다.
    assert facts(0.0) == []

    # ③ **기준 위로 남으면 안 뜬다.** 뜨는 조건만 보면 늘 뜨는 경고가 된다.
    assert _after_pct(sample_usage, sample_unit_pv, PV_KWP, 97.0) >= power_factor_floor_pct()
    assert facts(PV_KWP, 97.0) == []


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


def test_확실성은_계산에만_남는다(sample_comparison: ComparisonResult) -> None:
    """**등급을 산출물에서 뺐다** (53세션 1-4). 계산은 그대로다.

    28세션에 화면에서 뺀 뒤로 Excel·Word 에만 남아 있었다 — 무엇에 대한
    등급인지 이름에 없어 「높음」 이 어느 정도인지 알 수 없는 값이다.
    **되살릴 수 있게 계산과 데이터는 남긴다.**
    """
    frame = sample_comparison.frame()
    assert "확실성" not in frame.columns
    assert [str(item.certainty) for item in sample_comparison.combinations] == [
        "높음",
        "높음",
        "중간",
        "중간~낮음",
    ]


# --------------------------------------------------------------------- ESS 야간 피크


def synthetic_load() -> pd.Series:
    """야간 기저 400 kW, 정오 스파이크 1,000 kW 인 사흘치."""
    index = pd.date_range("2024-03-01 00:15", periods=96 * 3, freq="15min")
    load = pd.Series(400.0, index=index)
    load.loc[pd.Timestamp("2024-03-01 12:00") : pd.Timestamp("2024-03-01 13:00")] = 1_000.0
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
    assert any("새 피크" in message for message in texts(result.notices))
    # **코드 식별자를 쓰지 않는다** (25세션 4-1). 무엇을 해야 하는지만 적는다.
    assert any("충전 전력을 목표 아래로" in message for message in texts(result.notices))


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
    assert not any("새 피크" in message for message in texts(result.notices))


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
    """요구사항서 8장 표 — 조합 | 절감액 | 투자비 | 회수기간 (53세션에 확실성을 뺐다)."""
    frame = sample_comparison.frame()
    for column in ("절감액(원)", "투자비(원)", "회수기간(년)"):
        assert column in frame.columns
    assert frame.index.name == "조합"
    assert len(frame) == 4
    # 33세션 6절에 문구를 다시 썼다 — 뜻은 그대로다.
    assert any("단순 합이 아니라" in note for note in texts(sample_comparison.notices))


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
    assert list(with_pv.index) == ["평탄형", "기준", "첨예형"]
    assert list(with_pv["첨예도 s"]) == [0.85, 1.00, 1.25]

    without_pv = sensitivity_comparison(
        sample_usage,
        tariff,
        CombinationSpec("요금제만", BEST),
        baseline_bill=sample_bill,
        quality=sample_report,
    )
    assert without_pv["절감액(원)"].nunique() == 1  # 확정 계산이라 감도와 무관하다
    assert set(without_pv["발전량(kWh)"]) == {0.0}


@pytest.fixture(scope="module")
def pv_sensitivity(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
    sample_unit_pv: pd.Series,
) -> pd.DataFrame:
    return sensitivity_comparison(
        sample_usage,
        tariff,
        CombinationSpec("PV", BEST, pv_capacity_kwp=PV_KWP),
        baseline_bill=sample_bill,
        unit_pv_kw_per_kwp=sample_unit_pv,
        quality=sample_report,
    )


def test_generation_is_preserved_across_scenarios(pv_sensitivity: pd.DataFrame) -> None:
    """총 발전량이 세 시나리오에서 같다. 첨예도만 바꿨기 때문이다 (9.2)."""
    generation = pv_sensitivity["발전량(kWh)"]
    assert float(generation.max() / generation.min() - 1.0) < 0.01


def test_energy_saving_barely_moves_but_base_fee_saving_does(
    pv_sensitivity: pd.DataFrame,
) -> None:
    """**감도가 흔들어야 할 것은 기본요금이다.**

    전력량요금 절감액은 총 발전량에 붙으므로 시나리오 간 차이가 5% 이내여야 하고,
    기본요금 절감액은 피크 시각의 출력에 붙으므로 그보다 훨씬 크게 벌어져야 한다.
    일률 계수를 곱하던 이전 방식은 둘을 함께 흔들어 감도의 뜻을 흐렸다.
    """

    def spread(column: str) -> float:
        values = pv_sensitivity[column]
        return float((values.max() - values.min()) / values.abs().max())

    energy_spread = spread("전력량요금 절감액(원)")
    base_spread = spread("기본요금 절감액(원)")
    assert energy_spread < 0.05
    assert base_spread > energy_spread * 5


def test_base_case_matches_the_unadjusted_combination(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
    sample_unit_pv: pd.Series,
    pv_sensitivity: pd.DataFrame,
) -> None:
    """s=1.0 시나리오는 감도를 적용하지 않은 조합과 완전히 같다."""
    plain = evaluate_combination(
        sample_usage,
        tariff,
        CombinationSpec("PV", BEST, pv_capacity_kwp=PV_KWP),
        baseline_bill=sample_bill,
        unit_pv_kw_per_kwp=sample_unit_pv,
        quality=sample_report,
    )
    assert pv_sensitivity.loc["기준", "절감액(원)"] == pytest.approx(plain.saving_won)
    assert pv_sensitivity.loc["기준", "발전량(kWh)"] == pytest.approx(plain.generation_kwh)


def test_scenario_names_describe_the_profile_not_the_saving(
    pv_sensitivity: pd.DataFrame,
) -> None:
    """**'낙관'이 절감액이 크다는 뜻이 아니다.**

    총량이 보존되므로 첨예도를 올리면 정오는 높아지지만 아침·저녁 어깨가 낮아진다.
    샘플은 최대수요가 09:30 인 오전 피크형이라 '낙관'의 기본요금 절감액이 오히려
    작다. 이름을 절감액으로 읽으면 결론이 뒤집히므로 안내 문구를 함께 낸다.
    """
    assert "좋고 나쁨의 축이" in SCENARIO_NAME_CAVEAT
    base_savings = pv_sensitivity["기본요금 절감액(원)"]
    assert base_savings.nunique() == 3  # 방향은 부하 형태가 정한다


# --------------------------------------------------------------------- 감도 범위 표시 (9.2)


def test_result_is_shown_as_a_range_not_three_columns(pv_sensitivity: pd.DataFrame) -> None:
    """**3열 나열이 아니라 범위로 보여 준다.**

    세 값을 나란히 놓으면 "어느 쪽이 좋은 값인가" 를 찾게 되는데 이 축에는
    좋고 나쁨이 없다. min/max 로 범위만 뽑는다.
    """
    frame = sensitivity_range_frame(pv_sensitivity)
    assert frame.index.name == "지표"
    row = frame.loc["기본요금 절감액(원)"]
    detail = pv_sensitivity["기본요금 절감액(원)"]
    assert row["범위 하한"] == pytest.approx(detail.min())
    assert row["범위 상한"] == pytest.approx(detail.max())
    assert row["기준값"] == pytest.approx(detail.loc["기준"])
    assert "프로파일 감도 범위" in row["표시"]


def test_range_endpoints_are_not_pinned_to_a_scenario(pv_sensitivity: pd.DataFrame) -> None:
    """최댓값이 평탄형에서 나올 수도 첨예형에서 나올 수도 있다.

    샘플은 최대수요가 09:30 인 오전 피크형이라 기본요금 절감액의 상한이
    **첨예형에서 나오지 않는다.** 일률 계수를 곱하던 이전 방식에서는 낙관이 항상
    최대였으므로 여기서 읽는 방식이 갈린다. 그래서 시나리오를 고정하지 않고
    min/max 로 뽑는다.
    """
    ranges = {item.metric: item for item in sensitivity_ranges(pv_sensitivity)}
    base_fee = ranges["기본요금 절감액(원)"]
    assert base_fee.high_scenario != "첨예형"
    assert base_fee.low_scenario == "첨예형"
    assert base_fee.high is not None and base_fee.low is not None
    assert base_fee.high > base_fee.low


def test_range_width_separates_total_and_peak_metrics(pv_sensitivity: pd.DataFrame) -> None:
    """총량 기반 지표는 범위가 좁고 피크 기반 지표만 벌어진다."""
    ranges = {item.metric: item for item in sensitivity_ranges(pv_sensitivity)}
    energy = ranges["전력량요금 절감액(원)"].spread_ratio
    base_fee = ranges["기본요금 절감액(원)"].spread_ratio
    generation = ranges["발전량(kWh)"].spread_ratio
    assert generation is not None and generation < 0.01
    assert energy is not None and energy < 0.05
    assert base_fee is not None and base_fee > energy * 5


# ===================================================================== 57세션 · 잉여 처리


def test_잉여_수익은_요금_재계산_없이_얹힌다(
    sample_comparison: ComparisonResult,
) -> None:
    """**값이 이미 손에 있는데 조합 여섯의 요금이 다시 돌았다** (57세션 2절).

    잉여 수익은 요금 계산 **밖에서** 붙는 덧셈이라 부하도 청구서도 바꾸지 않는다.
    그런데 조합 명세에 들어 있어 라디오를 누를 때마다 2.3초가 들었고, 세션
    기억이 여덟 칸뿐이라 새 항목이 **태양광 곡선과 ESS 정밀화를 밀어내** 6.5초짜리
    재계산까지 불렀다.
    """
    base = sample_comparison.with_surplus_revenue(None, "")
    added = base.with_surplus_revenue(1_000_000.0, "상계거래(한전)")

    # **청구서는 그대로다** — 다시 계산하지 않았다는 뜻이다.
    assert [item.bill for item in added.combinations] == [item.bill for item in base.combinations]
    # 태양광을 켠 조합에만 붙는다.
    for before, after in zip(base.combinations, added.combinations, strict=True):
        expected = 1_000_000.0 if after.spec.has_pv else 0.0
        assert after.surplus_revenue_won == expected
        assert after.saving_won == pytest.approx(before.saving_won + expected)
    assert any(item.spec.has_pv for item in added.combinations), "태양광 조합이 있어야 본다"

    # **되돌리면 처음과 같다** — 덧셈만 하므로 어디서 출발해도 같은 답이다.
    back = added.with_surplus_revenue(None, "")
    assert [item.saving_won for item in back.combinations] == [
        item.saving_won for item in base.combinations
    ]
    assert [item.annual_saving_won for item in back.combinations] == [
        item.annual_saving_won for item in base.combinations
    ]


def test_잉여_수익_근거가_한_자리에서_나온다(sample_comparison: ComparisonResult) -> None:
    """**같은 글을 두 자리에서 짓지 않는다** (46세션과 같은 줄기)."""
    from kwise.compare.combination import SURPLUS_REVENUE_FACT, surplus_notice

    assert surplus_notice(0.0, "출력제어") is None
    note = surplus_notice(1_000_000.0, "상계거래(한전)")
    assert note is not None and note.fact == SURPLUS_REVENUE_FACT

    added = sample_comparison.with_surplus_revenue(1_000_000.0, "상계거래(한전)")
    facts = [item.fact_base for item in added.notices]
    assert SURPLUS_REVENUE_FACT in facts
    # 0 으로 되돌리면 근거도 사라진다 — 없는 사실을 남기지 않는다.
    cleared = added.with_surplus_revenue(None, "")
    assert SURPLUS_REVENUE_FACT not in [item.fact_base for item in cleared.notices]


def test_조합_비교_열쇠가_잉여_수익을_안_본다() -> None:
    """**요금과 무관한 값이 열쇠에 있으면 캐시가 죽는다** (57세션 2절)."""
    import inspect

    # **`from kwise.ui import cache` 가 아니다** (70세션 2절). `kwise.ui` 는
    # 그 하위 모듈을 내보내지 않아 형이 안 잡힌다 — 모듈을 곧장 부른다.
    import kwise.ui.cache as cache

    source = inspect.getsource(cache.cached_comparison)
    assert "stripped" in source
    assert "surplus_revenue_won=None" in source
    assert 'key = f"compare|{token}|{stripped}|{options_key}|{stamp}"' in source
    assert "with_surplus_revenue" in source
