"""문서 생성 시험 (요구사항서 13장).

**여기서 지키는 것 다섯**

    ① md 두 개에서 html 두 개가 나온다
    ② html 이 **단일 파일이다** — 바깥 이미지·스타일·스크립트를 참조하지 않는다
    ③ 목차 링크가 실제 절로 연결된다 (죽은 링크가 없다)
    ④ 앵커 30개가 매뉴얼에 모두 있다
    ⑤ 화면의 [자세히] 링크가 살아나고 그 앵커가 매뉴얼과 일치한다
    ⑥ **커밋된 html 이 원본과 같다** (74세션) — 나머지는 임시 폴더에 새로
      만들어 보므로 **만드는 쪽만 지켰다.** 저장소의 것은 아무도 안 봤고,
      그래서 둘이 **S58 판으로 열네 세션**을 서 있었다
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from kwise.docsite import (
    DEFAULT_DOCS,
    TOC_LEVELS,
    build_all,
    build_page,
    collect_anchors,
    render_html,
    render_markdown,
    slugify,
)
from kwise.ui.anchors import ANCHORS, MANUAL_FILENAME, anchor_keys, manual_tip

DOCS = Path("docs")
SOURCES = ("TECHNICAL.md", "MANUAL.md")
TARGETS = ("TECHNICAL.html", "MANUAL.html")


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """원본을 임시 폴더로 옮겨 새로 만든다. 저장소 산출물을 건드리지 않는다."""
    workspace = tmp_path_factory.mktemp("docs")
    for name in SOURCES:
        (workspace / name).write_text((DOCS / name).read_text(encoding="utf-8"), encoding="utf-8")
    build_all(workspace)
    return workspace


# ===================================================================== ① 생성


def test_원본_두_개가_있다() -> None:
    for name in SOURCES:
        assert (DOCS / name).is_file(), f"{name} 이 없습니다."


def test_md_두_개에서_html_두_개가_나온다(built: Path) -> None:
    for name in TARGETS:
        target = built / name
        assert target.is_file()
        assert target.stat().st_size > 10_000


def test_저장소에도_생성물이_있다() -> None:
    """``tools\\build_docs.py`` 를 돌린 결과가 커밋되어 있어야 한다."""
    for name in TARGETS:
        assert (DOCS / name).is_file(), f"{name} — tools\\build_docs.py 를 실행하십시오."


def test_저장소의_생성물이_원본과_같다() -> None:
    """**있는지가 아니라 최신인지를 본다** (74세션 1절).

    바로 위 시험은 파일이 **있는지**만 봤다. 그래서 `docs\\*.html` 둘이
    **S58 판으로 열네 세션**을 서 있었는데 아무것도 말하지 않았다 —
    S61·S63·S66 이 원본만 고치고 `build_docs.py` 를 안 돌렸고, S73 이
    다른 일로 그것을 돌리다가 알았다. **결함 유형 ⑤** — 시험은 통과하는데
    실물이 낡아 있었다.

    이 파일의 다른 시험들은 **임시 폴더에 새로 만들어** 본다("저장소 산출물을
    건드리지 않는다"). 곧 **만드는 쪽은 지키는데 커밋된 것은 아무도 안 봤다.**

    **장치는 `test_앵커_문서가_정본과_같다` 를 옮겨 썼다** — 다시 만들어
    전문을 대조하고, 어긋나면 **무엇을 돌려야 하는지** 실패 메시지가 말한다.

    **전문을 그대로 맞대도 된다.** :func:`render_html` 은 원본 글·제목·
    묻어 둔 스타일·스크립트만으로 글을 짓는다 — 생성 시각도, 경로도, 환경도
    안 들어간다. 그래서 같은 원본이면 언제 어디서 돌려도 같은 글이 나온다.
    """
    for source, target, title in DEFAULT_DOCS:
        built_now, _headings = render_html((DOCS / source).read_text(encoding="utf-8"), title=title)
        # **`assert` 로 맞대지 않는다.** 어긋나면 pytest 가 html 전문을 줄줄이
        # 펴는데(심어 보니 577줄), 이 실패는 **손으로 고치는 것이 아니라 도구를
        # 돌려 고치는 것**이라 그 diff 를 아무도 읽지 않는다. 할 일만 낸다.
        if (DOCS / target).read_text(encoding="utf-8") != built_now:
            pytest.fail(
                f"{target} 이 {source} 보다 낡았습니다 — tools\\build_docs.py 를 실행하십시오."
            )


def test_원본이_없으면_만들지_않고_실패한다(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        build_page(tmp_path / "없음.md", tmp_path / "없음.html", "제목")


def test_문서_목록이_둘이다() -> None:
    assert len(DEFAULT_DOCS) == 2
    assert {item[0] for item in DEFAULT_DOCS} == set(SOURCES)


# ===================================================================== ② 단일 파일


@pytest.mark.parametrize("name", TARGETS)
def test_바깥_자원을_참조하지_않는다(built: Path, name: str) -> None:
    """**html 하나만 옮겨도 그대로 열려야 한다.**"""
    text = (built / name).read_text(encoding="utf-8")
    assert "<link" not in text, "외부 스타일시트를 참조합니다."
    assert not re.search(r"<script[^>]+\bsrc=", text), "외부 스크립트를 참조합니다."
    assert not re.search(r'(?:src|href)="(?:https?:)?//', text), "외부 주소를 참조합니다."
    assert "@import" not in text


@pytest.mark.parametrize("name", TARGETS)
def test_이미지가_있다면_base64_다(built: Path, name: str) -> None:
    """지금은 캡처가 없다. 넣더라도 외부 파일을 가리키면 안 된다."""
    text = (built / name).read_text(encoding="utf-8")
    for source in re.findall(r'<img[^>]+src="([^"]*)"', text):
        assert source.startswith("data:"), f"외부 이미지: {source}"


@pytest.mark.parametrize("name", TARGETS)
def test_스타일과_스크립트를_품고_있다(built: Path, name: str) -> None:
    text = (built / name).read_text(encoding="utf-8")
    assert "<style>" in text
    assert "<script>" in text
    assert "@media print" in text, "인쇄 스타일이 없습니다."
    assert "Malgun Gothic" in text, "웹폰트 실패 시 폴백이 없습니다."


def test_캡처_자리가_이미지_태그를_만들지_않는다() -> None:
    """자리와 캡션만 표시한다. 사람이 나중에 넣는다."""
    body, _headings = render_markdown("![캡처 C-01] 첫 화면")
    assert "figure-slot" in body
    assert "<img" not in body
    assert "캡처 C-01" in body
    assert "첫 화면" in body


# ===================================================================== ③ 목차


@pytest.mark.parametrize("name", TARGETS)
def test_목차_링크가_모두_실제_절로_간다(built: Path, name: str) -> None:
    """**죽은 링크가 없어야 한다.**"""
    text = (built / name).read_text(encoding="utf-8")
    ids = set(re.findall(r'<h[1-6] id="([^"]+)"', text))
    links = re.findall(r"#toc[^>]*>.*?</nav>", text, flags=re.S)
    assert links, "목차가 없습니다."
    targets = re.findall(r'data-anchor="([^"]+)"', links[0])
    assert targets, "목차 항목이 없습니다."
    assert set(targets) <= ids, f"목차가 없는 절을 가리킵니다: {sorted(set(targets) - ids)}"


@pytest.mark.parametrize("name", TARGETS)
def test_목차가_모든_절을_담는다(built: Path, name: str) -> None:
    """h4 까지 담는다 — 링크가 걸린 소절을 목차에서 못 찾으면 곤란하다."""
    source = (built / name.replace(".html", ".md")).read_text(encoding="utf-8")
    _body, headings = render_markdown(source)
    expected = [item for item in headings if item.level in TOC_LEVELS]
    text = (built / name).read_text(encoding="utf-8")
    nav = re.findall(r"<nav id=\"toc\">.*?</nav>", text, flags=re.S)[0]
    targets = re.findall(r'data-anchor="([^"]+)"', nav)
    assert len(targets) == len(expected)


@pytest.mark.parametrize("name", TARGETS)
def test_문서_안_링크가_존재하는_앵커를_가리킨다(built: Path, name: str) -> None:
    """본문에서 `(#anchor)` 로 건 링크도 살아 있어야 한다."""
    text = (built / name).read_text(encoding="utf-8")
    ids = set(re.findall(r'<h[1-6] id="([^"]+)"', text))
    internal = {
        link for link in re.findall(r'<a href="#([^"]+)"', text) if not link.startswith("toc")
    }
    assert internal <= ids, f"없는 앵커를 가리킵니다: {sorted(internal - ids)}"


def test_id_가_겹치면_번호를_붙인다() -> None:
    _body, headings = render_markdown("## 같은 제목\n\n## 같은 제목\n")
    assert headings[0].anchor != headings[1].anchor


def test_명시한_id_를_그대로_쓴다() -> None:
    _body, headings = render_markdown("## 아무 제목 {#my-anchor}\n")
    assert headings[0].anchor == "my-anchor"
    assert headings[0].text == "아무 제목"


def test_슬러그가_한글을_남긴다() -> None:
    assert slugify("요금적용전력 3규칙") == "요금적용전력-3규칙"
    assert slugify("!!!") == "section"


# ===================================================================== ④ 앵커 30개


def test_앵커가_매뉴얼에_모두_있다() -> None:
    """없으면 화면 툴팁의 요지에 대응하는 전문이 없다 (16세션 4절)."""
    present = set(collect_anchors((DOCS / "MANUAL.md").read_text(encoding="utf-8")))
    missing = [key for key in anchor_keys() if key not in present]
    assert missing == [], f"매뉴얼에 없는 앵커: {missing}"


def test_생성된_매뉴얼_html_에도_앵커가_있다(built: Path) -> None:
    text = (built / MANUAL_FILENAME).read_text(encoding="utf-8")
    ids = set(re.findall(r'<h[1-6] id="([^"]+)"', text))
    assert set(anchor_keys()) <= ids


def test_앵커_수가_31개다() -> None:
    """**화면에서 없앤 자리는 앵커도 없다** (28세션 4·5절 · 36세션 1절).

    확실성·감도가 빠졌고, Word 는 애초에 앵커가 없었다. 36세션에 PPT 가 하나
    늘었다 (``ppt-report``).
    """
    assert len(ANCHORS) == 31
    assert "certainty" not in anchor_keys()
    assert "sensitivity" not in anchor_keys()


# ===================================================================== ⑤ 화면 툴팁


def test_툴팁이_제목과_요지를_함께_준다() -> None:
    """**링크가 아니라 툴팁이다** (16세션 4절). 화면에서 나가지 않고 요지를 읽는다."""
    tip = manual_tip("payback")
    assert tip.startswith("회수기간")
    assert "OPEX" in tip
    assert "](" not in tip and "http" not in tip


def test_모든_앵커가_툴팁을_낸다() -> None:
    """**제목 + 요지**다. 개선안 번호만 화면 순번으로 바뀐다 (27세션 2절)."""
    from kwise.ui.labels import measure_title

    for item in ANCHORS:
        tip = manual_tip(item.key)
        title = measure_title(item.title)
        assert title in tip
        assert len(tip) > len(title)


def test_등록되지_않은_앵커는_바로_실패한다() -> None:
    with pytest.raises(KeyError):
        manual_tip("없는-앵커")


# ===================================================================== 문법


def test_표를_표로_바꾼다() -> None:
    body, _headings = render_markdown("| 가 | 나 |\n|---|---|\n| 1 | 2 |\n")
    assert "<table>" in body
    assert "<th>가</th>" in body
    assert "<td>1</td>" in body


def test_코드블록에_복사_자리를_만든다() -> None:
    body, _headings = render_markdown("```\npytest\n```\n")
    assert 'class="code"' in body
    assert "<pre" in body
    assert "pytest" in body


def test_체크박스를_만든다() -> None:
    body, _headings = render_markdown("- [ ] 준비물\n- [x] 끝난 것\n")
    assert body.count('type="checkbox"') == 2
    assert "checked" in body


def test_코드_안의_별표는_굵게가_아니다() -> None:
    body, _headings = render_markdown("`a**b**c` 와 **진짜**\n")
    assert "a**b**c" in body
    assert "<strong>진짜</strong>" in body


def test_html_을_이스케이프한다() -> None:
    body, _headings = render_markdown("<script>alert(1)</script>\n")
    assert "<script>alert" not in body
    assert "&lt;script&gt;" in body


def test_인용과_가로줄() -> None:
    body, _headings = render_markdown("> 경고다\n\n---\n")
    assert "<blockquote>경고다</blockquote>" in body
    assert "<hr>" in body


def test_제목이_없어도_돈다() -> None:
    page, headings = render_html("그냥 문단 하나.", title="제목")
    assert headings == ()
    assert "그냥 문단 하나." in page
    assert '<nav id="toc">' in page


# ===================================================================== 중복 방지


def test_계산식은_기술서에만_있다() -> None:
    """매뉴얼은 근거를 가리키기만 한다 (중복 방지 규약)."""
    manual = (DOCS / "MANUAL.md").read_text(encoding="utf-8")
    assert "기술서" in manual, "매뉴얼이 기술서를 가리키지 않습니다."
    # 매뉴얼에 조문 색인 표를 옮겨 적지 않았는지 — 조문은 기술서 부록 C 한 곳이다
    assert manual.count("제68조") <= 1
    assert "부록 C" in (DOCS / "TECHNICAL.md").read_text(encoding="utf-8")


def test_사용법은_매뉴얼에만_있다() -> None:
    technical = (DOCS / "TECHNICAL.md").read_text(encoding="utf-8")
    assert "매뉴얼 2장" in technical, "기술서가 매뉴얼을 가리키지 않습니다."


def test_캡처_목록이_매뉴얼_자리와_맞는다() -> None:
    manual = (DOCS / "MANUAL.md").read_text(encoding="utf-8")
    captures = set(re.findall(r"!\[캡처 (C-\d+)\]", manual))
    listed = set(re.findall(r"\*\*(C-\d+)\*\*", (DOCS / "CAPTURES.md").read_text(encoding="utf-8")))
    assert captures == listed, f"매뉴얼 {sorted(captures)} vs 목록 {sorted(listed)}"
    assert len(captures) >= 8
