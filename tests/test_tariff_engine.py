"""요금 계산기 (요구사항서 5장, 11.2).

수기 케이스는 **요금표에서 직접 계산한 값**을 적는다. 코드가 낸 값을 옮겨 적으면
검증이 아니라 기록이 된다. 슬롯 수를 손으로 세어 놓았으므로 요일 규칙이 어긋나면
kWh 배분에서 먼저 걸린다.

균일 부하 100 kWh/15분 = 400 kW 를 쓴다. 하루 96슬롯이다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from kwise.io import UsageData, load_usage
from kwise.quality import QualityReport
from kwise.tariff import (
    NOT_INCLUDED_NOTICE,
    BillingOptions,
    BillingResult,
    TariffSelection,
    TariffTable,
    billing_demands,
    calculate_bill,
    list_selections,
)
from tests._synthetic import write_month

HIGH_A_I = TariffSelection("general_b", "high_a", "I")
HIGH_B_II = TariffSelection("general_b", "high_b", "II")

# 요금표에서 옮긴 단가 (부록 A.1)
BASE_A_I = 7_220.0
BASE_B_II = 7_380.0
SUMMER_A_I = {"light": 92.8, "mid": 145.7, "peak": 227.8}
SUMMER_B_II = {"light": 92.1, "mid": 144.4, "peak": 225.6}
WINTER_A_I = {"light": 99.8, "mid": 145.9, "peak": 203.4}
SPRING_A_I = {"light": 92.8, "mid": 115.3, "peak": 146.0}

# 하루 슬롯 배분 (구간 시작 시각 기준)
#   평일   경부하 22~08 = 40슬롯, 중간 08~15·21~22 = 32슬롯, 최대 15~21 = 24슬롯
#   토요일 최대부하가 중간부하로 → 40 / 56 / 0
#   일요일·공휴일 전량 경부하 → 96 / 0 / 0
KWH = 100.0


def month_usage(tmp_path: Path, year: int, month: int) -> UsageData:
    return load_usage(write_month(tmp_path / f"{year}{month:02d}.csv", year, month))


def bill(
    usage: UsageData,
    tariff: TariffTable,
    selection: TariffSelection = HIGH_A_I,
    **kwargs: object,
) -> BillingResult:
    options = BillingOptions(**kwargs)  # type: ignore[arg-type]
    return calculate_bill(usage, tariff, selection, options=options)


# --------------------------------------------------------------------- 수기 케이스 1


def test_hand_case_summer_month_high_a_option1(tmp_path: Path, tariff: TariffTable) -> None:
    """2023-07 (31일: 평일 21, 토 5, 일 5, 공휴일 0), 400 kW 균일 부하, 고압A 선택Ⅰ.

    경부하 = 21×40 + 5×40 + 5×96 = 1,520슬롯 → 152,000 kWh
    중간부하 = 21×32 + 5×56           =   952슬롯 →  95,200 kWh
    최대부하 = 21×24                  =   504슬롯 →  50,400 kWh
    """
    result = bill(month_usage(tmp_path, 2023, 7), tariff)
    row = result.monthly.loc[pd.Period("2023-07", freq="M")]

    assert row["light_kwh"] == pytest.approx(152_000.0)
    assert row["mid_kwh"] == pytest.approx(95_200.0)
    assert row["peak_kwh"] == pytest.approx(50_400.0)
    assert row["total_kwh"] == pytest.approx(31 * 96 * KWH)

    expected_energy = (
        152_000 * SUMMER_A_I["light"] + 95_200 * SUMMER_A_I["mid"] + 50_400 * SUMMER_A_I["peak"]
    )
    assert expected_energy == pytest.approx(39_457_360.0)  # 손으로 계산한 값
    assert row["energy_won"] == pytest.approx(expected_energy)

    assert row["max_demand_kw"] == pytest.approx(400.0)
    assert row["billing_demand_kw"] == pytest.approx(400.0)
    assert row["base_fee_factor"] == pytest.approx(1.0)  # 온전한 달이다
    assert row["base_won"] == pytest.approx(400.0 * BASE_A_I)
    assert row["base_won"] == pytest.approx(2_888_000.0)
    assert result.total_won == pytest.approx(2_888_000.0 + 39_457_360.0)


# --------------------------------------------------------------------- 수기 케이스 2


def test_hand_case_summer_month_high_b_option2(tmp_path: Path, tariff: TariffTable) -> None:
    """같은 데이터, 고압B 선택Ⅱ. 단가만 바뀐다."""
    result = bill(month_usage(tmp_path, 2023, 7), tariff, HIGH_B_II)
    row = result.monthly.loc[pd.Period("2023-07", freq="M")]

    expected_energy = (
        152_000 * SUMMER_B_II["light"] + 95_200 * SUMMER_B_II["mid"] + 50_400 * SUMMER_B_II["peak"]
    )
    assert expected_energy == pytest.approx(39_116_320.0)
    assert row["energy_won"] == pytest.approx(expected_energy)
    assert row["base_won"] == pytest.approx(400.0 * BASE_B_II)
    assert row["base_won"] == pytest.approx(2_952_000.0)
    assert result.base_rate_won_per_kw == BASE_B_II
    assert result.voltage_label == "고압B"


# --------------------------------------------------------------------- 수기 케이스 3


def test_hand_case_winter_month_with_christmas(tmp_path: Path, tariff: TariffTable) -> None:
    """2023-12 (평일 20, 토 5, 일 5, 공휴일 1=12/25 월), 겨울 시간대.

    경부하 = 20×40 + 5×40 + 6×96 = 1,576슬롯 → 157,600 kWh
    중간부하 = 20×32 + 5×56           =   920슬롯 →  92,000 kWh
    최대부하 = 20×24                  =   480슬롯 →  48,000 kWh
    """
    result = bill(month_usage(tmp_path, 2023, 12), tariff)
    row = result.monthly.loc[pd.Period("2023-12", freq="M")]

    assert row["season"] == "winter"
    assert row["light_kwh"] == pytest.approx(157_600.0)
    assert row["mid_kwh"] == pytest.approx(92_000.0)
    assert row["peak_kwh"] == pytest.approx(48_000.0)

    expected_energy = (
        157_600 * WINTER_A_I["light"] + 92_000 * WINTER_A_I["mid"] + 48_000 * WINTER_A_I["peak"]
    )
    assert expected_energy == pytest.approx(38_914_480.0)
    assert row["energy_won"] == pytest.approx(expected_energy)


# --------------------------------------------------------------------- 수기 케이스 4


def test_hand_case_temporary_holiday_changes_the_bill(tmp_path: Path, tariff: TariffTable) -> None:
    """2023-10 (평일 20, 토 4, 일 5, 공휴일 2=10/3·10/9). 임시공휴일 10/2 는 기본 제외.

    기본:        경 163,200 / 중 86,400 / 최 48,000 kWh
    10/2 추가 시: 경 168,800 / 중 83,200 / 최 45,600 kWh
    """
    usage = month_usage(tmp_path, 2023, 10)
    default = bill(usage, tariff).monthly.loc[pd.Period("2023-10", freq="M")]
    added = bill(usage, tariff, extra_holidays=("2023-10-02",)).monthly.loc[
        pd.Period("2023-10", freq="M")
    ]

    assert default["light_kwh"] == pytest.approx(163_200.0)
    assert default["mid_kwh"] == pytest.approx(86_400.0)
    assert default["peak_kwh"] == pytest.approx(48_000.0)
    expected_default = (
        163_200 * SPRING_A_I["light"] + 86_400 * SPRING_A_I["mid"] + 48_000 * SPRING_A_I["peak"]
    )
    assert expected_default == pytest.approx(32_114_880.0)
    assert default["energy_won"] == pytest.approx(expected_default)

    assert added["light_kwh"] == pytest.approx(168_800.0)
    expected_added = (
        168_800 * SPRING_A_I["light"] + 83_200 * SPRING_A_I["mid"] + 45_600 * SPRING_A_I["peak"]
    )
    assert expected_added == pytest.approx(31_915_200.0)
    assert added["energy_won"] == pytest.approx(expected_added)
    assert default["energy_won"] - added["energy_won"] == pytest.approx(199_680.0)


# --------------------------------------------------------------------- 수기 케이스 5


def test_hand_case_sunday_rule_moves_real_money(tmp_path: Path, tariff: TariffTable) -> None:
    """일요일을 공휴일로 계량하지 않으면 2023-07 전력량요금이 6.2% 과대 산출된다.

    일요일이 평일이 되면 경 124,000 / 중 111,200 / 최 62,400 kWh 가 된다.
    """
    usage = month_usage(tmp_path, 2023, 7)
    correct = bill(usage, tariff).monthly.loc[pd.Period("2023-07", freq="M")]
    wrong = bill(usage, tariff, sunday_is_holiday=False).monthly.loc[pd.Period("2023-07", freq="M")]

    assert wrong["light_kwh"] == pytest.approx(124_000.0)
    assert wrong["mid_kwh"] == pytest.approx(111_200.0)
    assert wrong["peak_kwh"] == pytest.approx(62_400.0)
    expected_wrong = (
        124_000 * SUMMER_A_I["light"] + 111_200 * SUMMER_A_I["mid"] + 62_400 * SUMMER_A_I["peak"]
    )
    assert expected_wrong == pytest.approx(41_923_760.0)
    assert wrong["energy_won"] == pytest.approx(expected_wrong)
    assert wrong["energy_won"] / correct["energy_won"] == pytest.approx(1.062, abs=0.001)


# --------------------------------------------------------------------- 요금적용전력 (5.2)


def periods(*labels: str) -> list[pd.Period]:
    return [pd.Period(label, freq="M") for label in labels]


def test_billing_demand_keeps_the_12_month_maximum() -> None:
    """여름 피크만 낮추고 겨울 피크가 그대로면 기본요금은 변하지 않는다."""
    peaks = {"2023-07": 5_000.0, "2023-08": 4_000.0, "2023-09": 3_000.0}
    demands = billing_demands(peaks)
    assert demands[pd.Period("2023-07", freq="M")] == 5_000.0
    assert demands[pd.Period("2023-08", freq="M")] == 5_000.0
    assert demands[pd.Period("2023-09", freq="M")] == 5_000.0


def test_billing_demand_drops_the_peak_after_12_months() -> None:
    peaks = {"2023-01": 5_000.0}
    peaks.update({f"2023-{month:02d}": 3_000.0 for month in range(2, 13)})
    peaks["2024-01"] = 3_000.0  # 13번째 달 — 2023-01 이 창을 벗어난다
    demands = billing_demands(peaks)
    assert demands[pd.Period("2023-12", freq="M")] == 5_000.0  # 아직 창 안이다
    assert demands[pd.Period("2024-01", freq="M")] == 3_000.0


def test_prior_peaks_can_be_injected() -> None:
    """직전 이력이 없으면 첫 11개월이 과소 산출된다. 청구서로 주입할 수 있어야 한다."""
    peaks = {"2023-05": 4_000.0, "2023-06": 4_200.0}
    without = billing_demands(peaks)
    with_prior = billing_demands(peaks, prior_peaks={"2022-12": 5_500.0})
    assert without[pd.Period("2023-05", freq="M")] == 4_000.0
    assert with_prior[pd.Period("2023-05", freq="M")] == 5_500.0
    assert with_prior[pd.Period("2023-06", freq="M")] == 5_500.0


def test_prior_peaks_outside_the_window_are_ignored() -> None:
    demands = billing_demands({"2024-01": 4_000.0}, prior_peaks={"2022-12": 9_000.0})
    assert demands[pd.Period("2024-01", freq="M")] == 4_000.0


def test_missing_prior_history_is_warned(sample_bill: BillingResult) -> None:
    assert not sample_bill.prior_peaks_supplied
    assert any("직전 12개월" in message for message in sample_bill.warnings)


def test_sample_billing_demand_follows_the_12_month_rule(sample_bill: BillingResult) -> None:
    """2023-08 최대수요는 5,288 kW 지만 7월의 5,293 kW 가 요금적용전력이다."""
    monthly = sample_bill.monthly
    august = monthly.loc[pd.Period("2023-08", freq="M")]
    assert august["max_demand_kw"] == pytest.approx(5_287.7, abs=0.1)
    assert august["billing_demand_kw"] == pytest.approx(5_293.44)
    # 연간 최대 이후 모든 달의 요금적용전력이 그 값으로 고정된다
    after = monthly.loc[pd.Period("2023-07", freq="M") :, "billing_demand_kw"]
    assert (after - 5_293.44).abs().max() < 1e-6
    assert sample_bill.billing_demand_kw == pytest.approx(5_293.44)


def test_sample_monthly_peaks_match_appendix_b(sample_bill: BillingResult) -> None:
    """부록 B 의 월별 최대수요 표."""
    expected = {
        "2023-04": 4_164,
        "2023-05": 4_787,
        "2023-06": 5_210,
        "2023-07": 5_293,
        "2023-08": 5_288,
        "2023-09": 5_003,
        "2023-10": 4_614,
        "2023-11": 4_196,
        "2023-12": 4_349,
        "2024-01": 4_576,
        "2024-02": 4_404,
        "2024-03": 4_208,
        "2024-04": 4_519,
    }
    for label, value in expected.items():
        row = sample_bill.monthly.loc[pd.Period(label, freq="M")]
        assert row["max_demand_kw"] == pytest.approx(value, abs=1.0), label


# --------------------------------------------------------------------- 결측 월 (5.4)


def test_sample_november_is_marked_limited(sample_bill: BillingResult) -> None:
    """11월 결측률 32.3% — 최대수요를 '신뢰 제한' 으로 표시한다."""
    row = sample_bill.monthly.loc[pd.Period("2023-11", freq="M")]
    assert row["missing_ratio"] == pytest.approx(0.323, abs=0.005)
    assert row["demand_confidence"] == "신뢰 제한"
    assert sample_bill.limited_months == (pd.Period("2023-11", freq="M"),)
    assert any("신뢰 제한" in message for message in sample_bill.warnings)


def test_sample_november_adjusted_energy_charge(sample_bill: BillingResult) -> None:
    """결측 보정 기준 = 관측 기준 ÷ (1 − 결측률). 11월만 크게 벌어진다."""
    row = sample_bill.monthly.loc[pd.Period("2023-11", freq="M")]
    expected = row["energy_won"] / (1.0 - row["missing_ratio"])
    assert row["energy_won_adjusted"] == pytest.approx(expected)
    assert row["energy_won_adjusted"] / row["energy_won"] == pytest.approx(1.477, abs=0.01)
    # 기본요금은 최대값 기반이라 보정하지 않는다
    assert row["total_won_adjusted"] - row["total_won"] == pytest.approx(
        row["energy_won_adjusted"] - row["energy_won"]
    )


def test_sample_full_months_are_unaffected_by_adjustment(sample_bill: BillingResult) -> None:
    row = sample_bill.monthly.loc[pd.Period("2023-07", freq="M")]
    assert row["missing_ratio"] == 0.0
    assert row["energy_won_adjusted"] == pytest.approx(row["energy_won"])


def test_sample_adjustment_moves_only_months_with_missing_data(
    sample_bill: BillingResult,
) -> None:
    """결측이 있는 달만 올라간다. 샘플에서는 11월(32.3%)과 2024-04(1.7%) 둘뿐이다."""
    monthly = sample_bill.monthly
    per_month = monthly["energy_won_adjusted"] - monthly["energy_won"]
    moved = per_month[per_month > 1.0]
    assert list(moved.index) == periods("2023-11", "2024-04")

    difference = sample_bill.total_energy_won_adjusted - sample_bill.total_energy_won
    assert difference == pytest.approx(float(per_month.sum()), rel=1e-9)
    # 11월이 대부분을 차지한다
    november = monthly.loc[pd.Period("2023-11", freq="M")]
    assert november["energy_won_adjusted"] - november["energy_won"] == pytest.approx(
        82_221_077.0, rel=1e-4
    )
    assert sample_bill.total_won_adjusted > sample_bill.total_won


def test_quality_report_is_reused_not_recomputed(
    sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable
) -> None:
    """월별 결측률은 2세션 결과를 그대로 쓴다. 다시 계산해도 같아야 한다."""
    with_report = calculate_bill(sample_usage, tariff, HIGH_A_I, quality=sample_report)
    without = calculate_bill(sample_usage, tariff, HIGH_A_I)
    pd.testing.assert_series_equal(
        with_report.monthly["missing_ratio"], without.monthly["missing_ratio"]
    )
    ratios = {item.month: item.ratio for item in sample_report.monthly}
    for month, value in with_report.monthly["missing_ratio"].items():
        assert value == pytest.approx(ratios[month])


# --------------------------------------------------------------------- 부분 월 (5.5)


def test_sample_does_not_charge_13_months_of_base_fee(sample_bill: BillingResult) -> None:
    """기간이 13개 월 버킷이지만 기본요금은 12개월분이어야 한다."""
    assert len(sample_bill.monthly) == 13
    assert sample_bill.base_fee_months == pytest.approx(12.0)
    assert sample_bill.base_fee_months < 13.0


def test_sample_partial_months_are_the_two_aprils(sample_bill: BillingResult) -> None:
    partial = sample_bill.monthly[sample_bill.monthly["is_partial"]]
    assert list(partial.index) == periods("2023-04", "2024-04")
    assert partial.loc[pd.Period("2023-04", freq="M"), "covered_days"] == pytest.approx(6.0)
    assert partial.loc[pd.Period("2024-04", freq="M"), "covered_days"] == pytest.approx(26.0)
    # 두 조각을 합쳐 한 달 — 6/32 + 26/32 = 1
    assert partial["base_fee_factor"].sum() == pytest.approx(1.0)
    assert any("두 조각을 합쳐" in note for note in sample_bill.notes)


def test_prorate_policy_is_available_and_labelled(
    sample_usage: UsageData, tariff: TariffTable
) -> None:
    result = calculate_bill(
        sample_usage,
        tariff,
        HIGH_A_I,
        options=BillingOptions(partial_month_policy="prorate"),
    )
    # 6/30 + 26/30 = 1.0667
    assert result.base_fee_months == pytest.approx(11 + 6 / 30 + 26 / 30)
    assert result.partial_month_policy == "prorate"
    assert any("일수 비례" in note for note in result.notes)
    assert result.total_base_won > 0


def test_single_partial_month_falls_back_to_prorating(tmp_path: Path, tariff: TariffTable) -> None:
    """합칠 짝이 없으면 일수 비례로 안분하고 그 사실을 적는다."""
    rows_path = write_month(tmp_path / "part.csv", 2023, 7)
    text = rows_path.read_text(encoding="utf-8-sig").splitlines()
    rows_path.write_text("\n".join(text[: 1 + 96 * 10]) + "\n", encoding="utf-8-sig")
    result = bill(load_usage(rows_path), tariff)
    assert result.base_fee_months == pytest.approx(10 / 31)
    assert any("합칠 짝이 없어" in note for note in result.notes)


def test_annualize_scales_to_twelve_months(sample_bill: BillingResult) -> None:
    annual = sample_bill.annualize()
    assert annual.factor == pytest.approx(1.0)  # 이미 12개월분이다
    assert annual.total_won == pytest.approx(sample_bill.total_won)
    assert annual.warnings == ()


def test_annualize_warns_for_short_periods(tmp_path: Path, tariff: TariffTable) -> None:
    result = bill(month_usage(tmp_path, 2023, 7), tariff)
    annual = result.annualize()
    assert annual.factor == pytest.approx(12.0)
    assert annual.total_won == pytest.approx(result.total_won * 12)
    assert any("12개월 미만" in message for message in annual.warnings)


def test_period_label_is_used_instead_of_annual(sample_bill: BillingResult) -> None:
    """'연간' 이라 쓰지 않는다. 실제 기간을 명시한다."""
    label = sample_bill.period_label
    assert "2023-04-25" in label
    assert "2024-04-27" in label
    assert "368일" in label
    assert "12.00개월분" in label


# --------------------------------------------------------------------- 정합·추적성


def test_monthly_energy_sums_to_total_kwh(
    sample_bill: BillingResult, sample_usage: UsageData
) -> None:
    """월별 kWh 합계 = 총 사용량. 그리드 이탈분이 빠지지 않았는지 본다."""
    assert sample_bill.monthly["total_kwh"].sum() == pytest.approx(sample_usage.total_kwh)


def test_totals_are_consistent(sample_bill: BillingResult) -> None:
    monthly = sample_bill.monthly
    assert sample_bill.total_base_won == pytest.approx(monthly["base_won"].sum())
    assert sample_bill.total_energy_won == pytest.approx(monthly["energy_won"].sum())
    assert sample_bill.total_won == pytest.approx(
        sample_bill.total_base_won + sample_bill.total_energy_won
    )


def test_traceability_is_reported(sample_bill: BillingResult) -> None:
    """산출물에 적용 근거를 표기한다 (5.8)."""
    lines = sample_bill.traceability()
    assert any("2026-06-01 시행" in line for line in lines)
    assert any("일반용전력(을) 고압A 선택I" in line for line in lines)
    assert any("7,220 원/kW" in line for line in lines)


def test_excluded_charge_elements_are_stated(sample_bill: BillingResult) -> None:
    """기본요금·전력량요금만 계산했다는 사실을 반드시 적는다 (5.1)."""
    assert NOT_INCLUDED_NOTICE in sample_bill.notes
    assert any("부가가치세" in note for note in sample_bill.notes)
    assert any("하한 규정" in note for note in sample_bill.notes)


def test_unverified_tariff_is_warned(sample_bill: BillingResult) -> None:
    assert any("검증되지 않았습니다" in message for message in sample_bill.warnings)


def test_every_selection_can_be_priced(sample_usage: UsageData, tariff: TariffTable) -> None:
    """선택요금 비교(5세션)는 이 함수를 조합마다 부른다. 6조합이 모두 계산돼야 한다."""
    results = {
        str(selection): calculate_bill(sample_usage, tariff, selection)
        for selection in list_selections(tariff)
    }
    assert len(results) == 6
    assert all(result.total_won > 0 for result in results.values())
    # 선택Ⅰ→Ⅱ 는 기본요금이 오르고 전력량요금이 내린다
    option1 = results["general_b/high_a/I"]
    option2 = results["general_b/high_a/II"]
    assert option2.total_base_won > option1.total_base_won
    assert option2.total_energy_won < option1.total_energy_won


def test_sample_optimal_option_matches_appendix_b(
    sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable
) -> None:
    """부록 B — 최적 선택요금은 선택Ⅱ.

    월 이용시간이 351 h 라 Ⅰ→Ⅱ 손익분기(200 h)는 넘지만 Ⅱ→Ⅲ 손익분기(491 h)에는
    못 미친다. 요금표의 설계와 계산 결과가 맞물린다.
    """
    hours_per_month = sample_usage.total_kwh / sample_usage.meta.max_demand_kw / 12
    assert hours_per_month == pytest.approx(351.0, abs=1.0)

    totals = {
        option: calculate_bill(
            sample_usage,
            tariff,
            TariffSelection("general_b", "high_a", option),
            quality=sample_report,
        ).total_won
        for option in ("I", "II", "III")
    }
    assert min(totals, key=lambda option: totals[option]) == "II"
    assert totals["II"] < totals["I"]
    assert totals["II"] < totals["III"]
