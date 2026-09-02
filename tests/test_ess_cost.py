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
    NOT_VIABLE_CONCLUSION,
    SHORTEST_PAYBACK,
    SPEC_TABLE_ROWS,
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
    reference_targets,
    refine_ess_target,
    refine_targets,
    refine_window_kw,
    required_discharge_hours,
    snap_step_kw,
    target_step_kw,
    viable_discharge_hours,
)
from kwise.measures.ess import EssOptimumPoint
from kwise.measures.ess_cost import load_ess_cost_model, reference_data_path
from kwise.notices import texts
from kwise.quality import QualityReport
from kwise.report import frames
from kwise.report.casestudy import CaseDefinition
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

    **화면에 내는 값이 아니다** (45세션). 표시하는 방전시간은 정격 기준이며
    아래 :func:`test_방전시간은_정격_용량_나누기_출력이다` 가 지킨다.
    """
    excess = analyze_peak_excess(sample_usage.kw, TARGET_KW, sample_usage.meta.interval_minutes)
    hours = required_discharge_hours(excess)
    assert hours == pytest.approx(excess.max_daily_excess_kwh / excess.max_excess_kw)
    assert 0.3 < hours < 0.5  # 샘플은 짧다


def test_방전시간은_정격_용량_나누기_출력이다(sample_ess: EssResult) -> None:
    """**사양 셋이 서로 맞아야 한다** (45세션).

    34세션에 용량을 정격으로 고치면서 방전시간이 함께 옮겨지지 않아, 한 카드
    안에서 출력·정격 용량·방전시간이 서로 안 맞았다 — 119.6 kWh ÷ 123.4 kW 는
    0.82h 가 아니라 0.97h 인데 0.82h 를 적고 있었다. **사용자가 조달하는 것은
    정격이다.** 반올림 오차도 허용하지 않는다.
    """
    assert sample_ess.discharge_hours == sample_ess.capacity_kwh / sample_ess.power_kw
    # 계통 전달 기준과는 **다르다** — 같아지면 정격 환산이 빠진 것이다.
    assert sample_ess.discharge_hours > required_discharge_hours(sample_ess.excess)


def test_카드와_경고와_근거가_같은_방전시간을_적는다(sample_ess: EssResult) -> None:
    """세 자리가 한 값을 쓴다 (45세션).

    45세션 전에는 카드 0.8시간 · 성립 조건 0.97시간 · 계산 근거 0.82h 로
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


def test_c_rate_warning_below_half_an_hour(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
) -> None:
    """0.5h 미만이면 고출력 셀 사양임을 경고한다.

    **사양을 직접 준다** (50세션). 격자를 씌우면서 샘플의 산출 사양이
    100 kW / 50 kWh(0.50h)가 되어 경계에 딱 걸린다 — 격자 위에서 0.5h 아래로
    내려가려면 출력이 용량의 두 배를 넘어야 하는데 이 자료에는 그런 목표가 없다.
    **경고 자체는 살아 있어야 하므로** 견적 사양을 넣어 그 길을 태운다.
    """
    result = evaluate_ess(
        sample_usage,
        tariff,
        SAMPLE_SELECTION,
        target_kw=TARGET_KW,
        cost=EssCostInput.of_unit_cost(615_231.0),
        power_kw=150.0,
        capacity_kwh=50.0,
        charge_mask=light_band_mask(sample_usage, tariff, selection=SAMPLE_SELECTION),
        baseline=sample_bill,
        quality=sample_report,
    )
    assert result.discharge_hours < high_rate_discharge_hours()
    assert result.c_rate == pytest.approx(1.0 / result.discharge_hours)
    assert any("고출력 셀" in message for message in texts(result.notices))
    assert any("C 방전" in message for message in texts(result.notices))


def test_격자_사양은_0_5시간_경계에_선다(sample_ess: EssResult) -> None:
    """**격자가 사양을 규격으로 올리면서 방전시간도 눈금에 선다** (50세션 3-2).

    샘플 목표 5,200 kW 는 필요 93.4 kW / 41.1 kWh 인데, 살 수 있는 것은
    100 kW / 50 kWh 다. 0.44h 가 0.50h 가 되어 고출력 셀 경고를 벗어난다 —
    **더 산 만큼 사양이 순해진 것이므로 맞는 방향이다.**
    """
    assert (sample_ess.power_kw, sample_ess.capacity_kwh) == (100.0, 50.0)
    assert sample_ess.required_power_kw == pytest.approx(93.4, abs=0.1)
    assert sample_ess.required_capacity_kwh == pytest.approx(41.1, abs=0.1)
    assert sample_ess.discharge_hours == pytest.approx(0.5)
    assert not any("고출력 셀" in message for message in texts(sample_ess.notices))


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
    # **오른쪽도 None 일 수 있다** (70세션 2절에 mypy 가 짚었다). 단언을 하나
    # 더 두는 것이 맞다 — 없으면 값이 비었을 때 견주다가 터진다.
    assert sample_ess.payback_years is not None
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


def test_quote_no_longer_clamps_to_a_market_minimum() -> None:
    """**하한으로 올려 잡지 않는다** (50세션 3-3). 그 일은 규격 격자가 앞에서 한다.

    49세션까지는 100 kWh 미만을 100 kWh 로 올려 산정했다. 그 규칙이 사양 표
    다섯 줄에 모두 「최소 규모」 표식을 달아 **구별하는 힘이 없었고** 투자비도
    다섯 줄이 같았다. 격자를 쓰면 살 수 있는 최소 구성(50 kWh)이 자연히 하한이
    되므로 따로 걸 것이 없다.
    """
    model = load_ess_cost_model()
    assert model.min_kwh == 50.0, "적용 하한은 격자의 가장 작은 배터리다"
    assert not hasattr(model, "market_minimum_kwh")
    quote = model.quote(50.0)
    assert quote.applied_kwh == 50.0, "올려 잡지 않는다"
    assert quote.in_range
    assert not any("시장 최소" in note for note in texts(quote.notices))

    # **위쪽은 그대로 알린다** — 사례 최대(400 kWh)를 넘으면 참고값이다.
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
    """샘플(목표 5,200 kW)에서 필요 저감량 399 kW, 실제 93 kW.

    **50세션에 358 → 399 로 늘었다.** 격자가 사양을 100 kW / 50 kWh 로 올리면서
    방전시간이 0.44h → 0.50h 가 되어 kW당 배터리비가 그만큼 붙는다. 마진이 줄면
    고정비를 덮는 데 더 큰 저감량이 필요하다 — **더 산 만큼 조건이 빡빡해진
    것이므로 맞는 방향이다.**
    """
    feasibility = sample_ess.feasibility
    assert feasibility is not None
    assert feasibility.required_reduction_kw == pytest.approx(399, abs=2)
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
    assert best.target_kw == pytest.approx(5_180.0)
    # 46세션에 최소 규모를 정격에 걸면서 24.6 → 26.0, 50세션에 규격 격자를
    # 씌우면서 최소가 5,170 → 5,180 으로 옮겨 26.6 이 됐다.
    assert best.payback_years == pytest.approx(26.6, abs=0.2)
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

    50 kW 간격이면 5,200 kW 에서 27.2년으로 멈추고, 10 kW 간격이라야
    5,180 kW 26.6년을 찾는다. 기본 격자를 10 kW 이하로 두는 이유다.

    **50세션에 자리가 옮겨 앉았다** — 규격 격자가 투자비를 바꾸면서 곡선 최소가
    5,170 → 5,180 으로, 성긴 격자의 최소가 5,150 → 5,200 으로 갔다. 재는 것은
    그대로다: **성긴 격자는 참 최소를 놓친다.**
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
    assert coarse.best.target_kw == pytest.approx(5_200.0)
    assert coarse.best.payback_years == pytest.approx(27.2, abs=0.3)
    assert (coarse.best.payback_years or 0.0) > (target_curve.best.payback_years or 0.0)


