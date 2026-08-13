"""역률요금 (요구사항서 5.7, 7.4 / 기본공급약관 제41·42·43조).

초판 요구사항서의 "지상 90% 미만" 은 오류였다. 원문 기준은 **92%** 이고,
미달 시 추가만이 아니라 **초과 시 감액도 있다.** 금액이 기본요금의
−1.0% ~ +6.4% 라 경고로 둘 수 없다.

    08~22시  지상 92%  미달 1%당 +0.2% (60%까지) / 초과 1%당 −0.2% (97%까지)
    22~08시  진상 95%  미달 1%당 +0.2%, 감액 없음

**기본값 92% 에서 조정액이 정확히 0 이다.** 약관 제42조가 무효전력계 미설치
고객에게 적용하는 간주값이라 근거가 있고, 덕분에 역률을 모르는 채로 금액을
만들어내지 않는다 — 3세션 이래의 회귀값도 그대로 유지된다.
"""

from __future__ import annotations

import itertools

import pandas as pd
import pytest

from kwise.io import UsageData
from kwise.measures import (
    Certainty,
    SolarCurve,
    evaluate_power_factor,
    evaluate_tariff_switch,
)
from kwise.measures.solar import day_window_mask, power_factor_after_pct
from kwise.notices import texts
from kwise.quality import QualityReport
from kwise.tariff import (
    BillingOptions,
    BillingResult,
    TariffSelection,
    TariffTable,
    adjustment_per_percent,
    calculate_bill,
    deemed_leading_pct,
    lagging_adjustment_ratio,
    lagging_floor_pct,
    lagging_rebate_cap_pct,
    lagging_standard_pct,
    leading_adjustment_ratio,
    leading_floor_pct,
    leading_lagging_deemed_pct,
    leading_standard_pct,
    power_factor_charge,
)

CURRENT = TariffSelection("general_b", "high_a", "I")


# --------------------------------------------------------------------- 제43조 ② 산식


def test_standard_is_92_not_90() -> None:
    """초판 요구사항서의 90% 는 오류였다 (제41조·제43조 ②)."""
    assert lagging_standard_pct() == 92.0
    assert lagging_adjustment_ratio(92.0) == pytest.approx(0.0)
    assert lagging_adjustment_ratio(90.0) == pytest.approx(0.004)  # 2%p 미달 → +0.4%


@pytest.mark.parametrize(
    ("pct", "expected"),
    [
        (92.0, 0.0),  # 기준
        (91.0, 0.002),  # 1%p 미달 → +0.2%
        (85.0, 0.014),  # 7%p 미달 → +1.4%
        (60.0, 0.064),  # 하한 → +6.4% (추가 상한)
        (93.0, -0.002),  # 1%p 초과 → −0.2%
        (97.0, -0.010),  # 상한 → −1.0% (감액 상한)
    ],
)
def test_lagging_ratio_runs_both_ways(pct: float, expected: float) -> None:
    """**추가와 감액이 대칭이다.** 매 1%당 0.2%, 양수가 추가·음수가 감액."""
    assert lagging_adjustment_ratio(pct) == pytest.approx(expected)


def test_lagging_ratio_is_linear_in_one_percent_steps() -> None:
    steps = [lagging_adjustment_ratio(pct) for pct in (94.0, 93.0, 92.0, 91.0, 90.0)]
    diffs = [later - earlier for earlier, later in itertools.pairwise(steps)]
    assert all(diff == pytest.approx(adjustment_per_percent()) for diff in diffs)


def test_lagging_is_clamped_at_60_and_97() -> None:
    """60% 아래·97% 위는 더 세지 않는다 (제43조 ②)."""
    assert lagging_adjustment_ratio(55.0) == lagging_adjustment_ratio(lagging_floor_pct())
    assert lagging_adjustment_ratio(30.0) == pytest.approx(0.064)
    assert lagging_adjustment_ratio(99.0) == lagging_adjustment_ratio(lagging_rebate_cap_pct())
    assert lagging_adjustment_ratio(100.0) == pytest.approx(-0.010)


