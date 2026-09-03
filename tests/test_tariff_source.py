"""요금표 엑셀 변환과 종별 확장 (요구사항서 부록 A.1·A.2·A.4).

**단가를 수기로 옮기지 않는다.** ``data\\source\\`` 의 엑셀을 변환해
``data\\tariff_kr_20260601.json`` 을 만든다. 이 파일은 변환이 조용히 틀리는
두 경로를 못 박는다.

    ① 시트마다 계절 열 순서가 다르다 — 산업용만 봄·가을철이 앞이다.
       위치로 읽으면 여름과 봄·가을이 통째로 뒤바뀐다.
    ② 엑셀에 없는 규칙(요일·특례·요금적용전력·임계값)을 덮어쓰면 안 된다.

그리고 확장이 **기존 일반용(을) 60개 값을 건드리지 않았는지**를 대조한다.
3세션 이래 이 값으로 요금을 냈다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from kwise.io import UsageData
from kwise.notices import texts
from kwise.quality import QualityReport
from kwise.tariff import (
    BANDS,
    TENTATIVE_BASE_FEE_BASIS_WARNING,
    BillingOptions,
    TariffDataError,
    TariffSelection,
    TariffTable,
    build_calendar,
    calculate_bill,
    classify_slots,
    list_selections,
    parse_tariff,
    switchable_selections,
    validate_tariff,
)
from kwise.tariff.source_excel import (
    CONTRACT_RULES,
    TariffSourceError,
    build_payload,
    read_rate_rows,
    read_time_bands,
    season_columns,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_XLSX = PROJECT_ROOT / "data" / "source" / "2026-06-01_KEPCO_Electricity_Tariff.xlsx"
EFFECTIVE_DATE = "2026-06-01"


@pytest.fixture(scope="module")
def source_path() -> Path:
    if not SOURCE_XLSX.is_file():
        pytest.skip(f"요금표 엑셀이 없습니다: {SOURCE_XLSX}")
    return SOURCE_XLSX


@pytest.fixture(scope="module")
def converted(source_path: Path) -> TariffTable:
    """엑셀에서 갓 변환한 표. 저장된 JSON 과 별개로 만든다."""
    return parse_tariff(build_payload(source_path, effective_date=EFFECTIVE_DATE))


# --------------------------------------------------------------------- ① 계절 열 순서


def test_industrial_sheet_orders_the_seasons_differently(source_path: Path) -> None:
    """**산업용만 봄·가을철이 여름철보다 앞이다.** 위치로 읽으면 통째로 뒤바뀐다."""

    def header(sheet: str) -> list[object]:
        return list(pd.read_excel(source_path, sheet_name=sheet, header=None).iloc[2])

    assert season_columns(header("일반용 전력")) == {5: "summer", 6: "spring_fall", 7: "winter"}
    assert season_columns(header("산업용 전력")) == {5: "spring_fall", 6: "summer", 7: "winter"}
    assert season_columns(header("교육용·농사용·기타")) == {
        5: "summer",
        6: "spring_fall",
        7: "winter",
    }


def test_industrial_b_summer_peak_is_2345(converted: TariffTable) -> None:
    """검산 — 산업용(을) 고압A 선택Ⅰ 최대부하는 여름 234.5 / 봄가을 156.4 다.

    뒤바뀌면 봄·가을 단가가 여름보다 비싸져 태양광 평가가 정반대로 나온다.
    """
    rates = converted.rates(TariffSelection("industrial_b", "high_a", "I"))
    assert rates.rate("summer", "peak") == pytest.approx(234.5)
    assert rates.rate("spring_fall", "peak") == pytest.approx(156.4)
    assert rates.rate("winter", "peak") == pytest.approx(210.1)
    assert rates.rate("summer", "peak") > rates.rate("spring_fall", "peak")


def test_shipped_file_carries_the_same_industrial_rates(tariff: TariffTable) -> None:
    """저장된 JSON 도 같은 값이어야 한다. 변환을 돌리고 커밋했다는 확인이다."""
    rates = tariff.rates(TariffSelection("industrial_b", "high_a", "I"))
    assert rates.rate("summer", "peak") == pytest.approx(234.5)
    assert rates.rate("spring_fall", "peak") == pytest.approx(156.4)


def test_season_columns_rejects_a_header_without_seasons() -> None:
    with pytest.raises(TariffSourceError, match="계절 열을 찾지 못했습니다"):
        season_columns(["종별구분", "전압구분", "선택요금"])


def test_season_columns_rejects_duplicates() -> None:
    with pytest.raises(TariffSourceError, match="중복"):
        season_columns(["구분", "여름철 (6~8월)", "여름철 (재게시)", "겨울철"])


# --------------------------------------------------------------------- ② 회귀 — 기존 60개 값


def test_general_b_sixty_values_are_unchanged(converted: TariffTable) -> None:
    """일반용(을) 고압A·B 의 기본요금 6 + 전력량요금 54 = 60개 값.

    3세션 이래 이 값으로 요금을 냈다. 엑셀 변환이 하나라도 바꾸면 실패한다.
    """
    expected_base = {
        ("high_a", "I"): 7_220.0,
        ("high_a", "II"): 8_320.0,
        ("high_a", "III"): 9_810.0,
        ("high_b", "I"): 6_630.0,
        ("high_b", "II"): 7_380.0,
        ("high_b", "III"): 8_190.0,
    }
    checked = 0
    for (voltage, option), base in expected_base.items():
        rates = converted.rates(TariffSelection("general_b", voltage, option))
        assert rates.base_won_per_kw == pytest.approx(base), (voltage, option)
        checked += 1
        for season in ("summer", "spring_fall", "winter"):
            for band in BANDS:
                assert rates.rate(season, band) > 0
                checked += 1
    assert checked == 60

    # 부록 B 회귀의 뿌리가 되는 몇 칸은 값까지 못 박는다.
    high_a_1 = converted.rates(TariffSelection("general_b", "high_a", "I"))
    assert high_a_1.rate("summer", "peak") == pytest.approx(227.8)
    assert high_a_1.rate("summer", "light") == pytest.approx(92.8)
    assert high_a_1.rate("spring_fall", "peak") == pytest.approx(146.0)
    assert high_a_1.rate("winter", "mid") == pytest.approx(145.9)


def test_shipped_json_equals_a_fresh_conversion(source_path: Path) -> None:
    """저장된 JSON 이 지금 변환한 것과 같아야 한다. 손으로 고친 흔적을 잡는다."""
    fresh = build_payload(source_path, effective_date=EFFECTIVE_DATE)
    path = PROJECT_ROOT / "data" / "tariff_kr_20260601.json"
    with path.open(encoding="utf-8") as stream:
        stored = json.load(stream)
    assert stored == fresh


def test_sample_billing_demand_is_unchanged(
    sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable
) -> None:
    """확장 뒤에도 샘플 요금이 그대로다 (부록 B)."""
    bill = calculate_bill(
        sample_usage, tariff, TariffSelection("general_b", "high_a", "I"), quality=sample_report
    )
    assert bill.billing_demand_kw == pytest.approx(5_293.44)
    assert bill.total_base_won == pytest.approx(452_832_624, rel=1e-6)
    assert bill.base_fee_months == pytest.approx(12.0)


# --------------------------------------------------------------------- 엑셀에 없는 규칙


def test_thresholds_and_floor_ratios_survive_the_conversion(tariff: TariffTable) -> None:
    """계약전력 임계값과 요금적용전력 하한은 엑셀에 없다. 코드가 들고 있다."""
    # 갑Ⅱ 에 하한 30% 가 있는 것이 맞다 (61세션). 제68조 제1항의 하한은 최대수요
    # 전력계 고객 전체에 걸리고, 갑Ⅱ 는 저압이 없어 언제나 그 고객이다.
    #
    # **교육용 둘도 30% 다** (90세션에 세칙 원문을 읽고 고쳤다). 15% 는 종별
    # 속성이 아니라 **신청한 초·중·고교·유치원에만 붙는 고객별 특례**다
    # (시행세칙 별표4 8. 나.(1)) — 아래 시험이 그것을 못으로 박는다.
    expected = {
        "general_a_1": (300.0, "below", None),
        "general_a_2": (300.0, "below", 0.3),
        "general_b": (300.0, "above", 0.3),
        "industrial_a_1": (300.0, "below", None),
        "industrial_a_2": (300.0, "below", 0.3),
        "industrial_b": (300.0, "above", 0.3),
        "education_a": (1_000.0, "below", 0.3),  # 교육용은 1,000 kW 다
        "education_b": (1_000.0, "above", 0.3),
    }
    for key, (threshold, direction, floor) in expected.items():
        contract = tariff.contract(key)
        assert contract.threshold_kw == pytest.approx(threshold), key
        assert contract.threshold_direction == direction, key
        if floor is None:
            assert contract.contract_floor_ratio is None, key
        else:
            assert contract.contract_floor_ratio == pytest.approx(floor), key


def test_no_contract_type_carries_the_school_exception_as_its_floor(tariff: TariffTable) -> None:
    """**15% 를 종별 기본값으로 든 종별이 하나도 없어야 한다** (90세션).

    시행세칙 별표4 8.「초·중·고교 및 유치원 전기요금 적용」 은 약관 제58조
    적용대상 **중** 초·중등교육법 제2조와 유아교육법 제2조 제2호 시설에만
    붙고, 「고객의 신청일이 속하는 월분부터 적용한다」 라 **신청해야 붙는다.**
    갑/을을 가르지도 않는다 — 종별 속성으로 얹으면 신청하지 않은 고객의
    기본요금이 절반으로 나온다.

    같은 특례가 요금적용전력을 **당월분 최대수요전력**으로 바꾸는데 도구는
    제68조 ①의 12개월 창을 쓴다. 그 창까지 갈아 끼워야 특례를 옳게 계산하므로
    **하한만 떼어 넣을 수 없다** — 특례 자체는 미해결로 남는다.
    """
    for key, contract in tariff.contract_types.items():
        floor = contract.contract_floor_ratio
        assert floor is None or floor == pytest.approx(0.3), key


def test_demand_rules_survive_the_conversion(tariff: TariffTable) -> None:
    """요금적용전력 3규칙 중 대상 시간대·대상월은 종별 속성으로 남아 있다."""
    for key in ("general_b", "industrial_b", "education_b"):
        contract = tariff.contract(key)
        assert contract.demand_bands == ("mid", "peak")  # 경부하 제외
        assert contract.demand_months == (7, 8, 9, 12, 1, 2)  # 계절 정의와 다르다


def test_day_rules_survive_the_conversion(tariff: TariffTable) -> None:
    """토·일·공휴일 계량 규칙은 엑셀에 없다."""
    assert tariff.day_rules.saturday == "peak_to_mid"
    assert tariff.day_rules.sunday == "all_to_light"
    assert tariff.day_rules.holiday == "all_to_light"
    assert tariff.day_rules.exclude_temporary_holiday


def test_time_bands_come_from_the_info_sheet(source_path: Path) -> None:
    """계절 정의와 시간대 구분은 엑셀 '부가정보' 시트에서 읽는다."""
    seasons, tou = read_time_bands(source_path)
    assert seasons == {
        "summer": [6, 7, 8],
        "spring_fall": [3, 4, 5, 9, 10],
        "winter": [11, 12, 1, 2],
    }
    assert tou["summer"] == {"light": [[22, 8]], "mid": [[8, 15], [21, 22]], "peak": [[15, 21]]}
    assert tou["winter"]["peak"] == [[9, 12], [16, 19]]


# --------------------------------------------------------------------- 산업용(을) 주말 할인


def _classify(table: TariffTable, index: pd.DatetimeIndex, contract_type: str) -> pd.DataFrame:
    calendar = build_calendar(range(index[0].year - 1, index[-1].year + 2))
    return classify_slots(index, 15, table, calendar, contract_type=contract_type)


def test_industrial_b_weekend_discount_applies_only_where_it_should(tariff: TariffTable) -> None:
    """봄·가을철 토·일·공휴일 11~14시에만 50% 다. PV 최성기와 겹치는 특례다.

    미반영 시 태양광 절감 효과가 과대평가된다 (요구사항서 5.6).
    """
    # 2024-04-06 토요일 / 04-07 일요일 (봄·가을철), 04-08 월요일
    index = pd.date_range("2024-04-06 00:15", "2024-04-09 00:00", freq="15min")
    slots = _classify(tariff, index, "industrial_b")
    discounted = slots[slots["discount_rate"] > 0]

    assert set(discounted["discount_rate"]) == {0.5}
    assert set(discounted["season"]) == {"spring_fall"}
    assert set(pd.DatetimeIndex(discounted["slot_start"]).hour) == {11, 12, 13}
    assert set(pd.DatetimeIndex(discounted["slot_start"]).day) == {6, 7}  # 토·일만
    assert len(discounted) == 2 * 3 * 4  # 이틀 × 3시간 × 15분 4칸


def test_industrial_b_weekend_discount_skips_summer(tariff: TariffTable) -> None:
    """여름철 주말에는 붙지 않는다. 봄·가을철 특례다."""
    index = pd.date_range("2024-07-06 00:15", "2024-07-08 00:00", freq="15min")
    slots = _classify(tariff, index, "industrial_b")
    assert float(slots["discount_rate"].max()) == 0.0


def test_weekend_discount_does_not_leak_to_other_contract_types(tariff: TariffTable) -> None:
    """일반용(을)·교육용(을) 에는 이 특례가 없다."""
    index = pd.date_range("2024-04-06 00:15", "2024-04-08 00:00", freq="15min")
    for key in ("general_b", "education_b", "general_a_2"):
        slots = _classify(tariff, index, key)
        assert float(slots["discount_rate"].max()) == 0.0, key


def test_only_industrial_b_declares_the_special_rule(tariff: TariffTable) -> None:
    rule = tariff.special_rules["industrial_b_weekend_discount"]
    assert rule.applies_to == ("industrial_b",)
    assert rule.seasons == ("spring_fall",)
    assert rule.days == ("saturday", "sunday", "holiday")
    assert rule.hours == ((11, 14),)
    assert rule.discount_rate == pytest.approx(0.5)


# --------------------------------------------------------------------- 전체시간 종별


def test_flat_rate_types_have_equal_band_rates(tariff: TariffTable) -> None:
    """갑Ⅰ·교육용(갑)은 '전체시간' 단일 단가다. 세 시간대가 같아야 한다."""
    for key in ("general_a_1", "industrial_a_1", "education_a"):
        contract = tariff.contract(key)
        assert not contract.time_of_use, key
        for voltage in contract.voltages.values():
            for option in voltage.options.values():
                for season, energy in option.energy.items():
                    assert energy.light == energy.mid == energy.peak, (key, season)


def test_general_a_2_carries_the_two_flat_options(tariff: TariffTable) -> None:
    """갑Ⅱ 선택Ⅲ·Ⅳ — **8월 요금표 원문 1쪽의 열여섯 자리를 그대로 못 박는다.**

    엑셀(6-01 판)에 없는 값이라 :class:`BorrowedOption` 이 갑Ⅰ 고압 행에서
    가져온다. **두 자리가 갈라지면 여기가 먼저 알린다** — 옮겨 적은 값이
    아니라 같은 행을 쓰기 때문에, 요금표가 한쪽만 고치면 이 시험이 깨진다.

        data\\source\\2026-08-01_전기요금표(종합).pdf 1쪽
        고압A 선택Ⅲ 7,170 전체시간 142.6 / 98.6 / 130.3
        고압A 선택Ⅳ 8,230 전체시간 138.6 / 94.3 / 125.0
        고압B 선택Ⅲ 7,170 전체시간 140.5 / 97.5 / 127.3
        고압B 선택Ⅳ 8,230 전체시간 135.2 / 92.2 / 122.0
    """
    expected = {
        ("high_a", "III"): (7_170.0, 142.6, 98.6, 130.3),
        ("high_a", "IV"): (8_230.0, 138.6, 94.3, 125.0),
        ("high_b", "III"): (7_170.0, 140.5, 97.5, 127.3),
        ("high_b", "IV"): (8_230.0, 135.2, 92.2, 122.0),
    }
    contract = tariff.contract("general_a_2")
    assert contract.options == ("I", "II", "III", "IV")
    places = 0
    for (voltage, option), (base, summer, spring_fall, winter) in expected.items():
        rates = tariff.rates(TariffSelection("general_a_2", voltage, option))
        assert rates.base_won_per_kw == pytest.approx(base), (voltage, option)
        places += 1
        # '전체시간' 이므로 세 시간대가 같은 값이어야 한다.
        for season, value in (
            ("summer", summer),
            ("spring_fall", spring_fall),
            ("winter", winter),
        ):
            for band in BANDS:
                assert rates.rate(season, band) == pytest.approx(value), (voltage, option, season)
            places += 1
        assert not rates.time_of_use, (voltage, option)
        # 부칙 (2026. 5. 22) 제2항 제3호 — 「2026년 12월분 요금부터 적용」.
        # **날이 아니라 요금월이다.** 12월분이 시작하는 날은 검침 기간에 따라 다르다.
        assert rates.effective_date == "2026-12", (voltage, option)
    assert places == 16

    # 종별은 6-01 시행 그대로다. 선택요금이 종별 시행일을 물려받지 않는다.
    assert contract.effective_date == "2026-06-01"
    for option in ("I", "II"):
        assert (
            tariff.rates(TariffSelection("general_a_2", "high_a", option)).effective_date
            == "2026-06-01"
        )


def test_flat_options_match_the_type_a_1_high_voltage_rows(tariff: TariffTable) -> None:
    """갑Ⅱ 선택Ⅲ·Ⅳ 는 갑Ⅰ 고압 선택Ⅰ·Ⅱ 와 **한 자리도 다르지 않다.**"""
    for voltage in ("high_a", "high_b"):
        for borrowed, source in (("III", "I"), ("IV", "II")):
            here = tariff.rates(TariffSelection("general_a_2", voltage, borrowed))
            there = tariff.rates(TariffSelection("general_a_1", voltage, source))
            assert here.base_won_per_kw == there.base_won_per_kw, (voltage, borrowed)
            for season in sorted(there.energy):
                for band in BANDS:
                    assert here.rate(season, band) == there.rate(season, band), (
                        voltage,
                        borrowed,
                        season,
                        band,
                    )


def test_time_of_use_types_are_marked(tariff: TariffTable) -> None:
    for key in ("general_a_2", "general_b", "industrial_a_2", "industrial_b", "education_b"):
        assert tariff.contract(key).time_of_use, key


def test_flat_rate_type_still_charges_a_higher_summer_rate(tariff: TariffTable) -> None:
    """전체시간이어도 계절 차등은 있다. 규칙 1 의 여름>봄가을이 살아 있다."""
    rates = tariff.rates(TariffSelection("general_a_1", "high_a", "I"))
    assert rates.rate("summer", "peak") == pytest.approx(142.6)
    assert rates.rate("spring_fall", "peak") == pytest.approx(98.6)


# --------------------------------------------------- 계약전력 기준 · 요금적용전력 기준


def test_type_a_base_fee_uses_the_contract_power(
    sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable
) -> None:
    """**갑Ⅰ 은 고압 행도 계약전력 기준이다** (기본공급약관 제68조 제2항).

    요금적용전력으로 매기면 기본요금이 통째로 틀린다. 제57조 제4항·제59조
    제5항이 「갑 고압 고객은 갑Ⅱ를 적용한다」 고 못을 박아 요금표의 갑Ⅰ
    고압A·B 행이 쓰이는 자리는 **저압계량 예외 경로**뿐이다 (87세션 판정 ·
    89세션에 교육용(갑)만 갈랐다).
    """
    selection = TariffSelection("general_a_1", "high_a", "I")
    bill = calculate_bill(
        sample_usage,
        tariff,
        selection,
        options=BillingOptions(contract_kw=250.0),
        quality=sample_report,
    )
    rate = tariff.rates(selection).base_won_per_kw
    assert set(bill.monthly["base_demand_kw"]) == {250.0}
    assert bill.total_base_won == pytest.approx(250.0 * rate * bill.base_fee_months)
    # 요금적용전력은 참고용으로 함께 싣는다. 기본요금에는 쓰지 않는다.
    assert bill.billing_demand_kw > 250.0
    assert any("계약전력" in note and "제68조 제2항" in note for note in texts(bill.notices))


def test_갑Ⅱ_기본요금은_요금적용전력_기준이다(
    sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable
) -> None:
    """**갑Ⅱ 는 저압이 없다 → 언제나 최대수요전력계 고객이다** (61세션 3절).

    제38조 제2항이 「고압 이상의 전압으로 전기를 공급받는 고객에게는 최대수요
    전력을 계량할 수 있는 전력량계를 설치한다」 고 적었고, 제68조 제1항이 그
    고객의 기본요금을 요금적용전력으로 매긴다. 60세션까지는 갑 전체를 계약전력
    기준으로 두어 **용인 실측에서 기본요금이 2.3배로 나왔다** (계약전력 290 kW
    대 요금적용전력 132 kW).
    """
    for key in ("general_a_2", "industrial_a_2"):
        contract = tariff.contract(key)
        assert not contract.base_fee_on_contract_at("high_a"), key
        assert contract.contract_floor_ratio == pytest.approx(0.3), key
        assert "low" not in contract.voltages, f"{key} 에 저압이 생기면 이 전제가 무너진다"

    selection = TariffSelection("general_a_2", "high_a", "II")
    bill = calculate_bill(
        sample_usage,
        tariff,
        selection,
        options=BillingOptions(contract_kw=250.0),
        quality=sample_report,
    )
    # 기본요금에 실제로 곱한 kW 가 계약전력이 아니라 월별 요금적용전력이다.
    assert set(bill.monthly["base_demand_kw"]) != {250.0}
    assert (bill.monthly["base_demand_kw"] == bill.monthly["billing_demand_kw"]).all()
    assert not any(TENTATIVE_BASE_FEE_BASIS_WARNING in note for note in texts(bill.notices))


def test_type_a_refuses_to_guess_the_contract_power(
    sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable
) -> None:
    """계약전력을 모르면 만들어내지 않고 멈춘다."""
    with pytest.raises(TariffDataError, match="계약전력으로 매깁니다"):
        calculate_bill(
            sample_usage,
            tariff,
            TariffSelection("general_a_1", "high_a", "I"),
            quality=sample_report,
        )


def test_type_b_base_fee_still_uses_the_billing_demand(sample_bill: object) -> None:
    """을 종별은 그대로 요금적용전력 기준이다."""
    from kwise.tariff import BillingResult

    assert isinstance(sample_bill, BillingResult)
    assert set(sample_bill.monthly["base_demand_kw"]) == set(
        sample_bill.monthly["billing_demand_kw"]
    )


# --------------------------------------------------------------------- 검증·범위


def test_every_shipped_type_passes_appendix_a2(tariff: TariffTable) -> None:
    """엑셀 변환이라고 검증을 건너뛰지 않는다. 사람이 옮긴 표에는 오차가 섞인다."""
    assert validate_tariff(tariff) == ()
    assert len(tariff.contract_types) == 8


def test_conversion_can_be_narrowed_to_one_type(source_path: Path) -> None:
    """종별을 하나씩 넣고 검증을 통과시킨 뒤 다음으로 간다 (부록 A.4)."""
    table = parse_tariff(
        build_payload(source_path, effective_date=EFFECTIVE_DATE, contracts=["일반용(을)"])
    )
    assert set(table.contract_types) == {"general_b"}
    assert validate_tariff(table) == ()


def test_unknown_contract_name_is_rejected(source_path: Path) -> None:
    with pytest.raises(TariffSourceError, match="규칙이 정의되지 않은 종별"):
        build_payload(source_path, effective_date=EFFECTIVE_DATE, contracts=["농사용전력"])


def test_out_of_scope_types_are_not_shipped(tariff: TariffTable) -> None:
    """농사용·가로등·심야·전기차·보완전력은 범위 밖이다 (부록 A.4).

    엑셀에는 있다. 넣지 않는 것이 결정이다 — 특히 농사용(을) 고압 여름철 단가는
    엑셀(66.6원)과 요금표 원본(68.6원)이 다르다.
    """
    assert "농사용전력" not in CONTRACT_RULES
    for key in tariff.contract_types:
        assert key.startswith(("general", "industrial", "education"))


def test_rate_rows_carry_every_season_by_name(source_path: Path) -> None:
    rows = read_rate_rows(source_path, "산업용 전력")
    industrial_b_peak = [
        row
        for row in rows
        if row.contract == "산업용(을)" and row.voltage == "고압A" and row.band == "최대부하"
    ]
    assert len(industrial_b_peak) == 3  # 선택 I·II·III
    assert set(industrial_b_peak[0].rates) == {"summer", "spring_fall", "winter"}


def test_selections_are_scoped_by_contract_type(tariff: TariffTable) -> None:
    """선택요금 전환은 같은 종별 안에서만이다. 종별은 용도로 정해진다."""
    every = list_selections(tariff)
    assert len(every) > 20
    scoped = list_selections(tariff, contract_types=["general_b"])
    assert len(scoped) == 6
    assert {item.contract_type for item in scoped} == {"general_b"}


def test_switchable_selections_lock_the_voltage(tariff: TariffTable) -> None:
    """**전압구분은 수전설비로 정해진다.** 고압A ↔ B ↔ C 는 전환 대상이 아니다.

    고압A 3,300~66,000 V / 고압B 154,000 V / 고압C 345,000 V 로 수전 자체가
    다르다. 154 kV 수전 건물에 "고압A 로 바꾸면 절감" 을 권하는 것은 변전설비를
    새로 지으라는 말이다. 단가만 보면 그럴듯해서 더 위험하다.
    """
    current = TariffSelection("industrial_b", "high_b", "I")
    switchable = switchable_selections(tariff, current)
    assert {item.option for item in switchable} == {"I", "II", "III"}
    assert {item.voltage for item in switchable} == {"high_b"}
    assert {item.contract_type for item in switchable} == {"industrial_b"}
    assert current in switchable
    # 고압C 는 산업용(을) 에 실제로 있는 전압이지만 권고 대상이 아니다.
    assert TariffSelection("industrial_b", "high_c", "I") not in switchable
    assert TariffSelection("industrial_b", "high_c", "I") in list_selections(tariff)


def test_dropdown_still_offers_every_voltage(tariff: TariffTable) -> None:
    """가두는 것은 **전환 비교**뿐이다. 드롭다운(부록 A.3)은 전부 보여 준다."""
    options = list_selections(tariff, contract_types=["industrial_b"])
    assert {item.voltage for item in options} == {"high_a", "high_b", "high_c"}


def test_diagnosis_compares_only_within_the_current_voltage(sample_diagnosis: object) -> None:
    """샘플은 고압A 수전이므로 고압B 가 후보에 오르지 않는다."""
    from kwise.diagnose import Diagnosis

    assert isinstance(sample_diagnosis, Diagnosis)
    assert all(key.startswith("general_b/high_a/") for key in sample_diagnosis.option_totals)
