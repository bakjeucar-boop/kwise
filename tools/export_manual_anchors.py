"""매뉴얼 앵커 목록을 문서로 내보낸다 (요구사항서 10.2).

    .venv\\Scripts\\python.exe tools\\export_manual_anchors.py

앵커 이름의 정본은 :data:`kwise.ui.anchors.ANCHORS` 다. 이 스크립트는
:func:`kwise.ui.anchors.anchor_document` 가 만든 글을 파일로 옮길 뿐이며,
**두 벌이 어긋나지 않는지 테스트가 지킨다** (``tests\\test_ui.py``).

10세션에서 매뉴얼을 쓸 때 이 목록의 ``id`` 를 그대로 쓰면 화면의 [자세히]
링크가 저절로 살아난다.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from kwise.ui.anchors import ANCHOR_DOC_FILENAME, ANCHORS, anchor_document

__all__ = ["main"]


def main() -> None:
    parser = argparse.ArgumentParser(description="매뉴얼 앵커 목록을 문서로 내보낸다")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs") / ANCHOR_DOC_FILENAME,
        help="내보낼 경로",
    )
    args = parser.parse_args()
    path: Path = args.output
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(anchor_document(), encoding="utf-8")
    print(f"앵커 {len(ANCHORS)}개를 {path} 에 적었습니다.")


if __name__ == "__main__":
    main()
