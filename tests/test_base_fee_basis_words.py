"""기본요금이 무엇에 매이는지 — 갈래를 모르는 글자는 말하지 않는다 (S170 2절).

**기본요금을 정하는 것은 벌마다 셋으로 갈린다.**

    피크형  요금적용전력이 피크를 따른다 (을·갑Ⅱ·교육용(갑) 고압 · 하한이 안 걸린다)
    하한형  하한이 전 달에 걸려 계약전력 × 하한비율이 정한다 (`large-b-over` 등)
    계약형  계약전력이 정한다 (제68조 ② — 갑Ⅰ·교육용(갑) 저압)

갈래를 모르는 자리가 「피크가 기본요금을 정한다」 를 적으면 뒤 두 갈래에서 거짓이다.
S170 이 덱 벌 열여덟에서 그런 자리 스물넷을 셌고 일곱 벌(계약형 넷 · 하한형 셋)에서
거짓이었다 — 부하율 툴팁 · 그림 툴팁 · 수단 개요 · PPT 장05·장06·장07 · 감도 풀이 ….
기준을 말하는 문장은 **갈래를 아는 자리**(요금 엔진의 요금적용전력 안내 · 하한 걸린 달 ·
ESS·계약전력 조정 결론)만 낸다.

**식을 다시 적지 않는다.** 앱을 띄워 화면 · PPT · Excel · Word 에 **실제로 뜬 글자**에서
그 주장을 찾는다. 두 벌을 돈다 — 계약형(`large-a`)과 하한형(`large-b-over`). 하한형은
**요금적용전력 기준 벌**이므로 두 기준을 다 문다. 태양광 결과 자리까지 뜨도록 사전 취득
기상을 쓴다.

**안 무는 것** — 「기본요금」 이 없는 하한 전제(「요금적용전력 하한에 걸려 있는지를
봅니다」 · 「점선이 요금적용전력 하한입니다」) · 한 문장 안에 부정이 섞인 주장 · 바꿔 말하기.
"""

from __future__ import annotations

import io
import json
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: 피크 쪽 낱말 — 글자 한 덩이(툴팁 · 칸 · 문단) 안에 이것이 있어야 본다.
DRIVER = re.compile(r"피크|최대\s?수요|요금적용전력|이 선|태양광|ESS|PV|솟")
#: 기본요금이 움직인다 · 매인다는 말.
BIND = re.compile(
    r"매기|매겨|매깁|매긴|매여|매이|결정|끌어올|정한|정합|정해|줄어|줄일|줄이|줄고|줄여|낮출|낮추|낮춰"
    r"|기반이 달라|걸린|절감액이 크|기여|저감"
)
NEGATION = re.compile(r"않|없|그대로|아니|못")
#: 계약전력이 지렛대인 문장은 피크 주장이 아니다 (「계약전력을 낮추면 …」).
LEVER = re.compile(r"계약전력(?:을|으로|에 붙)")
SENTENCE = re.compile(r"(?<=[.。])\s+|\n")

#: 계약형에서만 서야 하는 기준 문장 — 엔진 안내 · 1단계 요금 · ESS 결론 · 계약전력 조정 결론.
CONTRACT_CLAIM = re.compile(
    r"기본요금(?:을|은) \**계약전력\**(?:으로 매| [\d,.]+ kW 기준)"
    r"|기본요금이 계약전력에 붙는 종별이라"
)


def peak_claims(texts: list[str]) -> list[str]:
    """「피크(요금적용전력)가 기본요금을 정한다」 를 부정 없이 말하는 문장."""
    found: list[str] = []
    for text in texts:
        if not DRIVER.search(text):
            continue
        for sentence in SENTENCE.split(text):
            if (
                "기본요금" in sentence
                and BIND.search(sentence)
                and not NEGATION.search(sentence)
                and not LEVER.search(sentence)
            ):
                found.append(sentence.strip())
    return found


# ===================================================================== 실물 두 벌


@dataclass(frozen=True)
class Rendered:
    key: str
    texts: tuple[str, ...]
    #: 화면에 그려진 줄 — `(slot, text)`. **라벨과 값이 다른 줄로 온다** (S211 4-1).
    screen: tuple[tuple[str, str], ...] = ()
    #: 화면 줄마다의 자리(``Line.where``) — ``screen`` 과 같은 차례다 (S216 3-1).
    #: **표 칸이 어느 표의 것인지** 가르려면 자리가 있어야 한다.
    screen_at: tuple[str, ...] = ()
    #: Excel 한 행 — 이름과 값이 **한 행 안에** 있다 (S211 4-1).
    excel_rows: tuple[tuple[str, ...], ...] = ()
    #: 산출물 실물 바이트 — 「이름 → 바이트」 (S212 4절). **덱 그물이 읽는 것이 이것이다.**
    payloads: dict[str, bytes] = field(default_factory=dict)
    #: 덱 그물이 뜬 **그림 안** 줄 (S217 5절) — 화면 차트 자료 · PPT·Word png 의 글자.
    figures: tuple[tuple[str, ...], ...] = ()


def _deck(payload: bytes) -> list[str]:
    from pptx import Presentation

    out: list[str] = []

    def walk(shapes: Any) -> None:
        for shape in shapes:
            if shape.shape_type == 6:  # 묶음
                walk(shape.shapes)
            if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
                out.extend(p.text for p in shape.text_frame.paragraphs if p.text.strip())
            if getattr(shape, "has_table", False) and shape.has_table:
                out.extend(c.text for row in shape.table.rows for c in row.cells if c.text.strip())

    for slide in Presentation(io.BytesIO(payload)).slides:
        walk(slide.shapes)
    return out