def test_검산값과_일치한다(target_curve: EssTargetCurve) -> None:
    """14세션 3-2 의 검산값 (관측 최대수요 기준 개략치)."""
    frame = target_curve.frame().set_index("목표 요금적용전력(kW)")
    # **규격 용량과 회수기간은 50세션에 바뀌었다** — 정격을 살 수 있는 배터리로
    # 올려 잡는다 (46세션의 「과금 용량」 이 있던 자리다).
    expected = {
        5_200.0: (93.0, 35.0, 50.0, 27.2),
        5_180.0: (113.0, 72.0, 100.0, 26.6),
        5_170.0: (123.0, 101.0, 150.0, 28.4),
        5_150.0: (143.0, 183.0, 250.0, 32.6),
    }
    for target, (reduction, capacity, grid, payback) in expected.items():
        row = frame.loc[target]
        assert float(row["저감량(kW)"]) == pytest.approx(reduction, abs=1)
        assert float(row["필요 용량(kWh)"]) == pytest.approx(capacity, abs=1)
        assert float(row["규격 용량(kWh)"]) == pytest.approx(grid, abs=1)
        assert float(row["회수기간(년)"]) == pytest.approx(payback, abs=0.3)


def test_곡선이_U자다(target_curve: EssTargetCurve) -> None:
    """**왼쪽 팔을 만드는 것은 고정비다** (14세션 3-2 · 50세션에 이유를 고쳤다).

    49세션까지는 「시장 최소 규모 100 kWh」 를 왼쪽 팔의 이유로 적었다. 그 하한을
    규격 격자로 바꾸면서 살 수 있는 최소 배터리가 50 kWh 로 내려갔는데 **U자는
    그대로다** — 1억을 넘는 고정비와 전기공사비가 용량을 줄여도 줄지 않는다.
    """
    best = target_curve.best
    assert best is not None
    frame = target_curve.frame()
    left = frame[frame["목표 요금적용전력(kW)"] < best.target_kw]["회수기간(년)"]
    right = frame[frame["목표 요금적용전력(kW)"] > best.target_kw]["회수기간(년)"]
    assert float(left.iloc[0]) > (best.payback_years or 0.0)  # 왼쪽으로 나빠진다
    assert float(right.iloc[-1]) > (best.payback_years or 0.0)  # 오른쪽으로도 나빠진다
    # 얕은 목표에서 살 수 있는 최소 배터리에 걸리는 구간이 실제로 있다.
    assert bool((frame["규격 용량(kWh)"] == 50.0).any())
    assert "고정비" in target_curve.u_shape_reason
    assert "최소 규모" not in target_curve.u_shape_reason
    # **최소 규격에 못 미치는 목표도 곡선에는 남는다** — 고를 수 없을 뿐이다.
    assert bool(frame["최소 규격 미달"].any())
    assert target_curve.min_power_kw == 50.0


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
    #
    # **50세션부터 견주는 것은 규격 사양이다.** 사용자가 조달하는 것이 그것이고,
    # 정격은 격자에 올려 잡기 전의 중간값이라 화면에 나가지 않는다.
    assert best.grid_power_kw == card.power_kw
    assert best.grid_capacity_kwh == card.capacity_kwh
    assert best.grid_discharge_hours == card.discharge_hours
    # 필요 사양도 카드가 함께 들고 있다 — 규격이 왜 그 값인지의 근거다.
    assert best.power_kw == card.required_power_kw
    assert best.nameplate_capacity_kwh == card.required_capacity_kwh

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
    # **50세션에 규격 격자를 씌우면서 옮겼다.** 필요 사양을 살 수 있는 규격으로
    # 올려 잡으니 투자비가 달라져 최소가 옮겨 앉았다 — 5,170 kW 는 정격
    # 119.6 kWh 가 150 kWh 로 올라가고, 5,180 kW 는 85.2 kWh 가 100 kWh 로만
    # 올라가 뒤쪽이 이겼다 (33.6년 대 31.7년).
    "샘플": 5_180.0,
    "C1": 5_180.0,
    "C2": 5_180.0,
    # **48세션에 3,010 → 3,035 로 옮겼다.** 격자가 요금적용전력의 0.2% 가 되어
    # C3(3,052 kW)에서 10 → 5 kW 로 촘촘해졌고, 마진 조건이 3,010 kW(방전 3.50h)를
    # 후보에서 뺐다. 성립 한계는 1.031h 다.
    #
    # **50세션부터 C3 는 기본 설정에서 산출되지 않는다** — 필요 출력이 7 kW 라
    # 상업용 최소 규격 50 kW 에 못 미친다. 이 값은 **문지기를 끈**
    # (``min_power_kw=0``) 상태의 참 최소이며, 창 검증 시험이 그렇게 쓴다.
    "C3": 3_035.0,
    "C5": 5_970.0,  # 50세션 격자 — 정격 191.4 kWh → 규격 200 kWh
    "C4": 5_180.0,
}

#: **문지기를 끄고 재야 하는 케이스** (50세션 3-3). 창 검증이 재는 것은 정밀화
#: 장치이지 최소 규격이 아니다 — C3 는 필요 출력이 7 kW 라 기본 설정에서는
#: 후보가 아예 없어 창을 훑는 일 자체가 일어나지 않는다.
GATE_OFF = 0.0


def _case_material(
    path: Path,
    selection: TariffSelection,
    tariff: TariffTable,
    *,
    min_power_kw: float | None = None,
) -> tuple[UsageData, QualityReport, BillingResult, EssTargetCurve]:
    """케이스 하나의 재료. **케이스 스터디와 같은 계약전력 가정을 쓴다.**

    Args:
        min_power_kw: 상업용 최소 PCS 출력 문지기. :data:`GATE_OFF` 를 주면 끈다
            — 창 검증 시험이 재는 것은 정밀화 장치이지 최소 규격이 아니다.
    """
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
        min_power_kw=min_power_kw,
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
    assert sample_optimum.curve_target_kw == 5_180.0
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
            definition.usage_path, definition.selection, tariff, min_power_kw=GATE_OFF
        )
        assert curve.viable_best is not None
        window = refine_window_kw(curve.baseline_demand_kw)
        assert abs(truth - curve.viable_best.target_kw) <= window, (
            f"{definition.key}: 참 최소 {truth:,.0f} kW 가 개략 최적 "
            f"{curve.viable_best.target_kw:,.0f} kW 의 ±{window:,.0f} kW 밖입니다."
        )
        assert truth in refine_targets(curve)


