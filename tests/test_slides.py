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
from kwise.measures import MEASURE_CATALOG, TariffSwitchResult
from kwise.report import figures
from kwise.report.appendix import APPENDIX_TITLES
from kwise.report.design import DesignGuideError, design_path, load_design_guide
from kwise.report.document import (
    DocumentSections,
    MeasureEntry,
    build_document,
    measure_entries,
)
from kwise.report.slides import (
    ANNUAL_BASIS_NOTE,
    APPENDIX_ROW_LIMIT,
    LAYOUTS,
    MEASURE_AGENDA_ITEM,
    NEXT_STEPS,
    NEXT_STEPS_HEADLINE,
    SLIDE_TITLES,
    _cautions,
    agenda_items,
    appendix_pages,
    build_slides,
    export_slides,
    measure_slide_title,
    plain_text,
    season_pairs,
    slide_specs,
    slides_bytes,
    slides_path,
    split_reason,
)
from kwise.tariff import BillingResult, TariffTable

#: DrawingML 이름공간. 글꼴 지정이 이 안에 있다.
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src" / "kwise"

#: 지시서 2절의 괄호 — **하나가 슬라이드 한 장이다.**
EXPECTED_ORDER: tuple[str, ...] = (
    SLIDE_TITLES["cover"],
    SLIDE_TITLES["agenda"],
    SLIDE_TITLES["building"],
    SLIDE_TITLES["usage_pattern"],
    SLIDE_TITLES["peak_summary"],
    SLIDE_TITLES["peak_detail"],
    SLIDE_TITLES["structure"],
    SLIDE_TITLES["measure_summary"],
    SLIDE_TITLES["combination"],
    SLIDE_TITLES["appendix"],
    SLIDE_TITLES["closing"],
)

#: 부록 앞의 뼈대 장 수 (표지·목차·진단 다섯·요약·조합).
FRAME_BEFORE_APPENDIX = 9


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
                cell.text
                for row in shape.table.rows
                for cell in row.cells  # type: ignore[union-attr]
            )
    return "\n".join(parts)


def _deck_text(deck: object) -> str:
    return "\n".join(_slide_text(slide) for slide in deck.slides)  # type: ignore[attr-defined]


def _all_measures(sections: DocumentSections) -> tuple[MeasureEntry, ...]:
    """수단을 **모두 켠** 항목 목록. 41세션부터 여섯이다.

    실제로 여섯을 계산하면 시험이 몇 분 늘어난다. 여기서 보는 것은 **장 수가
    수단 수를 따라가는가** 하나이므로 항목만 채워 만든다 — 카탈로그 차례를
    그대로 쓰므로 실제 경로와 같은 순서다.
    """
    known = {entry.kind.key: entry for entry in sections.measures}
    return tuple(
        known.get(
            kind.key,
            MeasureEntry(
                kind=kind,
                conclusion=f"{kind.title} 검토 결과입니다.",
                saving="0원",
                investment="0원",
                payback="즉시",
                certainty="높음",
            ),
        )
        for kind in MEASURE_CATALOG
    )


def _slide_by_key(deck: object, sections: DocumentSections, key: str) -> object:
    """자리표의 열쇠로 실물 슬라이드를 찾는다.

    **차례에서 세지 않는다.** 「맨 뒤」 같은 자리 표현은 장이 하나 붙는 순간
    조용히 다른 슬라이드를 가리킨다 — 37세션에 마무리를 붙이며 겪었다.
    """
    index = [spec.key for spec in slide_specs(sections)].index(key)
    return list(deck.slides)[index]  # type: ignore[attr-defined]


def _visual_shapes(slide: object) -> list[object]:
    """**글자 상자가 아닌 것.** 그림·표·선이 시각 요소다 (36세션 5절)."""
    visual = {MSO_SHAPE_TYPE.PICTURE, MSO_SHAPE_TYPE.TABLE, MSO_SHAPE_TYPE.LINE}
    return [shape for shape in slide.shapes if shape.shape_type in visual]  # type: ignore[attr-defined]


# ===================================================================== ① 차례


def test_슬라이드_구성이_목차대로다(full_sections: DocumentSections) -> None:
    """**괄호 하나가 슬라이드 한 장이다** (36세션 2절)."""
    titles = [spec.title for spec in slide_specs(full_sections)]
    measures = [measure_slide_title(entry) for entry in full_sections.measures]
    assert measures, "이 시험은 수단이 켜진 덱을 본다."
    appendix = [page.title for page in appendix_pages(full_sections)]
    expected = [
        *EXPECTED_ORDER[:8],
        *measures,  # 검토한 수단별 1장씩 — **켠 것만, 차례대로**
        SLIDE_TITLES["combination"],
        *appendix,  # 부록은 수단마다 한 장 이상 (39세션 5절)
        SLIDE_TITLES["closing"],
    ]
    assert expected[-1] == SLIDE_TITLES["closing"], "마무리는 맨 뒤다."
    assert titles == expected


def test_실물_덱이_차례와_같다(full_sections: DocumentSections) -> None:
    """자리표와 실물이 갈라지지 않는다 — 덱은 :func:`slide_specs` 대로 만들어진다."""
    deck = build_slides(full_sections)
    specs = slide_specs(full_sections)
    assert len(deck.slides) == len(specs)
    for slide, spec in zip(deck.slides, specs, strict=True):
        assert spec.title in _slide_text(slide), f"「{spec.title}」 장이 제목을 잃었습니다."


def test_목차가_수단을_한_줄로_묶는다(full_sections: DocumentSections) -> None:
    """슬라이드마다 한 줄을 적으면 수단이 일곱일 때 목차가 열두 줄이 된다.

    **수단 이름을 나열하지도 않는다** (38세션 1-1). 어느 수단을 보았는지는
    바로 다음 장인 「개선안별 요약」 표가 낸다.
    """
    items = agenda_items(full_sections)
    assert len(items) == len(EXPECTED_ORDER) - 2 + 1  # 표지·목차를 빼고 수단 묶음 한 줄
    assert MEASURE_AGENDA_ITEM in items
    for entry in full_sections.measures:
        assert measure_slide_title(entry) not in " ".join(items), (
            "목차에 수단을 나열하면 줄이 넘쳐 다음 항목을 덮는다."
        )


def test_목차_항목이_한_줄을_넘지_않는다(full_sections: DocumentSections) -> None:
    """**07번이 08번을 덮고 있었다** (38세션 1-1).

    목차는 번호 칸(0.8in)을 뺀 폭에 한 줄로 앉는다. 두 줄이 되면 아래 항목의
    자리를 침범하는데, 줄 간격이 항목 수로 정해져 있어 **넘친 만큼 겹친다.**

    수단이 일곱일 때가 가장 긴 경우다 — 그때도 넘치지 않아야 한다.
    """
    from dataclasses import replace

    from kwise.report.design import load_design_guide
    from kwise.report.slides import _text_width_in

    guide = load_design_guide()
    span = guide.slide.content_width_in - 0.8
    every = replace(full_sections, measures=_all_measures(full_sections))
    for sections in (full_sections, every, diagnosis_only_of(full_sections)):
        for line in agenda_items(sections):
            width = _text_width_in(line, guide.type_scale.card_title)
            assert width <= span, (
                f"목차 「{line}」 가 한 줄을 넘습니다 ({width:.2f}in > {span:.2f}in)"
            )


def diagnosis_only_of(sections: DocumentSections) -> DocumentSections:
    """수단을 뺀 같은 자료. 목차가 가장 짧은 경우다."""
    from dataclasses import replace

    return replace(sections, measures=())


# ===================================================================== 38세션 · 편집 결함


#: PPT 가 해석하지 못하는 마크다운 표식 (38세션 1-2).
#: 화면(Streamlit)은 이것을 굵게 그리지만 PowerPoint 는 글자 그대로 찍는다.
MARKDOWN_MARKS: tuple[str, ...] = ("**", "__", "`")


def test_마크다운_기호가_슬라이드에_남지_않는다(loaded_sections: DocumentSections) -> None:
    """**PPT 는 마크다운을 해석하지 않는다** (38세션 1-2).

    「자격요건은 판정하지 않았습니다」 를 감싼 별표가 슬라이드에 그대로
    찍히고 있었다. 문구는 화면·Word 로도 가므로 낳는 자리에서 고칠 수 없어,
    **적는 순간 벗긴다** — :func:`~kwise.report.slides.plain_text` 한 곳이다.
    """
    text = _deck_text(build_slides(loaded_sections))
    for mark in MARKDOWN_MARKS:
        assert mark not in text, f"슬라이드에 마크다운 표식 「{mark}」 이 남아 있습니다."


def test_마크다운을_벗기는_자리가_하나다() -> None:
    """**자리마다 벗기면 새 문구를 붙일 때 빠뜨린다.**

    글자를 쓰는 곳은 :func:`_text` 와 :func:`_table` 둘뿐이고, 둘 다
    ``plain_text`` 를 지나야 한다.
    """
    assert plain_text("**굵게** 와 `코드` 와 __밑줄__") == "굵게 와 코드 와 밑줄"
    source = (SRC_ROOT / "report" / "slides.py").read_text(encoding="utf-8")
    writes = [line for line in source.splitlines() if "run.text =" in line]
    assert writes, "글자를 쓰는 자리를 찾지 못했습니다."
    for line in writes:
        assert "plain_text(" in line, f"마크다운을 벗기지 않는 자리가 있습니다: {line.strip()}"


