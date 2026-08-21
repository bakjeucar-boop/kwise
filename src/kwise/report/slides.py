"""PPT 보고서 (36세션).

Word 다섯 장을 **옮기는 것이 아니라 구조를 다시 짠다.** 문서의 절과 슬라이드
한 장은 담는 양이 다르다 — 글은 줄이고 그림을 키운다. **한 장에 한 메시지다.**

    표지 · 목차
    건물현황 및 계약정보 · 전력사용현황 · 부하패턴 및 피크특성 · 피크특성
    현재 요금 구조 · 개선안별 요약
    검토한 수단별 1장씩 (**켠 수단만.** 0개면 그 장이 통째로 빠진다)
    조합구성 및 합산효과 · 부록 산출근거 상세

**색·크기는 여기서 정하지 않는다.** 전부 ``data\\ppt_design.json`` 에서 오고
:mod:`kwise.report.design` 이 읽는다 (36세션 3절). 그림 png 도 같은 팔레트를
쓴다 (:func:`kwise.report.figures.chart_palette`).

**재료는 Word 와 같은 :class:`~kwise.report.document.DocumentSections` 다.** 두
산출물이 같은 값을 봐야 나란히 놓았을 때 어긋나지 않는다. 다른 것은 무엇을
싣느냐뿐이다 — PPT 부록은 **산출 근거만** 담는다 (36세션 6절).

**레이아웃을 섞는다** (36세션 3-4). 카드 기반으로만 만들지 않는다 — 표·리스트·
여백만인 슬라이드를 함께 둔다. :data:`SlideSpec.layout` 이 그 형태이고,
시험이 형태별 개수를 센다.

파일명에 날짜·시각 접미사를 붙인다 — PowerPoint 가 파일을 열고 있으면
덮어쓰기가 실패한다 (Excel·Word 와 같은 이유).
"""

from __future__ import annotations

import datetime as dt
import io
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.parts.image import Image
from pptx.presentation import Presentation as PresentationType
from pptx.slide import Slide
from pptx.util import Emu, Inches, Pt

from kwise import money
from kwise.diagnose import ChargeStructure
from kwise.report import figures
from kwise.report.design import DesignGuide, load_design_guide
from kwise.report.document import DocumentSections, MeasureEntry
from kwise.report.notices import NOT_INCLUDED_NOTICE, TRUNCATION_FOOTNOTE
from kwise.report.worksheet import COLUMNS
from kwise.tariff.labels import SEASON_LABELS

__all__ = [
    "APPENDIX_SLIDE_TITLE",
    "DECK_TITLE",
    "DEFAULT_OUTPUT_DIR",
    "LAYOUTS",
    "SLIDE_TITLES",
    "SlideSpec",
    "agenda_items",
    "build_slides",
    "export_slides",
    "season_pairs",
    "slide_specs",
    "slides_bytes",
    "slides_path",
]

DECK_TITLE = "전력 비용 진단 보고서"
DEFAULT_OUTPUT_DIR = Path("output")
APPENDIX_SLIDE_TITLE = "부록 산출근거 상세"

#: 슬라이드 제목 — **지시서의 괄호 하나가 한 장이다** (36세션 2절).
SLIDE_TITLES: dict[str, str] = {
    "cover": DECK_TITLE,
    "agenda": "목차",
    "building": "건물현황 및 계약정보",
    "usage": "전력사용현황",
    "load_pattern": "부하패턴 및 피크특성",
    "peak": "피크특성",
    "structure": "현재 요금 구조",
    "measure_summary": "개선안별 요약",
    "combination": "조합구성 및 합산효과",
    "appendix": APPENDIX_SLIDE_TITLE,
}

#: 레이아웃 형태 (36세션 3-4). **같은 형태를 반복하지 않는다.**
#:
#:     cover        전체 다크 배경 밴드 — 샌드위치의 바깥
#:     agenda       리스트형. 구분선으로 나눈다
#:     table        표형. **구분선 기반이고 카드가 아니다**
#:     chart        그림 한 장을 크게. 글은 캡션 한 줄
#:     stat_chart   통계 강조형 — 지표 + 차트
#:     chart_pair   같은 크기의 그림 둘을 좌우로
#:     split        좌우가 서로 다른 것을 담는다 (지표+차트 | 격자)
#:     compare      비교형 — 좌우 2단에 중앙 얇은 구분선
LAYOUTS: tuple[str, ...] = (
    "cover",
    "agenda",
    "table",
    "chart",
    "stat_chart",
    "chart_pair",
    "split",
    "compare",
)

_UNPRICED = "미산출"

#: 글자 하나가 차지하는 폭 — 글꼴 크기에 대한 비율.
#:
#: **한글은 전각, 숫자·영문은 반각에 가깝다.** 정확한 값은 글꼴이 쥐고 있어
#: 여기서 알 수 없으므로 **넉넉히 잡는다** — 잘못 잡으면 큰 글씨가 두 줄로
#: 넘쳐 아래 그림을 덮는다. 좁게 잡아 빈자리가 남는 편이 낫다.
_WIDE_GLYPH = 1.0
_NARROW_GLYPH = 0.6


def _text_width_in(text: str, size_pt: float) -> float:
    """글자열이 차지할 대략의 폭 (in)."""
    units = sum(_WIDE_GLYPH if ord(char) > 0x2000 else _NARROW_GLYPH for char in text)
    return units * size_pt / 72.0


def _fitting_size(values: Sequence[str], *, span: float, ladder: Sequence[float]) -> float:
    """칸 폭에 **한 줄로** 들어가는 가장 큰 글자 크기. 없으면 사다리의 끝이다."""
    for size in ladder:
        if all(_text_width_in(value, size) <= span for value in values):
            return size
    return ladder[-1]


@dataclass(frozen=True)
class SlideSpec:
    """슬라이드 한 장의 자리표.

    **덱은 이 목록대로 만들어진다.** 구성과 실물이 갈라지지 않게 하려면 차례를
    쥔 자리가 하나여야 한다 — 시험도 이것을 본다.
    """

    key: str
    title: str
    layout: str
    measure: int | None = None
    """수단별 장이면 :attr:`DocumentSections.measures` 의 자리."""


