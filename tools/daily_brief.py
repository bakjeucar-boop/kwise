r"""아침 브리핑을 만든다.

`PROCEED.md` 에서 오늘 시작에 필요한 것만 뽑는다.
새 대화의 첫 메시지로 붙이는 것이 용도다.

**칸마다 자를지 접을지가 다르다** (68세션 2절). 이것은 아침에 읽는 요약이면서
**인수인계 매체**다 — 짧아야 읽히고 온전해야 넘겨진다. 그래서 셋은 접기만 하고
자르지 않는다: **오늘 첫 작업 · 미해결 항목 · 블로커.** 나머지(직전 세션 요약 ·
근거 넷)는 그대로 자른다.

**예산도 칸마다 따로다** (85세션 1절). 전역 한도 하나를 두면 앞 칸이 다 먹었을
때 뒤 칸이 통째로 죽는다 — 09-02 아침에 71줄이 그렇게 사라졌다. 지금 한도는
**미해결 한 항목의 본문**에만 있고(:data:`ITEM_BODY_LINES`), 갈래 제목 ·
항목 이름 · 뒤 칸 넷(블로커 · 오늘 첫 작업 · 근거 셋 · 「내가 밟아야 할 것」)은
어떤 경우에도 온전히 나온다.

    .venv\Scripts\python.exe tools\daily_brief.py
    .venv\Scripts\python.exe tools\daily_brief.py --no-clip   클립보드 복사 생략
    .venv\Scripts\python.exe tools\daily_brief.py --out brief.md

프로젝트 지식에 올리는 세 문서(`docs\project\`)가 「무엇을 만드는가」 와
「어떻게 일하는가」 를 맡고, 이 도구는 「오늘 어디서부터인가」 만 맡는다.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import NamedTuple

# **예산은 칸마다 따로 둔다** (85세션 1절). 전에는 전역 한도 하나(`MAX_LINES`)가
# 문서 전체에 걸려 있었다 — **앞 칸이 예산을 다 먹으면 뒤 칸이 통째로 죽는다.**
# 09-02 아침에 그렇게 됐다: 미해결 ②-13 이 문장 한가운데서 끊기고 그 뒤 71줄이
# 사라졌다(②-14~②-20 일곱 · ③ 갈래 · 블로커 · 오늘 첫 작업 · 근거 셋 ·
# 「내가 밟아야 할 것」). 50 → 90 으로 올린 것이 68세션인데, **올리는 것으로는
# 안 낫는다** — 항목이 느는 날 같은 자리에서 다시 죽는다. 자리를 옮겨야 낫는다.
#
# 그래서 한도가 **미해결 한 항목의 본문**에만 걸린다. 잘리는 것은 그 본문뿐이고
# 갈래 제목 · 항목 이름 · 뒤 칸 넷은 어떤 경우에도 온전히 나온다.
ITEM_BODY_LINES = 3
# 한 줄이 길면 터미널에서 접혀 줄 수가 어긋난다.
WRAP_AT = 96

ROOT = Path(__file__).resolve().parent.parent
PROCEED = ROOT / "PROCEED.md"


# ── PROCEED.md 읽기 ──────────────────────────────────────────────────────


def strip_md(text: str) -> str:
    """굵게·기울임·링크·코드 표시를 걷어낸다. 붙여 쓸 때는 평문이 낫다."""
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # 링크
    text = text.replace("**", "").replace("`", "")
    # **이스케이프한 세로줄을 되돌린다** (72세션 3절). 표 칸 안에서는 `\|` 로
    # 적어야 행이 안 갈리는데, 사람이 읽을 때는 그냥 세로줄이어야 한다.
    text = text.replace("\\|", "|")
    return re.sub(r"\s+", " ", text).strip()


#: 표 칸 구분자. **이스케이프한 세로줄(``\|``)은 자르지 않는다** (72세션 3절).
#:
#: 68·70세션이 같은 자리에 물렸다 — `Diagnosis | None`·`QualityReport | None` 을
#: 칸 안에 그냥 적어 그 표 행이 **통째로 사라졌다.** 그때까지는 「세로줄을 쓰지
#: 마라」 말고 달리 쓸 방법이 없었다. `\|` 는 markdown 정본 표기이기도 해서
#: **파서와 렌더러가 같은 것을 본다.**
_CELL = re.compile(r"(?<!\\)\|")


def _state_block(body: str) -> str:
    """「현재 상태」 표가 든 덩어리. 없으면 빈 글."""
    m = re.search(r"^## 현재 상태\s*$(.*?)^---", body, re.M | re.S)
    return m.group(1) if m else ""


def _cells(line: str) -> list[str]:
    return [c.strip() for c in _CELL.split(line.strip().strip("|"))]


def clip(text: str, width: int = WRAP_AT) -> str:
    """한 줄을 폭에 맞춰 **자른다.** 자른 자리는 … 로 표시한다.

    **잘라도 되는 칸에만 쓴다** — 직전 세션 요약과 근거 넷이다.
    """
    text = text.strip()
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"


def wrap(text: str, first: str, rest: str) -> list[str]:
    """폭에 맞춰 **접는다. 자르지 않는다** (68세션 2절).

    브리핑은 두 일을 겸한다 — 아침에 읽는 요약이면서 **웹 대화창에 붙이는
    인수인계 매체**다. 짧아야 읽히고 온전해야 인수인계가 되니 목적이 부딪힌다.
    이틀 연속 같은 자리가 잘려 원문을 다시 물어야 했다 — 오늘 첫 작업(후보
    전문) · 미해결 항목 · 블로커. **그 셋만 접고 나머지는 그대로 자른다.**
    """
    body = textwrap.wrap(text.strip(), width=WRAP_AT - len(first)) or [""]
    return [f"{first}{body[0]}"] + [f"{rest}{line}" for line in body[1:]]


def read_proceed() -> str:
    if not PROCEED.exists():
        sys.exit(f"PROCEED.md 를 찾을 수 없다: {PROCEED}")
    return PROCEED.read_text(encoding="utf-8")


def current_state(body: str) -> dict[str, str]:
    """「현재 상태」 표를 항목→값 으로 읽는다."""
    rows: dict[str, str] = {}
    for line in _state_block(body).splitlines():
        cells = _cells(line)
        if len(cells) == 2 and cells[0] and not set(cells[0]) <= set("-: "):
            rows[strip_md(cells[0])] = cells[1]
    rows.pop("항목", None)
    return rows


def unread_rows(body: str) -> list[str]:
    """「현재 상태」 표에서 **칸이 둘로 안 갈린 행의 이름** (72세션 3절).

    칸 안의 세로줄 하나가 그 행을 **통째로** 지운다. 68세션은 미해결 칸에서,
    70세션은 「다음 작업」 칸에서 물렸고 **70세션에는 경고조차 안 났다** —
    사라진 자리에 그럴듯한 기본값이 떴다.

    68세션이 만든 장치는 **미해결이 0건일 때만** 소리를 냈다. 어느 행이든
    사라지면 말해야 한다 — **이름을 그대로 낸다.** 어느 줄을 고칠지가 곧 답이다.
    """
    gone: list[str] = []
    for line in _state_block(body).splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = _cells(line)
        if len(cells) != 2:
            gone.append(strip_md(cells[0]) or "(이름 없는 행)")
    return gone


def latest_session(body: str) -> tuple[str, str]:
    """맨 위 세션 표에서 가장 큰 번호의 줄을 집는다. (제목, 본문)"""
    pat = re.compile(
        r"^\|\s*\*{0,2}(\d+)세션\*{0,2}\s*\((\d{2}-\d{2})\)\s*\|\s*(.+?)\s*\|?\s*$", re.M
    )
    best: tuple[int, str, str] | None = None
    for num, day, text in pat.findall(body):
        n = int(num)
        if best is None or n > best[0]:
            best = (n, day, text)
    if best is None:
        return ("", "")
    return (f"{best[0]}세션 ({best[1]})", best[2])


# ── 미해결 갈래 ──────────────────────────────────────────────────────────

#: 갈래 **머리말**의 표식. **뒤에 빈칸이 와야 머리말이다** (68세션 2절).
#:
#: 그냥 `[①②③④⑤]` 였을 때 본문의 「①-4 「실측」 은 …」 안의 ① 까지 갈래로
#: 세어 **표식을 넷(① ① ② ③) 잡았다.** ① 이 두 덩어리로 갈리고 뒤 덩어리가
#: 유령 항목이 되어 브리핑에 떴다 — 67세션이 그것을 「유령」 이라 적었다.
#: 머리말은 `① 자료를 기다리는 것 7건` 이고 곁가리는 `①-4` 라 **다음 글자**가
#: 가른다.
GROUP = re.compile(r"[①②③④⑤](?=\s)")

#: **굵게 여는** 표식 — 갈래 머리말의 꼴이다 (92세션 2절).
#:
#: 미해결 칸의 갈래는 셋 다 `**① 자료를 기다리는 것 7건**` 로 열고, 표식이
#: 이름에 붙어 :data:`GROUP` 이 놓치는 꼴(`**①자료를 …**`)도 `**` 는 그대로다.
#: 조문 번호는 「제57조 ④」 처럼 **글 가운데**에 서므로 걸리지 않는다.
#: :func:`missing_groups` 가 쓴다.
HEAD_MARK = re.compile(r"\*\*\s*([①②③④⑤])")


def first_paren(text: str) -> tuple[str, str, str]:
    """이름 · **첫 괄호 짝 안** · **그 뒤에 남은 글** 로 가른다 (68세션 2절).

    `partition("(")` + `rsplit(")")` 는 **첫 여는 괄호부터 마지막 닫는 괄호
    까지**를 잡는다. ① 은 목록 괄호가 닫힌 뒤에도 서술이 이어지므로 그
    서술이 통째로 항목 안에 들어왔다 — 마지막 항목이
    「실측 1). ①-4 「실측」 은 …」 이 됐다. **짝을 세어 닫는 자리를 찾는다.**

    **셋째를 72세션에 붙였다.** 그 뒤에 남는 글은 **조용히 버려진다** —
    ① 의 곁가리 서술처럼 그래도 되는 것이 있고, 71세션처럼 **목록 자체가
    거기로 밀려나** 항목이 여덟에서 하나로 준 것도 있다. 가르는 것은
    :func:`hijacked_groups` 다.
    """
    start = text.find("(")
    if start < 0:
        return text.strip(), "", ""
    depth = 0
    for i in range(start, len(text)):
        if text[i] in "(（":
            depth += 1
        elif text[i] in ")）":
            depth -= 1
            if depth == 0:
                return text[:start].strip(), text[start + 1 : i], text[i + 1 :]
    return text[:start].strip(), text[start + 1 :], ""  # 안 닫혔으면 끝까지


#: 갈래 이름 끝의 「N건」. **줄이 아니라 건을 센다** (68세션 2절).
COUNT = re.compile(r"(\d+)\s*건")


def group_count(name: str, lines: int) -> int:
    """갈래 머리말이 말하는 **건수**. 없으면 줄 수로 갈음한다.

    「청구서 4」 한 줄이 넷을 담는다 — 줄을 세면 ① 이 4건이 되는데 머리말은
    7건이다. **뭉침이지 실종이 아니므로**(「4」 가 적혀 있다) 펴지 않고
    **세는 쪽을 고친다.**
    """
    found = COUNT.search(name)
    return int(found.group(1)) if found else lines


class Item(NamedTuple):
    """미해결 한 줄."""

    sym: str
    """갈래 표식 — ① ② ③."""
    tag: str
    """`①-1` 처럼 **갈래 안에서** 매긴 번호 (69세션 1절).

    통번호는 앞 갈래에서 하나만 늘거나 줄어도 **뒤가 전부 밀린다.** 67세션의
    「미해결 10번」(`ruff format`)과 68세션 지시서의 「미해결 10」(3단계 태양광
    역률)이 서로 다른 것을 가리킨 까닭이다. `PROCEED.md` 는 이미 이 식을 쓰고
    61세션도 「미해결 ①-3」 이라 적었다 — **브리핑이 원본을 따라간다.**
    """
    name: str
    """갈래 이름 — 「자료를 기다리는 것 7건」."""
    text: str
    """항목 한 줄."""


def missing_groups(raw: str, items: list[Item]) -> list[str]:
    """칸에 표식이 있는데 **갈래로 안 잡힌 것** (69세션 2절).

    행 전체가 사라지면 항목이 0이 되어 눈에 띄지만, **갈래 하나만 사라지면
    수만 줄어 조용하다.** :data:`GROUP` 이 「표식 뒤에 빈칸」 을 요구하므로
    `**①자료를 기다리는 것**` 처럼 붙여 쓰면 그 갈래가 통째로 빠진다 —
    68세션이 그 조건을 만들어 놓고 잡는 장치는 0 에만 걸어 두었다.

    글에 보이는 표식과 잡은 표식을 맞대 본다. 본문이 「②-3 을 보라」 처럼
    이미 잡힌 갈래를 가리키는 것은 걸리지 않는다 — 집합으로 견주기 때문이다.

    **다만 「글에 보이는 표식」 이 아니라 「굵게 시작하는 표식」 을 본다**
    (92세션 2절). 그냥 `[①②③④⑤]` 로 세면 **조문 번호가 걸린다** —
    89세션이 미해결에 「제57조 ④·제59조 ⑤」 를 적은 뒤로 브리핑이 날마다
    「갈래 ④ · ⑤ 를 못 읽었다」 를 찍었다. 아무것도 안 막지만 **매일 뜨는
    경고는 안 읽힌다**(72세션 잣대) — :func:`group_chunks` 가 「N건」 으로
    조문을 걸러 낸 것과 같은 자리에서 이쪽만 안 걸렀다.

    갈래 머리말은 언제나 `**① 자료를 …**` 로 굵게 열고, 잡히지 않는 꼴
    (`**①자료를 …**`)도 그 `**` 는 그대로다 — 곧 **막으려는 것은 다 잡고
    조문은 다 버린다.**
    """
    on_page = set(HEAD_MARK.findall(raw))
    return sorted(on_page - {item.sym for item in items})


def total_items(items: list[Item]) -> int:
    """미해결 **건수.** 갈래 머리말이 말하는 수를 더한다.

    줄을 세면 ① 이 4가 되는데 머리말은 7이다 — 「청구서 4」 한 줄이 넷을
    담기 때문이다. **머리말이 7이면 총계도 그 7을 담아야 한다.**
    """
    counted: dict[str, tuple[str, int]] = {}
    for item in items:
        head, seen = counted.get(item.sym, (item.name, 0))
        counted[item.sym] = (head, seen + 1)
    return sum(group_count(name, seen) for name, seen in counted.values())


def group_chunks(raw: str) -> list[tuple[str, str]]:
    """미해결 칸을 갈래별 **(표식, 나머지)** 로 자른다 — ① … ② … ③ …

    **머리말이 아닌 표식은 앞 갈래에 도로 붙인다** (74세션 2절). 68세션이
    「표식 뒤에 빈칸」 으로 좁혔는데 **조문 번호가 그 조건을 그대로 만족한다** —
    73세션이 「제43조 ③ 역률…」 이라 적었더니 그 자리에서 갈래가 하나 생겨
    **17건이 18건이 되고 ② 목록 가운데가 갈렸다.** 67세션 유령과 같은 자리다.

    73세션은 조문을 「제3항」 으로 바꿔 피했다. **그건 회피다** — 이 프로젝트는
    조문을 인용하는 프로젝트이고, 다음에 누가 또 적으면 또 난다.

    **가르는 표지는 글 안에 이미 있다 — 「N건」.** 세 갈래 머리말이 모두 달고
    있고(「자료를 기다리는 것 7건」) 유령은 안 단다. **서식을 바꾸지 않는다.**

    **짖지 않고 막는다.** 「제43조 ③」 은 **바르게 쓴 글**이지 실수가 아니다 —
    쓸 때마다 경고를 내면 매일 뜨고, 매일 뜨는 경고는 안 읽힌다 (72세션 잣대).
    **진짜 갈래가 삼켜지는 위험은 :func:`missing_groups` 가 이미 받는다** —
    「N건」 없는 ④ 를 새로 열면 글에는 있고 항목에는 없으므로 그쪽이 짖는다.
    """
    marks = list(GROUP.finditer(raw))
    out: list[tuple[str, str]] = []
    for i, mk in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(raw)
        chunk = strip_md(raw[mk.start() : end]).strip(" ·")
        body = chunk[1:].strip()
        if out and not COUNT.search(first_paren(body)[0]):
            # 머리말이 아니다 — 표식째로 앞 갈래 글에 도로 넣는다.
            prev_sym, prev_body = out[-1]
            out[-1] = (prev_sym, f"{prev_body} {chunk}")
            continue
        out.append((chunk[0], body))
    return out


def hijacked_groups(raw: str) -> list[str]:
    """**목록이 괄호 밖으로 밀려난 갈래** (72세션 3절).

    항목 목록은 갈래 이름 다음의 **첫 괄호 짝** 안이다. 그 앞에 딴 괄호를
    하나 끼우면 **그것이 목록으로 읽히고 진짜 목록은 꼬리로 밀린다** —
    71세션이 「② … 8건」 뒤에 종결 근거를 한 줄 붙였더니 ② 항목이 여덟에서
    **하나**로 줄었다. 71세션은 돌려 보고서야 알았다.

    **꼬리가 있다고 다 말하지 않는다.** ① 은 곁가리 서술(「①-4 「실측」 은 …」)
    을 괄호 뒤에 두고 있고 그것은 68세션이 일부러 버리게 한 것이다. 가르는 것은
    **꼬리가 또 괄호로 시작하는가** — 그러면 괄호 짝이 둘이라 **어느 쪽이
    목록인지 글만 봐서는 모른다.** 서술은 괄호로 시작하지 않는다.

    「괄호가 둘 이상」 으로 세면 ① 이 걸린다 — 곁가리 서술 **안**에
    (`WeatherLabel` 로 바꿔 끼운다)가 들어 있다. **첫 글자를 본다.**
    """
    return [
        sym
        for sym, chunk in group_chunks(raw)
        if first_paren(chunk)[2].lstrip(" ·.,").startswith(("(", "（"))
    ]


def open_items(state: dict[str, str]) -> list[Item]:
    """미해결 칸을 :class:`Item` 으로 편다."""
    raw = state.get("미해결", "")
    if not raw:
        return []
    out: list[Item] = []
    order: dict[str, int] = {}

    def add(sym: str, name: str, text: str) -> None:
        order[sym] = order.get(sym, 0) + 1
        out.append(Item(sym, f"{sym}-{order[sym]}", name, text))

    for sym, chunk in group_chunks(raw):
        # 「자료를 기다리는 것 7건 (청구서 4 · …)」 → 이름과 괄호 안을 가른다
        name, detail, _tail = first_paren(chunk)
        if not detail:
            add(sym, name, "")
            continue
        for text in split_top(detail):
            add(sym, name, text)
    return out


def split_top(text: str) -> list[str]:
    """괄호 밖의 ` · ` 로만 자른다. 항목 안의 괄호를 건드리지 않기 위함이다."""
    parts: list[str] = []
    depth = 0
    buf = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in "(（":
            depth += 1
        elif ch in ")）":
            depth = max(0, depth - 1)
        if depth == 0 and text.startswith(" · ", i):
            parts.append(buf.strip())
            buf = ""
            i += 3
            continue
        buf += ch
        i += 1
    if buf.strip():
        parts.append(buf.strip())
    return [p for p in parts if p]


def item_parts(text: str) -> tuple[str, str]:
    """미해결 한 항목을 **이름 · 본문**으로 가른다 (85세션 1절).

    등재 서술은 언제나 **항목 끝의 괄호**에 담긴다. 그래서 끝에서 찾는다 —
    :func:`first_paren` 처럼 앞에서 찾으면 **이름 안의 괄호**를 본문으로
    오인한다. ②-6 「갑Ⅰ·교육용(갑)의 전압별 기본요금 기준」 이 그 자리다:
    앞에서 찾으면 본문이 「갑」 한 자가 되고 **이름의 뒤 절반이 통째로
    사라진다.** 서술이 없는 항목(「청구서 4」)은 본문이 빈 글이다.

    :func:`first_paren` 을 고치지 않는다 — 갈래 머리말은 **앞** 괄호가 목록이
    맞고, 71·72세션이 그 자리에 못을 박아 두었다.
    """
    text = text.strip()
    if not text.endswith((")", "）")):
        return text, ""
    depth = 0
    for i in range(len(text) - 1, -1, -1):
        if text[i] in ")）":
            depth += 1
        elif text[i] in "(（":
            depth -= 1
            if depth == 0:
                return text[:i].strip(), text[i + 1 : -1].strip()
    return text, ""


#: 문장 끝. **한가운데서 끊지 않으려고** 문장 단위로 담는다 (85세션 1절).
_SENTENCE = re.compile(r"(?<=\.)\s+")


def clip_body(text: str, first: str, rest: str, tag: str) -> list[str]:
    """미해결 항목 **본문**을 :data:`ITEM_BODY_LINES` 줄까지 담는다 (85세션 1절).

    **자르는 것은 본문뿐이다.** 이름 줄은 부르는 쪽이 이미 온전히 실었다.

    **문장 한가운데서 끊지 않는다.** 줄 수가 상한을 넘지 않는 데까지 문장을
    담는다 — 09-02 브리핑은 ②-13 을 「여덟 종별 단가 · 계절 구분 ·」 에서
    끊었고, 그 조각만 읽으면 무슨 말인지 알 수 없다. 첫 문장 하나가 이미
    상한을 넘으면 그것만 낱말 자리에서 접어 싣는다.

    **자른 자리에 무엇이 잘렸는지와 전문이 어디 있는지를 남긴다.** 잘린 줄
    자체가 없으면 브리핑을 읽는 쪽은 **본문이 원래 그만큼인 줄 안다.**
    """
    text = text.strip()
    if not text:
        return []
    kept = ""
    for sentence in _SENTENCE.split(text):
        trial = f"{kept} {sentence}".strip()
        if kept and len(wrap(trial, first, rest)) > ITEM_BODY_LINES:
            break
        kept = trial
    if not kept:  # 첫 문장 하나가 이미 상한을 넘는다 — 낱말 자리에서 접는다
        kept = " ".join(line.strip() for line in wrap(text, first, rest)[:ITEM_BODY_LINES])
    lines = wrap(kept, first, rest)[:ITEM_BODY_LINES]
    left = len(text) - len(kept)
    if left > 0:
        lines.append(f"{rest}… {tag} 본문 {left}자를 줄였다. 전문은 PROCEED.md 「현재 상태」")
    return lines


# ── 브리핑 조립 ──────────────────────────────────────────────────────────


def git(*args: str) -> str:
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def build() -> str:
    body = read_proceed()
    state = current_state(body)
    title, detail = latest_session(body)
    lines: list[str] = []

    head = git("rev-parse", "--short", "HEAD") or "?"
    subject = git("log", "-1", "--format=%s") or "?"
    # -uall 이 있어야 파일 수다. 없으면 추적 안 되는 디렉터리가 한 줄로 접혀
    # 「변경 2건」 이 실은 파일 3개인 일이 생긴다.
    dirty = git("status", "--porcelain", "-uall")
    mark = f"  (커밋 안 된 파일 {len(dirty.splitlines())}개)" if dirty else ""
    lines.append(f"# 오늘의 출발점 — HEAD {head}{mark}")
    lines.append(f"  {clip(subject)}")
    # **행이 사라지면 어느 행인지 말한다** (72세션 3절). 68세션 장치는 미해결이
    # 0건일 때만 걸려, 70세션에 「다음 작업」 이 사라졌을 때는 아무 소리도 안 났다.
    for name in unread_rows(body):
        lines.append(f"  **「{name}」 행을 못 읽었다** — 칸 안의 세로줄은 \\| 로 적는다")
    lines.append("")

    # 직전 세션이 무엇을 했나 — 세 줄. **「어제」 가 아니다** (68세션 2절) —
    # 하루에 두 세션이 돌면 「어제」 가 오늘을 가리킨다. 날짜는 괄호에 있다.
    lines.append(f"## 직전 세션 — {title}" if title else "## 직전 세션")
    for seg in split_top(strip_md(detail))[:3]:
        lines.append(f"  · {clip(seg, WRAP_AT - 4)}")
    lines.append("")

    # 미해결 — 갈래 · 번호 · 한 줄. **자르지 않는다** (아래 KEEP_WHOLE).
    items = open_items(state)
    if not items:
        # **조용한 0 을 막는다.** 칸에 세로줄(|)이 하나 섞이면 그 표 행이 통째로
        # 사라지는데, 그때 「미해결 0건」 이 아무 말 없이 나왔다 (68세션 1절).
        lines.append("## 미해결 — **칸을 못 읽었다.** PROCEED.md 「현재 상태」 의 미해결 행을 보라")
        lines.append("   (칸 안에 세로줄 | 이 있으면 그 행이 통째로 사라진다)")
    else:
        lines.append(f"## 미해결 {total_items(items)}건")
        # **갈래 하나만 사라지면 수만 줄어 조용하다** (69세션 2절).
        gone = missing_groups(state.get("미해결", ""), items)
        if gone:
            lines.append(
                f"  **갈래 {' · '.join(gone)} 를 못 읽었다** — 표식 뒤에 빈칸이 있는지 보라"
            )
        # **목록이 괄호 밖으로 밀려나면 말한다** (72세션 3절).
        for sym in hijacked_groups(state.get("미해결", "")):
            lines.append(
                f"  **갈래 {sym} 의 목록이 괄호 밖으로 밀렸다** — 머리말 다음 괄호가 목록이다"
            )
    seen: set[str] = set()
    for item in items:
        if item.sym not in seen:
            seen.add(item.sym)
            lines.append(f"  {item.sym} {item.name}")
        first = f"    {item.tag}. "
        rest = " " * len(first)
        # **이름은 온전히 · 본문만 줄인다** (85세션 1절).
        name, body = item_parts(item.text or item.name)
        lines.extend(wrap(name, first, rest))
        lines.extend(clip_body(body, rest, rest, item.tag))
    if state.get("블로커", "").strip(" —-"):
        lines.extend(wrap(strip_md(state["블로커"]).lstrip("— "), "  블로커 — ", "    "))
    else:
        lines.append("  블로커 없음")
    lines.append("")

    # 오늘 첫 작업과 근거. **첫 작업은 자르지 않는다** — 후보 원문이 여기 있다.
    lines.append("## 오늘 첫 작업")
    nxt = strip_md(state.get("다음 작업", ""))
    if not nxt:
        # **그럴듯한 기본값을 두지 않는다** (72세션 3절). 70세션에 이 행이
        # 사라졌을 때 「정해지지 않았다 — 지시서를 기다린다」 가 떴다. 그 말은
        # **읽히는 말**이라 아무도 이상하게 보지 않았다 — 네 사고 가운데 가장
        # 나빴던 까닭이다. **없으면 없다고 말한다.**
        nxt = "**「다음 작업」 을 못 읽었다** — PROCEED.md 「현재 상태」 의 그 행을 보라"
    lines.extend(wrap(nxt, "  ", "  "))
    for key in ("테스트 상태", "케이스 스터디", "화면 감사"):
        if key in state:
            lines.append(f"  근거 · {key} — {clip(strip_md(state[key]), WRAP_AT - 14)}")
    lines.append("")

    # 내가 밟아야 할 것 — 사람이 움직여야 풀리는 갈래만.
    # **위 목록과 같은 표식을 쓴다** (69세션 1절). 예전에는 갈래 이름을 깎아
    # 「[자료]」 를 붙였는데, 그것은 바로 위 머리말을 되풀이할 뿐이고 어느
    # 항목인지는 못 가리켰다 — 한 문서에 표식이 두 벌이면 결함 유형 ③ 이다.
    mine = [item for item in items if item.sym in ("①", "③")]
    if mine or "캡처" in nxt:
        lines.append("## 내가 밟아야 할 것")
        if "캡처" in nxt:
            lines.append(r"  · 화면 캡처를 떠 준다 (docs\CAPTURES.md 목록)")
        for item in mine:
            lines.append(f"  · {item.tag} {clip(item.text or item.name, WRAP_AT - 10)}")

    while lines and not lines[-1].strip():
        lines.pop()

    # **전역 한도를 두지 않는다** (85세션 1절). 여기서 자르면 그것이 곧 뒤 칸을
    # 죽이는 자리다 — 예산은 :data:`ITEM_BODY_LINES` 로 미해결 항목 본문에만 있다.
    return "\n".join(lines) + "\n"


def to_clipboard(text: str) -> bool:
    """clip.exe 는 UTF-16LE 를 받는다. cp949 로 보내면 한글이 깨진다.

    BOM 은 붙이지 않는다 — clip.exe 가 BOM 없이도 UTF-16LE 로 읽고,
    붙이면 U+FEFF 가 붙여넣기 첫 글자로 딸려 나온다.
    """
    try:
        p = subprocess.run(["clip"], input=text.encode("utf-16-le"), timeout=15)
        return p.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="아침 브리핑을 만든다")
    ap.add_argument("--no-clip", action="store_true", help="클립보드 복사를 생략한다")
    ap.add_argument("--out", type=Path, default=None, help="파일로도 저장한다")
    args = ap.parse_args()

    text = build()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    print(text, end="")

    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"\n[저장] {args.out}")
    if not args.no_clip:
        ok = to_clipboard(text)
        print(f"\n[클립보드] {'복사했다 — 새 대화에 붙여넣는다' if ok else '복사 실패'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
