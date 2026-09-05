"""진단 (요구사항서 6장, 부록 B).

진단은 **설비 정보 없이** 나와야 한다. PV 를 넣지 않아도 태양광 검토 신호까지
나오는 것이 이 모듈의 요점이다.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pandas as pd
import pytest

from kwise.diagnose import (
    ContractAdequacy,
    ContractInfo,
    Diagnosis,
    PvPotential,
    assess_contract,
    diagnose,
    judge_pv_potential,
    peak_profile,
)
from kwise.io import UsageData, load_usage
from kwise.measures.contract import evaluate_contract_adjustment
from kwise.notices import texts
from kwise.quality import QualityReport
from kwise.tariff import (
    BillingOptions,
    BillingResult,
    TariffSelection,
    TariffTable,
    build_calendar,
    classify_slots,
    demand_eligible_mask,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
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
    """8월 최대수요는 5,288 kW 지만 요금적용전력은 7월의 5,293 kW 다.

    **5,293.44 가 아니라 5,293 이다** (S118 ⑳). 제7조 ① 이 요금적용전력의
    계산단위를 1kW 로 못 박았다.
    """
    monthly = sample_diagnosis.peak.monthly
    august = monthly.loc[pd.Period("2023-08", freq="M")]
    assert august["max_demand_kw"] < august["billing_demand_kw"]
    assert august["billing_demand_kw"] == pytest.approx(5_293.0)
    assert sample_diagnosis.peak.billing_demand_kw == pytest.approx(5_293.0)


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
        "operating_hours",
        "contract_floor_ratio",
        # 사용자가 지목한 「쉬는 날」 (29세션). 설비가 아니라 달력 보정이고
        # **DR 판정에만** 쓴다 — 요금 계산의 공휴일은 법정 공휴일 그대로다.
        "dr_off_days",
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
    assert result.peak.billing_demand_kw == pytest.approx(5_293.0)
    assert result.summary.pv_potential is PvPotential.HIGH
    assert result.summary.tariff_switch_saving_won is None
    assert any("계약 정보가 없어" in message for message in texts(result.notices))
    assert len(result.summary.lines) == 3


def test_diagnose_warns_about_a_pending_option(tmp_path: Path, tariff: TariffTable) -> None:
    """**1단계 산출물에도 같은 안내가 선다** — 고객이 현행으로 고른 경우다.

    안내를 내는 자리는 둘이다(진단과 선택요금 전환). 한쪽만 붙이면 계약 정보만
    확정하고 2단계로 안 간 사람이 오차를 모른 채 금액을 읽는다.
    """
    from tests._synthetic import write_month

    usage = load_usage(write_month(tmp_path / "2026-03.csv", 2026, 3, kwh=25.0))
    result = diagnose(
        usage,
        tariff,
        ContractInfo(TariffSelection("general_a_2", "high_a", "III"), contract_kw=200.0),
    )
    pending = [item for item in texts(result.notices) if "2026년 12월분 요금부터" in item]
    assert len(pending) == 1


def test_diagnose_is_silent_once_the_period_starts_after_the_option(
    tmp_path: Path, tariff: TariffTable
) -> None:
    """**1단계의 안 뜨는 판** — 기간 전체가 시행 뒤면 오차가 없다.

    안 뜨는 갈래는 `pending_option_notices` 안에 있고 2단계 시험 둘이 이미
    본다. 여기서 보는 것은 그 갈래가 아니라 **1단계가 넘기는
    ``period_start``** 다 — 잘못 넘기면 서는 판은 그대로 통과하고 이 판만
    빨개진다.
    """
    from tests._synthetic import write_month

    usage = load_usage(write_month(tmp_path / "2027-03.csv", 2027, 3, kwh=25.0))
    result = diagnose(
        usage,
        tariff,
        ContractInfo(TariffSelection("general_a_2", "high_a", "III"), contract_kw=200.0),
    )
    assert not [item for item in texts(result.notices) if "요금부터 쓸 수 있습니다" in item]


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


def test_산출물이_세는_기본요금_비중은_역률이_붙어도_백을_채운다(
    sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable
) -> None:
    """**분자와 분모의 짝을 맞춘다** (S124 · ②-40).

    위 못은 **역률요금이 0원인 간주 92% 에서만** 참이었다. ``base_share`` 는
    역률요금을 뺀 분자를 담은 분모로 나누므로, 85% 를 걸면 전력량요금 비중과
    합해도 100% 가 안 된다 — 그 둘로 세운 Word 표는 기본 + 전력량이 합계보다
    모자란다. 산출물은 :attr:`base_with_power_factor_share` 를 센다.
    """
    options = BillingOptions(power_factor_pct=85.0, contract_kw=5_500.0)
    structure = diagnose(
        sample_usage,
        tariff,
        ContractInfo(CURRENT, contract_kw=5_500.0),
        quality=sample_report,
        options=options,
    ).structure
    assert structure is not None
    assert structure.bill.total_power_factor_won > 0, "역률요금이 서는 벌이어야 한다"

    # 낡은 몫은 100% 를 못 채운다 — 이 못이 잡는 것이 그 자리다.
    assert structure.base_share + structure.energy_share < 0.999
    assert structure.base_with_power_factor_share + structure.energy_share == pytest.approx(1.0)
    # 표의 금액도 같은 말을 한다 — 기본 + 전력량 = 합계.
    assert structure.base_with_power_factor_won + structure.energy_won == pytest.approx(
        structure.total_won
    )


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
    """계약 5,500 kW 에 최대수요 5,293 kW — 하한 1,650 kW 는 걸리지 않는다."""
    adequacy = sample_diagnosis.contract
    assert adequacy is not None
    assert adequacy.utilization == pytest.approx(0.962, abs=0.001)
    assert adequacy.headroom_kw == pytest.approx(206.56, abs=0.1)
    assert adequacy.floor_kw == pytest.approx(1_650.0)
    assert adequacy.target_contract_kw is None
    assert not adequacy.floor_binding


def _adequacy(
    usage: UsageData,
    bill: BillingResult,
    *,
    contract_kw: float,
    billing_demand_kw: float,
    contract_floor_ratio: float | None = None,
    table: TariffTable | None = None,
    options: BillingOptions | None = None,
) -> ContractAdequacy:
    """1단계 적정성 — **판정은 조정 쪽에서 온다** (100세션).

    ``diagnose()`` 가 하는 것과 같은 순서다. 옛 판은 여기서 하한을 따로 세었고,
    그래서 2단계가 「299 kW 로 낮춰라」 하는 판에서 1단계가 「적정합니다」 라고
    적었다.
    """
    adjustment = evaluate_contract_adjustment(
        usage,
        bill,
        contract_kw=contract_kw,
        contract_floor_ratio=contract_floor_ratio,
        table=table,
        options=options,
    )
    return assess_contract(adjustment, billing_demand_kw=billing_demand_kw)


def test_하한_비율을_모르면_1단계도_미산출이다(
    sample_usage: UsageData, tariff: TariffTable, sample_report: QualityReport
) -> None:
    """**하한 비율을 모르면 목표도 금액도 없다** (83세션).

    옛 판은 여유율을 얹은 「권장 계약전력」 을 비율 없이도 냈다 — 근거가 없는
    수였다. 목표는 하한비율에서만 나온다. 1단계는 그 사실을 **차단 안내**로 낸다.
    """
    import copy
    import json

    from kwise.tariff import calculate_bill, default_tariff_dir, parse_tariff

    with (default_tariff_dir() / "tariff_kr_20260601.json").open(encoding="utf-8") as stream:
        payload = copy.deepcopy(json.load(stream))
    payload["contract_types"]["general_b"]["contract_floor_ratio"] = None
    unknown_table = parse_tariff(payload)
    bill = calculate_bill(sample_usage, unknown_table, CURRENT, quality=sample_report)

    adequacy = _adequacy(sample_usage, bill, contract_kw=7_000.0, billing_demand_kw=5_293.44)
    assert adequacy.floor_kw is None
    assert adequacy.target_contract_kw is None
    assert adequacy.saving_won is None  # 하한 비율이 없으면 금액을 만들지 않는다
    assert "미산출" in adequacy.saving_basis
    assert "contract.floor_unknown" in {item.fact for item in adequacy.notices}


def test_1단계_적정성이_2단계_조정과_같은_말을_한다(
    sample_usage: UsageData, sample_bill: BillingResult, tariff: TariffTable
) -> None:
    """**한 산출물 안에서 두 장이 반대로 말했다** (100세션).

    1단계는 하한 한 줄만 보고 있었고 2단계는 98·99세션에 종별 전환을 들였다.
    그래서 소형 을 300 kW 에서 2단계가 「299 kW 로 낮춰라 · 2,181만원」 할 때
    1단계는 **「적정합니다 · 없음」** 이라고 적었다. 이제 판정이 한 자리다.

    **갈래 넷을 다 지난다** — 하한이 이긴다 · 하한은 지지만 종별을 넘는다 ·
    낮출 자리가 없다 · 초과 위약.
    """
    options = BillingOptions(contract_kw=6_000.0)
    for contract_kw in (20_000.0, 6_000.0, 5_000.0):
        adjustment = evaluate_contract_adjustment(
            sample_usage,
            sample_bill,
            contract_kw=contract_kw,
            table=tariff,
            options=options,
        )
        adequacy = assess_contract(adjustment, billing_demand_kw=5_293.44)
        assert adequacy.target_contract_kw == adjustment.target_contract_kw, contract_kw
        assert adequacy.saving_won == adjustment.saving_won, contract_kw
        assert adequacy.floor_binding is adjustment.floor_binding, contract_kw
        assert adequacy.reducible is adjustment.reducible, contract_kw
        assert adequacy.crossed_label == adjustment.crossed_label, contract_kw


def test_용인_계약_700이면_하한이_이기면서_종별도_넘는다(tariff: TariffTable) -> None:
    """**하한이 이기는 갈래가 뜨는 시험 자료가 하나도 없었다** (83세션 14).

    시험 자료 셋(용인 290 · 대형 6,000 · 소형 300)이 전부 하한이 지는 쪽이라,
    만든 갈래가 실물에 한 번도 서지 않았다 — **뜨지 않는 갈래는 없는 갈래와
    같다.** 여기 박는 수는 **실제로 있었던 상황**이다: 용인 건물은 계약전력
    700 kW 였고 290 으로 내리면서 77.7 kW 어치가 사라졌다.

        하한 700 × 30% = 210 kW  >  최대수요 132.3 kW  → 하한이 기준
        같은 을 안의 목표 132.3 ÷ 0.3 = 441 kW

    **441 은 이 벌의 답이 아니다** (100세션). 441 은 문턱 300 kW 위라 을에
    머무는 수이고, 299 kW 로 더 내리면 갑Ⅱ 로 넘어가 요금 전체가 준다.
    98·99세션이 2단계에 세운 그 사실을 **1단계는 몰라 441 에서 멈춰 있었다.**
    """
    from kwise.tariff import calculate_bill

    usage = load_usage(PROJECT_ROOT / "input" / "전기사용량_소형건물.xlsx")
    options = BillingOptions(contract_kw=700.0)
    bill = calculate_bill(usage, tariff, CURRENT, options=options)
    adequacy = _adequacy(
        usage,
        bill,
        contract_kw=700.0,
        billing_demand_kw=132.28,
        table=tariff,
        options=options,
    )
    assert adequacy.floor_binding  # 하한 210 kW 가 최대수요 132.3 kW 를 넘는다
    assert adequacy.floor_kw == pytest.approx(210.0)
    assert adequacy.reducible
    assert adequacy.target_contract_kw == 299.0  # 441 이 아니다 — 문턱 바로 아래다
    assert adequacy.crossed_label == "일반용전력(갑)Ⅱ"
    assert adequacy.over_contract_slots == 0

    # **290 으로 내리면 그 몫이 사라진다.** 갑Ⅱ 는 이미 문턱 아래라 넘을 곳이 없다.
    now_options = BillingOptions(contract_kw=290.0)
    now_selection = TariffSelection("general_a_2", "high_a", "II")
    now_bill = calculate_bill(usage, tariff, now_selection, options=now_options)
    now = _adequacy(
        usage,
        now_bill,
        contract_kw=290.0,
        billing_demand_kw=132.28,
        table=tariff,
        options=now_options,
    )
    assert not now.floor_binding
    assert not now.reducible
    assert now.target_contract_kw is None
    assert now.saving_won == pytest.approx(0.0)


def test_교육용_덱_벌에_하한이_이기는_자리가_있다(tariff: TariffTable) -> None:
    """**교육용에는 하한이 이기는 벌이 하나도 없었다** (91세션 1절).

    90세션이 교육용 둘의 하한을 0.3 으로 세웠는데 덱 벌 아홉 가운데 하한이
    이기는 것은 `large-b-over`(을)와 `small-a2-was`(그때는 갑Ⅱ. 96세션에
    을로 바꿨다 — 갑Ⅱ 700 kW 가 종별 경계 밖이었다) 둘뿐이었다 —
    교육용에서는 그 갈래의 글과 그림이 **한 번도 그려진 적이 없다.** 83세션이
    갑Ⅱ 에 `small-a2-was` 를 지은 자리와 같은 병이다.

    벌을 지웠거나 계약전력을 만지면 여기서 걸린다. **성립 여부도 함께 본다** —
    교육용전력(갑)은 계약전력 1,000 kW 미만이라(약관 제58조 ② 1.) 하한을
    이기려고 계약전력을 올리다 보면 종별 자체가 성립하지 않게 된다.
    """
    import sys

    sys.path.insert(0, str(PROJECT_ROOT / "tools"))
    import render_deck

    case = render_deck.BY_KEY["small-edu-a-over"]
    contract_type = tariff.contract_types[case.contract_type]
    assert not contract_type.base_fee_on_contract_at(case.voltage), "요금적용전력 기준이어야 한다"
    threshold_kw = contract_type.threshold_kw
    assert threshold_kw is not None
    assert case.contract_kw < threshold_kw, "교육용(갑)은 1,000 kW 미만이다"

    ratio = contract_type.contract_floor_ratio
    assert ratio is not None
    floor_kw = case.contract_kw * ratio
    # **전체 최대수요를 넘으면 요금적용전력도 반드시 넘는다** — 대상 시간대·대상월을
    # 다시 고르지 않아도 판정이 선다.
    peak_kw = float(load_usage(case.csv).kw.max())
    assert floor_kw > peak_kw, f"하한 {floor_kw} kW 가 최대수요 {peak_kw} kW 를 못 넘는다"


def test_특례를_켜면_계약전력_조정_권고가_뒤집힌다(tariff: TariffTable) -> None:
    """**값이 아니라 권고가 바뀌는 자리다** (91세션 · 97세션 5절).

    같은 자료·같은 계약전력에서 하한이 285 → 142.5 kW 로 내려가면 **어느 달에도
    안 걸려서**(가장 작은 달이 208.24 kW 다) 낮출 이유 자체가 사라진다.
    절감액이 1,563,365.625원에서 0 원이 된다 — 특례는 기본요금만 깎는 것이 아니라
    **개선 수단 하나의 판정을 통째로 바꾼다.** 금액만 보고 「−7%」 로 정리하면
    이 뒤집힘이 안 보인다.

    **105세션에 목표 883 → 694 kW · 절감 1,338,660 → 1,576,103원이 됐다**
    (그 1,576,103 은 **S119 에 1,563,365.625 로 갈렸다** — ⑳ · 제7조 ①).
    가장 작은 달(208.24 kW)로 나누므로 883 kW 로 멈출 때보다 더 얻는다 —
    883 의 하한 264.9 kW 는 그 달들에 여전히 걸려 있었다.
    """
    import sys

    sys.path.insert(0, str(PROJECT_ROOT / "tools"))
    import render_deck

    from kwise.measures.contract import evaluate_contract_adjustment
    from kwise.ui.pipeline import ContractForm, baseline_bill

    case = render_deck.BY_KEY["small-edu-a-over"]
    usage = load_usage(case.csv)

    def adjust(school: bool) -> object:
        form = ContractForm(
            case.contract_type,
            case.voltage,
            case.option,
            contract_kw=case.contract_kw,
            school_exception=school,
        )
        return evaluate_contract_adjustment(
            usage,
            baseline_bill(usage, tariff, form),
            contract_kw=case.contract_kw,
            contract_floor_ratio=None,  # 요금 결과가 들고 온 값을 쓴다
        )

    plain = adjust(False)
    special = adjust(True)
    assert plain.floor_kw == pytest.approx(285.0)  # type: ignore[attr-defined]
    assert plain.target_contract_kw == 694.0  # type: ignore[attr-defined]
    # **S119 에 1,576,103 → 1,563,365.625 로 갈아 끼웠다** (⑳ · 제7조 ①).
    # 요금적용전력이 1kW 로 접히면서 목표 694 kW 의 기본요금 기반이 움직였다 —
    # **뒤집힘 자체(목표 694 → None · 절감 → 0)는 그대로다.**
    assert plain.saving_won == pytest.approx(1_563_365.625, abs=1.0)  # type: ignore[attr-defined]
    assert special.floor_kw == pytest.approx(142.5)  # type: ignore[attr-defined]
    assert special.target_contract_kw is None  # type: ignore[attr-defined]
    assert special.saving_won == pytest.approx(0.0)  # type: ignore[attr-defined]


def test_contract_warnings_only_when_lowering_helps(
    sample_usage: UsageData, sample_bill: BillingResult
) -> None:
    """**낮출 자리가 있을 때만 하향 경고를 낸다** (83세션).

    낮춰도 한 푼 안 주는 자료에서 「되돌리기 어렵다」 를 읽히면 하지도 못할
    일을 조심하라는 말이 된다. 하한이 이기는 자료에서는 그대로 나온다.
    """

    def assess(ratio: float) -> ContractAdequacy:
        return _adequacy(
            sample_usage,
            sample_bill,
            contract_kw=7_000.0,
            billing_demand_kw=5_293.44,
            contract_floor_ratio=ratio,
        )

    binding = assess(1.0)
    assert binding.reducible
    assert any("여유를 확보" in message for message in texts(binding.notices))
    assert any("12개월간 적용" in message for message in texts(binding.notices))

    assert not any("여유를 확보" in message for message in texts(assess(0.3).notices))


def test_over_contract_slots_are_flagged(
    sample_usage: UsageData, sample_bill: BillingResult
) -> None:
    adequacy = _adequacy(sample_usage, sample_bill, contract_kw=5_000.0, billing_demand_kw=5_293.44)
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
