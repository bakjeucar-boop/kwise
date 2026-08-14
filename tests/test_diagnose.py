"""진단 (요구사항서 6장, 부록 B).

진단은 **설비 정보 없이** 나와야 한다. PV 를 넣지 않아도 태양광 검토 신호까지
나오는 것이 이 모듈의 요점이다.
"""

from __future__ import annotations

import inspect

import pandas as pd
import pytest

from kwise.diagnose import (
    ContractInfo,
    Diagnosis,
    PvPotential,
    assess_contract,
    diagnose,
    judge_pv_potential,
    peak_profile,
)
from kwise.io import UsageData
from kwise.notices import texts
from kwise.quality import QualityReport
from kwise.tariff import (
    TariffSelection,
    TariffTable,
    build_calendar,
    classify_slots,
    demand_eligible_mask,
)

CURRENT = TariffSelection("general_b", "high_a", "I")
CONTRACT_KW = 5_500.0


# --------------------------------------------------------------------- 6.2 피크 특성


def test_monthly_peaks_match_appendix_b(sample_diagnosis: Diagnosis) -> None:
    """부록 B 의 월별 최대수요 표. 12개가 아니라 13개 버킷이다."""
    expected = {
        "2023-04": (4_164, "화", 12),
        "2023-05": (4_787, "목", 10),
        "2023-06": (5_210, "목", 8),
        "2023-07": (5_293, "월", 9),
        "2023-08": (5_288, "화", 16),
        "2023-09": (5_003, "화", 16),
        "2023-10": (4_614, "목", 9),
        "2023-11": (4_196, "목", 16),
        "2023-12": (4_349, "수", 10),
        "2024-01": (4_576, "월", 10),
        "2024-02": (4_404, "수", 11),
        "2024-03": (4_208, "금", 9),
        "2024-04": (4_519, "금", 10),
    }
    monthly = sample_diagnosis.peak.monthly
    assert len(monthly) == 13
    for label, (kw, weekday, hour) in expected.items():
        row = monthly.loc[pd.Period(label, freq="M")]
        assert row["max_demand_kw"] == pytest.approx(kw, abs=1.0), label
        assert row["weekday"] == weekday, label
        assert row["hour"] == hour, label


def test_peak_timestamp_matches_appendix_b(sample_diagnosis: Diagnosis) -> None:
    row = sample_diagnosis.peak.monthly.loc[pd.Period("2023-07", freq="M")]
    assert row["max_demand_at"] == pd.Timestamp("2023-07-03 09:30")
    assert sample_diagnosis.peak.peak_kw == pytest.approx(5_293.44)


def test_billing_demand_follows_the_12_month_rule(sample_diagnosis: Diagnosis) -> None:
    """8월 최대수요는 5,288 kW 지만 요금적용전력은 7월의 5,293 kW 다."""
    monthly = sample_diagnosis.peak.monthly
    august = monthly.loc[pd.Period("2023-08", freq="M")]
    assert august["max_demand_kw"] < august["billing_demand_kw"]
    assert august["billing_demand_kw"] == pytest.approx(5_293.44)
    assert sample_diagnosis.peak.billing_demand_kw == pytest.approx(5_293.44)


def test_top_100_hour_distribution_matches_appendix_b(sample_diagnosis: Diagnosis) -> None:
    """부록 B 의 상위 100구간 시각 분포. 검침 라벨 기준이다."""
    counts = sample_diagnosis.peak.hour_counts
    expected = {7: 1, 8: 6, 9: 5, 10: 15, 11: 14, 12: 20, 13: 8, 14: 9, 15: 9, 16: 10, 17: 3}
    assert {hour: int(value) for hour, value in counts.items() if value} == expected
    assert int(counts.sum()) == 100


