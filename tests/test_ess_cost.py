"""ESS 단가·차익거래·방전시간 (요구사항서 7.6).

**참고단가는 추정치가 아니라 하한선이다.** 계통용 대형 ESS 기준이고 안전 규제
대응비와 연계공사비가 빠져 있다. 그래서 손익분기와의 비교가 비대칭이고, 참고값이
기본값으로 조용히 적용되어서도 안 된다. 그 두 가지를 여기서 못 박는다.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from kwise.io import UsageData
from kwise.measures import (
    EssCostInput,
    EssCostReferenceError,
    EssOptimum,
    EssResult,
    EssTargetCurve,
    analyze_peak_excess,
    arbitrage_value,
    c_rate,
    default_round_trip,
    ess_payback_curve,
    ess_target_curve,
    evaluate_ess,
    high_rate_discharge_hours,
    light_band_mask,
    load_ess_cost_reference,
    peak_days_by_season,
    reference_table,
    refine_ess_target,
    refine_targets,
    refine_window_kw,
    required_discharge_hours,
)
from kwise.measures.ess_cost import load_ess_cost_model, reference_data_path
from kwise.notices import texts
from kwise.quality import QualityReport
from kwise.report import frames
from kwise.tariff import BillingResult, TariffSelection, TariffTable

from .conftest import SAMPLE_SELECTION

PROJECT_ROOT = Path(__file__).resolve().parent.parent

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

    # 단가와 총액을 함께 줄 수 없다. 둘 다 없으면 조달 사례 모델이 산정한다 (13세션).
    with pytest.raises(ValueError, match="함께 줄 수 없습니다"):
        EssCostInput(unit_cost_won_per_kw=500_000.0, total_won=1_000_000.0)
    assert EssCostInput.unpriced().is_unpriced

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


def test_fixed_verdict_phrase_is_gone() -> None:
    """**값과 무관하게 나오던 고정 판정 문구를 지웠다** (13세션).

    "경제성 없음 — 참고단가 하한선 …" 이 언제나 나와서 판정처럼 읽혔다.
    판정은 성립 조건 계산에서 나온다.
    """
    reference = load_ess_cost_reference()
    assert not hasattr(reference, "verdict")
    source = Path("src/kwise/measures/ess_cost.py").read_text(encoding="utf-8")
    assert "강한 판정" not in source
    assert "참고단가 하한선" not in source


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
    """계통 전달 기준 시간 = 하루 최대 초과 에너지 ÷ 최대 초과 출력.

    **화면에 내는 값이 아니다** (43세션). 표시하는 방전시간은 정격 기준이며
    아래 :func:`test_방전시간은_정격_용량_나누기_출력이다` 가 지킨다.
    """
    excess = analyze_peak_excess(sample_usage.kw, TARGET_KW, sample_usage.meta.interval_minutes)
    hours = required_discharge_hours(excess)
    assert hours == pytest.approx(excess.max_daily_excess_kwh / excess.max_excess_kw)
    assert 0.3 < hours < 0.5  # 샘플은 짧다


def test_방전시간은_정격_용량_나누기_출력이다(sample_ess: EssResult) -> None:
    """**사양 셋이 서로 맞아야 한다** (43세션).

    34세션에 용량을 정격으로 고치면서 방전시간이 함께 옮겨지지 않아, 한 카드
    안에서 출력·정격 용량·방전시간이 서로 안 맞았다 — 119.6 kWh ÷ 123.4 kW 는
    0.82h 가 아니라 0.97h 인데 0.82h 를 적고 있었다. **사용자가 조달하는 것은
    정격이다.** 반올림 오차도 허용하지 않는다.
    """
    assert sample_ess.discharge_hours == sample_ess.capacity_kwh / sample_ess.power_kw
    # 계통 전달 기준과는 **다르다** — 같아지면 정격 환산이 빠진 것이다.
    assert sample_ess.discharge_hours > required_discharge_hours(sample_ess.excess)


def test_카드와_경고와_근거가_같은_방전시간을_적는다(sample_ess: EssResult) -> None:
    """세 자리가 한 값을 쓴다 (43세션).

    43세션 전에는 카드 0.8시간 · 성립 조건 0.97시간 · 계산 근거 0.82h 로
    **한 카드 안에서 세 값**이었다. 자릿수까지 맞춰야 한 화면에서 같게 읽힌다.
    """
    shown = f"{sample_ess.discharge_hours:.2f}"
    assert sample_ess.feasibility is not None
    assert shown in sample_ess.feasibility.message()
    basis_lines = [
        message
        for message in texts(sample_ess.notices)
        if "방전시간" in message and "C)" in message
    ]
    assert basis_lines, texts(sample_ess.notices)
    assert all(shown in line for line in basis_lines), basis_lines


def test_c_rate_warning_below_half_an_hour(sample_ess: EssResult) -> None:
    """0.5h 미만이면 고출력 셀 사양임을 경고한다."""
    assert sample_ess.discharge_hours < high_rate_discharge_hours()
    assert sample_ess.c_rate == pytest.approx(1.0 / sample_ess.discharge_hours)
    assert any("고출력 셀" in message for message in texts(sample_ess.notices))
    assert any("C 방전" in message for message in texts(sample_ess.notices))


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
        round_trip=default_round_trip(),
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
        round_trip=default_round_trip(),
        base_fee_months=sample_bill.base_fee_months,
        capex_energy_won_per_kwh=reference.default.capex_energy_won_per_kwh,
    )
    assert value.won_per_kwh_year == pytest.approx(19_476.0, rel=0.02)
    assert value.standalone_payback_years == pytest.approx(19.0, rel=0.05)
    assert value.outlives_battery  # 배터리 수명 10~15년을 넘는다
    assert any("단독으로는 성립하지 않습니다" in note for note in texts(value.notices))


def test_arbitrage_is_not_added_to_the_saving(sample_ess: EssResult) -> None:
    """피크저감 절감액에 더하지 않는다. **값은 41세션에도 그대로다** (B 안)."""
    assert sample_ess.arbitrage is not None
    assert sample_ess.total_saving_won == pytest.approx(
        sample_ess.base_saving_won + sample_ess.energy_saving_won
    )


def test_arbitrage_reason_is_the_reserve_rule_not_double_counting(
    sample_ess: EssResult,
) -> None:
    """**근거를 41세션에 다시 썼다.**

    26세션은 「피크컷 디스패치가 이미 일부를 실현해 이중 계산」 이라고 적었는데,
    그 「일부」 를 재 보니 사이클 1.06% · 금액 0.80% 였다 — 근거가 자기 숫자에
    반박당하고 있었다. 실질은 **예비 규칙**이다: 그날 피크에 쓸 몫을 남기지 않고
    돌리면 피크를 못 깎아 회수기간이 30.75 → 50.61년으로 나빠진다 (샘플 실측).
    """
    notes = texts(sample_ess.notices)
    reason = next(note for note in notes if "더하지 않은 값입니다" in note)
    assert "몫을 남기는 운전 규칙" in reason, reason
    assert "피크를 못 깎아" in reason, reason
    assert "회수기간이 나빠집니다" in reason, reason
    # 틀린 근거는 남기지 않는다.
    assert not any("이중 계산" in note for note in notes), notes
    assert not any("이미 일부를 실현" in note for note in notes), notes


def test_overlap_ratio_is_gone_from_the_notices(sample_ess: EssResult) -> None:
    r"""겹침 비율(1%)을 화면에서 뺐다 (41세션 1-3).

    근거가 바뀌었으므로 그 숫자가 설 자리가 없다. 산출 근거는
    ``docs\TECHNICAL.md`` 6.6 과 :mod:`kwise.measures.arbitrage` 도크스트링에 있다.
    """
    upper = next(note for note in texts(sample_ess.notices) if "이쪽이 상한입니다" in note)
    assert "가정 사이클의" not in upper, upper
    assert "돌리고 있어" not in upper, upper
    assert "매 평일 한 사이클을 온전히 돌리는 운전을 전제한 값입니다" in upper, upper


def test_arbitrage_potential_is_still_shown(sample_ess: EssResult) -> None:
    """**잠재값은 계속 보여준다** (41세션 1-4).

    「이만큼의 여지가 있으나 지금 계산에는 넣지 않았다」 가 사용자에게 유용하다.
    빼는 것은 근거이지 값이 아니다.
    """
    arbitrage = sample_ess.arbitrage
    assert arbitrage is not None
    assert arbitrage.annual_won > 0
    assert sample_ess.payback_with_arbitrage_years is not None
    assert sample_ess.payback_with_arbitrage_years < sample_ess.payback_years


def test_arbitrage_scales_with_capacity(
    sample_usage: UsageData, tariff: TariffTable, sample_bill: BillingResult
) -> None:
    """차익거래 수익은 **용량(kWh)에 비례**한다. 피크저감은 출력에 비례한다."""
    one = arbitrage_value(
        sample_usage,
        tariff,
        SAMPLE_SELECTION,
        usable_kwh=1.0,
        round_trip=default_round_trip(),
        base_fee_months=sample_bill.base_fee_months,
    )
    ten = arbitrage_value(
        sample_usage,
        tariff,
        SAMPLE_SELECTION,
        usable_kwh=10.0,
        round_trip=default_round_trip(),
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
        round_trip=default_round_trip(),
        base_fee_months=sample_bill.base_fee_months,
    )
    doubled = arbitrage_value(
        sample_usage,
        tariff,
        SAMPLE_SELECTION,
        usable_kwh=1.0,
        round_trip=default_round_trip(),
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
    assert any("단가 기준" in note for note in texts(sample_ess.notices))


def test_arbitrage_inclusive_payback_is_shorter(sample_ess: EssResult) -> None:
    """차익거래를 더한 쪽이 **상한**이다. 겹침 비율을 함께 밝힌다."""
    assert sample_ess.payback_with_arbitrage_years is not None
    assert sample_ess.payback_years is not None
    assert sample_ess.payback_with_arbitrage_years < sample_ess.payback_years
    assert any("상한입니다" in note for note in texts(sample_ess.notices))


# ================================================= 조달 사례 모델 (13세션)


def test_model_coefficients_come_from_the_fit_script() -> None:
    """**계수를 코드에 손으로 적지 않는다.** 파일에서 읽는다."""
    model = load_ess_cost_model()
    assert model.sample_size == 4
    assert model.r2 > 0.999
    assert model.fixed_won > 1e8  # 고정비가 지배적이다
    source = Path("src/kwise/measures/ess_cost.py").read_text(encoding="utf-8")
    assert str(int(model.per_kwh_won)) not in source
    assert str(int(model.fixed_won)) not in source


def test_single_unit_price_is_not_offered() -> None:
    """총액÷용량이 세 배 변한다. 그 값을 단가라 부르면 판단이 틀어진다."""
    model = load_ess_cost_model()
    small = model.equipment_won(50.0) / 50.0
    large = model.equipment_won(400.0) / 400.0
    assert small > large * 2.5


def test_quote_splits_equipment_and_electrical_work() -> None:
    model = load_ess_cost_model()
    quote = model.quote(150.0)
    assert quote.in_range
    assert quote.total_won == pytest.approx(quote.equipment_won + quote.electrical_won)
    assert quote.electrical_low_won < quote.electrical_won < quote.electrical_high_won


def test_quote_clamps_below_the_case_range() -> None:
    """**시장 최소 규모**(100 kWh) 아래는 그 규모로 산정하고 알린다 (14세션 3-5)."""
    model = load_ess_cost_model()
    assert model.market_minimum_kwh == model.min_kwh == 100.0
    assert model.billed_capacity_kwh(40.0) == 100.0
    quote = model.quote(40.0)
    assert quote.applied_kwh == model.market_minimum_kwh
    assert not quote.in_range
    assert any("시장 최소" in note for note in texts(quote.notices))

    beyond = model.quote(600.0)
    assert not beyond.in_range
    assert any("참고값" in note for note in texts(beyond.notices))


def test_feasibility_uses_the_contract_base_fee(tariff: TariffTable, sample_ess: EssResult) -> None:
    """**기본요금 단가는 계약종별에서 온다.** 하드코딩하지 않는다."""
    feasibility = sample_ess.feasibility
    assert feasibility is not None
    expected = tariff.rates(SAMPLE_SELECTION).base_won_per_kw
    assert feasibility.base_fee_won_per_kw == pytest.approx(expected)
    assert feasibility.saving_won_per_kw == pytest.approx(expected * 12 * 10)


def test_feasibility_names_the_required_reduction(sample_ess: EssResult) -> None:
    """샘플(목표 5,200 kW)에서 필요 저감량 358 kW, 실제 93 kW."""
    feasibility = sample_ess.feasibility
    assert feasibility is not None
    assert feasibility.required_reduction_kw == pytest.approx(358, abs=2)
    assert feasibility.actual_reduction_kw == pytest.approx(93, abs=1)
    assert not feasibility.feasible
    # 사실만 적는다 — 필요한 값과 산출된 값을 나란히 놓는다 (14세션 3-3).
    assert "필요한 저감량" in feasibility.message()
    assert "93 kW" in feasibility.message()
    assert "성립하지" not in feasibility.message()


def test_long_discharge_cannot_be_feasible() -> None:
    """방전시간이 길면 배터리비가 절감액을 넘어 **규모와 무관하게** 성립하지 않는다."""
    model = load_ess_cost_model()
    quote = model.quote(150.0)
    feasibility = model.feasibility(
        discharge_hours=1.5,
        base_fee_won_per_kw=8_320.0,
        target_years=10.0,
        actual_reduction_kw=10_000.0,
        quote=quote,
    )
    assert feasibility.required_reduction_kw is None
    assert not feasibility.feasible
    # **판정 문장을 쓰지 않는다** (14세션 3-3). 두 값을 나란히 놓는 데서 그친다.
    message = feasibility.message()
    assert "kW당 배터리비" in message and "기본요금 절감액" in message
    assert "성립하지" not in message


def test_model_prices_when_no_input_is_given(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
) -> None:
    """단가·총액이 없으면 **도입 사례 모델**이 투자비를 낸다 (13세션)."""
    result = evaluate_ess(
        sample_usage,
        tariff,
        SAMPLE_SELECTION,
        target_kw=5_200.0,
        cost=EssCostInput.unpriced(),
        charge_mask=light_band_mask(sample_usage, tariff, selection=SAMPLE_SELECTION),
        baseline=sample_bill,
        quality=sample_report,
    )
    assert result.quote is not None
    assert result.investment_won == pytest.approx(result.quote.total_won)
    assert "도입 사례 모델" in result.cost.source  # 26세션 2-4 — 화면에서 「조달」 을 뺐다
    assert any("배터리 보증 수명" in message for message in texts(result.notices))


# ============================================================ 14세션 · 목표 재설계


def test_계수_기본값이_천_단위다() -> None:
    """**적합값을 원 단위로 두면** 사례 넷으로 다섯째 자리까지 맞는 척한다 (14세션 1절)."""
    model = load_ess_cost_model()
    assert model.fixed_won == 106_925_000.0
    assert model.per_kwh_won == 840_000.0
    assert model.fixed_won % 1_000 == 0
    assert model.per_kwh_won % 1_000 == 0


def test_반올림_재현_오차가_일_점_오_퍼센트_안이다() -> None:
    """반올림해도 사실상 달라지지 않아야 쓴다."""
    model = load_ess_cost_model()
    assert model.rounding, "반올림 재현 결과가 없습니다 — 재적합 스크립트를 다시 도십시오."
    assert model.max_rounding_error <= 0.015
    for row in model.rounding:
        predicted = model.fixed_won + model.per_kwh_won * float(row["capacity_kwh"])
        assert predicted == pytest.approx(float(row["predicted_won"]))


def test_계수를_둘만_갈아_끼운다() -> None:
    """**kW당 단가로는 표현할 수 없다** (14세션 3-4). kW 가 설명 변수가 아니다."""
    model = load_ess_cost_model()
    assert not model.adjusted
    tuned = model.with_coefficients(fixed_won=100_000_000.0, per_kwh_won=800_000.0)
    assert tuned.adjusted
    assert tuned.equipment_won(100.0) == pytest.approx(180_000_000.0)
    # 같은 100 kW 라도 용량이 다르면 총액이 크게 다르다.
    assert model.equipment_won(156.4) != model.equipment_won(400.0)
    with pytest.raises(ValueError, match="계수는 음수"):
        model.with_coefficients(fixed_won=-1.0, per_kwh_won=1.0)


def test_카탈로그_사례_문구를_정정했다() -> None:
    """종합쇼핑몰 제품은 **장치비용만**이고 설치공사는 별도다 (14세션 3-4)."""
    model = load_ess_cost_model()
    catalog = next(case for case in model.cases if case["key"] == "mall_cabinet")
    assert catalog["category"] == "catalog"  # 분류는 그대로 둔다
    note = str(catalog["note"])
    assert "장치비용" in note
    assert "설치공사는 별도" in note
    assert "옥외 컨테이너·소방·공조" in note
    # 표에 비고 열이 있어야 화면에서 이 정정이 보인다.
    assert "비고" in model.case_table().columns


# --------------------------------------------------------------------- U곡선


@pytest.fixture(scope="module")
def target_curve(sample_usage: UsageData, tariff: TariffTable) -> EssTargetCurve:
    """샘플의 목표 곡선. **기본요금단가는 현행 요금제(선택Ⅰ) 기준이다.**"""
    return ess_target_curve(
        sample_usage.kw,
        15,
        baseline_demand_kw=5_293.44,
        base_fee_won_per_kw=float(tariff.rates(SAMPLE_SELECTION).base_won_per_kw),
    )


def test_최적_목표를_자동으로_찾는다(target_curve: EssTargetCurve) -> None:
    """**사용자가 찍게 두면 대개 틀린 자리를 찍는다** (14세션 3-1)."""
    best = target_curve.best
    assert best is not None
    assert best.target_kw == pytest.approx(5_170.0)
    # 44세션에 최소 규모를 정격에 걸면서 24.6 → 26.0 이 됐다.
    assert best.payback_years == pytest.approx(26.0, abs=0.2)
    # 최소 지점이 실제로 최소다.
    others = [
        item.payback_years
        for item in target_curve.points
        if item.payback_years is not None and item is not best
    ]
    assert all(value >= (best.payback_years or 0.0) for value in others)


def test_격자가_성기면_최소_지점을_놓친다(
    sample_usage: UsageData, tariff: TariffTable, target_curve: EssTargetCurve
) -> None:
    """**곡선이 격자에 민감하다** (14세션 3-1).

    50 kW 간격이면 5,150 kW 에서 29.4년으로 멈추고, 10 kW 간격이라야
    5,170 kW 26.0년을 찾는다. 기본 격자를 10 kW 이하로 두는 이유다.
    """
    assert target_curve.step_kw <= 10.0
    coarse = ess_target_curve(
        sample_usage.kw,
        15,
        baseline_demand_kw=5_293.44,
        base_fee_won_per_kw=float(tariff.rates(SAMPLE_SELECTION).base_won_per_kw),
        step_kw=50.0,
    )
    assert coarse.best is not None and target_curve.best is not None
    assert coarse.best.target_kw == pytest.approx(5_150.0)
    assert coarse.best.payback_years == pytest.approx(29.4, abs=0.3)
    assert (coarse.best.payback_years or 0.0) > (target_curve.best.payback_years or 0.0)


def test_검산값과_일치한다(target_curve: EssTargetCurve) -> None:
    """14세션 3-2 의 검산값 (관측 최대수요 기준 개략치)."""
    frame = target_curve.frame().set_index("목표 요금적용전력(kW)")
    # 과금 용량과 회수기간은 44세션에 바뀌었다 — 최소 규모를 **정격**에 건다.
    expected = {
        5_200.0: (93.0, 35.0, 100.0, 32.4),
        5_180.0: (113.0, 72.0, 100.0, 26.7),
        5_170.0: (123.0, 101.0, 119.6, 26.0),
        5_150.0: (143.0, 183.0, 216.2, 29.4),
    }
    for target, (reduction, capacity, billed, payback) in expected.items():
        row = frame.loc[target]
        assert float(row["저감량(kW)"]) == pytest.approx(reduction, abs=1)
        assert float(row["필요 용량(kWh)"]) == pytest.approx(capacity, abs=1)
        assert float(row["과금 용량(kWh)"]) == pytest.approx(billed, abs=1)
        assert float(row["회수기간(년)"]) == pytest.approx(payback, abs=0.3)


def test_곡선이_U자다(target_curve: EssTargetCurve) -> None:
    """**최소 지점이 최소 규모 경계 근처에 생긴다.** 조달 규격의 산물이다."""
    best = target_curve.best
    assert best is not None
    frame = target_curve.frame()
    left = frame[frame["목표 요금적용전력(kW)"] < best.target_kw]["회수기간(년)"]
    right = frame[frame["목표 요금적용전력(kW)"] > best.target_kw]["회수기간(년)"]
    assert float(left.iloc[0]) > (best.payback_years or 0.0)  # 왼쪽으로 나빠진다
    assert float(right.iloc[-1]) > (best.payback_years or 0.0)  # 오른쪽으로도 나빠진다
    # 최소 규모에 걸리는 구간이 실제로 있다 — 그것이 왼쪽 팔을 만든다.
    assert bool(frame["최소 규모 적용"].any())
    assert "최소 규모 100 kWh" in target_curve.u_shape_reason
    assert target_curve.market_minimum_kwh == 100.0


def test_대표_지점이_최소_지점을_품는다(target_curve: EssTargetCurve) -> None:
    """최소 지점을 가운데 둔 대표 지점 다섯~여섯 (14세션 3-2).

    **화면 표는 26세션에 없앴다** (1-3). 그림 하나로 고르는 자리에 표까지 두면
    읽을 것이 둘이 되고, 최적 지점의 사양은 아래 지표 카드가 이미 낸다.
    :meth:`~kwise.measures.EssTargetCurve.highlights` 자체는 남긴다 — 곡선이
    최소 지점 둘레를 어떻게 잡는지를 재는 자리다.
    """
    highlights = target_curve.highlights()
    assert 5 <= len(highlights) <= 6
    assert target_curve.best in highlights
    targets = [item.target_kw for item in highlights]
    assert targets == sorted(targets, reverse=True)
    assert not hasattr(frames, "ess_target_table"), "목표 선택 표가 되살아났습니다."


def test_표의_최적_행이_카드_요약과_같다(
    target_curve: EssTargetCurve,
    sample_usage: UsageData,
    sample_report: QualityReport,
    sample_bill: BillingResult,
    tariff: TariffTable,
) -> None:
    """**같은 값이 두 개로 나오지 않는다** (18세션 1절).

    곡선 표는 「필요 용량 100.94 kWh · 회수기간 24.56년」 을, 카드는 「120 kWh ·
    30.8년」 을 냈다. 곡선은 내보낼 에너지 기준이고 카드는 정격(왕복효율·DoD 반영)
    기준이라 계산은 각각 옳았지만, 사용자에게는 그냥 불일치다.

    **카드가 최종 사양이다.** 표는 카드와 값이 같은 사양 셋만 싣고, 돈에 관한
    숫자는 카드 하나만 낸다.
    """
    best = target_curve.best
    assert best is not None
    card = evaluate_ess(
        sample_usage,
        tariff,
        SAMPLE_SELECTION,
        target_kw=best.target_kw,
        cost=EssCostInput.unpriced(),
        baseline=sample_bill,
        quality=sample_report,
    )
    # 사양 셋은 **정확히** 같다. 반올림 오차도 허용하지 않는다 — 곡선의 표식과
    # 카드가 한 화면에 있으므로 한 자리라도 다르면 불일치로 읽힌다.
    assert best.power_kw == card.power_kw
    assert best.nameplate_capacity_kwh == card.capacity_kwh
    assert best.discharge_hours == card.discharge_hours

    # 곡선 내부에는 그대로 남아 목표를 고른다 — 지운 것이 아니라 화면에서 뺐다.
    assert best.payback_years is not None
    assert best.required_capacity_kwh != card.capacity_kwh


def test_절감액은_현행_요금제_단가로_낸다(sample_usage: UsageData, tariff: TariffTable) -> None:
    """**최적 요금제로 바꾼 뒤의 단가를 쓰지 않는다** (14세션 2절 독립 평가).

    선택Ⅰ과 선택Ⅱ는 기본요금 단가가 다르다. ESS 절감액이 요금제 전환에 딸려
    움직이면 두 카드가 서로에게 영향을 주게 된다.
    """
    current = float(tariff.rates(SAMPLE_SELECTION).base_won_per_kw)
    other = float(tariff.rates(replace(SAMPLE_SELECTION, option="II")).base_won_per_kw)
    assert current != other

    curve = ess_target_curve(
        sample_usage.kw, 15, baseline_demand_kw=5_293.44, base_fee_won_per_kw=current
    )
    assert curve.base_fee_won_per_kw == current
    point = curve.points[0]
    assert point.annual_saving_won == pytest.approx(point.reduction_kw * current * 12.0)
    assert point.annual_saving_won != pytest.approx(point.reduction_kw * other * 12.0)


def test_잘못된_입력을_막는다(sample_usage: UsageData) -> None:
    with pytest.raises(ValueError, match="현행 요금적용전력"):
        ess_target_curve(sample_usage.kw, 15, baseline_demand_kw=0.0, base_fee_won_per_kw=7_220.0)
    with pytest.raises(ValueError, match="탐색 격자"):
        ess_target_curve(
            sample_usage.kw,
            15,
            baseline_demand_kw=5_293.44,
            base_fee_won_per_kw=7_220.0,
            step_kw=0.0,
        )
    with pytest.raises(ValueError, match="탐색 하한 비율"):
        ess_target_curve(
            sample_usage.kw,
            15,
            baseline_demand_kw=5_293.44,
            base_fee_won_per_kw=7_220.0,
            search_ratio=1.5,
        )


# ===================================================================== 40세션 · 최적점 정밀화
#
# **개략 곡선이 고른 점은 실제 최적이 아닐 수 있다** (39세션 조사). 여기서
# 지키는 것은 넷이다.
#
#     ① 정밀화가 39세션 전수 조사의 참 최소를 맞힌다 (자료 일곱)
#     ② 브래킷(±100 kW)이 그 참 최소를 담는다
#     ③ 창 가장자리에서 잡히면 넓혀 다시 찾는다
#     ④ 샘플 목표가 5,170 kW 그대로다 — 회귀값이 흔들리지 않는다


#: 39세션이 159점 전수로 밝힌 **카드 기준 참 최소.**
#:
#: 곡선은 셋을 생략한다 — 전력량요금·차익거래를 빼고, 기본요금 절감을 12개월
#: 내내 최대 폭으로 얻는다고 보며, 투자비를 정격이 아닌 내보낼 에너지로 매긴다.
#: 그 셋이 목표마다 다른 크기로 어긋나 **최소가 놓인 자리 자체가 옮겨 간다.**
CARD_BASIS_OPTIMUM: dict[str, float] = {
    "샘플": 5_170.0,
    "C1": 5_170.0,
    "C2": 5_170.0,
    "C3": 3_010.0,  # 곡선 3,000 → 정격 +88.9 kWh (+60%) · 투자 +8,751만원
    "C4": 5_170.0,
    "C5": 5_960.0,  # 곡선 5,940 → 정격 +35.0 kWh (+17%) · 투자 +4,158만원
}


def _case_material(
    path: Path, selection: TariffSelection, tariff: TariffTable
) -> tuple[object, ...]:
    """케이스 하나의 재료. **케이스 스터디와 같은 계약전력 가정을 쓴다.**"""
    from kwise.diagnose import ContractInfo, diagnose
    from kwise.io import load_usage
    from kwise.quality import check_quality
    from kwise.report.casestudy import CONTRACT_MARGIN

    usage = load_usage(path)
    quality = check_quality(usage)
    diag = diagnose(
        usage,
        tariff,
        ContractInfo(selection, contract_kw=float(usage.kw.max()) * CONTRACT_MARGIN),
        quality=quality,
    )
    assert diag.structure is not None
    curve = ess_target_curve(
        usage.kw,
        usage.meta.interval_minutes,
        baseline_demand_kw=diag.peak.billing_demand_kw,
        base_fee_won_per_kw=float(tariff.rates(selection).base_won_per_kw),
    )
    return usage, quality, diag.structure.bill, curve


@pytest.fixture(scope="module")
def sample_optimum(
    sample_usage: UsageData,
    sample_report: QualityReport,
    sample_bill: BillingResult,
    tariff: TariffTable,
    target_curve: EssTargetCurve,
) -> EssOptimum:
    return refine_ess_target(
        sample_usage,
        tariff,
        SAMPLE_SELECTION,
        curve=target_curve,
        baseline=sample_bill,
        quality=sample_report,
    )


def test_최적점을_카드_기준으로_다시_고른다(sample_optimum: EssOptimum) -> None:
    """**개략 곡선의 선택을 그대로 쓰지 않는다** (40세션 1절).

    카드 기준이란 :func:`evaluate_ess` 와 같은 산식이다 — 디스패치를 돌려 요금을
    처음부터 다시 계산하고, 투자비는 왕복효율·DoD 를 반영한 정격 용량으로 매긴다.
    """
    assert sample_optimum.target_kw == CARD_BASIS_OPTIMUM["샘플"]
    assert sample_optimum.payback_years is not None
    # 샘플은 개략 곡선과 같은 자리다 — **회귀값이 흔들리지 않는다.**
    assert sample_optimum.curve_target_kw == 5_170.0
    assert not sample_optimum.moved
    assert sample_optimum.shift_kw == 0.0


def test_정밀화_회수기간이_카드와_같다(
    sample_optimum: EssOptimum,
    sample_usage: UsageData,
    sample_report: QualityReport,
    sample_bill: BillingResult,
    tariff: TariffTable,
) -> None:
    """**같은 산식이어야 고른 값이 뜻을 갖는다.**

    차익거래·전망단가는 회수기간에 들어가지 않으므로 정밀화에서도 뺀다 — 뺀
    만큼 값이 달라지면 안 된다.
    """
    card = evaluate_ess(
        sample_usage,
        tariff,
        SAMPLE_SELECTION,
        target_kw=sample_optimum.target_kw,
        cost=EssCostInput.unpriced(),
        baseline=sample_bill,
        quality=sample_report,
    )
    assert card.payback_years is not None and sample_optimum.payback_years is not None
    assert sample_optimum.payback_years == pytest.approx(card.payback_years, abs=1e-6)


def test_정밀화_격자가_곡선_격자와_같다(
    sample_optimum: EssOptimum, target_curve: EssTargetCurve
) -> None:
    """**표식이 곡선 위에 찍혀야 한다** (40세션 1-1).

    곡선에 없는 자리를 고르면 표식이 선 밖에 뜬다. 1 kW 격자라면 샘플에서
    5,175 kW·30.35년(−0.40년·정격 −15%)을 더 찾지만, 그 값을 쓰려면 곡선 전체를
    1 kW 로 다시 그려야 한다 (201점 84초).
    """
    targets = {point.target_kw for point in target_curve.points}
    assert sample_optimum.target_kw in targets
    for point in sample_optimum.points:
        assert point.target_kw in targets


def test_브래킷이_참_최소를_담는다(tariff: TariffTable) -> None:
    """**±100 kW 라는 폭이 옳은지 자료로 확인한다** (40세션 1-2).

    39세션 전수 조사에서 일곱 자료의 참 최소가 모두 개략 최적의 ±20 kW 안에
    있었고, 그 네 배를 창으로 잡았다. 이 시험이 그 근거를 못박는다.
    """
    from kwise.report.casestudy import build_case_definitions

    directory = PROJECT_ROOT / "input" / "cases"
    if not directory.is_dir():
        pytest.skip(f"케이스 파일이 없습니다: {directory}")
    for definition in build_case_definitions(directory):
        truth = CARD_BASIS_OPTIMUM.get(definition.key)
        if truth is None:
            continue
        _usage, _quality, _bill, curve = _case_material(
            definition.usage_path, definition.selection, tariff
        )
        assert curve.best is not None
        window = refine_window_kw()
        assert abs(truth - curve.best.target_kw) <= window, (
            f"{definition.key}: 참 최소 {truth:,.0f} kW 가 개략 최적 "
            f"{curve.best.target_kw:,.0f} kW 의 ±{window:,.0f} kW 밖입니다."
        )
        assert truth in refine_targets(curve)


@pytest.mark.parametrize("key", ["C3", "C5"])
def test_곡선과_정밀화가_같은_자리를_고른다(key: str, tariff: TariffTable) -> None:
    """**44세션에 투자비 기준을 맞추자 개략 곡선이 카드 쪽으로 옮겨 붙었다.**

    39세션 조사에서는 C3 가 3,000 kW, C5 가 5,940 kW 로 카드 기준 최적과 어긋났다.
    원인은 최소 규모 100 kWh 를 곡선은 전달 용량에, 카드는 정격 용량에 걸어
    **투자비가 최대 1.14배까지 갈라진 것**이었다. 정격으로 맞추자 투자비가
    똑같아지고 두 선택이 일치했다.

    **정밀화를 지운 것이 아니다** — 고르는 자리는 같아졌어도 **값은 여전히
    다르다** (샘플 26.0년 대 30.8년). 절감액을 어림하느냐 요금을 다시 계산하느냐의
    차이는 그대로다. 자료가 늘면 다시 갈라질 수 있으므로 창 검증도 남는다.
    """
    from kwise.report.casestudy import build_case_definitions

    directory = PROJECT_ROOT / "input" / "cases"
    if not directory.is_dir():
        pytest.skip(f"케이스 파일이 없습니다: {directory}")
    definition = next(item for item in build_case_definitions(directory) if item.key == key)
    usage, quality, bill, curve = _case_material(
        definition.usage_path, definition.selection, tariff
    )
    optimum = refine_ess_target(
        usage,  # type: ignore[arg-type]
        tariff,
        definition.selection,
        curve=curve,  # type: ignore[arg-type]
        baseline=bill,  # type: ignore[arg-type]
        quality=quality,  # type: ignore[arg-type]
    )
    assert optimum.target_kw == CARD_BASIS_OPTIMUM[key]
    assert not optimum.moved
    # **값은 다르다.** 같아지면 정밀화가 요금을 다시 계산하지 않고 있는 것이다.
    assert curve.best is not None
    assert optimum.payback_years != curve.best.payback_years


def test_곡선과_카드가_같은_용량에_최소_규모를_건다(
    sample_usage: UsageData, tariff: TariffTable
) -> None:
    """**같은 규칙을 다른 양에 걸지 않는다** (44세션).

    곡선은 ``billed_capacity_kwh`` 로, 카드는 ``quote`` 로 100 kWh 하한을 거는데
    한쪽은 전달 용량, 한쪽은 정격 용량이었다. 정격이 1.185배 크므로 경계가
    어긋나 C5 목표 6,020 kW 에서 곡선만 걸렸다. **투자비가 같아야 맞은 것이다.**
    """
    from kwise.measures.ess_cost import load_ess_cost_model

    model = load_ess_cost_model()
    curve = ess_target_curve(
        sample_usage.kw,
        15,
        baseline_demand_kw=5_293.44,
        base_fee_won_per_kw=float(tariff.rates(SAMPLE_SELECTION).base_won_per_kw),
    )
    for point in curve.points:
        assert point.billed_capacity_kwh == max(
            point.nameplate_capacity_kwh, model.market_minimum_kwh
        )
        assert point.at_market_minimum == (point.nameplate_capacity_kwh < model.market_minimum_kwh)
        assert point.investment_won == model.quote(point.nameplate_capacity_kwh).total_won


def _c3_material(tariff: TariffTable) -> tuple[object, ...]:
    """C3 평탄형. 카드 기준 최소는 3,010 kW 다."""
    from kwise.report.casestudy import build_case_definitions

    directory = PROJECT_ROOT / "input" / "cases"
    if not directory.is_dir():
        pytest.skip(f"케이스 파일이 없습니다: {directory}")
    definition = next(item for item in build_case_definitions(directory) if item.key == "C3")
    return (*_case_material(definition.usage_path, definition.selection, tariff), definition)


def _anchored_at(curve: EssTargetCurve, target_kw: float) -> EssTargetCurve:
    """곡선이 ``target_kw`` 를 골랐다고 두고 창 검증을 태운다.

    **44세션 전에는 C3 가 실제로 어긋나 있었다** — 곡선 3,000, 카드 3,010.
    최소 규모를 정격으로 맞추면서 둘이 같아졌으므로, 창 검증은 어긋난 자리를
    직접 심어 태운다. **지운 것이 아니라 태울 자료가 없어진 것이다.**
    """
    from dataclasses import replace

    anchor = next(point for point in curve.points if point.target_kw == target_kw)
    return replace(curve, best=anchor)


def test_창_가장자리면_넓혀_다시_찾는다(tariff: TariffTable) -> None:
    """**창이 좁으면 조용히 틀린다** (40세션 1-2).

    곡선이 3,030 kW 를 골랐다고 두면 카드 기준 최소(3,010)가 창 10 kW 밖이다.
    가장자리에서 잡히므로 넓혀 다시 훑어야 한다.
    """
    usage, quality, bill, curve, definition = _c3_material(tariff)
    narrow = refine_ess_target(
        usage,  # type: ignore[arg-type]
        tariff,
        definition.selection,  # type: ignore[attr-defined]
        curve=_anchored_at(curve, 3_030.0),  # type: ignore[arg-type]
        baseline=bill,  # type: ignore[arg-type]
        quality=quality,  # type: ignore[arg-type]
        window_kw=10.0,
        max_widen=4,
    )
    assert narrow.target_kw == CARD_BASIS_OPTIMUM["C3"]
    assert narrow.window_kw > 10.0, "가장자리에서 잡혔으면 창을 넓혀야 한다."
    assert narrow.widened >= 1
    assert not narrow.at_edge, "넓히고 나면 가장자리가 아니어야 한다."


def test_넓히고도_가장자리면_경고를_남긴다(tariff: TariffTable) -> None:
    """**조용히 자르지 않는다.** 자른 채 두면 「그 자리가 최적」 으로 읽힌다."""
    usage, quality, bill, curve, definition = _c3_material(tariff)
    stuck = refine_ess_target(
        usage,  # type: ignore[arg-type]
        tariff,
        definition.selection,  # type: ignore[attr-defined]
        curve=_anchored_at(curve, 3_030.0),  # type: ignore[arg-type]
        baseline=bill,  # type: ignore[arg-type]
        quality=quality,  # type: ignore[arg-type]
        window_kw=10.0,
        max_widen=0,
    )
    assert stuck.at_edge
    assert stuck.widened == 0
    facts = {notice.fact for notice in stuck.notices}
    assert "ess.refine_at_edge" in facts


def test_절감액이_없으면_곡선의_선택을_그대로_쓴다(
    sample_usage: UsageData, tariff: TariffTable, target_curve: EssTargetCurve
) -> None:
    """야간 피크형처럼 **카드 기준 회수기간이 아예 안 나오는 자료**가 있다 (C6).

    그때는 개략 곡선의 선택을 그대로 쓰고 그 사실을 적는다 — 목표가 사라지면
    ESS 행이 통째로 빠진다.
    """
    from dataclasses import replace

    # 회수기간이 나올 수 없게 기본요금단가를 0 으로 둔 곡선
    flat = ess_target_curve(
        sample_usage.kw,
        15,
        baseline_demand_kw=5_293.44,
        base_fee_won_per_kw=0.0,
    )
    assert flat.best is None
    empty = refine_ess_target(sample_usage, tariff, SAMPLE_SELECTION, curve=flat)
    assert empty.target_kw == 0.0
    assert empty.payback_years is None
    assert replace(target_curve, best=None).best is None


def test_사양_표가_최적을_가운데_두고_양쪽으로_벌린다(
    sample_usage: UsageData,
    tariff: TariffTable,
    sample_bill: BillingResult,
    sample_report: QualityReport,
    target_curve: EssTargetCurve,
) -> None:
    """**「목표를 낮추면 나빠진다」 가 표로 읽혀야 한다** (44세션).

    창의 양 끝과 최적을 반드시 넣는다. 끝을 빼면 U 가 안 보이고, 최적을 빼면
    표식을 찍을 줄이 없다.
    """
    from kwise.report.frames import ESS_SPEC_ROWS, ess_spec_frame

    optimum = refine_ess_target(
        sample_usage,
        tariff,
        SAMPLE_SELECTION,
        curve=target_curve,
        baseline=sample_bill,
        quality=sample_report,
    )
    frame = ess_spec_frame(
        optimum,
        baseline_demand_kw=target_curve.baseline_demand_kw,
        market_minimum_kwh=target_curve.market_minimum_kwh,
    )
    assert len(frame) == ESS_SPEC_ROWS
    targets = list(frame["목표 요금적용전력(kW)"])
    assert targets == sorted(targets, reverse=True), "목표가 높은 쪽부터다"
    assert targets[0] == max(point.target_kw for point in optimum.points)
    assert targets[-1] == min(point.target_kw for point in optimum.points)
    assert optimum.target_kw in targets

    # **U 가 읽힌다** — 최적 줄이 양 끝보다 짧다.
    payback = list(frame["회수기간(년)"])
    best_at = targets.index(optimum.target_kw)
    assert payback[best_at] == min(value for value in payback if value is not None)
    assert payback[0] > payback[best_at]
    assert payback[-1] > payback[best_at]

    marks = list(frame["표식"])
    assert "최적" in marks[best_at]
    # 최소 규모에 걸린 줄은 그 사실을 적는다 — 용량이 달라도 투자비가 같아진다.
    assert any("최소 규모" in mark for mark in marks)


def test_사양_표는_모두_카드_기준_참값이다(
    sample_usage: UsageData,
    tariff: TariffTable,
    sample_bill: BillingResult,
    sample_report: QualityReport,
    target_curve: EssTargetCurve,
) -> None:
    """**두 숫자 문제가 사라졌다** (44세션).

    43세션까지 화면에는 곡선 최소 26.0년과 카드 30.8년이 함께 있었다. 표의
    최적 줄은 ``evaluate_ess`` 가 내는 값과 **같아야** 한다.
    """
    from kwise.report.frames import ess_spec_frame

    optimum = refine_ess_target(
        sample_usage,
        tariff,
        SAMPLE_SELECTION,
        curve=target_curve,
        baseline=sample_bill,
        quality=sample_report,
    )
    frame = ess_spec_frame(optimum, baseline_demand_kw=target_curve.baseline_demand_kw)
    row = frame[frame["목표 요금적용전력(kW)"] == optimum.target_kw].iloc[0]
    card = evaluate_ess(
        sample_usage,
        tariff,
        SAMPLE_SELECTION,
        target_kw=optimum.target_kw,
        cost=EssCostInput.unpriced(),
        baseline=sample_bill,
        quality=sample_report,
        charge_mask=light_band_mask(sample_usage, tariff, selection=SAMPLE_SELECTION),
    )
    assert float(row["필요 출력(kW)"]) == pytest.approx(card.power_kw)
    assert float(row["정격 용량(kWh)"]) == pytest.approx(card.capacity_kwh)
    assert float(row["방전시간(h)"]) == pytest.approx(card.discharge_hours)
    assert float(row["투자비(원)"]) == pytest.approx(card.investment_won)
    assert float(row["회수기간(년)"]) == pytest.approx(card.payback_years, rel=1e-9)


def test_화면과_산출물이_같은_사양_표를_쓴다(
    sample_usage: UsageData,
    tariff: TariffTable,
    sample_bill: BillingResult,
    sample_report: QualityReport,
    target_curve: EssTargetCurve,
) -> None:
    """서식을 한 곳에서 만든다 — **「개략」 은 어디에도 없다** (44세션)."""
    from kwise.report.frames import ESS_SPEC_CAPTION, ESS_SPEC_HEADER, ess_spec_frame, ess_spec_rows

    optimum = refine_ess_target(
        sample_usage,
        tariff,
        SAMPLE_SELECTION,
        curve=target_curve,
        baseline=sample_bill,
        quality=sample_report,
    )
    rows = ess_spec_rows(
        ess_spec_frame(optimum, baseline_demand_kw=target_curve.baseline_demand_kw)
    )
    assert rows[0] == ESS_SPEC_HEADER
    assert all(len(row) == len(ESS_SPEC_HEADER) for row in rows)
    joined = "\n".join(" ".join(row) for row in rows) + ESS_SPEC_CAPTION
    assert "개략" not in joined
    # 곡선을 그리던 이름은 지웠다 — 남아 있으면 「개략」 이 되살아난다.
    for module in ("kwise.report.frames", "kwise.ui.charts", "kwise.report.figures"):
        __import__(module)
        gone = ("ess_target_chart", "ess_target_frame", "ESS_PAYBACK_AXIS", "ess_payback_png")
        assert not [name for name in gone if hasattr(sys.modules[module], name)]
