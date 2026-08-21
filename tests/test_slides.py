"""PPT 보고서 시험 (36세션).

**여기서 지키는 것 여덟**

    ① 슬라이드 차례가 지시서의 목차 그대로다
    ② 수단 0개여도 만들어진다 — **수단별 장만 빠진다**
    ③ 색·크기가 **가이드 한 곳**에서 온다 (하드코딩 검사)
    ④ 슬라이드마다 **시각 요소가 하나 이상** 있다
    ⑤ 레이아웃이 반복되지 않는다 (형태별 개수)
    ⑥ 부록에서 뺀 셋이 PPT 에 없고 **Word 에는 있다**
    ⑦ 한글이 깨지지 않고 **OS 전용 글꼴 이름이 코드에 없다**
    ⑧ 파일명에 날짜·시각 접미사가 붙는다
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pytest
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Inches

from kwise.compare import ComparisonResult
from kwise.diagnose import Diagnosis
from kwise.io import UsageData
from kwise.measures import TariffSwitchResult
from kwise.report import figures
from kwise.report.appendix import APPENDIX_TITLES
from kwise.report.design import DesignGuideError, design_path, load_design_guide
from kwise.report.document import DocumentSections, build_document, measure_entries
from kwise.report.slides import (
    APPENDIX_ROW_LIMIT,
    LAYOUTS,
    SLIDE_TITLES,
    agenda_items,
    build_slides,
    export_slides,
    season_pairs,
    slide_specs,
    slides_bytes,
    slides_path,
)
from kwise.tariff import BillingResult

#: DrawingML 이름공간. 글꼴 지정이 이 안에 있다.
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src" / "kwise"

#: 지시서 2절의 괄호 — **하나가 슬라이드 한 장이다.**
EXPECTED_ORDER: tuple[str, ...] = (
    SLIDE_TITLES["cover"],
    SLIDE_TITLES["agenda"],
    SLIDE_TITLES["building"],
    SLIDE_TITLES["usage"],
    SLIDE_TITLES["load_pattern"],
    SLIDE_TITLES["peak"],
    SLIDE_TITLES["structure"],
    SLIDE_TITLES["measure_summary"],
    SLIDE_TITLES["combination"],
    SLIDE_TITLES["appendix"],
)


# ===================================================================== 도우미


@pytest.fixture(scope="module")
def entries(
    sample_switch: TariffSwitchResult,
    sample_bill: BillingResult,
    sample_usage: UsageData,
) -> tuple[object, ...]:
    from kwise.measures import evaluate_contract_adjustment

    contract = evaluate_contract_adjustment(sample_usage, sample_bill, contract_kw=5_800.0)
    return measure_entries(switch=sample_switch, contract=contract)


@pytest.fixture(scope="module")
def full_sections(
    sample_usage: UsageData,
    sample_bill: BillingResult,
    sample_diagnosis: Diagnosis,
    sample_comparison: ComparisonResult,
    sample_switch: TariffSwitchResult,
    entries: tuple[object, ...],
) -> DocumentSections:
    from kwise.report.worksheet import tariff_switch_worksheet

    return DocumentSections(
        usage=sample_usage,
        bill=sample_bill,
        diagnosis=sample_diagnosis,
        comparison=sample_comparison,
        measures=entries,  # type: ignore[arg-type]
        worksheets=(tariff_switch_worksheet(sample_switch),),
        building_name="본사 사옥",
        prepared_on=dt.date(2026, 8, 11),
    )


@pytest.fixture(scope="module")
def diagnosis_only(
    sample_usage: UsageData, sample_bill: BillingResult, sample_diagnosis: Diagnosis
) -> DocumentSections:
    """**수단을 하나도 켜지 않은 덱.** 진단만 보고 받아 가는 정상 경로다."""
    return DocumentSections(usage=sample_usage, bill=sample_bill, diagnosis=sample_diagnosis)


def _slide_text(slide: object) -> str:
    parts: list[str] = []
    for shape in slide.shapes:  # type: ignore[attr-defined]
        if shape.has_text_frame:
            parts.append(shape.text_frame.text)
        if shape.has_table:
            parts.extend(
                cell.text for row in shape.table.rows for cell in row.cells  # type: ignore[union-attr]
            )
    return "\n".join(parts)


def _deck_text(deck: object) -> str:
    return "\n".join(_slide_text(slide) for slide in deck.slides)  # type: ignore[attr-defined]


def _visual_shapes(slide: object) -> list[object]:
    """**글자 상자가 아닌 것.** 그림·표·선이 시각 요소다 (36세션 5절)."""
    visual = {MSO_SHAPE_TYPE.PICTURE, MSO_SHAPE_TYPE.TABLE, MSO_SHAPE_TYPE.LINE}
    return [shape for shape in slide.shapes if shape.shape_type in visual]  # type: ignore[attr-defined]


# ===================================================================== ① 차례


def test_슬라이드_구성이_목차대로다(full_sections: DocumentSections) -> None:
    """**괄호 하나가 슬라이드 한 장이다** (36세션 2절)."""
    titles = [spec.title for spec in slide_specs(full_sections)]
    measures = [entry.title for entry in full_sections.measures]
    assert measures, "이 시험은 수단이 켜진 덱을 본다."
    expected = [
        *EXPECTED_ORDER[:8],
        *measures,  # 검토한 수단별 1장씩 — **켠 것만, 차례대로**
        *EXPECTED_ORDER[8:],
    ]
    assert titles == expected


def test_실물_덱이_차례와_같다(full_sections: DocumentSections) -> None:
    """자리표와 실물이 갈라지지 않는다 — 덱은 :func:`slide_specs` 대로 만들어진다."""
    deck = build_slides(full_sections)
    specs = slide_specs(full_sections)
    assert len(deck.slides) == len(specs)
    for slide, spec in zip(deck.slides, specs, strict=True):
        assert spec.title in _slide_text(slide), f"「{spec.title}」 장이 제목을 잃었습니다."


def test_목차가_수단을_한_줄로_묶는다(full_sections: DocumentSections) -> None:
    """슬라이드마다 한 줄을 적으면 수단이 일곱일 때 목차가 열두 줄이 된다."""
    items = agenda_items(full_sections)
    assert len(items) == len(EXPECTED_ORDER) - 2 + 1  # 표지·목차를 빼고 수단 묶음 한 줄
    assert any("검토한 수단별" in line for line in items)
    for entry in full_sections.measures:
        assert entry.title in " ".join(items)


# ===================================================================== ② 수단 0개


def test_수단이_없어도_만들어진다(diagnosis_only: DocumentSections) -> None:
    """**수단별 장만 빠진다.** 나머지는 그대로다 (Word 와 같은 규약)."""
    specs = slide_specs(diagnosis_only)
    assert [spec.title for spec in specs] == list(EXPECTED_ORDER)
    assert all(spec.measure is None for spec in specs)

    deck = build_slides(diagnosis_only)
    assert len(deck.slides) == len(EXPECTED_ORDER)
    payload, _name = slides_bytes(diagnosis_only)
    assert payload[:2] == b"PK", "pptx 가 아닙니다."


def test_수단_0개면_개선안별_요약이_비어_있음을_적는다(
    diagnosis_only: DocumentSections,
) -> None:
    """**빈 표를 남기지 않는다.** 빈칸은 「효과가 없다」 로 읽힌다."""
    deck = build_slides(diagnosis_only)
    index = list(EXPECTED_ORDER).index(SLIDE_TITLES["measure_summary"])
    assert "검토한 수단이 없습니다" in _slide_text(list(deck.slides)[index])


# ===================================================================== ③ 값이 오는 자리


def test_가이드가_색과_크기를_쥔다() -> None:
    """**데이터 파일 한 곳에서 온다.** 코드에 기본값을 두지 않는다."""
    guide = load_design_guide()
    assert design_path().is_file()
    assert guide.slide.width_in == pytest.approx(13.333)
    assert guide.slide.height_in == pytest.approx(7.5)
    assert guide.slide.margin_in == pytest.approx(0.5)
    assert 0.3 <= guide.slide.block_gap_in <= 0.4
    assert 0.25 <= guide.slide.title_gap_in <= 0.3
    assert guide.slide.text_slack == pytest.approx(0.1), "텍스트 상자 여유 10% 규약."
    scale = guide.type_scale
    assert 44 <= scale.cover <= 54
    assert 36 <= scale.section <= 40
    assert 28 <= scale.slide_title <= 32
    assert 20 <= scale.card_title <= 22
    assert 14 <= scale.body <= 16
    assert 11 <= scale.caption <= 12


def test_가이드가_없으면_멈춘다(tmp_path: Path) -> None:
    """**조용히 기본값으로 넘어가지 않는다.** 고쳐도 반영되지 않는 사고가 난다."""
    load_design_guide.cache_clear()
    try:
        with pytest.raises(DesignGuideError, match="디자인 가이드가 없습니다"):
            load_design_guide(str(tmp_path / "없는파일.json"))
    finally:
        load_design_guide.cache_clear()


#: 색을 코드에 박았는지 보는 자리. **슬라이드와 png 를 그리는 두 모듈이다.**
_COLOR_SCANNED = ("report/slides.py", "report/figures.py")
_HEX_COLOR = re.compile(r"[\"']#[0-9a-fA-F]{3,8}[\"']")


def test_색을_코드에_박지_않는다() -> None:
    """가이드가 바뀌면 **고칠 자리가 한 곳**이어야 한다 (36세션 3절)."""
    offenders = {
        name: _HEX_COLOR.findall((SRC_ROOT / name).read_text(encoding="utf-8"))
        for name in _COLOR_SCANNED
    }
    found = {name: hits for name, hits in offenders.items() if hits}
    assert not found, (
        f"색을 코드에 박았습니다: {found}. data\\ppt_design.json 에 넣고 "
        "kwise.report.design.load_design_guide() 로 읽으십시오."
    )


def test_png_팔레트가_가이드에서_온다() -> None:
    """**화면은 그대로 두고 png 만 맞춘다** (36세션 4절)."""
    guide = load_design_guide()
    assert figures.chart_palette() is guide.chart
    assert guide.chart.canvas == "#FFFFFF", "png 는 흰 캔버스가 확정이다."
    assert guide.chart.series[0] == guide.colors.blue
    assert guide.chart.increase == guide.colors.coral
    # 계시 색은 **단가가 높을수록 짙다** — 화면과 같은 규칙이다.
    assert set(guide.chart.band) == {"경부하", "중간부하", "최대부하"}


def test_png_은_배경을_투명으로_굽지_않는다(sample_diagnosis: Diagnosis) -> None:
    """**png 글자색 규약은 「흰 바탕이 확정」 위에 선다** (36세션 4절).

    화면(altair)은 다크 모드를 타서 글자에 중립색을 박지 않지만, png 는 배경이
    하나뿐이라 그 규약이 성립하지 않는다. 대신 그 배경을 여기서 확정한다.
    """
    source = (SRC_ROOT / "report" / "figures.py").read_text(encoding="utf-8")
    assert "transparent=True" not in source, "png 를 투명 배경으로 구우면 글자가 사라진다."
    assert "facecolor=canvas" in source
    png = figures.hourly_profile_png(sample_diagnosis.peak)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


# ===================================================================== ④⑤ 형태


def test_슬라이드마다_시각_요소가_하나_이상이다(full_sections: DocumentSections) -> None:
    """**표형 슬라이드도 표 자체가 시각 요소다** (36세션 5절)."""
    deck = build_slides(full_sections)
    specs = slide_specs(full_sections)
    for slide, spec in zip(deck.slides, specs, strict=True):
        assert _visual_shapes(slide), f"「{spec.title}」 장에 시각 요소가 없습니다."


def test_레이아웃이_반복되지_않는다(full_sections: DocumentSections) -> None:
    """**같은 형태를 되풀이하지 않는다** (36세션 3-4). 가이드가 금지한다."""
    specs = slide_specs(full_sections)
    used = [spec.layout for spec in specs]
    assert set(used) <= set(LAYOUTS), f"모르는 레이아웃이 있습니다: {set(used) - set(LAYOUTS)}"

    # 수단별 장은 가이드가 「차트 + 지표」 로 못박은 자리라 형태가 같은 것이 옳다.
    # **나머지 장**을 본다 — 여기가 되풀이되면 덱이 한 형태로 보인다.
    frame = [spec.layout for spec in specs if spec.measure is None]
    counts = {kind: frame.count(kind) for kind in set(frame)}
    assert len(counts) >= 5, f"형태가 {len(counts)}가지뿐입니다: {counts}"
    assert max(counts.values()) <= 3, f"한 형태가 너무 잦습니다: {counts}"


def test_카드로만_만들지_않는다(full_sections: DocumentSections) -> None:
    """**표·리스트·여백만인 슬라이드를 섞는다** (36세션 3-4).

    가이드가 금지하는 것은 카드가 아니라 **카드만인 덱**이다. 그림 없이 표와
    선만으로 서는 장이 있어야 한다.
    """
    deck = build_slides(full_sections)
    picture_free = [
        slide
        for slide in deck.slides
        if not any(shape.shape_type == MSO_SHAPE_TYPE.PICTURE for shape in slide.shapes)
    ]
    assert len(picture_free) >= 3, "그림 없이 서는 슬라이드가 셋에 못 미칩니다."


def test_슬라이드당_큰_타이틀은_하나다(full_sections: DocumentSections) -> None:
    """**크기 위계** (36세션 3-3). 제목 크기의 글자가 한 장에 둘 있으면 안 된다."""
    guide = load_design_guide()
    threshold = guide.type_scale.card_title
    deck = build_slides(full_sections)
    for slide in deck.slides:
        big = [
            run
            for shape in slide.shapes
            if shape.has_text_frame
            for paragraph in shape.text_frame.paragraphs
            for run in paragraph.runs
            if run.font.size is not None and run.font.size.pt > threshold
        ]
        assert len(big) <= 1, f"큰 타이틀이 {len(big)}개입니다: {[run.text for run in big]}"


def test_본문은_좌측_정렬이다(full_sections: DocumentSections) -> None:
    """**본문은 항상 좌측 정렬이다** (36세션 3-3)."""
    from pptx.enum.text import PP_ALIGN

    deck = build_slides(full_sections)
    for slide in deck.slides:
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            for paragraph in shape.text_frame.paragraphs:
                assert paragraph.alignment in (None, PP_ALIGN.LEFT)


def test_슬라이드_밖으로_나가는_것이_없다(full_sections: DocumentSections) -> None:
    """**텍스트 상자에 여유를 두었어도 자리를 벗어나면 안 된다** (36세션 3-1)."""
    guide = load_design_guide()
    width = Inches(guide.slide.width_in)
    height = Inches(guide.slide.height_in)
    deck = build_slides(full_sections)
    for index, slide in enumerate(deck.slides, start=1):
        for shape in slide.shapes:
            if shape.left is None or shape.top is None:
                continue
            right = shape.left + (shape.width or 0)
            bottom = shape.top + (shape.height or 0)
            assert shape.left >= 0 and shape.top >= 0, f"{index}장이 왼쪽·위로 넘칩니다."
            assert right <= width + Inches(0.01), f"{index}장이 오른쪽으로 넘칩니다."
            assert bottom <= height + Inches(0.01), f"{index}장이 아래로 넘칩니다."


def test_표지만_다크다(full_sections: DocumentSections) -> None:
    """**샌드위치 구조** (36세션 3-2). 딥그린은 전체 배경 밴드로만 쓴다."""
    guide = load_design_guide()
    deck = build_slides(full_sections)
    fills = [str(slide.background.fill.fore_color.rgb) for slide in deck.slides]
    assert fills[0] == guide.colors.cover.lstrip("#").upper()
    assert set(fills[1:]) == {guide.colors.white.lstrip("#").upper()}


def test_전체_배경_밴드에_포인트색을_쓸_수_없다() -> None:
    """**딥그린·네이비만 전체 배경이다** (36세션 3-2). 코랄은 작은 포인트뿐이다."""
    from dataclasses import replace

    from kwise.report.design import DesignGuideError

    colors = load_design_guide().colors
    assert colors.cover in (colors.deep_green, colors.dark_primary)
    with pytest.raises(DesignGuideError, match="전체 배경 밴드"):
        _ = replace(colors, cover_background="coral").cover


# ===================================================================== ⑥ 부록


#: **PPT 부록에서 뺀 셋** (36세션 6절). Word 에는 그대로 있다.
DROPPED_FROM_DECK: tuple[str, ...] = (
    "ESS 조달 사례",
    APPENDIX_TITLES["B"],
    APPENDIX_TITLES["C"],
)


@pytest.fixture(scope="module")
def loaded_sections(full_sections: DocumentSections) -> DocumentSections:
    """**부록 재료를 다 넣은 덱.** 뺐다는 것을 보이려면 넣어 놓고 봐야 한다."""
    from dataclasses import replace

    from kwise.measures import load_ess_cost_model
    from kwise.tariff import load_tariff

    return replace(
        full_sections,
        ess_cases=load_ess_cost_model().case_table(),
        tariff_table=load_tariff(),
    )


def test_부록에서_뺀_셋이_PPT_에_없다(loaded_sections: DocumentSections) -> None:
    """슬라이드에서 읽히지 않는 분량이다. **지운 것이 아니라 매체를 가린 것이다.**"""
    sections = loaded_sections
    text = _deck_text(build_slides(sections))
    for title in DROPPED_FROM_DECK:
        assert title not in text, f"「{title}」 이 PPT 에 남아 있습니다."


def test_뺀_셋이_Word_에는_남아_있다(loaded_sections: DocumentSections) -> None:
    """**PPT 에서만 뺐다.** Word 는 그대로다 (36세션 6절)."""
    document = build_document(loaded_sections)
    parts = [item.text for item in document.paragraphs]
    for table in document.tables:
        parts.extend(cell.text for row in table.rows for cell in row.cells)
    text = "\n".join(parts)
    for title in DROPPED_FROM_DECK:
        assert title in text, f"「{title}」 이 Word 에서 사라졌습니다."


def test_부록이_넘치면_뺀_줄_수를_적는다(full_sections: DocumentSections) -> None:
    """**조용히 자르지 않는다.** 자른 채 두면 「이게 전부」 로 읽힌다."""
    from dataclasses import replace

    from kwise.report.worksheet import Worksheet

    sheet = full_sections.worksheets[0]
    many = Worksheet(key=sheet.key, title=sheet.title, rows=sheet.rows * 6)
    deck = build_slides(replace(full_sections, worksheets=(many,)))
    text = _slide_text(list(deck.slides)[-1])
    assert "줄은 자리가 모자라 뺐습니다" in text
    assert "Excel·Word 부록 A" in text
    assert len(many.frame()) > APPENDIX_ROW_LIMIT


# ===================================================================== ⑦ 글꼴·한글


def test_OS_전용_글꼴_이름이_슬라이드_코드에_없다() -> None:
    """**설정으로 뺐다** (36세션 3-1). 배포 시험이 ``src\\`` 를 훑는다."""
    from tests.test_deployment import FONT_NAME_ALLOWED

    assert "src/kwise/report/slides.py" not in FONT_NAME_ALLOWED
    assert "src/kwise/report/design.py" not in FONT_NAME_ALLOWED
    source = (SRC_ROOT / "report" / "slides.py").read_text(encoding="utf-8")
    assert "guide.typography.primary" in source, "글꼴 이름은 가이드에서 와야 합니다."


def test_글꼴_후보가_없으면_물러선다() -> None:
    """**없을 때 물러설 곳을 정해 둔다** (36세션 3-1)."""
    from dataclasses import replace

    typography = load_design_guide().typography
    assert typography.primary == typography.candidates[0]
    assert replace(typography, candidates=()).primary == typography.fallback


def test_한글이_깨지지_않는다(full_sections: DocumentSections, tmp_path: Path) -> None:
    """**동아시아 글꼴을 따로 지정해야** PowerPoint 가 한글에 쓴다."""
    path = export_slides(full_sections, output_dir=tmp_path)
    deck = Presentation(str(path))
    text = _deck_text(deck)
    assert "전력 비용 진단 보고서" in text
    assert "건물현황 및 계약정보" in text
    assert "�" not in text and "?" * 3 not in text

    # **동아시아 글꼴(``a:ea``)은 ``a:rPr`` 안에 있다.** ``a:latin`` 만 있으면
    # PowerPoint 가 한글에 그 글꼴을 쓰지 않는다 (Word 의 ``w:eastAsia`` 와 같다).
    name = load_design_guide().typography.primary
    eastasian = [
        run._r.find(f"{_A}rPr/{_A}ea")
        for slide in deck.slides
        for shape in slide.shapes
        if shape.has_text_frame
        for paragraph in shape.text_frame.paragraphs
        for run in paragraph.runs
    ]
    assert eastasian and all(item is not None for item in eastasian)
    assert all(item.get("typeface") == name for item in eastasian if item is not None)


# ===================================================================== ⑧ 파일명


def test_파일명에_날짜_시각이_붙는다() -> None:
    """PowerPoint 가 파일을 열고 있으면 덮어쓰기가 실패한다 (Excel·Word 와 같다)."""
    path = slides_path(Path("out"), now=dt.datetime(2026, 8, 11, 9, 5))
    assert path.name == "kwise_report_20260811_0905.pptx"


def test_바이트를_돌려줄_때도_같은_이름이다(full_sections: DocumentSections) -> None:
    _payload, name = slides_bytes(full_sections, now=dt.datetime(2026, 8, 11, 9, 5))
    assert name == "kwise_report_20260811_0905.pptx"


def test_저장_경로에_폴더가_없으면_만든다(
    full_sections: DocumentSections, tmp_path: Path
) -> None:
    target = tmp_path / "없던" / "폴더"
    path = export_slides(full_sections, output_dir=target)
    assert path.is_file()


# ===================================================================== 도넛 넷


def test_계시_도넛이_2x2_로_한_장에_들어간다(sample_diagnosis: Diagnosis) -> None:
    """**넷의 조각 순서·색·시작각을 맞춘다** (36세션 4절).

    도넛에는 y 축이 없으므로 축 대신 이 셋이 「같은 잣대로 그렸다」 를 진다.
    """
    structure = sample_diagnosis.structure
    assert structure is not None
    pairs = season_pairs(structure)
    assert [label for label, _key in pairs] == ["전체", "봄·가을", "여름", "겨울"]
    assert figures.DONUT_GRID == (2, 2)
    png = figures.band_donut_grid_png(structure, pairs)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"

    source = (SRC_ROOT / "report" / "figures.py").read_text(encoding="utf-8")
    assert "startangle=90" in source and "counterclock=False" in source


def test_없는_계절은_도넛을_그리지_않는다(sample_diagnosis: Diagnosis) -> None:
    """**빈 원은 「그 계절에 안 썼다」 로 읽힌다** (화면과 같은 규칙)."""
    from dataclasses import replace

    structure = sample_diagnosis.structure
    assert structure is not None
    half = structure.band_season_kwh.drop(index="summer")
    pairs = season_pairs(replace(structure, band_season_kwh=half))
    assert [label for label, _key in pairs] == ["전체", "봄·가을", "겨울"]


# ===================================================================== png 날짜 축


def test_새_png_도_한국식_날짜_축이다(sample_usage: UsageData) -> None:
    """**화면만 고치면 어긋난다** (33세션 1절 · 13세션). matplotlib 은 기본이 영어다."""
    source = (SRC_ROOT / "report" / "figures.py").read_text(encoding="utf-8")
    body = source.split("def daily_usage_png")[1].split("\ndef ")[0]
    assert "date_axis(axes)" in body, "연간 사용량 png 가 날짜 축 규약을 타지 않습니다."
    png = figures.daily_usage_png(sample_usage)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_월별_요금_png_의_달_축이_한국식이다(sample_diagnosis: Diagnosis) -> None:
    """**해는 바뀔 때만 적는다** (33세션 1절)."""
    source = (SRC_ROOT / "report" / "figures.py").read_text(encoding="utf-8")
    body = source.split("def monthly_charge_png")[1].split("\ndef ")[0]
    assert "month_labels(months)" in body
    structure = sample_diagnosis.structure
    assert structure is not None
    assert figures.monthly_charge_png(structure)[:8] == b"\x89PNG\r\n\x1a\n"
