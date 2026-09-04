"""개선 수단 평가 (요구사항서 7장, 11.3, 부록 B).

수단마다 요금을 다시 계산한다. 빼기로 어림한 값이 아니라는 것을 여러 곳에서 확인한다.
"""

from __future__ import annotations

import inspect
import math
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from kwise.compare import CombinationSpec, evaluate_combination
from kwise.diagnose import Diagnosis
from kwise.io import UsageData, load_usage
from kwise.measures import (
    CURTAIL_SCENARIO,
    EXTERNAL_SCENARIO,
    OFFSET_SCENARIO,
    PV_UNPRICED_REASON,
    Certainty,
    ContractStatus,
    EssCostInput,
    EssResult,
    NetLoad,
    PvCostInput,
    SolarCurve,
    SolarPoint,
    SurplusResult,
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
    offset_carry_only_max_kw,
    offset_max_kw,
    offset_settles_cash,
    roof_capacity_limit_kwp,
    size_for_target,
    solar_curve,
    solar_point,
    surplus_options,
    unit_generation_kw,
    with_load,
    with_surplus_revenue,
)
from kwise.measures.contract import (
    CONTRACT_AT_OBSERVED_MAX_NOTICE,
    TYPE_THRESHOLD_FACT,
    target_contract_kw,
)
from kwise.notices import texts
from kwise.pv import ArrayConfig, PvSystemConfig
from kwise.quality import QualityReport
from kwise.tariff import (
    BillingOptions,
    BillingResult,
    TariffDataError,
    TariffSelection,
    TariffTable,
    apply_contract_floor,
    calculate_bill,
)
from kwise.tariff.schema import switchable_selections, threshold_text, within_type_threshold
from tests._synthetic import clearsky_weather, night_peak_month, write_month

CURRENT = TariffSelection("general_b", "high_a", "I")
BEST = TariffSelection("general_b", "high_a", "II")
INTERVAL = 15
ESS_COST_WON_PER_KW = 615_231.0  # 참고단가 LFP 2025 · 1h 방전 환산값
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


def test_switch_details_cover_every_option(sample_switch: TariffSwitchResult) -> None:
    """**모든 선택요금이 기본/전력량으로 갈라진다** (17세션 1-4).

    현행·최적 둘만 상세를 내던 것은 계산을 아끼려는 최적화였는데, 화면이 나머지를
    「상세 미산출」 로 그렸다. 값이 없는 것이 아니라 쪼개지 않았을 뿐이다.
    """
    assert sample_switch.quotes
    for quote in sample_switch.quotes:
        assert quote.base_won is not None, quote.key
        assert quote.energy_won is not None, quote.key
        assert quote.base_won + quote.energy_won == pytest.approx(quote.total_won)
    assert {CURRENT, BEST} <= {quote.selection for quote in sample_switch.quotes}


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


PENDING = TariffSelection("general_a_2", "high_a", "III")
PENDING_TEXT = "2026년 12월분 요금부터"


def _small_usage(tmp_path: Path, year: int, month: int) -> UsageData:
    """한 달치 균일 부하 (15분 25 kWh = 100 kW). 갑Ⅱ 규모다."""
    return load_usage(write_month(tmp_path / f"{year}-{month:02d}.csv", year, month, kwh=25.0))


def test_a_pending_option_warns_when_the_period_starts_before_it(
    tmp_path: Path, tariff: TariffTable
) -> None:
    """**서는 판** — 갑Ⅱ 선택Ⅲ 은 2026년 12월분부터인데 기간이 그 앞이다.

    도구는 고른 선택요금 하나로 기간 전체를 간다. 그 오차를 산출물에 낸다.
    """
    result = evaluate_tariff_switch(
        _small_usage(tmp_path, 2026, 3),
        tariff,
        PENDING,
        options=BillingOptions(contract_kw=200.0),
    )
    pending = [item for item in texts(result.notices) if PENDING_TEXT in item]
    assert len(pending) == 1
    assert "실제 청구와 다를 수 있습니다" in pending[0]
    # 조문·부칙 번호는 매뉴얼로 간다. 화면 문구에 넣지 않는다.
    assert "부칙" not in pending[0] and "제2항" not in pending[0]


def test_a_pending_option_is_silent_once_the_period_starts_after_it(
    tmp_path: Path, tariff: TariffTable
) -> None:
    """**안 서는 판** — 기간 전체가 시행 뒤면 오차가 없다. 없는 오차는 안 알린다."""
    result = evaluate_tariff_switch(
        _small_usage(tmp_path, 2027, 3),
        tariff,
        PENDING,
        options=BillingOptions(contract_kw=200.0),
    )
    assert not [item for item in texts(result.notices) if PENDING_TEXT in item]


def test_options_that_are_already_in_force_never_warn(tmp_path: Path, tariff: TariffTable) -> None:
    """**요금표 판과 함께 선 선택요금은 안 알린다.**

    분석 기간은 대개 요금표 시행일보다 앞서 시작한다. 그것만으로 걸리게 두면
    안내가 선택Ⅰ·Ⅱ 까지 전부에 뜬다 — 만들자마자 값으로 보고 잡은 자리다.
    """
    result = evaluate_tariff_switch(
        _small_usage(tmp_path, 2026, 3),
        tariff,
        TariffSelection("general_a_2", "high_a", "II"),
        options=BillingOptions(contract_kw=200.0),
    )
    assert not [item for item in texts(result.notices) if "요금부터 쓸 수 있습니다" in item]


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
    assert result.floor_kw == pytest.approx(2_100.0)
    # 7,000 × 30% = 2,100 kW 로 최대수요 5,293 kW 에 못 미친다 → 낮출 이유가 없다
    assert not result.floor_binding
    assert result.target_contract_kw is None
    assert result.no_saving
    assert result.saving_won == pytest.approx(0.0)


def test_갑Ⅱ도_계약전력_조정_금액을_낸다(
    sample_usage: UsageData, tariff: TariffTable, sample_report: QualityReport
) -> None:
    """**「미산출」 의 뿌리는 기본요금 기준 오류였다** (61세션 6절).

    갑Ⅱ 의 하한 비율이 요금 데이터에 비어 있어 카드가 :data:`ContractStatus.UNKNOWN`
    을 내고 있었다. 61세션이 제68조 제1항의 30% 를 넣자 **따로 고치지 않고
    금액이 나왔다** — 같은 뿌리였다.

    **하한이 절감액의 바닥을 만든다.** 계약전력 20,000 kW 의 30% 는 6,000 kW 로
    요금적용전력 5,293 kW 를 넘으므로 지금 기본요금은 6,000 kW 로 매겨진다.
    낮추면 그 바닥이 내려가지만, 요금적용전력 아래로는 못 내려간다.
    """
    selection = TariffSelection("general_a_2", "high_a", "II")
    bill = calculate_bill(
        sample_usage,
        tariff,
        selection,
        options=BillingOptions(contract_kw=20_000.0),
        quality=sample_report,
    )
    result = evaluate_contract_adjustment(sample_usage, bill, contract_kw=20_000.0)
    assert result.status is ContractStatus.CONFIRMED
    assert result.contract_floor_ratio == pytest.approx(0.30)
    assert result.saving_won is not None and result.saving_won > 0

    assert result.target_contract_kw is not None
    rate = bill.base_rate_won_per_kw
    monthly = bill.monthly
    # **요금적용전력을 시험이 다시 만들지 않는다** (S119 ⑳). 실물이 쓰는
    # :func:`apply_contract_floor` 를 그대로 불러 하한과 1kW 반올림(제7조 ①)을
    # 함께 받는다 — 식을 여기 다시 적으면 **실물이 갈려도 시험은 제 식으로
    # 통과한다.** S118 이 ``.map(round_kw)`` 를 손으로 넣은 것이 그 신호였다.
    before = monthly["demand_before_floor_kw"].to_dict()
    now = pd.Series(apply_contract_floor(before, contract_kw=20_000.0, floor_ratio=0.30))
    then = pd.Series(
        apply_contract_floor(before, contract_kw=result.target_contract_kw, floor_ratio=0.30)
    )
    expected = float((now - then).mul(monthly["base_fee_factor"]).sum() * rate)
    assert result.saving_won == pytest.approx(expected)

    # **하한을 안 씌우면 이만큼 과다 산출된다.** 계약전력 차이로 곧장 곱한 값이다.
    naive = (20_000.0 - result.target_contract_kw) * rate * bill.base_fee_months
    assert result.saving_won < naive