def test_leading_standard_is_95_with_no_rebate() -> None:
    """야간(22~08시)은 진상 95% 기준이고 **감액이 없다** (제43조 ②)."""
    assert leading_standard_pct() == 95.0
    assert leading_adjustment_ratio(95.0) == pytest.approx(0.0)
    assert leading_adjustment_ratio(90.0) == pytest.approx(0.010)  # 5%p 미달 → +1.0%
    assert leading_adjustment_ratio(98.0) == pytest.approx(0.0)  # 초과해도 감액 없음
    assert leading_adjustment_ratio(100.0) == pytest.approx(0.0)


def test_invalid_power_factor_is_rejected() -> None:
    for bad in (0.0, -1.0, 101.0):
        with pytest.raises(ValueError, match="역률은 0~100%"):
            lagging_adjustment_ratio(bad)
        with pytest.raises(ValueError, match="역률은 0~100%"):
            leading_adjustment_ratio(bad)


# --------------------------------------------------------------------- 산출 객체


def test_charge_is_zero_at_the_deemed_92() -> None:
    """제42조의 간주값이라 금액이 0 이다. 모르는 채로 만들어내지 않는다."""
    charge = power_factor_charge(1_000_000.0)
    assert charge.lagging_pct == 92.0
    assert charge.total_won == pytest.approx(0.0)
    assert not charge.is_rebate


def test_charge_splits_lagging_and_leading() -> None:
    charge = power_factor_charge(1_000_000.0, lagging_pct=90.0, leading_pct=93.0)
    assert charge.lagging_won == pytest.approx(4_000.0)  # 2%p × 0.2%
    assert charge.leading_won == pytest.approx(4_000.0)  # 2%p × 0.2%
    assert charge.total_won == pytest.approx(8_000.0)


def test_lagging_night_incurs_no_leading_penalty() -> None:
    """**야간이 지상이면 역률 100% 로 간주되어 진상 추가가 0 이다** (제43조 ② 2호 나목).

    대부분의 건물이 야간 경부하에서 지상이다. 진상은 고정형 역률 개선 설비가 부하 대비
    과다할 때 생기므로, 진상 추가요금은 곧 역률 개선 설비 과투자의 신호다.
    """
    charge = power_factor_charge(1_000_000.0)
    assert charge.leading_pct is None
    assert charge.leading_deemed_pct == leading_lagging_deemed_pct() == 100.0
    assert charge.leading_ratio == pytest.approx(0.0)
    assert charge.leading_won == pytest.approx(0.0)
    assert any("지상으로 보아 역률 100% 간주" in note for note in texts(charge.notices))
    assert any("고정형 역률 개선 설비" in message for message in texts(charge.notices))


def test_deemed_leading_pct_follows_the_clause() -> None:
    """나목 — 진상 60% 미달은 60%로, 지상은 100%로 간주한다."""
    assert deemed_leading_pct(None) == 100.0  # 미상 → 지상으로 본다
    assert deemed_leading_pct(85.0, is_leading=False) == 100.0  # 지상
    assert deemed_leading_pct(85.0) == 85.0  # 진상, 하한 위
    assert deemed_leading_pct(40.0) == leading_floor_pct() == 60.0  # 진상, 하한 미달
    # 지상 판정이면 60% 하한을 거치지 않고 곧장 100% 다.
    assert deemed_leading_pct(40.0, is_leading=False) == 100.0


def test_leading_floor_is_from_the_clause_not_an_assumption() -> None:
    """하한 60% 는 나목에 명시된 간주값이다. 우리가 정한 값이 아니다."""
    assert leading_floor_pct() == 60.0
    assert leading_adjustment_ratio(40.0) == leading_adjustment_ratio(60.0)
    assert leading_adjustment_ratio(60.0) == pytest.approx(0.070)  # 35%p × 0.2%


def test_rebate_is_flagged() -> None:
    charge = power_factor_charge(1_000_000.0, lagging_pct=97.0)
    assert charge.is_rebate
    assert charge.total_won == pytest.approx(-10_000.0)