def _workbook_rows(payload: bytes) -> list[tuple[Any, ...]]:
    """Excel 을 **행째로** 낸다 — 이름과 값을 짝지어 봐야 하는 자리가 있다 (S211 4-1)."""
    from openpyxl import load_workbook

    book = load_workbook(io.BytesIO(payload), read_only=True)
    return [
        row
        for sheet in book.worksheets
        if "시계열" not in sheet.title
        for row in sheet.iter_rows(values_only=True)
    ]


def _workbook(rows: list[tuple[Any, ...]]) -> list[str]:
    return [value for row in rows for value in row if isinstance(value, str) and value.strip()]


def _document(payload: bytes) -> list[str]:
    from docx import Document

    document = Document(io.BytesIO(payload))
    out = [p.text for p in document.paragraphs if p.text.strip()]
    out += [c.text for t in document.tables for row in t.rows for c in row.cells if c.text.strip()]
    return out


@pytest.fixture(scope="module", params=["large-a", "large-b-over"])
def rendered(request: pytest.FixtureRequest) -> Iterator[Rendered]:
    """덱 벌 하나를 `render_deck.build_deck` 과 같은 세션 상태로 띄워 네 산출물을 뜬다."""
    from streamlit.testing.v1 import AppTest

    sys.path.insert(0, str(PROJECT_ROOT / "tools"))
    import render_deck
    import screen_audit

    from kwise.report import slides_bytes
    from kwise.report.document import document_bytes
    from kwise.ui.artifacts import ARTIFACT_KEY
    from kwise.ui.pipeline import ContractForm
    from kwise.ui.views import compare as compare_view

    case = render_deck.BY_KEY[request.param]
    if not case.csv.is_file():
        pytest.skip(f"자료가 없습니다: {case.csv}")
    patch = pytest.MonkeyPatch()
    # **사전 취득 기상을 쓴다** — 태양광 그림 툴팁 · 감도 풀이까지 뜨게 한다.
    patch.delenv("KWISE_WEATHER_DIR", raising=False)
    captured: dict[str, Any] = {}

    def grab(document: Any, **options: Any) -> Any:
        """Word 는 화면에서 감췄으므로(36세션) PPT 가 받는 재료를 가로채 굽는다."""
        captured["sections"] = document
        return slides_bytes(document, **options)

    patch.setattr(compare_view, "slides_bytes", grab)
    try:
        app = AppTest.from_file(str(render_deck.APP), default_timeout=900)
        state = app.session_state
        state["upload_bytes"] = case.csv.read_bytes()
        state["upload_name"] = case.csv.name
        state["contract_form"] = ContractForm(
            contract_type=case.contract_type,
            voltage=case.voltage,
            option=case.option or render_deck._first_option(case.contract_type, case.voltage),
            contract_kw=case.contract_kw,
            power_factor_pct=case.power_factor_pct,
        )
        state["building_province"] = case.province
        state["building_sigungu"] = case.sigungu
        state["solar_inputs"] = render_deck.solar_inputs_for(case)
        for key in render_deck.ALL_MEASURES:
            state[f"measure_on_{key}"] = True
        state["combination_pick"] = render_deck.ALL_MEASURES
        app.run()
        assert not app.exception, app.exception
        collected = screen_audit.collect(app)
        screen = [(line.slot, line.text) for line in collected]
        texts = [text for _slot, text in screen]
        # **그림 안 글자도 같은 판에서 뜬다** (S217 5절) — 새 렌더를 안 붙인다.
        deck_words = _deck_words()
        figures = deck_words._screen_figures(app)
        with deck_words.FigureTap() as tap:
            app.button(key="build_ppt").click().run(timeout=900)
            app.button(key="build_excel").click().run(timeout=900)
            assert not app.exception, app.exception
            word, _name = document_bytes(captured["sections"])
        store = dict(app.session_state[ARTIFACT_KEY])
        excel_rows = _workbook_rows(store["excel"].payload)
        texts += _deck(store["ppt"].payload)
        texts += _workbook(excel_rows)
        texts += _document(word)
        figures += tap.rows("PPT", deck_words._deck_pictures(store["ppt"].payload))
        figures += tap.rows("Word", deck_words._word_pictures(word))
    finally:
        patch.undo()
    yield Rendered(
        request.param,
        tuple(texts),
        tuple(screen),
        tuple(line.where for line in collected),
        tuple(tuple(str(v) for v in row if v is not None) for row in excel_rows),
        {"excel": store["excel"].payload, "ppt": store["ppt"].payload, "word": word},
        tuple(tuple(row) for row in figures),
    )


def test_기본요금이_피크에_안_매이는_벌에서_피크를_기준으로_말하지_않는다(
    rendered: Rendered,
) -> None:
    """계약형 · 하한형 벌의 네 산출물에 「피크가 기본요금을 정한다」 가 없다.

    **S172 에 마지막 하나가 사라져 0 이 됐다** — 앞서는 요구사항서 9.4 필수 경고
    (「기본요금은 직전 12개월 중 최대수요로 결정됩니다」)가 두 벌에 다 서서, 그물이
    살아 있는지를 그것으로 봤다. 그 문장을 걷었으므로 이제 잡을 것이 없다.

    **그물이 살아 있는지는 아래 반대쪽이 본다** — 「기본요금을 계약전력으로 매깁니다」
    류는 계약형에만 서고 하한형(요금적용전력 기준)에는 없다. 그 줄이 죽으면
    「계약형 벌에 기준 문장이 안 섰다」 로 빨개진다. 한 벌을 두 번 띄우지 않으려고
    한 시험에 둔다.
    """
    assert peak_claims(list(rendered.texts)) == [], rendered.key

    hits = [text for text in rendered.texts if CONTRACT_CLAIM.search(text)]
    if rendered.key == "large-a":
        assert hits, "계약형 벌에 기준 문장이 안 섰다 — 그물이 죽었다"
    else:
        assert hits == [], hits


