r"""한 사실을 만드는 자리를 **전수로** 센다 (S135 2절).

    .venv\Scripts\python.exe tools\count_sites.py
    .venv\Scripts\python.exe tools\count_sites.py "MWh 표기"

**왜 도구로 두는가.** 지금까지 이 셈은 판마다 사람이 `grep` 을 새로 지었다 —
그래서 판마다 그물이 다르고 수가 갈렸다. S130 리뷰가 회수기간을 다섯으로 셌는데
S134 가 다시 세니 **열다섯**이었고, 비중은 다섯이 **일곱**이었다. 두 판 다 같은
갈래를 빠뜨렸다 — 도구(`tools\` · `cli.py`) · **인라인**(값을 지역 이름 없이 그
자리에 적는 것) · **말로 적는 모듈**(`report\narrative.py`). 셋을 다 훑으려면
뿌리를 `src` 와 `tools` **통째로** 잡는 수밖에 없고, 그것을 사람 손에 두면 다음
판이 또 좁게 잡는다.

**그물은 자료에 둔다** — `tools\fact_sites.json`. 코드에 박으면 사실을 하나
더할 때마다 코드를 고치게 되고, 그러면 그물이 코드 이력에 섞여 안 읽힌다.

**이 도구가 못 보는 것.**

* **주석과 문자열을 가르지 않는다.** 줄을 글자로 볼 뿐이라 주석에 그물에 걸리는
  낱말을 적으면 그대로 센다 — S133·S134 가 값으로 확인한 성질이다.
* **여러 줄에 걸친 식은 그물이 걸리는 줄만 센다.** 한 자리를 두 줄로 적으면
  둘로 세거나(그물이 두 줄에 다 걸릴 때) 아예 못 센다(줄이 갈려 걸릴 때).
* **안 적는 자리는 못 본다.** 그물은 쓰인 것을 보지 빠진 것을 못 본다 —
  「부분 합 조각」 이 그 자리다(:data:`FACTS` 의 비고를 본다).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
FACTS = ROOT / "tools" / "fact_sites.json"

SKIP_DIRS = frozenset({"__pycache__", ".venv", ".git"})


class Site(NamedTuple):
    """사실을 만드는 한 자리."""

    path: Path
    """저장소 뿌리 기준 **상대 경로**."""
    line: int
    text: str
    home: bool
    """「한 자리」 로 인정한 파일인가."""

    def __str__(self) -> str:
        mark = "  " if self.home else "! "
        return f"{mark}{self.path}:{self.line}: {self.text}"


class Fact(NamedTuple):
    """세는 사실 하나."""

    name: str
    net: re.Pattern[str]
    homes: tuple[Path, ...]
    nailed: bool
    """못이 무는 사실인가 — 「한 자리 밖」 이 0 이어야 한다."""
    note: str


def load(path: Path = FACTS) -> tuple[tuple[Path, ...], list[Fact]]:
    """그물 자료를 읽는다. **뿌리도 자료에 있다** — 범위가 코드에 박히면 안 된다."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    roots = tuple(ROOT / name for name in raw["뿌리"])
    facts = [
        Fact(
            name=entry["이름"],
            net=re.compile(entry["그물"]),
            homes=tuple(Path(name) for name in entry["한 자리"]),
            nailed=bool(entry["못"]),
            note=entry["비고"],
        )
        for entry in raw["사실"]
    ]
    return roots, facts


def _sources(folder: Path) -> list[Path]:
    """훑을 파이썬 원본. `scan_ctrl` 과 같이 **가지를 치면서** 내려간다."""
    found: list[Path] = []
    for entry in sorted(folder.iterdir()):
        if entry.is_dir():
            if entry.name not in SKIP_DIRS:
                found.extend(_sources(entry))
        elif entry.suffix == ".py":
            found.append(entry)
    return found


def sites(fact: Fact, roots: tuple[Path, ...]) -> list[Site]:
    """``fact`` 를 만드는 자리 전수. **한 자리도 뺴지 않고 함께 낸다** —
    빼고 세면 「모았다」 와 「그물이 안 걸렸다」 를 구별할 수 없다."""
    found: list[Site] = []
    for root in roots:
        for path in _sources(root):
            relative = path.relative_to(ROOT)
            home = any(relative == candidate for candidate in fact.homes)
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if fact.net.search(line):
                    found.append(Site(relative, number, line.strip(), home))
    return found


def strays(fact: Fact, roots: tuple[Path, ...]) -> list[Site]:
    """「한 자리」 밖에 선 자리."""
    return [site for site in sites(fact, roots) if not site.home]


def main(argv: list[str]) -> int:
    roots, facts = load()
    wanted = set(argv[1:])
    if wanted:
        facts = [fact for fact in facts if fact.name in wanted]
        if not facts:
            print(f"그런 사실이 없다: {sorted(wanted)}")
            return 2
    print(f"뿌리 {' · '.join(str(root.relative_to(ROOT)) for root in roots)}")
    for fact in facts:
        found = sites(fact, roots)
        outside = [site for site in found if not site.home]
        files = {site.path for site in found}
        nail = " [못]" if fact.nailed else ""
        print(
            f"\n{fact.name} — 자리 {len(found)} (파일 {len(files)})"
            f" · 한 자리 밖 {len(outside)}{nail}"
        )
        print(f"  ※ {fact.note}")
        for site in found:
            print(f"  {site}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