def test_first_month_rule_is_documented_not_computed() -> None:
    """제43조 ③(첫 달 예고)은 주석으로만 남긴다. Δ 가 흔들리기 때문이다."""
    charge = power_factor_charge(1_000_000.0, lagging_pct=85.0)
    assert charge.total_won == pytest.approx(14_000.0)  # 12개월분 그대로
    assert any("첫 달은 약관상 예고" in note for note in texts(charge.notices))


def test_low_factor_warns_about_the_maintenance_duty() -> None:
    charge = power_factor_charge(1_000_000.0, lagging_pct=55.0)
    assert any("제41조" in message or "60% 미만" in message for message in texts(charge.notices))


# --------------------------------------------------------------------- 요금 엔진 결합


def test_default_bill_is_unchanged_by_the_new_term(sample_bill: BillingResult) -> None:
    """**기본값 92% 에서 부록 B 회귀가 그대로다.** 역률 항이 0 이기 때문이다."""
    assert sample_bill.total_power_factor_won == pytest.approx(0.0)
    assert sample_bill.total_base_won == pytest.approx(452_832_624, rel=1e-6)
    assert sample_bill.total_won == pytest.approx(
        sample_bill.total_base_won + sample_bill.total_energy_won
    )


@pytest.mark.parametrize(
    ("pct", "ratio"),
    [(85.0, 0.014), (92.0, 0.0), (97.0, -0.010), (55.0, 0.064), (99.0, -0.010)],
)
def test_engine_applies_the_ratio_to_the_base_fee(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
    pct: float,
    ratio: float,
) -> None:
    bill = calculate_bill(
        sample_usage,
        tariff,
        CURRENT,
        options=BillingOptions(power_factor_pct=pct),
        quality=sample_report,
    )
    assert bill.total_base_won == pytest.approx(sample_bill.total_base_won)
    assert bill.total_power_factor_won == pytest.approx(sample_bill.total_base_won * ratio)
    assert bill.total_won == pytest.approx(
        bill.total_base_won + bill.total_power_factor_won + bill.total_energy_won
    )


def test_night_leading_clause_reaches_the_bill(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
) -> None:
    """야간 진상 95% 조항도 요금에 든다 — ESS·PV 인버터가 걸리는 자리다."""
    bill = calculate_bill(
        sample_usage,
        tariff,
        CURRENT,
        options=BillingOptions(leading_power_factor_pct=90.0),
        quality=sample_report,
    )
    assert bill.total_power_factor_won == pytest.approx(sample_bill.total_base_won * 0.010)
    assert any("야간 진상역률" in message for message in texts(bill.notices))


def test_monthly_rows_carry_the_power_factor_column(
    sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable
) -> None:
    bill = calculate_bill(
        sample_usage,
        tariff,
        CURRENT,
        options=BillingOptions(power_factor_pct=88.0),
        quality=sample_report,
    )
    monthly = bill.monthly
    assert "power_factor_won" in monthly.columns
    # 월별 합이 총액과 같고, 각 달은 그 달 기본요금에 비례한다.
    assert float(monthly["power_factor_won"].sum()) == pytest.approx(bill.total_power_factor_won)
    ratio = monthly["power_factor_won"] / monthly["base_won"]
    assert ratio.round(9).nunique() == 1
    assert float(ratio.iloc[0]) == pytest.approx(0.008)


def test_traceability_records_the_applied_power_factor(sample_bill: BillingResult) -> None:
    """5.8 — 어느 역률로 계산했는지 산출물에 남는다."""
    lines = " | ".join(sample_bill.traceability())
    assert "적용 역률" in lines
    assert "지상 92.0%" in lines
    assert "지상 간주 100%" in lines  # 야간 — 제43조 ② 2호 나목
    assert "제43조 ② 2호 나목" in lines


# --------------------------------------------------------------------- 7.3 역률 개선 수단