def test_절_번호가_슬라이드에_없다(loaded_sections: DocumentSections) -> None:
    """**7.1~7.7 을 뺀다** (38세션 1-3).

    「7.」 이 무엇인지 덱 어디에도 적혀 있지 않다 — 처음 받아 보는 사람에게는
    없는 7장을 찾게 만드는 표시다. 화면은 27세션에 이미 뗐다.
    """
    sections = loaded_sections
    text = _deck_text(build_slides(sections))
    for kind in MEASURE_CATALOG:
        assert kind.number not in text, f"절 번호 「{kind.number}」 이 슬라이드에 남아 있습니다."
    # **이름은 남는다.** 번호만 뗀 것이지 수단을 감춘 것이 아니다.
    for entry in sections.measures:
        assert entry.kind.label in text


def test_Word_에는_절_번호가_남아_있다(loaded_sections: DocumentSections) -> None:
    """**PPT 에서만 뗐다.** Word·Excel 은 요구사항서와 맞물린 번호를 쓴다."""
    document = build_document(loaded_sections)
    text = "\n".join(item.text for item in document.paragraphs)
    for entry in loaded_sections.measures:
        assert entry.kind.number in text, f"Word 에서 「{entry.kind.number}」 이 사라졌습니다."


# ===================================================================== 38세션 2절 · 4~6장


def test_4장이_전력사용현황과_부하패턴을_합친다(full_sections: DocumentSections) -> None:
    """**화면 1단계의 차례를 그대로 따른다** (38세션 2-1).

    화면은 머릿수 지표 자리에 그림이 없고 첫 그림이 「부하 패턴」 절에 나온다.
    36세션의 덱은 그 구조와 어긋나 두 장이 같은 이야기를 지표만 바꿔 되풀이했다.
    """
    specs = slide_specs(full_sections)
    frame = [spec for spec in specs if spec.measure is None]
    assert [spec.key for spec in frame[2:6]] == [
        "building",
        "usage_pattern",
        "peak_summary",
        "peak_detail",
    ]
    slide = _slide_by_key(build_slides(full_sections), full_sections, "usage_pattern")
    text = _slide_text(slide)
    assert SLIDE_TITLES["usage_pattern"] == "전력사용현황 및 부하패턴"
    for label in ("부하율", "기저부하 비율", "운영시간 외 부하 비중"):
        assert label in text, f"「{label}」 지표가 4장에 없습니다."
    assert "사용량" in text
    # **겹치는 것은 뺐다** — 최대수요·요금적용전력은 다음 장이 낸다.
    assert "최대수요" not in text and "요금적용전력" not in text
    assert "분석 기간" not in text, "3장 표에 이미 있는 값이다."


def test_4장_그림이_기온을_겹쳐_그린다(full_sections: DocumentSections) -> None:
    """**냉난방이 부하의 얼마를 차지하는지**가 태양광·ESS 판단을 가른다.

    기온이 없으면 사용량만 그리고 캡션이 그 사실을 적는다 — 화면과 같은 규칙이다.
    """
    from dataclasses import replace

    import pandas as pd

    slide = _slide_by_key(build_slides(full_sections), full_sections, "usage_pattern")
    assert "일평균 기온" not in _slide_text(slide), "기온이 없으면 사용량만 그린다."

    index = pd.date_range(full_sections.usage.meta.start, periods=48, freq="h")
    warm = replace(full_sections, temperature=pd.Series(range(48), index=index, dtype=float))
    assert "일평균 기온" in _slide_text(_slide_by_key(build_slides(warm), warm, "usage_pattern"))


def test_5장이_정오_비중을_낸다(full_sections: DocumentSections) -> None:
    """**정오 비중이 태양광 판정의 근거다** (38세션 2-2).

    36세션의 덱에는 이 숫자가 없어 상위 구간 그래프만 덩그러니 서 있었다.
    """
    slide = _slide_by_key(build_slides(full_sections), full_sections, "peak_summary")
    text = _slide_text(slide)
    assert "상위 구간 정오 비중" in text
    assert "상위 구간 주말 비중" in text
    assert "요금적용전력" in text


def test_5장_지표가_화면과_같은_갈림이다(full_sections: DocumentSections) -> None:
    """관측 최대와 요금적용 대상 최대가 같으면 **한 칸으로 접는다** (13세션).

    갈릴 때는 넷이 된다 — 화면은 칸이 셋이라 정오 비중을 밀어냈지만 슬라이드는
    밀어낼 것이 없다.
    """
    from dataclasses import replace

    from kwise.report.slides import _peak_stats

    diagnosis = full_sections.diagnosis
    assert diagnosis is not None
    joined = [label for label, _value in _peak_stats(full_sections)]
    assert joined[0] == "최대수요 = 요금적용전력"
    assert len(joined) == 3

    night = replace(diagnosis, peak=replace(diagnosis.peak, billing_demand_kw=1_000.0))
    split = [label for label, _value in _peak_stats(replace(full_sections, diagnosis=night))]
    assert split[:2] == ["관측 최대수요", "요금적용전력"]
    assert "상위 구간 정오 비중" in split
    assert len(split) == 4


def test_6장이_그림_둘을_좌우로_놓는다(full_sections: DocumentSections) -> None:
    """지금 구성 그대로다 — 시간대별 평균 부하와 상위 구간 발생 시각."""
    sections = full_sections
    spec = next(item for item in slide_specs(sections) if item.key == "peak_detail")
    assert spec.layout == "chart_pair"
    slide = _slide_by_key(build_slides(sections), sections, "peak_detail")
    pictures = [shape for shape in slide.shapes if shape.shape_type == MSO_SHAPE_TYPE.PICTURE]
    assert len(pictures) == 2
    text = _slide_text(slide)
    assert "평균 부하" in text and "발생한 시각" in text


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
    slide = _slide_by_key(build_slides(diagnosis_only), diagnosis_only, "measure_summary")
    assert "검토한 수단이 없습니다" in _slide_text(slide)


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
    # **부록은 세지 않는다** (39세션 5절). 수단마다 한 장씩 붙는 자리라 형태가
    # 같은 것이 옳다 — 세면 「표형이 잦다」 는 결론만 나온다.
    frame = [spec.layout for spec in specs if spec.measure is None and spec.page is None]
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


def test_본문은_좌측_정렬이고_그림_캡션만_가운데다(full_sections: DocumentSections) -> None:
    """**본문은 좌측 정렬이다** (36세션 3-3). **그림 캡션만 가운데다** (53세션 1-2).

    그림은 칸 가운데 앉는데(``_picture_block``) 캡션만 칸 왼쪽 귀퉁이에 붙어
    있어 그림 아래가 아니라 그림 옆에 매달린 것처럼 보였다.
    """
    from pptx.enum.text import PP_ALIGN

    guide = load_design_guide()
    deck = build_slides(full_sections)
    centered = 0
    for slide in deck.slides:
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            for paragraph in shape.text_frame.paragraphs:
                if paragraph.alignment is PP_ALIGN.CENTER:
                    centered += 1
                    sizes = {run.font.size.pt for run in paragraph.runs if run.font.size}
                    assert sizes == {guide.type_scale.caption}, (
                        f"캡션이 아닌 글이 가운데 정렬입니다: {paragraph.text}"
                    )
                    continue
                assert paragraph.alignment in (None, PP_ALIGN.LEFT)
    assert centered, "가운데 정렬된 캡션이 하나도 없습니다."


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


def test_표지와_마무리가_다크다(full_sections: DocumentSections) -> None:
    """**샌드위치 구조** (36세션 3-2 · 37세션).

    36세션은 표지만 다크로 두어 구조가 반쪽이었다 — 지시서 목차에 마무리 장이
    없었기 때문이다. 아랫빵을 붙여 앞뒤가 짝을 이룬다.
    """
    guide = load_design_guide()
    deck = build_slides(full_sections)
    fills = [str(slide.background.fill.fore_color.rgb) for slide in deck.slides]
    assert fills[0] == guide.colors.cover.lstrip("#").upper()
    assert fills[-1] == guide.colors.closing.lstrip("#").upper()
    assert set(fills[1:-1]) == {guide.colors.white.lstrip("#").upper()}, (
        "본문 콘텐츠는 라이트다 — 다크는 바깥 둘뿐이다."
    )


def test_전체_배경_밴드에_포인트색을_쓸_수_없다() -> None:
    """**딥그린·네이비만 전체 배경이다** (36세션 3-2). 코랄은 작은 포인트뿐이다."""
    from dataclasses import replace

    from kwise.report.design import DesignGuideError

    colors = load_design_guide().colors
    for value in (colors.cover, colors.closing):
        assert value in (colors.deep_green, colors.dark_primary)
    with pytest.raises(DesignGuideError, match="전체 배경 밴드"):
        _ = replace(colors, cover_background="coral").cover
    with pytest.raises(DesignGuideError, match="전체 배경 밴드"):
        _ = replace(colors, closing_background="coral").closing


