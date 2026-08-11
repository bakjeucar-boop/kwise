"""문서 변환 — Markdown 원본에서 단일 HTML 을 만든다 (요구사항서 13장).

**md 를 원본으로 두고 html 을 생성한다.** 두 벌을 손으로 유지하면 반드시
어긋난다. 사람이 고치는 것은 ``docs\\*.md`` 뿐이고 html 은 생성물이다.

**변환기를 직접 쓴 이유는 앵커 때문이다.** 화면의 [자세히] 링크가
``MANUAL.html#column-detection`` 같은 **확정된 id** 로 걸려 있다 (8세션).
일반 변환기는 제목을 제 나름대로 슬러그로 만들어 한글 제목에서 무엇이 나올지
알 수 없다. 여기서는 ``## 제목 {#id}`` 로 id 를 못 박고, 못 박지 않은 제목만
슬러그를 만든다.

지원하는 문법은 **우리가 쓰는 것뿐**이다 — 제목·문단·목록·표·코드블록·인용·
가로줄·체크박스와 인라인 서식(코드·굵게·기울임·링크). 쓰지 않는 문법은 넣지
않는다. 넣으면 시험이 닿지 않는 코드가 생긴다.

산출물은 **단일 파일**이다. 바깥 이미지·스타일·스크립트를 참조하지 않으므로
html 하나만 옮기면 그대로 열린다.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "DEFAULT_DOCS",
    "TOC_LEVELS",
    "DocPage",
    "Heading",
    "build_all",
    "build_page",
    "collect_anchors",
    "render_html",
    "render_markdown",
    "slugify",
]

# 만들 문서. (원본, 산출물, 제목)
DEFAULT_DOCS: tuple[tuple[str, str, str], ...] = (
    ("TECHNICAL.md", "TECHNICAL.html", "kWise 기술서"),
    ("MANUAL.md", "MANUAL.html", "kWise 사용 매뉴얼"),
)

# ``{#id}`` 는 한글도 받는다 — 부록처럼 한글 id 를 쓰는 자리가 있다.
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*(?:\{#([^\s{}]+)\})?\s*$")
_TABLE_SEPARATOR = re.compile(r"^\|[\s:\-|]+\|$")
_TASK = re.compile(r"^\[([ xX])\]\s+(.*)$")

# 화면 캡처 자리. **이미지를 넣지 않는다** — 자리와 캡션만 표시하고 사람이
# 나중에 찍어 넣는다 (``docs\CAPTURES.md``). ``<img>`` 를 만들지 않으므로
# 산출물이 단일 파일이라는 약속이 깨지지 않는다.
_CAPTURE = re.compile(r"^!\[(캡처\s+[^\]]+)\]\s*(.*)$")


def slugify(text: str) -> str:
    """제목에서 id 를 만든다. **한글을 그대로 둔다** — 링크가 읽혀야 한다."""
    cleaned = re.sub(r"[^\w\s가-힣.-]", "", text, flags=re.UNICODE).strip()
    cleaned = re.sub(r"\s+", "-", cleaned)
    return cleaned.strip("-").lower() or "section"


@dataclass(frozen=True)
class Heading:
    """목차 한 줄."""

    level: int
    text: str
    anchor: str


# --------------------------------------------------------------------- 인라인


def _inline(text: str) -> str:
    """인라인 서식. **코드부터 떼어 놓는다** — 코드 안의 별표는 굵게가 아니다."""
    spans: list[str] = []

    def stash(match: re.Match[str]) -> str:
        spans.append(f"<code>{html.escape(match.group(1))}</code>")
        return f"\x00{len(spans) - 1}\x00"

    text = re.sub(r"`([^`]+)`", stash, text)
    text = html.escape(text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], text)


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


# --------------------------------------------------------------------- 블록


@dataclass
class _Renderer:
    lines: list[str]
    headings: list[Heading] = field(default_factory=list)
    out: list[str] = field(default_factory=list)
    used: set[str] = field(default_factory=set)
    index: int = 0

    # ---- 도우미

    def _peek(self, offset: int = 0) -> str | None:
        position = self.index + offset
        return self.lines[position] if position < len(self.lines) else None

    def _unique(self, anchor: str) -> str:
        candidate = anchor
        suffix = 2
        while candidate in self.used:
            candidate = f"{anchor}-{suffix}"
            suffix += 1
        self.used.add(candidate)
        return candidate

    # ---- 블록별

    def _heading(self, match: re.Match[str]) -> None:
        level = len(match.group(1))
        text = match.group(2)
        anchor = self._unique(match.group(3) or slugify(text))
        self.headings.append(Heading(level=level, text=text, anchor=anchor))
        self.out.append(f'<h{level} id="{anchor}">{_inline(text)}</h{level}>')
        self.index += 1

    def _fence(self) -> None:
        language = self.lines[self.index].strip().removeprefix("```").strip()
        self.index += 1
        body: list[str] = []
        while self.index < len(self.lines) and not self.lines[self.index].startswith("```"):
            body.append(self.lines[self.index])
            self.index += 1
        self.index += 1  # 닫는 울타리
        classes = f' class="lang-{html.escape(language)}"' if language else ""
        code = html.escape("\n".join(body))
        # 복사 단추는 스크립트가 붙인다. 여기서는 자리만 만든다.
        self.out.append(f'<div class="code"><pre{classes}><code>{code}</code></pre></div>')

    def _table(self) -> None:
        header = _cells(self.lines[self.index])
        self.index += 2  # 머리글 + 구분선
        rows: list[list[str]] = []
        while self.index < len(self.lines) and self.lines[self.index].lstrip().startswith("|"):
            rows.append(_cells(self.lines[self.index]))
            self.index += 1
        head = "".join(f"<th>{_inline(cell)}</th>" for cell in header)
        body = "".join(
            "<tr>" + "".join(f"<td>{_inline(cell)}</td>" for cell in row) + "</tr>" for row in rows
        )
        self.out.append(
            f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>"
        )

    def _list(self, ordered: bool) -> None:
        tag = "ol" if ordered else "ul"
        marker = re.compile(r"^(\s*)(?:\d+\.|[-*])\s+(.*)$")
        items: list[tuple[int, str]] = []
        while self.index < len(self.lines):
            match = marker.match(self.lines[self.index])
            if not match:
                if self.lines[self.index].strip() == "":
                    break
                # 이어지는 줄은 앞 항목에 붙인다.
                if items:
                    items[-1] = (items[-1][0], items[-1][1] + " " + self.lines[self.index].strip())
                    self.index += 1
                    continue
                break
            items.append((len(match.group(1)), match.group(2)))
            self.index += 1

        self.out.append(f"<{tag}>")
        depth = 0
        for indent, text in items:
            level = indent // 2
            while level > depth:
                self.out.append(f"<{tag}>")
                depth += 1
            while level < depth:
                self.out.append(f"</{tag}>")
                depth -= 1
            task = _TASK.match(text)
            if task:
                checked = " checked" if task.group(1).lower() == "x" else ""
                self.out.append(
                    f'<li class="task"><label><input type="checkbox"{checked}> '
                    f"{_inline(task.group(2))}</label></li>"
                )
            else:
                self.out.append(f"<li>{_inline(text)}</li>")
        while depth > 0:
            self.out.append(f"</{tag}>")
            depth -= 1
        self.out.append(f"</{tag}>")

    def _quote(self) -> None:
        body: list[str] = []
        while self.index < len(self.lines) and self.lines[self.index].startswith(">"):
            body.append(self.lines[self.index].lstrip(">").strip())
            self.index += 1
        self.out.append(f"<blockquote>{_inline(' '.join(body))}</blockquote>")

    def _paragraph(self) -> None:
        body: list[str] = []
        while self.index < len(self.lines):
            line = self.lines[self.index]
            if not line.strip() or _HEADING.match(line) or line.startswith(("```", ">", "|")):
                break
            if re.match(r"^\s*(?:\d+\.|[-*])\s+", line):
                break
            body.append(line.strip())
            self.index += 1
        if body:
            self.out.append(f"<p>{_inline(' '.join(body))}</p>")

    # ---- 본체

    def run(self) -> tuple[str, tuple[Heading, ...]]:
        while self.index < len(self.lines):
            line = self.lines[self.index]
            stripped = line.strip()
            if not stripped:
                self.index += 1
                continue
            if stripped in {"---", "***", "___"}:
                self.out.append("<hr>")
                self.index += 1
                continue
            capture = _CAPTURE.match(stripped)
            if capture:
                label = html.escape(capture.group(1))
                caption = _inline(capture.group(2))
                self.out.append(
                    f'<div class="figure-slot"><strong>{label}</strong>'
                    + (f"<br>{caption}" if caption else "")
                    + "</div>"
                )
                self.index += 1
                continue
            heading = _HEADING.match(line)
            if heading:
                self._heading(heading)
                continue
            if stripped.startswith("```"):
                self._fence()
                continue
            if stripped.startswith("|") and _TABLE_SEPARATOR.match((self._peek(1) or "").strip()):
                self._table()
                continue
            if re.match(r"^\s*\d+\.\s+", line):
                self._list(ordered=True)
                continue
            if re.match(r"^\s*[-*]\s+", line):
                self._list(ordered=False)
                continue
            if stripped.startswith(">"):
                self._quote()
                continue
            self._paragraph()
        return "\n".join(self.out), tuple(self.headings)


def render_markdown(text: str) -> tuple[str, tuple[Heading, ...]]:
    """Markdown 본문을 HTML 조각과 제목 목록으로 바꾼다."""
    return _Renderer(lines=text.replace("\r\n", "\n").split("\n")).run()


def collect_anchors(text: str) -> tuple[str, ...]:
    """문서가 가진 앵커 id 전부. 화면 링크와 대조하는 데 쓴다."""
    _body, headings = render_markdown(text)
    return tuple(item.anchor for item in headings)


# --------------------------------------------------------------------- HTML 껍데기

# 웹폰트를 불러오지 않는다. **단일 파일** 규약이기도 하고, 실패하면 한글이
# 깨지기 때문이다. 맑은 고딕까지 내려오는 글꼴 사슬을 둔다.
_FONT_STACK = (
    '"Pretendard","Noto Sans KR","Apple SD Gothic Neo",'
    '"맑은 고딕","Malgun Gothic","돋움",sans-serif'
)
_MONO_STACK = '"Cascadia Mono","D2Coding","Consolas","맑은 고딕","Malgun Gothic",monospace'

_STYLE = f"""
:root {{
  --ink:#1b1f23; --dim:#5b6570; --line:#dfe3e8; --bg:#ffffff; --side:#f6f8fa;
  --mark:#08519c; --warn:#b45309; --code:#f2f4f7;
}}
* {{ box-sizing:border-box; }}
html {{ scroll-behavior:smooth; scroll-padding-top:1rem; }}
body {{
  margin:0; background:var(--bg); color:var(--ink);
  font-family:{_FONT_STACK}; font-size:16px; line-height:1.75;
  word-break:keep-all; overflow-wrap:anywhere;
}}
#layout {{ display:flex; align-items:flex-start; }}
#toc {{
  position:sticky; top:0; flex:0 0 19rem; height:100vh; overflow-y:auto;
  background:var(--side); border-right:1px solid var(--line); padding:1.5rem 1rem;
}}
#toc h2 {{ font-size:.95rem; margin:.2rem 0 1rem; color:var(--dim); border:0; }}
#toc ol {{ list-style:none; margin:0; padding:0; counter-reset:none; }}
#toc a {{
  display:block; padding:.22rem .5rem; border-radius:4px; color:var(--dim);
  text-decoration:none; font-size:.88rem; border-left:2px solid transparent;
}}
#toc a:hover {{ background:#e8edf3; color:var(--ink); }}
#toc a.lvl3 {{ padding-left:1.5rem; font-size:.82rem; }}
#toc a.lvl4 {{ padding-left:2.5rem; font-size:.78rem; color:#7b858f; }}
#toc a.current {{
  color:var(--mark); background:#e6eef8; border-left-color:var(--mark); font-weight:700;
}}
main {{ flex:1 1 auto; max-width:60rem; padding:2.5rem 3rem 6rem; min-width:0; }}
h1 {{ font-size:1.9rem; margin:0 0 .3rem; letter-spacing:-.01em; }}
h2 {{
  font-size:1.4rem; margin:2.6rem 0 .8rem;
  padding-bottom:.35rem; border-bottom:2px solid var(--line);
}}
h3 {{ font-size:1.12rem; margin:1.9rem 0 .6rem; }}
h4 {{ font-size:1rem; margin:1.4rem 0 .4rem; color:var(--dim); }}
p, li {{ margin:.5rem 0; }}
a {{ color:var(--mark); }}
hr {{ border:0; border-top:1px solid var(--line); margin:2.2rem 0; }}
blockquote {{
  margin:1rem 0; padding:.7rem 1rem; background:#fffbeb;
  border-left:4px solid var(--warn); color:#6b4a10;
}}
code {{ background:var(--code); padding:.1rem .32rem; border-radius:4px;
  font-family:{_MONO_STACK}; font-size:.88em; }}