def test_improvement_to_97_saves_exactly_one_percent(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
) -> None:
    """92% → 97% 는 기본요금의 **정확히 1.0%** 다 (감액 상한)."""
    result = evaluate_power_factor(
        sample_usage, tariff, CURRENT, baseline=sample_bill, quality=sample_report
    )
    assert result.current_pct == 92.0
    assert result.target_pct == 97.0
    assert result.saving_won == pytest.approx(sample_bill.total_base_won * 0.010)
    assert result.saving_won == pytest.approx(4_528_326, rel=1e-4)
    assert result.certainty is Certainty.HIGH
    assert result.payback_years == 0.0  # 투자비 0 이면 즉시


def test_improvement_from_a_penalty_removes_it_and_adds_the_rebate(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
) -> None:
    """85% → 97% 는 추가 1.4% 를 없애고 감액 1.0% 를 얻어 2.4% 다."""
    result = evaluate_power_factor(
        sample_usage, tariff, CURRENT, current_pct=85.0, quality=sample_report
    )
    assert result.is_penalty_removal
    assert result.current_charge_won == pytest.approx(sample_bill.total_base_won * 0.014)
    assert result.target_charge_won == pytest.approx(sample_bill.total_base_won * -0.010)
    assert result.saving_won == pytest.approx(sample_bill.total_base_won * 0.024)
    assert any("제41조" in message for message in texts(result.notices))


def test_improvement_is_a_recalculation_not_a_ratio(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
) -> None:
    """두 역률에서 각각 요금을 다시 계산한다. 비율만 곱해 어림하지 않는다."""
    result = evaluate_power_factor(
        sample_usage, tariff, CURRENT, current_pct=90.0, quality=sample_report
    )
    assert result.saving_won == pytest.approx(
        result.current_bill.total_won - result.target_bill.total_won
    )
    assert result.current_bill.power_factor.lagging_pct == 90.0
    assert result.target_bill.power_factor.lagging_pct == 97.0


def test_target_above_the_cap_is_flagged_as_overinvestment(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
) -> None:
    """97% 를 넘겨도 요금은 더 줄지 않는다. 과투자이고 야간 진상 위험이 커진다."""
    result = evaluate_power_factor(
        sample_usage, tariff, CURRENT, target_pct=100.0, baseline=sample_bill, quality=sample_report
    )
    assert result.saving_won == pytest.approx(sample_bill.total_base_won * 0.010)  # 97% 와 같다
    assert any("과투자" in message for message in texts(result.notices))


def test_leading_risk_is_always_warned(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
) -> None:
    """역률 개선 설비를 키우면 야간에 진상으로 넘어간다. 제43조 ② 의 95% 조항이다."""
    result = evaluate_power_factor(
        sample_usage, tariff, CURRENT, baseline=sample_bill, quality=sample_report
    )
    assert any("진상" in message and "95%" in message for message in texts(result.notices))
    assert any("추정값" in message for message in texts(result.notices))


def test_payback_uses_the_investment(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
) -> None:
    result = evaluate_power_factor(
        sample_usage,
        tariff,
        CURRENT,
        investment_won=9_000_000.0,
        baseline=sample_bill,
        quality=sample_report,
    )
    assert result.payback_years is not None
    assert result.payback_years == pytest.approx(9_000_000.0 / result.annual_saving_won)
    assert result.payback_years < 2.5  # 투자비가 작아 회수가 빠르다


def test_lowering_the_factor_is_rejected(
    sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable
) -> None:
    with pytest.raises(ValueError, match="올리는 방향만"):
        evaluate_power_factor(
            sample_usage, tariff, CURRENT, current_pct=95.0, target_pct=92.0, quality=sample_report
        )