def test_top_100_weekday_distribution_matches_appendix_b(sample_diagnosis: Diagnosis) -> None:
    """월 25, 화 32, 수 12, 목 10, 금 21 — 주말 0건."""
    counts = sample_diagnosis.peak.weekday_counts
    assert counts.to_dict() == {"월": 25, "화": 32, "수": 12, "목": 10, "금": 21, "토": 0, "일": 0}
    assert sample_diagnosis.peak.weekend_slots == 0


def test_top_slots_carry_both_time_conventions(sample_diagnosis: Diagnosis) -> None:
    """라벨 시각과 구간 시작 시각을 함께 담는다. 둘은 15분 다르다.

    분포는 라벨 기준(부록 B·청구서 관행)이고, 요금 귀속은 구간 시작 기준이다.
    섞으면 조용히 틀리므로 두 값을 모두 남긴다.
    """
    top = sample_diagnosis.peak.top_slots
    assert (
        pd.DatetimeIndex(top.index) - pd.DatetimeIndex(top["slot_start"])
    ).unique().tolist() == [pd.Timedelta(minutes=15)]
    label_counts = top["hour"].value_counts().sort_index()
    start_counts = top["slot_start_hour"].value_counts().sort_index()
    assert label_counts.to_dict() != start_counts.to_dict()


def test_hourly_profile_covers_the_day(sample_diagnosis: Diagnosis) -> None:
    profile = sample_diagnosis.peak.hourly_profile
    assert len(profile) == 24
    assert profile.idxmax() in range(9, 17)  # 업무 시간대에 평균 부하가 가장 높다
    assert profile.min() > 0


def test_peak_profile_needs_observations() -> None:
    empty = pd.Series(float("nan"), index=pd.date_range("2024-01-01", periods=4, freq="15min"))
    with pytest.raises(ValueError, match="관측된 수요가 없어"):
        peak_profile(empty, 15)


# --------------------------------------------------------------------- 6.5 태양광 등급


def test_sample_is_judged_high_pv_potential(sample_diagnosis: Diagnosis) -> None:
    """상위 구간이 10~15시에 몰려 있으면 태양광 피크 기여 가능성이 높다."""
    summary = sample_diagnosis.summary
    assert summary.pv_potential is PvPotential.HIGH
    assert summary.pv_midday_share == pytest.approx(0.66, abs=0.01)


def test_evening_peaks_are_judged_low(sample_usage: UsageData) -> None:
    """저녁 피크형은 태양광 기여가 거의 없다. 같은 지표가 정반대로 판정해야 한다."""
    kw = sample_usage.kw.copy()
    evening = pd.DatetimeIndex(kw.index).hour.isin([19, 20, 21])
    kw[evening] = kw.max() * 1.5  # 저녁을 최고 부하로 만든다
    profile = peak_profile(kw, 15)
    potential, share = judge_pv_potential(profile)
    assert potential is PvPotential.LOW
    assert share < 0.25


def test_potential_thresholds_are_adjustable(sample_diagnosis: Diagnosis) -> None:
    potential, _ = judge_pv_potential(sample_diagnosis.peak, high_share=0.9)
    assert potential is PvPotential.MEDIUM


# --------------------------------------------------------------------- 설비 정보 없이


def test_diagnose_takes_no_pv_input() -> None:
    """진단은 설비 정보를 받지 않는다. 인자에 PV 가 없어야 한다."""
    parameters = set(inspect.signature(diagnose).parameters)
    assert not {name for name in parameters if "pv" in name or "array" in name}
    assert parameters == {
        "usage",
        "table",
        "contract",
        "quality",
        "options",
        "top_n",
        "margin_ratio",
        "operating_hours",
        "contract_floor_ratio",
    }


def test_diagnose_works_without_contract_info(
    sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable
) -> None:
    """파일만 올려도 부하 패턴·피크 특성·태양광 신호까지 나온다."""
    result = diagnose(sample_usage, tariff, quality=sample_report)
    assert not result.has_charges
    assert result.structure is None
    assert result.contract is None
    assert result.pattern.load_factor == pytest.approx(0.490, abs=0.001)
    assert result.peak.billing_demand_kw == pytest.approx(5_293.44)
    assert result.summary.pv_potential is PvPotential.HIGH
    assert result.summary.tariff_switch_saving_won is None
    assert any("계약 정보가 없어" in message for message in texts(result.notices))
    assert len(result.summary.lines) == 3