#: 두 수가 함께 쓰는 앞머리. **여기까지가 같아서 한 글자 차로 갈렸다.**
OFF_HOURS = "운영시간 외 부하"


def test_운영시간_외_부하_두_수가_이름에_제_식을_달고_갈린다(rendered: Rendered) -> None:
    """**한 글자 차 이름이 두 정의를 가렸다** (S211 1·2절).

    「비중」(`off_hours_energy_share`)은 **밖 사용량 ÷ 전체**이고 「비율」
    (`off_hours_ratio`)은 **밖 평균 ÷ 운영시간 평균**이다 — 정의가 둘이고 둘 다
    제자리에서 참이다(S211 1-6 ㄴ). 덱 19벌에서 **차가 0 인 벌이 없고 부호까지
    갈린다**(−10.7 ~ +18.2%p). 앞 여섯 글자가 같아 덱의 70.8% 와 Excel 의 89.0%
    를 **같은 것의 두 값**으로 읽게 된다.

    **한 못이 두 자리를 함께 문다.** 화면만 보면 Excel 이 갈려도 초록이고 그
    반대도 같다 — S192 3-1 이 「「비율」 과 「비중」 을 맞대는 못 0」 이라 적은
    자리다.

    **실물만 본다** — 소스 리터럴이 아니라 그려진 화면 줄과 구운 Excel 행이다.
    """
    화면 = [text for slot, text in rendered.screen if text.startswith(OFF_HOURS)]
    assert 화면 == [f"{OFF_HOURS} 비중"], f"화면 이름이 달라졌다 — {화면}"

    라벨 = [i for i, (_slot, text) in enumerate(rendered.screen) if text == f"{OFF_HOURS} 비중"]
    assert len(라벨) == 1, f"화면 라벨이 하나여야 합니다 — {라벨}"
    slot, 화면값 = rendered.screen[라벨[0] + 1]
    assert slot == "지표", f"라벨 다음이 지표 값이어야 합니다 — {slot} · {화면값}"

    엑셀 = [row for row in rendered.excel_rows if row and row[0].startswith(OFF_HOURS)]
    assert len(엑셀) == 1, f"Excel 이름이 하나여야 합니다 — {엑셀}"
    엑셀이름, 엑셀값 = 엑셀[0][0], 엑셀[0][1]

    # ① 앞머리가 같으므로 **뒤에 식이 붙어 갈려야 한다.**
    assert 엑셀이름 != f"{OFF_HOURS} 비중", "Excel 이 「비중」 이름을 쓰는데 값은 비율이다"
    assert "÷" in 엑셀이름, (
        f"Excel 이름이 제 식을 안 달았다 — {엑셀이름!r}. 「비중」 과 한 글자 차라 "
        "그대로 두면 덱의 수와 같은 것으로 읽힌다 (S211 2-2)."
    )
    # ② 「비중」 쪽은 실물에 식이 이미 떠 있다 — 툴팁이 그 자리다.
    assert any("밖 사용량 ÷ 전체 사용량" in text for text in rendered.texts), (
        "「비중」 의 식이 실물에서 사라졌다"
    )
    # ③ **두 수는 실제로 다르다** — 같아지면 정의 하나가 조용히 사라진 것이다.
    assert 화면값 != 엑셀값, f"{rendered.key} — 두 수가 같아졌다 ({화면값})"


# ===================================================================== 갈래를 모르는 상수


def test_갈래를_모르는_상수가_기본요금을_피크에_매지_않는다() -> None:
    """**모든 벌에 그대로 뜨는 글자**다 — 여기에 피크 주장이 있으면 일곱 벌에서 거짓이다.

    용어 풀이는 화면 툴팁에 안 뜨는 칸(정오 비중 · 주말 비중의 뜻)까지 본다.
    """
    from kwise.compare.sensitivity import SCENARIO_NAME_CAVEAT, SENSITIVITY_NOTE
    from kwise.report import narrative
    from kwise.ui.spec import measure
    from kwise.ui.text import CHART_TIPS

    texts = [SENSITIVITY_NOTE, SCENARIO_NAME_CAVEAT, *CHART_TIPS.values()]
    texts += [
        part
        for term in narrative.terms().values()
        for part in (term.formula, term.meaning, term.short)
    ]
    texts += list(narrative._PV_LEAD.values())
    for key in ("tariff_switch", "contract", "demand_response", "power_factor", "solar", "ess"):
        texts += [measure(key).overview, measure(key).headline]
    assert peak_claims([text for text in texts if isinstance(text, str)]) == []


