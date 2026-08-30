r"""아침 브리핑을 만든다.

`PROCEED.md` 에서 오늘 시작에 필요한 것만 뽑는다.
새 대화의 첫 메시지로 붙이는 것이 용도다.

**칸마다 자를지 접을지가 다르다** (68세션 2절). 이것은 아침에 읽는 요약이면서
**인수인계 매체**다 — 짧아야 읽히고 온전해야 넘겨진다. 그래서 셋은 접기만 하고
자르지 않는다: **오늘 첫 작업 · 미해결 항목 · 블로커.** 나머지(직전 세션 요약 ·
근거 넷)는 그대로 자른다.

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

# 한도는 **마지막 안전장치**다. 길면 그 자체가 비용이라 두되, 칸마다 자를지
# 접을지를 먼저 정한다 (:func:`clip` 과 :func:`wrap`). 50 이던 것을 68세션에
# 90 으로 올렸다 — 자르지 않을 칸 셋을 온전히 실으면서 **43줄 → 52줄**이 됐고,
# 50 이 그 뒤를 잘라 「내가 밟아야 할 것」 을 도로 지웠다.
MAX_LINES = 90
# 한 줄이 길면 터미널에서 접혀 줄 수가 어긋난다.
WRAP_AT = 96

ROOT = Path(__file__).resolve().parent.parent
PROCEED = ROOT / "PROCEED.md"


# ── PROCEED.md 읽기 ──────────────────────────────────────────────────────


def strip_md(text: str) -> str:
    """굵게·기울임·링크·코드 표시를 걷어낸다. 붙여 쓸 때는 평문이 낫다."""
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # 링크
    text = text.replace("**", "").replace("`", "")
    return re.sub(r"\s+", " ", text).strip()


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
    m = re.search(r"^## 현재 상태\s*$(.*?)^---", body, re.M | re.S)
    if not m:
        return {}
    rows: dict[str, str] = {}
    for line in m.group(1).splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 2 and cells[0] and not set(cells[0]) <= set("-: "):
            rows[strip_md(cells[0])] = cells[1]
    rows.pop("항목", None)
    return rows


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


def first_paren(text: str) -> tuple[str, str]:
    """이름과 **첫 괄호 짝 안**을 가른다 (68세션 2절).

    `partition("(")` + `rsplit(")")` 는 **첫 여는 괄호부터 마지막 닫는 괄호
    까지**를 잡는다. ① 은 목록 괄호가 닫힌 뒤에도 서술이 이어지므로 그
    서술이 통째로 항목 안에 들어왔다 — 마지막 항목이
    「실측 1). ①-4 「실측」 은 …」 이 됐다. **짝을 세어 닫는 자리를 찾는다.**
    """
    start = text.find("(")
    if start < 0:
        return text.strip(), ""
    depth = 0
    for i in range(start, len(text)):
        if text[i] in "(（":
            depth += 1
        elif text[i] in ")）":
            depth -= 1
            if depth == 0:
                return text[:start].strip(), text[start + 1 : i]
    return text[:start].strip(), text[start + 1 :]  # 안 닫혔으면 끝까지


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


def total_items(items: list[tuple[str, str, str]]) -> int:
    """미해결 **건수.** 갈래 머리말이 말하는 수를 더한다.

    줄을 세면 ① 이 4가 되는데 머리말은 7이다 — 「청구서 4」 한 줄이 넷을
    담기 때문이다. **머리말이 7이면 총계도 그 7을 담아야 한다.**
    """
    counted: dict[str, tuple[str, int]] = {}
    for sym, name, _ in items:
        head, seen = counted.get(sym, (name, 0))
        counted[sym] = (head, seen + 1)
    return sum(group_count(name, seen) for name, seen in counted.values())


def open_items(state: dict[str, str]) -> list[tuple[str, str, str]]:
    """미해결 칸을 (갈래기호, 갈래이름, 한 줄) 로 편다."""
    raw = state.get("미해결", "")
    if not raw:
        return []
    out: list[tuple[str, str, str]] = []
    # ① … ② … ③ … 로 자른다
    marks = list(GROUP.finditer(raw))
    for i, mk in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(raw)
        chunk = strip_md(raw[mk.start() : end]).strip(" ·")
        sym = chunk[0]
        chunk = chunk[1:].strip()
        # 「자료를 기다리는 것 7건 (청구서 4 · …)」 → 이름과 괄호 안을 가른다
        name, detail = first_paren(chunk)
        if not detail:
            out.append((sym, name, ""))
            continue
        for item in split_top(detail):
            out.append((sym, name, item))
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
    seen: set[str] = set()
    for n, (sym, name, one) in enumerate(items, 1):
        if sym not in seen:
            seen.add(sym)
            lines.append(f"  {sym} {name}")
        lines.extend(wrap(one or name, f"    {n:>2}. ", "        "))
    if state.get("블로커", "").strip(" —-"):
        lines.extend(wrap(strip_md(state["블로커"]).lstrip("— "), "  블로커 — ", "    "))
    else:
        lines.append("  블로커 없음")
    lines.append("")

    # 오늘 첫 작업과 근거. **첫 작업은 자르지 않는다** — 후보 원문이 여기 있다.
    lines.append("## 오늘 첫 작업")
    nxt = strip_md(state.get("다음 작업", "")) or "정해지지 않았다 — 지시서를 기다린다"
    lines.extend(wrap(nxt, "  ", "  "))
    for key in ("테스트 상태", "케이스 스터디", "화면 감사"):
        if key in state:
            lines.append(f"  근거 · {key} — {clip(strip_md(state[key]), WRAP_AT - 14)}")
    lines.append("")

    # 내가 밟아야 할 것 — 사람이 움직여야 풀리는 갈래만
    mine = [it for it in items if it[0] in ("①", "③")]
    if mine or "캡처" in nxt:
        lines.append("## 내가 밟아야 할 것")
        if "캡처" in nxt:
            lines.append(r"  · 화면 캡처를 떠 준다 (docs\CAPTURES.md 목록)")
        for sym, name, one in mine:
            tag = re.sub(r"[을를]\s*기다리는 것.*", "", name).strip() or sym
            lines.append(f"  · [{tag}] {clip(one or name, WRAP_AT - 10)}")

    while lines and not lines[-1].strip():
        lines.pop()

    if len(lines) > MAX_LINES:
        cut = len(lines) - (MAX_LINES - 1)
        lines = lines[: MAX_LINES - 1]
        lines.append(f"  … {cut}줄 줄였다. 전문은 PROCEED.md 「현재 상태」")
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