@pytest.mark.parametrize("key", ["C3", "C5"])
def test_곡선과_정밀화가_같은_자리를_고른다(key: str, tariff: TariffTable) -> None:
    """**46세션에 투자비 기준을 맞추자 개략 곡선이 카드 쪽으로 옮겨 붙었다.**

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
        definition.usage_path, definition.selection, tariff, min_power_kw=GATE_OFF
    )
    optimum = refine_ess_target(
        usage,
        tariff,
        definition.selection,
        curve=curve,
        baseline=bill,
        quality=quality,
        min_power_kw=GATE_OFF,
    )
    assert optimum.target_kw == CARD_BASIS_OPTIMUM[key]
    assert not optimum.moved
    # **값은 다르다.** 같아지면 정밀화가 요금을 다시 계산하지 않고 있는 것이다.
    assert curve.best is not None
    assert optimum.payback_years != curve.best.payback_years


def test_곡선과_카드가_같은_규격_용량에_투자비를_매긴다(
    sample_usage: UsageData, tariff: TariffTable
) -> None:
    """**같은 규칙을 다른 양에 걸지 않는다** (46세션 · 50세션에 격자로 옮겼다).

    46세션까지는 100 kWh 하한을 곡선은 전달 용량에, 카드는 정격 용량에 걸어
    투자비가 최대 1.14배 갈라졌다. 지금은 하한이 아니라 **규격 격자**인데, 두
    자리가 같은 격자를 같은 양에 걸어야 한다는 점은 그대로다.
    """
    from kwise.measures import snap_spec
    from kwise.measures.ess_cost import load_ess_cost_model

    model = load_ess_cost_model()
    curve = ess_target_curve(
        sample_usage.kw,
        15,
        baseline_demand_kw=5_293.44,
        base_fee_won_per_kw=float(tariff.rates(SAMPLE_SELECTION).base_won_per_kw),
    )
    for point in curve.points:
        power, capacity = snap_spec(point.power_kw, point.nameplate_capacity_kwh)
        assert (point.grid_power_kw, point.grid_capacity_kwh) == (power, capacity)
        assert point.grid_capacity_kwh >= point.nameplate_capacity_kwh, "내려 잡지 않는다"
        assert point.grid_power_kw >= point.power_kw
        assert point.investment_won == model.quote(point.grid_capacity_kwh).total_won


def _c3_material(
    tariff: TariffTable,
) -> tuple[UsageData, QualityReport, BillingResult, EssTargetCurve, CaseDefinition]:
    """C3 평탄형. 카드 기준 최소는 3,010 kW 다."""
    from kwise.report.casestudy import build_case_definitions

    directory = PROJECT_ROOT / "input" / "cases"
    if not directory.is_dir():
        pytest.skip(f"케이스 파일이 없습니다: {directory}")
    definition = next(item for item in build_case_definitions(directory) if item.key == "C3")
    # **문지기를 끈다** (50세션). C3 는 필요 출력이 7 kW 라 기본 설정에서는 후보가
    # 없어 창을 훑는 일 자체가 일어나지 않는다 — 여기서 재는 것은 창 검증이다.
    return (
        *_case_material(definition.usage_path, definition.selection, tariff, min_power_kw=GATE_OFF),
        definition,
    )


def _anchored_at(curve: EssTargetCurve, target_kw: float) -> EssTargetCurve:
    """곡선이 ``target_kw`` 를 골랐다고 두고 창 검증을 태운다.

    **46세션 전에는 C3 가 실제로 어긋나 있었다** — 곡선 3,000, 카드 3,010.
    최소 규모를 정격으로 맞추면서 둘이 같아졌으므로, 창 검증은 어긋난 자리를
    직접 심어 태운다. **지운 것이 아니라 태울 자료가 없어진 것이다.**

    심는 자리는 **3,045 kW** 다 (48세션). 격자가 5 kW 로 촘촘해져 3,030 kW 에
    ±10 kW 를 걸면 참 최소(3,035)가 창 안에 들어와 버린다.
    """
    from dataclasses import replace

    anchor = next(point for point in curve.points if point.target_kw == target_kw)
    # **정밀화의 기준점은 ``viable_best`` 다** (48세션). ``best`` 만 바꾸면 심은
    # 자리가 쓰이지 않는다 — 둘 다 옮겨야 창 검증이 그 자리에서 시작한다.
    return replace(curve, best=anchor, viable_best=anchor)


def test_창_가장자리면_넓혀_다시_찾는다(tariff: TariffTable) -> None:
    """**창이 좁으면 조용히 틀린다** (40세션 1-2).

    곡선이 3,045 kW 를 골랐다고 두면 카드 기준 최소(3,035)가 창 10 kW 의
    **가장자리**에 놓인다. 가장자리에서 잡히므로 넓혀 다시 훑어야 한다.
    """
    usage, quality, bill, curve, definition = _c3_material(tariff)
    narrow = refine_ess_target(
        usage,
        tariff,
        definition.selection,
        curve=_anchored_at(curve, 3_045.0),
        baseline=bill,
        quality=quality,
        window_kw=10.0,
        max_widen=4,
        min_power_kw=GATE_OFF,
    )
    assert narrow.target_kw == CARD_BASIS_OPTIMUM["C3"]
    assert narrow.window_kw > 10.0, "가장자리에서 잡혔으면 창을 넓혀야 한다."
    assert narrow.widened >= 1
    assert not narrow.at_edge, "넓히고 나면 가장자리가 아니어야 한다."


def test_넓히고도_가장자리면_경고를_남긴다(tariff: TariffTable) -> None:
    """**조용히 자르지 않는다.** 자른 채 두면 「그 자리가 최적」 으로 읽힌다."""
    usage, quality, bill, curve, definition = _c3_material(tariff)
    stuck = refine_ess_target(
        usage,
        tariff,
        definition.selection,
        curve=_anchored_at(curve, 3_045.0),
        baseline=bill,
        quality=quality,
        window_kw=10.0,
        max_widen=0,
        min_power_kw=GATE_OFF,
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
    """**「목표를 낮추면 나빠진다」 가 표로 읽혀야 한다** (46세션).

    창의 양 끝과 고른 자리를 반드시 넣는다. 끝을 빼면 U 가 안 보이고, 고른
    자리를 빼면 표식을 찍을 줄이 없다.

    **50세션부터 줄은 목표가 아니라 사양이다** (3-6). 격자를 쓰면 목표 여럿이 한
    사양으로 뭉치고, 뭉친 줄의 목표는 범위로 적는다.
    """
    from kwise.report.frames import ESS_SPEC_ROWS, ess_spec_frame, ess_spec_groups

    optimum = refine_ess_target(
        sample_usage,
        tariff,
        SAMPLE_SELECTION,
        curve=target_curve,
        baseline=sample_bill,
        quality=sample_report,
    )
    groups = ess_spec_groups(optimum.points)
    frame = ess_spec_frame(optimum, baseline_demand_kw=target_curve.baseline_demand_kw)
    assert len(frame) == ESS_SPEC_ROWS
    assert len(groups) < len(optimum.points), "격자를 쓰면 목표 여럿이 한 사양으로 뭉친다"

    labels = list(frame["목표 요금적용전력(kW)"])
    lows = [float(str(label).split("~")[0].replace(",", "")) for label in labels]
    assert lows == sorted(lows, reverse=True), "목표가 높은 쪽부터다"
    assert lows[0] == groups[0][0].target_kw
    assert lows[-1] == groups[-1][0].target_kw
    assert any("~" in str(label) for label in labels), "뭉친 줄은 범위로 적는다"

    # **U 가 읽힌다** — 고른 줄이 양 끝보다 짧다.
    payback = list(frame["회수기간(년)"])
    best_at = lows.index(optimum.target_kw)
    assert payback[best_at] == min(value for value in payback if value is not None)
    assert payback[0] > payback[best_at]
    assert payback[-1] > payback[best_at]

    marks = list(frame["표식"])
    assert SHORTEST_PAYBACK in marks[best_at]
    # **「최소 규모」 표식이 사라졌다** (50세션 3-4). 다섯 줄에 모두 붙어
    # 구별하는 힘이 없었다 — 격자와 최소 규격이 그 자리를 대신한다.
    assert not any("최소 규모" in mark for mark in marks)
    # **모든 줄에 붙는 표식이 없다** — 있으면 구별하는 힘이 없다.
    assert any(not mark for mark in marks)


def test_사양_표는_모두_카드_기준_참값이다(
    sample_usage: UsageData,
    tariff: TariffTable,
    sample_bill: BillingResult,
    sample_report: QualityReport,
    target_curve: EssTargetCurve,
) -> None:
    """**두 숫자 문제가 사라졌다** (46세션).

    45세션까지 화면에는 곡선 최소 26.0년과 카드 30.8년이 함께 있었다. 표의
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
    # 뭉친 줄은 목표를 범위로 적으므로 **하한**으로 찾는다 (50세션 3-6).
    lows = [
        float(str(label).split("~")[0].replace(",", "")) for label in frame["목표 요금적용전력(kW)"]
    ]
    row = frame.iloc[lows.index(optimum.target_kw)]
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
    assert float(row["출력(kW)"]) == pytest.approx(card.power_kw)
    assert float(row["용량(kWh)"]) == pytest.approx(card.capacity_kwh)
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
    """서식을 한 곳에서 만든다 — **「개략」 은 어디에도 없다** (46세션)."""
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