def test_계약전력_변경_경고가_기본요금을_피크에_매지_않는다() -> None:
    """**경고 글자 사본 셋을 문다.** S172 에 요구사항서 9.4 와 사본 셋에서 첫 문장
    (「기본요금은 직전 12개월 중 최대수요로 결정됩니다」)을 걷어 xfail 을 걷었다.

    앞서는 `report\\notices.py` 하나만 읽어 나머지 두 사본을 옛 글자로 되돌려도
    초록이었다 (S172 4-3 · S183 2-3 에 다시 봤다). 셋을 따로 넘겨 어느 사본이
    말했는지 이름으로 남긴다."""
    from kwise.diagnose.contract import _MARGIN_NOTICE
    from kwise.measures import MARGIN_NOTICE
    from kwise.report import CONTRACT_CHANGE_WARNING

    copies = {
        "report.notices": CONTRACT_CHANGE_WARNING,
        "measures.contract": MARGIN_NOTICE,
        "diagnose.contract": _MARGIN_NOTICE,
    }
    assert {name for name, text in copies.items() if peak_claims([text])} == set()


def test_조합_이유는_기본요금이_곱한_전력이_움직였을_때만_선다() -> None:
    """3단계 계산 근거 「기본요금 기반이 달라집니다」 · 「역률 감액은 기본요금에 비례」.

    **실제로 발생한 상호작용만 적는다** (14세션 5-2). 계약형은 계약전력이, 하한형은
    하한이 기본요금을 곱해 태양광·ESS 를 켜도 그 전력이 그대로다 — 앞서는 그 일곱 벌에서도
    두 줄이 섰다(「6,000 kW 에서 6,000 kW 로 내려갔고」). 값을 손으로 넣어 함수만 부른다.
    """
    from kwise.ui.views.compare import _interaction_reasons

    def reasons(before_kw: float, after_kw: float) -> str:
        selection = SimpleNamespace(option="I")
        baseline = SimpleNamespace(
            selection=selection,
            billing_demand_kw=before_kw,
            bill=SimpleNamespace(mean_base_demand_kw=before_kw),
        )
        combined = SimpleNamespace(
            selection=selection,
            spec=SimpleNamespace(measure_keys=("solar", "power_factor")),
            billing_demand_kw=after_kw,
            bill=SimpleNamespace(mean_base_demand_kw=after_kw),
        )
        comparison: Any = SimpleNamespace(baseline=baseline)
        combination: Any = combined
        return " ".join(_interaction_reasons(comparison, combination, ()))

    still = reasons(6_000.0, 6_000.0)
    assert "기본요금 기반이 달라집니다" not in still
    assert "역률 감액은 기본요금에 비례합니다" not in still
    moved = reasons(5_293.0, 5_101.0)
    assert "기본요금 기반이 달라집니다" in moved
    assert "역률 감액은 기본요금에 비례합니다" in moved


# ============================================================ 덱 그물 (S212 4절)
#
# **그물이 좁으면 「닫혔다」 로 잘못 읽는다 — 값으로 겪었다.** S210 이 Excel 을 세
# 시트만 떠 「비율 0」 을 냈고 S211 이 열두 시트로 다시 세어 19벌 19줄을 찾았다.
# S212 가 `tools\deck_words.py` 를 화면 하나에서 넷으로 넓혔고, **아래 둘이 그
# 넓힌 범위를 문다** — 다시 좁아지면 빨개진다.
#
# **새 렌더를 안 붙인다** — 위 모듈 픽스처가 이미 구운 실물 바이트를 읽는다.


def _deck_words() -> Any:
    sys.path.insert(0, str(PROJECT_ROOT / "tools"))
    try:
        import deck_words
    finally:
        sys.path.pop(0)
    return deck_words


#: S210 의 덤프가 못 보던 시트. **여기 이름이 빠지면 그때 값으로 겪은 자리가 다시 선다.**
WIDE_SHEETS = ("진단", "월별 집계", "부록 C 한계와 전제")


def test_덱_그물이_네_산출물을_실물에서_담는다(rendered: Rendered) -> None:
    """`tools\\deck_words.py` 가 **화면 하나가 아니라 넷**을 담는다 (S212 1·2절).

    실물 바이트를 읽어 센다 — 소스 리터럴로 판정하지 않는다. 무는 것 넷.

        ㄱ  Excel·PPT·Word 가 **한 줄이라도** 서는가 (화면만 보던 자리)
        ㄴ  줄 첫 칸이 산출물인가 (넷을 갈라 셀 수 있는가)
        ㄷ  수치 조각이 **줄 전체**에서 세지는가 (`row[3]` 한 칸이면 값 칸이 밖이다)
        ㄹ  S210 이 놓쳤던 시트 셋이 덤프 안에 있는가
    """
    deck_words = _deck_words()
    excel = deck_words._excel_rows(rendered.payloads["excel"])
    deck = deck_words._deck_rows(rendered.payloads["ppt"])
    word = deck_words._word_rows(rendered.payloads["word"])

    assert deck_words.OUTPUTS == ("화면", "Excel", "PPT", "Word")
    assert {row[0] for row in excel + deck + word} == {"Excel", "PPT", "Word"}
    sheets = {row[1] for row in excel}
    assert set(WIDE_SHEETS) <= sheets, sorted(sheets)
    assert not [name for name in sheets if "시계열" in name], "시계열 시트는 안 담는다"
    assert {row[1] for row in word if row[1].startswith("Heading")}, "Word 절 제목이 없다"

    # **ㄷ — 한 칸만 보면 값이 샌다.** 줄 전체로 세면 더 나와야 한다.
    전체 = sum(len(deck_words.MONEY.findall(deck_words.text_of(row))) for row in excel)
    한칸 = sum(len(deck_words.MONEY.findall(row[3])) for row in excel if len(row) > 3)
    assert 전체 > 한칸 > 0, (전체, 한칸)

    # **ㅁ — 그림 안 글자** (S217 2절). 앞까지 화면은 축 이름만 · PPT·Word 그림은 통째
    # 밖이라 기온 기준선 「연평균」 이 스냅 0곳이었다. 그 자리가 다시 좁아지면 빨개진다.
    갈래: dict[str, set[str]] = defaultdict(set)
    for row in rendered.figures:
        assert deck_words.inside(list(row)), row
        갈래[row[0]].add(row[2])
    for name, want in (
        ("화면", {"범례", "눈금", "라벨"}),
        ("PPT", {"범례", "눈금", "축 이름"}),
        ("Word", {"범례", "눈금", "축 이름"}),
    ):
        assert want <= 갈래[name], (name, sorted(갈래[name]))
        assert "못 뜬 그림" not in 갈래[name], f"{name} 에 가로채지 못한 그림이 있다"
    # 셀 때 자리 칸(차트 제목)을 줄마다 세지 않는다 (S217 7절 — 「왼쪽이 절감」 19 → 178 이었다).
    제목 = f"{deck_words.FIGURE}화면01 기간 현행 대비 (만원) — 왼쪽이 절감"
    가짜 = [["화면", 제목, "범례", "절감"]]
    assert not deck_words.count_words({"벌": 가짜}, ["왼쪽이 절감"])["왼쪽이 절감"]
    # 실물 글자 — 월별 최대수요 그림의 범례(`figures.monthly_peak_png`)는 PPT·Word 가 다 싣는다.
    for name in ("PPT", "Word"):
        assert [r for r in rendered.figures if r[0] == name and r[3] == "요금적용 대상 최대"], name


