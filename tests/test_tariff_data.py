"""요금 데이터 검증 (요구사항서 부록 A.2, A.3, A.4).

단가를 수기로 옮길 때의 오타는 결과가 그럴듯하게 나와 발견하기 어렵다.
**이 규칙들이 오타를 잡는 유일한 수단이다.** 규칙 하나하나를 값으로 못 박고,
일부러 오타를 낸 요금표가 실제로 걸리는지도 확인한다.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from kwise.tariff import (
    BANDS,
    DEFAULT_POLICY,
    TariffDataError,
    TariffSelection,
    TariffTable,
    available_tariff_files,
    default_tariff_dir,
    list_contract_types,
    list_options,
    list_selections,
    list_voltages,
    load_tariff,
    option_pair_diffs,
    parse_tariff,
    switchable_selections,
    validate_tariff,
)

SEASONS = ("summer", "spring_fall", "winter")
OPTIONS = ("I", "II", "III")
VOLTAGES = ("high_a", "high_b")


@pytest.fixture(scope="module")
def payload() -> dict[str, Any]:
    path = default_tariff_dir() / "tariff_kr_20260601.json"
    with path.open(encoding="utf-8") as stream:
        loaded: dict[str, Any] = json.load(stream)
    return loaded


def broken(payload: dict[str, Any], mutate: Any) -> TariffTable:
    """오타를 낸 요금표 사본. 원본은 건드리지 않는다."""
    copied = copy.deepcopy(payload)
    mutate(copied)
    return parse_tariff(copied)


def energy_of(copied: dict[str, Any], voltage: str, option: str) -> dict[str, dict[str, float]]:
    voltages = copied["contract_types"]["general_b"]["voltages"]
    result: dict[str, dict[str, float]] = voltages[voltage][option]["energy"]
    return result


# --------------------------------------------------------------------- 파일·스키마


def test_shipped_file_is_found_and_loaded(tariff: TariffTable) -> None:
    assert tariff.source_path is not None
    assert tariff.source_path.name == "tariff_kr_20260601.json"
    assert tariff.effective_date == "2026-06-01"
    assert tariff.schema_version == "0.3"  # 93세션 — 선택요금별 시행일
    assert not tariff.verified  # 청구서 대조 전이다


def test_every_option_declares_its_own_effective_date(tariff: TariffTable) -> None:
    """스키마 0.3 — **선택요금마다 시행일이 있다.** 종별 값을 물려받지 않는다."""
    counted = 0
    for contract in tariff.contract_types.values():
        for voltage in contract.voltages.values():
            for option in voltage.options.values():
                assert option.effective_date, (contract.key, voltage.voltage, option.option)
                counted += 1
    # 종별 여덟의 (전압·선택요금) 칸 전부. 42 였다가 93세션에 갑Ⅱ 선택Ⅲ·Ⅳ
    # 넷(고압A·B × Ⅲ·Ⅳ)이 붙어 46 이 됐다.
    assert counted == 46


def test_an_option_without_an_effective_date_fails(payload: dict[str, Any]) -> None:
    """**폴백을 두지 않는다** (21세션에 걷어낸 것). 없으면 읽기가 실패한다."""
    copied = copy.deepcopy(payload)
    del copied["contract_types"]["general_b"]["voltages"]["high_a"]["I"]["effective_date"]
    with pytest.raises(TariffDataError, match="effective_date"):
        parse_tariff(copied)


def test_available_files_are_sorted() -> None:
    files = available_tariff_files()
    assert files
    assert list(files) == sorted(files)
    assert all(path.name.startswith("tariff_") for path in files)


def test_scope_follows_appendix_a4(tariff: TariffTable) -> None:
    """부록 A.4 — 일반용·산업용·교육용까지. 농사용·가로등·심야는 범위 밖이다."""
    keys = tuple(key for key, _ in list_contract_types(tariff))
    assert keys == (
        "education_a",
        "education_b",
        "general_a_1",
        "general_a_2",
        "general_b",
        "industrial_a_1",
        "industrial_a_2",
        "industrial_b",
    )
    labels = " ".join(label for _, label in list_contract_types(tariff))
    for excluded in ("농사용", "가로등", "심야", "전기차", "보완전력", "주택용"):
        assert excluded not in labels


def test_general_b_shape_is_unchanged(tariff: TariffTable) -> None:
    """3세션 이래의 일반용(을) 형태가 그대로다. 확장이 기존을 건드리지 않았다."""
    assert tuple(key for key, _ in list_voltages(tariff, "general_b")) == VOLTAGES
    for voltage in VOLTAGES:
        assert list_options(tariff, "general_b", voltage) == OPTIONS


def test_dropdown_choices_come_from_the_data(tariff: TariffTable) -> None:
    """부록 A.3 — 데이터에 없는 조합은 선택지에 나타나지 않는다."""
    selections = list_selections(tariff, contract_types=["general_b"])
    assert len(selections) == 6  # 전압 2 × 선택요금 3
    assert TariffSelection("general_b", "high_a", "II") in selections
    assert all(item.contract_type == "general_b" for item in selections)
    # 산업용(을) 은 고압C 가 더 있다. 종별마다 선택지가 다르다는 증거다.
    industrial = list_selections(tariff, contract_types=["industrial_b"])
    assert len(industrial) == 9
    assert TariffSelection("industrial_b", "high_c", "III") in industrial


def test_pending_options_are_not_filtered_out_of_the_dropdown(tariff: TariffTable) -> None:
    """**시행일로 후보를 막지 않는다** (93세션에 사람이 정한 것).

    갑Ⅱ 선택Ⅲ·Ⅳ 는 2026년 12월분 시행인데 오늘이 그 앞이어도 고를 수 있어야
    한다. 화면 실물로도 확인했다 — 갑Ⅱ 고압A 의 선택요금 드롭다운에
    선택Ⅰ·Ⅱ·Ⅲ·Ⅳ 넷이 선다. 걸러 내는 갈래를 만들면 여기가 깨진다.
    """
    for voltage in ("high_a", "high_b"):
        assert list_options(tariff, "general_a_2", voltage) == ("I", "II", "III", "IV")
    selections = list_selections(tariff, contract_types=["general_a_2"])
    assert len(selections) == 8  # 전압 2 × 선택요금 4
    current = TariffSelection("general_a_2", "high_a", "II")
    assert set(switchable_selections(tariff, current)) == {
        TariffSelection("general_a_2", "high_a", option) for option in ("I", "II", "III", "IV")
    }


def test_unknown_combination_raises(tariff: TariffTable) -> None:
    with pytest.raises(TariffDataError, match="전압구분"):
        tariff.rates(TariffSelection("general_b", "low", "I"))
    with pytest.raises(TariffDataError, match="선택요금"):
        tariff.rates(TariffSelection("general_b", "high_a", "IV"))
    with pytest.raises(TariffDataError, match="계약종별"):
        tariff.rates(TariffSelection("농사용", "high_a", "I"))


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(TariffDataError, match="요금 데이터 파일이 없습니다"):
        load_tariff(tmp_path / "none.json")


# --------------------------------------------------------------------- 규칙 1 순서 관계


def test_shipped_table_passes_every_rule(tariff: TariffTable) -> None:
    assert validate_tariff(tariff) == ()


@pytest.mark.parametrize("voltage", VOLTAGES)
@pytest.mark.parametrize("option", OPTIONS)
@pytest.mark.parametrize("season", SEASONS)
def test_rule1_band_order(tariff: TariffTable, voltage: str, option: str, season: str) -> None:
    rates = tariff.rates(TariffSelection("general_b", voltage, option))
    energy = rates.energy[season]
    assert energy.light < energy.mid < energy.peak


@pytest.mark.parametrize("voltage", VOLTAGES)
def test_rule1_base_fee_increases_with_option(tariff: TariffTable, voltage: str) -> None:
    bases = [
        tariff.rates(TariffSelection("general_b", voltage, option)).base_won_per_kw
        for option in OPTIONS
    ]
    assert bases[0] < bases[1] < bases[2]


@pytest.mark.parametrize("voltage", VOLTAGES)
def test_rule1_energy_rate_decreases_with_option(tariff: TariffTable, voltage: str) -> None:
    for season in SEASONS:
        for band in BANDS:
            values = [
                tariff.rates(TariffSelection("general_b", voltage, option)).rate(season, band)
                for option in OPTIONS
            ]
            assert values[0] > values[1] > values[2], (season, band)


@pytest.mark.parametrize("voltage", VOLTAGES)
@pytest.mark.parametrize("option", OPTIONS)
def test_rule1_summer_peak_beats_spring_fall_peak(
    tariff: TariffTable, voltage: str, option: str
) -> None:
    rates = tariff.rates(TariffSelection("general_b", voltage, option))
    assert rates.rate("summer", "peak") > rates.rate("spring_fall", "peak")


def test_rule1_catches_swapped_bands(payload: dict[str, Any]) -> None:
    def mutate(copied: dict[str, Any]) -> None:
        energy = energy_of(copied, "high_a", "I")["summer"]
        energy["light"], energy["mid"] = energy["mid"], energy["light"]

    findings = validate_tariff(broken(payload, mutate))
    assert any(finding.rule == "규칙 1" for finding in findings)


# --------------------------------------------------------------------- 규칙 2 균일성


def test_rule2_high_a_option1_to_2_is_uniform_5_5(tariff: TariffTable) -> None:
    """고압A Ⅰ→Ⅱ 는 전 계절·시간대에서 5.5 원 차이다 (±0.05)."""
    diffs = option_pair_diffs(tariff, "general_b", "high_a", "I", "II")
    assert len(diffs) == 9  # 계절 3 × 시간대 3
    for key, value in diffs.items():
        assert value == pytest.approx(5.5, abs=0.05), key


def test_rule2_high_b_option1_to_2_is_uniform_3_8(tariff: TariffTable) -> None:
    diffs = option_pair_diffs(tariff, "general_b", "high_b", "I", "II")
    for key, value in diffs.items():
        assert value == pytest.approx(3.8, abs=0.05), key


def test_rule2_high_b_option2_to_3_is_uniform_within_rounding(tariff: TariffTable) -> None:
    """고압B Ⅱ→Ⅲ 는 1.6~1.7 원. 폭 ±0.15 는 반올림 때문이다."""
    values = list(option_pair_diffs(tariff, "general_b", "high_b", "II", "III").values())
    assert min(values) == pytest.approx(1.6, abs=0.01)
    assert max(values) == pytest.approx(1.7, abs=0.01)
    mean = sum(values) / len(values)
    assert max(abs(value - mean) for value in values) <= 0.15


def test_rule2_high_a_option2_to_3_is_peak_focused_not_uniform(tariff: TariffTable) -> None:
    """고압A Ⅱ→Ⅲ 는 할인이 최대부하에 집중된 설계다. 균일하지 않은 것이 정상이다.

    대신 '최대부하 할인폭 > 중간·경부하 할인폭' 을 검사한다.
    """
    diffs = option_pair_diffs(tariff, "general_b", "high_a", "II", "III")
    values = list(diffs.values())
    assert min(values) == pytest.approx(0.6, abs=0.01)
    assert max(values) == pytest.approx(12.4, abs=0.01)

    mean = sum(values) / len(values)
    assert max(abs(value - mean) for value in values) > 0.05  # 균일성 검사를 걸면 걸린다

    for season in SEASONS:
        assert diffs[(season, "peak")] > diffs[(season, "mid")], season
        assert diffs[(season, "peak")] > diffs[(season, "light")], season


def test_rule2_uniformity_is_not_applied_to_high_a_2_to_3() -> None:
    """적용 범위 한정 — 정책에 예외가 박혀 있어야 한다."""
    policy = DEFAULT_POLICY[("general_b", "high_a", "II", "III")]
    assert policy.uniform_tolerance is None
    assert policy.peak_focused
    assert policy.breakeven_range is None


def test_rule2_catches_a_single_digit_typo(payload: dict[str, Any]) -> None:
    """고압A Ⅰ→Ⅱ 의 한 칸만 1원 어긋나도 걸려야 한다."""

    def mutate(copied: dict[str, Any]) -> None:
        energy_of(copied, "high_a", "II")["winter"]["mid"] += 1.0

    findings = validate_tariff(broken(payload, mutate))
    assert any(finding.rule == "규칙 2" for finding in findings)


def test_rule2_catches_broken_peak_focus(payload: dict[str, Any]) -> None:
    def mutate(copied: dict[str, Any]) -> None:
        # 최대부하 할인폭을 경부하보다 작게 만든다
        energy_of(copied, "high_a", "III")["summer"]["peak"] = 222.2

    findings = validate_tariff(broken(payload, mutate))
    assert any(
        finding.rule == "규칙 2" and "최대부하 할인폭" in finding.message for finding in findings
    )


# --------------------------------------------------------------------- 규칙 3 손익분기


def breakeven_hours(tariff: TariffTable, voltage: str, lower: str, upper: str) -> float:
    diffs = list(option_pair_diffs(tariff, "general_b", voltage, lower, upper).values())
    mean_diff = sum(diffs) / len(diffs)
    low = tariff.rates(TariffSelection("general_b", voltage, lower)).base_won_per_kw
    high = tariff.rates(TariffSelection("general_b", voltage, upper)).base_won_per_kw
    return (high - low) / mean_diff


def test_rule3_high_a_option1_to_2_breakeven(tariff: TariffTable) -> None:
    """1,100 ÷ 5.5 = 200 h/월."""
    hours = breakeven_hours(tariff, "high_a", "I", "II")
    assert hours == pytest.approx(200.0, abs=1.0)
    assert 150 <= hours <= 250


def test_rule3_high_b_option1_to_2_breakeven(tariff: TariffTable) -> None:
    """750 ÷ 3.8 = 197 h/월."""
    hours = breakeven_hours(tariff, "high_b", "I", "II")
    assert hours == pytest.approx(197.0, abs=1.0)
    assert 150 <= hours <= 250


def test_rule3_high_b_option2_to_3_matches_the_500_hour_notice(tariff: TariffTable) -> None:
    """810 ÷ 1.65 ≈ 491 h. 요금표 안내 '월 500시간 초과 고객에게 유리' 와 맞는다.

    요금표의 안내 문구와 산출값을 대조하는 것이 가장 강력한 검증이다.
    """
    hours = breakeven_hours(tariff, "high_b", "II", "III")
    assert hours == pytest.approx(491.0, abs=2.0)
    assert 400 <= hours <= 600
    assert abs(hours - 500) < 50


def test_rule3_catches_wrong_base_fee(payload: dict[str, Any]) -> None:
    def mutate(copied: dict[str, Any]) -> None:
        voltages = copied["contract_types"]["general_b"]["voltages"]
        voltages["high_a"]["II"]["base_won_per_kw"] = 9000  # 손익분기가 324 h 로 튄다

    findings = validate_tariff(broken(payload, mutate))
    assert any(finding.rule == "규칙 3" for finding in findings)


# --------------------------------------------------------------------- 규칙 4 시간대 완전성


@pytest.mark.parametrize("season", SEASONS)
def test_rule4_bands_cover_exactly_24_hours(tariff: TariffTable, season: str) -> None:
    hours = tariff.hour_bands["mainland"][season]
    assert len(hours) == 24
    assert all(band is not None for band in hours)

    counts = {band: sum(1 for value in hours if value == band) for band in BANDS}
    assert sum(counts.values()) == 24
    assert counts["light"] == 10  # 22:00~08:00
    assert counts["mid"] == 8
    assert counts["peak"] == 6


def test_rule4_catches_overlap(payload: dict[str, Any]) -> None:
    def mutate(copied: dict[str, Any]) -> None:
        copied["tou_definition"]["mainland"]["summer"]["mid"] = [[8, 16], [21, 22]]  # 15~16 중복

    findings = validate_tariff(broken(payload, mutate))
    assert any(finding.rule == "규칙 4" and "겹칩니다" in finding.message for finding in findings)


def test_rule4_catches_gap(payload: dict[str, Any]) -> None:
    def mutate(copied: dict[str, Any]) -> None:
        copied["tou_definition"]["mainland"]["winter"]["mid"] = [[8, 9], [12, 16]]  # 19~22 누락

    findings = validate_tariff(broken(payload, mutate))
    assert any(
        finding.rule == "규칙 4" and "정의되지 않은" in finding.message for finding in findings
    )


def test_season_definition_covers_every_month(tariff: TariffTable) -> None:
    assert {tariff.season_of(month) for month in range(1, 13)} == set(SEASONS)
    assert tariff.seasons["summer"] == (6, 7, 8)
    assert tariff.seasons["winter"] == (11, 12, 1, 2)


def test_duplicate_month_in_season_definition_raises(payload: dict[str, Any]) -> None:
    copied = copy.deepcopy(payload)
    copied["season_definition"]["summer"] = [5, 6, 7, 8]  # 5월이 봄·가을과 겹친다
    with pytest.raises(TariffDataError, match="두 번"):
        parse_tariff(copied)