def slide_specs(sections: DocumentSections) -> tuple[SlideSpec, ...]:
    """덱의 차례 (36세션 2절).

    **수단을 하나도 켜지 않으면 수단별 장만 빠진다.** 나머지는 그대로다 —
    진단만 보고 받아 가는 것이 정상 경로다 (Word 와 같은 규약).
    """
    specs = [
        SlideSpec("cover", SLIDE_TITLES["cover"], "cover"),
        SlideSpec("agenda", SLIDE_TITLES["agenda"], "agenda"),
        SlideSpec("building", SLIDE_TITLES["building"], "table"),
        SlideSpec("usage", SLIDE_TITLES["usage"], "chart"),
        SlideSpec("load_pattern", SLIDE_TITLES["load_pattern"], "stat_chart"),
        SlideSpec("peak", SLIDE_TITLES["peak"], "chart_pair"),
        SlideSpec("structure", SLIDE_TITLES["structure"], "split"),
        SlideSpec("measure_summary", SLIDE_TITLES["measure_summary"], "table"),
    ]
    specs.extend(
        SlideSpec(f"measure_{entry.kind.key}", entry.title, "stat_chart", measure=index)
        for index, entry in enumerate(sections.measures)
    )
    specs.append(SlideSpec("combination", SLIDE_TITLES["combination"], "compare"))
    specs.append(SlideSpec("appendix", SLIDE_TITLES["appendix"], "table"))
    return tuple(specs)


def agenda_items(sections: DocumentSections) -> tuple[str, ...]:
    """목차에 적을 줄. **수단별 장은 한 줄로 묶는다.**

    슬라이드마다 한 줄을 적으면 수단이 일곱일 때 목차가 열두 줄이 된다 — 목차는
    어디를 보는지 알려 주는 자리이지 슬라이드 색인이 아니다.
    """
    lines = [
        SLIDE_TITLES["building"],
        SLIDE_TITLES["usage"],
        SLIDE_TITLES["load_pattern"],
        SLIDE_TITLES["peak"],
        SLIDE_TITLES["structure"],
        SLIDE_TITLES["measure_summary"],
    ]
    if sections.measures:
        names = " · ".join(entry.title for entry in sections.measures)
        lines.append(f"검토한 수단별 상세 — {names}")
    lines.append(SLIDE_TITLES["combination"])
    lines.append(SLIDE_TITLES["appendix"])
    return tuple(lines)


def season_pairs(structure: ChargeStructure) -> tuple[tuple[str, str | None], ...]:
    """도넛 넷의 갈래 (36세션 4절). **화면과 같은 차례다.**

    자료에 없는 계절은 두지 않는다 — 빈 원은 「그 계절에 안 썼다」 로 읽힌다
    (화면의 ``season_choices`` 와 같은 규칙이고, 이유도 같다).
    """
    available = {str(key) for key in structure.band_season_kwh.index}
    pairs: list[tuple[str, str | None]] = [("전체", None)]
    pairs.extend((label, key) for key, label in SEASON_LABELS.items() if key in available)
    return tuple(pairs)


# ===================================================================== 그리기 도구
#
# **색·크기를 여기서 정하지 않는다.** 전부 :class:`DesignGuide` 에서 온다.


def _rgb(value: str) -> RGBColor:
    return RGBColor.from_string(value.lstrip("#").upper())


def _apply_font(run: object, guide: DesignGuide, size: float, color: str, *, bold: bool) -> None:
    """글꼴·크기·색. **동아시아 글꼴을 따로 지정해야** PowerPoint 가 한글에 쓴다."""
    font = run.font  # type: ignore[attr-defined]
    name = guide.typography.primary
    font.name = name
    font.size = Pt(size)
    font.bold = bold
    font.color.rgb = _rgb(color)
    rpr = run._r.get_or_add_rPr()  # type: ignore[attr-defined]
    for tag in ("a:ea", "a:cs"):
        element = rpr.find(qn(tag))
        if element is None:
            element = rpr.makeelement(qn(tag), {})
            rpr.append(element)
        element.set("typeface", name)


def _text(
    slide: Slide,
    guide: DesignGuide,
    lines: Sequence[str],
    *,
    left: float,
    top: float,
    width: float,
    height: float,
    size: float,
    color: str,
    bold: bool = False,
    align: PP_ALIGN = PP_ALIGN.LEFT,
    anchor: MSO_ANCHOR = MSO_ANCHOR.TOP,
    spacing: float = 1.25,
) -> None:
    """텍스트 상자 하나. **본문은 항상 좌측 정렬이다** (36세션 3-3).

    높이에 :meth:`~kwise.report.design.SlideGeometry.slack` 만큼 여유를 둔다 —
    렌더러마다 글자 폭이 달라 딱 맞춘 상자는 다른 PowerPoint 에서 넘친다.
    """
    box = slide.shapes.add_textbox(
        Inches(left), Inches(top), Inches(width), Inches(guide.slide.slack(height))
    )
    frame = box.text_frame
    frame.word_wrap = True
    frame.vertical_anchor = anchor
    frame.margin_left = frame.margin_right = 0
    frame.margin_top = frame.margin_bottom = 0
    for index, line in enumerate(lines):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.alignment = align
        paragraph.line_spacing = spacing
        run = paragraph.add_run()
        run.text = line
        _apply_font(run, guide, size, color, bold=bold)