.code {{ position:relative; margin:1rem 0; }}
.code pre {{
  margin:0; padding:.9rem 1rem; background:#0f172a; color:#e2e8f0;
  border-radius:6px; overflow-x:auto; font-family:{_MONO_STACK}; font-size:.85rem;
  line-height:1.6;
}}
.code pre code {{ background:none; color:inherit; padding:0; }}
.copy {{
  position:absolute; top:.5rem; right:.5rem; padding:.2rem .55rem; font-size:.75rem;
  border:1px solid #475569; border-radius:4px; background:#1e293b; color:#cbd5e1;
  cursor:pointer; font-family:{_FONT_STACK};
}}
.copy:hover {{ background:#334155; color:#fff; }}
.table-wrap {{ overflow-x:auto; margin:1rem 0; }}
table {{ border-collapse:collapse; width:100%; font-size:.92rem; }}
th, td {{
  border:1px solid var(--line); padding:.45rem .6rem; text-align:left; vertical-align:top;
}}
th {{ background:var(--side); font-weight:700; }}
li.task {{ list-style:none; margin-left:-1.3rem; }}
li.task label {{ cursor:pointer; }}
li.task input {{ margin-right:.5rem; }}
.figure-slot {{
  margin:1rem 0; padding:1.6rem 1rem; border:2px dashed var(--line); border-radius:6px;
  text-align:center; color:var(--dim); font-size:.9rem; background:var(--side);
}}
#burger {{ display:none; }}
@media (max-width:900px) {{
  #layout {{ display:block; }}
  #toc {{ position:static; height:auto; width:auto; flex:none; }}
  #toc[hidden] {{ display:none; }}
  main {{ padding:1.5rem 1.1rem 4rem; }}
  #burger {{
    display:block; position:sticky; top:0; z-index:5; width:100%; padding:.7rem 1rem;
    background:var(--side); border:0; border-bottom:1px solid var(--line);
    font-family:{_FONT_STACK}; font-size:.95rem; text-align:left; cursor:pointer;
  }}
}}
@media print {{
  #toc, #burger, .copy {{ display:none !important; }}
  body {{ font-size:10.5pt; }}
  main {{ max-width:none; padding:0; }}
  .code pre {{ background:#fff; color:#000; border:1px solid #999; }}
  h2 {{ page-break-after:avoid; }}
  table, .figure-slot, blockquote {{ page-break-inside:avoid; }}
  a {{ color:#000; text-decoration:none; }}
}}
"""

_SCRIPT = """
document.querySelectorAll('.code').forEach(function (block) {
  var button = document.createElement('button');
  button.className = 'copy';
  button.type = 'button';
  button.textContent = '복사';
  button.addEventListener('click', function () {
    var text = block.querySelector('code').innerText;
    navigator.clipboard.writeText(text).then(function () {
      button.textContent = '복사됨';
      setTimeout(function () { button.textContent = '복사'; }, 1500);
    });
  });
  block.appendChild(button);
});

var links = Array.prototype.slice.call(document.querySelectorAll('#toc a'));
var targets = links
  .map(function (link) { return document.getElementById(link.dataset.anchor); })
  .filter(Boolean);
if (window.IntersectionObserver && targets.length) {
  var current = null;
  var observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (!entry.isIntersecting) { return; }
      if (current) { current.classList.remove('current'); }
      current = document.querySelector('#toc a[data-anchor="' + entry.target.id + '"]');
      if (current) { current.classList.add('current'); }
    });
  }, { rootMargin: '0px 0px -75% 0px', threshold: 0 });
  targets.forEach(function (target) { observer.observe(target); });
}

