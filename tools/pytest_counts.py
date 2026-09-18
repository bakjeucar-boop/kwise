r"""pytest 출력에서 **수집 건수와 결과 수와 실패 이름**을 읽는다 (S207 4절).

``CLAUDE.md`` 9항 8번이 판마다 요구하는 일이다 — **종료 코드로 통과를 판정하지
않는다.** 읽을 것은 넷이다.

    수집 건수   앞 판과 같은가. **안 돈 시험은 실패 줄을 내지 않는다** (83세션)
    결과 수     passed · failed · xfailed · xpassed · **skipped 도 안 돈 시험이다**
    실패 이름   ``-rf`` 가 남기는 ``FAILED`` 줄 (S132 0절 — ``-rN`` 이 아니다)
    오류        ``ERROR`` 줄 — 수집이 통째로 깨진 자리

판마다 이 수를 세는 스크래치를 새로 지었다(S199~S206 여덟 판이 세는 자리에서
파이프로 샜다). 여기로 내린다.

    .venv\\Scripts\\python.exe tools\\pytest_counts.py                 가장 새 판을 읽는다
    .venv\\Scripts\\python.exe tools\\pytest_counts.py <출력 파일>
    .venv\\Scripts\\python.exe tools\\pytest_counts.py --base 1785     앞 판 수집 건수와 맞댄다

출력 파일은 ``tools\\run_tool.py`` 가 ``PROJECT_CACHE\\runs\\`` 에 받아 둔 것이다.
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

__all__ = ["Counts", "latest_run", "read", "runs_dir"]

#: ``collected 1787 items`` · ``1787 items`` · ``collected 1787 items / 2 deselected``
_COLLECTED = re.compile(r"(\d+)\s+items?\b")
#: 요약 줄의 ``N passed`` 꼴. **``-q`` 가 겹쳐 ``-qq`` 가 되면 이 줄이 사라진다** (67세션).
_RESULT = re.compile(r"(\d+)\s+(passed|failed|xfailed|xpassed|skipped|error|errors|deselected)")
#: pytest 가 인자 이름의 한글을 역슬래시 u 네 자리 꼴로 박아 넣는다 (테스트 ID 이스케이프).
#: **풀지 않으면 실패한 시험 이름을 사람이 못 읽는다** — 규약 9항 8번이 그 이름을 쓴다.
_ESCAPED = re.compile(r"\\u([0-9a-fA-F]{4})")


def _readable(line: str) -> str:
    return _ESCAPED.sub(lambda m: chr(int(m.group(1), 16)), line)


@dataclass(frozen=True)
class Counts:
    """한 판이 낸 수."""

    collected: int | None
    results: dict[str, int]
    failures: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    source: Path | None = None
    lines: int = 0
    notes: tuple[str, ...] = field(default=())

    @property
    def total(self) -> int:
        """결과 수의 합. 수집 건수와 어긋나면 안 돈 시험이 있다."""
        return sum(
            count for name, count in self.results.items() if name not in {"deselected", "errors"}
        )


def runs_dir() -> Path:
    raw = os.environ.get("PROJECT_CACHE")
    return (Path(raw) if raw else PROJECT_ROOT / "cache") / "runs"


def latest_run() -> Path | None:
    """``runs\\`` 에서 가장 새 pytest 출력. 없으면 ``None``."""
    found = sorted(runs_dir().glob("pytest_*.txt"), key=lambda path: path.stat().st_mtime)
    return found[-1] if found else None


def read(path: Path) -> Counts:
    """출력 파일 하나를 읽는다. **줄을 통째로 보고 마지막 값을 쓴다.**"""
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    # **마지막 값을 쓴다.** 꼴이 둘이다 — 맨 pytest 의 ``collected 1787 items`` 와
    # xdist 의 ``8 workers [1787 items]``. ``addopts = "-q"`` 면 앞 것이 안 나온다.
    collected: int | None = None
    for line in lines:
        found = _COLLECTED.search(line)
        if found and ("item" in line):
            collected = int(found.group(1))

    # 요약 줄. ``-q`` 면 ``===`` 로 안 둘리고 ``1 failed, 28 passed in 2.5s`` 한 줄이다.
    results: dict[str, int] = {}
    for line in lines:
        hits = _RESULT.findall(line)
        if hits and (line.startswith("=") or " in " in line):
            results = {{"error": "errors"}.get(name, name): int(count) for count, name in hits}

    failures = tuple(_readable(line.strip()) for line in lines if line.startswith("FAILED"))
    errors = tuple(_readable(line.strip()) for line in lines if line.startswith("ERROR"))

    notes: list[str] = []
    if not results:
        notes.append("결과 줄이 없다 — `-q` 가 겹쳐 `-qq` 가 됐는지 본다 (67세션)")
    if collected is None and results:
        # ``addopts = "-q"`` + xdist 판에는 ``N workers [M items]`` 줄조차 없다.
        # 그 판의 「수집 건수」 는 결과 합이다 — 기록이 그렇게 적어 왔다.
        notes.append("수집 줄이 없다 — `-q` + xdist 판이라 **결과 합이 수집 건수**다")
    if results.get("skipped"):
        notes.append(f"skip {results['skipped']}건 — **안 돈 시험이다.** 회귀 보고에 함께 적는다")
    돈것 = sum(count for name, count in results.items() if name not in {"deselected", "errors"})
    if collected is not None and results and collected != 돈것:
        notes.append(f"수집 {collected} 과 결과 합 {돈것} 이 어긋난다 — 안 돈 시험이 있다")
    return Counts(collected, results, failures, errors, path, len(lines), tuple(notes))


def main() -> int:
    parser = argparse.ArgumentParser(description="pytest 출력에서 수를 읽는다")
    parser.add_argument("path", nargs="?", type=Path, help="없으면 가장 새 판을 읽는다")
    parser.add_argument("--base", type=int, help="앞 판 수집 건수. 어긋나면 적는다")
    args = parser.parse_args()

    path = args.path or latest_run()
    if path is None:
        print(f"읽을 것이 없습니다 — {runs_dir()} 에 pytest_*.txt 가 없습니다")
        print("먼저 `tools\\run_tool.py pytest tests -rf --tb=no` 를 돌리십시오")
        return 1
    if not path.is_file():
        print(f"없는 파일입니다: {path}")
        return 1

    counts = read(path)
    print(f"{path}  ({counts.lines:,}줄)")
    print(f"수집  {counts.collected if counts.collected is not None else '못 읽었다'}")
    if counts.results:
        print("결과  " + " · ".join(f"{name} {count}" for name, count in counts.results.items()))
        print(f"합    {counts.total}")
    지금 = counts.collected if counts.collected is not None else (counts.total or None)
    if args.base is not None and 지금 is not None:
        gap = 지금 - args.base
        말 = "같다" if gap == 0 else f"{gap:+d}"
        print(f"앞 판  {args.base} → {지금} ({말})")
    if counts.failures:
        print(f"\n실패 {len(counts.failures)}건")
        for line in counts.failures:
            print(f"    {line}")
    if counts.errors:
        print(f"\n오류 {len(counts.errors)}건")
        for line in counts.errors:
            print(f"    {line}")
    for note in counts.notes:
        print(f"\n!! {note}")
    return 1 if (counts.failures or counts.errors) else 0


if __name__ == "__main__":
    raise SystemExit(main())
