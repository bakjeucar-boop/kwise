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

import ast
import re
from collections import Counter
from pathlib import Path
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


# ===================================================================== 기간 이름 (S188)

#: 읽는 소스 뿌리. **모듈 속성으로 둔다** — 되돌려 확인할 때 옛 판 사본을 가리킨다.
SRC = Path(__file__).resolve().parent.parent / "src" / "kwise"

#: (파일, 문자열 조각, 그 조각이 서는 수). 조각은 문자열 상수 **한 덩이 전체**다 —
#: f-string 은 ``{}`` 사이 글자 하나하나가 한 덩이다. 수가 0 인 줄은 옛 이름이다.
PERIOD_NAME_PIECES: tuple[tuple[str, str, int], ...] = (
    # 계산 근거 표 — 화면 2단계 · PPT 부록 · Excel 부록 A · Word 부록 A (S10 · P12 · X13 · W16)
    ("report/worksheet.py", "기간 절감액", 6),
    ("report/worksheet.py", "기간 기본요금 절감", 2),
    ("report/worksheet.py", "기간 전력량요금 절감", 2),
    ("report/worksheet.py", "기간 역률요금 절감", 1),
    ("report/worksheet.py", "절감액", 0),
    ("report/worksheet.py", "기본요금 절감", 0),
    ("report/worksheet.py", "전력량요금 절감", 0),
    ("report/worksheet.py", "역률요금 절감", 0),
    # 선택요금 그림 축 — 화면 (S11) · PPT · Word (P13 · W11)
    ("ui/charts.py", "기간 현행 대비 (", 1),
    ("ui/charts.py", "현행 대비 (", 0),
    ("report/figures.py", "기간 현행 대비 (", 1),
    ("report/figures.py", "현행 대비 (", 0),
    # PPT 장08 (P1 · P2)
    ("report/narrative.py", "설비 투자 없이 기간에 ", 1),
    ("report/narrative.py", "설비 투자 없이 ", 0),
    ("report/slides.py", "투자 없이 가능한 기간 절감액", 1),
    ("report/slides.py", "투자 없이 가능한 절감액", 0),
    # 결론 문장 — PPT 수단 장 · Word 3장 (P4 · W5) 넷 + Word 4장 권장안 (W12).
    # 이어 적은 f-string 은 한 덩이로 합쳐지므로 앞 글자까지 한 조각이다.
    ("report/document.py", " 로 바꾸면 기간에 ", 1),
    ("report/document.py", " 로 바꾸면 ", 0),
    ("report/document.py", " 로 올리면 추가요금이 없어지고 감액을 받아 기간에 ", 1),
    ("report/document.py", " 로 올리면 추가요금이 없어지고 감액을 받아 ", 0),
    ("report/document.py", " 로 올리면 기간에 ", 1),
    ("report/document.py", " 로 올리면 ", 0),
    # **S213 에 「기간에」 가 문장 앞으로 갔다** — 발전량(kWh)도 같은 기간 값인데
    # 앞은 「연」 뒤는 「기간에」 였다. 한 「기간에」 가 둘을 덮는다.
    ("report/document.py", " kWp 를 설치하면 기간에 ", 1),
    ("report/document.py", " kWp 를 설치하면 연 ", 0),
    ("report/document.py", "」 입니다. 기간에 ", 1),
    ("report/document.py", "」 입니다. ", 0),
    # PPT 태양광 각주 (P6) · 잉여 장 머리 (P8)
    ("report/document.py", "기간 ", 1),
    ("report/document.py", "기간 수익", 1),
    ("report/document.py", "연 수익", 0),
    # Word 1장 (W1 · W2 · W3) · 2장 (W4) · 3장 계약전력 (W7) · 4장 표와 그림 (W13 · W14)
    ("report/document.py", "투자 없이 기간에 ", 1),
    ("report/document.py", "투자 없이 ", 0),
    ("report/document.py", "투자 없이 가능한 기간 절감액", 1),
    ("report/document.py", "투자 없이 가능한 절감액", 0),
    ("report/document.py", "선택요금 전환 (기간)", 1),
    ("report/document.py", "계약전력 조정 (기간)", 1),
    ("report/document.py", "기간 총 절감액", 1),
    ("report/document.py", "총 절감액", 0),
    ("report/document.py", "예상 기간 절감액", 1),
    ("report/document.py", "예상 절감액", 0),
    # S219 에 Word 3장 수단 칸 이름(`_measure_saving_label`)이 하나 더했다 (규칙 다).
    ("report/document.py", "기간 절감액", 3),
    ("report/document.py", "-1. 조합별 기간 절감액과 투자비", 1),
    ("report/document.py", "-1. 조합별 절감액과 투자비", 0),
    # Excel 요약 · 진단 (X1 · X2)
    ("report/excel.py", "선택요금 전환 (기간)", 1),
    ("report/excel.py", "계약전력 조정 (기간)", 1),
    ("report/excel.py", "계약전력 조정 기간 절감액", 1),
    ("report/excel.py", "계약전력 조정 절감액", 0),
)


def _string_pieces(path: Path) -> Counter[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return Counter(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


def test_기간_값을_적는_자리에_기간_이름이_선다() -> None:
    """**기간 값을 적는 스물셋 자리에 「기간」 이 선다** (S188 · 길 ㄴ).

    12개월 미만 벌(`small-ind-a1` · 122일)에서 같은 이름 「절감액」 이 세 배 다른
    두 값을 가리켰다 — 이 자리들은 관측 기간 값을 적고 카드 지표·PPT 수단 장은
    12개월 환산을 적는다. 이름은 기간 길이로 갈리지 않으므로 **덱 벌 열여덟
    전부에서 선다.**

    **소스를 문다 — 실물을 안 굽는다.** 스물셋은 네 산출물 × 여러 벌에 흩어져
    있어 실물로 물려면 벌마다 산출물 넷을 구워야 하고(한 벌 10~15초 · 1번 PC)
    그 자료는 `input\\` 이라 저장소 밖이다. 대신 한계가 있다 — **조각이 서 있어도
    그 글자가 실물에 실리는지는 못 본다**(화면 감사가 산출물을 소스로만 보는 것과
    같은 한계). 실물은 S188 4절이 두 벌에서 봤다.
    """
    pieces: dict[str, Counter[str]] = {}
    wrong = []
    for relative, piece, expected in PERIOD_NAME_PIECES:
        if relative not in pieces:
            pieces[relative] = _string_pieces(SRC / relative)
        got = pieces[relative][piece]
        if got != expected:
            wrong.append(f"{relative} 「{piece}」 {got} ≠ {expected}")
    assert wrong == [], wrong