# ===================================================================== 48세션 · 후보 걸러내기
#
# **두 가지를 후보에서 뺀다.**
#
#     목표 미달   디스패치가 목표를 못 지킨 점. 「210 kW」 라 적고 실제
#                 요금적용전력은 264 kW 인 표가 나오던 자리다
#     마진 미달   kW당 배터리비가 10년 기본요금 절감액을 넘는 점. 소형 사무빌딩
#                 자료에서 방전 8.95h·회수 154.5년 짜리를 「최적」 으로 골랐다
#
# 그리고 격자·창을 **요금적용전력의 비율**로 잡는다. 절대 kW 는 265 kW 짜리
# 건물에서 곡선을 8점으로 만들고 창이 곡선 전체를 덮었다.

_OFFICE_CASE = PROJECT_ROOT / "input" / "사용량조회_소형사무빌딩.csv"


def test_격자를_눈금에_맞춘다() -> None:
    """**비율을 그대로 쓰면 10.587 kW 같은 격자가 나온다.** 1·2·5 눈금에 맞춘다."""
    assert snap_step_kw(10.587) == 10.0  # 샘플 5,293.44 × 0.2%
    assert snap_step_kw(0.529) == 0.5  # 소형 사무빌딩 264.68 × 0.2%
    assert snap_step_kw(6.104) == 5.0  # C3 3,052.3 × 0.2% — 기하 중점 7.07 아래
    assert snap_step_kw(7.56) == 10.0  # C6 3,780 × 0.2% — 기하 중점 위
    assert snap_step_kw(12.35) == 10.0  # C5 6,174.7 × 0.2%
    with pytest.raises(ValueError):
        snap_step_kw(0.0)


def test_샘플_격자와_창이_전과_같다() -> None:
    """**비율로 바꿔도 회귀값이 흔들리면 안 된다** (48세션).

    5,293.44 kW 에서 격자는 10 kW 그대로이고, 창은 ±100 → ±105.9 kW 로 커지되
    10 kW 격자에서 담는 점이 21개로 같다.
    """
    assert target_step_kw(5_293.44) == 10.0
    assert refine_window_kw(5_293.44) == pytest.approx(105.87, abs=0.01)
    # 5,170 ± 105.87 → 5,070 ~ 5,270. 옛 ±100 과 같은 21점이다.
    assert len(range(5_070, 5_280, 10)) == 21


def test_창이_곡선_전체를_덮지_않는다(target_curve: EssTargetCurve) -> None:
    """**덮으면 가장자리 검사가 늘 공집합이다** (48세션).

    ``at_edge`` 는 「창의 끝이되 곡선의 끝은 아닌」 자리에서만 참이다. 창이
    곡선을 다 덮으면 두 집합이 같아져 차집합이 비고, 경고가 구조적으로 못 뜬다.
    """
    span = target_curve.points[0].target_kw - target_curve.points[-1].target_kw
    assert refine_window_kw(target_curve.baseline_demand_kw) * 2 < span


def test_마진_조건의_경계_방전시간(tariff: TariffTable) -> None:
    """``기본요금단가 × 12 × 10년 ÷ 용량단가``. 이보다 길면 규모를 키워도 안 된다."""
    fee = float(tariff.rates(SAMPLE_SELECTION).base_won_per_kw)
    model = load_ess_cost_model()
    limit = viable_discharge_hours(fee, model=model)
    assert limit == pytest.approx(fee * 12 * 10 / model.per_kwh_won)
    assert limit == pytest.approx(1.031, abs=0.001)


def test_곡선이_마진_조건을_판정한다(target_curve: EssTargetCurve) -> None:
    """**요금 재계산 없이 가려낸다.** 곡선이 이미 방전시간을 내고 있다."""
    limit = target_curve.viable_limit_hours
    assert limit > 0
    for point in target_curve.points:
        assert point.viable == (0.0 < point.discharge_hours < limit)
    assert target_curve.any_viable
    # 샘플은 곡선 최소가 마진 조건 안에 있다 — 기준점이 옮겨 가지 않는다.
    assert target_curve.viable_best is target_curve.best


def test_샘플_최적이_5180이다(sample_optimum: EssOptimum) -> None:
    """**48세션의 두 필터가 샘플을 건드리지 않는다** (50세션에 자리가 옮겨 앉았다).

    49세션까지는 5,170 kW 였다. 규격 격자를 씌우자 그 자리의 정격 119.6 kWh 가
    150 kWh 로 올라가고 5,180 kW 의 85.2 kWh 는 100 kWh 로만 올라가, **더 얕은
    쪽이 이겼다** (33.6년 대 31.7년). 정밀화 21점 어디에도 목표 미달은 없다.
    """
    assert sample_optimum.viable
    assert not sample_optimum.below_minimum
    assert sample_optimum.target_kw == 5_180.0
    best = next(item for item in sample_optimum.points if item.target_kw == 5_180.0)
    assert (best.grid_power_kw, best.grid_capacity_kwh) == (150.0, 100.0)
    assert best.discharge_hours == pytest.approx(0.667, abs=0.001)
    assert best.target_met and best.viable and not best.below_min_power
    assert all(item.target_met for item in sample_optimum.points)


def test_미달_점은_후보에서_빠진다() -> None:
    """**디스패치가 이미 쥐고 있던 사실이다.** 버리면 표가 사실과 달라진다."""
    met = EssOptimumPoint(230.0, 310.0, 4.7e8, 3.1e6, 154.5, power_kw=34.7)
    unmet = replace(met, target_kw=210.0, unmet_kwh=805.9, achieved_demand_kw=264.4)
    assert met.target_met and met.eligible
    assert not unmet.target_met and not unmet.eligible
    # 마진이 없으면 목표를 지켜도 후보가 아니다.
    assert not replace(met, viable=False).eligible
    # 값이 안 매겨진 점도 후보가 아니다.
    assert not replace(met, payback_years=None).eligible


