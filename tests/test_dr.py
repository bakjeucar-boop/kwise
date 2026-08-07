"""경제성DR 참여 여력 (요구사항서 6.6, 7.3 / 전력시장운영규칙 제12장).

**거래일 제약이 이 모듈의 전부다.** 제12.4.2.1조 제1항 1호는 "관공서의 공휴일에
관한 규정"의 공휴일과 **토요일**을 제외한 평일에만 입찰할 수 있게 한다. 이 제약을
빼면 감축 가능량이 30% 이상 과대평가된다.

**요금 계량의 '평일' 과 정의가 다르다.** 요금은 토요일을 최대부하 → 중간부하로
낮출 뿐 공휴일로 보지 않지만, DR 은 토·일·공휴일이 모두 똑같이 제외다.
같은 함수로 판정하면 두 규칙이 조용히 섞인다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from kwise.diagnose import (
    DR_REFERENCE_CAPACITY_KW,
    Diagnosis,
    DrPotential,
    DrProfile,
    DrResourceType,
    dr_day_mask,
    dr_eligible_days,
    dr_profile,
    judge_resource_types,
)
from kwise.io import UsageData
from kwise.measures import (
    Certainty,
    evaluate_demand_response,
    shortfall_penalty_won,
)
from kwise.measures.demand_response import UNPRICED_REASON
from kwise.quality import QualityReport, load_pattern
from kwise.tariff import BillingResult, TariffTable, build_calendar, classify_slots

# 2024-03: 01(금, 삼일절) 02(토) 03(일) … 30(토) 31(일)
MARCH_INDEX = pd.date_range("2024-03-01 00:15", "2024-04-01 00:00", freq="15min")


@pytest.fixture(scope="module")
def calendar() -> object:
    return build_calendar(range(2023, 2026))


# --------------------------------------------------------------------- 거래일 (제12.4.2.1조)


def test_saturday_sunday_and_holiday_are_all_excluded(calendar: object) -> None:
    """토·일·공휴일이 **모두** 빠진다. 일요일은 공휴일 규정에 포함된다."""
    days = dr_eligible_days(MARCH_INDEX, 15, calendar)  # type: ignore[arg-type]
    assert pd.Timestamp("2024-03-01") not in days  # 금요일이지만 삼일절
    assert pd.Timestamp("2024-03-02") not in days  # 토요일
    assert pd.Timestamp("2024-03-03") not in days  # 일요일
    assert pd.Timestamp("2024-03-04") in days  # 월요일
    assert pd.Timestamp("2024-03-08") in days  # 금요일
    assert all(day.weekday() < 5 for day in days)
    # 3월 평일 21일 중 삼일절 하루를 뺀 20일
    assert len(days) == 20


def test_dr_weekday_differs_from_the_tariff_weekday(calendar: object, tariff: TariffTable) -> None:
    """**토요일에서 갈린다.** 요금은 중간부하로 낮출 뿐 공휴일이 아니다.

    이 차이 때문에 DR 거래일 판정을 tariff 와 따로 둔다.
    """
    saturday = pd.date_range("2024-03-09 00:15", "2024-03-10 00:00", freq="15min")
    slots = classify_slots(saturday, 15, tariff, calendar)  # type: ignore[arg-type]

    # 요금 규칙에서 토요일은 공휴일이 아니다 (day_type 이 saturday 이고 경부하도 아니다).
    assert set(slots["day_type"]) == {"saturday"}
    assert not bool(slots["is_holiday"].any())
    assert set(slots["band"]) != {"light"}  # 전량 경부하로 접히지 않는다

    # DR 에서는 통째로 빠진다.
    assert len(dr_eligible_days(saturday, 15, calendar)) == 0  # type: ignore[arg-type]
    assert not bool(dr_day_mask(saturday, 15, calendar).any())  # type: ignore[arg-type]


def test_dr_day_mask_follows_the_slot_start_convention(calendar: object) -> None:
    """라벨은 구간 끝이다. ``03-05 00:00`` 슬롯은 04일 23:45~24:00 이라 4일에 속한다."""
    index = pd.DatetimeIndex(["2024-03-05 00:00", "2024-03-05 00:15", "2024-03-11 00:00"])
    mask = dr_day_mask(index, 15, calendar)  # type: ignore[arg-type]
    assert list(mask) == [True, True, False]  # 3번째는 10일(일요일) 소속


def test_sample_eligible_days_are_about_two_thirds(sample_diagnosis: Diagnosis) -> None:
    """**이 제약을 빼면 감축 가능량이 30% 이상 과대평가된다.**"""
    profile = sample_diagnosis.dr
    assert profile is not None
    assert profile.total_days == 359
    assert profile.eligible_days == 245
    assert profile.excluded_days == 114
    assert profile.eligible_day_ratio == pytest.approx(0.682, abs=0.005)
    # 전체 일수로 재면 46% 부풀려진다.
    assert profile.total_days / profile.eligible_days > 1.30


# --------------------------------------------------------------------- 자원 유형 (제12.1.1조)


@pytest.mark.parametrize(
    ("contract_type", "contract_kw", "expected"),
    [
        # 국민DR — 계약전력 200 kW 이하
        ("general_b", 200.0, ("표준DR", "중소형DR", "국민DR")),
        ("general_b", 200.1, ("표준DR", "중소형DR")),
        # 중소형DR — 산업용은 2 MW 이하
        ("industrial_b", 2_000.0, ("표준DR", "중소형DR")),
        ("industrial_b", 2_000.1, ("표준DR",)),
        ("industrial_b", 5_500.0, ("표준DR",)),
        # 일반용·교육용은 용량 제한 없이 중소형DR
        ("general_b", 5_500.0, ("표준DR", "중소형DR")),
        ("education_b", 5_500.0, ("표준DR", "중소형DR")),
        # 표준DR 은 계약종별 제한이 없다
        (None, None, ("표준DR",)),
    ],
)
def test_resource_type_boundaries(
    contract_type: str | None, contract_kw: float | None, expected: tuple[str, ...]
) -> None:
    types = judge_resource_types(contract_type, contract_kw)
    assert tuple(str(item) for item in types) == expected


def test_standard_dr_is_always_available() -> None:
    """표준DR 은 계약종별 제한이 없다 (제12.1.1조)."""
    for contract_type in ("general_a_1", "industrial_b", "education_a", None):
        assert DrResourceType.STANDARD in judge_resource_types(contract_type, 9_999.0)


def test_sample_is_standard_and_small_medium(sample_diagnosis: Diagnosis) -> None:
    """일반용(을) 5,500 kW — 국민DR 은 아니다."""
    profile = sample_diagnosis.dr
    assert profile is not None
    assert profile.resource_types == (DrResourceType.STANDARD, DrResourceType.SMALL_MEDIUM)


# --------------------------------------------------------------------- 감축 여력


def test_capacity_reuses_the_base_load_ratio(sample_diagnosis: Diagnosis) -> None:
    """6.1 의 기저부하 비율을 그대로 쓴다. 다시 계산하지 않는다."""
    profile = sample_diagnosis.dr
    assert profile is not None
    ratio = sample_diagnosis.pattern.base_load_ratio
    assert ratio is not None
    assert profile.base_load_ratio == ratio
    assert profile.day_mean_kw is not None
    assert profile.base_load_kw == pytest.approx(profile.day_mean_kw * ratio)


def test_registered_capacity_is_conservative(sample_diagnosis: Diagnosis) -> None:
    """**등록 권장값은 평균이 아니라 하위 10% 기준이다.**

    평균으로 등록하면 절반의 날에 미달해 위약금이 난다 (별표26).
    """
    profile = sample_diagnosis.dr
    assert profile is not None
    assert profile.day_floor_kw is not None and profile.day_mean_kw is not None
    assert profile.day_floor_kw < profile.day_mean_kw
    assert 0 < profile.registered_capacity_kw < profile.mean_reducible_kw
    assert profile.registered_capacity_kw == pytest.approx(632, abs=5)
    assert profile.mean_reducible_kw == pytest.approx(1_390, abs=5)


def test_registration_and_low_load_percentiles_are_separate() -> None:
    """등록 분위수(10%)와 저부하일 문턱(5%)은 쓰임이 다른 값이다."""
    from kwise.diagnose.dr import LOW_LOAD_PERCENTILE, REGISTRATION_PERCENTILE

    assert REGISTRATION_PERCENTILE == 0.10
    assert LOW_LOAD_PERCENTILE == 0.05
    assert REGISTRATION_PERCENTILE != LOW_LOAD_PERCENTILE


def test_annual_reduction_sums_daily_headroom(sample_diagnosis: Diagnosis) -> None:
    """**연간 감축 가능량은 등록값 × 일수가 아니라 거래일별 여력의 합이다.**

    경제성DR 은 하루 전 입찰이라 매일 다른 양을 입찰한다. 등록값으로 곱하면
    부하가 많은 날의 여력을 통째로 버려 연간 수익이 크게 과소평가된다.
    """
    profile = sample_diagnosis.dr
    assert profile is not None
    daily = profile.daily_reducible_kw
    assert len(daily) == profile.eligible_days == 245
    assert (daily >= 0).all()  # 기저부하 아래로 내려가지 않는다

    annual = profile.annual_reducible_kwh(1.0)
    assert annual == pytest.approx(float(daily.sum()))
    assert annual == pytest.approx(341_921, rel=1e-3)

    flat = profile.registered_capacity_kw * 1.0 * profile.eligible_days
    assert flat < annual  # 등록값으로 곱하면 과소평가된다
    assert 1 - flat / annual == pytest.approx(0.55, abs=0.02)


def test_bid_hours_scale_the_annual_reduction(sample_diagnosis: Diagnosis) -> None:
    profile = sample_diagnosis.dr
    assert profile is not None
    assert profile.annual_reducible_kwh(2.0) == pytest.approx(profile.annual_reducible_kwh(1.0) * 2)
    with pytest.raises(ValueError, match="입찰 지속시간"):
        profile.annual_reducible_kwh(0.0)


def test_potential_grades_by_capacity(sample_diagnosis: Diagnosis) -> None:
    profile = sample_diagnosis.dr
    assert profile is not None
    assert profile.potential is DrPotential.HIGH  # 등록 권장 632 kW ≥ 500
    assert profile.meets_reference_capacity  # 참고 문턱 100 kW


def test_reference_threshold_is_a_resource_level_note(
    sample_usage: UsageData, sample_report: QualityReport, calendar: object
) -> None:
    """0.1 MW-h 문턱은 **자원 단위** 기준이다. 개별 고객 판정으로 쓰지 않는다."""
    assert DR_REFERENCE_CAPACITY_KW == 100.0
    tiny = sample_usage.kw * 0.02  # 등록 가능 용량이 문턱 아래로 내려간다
    profile = dr_profile(
        tiny,
        15,
        calendar,  # type: ignore[arg-type]
        pattern=load_pattern(tiny, 15),
        contract_type="general_b",
        contract_kw=110.0,
    )
    assert not profile.meets_reference_capacity
    assert profile.potential is DrPotential.LOW
    assert any("묶은 자원 단위" in message for message in profile.warnings)


# --------------------------------------------------------------------- 무비용 감축 가능일


def test_low_load_threshold_uses_dr_days_only(sample_diagnosis: Diagnosis) -> None:
    """**기준선은 거래 가능일만으로 계산한다.**

    주말이 섞이면 기준선이 내려가 평일 저부하일이 걸리지 않는다.
    """
    profile = sample_diagnosis.dr
    assert profile is not None
    assert profile.low_load_threshold_kw == pytest.approx(2_516, abs=5)
    assert len(profile.low_load_days) == 13
    # 뽑힌 날은 모두 거래 가능일이다 — 주말·공휴일이 섞이지 않는다.
    assert all(day.weekday() < 5 for day in profile.low_load_days)
    assert any("거래 가능일만으로 계산" in note for note in profile.notes)


def test_threshold_would_drop_if_weekends_were_mixed_in(
    sample_usage: UsageData, calendar: object
) -> None:
    """회귀로 고정 — 전 일자로 재면 기준선이 내려간다."""
    from kwise.io import slot_start

    starts = slot_start(pd.DatetimeIndex(sample_usage.kw.dropna().index), 15)
    day_of = pd.Series(starts.normalize(), index=sample_usage.kw.dropna().index)
    all_day_mean = sample_usage.kw.dropna().groupby(day_of).mean()
    mixed_threshold = float(all_day_mean.quantile(0.05))

    profile = dr_profile(
        sample_usage.kw,
        15,
        calendar,  # type: ignore[arg-type]
        pattern=load_pattern(sample_usage.kw, 15),
        contract_type="general_b",
        contract_kw=5_500.0,
    )
    assert profile.low_load_threshold_kw is not None
    assert mixed_threshold < profile.low_load_threshold_kw  # 주말이 기준선을 끌어내린다


def test_outage_days_are_excluded_from_low_load_days(
    sample_usage: UsageData, calendar: object
) -> None:
    """정전으로 부하가 낮았던 날은 '무비용 감축 가능' 이 아니다."""
    pattern = load_pattern(sample_usage.kw, 15)
    without = dr_profile(
        sample_usage.kw,
        15,
        calendar,  # type: ignore[arg-type]
        pattern=pattern,
        contract_type="general_b",
    )
    blanket = pd.Series(True, index=sample_usage.kw.index)  # 전 구간을 정전으로 본다
    with_outage = dr_profile(
        sample_usage.kw,
        15,
        calendar,  # type: ignore[arg-type]
        pattern=pattern,
        contract_type="general_b",
        outage_mask=blanket,
    )
    assert len(without.low_load_days) > 0
    assert len(with_outage.low_load_days) == 0


def test_empty_series_is_rejected(calendar: object) -> None:
    empty = pd.Series(float("nan"), index=pd.date_range("2024-03-01", periods=8, freq="15min"))
    with pytest.raises(ValueError, match="관측된 수요가 없어"):
        dr_profile(empty, 15, calendar, pattern=load_pattern(sample := empty.fillna(1.0), 15))  # type: ignore[arg-type]
    assert sample is not None


# --------------------------------------------------------------------- 7.3 수단


def test_reduction_is_counted_on_dr_days_only(sample_diagnosis: Diagnosis) -> None:
    """연간 감축 가능량은 **거래 가능일 기준으로만** 낸다."""
    profile = sample_diagnosis.dr
    assert profile is not None
    result = evaluate_demand_response(profile, bid_hours_per_day=1.0)
    assert result.eligible_days == 245
    assert result.annual_reducible_kwh == pytest.approx(profile.annual_reducible_kwh(1.0))
    assert result.low_cost_reduction_kwh == pytest.approx(profile.low_cost_reducible_kwh(1.0))
    assert result.investment_won == 0.0
    assert result.certainty is Certainty.MEDIUM


def test_two_capacities_have_distinct_roles(sample_diagnosis: Diagnosis) -> None:
    """등록 권장값은 계약용, 연간 감축량은 수익 추정용이다. 섞지 않는다."""
    profile = sample_diagnosis.dr
    assert profile is not None
    result = evaluate_demand_response(profile)
    assert result.registered_capacity_kw == pytest.approx(profile.registered_capacity_kw)
    assert result.flat_reduction_kwh < result.annual_reducible_kwh
    assert any("거래일별 여력을 합산한 값입니다" in note for note in result.notes)
    assert any("과소평가" in note for note in result.notes)


def test_missing_price_returns_a_reason_not_an_amount(sample_diagnosis: Diagnosis) -> None:
    """**정산 단가는 우리가 만들 수 없다.** 없으면 감축량만 내고 사유를 적는다."""
    profile = sample_diagnosis.dr
    assert profile is not None
    result = evaluate_demand_response(profile)
    assert result.unit_price_won_per_kwh is None
    assert result.settlement_won is None
    assert not result.is_priced
    assert result.settlement_label == UNPRICED_REASON
    assert "순편익가격" in result.settlement_label
    assert result.annual_reducible_kwh > 0  # 감축량은 낸다
    assert any("정산 단가를 입력하지 않아" in message for message in result.warnings)


def test_price_produces_a_settlement(sample_diagnosis: Diagnosis) -> None:
    profile = sample_diagnosis.dr
    assert profile is not None
    result = evaluate_demand_response(profile, unit_price_won_per_kwh=150.0)
    assert result.settlement_won == pytest.approx(result.annual_reducible_kwh * 150.0)
    assert result.low_cost_settlement_won == pytest.approx(result.low_cost_reduction_kwh * 150.0)
    assert result.settlement_label != UNPRICED_REASON


def test_shortfall_penalty_follows_appendix_26() -> None:
    """실적위약금 = (감축계획량 − 실제감축량) × Max(하루전에너지가격, 0)."""
    assert shortfall_penalty_won(100.0, 60.0, 1.0, 120.0) == pytest.approx(4_800.0)
    assert shortfall_penalty_won(100.0, 60.0, 2.0, 120.0) == pytest.approx(9_600.0)
    assert shortfall_penalty_won(100.0, 100.0, 1.0, 120.0) == 0.0  # 계획을 채웠다
    assert shortfall_penalty_won(100.0, 130.0, 1.0, 120.0) == 0.0  # 넘겨도 0
    assert shortfall_penalty_won(100.0, 0.0, 1.0, -50.0) == 0.0  # 음수 가격은 0 으로


def test_penalty_risk_is_reported(sample_diagnosis: Diagnosis) -> None:
    """투자비는 0원이지만 리스크는 0이 아니다."""
    profile = sample_diagnosis.dr
    assert profile is not None
    result = evaluate_demand_response(profile, day_ahead_price_won_per_kwh=120.0)
    assert result.penalty_per_shortfall_kw_won == pytest.approx(120.0)
    assert any("실적위약금" in message for message in result.warnings)

    without = evaluate_demand_response(profile)
    assert without.penalty_per_shortfall_kw_won is None
    assert any("하루전에너지가격" in message for message in without.warnings)


def test_base_fee_saving_is_not_claimed(sample_diagnosis: Diagnosis) -> None:
    """SMP 기준 산발 입찰이라 연중 최대수요일과 겹칠 확률이 낮다. 편익은 정산금뿐이다."""
    profile = sample_diagnosis.dr
    assert profile is not None
    result = evaluate_demand_response(profile, unit_price_won_per_kwh=150.0)
    assert not hasattr(result, "base_saving_won")
    assert any("기본요금 절감은 계산하지 않았습니다" in note for note in result.notes)


def test_advisory_names_the_aggregator(sample_diagnosis: Diagnosis) -> None:
    """수요관리사업자를 통해서만 참여할 수 있다는 것을 반드시 적는다."""
    profile = sample_diagnosis.dr
    assert profile is not None
    result = evaluate_demand_response(profile)
    joined = " ".join(result.notes)
    assert "수요관리사업자를 통해서만" in joined
    assert "위약금 조항은 사업자와 상담" in joined


def test_invalid_inputs_are_rejected(sample_diagnosis: Diagnosis) -> None:
    profile = sample_diagnosis.dr
    assert profile is not None
    with pytest.raises(ValueError, match="입찰 지속시간"):
        evaluate_demand_response(profile, bid_hours_per_day=0.0)
    with pytest.raises(ValueError, match="감축계획량"):
        evaluate_demand_response(profile, reduction_kw=-1.0)


def test_profile_is_carried_in_the_diagnosis(sample_diagnosis: Diagnosis) -> None:
    """6.6 은 진단 한 벌에 들어 있다. 설비 정보를 묻지 않는다."""
    assert isinstance(sample_diagnosis.dr, DrProfile)
    assert sample_diagnosis.dr.eligible_days > 0


# --------------------------------------------------------------------- 산출물


def test_diagnosis_sheet_carries_the_dr_rows(
    sample_diagnosis: Diagnosis, sample_usage: UsageData, sample_bill: BillingResult
) -> None:
    """진단 시트에 거래 가능일과 등록 가능 용량이 실린다 (6.6)."""
    from kwise.report import ReportSections, build_sheets

    profile = sample_diagnosis.dr
    assert profile is not None
    usage, bill = sample_usage, sample_bill
    sections = ReportSections(
        usage=usage, bill=bill, diagnosis=sample_diagnosis, include_timeseries=False
    )
    values = build_sheets(sections)["진단"]["값"]
    assert values["DR 거래 가능일"] == f"{profile.eligible_days}일 / 전체 {profile.total_days}일"
    assert values["DR 제외일 (토·일·공휴일)"] == f"{profile.excluded_days}일"
    assert values["DR 적합성"] == str(profile.potential)
    assert "표준DR" in values["DR 자원 유형"]


def test_measure_sheet_shows_the_reason_when_price_is_missing(
    sample_diagnosis: Diagnosis,
) -> None:
    """수단별 시트의 DR 행은 빈칸이 아니라 사유로 채워진다."""
    from kwise.report import measure_summary_frame

    profile = sample_diagnosis.dr
    assert profile is not None
    row = measure_summary_frame(demand_response=evaluate_demand_response(profile)).iloc[0]
    assert row["투자비(원)"] == 0.0
    assert row["절감액(원)"] == UNPRICED_REASON
    assert str(row["확실성"]) == str(Certainty.MEDIUM)
    assert "수요관리사업자를 통해서만" in row["비고"]
    assert "실적위약금" in row["비고"]
