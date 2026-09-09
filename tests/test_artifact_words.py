"""산출물 실물에 **코드 식별자가 없다** (S156 3절).

**이 자리가 못 없이 비어 있었다.** 화면은 `tools\\screen_audit.py` 가 규칙
다섯으로 매 판 훑는데 **PPT·Excel·Word 는 아무도 안 봤고**, 그래서
「선택single」 이 벌마다 일곱 자리로 나가고 있었다 (S151 이 값으로 봤다).

**실물 열일곱 벌을 매 판 뽑지 않는다.** 한 벌이 15~57초라 열일곱이면 6~17분이고
(S156 2-1), 게다가 `small-ind-a1` 의 자료는 `.gitignore` 밖이라 **2번 PC 에는
없다** — 실물로 물면 그 PC 에서 통째로 skip 된다. **안 돈 시험은 실패 줄을 내지
않는다.**

그래서 **합성 자료에 선택요금 `single` 을 씌운다.** S151 이 「픽스처는
「선택single」 이 안 뜬다」 고 적은 것은 픽스처가 언제나 `I`·`II` 였기 때문이지
픽스처로 못 뜨기 때문이 아니다 — 저압 산업용(갑)Ⅰ 은 선택요금이 `single`
하나뿐이라 그 조합을 골라 주면 같은 글자가 그대로 선다.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from kwise.io import UsageData
from kwise.measures import TariffSwitchResult, evaluate_tariff_switch
from kwise.quality import QualityReport
from kwise.report import DocumentSections, build_document, build_slides
from kwise.report.document import measure_entries
from kwise.report.excel import measure_summary_frame
from kwise.tariff import (
    BillingOptions,
    BillingResult,
    TariffSelection,
    TariffTable,
    calculate_bill,
)

#: 선택요금이 `single` 하나뿐인 조합 (약관 제59조 ② 1호 · `render_deck` 의
#: `small-ind-a1` 과 같은 자리). **날 열쇠가 화면에 뜨는 유일한 갈래다** —
#: `I`·`II` 는 로마자로 적혀 눈에 안 띄지만 그것도 코드 열쇠이기는 마찬가지다.
SINGLE = TariffSelection("industrial_a_1", "low", "single")

#: `screen_audit.CODE_WORDS` 와 같은 뜻이되 **경계가 ASCII 다** (S156 3-3).
#: 저쪽의 `\w` 는 한글을 낱말 문자로 세어 「…역률power_factor」 처럼 한글에 바로
#: 붙은 열쇠를 놓친다. 규칙을 두 벌 두지 않으려면 저쪽을 고쳐야 하는데, 저쪽은
#: 도구라 여기서 들여올 수 없다 — **같은 정규식을 여기 한 번 더 적는 대신
#: 그것이 같은 것임을 :func:`test_감사_도구와_같은_정규식을_쓴다` 가 문다.**
CODE_WORDS = re.compile(
    r"(?<![A-Za-z0-9_.\\/])(?:"
    r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+"
    r"|[A-Za-z_][A-Za-z0-9_]*=(?!=)"
    r")(?![\w]*\.[a-z]{2,4}\b)"
)

#: 코드가 쓰는 날 열쇠 가운데 **정규식이 못 잡는 것.** `single` 은 밑줄이 없어
#: snake_case 가 아니고 `=` 도 안 붙는다 — 경계를 어떻게 고쳐도 안 걸린다.
#: S151 이 센 일곱 자리가 전부 이것이다.
BARE_KEYS = ("single", "spring_fall", "general_b", "industrial_a_1", "high_a")

KOREAN = re.compile(r"[가-힣]")

#: 갑Ⅰ 은 기본요금을 **계약전력**으로 매긴다 (기본공급약관 제68조 ②). 값이
#: 아니라 글자를 무는 시험이라 수는 아무 것이나 되지만, 합성 자료의 최대수요를
#: 덮는 수를 준다 — 초과사용부가금 갈래가 끼면 글자가 늘어 못이 넓어진다.
OPTIONS = BillingOptions(contract_kw=5_500.0)


def offenders(texts: list[str]) -> list[str]:
    """한글이 든 글자 가운데 코드 열쇠가 새는 것."""
    return [
        text
        for text in texts
        if text
        and KOREAN.search(text)
        and (CODE_WORDS.search(text) or any(key in text for key in BARE_KEYS))
    ]


def _deck_texts(deck: Any) -> list[str]:
    out: list[str] = []
    for slide in deck.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                out.append(shape.text_frame.text)
            if shape.has_table:
                out.extend(cell.text for row in shape.table.rows for cell in row.cells)
    return out


#: **부록 B 는 이 못이 안 본다** (S156 3-3). 그 표는 `data\` 의 기준 데이터를
#: **원문 그대로 싣는 것이 뜻**이라 `key=office, label=사무실, …` 이나
#: `all_to_light` 같은 열쇠가 값 칸에 그대로 선다. 고칠 일인지부터 사람이 정할
#: 자리이므로 미해결에 이름으로 두고, 여기서는 **표를 머리글로 가려 뺀다** —
#: 「없는 규칙」 이 아니라 「이 표는 다르다」 를 값으로 적어 두는 것이다.
APPENDIX_B_HEADER = ("구분", "항목", "값", "근거", "확인일")


def _document_texts(document: Any) -> list[str]:
    out = [para.text for para in document.paragraphs]
    for table in document.tables:
        if tuple(cell.text for cell in table.rows[0].cells) == APPENDIX_B_HEADER:
            continue
        out.extend(cell.text for row in table.rows for cell in row.cells)
    return out


@pytest.fixture(scope="module")
def single_bill(
    sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable
) -> BillingResult:
    return calculate_bill(sample_usage, tariff, SINGLE, options=OPTIONS, quality=sample_report)


@pytest.fixture(scope="module")
def single_switch(
    sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable
) -> TariffSwitchResult:
    return evaluate_tariff_switch(
        sample_usage, tariff, SINGLE, options=OPTIONS, quality=sample_report
    )


def test_요금_근거에_선택요금_열쇠가_안_적힌다(single_bill: BillingResult) -> None:
    """`BillingResult.traceability` — Word·Excel 이 함께 싣는 근거 줄이다."""
    assert offenders(list(single_bill.traceability())) == []


def test_수단_카드_결론에_선택요금_열쇠가_안_적힌다(single_switch: TariffSwitchResult) -> None:
    """PPT 와 Word 가 **같은** `MeasureEntry` 를 읽으므로 한 자리가 둘을 쥔다."""
    entries = measure_entries(switch=single_switch)
    assert offenders([entry.conclusion for entry in entries]) == []


def test_엑셀_수단_시트에_선택요금_열쇠가_안_적힌다(single_switch: TariffSwitchResult) -> None:
    """`TariffSelection.__str__` 이 `종별/전압/선택` 이라 셀에 그대로 나갔다.

    **「수단」 은 열이 아니라 index 다** — 값만 훑던 첫 판이 그래서 초록이었다.
    머리글·index·값 셋을 다 본다.
    """
    frame = measure_summary_frame(switch=single_switch)
    cells = [str(value) for value in frame.to_numpy().ravel()]
    cells += [str(value) for value in frame.index]
    cells += [str(value) for value in frame.columns]
    assert offenders(cells) == []


def test_덱과_문서_실물에_선택요금_열쇠가_안_적힌다(
    sample_usage: UsageData, single_bill: BillingResult, single_switch: TariffSwitchResult
) -> None:
    """**실물을 굽는다.** 위 셋이 재료를 물었다면 이것은 조립을 문다 —
    슬라이드와 문서가 재료를 안 쓰고 제 자리에서 다시 적는 자리가 있었다
    (`slides.py:1020`·`:1099` · `document.py:1395`).
    """
    sections = DocumentSections(
        usage=sample_usage,
        bill=single_bill,
        measures=measure_entries(switch=single_switch),
        building_name="합성 자료",
    )
    assert offenders(_deck_texts(build_slides(sections))) == []
    assert offenders(_document_texts(build_document(sections))) == []


def test_감사_도구와_같은_정규식을_쓴다() -> None:
    """**규칙이 두 벌이 되지 않게 한다.** 화면 감사와 이 시험이 같은 것을 본다."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import screen_audit

    assert screen_audit.CODE_WORDS.pattern == CODE_WORDS.pattern