def test_사양_표가_미달을_밝힌다() -> None:
    """**「210 kW · 저감 55 kW」 옆에 실제 264 kW 를 적는다** (48세션).

    적지 않으면 표가 사실과 다르다 — 그 사양으로는 요금적용전력이 그만큼
    내려가지 않는다.
    """
    points = (
        EssOptimumPoint(
            210.0,
            583.0,
            7.4e8,
            1.5e6,
            490.7,
            54.7,
            unmet_kwh=805.9,
            achieved_demand_kw=264.4,
            grid_power_kw=75.0,
            grid_capacity_kwh=600.0,
        ),
        EssOptimumPoint(
            230.0,
            310.0,
            4.7e8,
            3.1e6,
            154.5,
            power_kw=34.7,
            grid_power_kw=50.0,
            grid_capacity_kwh=350.0,
        ),
        EssOptimumPoint(
            250.0,
            62.0,
            2.6e8,
            1.1e6,
            229.3,
            power_kw=14.7,
            viable=False,
            grid_power_kw=50.0,
            grid_capacity_kwh=100.0,
        ),
    )
    optimum = EssOptimum(230.0, 154.5, 230.0, 100.0, 0, False, points=points)
    frame = frames.ess_spec_frame(optimum, baseline_demand_kw=264.68)
    marks = dict(zip(frame["목표 요금적용전력(kW)"], frame["표식"], strict=True))
    assert "목표 미달 (실제 264 kW)" in marks["210"]
    assert marks["230"] == SHORTEST_PAYBACK
    assert "마진 미달" in marks["250"]
    # **「최소 규모」 표식은 없다** (50세션 3-4).
    assert not any("최소 규모" in mark for mark in marks.values())


def test_성립하는_점이_없으면_목표를_고르지_않는다(
    sample_usage: UsageData, tariff: TariffTable, sample_bill: BillingResult
) -> None:
    """**결론을 낸다** (48세션).

    기본요금단가를 낮추면 마진 조건 경계가 내려가 어떤 방전시간도 넘지 못한다 —
    C6 야간 피크형이 실제 자료로 이 자리에 있다. 목표를 제시하는 대신
    :data:`NOT_VIABLE_CONCLUSION` 을 내고 표는 참고로 남긴다.
    """
    curve = ess_target_curve(
        sample_usage.kw,
        15,
        baseline_demand_kw=5_293.44,
        # 성립 한계를 0.01h 로 끌어내린다. 곡선의 가장 짧은 방전시간이 0.296h 다.
        base_fee_won_per_kw=70.0,
    )
    assert curve.best is not None
    assert not curve.any_viable and curve.viable_best is None
    optimum = refine_ess_target(
        sample_usage, tariff, SAMPLE_SELECTION, curve=curve, baseline=sample_bill
    )
    assert not optimum.viable
    assert optimum.target_kw == 0.0
    assert optimum.payback_years is None
    assert texts(optimum.notices) == (NOT_VIABLE_CONCLUSION,)
    # **창을 훑지 않는다.** 표에 세울 만큼만 잰다 — 21점이 아니라 다섯이다.
    assert len(optimum.points) == SPEC_TABLE_ROWS
    frame = frames.ess_spec_frame(optimum, baseline_demand_kw=curve.baseline_demand_kw)
    # **다섯 점이 네 사양으로 뭉쳤다** (50세션 3-6). 줄 수는 사양 수를 넘지 않는다.
    assert 0 < len(frame) <= SPEC_TABLE_ROWS
    assert SHORTEST_PAYBACK not in " ".join(frame["표식"])


def test_저감량이_전_줄_0_이면_캡션이_바뀐다() -> None:
    """**캡션이 표와 어긋나 있었다** (59세션 3절 · PPT 목록 P3).

    계약전력이 과다해 요금적용전력이 하한(계약전력의 30%)에 걸려 있으면 피크를
    아무리 깎아도 기준 전력이 안 내려간다 — 사양 표의 저감량이 다섯 줄 모두
    0 kW 다. 그 위에 「목표를 낮추면 저감량은 늘지만」 이 서 있었다.

    **줄을 뭉치거나 빼지 않는다.** 0 은 못 낸 값이 아니라 이 자료의 사실이고,
    다섯 줄이 나란히 0 인 것 자체가 근거다. 바뀌는 것은 한 줄뿐이다.
    """
    import pandas as pd

    from kwise.report.frames import ESS_SPEC_CAPTION, NO_REDUCTION_CAPTION, ess_spec_caption

    zeros = pd.DataFrame({"저감량(kW)": [0.0] * 5})
    assert ess_spec_caption(zeros) == NO_REDUCTION_CAPTION
    assert "0 kW" in NO_REDUCTION_CAPTION and "기본요금" in NO_REDUCTION_CAPTION

    # 한 줄이라도 내려가면 원래 캡션이다 — 관계를 설명하는 말이 참이 된다.
    mixed = pd.DataFrame({"저감량(kW)": [0.0, 0.0, 113.0]})
    assert ess_spec_caption(mixed) == ESS_SPEC_CAPTION
    # 표가 아예 없으면 관계를 말할 것도 없으므로 기본값이다.
    assert ess_spec_caption(pd.DataFrame({"저감량(kW)": []})) == ESS_SPEC_CAPTION


def test_회수기간_50년_초과는_상한으로_적는다() -> None:
    """**500년·3,000년 같은 값은 근거로 읽히지 않는다** (50세션 3-7).

    자르는 것은 표시뿐이고 계산은 그대로 둔다 — 상한은 기준 데이터에 있다.
    화면·Excel·Word·PPT 가 같은 함수를 쓴다.
    """
    from kwise.measures import payback_display_cap_years, payback_text
    from kwise.report.standalone import _payback
    from kwise.ui import text as ui_text

    cap = payback_display_cap_years()
    assert cap == 50.0
    assert payback_text(cap) == "50.0년", "상한 자체는 자르지 않는다"
    assert payback_text(cap + 0.1) == ">50년"
    assert payback_text(578.2) == ">50년"
    assert payback_text(31.7) == "31.7년"
    assert payback_text(None) == "—"
    # 화면 서식기도 같은 자리에서 자른다.
    assert ui_text.payback(578.2) == ">50년"
    assert ui_text.payback(31.7) == "31.7년"
    # 3단계 개선안별 요약도 마찬가지다. **진짜 줄로 부른다** (70세션 2절) —
    # `SimpleNamespace` 로 흉내 내면 필드 이름이 바뀌어도 시험이 안 걸린다.
    from kwise.measures import Certainty, measure_kind
    from kwise.report.standalone import StandaloneRow

    row = StandaloneRow(
        kind=measure_kind("ess"),
        reduction="500 kW 설치",
        annual_saving_won=1.0,
        investment_won=1.0,
        payback_years=578.2,
        certainty=Certainty.MEDIUM_LOW,
    )
    assert _payback(row) == ">50년"


def test_참고_지점은_곡선_전체에_벌려_잡는다(target_curve: EssTargetCurve) -> None:
    """얕은 쪽과 깊은 쪽이 한 표에 함께 서야 「왜 안 되는가」 가 읽힌다."""
    picks = reference_targets(target_curve, SPEC_TABLE_ROWS)
    assert len(picks) == SPEC_TABLE_ROWS
    assert picks[0] == target_curve.points[0].target_kw
    assert picks[-1] == target_curve.points[-1].target_kw
    assert list(picks) == sorted(picks, reverse=True)