def test_덱_그물이_뜰_때_산출물을_함께_부른다(monkeypatch: pytest.MonkeyPatch) -> None:
    """`snap()` 이 **화면만 부르면** 빨개진다 (S212 2-2 ㄱ).

    위 시험은 줄을 만드는 세 함수를 문다 — 그 셋이 살아 있어도 `snap()` 이 안
    부르면 그물은 다시 화면 하나다. **앱을 안 띄운다** — 화면 쪽을 빈 줄로 세워
    부르는 자리만 본다.
    """
    deck_words = _deck_words()
    key = deck_words.render_deck.CASES[0].key
    불린: list[str] = []

    class 앱:
        exception = None

        def run(self) -> 앱:
            return self

    def 가짜(app: Any, name: str) -> list[list[str]]:
        불린.append(name)
        return [["Excel", "진단", "1,000원"]]

    monkeypatch.setattr(deck_words.render_deck, "build_app", lambda case: 앱())
    monkeypatch.setattr(deck_words.screen_audit, "collect", lambda app: ())
    monkeypatch.setattr(deck_words, "_artifacts", 가짜)
    rows = deck_words.snap([key])[key]
    assert 불린 == [key], "snap() 이 산출물을 안 불렀다 — 그물이 화면 하나로 좁아졌다"
    assert [row[0] for row in rows] == ["Excel"]
    assert deck_words.text_of(rows[0]) == "진단\t1,000원"