# ===================================================================== 37세션 · 마무리


def test_마무리가_맨_뒤에_있다(full_sections: DocumentSections) -> None:
    """**표지·목차와 같은 취급이다** — 자료가 무엇이든 늘 나온다 (37세션)."""
    for sections in (
        full_sections,
        DocumentSections(usage=full_sections.usage, bill=full_sections.bill),
    ):
        specs = slide_specs(sections)
        assert specs[-1].key == "closing"
        assert specs[-1].layout == "closing"
        assert specs[-1].title == SLIDE_TITLES["closing"]


def test_마무리가_다음_단계를_적는다(full_sections: DocumentSections) -> None:
    """**결론이 아니라 성격을 밝힌다.** 채택한 안은 현장에서 확정된다."""
    deck = build_slides(full_sections)
    text = _slide_text(list(deck.slides)[-1])
    assert SLIDE_TITLES["closing"] in text
    assert NEXT_STEPS_HEADLINE in text
    for title, detail in NEXT_STEPS:
        assert title in text and detail in text
    assert _visual_shapes(list(deck.slides)[-1]), "마무리에 시각 요소가 없습니다."


def test_다음_단계_문구가_앞뒤로_겹치지_않는다(loaded_sections: DocumentSections) -> None:
    """**같은 말을 두 번 하지 않는다** (37세션).

    덱 어디에도 없던 문구라 옮겨 올 것이 없었다 — 새로 적는 자리도 마무리
    하나뿐이어야 한다. 앞 장이 같은 말을 하기 시작하면 여기서 걸린다.
    """
    deck = build_slides(loaded_sections)
    slides = list(deck.slides)
    assert _deck_text(deck).count(NEXT_STEPS_HEADLINE) == 1
    front = "\n".join(_slide_text(slide) for slide in slides[:-1])
    for _title, detail in NEXT_STEPS:
        assert detail not in front, f"「{detail}」 가 앞 장에도 있습니다."
    assert "초기 판단용" not in front


def test_장수가_수단_개수를_따라간다(
    full_sections: DocumentSections, diagnosis_only: DocumentSections
) -> None:
    """뼈대 열한 장 + 켠 수단 수 (37세션). 수단 0개면 11장, 여섯이면 17장이다.

    **41세션에 수단이 여섯이 됐다** — 7.7 잉여 활용을 태양광 안으로 넣었다.
    """
    from dataclasses import replace

    # 뼈대 = 표지·목차·진단 다섯·요약·조합·마무리 + **부록 장 수** (39세션 5절).
    frame = FRAME_BEFORE_APPENDIX + 1  # 마무리
    assert len(slide_specs(diagnosis_only)) == frame + len(appendix_pages(diagnosis_only))
    assert len(slide_specs(full_sections)) == (
        frame + len(full_sections.measures) + len(appendix_pages(full_sections))
    )

    every = replace(full_sections, measures=_all_measures(full_sections))
    assert len(every.measures) == 6
    assert len(slide_specs(every)) == frame + 6 + len(appendix_pages(every))


# ===================================================================== 39세션 · 해석과 문구


#: 해석 한 줄과 용어 각주가 있어야 할 장 (39세션 1절).
#: 표지·목차·마무리는 뼈대라 빼고, 수단 장은 결론 한 줄이 그 몫을 진다.
LEAD_SLIDES: tuple[str, ...] = (
    "building",
    "usage_pattern",
    "peak_summary",
    "peak_detail",
    "structure",
    "measure_summary",
    "combination",
    "appendix",
)


def test_슬라이드마다_해석_한_줄이_있다(full_sections: DocumentSections) -> None:
    """**그래프만 나열하면 무엇을 보아야 하는지 알 수 없다** (39세션 1-1).

    제목 아래 문장이 그림보다 먼저 읽혀야 한다. 진단이 이미 내린 판정을 옮기거나,
    값의 크기로 고르거나, 고정 문장이다.
    """
    deck = build_slides(full_sections)
    specs = slide_specs(full_sections)
    for slide, spec in zip(deck.slides, specs, strict=True):
        if spec.key not in LEAD_SLIDES:
            continue
        body = _slide_text(slide).replace(spec.title, "", 1)
        assert any(line.strip().endswith("니다.") for line in body.splitlines()), (
            f"「{spec.title}」 장에 해석 한 줄이 없습니다."
        )


def test_수단_장은_결론_한_줄이_해석을_진다(full_sections: DocumentSections) -> None:
    """수단 장에는 별도 해석을 얹지 않는다 — **같은 말을 두 번 하지 않는다.**"""
    deck = build_slides(full_sections)
    specs = slide_specs(full_sections)
    for slide, spec in zip(deck.slides, specs, strict=True):
        if spec.measure is None:
            continue
        entry = full_sections.measures[spec.measure]
        assert entry.conclusion in _slide_text(slide)


def test_판정을_문장으로_옮기고_근거_숫자를_붙인다(full_sections: DocumentSections) -> None:
    """**진단이 이미 판정한 것은 그 판정을 쓴다** (39세션 1-1).

    화면 「개선 여지 요약」 의 태양광 등급이고, 근거 숫자가 바로 아래 지표의
    정오 비중이다 — 판정과 근거가 함께 읽혀야 한다.
    """
    from kwise.diagnose.summary import PvPotential
    from kwise.report import narrative

    diagnosis = full_sections.diagnosis
    assert diagnosis is not None
    lead = narrative.peak_summary_lead(diagnosis)
    share = f"{diagnosis.summary.pv_midday_share * 100:,.0f}%"
    assert share in lead, "판정만 적고 근거 숫자를 빠뜨렸습니다."
    assert set(PvPotential) == {PvPotential.HIGH, PvPotential.MEDIUM, PvPotential.LOW}
    # 등급마다 다른 문장을 낸다 — 하나로 뭉뚱그리면 판정을 옮긴 것이 아니다.
    assert len({text for text in narrative._PV_LEAD.values()}) == len(PvPotential)


def test_용어_풀이가_화면_툴팁에서_온다() -> None:
    """**두 벌로 적지 않는다** (39세션 1-2).

    화면은 「산식 한 줄 + 의미 한 줄」 로 툴팁을 적고, 슬라이드는 같은 산식을
    각주로 깐다 — 낳는 자리가 하나여야 한쪽만 고쳐지지 않는다.
    """
    from kwise.report import narrative

    table = narrative.terms()
    for key in ("load_factor", "base_load_ratio", "weekend_ratio", "off_hours_energy_share"):
        term = table[key]
        assert term.formula and term.meaning
        assert term.tooltip.startswith(term.formula)
        assert term.meaning in term.tooltip
        assert term.name in term.line

    source = (SRC_ROOT / "ui" / "views" / "diagnose.py").read_text(encoding="utf-8")
    assert "narrative.terms(" in source, "화면이 용어를 따로 들고 있으면 두 벌이 된다."


def test_용어_각주가_그_장의_용어만_깐다(full_sections: DocumentSections) -> None:
    """**전부 깔면 각주가 본문이 된다.** 그 장에 나오는 것만 고른다."""
    from kwise.report import narrative

    deck = build_slides(full_sections)
    table = narrative.terms()
    for key, chosen in narrative.GLOSSARY_KEYS.items():
        if not chosen:
            continue
        text = _slide_text(_slide_by_key(deck, full_sections, key))
        for term in chosen:
            assert f"{table[term].name} = " in text, f"「{key}」 장에 「{term}」 풀이가 없습니다."
        loose = [name for name in set(table) - set(chosen) if f"{table[name].name} = " in text]
        assert not loose, f"「{key}」 장에 그 장의 용어가 아닌 풀이가 있습니다: {loose}"


def test_상위_구간을_왜_보는지_적고_판정까지_한다(full_sections: DocumentSections) -> None:
    """화면 툴팁 문장 뒤에 **판정이 온다** (39세션 1-3 · 53세션 4-4).

    39세션까지는 「무엇을 보는 그림인가」 만 적고 이 건물이 어느 쪽인지는
    적지 않았다 — 그림 둘을 보고 읽는 사람이 스스로 세어야 했다.
    """
    from kwise.report import narrative

    deck = build_slides(full_sections)
    text = _slide_text(_slide_by_key(deck, full_sections, "peak_detail"))
    assert "낮에 몰리면 태양광이" in text
    assert full_sections.diagnosis is not None
    assert narrative.peak_summary_lead(full_sections.diagnosis) in text
    # **5장은 그 판정을 더 이상 적지 않는다** — 그림이 월별 최대수요다.
    summary = _slide_text(_slide_by_key(deck, full_sections, "peak_summary"))
    assert "낮 시간에 발생해" not in summary, summary
    assert "최대수요가" in summary


def test_6장_그림이_문장_차례와_같다(full_sections: DocumentSections) -> None:
    """**문장이 말하는 그림이 왼쪽이다** (53세션 4-5)."""
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    from pptx.util import Emu

    slide = _slide_by_key(build_slides(full_sections), full_sections, "peak_detail")
    captions = sorted(

            (Emu(shape.left).inches, shape.text_frame.text)
            for shape in slide.shapes  # type: ignore[attr-defined]
            if (shape.has_text_frame and "구간이 발생한 시각" in shape.text_frame.text)
            or (shape.has_text_frame and "평균 부하 모양" in shape.text_frame.text)

    )
    assert next(text for _left, text in captions).startswith("최대수요 상위")
    pictures = sorted(
        Emu(shape.left).inches
        for shape in slide.shapes  # type: ignore[attr-defined]
        if shape.shape_type is MSO_SHAPE_TYPE.PICTURE
    )
    assert len(pictures) == 2