def test_unknown_floor_rule_makes_no_money(
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
    # **목표도 없다** — 하한비율이 없으면 목표를 낼 근거 자체가 없다 (83세션).
    assert result.floor_kw is None
    assert result.target_contract_kw is None
    assert not result.no_saving  # 「없음」 이 아니라 「미산출」 이다
    assert any("하한 비율" in message for message in texts(result.notices))


def test_confirmed_floor_rule_recalculates_the_base_fee(
    sample_usage: UsageData, sample_bill: BillingResult
) -> None:
    """월별 요금적용전력에 하한을 씌워 기본요금을 다시 합산한다."""
    result = evaluate_contract_adjustment(
        sample_usage, sample_bill, contract_kw=7_000.0, contract_floor_ratio=1.0
    )
    assert result.status is ContractStatus.CONFIRMED
    # 목표 5,294 kW 는 **보전** 갈래에서 온다 — 관측 최대 5,293.44 를 올린 값이다
    # (106세션 1절). 포화는 가장 작은 달 4,164.48 ÷ 100% = 4,164 라 더 낮고,
    # 큰 쪽이 목표다. 하한이 모든 달에 걸린다.
    assert result.target_contract_kw == 5_294.0
    expected = (7_000.0 - 5_294.0) * 7_220.0 * 12.0
    assert result.saving_won == pytest.approx(expected)
    assert result.current_base_won == pytest.approx(7_000.0 * 7_220.0 * 12.0)
    assert result.adjusted_base_won == pytest.approx(5_294.0 * 7_220.0 * 12.0)
    assert "하한 100%" in result.saving_basis


@pytest.mark.parametrize(
    ("pct", "ratio"),
    [(None, 0.0), (85.0, 0.014), (97.0, -0.010)],
    ids=["간주값92", "미달85", "초과97"],
)
def test_절감액은_역률요금까지_담은_총액_차이다(
    sample_usage: UsageData, tariff: TariffTable, pct: float | None, ratio: float
) -> None:
    """역률요금은 그 달 기본요금에 대한 비율이다 (약관 제43조 ②) — S116 · ⑭.

    기본요금이 줄면 역률요금도 **같은 비율로 함께 준다.** 기본요금 차이만 내면
    고객이 실제로 덜 내는 돈과 어긋난다. 간주값 92% 에서는 비율이 0 이라
    종전과 같은 값이고 — **그래서 회귀 기대값이 안 움직인다.**
    """
    options = BillingOptions(contract_kw=20_000.0, power_factor_pct=pct)
    bill = calculate_bill(sample_usage, tariff, CURRENT, options=options)
    assert bill.power_factor.total_ratio == pytest.approx(ratio)

    result = evaluate_contract_adjustment(sample_usage, bill, contract_kw=20_000.0)
    target = result.target_contract_kw
    assert target is not None
    assert result.crossed_selection is None  # 같은 종별 갈래다 — 넘는 쪽은 이미 총액이다

    base_gap = result.current_base_won - (result.adjusted_base_won or 0.0)
    assert result.saving_won == pytest.approx(base_gap * (1.0 + ratio))
    # 목표에서 요금을 처음부터 다시 계산한 총액 차이와 같다.
    # 전력량요금은 계약전력과 무관하고, 목표는 관측 최대 위라 부가금도 안 붙는다.
    after = calculate_bill(
        sample_usage, tariff, CURRENT, options=replace(options, contract_kw=target)
    )
    assert result.saving_won == pytest.approx(bill.total_won - after.total_won)


def test_floor_below_the_demand_yields_no_saving(
    sample_usage: UsageData, sample_bill: BillingResult
) -> None:
    """하한이 요금적용전력에 걸리지 않으면 계약을 낮춰도 요금은 그대로다."""
    result = evaluate_contract_adjustment(
        sample_usage, sample_bill, contract_kw=7_000.0, contract_floor_ratio=0.3
    )
    assert result.saving_won == pytest.approx(0.0)
    assert any("걸리지 않아" in note for note in texts(result.notices))


def test_목표는_가장_작은_달을_하한비율로_나눈_값이다(
    sample_usage: UsageData, sample_bill: BillingResult
) -> None:
    """**여유율을 곱하지 않는다** (83세션 4). **가장 작은 달로 나눈다** (105세션 3절).

    83세션은 **연간 최대** ÷ 하한비율을 「그 아래로 내려도 더 얻을 것이 없는
    상한」 이라 적었는데 **사실이 아니었다** — 그 값에서도 하한이 연간 최대와
    같아지므로 **그보다 작은 달들은 여전히 하한으로 끌어올려진다.** 요금적용
    전력은 달마다 정해지므로(제68조 ①) 하한이 어느 달에도 안 걸리려면 **가장
    작은 달** 아래로 내려야 한다.

    20,000 kW 의 30% 는 6,000 kW 로 열세 달이 다 걸린다. 가장 작은 달은
    첫 달 4,164.48 kW(굴림 창을 못 채웠다)이고 그것을 30% 로 나누면
    13,881.6 → **13,881 kW** 다. 연간 최대로 낸 17,645 kW 보다 낮고,
    그만큼 기본요금을 더 얻는다.
    """
    result = evaluate_contract_adjustment(sample_usage, sample_bill, contract_kw=20_000.0)
    assert result.floor_binding
    ratio = result.contract_floor_ratio
    assert ratio is not None
    monthly = sample_bill.monthly["demand_before_floor_kw"]
    assert result.target_contract_kw == math.floor(float(monthly.min()) / ratio)
    assert result.target_contract_kw == 13_881.0
    # **목표에서 다시 씌운 하한은 어느 달도 안 넘는다** — 더 내려도 얻을 것이 없다.
    assert result.target_contract_kw is not None
    assert result.target_contract_kw * ratio <= float(monthly.min())
    # **연간 최대로 냈다면 열두 달이 그 값으로 끌어올려진다.** 그 차이가 이 못이다.
    assert result.target_contract_kw < math.ceil(result.demand_before_floor_kw / ratio)


def test_83세션이_상한이라_부른_값_아래로_내려도_더_얻는다(
    sample_usage: UsageData, tariff: TariffTable
) -> None:
    """**뒤집힌 사실을 값으로 본다** (83세션 → 105세션 3절 → 106세션 6절).

    83세션은 「**연간 최대 ÷ 하한비율**은 그 아래로 내려도 얻을 것이 없는
    상한이다」 라 적었고 그 문장이 세 세션을 옮겨 다녔다. 105세션이 **사실이
    아니라고 판정**했는데, 그 세션이 갈아낸 시험 여섯은 **목표끼리만 견주었을
    뿐**(`target < ceil(연간 최대 / 비율)`) **「그 아래로 내려도 얻는다」 를
    돈으로 본 자리가 없었다.** 그 자리를 여기 짓는다.

    샘플 을 20,000 kW 다. 83세션이 상한이라 부른 값은 `ceil(5,293.44 / 0.3)`
    = **17,645 kW** 인데, 그 계약전력에서도 **열세 달이 다 하한에 걸려 있다** —
    하한 5,293.5 kW 가 가장 작은 달 4,164.48 kW 를 훨씬 넘기 때문이다.
    거기서 13,881 kW 까지 더 내리면 **5,867,603.75원**을 더 얻는다.

    **S119 에 5,796,216 → 5,867,603.75 로 갈아 끼웠다** (⑳ · 제7조 ①). 요금적용
    전력이 1kW 로 접히면서 두 계약전력의 월별 기본요금 기반이 함께 움직였다 —
    이 값은 엔진의 총액 차이라 :func:`_base_fee_won` 을 안 지난다.
    """
    selection = TariffSelection("general_b", "high_a", "I")
    old_cap = math.ceil(5_293.44 / 0.3)  # 83세션이 「상한」 이라 부른 값
    assert old_cap == 17_645

    bills = {
        kw: calculate_bill(sample_usage, tariff, selection, options=BillingOptions(contract_kw=kw))
        for kw in (float(old_cap), 13_881.0)
    }
    # **그 「상한」 에서도 하한이 열세 달에 걸려 있다** — 그래서 얻을 것이 남았다.
    assert len(bills[float(old_cap)].floor_bound_months) == 13
    assert len(bills[13_881.0].floor_bound_months) == 0
    gap = bills[float(old_cap)].total_won - bills[13_881.0].total_won
    assert gap == pytest.approx(5_867_603.75, abs=1.0)


def test_목표는_관측_최대_아래로_안_내려간다(tmp_path: Path, tariff: TariffTable) -> None:
    """**달별 수요의 최대는 관측 최대가 아니다** (106세션 1절 ㄹ).

    105세션이 「보전」 갈래를 지으며 「달별 수요의 최대가 곧 관측 최대다」 라
    적었는데 **사실이 아니다.** ``demand_before_floor_kw`` 는 요금적용전력
    산정 대상 수요라 **경부하(22~08시)와 비대상월이 빠져 있다** — 야간 피크형은
    둘이 몇 배로 벌어진다.

    이 벌은 야간 2,000 kW · 정오 400 kW · 나머지 200 kW 다. 계약 3,000 kW 의
    하한 900 kW 가 정오 400 kW 를 넘어 하한이 걸린다.

        포화   400 ÷ 30% = 1,333 kW   ← 여기서 멈추면 실측이 667 kW 넘는다
        보전   관측 최대 2,000 kW     ← 목표는 이쪽이다

    **실물에서도 같은 자리가 있다** — 케이스 C6(야간 피크형)은 관측 최대
    10,920.64 kW 인데 달별 하한 전 최대가 2,801.00 kW 다. 고치기 전에는 계약
    12,012.7 kW 에서 목표 **7,415 kW** 가 나와 실측이 **935구간·3,505.64 kW**
    를 넘었다 (고친 뒤 10,921 kW · 초과 0구간). C6 자료는 저장소에 없어
    이 못은 합성 벌로 같은 사실을 본다.
    """
    usage = load_usage(
        night_peak_month(tmp_path / "night.csv", night_kwh=500.0, midday_kwh=100.0, other_kwh=50.0)
    )
    options = BillingOptions(contract_kw=3_000.0)
    bill = calculate_bill(
        usage, tariff, TariffSelection("general_b", "high_a", "I"), options=options
    )

    observed_max = float(usage.kw.max())
    monthly_max = float(bill.monthly["demand_before_floor_kw"].max())
    # **뿌리부터 값으로 본다** — 둘이 같다는 전제가 틀렸다.
    assert observed_max == pytest.approx(2_000.0)
    assert monthly_max == pytest.approx(400.0)

    result = evaluate_contract_adjustment(usage, bill, contract_kw=3_000.0)
    assert result.reducible
    assert result.target_contract_kw == pytest.approx(2_000.0)
    # **목표를 권해도 초과사용부가금 자리에 안 놓는다.**
    assert int((usage.kw > result.target_contract_kw).sum()) == 0
    # 포화만 봤다면 여기서 멈춘다 — 그 값은 관측 최대 아래다.
    assert math.floor(monthly_max / 0.3) == 1_333
    assert observed_max > 1_333


def test_목표는_초과_0_안의_총액_최저이고_동점이면_현행에_가장_가까운_값이다(
    tmp_path: Path, sample_usage: UsageData, tariff: TariffTable
) -> None:
    """**규칙을 값으로 본다** (114세션 → 115세션 · `docs\\CALC_LOGIC.md` 2부).

        계약전력 목표는 초과사용부가금이 0 인 값 가운데 총액이 가장 낮은 값으로
        고른다. 총액이 같은 값이 여럿이면 현행 계약전력에 가장 가까운 값을
        고른다 — 현행이 그 구간 안이면 조정을 권하지 않는다.

    ②-31 은 「보전 갈래가 총액을 안 보고 목표를 고른다」 였다. 산식
    (:func:`target_contract_kw`)에 총액이 인자로 없는 것은 지금도 그대로인데,
    **값으로는 두 갈래가 다 이 규칙과 같은 답을 낸다** (114세션 1절 · 벌
    열일곱 전수). 그 사실이 조용히 깨지지 않게 여기 못을 박는다.

    **총액은 다시 계산한 값이다** — 계약전력을 바꾸면 요금적용전력 하한과
    초과사용부가금이 함께 바뀌고 최적 선택요금도 바뀔 수 있으므로, 절감액을
    빼서 만들지 않고 후보마다 처음부터 계산해 **가장 싼 것**을 쓴다.
    종별 문턱(300 kW)은 두 벌 다 한참 위에 있어 안 걸린다 — 걸리는 벌은
    `small-a2-was` 이고 그것은 저장소에 없는 자료를 쓴다.

    **동점을 115세션에 정했다.** 앞서는 「총액이 같은 값은 통과시킨다」 였다 —
    규칙이 동점을 안 말했으므로 시험이 먼저 정하지 않았다. 이제 규칙이
    말하므로 **구간 안 아무 데나가 아니라 「현행에 가장 가까운 값」** 을 본다.
    포화 벌의 구간은 5,294~13,881 이고 현행이 20,000 이라 **위끝이 답**이다.

    벌 셋을 본다. **저장소에 있는 자료만 쓴다.**

        보전 갈래   합성 야간 피크 한 달. 관측 최대 2,000 kW · 목표 2,000 kW
        포화 갈래   실측 샘플 을 20,000 kW. 초과 0 하한 5,294 · 목표 13,881 kW
        구간 안     같은 실측 샘플 을 6,000 kW. 구간 안이라 **목표를 안 낸다**
    """
    selection = TariffSelection("general_b", "high_a", "I")

    def cheapest_total(usage: UsageData, kw: float) -> BillingResult:
        """그 계약전력에서 **가장 싼** 한 벌. 선택요금을 다시 고른다."""
        options = BillingOptions(contract_kw=kw)
        return min(
            (
                calculate_bill(usage, tariff, item, options=options)
                for item in switchable_selections(tariff, selection)
            ),
            key=lambda item: item.total_won,
        )

    night = load_usage(
        night_peak_month(tmp_path / "night.csv", night_kwh=500.0, midday_kwh=100.0, other_kwh=50.0)
    )
    herds = (
        (night, 3_000.0, 2_000.0),
        (sample_usage, 20_000.0, 13_881.0),
    )
    for usage, contract_kw, expected_target in herds:
        options = BillingOptions(contract_kw=contract_kw)
        bill = calculate_bill(usage, tariff, selection, options=options)
        result = evaluate_contract_adjustment(
            usage, bill, contract_kw=contract_kw, table=tariff, options=options
        )
        target = result.target_contract_kw
        assert target == pytest.approx(expected_target)

        # ㄱ. 목표에서 초과사용부가금이 0 이다 — 「초과 0 인 값 가운데」 의 앞 절반.
        at_target = cheapest_total(usage, target)
        assert at_target.excess.exceeded_months == ()
        assert at_target.total_excess_won == pytest.approx(0.0)

        # ㄴ. 목표보다 낮은 초과 0 값 가운데 **더 싼 것이 없다** — 뒤 절반.
        #     아래끝은 초과가 0 이 되는 가장 낮은 계약전력이다. 그 아래는
        #     초과가 서므로 규칙이 보는 자리가 아니다.
        floor_kw = math.ceil(float(usage.kw.dropna().max()))
        assert int((usage.kw > floor_kw).sum()) == 0
        assert int((usage.kw > floor_kw - 1).sum()) > 0
        for kw in (floor_kw, (floor_kw + target) / 2.0, target - 1.0):
            if kw < floor_kw:
                continue
            assert cheapest_total(usage, kw).total_won >= at_target.total_won - 1.0, kw

        # ㄷ. **동점이면 현행에 가장 가까운 값이다** (115세션). 아래끝이
        #     목표와 **다른 자리인데 총액이 한 원도 안 다르면** 동점이고,
        #     그때 목표는 현행 쪽 끝이어야 한다. 보전 갈래는 아래끝과 목표가
        #     **같은 값**이라(단일점) 이 자리를 지나간다 — 고를 것이 없다.
        at_floor = cheapest_total(usage, float(floor_kw))
        if floor_kw < target and at_floor.total_won == pytest.approx(at_target.total_won):
            assert abs(target - contract_kw) < abs(floor_kw - contract_kw), target

    # ㄹ. **현행이 그 구간 안이면 조정을 권하지 않는다** — 규칙의 마지막 절.
    #     같은 실측 샘플의 현행 6,000 kW 는 구간 5,294~13,881 안이라 목표가
    #     없어야 한다 (`large-b` 벌의 자리다). 하한이 한 달도 안 걸려
    #     :func:`target_contract_kw` 를 아예 안 부르는 갈래이고, 그래서
    #     산식의 날값 13,881 이 산출물로 새어 나가지 않는다.
    inside = BillingOptions(contract_kw=6_000.0)
    at_inside = evaluate_contract_adjustment(
        sample_usage,
        calculate_bill(sample_usage, tariff, selection, options=inside),
        contract_kw=6_000.0,
        table=tariff,
        options=inside,
    )
    assert at_inside.target_contract_kw is None
    assert at_inside.status is ContractStatus.CONFIRMED


def test_목표_산식은_부동소수_부스러기에_한_칸_안_밀린다() -> None:
    """**나눗셈이 들어간 자리는 한 칸씩 밀린다** (83세션 → 106세션 2절).

    83세션이 앓던 병이다 — ``132.3 / 0.3`` 이 ``441.00000000000006`` 이라
    그대로 올리면 **442** 가 되고 화면·PPT·Excel 이 1 kW 어긋난 목표를 적었다.
    105세션이 산식을 「포화(내림)와 보전(올림)의 큰 쪽」 으로 갈면서 **미는
    방향이 둘로 늘었다** — 내림은 아래로, 올림은 위로 민다. **둘 다 본다.**

    부스러기를 터는 자리는 ``±1e-9`` 다. 그 한 자리가 빠지면 이 못이 문다.
    """
    # 포화 — 하한을 다시 비율로 나눈 값이 정수 바로 아래로 떨어진다.
    crumb = 218 * 0.3  # 65.39999999999999
    assert crumb / 0.3 == 217.99999999999997
    assert math.floor(crumb / 0.3) == 217  # 그대로 내리면 한 칸 아래
    assert target_contract_kw({"m": crumb}, 0.3, observed_max_kw=0.0) == 218.0

    # 보전 — 관측 최대가 정수 바로 위로 떨어진다.
    over = 132.3 / 0.3  # 441.00000000000006
    assert math.ceil(over) == 442  # 그대로 올리면 한 칸 위
    assert target_contract_kw({"m": 0.0}, 0.3, observed_max_kw=over) == 441.0


def test_penalty_warning_only_when_lowering_helps(
    sample_usage: UsageData, sample_bill: BillingResult
) -> None:
    """**낮출 자리가 있을 때만 낸다** (83세션).

    하한이 지는 갈래에서는 낮출 이유가 없다 — 그 자리에서 「하향은 되돌리기
    어렵다」 를 읽히면 하지도 못할 일을 조심하라는 말이 된다.
    """
    binding = evaluate_contract_adjustment(
        sample_usage, sample_bill, contract_kw=7_000.0, contract_floor_ratio=1.0
    )
    assert any("위약금" in message for message in texts(binding.notices))
    assert any("12개월간 적용" in message for message in texts(binding.notices))

    slack = evaluate_contract_adjustment(
        sample_usage, sample_bill, contract_kw=7_000.0, contract_floor_ratio=0.3
    )
    assert not any("위약금" in message for message in texts(slack.notices))


def test_invalid_floor_ratio_raises(sample_usage: UsageData, sample_bill: BillingResult) -> None:
    with pytest.raises(ValueError, match="하한 비율"):
        evaluate_contract_adjustment(
            sample_usage, sample_bill, contract_kw=7_000.0, contract_floor_ratio=1.5
        )


def test_목표가_종별_경계를_넘으면_그_사실을_낸다(
    sample_usage: UsageData, tariff: TariffTable, sample_report: QualityReport
) -> None:
    """**갑Ⅱ 는 300 kW 미만 종별이다** (96세션).

    계약전력 20,000 kW 는 그 자체가 갑Ⅱ 에 없는 값이고 목표 13,881 kW 도
    경계 밖이다 — 그 값으로 바꾸면 종별이 바뀌므로 지금 단가로 낸 절감액이
    아니다. **단가를 갈아 끼우지는 않는다** — 경계를 넘는다는 사실까지가
    이 안내의 몫이다.

    **목표는 105세션에 17,645 → 13,881 kW 로 내려갔다** (가장 작은 달로 나눈다).
    경계 밖이라는 사실은 그대로다.
    """
    bill = calculate_bill(
        sample_usage,
        tariff,
        TariffSelection("general_a_2", "high_a", "II"),
        options=BillingOptions(contract_kw=20_000.0),
        quality=sample_report,
    )
    assert bill.threshold_kw == pytest.approx(300.0)
    assert bill.threshold_direction == "below"

    result = evaluate_contract_adjustment(sample_usage, bill, contract_kw=20_000.0)
    assert result.target_contract_kw == 13_881.0
    crossing = [item for item in result.notices if item.fact == TYPE_THRESHOLD_FACT]
    assert len(crossing) == 1
    assert "300 kW 미만" in crossing[0].text
    assert "13,881 kW" in crossing[0].text


def test_경계_안이면_안내를_내지_않는다(
    sample_usage: UsageData, sample_bill: BillingResult
) -> None:
    """**안 넘는 판이 실물에 있다** (96세션). 을은 300 kW **이상** 종별이라
    목표 5,294 kW 는 경계 안이다 — 뜨지 않아야 할 자리에서 안 뜨는지 본다.
    """
    assert sample_bill.threshold_direction == "above"
    result = evaluate_contract_adjustment(
        sample_usage, sample_bill, contract_kw=7_000.0, contract_floor_ratio=1.0
    )
    assert result.target_contract_kw == 5_294.0
    assert not [item for item in result.notices if item.fact == TYPE_THRESHOLD_FACT]


@pytest.fixture(scope="session")
def small_general_b_usage(tmp_path_factory: pytest.TempPathFactory) -> UsageData:
    """을 종별인데 **최대수요가 90 kW 아래**인 한 달치 (98세션).

    실측 벌 넷은 하나도 경계를 넘지 않는다 — 을에서 목표(가장 작은 달 ÷ 30%)가
    300 kW 아래로 가려면 그 달이 90 kW 미만이어야 한다. 그 자리를 짓는다.
    **한 달치라 가장 작은 달과 최대수요가 같은 수다.**
    """
    from tests._synthetic import make_labels, month_dates, write_csv

    rows = [
        (label, 20.0)  # 15분 20 kWh = 80 kW
        for date in month_dates(2024, 3)
        for label in make_labels(date)
    ]
    return load_usage(write_csv(tmp_path_factory.mktemp("small-b") / "flat.csv", rows))


def test_목표가_문턱_아래로_가면_넘어간_종별로_다시_계산한다(
    small_general_b_usage: UsageData, tariff: TariffTable
) -> None:
    """**단가를 갈아 끼운다** (98세션). 96세션은 경계를 읽기만 했다.

    최대수요 80 kW · 계약 400 kW 의 을 고객이다. 하한 120 kW 가 최대수요를
    이기므로 목표는 80 ÷ 30% 을 **내린** 266 kW 이고, 그 값은 300 kW 아래라
    종별이 일반용전력(갑)Ⅱ 로 바뀐다 (약관 제57조 ② 1. · ④).

    **105세션에 267 → 266 이 됐다.** 267 kW 의 하한은 80.1 kW 로 최대수요
    80 kW 를 **아직 넘어** 그 달들이 80.1 로 매겨진다 — 266 이 하한을 79.8 로
    내려 마지막 한 원까지 얻는 자리다.

    **절감액이 총액 차이다.** 종별이 바뀌면 전력량요금 단가까지 함께 바뀌므로
    기본요금만 빼면 절반만 맞는 값이 된다.
    """
    options = BillingOptions(contract_kw=400.0)
    bill = calculate_bill(
        small_general_b_usage, tariff, TariffSelection("general_b", "high_a", "I"), options=options
    )
    result = evaluate_contract_adjustment(
        small_general_b_usage, bill, contract_kw=400.0, table=tariff, options=options
    )

    assert result.crosses_type
    assert result.crossed_selection is not None
    assert result.crossed_selection.contract_type == "general_a_2"
    assert result.crossed_selection.voltage == "high_a"
    assert result.crossed_label == "일반용전력(갑)Ⅱ"
    # 목표는 **문턱 바로 아래**다. 같은 종별 목표 266 kW 가 이미 그보다 낮다.
    assert result.target_contract_kw == pytest.approx(266.0)
    assert result.current_total_won is not None and result.crossed_total_won is not None
    assert result.saving_won == pytest.approx(result.current_total_won - result.crossed_total_won)
    assert "일반용전력(갑)Ⅱ 고압A 선택Ⅱ 로 종별을 바꿔" in result.saving_basis

    # **빼기로 어림한 값이 아니다.** 전환 후 총액은 그 종별로 처음부터 계산한다.
    crossed = calculate_bill(
        small_general_b_usage,
        tariff,
        result.crossed_selection,
        options=replace(options, contract_kw=result.target_contract_kw),
    )
    assert result.crossed_total_won == pytest.approx(crossed.total_won)

    # 같은 종별 안에서만 보면 기본요금 차이뿐이라 **훨씬 작다.**
    same_type_only = evaluate_contract_adjustment(small_general_b_usage, bill, contract_kw=400.0)
    assert same_type_only.saving_won is not None and result.saving_won is not None
    assert result.saving_won > same_type_only.saving_won


def test_넘는_판의_근거표는_총액_둘의_차이를_적는다(
    small_general_b_usage: UsageData, tariff: TariffTable
) -> None:
    """**산식 칸이 값과 산술로 맞아야 한다** (100세션 0절).

    99세션이 하한이 지는 띠를 연 뒤 덱의 부록 19장이 「현재 기본요금
    22,642,000 − 조정 후 기본요금 25,809,000 = 21,810,000원」 이라고 적고
    있었다. 종별을 넘으면 차액의 상대가 기본요금이 아니라 **총액**이다.
    """
    from kwise.report.worksheet import contract_worksheet

    options = BillingOptions(contract_kw=400.0)
    bill = calculate_bill(
        small_general_b_usage, tariff, TariffSelection("general_b", "high_a", "I"), options=options
    )
    result = evaluate_contract_adjustment(
        small_general_b_usage, bill, contract_kw=400.0, table=tariff, options=options
    )
    assert result.crosses_type

    frame = contract_worksheet(result).frame()
    shown = dict(zip(frame["구분"], frame["값"], strict=True))
    formula = dict(zip(frame["구분"], frame["산식"], strict=True))

    # 기본요금 두 줄은 이 갈래에 서지 않는다 — 서면 빼기가 어긋난다.
    assert "조정 후 기본요금" not in shown
    assert formula["목표 계약전력"] == "일반용전력(갑)Ⅱ 문턱 바로 아래"
    assert formula["절감액"] == "현행 − 바뀐 종별"

    def _won_of(label: str) -> int:
        return int(shown[label].removesuffix("원").replace(",", ""))

    # **천원 절사는 뺄셈과 함께 가지 않는다** (105세션 3절에 값으로 봤다).
    # 세 칸이 각자 잘리므로 앞 둘의 차가 절감액 칸과 **1,000원까지** 어긋날 수
    # 있다 — 100세션이 잡은 것은 22,642,000 − 25,809,000 = 21,810,000 처럼
    # **상대가 아예 다른** 어긋남이라 이 여유로도 그대로 잡힌다. 절사 자체는
    # 미해결 ②-29 다.
    gap = _won_of("현행 종별 총 요금") - _won_of("일반용전력(갑)Ⅱ 총 요금") - _won_of("절감액")
    assert abs(gap) <= 1_000


def test_넘는_판의_안내는_종별이_바뀐다고_말한다(
    small_general_b_usage: UsageData, tariff: TariffTable
) -> None:
    """**말할 것은 「계약전력이 줄었다」 가 아니라 「종별이 바뀐다」 다** (98세션).

    **문구가 늘지 않았다** — 넘는 판에서는 「전력량요금은 변하지 않습니다」 를
    빼고 그 자리에 종별 안내가 선다. 전력량요금 단가도 함께 바뀌므로 그 문장은
    넘는 판에서 사실과 어긋난다.
    """
    options = BillingOptions(contract_kw=400.0)
    bill = calculate_bill(
        small_general_b_usage, tariff, TariffSelection("general_b", "high_a", "I"), options=options
    )
    result = evaluate_contract_adjustment(
        small_general_b_usage, bill, contract_kw=400.0, table=tariff, options=options
    )
    facts = [item.fact for item in result.notices]
    assert facts.count(TYPE_THRESHOLD_FACT) == 1
    assert "contract.energy_unchanged" not in facts

    crossing = next(item for item in result.notices if item.fact == TYPE_THRESHOLD_FACT)
    assert "계약종별이 일반용전력(갑)Ⅱ 로 바뀝니다" in crossing.text
    assert "266 kW" in crossing.text
    # **약관 원문을 그대로 옮기지 않는다** — 설비 이름이 딸려 들어온다.
    assert "전력량계" not in crossing.text
    assert "최대수요전력계" not in crossing.text

    # **요금표를 안 주면 96세션의 옛 안내가 그대로 선다** — 갈아 끼울 단가가
    # 없으니 「지금 종별 단가로 낸 값이다」 가 여전히 사실이다.
    no_table = evaluate_contract_adjustment(small_general_b_usage, bill, contract_kw=400.0)
    old = next(item for item in no_table.notices if item.fact == TYPE_THRESHOLD_FACT)
    assert "지금 종별 단가로 낸 값입니다" in old.text
    assert "contract.energy_unchanged" in [item.fact for item in no_table.notices]


def test_경계를_안_넘는_판은_요금표를_줘도_종전과_같다(
    sample_usage: UsageData, sample_bill: BillingResult, tariff: TariffTable
) -> None:
    """**안 넘는 판이 새면 갈래가 잘못 선 것이다** (98세션).

    을 7,000 kW · 최대수요 5,293.4 kW 는 목표가 17,645 kW 라 300 kW 문턱을
    넘을 길이 없다 — 요금표를 줘도 값이 한 자리도 움직이지 않아야 한다.
    """
    before = evaluate_contract_adjustment(
        sample_usage, sample_bill, contract_kw=7_000.0, contract_floor_ratio=1.0
    )
    after = evaluate_contract_adjustment(
        sample_usage,
        sample_bill,
        contract_kw=7_000.0,
        contract_floor_ratio=1.0,
        table=tariff,
        options=BillingOptions(contract_kw=7_000.0),
    )
    assert not after.crosses_type
    assert after.target_contract_kw == before.target_contract_kw
    assert after.saving_won == before.saving_won
    assert after.current_base_won == before.current_base_won
    assert after.adjusted_base_won == before.adjusted_base_won
    # **근거 줄에는 꼬리가 붙는다** (S112 2·5절). 요금표를 주면 목표 계약전력에서
    # 선택요금을 다시 고르고 그 사실을 근거에 적는다 — **금액은 안 움직인다**
    # (재선정 몫은 ``retuned_saving_won`` 에 갈라 둔다). 이 시험이 지키는 것은
    # 「종별 문턱 갈래가 안 넘는 판에서 새지 않는다」 이고 그쪽은 그대로다.
    assert after.saving_basis.startswith(before.saving_basis)
    assert before.retuned_selection is None  # 요금표가 없으면 못 고른다


def test_요금표만_주고_옵션을_안_주면_멈춘다(
    sample_usage: UsageData, sample_bill: BillingResult, tariff: TariffTable
) -> None:
    """**다른 옵션으로 다시 계산하면 총액 차이가 종별 때문이 아니게 된다.**

    조용히 기본 옵션으로 계산하지 않고 그 자리에서 멈춘다.
    """
    with pytest.raises(ValueError, match="options"):
        evaluate_contract_adjustment(sample_usage, sample_bill, contract_kw=7_000.0, table=tariff)


def test_문턱_아래_종별은_요금_데이터가_정한다(tariff: TariffTable) -> None:
    """**코드에 짝을 박지 않는다.** 을 셋만 값을 들고, 갑은 비어 있다 —
    계약전력 조정은 낮추는 권고만 하므로 위로 넘는 갈래가 없다.
    """
    below = {key: contract.below_threshold_key for key, contract in tariff.contract_types.items()}
    assert below == {
        "general_b": "general_a_2",
        "industrial_b": "industrial_a_2",
        "education_b": "education_a",
        "general_a_1": None,
        "general_a_2": None,
        "industrial_a_1": None,
        "industrial_a_2": None,
        "education_a": None,
    }


@pytest.fixture(scope="session")
def floor_losing_general_b_usage(tmp_path_factory: pytest.TempPathFactory) -> UsageData:
    """을 종별이고 **최대수요가 하한과 문턱 사이**인 한 달치 (99세션).

    계약 400 kW 에서 하한 120 kW 는 최대수요 200 kW 에 **진다** — 같은 종별
    안에서는 낮출 이유가 없는 자리다. 그런데 문턱 바로 아래 299 kW 는 최대수요
    위에 있어 **넘어갈 자리는 있다.** 98세션이 못 보던 띠가 이것이다.
    """
    from tests._synthetic import make_labels, month_dates, write_csv

    rows = [
        (label, 50.0)  # 15분 50 kWh = 200 kW
        for date in month_dates(2024, 3)
        for label in make_labels(date)
    ]
    return load_usage(write_csv(tmp_path_factory.mktemp("floor-losing") / "flat.csv", rows))


def test_하한이_져도_문턱_아래_종별로_넘어간다(
    floor_losing_general_b_usage: UsageData, tariff: TariffTable
) -> None:
    """**후보는 하한 갈래 밖에 있다** (99세션).

    98세션은 문턱 아래 종별을 「하한이 이긴다」 가지 안에서만 봤다. 그래서
    하한이 지는 을 판은 전환 이득을 통째로 0 으로 냈다 — 용인 실측 을 400 kW
    에서 **0원 대 8,725,941원**이었다.

    **하한이 지는 것과 낮출 자리가 없는 것은 다른 사실이다.**
    """
    options = BillingOptions(contract_kw=400.0)
    bill = calculate_bill(
        floor_losing_general_b_usage,
        tariff,
        TariffSelection("general_b", "high_a", "I"),
        options=options,
    )
    result = evaluate_contract_adjustment(
        floor_losing_general_b_usage, bill, contract_kw=400.0, table=tariff, options=options
    )

    # 하한은 진다. 그래도 낮출 자리는 있다 — 둘이 다른 사실이다.
    assert not result.floor_binding
    assert result.reducible
    assert result.crosses_type
    assert result.target_contract_kw == pytest.approx(299.0)
    assert result.crossed_label == "일반용전력(갑)Ⅱ"

    # **절감액이 총액 차이다.** 빼기로 어림한 값이 아니다.
    assert result.current_total_won is not None and result.crossed_total_won is not None
    assert result.saving_won == pytest.approx(result.current_total_won - result.crossed_total_won)
    assert result.saving_won is not None and result.saving_won > 0.0
    assert result.crossed_selection is not None
    crossed = calculate_bill(
        floor_losing_general_b_usage,
        tariff,
        result.crossed_selection,
        options=replace(options, contract_kw=299.0),
    )
    assert result.crossed_total_won == pytest.approx(crossed.total_won)

    # **안내가 실제로 뜬다.** 「낮춰도 안 준다」 는 이 판에서 거짓이라 빠진다.
    facts = [item.fact for item in result.notices]
    assert facts.count(TYPE_THRESHOLD_FACT) == 1
    assert "contract.floor_not_binding" not in facts
    assert "contract.energy_unchanged" not in facts

    # **결론도 하한을 말하지 않는다.** 하한이 최대수요보다 높다고 적으면 거짓이다.
    from kwise.report.document import measure_entries

    entry = next(item for item in measure_entries(contract=result) if item.kind.key == "contract")
    assert "계약종별이 일반용전력(갑)Ⅱ 로 바뀌어" in entry.conclusion
    assert "보다 높아" not in entry.conclusion
    assert entry.actionable


@pytest.mark.parametrize(
    ("power_factor_pct", "expected_ratio"),
    [(85.0, 0.014), (97.0, -0.01)],
)
def test_종별을_넘는_갈래는_역률이_걸려도_절감액이_총액_차이_그대로다(
    floor_losing_general_b_usage: UsageData,
    tariff: TariffTable,
    power_factor_pct: float,
    expected_ratio: float,
) -> None:
    """**S116 의 곱셈은 이 갈래에 닿지 않는다** (S117 3절 · ⑭).

    ⑭ 를 닫은 산식 ``(현재 − 조정 후 기본요금) × (1 + 역률 비율)`` 은
    **절감액이 기본요금 차이일 때** 선다. 종별을 넘으면 절감액은 **총액
    차이**이고 역률요금은 이미 그 총액 안에 있다 — 곱하면 두 번 센다.

    **S116 에는 안 드러났다.** 그 갈래 벌 셋이 세 역률에서 앞뒤 값이 같아
    0 으로 나왔는데, 재 보니 **역률이 안 걸려서가 아니라 식이 맞아서** 0
    이었다. 여기서는 역률을 실제로 걸어 그 사실을 못으로 박는다.
    """
    options = BillingOptions(contract_kw=400.0, power_factor_pct=power_factor_pct)
    bill = calculate_bill(
        floor_losing_general_b_usage,
        tariff,
        TariffSelection("general_b", "high_a", "I"),
        options=options,
    )
    # **역률이 실제로 걸린다.** 이 줄이 빨개지면 아래 확인이 뜻을 잃는다 —
    # 「걸리는데도 안 새는가」 를 보는 시험이지 0 을 0 과 견주는 시험이 아니다.
    assert bill.power_factor.total_ratio == pytest.approx(expected_ratio)
    assert bill.total_power_factor_won != 0.0

    result = evaluate_contract_adjustment(
        floor_losing_general_b_usage, bill, contract_kw=400.0, table=tariff, options=options
    )
    assert result.crosses_type
    assert result.current_total_won is not None and result.crossed_total_won is not None
    assert result.saving_won is not None

    # **절감액은 총액 차이 그대로다.** 곱셈이 이 갈래로 새면 여기서 어긋난다.
    assert result.saving_won == pytest.approx(result.current_total_won - result.crossed_total_won)

    # **기본요금 차이로는 이 값이 안 나온다.** 종별을 넘으면 기본요금 단가가
    # 올라 조정 후가 **현재보다 크고**(1,444,000 → 1,646,000) 곱셈 식은 음수가
    # 된다 — 총액이 싼 것은 전력량요금이 싸기 때문이다.
    assert result.adjusted_base_won is not None
    assert result.adjusted_base_won > result.current_base_won
    from_base_fee = (result.current_base_won - result.adjusted_base_won) * (
        1.0 + bill.power_factor.total_ratio
    )
    assert from_base_fee < 0.0 < result.saving_won

    # **요금표를 안 주면 종전 그대로다** — 갈아 끼울 단가가 없다.
    without = evaluate_contract_adjustment(floor_losing_general_b_usage, bill, contract_kw=400.0)
    assert not without.reducible
    assert without.saving_won == pytest.approx(0.0)


def test_후보가_최대수요_아래면_안_넘는다(
    sample_usage: UsageData, sample_bill: BillingResult, tariff: TariffTable
) -> None:
    """**안 뜨는 판을 함께 박는다** (99세션).

    대형 을 6,000 kW 는 하한 1,800 kW 가 최대수요 5,293.44 kW 에 지는데,
    문턱 바로 아래 299 kW 는 그 최대수요보다 **낮다** — 초과사용부가금 대상이
    되므로 권고하지 않는다. **화면 감사 네 조건 가운데 을 조건이 이것이다.**
    """
    options = BillingOptions(contract_kw=6_000.0)
    result = evaluate_contract_adjustment(
        sample_usage, sample_bill, contract_kw=6_000.0, table=tariff, options=options
    )
    assert not result.floor_binding
    assert not result.reducible
    assert not result.crosses_type
    assert result.no_saving
    assert result.saving_won == pytest.approx(0.0)
    assert "contract.floor_not_binding" in [item.fact for item in result.notices]


def test_목표가_현행과_같으면_낮출_자리가_없다(tariff: TariffTable) -> None:
    """**목표 = 현행인데 「낮출 자리가 있다」 고 말하던 자리** (108세션 2절).

    C6 야간 피크형은 관측 최대 **10,920.64 kW** 이고 권고 목표가 **10,921 kW**
    다. 그 권고를 받아들여 계약전력을 10,921 로 바꾸고 다시 돌리면 하한은
    **13개 월에 그대로 걸리는데** 목표가 현행 위로 올라가 ``min()`` 이 현행으로
    눌러 놓았다 — **절감액 0원인데 ``reducible`` 이 참**이라 「10,921 →
    10,921 kW 로 낮추면 그만큼 기본요금이 줄어듭니다」 가 나갔다.

    **초과 경고가 대신 막아 주지 않는다.** 이 자리는 초과 구간이 **0건**이라
    ``contract.over_limit`` 이 안 뜬다 — 곧 **우리가 권고한 값을 그대로 쓴
    고객만 이 거짓 문장을 본다.**

    **까닭 문장도 함께 본다.** 하한이 걸린 판이므로 「하한이 걸리지 않아」 는
    쓸 수 없다.
    """
    usage_path = Path(__file__).resolve().parent.parent / "input" / "cases" / "C6_야간 피크형.csv"
    if not usage_path.is_file():
        pytest.skip(f"C6 케이스 자료가 없습니다: {usage_path} (tools\\make_cases.py 실행)")
    usage = load_usage(usage_path)
    selection = TariffSelection("general_b", "high_a", "I")

    # 먼저 권고 목표를 받는다 — 현행은 관측 최대 × 1.1 이다.
    current_kw = float(usage.kw.max()) * 1.1
    options = BillingOptions(contract_kw=current_kw)
    bill = calculate_bill(usage, tariff, selection, options=options)
    first = evaluate_contract_adjustment(
        usage, bill, contract_kw=current_kw, table=tariff, options=options
    )
    assert first.reducible
    assert first.target_contract_kw == pytest.approx(10_921.0)

    # 그 목표를 계약전력으로 놓고 다시 돌린다.
    target_kw = first.target_contract_kw
    assert target_kw is not None
    again_options = BillingOptions(contract_kw=target_kw)
    again_bill = calculate_bill(usage, tariff, selection, options=again_options)
    again = evaluate_contract_adjustment(
        usage, again_bill, contract_kw=target_kw, table=tariff, options=again_options
    )

    # **하한은 그대로 걸려 있고 초과는 0건이다** — 이 판이 실제로 선다.
    assert len(again.floor_bound_months) == 13
    assert again.over_contract_slots == 0

    # **낮출 자리가 없다.** 목표를 비우고 절감액은 「없음」 이다.
    assert again.target_contract_kw is None
    assert not again.reducible
    assert again.no_saving
    assert again.saving_won == pytest.approx(0.0)

    # **까닭은 하한이 아니라 관측 최대다.**
    verdict = next(item for item in again.notices if item.fact == "contract.floor_not_binding")
    assert verdict.text == CONTRACT_AT_OBSERVED_MAX_NOTICE

    # **하향 경고도 안 나간다** — 하지도 못할 일을 조심하라는 말이 된다.
    facts = [item.fact for item in again.notices]
    assert "contract.margin" not in facts
    assert "contract.penalty" not in facts

    # **결론도 「낮추면 줄어듭니다」 를 안 쓴다.**
    from kwise.report.document import measure_entries

    entry = next(item for item in measure_entries(contract=again) if item.kind.key == "contract")
    assert "낮추면" not in entry.conclusion
    assert not entry.actionable


def test_달_단위로만_걸리는_하한이_절감액에_잡힌다(tariff: TariffTable) -> None:
    """**굴림 창이 안 찬 초기 달에만 하한이 걸리는 판** (99세션 → S102 1절).

    용인 실측(2025-08-28~2026-08-28 · 최대수요 132.28 kW)을 을 고압A 선택Ⅰ 로
    놓고 계약전력만 400 → 350 kW 로 바꾸면 총액이
    **70,754,442 → 70,573,709원**, 곧 **180,733원**이 준다. 그 몫은 전부
    기본요금이고 **2025-08·09·10·11 네 달**에서 왔다 — 그 달들은 대상월
    이력(7·8·9·12·1·2)을 아직 못 채워 굴림최대가 112.24 kW 에 머무는데
    하한 120.0 kW 가 그 위에 서기 때문이다.

    **종별 전환을 빼고 본다.** 요금표를 함께 주면 299 kW·갑Ⅱ 후보가 값을
    덮어써(8,725,941원) 이 한 자리가 안 보인다 — 여기서 박는 것은 **같은 을
    안에서 낮춰도 주는 몫**이다.

    **S102 가 `xfail(strict=True)` 로 박았고 105세션 4절에 걷었다.** 길 C 가
    서면서 사실이 뒤집혀 XPASS 로 빨개졌다 — 설계대로다. 못은 없애지 않고
    **정상으로 통과하는 자리로 옮겼다**: 그때는 「어느 길을 고를지 미리 정하지
    않는다」 며 절감액만 박았는데, 이제 길이 정해졌으므로 **목표 374 kW 도
    함께 박는다.** 375 에서 멈추면 158,141원이다 — 하한 112.5 kW 가 그 넉 달의
    112.24 kW 를 아직 넘어서다.

    **S119 에 셋을 갈아 끼웠다** (⑳ · 제7조 ①). 175,311 → **180,733**원 ·
    169,437 → **158,141**원 · 총액 70,771,509 → 70,754,442원이다. 요금적용전력이
    1kW 로 접히면서 넉 달의 기반이 움직였다 — **부호가 양쪽인 것이 이 조문의
    성질이다.** 목표 374 kW 와 「넉 달」 은 안 움직였다.

    **한 판 안의 두 문장이 안 어긋나는지도 여기서 본다** (②-13 ⓒ). 엔진이
    「4개 월에 걸렸습니다」 라 적는 판에서 판정이 「걸리지 않아 줄지 않습니다」
    라 적으면 안 된다.
    """
    usage_path = Path(__file__).resolve().parent.parent / "input" / "전기사용량_소형건물.xlsx"
    if not usage_path.is_file():
        pytest.skip(f"용인 실측 자료가 없습니다: {usage_path}")
    usage = load_usage(usage_path)
    options = BillingOptions(contract_kw=400.0)
    bill = calculate_bill(
        usage, tariff, TariffSelection("general_b", "high_a", "I"), options=options
    )
    result = evaluate_contract_adjustment(usage, bill, contract_kw=400.0)

    assert result.reducible
    assert result.target_contract_kw == pytest.approx(374.0)
    assert result.saving_won == pytest.approx(180_733.0, abs=1.0)
    # **하한은 연간으로는 진다.** 그런데도 낮출 자리가 있다 — 그 둘이 다른
    # 사실이라는 것이 ②-13 의 뿌리였다.
    assert not result.floor_binding

    # **엔진과 판정이 같은 판에서 같은 말을 한다.**
    assert len(bill.floor_bound_months) == 4
    assert any(item.fact == "tariff.floor_bound_months" for item in bill.notices)
    assert "contract.floor_not_binding" not in [item.fact for item in result.notices]

    # **375 는 마지막 한 원을 못 얻는다.** 목표가 374 여야 하는 까닭이다.
    at_375 = calculate_bill(
        usage,
        tariff,
        TariffSelection("general_b", "high_a", "I"),
        options=BillingOptions(contract_kw=375.0),
    )
    assert bill.total_won - at_375.total_won == pytest.approx(158_141.0, abs=1.0)


def test_후보가_지금_계약전력_이상이면_안_넘는다(
    floor_losing_general_b_usage: UsageData, tariff: TariffTable
) -> None:
    """**낮추는 권고만 한다** (99세션).

    계약전력이 이미 문턱 아래면 넘어갈 자리가 아니다. 못이 없으면 을 250 kW
    같은 어긋난 입력에 **299 kW 로 올리라는** 권고가 난다.
    """
    options = BillingOptions(contract_kw=250.0)
    bill = calculate_bill(
        floor_losing_general_b_usage,
        tariff,
        TariffSelection("general_b", "high_a", "I"),
        options=options,
    )
    result = evaluate_contract_adjustment(
        floor_losing_general_b_usage, bill, contract_kw=250.0, table=tariff, options=options
    )
    assert not result.crosses_type
    assert result.target_contract_kw is None


def test_방향을_모르면_실패한다() -> None:
    """**코드에 기본값을 두지 않는다** (21세션). 방향을 모른 채 한쪽으로 읽으면
    안내가 사실과 반대되는 말을 하게 된다.
    """
    assert within_type_threshold(700.0, None, None)  # 문턱이 없으면 경계도 없다
    with pytest.raises(TariffDataError, match="방향"):
        within_type_threshold(700.0, 300.0, None)
    with pytest.raises(TariffDataError, match="방향"):
        threshold_text(300.0, "over")


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
        cost=PvCostInput.of_unit_cost(PV_COST_WON_PER_KWP),
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
        cost=PvCostInput.of_unit_cost(PV_COST_WON_PER_KWP),
        steps=1,
        baseline=sample_bill,
        quality=sample_report,
    )
    point = curve.points[-1]
    assert point.power_factor_after_pct < 92.0
    assert point.power_factor_extra_won > 0  # 92% 미만이므로 추가요금이다
    assert point.saving_after_power_factor_won < point.total_saving_won
    assert any("역률 개선 설비" in message for message in texts(curve.notices))
    assert any("제41·43조" in message for message in texts(curve.notices))


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
    for sharpness in (0.85, 1.00, 1.25):
        switch_savings.add(
            evaluate_tariff_switch(sample_usage, tariff, CURRENT, quality=sample_report).saving_won
        )
        curve = solar_curve(
            sample_usage,
            tariff,
            CURRENT,
            sample_unit_pv,
            max_capacity_kwp=300.0,
            cost=PvCostInput.of_unit_cost(PV_COST_WON_PER_KWP),
            steps=1,
            sharpness=sharpness,
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
    """**50세션부터 사양은 규격 격자 위의 값이다** (3-2).

    필요 93.4 kW / 41.1 kWh 는 기성품에 없다. 살 수 있는 것은 100 kW / 50 kWh 이고
    투자비도 그 출력으로 낸다 — 더 산 만큼 회수기간이 길어지는데 그것이 정직한
    방향이다. 필요 사양은 ``required_*`` 에 그대로 남는다.
    """
    result = sample_ess
    assert result.power_kw == 100.0
    assert result.capacity_kwh == 50.0
    assert result.required_power_kw == pytest.approx(93.4, abs=0.1)
    assert result.required_capacity_kwh == pytest.approx(41.1, abs=0.5)
    # 투자비 = **출력 × kW당 단가**. 방전시간은 단가에 들어 있어 다시 곱하지 않는다.
    assert result.investment_won == pytest.approx(result.power_kw * ESS_COST_WON_PER_KW)
    assert result.total_saving_won > 0
    assert result.payback_years is not None
    assert result.certainty is Certainty.MEDIUM_LOW


def test_breakeven_unit_cost_is_reversed_from_ten_years(sample_ess: EssResult) -> None:
    """회수기간 10년이 되는 단가를 역산한다. '경제성 없음' 도 의사결정 자료가 된다."""
    result = sample_ess
    expected = result.annual_saving_won * 10.0 / result.power_kw
    assert result.breakeven_unit_cost_won_per_kw == pytest.approx(expected)
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
        cost=EssCostInput.of_unit_cost(ESS_COST_WON_PER_KW),
        charge_mask=mask,
        power_kw=50.0,
        capacity_kwh=50.0,
        baseline=sample_bill,
        quality=sample_report,
    )
    assert not result.dispatch.target_met
    assert result.dispatch.achieved_peak_kw > 5_000.0
    assert any("지키지 못한" in message for message in texts(result.notices))


