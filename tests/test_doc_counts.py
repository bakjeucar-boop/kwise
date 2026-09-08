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

    ① 실물을 세는 데 케이스나 덱을 돌려야 하는 것 — 케이스 판정 118건 ·
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
import sys
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
    "docs/OPEN_ITEMS.md",
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


def _deck_cases() -> int:
    """덱 벌 수 — ``tools\\render_deck.py`` 의 ``CASES`` 를 센다 (S138 3절).

    **들여오는 값을 재고 골랐다.** S137 은 「실물을 세려면 ``render_deck`` 을
    들여와야 해서」 못을 안 지었는데, 이 시험이 이미 ``kwise`` 를 통째로
    들이고 있어 **더 드는 것이 0.002초 · 모듈 하나**다. 맨 자리에서 재면
    1.4초·1,747모듈이지만 그것은 이 시험이 이미 낸 값이다.

    **글자로 세지 않는다** — ``key=`` 는 벌 말고 화면 버튼 하나를 더 물어
    한 벌 많게 나온다.
    """
    sys.path.insert(0, str(PROJECT_ROOT / "tools"))
    try:
        import render_deck
    finally:
        sys.path.pop(0)
    return len(render_deck.CASES)


def _live_nails() -> int:
    """살아 있는 xfail 못 수 — ``tests\\`` 전수에서 **데코레이터 줄**을 센다 (S147 6절).

    **글자 `xfail` 로 세지 않는다.** 걷힌 못을 적은 주석·독스트링이 일곱 자리에
    있어 그렇게 세면 0 이 아니라 7 이 나온다. 데코레이터는 줄 앞에 서므로
    ``^\\s*@pytest.mark.xfail`` 만 센다 — 인용은 줄 가운데 있어 안 걸린다.
    """
    return sum(
        len(re.findall(r"^\s*@pytest\.mark\.xfail", path.read_text(encoding="utf-8"), re.MULTILINE))
        for path in (PROJECT_ROOT / "tests").rglob("*.py")
    )


def _open_items() -> int:
    """미해결 건수 — 갈래 머리말이 말하는 수의 합 (``tools\\daily_brief.py`` 와 같은 자리).

    브리핑이 세는 수를 **여기서 다시 세지 않는다.** 그 도구를 불러 쓴다.
    """
    sys.path.insert(0, str(PROJECT_ROOT / "tools"))
    try:
        import daily_brief
    finally:
        sys.path.pop(0)
    state = daily_brief.current_state(_read("PROCEED.md"))
    return daily_brief.total_items(daily_brief.open_items(state))


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
    # S131 2절 — 「다음 작업」 칸이 「미해결 59건」 이라 적고 있었는데 실물은 63 이었다.
    ("미해결 건수", _open_items, r"미해결\s*(\d[\d,]*)\s*건"),
    # S138 3절 — 세 판이 「열하나」·「열둘」·「열넷」 을 서로 다른 칸에 적고 있었다.
    # **고유어를 못이 못 무므로 문서 쪽을 숫자 꼴로 맞췄다** (앵커 때와 같다).
    ("덱 벌 수", _deck_cases, r"덱 벌\s*\*{0,2}(\d[\d,]*)\*{0,2}\s*벌"),
    # S147 6절 — 「살아 있는 못」 행이 **「0건」 인 채로 남는 것**을 막는다. 못이
    # 하나 서는 순간 그 행이 거짓이 되는데 앞서는 사람이 세어 보고서야 알았다
    # (S145·S146 이 그 행을 두고 두 판을 썼다).
    ("살아 있는 못 수", _live_nails, r"살아 있는 못[^\n]{0,20}?\*{0,2}(\d[\d,]*)\*{0,2}\s*건"),
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


# ------------------------------------- 번호가 아니라 이름으로 부른다 (S131 2절)

#: 이름 대신 번호로 부른 자리 — 「②-32」 처럼 갈래 표식에 번호가 붙은 꼴.
#: **항목 자체의 머리 번호는 서식이라 이 꼴로 적지 않는다** — 미해결 칸은
#: `**이름** (몸)` 으로만 열고 번호는 브리핑이 붙인다.
GROUP_REF = re.compile(r"[①-⑮]-\d+")

#: 이 못이 보는 칸 둘. **「다시 열지 마라」 와 세션 목록표는 밖이다** — 닫힌
#: 기록이라 그때의 번호와 이름이 함께 붙어 있고, 고치면 이력이 아니게 된다.
REF_ROWS = ("미해결", "다음 작업")