@pytest.mark.skipif(not _OFFICE_CASE.is_file(), reason="소형 사무빌딩 자료가 없습니다")
def test_소형_사무빌딩에서_성립하지_않는_사양을_고르지_않는다(tariff: TariffTable) -> None:
    """**48세션 조사가 시작된 자리다.**

    47세션 자료에서 정밀화가 목표 230 kW · 방전 8.95h · 회수 154.5년을 골랐다.
    kW당 배터리비가 10년 기본요금 절감액의 8.7배라 어떤 규모로도 회수되지 않고,
    배터리를 하룻밤에 되채우지 못해 220 kW 아래로는 목표 자체를 놓치고 있었다.
    """
    from kwise.diagnose import ContractInfo, diagnose
    from kwise.io import load_usage
    from kwise.quality import check_quality

    usage = load_usage(_OFFICE_CASE)
    quality = check_quality(usage, contract_kw=300.0)
    selection = TariffSelection("general_b", "high_a", "I")
    diag = diagnose(usage, tariff, ContractInfo(selection, contract_kw=300.0), quality=quality)
    assert diag.structure is not None
    curve = ess_target_curve(
        usage.kw,
        usage.meta.interval_minutes,
        baseline_demand_kw=diag.peak.billing_demand_kw,
        base_fee_won_per_kw=float(tariff.rates(selection).base_won_per_kw),
    )
    # **격자가 0.5 kW 다** — 265 kW 짜리 건물이 5,293 kW 짜리와 같은 해상도를 얻는다.
    assert curve.step_kw == 0.5
    assert len(curve.points) == 159
    # **문지기를 끄면 48세션의 결론이 그대로 선다** — 마진 조건이 8.95h 짜리를
    # 후보에서 빼고 0.97h 짜리를 고른다.
    open_gate = refine_ess_target(
        usage,
        tariff,
        selection,
        curve=ess_target_curve(
            usage.kw,
            usage.meta.interval_minutes,
            baseline_demand_kw=diag.peak.billing_demand_kw,
            base_fee_won_per_kw=float(tariff.rates(selection).base_won_per_kw),
            min_power_kw=GATE_OFF,
        ),
        baseline=diag.structure.bill,
        quality=quality,
        min_power_kw=GATE_OFF,
    )
    assert open_gate.viable
    best = next(item for item in open_gate.points if item.target_kw == open_gate.target_kw)
    assert best.target_met, "목표를 못 지키는 사양을 고르면 안 된다."
    # 8.95h 짜리는 이제 고르지 않는다.
    assert best.nameplate_capacity_kwh / best.power_kw < 1.0

    # **기본 설정에서는 아예 산출하지 않는다** (50세션 3-3). 필요 출력 6.2 kW 는
    # 상업용 ESS 최소 규격 50 kW 에 못 미친다 — 살 물건이 없다.
    optimum = refine_ess_target(
        usage, tariff, selection, curve=curve, baseline=diag.structure.bill, quality=quality
    )
    assert not optimum.viable
    assert optimum.below_minimum
    assert optimum.required_power_kw == pytest.approx(6.2, abs=0.1)
    assert optimum.required_capacity_kwh == pytest.approx(6.0, abs=0.1)
    assert optimum.required_discharge_hours == pytest.approx(0.97, abs=0.01)
    assert optimum.minimum_power_kw == 50.0
    message = texts(optimum.notices)[0]
    assert "최소 규격" in message and "50 kW" in message
    # **「경제성 없음」 이라 쓰지 않는다** — 확인된 사실이 아니다.
    assert "경제성" not in message
    # **주어가 시장이 아니라 이 건물이다** (63세션). 「제품을 찾기 어려워」 는
    # 「제품만 나오면 되겠다」 로 읽혔다 — 사유는 이 건물이 필요로 하는 양이
    # 설비 한 대보다 작다는 것이다.
    assert "찾기 어려" not in message
    assert "필요보다 큰 설비" in message
    # **회수기간을 사유로 쓰지 않는다** (63세션). 초기 검토 단계라 단단하지 않다.
    assert "회수" not in message and "년" not in message
    # 표를 싣지 않는다 — 회수기간도 목표별 사양도 낼 것이 없다.
    assert optimum.points == ()


# ===================================================================== 54세션 · 성립 판정
#
# **「고를 수 있는 목표가 없다」 는 갈래가 둘인데 하나만 그렇게 말하고 있었다.**
#
#     곡선에 마진 조건을 넘는 점이 없다      → viable=False · 목표 0    (48세션)
#     창을 넓혀도 후보가 하나도 없다          → **viable 기본값(참)이 그대로 나갔다**
#
# 뒤쪽이 나가면 3단계가 목표를 받아 카드와 PPT 를 만들고, 그 목표로 다시 계산한
# 절감액이 **음수**로 나오며, 사양 표에는 회수기간이 「—」 인 줄에 「최단
# 회수기간」 표식이 붙는다. 실물에서 다섯 줄이 모두 음수로 나온 자리다.

_NIGHT_CASE = PROJECT_ROOT / "input" / "cases" / "C6_야간 피크형.csv"


def _night_optimum(tariff: TariffTable, *, scale: float) -> tuple[EssOptimum, float]:
    """야간 피크 자료를 **단가를 낮춰** 돌린다.

    단가를 낮추면 개략 곡선의 마진 조건이 통과해 48세션 갈래를 빠져나가고,
    정밀화는 목표를 하나도 못 지켜 후보가 비는 — 바로 그 자리에 닿는다.
    """
    from kwise.diagnose import ContractInfo, diagnose
    from kwise.io import load_usage
    from kwise.measures.ess_cost import load_ess_cost_model
    from kwise.quality import check_quality
    from kwise.tariff import BillingOptions, TariffSelection, calculate_bill

    usage = load_usage(_NIGHT_CASE)
    selection = TariffSelection("general_b", "high_a", "I")
    options = BillingOptions(contract_kw=6_000.0)
    quality = check_quality(usage, contract_kw=6_000.0)
    diag = diagnose(usage, tariff, ContractInfo(selection, contract_kw=6_000.0), quality=quality)
    baseline = calculate_bill(usage, tariff, selection, options=options, quality=quality)
    peak = float(diag.peak.billing_demand_kw)
    base = load_ess_cost_model()
    model = base.with_coefficients(
        fixed_won=base.fixed_won * scale, per_kwh_won=base.per_kwh_won * scale
    )
    curve = ess_target_curve(
        usage.kw,
        usage.meta.interval_minutes,
        baseline_demand_kw=peak,
        base_fee_won_per_kw=float(tariff.rates(selection).base_won_per_kw),
        model=model,
    )
    optimum = refine_ess_target(
        usage,
        tariff,
        selection,
        curve=curve,
        baseline=baseline,
        quality=quality,
        options=options,
        model=model,
    )
    return optimum, peak


def test_후보가_없으면_성립하지_않는다고_말한다(tariff: TariffTable) -> None:
    """**깃발이 서지 않던 갈래다** (54세션 1-1).

    창을 넓혀도 「값이 매겨지고 목표를 지키고 마진이 있는」 점이 하나도 없으면
    고를 수 있는 목표가 없는 것이다. 그런데 ``viable`` 을 넘기지 않아 기본값
    참이 그대로 나갔고, 3단계가 그 목표로 ESS 를 만들었다.
    """
    optimum, _peak = _night_optimum(tariff, scale=0.1)
    # 개략 곡선은 마진을 통과한다 — 48세션 갈래가 아니다.
    assert not optimum.below_minimum
    assert optimum.points, "왜 안 되는지 보이는 참고 지점은 남는다"
    assert all(point.annual_saving_won <= 0 for point in optimum.points)

    assert not optimum.viable, "후보가 없는데 성립이라고 말한다"
    assert optimum.target_kw == 0.0, "성립하지 않으면 목표를 내지 않는다"
    assert optimum.payback_years is None
    assert any(item.fact == "ess.refine_unpriced" for item in optimum.notices)


def test_회수기간이_없는_줄에는_표식이_없다(tariff: TariffTable) -> None:
    """**절감액이 0 이하면 표식을 붙이지 않는다** (54세션 1-4).

    「최단 회수기간」 과 회수기간 「—」 가 한 줄에 있었다. 깃발이 바로잡힌
    뒤에도 이 조건은 남긴다 — 다른 자료에서 또 날 수 있다.
    """
    from kwise.measures import SHORTEST_PAYBACK
    from kwise.report.frames import ess_spec_frame, ess_spec_rows

    optimum, peak = _night_optimum(tariff, scale=0.1)
    rows = ess_spec_rows(ess_spec_frame(optimum, baseline_demand_kw=peak))
    for row in rows[1:]:
        payback, mark = row[-2], row[-1]
        if SHORTEST_PAYBACK in mark:
            assert payback != "—", f"회수기간이 없는 줄에 표식이 붙었다: {row}"
    assert not any(SHORTEST_PAYBACK in row[-1] for row in rows[1:])


