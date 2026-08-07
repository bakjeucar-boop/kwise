"""ESS 단가·차익거래·방전시간 (요구사항서 7.6).

**참고단가는 추정치가 아니라 하한선이다.** 계통용 대형 ESS 기준이고 안전 규제
대응비와 연계공사비가 빠져 있다. 그래서 손익분기와의 비교가 비대칭이고, 참고값이
기본값으로 조용히 적용되어서도 안 된다. 그 두 가지를 여기서 못 박는다.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from kwise.io import UsageData
from kwise.measures import (
    DEFAULT_ROUND_TRIP,
    HIGH_RATE_DISCHARGE_HOURS,
    EssCostInput,
    EssCostReferenceError,
    EssResult,
    analyze_peak_excess,
    arbitrage_value,
    c_rate,
    ess_payback_curve,
    evaluate_ess,
    light_band_mask,
    load_ess_cost_reference,
    peak_days_by_season,
    reference_table,
    required_discharge_hours,
)
from kwise.measures.ess_cost import reference_data_path
from kwise.quality import QualityReport
from kwise.tariff import BillingResult, TariffTable

from .conftest import SAMPLE_SELECTION

TARGET_KW = 5_200.0  # 부록 B 초과 분석 표의 첫 줄


# --------------------------------------------------------------------- 참고단가


def test_reference_comes_from_the_data_file() -> None:
    """출처를 데이터에 적는다. 코드에 값을 두지 않는다."""
    with reference_data_path().open(encoding="utf-8") as stream:
        payload = json.load(stream)
    source = payload["source"]
    assert source["publisher"] == "에너지경제연구원"
    assert source["basis_year"] == 2025
    assert source["exchange_rate_won_per_usd"] == 1412.0
    assert "LCOS" in source["title"]

    reference = load_ess_cost_reference()
    assert "에너지경제연구원" in reference.citation
    assert "1,412원/달러" in reference.citation


def test_two_component_capex_is_stored_as_given() -> None:
    """원본은 2성분이다. 환산은 조회 시점에 한다."""
    reference = load_ess_cost_reference()
    lfp = reference.technology("lfp_2025")
    assert lfp.capex_power_won_per_kw == 245_295.0
    assert lfp.capex_energy_won_per_kwh == 369_936.0
    assert lfp.opex_power_won_per_kw_year == 10_770.0


def test_pumped_hydro_is_not_included() -> None:
    """양수발전은 건물 대상이 아니다."""
    reference = load_ess_cost_reference()
    keys = {item.key for item in reference.technologies}
    assert keys == {"lfp_2025", "ncm_2025", "lfp_2030", "ncm_2030"}


@pytest.mark.parametrize(
    ("hours", "lfp_2025", "lfp_2030"),
    [
        (0.5, 430_263.0, 355_312.5),
        (1.0, 615_231.0, 497_400.0),
        (2.0, 985_167.0, 781_575.0),
        (4.0, 1_725_039.0, 1_349_925.0),
    ],
)
def test_unit_cost_per_kw_conversion(hours: float, lfp_2025: float, lfp_2030: float) -> None:
    """``kW당 단가 = CAPEX_Power + CAPEX_Energy × 방전시간``."""
    reference = load_ess_cost_reference()
    assert reference.technology("lfp_2025").unit_cost_won_per_kw(hours) == pytest.approx(lfp_2025)
    assert reference.technology("lfp_2030").unit_cost_won_per_kw(hours) == pytest.approx(lfp_2030)


def test_reference_table_marks_the_calculated_discharge_hours() -> None:
    """8세션 UI 가 산출된 방전시간 행을 강조한다."""
    frame = reference_table(discharge_hours=(0.5, 1.0, 2.0), highlight_hours=1.0)
    assert frame.loc[1.0, "산출 사양"] == "◀ 산출된 방전시간"
    assert frame.loc[0.5, "산출 사양"] == ""


def test_reference_is_never_auto_applied() -> None:
    """**참고값이 기본값으로 자동 적용되면 안 된다.** 명시적으로 골라야 한다."""
    reference = load_ess_cost_reference()
    assert reference.auto_apply is False

    # 단가 입력은 기본값이 없다. 둘 중 하나를 반드시 준다.
    with pytest.raises(ValueError, match="정확히 하나"):
        EssCostInput()
    with pytest.raises(ValueError, match="정확히 하나"):
        EssCostInput(unit_cost_won_per_kw=500_000.0, total_won=1_000_000.0)

    # 참고단가를 쓰려면 from_reference 를 **직접** 부른다.
    picked = EssCostInput.from_reference(1.0)
    assert picked.unit_cost_won_per_kw == pytest.approx(615_231.0)
    assert "참고단가" in picked.source


def test_auto_apply_true_is_rejected(tmp_path: object) -> None:
    """설정에서 자동 적용을 켜도 막는다. 출처가 다른 값이 견적으로 둔갑한다."""
    from pathlib import Path

    with reference_data_path().open(encoding="utf-8") as stream:
        payload = json.load(stream)
    payload["auto_apply"] = True
    target = Path(str(tmp_path)) / "ess_cost_reference.json"
    with target.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False)
    with pytest.raises(EssCostReferenceError, match="자동 적용"):
        load_ess_cost_reference(str(target))


def test_verdict_is_asymmetric() -> None:
    """하한선 > 손익분기 → 강한 판정. 하한선 < 손익분기 → 약한 판정."""
    reference = load_ess_cost_reference()
    lower_bound = reference.default.unit_cost_won_per_kw(1.0)

    expensive = reference.verdict(lower_bound / 2, 1.0)
    assert "경제성 없음" in expensive and "강한 판정" in expensive

    cheap = reference.verdict(lower_bound * 2, 1.0)
    assert "견적을 받아볼 가치" in cheap and "약한 판정" in cheap
    assert "하한선" in cheap  # 실제 견적은 이보다 높다


# --------------------------------------------------------------------- 단가 입력


def test_investment_is_power_times_unit_cost(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
) -> None:
    """**투자비 = 출력 × kW당 단가.** 방전시간을 다시 곱하지 않는다."""
    mask = light_band_mask(sample_usage, tariff, selection=SAMPLE_SELECTION)
    result = evaluate_ess(
        sample_usage,
        tariff,
        SAMPLE_SELECTION,
        target_kw=TARGET_KW,
        cost=EssCostInput.of_unit_cost(500_000.0),
        charge_mask=mask,
        baseline=sample_bill,
        quality=sample_report,
    )
    assert result.investment_won == pytest.approx(result.power_kw * 500_000.0)


def test_total_investment_path_wins(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
) -> None:
    """견적서를 받았으면 총액을 그대로 쓴다."""
    mask = light_band_mask(sample_usage, tariff, selection=SAMPLE_SELECTION)
    result = evaluate_ess(
        sample_usage,
        tariff,
        SAMPLE_SELECTION,
        target_kw=TARGET_KW,
        cost=EssCostInput.of_total(88_000_000.0),
        charge_mask=mask,
        baseline=sample_bill,
        quality=sample_report,
    )
    assert result.investment_won == 88_000_000.0
    assert result.cost.is_total


# --------------------------------------------------------------------- 방전시간


def test_discharge_hours_is_calculated_not_forced(sample_usage: UsageData) -> None:
    """방전시간 = 하루 최대 초과 에너지 ÷ 최대 초과 출력."""
    excess = analyze_peak_excess(sample_usage.kw, TARGET_KW, sample_usage.meta.interval_minutes)
    hours = required_discharge_hours(excess)
    assert hours == pytest.approx(excess.max_daily_excess_kwh / excess.max_excess_kw)
    assert 0.3 < hours < 0.5  # 샘플은 짧다


def test_c_rate_warning_below_half_an_hour(sample_ess: EssResult) -> None:
    """0.5h 미만이면 고출력 셀 사양임을 경고한다."""
    assert sample_ess.discharge_hours < HIGH_RATE_DISCHARGE_HOURS
    assert sample_ess.c_rate == pytest.approx(1.0 / sample_ess.discharge_hours)
    assert any("고출력 셀" in message for message in sample_ess.warnings)
    assert any("C 방전" in message for message in sample_ess.warnings)


def test_no_warning_when_the_spec_is_ordinary() -> None:
    assert c_rate(1.0) == 1.0
    assert c_rate(2.0) == 0.5


# --------------------------------------------------------------------- 차익거래


def test_arbitrage_rates_come_from_the_tariff_table(
    sample_usage: UsageData, tariff: TariffTable, sample_bill: BillingResult
) -> None:
    """**계시별 단가는 요금 엔진에서 자동으로 온다. 사용자 입력이 아니다.**"""
    value = arbitrage_value(
        sample_usage,
        tariff,
        SAMPLE_SELECTION,
        usable_kwh=1.0,
        round_trip=DEFAULT_ROUND_TRIP,
        base_fee_months=sample_bill.base_fee_months,
    )
    rates = tariff.rates(SAMPLE_SELECTION)
    for spread in value.spreads:
        assert spread.peak_won_per_kwh == rates.rate(spread.season, "peak")
        assert spread.light_won_per_kwh == rates.rate(spread.season, "light")
        # 왕복손실만큼 충전을 더 사야 한다.
        assert spread.spread_won_per_kwh < spread.peak_won_per_kwh - spread.light_won_per_kwh


def test_peak_days_exclude_weekends_and_holidays(
    sample_usage: UsageData, tariff: TariffTable
) -> None:
    """최대부하가 존재하는 날만 센다. 토·일·공휴일은 자동으로 빠진다."""
    days = peak_days_by_season(sample_usage, tariff, selection=SAMPLE_SELECTION)
    assert set(days) == {"summer", "winter", "spring_fall"}
    assert sum(days.values()) == 250  # 359일 중 최대부하가 있는 날


def test_arbitrage_standalone_payback_outlives_the_battery(
    sample_usage: UsageData, tariff: TariffTable, sample_bill: BillingResult
) -> None:
    """**차익거래 단독으로는 성립하지 않는다.** 그 근거를 숫자로 보인다."""
    reference = load_ess_cost_reference()
    value = arbitrage_value(
        sample_usage,
        tariff,
        SAMPLE_SELECTION,
        usable_kwh=1.0,
        round_trip=DEFAULT_ROUND_TRIP,
        base_fee_months=sample_bill.base_fee_months,
        capex_energy_won_per_kwh=reference.default.capex_energy_won_per_kwh,
    )
    assert value.won_per_kwh_year == pytest.approx(19_476.0, rel=0.02)
    assert value.standalone_payback_years == pytest.approx(19.0, rel=0.05)
    assert value.outlives_battery  # 배터리 수명 10~15년을 넘는다
    assert any("단독으로는 성립하지 않습니다" in note for note in value.notes)


def test_arbitrage_is_not_added_to_the_saving(sample_ess: EssResult) -> None:
    """피크저감 절감액에 더하지 않는다. 더하면 이중 계산이다."""
    assert sample_ess.arbitrage is not None
    assert sample_ess.total_saving_won == pytest.approx(
        sample_ess.base_saving_won + sample_ess.energy_saving_won
    )
    assert any("이중 계산" in note for note in sample_ess.notes)


def test_arbitrage_scales_with_capacity(
    sample_usage: UsageData, tariff: TariffTable, sample_bill: BillingResult
) -> None:
    """차익거래 수익은 **용량(kWh)에 비례**한다. 피크저감은 출력에 비례한다."""
    one = arbitrage_value(
        sample_usage,
        tariff,
        SAMPLE_SELECTION,
        usable_kwh=1.0,
        round_trip=DEFAULT_ROUND_TRIP,
        base_fee_months=sample_bill.base_fee_months,
    )
    ten = arbitrage_value(
        sample_usage,
        tariff,
        SAMPLE_SELECTION,
        usable_kwh=10.0,
        round_trip=DEFAULT_ROUND_TRIP,
        base_fee_months=sample_bill.base_fee_months,
    )
    assert ten.annual_won == pytest.approx(one.annual_won * 10.0)


def test_cycles_per_day_is_configurable(
    sample_usage: UsageData, tariff: TariffTable, sample_bill: BillingResult
) -> None:
    base = arbitrage_value(
        sample_usage,
        tariff,
        SAMPLE_SELECTION,
        usable_kwh=1.0,
        round_trip=DEFAULT_ROUND_TRIP,
        base_fee_months=sample_bill.base_fee_months,
    )
    doubled = arbitrage_value(
        sample_usage,
        tariff,
        SAMPLE_SELECTION,
        usable_kwh=1.0,
        round_trip=DEFAULT_ROUND_TRIP,
        base_fee_months=sample_bill.base_fee_months,
        cycles_per_day=2.0,
    )
    assert base.cycles_per_day == 1.0
    assert doubled.won_per_kwh_year == pytest.approx(base.won_per_kwh_year * 2.0)


# --------------------------------------------------------------------- 회수기간 구조


@pytest.fixture(scope="module")
def payback_curve(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
) -> pd.DataFrame:
    return ess_payback_curve(
        sample_usage,
        tariff,
        SAMPLE_SELECTION,
        target_kw=TARGET_KW,
        charge_mask=light_band_mask(sample_usage, tariff, selection=SAMPLE_SELECTION),
        baseline=sample_bill,
        quality=sample_report,
    )


def test_payback_gets_worse_as_capacity_grows(payback_curve: pd.DataFrame) -> None:
    """**용량을 늘릴수록 회수기간이 나빠진다.** 이것이 핵심 메시지다.

    피크저감 수익은 출력에 붙어 그대로인데 투자비는 용량을 따라 오른다.
    차익거래를 더해도 (그쪽은 용량에 비례하지만 투자비 증가를 못 따라가서)
    여전히 단조증가한다.
    """
    assert payback_curve["12개월 환산 절감액(원)"].nunique() == 1  # 출력이 고정이다
    assert payback_curve["회수기간(피크저감만, 년)"].is_monotonic_increasing
    assert payback_curve["회수기간(차익거래 포함, 년)"].is_monotonic_increasing
    assert payback_curve["차익거래 잠재(원/년)"].is_monotonic_increasing


def test_curve_matches_the_reference_unit_costs(payback_curve: pd.DataFrame) -> None:
    assert payback_curve.loc[0.5, "kW당 단가(원)"] == pytest.approx(430_263.0)
    assert payback_curve.loc[4.0, "kW당 단가(원)"] == pytest.approx(1_725_039.0)


def test_outlook_payback_is_shorter_than_today(sample_ess: EssResult) -> None:
    """ "지금은 안 됨" 을 "언제쯤 되는가" 로 바꾼다."""
    assert sample_ess.outlook_payback_years is not None
    assert sample_ess.payback_years is not None
    assert sample_ess.outlook_payback_years < sample_ess.payback_years
    assert "2030" in sample_ess.outlook_label
    assert any("단가 기준" in note for note in sample_ess.notes)


def test_arbitrage_inclusive_payback_is_shorter(sample_ess: EssResult) -> None:
    """차익거래를 더한 쪽이 **상한**이다. 겹침 비율을 함께 밝힌다."""
    assert sample_ess.payback_with_arbitrage_years is not None
    assert sample_ess.payback_years is not None
    assert sample_ess.payback_with_arbitrage_years < sample_ess.payback_years
    assert any("상한입니다" in note for note in sample_ess.notes)