def _line(
    slide: Slide,
    guide: DesignGuide,
    *,
    left: float,
    top: float,
    right: float,
    bottom: float,
    color: str,
    width_pt: float | None = None,
) -> None:
    """얇은 선 하나. **테마 효과를 뗀다.**

    ``add_connector`` 는 테마의 ``effectRef`` 를 달고 나온다 — 옅은 그림자다.
    가이드가 무거운 그림자를 금지하므로(3-5) 애초에 효과를 걸지 않는다.
    """
    line = slide.shapes.add_connector(
        MSO_CONNECTOR.STRAIGHT, Inches(left), Inches(top), Inches(right), Inches(bottom)
    )
    style = line._element.find(qn("p:style"))
    if style is not None:
        line._element.remove(style)
    line.line.color.rgb = _rgb(color)
    line.line.width = Pt(width_pt if width_pt is not None else guide.slide.rule_pt)


def _rule(
    slide: Slide,
    guide: DesignGuide,
    *,
    left: float,
    top: float,
    length: float,
    color: str,
    width_pt: float | None = None,
) -> None:
    """가로 구분선. **슬라이드 폭을 가로지르는 색 바가 아니다** (36세션 3-5).

    표와 리스트의 줄을 가르는 얇은 선이며, 제목 아래에는 긋지 않는다.
    """
    _line(
        slide,
        guide,
        left=left,
        top=top,
        right=left + length,
        bottom=top,
        color=color,
        width_pt=width_pt,
    )


def _vrule(
    slide: Slide, guide: DesignGuide, *, left: float, top: float, length: float, color: str
) -> None:
    """세로 구분선. 비교형의 **중앙 얇은 선**과 지표 사이를 가른다 (36세션 3-4)."""
    _line(slide, guide, left=left, top=top, right=left, bottom=top + length, color=color)


def _title(slide: Slide, guide: DesignGuide, text: str) -> float:
    """슬라이드 제목 하나. **큰 타이틀은 한 장에 하나다** (36세션 3-3).

    **아래에 강조선을 긋지 않는다** (3-5). 돌려주는 값은 본문이 시작할 y 다.
    """
    geometry = guide.slide
    height = 0.62
    _text(
        slide,
        guide,
        [text],
        left=geometry.margin_in,
        top=geometry.margin_in,
        width=geometry.content_width_in,
        height=height,
        size=guide.type_scale.slide_title,
        color=guide.colors.ink,
        bold=True,
    )
    return geometry.margin_in + height + geometry.title_gap_in


def _caption(
    slide: Slide, guide: DesignGuide, text: str, *, left: float, top: float, width: float
) -> None:
    _text(
        slide,
        guide,
        [text],
        left=left,
        top=top,
        width=width,
        height=0.22,
        size=guide.type_scale.caption,
        color=guide.colors.muted,
    )


#: 캡션 한 줄이 차지하는 높이 (in). 그림 덩어리의 높이 계산에 함께 든다.
_CAPTION_HEIGHT = 0.32


def _aspect(png: bytes) -> float:
    """그림의 세로÷가로. **꾸러미에 넣지 않고** 바이트에서 읽는다."""
    width, height = Image.from_blob(png).size
    return height / width if width else 1.0

# ===================================================================== 그림 비율
#
# **Word 의 그림 비율을 그대로 쓰지 않는다.** 9:3.6 은 문서 한 단(6.3in)에 맞춘
# 것이라 16:9 슬라이드의 반 칸에 넣으면 위아래가 통째로 남는다. 슬라이드가
# 자기 칸에 맞는 크기를 **부를 때 정해서** 넘긴다 (:mod:`kwise.report.figures`
# 의 ``size``). 값은 칸의 가로세로에서 나온 것이라 여기 둔다.

#: 반 폭 칸(좌우 2단)에 들어갈 그림.
HALF_FIGURE = (6.0, 4.4)

#: 반 폭이되 **범례가 바깥에 붙는** 그림. 범례가 폭을 늘려 비율이 납작해지므로
#: 그만큼 미리 좁혀 잡는다 — 그러지 않으면 옆 그림보다 작게 앉는다.
HALF_FIGURE_WITH_LEGEND = (5.4, 4.6)

#: 좌우 2단의 넓은 쪽.
WIDE_FIGURE = (7.2, 4.4)


def _picture(
    slide: Slide,
    png: bytes,
    *,
    left: float,
    top: float,
    width: float,
    height: float,
) -> float:
    """그림을 상자 안에 **비율을 지켜** 앉힌다. 돌려주는 값은 그림의 아랫변이다.

    **모서리를 둥글리지 않는다** (36세션 3-5). 라운드를 준다면 0.08in 이상이라야
    하는데, 그만한 라운드는 이 크기의 차트에서 축 눈금을 갉아먹는다.
    """
    stream = io.BytesIO(png)
    picture = slide.shapes.add_picture(stream, Inches(left), Inches(top))
    ratio = picture.height / picture.width
    if width * ratio <= height:
        picture.width = Emu(int(Inches(width)))
        picture.height = Emu(int(Inches(width) * ratio))
    else:
        picture.height = Emu(int(Inches(height)))
        picture.width = Emu(int(Inches(height) / ratio))
    picture.left = Emu(int(Inches(left) + (Inches(width) - picture.width) / 2))
    picture.top = Emu(int(top * 914_400))
    return top + picture.height / 914_400


def _picture_block(
    slide: Slide,
    guide: DesignGuide,
    png: bytes,
    caption: str,
    *,
    left: float,
    top: float,
    width: float,
    height: float,
) -> None:
    """그림과 캡션을 **한 덩어리로 남는 높이 가운데** 앉힌다.

    차트는 가로로 길어서 폭이 먼저 차고, 그러면 아래에 빈 띠가 남는다. 위로
    붙여 두면 슬라이드가 덜 만들어진 것처럼 보인다 — 남는 여백을 위아래로
    나누면 **여백이 자리가 된다.**
    """
    # **넣어 보고 지우지 않는다.** ``add_picture`` 는 그림 파트를 꾸러미에 넣고
    # 관계를 맺는다 — 도형만 지우면 그 파트가 남아 파일이 두 배로 커진다.
    # 비율은 바이트에서 바로 읽는다.
    ratio = _aspect(png)
    room = height - _CAPTION_HEIGHT
    drawn_width = min(width, room / ratio) if ratio else width
    drawn_height = drawn_width * ratio
    offset = max(0.0, (room - drawn_height) / 2)
    _picture(
        slide,
        png,
        left=left + (width - drawn_width) / 2,
        top=top + offset,
        width=drawn_width,
        height=drawn_height,
    )
    # **캡션은 칸의 밑변에 붙인다.** 그림 바로 밑에 두면 좌우로 나란한 두 그림의
    # 캡션이 서로 다른 높이에 앉는다 — 그림 높이가 범례 유무로 갈리기 때문이다.
    _caption(slide, guide, caption, left=left, top=top + height - _CAPTION_HEIGHT, width=width)