def test_저감량은_실제로_내려간_만큼이다(tariff: TariffTable) -> None:
    """**목표에서 빼면 안 된다** (54세션 1-2).

    「저감량 836 kW」 옆에 「목표 미달 (실제 2,801 kW)」 가 나란히 섰다 —
    요금적용전력은 한 kW 도 안 내려갔는데 저감량이 836 kW 라고 적혀 있었다.
    """
    from kwise.report.frames import ess_spec_frame

    optimum, peak = _night_optimum(tariff, scale=0.1)
    frame = ess_spec_frame(optimum, baseline_demand_kw=peak)
    # 목표를 하나도 못 지킨 자료다 — 저감량은 전부 0 이어야 한다.
    assert list(frame["저감량(kW)"]) == [0.0] * len(frame), frame.to_string()


def test_목표를_지킨_줄은_저감량이_그대로다(
    sample_usage: UsageData, tariff: TariffTable, sample_bill: BillingResult
) -> None:
    """**회귀값 불변** (54세션 1-2). 달성값이 곧 목표라 값이 달라지지 않는다."""
    from kwise.diagnose import ContractInfo, diagnose
    from kwise.quality import check_quality
    from kwise.report.frames import ess_spec_frame
    from kwise.tariff import TariffSelection

    selection = TariffSelection("general_b", "high_a", "I")
    quality = check_quality(sample_usage, contract_kw=6_000.0)
    diag = diagnose(
        sample_usage, tariff, ContractInfo(selection, contract_kw=6_000.0), quality=quality
    )
    peak = float(diag.peak.billing_demand_kw)
    curve = ess_target_curve(
        sample_usage.kw,
        sample_usage.meta.interval_minutes,
        baseline_demand_kw=peak,
        base_fee_won_per_kw=float(tariff.rates(selection).base_won_per_kw),
    )
    optimum = refine_ess_target(
        sample_usage, tariff, selection, curve=curve, baseline=sample_bill, quality=quality
    )
    assert optimum.viable
    assert optimum.target_kw == pytest.approx(5_180.0)
    frame = ess_spec_frame(optimum, baseline_demand_kw=peak)
    chosen = frame[frame["목표 요금적용전력(kW)"].str.startswith("5,180")]
    assert len(chosen) == 1
    assert float(chosen["저감량(kW)"].iloc[0]) == pytest.approx(113.0, abs=1.0)
    assert float(chosen["연간 절감액(원)"].iloc[0]) == pytest.approx(8_264_580.0, abs=1.0)
    assert float(chosen["회수기간(년)"].iloc[0]) == pytest.approx(31.69, abs=0.01)


def test_충방전_시각이_구간으로_나온다(
    sample_usage: UsageData, tariff: TariffTable, sample_bill: BillingResult
) -> None:
    """**「22:00–22:00」 은 구간이 아니다** (54세션 1-3).

    ``시각`` 열은 구간 **시작**이라 그대로 min–max 를 적으면 오른쪽 끝이 한
    구간 짧고, 한 구간만 돌면 길이가 0 인 구간이 된다 — 35 kWh 를 충전했다면서
    시작과 끝이 같았다.
    """
    from kwise.measures import EssCostInput, evaluate_ess
    from kwise.report.frames import dispatch_schedule, ess_day_frame
    from kwise.tariff import TariffSelection
    from kwise.ui.state import reference_day

    result = evaluate_ess(
        sample_usage,
        tariff,
        TariffSelection("general_b", "high_a", "I"),
        target_kw=5_180.0,
        cost=EssCostInput.unpriced(),
        baseline=sample_bill,
    )
    day = reference_day(sample_usage)
    assert day is not None
    frame = ess_day_frame(sample_usage, result.dispatch, day.date)
    charge, discharge = dispatch_schedule(frame)
    for span in (charge, discharge):
        assert span, "구간을 못 읽었다"
        start, _, end = span.partition("–")
        assert start != end, f"길이가 0 인 구간이다: {span}"
    # 대표일의 충전은 한 구간뿐이다 — 그래도 구간으로 적힌다.
    assert charge == "22:00–22:15", charge


def test_케이스_스터디가_ESS_를_돌린다() -> None:
    """**여태 한 번도 안 돌았다** (54세션 1-5).

    요금·진단·태양광·감도는 여섯 케이스를 다 훑는데 ESS 만 빠져 있어,
    깨진 갈래가 66/66 을 통과한 채 실물에만 나왔다.
    """
    import inspect

    from kwise.report import casestudy, validity

    source = inspect.getsource(casestudy.run_one_case)
    assert "_case_ess(" in source, "케이스 스터디가 ESS 를 돌리지 않는다."
    assert "7.6 ESS" in source
    checks = inspect.getsource(validity)
    assert "_ess_checks(" in checks
    for name in (
        "ESS 성립하지 않으면 목표가 0",
        "ESS 목표를 냈으면 절감액 > 0",
        "ESS 목표 달성이면 실제 요금적용전력이 목표 이하",
        "ESS 절감액이 0 이하인 점에는 회수기간이 없다",
    ):
        assert name in checks, name


# ===================================================================== 56세션 · 갑 종별
#
# **기본 단가에서도 그 갈래가 열렸다.** 54세션은 단가를 ×0.1 로 낮춰야 열린다고
# 봤는데, 사용자 실물의 투자비는 기본 단가 그대로였다 — 갈림길은 단가가 아니라
# **계약종별**이었다.
#
#     요금적용전력 기준   피크를 낮추면 기본요금이 그만큼 준다
#     계약전력 기준       아무리 깎아도 그대로다
#
# **「계약종별」 이 아니라 「종별과 전압」 이었다** (61세션 → 89세션). 갑Ⅰ 과
# 교육용(갑) **저압**은 전력량요금까지 **단일 단가**라 충·방전 차익도 0 이고
# 왕복손실만 남는다 — 절감액이 **음수**가 된다. 개략 곡선은 그것을 모르고
# 「성립하는 목표」 를 만들어 내므로, 정밀화가 후보를 못 찾아 창만 상한까지
# 넓히고 끝난다. **교육용(갑) 고압은 89세션에 이 갈래에서 빠졌다.**

_KAP_SELECTION = ("general_a_1", "high_a", "I")


def _kap_optimum(
    tariff: TariffTable,
    contract_type: str = "general_a_1",
    voltage: str = "high_a",
    option: str = "I",
) -> EssOptimum:
    from kwise.diagnose import ContractInfo, diagnose
    from kwise.io import load_usage
    from kwise.quality import check_quality
    from kwise.tariff import BillingOptions, calculate_bill

    usage = load_usage(PROJECT_ROOT / "input" / "사용량조회_20240429.csv")
    selection = TariffSelection(contract_type, voltage, option)
    options = BillingOptions(contract_kw=6_000.0)
    quality = check_quality(usage, contract_kw=6_000.0)
    diag = diagnose(
        usage,
        tariff,
        ContractInfo(selection, contract_kw=6_000.0),
        quality=quality,
        options=options,
    )
    baseline = calculate_bill(usage, tariff, selection, options=options, quality=quality)
    curve = ess_target_curve(
        usage.kw,
        usage.meta.interval_minutes,
        baseline_demand_kw=float(diag.peak.billing_demand_kw),
        base_fee_won_per_kw=float(tariff.rates(selection).base_won_per_kw),
    )
    return refine_ess_target(
        usage,
        tariff,
        selection,
        curve=curve,
        baseline=baseline,
        quality=quality,
        options=options,
    )


