r"""「기본요금」 이라는 이름이 어느 금액을 가리키는가 (S141 3절).

**사람이 정했다 — 「기본요금」 은 역률 가감이 반영된 뒤의 금액이다.** 근거 셋 —
한전 고지서의 기본요금 칸이 그 몫이라 고객이 청구서와 맞대 본다 · 역률 개선
카드가 그 기준 위에 선다 (S133 이 약관 제41~43조로 검산) · 산식이 이미
``ChargeStructure.base_with_power_factor_share`` 한 자리에 모여 있다.

**이 못이 서는 까닭.** ``tools\fact_sites.json`` 의 「「기본요금」 금액」 그물은
**자리가 몇인지**를 셀 뿐 **그 자리가 어느 쪽 금액을 내는지**는 안 문다. 정의가
다시 갈리면 그물은 조용하고 이 시험만 빨개진다.

**벌은 역률 85% 하나뿐이다.** 저장소의 케이스 여덟과 덱 벌 열셋이 전부 약관
제42조 간주값(92%)이라 **역률요금이 0원**이고, 그러면 접기 전과 후가 같은 값이라
**어긋남이 뜰 수가 없다** (결함 유형 ② — 뜨지 않는 갈래는 없는 갈래와 같다).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from kwise.diagnose import Diagnosis, diagnose
from kwise.diagnose.contract import ContractInfo
from kwise.io import UsageData
from kwise.money import truncate_won
from kwise.quality import QualityReport
from kwise.report.casestudy import CaseDefinition, CaseResult
from kwise.report.excel import ReportSections, _summary_rows
from kwise.report.narrative import terms
from kwise.report.worksheet import _base_fee_row
from kwise.tariff import (
    BillingOptions,
    BillingResult,
    TariffSelection,
    TariffTable,
    calculate_bill,
)

CURRENT = TariffSelection("general_b", "high_a", "I")
#: `render_deck.py` 의 `large-b-pf85` 와 같은 조건 (S132 2절).
CONTRACT_KW = 6_000.0
POWER_FACTOR_PCT = 85.0


@pytest.fixture(scope="module")
def pf85(sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable) -> Diagnosis:
    options = BillingOptions(contract_kw=CONTRACT_KW, power_factor_pct=POWER_FACTOR_PCT)
    return diagnose(
        sample_usage,
        tariff,
        ContractInfo(CURRENT, contract_kw=CONTRACT_KW),
        quality=sample_report,
        options=options,
    )


def _won_of(rows: list[tuple[str, str, str]], item: str) -> float:
    """요약 시트 한 줄의 금액. **없는 줄이면 그 자리에서 죽는다.**"""
    for _group, name, text in rows:
        if name == item:
            return float(text.replace(",", "").replace(" 원", ""))
    raise AssertionError(f"요약 시트에 「{item}」 줄이 없습니다.")


def test_산출물_넷의_기본요금이_같은_금액이다(
    sample_usage: UsageData, sample_report: QualityReport, pf85: Diagnosis
) -> None:
    """화면·PPT·Word · Excel 요약 · 케이스 스터디가 한 금액을 적는다.

    앞서 **Excel 요약 시트와 케이스 스터디 xlsx 둘만 역률을 안 접었다**
    (129세션이 값으로 보고 이름을 올린 자리). 이 조건에서 역률요금이
    6,339,264원이라 452,804,556 대 459,143,820원으로 갈렸다.

    화면·PPT·Word 셋은 ``ChargeStructure.base_with_power_factor_won`` 하나를
    읽으므로 **그 속성을 여기서 대표로 문다** — 셋이 각자 나누거나 더하면
    S133·S140 이 모은 자리가 다시 흩어진다.
    """
    structure = pf85.structure
    assert structure is not None
    bill = structure.bill
    assert bill.total_power_factor_won > 0, "역률요금이 서는 벌이어야 어긋남이 뜬다"

    want = bill.base_with_power_factor_won
    assert want == pytest.approx(bill.total_base_won + bill.total_power_factor_won)

    # ① 화면 · PPT · Word — 셋이 함께 읽는 재료.
    assert structure.base_with_power_factor_won == pytest.approx(want)

    # ② Excel 요약 시트.
    rows = _summary_rows(ReportSections(usage=sample_usage, bill=bill, diagnosis=pf85))
    assert _won_of(rows, "기본요금") == pytest.approx(want, abs=1_000.0)

    # ③ 케이스 스터디 xlsx 의 요약 행.
    row = _case_result(sample_usage, sample_report, pf85).summary_row()
    assert float(row["기본요금(원)"]) == pytest.approx(want)


def test_요약_시트의_조각_합이_합계와_맞는다(sample_usage: UsageData, pf85: Diagnosis) -> None:
    """**역률을 두 번 세지 않는다.**

    요약 시트는 기본요금·전력량요금·초과사용부가금을 줄로 세우고 그 아래
    「합계 (관측 기준)」 을 적는다 — 읽는 사람이 더해 맞대는 자리다. 기본요금이
    역률을 담게 됐으므로 **역률요금 줄을 따로 세우면 합이 6,339,264원 넘친다.**
    조각 구성을 화면·PPT·Word 셋과 같게 두는 것이 그 장치다.

    **금액은 천 원 단위로 절사해 적는다** (14세션) — 조각마다 최대 999원이
    깎이므로 넷을 더하면 3,996원까지 벌어질 수 있다. 빠진 조각(역률요금
    6,339,264원)과는 세 자리 떨어져 있어 이 잣대로도 걸러진다.
    """
    structure = pf85.structure
    assert structure is not None
    rows = _summary_rows(ReportSections(usage=sample_usage, bill=structure.bill, diagnosis=pf85))
    names = {name for _group, name, _text in rows}
    assert "역률요금" not in names, "역률요금 줄이 서면 합계가 역률을 두 번 셉니다."

    parts = sum(_won_of(rows, name) for name in ("기본요금", "전력량요금") if name in names) + (
        _won_of(rows, "초과사용부가금") if "초과사용부가금" in names else 0.0
    )
    total = _won_of(rows, "합계 (관측 기준)")
    assert parts == pytest.approx(total, abs=4_000.0), (
        f"조각 합이 합계와 {parts - total:,.0f}원 어긋납니다."
    )


def _case_result(usage: UsageData, quality: QualityReport, diagnosis: Diagnosis) -> CaseResult:
    """케이스 스터디 결과 한 벌. **시계열 계산은 안 돌린다** — 요약 행만 본다."""
    structure = diagnosis.structure
    assert structure is not None
    return CaseResult(
        definition=CaseDefinition(
            key="PF85",
            name="역률 85%",
            usage_path=Path(usage.meta.source_name),
            contract_type="general_b",
        ),
        usage=usage,
        quality=quality,
        diagnosis=diagnosis,
        baseline=structure.bill,
        contract_kw=CONTRACT_KW,
        pv_rows=(),
        sensitivity_rows=(),
        selection_rows=(),
        measure_rows=(),
        ess=None,
        elapsed_sec=0.0,
        weather_source="시험",
    )


# ------------------------------------------------------------------ S149 1절
#
# **산식 문장에 값을 문다.** 「기본요금 = 기준전력 × 단가 × 개월수」 가 두
# 자리에서 산출물 넷에 뜨는데(`narrative.py` 의 용어 각주 — 화면 툴팁·PPT 각주 ·
# `worksheet.py` 의 계산 근거 줄 — 화면·Excel·Word 부록 A) **값을 무는 시험이
# 0곳이었다** (S148 3절). 닿는 시험 둘은 꼴의 조각(`"kW ×"`·`"원/kW"`)과
# 이름(「기본요금 = 」)만 보므로 **항 하나를 빼도 전부 초록이다** — S147 이
# 「× 개월수」 를 빼고 1,692건이 통째로 통과하는 것을 값으로 봤다.
#
# **식을 여기에 옮겨 적지 않는다.** 곱셈·덧셈의 차례와 항의 이름은 **문장에서
# 읽고**, 값은 엔진이 낸 것을 부른다. 시험이 제 식을 들고 있으면 실물이 갈려도
# 제 식으로 통과한다 (S118 이 `.map(round_kw)` 를 손으로 넣은 자국).

#: 산식 문장이 서는 벌 넷. **성격 셋을 남긴다** — 요금적용전력 기준 종별 ·
#: 계약전력 기준 종별(고압·저압 둘) · 역률이 제42조 간주값(92%)이 아닌 벌.
#:
#: 앞 둘은 `render_deck.py` 의 `large-b`·`large-a` 와 같은 조건이고(자료도
#: 같은 `input\사용량조회_20240429.csv` 다), 셋째는 화면 감사 조건 「교육갑저압」,
#: 넷째는 `large-b-pf85` 다. **계약전력 기준을 반드시 넣는다** — 그 종별에서는
#: 기본요금을 요금적용전력이 아니라 계약전력이 매기므로 두 열이 갈리고,
#: `large-a` 에서 66,571,213원 차다 (S139).
BEDS: tuple[tuple[str, TariffSelection, float, float | None], ...] = (
    ("large-b", TariffSelection("general_b", "high_a", "I"), 6_000.0, None),
    ("large-a", TariffSelection("general_a_1", "high_a", "I"), 6_000.0, None),
    ("교육갑저압", TariffSelection("education_a", "low", "single"), 200.0, None),
    ("large-b-pf85", CURRENT, CONTRACT_KW, POWER_FACTOR_PCT),
)
BED_KEYS = tuple(bed[0] for bed in BEDS)

_NUMBER = r"[\d,]+(?:\.\d+)?"
#: 계산 근거 줄의 산식. **수를 잡을 뿐 식을 적지 않는다** — 세 수가 무엇을
#: 곱하는지는 문장이 정하고, 이 시험은 문장이 적은 그 수를 그대로 곱한다.
_WORKSHEET_FORMULA = re.compile(rf"월평균 ({_NUMBER}) kW × ({_NUMBER}) 원/kW × ({_NUMBER})개월")

#: 용어 각주의 낱말이 가리키는 엔진 값. **식이 아니라 사전이다** — 항의 차례와
#: 연산자는 각주 문장에서 읽는다. 모르는 낱말이 오면 그 자리에서 죽는다.
TERM_VALUES: dict[str, Callable[[BillingResult], float]] = {
    "월평균 기본요금 기준전력": lambda bill: bill.mean_base_demand_kw,
    "단가": lambda bill: bill.base_rate_won_per_kw,
    "개월수": lambda bill: bill.base_fee_months,
    "역률요금": lambda bill: bill.total_power_factor_won,
}


@pytest.fixture(scope="module")
def bills(
    sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable
) -> dict[str, BillingResult]:
    """벌 넷의 요금. **덱을 뽑지 않는다** — 산식 문장은 요금 하나에서 나온다."""
    return {
        key: calculate_bill(
            sample_usage,
            tariff,
            selection,
            options=BillingOptions(contract_kw=contract_kw, power_factor_pct=power_factor),
            quality=sample_report,
        )
        for key, selection, contract_kw, power_factor in BEDS
    }


@pytest.mark.parametrize("key", BED_KEYS)
def test_계산_근거의_기본요금_산식이_적힌_수로_그_줄의_금액을_낸다(
    bills: dict[str, BillingResult], key: str
) -> None:
    """**읽고 검산하면 맞는다.**

    이 줄이 말하는 금액은 ``total_base_won`` — **역률 조정 전**이다. 이름표가
    「역률 조정 전 기본요금」 이고 값 칸이 그 금액이며, S141 이 정한 「기본요금」
    (역률 가감 후)은 아래 「역률 요금」 줄을 더해야 나온다.

    **잣대는 천 원 자리다** (S139). 값 칸이 천 원 절사이고, 산식의 kW 가 `,.3f`
    라 곱은 최대 몇 원 어긋난다 — 넷 가운데 어긋나는 것은 `large-b`·
    `large-b-pf85` 의 3.61원뿐이고 나머지 둘은 0원이다.

    **도달은 여기서 안 문다** — 이 줄이 화면·Excel·Word 부록 A 에 같은 글자로
    닿는 것은 `test_document.py` 의 `test_계산_근거가_화면_Excel_Word_에서_같다`
    가 이미 문다. 여기는 그 글자가 참인지만 본다.
    """
    bill = bills[key]
    row = _base_fee_row(bill)
    match = _WORKSHEET_FORMULA.fullmatch(row.formula)
    assert match, f"{key}: 기본요금 산식 줄의 꼴이 갈렸습니다 — 「{row.formula}」"

    product = 1.0
    for text in match.groups():
        product *= float(text.replace(",", ""))
    shown = float(row.value.removesuffix("원").replace(",", ""))

    assert truncate_won(product) == truncate_won(bill.total_base_won), (
        f"{key}: 산식이 적은 수를 곱하면 {product:,.2f}원인데 엔진은 "
        f"{bill.total_base_won:,.2f}원입니다 (차 {product - bill.total_base_won:,.2f}원). "
        f"산식 「{row.formula}」"
    )
    assert shown == truncate_won(product), (
        f"{key}: 산식의 곱은 {truncate_won(product):,.0f}원인데 값 칸은 "
        f"{row.value} 입니다. 산식 「{row.formula}」"
    )


@pytest.mark.parametrize("key", BED_KEYS)
def test_용어_각주의_기본요금_산식이_큰_글자를_검산한다(
    bills: dict[str, BillingResult], key: str
) -> None:
    """각주가 말하는 항을 그대로 세어 「기본요금」 큰 글자와 맞댄다.

    각주에는 수가 없다 — 항의 **이름**만 있다. 그래서 이름을 엔진 값으로 바꿔
    문장이 적은 차례대로 곱하고 더한다. **항을 하나 빼면 곱이 갈리고, 이름을
    갈면 :data:`TERM_VALUES` 에 없어 그 자리에서 죽는다.**

    **역률요금 항이 여기 있는 까닭** (S142 1절) — 이 각주가 검산하는 큰 글자는
    ``base_with_power_factor_won`` 이고 S141 이 그것을 「기본요금」 이라 정했다.
    항이 없으면 `large-b-pf85` 에서 6,339,264원 모자란다. 간주 92% 인 벌
    셋에서는 그 항이 0원이라 **더해도 참이다.**
    """
    bill = bills[key]
    sentence = terms()["base_fee"].formula.rstrip(".")

    total = 0.0
    for addend in sentence.split(" + "):
        product = 1.0
        for factor in addend.split(" × "):
            name = factor.strip()
            assert name in TERM_VALUES, (
                f"각주 산식에 모르는 항 「{name}」 이 있습니다 — 문장 「{sentence}」. "
                "항을 새로 세웠다면 TERM_VALUES 에 그 항이 어느 값인지 적으십시오."
            )
            product *= TERM_VALUES[name](bill)
        total += product

    want = bill.base_with_power_factor_won
    assert truncate_won(total) == truncate_won(want), (
        f"{key}: 각주 산식대로 세면 {total:,.2f}원인데 「기본요금」 은 "
        f"{want:,.2f}원입니다 (차 {total - want:,.2f}원). 문장 「{sentence}」"
    )