# ===================================================================== 39세션 2절 · 어투


#: 개발자 어투 (39세션 2-1·2-3·2-4). **고객에게 우리 사정을 말하지 않는다.**
DEVELOPER_VOICE: tuple[str, ...] = (
    "모릅니다",
    "넣지 않았습니다",
    "자리가 모자라",
    "그렸습니다",
    "채우지 않습니다",
    "다시 잰 값",
)


def test_개발자_어투가_없다(loaded_sections: DocumentSections) -> None:
    """**「모릅니다」 는 우리 사정이다** (39세션 2-1).

    「미산출 — 투자비를 모릅니다」 와 「미산출 — 계통 연계·설비 조건에 따릅니다」 가
    나란히 있었다. 값 자리에는 「미산출」 만 두고, 무엇을 넣으면 값이 나오는지는
    각주가 받는다.
    """
    text = _deck_text(build_slides(loaded_sections))
    for phrase in DEVELOPER_VOICE:
        assert phrase not in text, f"개발자 어투가 남아 있습니다: 「{phrase}」"


def test_미산출은_값과_사유를_가른다() -> None:
    """지표 칸에는 값만, 사유는 각주로 (39세션 2-1)."""
    assert split_reason("미산출 — 투자비 미입력") == ("미산출", "투자비 미입력")
    assert split_reason("미산출") == ("미산출", "")
    assert split_reason("53,575,000원") == ("53,575,000원", "")


def test_12개월_환산을_값마다_되풀이하지_않는다(full_sections: DocumentSections) -> None:
    """**같은 값을 두 번 적지 않는다** (39세션 2-2).

    「9,050,000원 (12개월 환산 9,050,000원)」 이 두 줄로 흐르고, 읽는 사람은 어느
    쪽이 답인지 되묻는다. 기준은 각주가 **한 번** 적는다.
    """
    text = _deck_text(build_slides(full_sections))
    assert "12개월 환산" not in text.replace(ANNUAL_BASIS_NOTE, "")
    assert text.count(ANNUAL_BASIS_NOTE) == 1
    for entry in full_sections.measures:
        assert "12개월 환산" not in entry.slide_saving


def test_Word_에도_마크다운_표식이_없다(loaded_sections: DocumentSections) -> None:
    """**Word 도 마크다운을 해석하지 않는다** (39세션 2-6).

    38세션에 PPT 만 막았고 Word 에는 별표가 남아 있었다. 벗기는 자리는
    :func:`kwise.report.notices.plain_text` 하나다.
    """
    document = build_document(loaded_sections)
    parts = [item.text for item in document.paragraphs]
    for table in document.tables:
        parts.extend(cell.text for row in table.rows for cell in row.cells)
    text = "\n".join(parts)
    for mark in MARKDOWN_MARKS:
        assert mark not in text, f"Word 에 마크다운 표식 「{mark}」 이 남아 있습니다."


def test_Word_가_글자를_쓰는_자리를_모두_지난다() -> None:
    """**자리마다 벗기면 새 문구를 붙일 때 빠뜨린다** (39세션 2-6)."""
    source = (SRC_ROOT / "report" / "document.py").read_text(encoding="utf-8")
    calls = ("document.add_paragraph(", "document.add_heading(", "cell.text =")
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "plain_text(" in stripped:
            continue
        if "add_paragraph()" in stripped:
            continue
        for call in calls:
            assert call not in stripped, f"마크다운을 벗기지 않는 자리가 있습니다: {stripped}"


def test_단위가_빠진_칸이_없다() -> None:
    """「역률 개선 (97)」 이 그렇게 나가고 있었다 (39세션 2-5)."""
    from kwise.measures import AppliedMeasure

    assert AppliedMeasure("power_factor", (("power_factor_pct", 97.0),)).label == "역률 개선 (97%)"
    assert AppliedMeasure("solar", (("capacity_kwp", 240.0),)).label == "태양광 (240 kWp)"


def test_조합_구성을_한_줄로_이어_적는다(full_sections: DocumentSections) -> None:
    """**고른 수단을 순서대로** (39세션 3-3).

    조합 이름은 「+ ESS 목표 5,170 kW」 처럼 직전 조합에 무엇을 더했는가를 적는
    것이라, 그것만 떼어 놓으면 앞의 수단들이 보이지 않았다.
    """
    comparison = full_sections.comparison
    assert comparison is not None
    baseline = comparison.combinations[0].spec.selection
    line = comparison.best.spec.composition(baseline)
    assert " + " in line
    assert "선택요금 전환" in line
    assert "(" not in line, "괄호를 겹쳐 적지 않는다."
    text = _slide_text(_slide_by_key(build_slides(full_sections), full_sections, "combination"))
    assert line in text


# ===================================================================== 39세션 4절 · 값이 0인 수단


def test_잉여가_0인_까닭을_숫자로_적는다() -> None:
    """**0이라는 것 자체가 정보다. 다만 왜 0인지가 있어야 한다** (39세션 4-3)."""
    from kwise.report import narrative

    line = narrative.surplus_lead(
        total_kwh=0.0,
        generation_kwh=281_293.0,
        self_consumed_kwh=281_293.0,
        surplus_free_kwp=2_048.0,
    )
    assert "자가소비" in line and "2,048 kWp" in line
    assert "281" in line, "발전량을 숫자로 보인다."


def test_DR_이_0인_까닭을_적는다() -> None:
    """**거래 가능일과 저부하 평일만 적으면 왜 그런지 알 수 없다** (39세션 4-1)."""
    from kwise.report import narrative

    assert narrative.dr_lead(None) == ""


def test_결론과_무관한_주의사항을_싣지_않는다(full_sections: DocumentSections) -> None:
    """**하지도 않을 일을 조심하라는 글이 결론보다 길어진다** (39세션 4-2)."""
    from dataclasses import replace

    base = full_sections.measures[0]
    idle = replace(
        base,
        actionable=False,
        cautions=("계약전력 하향은 되돌리기 어렵습니다.",),
        facts=(("하향 여지", "0 kW"),),
        figure=None,
        figures=(),
    )
    assert _cautions(idle) == (), "여지가 없으면 실행 주의사항을 싣지 않는다."
    assert _cautions(replace(idle, actionable=True)), "여지가 있으면 그대로 싣는다."


# ===================================================================== 38세션 3절 · 그래프 대조


#: **화면 그림 → PPT 그림.** 38세션 3-5 에 하나씩 대조한 결과다.
#:
#: 화면에 있는데 PPT 에 없던 것이 셋이었다 — 역률 일일 곡선 · 태양광 연간
#: 발전량 · ESS 회수기간 곡선. 셋 다 「무엇을 보고 그렇게 판단했나」 에 답하는
#: 그림이라, 빠지면 슬라이드에 결론만 남는다.
#:
#: **요금제 전환 둘은 png 한 장이 함께 진다** — ``tariff_option_png`` 가 위 칸에
#: 그룹 막대, 아래 칸에 현행 대비 차액을 그린다 (17세션 1-2·1-3).
SCREEN_TO_DECK: dict[str, str] = {
    "daily_temperature_chart": "daily_temperature_png",
    "monthly_peak_chart": "monthly_peak_png",
    "hourly_profile_chart": "hourly_profile_png",
    "top_hour_chart": "top_hour_png",
    "monthly_charge_chart": "monthly_charge_png",
    "band_donut_chart": "band_donut_grid_png",
    "tariff_delta_chart": "tariff_option_png",
    "tariff_option_chart": "tariff_option_png",
    "dr_daily_chart": "dr_daily_png",
    "power_triangle_chart": "power_triangle_png",
    "power_factor_day_chart": "power_factor_day_png",
    "solar_annual_chart": "solar_annual_png",
    "solar_day_chart": "solar_day_png",
    "ess_day_chart": "ess_day_png",
    "surplus_daily_chart": "surplus_daily_png",
}


def _screen_charts() -> set[str]:
    """화면이 **실제로 그리는** 그림 이름. 정의만 있고 안 그리는 것은 세지 않는다."""
    pattern = re.compile(r"charts\.(\w+_chart)\(")
    names: set[str] = set()
    for path in (SRC_ROOT / "ui" / "views").glob("*.py"):
        names |= set(pattern.findall(path.read_text(encoding="utf-8")))
    return names


