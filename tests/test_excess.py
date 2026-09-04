"""초과사용부가금 (기본공급약관 제67조의3 ③ · 109세션).

**뜨지 않는 갈래를 만들지 않는다** — 구간 여섯을 「표에 여섯 줄이 있다」 로
세지 않고, 구간마다 **실제로 서는 입력**을 지어 배수와 금액을 함께 본다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from kwise.io import UsageData
from kwise.quality import QualityReport
from kwise.tariff import (
    BillingOptions,
    TariffSelection,
    TariffTable,
    calculate_bill,
    excess_charges,
    excess_grace_months,
    excess_multiplier,
    excess_tiers,
)

CONTRACT_KW = 1_000.0
RATE = 8_000.0  # 기본요금 단가 (원/kW). 값이 아니라 곱해지는지를 본다

#: (초과비율, 배수). **구간 여섯이 각각 한 줄씩이다.**
TIER_CASES = (
    (0.10, 1.5),
    (0.25, 2.0),
    (0.35, 2.5),
    (0.45, 3.0),
    (0.55, 3.5),
    (0.70, 4.0),
)


def _months(ratios: list[float]) -> dict[pd.Period, float]:
    """2024-01 부터 한 달에 하나씩. 값은 계약전력 × (1 + 초과비율)."""
    return {
        pd.Period("2024-01", freq="M") + i: CONTRACT_KW * (1.0 + ratio)
        for i, ratio in enumerate(ratios)
    }


def test_구간_여섯이_각각_선다() -> None:
    """**금액까지 본다.** 배수만 보면 곱하는 자리가 틀려도 초록이다."""
    # 맨 앞의 달은 예고에 쓴다 (제4항). 그래야 뒤 여섯이 모두 **청구**된다.
    ratios = [0.05, *(ratio for ratio, _ in TIER_CASES)]
    charge = excess_charges(
        _months(ratios), contract_kw=CONTRACT_KW, base_rate_won_per_kw=RATE
    )

    assert len(charge.exceeded_months) == 7
    assert len(charge.charged_months) == 6
    assert charge.months[0].charged is False

    for item, (ratio, multiplier) in zip(charge.months[1:], TIER_CASES, strict=True):
        assert item.excess_ratio == pytest.approx(ratio)
        assert item.multiplier == multiplier
        assert item.won == pytest.approx(CONTRACT_KW * ratio * RATE * multiplier)

    assert {item.multiplier for item in charge.months[1:]} == {1.5, 2.0, 2.5, 3.0, 3.5, 4.0}


def test_첫_초과_달은_예고뿐이고_두_번째_달부터_청구한다() -> None:
    """제67조의3 ④ · 시행세칙 제48조의2 ② 1."""
    charge = excess_charges(
        _months([0.5, 0.5, 0.5]), contract_kw=CONTRACT_KW, base_rate_won_per_kw=RATE
    )
    assert [item.charged for item in charge.months] == [False, True, True]
    assert charge.months[0].won == 0.0
    # **「초과한 달」 과 「청구되는 달」 은 다른 사실이다.**
    assert len(charge.exceeded_months) == 3
    assert charge.charged_months == charge.exceeded_months[1:]
    assert charge.total_won == pytest.approx(2 * CONTRACT_KW * 0.5 * RATE * 3.5)
    assert excess_grace_months() == 1


def test_경계에서_부동소수_부스러기를_턴다() -> None:
    """계약 103.0 kW · 최대 123.6 kW 는 나눗셈이 0.19999999999999996 이다.

    **턴 값으로 판정하지 않으면 20% 구간이 아니라 그 아래로 떨어진다** —
    배수가 200% 에서 150% 로 갈린다.
    """
    assert (123.6 - 103.0) / 103.0 < 0.2  # 부스러기가 실제로 난다
    assert excess_multiplier((123.6 - 103.0) / 103.0) == 2.0
    charge = excess_charges(
        {pd.Period("2024-01", freq="M"): 123.6, pd.Period("2024-02", freq="M"): 123.6},
        contract_kw=103.0,
        base_rate_won_per_kw=RATE,
    )
    assert charge.months[1].multiplier == 2.0


def test_계약전력을_모르면_0원이_아니라_산출하지_않는다() -> None:
    """``applicable`` 이 둘을 가른다. 섞으면 산출물이 0 을 두 뜻으로 말한다."""
    charge = excess_charges(
        _months([0.5, 0.5]), contract_kw=None, base_rate_won_per_kw=RATE
    )
    assert charge.applicable is False
    assert charge.total_won == 0.0
    assert charge.exceeded_months == ()


def test_구간표를_기준_데이터에서_읽는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """**코드에 박으면 이 시험이 뜬다.**"""
    assert len(excess_tiers()) == 6
    monkeypatch.setattr(
        "kwise.tariff.excess.rule_value",
        lambda key: [[0.0, 9.0]] if key == "excess_charge.ratio_tiers" else 1,
    )
    assert excess_multiplier(0.7) == 9.0


def test_결측이_있는_달도_보간하지_않고_관측값으로_판정한다() -> None:
    """자료가 없는 달은 ``NaN`` 으로 온다 — 초과로도 미초과로도 세지 않는다."""
    months = _months([0.5, 0.5, 0.5])
    keys = sorted(months)
    months[keys[1]] = float("nan")
    charge = excess_charges(months, contract_kw=CONTRACT_KW, base_rate_won_per_kw=RATE)
    assert charge.exceeded_months == (keys[0], keys[2])
    assert charge.months[0].charged is False  # 예고는 남은 첫 달에 붙는다


# --------------------------------------------------------------------- 엔진


def test_엔진이_부가금을_청구_총액에_싣는다(
    sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable
) -> None:
    """**총액에 실려 있어야 한다.** 108세션까지 이 몫이 한 원도 없었다."""
    bill = calculate_bill(
        sample_usage,
        tariff,
        TariffSelection("general_b", "high_a", "I"),
        options=BillingOptions(contract_kw=3_000.0),
        quality=sample_report,
    )
    assert bill.excess.applicable is True
    assert bill.total_excess_won > 0
    assert bill.total_excess_won == pytest.approx(float(bill.monthly["excess_won"].sum()))
    assert bill.total_won == pytest.approx(
        bill.total_base_won
        + bill.total_power_factor_won
        + bill.total_energy_won
        + bill.total_excess_won
    )
    # 첫 초과 달은 예고뿐이라 표에서도 0 이다.
    first = bill.excess.months[0].month
    assert float(bill.monthly.loc[first, "excess_won"]) == 0.0


def test_계약전력_기준_종별에는_이_구간표를_쓰지_않는다(
    sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable
) -> None:
    """제68조 ② 고객은 제67조의3 **제1항**이라 구간이 다르다 — 산출하지 않는다."""
    bill = calculate_bill(
        sample_usage,
        tariff,
        TariffSelection("general_a_1", "high_a", "I"),
        options=BillingOptions(contract_kw=3_000.0),
        quality=sample_report,
    )
    assert bill.excess.applicable is False
    assert bill.total_excess_won == 0.0