#: 표에 쓸 스타일 — **테두리도 띠도 없는 것** (36세션 3-4). 줄은 우리가 긋는다.
_PLAIN_TABLE_STYLE = "{2D5ABB26-0587-4C30-8999-92F81FD0307C}"


def _table(
    slide: Slide,
    guide: DesignGuide,
    rows: Sequence[Sequence[str]],
    *,
    left: float,
    top: float,
    width: float,
    height: float,
    widths: Sequence[float] | None = None,
) -> None:
    """표형 슬라이드의 표 — **구분선 기반이고 카드가 아니다** (36세션 3-4).

    칸을 칠하지 않고 줄 밑에만 얇은 선을 둔다. 기본 표 스타일은 띠를 칠하므로
    **꺼서 넣는다** — 칠한 띠는 카드와 같은 무게를 갖고, 가이드가 금지한
    「같은 형태의 반복」이 표에서 되살아난다.

    표 자체가 이 슬라이드의 **시각 요소**다 (36세션 5절).
    """
    frame = slide.shapes.add_table(
        len(rows), len(rows[0]), Inches(left), Inches(top), Inches(width), Inches(height)
    )
    table = frame.table
    properties = table._tbl.find(qn("a:tblPr"))
    properties.set("firstRow", "0")
    properties.set("bandRow", "0")
    style = properties.find(qn("a:tableStyleId"))
    if style is None:
        style = properties.makeelement(qn("a:tableStyleId"), {})
        properties.append(style)
    style.text = _PLAIN_TABLE_STYLE

    if widths is not None:
        # ``_ColumnCollection`` 은 Iterable 로 선언되어 있지 않아 zip 이 물린다.
        for index, share in enumerate(widths):
            table.columns[index].width = Emu(int(Inches(width) * share))

    scale = guide.type_scale
    colors = guide.colors
    for row_index, row in enumerate(rows):
        table.rows[row_index].height = Inches(guide.slide.slack(height / len(rows)))
        for col_index, value in enumerate(row):
            cell = table.cell(row_index, col_index)
            cell.fill.background()
            cell.margin_left = cell.margin_right = Inches(0.06)
            cell.margin_top = cell.margin_bottom = Inches(0.04)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            paragraph = cell.text_frame.paragraphs[0]
            paragraph.alignment = PP_ALIGN.LEFT
            run = paragraph.add_run()
            run.text = str(value)
            head = row_index == 0
            _apply_font(
                run,
                guide,
                scale.caption if not head else scale.caption + 0.5,
                colors.muted if head else colors.ink,
                bold=head,
            )
            _cell_rule(cell, colors.rule if not head else colors.ink, guide.slide.rule_pt)


def _cell_rule(cell: object, color: str, width_pt: float) -> None:
    """칸 **아래 줄만** 긋는다. 격자를 두르면 표가 상자가 된다.

    **``a:lnB`` 는 ``a:tcPr`` 의 맨 앞에 와야 한다.** DrawingML 이 자식 차례를
    강제해서(테두리 → 채우기 순), 채우기 뒤에 붙이면 PowerPoint 가 조용히
    무시한다 — 줄이 안 그려지는데 파일은 멀쩡히 열린다.
    """
    properties = cell._tc.get_or_add_tcPr()  # type: ignore[attr-defined]
    existing = properties.find(qn("a:lnB"))
    if existing is not None:
        properties.remove(existing)
    element = properties.makeelement(
        qn("a:lnB"), {"w": str(int(width_pt * 12_700)), "cap": "flat", "cmpd": "sng"}
    )
    fill = element.makeelement(qn("a:solidFill"), {})
    srgb = fill.makeelement(qn("a:srgbClr"), {"val": color.lstrip("#").upper()})
    fill.append(srgb)
    element.append(fill)
    properties.insert(0, element)


def _stats(
    slide: Slide,
    guide: DesignGuide,
    items: Sequence[tuple[str, str]],
    *,
    left: float,
    top: float,
    width: float,
) -> float:
    """통계 강조 — **큰 수와 이름, 그 사이를 세로선이 가른다** (36세션 3-4).

    **카드로 만들지 않는다.** 칠한 상자를 늘어놓으면 슬라이드마다 같은 형태가
    되고, 모서리 색 띠는 가이드가 금지한다 (3-5). 돌려주는 값은 아랫변이다.
    """
    if not items:
        return top
    scale = guide.type_scale
    colors = guide.colors
    span = width / len(items)
    # **칸에 안 들어가면 글자를 줄인다.** 「28억 9,828만원」·「1,234,000원 (12개월
    # 환산 …)」 처럼 늘어지는 값이 있는데, 큰 글씨 그대로 두면 두 줄로 넘쳐 아래
    # 그림과 겹친다. 길이만 보고 정하면 칸 수에 따라 또 넘친다 — **폭으로 잰다.**
    size = _fitting_size(
        [value for _label, value in items],
        span=span - 0.24,
        ladder=(scale.card_title, scale.body + 2, scale.body),
    )
    block = 0.86 if size >= scale.card_title else 0.94
    for index, (label, value) in enumerate(items):
        x = left + span * index
        _text(
            slide,
            guide,
            [label],
            left=x + (0.16 if index else 0.0),
            top=top,
            width=span - 0.24,
            height=0.22,
            size=scale.caption,
            color=colors.muted,
        )
        _text(
            slide,
            guide,
            [value],
            left=x + (0.16 if index else 0.0),
            top=top + 0.26,
            width=span - 0.24,
            height=block - 0.32,
            size=size,
            color=colors.ink,
            bold=True,
        )
        if index:
            _vrule(slide, guide, left=x, top=top, length=block - 0.08, color=colors.rule)
    return top + block


