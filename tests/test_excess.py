"""초과사용부가금 (기본공급약관 제67조의3 ③ · 109세션).

**뜨지 않는 갈래를 만들지 않는다** — 구간 여섯을 「표에 여섯 줄이 있다」 로
세지 않고, 구간마다 **실제로 서는 입력**을 지어 배수와 금액을 함께 본다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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


def test_안_쟀다는_사실이_실물_문구에_선다(
    sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable
) -> None:
    """**「0원」 과 「안 쟀다」 를 화면이 가른다** (S142 3절).

    ``applicable`` 은 그 사실을 들고 있었으나 **읽는 자리가 `src\\` 에 0곳**이라
    산출물 여섯이 전부 금액의 진위만 봤다 — 초과가 나는데 안 잰 벌과 초과가
    없어 정말 0원인 벌이 한 글자도 다르지 않았다 (S142 2절이 값으로 봤다).
    결함 유형 ②(뜨지 않는 경고는 없는 경고와 같다).

    **말하는 자리는 `quality.over_contract` 안내 하나다.** 여기서 무는 것은
    ① 그 갈래에서 실제로 뜨는가 · ② 「산출하지 않았다」 를 적는가 ·
    ③ 요금적용전력 기준 갈래의 같은 사실 ID 와 **다른 말을 하는가** 셋이다.
    """
    contract_kw = 3_000.0

    def _over_contract(selection: TariffSelection) -> str:
        bill = calculate_bill(
            sample_usage,
            tariff,
            selection,
            options=BillingOptions(contract_kw=contract_kw),
            quality=sample_report,
        )
        texts = [n.text for n in bill.notices if n.fact == "quality.over_contract"]
        assert len(texts) == 1, f"{selection} 에서 안내가 {len(texts)}개다 — {texts}"
        return texts[0]

    # ① 계약전력 기준 종별 — 안 쟀다고 적는다.
    on_contract = _over_contract(TariffSelection("general_a_1", "high_a", "I"))
    assert "산출하지 않았습니다" in on_contract
    assert "총액에 안 들어 있습니다" in on_contract

    # ② 요금적용전력 기준 종별 — 금액을 총액에 넣었다고 적는다.
    on_demand = _over_contract(TariffSelection("general_b", "high_a", "I"))
    assert "청구 총액에 넣었습니다" in on_demand

    # ③ **둘이 같은 말을 하면 안 된다.** 같아지는 순간 화면은 다시 못 가른다.
    assert on_contract != on_demand


#: 「안 쟀다」 를 가르는 꼬리. **앞머리로 견주지 않는다** — 「초과사용부가금
#: 대상」 까지는 두 갈래가 같은 말을 한다.
NOT_MEASURED_TAIL = "총액에 안 들어 있습니다"


def _rendered(
    usage: UsageData,
    report: QualityReport,
    table: TariffTable,
    selection: TariffSelection,
    contract_kw: float,
) -> dict[str, str]:
    """{산출물 이름: 그려진 글자}. **실물을 그려서 본다.**"""
    from kwise.diagnose import ContractInfo, diagnose
    from kwise.report.document import DocumentSections, build_document
    from kwise.report.slides import build_slides

    options = BillingOptions(contract_kw=contract_kw)
    bill = calculate_bill(usage, table, selection, options=options, quality=report)
    diagnosis = diagnose(
        usage,
        table,
        ContractInfo(selection, contract_kw=contract_kw),
        quality=report,
        options=options,
    )
    sections = DocumentSections(usage=usage, bill=bill, diagnosis=diagnosis)
    deck = build_slides(sections)
    document = build_document(sections)
    return {
        # 표 칸도 담는다 (S248) — 요금 구조 표의 부가금 줄은 문단이 아니라 표에 선다.
        "Word": "\n".join(
            [item.text for item in document.paragraphs]
            + [cell.text for table in document.tables for row in table.rows for cell in row.cells]
        ),
        "PPT": "\n".join(
            shape.text_frame.text
            for slide in deck.slides
            for shape in slide.shapes
            if shape.has_text_frame
        ),
    }


def test_안_쟀다는_사실이_PPT_와_Word_에_선다(
    sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable
) -> None:
    """**두 벌이 열 자리에서 한 글자도 안 달랐다** (S143 1절).

    부가금이 서는지를 산출물 열 자리가 **금액의 진위**로 가른다 — 초과가 나는데
    안 잰 벌(계약전력 기준 종별)과 초과가 없어 정말 0원인 벌은 그 값이 둘 다
    0원이라 **갈릴 수가 없다.** S142 가 화면과 Excel 을 갈랐고 PPT·Word 는
    안내를 통째로 싣는 자리가 없어 남아 있었다 (결함 유형 ②).

    무는 것은 셋이다 — ① 안 잰 벌에서 그 문장이 두 산출물에 실제로 **뜨는가** ·
    ② 0원인 벌에는 **안 뜨는가** · ③ 두 벌의 글자가 그 자리에서 **다른가**.
    """
    not_measured = _rendered(
        sample_usage, sample_report, tariff, TariffSelection("general_a_1", "high_a", "I"), 3_000.0
    )
    really_zero = _rendered(
        sample_usage, sample_report, tariff, TariffSelection("general_b", "high_a", "I"), 6_000.0
    )
    for name in ("Word", "PPT"):
        assert NOT_MEASURED_TAIL in not_measured[name], (
            f"{name} 가 갑Ⅰ 3,000 kW 벌에서 「안 쟀다」 를 안 적는다."
        )
        assert NOT_MEASURED_TAIL not in really_zero[name], (
            f"{name} 가 을 6,000 kW 벌(초과 0)에서 「안 쟀다」 를 적는다."
        )
        assert not_measured[name] != really_zero[name], f"{name} 에서 두 벌의 글자가 같다."


#: 제67조의3 ① 1호 표 — (초과횟수 하한, 배수). **조문에서 옮겼다** — 기준 데이터를
#: 읽어 기대값을 만들면 기준 데이터가 틀려도 초록이다. 첫 번째 초과는 예고라 표에 없다.
FIRST_CLAUSE_TABLE = ((2, 1.5), (4, 2.0), (6, 2.5))


def _first_clause_expected(bill_monthly: pd.DataFrame, contract_kw: float, rate: float) -> float:
    """조문 식 그대로 — 초과한 달을 차례로 세어 (최대수요 − 계약전력) × 단가 × 배수."""
    total, count = 0.0, 0
    for peak in bill_monthly["max_demand_kw"]:
        if not peak > contract_kw:
            continue
        count += 1
        multiplier = max(
            (value for floor, value in FIRST_CLAUSE_TABLE if count >= floor), default=0.0
        )
        total += (peak - contract_kw) * rate * multiplier
    return total


def test_저압_계약형_20kW_이상은_조문_식대로_부가금을_세고_고압은_산출하지_않는다(
    sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable
) -> None:
    """**S248 · 사람 결정 마-24 · 웹 대화창 결정 1.** 제67조의3 ① 1호 — 저압 계약전력
    20 kW 이상(세칙 제48조의2 ① 5호)은 초과전력 × 기본요금 단가 × 초과횟수 배수로 세어
    청구 총액에 싣는다. 고압 계약형과 저압 20 kW 미만은 2호라 「산출하지 않았다」 그대로다.

    값은 조문 식으로 따로 세고(``FIRST_CLAUSE_TABLE``), 흐름은 다른 종별의 꼴(총액 · 안내 ·
    PPT · Word)을 산출물을 실제로 그려 본다.
    """
    from kwise.report.notices import excess_not_measured_line

    low = TariffSelection("industrial_a_1", "low", "single")
    rate = tariff.rates(low).base_won_per_kw
    contract_kw = 3_000.0
    bill = calculate_bill(
        sample_usage, tariff, low, options=BillingOptions(contract_kw=contract_kw),
        quality=sample_report,
    )
    expected = _first_clause_expected(bill.monthly, contract_kw, rate)
    # 재료 — 세 배수가 다 서는 벌이다(여섯 달 넘게 넘는다).
    assert (bill.monthly["max_demand_kw"] > contract_kw).sum() >= 6
    assert bill.excess.applicable is True
    assert expected > 0
    assert bill.total_excess_won == pytest.approx(expected)
    assert {item.multiplier for item in bill.excess.months} == {0.0, 1.5, 2.0, 2.5}
    assert [item.charged for item in bill.excess.months][:2] == [False, True]  # 첫 달 예고
    assert bill.total_won == pytest.approx(
        bill.total_base_won + bill.total_power_factor_won + bill.total_energy_won + expected
    )
    said = [n.text for n in bill.notices if n.fact == "quality.over_contract"]
    assert len(said) == 1 and "청구 총액에 넣었습니다" in said[0], said
    assert excess_not_measured_line(bill) == ""

    # 산출물 — 「안 쟀다」 가 빠지고 부가금이 선다(③ 벌과 같은 꼴).
    rendered = _rendered(sample_usage, sample_report, tariff, low, contract_kw)
    for name in ("Word", "PPT"):
        assert NOT_MEASURED_TAIL not in rendered[name], name
        assert "초과사용부가금" in rendered[name], name

    # 넘지 않는 입력 — 0원이고 「안 쟀다」 도 아니다.
    calm = calculate_bill(
        sample_usage, tariff, low, options=BillingOptions(contract_kw=6_000.0),
        quality=sample_report,
    )
    assert (calm.excess.applicable, calm.total_excess_won) == (True, 0.0)

    # 경계 — 20 kW 이상이 1호, 미만은 2호(산출하지 않는다).
    assert calculate_bill(
        sample_usage, tariff, low, options=BillingOptions(contract_kw=20.0), quality=sample_report
    ).excess.applicable is True
    under = calculate_bill(
        sample_usage, tariff, low, options=BillingOptions(contract_kw=19.0), quality=sample_report
    )
    assert (under.excess.applicable, under.total_excess_won) == (False, 0.0)

    # 고압 계약형 — 2호 · 그대로 「산출하지 않았다」.
    high = calculate_bill(
        sample_usage, tariff, TariffSelection("industrial_a_1", "high_a", "I"),
        options=BillingOptions(contract_kw=contract_kw), quality=sample_report,
    )
    assert (high.excess.applicable, high.total_excess_won) == (False, 0.0)
    assert NOT_MEASURED_TAIL in excess_not_measured_line(high)

    # 배수는 기준 데이터에서 읽는다 — 조문 표와 같아야 한다.
    from kwise.tariff.excess import excess_count_multiplier

    assert [excess_count_multiplier(n) for n in range(1, 8)] == [0.0, 1.5, 1.5, 2.0, 2.0, 2.5, 2.5]


def test_부가금_경고는_호마다_조문_문턱을_댄다(
    sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable
) -> None:
    """**S248 · 웹 대화창 결정 2.** 제67조의3 ① 1호는 「최대수요전력이 계약전력을 초과」,
    2호는 「사용전력량이 계약전력 1㎾마다 월간 450㎾h를 초과」 가 문턱이다. 앞서 계약형
    경고는 2호 갈래(고압 계약형)에도 1호 문턱을 댔다.

    갑Ⅰ 고압A 5,250 kW — 관측 최대(5,293 kW)는 넘지만 어느 달도 450 kWh/kW 를 안 넘어
    **안 선다.** 3,000 kW — 450 을 넘는 달이 있어 선다 · 문턱 조각이 조문 글자다.
    저압 3,000 kW — 1호라 최대수요 문턱으로 선다.
    """
    high = TariffSelection("general_a_1", "high_a", "I")

    def over_contract(selection: TariffSelection, contract_kw: float) -> tuple[list[str], Any]:
        bill = calculate_bill(
            sample_usage, tariff, selection, options=BillingOptions(contract_kw=contract_kw),
            quality=sample_report,
        )
        return [n.text for n in bill.notices if n.fact == "quality.over_contract"], bill

    said, bill = over_contract(high, 5_250.0)
    per_kw = bill.monthly["total_kwh"] / 5_250.0
    assert bill.monthly["max_demand_kw"].max() > 5_250.0  # 재료 — 1호 문턱은 넘는다
    assert not (per_kw > 450.0).any()  # 재료 — 2호 문턱은 안 넘는다
    assert said == []

    said, bill = over_contract(high, 3_000.0)
    assert ((bill.monthly["total_kwh"] / 3_000.0) > 450.0).any()  # 재료
    assert len(said) == 1
    assert "사용전력량이 계약전력 1 kW마다 월간 450 kWh 를 넘습니다" in said[0]
    assert "관측 최대수요" not in said[0]
    assert NOT_MEASURED_TAIL in said[0]

    said, _ = over_contract(TariffSelection("industrial_a_1", "low", "single"), 3_000.0)
    assert len(said) == 1 and "관측 최대수요" in said[0]
    assert "계약전력 3,000 kW 를 넘습니다" in said[0]


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
