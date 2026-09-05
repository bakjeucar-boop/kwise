r"""문서가 적은 「수」 를 실물과 맞댄다 (121세션).

**여태 문서의 수를 지키는 못이 하나도 없었다.** ``test_deployment.py`` 는 문서
여섯에서 **진입점 경로**만 보고 ``test_docsite.py`` 는 **앵커**만 본다 — 값을
보는 자리가 없어서 코드가 움직여도 문서는 안 빨개졌다. 세 판 연속으로 그
자국이 났다: S119 가 「반올림을 건 다섯」 을 세니 넷, S120 이 ⑮ 의 「두 자리」
를 세니 넷, 같은 세션이 「세 파일」 을 세니 일곱. **셋 다 고치러 들어가서야
드러났고, 세 판 다 세어 보기 전에는 그 수를 믿고 있었다.**

**시험 안에 수를 다시 적지 않는다.** 적으면 그 시험이 곧 또 하나의 낡을
자리가 된다 — S119·S120 이 「시험이 식을 다시 적으면 실물이 갈려도 통과한다」
를 두 번 겪었다. 그래서 여기서는 **문서에서 수를 읽어** 실물과 맞댄다.
:data:`COUNTS` 의 어느 줄에도 기대값이 없다.

**자유 문장에서 긁지 않고 표식을 정한다.** 수마다 「그 수만 서는 꼴」 을 하나
골랐다 (`` `rules_kr.json` … N항목 ``, ``N시트``, ``전체 N개 · 변경됨`` 처럼).
표식이 서는 자리를 문서 쪽에서 맞춰 준 곳이 하나 있다 —
``project-overview.md`` 의 「rules_kr.json 35항목」(단위를 붙였다).

**``PROCEED.md`` 는 통째로 넣지 않는다.** 세션 기록이라 낡은 수가 일부러 남아
있다 — 「현재 상태」 표와 「pytest 분할 실행」 절만 보고, 그 표 안에서도
**「최근 세션」 칸은 뺀다**(그 칸이 세션 기록이다).

**못이 못 잡는 것 셋을 여기 적어 둔다.** 미해결에도 이름으로 남겼다.

    ① 실물을 세는 데 케이스나 덱을 돌려야 하는 것 — 케이스 판정 104건 ·
      화면 감사 넷 · PPT 장 수. 매 시험에 도는 자리에 못 온다.
    ② 고유어로 적힌 수 — ``아홉`` 은 ``(\d+)시트`` 에 안 걸린다. **122세션에
      Excel 앵커의 「아홉 시트」 를 실물 값 ``13시트`` 로 고치자 아래
      「Excel 시트 수」 줄이 그 자리를 함께 물었다** (``MANUAL_ANCHORS.md``
      가 이미 :data:`WHOLE_DOCS` 에 있다) — **줄을 더하지 않았다.** 남은
      고유어 수는 그대로 이 못 밖이다.
    ③ 사람이 세어야 아는 수 — 「자리 넷」 처럼 실물이 코드 밖에 있는 것.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from kwise.docsite import render_markdown
from kwise.report.excel import SHEET_ORDER
from kwise.rules import describe_items
from kwise.ui.anchors import ANCHORS

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA = PROJECT_ROOT / "data"
DOCS = PROJECT_ROOT / "docs"

#: 통째로 훑는 문서. 지금을 말하는 글만 온다.
WHOLE_DOCS = (
    "CLAUDE.md",
    "docs/BILL_CHECK.md",
    "docs/CALC_LOGIC.md",
    "docs/CAPTURES.md",
    "docs/ENVIRONMENT.md",
    "docs/MANUAL.md",
    "docs/MANUAL_ANCHORS.md",
    "docs/REQUIREMENTS_kwise.md",
    "docs/TECHNICAL.md",
    "docs/TEST_DATA.md",
    "docs/project/collaboration.md",
    "docs/project/project-overview.md",
)

#: ``PROCEED.md`` 에서 **지금 값을 적는 자리** 둘.
PROCEED_SECTIONS = ("## 현재 상태", "## pytest 분할 실행")

#: 「현재 상태」 표에서 뺄 칸 — 세션 기록이라 낡은 수가 일부러 있다.
STATE_ROWS_SKIPPED = ("최근 세션",)


def _read(name: str) -> str:
    return (PROJECT_ROOT / name).read_text(encoding="utf-8")


def _section(lines: list[str], title: str) -> list[str]:
    start = lines.index(title)
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    return lines[start:end]


def _proceed_now() -> str:
    """``PROCEED.md`` 에서 지금 값을 적는 두 자리만 이어 붙인다."""
    lines = _read("PROCEED.md").splitlines()
    kept: list[str] = []
    for title in PROCEED_SECTIONS:
        for line in _section(lines, title):
            row = line.split("|")
            if len(row) > 2 and row[1].strip() in STATE_ROWS_SKIPPED:
                continue
            kept.append(line)
    return "\n".join(kept)


def _texts() -> tuple[tuple[str, str], ...]:
    pairs = [(name, _read(name)) for name in WHOLE_DOCS]
    pairs.append(("PROCEED.md (현재 상태 · pytest 분할 실행)", _proceed_now()))
    return tuple(pairs)


# ------------------------------------------------------------------ 실물 세기


def _json_items(name: str) -> int:
    return len(json.loads((DATA / name).read_text(encoding="utf-8"))["items"])


def _weather(pattern: str) -> int:
    return sum(1 for path in (DATA / "weather").glob(pattern) if path.is_file())


def _weather_grids() -> int:
    """격자 하나가 해마다 파일 하나다. 좌표만 남겨 센다."""
    return len({path.name.rsplit("_", 1)[0] for path in (DATA / "weather").glob("*.parquet")})


def _test_functions(name: str) -> int:
    return len(re.findall(r"^def test_", _read(f"tests/{name}"), re.MULTILINE))


def _manual_sections() -> int:
    """매뉴얼의 절 수.

    **``tools\\build_docs.py`` 가 「절」 칸에 찍는 것과 같은 수여야 한다** — 그
    도구가 ``len(page.headings)`` 를 찍으므로 여기도 같은 자리를 부른다.
    ``TOC_LEVELS`` 로 세면 79 가 나오는데 도구는 81 을 찍는다: **두 규칙이
    나란히 서면 문서가 어느 쪽을 적었는지 아무도 모른다.**
    """
    _body, headings = render_markdown(_read("docs/MANUAL.md"))
    return len(headings)


def _engine_branch_files() -> int:
    """① 엔진 갈래가 무는 시험 파일 수.

    ``PROCEED.md`` 의 ① 명령에서 ``--ignore`` 를 읽어 뺀다 — **갈래를 고치면
    이 수가 따라 움직인다.** 여기에 파일 이름을 다시 적지 않는 까닭이다.
    """
    command = next(
        line
        for line in _proceed_now().splitlines()
        if "--ignore=tests" in line and "pytest tests" in line
    )
    ignored = set(re.findall(r"--ignore=tests[\\/]([\w.]+)", command))
    return sum(1 for path in (PROJECT_ROOT / "tests").glob("test_*.py") if path.name not in ignored)


#: (이름, 실물을 세는 함수, 문서에서 그 수를 찾는 표식).
#:
#: **표식마다 무리(group) 가 하나여야 한다** — 그 하나가 문서가 적은 수다.
COUNTS: tuple[tuple[str, Callable[[], int], str], ...] = (
    (
        "rules_kr.json 항목 수",
        lambda: _json_items("rules_kr.json"),
        r"rules_kr\.json[^\n]{0,40}?\*{0,2}(\d[\d,]*)\*{0,2}\s*(?:항목|개(?!월))",
    ),
    (
        "assumptions.json 항목 수",
        lambda: _json_items("assumptions.json"),
        r"assumptions\.json[^\n]{0,40}?\*{0,2}(\d[\d,]*)\*{0,2}\s*(?:항목|개(?!월))",
    ),
    (
        "기준 데이터 화면의 전체 개수",
        lambda: len(describe_items()),
        r"전체\s*(\d[\d,]*)개\s*·\s*변경됨",
    ),
    ("Excel 시트 수", lambda: len(SHEET_ORDER), r"(\d[\d,]*)\s*시트"),
    (
        "요금표 계약종별 수",
        lambda: len(
            json.loads((DATA / "tariff_kr_20260601.json").read_text(encoding="utf-8"))[
                "contract_types"
            ]
        ),
        r"(\d[\d,]*)\s*종별",
    ),
    ("매뉴얼 앵커 수", lambda: len(ANCHORS), r"앵커[^\n]{0,10}?\*{0,2}(\d[\d,]*)\*{0,2}\s*개"),
    (
        "시군구 좌표 수",
        lambda: len(json.loads((DATA / "sigungu_kr.json").read_text(encoding="utf-8"))),
        r"시군구\s*(\d[\d,]*)\s*개",
    ),
    ("기상 격자 수", _weather_grids, r"(?<![A-Za-z])(\d[\d,]*)\s*격자"),
    (
        "기상 parquet 파일 수",
        lambda: _weather("*.parquet"),
        r"격자\s*×[^,\n]*,\s*(\d[\d,]*)\s*파일",
    ),
    (
        "data\\weather\\ 전체 파일 수",
        lambda: _weather("*"),
        r"사전 취득분\s*(\d[\d,]*)\s*파일",
    ),
    (
        "test_deployment.py 시험 수",
        lambda: _test_functions("test_deployment.py"),
        r"test_deployment\.py[^\n]{0,20}?\*{0,2}(\d[\d,]*)\*{0,2}\s*건",
    ),
    (
        "매뉴얼 줄 수",
        lambda: len(_read("docs/MANUAL.md").splitlines()),
        r"\((\d[\d,]*)줄\s*·\s*html\s*\d[\d,]*절\)",
    ),
    (
        "매뉴얼 html 절 수",
        _manual_sections,
        r"\(\d[\d,]*줄\s*·\s*html\s*(\d[\d,]*)절\)",
    ),
    (
        "① 엔진 갈래가 무는 시험 파일 수",
        _engine_branch_files,
        r"아래\s*(\d[\d,]*)\s*파일",
    ),
)


@pytest.mark.parametrize("name,count,mark", COUNTS, ids=[row[0] for row in COUNTS])
def test_문서가_적은_수가_실물과_같다(name: str, count: Callable[[], int], mark: str) -> None:
    """**문서에서 읽은 수**와 **실물을 센 수**가 같아야 한다.

    기대값을 여기 적지 않는다 — 양쪽 다 밖에서 가져온다.
    """
    actual = count()
    found: list[tuple[str, int, int]] = []
    for source, text in _texts():
        for hit in re.finditer(mark, text):
            line = text[: hit.start()].count("\n") + 1
            found.append((source, line, int(hit.group(1).replace(",", ""))))

    assert found, (
        f"{name} 의 표식이 어느 문서에도 없습니다 ({mark!r}). "
        "표식이 사라지면 이 못은 아무것도 안 지킵니다 — 문서의 꼴을 바꿨으면 "
        "표식을 함께 고치십시오."
    )
    wrong = [(src, line, value) for src, line, value in found if value != actual]
    assert not wrong, f"{name} 이 문서와 갈립니다 — 실물은 {actual} 입니다. " + " · ".join(
        f"{src}:{line} 이 {value}" for src, line, value in wrong
    )


# ------------------------------------------- 한 문서 안에서 갈리는 것 (S120 ⑮)

#: ``CALC_LOGIC.md`` 부록의 갈래 표 — 「수」 칸과 「번호」 칸.
BRANCH_ROW = re.compile(r"^\|\s*\*\*([가-라])\..+?\|\s*\*\*(\d+)\*\*\s*\|\s*(.*?)\s*\|$", re.M)
CIRCLED = re.compile(r"[①-⑳]")


def test_의심_목록_갈래표의_수와_번호가_같다() -> None:
    """**수 칸이 번호 칸을 안 따라가면 빨개진다** (S120 이 ⑮ 에서 겪은 자리).

    같은 표 안에서 한쪽만 고치면 조용히 어긋난다 — ``CALC_LOGIC.md`` 자신이
    「닫을 때는 두 자리를 함께 고친다」 고 적어 두었고, 그것을 여기서 센다.
    """
    text = (DOCS / "CALC_LOGIC.md").read_text(encoding="utf-8")
    rows = BRANCH_ROW.findall(text)
    assert len(rows) == 4, f"갈래 표를 못 읽었습니다 — {len(rows)}줄만 잡혔습니다."

    for branch, declared, numbers in rows:
        listed = len(CIRCLED.findall(numbers))
        assert int(declared) == listed, (
            f"「{branch}」 갈래의 수 {declared} 와 번호 {listed} 개가 갈립니다 — "
            "표의 두 칸을 함께 고치십시오."
        )


# --------------------------------------------- 갈래 넷이 시험을 다 무는가 (S125)

#: 「pytest 분할 실행」 절에 적힌 갈래 명령 한 줄.
BRANCH_CMD = re.compile(r"^ {4}\.venv\\Scripts\\python\.exe -m pytest (?P<args>.+)$")

#: 명령이 부르는 시험 파일 — ``tests\X`` 와 ``--ignore=tests\X`` 를 함께 잡는다.
BRANCH_FILE = re.compile(r"tests[\\/](\S+)")


def _branch_commands() -> list[tuple[str, set[str]]]:
    """갈래 명령마다 (원문, 부르는 파일 이름) 을 낸다.

    파일을 하나도 안 부르는 명령(전체 실행)은 뺀다.
    """
    lines = _section(_read("PROCEED.md").splitlines(), "## pytest 분할 실행")
    out: list[tuple[str, set[str]]] = []
    for line in lines:
        found = BRANCH_CMD.match(line)
        if found is None:
            continue
        names = set(BRANCH_FILE.findall(found.group("args")))
        if names:
            out.append((found.group("args"), names))
    return out


def test_갈래_넷이_시험_파일을_빠짐없이_한_번씩_문다() -> None:
    """**갈래 합이 수집과 같은지를 파일 이름으로 지킨다** (S125).

    **여기에 1,662 같은 수를 적지 않는다** — 적으면 그 수가 또 하나의 낡을
    자리가 된다. 명령에서 파일 이름을 읽어 ``tests\\`` 아래 실물과 맞댈 뿐이다.

    셋을 함께 잡는다 — ① 어느 갈래에도 없는 파일 · ② 두 갈래에 겹치는 파일 ·
    ③ 명령의 오타. 오타는 「그런 파일이 없다」 로 걸린다: S124 가 ① 을 손으로
    칠 때 ``--ignore=tests\\test_document`` 처럼 ``.py`` 를 빠뜨려 81건이
    겹쳐 돌았고, **pytest 는 없는 경로를 조용히 지나간다.**
    """
    commands = _branch_commands()
    assert len(commands) == 4, f"갈래 명령 넷을 못 읽었습니다 — {len(commands)}개만 잡혔습니다."

    real = {path.name for path in (PROJECT_ROOT / "tests").glob("test_*.py")}
    for args, names in commands:
        missing = sorted(names - real)
        assert not missing, (
            f"명령이 없는 파일을 부릅니다 — {', '.join(missing)} "
            f"(`.py` 를 빠뜨렸는지 보십시오): {args}"
        )

    ignored = [names for args, names in commands if "--ignore=" in args]
    assert len(ignored) == 1, "``--ignore`` 로 거르는 갈래는 ① 하나여야 합니다."
    (engine_skips,) = ignored
    named = [names for args, names in commands if "--ignore=" not in args]

    seen: set[str] = set()
    for names in named:
        overlap = sorted(seen & names)
        assert not overlap, f"두 갈래가 같은 파일을 함께 뭅니다 — {', '.join(overlap)}"
        seen |= names

    assert engine_skips == seen, (
        "① 이 거르는 파일과 ②③④ 가 부르는 파일이 다릅니다 — "
        f"① 만 거르는 것 {sorted(engine_skips - seen)} · "
        f"②③④ 만 부르는 것 {sorted(seen - engine_skips)}"
    )