# ===================================================================== 값 다듬기


def _won(value: float | None, *, reason: str | None = None) -> str:
    if value is None:
        return reason if reason is not None else _UNPRICED
    return money.won_short(value, reason=_UNPRICED)


def _payback(years: float | None, investment_won: float | None) -> str:
    if investment_won is None:
        return _UNPRICED
    if not investment_won:
        return "즉시"
    return f"{years:,.1f}년" if years is not None else _UNPRICED


def _pct(value: float | None) -> str:
    return f"{value * 100:,.1f}%" if value is not None else "—"


# ===================================================================== 장별


def _build_cover(
    slide: Slide, guide: DesignGuide, sections: DocumentSections, _spec: SlideSpec
) -> None:
    """표지 — **전체 배경 밴드가 다크다** (36세션 3-2).

    딥그린은 여기와 같은 **전체 배경**으로만 쓴다. 슬라이드 폭을 가로지르는
    색 바로 쓰면 3-5 가 금지한 그것이 된다.
    """
    geometry = guide.slide
    colors = guide.colors
    scale = guide.type_scale
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = _rgb(colors.cover)

    # 표지의 시각 요소 — **작은 포인트 하나.** 코랄은 이런 자리에만 쓴다 (3-2).
    # 슬라이드를 가로지르지 않는 짧은 표식이라 3-5 의 「색 바」 와 다르다.
    left = geometry.margin_in + 0.5
    _rule(slide, guide, left=left, top=2.86, length=0.9, color=colors.coral, width_pt=3.0)
    bill = sections.bill
    _text(
        slide,
        guide,
        [DECK_TITLE],
        left=left,
        top=3.14,
        width=geometry.content_width_in - 1.0,
        height=1.0,
        size=scale.cover,
        color=colors.on_dark,
        bold=True,
    )
    _text(
        slide,
        guide,
        [
            sections.building,
            f"{bill.contract_label} {bill.voltage_label} 선택{bill.selection.option}",
            f"분석 기간 {bill.period_start:%Y-%m-%d} ~ {bill.period_end:%Y-%m-%d}"
            f" · 작성일 {sections.prepared:%Y-%m-%d}",
        ],
        left=left,
        top=4.52,
        width=geometry.content_width_in - 1.0,
        height=1.2,
        size=scale.body,
        color=colors.on_dark_muted,
    )


def _build_agenda(
    slide: Slide, guide: DesignGuide, sections: DocumentSections, _spec: SlideSpec
) -> None:
    """목차 — 리스트형. **번호와 구분선이 시각 요소다.**"""
    geometry = guide.slide
    colors = guide.colors
    scale = guide.type_scale
    top = _title(slide, guide, SLIDE_TITLES["agenda"])
    items = agenda_items(sections)
    step = min(0.62, (geometry.height_in - geometry.margin_in - top) / max(len(items), 1))
    for index, line in enumerate(items):
        y = top + step * index
        _text(
            slide,
            guide,
            [f"{index + 1:02d}"],
            left=geometry.margin_in,
            top=y + 0.04,
            width=0.7,
            height=0.32,
            size=scale.body,
            color=colors.coral,
            bold=True,
        )
        _text(
            slide,
            guide,
            [line],
            left=geometry.margin_in + 0.8,
            top=y,
            width=geometry.content_width_in - 0.8,
            height=0.36,
            size=scale.card_title,
            color=colors.ink,
        )
        _rule(
            slide,
            guide,
            left=geometry.margin_in,
            top=y + step - 0.08,
            length=geometry.content_width_in,
            color=colors.rule,
        )


def _build_building(
    slide: Slide, guide: DesignGuide, sections: DocumentSections, spec: SlideSpec
) -> None:
    """건물현황 및 계약정보 — **표형** (36세션 3-4). 카드가 아니다."""
    geometry = guide.slide
    top = _title(slide, guide, spec.title)
    bill = sections.bill
    meta = sections.usage.meta
    quality = sections.diagnosis.quality if sections.diagnosis is not None else None
    rows = [
        ["항목", "내용"],
        ["건물명", sections.building],
        ["계약종별", f"{bill.contract_label} {bill.voltage_label} 선택{bill.selection.option}"],
        [
            "분석 기간",
            f"{bill.period_start:%Y-%m-%d} ~ {bill.period_end:%Y-%m-%d}"
            f" ({bill.period_days:.0f}일 · 기본요금 {bill.base_fee_months:.2f}개월분)",
        ],
        ["검침 간격", f"{meta.interval_minutes}분 · {meta.expected_rows:,}구간"],
        ["총 사용량", f"{meta.total_kwh / 1000:,.1f} MWh"],
        [
            "결측",
            f"{meta.missing_rows:,}구간 ({meta.missing_ratio:.1%}) — 보간하지 않았습니다",
        ],
        ["적용 요금표 시행일", f"{bill.effective_date}"],
        ["작성일", f"{sections.prepared:%Y-%m-%d}"],
    ]
    if quality is not None:
        rows.insert(-2, ["정전 추정", f"{len(quality.outages)}건"])
    _table(
        slide,
        guide,
        rows,
        left=geometry.margin_in,
        top=top,
        width=geometry.content_width_in,
        height=min(5.0, 0.44 * len(rows)),
        widths=(0.26, 0.74),
    )


