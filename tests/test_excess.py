"""초과사용부가금 (기본공급약관 제67조의3 ③ · 109세션).

**뜨지 않는 갈래를 만들지 않는다** — 구간 여섯을 「표에 여섯 줄이 있다」 로
세지 않고, 구간마다 **실제로 서는 입력**을 지어 배수와 금액을 함께 본다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from kwise.diagnose.structure import charge_structure
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


def test_요금_구성이_합계와_맞는다(
    sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable
) -> None:
    """**기본(+역률) + 전력량 + 부가금 = 합계.** 접거나 빠뜨리면 여기서 뜬다."""
    options = BillingOptions(contract_kw=3_000.0)
    bill = calculate_bill(
        sample_usage,
        tariff,
        TariffSelection("general_b", "high_a", "I"),
        options=options,
        quality=sample_report,
    )
    structure = charge_structure(sample_usage, tariff, bill, options=options)
    assert structure.excess_won > 0
    # **부가금은 기본요금에 안 접는다** — 「기본요금 = 요금적용전력 × 단가 ×
    # 개월수」 라는 각주가 덱마다 따라다닌다.
    assert structure.base_with_power_factor_won == pytest.approx(
        bill.total_base_won + bill.total_power_factor_won
    )
    assert (
        structure.base_with_power_factor_won + structure.energy_won + structure.excess_won
    ) == pytest.approx(structure.total_won)


#: 월별 명세가 **합계를 설명하려면** 실어야 하는 요금 열 (S129 2절).
#: 넷을 더하면 ``total_won`` 이다 — 하나라도 빠지면 부분의 합이 합계에 못 미친다.
SPEC_CHARGE_COLUMNS = ("base_won", "power_factor_won", "excess_won", "energy_won")


def test_월별_명세의_부분_합이_합계와_맞는다(
    sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable
) -> None:
    """**화면과 Excel 의 월별 명세가 같은 넷을 싣는다** (S129 2절 · ②-32 이웃).

    S127 이 Word 요금 구조 표에서 고친 것과 같은 모양이 명세에도 있었다 —
    ``excess_won`` 열이 어디에도 없어 **기본 + 역률 + 전력량이 합계에 못
    미쳤다.** 금액을 여기 다시 적지 않는다. 부분을 더해 합계와 맞대기만 한다.
    """
    from kwise.ui.views.diagnose import SCREEN_MONTHLY_COLUMNS

    bill = calculate_bill(
        sample_usage,
        tariff,
        TariffSelection("general_b", "high_a", "I"),
        options=BillingOptions(contract_kw=3_000.0),
        quality=sample_report,
    )
    assert bill.total_excess_won > 0, "부가금이 서는 벌이어야 열이 빠진 것이 드러난다"

    monthly = bill.monthly
    parts = sum(float(monthly[name].sum()) for name in SPEC_CHARGE_COLUMNS)
    assert parts == pytest.approx(float(monthly["total_won"].sum()))

    for name in SPEC_CHARGE_COLUMNS:
        assert name in SCREEN_MONTHLY_COLUMNS, f"화면 월별 명세에 {name} 이 없습니다."

    # Excel 쪽은 열 목록이 소스에 있다 — `test_월별_명세는_결론_열만_낸다` 와 같은 꼴.
    source = (Path("src") / "kwise" / "report" / "excel.py").read_text(encoding="utf-8")
    detail = source[source.index('sheets["요금 계산 명세"]') :]
    detail = detail[: detail.index('sheets["수단별 결과"]')]
    for name in SPEC_CHARGE_COLUMNS:
        assert f'"{name}"' in detail, f"Excel 요금 계산 명세에 {name} 이 없습니다."


def _bar_gap(
    usage: UsageData, report: QualityReport, table: TariffTable, contract_kw: float
) -> tuple[float, float]:
    """(막대 조각 합 − 합계, 부가금 총액). ``contract_kw`` 로 부가금을 켜고 끈다."""
    from kwise.report.frames import monthly_charge_frame

    options = BillingOptions(contract_kw=contract_kw)
    bill = calculate_bill(
        usage,
        table,
        TariffSelection("general_b", "high_a", "I"),
        options=options,
        quality=report,
    )
    structure = charge_structure(usage, table, bill, options=options)
    frame = monthly_charge_frame(structure)
    gap = float(frame["원"].sum()) - float(structure.monthly["total_won"].sum())
    return gap, bill.total_excess_won


def test_월별_요금_구성_막대의_조각_합이_합계와_맞는다(
    sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable
) -> None:
    """**재료 자리에서 한 번 문다** (S131 1절 · ②-32 의 뿌리 못).

    「부분 합 = 합계」 를 지키는 자리가 넷인데 셋은 자리마다 못이 섰고(화면·Excel
    월별 명세 S129 · Word 요금 구조 표 S124·S127) **막대만 열려 있었다.** 막대의
    재료는 :func:`~kwise.report.frames.monthly_charge_frame` **한 자리**이고
    화면(``ui\\charts.py``)과 PPT(``report\\figures.py``)가 둘 다 그것을 읽는다 —
    그래서 자리마다 못을 박지 않고 여기 하나를 박는다.

    **부가금 0 인 벌은 이미 맞는다** (``test_월별_요금_구성이_네_조각이다``).
    갈리는 것은 부가금이 서는 벌뿐이라 두 벌을 한 못이 문다.

    **S140 2절에 xfail 을 걷었다.** 막대가 부가금 조각을 세우면서 어긋남이
    122,451,200원(총액의 3.5%)에서 0 이 됐다. 못은 남는다 — 사실이 뒤집힌
    것이 아니라 고쳐진 것이라 그 자리를 계속 물어야 한다.
    """
    # **1원 미만으로 본다.** 34억을 float64 로 접었다 펴는 자리라 조각을 다
    # 담아도 부스러기가 남는다 (4.8e-07원). ``approx(0.0)`` 의 기본 절대오차는
    # 1e-12 라 그 부스러기에 걸린다 — 빠진 조각(1억 2,245만원)과는 열네 자리
    # 떨어져 있어 이 잣대로도 걸러진다.
    #
    # ① 부가금 0 인 기본 벌 — 지금도 맞는다.
    gap, excess = _bar_gap(sample_usage, sample_report, tariff, 12_000.0)
    assert excess == 0.0, "이 벌은 부가금이 0 이어야 한다"
    assert gap == pytest.approx(0.0, abs=1.0)

    # ② 부가금이 서는 벌 — 조각에 ``excess_won`` 이 있어야 합계와 맞는다.
    gap, excess = _bar_gap(sample_usage, sample_report, tariff, 3_000.0)
    assert excess > 0, "부가금이 서는 벌이어야 빠진 조각이 드러난다"
    assert gap == pytest.approx(0.0, abs=1.0)


def test_요금제별_그룹_막대의_조각_합이_합계와_맞는다(
    sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable
) -> None:
    """**합계가 조각 옆에 서는 자리다** (S140 1-3).

    막대를 쌓지 않고 나란히 세우므로 읽는 사람이 조각을 더해 합계 막대와
    맞댄다. 앞서 조각이 ``base_won``(역률 뺀 값) + ``energy_won`` 둘뿐이라
    **역률요금과 부가금이 통째로 빠져 있었다** — `large-b-short` 조건에서
    선택Ⅰ 122,451,200원 · 선택Ⅲ 166,377,600원, 역률 85% 조건에서 선택Ⅰ
    6,339,264원이다.

    **막대 못과 따로 둔다** — 재료가 다르다
    (:func:`~kwise.report.frames.tariff_option_frame`).
    """
    from kwise.measures.tariff_switch import evaluate_tariff_switch
    from kwise.report.frames import tariff_option_frame, tariff_parts

    for contract_kw, power_factor_pct, wants in ((3_000.0, None, True), (12_000.0, 85.0, False)):
        options = BillingOptions(contract_kw=contract_kw, power_factor_pct=power_factor_pct)
        switch = evaluate_tariff_switch(
            sample_usage,
            tariff,
            TariffSelection("general_b", "high_a", "I"),
            options=options,
            quality=sample_report,
        )
        parts = tariff_parts(switch)
        assert ("초과사용부가금" in parts) is wants, (
            f"계약 {contract_kw:,.0f} kW — 부가금 조각이 서는 조건과 어긋납니다: {parts}"
        )
        frame = tariff_option_frame(switch)
        for _, row in frame.iterrows():
            total = float(row["합계(원)"])
            pieces = sum(float(row[f"{name}(원)"]) for name in parts if name != "합계")
            assert pieces == pytest.approx(total), (
                f"{row['요금제']} — 조각 합이 합계와 {pieces - total:,.0f}원 어긋납니다."
            )
