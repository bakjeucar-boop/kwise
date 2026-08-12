"""문서 생성 CLI (요구사항서 13장).

    .venv\\Scripts\\python.exe tools\\build_docs.py

``docs\\TECHNICAL.md`` · ``docs\\MANUAL.md`` 에서 html 두 개를 만든다.
**md 가 원본이고 html 은 생성물이다** — html 을 손으로 고치지 마십시오.

**화면은 이 산출물을 열지 않는다** (16세션 4절). 링크를 걷어내고 요지를
툴팁으로 옮겼으므로 정적 사본도 ``.streamlit`` 설정도 필요 없다.

그래도 **화면이 부르는 앵커가 매뉴얼에 모두 있는지** 확인하고, 없으면 종료
코드 1 로 멈춘다. 툴팁의 요지와 매뉴얼의 전문이 어긋나면 툴팁만 읽은 사람과
매뉴얼까지 읽은 사람이 다른 것을 알게 된다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from kwise.docsite import DEFAULT_DOCS, build_page, collect_anchors
from kwise.ui.anchors import ANCHORS, anchor_keys

console = Console()


def check_anchors(manual: Path) -> tuple[str, ...]:
    """매뉴얼에 없는 앵커를 돌려준다. 비어 있으면 통과다."""
    present = set(collect_anchors(manual.read_text(encoding="utf-8")))
    return tuple(key for key in anchor_keys() if key not in present)


def main() -> None:
    parser = argparse.ArgumentParser(description="Markdown 문서를 단일 HTML 로 만든다")
    parser.add_argument("--docs-dir", type=Path, default=Path("docs"))
    parser.add_argument(
        "--skip-anchor-check",
        action="store_true",
        help="앵커 대조를 건너뛴다 (매뉴얼을 쓰는 도중에만)",
    )
    args = parser.parse_args()
    base: Path = args.docs_dir

    table = Table(title="문서 생성")
    table.add_column("원본")
    table.add_column("산출물")
    table.add_column("절", justify="right")
    table.add_column("크기", justify="right")

    for source, target, title in DEFAULT_DOCS:
        page = build_page(base / source, base / target, title)
        table.add_row(
            source,
            target,
            f"{len(page.headings)}",
            f"{page.target.stat().st_size / 1024:,.0f} KB",
        )
    console.print(table)

    manual = base / "MANUAL.md"
    if args.skip_anchor_check or not manual.is_file():
        return
    missing = check_anchors(manual)
    if missing:
        console.print(
            f"[red]매뉴얼에 없는 앵커 {len(missing)}건[/red] — 요지만 있고 전문이 없습니다."
        )
        for key in missing:
            console.print(f"  · {key}")
        sys.exit(1)
    console.print(f"[green]앵커 {len(ANCHORS)}개가 모두 매뉴얼에 있습니다.[/green]")


if __name__ == "__main__":
    main()