def test_화면에_있는_그림이_PPT_에도_있다() -> None:
    """**하나씩 세어 대조한다** (38세션 3-5).

    화면에 그림을 더하고 슬라이드를 잊으면, 받는 사람은 「분석은 했는데 근거는
    없는」 보고서를 받는다. 그 어긋남은 둘을 나란히 놓고서야 드러나므로 시험이
    센다 — 짝을 못 지으면 :data:`SCREEN_TO_DECK` 에 한 줄을 더하거나 png 를
    새로 만들어야 한다.
    """
    drawn = _screen_charts()
    assert drawn, "화면 그림을 찾지 못했습니다 — 훑는 규칙이 낡았습니다."
    missing = sorted(drawn - set(SCREEN_TO_DECK))
    assert not missing, (
        f"화면에만 있고 PPT 에 없는 그림입니다: {missing}. "
        "png 를 만들어 슬라이드에 싣거나, 싣지 않는 이유를 SCREEN_TO_DECK 옆에 적으십시오."
    )
    stale = sorted(set(SCREEN_TO_DECK) - drawn)
    assert not stale, f"화면이 더는 그리지 않는 그림이 표에 남아 있습니다: {stale}"
    for screen, deck in SCREEN_TO_DECK.items():
        assert deck in figures.__all__, f"「{screen}」 의 짝 「{deck}」 이 figures 에 없습니다."


def test_역률_태양광_ESS_가_그림_둘을_싣는다(full_sections: DocumentSections) -> None:
    """**화면은 셋 다 그림이 둘이다** (38세션 3-1·3-2·3-3).

    36세션의 덱은 하나씩만 실어, 전력삼각형만으로 「어느 시간대가 요금 대상인가」
    를, 대표일 곡선만으로 「한 해에 얼마나 만드나」 를, 충·방전 곡선만으로
    「목표 5,170 kW 는 어디서 나왔나」 를 답하지 못했다.
    """
    from kwise.report.document import MeasureFigure

    entry = MeasureEntry(
        kind=MEASURE_CATALOG[0],
        conclusion="본문",
        saving="0원",
        investment="0원",
        payback="즉시",
        certainty="높음",
        figures=(MeasureFigure(b"a", "왼쪽"), MeasureFigure(b"b", "오른쪽")),
    )
    assert len(entry.slide_figures) == 2
    spec = slide_specs(_with_measures(full_sections, (entry,)))[8]
    assert spec.layout == "chart_pair", "그림이 둘이면 좌우로 나눈다."

    # 하나만 주면 폭 전체를 쓴다 — 빈 칸을 남기지 않는다.
    single = MeasureEntry(
        kind=MEASURE_CATALOG[0],
        conclusion="본문",
        saving="0원",
        investment="0원",
        payback="즉시",
        certainty="높음",
        figure=b"a",
        figure_caption="하나",
    )
    assert len(single.slide_figures) == 1
    assert slide_specs(_with_measures(full_sections, (single,)))[8].layout == "stat_chart"


def _with_measures(
    sections: DocumentSections, measures: tuple[MeasureEntry, ...]
) -> DocumentSections:
    from dataclasses import replace

    return replace(sections, measures=measures)


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


def test_부록이_넘치면_장을_나눈다(full_sections: DocumentSections) -> None:
    """**자르지 않고 나눈다** (39세션 5절).

    36세션까지는 한 장에 눌러 담고 「자리가 모자라 뺐다」 고 적었는데, 그러면
    선택요금 전환 하나만 실리고 나머지 여섯이 통째로 빠졌다.
    """
    from dataclasses import replace

    from kwise.report.worksheet import Worksheet

    sheet = full_sections.worksheets[0]
    many = Worksheet(key=sheet.key, title=sheet.title, rows=sheet.rows * 6)
    sections = replace(full_sections, worksheets=(many,))
    pages = appendix_pages(sections)
    assert len(many.frame()) > APPENDIX_ROW_LIMIT
    assert len(pages) > 1, "넘치면 장을 나눈다."
    assert sum(len(page.rows) for page in pages) == len(many.frame()), "한 줄도 버리지 않는다."
    assert all("(" in page.title for page in pages), "이어지는 장임을 제목이 밝힌다."
    text = _deck_text(build_slides(sections))
    assert "자리가 모자라" not in text, "제작 사정을 고객에게 말하지 않는다."


def test_부록이_수단마다_한_장_이상이다(full_sections: DocumentSections) -> None:
    """**분석한 자료를 감추지 않는다** (39세션 5절).

    절감액이 산출된 수단은 모두 근거가 실리고, 0이거나 미산출인 수단은 빠지되
    **뺐다는 사실을 각주가 적는다** — 조용히 빼면 「검토하지 않았다」 로 읽힌다.
    """
    sections = full_sections
    priced = [entry for entry in sections.measures if entry.has_saving]
    dropped = [entry for entry in sections.measures if not entry.has_saving]
    assert priced and dropped, "이 시험은 값이 있는 수단과 없는 수단을 함께 본다."
    titles = [page.title for page in appendix_pages(sections)]
    sheets = {sheet.key for sheet in sections.worksheets}
    for entry in priced:
        if entry.kind.key not in sheets:
            continue
        assert any(measure_slide_title(entry) in title for title in titles), (
            f"「{entry.kind.label}」 의 근거가 부록에 없습니다."
        )
    for entry in dropped:
        assert not any(measure_slide_title(entry) in title for title in titles)
    text = _deck_text(build_slides(sections))
    for entry in dropped:
        assert entry.kind.label in text, "뺐다는 사실을 각주가 적는다."
    assert "전문은 Excel 부록 A 에 있습니다" in text


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


def test_저장_경로에_폴더가_없으면_만든다(full_sections: DocumentSections, tmp_path: Path) -> None:
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


# ==================================================== 41세션 · 잉여를 태양광 안으로


def test_ppt_에_잉여_장이_없고_태양광_장이_진다(
    sample_usage: UsageData, tariff: TariffTable, sample_bill: BillingResult
) -> None:
    """**7.7 장을 없애고 7.5 에 녹였다** (41세션 2-1·2-6).

    잉여는 태양광을 얼마나 크게 지을지에 따라 나오는 결과이지 개선안이 아니다.
    장을 따로 세우면 같은 발전량 이야기를 두 번 한다 — 그래서 **장이 하나 준다.**

    일별 잉여 그림은 슬라이드로 오지 않는다. 수단 장은 그림 둘까지이고(38세션
    3절) 태양광 장은 연간 발전량과 대표일 곡선이 그 둘을 이미 쓴다. 그림은
    화면(잉여 처리 접힘)에 남겼다.
    """
    import numpy as np
    import pandas as pd

    from kwise.measures import apply_generation, solar_point
    from kwise.measures.surplus import evaluate_surplus
    from kwise.report.document import measure_entries
    from kwise.tariff import TariffSelection

    selection = TariffSelection("general_b", "high_a", "I")
    index = pd.DatetimeIndex(sample_usage.kw.index)
    hours = index.hour + index.minute / 60.0
    unit = pd.Series(
        np.clip(np.sin(np.pi * (hours - 7.0) / 12.0), 0.0, None) * 0.8,
        index=index,
        name="kw",
    )
    capacity = 4_000.0
    point = solar_point(sample_usage, tariff, selection, unit, capacity, baseline=sample_bill)
    net = apply_generation(sample_usage, unit * capacity)
    surplus = evaluate_surplus(
        sample_usage,
        tariff,
        selection,
        net.surplus_kw,
        generation_kwh=net.generated_kwh,
        net_usage=net.usage,
        capacity_kwp=capacity,
    )
    assert surplus.total_kwh > 0

    entries = measure_entries(solar=point, surplus=surplus, surplus_free_kwp=2_048.0)
    # **잉여 장이 없다.** 태양광 하나뿐이다.
    assert [entry.kind.key for entry in entries] == ["solar"]

    solar = next(entry for entry in entries if entry.kind.key == "solar")
    facts = dict(solar.facts)
    assert facts["잉여"].endswith("MWh"), facts
    assert facts["자가소비"].endswith("MWh"), facts
    assert facts["잉여 없는 최대 용량"] == "2,048 kWp", facts
    # **시나리오는 태양광 각주에 없다** (53세션 3절). 잉여가 나면 「잉여 활용」
    # 장이 다음에 붙어 표로 낸다 — 각주는 「버리기 0원」 까지 이어 적고 있었다.
    assert solar.slide_note == "", solar.slide_note
    assert any("자격요건은 판정하지 않았습니다" in line for line in solar.cautions)

    from kwise.report.document import surplus_page

    page = surplus_page(surplus, capacity_kwp=capacity, surplus_free_kwp=2_048.0)
    assert page is not None
    names = [row[0] for row in page.scenario_rows[1:]]
    assert "외부 판매" in names
    assert "버리기" not in names, names
    # 그림은 여전히 둘이다 — 셋을 넣으면 축 눈금이 뭉개진다 (38세션 3절).
    assert len(solar.slide_figures) <= 2


def test_잉여가_0이면_태양광_장이_늘지_않는다(
    sample_usage: UsageData, tariff: TariffTable, sample_bill: BillingResult
) -> None:
    """**팔 것이 없는데 파는 절차를 조심하라고 적지 않는다** (39세션 4-2)."""
    import pandas as pd

    from kwise.measures import apply_generation, solar_point
    from kwise.measures.surplus import evaluate_surplus
    from kwise.report.document import measure_entries
    from kwise.tariff import TariffSelection

    selection = TariffSelection("general_b", "high_a", "I")
    index = pd.DatetimeIndex(sample_usage.kw.index)
    unit = pd.Series(0.0, index=index, name="kw")
    point = solar_point(sample_usage, tariff, selection, unit, 100.0, baseline=sample_bill)
    net = apply_generation(sample_usage, unit * 100.0)
    surplus = evaluate_surplus(
        sample_usage,
        tariff,
        selection,
        net.surplus_kw,
        generation_kwh=net.generated_kwh,
        net_usage=net.usage,
        capacity_kwp=100.0,
    )
    assert surplus.total_kwh == 0.0

    solar = next(
        entry
        for entry in measure_entries(solar=point, surplus=surplus)
        if entry.kind.key == "solar"
    )
    assert "잉여" not in dict(solar.facts)
    assert solar.slide_note == ""
    assert not any("자격요건" in line for line in solar.cautions), solar.cautions