var burger = document.getElementById('burger');
var toc = document.getElementById('toc');
if (burger && toc) {
  var narrow = window.matchMedia('(max-width:900px)');
  var apply = function () { toc.hidden = narrow.matches; };
  apply();
  narrow.addEventListener('change', apply);
  burger.addEventListener('click', function () { toc.hidden = !toc.hidden; });
  toc.addEventListener('click', function (event) {
    if (event.target.tagName === 'A' && narrow.matches) { toc.hidden = true; }
  });
}
"""


# 목차에 담을 단계. h1 은 문서 제목이라 넣지 않는다.
TOC_LEVELS: tuple[int, ...] = (2, 3, 4)


def _toc(headings: tuple[Heading, ...]) -> str:
    """목차. **모든 절이 들어간다** — 링크가 걸린 소절을 목차에서 못 찾으면 곤란하다."""
    items: list[str] = []
    for item in headings:
        if item.level not in TOC_LEVELS:
            continue
        css = f"lvl{item.level}"
        items.append(
            f'<li><a class="{css}" href="#{item.anchor}" data-anchor="{item.anchor}">'
            f"{html.escape(item.text)}</a></li>"
        )
    return "<ol>" + "".join(items) + "</ol>"


@dataclass(frozen=True)
class DocPage:
    """만들어진 문서 하나."""

    source: Path
    target: Path
    title: str
    headings: tuple[Heading, ...]

    @property
    def anchors(self) -> tuple[str, ...]:
        return tuple(item.anchor for item in self.headings)


def render_html(markdown: str, *, title: str) -> tuple[str, tuple[Heading, ...]]:
    """단일 HTML 문서를 만든다. **바깥 자원을 참조하지 않는다.**"""
    body, headings = render_markdown(markdown)
    page = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{_STYLE}</style>
</head>
<body>
<button id="burger" type="button">☰ 목차</button>
<div id="layout">
<nav id="toc"><h2>목차</h2>{_toc(headings)}</nav>
<main>
{body}
</main>
</div>
<script>{_SCRIPT}</script>
</body>
</html>
"""
    return page, headings


def build_page(source: Path, target: Path, title: str) -> DocPage:
    """md 하나를 html 하나로. **원본이 없으면 만들지 않고 실패한다.**"""
    if not source.is_file():
        raise FileNotFoundError(f"원본 문서가 없습니다: {source}")
    page, headings = render_html(source.read_text(encoding="utf-8"), title=title)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(page, encoding="utf-8")
    return DocPage(source=source, target=target, title=title, headings=headings)


def build_all(docs_dir: Path | None = None) -> tuple[DocPage, ...]:
    """``docs\\`` 의 문서를 모두 만든다."""
    base = docs_dir if docs_dir is not None else Path("docs")
    return tuple(
        build_page(base / source, base / target, title) for source, target, title in DEFAULT_DOCS
    )