def test_diagnose_without_contract_kw_still_prices(
    sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable
) -> None:
    result = diagnose(sample_usage, tariff, ContractInfo(CURRENT), quality=sample_report)
    assert result.has_charges
    assert result.contract is None  # 계약전력을 모르면 적정성은 낼 수 없다
    assert result.summary.tariff_switch_saving_won is not None
    assert any("계약전력을 입력하면" in message for message in texts(result.notices))


def test_load_pattern_is_reused_not_reimplemented(
    sample_diagnosis: Diagnosis, sample_usage: UsageData
) -> None:
    """6.1 은 2세션 함수를 호출만 한다."""
    from kwise.quality import load_pattern

    expected = load_pattern(sample_usage.kw, 15)
    assert sample_diagnosis.pattern == expected


# --------------------------------------------------------------------- 6.3 요금 구조


def test_charge_structure_shares_add_up(sample_diagnosis: Diagnosis) -> None:
    structure = sample_diagnosis.structure
    assert structure is not None
    assert structure.base_share + structure.energy_share == pytest.approx(1.0)
    assert structure.base_share == pytest.approx(0.135, abs=0.005)
    assert float(structure.band_share.sum()) == pytest.approx(1.0)
    assert float(structure.season_share.sum()) == pytest.approx(1.0)


def test_band_energy_ties_to_total_usage(
    sample_diagnosis: Diagnosis, sample_usage: UsageData
) -> None:
    """시간대별 사용량 합계 = 총 사용량. 그리드 이탈분이 빠지지 않았는지 본다."""
    structure = sample_diagnosis.structure
    assert structure is not None
    assert float(structure.band_kwh.sum()) == pytest.approx(sample_usage.total_kwh)
    assert float(structure.season_kwh.sum()) == pytest.approx(sample_usage.total_kwh)


def test_monthly_statement_comes_from_the_tariff_engine(sample_diagnosis: Diagnosis) -> None:
    structure = sample_diagnosis.structure
    assert structure is not None
    assert len(structure.monthly) == 13
    assert structure.bill.base_fee_months == pytest.approx(12.0)
    assert structure.selection == CURRENT


# --------------------------------------------------------------------- 6.4 계약 적정성


def test_sample_contract_has_little_headroom(sample_diagnosis: Diagnosis) -> None:
    """계약 5,500 kW 에 요금적용전력 5,293 kW — 여유가 10% 도 없다."""
    adequacy = sample_diagnosis.contract
    assert adequacy is not None
    assert adequacy.utilization == pytest.approx(0.962, abs=0.001)
    assert adequacy.headroom_kw == pytest.approx(206.56, abs=0.1)
    assert adequacy.reduction_kw == 0.0
    assert not adequacy.is_over_contracted


def test_over_contracted_case_shows_reduction(sample_usage: UsageData) -> None:
    """계약 7,000 kW 면 여유율을 얹고도 1,177 kW 를 내릴 수 있다."""
    adequacy = assess_contract(
        sample_usage.kw,
        contract_kw=7_000.0,
        billing_demand_kw=5_293.44,
        base_rate_won_per_kw=7_220.0,
        base_fee_months=12.0,
    )
    assert adequacy.suggested_contract_kw == 5_823.0  # 5,293.44 × 1.10 올림
    assert adequacy.reduction_kw == pytest.approx(1_177.0)
    assert adequacy.is_over_contracted
    assert adequacy.saving_won is None  # 하한 규정을 모르면 금액을 만들지 않는다
    assert "미확인" in adequacy.saving_basis