def _build_usage(
    slide: Slide, guide: DesignGuide, sections: DocumentSections, spec: SlideSpec
) -> None:
    """전력사용현황 — **연간 사용량 그래프 한 장을 크게** (36세션 2절).

    한 장에 한 메시지다. 여기서 말하는 것은 「한 해가 어떻게 오르내리는가」다.
    """
    geometry = guide.slide
    top = _title(slide, guide, spec.title)
    meta = sections.usage.meta
    bottom = _stats(
        slide,
        guide,
        [
            ("총 사용량", f"{meta.total_kwh / 1000:,.0f} MWh"),
            ("최대수요", f"{meta.max_demand_kw:,.0f} kW"),
            ("평균 부하", f"{meta.mean_kw:,.0f} kW"),
            ("관측 기간", f"{meta.period_days:.0f}일"),
        ],
        left=geometry.margin_in,
        top=top,
        width=geometry.content_width_in,
    )
    body = bottom + geometry.block_gap_in
    _picture_block(
        slide,
        guide,
        figures.daily_usage_png(sections.usage),
        "일별 사용량 — 관측이 있는 날만 그렸습니다. 결측일은 0 으로 채우지 않습니다.",
        left=geometry.margin_in,
        top=body,
        width=geometry.content_width_in,
        height=geometry.height_in - geometry.margin_in - body,
    )


def _build_load_pattern(
    slide: Slide, guide: DesignGuide, sections: DocumentSections, spec: SlideSpec
) -> None:
    """부하패턴 및 피크특성 — **통계 강조형** (지표 + 월별 최대수요)."""
    geometry = guide.slide
    top = _title(slide, guide, spec.title)
    diagnosis = sections.diagnosis
    if diagnosis is None:  # pragma: no cover - 진단 없이 부르지 않는다
        return
    pattern = diagnosis.pattern
    peak = diagnosis.peak
    bottom = _stats(
        slide,
        guide,
        [
            ("부하율", _pct(pattern.load_factor)),
            ("기저부하 비율", _pct(pattern.base_load_ratio)),
            ("주말 부하 비율", _pct(pattern.weekend_ratio)),
            ("요금적용전력", f"{peak.billing_demand_kw:,.0f} kW"),
        ],
        left=geometry.margin_in,
        top=top,
        width=geometry.content_width_in,
    )
    body = bottom + geometry.block_gap_in
    _picture_block(
        slide,
        guide,
        figures.monthly_peak_png(peak),
        "월별 최대수요와 요금적용전력 — 경부하 시간대의 피크는 요금적용전력이 되지 않습니다.",
        left=geometry.margin_in,
        top=body,
        width=geometry.content_width_in,
        height=geometry.height_in - geometry.margin_in - body,
    )


def _build_peak(
    slide: Slide, guide: DesignGuide, sections: DocumentSections, spec: SlideSpec
) -> None:
    """피크특성 — **그림 둘을 위아래로.** 시간대별 평균부하와 상위 구간 시각.

    **그림 크기를 칸에 맞춰 부른다** (:data:`HALF_FIGURE`). Word 의 가로 긴
    비율을 그대로 반 칸에 넣으면 눈금이 겹치고 위아래가 남는다.
    """
    geometry = guide.slide
    top = _title(slide, guide, spec.title)
    diagnosis = sections.diagnosis
    if diagnosis is None:  # pragma: no cover
        return
    peak = diagnosis.peak
    gap = geometry.block_gap_in
    half = (geometry.content_width_in - gap) / 2
    height = geometry.height_in - geometry.margin_in - top
    for index, (png, caption) in enumerate(
        (
            (figures.hourly_profile_png(peak, size=HALF_FIGURE), "시간대별 평균 부하"),
            (
                figures.top_hour_png(peak, size=HALF_FIGURE_WITH_LEGEND),
                f"상위 {peak.top_n}구간 발생 시각",
            ),
        )
    ):
        _picture_block(
            slide,
            guide,
            png,
            caption,
            left=geometry.margin_in + (half + gap) * index,
            top=top,
            width=half,
            height=height,
        )


def _build_structure(
    slide: Slide, guide: DesignGuide, sections: DocumentSections, spec: SlideSpec
) -> None:
    """현재 요금 구조 — 월별 요금 막대와 **계시별 도넛 2×2** (36세션 2·4절)."""
    geometry = guide.slide
    top = _title(slide, guide, spec.title)
    diagnosis = sections.diagnosis
    structure = diagnosis.structure if diagnosis is not None else None
    if structure is None:  # pragma: no cover - 계약 정보가 없으면 부르지 않는다
        return
    gap = geometry.block_gap_in
    left_width = geometry.content_width_in * 0.56
    right_width = geometry.content_width_in - left_width - gap
    right_left = geometry.margin_in + left_width + gap
    height = geometry.height_in - geometry.margin_in - top

    base_won = structure.base_won + structure.bill.total_power_factor_won
    chart_top = (
        _stats(
            slide,
            guide,
            [
                ("기본요금", _won(base_won)),
                ("전력량요금", _won(structure.energy_won)),
                (
                    "기본요금 비중",
                    _pct(base_won / structure.total_won if structure.total_won else None),
                ),
            ],
            left=geometry.margin_in,
            top=top,
            width=left_width,
        )
        + gap
    )
    _picture_block(
        slide,
        guide,
        figures.monthly_charge_png(structure, size=WIDE_FIGURE),
        "월별 요금 구성 — 밑단(기본요금)이 같은 높이로 이어집니다.",
        left=geometry.margin_in,
        top=chart_top,
        width=left_width,
        height=geometry.height_in - geometry.margin_in - chart_top,
    )
    _picture_block(
        slide,
        guide,
        figures.band_donut_grid_png(structure, season_pairs(structure)),
        "계시별 사용량 구성 — 비중은 그 계절 안에서 다시 잰 값입니다.",
        left=right_left,
        top=top,
        width=right_width,
        height=height,
    )