# ===================================================================== 53세션 · 공통 규약


def _note_lines(slide: object, guide: object) -> list[str]:
    """슬라이드 아래쪽 작은 회색 글씨. **캡션과 각주가 여기 든다.**"""
    size = guide.type_scale.caption  # type: ignore[attr-defined]
    lines: list[str] = []
    for shape in slide.shapes:  # type: ignore[attr-defined]
        if not shape.has_text_frame:
            continue
        for paragraph in shape.text_frame.paragraphs:
            sizes = {run.font.size.pt for run in paragraph.runs if run.font.size}
            if sizes == {size} and paragraph.text.strip():
                lines.append(paragraph.text.strip())
    return lines


def test_참고용_작은_글씨에_표식이_붙는다(full_sections: DocumentSections) -> None:
    """**※ 하나가 참고와 본문을 가른다** (53세션 1-1).

    작은 회색 글씨라는 것만으로는 본문의 끝인지 참고인지 갈리지 않는다.
    **그림 캡션은 제외한다** — 캡션은 그림의 이름이지 참고가 아니다.
    """
    from kwise.report.slides import NOTE_MARK, mark_note

    guide = load_design_guide()
    deck = build_slides(full_sections)
    captions = {figure.caption for entry in full_sections.measures for figure in entry.figures}
    marked = 0
    for slide in deck.slides:
        for line in _note_lines(slide, guide):
            if line.startswith(NOTE_MARK.strip()):
                marked += 1
                continue
            assert line in captions or "—" in line or "습니다" not in line, (
                f"※ 없는 참고 줄: {line}"
            )
    assert marked >= 5, f"※ 가 붙은 줄이 {marked}개뿐입니다."
    # 두 번 붙지 않는다.
    assert mark_note(mark_note("가나다")) == "※ 가나다"


def test_각주가_산식을_쉼표로_가른다() -> None:
    """**「·」 가 산식 자리에서 수식 기호로 읽힌다** (53세션 1-3)."""
    from kwise.report.narrative import GLOSSARY_KEYS, glossary_note

    note = glossary_note(GLOSSARY_KEYS["usage_pattern"])
    assert "÷" in note
    assert " · " not in note, note
    assert note.count(", ") == 2, note


def test_투자가_없으면_줄표와_즉시로_적는다(full_sections: DocumentSections) -> None:
    """**「0 원」 은 값이 아니라 없음이다** (53세션 1-5)."""
    from kwise.report.slides import IMMEDIATE, NO_INVESTMENT, slide_investment, slide_payback

    assert slide_investment("0원") == NO_INVESTMENT
    assert slide_investment("261,893,000원") == "261,893,000원"
    assert slide_payback("즉시 (투자 없음)") == IMMEDIATE
    assert slide_payback("31.7년") == "31.7년"
    assert slide_payback("미산출 — 투자비 미입력") == "미산출"

    text = _deck_text(build_slides(full_sections))
    assert "즉시 (투자 없음)" not in text
    # **투자비 칸에만 걸린다.** 절감액이 실제로 0원인 수단은 그대로 0원이다 —
    # 계산해 보니 0 인 값과 애초에 살 것이 없는 값은 다른 사실이다.
    investments = {
        row.cells[2].text
        for slide in build_slides(full_sections).slides
        for shape in slide.shapes
        if shape.has_table and shape.table.rows[0].cells[0].text == "개선 수단"
        for row in list(shape.table.rows)[1:]
    }
    assert "0원" not in investments, investments


def test_부록에_해석_문구가_없다(full_sections: DocumentSections) -> None:
    """**부록은 근거를 펼치는 자리다** (53세션 1-6). 읽는 법을 일러 주지 않는다."""
    from kwise.report.narrative import APPENDIX_LEAD

    deck = build_slides(full_sections)
    slide = _slide_by_key(deck, full_sections, "appendix")
    assert APPENDIX_LEAD not in _slide_text(slide)
    assert APPENDIX_LEAD not in _deck_text(deck)


def test_확실성이_슬라이드에_없다(full_sections: DocumentSections) -> None:
    """**등급을 산출물에서 뺐다** (53세션 1-4). 계산은 그대로다."""
    assert "확실성" not in _deck_text(build_slides(full_sections))
    assert all(entry.certainty for entry in full_sections.measures), "계산값은 남는다"


# ===================================================================== 53세션 · 2절 ESS 배치


def _ess_entry_with_spec() -> MeasureEntry:
    """목표별 사양 표가 달린 ESS 항목. **표 모양만 실물과 같으면 된다.**"""
    from kwise.measures import measure_kind
    from kwise.report import figures, frames

    png = figures.chart_palette  # 존재 확인용 — 아래에서 실제 png 를 굽지 않는다
    assert png is not None
    rows = [frames.ESS_SPEC_HEADER]
    rows.extend(
        (
            f"{5_240 - 40 * index:,.0f}~{5_260 - 40 * index:,.0f} kW",
            f"{73 + 40 * index:,.0f} kW",
            "150 kW",
            "100 kWh",
            "0.67h",
            "261,893,000",
            "8,264,000",
            "31.7년",
            "목표 미달 (실제 5,264 kW)" if index else "최단 회수기간",
        )
        for index in range(5)
    )
    return MeasureEntry(
        kind=measure_kind("ess"),
        conclusion="피크를 5,180 kW 까지 낮추려면 ESS 150 kW / 100 kWh 가 필요합니다.",
        saving="8,264,000원",
        investment="261,893,000원",
        payback="31.7년",
        certainty="중간~낮음",
        spec_table=tuple(rows),
        spec_caption=frames.ESS_SPEC_CAPTION,
    )


def test_사양_표가_아래_그림을_덮지_않는다(full_sections: DocumentSections) -> None:
    """**두 세션 연속 지적된 자리다** (53세션 2절).

    50세션에 격자가 붙어 표가 여섯 줄이 되자, 46세션의 ``height * 0.55`` 배분이
    그대로 표를 키워 그림이 **0.85 × 0.41in** 로 우겨 넣어졌다. 줄 수로 잡는다.
    """
    import dataclasses

    from pptx.util import Emu

    entry = _ess_entry_with_spec()
    sections = dataclasses.replace(full_sections, measures=(entry,))
    slide = _slide_by_key(build_slides(sections), sections, f"measure_{entry.kind.key}")
    boxes = [
        (Emu(shape.top).inches, Emu(shape.top + shape.height).inches, shape)
        for shape in slide.shapes  # type: ignore[attr-defined]
        if shape.top is not None and shape.height
    ]
    from kwise.report.frames import ESS_SPEC_HEADER

    tables = [
        box
        for box in boxes
        if box[2].has_table
        and tuple(cell.text for cell in box[2].table.rows[0].cells) == ESS_SPEC_HEADER
    ]
    assert len(tables) == 1, "사양 표가 하나여야 한다"
    table_top, table_bottom, shape = tables[0]
    assert table_bottom < 7.0, f"표가 슬라이드 밖으로 나갑니다: {table_bottom:.2f}in"
    for start, _end, other in boxes:
        if other is shape or start < table_top:
            continue
        assert start >= table_bottom - 1e-6, (
            f"표(밑변 {table_bottom:.2f}in) 아래 요소가 {start:.2f}in 에서 시작합니다."
        )


def test_사양_표_열이_고르게_나뉘지_않는다() -> None:
    """**긴 칸에 폭을 몰아 준다** (53세션 2절).

    아홉 열을 고르게 나누면 「5,220~5,240 kW」 가 두 줄로 흘러 표가 1.5배가 된다.
    """
    from kwise.report.design import load_design_guide
    from kwise.report.frames import ESS_SPEC_HEADER
    from kwise.report.slides import SPEC_TABLE_WIDTHS, _spec_lines

    assert len(SPEC_TABLE_WIDTHS) == len(ESS_SPEC_HEADER)
    assert abs(sum(SPEC_TABLE_WIDTHS) - 1.0) < 1e-9
    guide = load_design_guide()
    rows = _ess_entry_with_spec().spec_table
    width = guide.slide.content_width_in
    size = guide.type_scale.caption
    # **몰아 준 폭에서는 한 줄에 앉는다.**
    assert _spec_lines(rows, width=width, size=size) == len(rows)
    # **고르게 나누면 흐른다** — 이것이 46세션 배치가 무너진 까닭이다.
    even = tuple(1.0 / len(ESS_SPEC_HEADER) for _ in ESS_SPEC_HEADER)
    monkey = width * even[0] - 0.12
    from kwise.report.slides import _fitting_lines

    assert _fitting_lines(["5,240~5,260 kW"], span=monkey, size=size) == 2