def test_saving_is_recalculated_when_the_floor_rule_is_known(sample_usage: UsageData) -> None:
    """하한 비율을 주면 두 계약전력에서 요금적용전력을 각각 다시 구해 뺀다."""
    adequacy = assess_contract(
        sample_usage.kw,
        contract_kw=7_000.0,
        billing_demand_kw=5_293.44,
        base_rate_won_per_kw=7_220.0,
        base_fee_months=12.0,
        contract_floor_ratio=1.0,  # 요금적용전력 하한 = 계약전력
    )
    # 7,000 kW → 5,823 kW, 12개월분
    expected = (7_000.0 - 5_823.0) * 7_220.0 * 12.0
    assert adequacy.saving_won == pytest.approx(expected)
    assert "하한 100%" in adequacy.saving_basis


def test_floor_rule_below_demand_yields_no_saving(sample_usage: UsageData) -> None:
    """하한이 낮아 요금적용전력에 걸리지 않으면 계약을 내려도 요금은 그대로다."""
    adequacy = assess_contract(
        sample_usage.kw,
        contract_kw=7_000.0,
        billing_demand_kw=5_293.44,
        base_rate_won_per_kw=7_220.0,
        base_fee_months=12.0,
        contract_floor_ratio=0.3,
    )
    assert adequacy.saving_won == pytest.approx(0.0)


def test_contract_warnings_include_the_penalty_notice(sample_diagnosis: Diagnosis) -> None:
    """하향은 되돌리기 어렵고 초과 시 위약금이 있다. 여유 확보 권고를 함께 낸다."""
    adequacy = sample_diagnosis.contract
    assert adequacy is not None
    assert any("여유를 확보" in message for message in texts(adequacy.notices))
    assert any("12개월간 적용" in message for message in texts(adequacy.notices))
    assert any("여유를 확보" in message for message in texts(sample_diagnosis.notices))


def test_over_contract_slots_are_flagged(sample_usage: UsageData) -> None:
    adequacy = assess_contract(
        sample_usage.kw,
        contract_kw=5_000.0,
        billing_demand_kw=5_293.44,
        base_rate_won_per_kw=7_220.0,
        base_fee_months=12.0,
    )
    assert adequacy.over_contract_slots > 0
    assert any("넘은 구간" in message for message in texts(adequacy.notices))


def test_invalid_contract_info_raises() -> None:
    with pytest.raises(ValueError, match="계약전력"):
        ContractInfo(CURRENT, contract_kw=0.0)
    with pytest.raises(ValueError, match="역률"):
        ContractInfo(CURRENT, power_factor_pct=0.0)


# --------------------------------------------------------------------- 6.5 개선 여지


def test_tariff_switch_saving_is_recalculated(sample_diagnosis: Diagnosis) -> None:
    """조합마다 요금을 다시 계산한다. 빼기로 어림하지 않는다."""
    summary = sample_diagnosis.summary
    assert summary.best_selection == TariffSelection("general_b", "high_a", "II")
    assert summary.current_total_won is not None
    assert summary.best_total_won is not None
    assert summary.tariff_switch_saving_won == pytest.approx(
        summary.current_total_won - summary.best_total_won
    )
    assert summary.tariff_switch_saving_won == pytest.approx(53_575_280.0, rel=1e-4)


def test_every_option_is_priced_but_only_totals_are_kept(sample_diagnosis: Diagnosis) -> None:
    """조합은 순차 처리하고 합계만 남긴다. 월별 명세는 현행 조합만 들고 있다.

    **현행 종별·전압 안에서만 돈다.** 고압A 수전 건물이므로 선택Ⅰ·Ⅱ·Ⅲ 셋뿐이다.
    """
    totals = sample_diagnosis.option_totals
    assert set(totals) == {
        "general_b/high_a/I",
        "general_b/high_a/II",
        "general_b/high_a/III",
    }
    assert all(isinstance(value, float) for value in totals.values())
    assert min(totals, key=lambda key: totals[key]) == "general_b/high_a/II"