def _build_measure_summary(
    slide: Slide, guide: DesignGuide, sections: DocumentSections, spec: SlideSpec
) -> None:
    """개선안별 요약 — **표형.** 켠 수단을 한 자리에 모은다."""
    geometry = guide.slide
    top = _title(slide, guide, spec.title)
    rows = [["개선 수단", "절감액", "투자비", "회수기간", "확실성"]]
    rows.extend(
        [entry.title, entry.saving, entry.investment, entry.payback, entry.certainty]
        for entry in sections.measures
    )
    if len(rows) == 1:
        rows.append(["검토한 수단이 없습니다", "—", "—", "—", "—"])
    _table(
        slide,
        guide,
        rows,
        left=geometry.margin_in,
        top=top,
        width=geometry.content_width_in,
        height=min(4.6, 0.5 * len(rows)),
        widths=(0.22, 0.28, 0.2, 0.15, 0.15),
    )
    _caption(
        slide,
        guide,
        f"{NOT_INCLUDED_NOTICE} {TRUNCATION_FOOTNOTE}",
        left=geometry.margin_in,
        top=geometry.height_in - geometry.margin_in - 0.3,
        width=geometry.content_width_in,
    )


#: 수단 한 장에 실을 주의사항 개수. **슬라이드는 읽는 자리가 아니라 보는 자리다** —
#: 전문은 Word 3장에 그대로 있다.
CAUTION_LIMIT = 3


def _cautions(entry: MeasureEntry) -> tuple[str, ...]:
    """주의사항을 **겹치지 않게** 추리고 개수를 자른다.

    같은 문장이 두 번 실리는 경우가 있다 — 카드가 낸 주의와 안내가 낸 근거가
    같은 말일 때다. 문서에서는 눈에 덜 띄지만 슬라이드에서는 한 화면에 나란히
    놓여 바로 보인다.
    """
    seen: dict[str, None] = {}
    for line in entry.cautions:
        seen.setdefault(line.strip(), None)
    return tuple(seen)[:CAUTION_LIMIT]


def _build_measure(
    slide: Slide, guide: DesignGuide, sections: DocumentSections, spec: SlideSpec
) -> None:
    """수단 한 장 — **차트 + 지표** (통계 강조형).

    **결론 한 줄이 먼저다.** 그림이 없는 수단은 표 대신 주의사항이 자리를 채운다 —
    빈 그림 자리를 남기지 않는다.
    """
    geometry = guide.slide
    colors = guide.colors
    assert spec.measure is not None
    entry: MeasureEntry = sections.measures[spec.measure]
    top = _title(slide, guide, entry.title)
    _text(
        slide,
        guide,
        [entry.conclusion],
        left=geometry.margin_in,
        top=top,
        width=geometry.content_width_in,
        height=0.4,
        size=guide.type_scale.body,
        color=colors.ink,
        bold=True,
    )
    stats_top = top + 0.52
    bottom = _stats(
        slide,
        guide,
        [
            ("절감액", entry.saving),
            ("투자비", entry.investment),
            ("회수기간", entry.payback),
            ("확실성", entry.certainty),
        ],
        left=geometry.margin_in,
        top=stats_top,
        width=geometry.content_width_in,
    )
    body = bottom + geometry.block_gap_in
    height = geometry.height_in - geometry.margin_in - body - 0.32
    if entry.figure is not None:
        _picture_block(
            slide,
            guide,
            entry.figure,
            entry.figure_caption,
            left=geometry.margin_in,
            top=body,
            width=geometry.content_width_in,
            height=height,
        )
        return
    # 그림이 없는 수단(계약전력 조정)은 **주의사항 표**가 시각 요소를 진다.
    rows = [["주의사항"], *[[line] for line in _cautions(entry)]]
    if len(rows) == 1:
        rows.append(["—"])
    _table(
        slide,
        guide,
        rows,
        left=geometry.margin_in,
        top=body,
        width=geometry.content_width_in,
        height=min(height, 0.7 * len(rows)),
    )


def _build_combination(
    slide: Slide, guide: DesignGuide, sections: DocumentSections, spec: SlideSpec
) -> None:
    """조합구성 및 합산효과 — **비교형.** 좌우 2단에 중앙 얇은 구분선 (36세션 3-4).

    왼쪽이 「무엇을 묶었나」, 오른쪽이 「묶으면 얼마인가」다. **조합마다 요금을
    다시 계산한 값이며 수단별 절감액의 단순 합이 아니다.**
    """
    geometry = guide.slide
    colors = guide.colors
    scale = guide.type_scale
    top = _title(slide, guide, spec.title)
    comparison = sections.comparison
    gap = geometry.block_gap_in
    half = (geometry.content_width_in - gap) / 2
    right_left = geometry.margin_in + half + gap
    _vrule(
        slide,
        guide,
        left=geometry.margin_in + half + gap / 2,
        top=top,
        length=geometry.height_in - geometry.margin_in - top - 0.1,
        color=colors.rule,
    )

    if comparison is None:
        _text(
            slide,
            guide,
            ["조합 비교는 계약 정보와 수단이 있어야 산출됩니다."],
            left=geometry.margin_in,
            top=top,
            width=half,
            height=0.4,
            size=scale.body,
            color=colors.ink,
        )
        rows = [["조합", "절감액"], ["—", "—"]]
        _table(
            slide,
            guide,
            rows,
            left=right_left,
            top=top,
            width=half,
            height=1.0,
            widths=(0.6, 0.4),
        )
        return

    best = comparison.best
    _text(
        slide,
        guide,
        ["조합 구성"],
        left=geometry.margin_in,
        top=top,
        width=half,
        height=0.34,
        size=scale.card_title,
        color=colors.ink,
        bold=True,
    )
    # **조합에 실제로 들어간 수단 이름을 그대로 쓴다** — 목록을 여기서 다시
    # 만들면 조합 재계산이 본 것과 갈라진다.
    members = list(best.spec.measure_labels)
    _text(
        slide,
        guide,
        [f"「{best.name}」"] + [f"· {name}" for name in members],
        left=geometry.margin_in,
        top=top + 0.42,
        width=half,
        height=0.3 * (len(members) + 1),
        size=scale.body,
        color=colors.ink,
    )
    table_top = top + 0.5 + 0.3 * (len(members) + 1) + gap
    rows = [["조합", "절감액", "회수기간"]]
    rows.extend(
        [item.name, _won(item.saving_won), _payback(item.payback_years, item.investment_won)]
        for item in comparison.combinations
    )
    _table(
        slide,
        guide,
        rows,
        left=geometry.margin_in,
        top=table_top,
        width=half,
        height=min(
            geometry.height_in - geometry.margin_in - table_top - 0.1,
            0.36 * len(rows),
        ),
        widths=(0.46, 0.3, 0.24),
    )

    _text(
        slide,
        guide,
        ["합산효과"],
        left=right_left,
        top=top,
        width=half,
        height=0.34,
        size=scale.card_title,
        color=colors.ink,
        bold=True,
    )
    stats_bottom = _stats(
        slide,
        guide,
        [
            ("총 절감액", _won(best.saving_won)),
            ("투자비", _won(best.investment_won)),
            ("회수기간", _payback(best.payback_years, best.investment_won)),
        ],
        left=right_left,
        top=top + 0.42,
        width=half,
    )
    chart_top = stats_bottom + gap
    _picture_block(
        slide,
        guide,
        figures.combination_png(comparison),
        "조합마다 요금을 다시 계산했습니다. 수단별 절감액의 단순 합이 아닙니다.",
        left=right_left,
        top=chart_top,
        width=half,
        height=geometry.height_in - geometry.margin_in - chart_top,
    )


