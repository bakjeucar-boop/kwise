"""경제성DR 참여 여력 (요구사항서 6.6, 7.3 / 전력시장운영규칙 제12장).

**거래일 제약이 이 모듈의 한 축이다.** 제12.4.2.1조 제1항 1호는 "관공서의 공휴일에
관한 규정"의 공휴일과 **토요일**을 제외한 평일에만 입찰할 수 있게 한다. 이 제약을
빼면 감축 가능량이 30% 이상 과대평가된다.

**나머지 한 축은 저부하 평일이다** (14세션). 연간 참여 일수 제한이 없으므로 실질
제약은 「감축할 여력이 있는 날이 며칠이냐」 하나다. 13세션의 연 60시간 한도는
경제성DR 의 제약이 아니어서 지웠다 — 코드에도 기준 데이터에도 남지 않았는지
:func:`test_연간_한도가_사라졌다` 가 지킨다.

**요금 계량의 '평일' 과 정의가 다르다.** 요금은 토요일을 최대부하 → 중간부하로
낮출 뿐 공휴일로 보지 않지만, DR 은 토·일·공휴일이 모두 똑같이 제외다.
같은 함수로 판정하면 두 규칙이 조용히 섞인다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from kwise.diagnose import (
    Diagnosis,
    DrPotential,
    DrProfile,
    DrResourceType,
    dr_day_mask,
    dr_eligible_days,
    dr_profile,
    dr_reference_capacity_kw,
    judge_resource_types,
)
from kwise.diagnose.dr import (
    dr_bid_restriction_months,
    dr_daily_hours_cap,
    dr_event_hours,
    dr_market_windows,
    dr_max_events_per_day,
    low_load_multiple,
    registration_percentile,
)
from kwise.io import UsageData
from kwise.measures import (
    Certainty,
    evaluate_demand_response,
    shortfall_penalty_won,
)
from kwise.measures.demand_response import UNPRICED_REASON
from kwise.notices import texts
from kwise.rules import rule_value
from kwise.rules.schema import RuleDataError
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


# --------------------------------------------------------------------- 저부하 평일 (14세션)


def test_연간_한도가_사라졌다() -> None:
    """**연 60시간 한도는 경제성DR 의 제약이 아니었다** (14세션에 지웠다).

    코드에도 기준 데이터에도 남아 있으면 안 된다 — 남으면 다음 사람이 그것을
    근거로 다시 곱한다.
    """
    import kwise.diagnose.dr as module

    assert not hasattr(module, "dr_annual_hours_cap")
    assert not hasattr(module, "low_load_percentile")
    with pytest.raises(RuleDataError, match=r"dr\.annual_hours_cap"):
        rule_value("dr.annual_hours_cap")

    offenders = [
        str(path)
        for path in Path("src/kwise").rglob("*.py")
        if "annual_hours_cap" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, offenders


def test_남는_제약은_하루_한도와_제재다() -> None:
    """하루 2회 × 최대 4시간 = 8시간. 미이행은 6개월 입찰 제한."""
    assert dr_max_events_per_day() == 2
    assert dr_event_hours() == (1.0, 4.0)
    assert dr_daily_hours_cap() == 8.0
    assert dr_bid_restriction_months() == 6.0


def test_기준선은_주말_공휴일_운영시간대_평균이다(sample_diagnosis: Diagnosis) -> None:
    """① 건물이 사실상 비어 있을 때의 수준을 기준선으로 삼는다."""
    profile = sample_diagnosis.dr
    assert profile is not None
    baseline = profile.weekend_baseline_kw
    assert baseline is not None
    assert profile.weekend_days == 114
    # **21세션에 판정 창이 좁아졌다** — 시장 시간대(09~12·13~20시) ∩ 건물 운영
    # 시간대(09~18시). 건물이 닫힌 18~20시 부하가 기준선에서 빠졌다.
    assert baseline == pytest.approx(2_171, abs=5)
    # ② 문턱은 기준선 × 배수. 배수는 assumptions.json 에 있다.
    assert profile.low_load_multiple == low_load_multiple() == 1.2
    assert profile.low_load_threshold_kw == pytest.approx(baseline * profile.low_load_multiple)
    assert profile.low_load_threshold_kw == pytest.approx(2_605, abs=5)


def test_저부하_평일을_데이터에서_찾는다(sample_diagnosis: Diagnosis) -> None:
    """**실질 제약은 감축할 여력이 있는 날이 며칠이냐 하나다.**

    샘플에서 걸린 이틀은 근로자의 날과 추석 연휴 사이의 월요일 — 사무실을 비운
    날이다. 하위 분위수 방식은 「항상 그만큼」 을 뽑아 이 사실을 가렸다.
    """
    profile = sample_diagnosis.dr
    assert profile is not None
    assert profile.low_load_days_count == 2
    assert [f"{day:%Y-%m-%d}" for day in profile.low_load_days] == ["2023-05-01", "2023-10-02"]
    assert all(day.weekday() < 5 for day in profile.low_load_days)
    assert profile.low_load_threshold_kw is not None
    assert profile.normal_weekday_mean_kw is not None
    assert profile.normal_weekday_mean_kw > profile.low_load_threshold_kw


def test_저부하_평일_목록을_보여_준다(sample_diagnosis: Diagnosis) -> None:
    """어떤 날인지 알아야 사용자가 맞는 날인지 판정할 수 있다 (14세션 4절)."""
    profile = sample_diagnosis.dr
    assert profile is not None
    table = profile.low_load_day_table()
    assert list(table.columns) == ["날짜", "요일", "감축 여력(kW)", "참여 가능 시간(h)"]
    assert len(table) == profile.low_load_days_count
    assert set(table["요일"]) <= {"월", "화", "수", "목", "금"}


def test_참여_시간은_하루_여덟_시간으로_잘린다(sample_diagnosis: Diagnosis) -> None:
    """④ 하루 최대 2회 × 4시간이 상한이다. 저부하가 더 오래 가도 8시간을 넘지 않는다."""
    profile = sample_diagnosis.dr
    assert profile is not None
    assert profile.daily_hours_cap == 8.0
    assert bool((profile.daily_hours <= 8.0 + 1e-9).all())
    assert profile.total_participation_hours == pytest.approx(16.0)


def test_감축_가능량은_저부하일별_곱의_합이다(sample_diagnosis: Diagnosis) -> None:
    """⑤ Σ(저부하일별 감축 여력 × 그날 참여 가능 시간)."""
    profile = sample_diagnosis.dr
    assert profile is not None
    expected = float((profile.daily_reducible_kw * profile.daily_hours).sum())
    assert profile.period_reducible_kwh == pytest.approx(expected)
    # 관측 기간을 365일로 환산한다. 기간이 1년이 아닐 수 있다.
    assert profile.annual_reducible_kwh == pytest.approx(expected * 365.0 / profile.total_days)
    assert profile.annual_reducible_kwh == pytest.approx(34_350, rel=1e-3)


def test_등록_권장_용량은_저부하일_분포의_하위값이다(sample_diagnosis: Diagnosis) -> None:
    """**보수적으로.** 미이행이 6개월 입찰 제한이라 과대 산정의 대가가 크다."""
    profile = sample_diagnosis.dr
    assert profile is not None
    assert registration_percentile() == 0.10
    assert 0 < profile.registered_capacity_kw <= profile.mean_reducible_kw
    assert profile.registered_capacity_kw == pytest.approx(
        float(profile.daily_reducible_kw.quantile(0.10))
    )


def test_감축_여력은_운영_시간대로만_잰다(sample_diagnosis: Diagnosis) -> None:
    """③ 점심과 야간은 빠진다. 참여할 수 없는 시간의 부하를 여력으로 세지 않는다."""
    profile = sample_diagnosis.dr
    assert profile is not None
    # **시장 시간대와 건물 운영 시간대는 다른 값이다** (21세션 4절). 제도는
    # 09~12·13~20시를 열어 두고, 이 건물은 09~18시에 돈다. 여력은 겹치는 곳에서만 난다.
    assert dr_market_windows() == ((9, 12), (13, 20))
    assert profile.windows == ((9, 12), (13, 18))
    hours = {hour for start, end in profile.windows for hour in range(start, end)}
    assert 12 not in hours  # 점심
    assert 19 not in hours  # 건물이 닫힌 뒤
    assert 8 not in hours


def test_배수를_올리면_저부하일이_늘어난다(sample_usage: UsageData, calendar: object) -> None:
    """배수가 판정을 정한다. 값은 assumptions.json 에 있다."""
    loose = dr_profile(
        sample_usage.kw,
        15,
        calendar,  # type: ignore[arg-type]
        contract_type="general_b",
        low_load_ratio=1.6,
    )
    tight = dr_profile(
        sample_usage.kw,
        15,
        calendar,  # type: ignore[arg-type]
        contract_type="general_b",
        low_load_ratio=1.0,
    )
    assert loose.low_load_days_count > tight.low_load_days_count
    assert loose.annual_reducible_kwh > tight.annual_reducible_kwh


def test_저부하일이_없으면_영을_내고_이유를_적는다(
    sample_usage: UsageData, calendar: object
) -> None:
    """**0 을 내되 빈칸으로 두지 않는다.**"""
    profile = dr_profile(
        sample_usage.kw,
        15,
        calendar,  # type: ignore[arg-type]
        contract_type="general_b",
        low_load_ratio=0.1,  # 아무 날도 걸리지 않는 문턱
    )
    assert profile.low_load_days_count == 0
    assert profile.annual_reducible_kwh == 0.0
    assert profile.registered_capacity_kw == 0.0
    assert profile.potential is DrPotential.LOW
    assert any("저부하 평일이 없습니다" in message for message in texts(profile.notices))


def test_정전일은_저부하_평일이_아니다(sample_usage: UsageData, calendar: object) -> None:
    """정전으로 부하가 낮았던 날은 감축 여력이 아니다."""
    without = dr_profile(
        sample_usage.kw,
        15,
        calendar,  # type: ignore[arg-type]
        contract_type="general_b",
    )
    blanket = pd.Series(True, index=sample_usage.kw.index)  # 전 구간을 정전으로 본다
    with_outage = dr_profile(
        sample_usage.kw,
        15,
        calendar,  # type: ignore[arg-type]
        contract_type="general_b",
        outage_mask=blanket,
    )
    assert without.low_load_days_count > 0
    assert with_outage.low_load_days_count == 0


def test_참고_문턱은_자원_단위_기준이다(sample_usage: UsageData, calendar: object) -> None:
    """0.1 MW-h 문턱은 **자원 단위** 기준이다. 개별 고객 판정으로 쓰지 않는다."""
    assert dr_reference_capacity_kw() == 100.0
    tiny = sample_usage.kw * 0.02  # 등록 가능 용량이 문턱 아래로 내려간다
    profile = dr_profile(
        tiny,
        15,
        calendar,  # type: ignore[arg-type]
        contract_type="general_b",
        contract_kw=110.0,
    )
    assert not profile.meets_reference_capacity
    assert profile.potential is DrPotential.LOW
    assert any("묶은 자원 단위" in message for message in texts(profile.notices))


def test_적합성_등급은_등록_용량으로_매긴다(sample_diagnosis: Diagnosis) -> None:
    profile = sample_diagnosis.dr
    assert profile is not None
    assert profile.potential is DrPotential.HIGH  # 등록 권장 1,838 kW
    assert profile.meets_reference_capacity


def test_관측치가_없으면_거부한다(calendar: object) -> None:
    empty = pd.Series(float("nan"), index=pd.date_range("2024-03-01", periods=8, freq="15min"))
    with pytest.raises(ValueError, match="관측된 수요가 없어"):
        dr_profile(empty, 15, calendar)  # type: ignore[arg-type]


# --------------------------------------------------------------------- 7.3 수단


def test_수단은_진단의_감축량을_그대로_쓴다(sample_diagnosis: Diagnosis) -> None:
    """감축량을 두 곳에서 만들면 어긋난다. 7.3 은 6.6 의 값을 옮길 뿐이다."""
    profile = sample_diagnosis.dr
    assert profile is not None
    result = evaluate_demand_response(profile)
    assert result.eligible_days == 245
    assert result.low_load_days == profile.low_load_days_count
    assert result.annual_reducible_kwh == pytest.approx(profile.annual_reducible_kwh)
    assert result.participation_hours == pytest.approx(profile.total_participation_hours)
    assert result.investment_won == 0.0
    assert result.certainty is Certainty.MEDIUM


def test_안내_문구가_제약_셋을_모두_적는다(sample_diagnosis: Diagnosis) -> None:
    """연간 제한 없음 · 하루 2회 8시간 · 6개월 입찰 제한."""
    profile = sample_diagnosis.dr
    assert profile is not None
    notice = evaluate_demand_response(profile).participation_notice
    assert "연간 참여 일수 제한은 없으나" in notice
    assert "하루 최대 2회(총 8시간)" in notice
    assert "6개월 입찰 제한" in notice
    assert "수요관리사업자와 상담해 결정하십시오" in notice


def test_단가가_없으면_사유를_낸다(sample_diagnosis: Diagnosis) -> None:
    """**정산 단가는 우리가 만들 수 없다.** 없으면 감축량만 내고 사유를 적는다."""
    profile = sample_diagnosis.dr
    assert profile is not None
    result = evaluate_demand_response(profile)
    assert result.unit_price_won_per_kwh is None
    assert result.settlement_won is None
    assert not result.is_priced
    assert result.settlement_label == UNPRICED_REASON
    # **사유는 표 한 칸 길이다** (28세션 1-4). 왜 만들지 않는지는 아래 차단
    # 안내가 말한다 — 같은 설명을 표 안에서 되풀이하지 않는다.
    assert result.settlement_label == "미산출 — 정산 단가 미입력"
    assert result.annual_reducible_kwh > 0  # 감축량은 낸다
    # **차단은 한 줄이다** (22세션 1절). 단가 둘이 다 없으면 한 문장으로 묶는다.
    blocked = [item for item in result.notices if item.fact == "dr.no_price"]
    assert len(blocked) == 1, [item.text for item in blocked]
    assert "순편익가격" in blocked[0].text
    assert "정산 단가 · 하루전에너지가격을 입력하지 않아" in blocked[0].text
    assert "금액과 위약금 리스크를 산출하지 않았습니다" in blocked[0].text
    # 조사가 어긋나지 않는다 — 「리스크을」 이 아니라 「리스크를」 이다.
    assert "리스크을" not in blocked[0].text


def test_단가가_있으면_정산금을_낸다(sample_diagnosis: Diagnosis) -> None:
    profile = sample_diagnosis.dr
    assert profile is not None
    result = evaluate_demand_response(profile, unit_price_won_per_kwh=150.0)
    assert result.settlement_won == pytest.approx(result.annual_reducible_kwh * 150.0)
    assert result.settlement_label != UNPRICED_REASON


def test_등록값을_바꾸면_감축량이_비례해_움직인다(sample_diagnosis: Diagnosis) -> None:
    """등록값과 감축량을 따로 놀게 두면 정산금이 근거를 잃는다."""
    profile = sample_diagnosis.dr
    assert profile is not None
    base = evaluate_demand_response(profile)
    half = evaluate_demand_response(profile, reduction_kw=profile.registered_capacity_kw / 2)
    assert half.registered_capacity_kw == pytest.approx(profile.registered_capacity_kw / 2)
    assert half.annual_reducible_kwh == pytest.approx(base.annual_reducible_kwh / 2)


def test_실적위약금은_별표26을_따른다() -> None:
    """실적위약금 = (감축계획량 − 실제감축량) × Max(하루전에너지가격, 0)."""
    assert shortfall_penalty_won(100.0, 60.0, 1.0, 120.0) == pytest.approx(4_800.0)
    assert shortfall_penalty_won(100.0, 60.0, 2.0, 120.0) == pytest.approx(9_600.0)
    assert shortfall_penalty_won(100.0, 100.0, 1.0, 120.0) == 0.0  # 계획을 채웠다
    assert shortfall_penalty_won(100.0, 130.0, 1.0, 120.0) == 0.0  # 넘겨도 0
    assert shortfall_penalty_won(100.0, 0.0, 1.0, -50.0) == 0.0  # 음수 가격은 0 으로


def test_위약금_리스크를_적는다(sample_diagnosis: Diagnosis) -> None:
    """투자비는 0원이지만 리스크는 0이 아니다. 1회 최대 지속시간 기준이다."""
    profile = sample_diagnosis.dr
    assert profile is not None
    result = evaluate_demand_response(profile, day_ahead_price_won_per_kwh=120.0)
    assert result.penalty_per_shortfall_kw_won == pytest.approx(120.0 * dr_event_hours()[1])
    assert any("실적위약금" in message for message in texts(result.notices))

    without = evaluate_demand_response(profile)
    assert without.penalty_per_shortfall_kw_won is None
    assert any("하루전에너지가격" in message for message in texts(without.notices))


def test_기본요금_절감을_주장하지_않는다(sample_diagnosis: Diagnosis) -> None:
    """SMP 기준 산발 입찰이라 연중 최대수요일과 겹칠 확률이 낮다. 편익은 정산금뿐이다."""
    profile = sample_diagnosis.dr
    assert profile is not None
    result = evaluate_demand_response(profile, unit_price_won_per_kwh=150.0)
    assert not hasattr(result, "base_saving_won")
    assert any("기본요금 절감은 계산하지 않았습니다" in note for note in texts(result.notices))


def test_수요관리사업자를_반드시_적는다(sample_diagnosis: Diagnosis) -> None:
    profile = sample_diagnosis.dr
    assert profile is not None
    joined = " ".join(texts(evaluate_demand_response(profile).notices))
    assert "수요관리사업자를 통해서만" in joined
    assert "위약금 조항은 사업자와 상담" in joined


def test_음수_감축계획량을_막는다(sample_diagnosis: Diagnosis) -> None:
    profile = sample_diagnosis.dr
    assert profile is not None
    with pytest.raises(ValueError, match="감축계획량"):
        evaluate_demand_response(profile, reduction_kw=-1.0)


def test_진단_한_벌에_들어_있다(sample_diagnosis: Diagnosis) -> None:
    """6.6 은 진단 한 벌에 들어 있다. 설비 정보를 묻지 않는다."""
    assert isinstance(sample_diagnosis.dr, DrProfile)
    assert sample_diagnosis.dr.eligible_days > 0


# --------------------------------------------------------------------- 산출물


def test_진단_시트에_DR_행이_실린다(
    sample_diagnosis: Diagnosis, sample_usage: UsageData, sample_bill: BillingResult
) -> None:
    """진단 시트에 거래 가능일·저부하 평일·등록 가능 용량이 실린다 (6.6)."""
    from kwise.report import ReportSections, build_sheets

    profile = sample_diagnosis.dr
    assert profile is not None
    sections = ReportSections(
        usage=sample_usage,
        bill=sample_bill,
        diagnosis=sample_diagnosis,
        include_timeseries=False,
    )
    values = build_sheets(sections)["진단"]["값"]
    assert values["DR 거래 가능일"] == f"{profile.eligible_days}일 / 전체 {profile.total_days}일"
    assert values["DR 제외일 (토·일·공휴일)"] == f"{profile.excluded_days}일"
    assert values["DR 저부하 평일"] == f"{profile.low_load_days_count}일"
    assert values["DR 적합성"] == str(profile.potential)
    assert "표준DR" in values["DR 자원 유형"]
    assert "6개월 입찰 제한" in values["DR 참여 안내"]


def test_수단_시트가_사유로_채워진다(sample_diagnosis: Diagnosis) -> None:
    """수단별 시트의 DR 행은 빈칸이 아니라 사유로 채워진다."""
    from kwise.report import measure_summary_frame

    profile = sample_diagnosis.dr
    assert profile is not None
    row = measure_summary_frame(demand_response=evaluate_demand_response(profile)).iloc[0]
    # 금액 칸은 전부 문자열이다 — 천 단위 절사 표기를 한 곳에서 찍는다 (14세션).
    assert row["투자비(원)"] == "0"
    assert row["절감액(원)"] == UNPRICED_REASON
    assert str(row["확실성"]) == str(Certainty.MEDIUM)
    assert "수요관리사업자를 통해서만" in row["비고"]
    assert "저부하 평일" in row["비고"]


def test_시장_시간대와_건물_운영_시간대를_가른다(sample_usage: UsageData, calendar: object) -> None:
    """**둘은 다른 값이다** (21세션 4절).

    시장 시간대는 제도가 정한 입찰 가능 시간(평일 09~12·13~20시)이고, 건물
    운영 시간대는 사람이 그 시간에 일하느냐다. 8시에 여는 곳은 8시부터 줄일
    것이 있지만, 시장이 9시에 열리므로 입찰은 9시부터다.
    """
    from kwise.diagnose.dr import overlap_windows

    market = dr_market_windows()
    assert market == ((9, 12), (13, 20)), "시장 시간대는 제도 규정이다."
    assert overlap_windows(market, (8, 19)) == ((9, 12), (13, 19))
    assert overlap_windows(market, (9, 18)) == ((9, 12), (13, 18))
    # 겹치는 구간이 없으면 시장 시간대로 되돌린다 — 창이 비면 진단이 멈춘다.
    assert overlap_windows(market, (0, 6)) == market

    early = dr_profile(
        sample_usage.kw,
        15,
        calendar,  # type: ignore[arg-type]
        contract_type="general_b",
        operating_hours=(8, 20),
    )
    late = dr_profile(
        sample_usage.kw,
        15,
        calendar,  # type: ignore[arg-type]
        contract_type="general_b",
        operating_hours=(9, 18),
    )
    assert early.windows == ((9, 12), (13, 20))
    assert late.windows == ((9, 12), (13, 18))
    assert early.weekend_baseline_kw != late.weekend_baseline_kw


# ===================================================================== 29세션 · 공휴일 보정


def test_공휴일_라이브러리가_못_잡는_날이_있다() -> None:
    """**저부하 평일로 잡힌 두 날이 실은 쉬는 날이었다** (29세션).

    자동 판정을 늘리지 않는 이유가 여기 있다 — 하나는 라이브러리가 아예 모르고,
    하나는 우리가 요금표 관행에 맞춰 일부러 뺀 것이다.
    """
    import datetime as dt

    import holidays

    from kwise.diagnose.dr import LIBRARY_HOLIDAY_GAPS

    korea = holidays.country_holidays("KR", years=[2023, 2026])
    # ① 근로자의 날 — 2025년까지는 목록에 없고 2026년부터 잡힌다.
    assert korea.get(dt.date(2023, 5, 1)) is None
    assert korea.get(dt.date(2026, 5, 1)) is not None
    # ② 임시공휴일 — 라이브러리는 알지만 요금표 관행에 맞춰 달력에서 뺀다.
    assert korea.get(dt.date(2023, 10, 2)) == "임시공휴일"
    calendar = build_calendar([2023])
    assert not calendar.is_holiday(dt.date(2023, 10, 2))
    assert dt.date(2023, 10, 2) in calendar.excluded_temporary
    # 사실을 코드에 남긴다 — 다음 사람이 같은 조사를 다시 하지 않도록.
    assert any("2026" in line for line in LIBRARY_HOLIDAY_GAPS)
    assert any("임시공휴일" in line for line in LIBRARY_HOLIDAY_GAPS)


def test_쉬는_날을_빼면_감축량이_다시_계산된다(sample_usage: UsageData, calendar: object) -> None:
    """**빼기만 하는 것이 아니다** (29세션).

    그 날은 거래 가능일에서 빠지고 **기준선 모집단(쉬는 날)으로 옮겨 간다** —
    기준선과 문턱이 함께 움직여야 나머지 날의 판정도 일관된다.
    """

    def profile(*off: str) -> object:
        return dr_profile(
            sample_usage.kw,
            15,
            calendar,  # type: ignore[arg-type]
            contract_type="general_b",
            off_days=off,
        )

    full = profile()
    assert full.low_load_days_count == 2  # type: ignore[attr-defined]
    assert full.annual_reducible_kwh > 0  # type: ignore[attr-defined]

    one = profile("2023-05-01")
    assert one.low_load_days_count == 1  # type: ignore[attr-defined]
    assert one.eligible_days == full.eligible_days - 1  # type: ignore[attr-defined]
    assert one.annual_reducible_kwh < full.annual_reducible_kwh  # type: ignore[attr-defined]
    # 기준선도 다시 잰다 — 쉬는 날이 하나 늘었으므로 평균이 움직인다.
    assert one.weekend_baseline_kw != full.weekend_baseline_kw  # type: ignore[attr-defined]

    both = profile("2023-05-01", "2023-10-02")
    assert both.low_load_days_count == 0  # type: ignore[attr-defined]
    assert both.annual_reducible_kwh == 0.0  # type: ignore[attr-defined]
    assert both.registered_capacity_kw == 0.0  # type: ignore[attr-defined]
    # 무엇을 뺐는지 근거로 남는다 — 산출물에서도 되짚을 수 있어야 한다.
    reasons = [item.text for item in both.notices if item.fact == "dr.user_off_days"]  # type: ignore[attr-defined]
    assert len(reasons) == 1
    assert "2023-05-01" in reasons[0] and "2023-10-02" in reasons[0]


def test_날짜_꼴이_섞여도_같은_날로_본다(sample_usage: UsageData, calendar: object) -> None:
    """화면은 ``date``, 세션은 문자열, 프로파일은 ``Timestamp`` 를 쓴다 (29세션)."""
    import datetime as dt

    from kwise.diagnose.dr import normalized_days

    assert normalized_days(["2023-05-01"]) == normalized_days([dt.date(2023, 5, 1)])
    assert normalized_days([pd.Timestamp("2023-05-01 13:45")]) == normalized_days(["2023-05-01"])

    text = dr_profile(
        sample_usage.kw, 15, calendar, contract_type="general_b", off_days=("2023-05-01",)
    )
    date = dr_profile(
        sample_usage.kw,
        15,
        calendar,
        contract_type="general_b",
        off_days=(dt.date(2023, 5, 1),),
    )
    assert text.annual_reducible_kwh == date.annual_reducible_kwh
