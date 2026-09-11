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
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
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


def _warning_head() -> str:
    from kwise.report import CONTRACT_CHANGE_WARNING

    return CONTRACT_CHANGE_WARNING.split(". ")[0]


# ===================================================================== 실물 두 벌


@dataclass(frozen=True)
class Rendered:
    key: str
    texts: tuple[str, ...]


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


def _workbook(payload: bytes) -> list[str]:
    from openpyxl import load_workbook

    book = load_workbook(io.BytesIO(payload), read_only=True)
    return [
        value
        for sheet in book.worksheets
        if "시계열" not in sheet.title
        for row in sheet.iter_rows(values_only=True)
        for value in row
        if isinstance(value, str) and value.strip()
    ]


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
        texts = [line.text for line in screen_audit.collect(app)]
        app.button(key="build_ppt").click().run(timeout=900)
        app.button(key="build_excel").click().run(timeout=900)
        assert not app.exception, app.exception
        store = dict(app.session_state[ARTIFACT_KEY])
        texts += _deck(store["ppt"].payload)
        texts += _workbook(store["excel"].payload)
        word, _name = document_bytes(captured["sections"])
        texts += _document(word)
    finally:
        patch.undo()
    yield Rendered(request.param, tuple(texts))


def test_기본요금이_피크에_안_매이는_벌에서_피크를_기준으로_말하지_않는다(
    rendered: Rendered,
) -> None:
    """계약형 · 하한형 벌의 네 산출물에 「피크가 기본요금을 정한다」 가 없다.

    **그물이 살아 있는지 같은 판에서 본다** — 이 판이 안 고친 요구사항서 9.4 필수 경고
    (「기본요금은 직전 12개월 중 최대수요로 결정됩니다」)가 두 벌에 다 서 있고, 그물은
    그것을 잡아야 한다. 잡은 것 가운데 그 경고만 빼고 0 이어야 한다 — 경고는 아래
    xfail 못이 따로 문다.

    **반대쪽도 같은 판에서 본다** — 「기본요금을 계약전력으로 매깁니다」 류는 계약형에만
    서고 하한형(요금적용전력 기준)에는 없다. 한 벌을 두 번 띄우지 않으려고 한 시험에 둔다.
    """
    claims = peak_claims(list(rendered.texts))
    head = _warning_head()
    assert any(head in claim for claim in claims), f"{rendered.key} — 그물이 경고를 못 잡았다"
    assert [claim for claim in claims if head not in claim] == [], rendered.key

    hits = [text for text in rendered.texts if CONTRACT_CLAIM.search(text)]
    if rendered.key == "large-a":
        assert hits, "계약형 벌에 기준 문장이 안 섰다 — 그물이 죽었다"
    else:
        assert hits == [], hits


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


@pytest.mark.xfail(
    strict=True,
    reason="요구사항서 9.4 필수 경고가 갈래를 안 가린다 — 원문이라 S170 에 안 고쳤다 (미해결)",
)
def test_계약전력_변경_경고가_기본요금을_피크에_매지_않는다() -> None:
    """**경고 글자 하나를 문다.** 고치면 XPASS 로 빨개진다 — 그때 xfail 을 걷는다."""
    from kwise.report import CONTRACT_CHANGE_WARNING

    assert peak_claims([CONTRACT_CHANGE_WARNING]) == []


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
