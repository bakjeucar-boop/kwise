r"""아침 브리핑을 만든다.

`PROCEED.md` 에서 오늘 시작에 필요한 것만 뽑아 50줄 안쪽으로 낸다.
새 대화의 첫 메시지로 붙이는 것이 용도다.

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
from pathlib import Path

# 50줄이 한도다. 매일 붙이는 것이라 길면 그 자체가 비용이다.
MAX_LINES = 50
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
    """한 줄을 폭에 맞춰 자른다. 자른 자리는 … 로 표시한다."""
    text = text.strip()
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"


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

GROUP = re.compile(r"[①②③④⑤]")


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
        # 「자료를 기다리는 것 8건 (청구서 4 · …)」 → 이름과 괄호 안을 가른다
        head, _, detail = chunk.partition("(")
        name = head.strip()
        detail = detail.rsplit(")", 1)[0] if detail else ""
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

    # 어제 무엇을 했나 — 세 줄
    lines.append(f"## 어제 — {title}" if title else "## 어제")
    for seg in split_top(strip_md(detail))[:3]:
        lines.append(f"  · {clip(seg, WRAP_AT - 4)}")
    lines.append("")

    # 미해결 — 갈래 · 번호 · 한 줄
    items = open_items(state)
    lines.append(f"## 미해결 {len(items)}건")
    seen: set[str] = set()
    for n, (sym, name, one) in enumerate(items, 1):
        if sym not in seen:
            seen.add(sym)
            lines.append(f"  {sym} {name}")
        lines.append(f"    {n:>2}. {clip(one or name, WRAP_AT - 8)}")
    if state.get("블로커", "").strip(" —-"):
        lines.append(f"  블로커 — {clip(strip_md(state['블로커']), WRAP_AT - 10)}")
    else:
        lines.append("  블로커 없음")
    lines.append("")

    # 오늘 첫 작업과 근거
    lines.append("## 오늘 첫 작업")
    nxt = strip_md(state.get("다음 작업", "")) or "정해지지 않았다 — 지시서를 기다린다"
    lines.append(f"  {clip(nxt, WRAP_AT - 2)}")
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
    ap = argparse.ArgumentParser(description="아침 브리핑을 만든다 (50줄 안쪽)")
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