# ===================================================================== 53세션 · 3절 잉여 장


def _surplus_result(*, weekday: float, weekend: float, holiday: float) -> object:
    """잉여 결과를 **값만 채워** 만든다. 갈래 시험은 계산이 아니라 문장을 본다."""
    import pandas as pd

    from kwise.measures.surplus import (
        DISCARD_SCENARIO,
        EXTERNAL_SCENARIO,
        OFFSET_SCENARIO,
        SurplusResult,
        SurplusScenario,
    )

    total = weekday + weekend + holiday
    return SurplusResult(
        total_kwh=total,
        generation_kwh=total * 8,
        share_of_generation=0.125 if total else None,
        hour_distribution=pd.Series(dtype=float),
        weekday_kwh=weekday,
        weekend_kwh=weekend,
        holiday_kwh=holiday,
        scenarios=(
            SurplusScenario(OFFSET_SCENARIO, 2_545_000.0, "상계", "계약 변경"),
            SurplusScenario(EXTERNAL_SCENARIO, None, "단가 미입력", "구매자 발굴"),
            SurplusScenario(DISCARD_SCENARIO, 0.0, "버린다", "없다"),
        ),
    )


def test_잉여가_있으면_장이_생기고_0이면_안_생긴다(full_sections: DocumentSections) -> None:
    """**41세션에 잉여 장이 사라지면서 보여 줄 자리가 없어졌다** (53세션 3-1)."""
    import dataclasses

    from kwise.report.document import surplus_page

    assert surplus_page(None, capacity_kwp=160.0) is None
    empty = _surplus_result(weekday=0.0, weekend=0.0, holiday=0.0)
    assert surplus_page(empty, capacity_kwp=160.0) is None  # type: ignore[arg-type]

    page = surplus_page(
        _surplus_result(weekday=105.0, weekend=20_000.0, holiday=3_311.0),  # type: ignore[arg-type]
        capacity_kwp=160.0,
        surplus_free_kwp=40.0,
    )
    assert page is not None
    # 태양광 장이 있어야 그 **다음**이 정해진다 — 수단을 모두 켠 덱으로 본다.
    base = dataclasses.replace(full_sections, measures=_all_measures(full_sections))
    with_page = dataclasses.replace(base, surplus=page)
    without = dataclasses.replace(base, surplus=None)
    keys = [item.key for item in slide_specs(with_page)]
    assert keys.count("surplus") == 1
    assert keys.index("surplus") == keys.index("measure_solar") + 1
    assert "surplus" not in [item.key for item in slide_specs(without)]
    assert len(slide_specs(with_page)) == len(slide_specs(without)) + 1
    # 목차도 한 줄 는다.
    assert SLIDE_TITLES["surplus"] in agenda_items(with_page)
    assert SLIDE_TITLES["surplus"] not in agenda_items(without)


def test_잉여_문장이_평일_휴일_비중으로_갈린다() -> None:
    """**이 건물 값에 맞춘 문장을 고정으로 박지 않는다** (53세션 3-2 · 4절 공통).

    소형 사무빌딩은 휴일이 99.6% 지만, 평일 낮에 문을 닫지 않는 건물은 반대다.
    """
    from kwise.report.document import surplus_page

    def lead(**parts: float) -> str:
        page = surplus_page(_surplus_result(**parts), capacity_kwp=160.0)  # type: ignore[arg-type]
        assert page is not None
        return page.lead

    assert "대부분 토·일·공휴일에 발생합니다" in lead(
        weekday=105.0, weekend=20_000.0, holiday=3_311.0
    )
    assert "대부분 평일에 발생합니다" in lead(weekday=9_000.0, weekend=500.0, holiday=500.0)
    assert "고르게 발생합니다" in lead(weekday=5_000.0, weekend=3_000.0, holiday=2_000.0)


def test_잉여_장에_버리기가_없다(full_sections: DocumentSections) -> None:
    """**27세션에 화면에서 뺐다** (53세션 3-2). 「아무것도 안 한다」 는 방안이 아니다."""
    import dataclasses

    from kwise.report.document import SURPLUS_SCENARIO_HEADER, surplus_page

    page = surplus_page(
        _surplus_result(weekday=105.0, weekend=20_000.0, holiday=3_311.0),  # type: ignore[arg-type]
        capacity_kwp=160.0,
        surplus_free_kwp=40.0,
    )
    assert page is not None
    assert page.scenario_rows[0] == SURPLUS_SCENARIO_HEADER
    assert [row[0] for row in page.scenario_rows[1:]] == ["상계거래(한전)", "외부 판매"]
    assert len(page.facts) == 4

    sections = dataclasses.replace(
        full_sections, measures=_all_measures(full_sections), surplus=page
    )
    slide = _slide_by_key(build_slides(sections), sections, "surplus")
    text = _slide_text(slide)
    assert "버리기" not in text
    assert "상계거래는 계약 변경과 역송 계량기가 필요합니다" in text
    assert "자격요건은 판정하지 않았습니다" in text


# ===================================================================== 53세션 · 4절 갈래


def test_결측_갈래가_셋이다(sample_report: object) -> None:
    """**결측이 없으면 뒷문장이 없다** (53세션 4-1).

    39세션은 「가장 심한 달」 하나만 짚었다. 여러 달이 높으면 이름을 다 이어
    붙이는 대신 개수로 적는다 — 해석 한 줄이 세 줄로 흐르지 않게.
    """
    from dataclasses import replace

    from kwise.report.narrative import building_lead

    assert "결측" in building_lead(None)
    clean = replace(sample_report, missing_slots=0, missing_ratio=0.0, monthly=())  # type: ignore[type-var]
    text = building_lead(clean)
    assert "결측이 없어" in text
    assert "낮게 잡혔을" not in text, text

    one = building_lead(sample_report)  # type: ignore[arg-type]
    assert "낮게 잡혔을 수 있습니다" in one, one
    assert "개 달이" not in one, one

    heavy = replace(
        sample_report,  # type: ignore[type-var]
        monthly=tuple(
            replace(month, ratio=0.4, missing_slots=100)
            for month in sample_report.monthly  # type: ignore[attr-defined]
        ),
    )
    many = building_lead(heavy)
    assert "개 달이 크게 비어" in many, many


def test_부하패턴_문장_둘이_각각_갈린다(sample_diagnosis: Diagnosis) -> None:
    """**조합이 넷이다** (53세션 4-2). 부하율이 높으면 「짧은 피크」 가 아니다."""
    from dataclasses import replace

    from kwise.report.narrative import base_load_high, load_factor_flat, pattern_lead

    flat = load_factor_flat() + 0.05
    peaky = load_factor_flat() - 0.05
    high = base_load_high() + 0.05
    low = base_load_high() - 0.05
    base = replace(sample_diagnosis.pattern, load_factor=peaky, base_load_ratio=high)
    assert "짧은 피크" in pattern_lead(base)
    assert "충전 여력은 좁습니다" in pattern_lead(base)
    both = pattern_lead(replace(base, load_factor=flat, base_load_ratio=low))
    assert "하루 내내 고르게" in both
    assert "충전 여력이 있습니다" in both
    # 값이 없으면 그 문장이 통째로 빠진다.
    assert "기저부하" not in pattern_lead(replace(base, base_load_ratio=None))
    assert "산출하지 못했습니다" in pattern_lead(
        replace(base, load_factor=None, base_load_ratio=None)
    )


def test_요금구조가_세_갈래다() -> None:
    """**가운데를 가운데라고 적는다** (53세션 4-6)."""
    from types import SimpleNamespace

    from kwise.report.narrative import base_fee_share_high, base_fee_share_low, structure_lead

    def lead(share: float) -> str:
        return structure_lead(
            SimpleNamespace(  # type: ignore[arg-type]
                base_won=share * 100.0,
                energy_won=(1 - share) * 100.0,
                total_won=100.0,
                bill=SimpleNamespace(total_power_factor_won=0.0),
            )
        )

    assert "나머지는 전력량요금입니다" in lead(base_fee_share_low() - 0.05)
    assert "함께 큽니다" in lead((base_fee_share_low() + base_fee_share_high()) / 2)
    assert "최대수요를 낮추는 방안을 먼저" in lead(base_fee_share_high() + 0.05)
    assert "산출하지 못했습니다" in structure_lead(
        SimpleNamespace(  # type: ignore[arg-type]
            base_won=0.0,
            energy_won=0.0,
            total_won=0.0,
            bill=SimpleNamespace(total_power_factor_won=0.0),
        )
    )


def test_요약_근거에_0원인_수단이_안_든다() -> None:
    """**계약전력 조정은 0원인데 근거로 들고 있었다** (53세션 4-7)."""
    from types import SimpleNamespace

    from kwise.report.narrative import measure_summary_lead

    def lead(switch: float | None, contract: float | None) -> str:
        summary = SimpleNamespace(
            tariff_switch_saving_won=switch,
            contract_saving_won=contract,
            no_investment_saving_won=(switch or 0.0) + (contract or 0.0),
        )
        return measure_summary_lead(SimpleNamespace(summary=summary), "5,358만원")  # type: ignore[arg-type]

    only = lead(53_575_000.0, 0.0)
    assert "요금제 전환입니다" in only, only
    assert "계약전력" not in only, only
    assert "계약전력 조정" in lead(53_575_000.0, 1_000_000.0)
    assert "미산출" not in lead(53_575_000.0, None)
    assert "줄일 수 있는 몫은 없습니다" in lead(0.0, 0.0)