# ------------------------------------------------------------------ 잉여 (태양광의 결과)
#
# **41세션에 개선안에서 뺐다.** 잉여는 태양광을 얼마나 크게 지을지에 따라 나오는
# 결과이지 따로 고르는 수단이 아니다 — 계산 모듈은 그대로 남아 태양광 카드가 부른다.


@dataclass(frozen=True, eq=False)
class SurplusCase:
    """부하보다 큰 PV. 잉여가 실제로 생기는 경우."""

    usage: UsageData
    net: NetLoad
    unit: pd.Series
    capacity_kwp: float = 1_000.0


@pytest.fixture(scope="module")
def surplus_case(tmp_path_factory: pytest.TempPathFactory) -> SurplusCase:
    path = write_month(tmp_path_factory.mktemp("surplus") / "small.csv", 2023, 7, kwh=25.0)
    usage = load_usage(path)  # 100 kW 균일 부하
    weather = clearsky_weather(start="2023-06-30", end="2023-08-01")
    config = PvSystemConfig(
        37.5, 127.0, arrays=(ArrayConfig.roof("지붕", 1_000.0),), altitude_m=50.0
    )
    unit = unit_generation_kw(usage, weather, config)
    return SurplusCase(usage=usage, net=apply_generation(usage, unit * 1_000.0), unit=unit)