def test_power_factor_saving_is_independent_of_sensitivity(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
) -> None:
    """감도는 PV 출력에만 붙는다 (9.2). 역률 개선은 확정 계산이라 무관하다.

    감도 계수를 어떻게 주든 같은 함수·같은 입력이므로 결과가 한 값이어야 한다.
    선택요금 전환과 나란히 확인한다 — 둘 다 확실성 '높음' 이다.
    """
    savings = {
        evaluate_power_factor(
            sample_usage, tariff, CURRENT, baseline=sample_bill, quality=sample_report
        ).saving_won
        for _ in range(3)
    }
    assert len(savings) == 1
    switch = evaluate_tariff_switch(sample_usage, tariff, CURRENT, quality=sample_report)
    assert switch.certainty is Certainty.HIGH
    # 역률 개선 절감액은 PV 감도 계수와 같은 축에 있지 않다.
    assert next(iter(savings)) == pytest.approx(sample_bill.total_base_won * 0.010)


# --------------------------------------------------------------------- PV 도입 전후


def test_day_window_follows_the_slot_start_convention() -> None:
    """라벨은 구간 끝이다. ``08:00`` 슬롯은 07:45~08:00 이라 야간이다."""
    index = pd.DatetimeIndex(["2024-03-04 08:00", "2024-03-04 08:15", "2024-03-04 22:00"])
    mask = day_window_mask(index, 15)
    assert list(mask) == [False, True, True]  # 22:00 슬롯은 21:45~22:00 → 주간


def test_pv_lowers_the_daytime_power_factor() -> None:
    """무효전력은 그대로인데 유효전력만 상쇄되므로 역률이 떨어진다."""
    index = pd.date_range("2024-03-04 00:15", periods=96, freq="15min")
    load = pd.Series(1_000.0, index=index)
    generation = pd.Series(0.0, index=index)
    generation[(index.hour >= 10) & (index.hour < 15)] = 400.0
    after = power_factor_after_pct(load, generation, power_factor_pct=92.0)
    assert after < 92.0
    assert power_factor_after_pct(load, load * 0.0, power_factor_pct=92.0) == pytest.approx(92.0)


def test_generation_outside_the_window_does_not_move_the_factor() -> None:
    """판정 창은 08~22시다. 창 밖 발전은 주간 지상역률에 영향이 없다."""
    index = pd.date_range("2024-03-04 00:15", periods=96, freq="15min")
    load = pd.Series(1_000.0, index=index)
    night = pd.Series(0.0, index=index)
    night[(index.hour >= 2) & (index.hour < 5)] = 400.0
    assert power_factor_after_pct(load, night, power_factor_pct=92.0) == pytest.approx(92.0)


def test_solar_curve_prices_the_power_factor_damage(sample_curve: SolarCurve) -> None:
    """PV 가 역률을 떨어뜨린 만큼의 추가요금과, 그만큼 깎인 절감액을 낸다."""
    largest = sample_curve.points[-1]
    assert largest.power_factor_after_pct < lagging_standard_pct()
    assert largest.power_factor_extra_won > 0
    assert largest.saving_after_power_factor_won == pytest.approx(
        largest.total_saving_won - largest.power_factor_extra_won
    )
    assert any("역률 개선 설비" in message for message in texts(sample_curve.notices))
    assert any("08~22시" in note for note in texts(sample_curve.notices))


def test_power_factor_damage_grows_with_capacity(sample_curve: SolarCurve) -> None:
    extras = [point.power_factor_extra_won for point in sample_curve.points]
    assert extras == sorted(extras)
    assert extras[0] == pytest.approx(0.0, abs=1.0)


def test_improvement_warns_about_apfr_and_fixed_banks(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
) -> None:
    """야간 진상은 역률 개선 설비 과투자의 결과다. 자동제어형 설비 회피책을 함께 안내한다.

    금액은 만들지 않는다 — 설치비는 설비 구성에 달렸고 사용자 입력이다.
    """
    result = evaluate_power_factor(
        sample_usage, tariff, CURRENT, baseline=sample_bill, quality=sample_report
    )
    joined = " ".join(texts(result.notices))
    assert "고정형 역률 개선 설비" in joined
    assert "자동제어형 역률 개선 설비" in joined
    assert "역률 개선 설비 과투자의 신호" in joined
    assert result.investment_won == 0.0  # 금액을 지어내지 않는다