def test_계약전력이_세_갈래다(sample_usage: UsageData, sample_bill: BillingResult) -> None:
    """**초과 위약 갈래가 없었다** (53세션 4-9). 계산은 이미 알고 있었다."""
    from kwise.measures import evaluate_contract_adjustment
    from kwise.report.document import measure_entries

    def conclusion(contract_kw: float) -> str:
        contract = evaluate_contract_adjustment(sample_usage, sample_bill, contract_kw=contract_kw)
        entry = next(
            item for item in measure_entries(contract=contract) if item.kind.key == "contract"
        )
        return entry.conclusion

    # 계약전력이 실측 최대보다 낮으면 초과 위약이다.
    over = conclusion(3_000.0)
    assert "초과 위약 검토 대상입니다" in over, over
    assert "상향을 검토해야 합니다" in over
    assert "낮출 여지가" in conclusion(6_500.0)


def test_역률이_세_갈래다(
    sample_usage: UsageData, tariff: TariffTable, sample_bill: BillingResult
) -> None:
    """**기준 미달이면 감액이 아니라 추가요금 회피다** (53세션 4-11)."""
    from kwise.measures import evaluate_power_factor
    from kwise.report.document import measure_entries
    from kwise.tariff import TariffSelection

    selection = TariffSelection("general_b", "high_a", "I")

    def conclusion(current: float, target: float) -> str:
        result = evaluate_power_factor(
            sample_usage,
            tariff,
            selection,
            current_pct=current,
            target_pct=target,
            baseline=sample_bill,
        )
        entry = next(
            item
            for item in measure_entries(power_factor=result)
            if item.kind.key == "power_factor"
        )
        return entry.conclusion

    penalty = conclusion(85.0, 97.0)
    assert "추가요금이 붙습니다" in penalty, penalty
    assert "추가요금이 없어지고 감액을 받아" in penalty
    assert "올리면" in conclusion(92.0, 97.0)
    assert "올릴 여지가 없습니다" in conclusion(97.0, 97.0)


def test_태양광_괄호가_면적과_한계를_나란히_놓지_않는다() -> None:
    """**2,038 kWp 를 지을 수 있는 것처럼 읽혔다** (53세션 4-12)."""
    from kwise.report.document import _solar_conclusion

    class _Point:
        capacity_kwp = 160.0
        generation_kwh = 194_078.0
        total_saving_won = 30_647_000.0
        surplus_kwh = 23_416.0

    point = _Point()
    ample = _solar_conclusion(point, 2_038.0, 2_000.0)  # type: ignore[arg-type]
    assert "설치 가능 면적 2,000 m² 가 허용하는 상한입니다" in ample
    assert "전부 자가소비됩니다" in ample
    assert "2,038" not in ample, "참고값을 실제 상한과 나란히 놓지 않는다"

    tight = _solar_conclusion(point, 40.0, 2_000.0)  # type: ignore[arg-type]
    assert "잉여 없이 지을 수 있는 것은 40 kWp 까지입니다" in tight
    assert "23,416 kWh 가 남습니다" in tight
    # 잉여 장이 뒤따르면 그 수는 다음 장이 맡는다.
    follows = _solar_conclusion(point, 40.0, 2_000.0, surplus_page_follows=True)  # type: ignore[arg-type]
    assert "23,416" not in follows, follows


def test_ESS_표식의_뜻이_표_아래에_있다() -> None:
    """**대형 표에 「마진 미달」 이 셋인데 뜻이 어디에도 없었다** (53세션 4-13)."""
    from kwise.measures import MARGIN_SHORT, SHORTEST_PAYBACK, TARGET_MISSED, spec_mark_note

    note = spec_mark_note([SHORTEST_PAYBACK, MARGIN_SHORT, MARGIN_SHORT, ""])
    assert note.startswith("표식 — ")
    assert SHORTEST_PAYBACK in note and MARGIN_SHORT in note
    # **표에 없는 표식은 설명하지 않는다.**
    assert TARGET_MISSED not in note, note
    # 값이 붙은 표식도 알아본다.
    assert TARGET_MISSED in spec_mark_note([f"{TARGET_MISSED} (실제 5,264 kW)"])
    assert spec_mark_note([]) == ""


def test_조합_문장이_수단_수로_갈린다() -> None:
    """**겹칠 것이 없으면 겹침을 설명하지 않는다** (53세션 4-14)."""
    from types import SimpleNamespace

    from kwise.report.narrative import COMBINATION_LEAD, SINGLE_MEASURE_LEAD, combination_lead

    assert combination_lead(None) == COMBINATION_LEAD
    assert combination_lead(SimpleNamespace(combinations=(1, 2))) == SINGLE_MEASURE_LEAD
    assert combination_lead(SimpleNamespace(combinations=(1, 2, 3))) == COMBINATION_LEAD


def _peak_stub(
    values: dict[str, float], *, demand_months: tuple[int, ...] = (7, 8, 9, 12, 1, 2)
) -> object:
    """월별 최대수요만 채운 진단 대역. **갈래 시험은 계산이 아니라 문장을 본다.**"""
    from types import SimpleNamespace

    import pandas as pd

    index = pd.PeriodIndex(list(values), freq="M")
    frame = pd.DataFrame(
        {"max_demand_kw": list(values.values()), "demand_basis_kw": list(values.values())},
        index=index,
    )
    return SimpleNamespace(
        peak=SimpleNamespace(monthly=frame, demand_months=demand_months, top_n=100)
    )


def _quality_stub(flagged: tuple[str, ...]) -> object:
    from types import SimpleNamespace

    import pandas as pd

    return SimpleNamespace(
        flagged_months=tuple(
            SimpleNamespace(month=pd.Period(month, freq="M")) for month in flagged
        )
    )


def test_5장이_그림과_같은_것을_말하고_세_갈래를_탄다(tariff: TariffTable) -> None:
    """**문장이 월별 최대수요를 말한다** (53세션 4-3).

    39세션은 여기에 정오 비중 판정을 적었는데 그림은 월별 최대수요였다.
    """
    from kwise.report.narrative import peak_month_lead

    # 기본 — 대상월이고 다음 달과 벌어져 있다.
    alone = peak_month_lead(
        _peak_stub({"2023-08": 5_300.0, "2023-07": 4_000.0}), None, tariff
    )
    assert alone == "8월에 최대수요가 가장 높고, 이 시기에 요금적용전력이 결정됩니다."

    # ③ 다음 달과 차이가 작으면 계절로 적는다.
    season = peak_month_lead(
        _peak_stub({"2023-08": 5_300.0, "2023-07": 5_290.0}), None, tariff
    )
    assert season.startswith("여름(6~8월)에 최대수요가 높고"), season
    # **한 해를 넘어가는 계절도 사람이 읽는 대로 적는다.**
    winter = peak_month_lead(
        _peak_stub({"2023-12": 6_100.0, "2024-01": 6_090.0}), None, tariff
    )
    assert winter.startswith("겨울(11~2월)"), winter

    # ① 대상월이 아니면 이월되지 않는다 — 실제로 정한 달을 함께 적는다.
    spring = peak_month_lead(
        _peak_stub({"2023-05": 5_300.0, "2023-08": 4_000.0}), None, tariff
    )
    assert "5월에 최대수요가 가장 높으나" in spring
    assert "요금적용전력은 8월 값으로 결정됩니다" in spring, spring

    # ② 신뢰 제한 달은 근거로 쓰지 않는다.
    limited = peak_month_lead(
        _peak_stub({"2023-08": 5_300.0, "2023-07": 4_000.0}),
        _quality_stub(("2023-08",)),
        tariff,
    )
    assert "결측이 많아 신뢰가 제한됩니다" in limited
    assert "결측이 적은 달 가운데는 7월이 가장 높습니다" in limited, limited

    # 값이 없으면 지어내지 않는다.
    assert "산출하지 못했습니다" in peak_month_lead(_peak_stub({}), None, tariff)


def test_DR_문장이_날_수로_세_갈래다(sample_diagnosis: Diagnosis) -> None:
    """**「245일 가운데 245일만」 은 성립하지 않는다** (53세션 4-10).

    C3 평탄형에서 거래 가능일이 전부 저부하로 잡힌다 — 「만」 은 적다는 뜻이라
    사실과 반대로 읽혔다.
    """
    from dataclasses import replace

    from kwise.report.narrative import dr_lead

    profile = sample_diagnosis.dr
    assert profile is not None
    assert dr_lead(None) == ""
    assert "내려오는 평일이 없어" in dr_lead(replace(profile, low_load_days=()))
    days = tuple(range(profile.eligible_days))
    assert "전부가 부하가 쉬는 날" in dr_lead(replace(profile, low_load_days=days))  # type: ignore[arg-type]
    assert "일만 부하가 쉬는 날" in dr_lead(replace(profile, low_load_days=days[:2]))  # type: ignore[arg-type]