def test_summary_lines_are_ready_for_the_screen(sample_diagnosis: Diagnosis) -> None:
    lines = sample_diagnosis.summary.lines
    assert len(lines) == 3
    assert lines[0].startswith("선택요금 전환")
    assert "5,358만원" in lines[0]
    assert "투자 불필요" in lines[1]
    assert "높음" in lines[2]


def test_contract_saving_is_zero_when_the_floor_does_not_bind(
    sample_diagnosis: Diagnosis,
) -> None:
    """하한 30% 가 확인됐다. 다만 이 건물은 하한이 걸리지 않아 절감액이 0 이다.

    계약 5,500 kW 의 하한은 1,650 kW 인데 요금적용전력이 5,293 kW 라 훨씬 위에 있다.
    """
    summary = sample_diagnosis.summary
    assert summary.contract_saving_won == pytest.approx(0.0)
    assert summary.no_investment_saving_won == pytest.approx(
        summary.tariff_switch_saving_won or 0.0
    )


def test_quality_warnings_are_carried_into_the_diagnosis(sample_diagnosis: Diagnosis) -> None:
    assert any("신뢰 제한" in message for message in texts(sample_diagnosis.notices))
    assert any("직전 12개월" in message for message in texts(sample_diagnosis.notices))


# --------------------------------------------------------------------- 5.2 ① 경부하 제외


def _demand_mask(
    usage: UsageData, table: TariffTable, *, contract_type: str = "general_b"
) -> pd.Series:
    """진단이 만드는 것과 같은 요금적용전력 대상 슬롯 마스크."""
    index = pd.DatetimeIndex(usage.kw.index)
    calendar = build_calendar(range(index[0].year - 1, index[-1].year + 2))
    slots = classify_slots(
        index, usage.meta.interval_minutes, table, calendar, contract_type=contract_type
    )
    return demand_eligible_mask(
        slots["band"], demand_bands=table.contract(contract_type).demand_bands
    )


def test_sample_grade_is_unchanged_by_the_light_band_exclusion(
    sample_diagnosis: Diagnosis,
) -> None:
    """샘플의 상위 100구간은 07~17시라 마스크를 씌워도 등급이 그대로다.

    바뀌는 것은 딱 한 칸이다. 라벨 07:45(구간 시작 07:30)는 경부하라 대상에서
    빠지고 그 자리를 09:45 가 채운다. 정오 비율 66% 와 '높음' 은 그대로다.
    **이 무영향을 회귀로 못 박는다** — 야간 피크형에서만 결과가 달라져야 한다.
    """
    peak = sample_diagnosis.peak
    assert peak.demand_eligible_applied
    assert peak.demand_hour_share(range(10, 15)) == pytest.approx(0.66)
    assert peak.hour_share(range(10, 15)) == pytest.approx(0.66)
    assert sample_diagnosis.summary.pv_potential is PvPotential.HIGH

    raw = set(pd.DatetimeIndex(peak.top_slots.index))
    masked = set(pd.DatetimeIndex(peak.demand_top_slots.index))
    assert raw - masked == {pd.Timestamp("2023-08-02 07:45")}
    assert masked - raw == {pd.Timestamp("2023-08-02 09:45")}
    assert min(peak.demand_top_slots["hour"]) == 8
    assert max(peak.demand_top_slots["hour"]) == 17