def test_덱_그물이_안_담는_것을_스스로_적는다(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """**넓힌 뒤에도 남는 구멍**을 사람이 기억하지 않아도 되게 도구가 적는다 (S212 2-4).

    길 셋에서 다 뜬다 — 뜨거나 읽은 뒤 · 맞댄 뒤 · 인자 없이 부를 때.
    **앱을 안 띄운다** — 담아 둔 스냅을 읽는 길만 쓴다.
    """
    deck_words = _deck_words()
    snap = tmp_path / "한벌.json"
    snap.write_text(
        json.dumps({"벌": [["Excel", "진단", "기본요금", "1,000원"]]}, ensure_ascii=False),
        encoding="utf-8",
    )
    for argv in (
        ["deck_words.py", "--read", str(snap)],
        ["deck_words.py", "--diff", str(snap), str(snap)],
        ["deck_words.py"],
    ):
        monkeypatch.setattr(sys, "argv", argv)
        assert deck_words.main() == 0
        assert deck_words.BLIND in capsys.readouterr().out, argv


# ───────────────────────────────────────────────── 기간 값을 「연」 이라 부르지 않는다
#
# **요구사항서 5.5** — 「결과를 "연간"으로 표시하지 않는다. 실제 기간을 명시하거나
# 12개월로 환산한다.」 S188 이 금액 자리 스물셋에 「기간」 을 달았고 **S213 이
# 에너지·대표일 자리 열을 마저 달았다.** 122일 벌에서 「연간 발전량 39,565 kWh」
# 처럼 이름과 값이 세 배 어긋나 있었다.
#
# **실물을 문다 — 소스 리터럴을 안 찾는다.** 그리고 **넷을 한 못으로 문다** —
# 한쪽만 보면 다른 쪽이 갈려도 초록이다(S212 가 값으로 겪은 자리다).
#
# **새 렌더를 안 붙인다** — 위 모듈 픽스처가 이미 구운 것을 읽는다.

#: 기간 값 자리에 서면 안 되는 옛 이름. **값과 이름이 세 배 어긋나던 글자다.**
YEAR_NAMES = (
    "연간 발전량",
    "연간 잉여",
    "연간 최대수요일",
    "연중 최대수요일",
    "연간 최대수요 상위",
    # **기간 길이로 이름을 가르지 않는다** (S216 · 사람이 정했다). 열두 달 벌에서
    # 「연간」 으로 갈리던 자리라 조건부로 되돌리면 이 두 벌에서 빨개진다.
    "연간 사용량",
    "연간 원단위",
)

#: 그 자리의 지금 이름. 하나라도 빠지면 이름이 도로 갈린 것이다.
PERIOD_NAMES = ("기간 발전량", "기간 최대수요일")


def test_기간_값을_적는_자리가_네_산출물에서_연이라_말하지_않는다(rendered: Rendered) -> None:
    """**네 산출물을 한 못으로 문다** (S213 1-6 · 울타리 ㄱ1~ㄱ10).

    **S214 에 예외가 사라졌다.** 앞서는 화면이 12개월 환산을 「연간 잉여」 로 적어
    화면 하나를 빼고 세었는데, 그 자리가 「12개월 환산 잉여」 로 가면서 **네 산출물
    모두에서 0** 이 됐다.

    **12개월 미만 벌이 없어도 문다.** 이름은 기간 길이로 갈리지 않으므로(S188 정본)
    열두 달 벌에서도 그대로 선다 — 조건부로 되돌리면 여기서 빨개진다.
    """
    deck_words = _deck_words()
    쪽 = {
        "Excel": deck_words._excel_rows(rendered.payloads["excel"]),
        "PPT": deck_words._deck_rows(rendered.payloads["ppt"]),
        "Word": deck_words._word_rows(rendered.payloads["word"]),
    }
    글자 = {name: [deck_words.text_of(row) for row in rows] for name, rows in 쪽.items()}
    글자["화면"] = [text for _slot, text in rendered.screen]

    셈 = {
        (산출물, 옛): sum(옛 in line for line in lines)
        for 산출물, lines in 글자.items()
        for 옛 in YEAR_NAMES
    }
    # **네 산출물 다 0 이다** (S214 에 화면 예외가 사라졌다).
    샌자리 = [f"{산출물} 「{옛}」 {수}" for (산출물, 옛), 수 in 셈.items() if 수]
    assert 샌자리 == [], (rendered.key, 샌자리)

    # **한 문장에 「연」 과 「기간에」 가 함께 서던 자리** (ㄱ2 · ㄱ3).
    for 산출물 in ("PPT", "Word"):
        섞인 = [line for line in 글자[산출물] if "연 " in line and "kWh 를 발전해" in line]
        assert 섞인 == [], (rendered.key, 산출물, 섞인)

    선이름 = {
        이름: sum(이름 in line for lines in 글자.values() for line in lines)
        for 이름 in PERIOD_NAMES
    }
    빠진 = [이름 for 이름, 수 in 선이름.items() if not 수]
    assert 빠진 == [], (rendered.key, 선이름)


#: **12개월 환산값 자리에 서면 안 되는 옛 이름** (S214 1-6 · 울타리 ㄱ1~ㄱ13).
ANNUAL_NAMES = (
    "연간 환산",
    "연간 감축 가능량",
    "연간 감축 잠재량",
    "연간 절감액",
    "연간 수익",
)

#: 그 자리의 지금 이름. **하나도 안 서면 이름이 통째로 빠진 것이다.**
CONVERTED_NAMES = (
    "12개월 환산",
    "12개월 환산 감축 가능량",
    "12개월 환산 절감액",
)


def test_12개월_환산값_자리가_네_산출물에서_연이라_말하지_않는다(rendered: Rendered) -> None:
    """**12개월 환산값을 「연간」 이라 부르지 않는다** (S214 1-6 · 울타리 ㄱ1~ㄱ13).

    S213 이 **기간 값** 쪽을 「기간」 으로 모았고 이 못은 그 반대쪽을 문다 —
    ``annualize()`` 와 365일 환산을 지난 값을 적는 자리다. S188 정본이 12개월 쪽
    낱말을 안 정해 「연간 환산」 과 「12개월 환산」 이 **한 뜻 두 낱말**로 같은 벌에
    나란히 서 있었다.

    **실물을 문다 — 소스 리터럴을 안 찾는다.** 그리고 **넷을 한 못으로 문다** —
    DR 감축 가능량은 화면·Excel·PPT·Word 넷에 다 실리고 ESS 절감액은 화면·PPT·Word
    셋에 실린다. 한쪽만 보면 다른 쪽이 갈려도 초록이다.

    **새 렌더를 안 붙인다** — 위 모듈 픽스처가 이미 구운 것을 읽는다.

    **S216 에 꼬리표 「/년」 을 더 문다** — 이름이 곁에 있는 지표·표 칸에 다시 서면
    빨갛고, 이름 없는 지표에서 다 사라져도 빨갛다.

    **S220 에 넷을 더 문다** — 12개월 값이 선 표(Excel 용량 곡선 · 조합 비교 · 감도 ·
    감도 상세 · Word 감도 표)의 기간 값 머리에 「기간」 · 경고의 역률요금 증가액에
    「기간」 · 차익거래 1 kWh 당 수익에 「연」 이 아니라 「12개월 환산」 · Excel 요약에
    같은 글자의 안내 행이 둘 이상 서지 않는다.
    """
    deck_words = _deck_words()
    쪽 = {
        "Excel": deck_words._excel_rows(rendered.payloads["excel"]),
        "PPT": deck_words._deck_rows(rendered.payloads["ppt"]),
        "Word": deck_words._word_rows(rendered.payloads["word"]),
    }
    글자 = {name: [deck_words.text_of(row) for row in rows] for name, rows in 쪽.items()}
    글자["화면"] = [text for _slot, text in rendered.screen]

    샌자리 = [
        f"{산출물} 「{옛}」 {수}"
        for 산출물, lines in 글자.items()
        for 옛 in ANNUAL_NAMES
        if (수 := sum(옛 in line for line in lines))
    ]
    assert 샌자리 == [], (rendered.key, 샌자리)

    선이름 = {
        이름: sum(이름 in line for lines in 글자.values() for line in lines)
        for 이름 in CONVERTED_NAMES
    }
    빠진 = [이름 for 이름, 수 in 선이름.items() if not 수]
    assert 빠진 == [], (rendered.key, 선이름)

    # **이름이 곁에 있으면 「/년」 을 안 붙이고 없으면 남긴다** (S216 · 사람이 정했다).
    # 곁은 같은 지표의 라벨과 같은 칸의 표 머리다 — 「/년」 은 화면에만 선다.
    겹: list[str] = []
    남김 = 0
    라벨 = ""
    for slot, text in rendered.screen:
        if slot == "라벨":
            라벨 = text
        elif slot == "지표" and text.endswith("/년"):
            if "12개월 환산" in 라벨:
                겹.append(f"지표 「{라벨}」 {text}")
            else:
                남김 += 1
    # 표는 자리로 가른다 — ESS 사양 표는 머리 「12개월 환산 절감액」 이 금액 칸의 이름이다.
    표: defaultdict[str, list[str]] = defaultdict(list)
    for (slot, text), at in zip(rendered.screen, rendered.screen_at, strict=True):
        if slot == "표":
            표[at].append(text)
    for cells in 표.values():
        if "방전시간" in cells and "12개월 환산 절감액" in cells:
            겹 += [f"ESS 사양 표 {cell}" for cell in cells if cell.endswith("/년")]
    assert 겹 == [], (rendered.key, 겹)
    assert 남김, (rendered.key, "이름 없는 12개월 환산 지표에서 「/년」 이 사라졌다")

    # **12개월 환산값이 이름 없이 「절감액」 으로 서지 않는다** (S218 · 사람이 정했다).
    # 화면 2단계 카드 지표 · PPT 수단 장 지표 · 8장 표 머리 · 수단 장 각주 · Word DR 행.
    # Word 의 다른 수단 행은 기간 값이 먼저라(괄호 안이 12개월) 그대로 「절감액」 이다.
    맨 = [
        f"화면 지표 라벨 {text}"
        for slot, text in rendered.screen
        if (slot, text) == ("라벨", "절감액")
    ]
    for row in 쪽["PPT"]:
        cells = row[2:]
        if "절감액" in cells or cells[0].startswith("※ 절감액 미산출"):
            맨.append(f"PPT {row[1]} {deck_words.text_of(row)}")
    맨 += [
        f"Word {row[1]} {deck_words.text_of(row)}"
        for row in 쪽["Word"]
        if row[2:3] == ["절감액"] and "정산" in deck_words.text_of(row)
    ]
    assert 맨 == [], (rendered.key, 맨)

    # **표시 규칙 하나** (S219 · 사람이 정했다) — (나) 12개월 환산값은 이름이나 「/년」
    # 가운데 하나(라벨이 있으면 이름) · (다) 12개월 값과 함께 서는 기간 값은 「기간」 ·
    # 「기간」 열에 12개월 값이 안 선다. 두 벌 다 12개월이라 값은 못 가르고 표시를 문다.
    어긋: list[str] = []
    용량 = [cells for cells in 표.values() if "자가소비율" in cells]
    assert 용량, (rendered.key, "화면 태양광 용량 표가 없다")
    for cells in 용량:
        if "12개월 환산 자가소비 절감액" not in cells:
            어긋.append(f"화면 용량 표 머리에 이름이 없다 {cells[:8]}")
        어긋 += [f"화면 용량 표 {cell}" for cell in cells if cell.endswith("원/년")]
    역률 = {
        name: [line for line in 글자[name] if "역률 영향 반영 시" in line]
        for name in ("PPT", "Word", "Excel")
    }
    assert all(역률.values()), (rendered.key, 역률)
    어긋 += [
        f"{name} {line}"
        for name in ("PPT", "Word")
        for line in 역률[name]
        if not re.search(r"역률 영향 반영 시 [\d,]+원/년", line)
    ]
    어긋 += [f"Excel {line}" for line in 역률["Excel"] if "역률 영향 반영 시 기간 " not in line]
    경고 = {
        name: [line for line in 글자[name] if "역률요금이" in line and " 늘어 " in line]
        for name in ("화면", "Word")
    }
    assert all(경고.values()), (rendered.key, 경고)
    어긋 += [
        f"{name} {line}"
        for name, lines in 경고.items()
        for line in lines
        if "늘어 절감액이" in line
    ]
    word_saving = [row for row in 쪽["Word"] if row[1].startswith("표") and len(row) > 3]
    어긋 += [
        f"Word {row[1]} {deck_words.text_of(row)}"
        for row in word_saving
        if row[2] == "절감액" and "(12개월 환산 " in row[3]
    ]
    if not [row for row in word_saving if row[2] == "기간 절감액" and "(12개월 환산 " in row[3]]:
        어긋.append("Word 3장 표에 「기간 절감액」 칸이 없다")
    수단표 = [row[2:] for row in 쪽["Excel"] if row[1] == "수단별 결과"]
    머리 = next(cells for cells in 수단표 if cells[:1] == ["수단"])
    if "절감액(원)" in 머리:
        어긋.append(f"Excel 수단별 결과 머리 {머리}")
    기간열, 환산열 = 머리.index("기간 절감액(원)"), 머리.index("12개월 환산(원)")
    for cells in 수단표:
        if cells[0].startswith("경제성DR") and cells[기간열] != "—":
            어긋.append(f"Excel 경제성DR 기간 열 {cells[기간열]} (12개월 열 {cells[환산열]})")

    # **S220 — 열쇠를 뗀 표 머리와 남은 자리** (규칙 다 · 나 · 사람이 정했다).
    # 12개월 환산값이 선 표에서 기간 값 머리((원) · (kWh))는 「기간」 으로 시작한다 —
    # Excel 용량 곡선 · 조합 비교 · 감도 상세 머리 · 감도 지표와 표시 칸 · Word 감도 표.
    def 기간값(name: str) -> bool:
        return name.endswith(("(원)", "(kWh)")) and "12개월 환산" not in name and name != "투자비(원)"

    for sheet, 첫 in (("태양광 용량 곡선", "용량(kWp)"), ("조합 비교", "조합"), ("감도 상세", "시나리오")):
        cells = next((row[2:] for row in 쪽["Excel"] if row[1] == sheet and row[2:3] == [첫]), [])
        if not any("12개월 환산" in cell for cell in cells):
            어긋.append(f"Excel {sheet} 머리에 12개월 환산 열이 없다 {cells}")
        어긋 += [f"Excel {sheet} 머리 {c}" for c in cells if 기간값(c) and not c.startswith("기간 ")]
    감도 = [("Excel 감도", row[2], row[-1]) for row in 쪽["Excel"] if row[1] == "감도"]
    감도 += [
        ("Word 감도 표", row[2], row[3])
        for row in 쪽["Word"]
        if row[1].startswith("표") and len(row) > 3 and "프로파일 감도 범위" in row[3]
    ]
    assert len({where for where, _, _ in 감도}) == 2, (rendered.key, 감도[:3])
    어긋 += [
        f"{where} {name}"
        for where, name, shown in 감도
        if 기간값(name) and not (name.startswith("기간 ") and shown.startswith(name + " "))
    ]
    # 경고의 역률요금 증가액도 기간 값이다 — 앞 금액에 「기간」.
    어긋 += [
        f"{name} 경고 {line}"
        for name, lines in 경고.items()
        for line in lines
        if not re.search(r"역률요금이 기간 [\d,]+원 늘어", line)
    ]
    # 차익거래 1 kWh 당 수익은 12개월 환산값이다 — 「연」 이 아니라 이름 (S214 · 규칙 나).
    차익 = {
        name: [
            line
            for line in 글자[name]
            if "원/kWh" in line and ("차익거래 단독" in line or "차익거래 잠재" in line)
        ]
        for name in ("화면", "Excel", "Word")
    }
    if any(차익.values()):
        assert all(차익.values()), (rendered.key, 차익)
        어긋 += [
            f"{name} 차익거래 {line}"
            for name, lines in 차익.items()
            for line in lines
            if re.search(r"(?<![가-힣])연 [\d,]", line)
            or not re.search(r"12개월 환산 [\d,]+ ?원/kWh", line)
        ]
    # Excel 요약 — 같은 등급 · 같은 글자의 안내 행이 둘 이상 서지 않는다 (묶음을 넘어서).
    안내 = Counter(
        (row[2], row[-1])
        for row in 쪽["Excel"]
        if row[1] == "요약" and row[2:3] and row[2].startswith("안내 · ")
    )
    assert 안내, (rendered.key, "Excel 요약에 안내 행이 없다")
    어긋 += [f"Excel 요약 같은 글자 {n}행 {k[1][:40]}" for k, n in 안내.items() if n > 1]
    assert 어긋 == [], (rendered.key, 어긋)

    # **기온 기준선은 관측 길이와 상관없이 「기간 평균」 이다** (S218 · 사람이 정했다).
    # 두 벌 다 1년이 넘는다 — 「연평균」 이 서던 바로 그 갈래다. 그림 안 글자를 본다.
    기온 = [row[-1] for row in rendered.figures if row[-1].endswith("℃") and "평균" in row[-1]]
    assert 기온 and not [text for text in 기온 if "연평균" in text], (rendered.key, 기온)
    assert {row[0] for row in rendered.figures if row[-1] in 기온} == {"화면", "PPT"}, (
        rendered.key,
        기온,
    )


def test_사용량_이름이_진단_지표와_태양광_캡션에서_같다(rendered: Rendered) -> None:
    """**한 벌 안에서 같은 값을 두 이름으로 부르지 않는다** (S213 1-5 · 울타리 ㄱ6).

    진단 지표(`ui\\views\\diagnose.py`)는 자료가 350일에 못 미치면 「기간 사용량」 으로
    갈아 다는데 **태양광 캡션만 무조건 「연간 사용량」 이었다** — 122일 벌에서 같은
    값이 한 화면에 두 이름으로 섰다. 이 못은 **두 자리의 낱말이 같은지**를 보므로
    열두 달 벌에서도 한쪽만 고치면 빨개진다.
    """
    화면 = [text for _slot, text in rendered.screen]
    지표 = [text for text in 화면 if text in ("연간 사용량", "기간 사용량")]
    캡션 = [text for text in 화면 if "사용량의" in text and "줄입니다" in text]
    assert 지표, (rendered.key, "진단 사용량 지표가 화면에 없다")
    assert 캡션, (rendered.key, "태양광 캡션이 화면에 없다")

    낱말 = 지표[0].removesuffix(" 사용량")
    어긋남 = [text for text in 캡션 if f"{낱말} 사용량의" not in text]
    assert 어긋남 == [], (rendered.key, 낱말, 어긋남)