def test_미해결과_다음_작업_칸은_갈래_번호로_부르지_않는다() -> None:
    """**세션을 건너 항목을 가리킬 때는 번호가 아니라 이름을 쓴다** (71세션 규약).

    번호는 앞 갈래가 하나만 닫혀도 통째로 밀린다 — S70 이 둘을 지워 뒤가 두
    칸씩 밀렸고, S71 은 「다음 작업」 의 한 문장만 옛 번호에 남아 브리핑이 딴
    항목을 그 자리의 본체라고 불렀다. **규약은 71세션부터 글로 있었는데 못이
    없어 S130 이 다시 셋을 찾았다** — 「덱 벌이 하나도 없는 종별 넷」 을 두
    자리가 서로 다른 옛 번호로 부르고 있었다.
    """
    rows = {}
    for line in _section(_read("PROCEED.md").splitlines(), "## 현재 상태"):
        cells = line.split("|")
        if len(cells) > 2 and cells[1].strip() in REF_ROWS:
            rows[cells[1].strip()] = line

    assert set(rows) == set(REF_ROWS), f"칸 둘을 못 읽었습니다 — {sorted(rows)}"
    for name, line in rows.items():
        found = GROUP_REF.findall(line)
        assert not found, (
            f"「{name}」 칸이 항목을 번호로 부릅니다 — {', '.join(sorted(set(found)))}. "
            "번호는 항목이 하나만 닫혀도 밀립니다. 이름으로 적으십시오."
        )


#: 미해결 한 항목의 **본문** 상한 (S135 3절 · 리뷰 6절 ㅂ B).
#:
#: 이 칸은 다음 판이 매번 통째로 읽는 자리라 **세 판 연속 ctx 에서 가장 크게
#: 먹은 것**으로 적혔다. 상한 아래는 「이름이 무슨 뜻인가」 이고 그 위는
#: **경위**다 — 경위는 세션 절이 제자리다.
ITEM_BODY_CAP = 600


def test_미해결_항목_본문은_상한을_넘지_않는다() -> None:
    """**넘으면 몸을 세션 절로 보내고 이름만 남긴다** (S135 3절).

    S135 가 열여섯을 옮겨 칸이 **36,948 → 18,972자**로 줄었다. 못이 없으면
    다시 자란다 — S130 리뷰가 쟀을 때 31,415자였고 다섯 판 만에 5,533자가
    늘었다.

    **브리핑을 불러 센다** — 미해결 칸을 읽는 자리를 여기서 새로 짓지 않는다.
    """
    sys.path.insert(0, str(PROJECT_ROOT / "tools"))
    try:
        import daily_brief
    finally:
        sys.path.pop(0)

    state = daily_brief.current_state(_read("PROCEED.md"))
    items = daily_brief.open_items(state)
    assert items, "미해결 칸을 못 읽었습니다 — PROCEED.md 「현재 상태」 를 보십시오."

    over = []
    for item in items:
        name, body = daily_brief.item_parts(item.text)
        if len(body) > ITEM_BODY_CAP:
            over.append(f"{name} ({len(body):,}자)")
    assert not over, (
        f"미해결 항목 본문이 {ITEM_BODY_CAP}자를 넘습니다 — {' · '.join(over)}. "
        "몸을 세션 절로 옮기고 칸에는 이름과 「몸은 N세션 절에 있다」 만 남기십시오."
    )


#: 「다음 작업」 칸의 상한 (S146 6절).
#:
#: **지금이 2,194자이고 그것이 두 판치다.** 3,000자면 두 판치가 넉넉히 들고
#: **세 판치가 되기 전에 문다.** 이 칸은 이미 두 번 자랐다 — 113세션이 한 번
#: 줄였고 S145 가 **13,131자**(브리핑 21,851자의 60.1%)에서 2,194자로 다시
#: 줄였다. 브리핑이 이 칸은 **자르지 않으므로**(68세션 2절) 칸이 자란 만큼
#: 브리핑이 자란다.
NEXT_STEP_CAP = 3000


def test_다음_작업_칸은_상한을_넘지_않는다() -> None:
    """**갈아 끼우는 칸이라 이어 붙이면 조용히 자란다** (S146 6절).

    S145 가 걷기 전에는 **열두 판치**가 이어 붙어 있었다. 못이 없으면 세 번째로
    자란다 — 두 번 다 사람이 세어 보고서야 알았다.

    **글자는 칸의 raw 셀 그대로 센다** — 미해결 칸을 세는 자리와 같다
    (:func:`daily_brief.build`).
    """
    sys.path.insert(0, str(PROJECT_ROOT / "tools"))
    try:
        import daily_brief
    finally:
        sys.path.pop(0)

    cell = daily_brief.current_state(_read("PROCEED.md")).get("다음 작업", "")
    assert cell, "「다음 작업」 칸을 못 읽었습니다 — PROCEED.md 「현재 상태」 를 보십시오."
    assert len(cell) <= NEXT_STEP_CAP, (
        f"「다음 작업」 칸이 {len(cell):,}자로 상한 {NEXT_STEP_CAP:,}자를 넘습니다. "
        "두 판치만 남기고 앞 판 기록은 세션 절로 보내십시오."
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
