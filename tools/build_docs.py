"""문서 생성 CLI (요구사항서 13장).

    .venv\\Scripts\\python.exe tools\\build_docs.py

``docs\\TECHNICAL.md`` · ``docs\\MANUAL.md`` 에서 html 두 개를 만든다.
**md 가 원본이고 html 은 생성물이다** — html 을 손으로 고치지 마십시오.

``MANUAL.html`` 이 생기는 순간 화면의 [자세히] 링크가 살아난다
(:func:`kwise.ui.anchors.manual_available` 이 파일 존재로 판정한다).
그래서 이 스크립트는 **화면이 부르는 앵커가 매뉴얼에 모두 있는지** 함께
확인하고, 없으면 종료 코드 1 로 멈춘다 — 죽은 링크를 화면에 내지 않는다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from kwise.docsite import DEFAULT_DOCS, build_page, collect_anchors
from kwise.ui.anchors import ANCHORS, STATIC_DIRNAME, anchor_keys

console = Console()

# Streamlit 정적 서빙 설정. 없으면 화면의 [자세히] 링크가 404 로 간다.
_CONFIG = """\
# 화면의 [자세히] 링크가 static\\MANUAL.html 로 갑니다 (요구사항서 13장).
# 이 설정이 없으면 Streamlit 이 파일을 내주지 않아 링크가 404 가 됩니다.
[server]
enableStaticServing = true
"""


def publish_static(pages: tuple[Path, ...], root: Path) -> Path:
    """산출물을 ``static\\`` 에 복사한다. **Streamlit 이 여기만 내준다.**"""
    target = root / STATIC_DIRNAME
    target.mkdir(parents=True, exist_ok=True)
    for page in pages:
        (target / page.name).write_text(page.read_text(encoding="utf-8"), encoding="utf-8")
    config = root / ".streamlit" / "config.toml"
    if not config.is_file():
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(_CONFIG, encoding="utf-8")
    elif "enableStaticServing" not in config.read_text(encoding="utf-8"):
        console.print(
            "[yellow].streamlit\\config.toml 에 enableStaticServing = true 를 넣으십시오.[/yellow]"
        )
    return target


def check_anchors(manual: Path) -> tuple[str, ...]:
    """매뉴얼에 없는 앵커를 돌려준다. 비어 있으면 통과다."""
    present = set(collect_anchors(manual.read_text(encoding="utf-8")))
    return tuple(key for key in anchor_keys() if key not in present)


def main() -> None:
    parser = argparse.ArgumentParser(description="Markdown 문서를 단일 HTML 로 만든다")
    parser.add_argument("--docs-dir", type=Path, default=Path("docs"))
    parser.add_argument(
        "--root", type=Path, default=Path(), help=r"static\ 과 .streamlit\ 을 둘 뿌리"
    )
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

    built: list[Path] = []
    for source, target, title in DEFAULT_DOCS:
        page = build_page(base / source, base / target, title)
        built.append(page.target)
        table.add_row(
            source,
            target,
            f"{len(page.headings)}",
            f"{page.target.stat().st_size / 1024:,.0f} KB",
        )
    console.print(table)

    static = publish_static(tuple(built), args.root)
    console.print(f"[dim]정적 사본 — {static}[/dim]")

    manual = base / "MANUAL.md"
    if args.skip_anchor_check or not manual.is_file():
        return
    missing = check_anchors(manual)
    if missing:
        console.print(f"[red]매뉴얼에 없는 앵커 {len(missing)}건[/red] — 화면 링크가 죽습니다.")
        for key in missing:
            console.print(f"  · {key}")
        sys.exit(1)
    console.print(
        f"[green]앵커 {len(ANCHORS)}개가 모두 매뉴얼에 있습니다.[/green] 화면 링크가 삽니다."
    )


if __name__ == "__main__":
    main()