def test_appendix_b_distribution_is_kept_separate_from_the_masked_one(
    sample_diagnosis: Diagnosis,
) -> None:
    """부록 B 원값(전 슬롯)과 마스크 적용 값을 함께 담되 섞지 않는다."""
    peak = sample_diagnosis.peak
    raw = {hour: int(value) for hour, value in peak.hour_counts.items() if value}
    masked = {hour: int(value) for hour, value in peak.demand_hour_counts.items() if value}
    assert raw == {7: 1, 8: 6, 9: 5, 10: 15, 11: 14, 12: 20, 13: 8, 14: 9, 15: 9, 16: 10, 17: 3}
    assert masked == {8: 6, 9: 6, 10: 15, 11: 14, 12: 20, 13: 8, 14: 9, 15: 9, 16: 10, 17: 3}
    assert raw != masked  # 원값에는 경부하 07시가 남아 있다
    assert int(peak.hour_counts.sum()) == int(peak.demand_hour_counts.sum()) == 100
    assert peak.demand_eligible_slots < peak.observed_slots


def test_holidays_never_reach_the_demand_population(sample_diagnosis: Diagnosis) -> None:
    """공휴일·일요일은 전량 경부하로 계량되므로 대상 모집단에 들 수 없다."""
    assert sample_diagnosis.peak.demand_weekend_slots == 0


@pytest.fixture
def night_peak_usage(tmp_path_factory: pytest.TempPathFactory) -> UsageData:
    """야간 최대, 정오 차순인 한 달치. 마스크로 등급이 뒤바뀌는 경로다."""
    from kwise.io import load_usage
    from tests._synthetic import night_peak_month

    return load_usage(night_peak_month(tmp_path_factory.mktemp("night") / "night.csv"))


def test_night_peak_grade_flips_when_the_light_band_is_excluded(
    night_peak_usage: UsageData, tariff: TariffTable
) -> None:
    """야간 피크형에서는 마스크 적용 여부로 등급이 정반대가 된다.

    전 슬롯을 모집단으로 삼으면 상위 100구간이 전부 경부하(22~08시)라 '낮음'이지만,
    경부하는 애초에 요금적용전력 대상이 아니다. 대상 슬롯만 남기면 정오가 상위를
    채워 '높음'이 된다. 태양광의 기본요금 기여는 후자가 맞다.
    """
    kw = night_peak_usage.kw
    unmasked = peak_profile(kw, 15)
    masked = peak_profile(kw, 15, demand_eligible=_demand_mask(night_peak_usage, tariff))

    assert judge_pv_potential(unmasked)[0] is PvPotential.LOW
    assert judge_pv_potential(masked)[0] is PvPotential.HIGH
    assert unmasked.hour_share(range(10, 15)) == pytest.approx(0.0)
    assert masked.demand_hour_share(range(10, 15)) > 0.9

    # 원값은 마스크와 무관하게 같다. 두 벌이 섞이지 않는다는 증거다.
    assert masked.hour_counts.to_dict() == unmasked.hour_counts.to_dict()
    assert not unmasked.demand_eligible_applied
    assert masked.demand_eligible_applied


def test_night_peak_diagnosis_uses_the_masked_population(
    night_peak_usage: UsageData, tariff: TariffTable
) -> None:
    """진단을 통째로 돌려도 같다. 판정 모집단이 산출물에 적힌다."""
    result = diagnose(night_peak_usage, tariff, ContractInfo(CURRENT, contract_kw=2_000.0))
    assert result.summary.pv_potential is PvPotential.HIGH
    assert "요금적용전력 대상 슬롯" in result.summary.pv_basis
    assert "부록 B" in result.summary.pv_basis
    # 야간 최대 2,000 kW 는 요금적용전력이 되지 못한다.
    assert result.peak.peak_kw == pytest.approx(2_000.0)
    assert result.peak.billing_demand_kw == pytest.approx(1_200.0)


def test_basis_says_so_when_no_mask_was_given() -> None:
    """마스크가 없으면 그 사실을 적는다. 조용히 전 슬롯으로 판정하지 않는다."""
    from kwise.diagnose import pv_basis_label

    kw = pd.Series(100.0, index=pd.date_range("2024-03-01 00:15", periods=96 * 5, freq="15min"))
    basis = pv_basis_label(peak_profile(kw, 15))
    assert "마스크를 받지 않아" in basis
    assert "계약종별 미입력" in basis