def _surplus(case: SurplusCase, tariff: TariffTable, **kwargs: object) -> SurplusResult:
    """``evaluate_surplus`` 를 케이스 인자로 부른다 — 시험마다 되풀이하지 않는다."""
    return evaluate_surplus(
        case.usage,
        tariff,
        CURRENT,
        case.net.surplus_kw,
        generation_kwh=case.net.generated_kwh,
        net_usage=case.net.usage,
        capacity_kwp=kwargs.pop("capacity_kwp", case.capacity_kwp),  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


def test_surplus_is_split_from_self_consumption(surplus_case: SurplusCase) -> None:
    net = surplus_case.net
    assert net.generated_kwh > 0
    assert net.surplus_kwh > 0
    assert net.self_consumed_kwh == pytest.approx(net.generated_kwh - net.surplus_kwh)
    assert 0 < (net.self_consumption_ratio or 0) < 1
    assert float(net.usage.kw.min()) >= 0.0  # 계통 사용량은 음수가 될 수 없다


def test_surplus_scenarios(surplus_case: SurplusCase, tariff: TariffTable) -> None:
    result = _surplus(
        surplus_case, tariff, external_price_won_per_kwh=90.0, smp_price_won_per_kwh=130.0
    )
    assert result.total_kwh == pytest.approx(surplus_case.net.surplus_kwh)
    # **셋이다** (41세션 2-2 · 57세션에 「버리기」 를 「출력제어」 로).
    # 27세션은 언제나 0원인 줄이라 표에서 뺐는데, 41세션에 표가 아니라
    # **고르는 자리**가 되면서 뜻이 달라졌다 — 「아무것도 하지 않는다」 를 고를
    # 수 없으면 셋 중 하나를 강요하게 된다.
    assert [item.name for item in result.scenarios] == [
        OFFSET_SCENARIO,
        EXTERNAL_SCENARIO,
        CURTAIL_SCENARIO,
    ]
    offset = result.scenario(OFFSET_SCENARIO)
    assert offset.revenue_won is not None
    assert offset.revenue_won > 0
    # 상계 단가는 요금표에서 나온다. 경부하(92.8)~최대부하(227.8) 사이여야 한다
    settlement = result.offset
    assert settlement is not None
    assert settlement.deducted_kwh > 0
    assert 92.8 <= settlement.deducted_won / settlement.deducted_kwh <= 227.8
    external = result.scenario(EXTERNAL_SCENARIO)
    assert external.revenue_won == pytest.approx(result.total_kwh * 90.0)
    assert result.scenario(CURTAIL_SCENARIO).revenue_won == 0.0


def test_external_price_is_not_invented(surplus_case: SurplusCase, tariff: TariffTable) -> None:
    result = _surplus(surplus_case, tariff)
    external = result.scenario(EXTERNAL_SCENARIO)
    assert external.revenue_won is None
    assert not external.is_priced
    assert "지어내지 않습니다" in external.basis
    # **입력칸과 같은 이름으로 적는다** (27세션 7-2).
    assert "잉여 판매 단가" in external.basis


def test_eligibility_is_not_judged(surplus_case: SurplusCase, tariff: TariffTable) -> None:
    """자격요건은 판정하지 않는다. 금액만 제시하고 확인 필요를 명시한다."""
    result = _surplus(surplus_case, tariff)
    assert any("자격요건" in note for note in texts(result.notices))
    assert all(scenario.admin_burden for scenario in result.scenarios)


def test_offset_deducts_by_the_real_tou_band(
    tmp_path_factory: pytest.TempPathFactory, tariff: TariffTable
) -> None:
    """**낮 시간을 일괄로 중간부하에 넣지 않는다** (41세션 2-3).

    여름 15~21시는 **최대부하**이고 태양광이 그 시간에도 발전한다. 그 창의 앞쪽
    두 시간에만 발전하는 합성 자료와 09~11시(중간부하)에만 발전하는 자료를
    나란히 넣으면, 차감 실효 단가가 최대부하 쪽에서 더 높아야 한다 — 낮을
    일괄로 중간부하에 넣었다면 둘이 같은 값이 된다.

    **창을 다 덮지 않는 것이 요령이다.** 발전이 부하를 넘는 슬롯은 사용량이 0 이
    되어 차감할 자리가 없어진다 — 같은 계시의 남은 시간이 그 자리를 준다.

    토요일(최대→중간)·일요일(전량 경부하)은 요금 엔진 규칙대로 섞이므로 실효
    단가는 최대부하 단가보다 낮게 나온다. **그래도 중간부하 단가는 넘는다.**
    """
    path = write_month(tmp_path_factory.mktemp("band") / "flat.csv", 2023, 7, kwh=25.0)
    usage = load_usage(path)  # 100 kW 균일 부하
    index = pd.DatetimeIndex(usage.kw.index)
    hour = index.hour + index.minute / 60.0
    rates = tariff.rates(CURRENT)
    mid_rate = rates.rate("summer", "mid")
    peak_rate = rates.rate("summer", "peak")
    assert mid_rate < peak_rate

    def effective(window: pd.Series) -> float:
        # 구간 끝 라벨이라 15:15 슬롯이 15:00~15:15 를 뜻한다.
        generation = pd.Series(np.where(window, 150.0, 0.0), index=index, name="kw")
        net = apply_generation(usage, generation)
        assert net.surplus_kwh > 0
        result = evaluate_surplus(
            usage,
            tariff,
            CURRENT,
            net.surplus_kw,
            generation_kwh=net.generated_kwh,
            net_usage=net.usage,
            capacity_kwp=500.0,
        )
        settlement = result.offset
        assert settlement is not None
        assert settlement.deducted_kwh > 0
        return settlement.deducted_won / settlement.deducted_kwh

    peak_window = effective((hour > 15.0) & (hour <= 17.0))
    mid_window = effective((hour > 9.0) & (hour <= 11.0))
    assert peak_window > mid_window, (peak_window, mid_window)
    assert peak_window > mid_rate, (peak_window, mid_rate)
    assert mid_window <= mid_rate, (mid_window, mid_rate)


def test_offset_never_goes_negative_and_carries_in_the_same_band(
    surplus_case: SurplusCase, tariff: TariffTable
) -> None:
    """차감은 그 달 그 계시 사용량까지만. 넘으면 0 에서 멈추고 이월한다."""
    result = _surplus(surplus_case, tariff, smp_price_won_per_kwh=130.0)
    settlement = result.offset
    assert settlement is not None
    # 100 kW 균일 부하에 1,000 kWp — 다 차감할 수 없다.
    assert settlement.deducted_kwh < result.total_kwh
    assert settlement.remaining_kwh > 0
    assert settlement.deducted_kwh + settlement.remaining_kwh == pytest.approx(result.total_kwh)
    for month in settlement.months:
        assert month.deducted_kwh >= 0.0
        assert month.carried_out_kwh >= 0.0


def test_carry_is_not_shown_when_there_is_none(
    tmp_path_factory: pytest.TempPathFactory, tariff: TariffTable
) -> None:
    """**이월이 없으면 표시하지 않는다** (41세션 2-3).

    부하가 큰 건물에서는 거의 안 생긴다 — 잉여가 그 달 사용량에 다 잠기기
    때문이다. 늘 「이월 없음」 을 적으면 없는 항목이 자리를 차지한다.
    """
    path = write_month(tmp_path_factory.mktemp("nocarry") / "big.csv", 2023, 7, kwh=2_500.0)
    usage = load_usage(path)  # 10,000 kW 균일 부하
    weather = clearsky_weather(start="2023-06-30", end="2023-08-01")
    config = PvSystemConfig(
        37.5, 127.0, arrays=(ArrayConfig.roof("지붕", 1_000.0),), altitude_m=50.0
    )
    unit = unit_generation_kw(usage, weather, config)
    net = apply_generation(usage, unit * 1_000.0)
    # 부하가 커서 잉여 자체가 없다 — 그래도 이월은 빈 값이어야 한다.
    result = evaluate_surplus(
        usage,
        tariff,
        CURRENT,
        net.surplus_kw,
        generation_kwh=net.generated_kwh,
        net_usage=net.usage,
        capacity_kwp=1_000.0,
    )
    settlement = result.offset
    assert settlement is not None
    assert settlement.carried == ()
    assert settlement.remaining_kwh == pytest.approx(0.0)


def test_offset_is_dropped_above_the_cap(surplus_case: SurplusCase, tariff: TariffTable) -> None:
    """**1,000 kW 를 넘으면 상계거래가 목록에 없다** (41세션 2-3)."""
    cap = offset_max_kw()
    assert surplus_options(cap) == (OFFSET_SCENARIO, EXTERNAL_SCENARIO, CURTAIL_SCENARIO)
    assert surplus_options(cap + 1.0) == (EXTERNAL_SCENARIO, CURTAIL_SCENARIO)

    result = _surplus(surplus_case, tariff, capacity_kwp=cap + 1.0)
    assert OFFSET_SCENARIO not in [item.name for item in result.scenarios]
    with pytest.raises(KeyError):
        result.scenario(OFFSET_SCENARIO)


def test_small_systems_carry_only(surplus_case: SurplusCase, tariff: TariffTable) -> None:
    """**10 kW 이하는 이월만 되고 현금 정산이 없다** (41세션 2-3)."""
    small = offset_carry_only_max_kw()
    assert not offset_settles_cash(small)
    assert offset_settles_cash(small + 1.0)

    result = _surplus(surplus_case, tariff, capacity_kwp=small, smp_price_won_per_kwh=130.0)
    settlement = result.offset
    assert settlement is not None
    assert not settlement.settles_cash
    assert settlement.smp_won is None
    assert settlement.smp_price_won_per_kwh is None  # 넣어도 쓰지 않는다
    # 금액은 나온다 — 차감분은 확정이고 잔여만 정산이 없을 뿐이다.
    assert settlement.revenue_won == pytest.approx(settlement.deducted_won)
    assert "현금 정산이 없습니다" in result.scenario(OFFSET_SCENARIO).basis


def test_smp_price_is_not_invented(surplus_case: SurplusCase, tariff: TariffTable) -> None:
    """**미입력이면 잔여 kWh 만 내고 금액은 미산출** (41세션 2-3)."""
    result = _surplus(surplus_case, tariff)
    settlement = result.offset
    assert settlement is not None
    assert settlement.settles_cash
    assert settlement.remaining_kwh > 0
    assert settlement.smp_won is None
    assert not settlement.is_priced
    offset = result.scenario(OFFSET_SCENARIO)
    assert offset.revenue_won is None
    assert not offset.is_priced
    assert "SMP 단가 미입력" in offset.basis
    assert f"{settlement.remaining_kwh:,.0f} kWh" in offset.basis


def test_offset_says_nothing_about_when_it_settles(
    surplus_case: SurplusCase, tariff: TariffTable
) -> None:
    """**정산 시점이나 기간에 관한 단서를 달지 않는다** (41세션 2-3).

    「해당 연도 평균 SMP」·「13개월째도 같은 단가」 같은 문구를 어디에도 두지
    않는다. 단가는 하나로 적용하고 기간 길이를 구분하지 않는다.
    """
    import re

    from kwise.measures import surplus as module

    said: list[str] = [module.__doc__ or ""]
    for smp in (None, 130.0):
        result = _surplus(surplus_case, tariff, smp_price_won_per_kwh=smp)
        said.extend(item.basis for item in result.scenarios)
        said.extend(item.text for item in result.notices)
    banned = re.compile(
        r"해당\s*연도|연도\s*평균|평균\s*SMP|\d+\s*개월째|말일|정산\s*시점|정산\s*주기"
        r"|익월|매월\s*말|연\s*단위|기간\s*말\s*시점"
    )
    for line in said:
        assert not banned.search(line), line


def test_offset_does_not_touch_the_base_fee(surplus_case: SurplusCase, tariff: TariffTable) -> None:
    """**기본요금은 바뀌지 않는다** (41세션 2-3).

    잉여는 부하가 낮은 시각에 나므로 요금적용전력과 무관하고, 태양광이 피크를
    낮춘 효과는 이미 태양광 계산에 들어 있다 — 여기 금액은 전부 전력량요금이다.
    """
    from kwise.tariff import calculate_bill

    case = surplus_case
    after = calculate_bill(case.net.usage, tariff, CURRENT)
    result = _surplus(case, tariff, smp_price_won_per_kwh=130.0)
    settlement = result.offset
    assert settlement is not None
    # 차감액이 태양광 적용 후 전력량요금을 넘지 않는다 — 넘으면 기본요금까지
    # 먹은 것이다.
    assert 0 < settlement.deducted_won <= after.total_energy_won
    assert any("기본요금은 바뀌지 않습니다" in note for note in texts(result.notices))


def test_surplus_hour_distribution_is_midday(
    surplus_case: SurplusCase, tariff: TariffTable
) -> None:
    result = _surplus(surplus_case, tariff)
    distribution = result.hour_distribution
    assert int(distribution.idxmax()) in range(11, 15)
    assert distribution.loc[3] == 0.0  # 새벽에는 잉여가 없다
    assert result.share_of_generation is not None
    # **토·일·공휴일을 함께 센다** (27세션 7-1). 옛 ``weekend_share`` 는 이름에
    # 주말을 달고 토요일을 빼먹고 있었다.
    assert result.off_day_share is not None
    assert result.off_day_share == pytest.approx(
        (result.weekend_kwh + result.holiday_kwh) / result.total_kwh
    )


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


# --------------------------------------------------------------------- 7.5 태양광 단가


def test_investment_is_capacity_times_unit_cost(sample_curve: SolarCurve) -> None:
    """**투자비 = 설치 용량(kWp) × 입력단가(원/kWp).** ESS 와 같은 규약이다."""
    for point in sample_curve.points:
        assert point.investment_won == pytest.approx(point.capacity_kwp * PV_COST_WON_PER_KWP)
    assert sample_curve.is_priced


def test_total_investment_path(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
    sample_unit_pv: pd.Series,
) -> None:
    """견적서를 받았으면 총액을 그대로 쓴다. 곡선 전체에 같은 값이 붙는 것을 경고한다."""
    curve = solar_curve(
        sample_usage,
        tariff,
        CURRENT,
        sample_unit_pv,
        max_capacity_kwp=300.0,
        cost=PvCostInput.of_total(400_000_000.0),
        steps=2,
        baseline=sample_bill,
        quality=sample_report,
    )
    assert {point.investment_won for point in curve.points} == {400_000_000.0}
    assert any("같은 총액" in message for message in texts(curve.notices))


def test_missing_unit_cost_returns_a_reason_not_zero(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
    sample_unit_pv: pd.Series,
) -> None:
    """**참고단가를 만들지 않는다.** 단가가 없으면 0원이 아니라 사유다."""
    curve = solar_curve(
        sample_usage,
        tariff,
        CURRENT,
        sample_unit_pv,
        max_capacity_kwp=300.0,
        steps=2,
        baseline=sample_bill,
        quality=sample_report,
    )
    assert not curve.is_priced
    assert all(point.investment_won is None for point in curve.points)
    assert all(point.payback_years is None for point in curve.points)
    assert curve.best_payback is None
    # 절감액은 유효하다 — 단가를 몰라도 요금 계산은 확정된다.
    assert curve.points[-1].total_saving_won > 0
    assert any("참고값은 제공하지 않습니다" in note for note in texts(curve.notices))
    assert PvCostInput.unpriced().reason == PV_UNPRICED_REASON


def test_cost_basis_and_scale_economy_are_stated(sample_curve: SolarCurve) -> None:
    """kWp 가 DC 정격임을, 그리고 규모의 경제를 반영하지 않았음을 밝힌다."""
    notes = "\n".join(texts(sample_curve.notices))
    assert "모듈 직류(DC) 정격" in notes
    assert "인버터 용량(kW-ac)과 다릅니다" in notes
    assert "부대비용" in notes
    assert "규모의 경제는 반영하지 않았습니다" in notes


def test_investment_is_linear_in_capacity(sample_curve: SolarCurve) -> None:
    """단일 단가 가정이므로 투자비는 용량에 **정확히 선형**이다.

    이 선형성이 곧 규모의 경제 미반영의 정체다. 주석과 짝을 이룬다.
    """
    priced = [
        point
        for point in sample_curve.points
        if point.investment_won is not None and point.capacity_kwp > 0
    ]
    ratios = {round((point.investment_won or 0.0) / point.capacity_kwp, 6) for point in priced}
    assert len(ratios) == 1


def test_both_cost_paths_cannot_be_given() -> None:
    with pytest.raises(ValueError, match="함께 줄 수 없습니다"):
        PvCostInput(unit_cost_won_per_kwp=1_000_000.0, total_won=5_000_000.0)


# ===================================================================== 용량 판정 (16세션 0-4)


def _point(capacity: float, *, saving: float, investment: float, payback: float) -> SolarPoint:
    """판정만 시험하는 최소 점. 시계열은 필요 없다."""
    return SolarPoint(
        capacity_kwp=capacity,
        generation_kwh=capacity * 1_200.0,
        self_consumed_kwh=capacity * 1_200.0,
        surplus_kwh=0.0,
        self_consumption_ratio=1.0,
        billing_demand_kw=5_000.0,
        base_saving_won=saving * 0.2,
        energy_saving_won=saving * 0.8,
        total_saving_won=saving,
        annual_saving_won=saving,
        investment_won=investment,
        payback_years=payback,
        power_factor_after_pct=92.0,
        power_factor_extra_won=0.0,
    )


def _curve(points: tuple[SolarPoint, ...]) -> SolarCurve:
    return SolarCurve(
        points=points,
        selection=TariffSelection("general_b", "high_a", "I"),
        baseline_total_won=0.0,
        baseline_base_won=0.0,
        baseline_energy_won=0.0,
        sharpness=1.0,
        max_capacity_kwp=points[-1].capacity_kwp,
        cost=PvCostInput(unit_cost_won_per_kwp=1_500_000.0),
        base_fee_months=12.0,
    )


def test_평평한_회수기간에서는_상한을_고른다() -> None:
    """**첫 단계를 최적이라 답하던 자리다** (16세션 0-4).

    kWp 당 단가면 투자비와 절감액이 함께 비례해 회수기간이 거의 같아진다.
    실측에서 8 kWp 8.060년 · 160 kWp 8.114년이었고, 최소점을 그대로 고르니
    **상한의 1/20** 이 최적으로 나왔다.
    """
    points = tuple(
        _point(
            capacity,
            saving=1_488_896.0 * capacity / 8.0,
            investment=1_500_000.0 * capacity,
            payback=8.060 + 0.054 * (capacity - 8.0) / 152.0,
        )
        for capacity in (8.0, 40.0, 80.0, 120.0, 160.0)
    )
    verdict = _curve(points).verdict()
    assert verdict.basis == "회수기간"
    assert verdict.best is not None
    assert verdict.best.capacity_kwp == 160.0
    assert verdict.at_limit
    assert not verdict.show_curve


def test_회수기간이_실제로_꺾이면_최소점을_고른다() -> None:
    """잉여가 생기면 회수기간이 동률 폭을 벗어난다 — U곡선 판정은 그대로다."""
    paybacks = {8.0: 9.0, 40.0: 7.5, 80.0: 7.0, 120.0: 9.5, 160.0: 14.0}
    points = tuple(
        _point(
            capacity,
            saving=1_500_000.0 * capacity / paybacks[capacity] / 10.0,
            investment=1_500_000.0 * capacity,
            payback=paybacks[capacity],
        )
        for capacity in paybacks
    )
    verdict = _curve(points).verdict()
    assert verdict.best is not None
    assert verdict.best.capacity_kwp == 80.0
    assert not verdict.at_limit
    assert verdict.show_curve


def test_동률_폭은_기준_데이터에서_온다() -> None:
    """**코드에 기본값을 두지 않는다** (요구사항서 12장)."""
    from kwise.measures.solar import payback_tie_ratio

    assert 0.0 < payback_tie_ratio() < 0.5


# ===================================================================== 48세션 · 잉여 합산
#
# **41세션이 자리만 옮기고 금액을 합치지 않았다.** 잉여 활용(7.7)을 개선안에서
# 빼고 태양광 카드 안으로 넣었는데, 태양광의 절감액·회수기간은 자가소비분만
# 보고 있었다 — 화면에 **더해지지 않는 두 수**가 남았다.
#
# 14세션의 「경제성DR·잉여 활용은 합산효과에 넣지 않는다」 는 잉여가 **독립
# 개선안**이던 시절의 결정이다. 41세션에 전제가 사라졌다. **차익거래는 계속
# 뺀다** — 그쪽은 「그날 피크에 쓸 몫을 남기는 운전 규칙이 없다」 가 살아 있다.


def test_잉여_수익은_고른_경우에만_더한다(surplus_case: SurplusCase, tariff: TariffTable) -> None:
    """**기본값 없음을 지킨다** (41세션 2-2). 안 고르면 아무것도 더하지 않는다."""
    point = solar_point(
        surplus_case.usage,
        tariff,
        CURRENT,
        surplus_case.unit,
        surplus_case.capacity_kwp,
        cost=PvCostInput.of_unit_cost(2_000_000.0),
    )
    assert point.surplus_scenario == ""
    assert point.surplus_revenue_won == 0.0
    # 고르지 않은 상태 — 시나리오 이름이 비면 그대로 돌려준다.
    untouched = with_surplus_revenue(point, revenue_won=1_000.0, scenario="", base_fee_months=1.0)
    assert untouched is point


def test_잉여_수익이_절감액과_회수기간에_실린다(
    surplus_case: SurplusCase, tariff: TariffTable
) -> None:
    """**두 수가 하나가 되어야 한다** (48세션).

    소형 사무빌딩 자료에서 절감액 2,543만원과 잉여 수익 241만원이 따로 놀았고,
    회수기간 12.6년은 앞의 것만 본 값이었다. 더하면 11.5년이다.
    """
    point = solar_point(
        surplus_case.usage,
        tariff,
        CURRENT,
        surplus_case.unit,
        surplus_case.capacity_kwp,
        cost=PvCostInput.of_unit_cost(2_000_000.0),
    )
    assert point.payback_years is not None
    combined = with_surplus_revenue(
        point, revenue_won=1_000_000.0, scenario=OFFSET_SCENARIO, base_fee_months=1.0
    )
    assert combined.surplus_scenario == OFFSET_SCENARIO
    assert combined.surplus_revenue_won == 1_000_000.0
    assert combined.total_saving_won == pytest.approx(point.total_saving_won + 1_000_000.0)
    # **자가소비분은 그대로 꺼낼 수 있다** — 툴팁이 이 값으로 가른다.
    assert combined.self_consumption_saving_won == pytest.approx(point.total_saving_won)
    # 더한 만큼 회수기간이 짧아진다.
    assert combined.payback_years is not None
    assert combined.payback_years < point.payback_years
    assert combined.investment_won == point.investment_won


def test_금액을_못_내면_더하지_않는다(surplus_case: SurplusCase, tariff: TariffTable) -> None:
    """단가를 넣지 않은 외부 판매다. **지어낸 0원을 절감액에 넣지 않는다.**"""
    point = solar_point(
        surplus_case.usage,
        tariff,
        CURRENT,
        surplus_case.unit,
        surplus_case.capacity_kwp,
        cost=PvCostInput.of_unit_cost(2_000_000.0),
    )
    combined = with_surplus_revenue(
        point, revenue_won=None, scenario=EXTERNAL_SCENARIO, base_fee_months=1.0
    )
    assert combined.surplus_revenue_won == 0.0
    assert combined.total_saving_won == point.total_saving_won
    # 이름은 남는다 — 「고르지 않음」 과 「골랐지만 금액을 못 냄」 은 다르다.
    assert combined.surplus_scenario == EXTERNAL_SCENARIO


def test_합산효과에_잉여를_더한다(surplus_case: SurplusCase, tariff: TariffTable) -> None:
    """**14세션의 결정을 뒤집는다** (48세션). 전제가 41세션에 사라졌다."""
    baseline = calculate_bill(surplus_case.usage, tariff, CURRENT)
    spec = CombinationSpec(
        name="태양광",
        selection=CURRENT,
        pv_capacity_kwp=surplus_case.capacity_kwp,
        pv_unit_cost_won_per_kwp=2_000_000.0,
    )
    plain = evaluate_combination(
        surplus_case.usage,
        tariff,
        spec,
        baseline_bill=baseline,
        unit_pv_kw_per_kwp=surplus_case.unit,
    )
    with_revenue = evaluate_combination(
        surplus_case.usage,
        tariff,
        replace(spec, surplus_revenue_won=1_000_000.0, surplus_scenario=OFFSET_SCENARIO),
        baseline_bill=baseline,
        unit_pv_kw_per_kwp=surplus_case.unit,
    )
    assert plain.surplus_revenue_won == 0.0
    assert with_revenue.surplus_revenue_won == 1_000_000.0
    assert with_revenue.saving_won == pytest.approx(plain.saving_won + 1_000_000.0)
    # **표의 「요금」 과 「절감액」 이 기준선으로 되돌아가야 한다.**
    assert baseline.total_won - with_revenue.total_won == pytest.approx(with_revenue.saving_won)
    facts = {notice.fact for notice in with_revenue.notices}
    assert "combination.surplus_revenue" in facts


def test_태양광이_없으면_잉여도_더하지_않는다(
    surplus_case: SurplusCase, tariff: TariffTable
) -> None:
    """**잉여는 태양광의 결과다** (41세션 2절). 켜지 않은 수단의 수익은 없다."""
    baseline = calculate_bill(surplus_case.usage, tariff, CURRENT)
    result = evaluate_combination(
        surplus_case.usage,
        tariff,
        CombinationSpec(
            name="현행",
            selection=CURRENT,
            surplus_revenue_won=1_000_000.0,
            surplus_scenario=OFFSET_SCENARIO,
        ),
        baseline_bill=baseline,
    )
    assert result.surplus_revenue_won == 0.0
    assert result.saving_won == pytest.approx(0.0)


# ===================================================================== 58세션 · 잉여 단가 기본값


def test_잉여_단가_기본값이_기준_데이터에서_온다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**코드에 박지 않는다** (58세션 1-1).

    박아 두면 기준 데이터 화면에서 고칠 수 없고, 「이 숫자를 누가 정했나」 를
    되물을 곳이 사라진다. 파일을 갈아 끼우면 함수가 그 값을 낸다.
    """
    import json
    import shutil

    from kwise.measures import (
        default_external_price_won_per_kwh,
        default_smp_price_won_per_kwh,
    )
    from kwise.rules import reload_rules

    assert default_external_price_won_per_kwh() == 140.0
    assert default_smp_price_won_per_kwh() == 120.0

    data = Path("data")
    root = tmp_path / "data"
    (root / "defaults").mkdir(parents=True)
    for name in ("rules_kr.json", "assumptions.json"):
        shutil.copy2(data / name, root / name)
        shutil.copy2(data / "defaults" / name, root / "defaults" / name)
    payload = json.loads((root / "assumptions.json").read_text(encoding="utf-8"))
    payload["items"]["surplus.external.price_won_per_kwh"]["value"] = 111.0
    payload["items"]["surplus.offset.smp_price_won_per_kwh"]["value"] = 99.0
    (root / "assumptions.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    monkeypatch.setenv("KWISE_TARIFF_DIR", str(root))
    reload_rules()
    try:
        assert default_external_price_won_per_kwh() == 111.0
        assert default_smp_price_won_per_kwh() == 99.0
    finally:
        reload_rules()


def test_잉여_단가가_출고층에도_있다() -> None:
    r"""**출고 복원이 앱을 죽이면 안 된다** (57세션 3절이 겪은 일).

    판단값 11건이 ``data\defaults`` 에 없어 「출고값 복원」 을 누르면 항목이
    사라졌다. 새로 더한 단가 둘도 양쪽에 있어야 한다.
    """
    from kwise.rules import RuleOrigin, assumptions, load_defaults

    keys = ("surplus.external.price_won_per_kwh", "surplus.offset.smp_price_won_per_kwh")
    current = assumptions()
    factory = load_defaults(RuleOrigin.JUDGEMENT)
    for key in keys:
        assert key in current.item_keys(), key
        assert key in factory.item_keys(), f"출고층에 없습니다: {key}"
        # **판단값이다.** 법령 유래가 아니므로 rules_kr.json 에 두지 않는다.
        assert current[key].source == "판단값"
        assert current[key].value == factory[key].value


def test_적용_단가_문구가_실제_값을_읽는다() -> None:
    """**숫자를 박지 않는다** (58세션 2절). 고친 단가가 그대로 적힌다."""
    from kwise.measures import APPLIED_PRICE_TAIL, applied_price_note

    text = applied_price_note(smp_price_won_per_kwh=120.0, external_price_won_per_kwh=140.0)
    assert "상계거래 SMP 120원/kWh" in text
    assert "외부 판매 140원/kWh" in text
    assert text.endswith(APPLIED_PRICE_TAIL)
    # 고치면 따라온다.
    assert "외부 판매 155원/kWh" in applied_price_note(external_price_won_per_kwh=155.0)
    # **적용하지 않은 단가는 문장에서 빠진다.**
    assert "상계거래" not in applied_price_note(external_price_won_per_kwh=140.0)
    assert applied_price_note() == ""


def test_적용_단가_문구가_결과에서_나온다(surplus_case: SurplusCase, tariff: TariffTable) -> None:
    """**네 산출물이 한 곳에서 읽는다** (58세션 2절)."""
    result = _surplus(
        surplus_case, tariff, external_price_won_per_kwh=140.0, smp_price_won_per_kwh=120.0
    )
    note = result.applied_price_note
    assert "상계거래 SMP 120원/kWh" in note
    assert "외부 판매 140원/kWh" in note
    assert note in texts(result.notices), "안내로도 나가야 Word·화면이 같은 문장을 쓴다."

    # **비우면 그 단가가 문장에서 빠지고 금액이 미산출로 돌아간다.**
    empty = _surplus(surplus_case, tariff)
    assert empty.scenario(EXTERNAL_SCENARIO).revenue_won is None
    assert "외부 판매" not in empty.applied_price_note


def test_잉여가_0이면_단가를_넣어도_문구가_없다(
    tmp_path_factory: pytest.TempPathFactory, tariff: TariffTable
) -> None:
    """**잉여가 없으면 절 자체가 없다** (58세션 3-4). 단가는 그것을 바꾸지 않는다."""
    path = write_month(tmp_path_factory.mktemp("nosurplus") / "big.csv", 2023, 7, kwh=2_500.0)
    usage = load_usage(path)
    weather = clearsky_weather(start="2023-06-30", end="2023-08-01")
    config = PvSystemConfig(
        37.5, 127.0, arrays=(ArrayConfig.roof("지붕", 1_000.0),), altitude_m=50.0
    )
    net = apply_generation(usage, unit_generation_kw(usage, weather, config) * 1_000.0)
    result = evaluate_surplus(
        usage,
        tariff,
        CURRENT,
        net.surplus_kw,
        generation_kwh=net.generated_kwh,
        net_usage=net.usage,
        capacity_kwp=1_000.0,
        external_price_won_per_kwh=140.0,
        smp_price_won_per_kwh=120.0,
    )
    assert result.total_kwh == pytest.approx(0.0)
    assert result.applied_price_note == ""
    assert not [item for item in result.notices if "산출했습니다" in item.text]


# ------------------------------------------------- 조건이 바뀌면 선택요금을 다시 (⑲)


@pytest.fixture(scope="module")
def flipping_night_usage(tmp_path_factory: pytest.TempPathFactory) -> UsageData:
    """**계약전력을 낮추면 선택요금 순위가 뒤집히는** 야간 피크형 (S112 1절).

    관측 최대 4,000 kW 인데 요금적용전력 산정 대상 수요는 그 훨씬 아래다 —
    경부하 시간대가 빠지기 때문이다. 계약전력을 관측 최대의 3배로 잡으면
    하한(30%)이 모든 달을 끌어올리고, 목표까지 낮추면 하한이 함께 내려가
    **기본요금 단가가 다른 후보들이 서로 다른 폭으로 싸진다.**
    """
    path = night_peak_month(
        tmp_path_factory.mktemp("flip19") / "night.csv",
        night_kwh=1_000.0,
        midday_kwh=150.0,
        other_kwh=30.0,
    )
    return load_usage(path)


FLIP19_SELECTION = TariffSelection("general_b", "high_b", "II")
FLIP19_CONTRACT_KW = 12_000.0


def _flip19_totals(
    usage: UsageData, tariff: TariffTable, contract_kw: float
) -> list[tuple[TariffSelection, float]]:
    """계약전력 하나에서 후보별 총액. 싼 순이다."""
    from kwise.tariff import switchable_selections

    options = BillingOptions(contract_kw=contract_kw)
    quotes = [
        (item, calculate_bill(usage, tariff, item, options=options).total_won)
        for item in switchable_selections(tariff, FLIP19_SELECTION)
    ]
    return sorted(quotes, key=lambda pair: pair[1])


def test_계약전력을_낮추면_선택요금_순위가_실제로_뒤집힌다(
    flipping_night_usage: UsageData, tariff: TariffTable
) -> None:
    """**못을 박기 전에 자료부터 확인한다** (S112 1절 ㄹ).

    아래 ``xfail`` 못이 「뜨지 않는 갈래」 가 아님을 이 시험이 지킨다 —
    자료가 바뀌어 뒤집힘이 사라지면 못보다 **이쪽이 먼저 깨진다.**
    """
    usage, table = flipping_night_usage, tariff
    adjustment = evaluate_contract_adjustment(
        usage,
        calculate_bill(
            usage,
            table,
            FLIP19_SELECTION,
            options=BillingOptions(contract_kw=FLIP19_CONTRACT_KW),
        ),
        contract_kw=FLIP19_CONTRACT_KW,
        table=table,
        options=BillingOptions(contract_kw=FLIP19_CONTRACT_KW),
    )
    target = adjustment.target_contract_kw
    assert target == pytest.approx(4_000.0)

    now = _flip19_totals(usage, table, FLIP19_CONTRACT_KW)
    after = _flip19_totals(usage, table, target)
    assert now[0][0].option == "II"
    assert after[0][0].option == "III"

    # 2단계가 권한 Ⅱ 를 목표에서 그대로 들면 이만큼 더 낸다.
    carried = dict(after)[now[0][0]] - after[0][1]
    assert carried == pytest.approx(1_340_816.0, abs=1.0)


def test_계약전력_조정이_목표에서_선택요금을_다시_고른다(
    flipping_night_usage: UsageData, tariff: TariffTable
) -> None:
    """**목표 계약전력에서 최적인 선택요금을 함께 권한다** (⑲ · S112 2절).

    **S112 1절이 ``xfail(strict)`` 로 박았고 2절에 XPASS 로 깨져 걷었다.**
    앞서는 현행 계약전력으로만 총액을 내 Ⅱ 를 권한 채 끝났다 — 목표까지
    낮추면 Ⅲ 이 1,340,816 원 싼데 그 사실이 카드에 없었다.
    """
    usage, table = flipping_night_usage, tariff
    options = BillingOptions(contract_kw=FLIP19_CONTRACT_KW)
    adjustment = evaluate_contract_adjustment(
        usage,
        calculate_bill(usage, table, FLIP19_SELECTION, options=options),
        contract_kw=FLIP19_CONTRACT_KW,
        table=table,
        options=options,
    )
    assert adjustment.retuned_selection is not None
    assert adjustment.retuned_selection.option == "III"
    assert adjustment.retuned_saving_won == pytest.approx(1_340_816.0, abs=1.0)

    # **두 몫이 갈라져 있다** (2절 ㄹ). 계약전력만 낮춘 몫은 기본요금 차이고,
    # 다시 고른 몫은 그 위에 얹힌다 — 합쳐 두면 「계약전력을 낮춰서 얻은 돈」
    # 을 잘못 읽는다.
    assert adjustment.saving_won is not None
    assert adjustment.saving_won != pytest.approx(adjustment.retuned_saving_won)

    # **화면이 그 사실을 말한다** (S112 5절). 새 안내를 붙이지 않고 산출 근거
    # 줄을 늘렸다 — 안내 항목이 늘면 화면 예산을 먹는데, 이것은 절감액이
    # 무엇을 잰 값인가를 말하는 것이라 근거 줄이 제자리다.
    reason = next(item.text for item in adjustment.notices if item.fact == "contract.saving_basis")
    assert "계약전력만 낮춘 몫" in reason
    assert "선택Ⅲ 쪽이" in reason
    # **표시 금액은 천 원 절사다** — `kwise.money.won` 을 거친다.
    assert "1,340,000원 더 유리" in reason


def test_현행이_이미_최적이면_다시_고른_것이_없다(
    sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable
) -> None:
    """**바뀌지 않으면 비운다** (S112 2절). 「바꿨다」 를 말할 자리가 아니다."""
    options = BillingOptions(contract_kw=7_000.0)
    adjustment = evaluate_contract_adjustment(
        sample_usage,
        calculate_bill(sample_usage, tariff, CURRENT, options=options, quality=sample_report),
        contract_kw=7_000.0,
        table=tariff,
        options=options,
    )
    assert adjustment.retuned_selection is None
    assert adjustment.retuned_saving_won is None


def test_다시_고른_것이_선택요금_전환_카드로_새지_않는다(
    flipping_night_usage: UsageData, tariff: TariffTable
) -> None:
    """**2단계 카드는 서로 독립 평가다** (`project-overview.md` 1절 · S112 2절 ㄴ).

    계약전력 조정 카드가 목표에서 Ⅲ 을 다시 골라도, 선택요금 전환 카드는
    **현행 계약전력**에서 Ⅱ 를 권한다. 두 카드가 다른 답을 내는 것이 정상이다 —
    계약전력 하향은 되돌리기 어려운 별개 결정이라(위약금) 그것을 전제로 요금제를
    권하면 두 결정이 엉킨다. 계약전력을 낮추면 요금제도 바뀐다는 사실은
    **계약전력 조정 카드 안에서** 말한다.
    """
    usage, table = flipping_night_usage, tariff
    options = BillingOptions(contract_kw=FLIP19_CONTRACT_KW)
    adjustment = evaluate_contract_adjustment(
        usage,
        calculate_bill(usage, table, FLIP19_SELECTION, options=options),
        contract_kw=FLIP19_CONTRACT_KW,
        table=table,
        options=options,
    )
    switch = evaluate_tariff_switch(usage, table, FLIP19_SELECTION, options=options)

    assert adjustment.retuned_selection is not None
    assert adjustment.retuned_selection.option == "III"
    # 선택요금 전환 카드는 현행 계약전력이 전제다 — 거기서는 Ⅱ 가 최적이다.
    assert switch.best.selection == FLIP19_SELECTION
    assert not switch.switch_needed
    assert switch.best.selection != adjustment.retuned_selection


def test_요금표를_안_주면_다시_고르지_않는다(
    flipping_night_usage: UsageData, tariff: TariffTable
) -> None:
    """**요금표가 있어야 후보를 안다.** 없으면 조용히 지금 종별 안에서만 본다."""
    usage, table = flipping_night_usage, tariff
    options = BillingOptions(contract_kw=FLIP19_CONTRACT_KW)
    adjustment = evaluate_contract_adjustment(
        usage,
        calculate_bill(usage, table, FLIP19_SELECTION, options=options),
        contract_kw=FLIP19_CONTRACT_KW,
    )
    assert adjustment.target_contract_kw == pytest.approx(4_000.0)
    assert adjustment.retuned_selection is None
