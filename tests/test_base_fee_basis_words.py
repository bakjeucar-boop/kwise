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
from itertools import pairwise
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
    #: 덱 스냅과 같은 꼴의 줄 (S232) — ``[산출물, 자리, …글자]``.
    #: 화면은 ``[화면, where, kind, slot, text]`` 다.
    rows: tuple[tuple[str, ...], ...] = ()
    #: 화면 차트의 vega-lite 명세 JSON (S251 · 나-14) — 축 이름이 어떻게 서는지 본다.
    charts: tuple[str, ...] = ()


def _chart_specs(app: Any) -> list[str]:
    """화면에 그려진 차트마다 vega-lite 명세 (``tools\\deck_words.py`` 의 걷기와 같다)."""
    from streamlit.testing.v1.element_tree import Element

    found: list[str] = []

    def walk(node: object) -> None:
        proto = getattr(node, "proto", None) if isinstance(node, Element) else None
        if proto is not None and isinstance(getattr(proto, "spec", None), str) and proto.spec:
            found.append(proto.spec)
        children = getattr(node, "children", None)
        for child in children.values() if isinstance(children, dict) else children or []:
            walk(child)

    walk(getattr(app, "main", None))
    walk(getattr(app, "sidebar", None))
    return found


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


#: 벌마다 한 번만 띄운다 — 두 픽스처(`rendered` · `rendered_sums`)가 같은 벌을 나눠 쓴다.
_RENDERED: dict[str, Rendered] = {}


@pytest.fixture(scope="module", params=["large-a", "large-b-over"])
def rendered(request: pytest.FixtureRequest) -> Iterator[Rendered]:
    """덱 벌 하나를 `render_deck.build_deck` 과 같은 세션 상태로 띄워 네 산출물을 뜬다."""
    yield _render(request.param)


#: 절사 두 못만 더 도는 벌 (S234) — 1-1 자리가 선 벌이다. `small-a2` 화면 3단계 지표
#: 「차이」 · `small-b-sell` 태양광 용량 곡선 고른 용량 줄 · 감도 상세 기준 기본요금 절감 ·
#: `small-ind-a2` 조합 「+ 역률 97%」 대 역률 단독 · 감도 상세 기준 기본요금 절감 ·
#: `large-b-short` 태양광 · ESS 계산 근거 표와 용량 곡선의 초과사용부가금 몫(S243 · 결정 1).
SUM_CASES = ["large-a", "large-b-over", "small-a2", "small-b-sell", "small-ind-a2", "large-b-short"]


@pytest.fixture(scope="module", params=SUM_CASES)
def rendered_sums(request: pytest.FixtureRequest) -> Iterator[Rendered]:
    """절사 못 둘이 도는 벌 — 앞 두 벌은 `rendered` 와 같은 렌더를 쓴다."""
    yield _render(request.param)


def _render(key: str, use: str = "") -> Rendered:
    """``use`` 는 옆단 「용도」 위젯 값이다 (S253) — 벌 정의에는 없는 칸이라 여기서 심는다."""
    slot = f"{key}|{use}" if use else key
    if slot not in _RENDERED:
        _RENDERED[slot] = _build(key, use)
    return _RENDERED[slot]


#: 사람 실물 점검 입력 (S256 확인 사례) — 밑 벌에서 더 심는 것. 역률 99.68 · DR 정산 단가 120 ·
#: 쉬는 날 · 운영 8~17시 · 설치 단가. 벌 정의에는 없는 칸이라 여기서 심는다.
CONFIRM_KEY = "small-a2-pf100-offset-area"


def _render_confirm() -> Rendered:
    slot = "confirm"
    if slot not in _RENDERED:
        _RENDERED[slot] = _build(CONFIRM_KEY, "office", confirm=True)
    return _RENDERED[slot]


def _render_human() -> Rendered:
    """S258 확인 사례 (가) — 사람 실물 입력(S257 뒤 재점검). 태양광은 **남동으로 계산한 결과**가
    저장돼 있고 화면 방위는 남이다(1-3 · 1-5) — 사람 화면에서 읽은 값이 그 판이다."""
    slot = "human"
    if slot not in _RENDERED:
        _RENDERED[slot] = _build(CONFIRM_KEY, "office", confirm=True, human=True)
    return _RENDERED[slot]


def _render_hourly(contract_type: str = "") -> Rendered:
    """S268 결정 2 확인 입력 — 용인 15분 실물(`small-a2`)을 1시간으로 합쳐 올린 벌.

    ``contract_type`` 을 주면 그 종별로 띄운다 (S269 결정 3 — 계약전력 기준 확인 입력).
    """
    slot = f"hourly|{contract_type}" if contract_type else "hourly"
    if slot not in _RENDERED:
        _RENDERED[slot] = _build("small-a2", hourly=True, contract_type=contract_type)
    return _RENDERED[slot]


def _hourly_bytes(source: Path) -> bytes:
    """15분 실물을 1시간으로 합친 업로드 바이트 — 저장소에 파일을 안 남긴다."""
    from kwise.io import load_usage

    energy = load_usage(source).energy_kwh()
    hourly = energy.resample("60min", label="right", closed="right").sum(min_count=1)
    lines = ["날짜시간,사용량(kWh)"]
    lines += [f"{stamp:%Y-%m-%d %H:%M},{value:.2f}" for stamp, value in hourly.items()]
    return ("\n".join(lines) + "\n").encode("utf-8-sig")