@pytest.mark.parametrize(
    ("contract_type", "voltage", "option"),
    # **갑Ⅱ 둘이 61세션에 빠졌다.** 갑Ⅱ 는 저압이 없어 기본요금이 요금적용전력에
    # 붙는다 (제38조 제2항 · 제68조 제1항) — 피크를 낮추면 기본요금이 줄고,
    # 따라서 ESS 가 성립할 수 있다. 판정은 종별 이름이 아니라 기준 필드가 한다.
    #
    # **89세션에 교육용(갑)이 고압에서 빠지고 저압으로 남았다.** 제38조 ③이
    # 「설치할 수 있다」(재량)라 저압은 계량기가 없는 것이 기본값이다.
    [
        ("general_a_1", "high_a", "I"),
        ("industrial_a_1", "high_a", "I"),
        ("education_a", "low", "single"),
    ],
)
def test_계약전력_기준_종별은_피크저감으로_기본요금이_줄지_않는다(
    tariff: TariffTable, contract_type: str, voltage: str, option: str
) -> None:
    """**기본 단가에서 열리던 갈래다** (56세션 1절 · 61세션에 범위를 좁혔다).

    기본요금이 계약전력에 붙는 종별은 피크를 깎아도 줄지 않는다 (약관 제68조
    제2항). 개략 곡선은 ``저감량 × 기본요금단가`` 로 회수기간을 내므로 그런
    종별에서도 「성립하는 목표」 를 만들어 내고, 정밀화가 요금을 다시 계산해
    0 이하를 만나 후보가 빈다.
    """
    from kwise.measures.ess import BASE_FEE_ON_CONTRACT_CONCLUSION

    optimum = _kap_optimum(tariff, contract_type, voltage, option)
    assert not optimum.viable, "갑 종별에서 목표를 내면 안 된다"
    assert optimum.target_kw == 0.0
    assert optimum.points == (), "잴 것이 없으므로 표도 싣지 않는다"
    assert any(item.fact == "ess.base_fee_on_contract" for item in optimum.notices)
    message = texts(optimum.notices)[0]
    assert tariff.contract(contract_type).label in message
    assert "계약전력으로 매깁니다" in message
    assert BASE_FEE_ON_CONTRACT_CONCLUSION.startswith("{label}")


@pytest.mark.parametrize("contract_type", ["general_a_2", "industrial_a_2", "education_a"])
def test_요금적용전력_기준이면_ESS_배제_갈래를_타지_않는다(
    tariff: TariffTable, contract_type: str
) -> None:
    """**배제 조건은 종별 이름이 아니라 기본요금 기준 필드가 판정한다** (61세션 5절).

    56세션이 이 갈래를 지을 때는 「갑 종별」 이라 적었지만 조건은 처음부터
    ``base_fee_on_contract`` 였다. 갑Ⅱ 가 요금적용전력 기준으로 옮겨 가면서
    **문지기가 저절로 열렸다** — 따로 고칠 자리가 없었다.

    **89세션에 문지기가 전압을 받는다** (``base_fee_on_contract_at``). 갑Ⅱ 는
    저압이 없어 답이 그대로이고, **교육용(갑)이 고압에서 여기로 들어왔다** —
    제38조 ②로 계량기가 서므로 피크를 낮추면 기본요금이 준다. 88세션까지는
    종별 하나로 읽어 고압까지 함께 막고 있었다.
    """
    contract = tariff.contract(contract_type)
    assert not contract.base_fee_on_contract_at("high_a")
    optimum = _kap_optimum(tariff, contract_type)
    assert not any(item.fact == "ess.base_fee_on_contract" for item in optimum.notices)


def test_을_종별은_그대로다(tariff: TariffTable) -> None:
    """**회귀값 불변** (56세션). 갑 단락이 을 경로를 건드리지 않는다."""
    optimum = _kap_optimum(tariff, "general_b")
    assert optimum.viable
    assert optimum.target_kw == pytest.approx(5_180.0)
    assert len(optimum.points) == 21


def test_갑_종별_절감액은_왕복손실뿐이다(sample_usage: UsageData, tariff: TariffTable) -> None:
    """**왜 음수인지** (56세션 1절). 사용자 실물의 −3,000원이 이 값이다.

    기본요금은 계약전력에 붙어 안 줄고, 갑Ⅰ 은 전력량요금이 단일 단가라
    충·방전 차익이 0 이다 — 남는 것은 왕복손실 요금뿐이다.
    """
    from kwise.measures import EssCostInput, evaluate_ess
    from kwise.tariff import BillingOptions, calculate_bill

    selection = TariffSelection(*_KAP_SELECTION)
    options = BillingOptions(contract_kw=6_000.0)
    rates = tariff.rates(selection)
    # 단일 단가 — 계시로 갈리지 않는다.
    for season in ("summer", "spring_fall", "winter"):
        assert len({rates.rate(season, band) for band in ("light", "mid", "peak")}) == 1
    assert tariff.contract(_KAP_SELECTION[0]).base_fee_on_contract_at(_KAP_SELECTION[1])

    baseline = calculate_bill(sample_usage, tariff, selection, options=options)
    result = evaluate_ess(
        sample_usage,
        tariff,
        selection,
        target_kw=5_180.0,
        cost=EssCostInput.unpriced(),
        baseline=baseline,
        options=options,
    )
    # 사양·투자비는 을 종별과 같다 — 갈리는 것은 절감액뿐이다.
    assert result.power_kw == pytest.approx(150.0)
    assert result.capacity_kwh == pytest.approx(100.0)
    assert result.investment_won == pytest.approx(261_893_955.0, abs=1.0)
    assert result.annual_saving_won < 0, "왕복손실만 남는다"
    assert result.payback_years is None


# ===================================================================== 56세션 · 재현 조건


def test_출고층이_현재_항목을_다_갖는다() -> None:
    """**출고 복원이 항목을 지우면 안 된다** (56세션 3절).

    39·41·50·53·56세션이 판단값을 더하면서 ``data\\defaults`` 에 넣지 않아
    **11건이 출고층에 없었다.** 그 상태로 「출고값 복원」 을 누르면 그 항목들이
    사라지고, 다음 실행이 ``assumption(...)`` 에서 멈춘다.
    """
    from kwise.rules import RuleOrigin, assumptions, load_defaults, rules

    for origin, current in (
        (RuleOrigin.STATUTORY, rules()),
        (RuleOrigin.JUDGEMENT, assumptions()),
    ):
        factory = load_defaults(origin)
        missing = sorted(set(current.item_keys()) - set(factory.item_keys()))
        extra = sorted(set(factory.item_keys()) - set(current.item_keys()))
        assert not missing, f"{origin} — 출고층에 없는 항목: {missing}"
        assert not extra, f"{origin} — 현재에 없는 출고 항목: {extra}"


def test_계산_조건이_산출물에_실린다() -> None:
    """**실물만 보고 재현 조건을 알 수 있어야 한다** (56세션 3절).

    두 세션 연속 실물과 재현이 갈렸다. 계약종별·선택요금은 표지와 건물현황
    표가 이미 적고 ESS 단가 경로는 부록이 적는다 — 빠진 것은 기준 데이터였다.
    """
    from kwise.report.notices import RULES_UNCHANGED, rules_basis_line

    line = rules_basis_line()
    assert line == RULES_UNCHANGED, f"출고층과 어긋나 있다: {line}"
