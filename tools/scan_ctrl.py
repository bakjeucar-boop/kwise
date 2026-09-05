r"""저장소에 남은 제어문자를 **문자 단위로** 훑는다 (66세션 3절 · 67세션 2절).

    .venv\Scripts\python.exe tools\scan_ctrl.py

**왜 도구로 두는가.** 셸에 문자열을 실어 파일을 고치면 백스페이스(U+0008)나
페이지 넘김(U+000C)이 조용히 박힌다 — 33세션에 시험 둘이 죽었고, 65세션에
`PROCEED.md` 가 오염됐다. 금지는 `CLAUDE.md` 에 적혀 있으나 **글로만 있는
금지는 뜨지 않는 경고와 같다.** 이것이 그 검사다.

**눈으로는 안 보인다.** U+0008·U+000C 는 화면에 자국을 안 남긴다.

**탭(U+0009)도 센다** (S126 · ②-18). 앞서는 탭을 빼 두어 `tests` + 탭 +
`est_document.py` 가 박힌 자리를 **0곳이라 했다** — 84·85세션이 눈으로 찾아낸
것을 도구가 못 봤다. 탭은 자국을 남기지만 **그 자국이 여백과 구별되지 않는다.**
저장소의 탭은 0개이므로(S126 3절에 세었다) 새로 박히면 그 자리에서 걸린다.

**`splitlines()` 로 세면 적게 센다.** U+000C 를 줄바꿈으로 삼켜 줄 안에서
안 보이기 때문이다 — 65세션이 그것에 걸려 일곱을 다섯으로 셌다. 그래서
줄로 자르지 않고 **글 전체를 문자로** 훑는다.

**약관 원문(`data\source`)의 U+000C 는 쪽 구분이라 정상이다.** :func:`scan`
은 그것까지 다 내고, 가르는 일은 :func:`is_source_text` 가 한다 — 거른 수를
함께 찍어야 「거르는 규칙이 아직 살아 있는가」 를 볼 수 있다.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent

SUFFIXES = frozenset({".py", ".md", ".toml", ".txt", ".json", ".cfg", ".ps1", ".yaml", ".yml"})
SKIP_DIRS = frozenset(
    {
        ".venv",
        ".git",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "__pycache__",
        "cache",
        "input",
        "output",
    }
)
#: PDF 에서 뽑은 약관·규칙 원문. 여기 U+000C 는 **쪽 구분이라 정상이다.**
SOURCE_TEXT = Path("data") / "source"

#: 개행·복귀만 남기고 C0 제어문자를 잡는다. **탭(U+0009)도 센다** (S126 · ②-18).
_CONTROL = re.compile(r"[\x00-\x09\x0b\x0c\x0e-\x1f]")


class Hit(NamedTuple):
    """제어문자가 박힌 한 자리."""

    path: Path
    """훑기 시작한 뿌리 기준 **상대 경로**."""
    line: int
    code: int

    def __str__(self) -> str:
        return f"{self.path}:{self.line}  U+{self.code:04X}"


def is_source_text(path: Path) -> bool:
    """약관 원문인가. 인자는 :func:`scan` 이 내는 **상대 경로**다."""
    return SOURCE_TEXT in path.parents


def _files(folder: Path) -> Iterator[Path]:
    """훑을 파일. **가지를 치면서 내려간다** — `rglob` 은 `.venv` 까지 다 걸어
    4.7초가 걸렸다 (67세션 2절). 안 볼 폴더는 들어가지 않는다."""
    for entry in sorted(folder.iterdir()):
        if entry.is_dir():
            if entry.name not in SKIP_DIRS:
                yield from _files(entry)
        elif entry.suffix in SUFFIXES:
            yield entry


def scan(root: Path) -> list[Hit]:
    """``root`` 아래에서 제어문자가 박힌 자리를 **전부** 낸다.

    거르지 않는다 — 약관 원문을 가르는 것은 부르는 쪽 몫이다.
    """
    hits: list[Hit] = []
    for path in _files(root):
        relative = path.relative_to(root)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for found in _CONTROL.finditer(text):
            line = text.count("\n", 0, found.start()) + 1
            hits.append(Hit(relative, line, ord(found.group())))
    return hits


def main() -> int:
    hits = scan(ROOT)
    skipped = [hit for hit in hits if is_source_text(hit.path)]
    left = [hit for hit in hits if not is_source_text(hit.path)]
    for hit in left:
        print(f"  {hit}")
    # **탭을 갈라 적는다** (S126 3절). 한 수로 뭉치면 「탭도 세는가」 를 값으로
    # 볼 수 없다 — ②-18 이 열려 있던 내내 그 수가 0 이었다.
    tabs = [hit for hit in left if hit.code == 0x09]
    print(
        f"남은 자리 {len(left)}곳 (제어문자 {len(left) - len(tabs)} · 탭 {len(tabs)})"
        f" · 약관 원문에서 건너뛴 것 {len(skipped):,}곳(정상)"
    )
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