def _build(
    case_key: str,
    use: str = "",
    *,
    confirm: bool = False,
    human: bool = False,
    hourly: bool = False,
    contract_type: str = "",
) -> Rendered:
    from dataclasses import replace

    from streamlit.testing.v1 import AppTest

    sys.path.insert(0, str(PROJECT_ROOT / "tools"))
    import render_deck
    import screen_audit

    from kwise.report import slides_bytes
    from kwise.report.document import document_bytes
    from kwise.ui.artifacts import ARTIFACT_KEY
    from kwise.ui.pipeline import ContractForm, SolarInputs
    from kwise.ui.state import input_key
    from kwise.ui.views import compare as compare_view

    case = render_deck.BY_KEY[case_key]
    if confirm:
        case = replace(case, power_factor_pct=99.68)
    if contract_type:
        case = replace(case, contract_type=contract_type, option="")
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
        state["upload_bytes"] = _hourly_bytes(case.csv) if hourly else case.csv.read_bytes()
        state["upload_name"] = "hourly.csv" if hourly else case.csv.name
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
        if use:
            state["building_use"] = use
        # 연면적도 `render_deck` 과 같게 위젯 키로 (S252 결정 9 · `small-a2-pf100-offset-area`)
        if case.floor_area_m2 is not None:
            state["building_area"] = case.floor_area_m2
        # 운영 시간대도 `render_deck` 과 같게 (S271 결정 4 · `small-ind-a1` 은 0 ~ 24시)
        if case.operating_hours is not None:
            state["building_hours"] = case.operating_hours
        # 잉여 처리도 `render_deck` 과 같게 (S234 — `small-b-sell` 은 외부 판매)
        if case.surplus_use:
            state["measure_solar_surplus_use"] = case.surplus_use
        for key in render_deck.ALL_MEASURES:
            state[f"measure_on_{key}"] = True
        state["combination_pick"] = render_deck.ALL_MEASURES
        if confirm:
            state["building_hours"] = (8, 17)
            state["solar_inputs"] = SolarInputs(
                region_key=case.sigungu, area_m2=case.area_m2, unit_cost_won_per_kwp=2_500_000.0
            )
            state[input_key("demand_response", "off_days")] = ["2025-10-02"]
        if human:
            # S258 (가) — 건물명 · 준공 · 야간 진상역률 100 · 저장 방위 남동 · 화면 방위도 남동
            # (S259 — 사람은 방위를 되돌리지 않았다 · 남은 스스로 풀린 값이었다).
            state[input_key("solar", "azimuth")] = "southeast"
            state["building_name"] = "용인건물"
            state["building_year"] = 2000
            state["contract_form"] = replace(state["contract_form"], leading_power_factor_pct=100.0)
            state["diag_pf_lagging"] = 99.68
            state["diag_pf_leading_known"] = True
            state["diag_pf_leading"] = 100.0
            state["solar_inputs"] = SolarInputs(
                region_key=case.sigungu,
                area_m2=case.area_m2,
                density_key="normal",
                azimuth_deg=135.0,
                unit_cost_won_per_kwp=2_500_000.0,
            )
        app.run()
        assert not app.exception, app.exception
        if confirm:
            box = next(item for item in app.checkbox if str(item.label).startswith("정산 단가를"))
            box.check().run()
            app.number_input(key=input_key("demand_response", "unit_price")).set_value(120.0).run()
            assert not app.exception, app.exception
        collected = screen_audit.collect(app)
        screen = [(line.slot, line.text) for line in collected]
        texts = [text for _slot, text in screen]
        # **그림 안 글자도 같은 판에서 뜬다** (S217 5절) — 새 렌더를 안 붙인다.
        deck_words = _deck_words()
        figures = deck_words._screen_figures(app)
        charts = _chart_specs(app)
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
        rows = [("화면", ln.where, ln.kind, ln.slot, ln.text) for ln in collected]
        rows += [tuple(row) for row in deck_words._excel_rows(store["excel"].payload)]
        rows += [tuple(row) for row in deck_words._deck_rows(store["ppt"].payload)]
        rows += [tuple(row) for row in deck_words._word_rows(word)]
    finally:
        patch.undo()
    return Rendered(
        case_key,
        tuple(texts),
        tuple(screen),
        tuple(line.where for line in collected),
        tuple(tuple(str(v) for v in row if v is not None) for row in excel_rows),
        {"excel": store["excel"].payload, "ppt": store["ppt"].payload, "word": word},
        tuple(tuple(row) for row in figures),
        tuple(rows),
        tuple(charts),
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


#: 이름 앞머리. S211 ~ S237 은 여기까지가 같고 한 글자 차로 두 수가 섰다.
OFF_HOURS = "운영시간 외 부하"


def test_운영시간_외_부하는_네_산출물이_같은_이름_같은_값이다(rendered: Rendered) -> None:
    """**「운영시간 외 부하」 는 어디서나 비중이다** (S238 사람 결정 1).

    밖 사용량 ÷ 전체 사용량(`off_hours_energy_share` · 요구사항서 6.1). S211 이
    Excel 에만 「비율」(밖 평균 ÷ 운영시간 평균)을 두고 이름에 식을 달아 갈랐는데
    덱 19벌에서 차가 −18.2 ~ +10.7%p 라 같은 이름 앞머리로 두 수가 섰다 — S238 이
    Excel 한 칸을 비중으로 모았다.

    **한 못이 네 자리를 함께 문다** — 화면만 보면 Excel 이 갈려도 초록이다.
    **실물만 본다** — 그려진 화면 줄 · 구운 PPT · Excel · Word 줄이다.
    """
    이름 = f"{OFF_HOURS} 비중"
    화면 = [text for slot, text in rendered.screen if text.startswith(OFF_HOURS)]
    assert 화면 == [이름], f"화면 이름이 달라졌다 — {화면}"
    라벨 = [i for i, (_slot, text) in enumerate(rendered.screen) if text == 이름]
    slot, 화면값 = rendered.screen[라벨[0] + 1]
    assert slot == "지표", f"라벨 다음이 지표 값이어야 합니다 — {slot} · {화면값}"

    # Excel · Word 는 한 행에 이름과 값이 있다 · PPT 는 이름 문단 다음 문단이 값이다.
    본: dict[str, list[tuple[str, str]]] = {"Excel": [], "PPT": [], "Word": []}
    rows = list(rendered.rows)
    for i, row in enumerate(rows):
        if row[0] in 본 and len(row) > 2 and row[2].startswith(OFF_HOURS):
            value = row[3] if len(row) > 3 else rows[i + 1][-1]
            본[row[0]].append((row[2], value))
    assert 본 == {name: [(이름, 화면값)] for name in 본}, (
        f"{rendered.key} — 화면 {화면값} 과 산출물이 갈렸다 — {본}"
    )


def test_Word_조합_투자비_칸은_빠진_입력을_말하고_세_자리가_같은_글자다(
    rendered: Rendered,
) -> None:
    """**미산출 사유는 실제로 빠진 입력이다** (S238 결정 3 · S207 · S233 ㄱ).

    Word 요약 표 「투자비」 · 조합 표 권장 줄 · 권장안 문장이 같은 사실(권장 조합
    투자비)이다. S237 까지 요약 표만 기본 사유(계약 「미산출 — 하한 규정 미확인」)를
    적어 19벌 다 거짓이었다 — 조합에는 계약 투자비가 없다. 갈래마다의 글자는
    `tests\\test_compare.py` 가 문다(태양광 단가 · 역률 투자비).
    """
    word = [row[1:] for row in rendered.rows if row[0] == "Word"]
    표 = next(cells[0] for cells in word if len(cells) == 3 and cells[1] == "권장 조합")
    요약 = {cells[1]: cells[2] for cells in word if cells[0] == 표 and len(cells) == 3}
    권장 = 요약["권장 조합"]
    조합 = [cells for cells in word if len(cells) == 6 and cells[1] == 권장]
    문장 = [cells[-1] for cells in word if "권장안은" in cells[-1]]
    assert len(조합) == 1 and len(문장) == 1, (조합, 문장)
    칸 = [요약["투자비"], 조합[0][4]]
    assert 칸[0] == 칸[1] and f"투자비는 {칸[0]}," in 문장[0], (rendered.key, 칸, 문장)
    조합칸 = [
        cells[4] for cells in word if len(cells) == 6 and cells[1].startswith(("+ ", "기준선"))
    ]
    assert not any("하한 규정" in text for text in [*칸, *조합칸]), (rendered.key, 칸, 조합칸)


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
            # 역률 여지가 있는 조합이다 — 없으면 이유 줄이 안 선다 (S237 ㄴ).
            power_factor_no_headroom=False,
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
    # 태양광 절감액은 역률 변화를 담은 한 값이라 「역률 영향 반영 시 …」 가 어디에도 안 선다
    # (S262 결정 2 · 앞서는 여기서 그 줄의 표시를 물었다).
    어긋 += [
        f"{name} {line}"
        for name in ("화면", "PPT", "Word", "Excel")
        for line in 글자[name]
        if "역률 영향 반영 시" in line
    ]
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
        money = name.endswith(("(원)", "(kWh)")) and name != "투자비(원)"
        return money and "12개월 환산" not in name

    표머리 = (("태양광 용량 곡선", "용량(kWp)"), ("조합 비교", "조합"), ("감도 상세", "시나리오"))
    for sheet, 첫 in 표머리:
        cells = next((row[2:] for row in 쪽["Excel"] if row[1] == sheet and row[2:3] == [첫]), [])
        if not any("12개월 환산" in cell for cell in cells):
            어긋.append(f"Excel {sheet} 머리에 12개월 환산 열이 없다 {cells}")
        어긋 += [
            f"Excel {sheet} 머리 {c}" for c in cells if 기간값(c) and not c.startswith("기간 ")
        ]
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


def test_대표일_그림_세로축이_그리는_부하를_부른다(rendered: Rendered) -> None:
    """**그림의 축 이름은 그 그림이 그리는 계열을 말한다** (S221 · ㅇ 뿌리).

    태양광 대표일 그림은 원부하 · 순부하와 그 사이 저감분을 그린다 — 발전 출력 선은
    17세션에 뺐다. 그런데 세로축이 「출력 (kW)」 이라 적혀 화면 · PPT · Word 세 자리에
    섰다. 같은 자료를 그리는 ESS 대표일은 「부하 (kW)」 다.

    **그려진 글자를 본다 — 소스 상수를 안 본다.** 원부하 범례가 선 그림마다 세로축
    이름이 「부하 (kW)」 인지 · 「출력」 이 없는지. 화면은 축 이름이 그림 이름 칸에
    든다. **세 산출물을 한 못으로 문다** — 한쪽만 되돌려도 빨갛다. 축 이름은 기간
    길이로 안 갈린다(221세션 절 1-3) — 두 벌이 12개월이라도 뜻은 같다.
    """
    groups: defaultdict[tuple[str, str], list[tuple[str, ...]]] = defaultdict(list)
    for row in rendered.figures:
        groups[(row[0], row[1])].append(row)
    seen: set[str] = set()
    어긋: list[str] = []
    for (kind, where), rows in groups.items():
        if not any(row[-1].startswith("원부하") for row in rows):
            continue
        seen.add(kind)
        names = [where] if kind == "화면" else [row[-1] for row in rows if row[2] == "축 이름"]
        loads = [name for name in names if name.endswith("부하 (kW)")]
        if not loads or [name for name in names if "출력" in name]:
            어긋.append(f"{kind} {where} {names}")
    assert seen == {"화면", "PPT", "Word"}, (rendered.key, seen)
    assert 어긋 == [], (rendered.key, 어긋)


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


# ============================================================ S232 · 절사와 자릿수

WON_CELL = re.compile(r"^(-?\d[\d,]*)\s*원$")
NUMBER = re.compile(r"-?\d[\d,]*")


def _won(text: str) -> int | None:
    found = WON_CELL.match(text.strip())
    return int(found.group(1).replace(",", "")) if found else None


def _worksheet_tables(
    rows: tuple[tuple[str, ...], ...],
) -> dict[tuple[str, str], list[tuple[str, str, str]]]:
    """계산 근거 표 — Excel 부록 A · PPT 표 · Word 표를 ``(라벨, 산식, 값)`` 줄로."""
    out: dict[tuple[str, str], list[tuple[str, str, str]]] = defaultdict(list)
    for row in rows:
        # 첫 칸이 「수단」 이다 — 판다스 줄 번호 열이 없다 (S243 · 결정 2)
        if row[0] == "Excel" and row[1] == "부록 A 산출 근거" and len(row) >= 4:
            key, label, rest = row[2], row[3], row[4:]
        elif row[0] in ("PPT", "Word") and "표" in row[1] and len(row) >= 3:
            key, label, rest = row[1], row[2], row[3:]
        else:
            continue
        out[(row[0], key)].append(
            (label.strip(), rest[0] if len(rest) >= 2 else "", rest[-1] if rest else "")
        )
    return out


#: 청구 표의 줄 — 더하면 「합계」 다.
BILL_LINES = ("역률 조정 전 기본요금", "소계", "역률 요금", "초과사용부가금")


def _saving_sum(values: dict[str, int], labels: set[str], totals: list[int]) -> int | None:
    """「기간 절감액」 이 서야 할 셈 — 태양광 · ESS · 계약 · 선택요금 · 역률. 없으면 ``None``."""
    if "설치 용량" in labels or "규격 출력" in labels:
        # 절감액에 든 몫을 다 줄로 세운다 (S243 · 결정 1) — 태양광 · ESS 가 같은 이름이다.
        # ESS 는 앞서 기본 · 전력량 둘만 적어 이 셈 밖이었다(`large-b-short` 17,311,000원).
        return sum(
            values.get(name, 0)
            for name in (
                "기간 기본요금 절감",
                "기간 전력량요금 절감",
                "기간 역률요금 절감",
                "초과사용부가금",
                "_잉여",
            )
        )
    if "현재 기본요금" in values and "조정 후 기본요금" in values:
        return (
            values["현재 기본요금"]
            - values["조정 후 기본요금"]
            + values.get("기간 역률요금 절감", 0)
        )
    crossed = [
        v for name, v in values.items() if name.endswith(" 총 요금") and name != "현행 종별 총 요금"
    ]
    if "현행 종별 총 요금" in values and crossed:
        return values["현행 종별 총 요금"] - crossed[0]
    if len(totals) == 2:
        return totals[0] - totals[1]
    if "현재 역률 요금" in values and "목표 역률 요금" in values:
        return values["현재 역률 요금"] - values["목표 역률 요금"]
    return None


def _sum_gaps(lines: list[tuple[str, str, str]]) -> list[str]:
    """표 하나에서 셈이 안 서는 자리 — 청구 합계 · 참고 산식 · 기간 절감액(S232 · S233)."""
    gaps: list[str] = []
    values: dict[str, int] = {}
    block: dict[str, int] = {}
    totals: list[int] = []
    for label, formula, text in lines:
        value = _won(text)
        if value is None:
            continue
        if label.startswith("참고") and "+" in formula:
            terms = sum(int(n.replace(",", "")) for n in NUMBER.findall(formula))
            if terms != value:
                gaps.append(f"{label} 산식 {formula} · 값 {text}")
        if label in BILL_LINES:
            block[label] = value
        if label == "합계":
            if {"역률 조정 전 기본요금", "소계"} <= set(block) and sum(block.values()) != value:
                gaps.append(f"청구 줄 합 {sum(block.values()):,} · 합계 {value:,}")
            totals.append(value)
            block = {}
        values.setdefault(label, value)
        if label.startswith("잉여 "):
            values["_잉여"] = value
    saving = values.get("기간 절감액")
    expected = _saving_sum(values, {label for label, _, _ in lines}, totals)
    if saving is not None and expected is not None and expected != saving:
        gaps.append(f"기간 절감액 셈 {expected:,} · 적힌 {saving:,}")
    return gaps


def _sheet(rows: tuple[tuple[str, ...], ...], name: str) -> list[dict[str, str]]:
    """Excel 시트 한 장 — 머리 줄 이름으로 칸을 단다(빈 칸이 빠지는 줄은 앞 칸만 맞는다)."""
    sheet = [row[2:] for row in rows if row[:2] == ("Excel", name)]
    if not sheet:
        return []
    head = sheet[0]
    return [dict(zip(head, cells, strict=False)) for cells in sheet[1:]]


def _lead_won(text: str) -> int | None:
    """「53,580,000 원 (투자 불필요)」 · 「53,580,000원 (12개월 환산 …)」 의 앞 금액."""
    found = re.match(r"^(-?\d[\d,]*)\s*원", text.strip())
    return int(found.group(1).replace(",", "")) if found else None


def test_절사한_줄끼리의_셈이_적힌_합계와_선다(rendered_sums: Rendered) -> None:
    """**적힌 줄을 더하거나 빼면 적힌 합계다** (S232 ㄱ · S233 에 1-1 자리 전부로 넓혔다).

    줄마다 천 원 절사해 적힌 줄의 셈이 적힌 합계와 1,000 ~ 2,000원 어긋났다(S231 3-3).
    합계는 원값 절사 그대로 두고 잘린 나머지가 큰 줄을 올린다(사람이 정한 A ·
    :func:`kwise.money.balance_won`) · 두 합계의 차는 적힌 두 합계의 차다(웹 대화창 판단 ㄴ ·
    :func:`kwise.money.gap_won`). **식을 다시 적지 않는다** — 산출물에 실제로 적힌 글자끼리
    셈한다. 무는 자리 — 계산 근거 표(Excel 부록 A · PPT · Word)의 청구 합계 · 참고 산식 ·
    태양광 · 계약(종별 안 · 종별 넘김) · 선택요금 · 역률 · 화면 3단계 「차이」 · Excel 요금
    계산 명세 관측 · 보정 합계 · Excel 요약 합계 · Excel 조합 비교 · Word 요약 표 「투자 없이」 ·
    화면 3단계 지표 「차이」(S234 — 계산 근거 차이의 만원 꼴).
    """
    rendered = rendered_sums
    gaps: list[str] = []
    for (output, key), lines in _worksheet_tables(rendered.rows).items():
        gaps += [f"{output} {key} — {gap}" for gap in _sum_gaps(lines)]

    # 화면 3단계 — 단순 합 · 합산효과 · 차이
    cells = [
        row[-1]
        for row in rendered.rows
        if row[0] == "화면"
        and row[1].endswith("3단계 · 개선안 조합 › 계산 근거")
        and row[2] == "Dataframe"
    ]
    won = [value for value in map(_won, cells) if value is not None]
    if len(won) >= 3 and won[1] - won[0] != won[2]:
        gaps.append(f"화면 3단계 {won[:3]}")
    # 지표 「차이」 도 계산 근거의 차이다 (S234 ㄱ · `small-a2` −7,000원/년 대 −8,000원)
    from kwise import money

    metric = [
        row[-1]
        for row in rendered.rows
        if row[0] == "화면" and row[1].endswith("3단계 · 개선안 조합") and row[2] == "Metric"
    ]
    if len(won) >= 3 and "차이" in metric:
        shown = metric[metric.index("차이") + 1]
        if shown != money.won_short(won[2], reason="") + "/년":
            gaps.append(f"화면 3단계 지표 차이 {shown} · 계산 근거 {won[2]:,}")

    parts = ("역률 조정 전 기본요금(원)", "역률 요금(원)", "초과사용부가금(원)")
    for column in _sheet(rendered.rows, "요금 계산 명세"):
        shared = sum(float(column[name]) for name in parts)
        for energy, total in (
            ("전력량요금(원)", "합계(원)"),
            ("전력량요금 보정(원)", "합계 보정(원)"),
        ):
            if abs(shared + float(column[energy]) - float(column[total])) > 0.5:
                gaps.append(
                    f"요금 계산 명세 {column['월']} {energy} 줄 합 · {total} {column[total]}"
                )

    # Excel 태양광 용량 곡선 — 줄마다 몫 열(「하한 걸린 달」 과 자가소비 절감액 사이)의 합이
    # 자가소비 절감액이다 (S243 · 결정 1 · 가-4). **잉여 수익을 실은 고른 줄도 문다** (S256 고6) —
    # S243 이 남긴 꼬리(`small-b-sell` 80 kWp −1,000원)를 적힌 기간 절감액 − 적힌 잉여로 닫았다.
    heads = [row[2:] for row in rendered.rows if row[:2] == ("Excel", "태양광 용량 곡선")][:1]
    if heads:
        head = list(heads[0])
        shares = head[head.index("하한 걸린 달") + 1 : head.index("기간 자가소비 절감액(원)")]
        for point in _sheet(rendered.rows, "태양광 용량 곡선"):
            capacity = point["용량(kWp)"]
            # 용량 0 줄은 자가소비율 칸이 비어 칸 자리가 밀린다 — 몫도 다 0 이다
            if float(capacity) == 0:
                continue
            written = sum(float(point[name]) for name in shares)
            if written != float(point["기간 자가소비 절감액(원)"]):
                gaps.append(f"용량 곡선 {capacity} kWp {shares} 합 {written:,.0f}")

    summary = {
        row[3]: _lead_won(row[4])
        for row in rendered.rows
        if row[:3] == ("Excel", "요약", "요금") and len(row) >= 5
    }
    if summary.get("합계 (관측 기준)") is not None:
        parts_won = [
            summary.get(name) or 0 for name in ("기본요금", "전력량요금", "초과사용부가금")
        ]
        if sum(parts_won) != summary["합계 (관측 기준)"]:
            gaps.append(
                f"Excel 요약 줄 합 {sum(parts_won):,} · 합계 {summary['합계 (관측 기준)']:,}"
            )

    combos = [row[2:] for row in rendered.rows if row[:2] == ("Excel", "조합 비교")][1:]
    if combos:
        base = float(combos[0][3])
        for combo in combos[1:]:
            # 여지 없는 칸은 「없음」 이다 (S251 사람 결정) — 줄일 몫이 0 이라는 뜻이다.
            saving = 0.0 if combo[4] == NO_SAVING_WORD else float(combo[4])
            if base - float(combo[3]) != saving:
                gaps.append(
                    f"조합 비교 {combo[0]} 차 {base - float(combo[3]):,.0f} · 절감액 {combo[4]}"
                )

    free = {
        row[2]: 0 if row[3] == NO_SAVING_WORD else _won(row[3])
        for row in rendered.rows
        if row[0] == "Word"
        and len(row) >= 4
        and row[2]
        in ("투자 없이 가능한 기간 절감액", "선택요금 전환 (기간)", "계약전력 조정 (기간)")
    }
    if len(free) == 3 and None not in free.values():
        together = (free["선택요금 전환 (기간)"] or 0) + (free["계약전력 조정 (기간)"] or 0)
        if together != free["투자 없이 가능한 기간 절감액"]:
            gaps.append(f"Word 투자 없이 {free}")
    assert gaps == [], (rendered.key, gaps)


#: 「없음」 칸 — 절감이 없다는 결론이라 셈에서 0 이다.
NO_SAVING_WORD = "없음"


def test_조정한_표기_값이_사실마다_네_산출물에서_같은_글자다(rendered_sums: Rendered) -> None:
    """**사실 하나는 어느 산출물에서나 한 글자다** (S233 ㄱ · 웹 대화창 판단).

    한 표 안의 셈을 맞추려 올린 값이 다른 산출물에서 옛 글자로 남으면 같은 사실이 두
    글자로 선다(S232 가 그래서 다섯 자리를 멈췄다). 표기 값을 사실마다 한 자리
    (`kwise.report.notices`)에서 만들고 모든 자리가 그것을 쓴다 — 여기서 **실물 글자**로
    본다. 사실 넷:

        청구서 줄    계산 근거 현행 청구 표 · Excel 요약 요금 · Word 요금 구조 표 · 역률 표 현재
        선택요금 절감 계산 근거 · Excel 요약 · 수단별 결과 · 조합 비교 첫 줄 · Word 요약 표
        조합 절감    Excel 조합 비교 · Word 조합 표 · Word 요약 표 기간 총 절감액 · 감도 상세 기준
        태양광 줄    계산 근거 태양광 표 · Excel 태양광 용량 곡선 같은 용량 줄(잉여 수익을 실은
                     벌도 · S234) · Excel 감도 상세 기준 줄(1,000원 안 · S234 ㄴ)
        조합 대 단독 Excel 조합 비교 줄 · 그 줄에 든 단독 수단의 수단별 결과(1,000원 안 · S234 ㄴ)
    """
    rendered = rendered_sums
    rows = rendered.rows
    seen: dict[str, dict[str, int]] = defaultdict(dict)
    tables = _worksheet_tables(rows)

    # 청구서 줄 — 현행 청구 표(선택요금 표의 첫 청구서) · 역률 표의 현재 역률 요금
    for (output, key), lines in tables.items():
        values = [(label, _won(text)) for label, _, text in lines]
        labels = [label for label, _ in values]
        if "현재 역률 요금" in labels:
            seen["청구 역률 요금"][f"{output} {key}"] = dict(values)["현재 역률 요금"] or 0
        # 현행 청구서 — 「현행 …」 줄 아래 첫 「합계」 까지(PPT 는 현행 · 최적을 두 장에 둔다)
        heads = [i for i, label in enumerate(labels) if label.startswith("현행 ")]
        if not heads or "합계" not in labels[heads[0] :]:
            continue
        start = heads[0]
        end = start + labels[start:].index("합계")
        got = {label: value for label, value in values[start:end] if value is not None}
        if "역률 조정 전 기본요금" in got and "소계" in got:
            place = f"{output} {key}"
            seen["청구 기본요금(역률 반영)"][place] = got["역률 조정 전 기본요금"] + got.get(
                "역률 요금", 0
            )
            seen["청구 전력량요금"][place] = got["소계"]
            seen["청구 역률 요금"][place] = got.get("역률 요금", 0)
    for row in rows:
        if row[:3] == ("Excel", "요약", "요금") and len(row) >= 5:
            if row[3] == "기본요금":
                seen["청구 기본요금(역률 반영)"]["Excel 요약"] = _lead_won(row[4]) or 0
            if row[3] == "전력량요금":
                seen["청구 전력량요금"]["Excel 요약"] = _lead_won(row[4]) or 0
        if row[0] == "Word" and len(row) >= 4 and row[2] in ("기본요금", "전력량요금"):
            value = _lead_won(row[3])
            if value is not None and "%" in row[3]:
                name = "청구 기본요금(역률 반영)" if row[2] == "기본요금" else "청구 전력량요금"
                seen[name][f"Word {row[1]}"] = value

    # 선택요금 절감
    for (output, key), lines in tables.items():
        for label, formula, text in lines:
            if label == "기간 절감액" and formula == "현행 합계 − 최적 합계":
                seen["선택요금 절감"][f"{output} {key}"] = _won(text) or 0
    for row in rows:
        if row[:4] == ("Excel", "요약", "개선 여지", "선택요금 전환 (기간)"):
            seen["선택요금 절감"]["Excel 요약"] = _lead_won(row[4]) or 0
        if row[:2] == ("Excel", "수단별 결과") and row[2].startswith("선택요금 전환"):
            seen["선택요금 절감"]["Excel 수단별 결과"] = int(row[4].replace(",", ""))
        if row[:2] == ("Excel", "조합 비교") and row[2].startswith("선택요금 전환"):
            seen["선택요금 절감"]["Excel 조합 비교"] = int(float(row[6]))
        if row[0] == "Word" and len(row) >= 4 and row[2] == "선택요금 전환 (기간)":
            seen["선택요금 절감"][f"Word {row[1]}"] = _won(row[3]) or 0

    # 조합 절감 — 조합마다
    combos = {
        row[2]: int(float(row[6]))
        for row in rows
        if row[:2] == ("Excel", "조합 비교") and re.fullmatch(r"-?[\d.]+", row[6])
    }
    for row in rows:
        if row[0] == "Word" and len(row) >= 5 and row[2] in combos and _won(row[4]) is not None:
            seen[f"조합 절감 {row[2]}"]["Excel 조합 비교"] = combos[row[2]]
            seen[f"조합 절감 {row[2]}"][f"Word {row[1]}"] = _won(row[4]) or 0
        if row[0] == "Word" and len(row) >= 4 and row[2] == "기간 총 절감액":
            best = _won(row[3])
            assert best in combos.values(), (rendered.key, best, combos)
    for column in _sheet(rows, "감도 상세"):
        if column.get("시나리오") == "기준" and "기간 절감액(원)" in column:
            value = int(float(column["기간 절감액(원)"]))
            near = [v for v in combos.values() if abs(v - value) <= 1_000]
            assert near == [] or value in near, (rendered.key, "감도 상세", value, combos)
    # 조합 절감 대 단독 수단 — 조합에 든 단독 수단 하나와 1,000원 안이면 원값이 같은 다른
    # 사실이라 한 글자다(S234 ㄴ · `small-ind-a2` 「+ 역률 97%」 113,000 대 역률 단독 112,000).
    alone = {
        re.match(r"[^\d(]+", row[2]).group(0).strip(): int(row[4].replace(",", ""))  # type: ignore[union-attr]
        for row in rows
        if row[:2] == ("Excel", "수단별 결과")
        and len(row) > 4
        and re.fullmatch(r"-?[\d,]+", row[4])
    }
    for row in rows:
        if row[:2] != ("Excel", "조합 비교") or row[2] not in combos or len(row) < 5:
            continue
        close = [
            (name, value)
            for name, value in alone.items()
            if name in row[4] and value and abs(value - combos[row[2]]) <= 1_000
        ]
        if len(close) == 1:
            seen[f"조합 절감 {row[2]}"]["Excel 조합 비교"] = combos[row[2]]
            seen[f"조합 절감 {row[2]}"][f"단독 {close[0][0]}"] = close[0][1]

    # 태양광 줄 — 계산 근거 표의 용량과 같은 곡선 줄. 잉여 수익을 실은 벌도 같은 글자다
    # (S234 ㄱ · `small-b-sell` 곡선 1,003,000 대 계산 근거 1,004,000 — 곡선 줄 절감액은 달라도).
    for (output, key), lines in tables.items():
        texts = {label: text for label, _, text in lines}
        if "설치 용량" not in texts:
            continue
        capacity = texts["설치 용량"].replace(" kWp", "").replace(",", "")
        for sensitivity in _sheet(rows, "감도 상세"):
            # 감도 상세 기준 줄의 기본 · 전력량 절감 — 태양광 줄과 1,000원 안이면 한 글자(S234 ㄴ)
            if sensitivity.get("시나리오") != "기준":
                continue
            for heading, label in (
                ("기간 기본요금 절감액(원)", "기간 기본요금 절감"),
                ("기간 전력량요금 절감액(원)", "기간 전력량요금 절감"),
            ):
                line = _won(texts.get(label, ""))
                if heading in sensitivity and line is not None:
                    value = int(float(sensitivity[heading]))
                    if abs(value - line) <= 1_000:
                        seen[f"태양광 {label} {capacity}"]["Excel 감도 상세"] = value
                        seen[f"태양광 {label} {capacity}"][f"{output} {key}"] = line
        for row in rows:
            cells = row[2:]
            if (
                row[:2] == ("Excel", "태양광 용량 곡선")
                and len(cells) > 9
                and re.fullmatch(r"[\d.]+", cells[0])
                and float(cells[0]) == float(capacity)
            ):
                seen[f"태양광 기본 {capacity}"]["Excel 곡선"] = int(float(cells[7]))
                seen[f"태양광 전력량 {capacity}"]["Excel 곡선"] = int(float(cells[8]))
                seen[f"태양광 기본 {capacity}"][f"{output} {key}"] = (
                    _won(texts["기간 기본요금 절감"]) or 0
                )
                seen[f"태양광 전력량 {capacity}"][f"{output} {key}"] = (
                    _won(texts["기간 전력량요금 절감"]) or 0
                )

    split = {fact: places for fact, places in seen.items() if len(set(places.values())) > 1}
    assert split == {}, (rendered.key, split)
    assert {"청구 기본요금(역률 반영)", "청구 전력량요금"} <= set(seen), (
        rendered.key,
        sorted(seen),
    )
    assert len(seen["청구 전력량요금"]) >= 4, (rendered.key, seen["청구 전력량요금"])


def _metric_values(rows: tuple[tuple[str, ...], ...]) -> list[tuple[str, str, str]]:
    """``(산출물 자리, 이름, 값)`` — 화면 지표 · PPT 지표는 이름 줄 다음 줄이 값이다."""
    out: list[tuple[str, str, str]] = []
    for here, after in pairwise(rows):
        if here[0] == "화면" and here[2:4] == ("Metric", "라벨") and after[3] == "지표":
            out.append((f"화면 {here[1]}", here[4], after[4]))
        if here[0] == "PPT" and len(here) == 3 and len(after) == 3 and here[1] == after[1]:
            out.append((f"PPT {here[1]}", here[2], after[2]))
    for row in rows:
        if row[0] == "Excel" and len(row) >= 4 and row[1] in ("요약", "진단"):
            out.append((f"Excel {row[1]}", row[-2], row[-1]))
        if row[0] in ("Word", "PPT", "Excel") and len(row) >= 3:
            out.append((f"{row[0]} {row[1]} 표", row[-3] if len(row) >= 5 else row[-2], row[-1]))
        if row[0] == "Word":
            text = row[-1]
            for name, found in (
                ("관측 최대수요", re.search(r"관측 최대수요는 ([\d,.]+ kW)", text)),
                ("요금적용전력", re.search(r"요금적용전력은 ([\d,.]+ kW) 입니다", text)),
            ):
                if found:
                    out.append(("Word 문장", name, found.group(1)))
    return out


#: 자릿수 증상 사실 (S192 증상 6) — 사실마다 서는 이름.
DIGIT_FACTS: dict[str, tuple[str, ...]] = {
    "관측 최대수요": ("관측 최대수요", "최대 수요"),
    "요금적용전력": ("요금적용전력", "최대수요 = 요금적용전력"),
}


def test_자릿수_증상_사실이_네_산출물에서_같은_글자다(rendered: Rendered) -> None:
    """**같은 사실은 화면 · PPT · Excel · Word 에서 같은 글자다** (S232 ㄴ · 교차 못).

    S192 증상 6 — 요금적용전력이 화면 「132.0 kW」 · PPT 「132 kW」 · Excel 「132.0 kW」,
    하한 전 최대수요가 화면 「132.3 kW」 · PPT 장10 「132 kW」 로 갈렸다. 자릿수를
    ``kwise.report.notices`` 의 사실마다 한 자리로 모았다. 「최대수요」 는 1단계(관측)와
    계약 카드(하한 전) 두 사실이 이름을 나눠 쓰므로 **값 글자가 한 벌 안에서 둘을 넘지
    않는지**와 **소수 자리가 사실마다 하나인지**를 본다.

    **안 무는 것** — 잉여와 ESS 필요 용량은 이 두 벌(대형)에 안 선다(잉여 0 · ESS 는
    최소 규격 안). 두 사실은 덱 스냅 대조(232세션 절 3-2)가 본다.

    **S235 에 넓혔다** — 자릿수 결함 넷(① Excel 수 칸 서식 · ② 그림 눈금 쉼표 · ③ 부록 B
    값 쉼표 · ④ 만원 카드 안의 증감과 ESS 사양 표 한 열의 단위)이 두 벌에 다 선다.
    """
    values = _metric_values(rendered.rows)
    kw = re.compile(r"^-?[\d,]+(?:\.\d+)? kW$")

    def spellings(names: tuple[str, ...]) -> dict[str, set[str]]:
        found: dict[str, set[str]] = defaultdict(set)
        for where, name, value in values:
            if name.strip() in names and kw.match(value):
                found[value].add(where.split(" ")[0])
        return found

    for fact in ("관측 최대수요", "요금적용전력"):
        found = spellings(DIGIT_FACTS[fact])
        outputs = set().union(*found.values()) if found else set()
        assert len(outputs) >= 2, (rendered.key, fact, dict(found))
        assert len(found) == 1, (rendered.key, fact, dict(found))

    # 「최대수요」 한 이름 — 1단계 관측과 계약 카드 하한 전. 값은 둘까지 · 자리는 한 자리.
    found = spellings(("최대수요",))
    outputs = set().union(*found.values()) if found else set()
    assert {"화면", "PPT", "Word"} <= outputs, (rendered.key, dict(found))
    places = {len(value.split(" ")[0].partition(".")[2]) for value in found}
    assert places == {1}, (rendered.key, dict(found))

    # **S235 — 자릿수 결함 넷(①~④)이 두 벌에 다 선다** (웹 대화창 판단 ㄱ).
    # ① Excel 수 칸은 셀 서식으로 쉼표 · 한 열 한 자리 · 사실 열의 자리는 위 글자와 같다 ·
    #   서식이 값을 안 자른다(서식 자리보다 긴 값이 남는다).
    from openpyxl import load_workbook

    sheet_places: dict[str, int] = {}
    어긋: list[str] = []
    longer = 0
    for sheet in load_workbook(io.BytesIO(rendered.payloads["excel"])).worksheets:
        heads = [str(cell.value) for cell in sheet[1]]
        for column in sheet.iter_cols(min_row=2):
            cells = [
                c
                for c in column
                if isinstance(c.value, int | float) and not isinstance(c.value, bool)
            ]
            if not cells:
                continue
            name = f"{sheet.title}:{heads[cells[0].column - 1]}"
            patterns = {cell.number_format for cell in cells}
            if len(patterns) != 1 or not str(next(iter(patterns))).startswith("#,##0"):
                어긋.append(f"{name} {sorted(map(str, patterns))}")
                continue
            sheet_places[name] = len(str(next(iter(patterns))).partition(".")[2])
            longer += sum(round(cell.value, sheet_places[name]) != cell.value for cell in cells)
    assert 어긋 == [], (rendered.key, 어긋[:8])
    assert longer, (rendered.key, "서식 자리보다 긴 값이 한 칸도 없다 — 서식이 값을 잘랐나")
    for fact, head in (
        ("관측 최대수요", "월별 집계:관측 최대수요(kW)"),
        ("요금적용전력", "월별 집계:요금적용전력(kW)"),
    ):
        spelled = {len(v.split(" ")[0].partition(".")[2]) for v in spellings(DIGIT_FACTS[fact])}
        assert spelled == {sheet_places[head]}, (rendered.key, fact, spelled, sheet_places[head])

    # ② 그림 눈금 · ③ 부록 B 값 — 정수부 네 자리 이상에 쉼표가 없는 글자가 없다.
    bare = re.compile(r"^[−-]?\d{4,}(?:\.\d+)?$")
    ticks = [row[-1] for row in rendered.figures if row[-2] == "눈금"]
    assert [t for t in ticks if re.fullmatch(r"[−-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?", t)], rendered.key
    assert [t for t in ticks if bare.match(t)] == [], rendered.key
    부록 = [
        row
        for row in rendered.rows
        if row[0] in ("Word", "Excel") and ("법령 유래" in row or "판단값" in row)
    ]
    # 표본 — 네 자리 법령 유래 값(교육용 종별 문턱 1,000 은 S252 결정 5 로 부록 B 에서 빠졌다).
    값 = [
        cell for row in 부록 for cell in row[2:] if "중소형DR 산업체 계약전력 상한" in "".join(row)
    ]
    assert 값 and not [cell for cell in 값 if bare.match(cell)], (rendered.key, 값)
    # 종별 문턱은 요금표 한 자리다 — 부록 B 에 기준 데이터 몫으로 따로 서지 않는다 (S252 결정 5).
    assert not [row for row in 부록 if "임계 계약전력" in "".join(row)], rendered.key

    # ④ 만원으로 적는 화면 카드 안의 증감 · ESS 사양 표의 한 열은 만원으로 적는다.
    won = re.compile(r"(?<![만억\d,])-?[\d,]*\d원")
    screen = [row for row in rendered.rows if row[0] == "화면" and len(row) >= 5]
    shown, 카드 = "", []
    for row in screen:
        if row[2] == "Metric" and row[3] == "지표":
            shown = row[4]
        elif row[2] == "Metric" and row[3] == "증감" and re.search("만원|억원", shown):
            카드.append((shown, row[4]))
    # 태양광 카드 증감 「역률 영향 반영 시」 는 S262 결정 2 로 걷혔다 — 남은 증감도 만원이다.
    assert not [c for c in 카드 if "역률 영향 반영 시" in c[1]], (rendered.key, 카드)
    assert [c for c in 카드 if won.search(c[1])] == [], (rendered.key, 카드)
    ess = [
        row[4]
        for row in screen
        if row[2] == "Dataframe"
        and row[1].endswith("6. ESS — 입력과 결과")
        and row[4].endswith("원")
    ]
    if [cell for cell in ess if "만원" in cell]:
        assert [c for c in ess if won.fullmatch(c) and c != "0원"] == [], (rendered.key, ess)


# ===================================================================== S251 사람 결정 · 결정 1 · 2


def _plain(text: str) -> str:
    return text.replace("**", "").removeprefix("⚠").strip()


def test_상향_권고는_화면_Word_PPT_가_같은_글자다() -> None:
    """**계약전력을 넘겨 쓰는 벌의 상향 권고가 PPT 에도 한 줄 선다** (S251 사람 결정 · 마-17).

    글자는 화면 · Word 가 쓰는 안내 그 자리다 — 새 글자를 짓지 않는다. PPT 는 계약전력
    조정 장 각주에 선다. 재료 — 화면에 선 줄(목표 · 몫이 든)을 먼저 찾는다.
    """
    rendered = _render("large-b-short")
    head = "초과가 0 이 되는 계약전력은"
    screen = {_plain(row[-1]) for row in rendered.rows if row[0] == "화면" and head in row[-1]}
    assert len(screen) == 1 and "총액이" in next(iter(screen)), screen
    line = next(iter(screen))
    word = [row for row in rendered.rows if row[0] == "Word" and _plain(row[-1]) == line]
    ppt = [row for row in rendered.rows if row[0] == "PPT" and line in _plain(row[-1])]
    assert word, line
    assert ppt, (line, [row for row in rendered.rows if row[0] == "PPT" and head in row[-1]])


def test_PPT_수단_장으로_옮긴_주의사항은_Word_3장이_적는_글자다(rendered: Rendered) -> None:
    """**주의사항은 수단 장 ※ 한 줄씩 · 있는 글자만 쓴다** (S251 사람 결정 · 마-19 · S258 결정 3).

    「주의사항」 장은 없고 덱 끝은 「다음 단계」 다. 옮긴 줄마다 Word 3장 수단 주의사항 목록에
    같은 글자(역률 추정은 그 첫 문장 · 경제성DR 위약금은 첫 문장을 뗀 뒤)가 선다 — 새 사실을
    더하지 않는다.
    """
    ppt = [row for row in rendered.rows if row[0] == "PPT"]
    last = max(int(row[1].removesuffix("표")) for row in ppt if row[1].removesuffix("표").isdigit())
    assert [row[2] for row in ppt if row[1] == str(last)][:1] == ["다음 단계"], rendered.key
    assert not [row for row in ppt if len(row) == 3 and row[2] == "주의사항"], rendered.key
    words = [row[-1] for row in rendered.rows if row[:2] == ("Word", "List Bullet")]
    fixed = (
        "계약전력을 하향할 경우",
        "계약전력 하향은 되돌리기",
        "감축계획량을 채우지",
        "무효전력 실측이 없어",
        "정해진 규칙 한 가지로 운전한다고",  # S263 결정 1 — 옛 「규칙기반 단일 디스패치」
        "기본요금을 계약전력으로 매겼습니다",
        "종별을 확인하십시오",
    )
    moved = [
        row[-1].removeprefix("※ ")
        for row in ppt
        if len(row) == 3 and row[2].startswith("※ ") and any(w in row[2] for w in fixed)
    ]
    assert moved, (rendered.key, "재료 — 옮긴 줄")
    for line in moved:
        assert [w for w in words if line[:-1] in w], (rendered.key, line)


def test_Excel_조합_비교의_여지_없는_칸은_없음이다() -> None:
    """**낮출 몫이 없는 수단만 더한 줄의 절감액은 「없음」 이다** (S251 사람 결정 · 마-21).

    `small-a2` 의 「계약전력 조정」 줄 — 하한이 안 걸려 계약전력을 낮춰도 한 푼 안 준다.
    다른 산출물(수단별 결과 · 요약)이 같은 사실을 「없음」 으로 적는다. 기준선 줄의 0 은
    계산해서 0 이라 수 그대로다.

    **S257 결정 1 곁** — 그 줄은 앞 줄(기준선 0원)과 금액 · 요금제가 같아 이제 서지 않는다.
    「없음」 칸은 그런 줄이 설 때(마지막 줄)의 꼴로 남는다 — 이 벌에서는 줄이 없음을 본다.
    """
    rendered = _render("small-a2")
    combo = {row[2]: row for row in rendered.rows if row[:2] == ("Excel", "조합 비교")}
    head = list(combo["조합"])
    saving, annual = head.index("기간 절감액(원)"), head.index("12개월 환산 절감액(원)")
    # 재료 — 같은 벌 수단별 결과가 계약전력 조정을 「없음」 으로 적는다.
    measure = [row for row in rendered.rows if row[:2] == ("Excel", "수단별 결과")]
    assert [row for row in measure if row[2].startswith("계약전력 조정") and "없음" in row], measure
    assert "계약전력 조정" not in combo, list(combo)
    base = combo["기준선 (현행)"]
    assert (base[saving], base[annual]) == ("0", "0"), base
    others = [r for name, r in combo.items() if name not in ("조합", "계약전력 조정")]
    assert all(r[saving] != "없음" for r in others), others


def test_Word_감도_표는_Excel_감도_시트와_같은_표기다(rendered: Rendered) -> None:
    """**Word 감도 표 칸은 Excel 「감도」 시트 「표시」 와 같은 글자다** (S251 결정 2 · S233).

    앞서 Word 는 날 글자(`large-b-over` 「6,000.0kW (… 6,000.0 ~ 6,000.0kW)」)를, Excel 은 감도
    상세의 표기 값(천 원 절사 · 0자리)을 적었다.
    """
    excel = {
        row[2]: row[-1]
        for row in rendered.rows
        if row[:2] == ("Excel", "감도") and row[2] != "지표" and len(row) > 3
    }
    assert excel, rendered.key
    word = {row[2]: row[3] for row in rendered.rows if row[0] == "Word" and len(row) == 4}
    # Word 는 기준값이 없는 줄(「미산출」)을 안 싣는다 — 싣는 줄만 맞댄다.
    shared = [label for label in excel if label in word]
    assert shared, (rendered.key, sorted(excel))
    for label in shared:
        assert word[label] == excel[label], (rendered.key, label, word[label], excel[label])


def test_화면_차트의_세로축_이름은_가로로_선다(rendered: Rendered) -> None:
    """**이름이 있는 세로축은 다 축 위에 가로로 얹는다** (나-14 · S163 `charts.FLAT_TITLE`).

    vega 는 세로축 이름을 90° 돌려 적어 한글이 한 글자씩 쌓였다(「무효전력」 이 「마 전 력 역」).
    S163 은 선택요금 그림 하나만 고쳤다. 재료 — 이름이 있는 세로축이 있다.
    """

    def encodings(node: Any) -> Iterator[dict[str, Any]]:
        if isinstance(node, dict):
            if isinstance(node.get("encoding"), dict):
                yield node["encoding"]
            for child in node.get("layer", []) or []:
                yield from encodings(child)

    titled, upright = 0, []
    for spec in rendered.charts:
        for encoding in encodings(json.loads(spec)):
            y = encoding.get("y")
            if isinstance(y, dict) and isinstance(y.get("title"), str) and y["title"]:
                titled += 1
                if (y.get("axis") or {}).get("titleAngle") != 0:
                    upright.append(y["title"])
    assert titled, rendered.key
    assert upright == [], (rendered.key, upright)


def test_Excel_요약에_AMI_기준_안내가_한_줄_선다(rendered: Rendered) -> None:
    """**요약 시트 안내 블록에 AMI 기준 한 줄** (나-4 · 73세션 2-2 가 자리와 글을 정했다).

    고객 산출물 판이다 — 「올려 주신」 을 뺐다 (S260 결정 2)."""
    from kwise.report.notices import AMI_BASIS_NOTICE

    rows = [
        row for row in rendered.rows if row[:2] == ("Excel", "요약") and AMI_BASIS_NOTICE in row
    ]
    assert len(rows) == 1, (rendered.key, rows)


#: 잉여 세 수의 이름 — 합(화면 12개월 환산 · PPT 기간) · 평일 · 토·일·공휴일.
SURPLUS_LABELS = ("12개월 환산 잉여", "기간 잉여", "평일 잉여", "토·일·공휴일 잉여")


def _surplus_figures(rows: tuple[tuple[str, ...], ...], out: str) -> dict[str, int]:
    """이름 줄 바로 뒤 줄(셋 안)의 첫 「N kWh」 — 화면 지표 · PPT 잉여 장은 이름과 값이
    다른 줄이다."""
    found: dict[str, int] = {}
    for index, row in enumerate(rows):
        if row[0] != out or row[-1] not in SURPLUS_LABELS:
            continue
        for near in rows[index + 1 : index + 4]:
            match = re.search(r"([\d,]+) kWh", near[-1])
            if near[0] == out and match:
                found[row[-1]] = int(match.group(1).replace(",", ""))
                break
    return found


def test_잉여_세_수는_적힌_수끼리_셈이_맞고_화면과_PPT_가_같은_글자다(
    rendered_sums: Rendered,
) -> None:
    """**평일 + 토·일·공휴일 = 합 — 적힌 수끼리** (S252 결정 2 · 절사 A 의 방식을 kWh 에).

    화면(12개월 환산)과 PPT 잉여 장(기간)이 한 자리(`notices.surplus_split_kwh`)에서 세 글자를
    받는다 — 같은 합이면 같은 세 글자다(S233). `small-a2` · `small-ind-a2` 는 반올림을 따로 하면
    4,586 + 9,154 = 13,740 대 13,739 였다.
    """
    seen: dict[str, tuple[int, int, int]] = {}
    for out in ("화면", "PPT"):
        found = _surplus_figures(rendered_sums.rows, out)
        if not found:
            continue
        total = found.get("12개월 환산 잉여", found.get("기간 잉여"))
        assert total is not None, (rendered_sums.key, out, found)
        weekday, off_day = found["평일 잉여"], found["토·일·공휴일 잉여"]
        assert weekday + off_day == total, (rendered_sums.key, out, found)
        seen[out] = (total, weekday, off_day)
    if len(seen) == 2 and seen["화면"][0] == seen["PPT"][0]:
        assert seen["화면"] == seen["PPT"], (rendered_sums.key, seen)
    if rendered_sums.key in ("small-a2", "small-ind-a2"):
        assert len(seen) == 2, (rendered_sums.key, "그물이 잉여 세 수를 못 찾았다")


def test_연면적은_Excel_요약_데이터에_화면과_같은_글자로_서고_PPT_Word_에는_없다() -> None:
    """**연면적은 Excel 에만 입력값 한 줄** (S252 사람 결정 · 결정 9).

    이름 · 단위 · 값은 화면 1단계 원단위 줄의 「연면적 3,000m²」 그대로다 — 새 이름을 짓지
    않는다. PPT · Word 본문은 그대로이고, 연면적을 안 넣은 벌에는 그 줄이 없다.
    """
    rendered = _render("small-a2-pf100-offset-area")
    screen = [
        match.group(1)
        for row in rendered.rows
        if row[0] == "화면"
        for match in [re.search(r"\(연면적 ([\d,]+m²) 기준\)", row[-1])]
        if match
    ]
    assert len(screen) == 1, screen
    assert ("데이터", "연면적", screen[0]) in rendered.excel_rows, rendered.key
    for out in ("PPT", "Word"):
        assert not [row for row in rendered.rows if row[0] == out and "연면적" in row[-1]], out
    plain = _render("large-a")
    assert not [row for row in plain.excel_rows if row[:2] == ("데이터", "연면적")], plain.key


def test_학교_안내는_교육시설일_때만_화면과_Word_에_같은_글자로_서고_PPT_Excel_에는_없다() -> None:
    """**학교 교육용(갑) 고압은 계산에 넣지 않고 안내 한 줄만** (S253 사람 결정 · 결정 1).

    화면은 계약종별 후보 자리 · Word 는 선택요금 전환 절이고 글자는 한 자리
    (`report\\notices.py`)에서 온다. 옛 저장값 「학교」(`school`)로 심어 합친 쪽
    (교육시설)으로 읽히는지도 함께 본다. 건물 종류를 안 고른 벌에는 없다.
    """
    from kwise.report.notices import SCHOOL_HIGH_VOLTAGE_NOTICE

    def outputs(rendered: Rendered) -> list[str]:
        return [row[0] for row in rendered.rows if SCHOOL_HIGH_VOLTAGE_NOTICE in row[-1]]

    school = _render("small-edu-a", use="school")
    assert sorted(outputs(school)) == ["Word", "화면"], outputs(school)
    # 같은 글자 — 한 줄이 통째로 그 글자다(앞뒤로 다른 말이 안 붙는다).
    assert all(
        row[-1] == SCHOOL_HIGH_VOLTAGE_NOTICE
        for row in school.rows
        if SCHOOL_HIGH_VOLTAGE_NOTICE in row[-1]
    )
    assert outputs(_render("large-a")) == []


#: 차익거래 잠재값 글 (S254 사람 결정으로 걷었다) — 화면 · PPT · Excel · Word 어디에도 없다.
ARBITRAGE_WORDS = re.compile(
    r"예비 규칙|이쪽이 상한|차익거래 단독|차익거래 잠재|충방전 차익거래|더하지 않은 값입니다"
    r"|계시별 단가는 요금표에서 가져왔습니다\. 사용자|최대부하가 존재하는 날만|왕복효율 \d+% 가정"
)


def _s254_lines(key: str) -> tuple[Rendered, list[str], list[str]]:
    rendered = _render(key)
    screen = [row[-1] for row in rendered.rows if row[0] == "화면"]
    return rendered, screen, [" | ".join(row) for row in rendered.rows]


def _s254_metrics(rendered: Rendered) -> list[tuple[str, str]]:
    return [
        (row[3], row[4])
        for row in rendered.rows
        if row[0] == "화면" and row[1].endswith("1단계 · 진단") and row[2] == "Metric"
    ]


def _s254_check(item: str) -> None:
    """항목 하나를 그 항목이 서는 벌의 실물로 본다. 재료(수단이 서는가)는 항목마다 함께 본다."""
    from kwise.measures.contract import MARGIN_NOTICE

    rendered, screen, lines = _s254_lines("small-a2" if item in ("#9", "#13") else "large-b-short")
    combo = [row for row in rendered.rows if row[:2] == ("Excel", "조합 비교")]
    measure = [row for row in rendered.rows if row[:2] == ("Excel", "수단별 결과")]
    if item == "#1":
        short = _s254_metrics(_render("small-a-short"))
        at = short.index(("라벨", "초과사용부가금"))
        assert short[at + 1] == ("지표", "미산출"), short[at : at + 2]
        assert ("라벨", "초과사용부가금") not in _s254_metrics(_render("large-a"))
    elif item == "#2":
        assert "저투자" in screen and not [t for t in lines if "저투자 (역률 개선)" in t]
    elif item == "#3":
        assert [t for t in screen if "선택요금의 설계 의도" in t]
        assert not [t for t in lines if "선택Ⅰ·Ⅱ·Ⅲ 의 설계 의도" in t]
    elif item == "#4":
        from kwise.report.notices import TARIFF_SWITCH_CAPTION

        assert screen.count(TARIFF_SWITCH_CAPTION) == 1, rendered.key
        assert TARIFF_SWITCH_CAPTION == "현행 대비 차액과 요금제별 요금 구성"
        assert "현행 대비 차액" not in screen and "요금제별 요금 구성" not in screen
        for out in ("PPT", "Word"):
            assert [r for r in rendered.rows if r[0] == out and TARIFF_SWITCH_CAPTION in r[-1]], out
    elif item == "#5":
        legend = {
            row[-1]
            for row in rendered.figures
            if row[0] == "화면" and "현행 대비" in row[1] and row[2] == "범례"
        }
        assert legend == {"요금 절감", "현행", "요금 증가"}, legend
    elif item == "#6":
        # 그룹 막대(요금제 × 구분)의 요금제 이름은 기울이고 막대는 그림 칸 안에서 잘린다 —
        # 밑동이 이름을 안 덮는다(을 6,000 kW 캡처).
        layers = [
            layer
            for spec in rendered.charts
            for layer in json.loads(spec).get("layer", [])
            if "xOffset" in (layer.get("encoding") or {})
            and layer["encoding"].get("x", {}).get("field") == "요금제"
            and "mark" in layer
        ]
        marks = [
            layer["mark"] if isinstance(layer["mark"], dict) else {"type": layer["mark"]}
            for layer in layers
        ]
        bars = [mark for mark in marks if mark.get("type") == "bar"]
        assert bars and all(mark.get("clip") is True for mark in bars), marks
        angles = {layer["encoding"]["x"].get("axis", {}).get("labelAngle") for layer in layers}
        assert angles == {-30}, angles
    elif item == "#9":
        assert not [t for t in lines if "상계거래 SMP" in t], rendered.key
        assert [t for t in lines if "외부 판매 140원/kWh 로 산출했습니다" in t], "재료 — 각주"
    elif item == "#11":
        assert "이 조합을 모두 도입했을 때의 12개월 환산 절감액입니다." in screen
    elif item == "#12":
        names = [row[2] for row in combo[1:]]
        assert names[-1] == "+ 태양광 1,600 kWp", names
        assert not [t for t in lines if "+ ESS 목표" in t], rendered.key
    elif item == "#13":
        stage3 = [row[-1] for row in rendered.rows if row[0] == "화면" and "3단계" in row[1]]
        margin = [t for t in stage3 if "계약전력을 하향할 경우" in t and t.startswith("⚠")]
        assert margin == [f"⚠ **{MARGIN_NOTICE}**"], margin
    elif item == "#16":
        column = list(measure[0]).index("회수기간")
        row = next(r for r in measure if r[2].startswith("계약전력 조정"))
        assert (row[3:5], row[column]) == (("0", "없음"), "없음"), row
    elif item == "#17":
        # S257 결정 1 곁 — 앞 줄과 금액 · 요금제가 같은 여지 없는 계약 줄은 이제 서지 않는다.
        assert not [row for row in combo if row[2].endswith("계약전력 조정")], combo
    elif item == "#18":
        head = ("Excel", "요약", "요금", "초과사용부가금")
        excess = [r[-1] for r in rendered.rows if r[:4] == head]
        assert len(excess) == 1, excess
        assert excess[0].endswith("(청구 12개월, 분석 기간의 첫 초과 달은 예고)"), excess
        # 같은 사실 같은 글자 (S255 결정 1) — 계산 근거 「초과 12개월 ×」 네 산출물.
        assert not [t for t in lines if "12개 월" in t], rendered.key
        assert len([t for t in lines if "초과 12개월 × 기본요금 단가 × 배수" in t]) == 8, "재료"
    elif item == "ESS":
        assert [t for t in screen if t.startswith("6. ESS")], "재료 — ESS 카드가 없다"
        assert not [t for t in lines if ARBITRAGE_WORDS.search(t)], rendered.key
    else:
        pytest.fail(f"모르는 항목 {item}")


S254_ITEMS = ["#1", "#2", "#3", "#4", "#5", "#6", "#9", "#11", "#12", "#13", "#16", "#17", "#18"]


@pytest.mark.parametrize("item", [*S254_ITEMS, "ESS"])
def test_문구_판_S254_항목이_실물에_선다(item: str) -> None:
    """**문구 판 20 항목 · ESS 차익거래 걷기** (S254 사람 결정) — 항목마다 그 항목이 서는 벌.

    `large-b-short` — #2 구간 이름 · #3 선택요금 앵커 · #4 캡션 한 줄(세 산출물 한 글자) ·
    #5 범례 · #6 그룹 막대 기울임 · 자르기 · #11 합산효과 캡션 · #12 여지 없는 ESS 줄 ·
    #16 · #17 「없음」 · #18 부가금 줄 · 차익거래 잠재값 글.
    `small-a2` — #9 쓴 단가만(잔여 0 · 외부 판매 각주는 남는다) ·
    #13 조합 이름 머리 없는 여유 확보 경고.
    `small-a-short` — #1 대상인데 미산출인 부가금 칸(`large-a` 에는 없다).
    """
    _s254_check(item)


def _digits(text: str) -> int:
    """「5,951,000원」 · 「5951000」 → 5951000."""
    return int(re.sub(r"[^\d]", "", text))


def _next_after(rows: list[tuple[str, ...]], label: str) -> str:
    """PPT 지표 — 라벨 줄 바로 다음 줄의 글자."""
    at = next(i for i, row in enumerate(rows) if row[-1] == label)
    return rows[at + 1][-1]


def _s256_check(item: str) -> None:
    """사람 실물 점검 입력(역률 99.68 · DR 정산 단가 120 · 상계거래 · 쉬는 날)의 네 산출물을 본다.

    재료(수단 · 금액이 서는가)와 되돌림 대조 벌(`large-a` — 역률 간주 · 단가 없음)을 함께 본다.
    """
    confirm = _render_confirm()
    rows = list(confirm.rows)
    lines = [" | ".join(row) for row in rows]
    ppt = [row for row in rows if row[0] == "PPT"]
    word = [row for row in rows if row[0] == "Word"]
    screen = [row for row in rows if row[0] == "화면"]
    if item == "가":
        # 단가를 넣은 판 — 빨간 차단 없이 ⚠ 한 문장 (결정 가 · 고1).
        basis = "정산 단가는 전력거래소가 지역별 SMP로 정산하는 몫과 사업자 수수료에, 위약금은 "
        dr = [row for row in screen if "경제성DR — 입력과 결과" in row[1]]
        warned = [r for r in dr if r[2] == "Markdown" and r[-1].startswith("⚠")]
        assert [r for r in warned if _plain(r[-1]).startswith(basis)], warned
        assert not [t for t in lines if "입력하지 않아" in t], [t for t in lines if "입력하지" in t]
        # 단가 없는 벌 — 차단은 정산 단가 하나 · 계통한계가격 입력을 전제하지 않는다.
        plain = [" | ".join(row) for row in _render("large-a").rows]
        assert [t for t in plain if "| 정산 단가를 입력하지 않아 금액을 산출하지 않았습니다." in t]
        assert not [t for t in plain if "계통한계가격을 입력" in t]
    elif item == "고2":
        estimated = ("현재 역률은 추정값", "역률요금은 추정 역률 기반")
        assert not [t for t in lines if any(word_ in t for word_ in estimated)], confirm.key
        # 재료 — 역률을 안 넣은 벌에는 네 자리(화면 · PPT · Excel · Word)에 선다.
        stood = [row for row in _render("large-a").rows if any(w in row[-1] for w in estimated)]
        assert {row[0] for row in stood} == {"화면", "PPT", "Excel", "Word"}, stood
    elif item == "고3":
        # PPT 16장 · Word 조합 표 · Excel 조합 비교의 끝 줄 = 합산효과 (고3 ㄴ · ㄷ).
        where = next(row[1] for row in ppt if row[2:] == ("조합", "기간 절감액", "회수기간"))
        combo = [row for row in ppt if row[1] == where][1:]
        assert combo[-1][2] == "+ 경제성DR", combo
        ppt_text = [row for row in ppt if row[1] == where.removesuffix("표")]
        # 누적 칸의 괄호(그 줄 몫)는 S258 결정 5 가 붙였다 — 앞 값이 합산효과다.
        assert combo[-1][3].split(" (")[0] == _next_after(ppt_text, "기간 총 절감액"), combo[-1]
        assert "경제성DR" in _next_after(ppt_text, "가장 유리한 조합")
        total = next(row[3] for row in word if row[2] == "기간 총 절감액")
        header = ("조합", "요금제", "기간 절감액", "투자비", "회수기간")
        word_where = next(row[1] for row in word if row[2:] == header)
        word_combo = [row for row in word if row[1] == word_where][1:]
        assert word_combo[-1][2] == "+ 경제성DR" and word_combo[-1][4] == total, word_combo[-1]
        excel = [row for row in rows if row[:2] == ("Excel", "조합 비교")]
        excel_head = list(excel[0])
        last = excel[-1]
        assert last[2] == "+ 경제성DR", last
        assert _digits(last[excel_head.index("기간 절감액(원)")]) == _digits(total), (last, total)
        figure = [
            row for row in confirm.figures if "combination_png" in row[1] and row[2] == "눈금"
        ]
        assert figure and figure[-1][-1] == "+ 경제성DR", figure
    elif item == "고4":
        text = [row for row in ppt if row[1] == "8"]
        free = _next_after(text, "투자 없이 가능한 기간 절감액")
        assert free not in ("0원", "—"), free
        assert [row for row in text if "경제성DR" in row[-1] and row[-1].startswith("설비 투자")]
    elif item == "고5":
        assert ("PPT", "8표", "역률 개선", "없음", "—", "없음") in rows, [
            row for row in ppt if row[1] == "8표"
        ]
        pf_row = next(r for r in rows if r[:2] == ("Excel", "수단별 결과") and "역률 개선" in r[2])
        assert pf_row[2:7] == (
            "역률 개선 (현재 99.7% · 개선 여지 없음)",
            "—",
            "없음",
            "없음",
            "없음",
        )
        # 판정은 반올림 값이라 안내 줄은 두 값을 함께 밝힌다 (S264 결정 2). 결론 한 줄(PPT
        # 역률 장 첫 문장)은 99.68 → 100 이 판정을 안 바꾸므로 넣은 값만 적는다 (S270 결정 4).
        assert [
            t
            for t in lines
            if "현재 지상역률 99.7% (요금 계산은 1% 단위 반올림 100%) 는 감액 상한 97% 이상" in t
        ]
        said = "지상역률 99.7% 는 감액 상한 97% 이상이라 개선할 것이 없습니다."
        assert [row for row in ppt if row[-1] == said], [r for r in ppt if "감액 상한" in r[-1]]
        assert not [row for row in ppt if "단위 반올림" in str(row[-1])]
        assert not [t for t in lines if "지상역률 100% 는" in t]
        legend = [r[-1] for r in confirm.figures if "power_triangle_png" in r[1] and r[2] == "범례"]
        assert legend and all(t.startswith("현재 — 역률 99.7% · ") for t in legend), legend
        assert not [r for r in rows if r[:2] == ("Excel", "부록 A 산출 근거") and "목표 역률" in r]
    elif item == "고6":
        note = next(row[-1] for row in ppt if "자가소비로 줄인 요금" in row[-1])
        own, surplus = (_digits(part) for part in re.findall(r"[\d,]+원", note)[:2])
        curve = [row for row in rows if row[:2] == ("Excel", "태양광 용량 곡선")]
        head = list(curve[0])
        chosen = next(row for row in curve if row[-1].startswith("◀"))
        parts = [
            int(float(chosen[head.index(name)]))
            for name in (
                "기간 기본요금 절감(원)",
                "기간 전력량요금 절감(원)",
                "기간 역률요금 절감(원)",
            )
        ]
        written = int(float(chosen[head.index("기간 자가소비 절감액(원)")]))
        assert sum(parts) == written == own, (parts, written, own)
        assert own + surplus == _digits(
            _next_after([r for r in ppt if r[1] == "13"], "12개월 환산 절감액")
        )
    elif item == "고7":
        dr_row = next(
            r for r in rows if r[:2] == ("Excel", "수단별 결과") and r[2].startswith("경제성DR")
        )
        assert dr_row[4] != "—" and dr_row[5] == "573,000", dr_row
        assert not [t for t in lines if "등록 가능 용량" in t]
        assert [t for t in lines if "등록 권장 용량이 참고 문턱" in t], "재료"
    elif item == "고8":
        caption = [row[-1] for row in ppt if row[1] == "7" and row[-1].startswith("월별 요금 구성")]
        assert caption == ["월별 요금 구성"], caption
        surplus_row = next(
            r for r in rows if r[:2] == ("Excel", "수단별 결과") and "└ 잉여" in r[2]
        )
        assert "외부 판매" not in surplus_row[-1], surplus_row
        assert [t for t in lines if t.startswith("PPT") and "외부 판매 140원/kWh 로 산출" in t]
        assert not [t for t in lines if "부록 B 의 시각 분포" in t or "봄·가을 피크" in t]
        assert [t for t in lines if "진단 시트의 시각 분포" in t]
        assert [t for t in lines if "3~6월·10~11월 피크 저감은" in t]
    elif item == "고9":
        tools = (
            "예상 소요를 미리 알릴",
            "샘플 한 벌 전체 실측",
            "진행률 단계 가중치",
            "화면 본문 줄 수 한도",
        )
        assert not [t for t in lines if any(w in t for w in (*tools, "화면 확인사항 개수 한도"))]
        # Excel 수단별 결과 줄 이름만 — Word 절 제목(「3.6 7.6 ESS」)은 이 항목 밖이다.
        assert not [t for t in lines if t.startswith("Excel | 수단별 결과 | 7.6 ESS")]
        assert [t for t in lines if t.startswith("Excel | 수단별 결과 | ESS |")], "재료"
        assert not [t for t in lines if "그리드 이탈 0.00" in t]
        assert [t for t in lines if "| 선택요금 전환 (선택Ⅱ 유지) |" in t]
        assert [t for t in lines if "| 선택요금 전환은 설비 도입과 무관하게" in t]
        assert [t for t in lines if "| 경제성DR 은 요금 계량의 평일 판정과" in t]
        assert [t for t in lines if "기간 자가소비 절감액(원)" in t]
    else:
        pytest.fail(f"모르는 항목 {item}")


@pytest.mark.parametrize("item", ["가", "고2", "고3", "고4", "고5", "고6", "고7", "고8", "고9"])
def test_사람_실물_점검_S256_항목이_실물에_선다(item: str) -> None:
    """**사람 실물 점검(S256)** — 결정 가 · 고2 ~ 고9 를 확인 사례 입력의 네 산출물로 본다.

    확인 사례 — `small-a2-pf100-offset-area` 에 역률 99.68 · DR 정산 단가 120 · 쉬는 날
    2025-10-02 · 운영 8~17시 · 설치 단가 2,500,000원/kWp. 대조 벌 `large-a`(역률 간주 · 단가 없음).
    """
    _s256_check(item)


def _metric(screen: list[tuple[str, ...]], label: str) -> str:
    """화면 3단계 지표 — 라벨 줄 바로 다음 「지표」 줄의 글자."""
    stage3 = [row for row in screen if "3단계" in row[1] and row[2] == "Metric"]
    at = next(i for i, row in enumerate(stage3) if row[3:] == ("라벨", label))
    return stage3[at + 1][-1]


def _s257_check(item: str) -> None:
    """S257 결정 1 ~ 7 을 확인 사례(S256 과 같은 입력)의 화면 · 네 산출물로 본다."""
    confirm = _render_confirm()
    rows = list(confirm.rows)
    lines = [" | ".join(row) for row in rows]
    screen = [row for row in rows if row[0] == "화면"]
    if item == "1":
        # 여지 없는 같은 금액 계약 줄(0원 = 기준선 0원)이 조합 표 네 자리에 없다.
        combos = [
            row
            for row in rows
            if row[:2] == ("Excel", "조합 비교")
            or (row[0] in ("PPT", "Word") and row[1].endswith("표") and "경제성DR" in "".join(row))
        ]
        assert combos, "재료 — 조합 표"
        assert not [t for t in lines if re.search(r"\| (\+ )?계약전력 조정 \| (선택|0원)", t)], [
            t for t in lines if "| 계약전력 조정 |" in t or "| + 계약전력 조정 |" in t
        ]
        assert not [t for t in lines if "combination_png" in t and t.endswith("계약전력 조정")]
    elif item == "2":
        # 합산효과의 DR = 2단계 카드(원 부하) — 차이가 0 이고 끝 줄이 단순 합과 같다.
        assert _metric(screen, "합산효과") == _metric(screen, "단순 합") == "574만원/년"
        excel = [row for row in rows if row[:2] == ("Excel", "조합 비교")]
        last, head = excel[-1], list(excel[0])
        # 기간 값 = 적힌 태양광 줄 5,170,000 + 기간 정산금 573,370 → 5,743,000 (S256 고3 ㄷ 꼴).
        assert last[2] == "+ 경제성DR", last
        assert _digits(last[head.index("기간 절감액(원)")]) == 5_743_000, last
        total = next(row[3] for row in rows if row[:3] == ("Word", "표2", "기간 총 절감액"))
        assert _digits(total) == 5_743_000, total
        dr_row = next(
            r for r in rows if r[:2] == ("Excel", "수단별 결과") and r[2].startswith("경제성DR")
        )
        assert dr_row[5] == "573,000", "재료 — 2단계 카드 12개월 정산금"
    elif item == "3":
        assert not [t for t in lines if "입력이 변경되었습니다" in t or "묵은 결과" in t]
        assert [t for t in lines if t.startswith("화면") and "5. 태양광" in t], "재료 — 카드"
    elif item == "4":
        assert not [t for t in lines if "충전 여력이 제한적" in t], confirm.key
        assert [t for t in lines if "| 기저부하 비율 |" in t], "재료 — 이름 · 값은 선다"
    elif item == "5":
        assert not [t for t in lines if "에만 가능합니다" in t or "남는 제약은" in t]
        assert [t for t in lines if "하루 최대 2회(총 8시간)는 이 도구의 가정입니다" in t]
        assert [t for t in lines if "은 이 도구의 가정이고, 입찰 시간대 가운데" in t]
        assert [t for t in screen if "하루 한도(가정) 2회" in t[-1]]
        assert [t for t in screen if "이 도구는 하루 최대 2회로 가정합니다" in t[-1]]
    elif item == "6":
        assert not [
            t for t in lines if "최대수요 과소평가 위험" in t or "피크 시간대 편중 배수" in t
        ]
        assert not [t for t in lines if "kW 미만 구간" in t]
    elif item == "7":
        basis_rows = [
            row[-1] for row in screen if row[1].endswith("3단계 · 개선안 조합 › 계산 근거")
        ]
        assert "차이" in basis_rows and "0원" in basis_rows, basis_rows
        assert not [t for t in basis_rows if t.startswith("이유 ")], basis_rows
    else:
        pytest.fail(f"모르는 항목 {item}")


@pytest.mark.parametrize("item", ["1", "2", "3", "4", "5", "6", "7"])
def test_S257_결정이_확인_사례_실물에_선다(item: str) -> None:
    """**S257 결정 1 ~ 7** — 계약 빈 줄 · 조합 DR 원 부하 · 묵은 결과 · ESS 문장 · DR 시간대 글 ·
    품질 경고 둘 · 차이 이유 줄을 S256 확인 사례 입력의 화면 · 네 산출물로 본다."""
    _s257_check(item)


def _slide(rows: list[tuple[str, ...]], title: str) -> list[tuple[str, ...]]:
    """제목이 ``title`` 인 PPT 장의 줄(표 칸 포함) — 목차(2장)에 같은 이름이 서도 뒤 장이다."""
    page = [row[1] for row in rows if row[0] == "PPT" and len(row) == 3 and row[2] == title][-1]
    return [row for row in rows if row[0] == "PPT" and row[1] in (page, f"{page}표")]


def _s258_check(item: str) -> None:
    """S258 결정 1 ~ 11 을 사람 실물 확인 사례 (가) 의 화면 · 네 산출물로 본다."""
    from kwise.report.narrative import COMBINATION_LEAD

    human = _render_human()
    rows = list(human.rows)
    lines = [" | ".join(row) for row in rows]
    screen = [row for row in rows if row[0] == "화면"]
    ppt = [row for row in rows if row[0] == "PPT"]
    excel = [row for row in rows if row[:2] == ("Excel", "조합 비교")]
    dr = [row[-1] for row in _slide(rows, "경제성DR")]
    if item == "1":
        note = (
            "※ 정산 단가 120원/kWh 로 산출했습니다. 정산 단가는 전력거래소가 지역별 SMP로 "
            "정산하는 몫과 사업자 수수료에 달려 있습니다."
        )
        assert note in dr, dr
        assert not [row for row in ppt if "쉬는 날로 지목한" in row[-1]]
        assert [row for row in screen if "2025-10-02" in row[-1]], "재료 — 화면 쉬는 날 목록"
    elif item == "2":
        heads = [row[-1] for row in _slide(rows, "조합구성 및 합산효과")]
        assert COMBINATION_LEAD in heads, heads
        assert not [t for t in lines if "켠 수단이 하나라" in t]
    elif item == "3":
        assert not [row for row in ppt if len(row) == 3 and row[2] == "주의사항"]
        assert (
            "※ 감축계획량을 채우지 못하면 실적위약금 = (감축계획량 − 실제감축량) × 계통한계가격 "
            "× 위약금계수(1) 이 부과됩니다 (전력시장운영규칙 별표26)."
        ) in dr, dr
        assert not [row for row in ppt if "리스크는 0이 아닙니다" in row[-1]]
    elif item == "4":
        assert not [t for t in lines if "계약전력을 하향할 경우" in t]
        assert not [t for t in lines if "계약전력 변경 시 주의" in t or "계약전력 변경 경고" in t]
        assert [t for t in lines if t.startswith("Word") and "5.2 추적성" in t], "재료 — 당긴 절"
    elif item == "5":
        table = [row[2:] for row in _slide(rows, "조합구성 및 합산효과") if row[1].endswith("표")]
        assert ("+ 경제성DR", "528만원 (+57만원)", "15.1년") in table, table
        assert ("+ 태양광 32 kWp", "471만원", "17.0년") in table, table
        heads = [row[-1] for row in _slide(rows, "조합구성 및 합산효과")]
        assert "조합별 누적 기간 절감액과 누적 투자비" in heads, heads
    elif item == "6":
        assert (
            "Excel | 부록 A 산출 근거 | 태양광 계산 근거 | 기간 역률요금 절감 | "
            "도입 후 역률 100% · 기본요금이 준 만큼 감액도 준다 | -4,000원"
        ) in lines, [t for t in lines if "기간 역률요금 절감" in t]
    elif item == "7":
        assert not [t for t in lines if "충전 여력" in t]
        assert [row for row in _slide(rows, "전력사용현황 및 부하패턴") if "부하율" in row[-1]]
    elif item == "8":
        head = list(excel[0])
        assert excel[-1][2] == "+ 경제성DR", excel[-1]
        end = _digits(excel[-1][head.index("기간 절감액(원)")])
        basis = [row[-1] for row in screen if row[1].endswith("3단계 · 개선안 조합 › 계산 근거")]
        assert end == 5_282_000 and "5,282,000원" in basis, (end, basis)
        # S257 확인 사례(= S258 (다))에서 갈렸다 — 끝 줄 5,743,000 · 계산 근거 5,744,000.
        other = list(_render_confirm().rows)
        table = [row for row in other if row[:2] == ("Excel", "조합 비교")]
        written = _digits(table[-1][list(table[0]).index("기간 절감액(원)")])
        shown = [
            row[-1]
            for row in other
            if row[0] == "화면" and row[1].endswith("3단계 · 개선안 조합 › 계산 근거")
        ]
        assert written == 5_743_000 and "5,743,000원" in shown, (written, shown)
        assert "5,744,000원" not in shown, shown
    elif item == "9":
        summary = [row[2:] for row in _slide(rows, "개선안별 요약") if row[1].endswith("표")]
        contract = next(row for row in summary if row[0] == "계약전력 조정")
        assert contract[1:] == ("없음", "—", "없음"), contract
        page = [row[-1] for row in _slide(rows, "계약전력 조정")]
        assert page[page.index("회수기간") + 1] == "없음", page
    elif item == "10":
        # 사람 실물 값 여덟 칸(S258 머리) — 태양광 12개월 · 경제성DR · 단순 합 · 합산효과 · 차이 ·
        # 회수기간 · Excel 끝 줄 · 용량 곡선 32 kWp 줄(도입 후 역률 · 역률요금 절감).
        measure = {
            row[2]: row for row in rows if row[:2] == ("Excel", "수단별 결과") and len(row) > 5
        }
        assert measure["태양광 32 kWp"][5] == "4,709,000", measure["태양광 32 kWp"]
        dr_row = next(row for name, row in measure.items() if name.startswith("경제성DR"))
        assert dr_row[5] == "573,000", dr_row
        assert _metric(screen, "단순 합") == _metric(screen, "합산효과") == "528만원/년"
        assert _metric(screen, "차이") == "0원/년"
        assert _metric(screen, "회수기간") == "15.1년"
        head = list(excel[0])
        assert _digits(excel[-1][head.index("기간 절감액(원)")]) == 5_282_000
        curve = [row for row in rows if row[:2] == ("Excel", "태양광 용량 곡선")]
        names = list(curve[0])
        row32 = next(row for row in curve if row[2] == "32")
        # 도입 후 역률은 요금을 셈한 1% 반올림 값이다 (S262 결정 1 — 99.6 → 100).
        assert float(row32[names.index("도입 후 역률(%)")]) == 100.0
        assert int(float(row32[names.index("기간 역률요금 절감(원)")])) == -4_000
    elif item == "11":
        # S259 가 바로잡았다 — 사람 화면의 방위는 남동이라 묵은 결과 경고가 없다(S258 은 스스로
        # 풀린 남을 사람 입력으로 읽어 경고가 선다고 물었다).
        assert not [row for row in screen if "입력이 변경되었습니다" in row[-1]]
    else:
        pytest.fail(f"모르는 항목 {item}")


@pytest.mark.parametrize("item", [str(n) for n in range(1, 12)])
def test_S258_결정이_사람_실물_확인_사례에_선다(item: str) -> None:
    """**S258 결정 1 ~ 11** — 사람 실물 재점검(S257 뒤) 확인 사례 (가) 의 화면 · 네 산출물.

    입력 — `small-a2-pf100-offset-area` 에 역률 99.68 · 야간 진상 100 · 사무실 · 8~17시 · 준공
    2000 · DR 120 · 쉬는 날 2025-10-02 · 태양광 400 m² 보통 2,500,000원/kWp(저장 방위 남동 ·
    화면 방위 남동 — S259 가 바로잡았다) · 잉여 상계 · SMP 120. 결정 10 은 사람 실물 값 여덟
    칸이 같은지다.
    """
    _s258_check(item)


# ===================================================================== S259


#: 막힘 줄 — 카드 경고 글자에 수단 이름을 붙인다 (S259 결정 2).
S259_BLOCK_LINE = "5. 태양광 — 입력이 변경되었습니다 — 다시 계산하십시오."
_S259: dict[str, Any] = {}


def _browser_radio(patch: pytest.MonkeyPatch) -> None:
    """**AppTest 라디오를 브라우저처럼** (S259 1-1).

    AppTest 는 실행마다 세션 값을 지금 라벨로 다시 적어 보내지만, 브라우저는 사람이 고른
    그 순간의 **라벨 글자**를 쥐고 있다가 백엔드가 값을 새로 박을 때만 바꾼다. 그 차이로
    S258 재현이 방위 풀림을 못 봤다 — 라디오마다 브라우저가 쥔 글자를 따로 든다.
    """
    from streamlit.proto.WidgetStates_pb2 import WidgetState
    from streamlit.testing.v1 import element_tree as et

    held: dict[str, str] = {}
    init, setter = et.Radio.__init__, et.Radio.set_value

    def _init(self: Any, proto: Any, root: Any) -> None:
        init(self, proto, root)
        if proto.set_value and proto.raw_value:
            held[proto.id] = proto.raw_value
        elif proto.id not in held and proto.HasField("default") and self.options:
            held[proto.id] = self.options[proto.default]

    def _set(self: Any, value: Any) -> Any:
        setter(self, value)
        held[self.id] = self.options[self.options.index(self.format_func(value))]
        return self

    def _state(self: Any) -> Any:
        state = WidgetState()
        state.id = self.id
        if self.id in held:
            state.string_value = held[self.id]
        return state

    patch.setattr(et.Radio, "__init__", _init)
    patch.setattr(et.Radio, "set_value", _set)
    patch.setattr(et.Radio, "_widget_state", property(_state))


def _s259_view(app: Any, keys: dict[str, str]) -> dict[str, Any]:
    """지금 화면 — 사람이 고른 입력 · 묵은 결과 경고 · 막힘 줄 · 눌리지 않는 단추."""
    state = app.session_state
    values = {name: state[key] for name, key in keys.items() if key in state}
    texts = [str(item.value) for item in app.markdown]
    buttons = {item.key: item.disabled for item in app.button if item.key}
    buttons |= {item.key: item.disabled for item in app.get("download_button") if item.key}
    return {
        "values": values,
        "stale": [t for t in texts if "입력이 변경되었습니다" in t],
        "blocks": [t for t in texts if S259_BLOCK_LINE in t],
        "disabled": sorted(key for key, off in buttons.items() if off),
        "buttons": buttons,
    }


def _s259_flow() -> dict[str, Any]:
    """사람 순서 한 판 — 입력 → 태양광 계산 → 그 뒤 동작들 → 방위를 바꾸고 → 다시 계산."""
    if _S259:
        return _S259
    from dataclasses import replace

    from streamlit.testing.v1 import AppTest

    sys.path.insert(0, str(PROJECT_ROOT / "tools"))
    import render_deck

    from kwise.pv import load_pv_presets
    from kwise.ui.pipeline import ContractForm
    from kwise.ui.state import input_key

    case = replace(render_deck.BY_KEY[CONFIRM_KEY], power_factor_pct=99.68)
    if not case.csv.is_file():
        pytest.skip(f"자료가 없습니다: {case.csv}")
    presets = load_pv_presets()
    density = next(item.key for item in presets.densities if item.key != presets.default.key)
    keys = {
        "방위": input_key("solar", "azimuth"),
        "벽면 방위": input_key("solar", "wall_azimuth"),
        "밀도": input_key("solar", "density"),
        "면적": input_key("solar", "area"),
        "설치 단가": input_key("solar", "unit_cost"),
        "총 투자비": input_key("solar", "total_cost"),
        "벽면 면적": input_key("solar", "wall_area"),
        "정산 단가를 안다": input_key("demand_response", "priced"),
    }
    chosen = {
        "방위": "southeast",
        "벽면 방위": "east",
        "밀도": density,
        "면적": 400.0,
        "설치 단가": 2_500_000.0,
        "총 투자비": 80_000_000.0,
        "벽면 면적": 100.0,
        "정산 단가를 안다": True,
    }
    patch = pytest.MonkeyPatch()
    patch.delenv("KWISE_WEATHER_DIR", raising=False)
    _browser_radio(patch)
    views: dict[str, Any] = {}
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
        for key in ("solar", "demand_response"):
            state[f"measure_on_{key}"] = True

        def step(name: str) -> None:
            app.run()
            assert not app.exception, (name, app.exception)
            views[name] = _s259_view(app, keys)

        step("시작")
        app.checkbox(key=keys["정산 단가를 안다"]).check()
        step("정산 단가")
        app.number_input(key=input_key("demand_response", "unit_price")).set_value(120.0)
        app.number_input(key=keys["면적"]).set_value(400.0)
        app.number_input(key=keys["설치 단가"]).set_value(2_500_000.0)
        app.number_input(key=keys["총 투자비"]).set_value(80_000_000.0)
        app.number_input(key=keys["벽면 면적"]).set_value(100.0)
        step("수치 입력")
        app.radio(key=keys["밀도"]).set_value(density)
        app.radio(key=keys["방위"]).set_value("southeast")
        app.radio(key=keys["벽면 방위"]).set_value("east")
        step("방위 · 밀도")
        app.button(key="solar_run").click()
        step("태양광 계산")
        app.radio(key=input_key("solar", "surplus_use")).set_value("상계거래(한전)")
        step("잉여 상계")
        app.button(key="nav_rules").click()
        step("기준 데이터")
        app.button(key="nav_analysis").click()
        step("돌아오기")
        app.selectbox(key=input_key("common", "ref_day")).set_value("custom")
        step("대표일")
        app.button(key="combo_run").click()
        step("합산효과")
        app.button(key="build_ppt").click()
        step("PPT 만들기")
        app.radio(key=keys["방위"]).set_value("south")
        step("방위 남(계산 안 함)")
        app.button(key="solar_run").click()
        step("다시 계산")
    finally:
        patch.undo()
    _S259.update(views=views, chosen=chosen)
    return _S259


#: 사람이 입력을 안 바꾼 흐름 — 태양광 계산 뒤 동작들 (1-1 이 해 본 후보).
S259_KEPT = (
    "태양광 계산",
    "잉여 상계",
    "기준 데이터",
    "돌아오기",
    "대표일",
    "합산효과",
    "PPT 만들기",
)
#: 막힐 자리 — 3단계 합산효과 · Excel · PPT 만들기 · 이미 만든 PPT 내려받기.
S259_BLOCKED = ["build_excel", "build_ppt", "combo_run", "dl_ppt"]


def _s259_check(item: str) -> None:
    flow = _s259_flow()
    views, chosen = flow["views"], flow["chosen"]
    if item == "1":
        for name in S259_KEPT:
            view = views[name]
            if name == "기준 데이터":  # 분석 화면을 안 그린다 — 값은 돌아온 뒤에 본다
                continue
            assert view["values"] == chosen, (name, view["values"])
            assert view["stale"] == [] and view["disabled"] == [], (name, view)
    elif item == "2":
        view = views["방위 남(계산 안 함)"]
        assert view["values"]["방위"] == "south", view["values"]
        assert view["disabled"] == S259_BLOCKED, view["disabled"]
        # 막힌 자리마다 한 줄 — 합산효과 단추 위 · Excel 탭 · PPT 탭.
        assert len(view["blocks"]) == 3, view["blocks"]
    elif item == "3":
        view = views["다시 계산"]
        assert view["stale"] == [] and view["blocks"] == [] and view["disabled"] == [], view
        # 다시 계산하면 저장 입력이 바뀌어 옛 파일은 내밀지 않는다(S193 토큰) — 새로 만든다.
        assert "dl_ppt" not in view["buttons"], view["buttons"]
    elif item == "4":
        from kwise.report.notices import CONTRACT_CHANGE_WARNING

        line = f"※ {CONTRACT_CHANGE_WARNING}"
        for key, expected in (("small-a2", True), ("small-ind-a2", True), ("large-a", False)):
            rows = list(_render(key).rows)
            combo = [row[-1] for row in _slide(rows, "조합구성 및 합산효과")]
            assert (line in combo) is expected, (key, combo)
            # 계약 장 ※ 에도 서는 벌(2단계가 하향을 권한다)은 조합 장에 두 번 세우지 않는다.
            everywhere = [row for row in rows if row[0] == "PPT" and row[-1] == line]
            assert not expected or len(everywhere) == 1, (key, everywhere)
    else:
        pytest.fail(f"모르는 항목 {item}")


@pytest.mark.parametrize("item", ["1", "2", "3", "4"])
def test_S259_사람이_고른_입력이_남고_묵은_결과로는_산출물을_못_만든다(item: str) -> None:
    """**S259 결정 1 ~ 3.**

    1 태양광 계산 뒤 아무 동작(잉여 처리 · 옆단 기준 데이터 다녀오기 · 대표일 · 합산효과 ·
      만들기)에도 사람이 고른 입력(방위 · 벽면 방위 · 밀도 · 면적 · 단가 · 총 투자비 · 벽면
      면적 · 「정산 단가를 안다」)이 남고 경고 · 막힘이 없다 — 라디오는 브라우저처럼 옛 라벨
      글자를 보낸다.
    2 사람이 방위를 바꾸고 다시 계산하지 않으면 합산효과 계산 · Excel · PPT 만들기 · 이미 만든
      내려받기가 눌리지 않고 막힌 자리마다 「5. 태양광 — 입력이 변경되었습니다 — …」 한 줄.
    3 태양광을 다시 계산하면 풀린다.
    4 조합만 하향을 권하는 벌(`small-a2` · `small-ind-a2`)에서만 PPT 조합 장에 필수 안내 ※.
    """
    _s259_check(item)


# ===================================================================== S260


def _user_strings(folder: Path) -> list[tuple[str, str]]:
    """``folder`` 아래 소스의 글자 상수 — 독스트링 · 식 문장 글자(주석 꼴)는 뺀다."""
    import ast

    found: list[tuple[str, str]] = []
    for path in sorted(folder.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip = {
            id(node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        }
        found += [
            (path.name, node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in skip
        ]
    return found


def _s260_check(item: str) -> None:
    """S260 결정 1 ~ 5 를 확인 사례 (가1) 의 화면 · 네 산출물로 본다."""
    from kwise.report import notices
    from kwise.report.narrative import COMBINATION_LEAD
    from kwise.report.slides import SLIDE_TITLES
    from kwise.rules import ItemDiff
    from kwise.tariff import AMI_BASIS_NOTICE as SCREEN_AMI

    rows = list(_render_human().rows)
    outputs = [row for row in rows if row[0] in ("Excel", "PPT", "Word")]
    screen = [row for row in rows if row[0] == "화면"]
    changed = (ItemDiff("surplus.smp_won_per_kwh", "", "판단", 130, 120, "변경"),)
    patch = pytest.MonkeyPatch()
    try:
        if item == "1":
            # PPT 3장 — 「기준 데이터」 행이 「적용 기준」 이 된다 · DR 장이 서면 전력시장운영규칙
            ppt = [row for row in outputs if row[0] == "PPT"]
            basis = [row for row in ppt if row[2:3] == ("적용 기준",)]
            assert [row[-1] for row in basis] == [
                "한전 기본공급약관 · 전기요금표 · 전력시장운영규칙"
            ], basis
            assert not [row for row in ppt if "기준 데이터" in row[2:3]]
            plain = notices.applied_basis_line(market_rules=False)
            assert plain == "한전 기본공급약관 · 전기요금표", plain
            patch.setattr(notices, "diff_from_defaults", lambda: changed)
            assert notices.applied_basis_line(market_rules=True) == (
                "한전 기본공급약관 · 전기요금표 · 전력시장운영규칙"
                " — 일부 값을 바꿔 계산했습니다"  # S263 결정 3 곁 — 「(Excel 부록 B)」 를 뺐다
            )
        elif item == "2":
            # 고객 산출물에서 「올려 주신」 을 뺀다 · 화면은 그대로
            assert SCREEN_AMI.replace("올려 주신 ", "") == notices.AMI_BASIS_NOTICE
            assert not [row for row in outputs if "올려 주신" in " ".join(row)]
            for name in ("Excel", "PPT"):
                hits = [r for r in outputs if r[0] == name and notices.AMI_BASIS_NOTICE in r[-1]]
                assert len(hits) == 1, (name, hits)
            assert [row for row in screen if SCREEN_AMI in row[-1]], "화면 AMI 줄이 빠졌다"
        elif item == "3":
            # 잉여 장 SMP 단가 — 기간 말 잔여가 있을 때만 선다(코드 0줄 · S254 #9)
            title = SLIDE_TITLES["surplus"]
            for key, remains in (("small-ind-a1", True), ("small-a2", False), ("human", False)):
                page = _slide(list(rows if key == "human" else _render(key).rows), title)
                texts = [" | ".join(row) for row in page]
                left = [m for t in texts for m in re.findall(r"기간 말 잔여 ([\d,]+) kWh", t)]
                assert left and (left[0] != "0") is remains, (key, left)
                assert any("상계거래 SMP" in t for t in texts) is remains, (key, texts)
        elif item == "4":
            assert COMBINATION_LEAD.endswith("조합을 합쳐서 다시 계산했습니다.")
            combo = [row[-1] for row in _slide(rows, "조합구성 및 합산효과")]
            assert COMBINATION_LEAD in combo, combo
            note = "합산효과에서 조합을 합쳐서 다시 계산합니다"
            assert [row for row in screen if note in row[-1]]
            assert not [row for row in rows if "통째로" in " ".join(row)]
        elif item == "5":
            excel = [row for row in outputs if row[:2] == ("Excel", "요약")]
            summary = [row for row in excel if "기준 데이터" in row]
            assert [row[-1] for row in summary] == ["기준 데이터는 기본값 그대로입니다."], summary
            assert not [row for row in rows if "출고값" in " ".join(row)]
            src = PROJECT_ROOT / "src" / "kwise"
            left = [
                hit
                for folder in ("ui", "report", "rules")
                for hit in _user_strings(src / folder)
                if "출고값" in hit[1]
            ]
            # `rules.ItemView.as_row` 의 열 이름 하나는 남긴다 — 화면 · 산출물에 안 서고
            # `tools\export_rules_xlsx.py` 가 그 이름으로 열을 고른다(S260 · tools 0줄).
            assert left == [("__init__.py", "출고값")], left
            patch.setattr(notices, "diff_from_defaults", lambda: changed)
            assert notices.rules_basis_line().startswith("기본값과 다른 항목 1건 — ")
        else:
            pytest.fail(f"모르는 항목 {item}")
    finally:
        patch.undo()


@pytest.mark.parametrize("item", ["1", "2", "3", "4", "5"])
def test_S260_사람_실물_재점검_글자가_선다(item: str) -> None:
    """**S260 결정 1 ~ 5** — 확인 사례 (가1)(S259 (가) 그대로)의 화면 · 네 산출물.

    1 PPT 3장 「적용 기준 | 한전 기본공급약관 · 전기요금표」(DR 장이 서면 「 · 전력시장운영규칙」 ·
      기준 데이터를 바꿨으면 「 — 일부 값을 바꿔 계산했습니다 (Excel 부록 B)」).
    2 PPT · Excel 의 AMI 기준 한 줄에 「올려 주신」 이 없다 · 화면은 그대로.
    3 잉여 장 상계거래 SMP 단가는 기간 말 잔여가 있을 때만 선다(사람이 다시 확인 · 코드 0줄).
    4 「통째로」 → 「합쳐서」 — PPT 조합 장 머리 · 화면 3단계 풀이.
    5 「출고값」 → 「기본값」 — Excel 요약 · 화면 · 기준 데이터 화면 글자.
    """
    _s260_check(item)


def _render_zero_dr() -> Rendered:
    """감축 가능량 0 에 정산 단가를 넣은 판 (S261) — `small-a` 에 확인 사례 입력(단가 120).

    저부하 평일이 0일이라 DR 정산금이 0원이고, 조합 끝에 「+ 경제성DR (+0원)」 줄이 서던 벌이다."""
    slot = "zero-dr"
    if slot not in _RENDERED:
        _RENDERED[slot] = _build("small-a", "office", confirm=True)
    return _RENDERED[slot]


#: 감축 가능량이 0 이면 서지 않는 참여 전제 글자 (S261 고침 1).
_DR_PREMISE = (
    "리스크는 0이 아닙니다",
    "위약금은 계통한계가격에 달려",
    "참고 문턱 100 kW 아래",
    "로 산출했습니다. 정산 단가는",
    "감축계획량을 채우지 못하면",
)


def _s261_check(item: str, zero: bool) -> None:
    """S261 고침 1 · 2 를 감축 0 판(`small-a`)과 0 보다 큰 판((가1))의 화면 · 네 산출물로 본다."""
    rendered = _render_zero_dr() if zero else _render_human()
    rows = [tuple(str(v) for v in row) for row in rendered.rows]
    plain = [row for row in rows if not row[1].startswith("그림")]
    texts = [" | ".join(row) for row in plain]
    if item == "1":
        premise = [t for t in texts if any(word in t for word in _DR_PREMISE)]
        screen = [row[-1] for row in plain if row[0] == "화면"]
        dr = [row[-1] for row in _slide(rows, "경제성DR")]
        if zero:
            assert not premise, premise
            assert [t for t in screen if "저부하 평일이 없습니다." in t], "화면 ⚠ 는 남는다"
            assert [t for t in dr if t.endswith("평일이 없어 줄일 여지가 없습니다.")], dr
        else:
            # (가1) — 등록 31 kW(문턱 아래) · 단가를 넣었다 · 다섯이 다 선다
            missing = [w for w in _DR_PREMISE if not any(w in t for t in premise)]
            assert not missing, missing
        assert not [t for t in texts if "추가로 줄일 여지" in t]
    elif item == "2":
        combo = [row for row in plain if row[:2] == ("Excel", "조합 비교")]
        excel = [row for row in combo if row[2].startswith("+ ")]
        word = [t for t in texts if t.startswith("Word") and "권장안은" in t]
        dr_rows = [t for t in texts if "+ 경제성DR" in t and not t.startswith("화면")]
        if zero:
            assert not dr_rows, dr_rows
            assert not [t for t in word if "경제성DR" in t], word
        else:
            assert excel[-1][2] == "+ 경제성DR", excel[-1]
            assert word and word[0].split("」")[0].endswith("+ 경제성DR"), word
        # 끝 줄 = 합산효과 — Excel 끝 줄 기간 절감이 Word 권장안의 기간 총 절감액과 같다
        assert [t for t in word if f"기간에 {int(excel[-1][6]):,}원" in t], (excel[-1], word)
    else:
        pytest.fail(f"모르는 항목 {item}")


@pytest.mark.parametrize("zero", [True, False], ids=["감축0", "감축있음"])
@pytest.mark.parametrize("item", ["1", "2"])
def test_S261_경제성DR_0_이면_참여_전제_글자와_0원_조합_줄이_서지_않는다(
    item: str, zero: bool
) -> None:
    """**S261 고침 1 · 2** (웹 대화창 판단 · 대형건물 사람 실물 점검).

    1 감축 가능량 0 이면 화면 ⚠ · Word · PPT ※ 의 참여 전제 글자(위약금 · 정산 단가 · 참고
      문턱)가 서지 않고 「저부하 평일이 없습니다」 는 선다 · PPT 머리에 「추가로」 가 없다.
      0 보다 크면 그대로 선다.
    2 효과 0 인 경제성DR 은 조합 끝 줄 · 권장안 이름에 서지 않고 끝 줄 = 합산효과 ·
      0 보다 크면 선다.
    """
    _s261_check(item, zero)


def _s262_check(item: str, request: pytest.FixtureRequest) -> None:
    """S262 결정 1 ~ 3 을 확인 사례 · 덱 벌의 화면 · 네 산출물과 계산 결과로 본다."""
    from kwise.tariff import TariffSelection

    selection = TariffSelection("general_b", "high_a", "I")
    if item == "1":
        from kwise.tariff import lagging_adjustment_ratio

        # 요금 셈 — 1% 단위 · 첫째자리 반올림 (제7조 ①)
        assert lagging_adjustment_ratio(95.5) == lagging_adjustment_ratio(96.0)
        assert lagging_adjustment_ratio(95.4) == lagging_adjustment_ratio(95.0)
        # (가1) 99.68 — 설명 글의 폭은 반올림한 100 에서 · 머리는 넣은 값 · 도입 후 역률은 정수
        lines = [" | ".join(row) for row in _render_human().rows]
        # S263 결정 7 — 두 값을 함께 · 폭과 감액률은 정수 (옛 「99.7% — … 8.0%p 초과, … 1.0% 감액」)
        head = (
            "주간 지상역률 99.7% (요금 계산은 1% 단위 반올림 100%) — 기준 92% 대비 8%p 초과, "
            "기본요금의 1% 감액"
        )
        assert [t for t in lines if head in t], [t for t in lines if "기준 92% 대비" in t]
        assert not [t for t in lines if "7.7%p" in t]
        assert [t for t in lines if "도입 후 역률 100% · 기본요금이 준 만큼 감액도 준다" in t]
        # 입력 되비춤 — 역률 개선 표의 현재 역률은 넣은 값이다
        echo = [t for t in lines if t.startswith("Word | 표") and "현재 역률 |" in t]
        assert [t for t in echo if t.endswith("| 99.7%")], echo
    elif item == "2":
        from kwise.measures import EssCostInput, evaluate_ess, with_load
        from kwise.report.worksheet import ess_worksheet
        from kwise.tariff import BillingOptions, calculate_bill

        # 태양광 — 「역률 영향 반영 시」 가 어디에도 없고 카드가 부록 기간 절감액과 같은 값이다
        rendered = _render("large-a")
        texts = [" | ".join(str(v) for v in row) for row in rendered.rows]
        assert not [t for t in texts if "역률 영향 반영 시" in t]
        note = [t for t in texts if t.startswith("PPT | 13") and "예상 역률" in t]
        assert note == ["PPT | 13 | ※ 예상 역률 90% 로 기준 92% 미달"], note
        formula = "도입 후 역률 90% · 추가 0.0% → 0.4%"
        solar = [t for t in texts if "태양광 계산 근거 | 기간 역률요금 절감 | " + formula in t]
        assert solar, [t for t in texts if "기간 역률요금 절감" in t]
        # ESS — 도입 후 역률로 다시 셈한 한 값 · 부록 역률 줄이 같은 원천의 꼴
        usage = request.getfixturevalue("sample_usage")
        tariff = request.getfixturevalue("tariff")
        options = BillingOptions(power_factor_pct=96.0)
        ess = evaluate_ess(
            usage,
            tariff,
            selection,
            target_kw=5_200.0,
            cost=EssCostInput.unpriced(),
            options=options,
        )
        after = BillingOptions(power_factor_pct=ess.power_factor_after_pct)
        base = calculate_bill(usage, tariff, selection, options=options)
        billed = calculate_bill(
            with_load(usage, ess.dispatch.net_kw), tariff, selection, options=after
        )
        assert ess.total_saving_won == pytest.approx(base.total_won - billed.total_won)
        rows = {row.label: row.formula for row in ess_worksheet(ess).rows}
        assert rows["기간 역률요금 절감"] == (
            f"도입 후 역률 {ess.power_factor_after_pct:,.0f}% · 기본요금이 준 만큼 감액도 준다"
        ), rows
    elif item == "3":
        from kwise.compare import CombinationSpec, compare_combinations
        from kwise.measures import high_rate_discharge_hours, min_pcs_power_kw, snap_spec
        from kwise.measures.ess import BELOW_MINIMUM_CONCLUSION
        from kwise.report import slides_bytes
        from kwise.report.document import DocumentSections, document_bytes

        comparison = request.getfixturevalue("sample_comparison")
        row = comparison.combinations[-1]
        dispatch = row.dispatch
        assert dispatch is not None
        power, capacity = dispatch.power_kw, dispatch.capacity_kwh
        # 2단계와 같은 조달 규격 — 격자에 올린 값이다
        assert snap_spec(power, capacity) == (power, capacity), (power, capacity)
        spec = f"{power:,.0f} kW / {capacity:,.0f} kWh (목표 5,000 kW)"
        assert row.row_name == f"+ ESS {spec}"
        # S263 결정 6 — Excel 조합 비교 A 열도 줄 이름 꼴
        assert f"ESS {spec}" in str(comparison.frame().loc[row.row_name, "수단"])
        hours = capacity / power
        facts = {item.fact for item in row.notices}
        assert ("ess.high_c_rate" in facts) == (0 < hours < high_rate_discharge_hours())
        sections = DocumentSections(
            usage=request.getfixturevalue("sample_usage"),
            bill=request.getfixturevalue("sample_bill"),
            diagnosis=request.getfixturevalue("sample_diagnosis"),
            comparison=comparison,
        )
        deck = _deck(slides_bytes(sections)[0])
        word = _document(document_bytes(sections)[0])
        assert [t for t in deck if t == f"+ ESS {spec}"], [t for t in deck if "ESS" in t]
        assert [t for t in word if t == f"+ ESS {spec}"], [t for t in word if "ESS" in t]
        # 최소 규격 미만 — 줄이 서지 않고 까닭은 2단계 문구 원천 한 줄이다
        usage = request.getfixturevalue("sample_usage")
        target = float(usage.kw.max()) - 20.0
        short = compare_combinations(
            usage,
            request.getfixturevalue("tariff"),
            (
                CombinationSpec("기준선", selection),
                CombinationSpec(f"+ ESS 목표 {target:,.0f} kW", selection, ess_target_kw=target),
            ),
        )
        assert [item.name for item in short.combinations] == ["기준선"]
        needed = short.ess_below_minimum_note
        assert needed.startswith("필요 출력이 ") and needed.endswith("산출하지 않았습니다."), needed
        assert needed == BELOW_MINIMUM_CONCLUSION.format(
            power=float(needed.split(" ")[2].replace(",", "")), minimum=min_pcs_power_kw()
        )
    else:
        pytest.fail(f"모르는 항목 {item}")


@pytest.mark.parametrize("item", ["1", "2", "3"])
def test_S262_역률_반올림_태양광_ESS_한_값_조합_ESS_규격(
    item: str, request: pytest.FixtureRequest
) -> None:
    """**S262 결정 1 ~ 3** (웹 대화창 판단 · 약관 제7조 ① · 결함 유형 ① · S233).

    1 역률은 1% 단위 반올림으로 요금을 셈하고 설명 글(폭)은 반올림 값 · 입력 되비춤은 입력값 ·
      도입 후 역률은 정수 %.
    2 태양광 · ESS 금액은 도입 후 역률로 다시 셈한 한 값 — 「역률 영향 반영 시」 가 서지 않고
      부록 역률 줄은 한 원천의 꼴(ESS 포함).
    3 조합 ESS 는 2단계와 같은 조달 규격 · 최소 규격 미만이면 줄이 서지 않는다(까닭 한 줄) ·
      PPT · Word 조합 줄과 Excel 수단 칸에 사양.
    """
    _s262_check(item, request)


def _s263_legend(figure: Any) -> tuple[list[str], list[str]]:
    """그림 하나의 (범례 차례, 막대가 위에서 아래로 선 차례) — 첫 칸의 막대로 잰다."""
    axes = figure.axes[0]
    legend = [text.get_text() for text in axes.get_legend().get_texts()]
    bars = [(box.get_label(), box.patches[0]) for box in axes.containers]
    if axes.yaxis_inverted():  # 가로 무리 막대 — y 가 작을수록 위다
        drawn = [label for label, _bar in sorted(bars, key=lambda item: item[1].get_y())]
    else:  # 세로 누적 막대 — 밑단이 높을수록 위다
        drawn = [label for label, _bar in sorted(bars, key=lambda item: -item[1].get_y())]
    return legend, drawn


def _s263_check(item: str, request: pytest.FixtureRequest) -> None:
    """S263 결정 1 ~ 7 · 3 곁을 덱 벌 · 확인 사례 (가1) · 표본 비교의 실물과 계산으로 본다."""
    from kwise.report.document import ESS_PAYBACK_CAVEAT

    old_caveat = "규칙기반 단일 디스패치"
    if item == "1":
        from pptx import Presentation

        rendered = _render("large-b-over")
        texts = [" | ".join(str(v) for v in row) for row in rendered.rows]
        assert not [t for t in texts if old_caveat in t]
        assert [t for t in texts if t.startswith("Word") and ESS_PAYBACK_CAVEAT in t]
        # PPT ESS 장 — 단서가 윗줄 ※ 와 한 글상자에 잇는다
        boxes = [
            [p.text for p in shape.text_frame.paragraphs]
            for slide in Presentation(io.BytesIO(rendered.payloads["ppt"])).slides
            for shape in slide.shapes
            if shape.has_text_frame
            and any(ESS_PAYBACK_CAVEAT in p.text for p in shape.text_frame.paragraphs)
        ]
        assert len(boxes) == 1, boxes
        assert [t for t in boxes[0] if t.startswith("※ 표식")], boxes
    elif item == "2":
        from kwise.report import figures

        captured: list[Any] = []

        def grab(figure: Any) -> bytes:
            captured.append(figure)
            return b""

        patch = pytest.MonkeyPatch()
        patch.setattr(figures, "render_png", grab)
        try:
            figures.combination_png(request.getfixturevalue("sample_comparison"))
            structure = request.getfixturevalue("sample_diagnosis").structure
            figures.monthly_charge_png(structure)
        finally:
            patch.undo()
        combination, monthly = (_s263_legend(figure) for figure in captured)
        assert combination == (["투자비", "절감액"], ["투자비", "절감액"]), combination
        assert monthly[0] == monthly[1] and monthly[0][-1] == "기본요금", monthly
    elif item == "3":
        rendered = _render("large-b-over")
        deck = [" | ".join(str(v) for v in row) for row in rendered.rows if row[0] == "PPT"]
        assert not [t for t in deck if "Excel" in t or "근거를 싣지 않았습니다" in t]
    elif item == "3곁":
        from kwise.report import notices

        patch = pytest.MonkeyPatch()
        patch.setattr(notices, "diff_from_defaults", lambda: ("바꾼 항목",))
        try:
            line = notices.applied_basis_line(market_rules=False)
        finally:
            patch.undo()
        assert line == "한전 기본공급약관 · 전기요금표 — 일부 값을 바꿔 계산했습니다", line
    elif item == "4":
        rendered = _render("large-b-over")
        tables = [
            row[2:] for row in rendered.rows if row[0] == "PPT" and str(row[1]).endswith("표")
        ]
        invest = [row for row in tables if row[:2] == ("투자비", "설비와 전기공사 포함")]
        assert len(invest) == 1, invest
        assert not [row for row in tables if row[0] in ("설비비", "전기공사")]
        excel = [row for row in rendered.rows if row[0] == "Excel" and "ESS 계산 근거" in row]
        assert [row for row in excel if "설비비" in row] and [r for r in excel if "전기공사" in r]
        total = next(row for row in excel if "투자비" in row)
        assert total[-1] == invest[0][2], (total, invest)
    elif item == "5":
        from kwise.compare import CombinationSpec, evaluate_combination
        from kwise.measures import power_factor_after_pct, with_load
        from kwise.tariff import BillingOptions, TariffSelection, billed_pct, calculate_bill

        usage = request.getfixturevalue("sample_usage")
        tariff = request.getfixturevalue("tariff")
        selection = TariffSelection("general_b", "high_a", "I")
        # 반올림 경계(92.5 → 93) — ESS 가 낮 부하를 줄이면 92 로 셈한다
        options = BillingOptions(power_factor_pct=92.5)
        row = evaluate_combination(
            usage,
            tariff,
            CombinationSpec("+ ESS 목표 5,200 kW", selection, ess_target_kw=5_200.0),
            baseline_bill=calculate_bill(usage, tariff, selection, options=options),
            options=options,
        )
        assert row.dispatch is not None and row.load_kw is not None
        after = billed_pct(
            power_factor_after_pct(
                usage.kw,
                usage.kw - row.load_kw,
                power_factor_pct=92.5,
                interval_minutes=usage.meta.interval_minutes,
            )
        )
        assert after == 92.0, after
        billed = calculate_bill(
            with_load(usage, row.load_kw),
            tariff,
            row.selection,
            options=BillingOptions(power_factor_pct=after),
        )
        assert row.bill.total_won == pytest.approx(billed.total_won)
    elif item == "6":
        from kwise.report import slides_bytes
        from kwise.report.document import DocumentSections, document_bytes
        from kwise.report.frames import combination_frame

        comparison = request.getfixturevalue("sample_comparison")
        row = comparison.combinations[-1]
        assert row.dispatch is not None and comparison.best is row
        size = f"{row.dispatch.power_kw:,.0f} kW / {row.dispatch.capacity_kwh:,.0f} kWh"
        short = f"+ ESS {size}"
        full = f"+ ESS {size} (목표 5,000 kW)"
        assert (row.short_name, row.row_name) == (short, full)
        assert f"ESS {size}" in row.composition() and "ESS 5,000 kW" not in row.composition()
        assert full in comparison.frame().index
        assert short in list(combination_frame(comparison)["조합"])
        assert [n for n in comparison.notices if n.text.startswith(f"{full} — ")]
        assert not [n for n in comparison.notices if "ESS 목표 5,000 kW" in n.text]
        sections = DocumentSections(
            usage=request.getfixturevalue("sample_usage"),
            bill=request.getfixturevalue("sample_bill"),
            diagnosis=request.getfixturevalue("sample_diagnosis"),
            comparison=comparison,
        )
        deck = _deck(slides_bytes(sections)[0])
        word = _document(document_bytes(sections)[0])
        assert [t for t in word if t == short], [t for t in word if "ESS" in t]
        assert [t for t in deck + word if f"+ ESS {size} + " in t or t.endswith(f"+ ESS {size}")]
        assert not [t for t in deck + word if "ESS 목표 5,000 kW" in t or "ESS 5,000 kW" in t]
    elif item == "7":
        from kwise.tariff import power_factor_charge

        def said(**kwargs: Any) -> list[str]:
            return [n.text for n in power_factor_charge(1_000_000.0, **kwargs).notices]

        # (가1) 99.68 — 입력값과 반올림 값을 함께 · 폭과 감액률은 정수
        lines = [" | ".join(row) for row in _render_human().rows]
        head = (
            "주간 지상역률 99.7% (요금 계산은 1% 단위 반올림 100%) — 기준 92% 대비 8%p 초과, "
            "기본요금의 1% 감액"
        )
        hits = [t for t in lines if head in t]
        assert [t for t in hits if t.startswith("Excel | 요약")], hits
        assert not [t for t in lines if "8.0%p" in t]
        # 같으면 괄호가 없다 · 판정이 반올림 값에서 나오면 두 값을 함께
        assert "주간 지상역률 96.0% — 기준 92% 대비 4%p 초과, 기본요금의 0.8% 감액" in " ".join(
            said(lagging_pct=96.0)
        )
        assert said(lagging_pct=91.6)[2].startswith(
            "주간 지상역률 91.6% (요금 계산은 1% 단위 반올림 92%) — 기준 92% 충족"
        )
        below = [t for t in said(lagging_pct=89.6) if t.startswith("주간 지상역률이")]
        assert below and "(89.6%, 요금 계산은 1% 단위 반올림 90%)" in below[0], below
        assert (
            "야간 진상역률 94.6% (요금 계산은 1% 단위 반올림 95%) — 기준 95% 이상이라 추가 없음."
            in said(lagging_pct=96.0, leading_pct=94.6)
        )
    else:
        pytest.fail(f"모르는 항목 {item}")


@pytest.mark.parametrize("item", ["1", "2", "3", "3곁", "4", "5", "6", "7"])
def test_S263_PPT_의견_넷_조합_ESS_역률_이름_역률_설명_글(
    item: str, request: pytest.FixtureRequest
) -> None:
    """**S263 결정 1 ~ 7** (사람 결정 1 ~ 4 · 웹 대화창 판단 3 곁 · 5 · 6 · 7).

    1 ESS 단서는 새 글자 · PPT 에서 윗줄 ※ 와 한 글상자. 2 조합 · 월별 요금 그림의 범례 차례 =
    막대가 선 차례. 3 PPT 에 Excel 안내 · 뺀 수단 각주가 없다. 3곁 적용 기준 꼬리에 Excel 이 없다.
    4 PPT ESS 부록 투자비 한 줄 · Excel 부록 A 는 세 줄. 5 조합 ESS 도 도입 후 역률로 셈한다.
    6 조합 ESS 는 규격으로 이름을 적는다. 7 역률 설명 글은 입력값과 반올림 값을 함께 · 정수 %.
    """
    _s263_check(item, request)


def _s264_sections(
    request: pytest.FixtureRequest, stage: tuple[float, float, float], *, pv: bool = True
) -> Any:
    """표본 비교(끝 줄 = 태양광 500 kWp + ESS 목표 5,000 kW)에 2단계 ESS 사양을 단 재료."""
    from dataclasses import replace

    from kwise.measures import measure_kind
    from kwise.report.document import DocumentSections, MeasureEntry

    comparison = request.getfixturevalue("sample_comparison")
    if not pv:
        rows = tuple(
            replace(item, spec=replace(item.spec, pv_capacity_kwp=0.0))
            for item in comparison.combinations
        )
        comparison = replace(comparison, combinations=rows)
    entry = MeasureEntry(
        kind=measure_kind("ess"),
        conclusion="",
        saving="",
        investment="",
        payback="",
        certainty="",
        ess_sizing=stage,
    )
    return DocumentSections(
        usage=request.getfixturevalue("sample_usage"),
        bill=request.getfixturevalue("sample_bill"),
        diagnosis=request.getfixturevalue("sample_diagnosis"),
        comparison=comparison,
        measures=(entry,),
    )


def _s264_check(item: str, request: pytest.FixtureRequest, tmp_path: Path) -> None:
    """S264 결정 1 ~ 6 을 표본 비교 · 덱 벌 · 실제로 만든 산출물의 글자로 본다."""
    from kwise.report.slides import combination_notes

    comparison = request.getfixturevalue("sample_comparison")
    row = comparison.combinations[-1]
    assert row.dispatch is not None and row.spec.has_pv
    power, capacity = round(row.dispatch.power_kw), round(row.dispatch.capacity_kwh)
    if item == "1":
        from kwise.report import slides_bytes

        sections = _s264_sections(request, (5_000.0, power + 100.0, capacity + 100.0))
        text = (
            f"태양광이 피크를 먼저 낮춰, 조합의 ESS 는 단독 도입({power + 100:,} kW / "
            f"{capacity + 100:,} kWh)보다 작은 {power:,} kW / {capacity:,} kWh 로 충분합니다."
        )
        assert text in combination_notes(sections), combination_notes(sections)
        deck = _deck(slides_bytes(sections)[0])
        assert f"※ {text}" in deck, [t for t in deck if t.startswith("※")]
    elif item == "1안":
        # 참이 아니면 세우지 않는다 — 같다 · 조합이 더 크다 · 목표가 다르다 · 태양광 없이 작다
        cases = {
            "같다": _s264_sections(request, (5_000.0, float(power), float(capacity))),
            "조합이 더 크다": _s264_sections(request, (5_000.0, power / 2, capacity / 2)),
            "한쪽만 작다": _s264_sections(request, (5_000.0, power + 100.0, capacity / 2)),
            "목표가 다르다": _s264_sections(request, (5_100.0, power + 100.0, capacity + 100.0)),
            "태양광 없음": _s264_sections(
                request, (5_000.0, power + 100.0, capacity + 100.0), pv=False
            ),
        }
        for name, sections in cases.items():
            assert not [t for t in combination_notes(sections) if "조합의 ESS" in t], name
        ppt = [row for row in _render("large-b-over").rows if row[0] == "PPT"]
        assert not [row for row in ppt if "조합의 ESS 는 단독 도입" in str(row[-1])]
    elif item == "2":
        from kwise.measures import evaluate_power_factor
        from kwise.report.document import _power_factor_conclusion
        from kwise.tariff import TariffSelection

        selection = TariffSelection("general_b", "high_a", "I")
        usage = request.getfixturevalue("sample_usage")
        tariff = request.getfixturevalue("tariff")
        tail = " 는 감액 상한 97% 이상이라 개선할 것이 없습니다."
        mixed = evaluate_power_factor(usage, tariff, selection, current_pct=96.7)
        assert mixed.no_headroom
        head = "지상역률 96.7% (요금 계산은 1% 단위 반올림 97%)"
        assert f"현재 {head}{tail}" in [n.text for n in mixed.notices]
        # 결론 한 줄(PPT 역률 장 첫 문장 · Word)은 반올림이 판정을 바꿀 때만 반올림 값을
        # 적고, 괄호 뒤에 조사를 달지 않는다 (S270 결정 4). 안내 줄은 위 그대로다.
        crossed = (
            "지상역률 96.7% 는 요금 계산에서 1% 단위 반올림으로 97% 가 되어, "
            "감액 상한 97% 이상이라 개선할 것이 없습니다."
        )
        assert _power_factor_conclusion(mixed) == crossed
        same = evaluate_power_factor(usage, tariff, selection, current_pct=98.0)
        assert f"현재 지상역률 98.0%{tail}" in [n.text for n in same.notices]
        assert _power_factor_conclusion(same) == f"지상역률 98.0%{tail}"
        # 99.68 → 100 은 넣은 값도 상한 이상이라 판정이 안 갈린다 — 결론에 괄호가 없다.
        above = evaluate_power_factor(usage, tariff, selection, current_pct=99.68)
        noted = f"현재 지상역률 99.7% (요금 계산은 1% 단위 반올림 100%){tail}"
        assert noted in [n.text for n in above.notices]
        assert _power_factor_conclusion(above) == f"지상역률 99.7%{tail}"
        # 실제로 만든 PPT — 역률 장 첫 문장이 그 글자다.
        from kwise.report import slides_bytes
        from kwise.report.document import DocumentSections, measure_entries

        for result, sentence in ((mixed, crossed), (above, f"지상역률 99.7%{tail}")):
            sections = DocumentSections(
                usage=usage,
                bill=request.getfixturevalue("sample_bill"),
                diagnosis=request.getfixturevalue("sample_diagnosis"),
                measures=measure_entries(power_factor=result),
            )
            deck = _deck(slides_bytes(sections)[0])
            assert sentence in deck, [t for t in deck if "감액 상한" in t]
            assert not [t for t in deck if ") 는" in t and "반올림" in t], deck
    elif item == "3":
        rendered = _render("large-b-over")
        word = [
            row[2:] for row in rendered.rows if row[0] == "Word" and str(row[1]).startswith("표")
        ]
        invest = [row for row in word if row[:2] == ("투자비", "설비와 전기공사 포함")]
        assert len(invest) == 1, invest
        assert not [row for row in word if row[:2] == ("설비비", "도입 사례 회귀")]
        assert not [row for row in word if row[:2] == ("전기공사", "옥외 기준 구간의 대표값")]
        excel = [row for row in rendered.rows if row[0] == "Excel" and "ESS 계산 근거" in row]
        assert [row for row in excel if "설비비" in row] and [r for r in excel if "전기공사" in r]
        total = next(row for row in excel if "투자비" in row)
        assert total[-1] == invest[0][2], (total, invest)
    elif item == "4":
        # 0줄 — 조정률 칸은 반올림 값에서 나온 한 값이고 입력값은 옆 줄에 따로 선다
        from kwise.measures import evaluate_power_factor
        from kwise.report.worksheet import power_factor_worksheet
        from kwise.tariff import TariffSelection

        result = evaluate_power_factor(
            request.getfixturevalue("sample_usage"),
            request.getfixturevalue("tariff"),
            TariffSelection("general_b", "high_a", "I"),
            current_pct=96.4,
        )
        cells = {r.label: r.value for r in power_factor_worksheet(result).rows}
        assert (cells["현재 역률"], cells["조정률"]) == ("96.4%", "+0.2%p"), cells
    elif item == "5":
        from kwise.report.batch import CaseSpec, run_case
        from tests.conftest import ESS_COST_WON_PER_KW, SAMPLE_USAGE_CSV

        if not SAMPLE_USAGE_CSV.is_file():
            pytest.skip(f"샘플 파일이 없습니다: {SAMPLE_USAGE_CSV}")
        patch = pytest.MonkeyPatch()
        patch.setenv("PROJECT_CACHE", str(tmp_path / "cache"))
        try:
            summary = run_case(
                CaseSpec(
                    name="S264",
                    usage=SAMPLE_USAGE_CSV,
                    ess_target_kw=5_000.0,
                    ess_unit_cost_won_per_kw=ESS_COST_WON_PER_KW,
                ),
                request.getfixturevalue("tariff"),
                output_dir=tmp_path / "out",
                include_timeseries=False,
            )
        finally:
            patch.undo()
        name = summary.best_combination
        assert re.search(r"\+ ESS [\d,]+ kW / [\d,]+ kWh$", name), name
        assert "목표" not in name, name
    elif item == "6":
        size = f"{power:,} kW / {capacity:,} kWh"
        sizing = [n.text for n in comparison.notices if n.fact.startswith("combination.ess_sizing")]
        assert sizing and all(
            t.startswith(f"+ ESS {size} (목표 5,000 kW) — 하루 최대 초과 에너지 ") for t in sizing
        ), sizing
        assert all(t.count(size) == 1 for t in sizing), sizing
    else:
        pytest.fail(f"모르는 항목 {item}")


@pytest.mark.parametrize("item", ["1", "1안", "2", "3", "4", "5", "6"])
def test_S264_조합_ESS_크기_안내_역률_상한_글_ESS_투자비_한_줄_이름(
    item: str, request: pytest.FixtureRequest, tmp_path: Path
) -> None:
    """**S264 결정 1 ~ 6** (사람 결정 1 · 웹 대화창 판단 2 ~ 6).

    1 조합 ESS 가 2단계보다 작고 태양광이 들면 PPT 조합 장 ※ 한 줄. 1안 참이 아니면 안 선다.
    2 역률 상한 판정 글은 입력값과 반올림 값을 함께(같으면 괄호 없음) — 결론 한 줄(PPT 역률 장
      첫 문장)은 반올림이 판정을 바꿀 때만 반올림 값을 문장으로 적는다(S270 결정 4).
    3 Word ESS 부록 투자비 한 줄 · Excel 은 세 줄. 4 조정률 칸은 한 값(0줄).
    5 배치 요약 CSV 권장 조합은 규격 이름. 6 조합 ESS 안내에 사양이 한 번.
    """
    _s264_check(item, request, tmp_path)


# ============================================ S266 — 사람 물음 열넷 (결정 1 · 2 · 4 · 9 · 10)

#: 줄끝을 안 바꾸는 원문 사본 둘 (S266 결정 9).
S266_COPIES = ("2024-02_전력시장운영규칙_검색용.txt", "2024-10-24_기본공급약관_검색용.txt")
#: 판정 근거로 저장소에 싣는 정본 둘 (S266 결정 10).
S266_PDFS = ("2026-06-01_기본공급약관시행세칙(합본).pdf", "2026-08-01_전기요금표(종합).pdf")


def _s266_settings(root: Path, item: str) -> None:
    """저장소 설정 둘 — 원문 사본 줄끝(결정 9) · 판정 근거 PDF(결정 10). ``root`` 는 뿌리다."""
    source = root / "data" / "source"
    if item == "9":
        rules = (root / ".gitattributes").read_text(encoding="utf-8").splitlines()
        assert "data/source/*_검색용.txt -text" in rules, rules
        for name in S266_COPIES:
            # 뽑은 그대로다 — git 이 줄끝을 LF 로 고쳐 실었으면 CRLF 가 없다.
            assert b"\r\n" in (source / name).read_bytes(), name
    else:
        ignored = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
        sizes = []
        for name in S266_PDFS:
            assert f"!data/source/{name}" in ignored, name
            assert (source / name).read_bytes()[:4] == b"%PDF", name
            sizes.append((source / name).stat().st_size)
        assert sum(sizes) <= 20 * 1024 * 1024, sizes


def _s266_check(item: str, request: pytest.FixtureRequest) -> None:
    """S266 결정 1 · 2 · 4 · 9 · 10 을 실제로 만든 산출물의 글자와 저장소 실물로 본다."""
    if item in ("9", "10"):
        _s266_settings(PROJECT_ROOT, item)
        return
    if item == "1":
        from kwise.report.casestudy import build_case_definitions

        sys.path.insert(0, str(PROJECT_ROOT / "tools"))
        import render_deck

        cases = PROJECT_ROOT / "input" / "cases"
        if not cases.is_dir():
            pytest.skip(f"케이스 파일이 없습니다: {cases}")
        case = {d.key: d for d in build_case_definitions(cases)}["C9"]
        deck = render_deck.BY_KEY["large-ind-b"]
        assert (
            case.usage_path.name,
            case.contract_type,
            case.voltage,
            case.option,
            case.contract_kw,
        ) == (deck.csv.name, "industrial_b", deck.voltage, deck.option, deck.contract_kw)
        assert deck.contract_type == "industrial_b"
        return
    rendered = _render("large-b-over")
    if item == "2":
        date = request.getfixturevalue("tariff").effective_date
        note = " (분석 기간 전체에 적용)"
        hits = [row for row in rendered.rows if any(note.strip() in str(v) for v in row)]
        # 새 글자는 이 괄호 하나이고 네 자리다 — 화면에는 없다.
        assert sorted((str(row[0]), str(row[-1])) for row in hits) == [
            ("Excel", f"{date} 시행{note}"),
            ("PPT", f"{date}{note}"),
            ("Word", f"{date}{note}"),
            ("Word", f"적용 요금표: {date} 시행{note}"),
        ], hits
        tables = [row for row in hits if row[0] in ("PPT", "Word") and len(row) == 4]
        assert [row[2] for row in tables] == ["적용 요금표 시행일"] * 2, tables
    elif item == "4":
        caption = "조합별 누적 기간 절감액과 누적 투자비"
        word = [str(row[-1]) for row in rendered.rows if row[0] == "Word"]
        assert [t for t in word if re.fullmatch(rf"그림 \d+-1\. {caption}", t)], [
            t for t in word if t.startswith("그림")
        ]
        assert caption in [str(row[-1]) for row in rendered.rows if row[0] == "PPT"]
        assert not [t for t in rendered.texts if "조합별 기간 절감액과 투자비" in t]
    else:
        pytest.fail(f"모르는 항목 {item}")


@pytest.mark.parametrize("item", ["1", "2", "4", "9", "10"])
def test_S266_요금표_시행일_괄호_Word_조합_캡션_산업용_벌_원문_사본_정본_PDF(
    item: str, request: pytest.FixtureRequest
) -> None:
    """**S266 결정 1 · 2 · 4 · 9 · 10** (사람 결정 1 · 웹 대화창 판단 2 · 4 · 9 · 10).

    1 케이스 스터디 C9 이 덱 벌 `large-ind-b` 와 같은 조건이다(산업용(을) · 대형 실측 · 6,000 kW).
    2 요금표 시행일을 적는 네 자리에 「 (분석 기간 전체에 적용)」 · 화면 0.
    4 Word 조합 그림 캡션이 PPT 그림 글과 같다. 9 원문 사본 둘은 줄끝을 안 바꾼다.
    10 판정 근거 PDF 둘이 저장소에 있다(합 20 MB 이하). 결정 5 는 `test_compare.py` ·
    `test_ui_screen.py` 의 세 시험이 문다.
    """
    _s266_check(item, request)


#: S268 결정 2 의 주의 글 — 글자를 여기 다시 적는다(상수를 들이면 글자가 바뀌어도 초록이다).
HOURLY_CAUTION = (
    "1시간 간격 자료입니다. 요금적용전력은 15분 최대수요로 정해지므로, "
    "이 자료로 낸 최대수요와 기본요금은 실제보다 낮을 수 있습니다."
)


def _interval_words(rendered: Rendered, word: str) -> dict[str, int]:
    """그림 · 표 이름에 간격 글자 ``word`` 가 선 자리 수 — 갈래마다."""
    cells = [str(cell) for row in (*rendered.rows, *rendered.figures) for cell in row[1:]]
    kinds = {
        "화면 그림 축 이름": rf"· {word} 부하$",
        "화면 그림 캡션": rf"· {word} 부하와 판정 창$",
        "화면 캡션 툴팁": rf"^대표일의 {word} 부하이고",
        "산출물 그림 축 이름": rf"· {word}$",
        "산출물 그림 범례": rf"^{word} 부하$",
        "시계열 시트 체크박스": rf"^{word} 시계열 시트 포함$",
    }
    return {name: sum(1 for cell in cells if re.search(mark, cell)) for name, mark in kinds.items()}


def test_1시간_자료면_주의와_간격_글자가_서고_15분_자료에는_안_선다() -> None:
    """**1시간 평균은 15분 최대수요보다 낮다 — 그 사실을 결과 곁에 세운다** (S268 결정 2).

    같은 건물(`small-a2` · 용인 15분 실물)을 그대로 올린 벌과 1시간으로 합쳐 올린 벌을
    맞댄다 — 둘 다 쪽을 문다. 1시간이면 주의 한 줄이 화면 1단계(한 번) · PPT 3장 · Excel 요약에
    서고 그림 · 표 이름의 간격 글자가 「1시간」 이다. 15분이면 그 줄이 없고 「15분」 이다.
    계산은 안 건드린다(보정하지 않는다).
    """
    from openpyxl import load_workbook

    def caution(rendered: Rendered) -> list[tuple[str, str]]:
        return sorted(
            (str(row[0]), str(row[1]))
            for row in rendered.rows
            if any(HOURLY_CAUTION in str(cell) for cell in row[2:])
        )

    def sheets(rendered: Rendered) -> list[str]:
        book = load_workbook(io.BytesIO(rendered.payloads["excel"]), read_only=True)
        return [name for name in book.sheetnames if "시계열" in name]

    hourly, quarter = _render_hourly(), _render("small-a2")

    # 서는 자리는 셋이고 저마다 한 번이다 — Word 는 품질 주의를 싣는 자리가 없어 0 이다.
    stood = caution(hourly)
    assert [kind for kind, _where in stood] == ["Excel", "PPT", "화면"], stood
    assert stood[0] == ("Excel", "요약") and stood[1] == ("PPT", "3"), stood
    assert stood[2][1].endswith("1단계 · 진단"), stood
    assert caution(quarter) == []

    assert all(count > 0 for count in _interval_words(hourly, "1시간").values()), hourly.key
    assert not any(_interval_words(hourly, "15분").values()), _interval_words(hourly, "15분")
    assert sheets(hourly) == ["1시간 시계열"]

    assert all(count > 0 for count in _interval_words(quarter, "15분").values()), quarter.key
    assert not any(_interval_words(quarter, "1시간").values()), _interval_words(quarter, "1시간")
    assert sheets(quarter) == ["15분 시계열"]


#: S269 결정 3 의 주의 글 — 기본요금을 계약전력으로 매기는 건물의 1시간 주의(글자를 다시 적는다).
HOURLY_CAUTION_ON_CONTRACT = (
    "1시간 간격 자료입니다. 이 자료로 낸 최대수요는 15분 최대수요보다 낮을 수 있습니다."
)


def _cells_with(rendered: Rendered, word: str) -> list[tuple[str, str]]:
    """``word`` 가 든 줄의 (산출물, 자리) — 줄마다 한 번."""
    return sorted(
        (str(row[0]), str(row[1]))
        for row in rendered.rows
        if any(word in str(cell) for cell in row[2:])
    )


def test_1시간_자료의_간격_문장과_계약전력_기준_주의_글() -> None:
    """**자료 간격을 말하는 문장은 판정한 간격을 따른다** (S269 결정 2 · 3).

    S268 은 그림 · 표 이름만 「1시간」 으로 갈았다 — 한계 글 「본 데이터는 15분 유효전력뿐입니다」
    와 툴팁 「어느 15분 구간에서도 …」 가 1시간 자료에도 그대로 섰다. 같은 건물을 15분 그대로 ·
    1시간으로 합쳐(요금적용전력 기준) · 1시간으로 합쳐 계약전력 기준 종별로 띄운 세 벌을 맞댄다.

    요금 제도를 말하는 「15분 최대수요」 는 사실이라 1시간 자료에서도 그대로다. 기본요금을
    계약전력으로 매기는 건물은 1시간 자료라도 기본요금이 낮게 서지 않는다 — 그 건물의
    주의는 최대수요만 말한다.
    """
    hourly, quarter = _render_hourly(), _render("small-a2")
    on_contract = _render_hourly("general_a_1")

    # 결정 2 — 한계 글은 Excel 요약 · 부록 C · Word 세 자리, 툴팁은 화면 한 자리.
    for rendered, here, gone in ((hourly, "1시간", "15분"), (quarter, "15분", "1시간")):
        limit = _cells_with(rendered, f"본 데이터는 {here} 유효전력뿐입니다")
        assert [kind for kind, _where in limit] == ["Excel", "Excel", "Word"], (rendered.key, limit)
        tip = _cells_with(rendered, f"어느 {here} 구간에서도 발전이 부하를 넘지 않는")
        assert [kind for kind, _where in tip] == ["화면"], (rendered.key, tip)
        assert _cells_with(rendered, f"본 데이터는 {gone}") == [], rendered.key
        assert _cells_with(rendered, f"어느 {gone} 구간에서도") == [], rendered.key

    # 결정 3 — 주의가 서는 자리는 같은 셋이고 글만 갈린다. 두 글이 한 벌에 함께 서지 않는다.
    assert [kind for kind, _where in _cells_with(hourly, HOURLY_CAUTION)] == [
        "Excel",
        "PPT",
        "화면",
    ]
    assert _cells_with(hourly, HOURLY_CAUTION_ON_CONTRACT) == []
    stood = _cells_with(on_contract, HOURLY_CAUTION_ON_CONTRACT)
    assert [kind for kind, _where in stood] == ["Excel", "PPT", "화면"], stood
    assert stood[0] == ("Excel", "요약") and stood[1] == ("PPT", "3"), stood
    assert _cells_with(on_contract, HOURLY_CAUTION) == []
    assert _cells_with(on_contract, "본 데이터는 1시간 유효전력뿐입니다")
    assert _cells_with(quarter, "1시간 간격 자료입니다") == []


#: 저압 건물에 서지 않는 야간 진상 안내 — 줄마다 그 글에만 든 조각 (S269 결정 1).
NIGHT_LEADING_WORDS = (
    "진상 추가요금 0원입니다",
    "야간 진상 여부를 확인하지 않았습니다",
    "야간(22~08시) 진상 95% 가 기준이며",
    "야간 경부하에서 진상으로 넘어갑니다",
    "야간 진상을 피할 수 있습니다",
)


def test_저압_건물에는_야간_진상_안내와_입력칸이_안_선다() -> None:
    """**저압에는 진상역률 요금이 없다 — 안내도 입력칸도 세우지 않는다** (S269 결정 1).

    약관 제43조 ② 2호 다목(S268 결정 1). 덱의 저압 벌(`small-ind-a1` · 산업용(갑)Ⅰ 저압)과
    같은 자료의 고압 벌(`small-ind-a2` · 고압A)을 앱으로 띄워 네 산출물을 맞댄다. 저압은
    야간 진상 안내 다섯 갈래가 0줄이고 「야간 진상역률을 안다」 입력칸이 없다. 고압은 다 선다.
    「역률 (선택)」 아래 「모르면 지상으로 간주해 추가요금이 없습니다.」 줄과 그 줄의 매뉴얼
    안내도 저압에는 없다(S270 결정 1). 2단계 카드의 매뉴얼 가리킴 툴팁과 기준 데이터 표의
    진상 줄은 두 벌 다 그대로다(건물을 말하지 않는다).
    """
    low, high = _render("small-ind-a1"), _render("small-ind-a2")

    for word in NIGHT_LEADING_WORDS:
        assert _cells_with(low, word) == [], (word, _cells_with(low, word))
        assert _cells_with(high, word), word
    box = "야간 진상역률을 안다"
    assert box not in [text for _slot, text in low.screen]
    assert box in [text for _slot, text in high.screen]

    # 그 칸을 두고 하던 말(「역률 (선택)」 아래 지상 간주 한 줄)과 그 줄에 달린 매뉴얼
    # 안내도 저압에는 안 선다 (S270 결정 1). 고압은 둘 다 선다. 같은 매뉴얼 안내는 2단계
    # 역률 개선 카드에 따로 달려 있어 저압에도 그 한 줄은 남는다.
    deemed = "모르면 지상으로 간주해 추가요금이 없습니다."
    block = "역률 (선택)"
    tip = "야간 진상 조항과 지상 간주"
    assert _cells_with(low, deemed) == [], _cells_with(low, deemed)
    assert [kind for kind, _where in _cells_with(high, deemed)] == ["화면"]
    assert not [where for _kind, where in _cells_with(low, tip) if where.endswith(block)]
    assert [where for _kind, where in _cells_with(high, tip) if where.endswith(block)]
    assert len(_cells_with(low, tip)) == len(_cells_with(high, tip)) - 1 > 0

    # 빼기만 했다 — 제도 한 줄은 야간 구절 없이 서고, 남은 「진상」 은 가리킴 툴팁과
    # 기준 데이터 표다.
    assert _cells_with(low, "주간(08~22시) 지상 92% 가 기준이며 매 1%당 0.2% 입니다.")
    for row in low.rows:
        line = " | ".join(str(cell) for cell in row)
        if "진상" in line:
            assert "야간 진상 조항과 지상 간주" in line or "기본공급약관 제43조 ② 2호" in line, row


# ─────────────────────────────────── S271 — 산업용(갑)Ⅰ 저압 실측 벌의 실물
#
# 세 벌을 앱으로 띄워 네 산출물을 맞댄다.
#
#     small-ind-a1   122일 · 저압 · 계약전력 기준 · 24시간 가동(주말 부하 = 평일의 97%)
#     small-ind-a2   365일 · 고압A · 요금적용전력 기준 · 주말 부하 = 평일의 59%
#     large-a        368일 · 고압A · 계약전력 기준
#
# 서는 쪽(산업용 벌)과 안 서는 쪽(맞수 둘)을 함께 문다.


def _kinds(rendered: Rendered, word: str) -> list[str]:
    """``word`` 가 든 줄의 산출물 이름 — 줄마다 한 번 · 이름 차례."""
    return [kind for kind, _where in _cells_with(rendered, word)]


def _diagnosis_sheet(rendered: Rendered) -> dict[str, str]:
    """Excel 「진단」 시트의 항목 → 값."""
    return {
        str(row[2]): str(row[3])
        for row in rendered.rows
        if row[:2] == ("Excel", "진단") and len(row) > 3
    }


#: 12개월 미만 문장의 몸 (S271 결정 1) — 화면은 물결표를 escape 하므로 날짜 뒤 조각으로 찾는다.
SHORT_PERIOD_BODY = (
    "(여름 92일, 봄·가을 30일, 겨울 0일)의 값을 12개월로 늘린 값이라 "
    "겨울이 반영되지 않았고, 실제보다 클 수 있습니다."
)
#: 계약전력 권고의 기간 기준 문장의 뒷몸 (S271 결정 2).
CONTRACT_PERIOD_BODY = "최대 기준입니다. 나머지 달의 최대를 확인한 뒤 신청하십시오."
#: 주말에도 가동하는 건물의 DR 한 줄 (S271 결정 3) — 수는 그 벌 값이다.
WEEKEND_OPERATING_LINE = (
    "주말·공휴일의 판정 시간대 평균 부하가 평일의 97% 로 기준 90% 이상이라, "
    "저부하 평일을 세지 않았습니다."
)


def test_S271_12개월_미만_문장과_계약전력_권고의_기간_기준() -> None:
    """**12개월 미만 자료는 기간과 계절 구성을 밝히고, 계약전력 권고에 기간 기준을 단다**
    (S271 결정 1 · 2 — 사람 결정).

    산업용 벌(122일 — 여름 92 · 봄·가을 30 · 겨울 0)에서 그 문장이 다섯 자리(화면 1단계 ·
    2단계 선택요금 카드 · PPT 3장 · Excel 요약 · Word)에 **한 글자**로 서고, 머리 문장만 선
    자리가 없다. 계약전력 필수 안내가 서는 여섯 자리마다 기간 기준 한 줄이 따라 선다.
    12개월 벌에는 둘 다 0줄이다.
    """
    low, high, over = _render("small-ind-a1"), _render("small-ind-a2"), _render("large-b-over")

    assert _kinds(low, SHORT_PERIOD_BODY) == ["Excel", "PPT", "Word", "화면", "화면"]
    assert _cells_with(low, "2026-09-08 " + SHORT_PERIOD_BODY)[:3] == [
        ("Excel", "요약"),
        ("PPT", "3"),
        ("Word", "List Bullet"),
    ]
    head = "분석 기간이 12개월 미만입니다."
    assert len(_cells_with(low, head)) == 5  # 머리 문장은 그 다섯 줄에만 선다
    assert _cells_with(high, "12개월 미만") == [] and _cells_with(over, "12개월 미만") == []

    # 결정 2 — 필수 안내가 선 자리 수만큼 기간 기준 줄이 선다(자리마다 바로 아래).
    margin = "충분한 여유를 확보하십시오"
    assert _kinds(low, margin) == ["Excel", "PPT", "Word", "Word", "Word", "화면"]
    assert _kinds(low, CONTRACT_PERIOD_BODY) == _kinds(low, margin)
    assert _cells_with(low, "분석 기간(2026년 5월 ") != []
    # 12개월 이상이면 하향을 권해도 안 선다.
    assert _cells_with(over, margin) != [] and _cells_with(over, CONTRACT_PERIOD_BODY) == []
    assert _cells_with(high, CONTRACT_PERIOD_BODY) == []


def test_S271_주말에도_가동하는_건물은_저부하_평일이_0일이다() -> None:
    """**주말·공휴일 부하가 평일의 90% 이상이면 저부하 평일을 세지 않는다** (S271 결정 3 —
    사람 결정 · 판단값 ``dr.weekend_operating_ratio``).

    산업용 벌은 판정 시간대 주말·공휴일 평균이 평일의 97% 다 — 앞서는 평일 82일 가운데
    67일을 「쉬는 날 수준」 으로 세고 12개월 환산 감축 가능량 10,242 kWh 를 냈다. 이제 0일
    이고 그 사실이 한 줄로 선다. 「쉬는 날 수준」 을 전제한 글은 그 벌에 안 선다.
    주말 부하가 평일의 59% 인 맞수 벌은 그대로 센다.

    감축량 칸은 0 이 아니라 「미산출」 이다 (S272 결정 1 — 아래 S272 못이 문다).
    """
    low, high = _render("small-ind-a1"), _render("small-ind-a2")

    # 금액 칸의 사유로도 서므로 Excel 에도 선다 (S272 결정 1).
    assert set(_kinds(low, WEEKEND_OPERATING_LINE)) == {"Excel", "PPT", "Word", "화면"}
    diagnosis = _diagnosis_sheet(low)
    assert diagnosis["DR 저부하 평일"] == "0일"
    # 기준선 값은 서고 문턱은 「미산출」 이다 — 「관측치 없음」 은 거짓이다.
    assert diagnosis["DR 저부하 판정 기준선 (주말·공휴일 평균 × 배수)"].endswith("문턱 미산출")
    for gone in (
        "쉬는 날 수준까지 내려옵니다",
        "쉬는 날 수준까지 내려오는 평일이 없어",
        "건물이 사실상 비어 있을 때의 수준입니다",
        "주말·공휴일 관측치 없음",
        "붉은 선 아래가 감축 가능일입니다",
        "문턱 아래로 내려온 평일",
    ):
        assert _cells_with(low, gone) == [], (gone, _cells_with(low, gone))

    # 맞수 — 주말 부하가 평일의 59% 라 그 한 줄이 안 서고 저부하 평일을 그대로 센다.
    assert _cells_with(high, "저부하 평일을 세지 않았습니다") == []
    assert _diagnosis_sheet(high)["DR 저부하 평일"] == "30일"
    assert _cells_with(high, "건물이 사실상 비어 있을 때의 수준입니다") != []
    assert _cells_with(high, "붉은 선 아래가 감축 가능일입니다") != []


def test_S271_산업용_실물의_참이_아닌_글_여덟과_운영시간() -> None:
    """**그 벌에서 참인 말만 선다** (S271 결정 4 · 6 · S207).

    계약전력 기준 건물(산업용 벌 · `large-a`)에는 요금적용전력이 기본요금을 정한다거나 피크를
    낮추면 요금이 준다고 읽히는 글이 안 서고, 12개월 미만 자료에는 「직전 12개월 최대」 가,
    여름 · 겨울이 다 들지 않은 자료에는 「여름에 높고 겨울에 낮다」 가, 저압에는 「야간 지상
    간주 (나목)」 이 안 선다. 월별 표의 「부분 월」 은 참 · 거짓 기호가 아니라 「예」 ·
    「아니오」 다.
    요금적용전력 기준 · 12개월 · 고압 맞수(`small-ind-a2`)에는 그 글들이 그대로 선다.
    """
    low, high, contract = _render("small-ind-a1"), _render("small-ind-a2"), _render("large-a")

    # 1 · 3 · 4 · 5 — 계약전력 기준이면 0줄, 요금적용전력 기준이면 선다.
    for word in (
        "이 시기에 요금적용전력이 결정됩니다",  # 1 PPT 5장
        "ESS 가 피크를 낮춥니다",  # 3 PPT 6장
        "태양광 피크 기여",  # 4 Excel 요약 · Word 요약 표
        "태양광 판정 모집단",  # 4 Excel 요약
        "태양광이 피크를 낮출 여지가 큽니다",  # 4 화면 툴팁
        "피크 저감은 기본요금 절감 가치가 거의 없습니다",  # 5 요금 안내
    ):
        assert _cells_with(low, word) == [], (word, _cells_with(low, word))
        assert _cells_with(contract, word) == [], (word, _cells_with(contract, word))
        assert _cells_with(high, word) != [], word
    assert ("PPT", "5") in _cells_with(low, "8월에 최대수요가 가장 높습니다.")
    assert ("PPT", "6") in _cells_with(low, "낮 시간에 발생해 태양광 발전 시간과 겹칩니다.")

    # 2 — 12개월 미만이면 「직전 12개월 최대」 가 0줄(각주 · 근거표). 12개월 벌에는 선다.
    assert _cells_with(low, "직전 12개월 최대") == [], _cells_with(low, "직전 12개월 최대")
    assert {"Excel", "PPT", "Word"} <= set(_kinds(high, "직전 12개월 최대"))
    assert {"Excel", "PPT", "Word"} <= set(_kinds(contract, "직전 12개월 최대"))

    # 6 — 자료에 겨울이 없다. 캡션은 뒷말 없이 서고 툴팁 뒷문단은 빠진다.
    assert _cells_with(low, "여름에 높고 겨울에 낮") == []
    assert ("PPT", "13") in _cells_with(low, "일별 발전량")
    assert set(_kinds(high, "여름에 높고 겨울에 낮")) == {"PPT", "화면"}

    # 7 — 저압은 야간 조각 없이 선다.
    # (2단계 역률 카드의 매뉴얼 가리킴 툴팁 「… 지상 간주 100%, …」 는 건물을 말하지 않아 남는다.)
    deemed = "지상 간주 100% (한전 기본공급약관 제43조 ② 2호 나목)"
    assert _cells_with(low, deemed) == []
    assert {"Excel", "Word"} <= set(_kinds(low, "주간(08~22시) 지상 100.0%"))
    assert {"Excel", "Word"} <= set(_kinds(high, deemed))

    # 8 — 월별 표의 「부분 월」 은 「예」 · 「아니오」 다 (Excel 월별 집계 · 화면 월별 명세).
    for rendered in (low, high):
        monthly = [row for row in rendered.rows if row[:2] == ("Excel", "월별 집계")]
        cells = {str(cell) for row in monthly[1:] for cell in row[2:]}
        assert not cells & {"True", "False"}, (rendered.key, cells & {"True", "False"})
        assert cells & {"예", "아니오"}, rendered.key
        screen = [text for slot, text in rendered.screen if text in ("예", "아니오")]
        assert screen, rendered.key
    assert [row[5] for row in low.rows if row[:2] == ("Excel", "월별 집계")][1:] == [
        "예",
        "아니오",
        "아니오",
        "아니오",
        "예",
    ]

    # 결정 4 — 24시간 가동. 운영시간 글자가 0 ~ 24시이고 「문 닫은 동안」 을 말하지 않는다.
    # (주말에도 가동하는 벌이라 「평일」 은 빠졌다 — S272 결정 2 · 아래 S272 못이 문다.)
    assert ("PPT", "4") in _cells_with(low, "0–24시) 밖 사용량 ÷ 전체")
    assert _cells_with(low, "0~24시 밖") != []
    assert _cells_with(low, "문 닫은 동안") == []
    assert _cells_with(high, "문 닫은 동안") != []
    assert _cells_with(low, "13~20시") != [] and _cells_with(low, "13~18시") == []

    # 결정 5 — 부록 B 요일 계량 규칙은 원문 글자다(Excel · Word). 열쇠는 안 선다.
    for rendered in (low, high):
        assert _cells_with(rendered, "all_to_light") == [] == _cells_with(rendered, "peak_to_mid")
        for text in (
            "최대수요전력 및 사용전력량 → 경부하 시간대로 계량",
            "최대부하 시간대의 사용전력량 → 중간부하 시간대로 계량",
        ):
            assert set(_kinds(rendered, text)) == {"Excel", "Word"}, (rendered.key, text)


def _dr_worksheet(rendered: Rendered) -> dict[str, str]:
    """Excel 부록 A 「경제성DR 계산 근거」 의 구분 → 값."""
    head = ("Excel", "부록 A 산출 근거", "경제성DR 계산 근거")
    return {str(row[3]): str(row[-1]) for row in rendered.rows if row[:3] == head}


def test_S272_주말에도_가동하는_건물의_DR_칸은_0_이_아니라_미산출이다() -> None:
    """**세지 않은 것은 「0」 이 아니라 「미산출」 이고 그 까닭이 사유로 선다** (S272 결정 1).

    산업용 벌은 주말에도 가동해 저부하 평일을 세지 않았다 (S271 결정 3). 그 벌의 등록 권장
    용량 · 감축 가능량 · 참여 시간 · 정산금 · 개선 방안 칸이 「0 kW」 · 「0 kWh」 · 「0시간」 ·
    「0 kWh 입찰」 로 서면 재어 본 0 으로 읽힌다. 사유는 진단이 낸 그 한 줄이다.

    갈래 밖은 그대로다 — 저부하 평일을 센 벌(`small-ind-a2`)과, 세어 보니 0일인 벌(`small-b`).
    """
    low, high, none = _render("small-ind-a1"), _render("small-ind-a2"), _render("small-b")

    diagnosis = _diagnosis_sheet(low)
    assert diagnosis["DR 등록 권장 용량 (저부하일 여력 하위값)"] == "미산출"
    assert diagnosis["DR 평균 기준 여력"] == "미산출"
    assert {k: v for k, v in diagnosis.items() if k.startswith("DR 12개월 환산 감축 가능량")} == {
        "DR 12개월 환산 감축 가능량 (참여 미산출, 하루 상한 8시간)": "미산출"
    }
    sheet = _dr_worksheet(low)
    assert sheet["등록 권장 용량"] == sheet["감축 가능량"] == sheet["12개월 환산"] == "미산출"
    assert sheet["저부하 평일"] == "0일"  # S271 결정 3 의 글자는 그대로다

    # 사유 — 금액 칸(Excel 수단별 결과 · 화면 3단계 표 · PPT 수단 장 ※)에 그 한 줄이 선다.
    reason = "미산출 — " + WEEKEND_OPERATING_LINE
    assert {"Excel", "PPT", "화면"} <= set(_kinds(low, reason)), _kinds(low, reason)
    screen = [text for _slot, text in low.screen]
    assert "미산출" in screen and "0 kWh 입찰" not in screen
    # 0 을 값으로 적는 글과, 단가를 넣으면 나온다고 읽히는 글은 그 벌에 안 선다.
    for gone in (
        "0 kWh 입찰",
        "등록 0 kW",
        "등록 권장 용량 0 kW",
        "감축 가능량 0 kWh",
        "0시간",
        "감축 가능량을 0 으로 두었습니다",
        "감축 가능량(kWh)만 참고하십시오",
        "정산 단가 미입력",
    ):
        assert _cells_with(low, gone) == [], (gone, _cells_with(low, gone))

    # 맞수 ① — 저부하 평일을 센 벌은 값이 그대로 선다.
    assert _diagnosis_sheet(high)["DR 등록 권장 용량 (저부하일 여력 하위값)"] == "28 kW"
    assert _dr_worksheet(high)["12개월 환산"] == "5,882 kWh"
    assert _cells_with(high, "5,882 kWh 입찰") != [] and _cells_with(high, reason) == []
    # 맞수 ② — 세어 보니 0일인 벌은 「0」 그대로다 (재어 본 0 이다).
    assert _diagnosis_sheet(none)["DR 등록 권장 용량 (저부하일 여력 하위값)"] == "0 kW"
    assert _dr_worksheet(none)["12개월 환산"] == "0 kWh"
    assert _cells_with(none, "0 kWh 입찰") != []
    assert _cells_with(none, "감축 가능량을 0 으로 두었습니다") != []
    assert _cells_with(none, "미산출 — 정산 단가 미입력") != []


def test_S272_주말에도_가동하는_건물은_운영시간을_모든_날에_적용한다() -> None:
    """**주말을 통째로 「운영시간 외」 로 세지 않는다** (S272 결정 2).

    산업용 벌은 운영 0 ~ 24시인데 「운영시간 외 부하 비중 29.2%」 가 섰다 — 주말 사용량 몫이다.
    주말에도 가동하는 건물(DR 진단의 그 갈래)은 운영시간을 모든 날에 적용한다 — 0 ~ 24시면
    밖이 없어 0.0% 다. 풀이 글자의 「평일」 과 「주말은 전부 밖입니다」 가 함께 빠진다.
    갈래 밖(`small-ind-a2`)은 평일만 적용하던 그대로다.
    """
    low, high = _render("small-ind-a1"), _render("small-ind-a2")

    assert _diagnosis_sheet(low)["운영시간 외 부하 비중"] == "0.0%"
    assert ("PPT", "4") in _cells_with(low, "운영시간(0–24시) 밖 사용량 ÷ 전체")
    assert _cells_with(low, "운영시간(0시–24시) 밖 사용량 ÷ 전체 사용량.") != []
    word = [row for row in low.rows if row[0] == "Word" and "운영시간 외 부하 비중" in row]
    assert [tuple(row[-2:]) for row in word] == [("0.0%", "0~24시 밖")], word
    for gone in ("주말은 전부 밖입니다", "평일 0~24시", "평일 0–24시", "평일 0시–24시", "29.2%"):
        assert _cells_with(low, gone) == [], (gone, _cells_with(low, gone))

    assert _diagnosis_sheet(high)["운영시간 외 부하 비중"] == "70.8%"
    assert ("PPT", "4") in _cells_with(high, "운영시간(평일 9–18시) 밖 사용량 ÷ 전체")
    assert _cells_with(high, "주말은 전부 밖입니다") != []
    assert _cells_with(high, "평일 9~18시 밖") != []