#: 부록 한 장에 담을 근거 줄 수. **넘치면 넘친 사실을 적는다** — 조용히 자르면
#: 「이게 전부」 로 읽힌다. 전문은 Excel 부록 A 와 Word 부록 A 에 있다.
APPENDIX_ROW_LIMIT = 12


def _build_appendix(
    slide: Slide, guide: DesignGuide, sections: DocumentSections, spec: SlideSpec
) -> None:
    """부록 산출근거 상세 — **표형.**

    **PPT 부록에는 산출 근거만 싣는다** (36세션 6절). ESS 조달 사례 표, 적용
    기준 데이터(Word 부록 B), 알려진 한계와 전제(Word 부록 C)는 여기 오지
    않는다 — 슬라이드에서 읽히지 않는 분량이고, 셋 다 **Word 에는 그대로
    남아 있다.** 지운 것이 아니라 매체를 가린 것이다.
    """
    geometry = guide.slide
    top = _title(slide, guide, spec.title)
    rows: list[list[str]] = [list(COLUMNS)]
    total = 0
    for sheet in sections.worksheets:
        frame = sheet.frame()
        total += len(frame)
        for record in frame.to_numpy():
            if len(rows) <= APPENDIX_ROW_LIMIT:
                rows.append([str(value) for value in record])
    if len(rows) == 1:
        rows.append(["계산 근거", "수단을 켜면 산식과 대입값이 실립니다", "—"])
    _table(
        slide,
        guide,
        rows,
        left=geometry.margin_in,
        top=top,
        width=geometry.content_width_in,
        height=min(4.8, 0.4 * len(rows)),
        widths=(0.24, 0.5, 0.26),
    )
    omitted = max(0, total - (len(rows) - 1))
    note = "산식과 대입한 값을 나란히 실었습니다."
    if omitted:
        note += f" {omitted}줄은 자리가 모자라 뺐습니다 — 전문은 Excel·Word 부록 A 에 있습니다."
    _caption(
        slide,
        guide,
        note,
        left=geometry.margin_in,
        top=geometry.height_in - geometry.margin_in - 0.3,
        width=geometry.content_width_in,
    )


_BUILDERS: dict[str, Callable[[Slide, DesignGuide, DocumentSections, SlideSpec], None]] = {
    "cover": _build_cover,
    "agenda": _build_agenda,
    "building": _build_building,
    "usage": _build_usage,
    "load_pattern": _build_load_pattern,
    "peak": _build_peak,
    "structure": _build_structure,
    "measure_summary": _build_measure_summary,
    "combination": _build_combination,
    "appendix": _build_appendix,
}


# ===================================================================== 조립


def build_slides(
    sections: DocumentSections, *, guide: DesignGuide | None = None
) -> PresentationType:
    """덱을 만든다. **차례는 :func:`slide_specs` 하나가 쥔다.**"""
    design = guide if guide is not None else load_design_guide()
    presentation = Presentation()
    presentation.slide_width = Inches(design.slide.width_in)
    presentation.slide_height = Inches(design.slide.height_in)
    blank = presentation.slide_layouts[6]
    colors = design.colors

    for spec in slide_specs(sections):
        slide = presentation.slides.add_slide(blank)
        if spec.layout != "cover":
            # **본문 콘텐츠는 라이트다** (36세션 3-2). 샌드위치의 속이다.
            slide.background.fill.solid()
            slide.background.fill.fore_color.rgb = _rgb(colors.white)
        builder = _BUILDERS.get(spec.key, _build_measure)
        builder(slide, design, sections, spec)
    return presentation


def slides_path(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    prefix: str = "kwise_report",
    now: dt.datetime | None = None,
) -> Path:
    """**날짜·시각 접미사를 붙인다.** PowerPoint 가 열고 있으면 덮어쓰기가 실패한다."""
    stamp = (now if now is not None else dt.datetime.now()).strftime("%Y%m%d_%H%M")
    return output_dir / f"{prefix}_{stamp}.pptx"


def export_slides(
    sections: DocumentSections,
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    prefix: str = "kwise_report",
    now: dt.datetime | None = None,
) -> Path:
    """파일로 쓴다."""
    path = slides_path(output_dir, prefix=prefix, now=now)
    path.parent.mkdir(parents=True, exist_ok=True)
    build_slides(sections).save(str(path))
    return path


def slides_bytes(
    sections: DocumentSections, *, now: dt.datetime | None = None, prefix: str = "kwise_report"
) -> tuple[bytes, str]:
    """내려받기용 바이트와 파일명. **디스크에 남기지 않는다** (10.2)."""
    buffer = io.BytesIO()
    build_slides(sections).save(buffer)
    return buffer.getvalue(), slides_path(Path(), prefix=prefix, now=now).name
