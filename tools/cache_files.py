r"""캐시 폴더의 파일을 **이름 · 크기 · 수정 시각**으로 뜬다 (S228).

판마다 셸이나 스크래치로 떴다 — ``runs\`` 에서 앞 판 pytest 출력을 찾고(S226 ·
S228 1-1), 덱 기준선 스냅을 고르고(S224 · S225 0-7). ``deck_words`` 는 스냅
**이름**만 내고 ``run_tool`` 은 받는 자리만 낸다.

    .venv\Scripts\python.exe tools\cache_files.py              폴더마다 파일 수 · 크기 · 새 시각
    .venv\Scripts\python.exe tools\cache_files.py runs         새것부터 20개
    .venv\Scripts\python.exe tools\cache_files.py runs --glob "python_*" --last 5
    .venv\Scripts\python.exe tools\cache_files.py deck_words
    .venv\Scripts\python.exe tools\cache_files.py runs\도는중  도는 판의 자라는 파일

폴더는 ``PROJECT_CACHE``(기본 ``.\cache``) 아래 상대 경로다.
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def cache_root() -> Path:
    """캐시 뿌리. ``PROJECT_CACHE`` 가 있으면 그것, 없으면 ``.\\cache`` (규약)."""
    raw = os.environ.get("PROJECT_CACHE")
    return Path(raw) if raw else PROJECT_ROOT / "cache"


def _at(stamp: float) -> str:
    return f"{datetime.fromtimestamp(stamp):%Y-%m-%d %H:%M:%S}"


def main() -> int:
    parser = argparse.ArgumentParser(description="캐시 폴더의 파일 이름 · 크기 · 수정 시각")
    parser.add_argument("folder", nargs="?", help="캐시 아래 폴더 (없으면 폴더마다 요약)")
    parser.add_argument("--glob", default="*", help="이름 거르기 (기본 *)")
    parser.add_argument("--last", type=int, default=20, help="새것부터 몇 개 (0 이면 전부)")
    args = parser.parse_args()

    root = cache_root()
    if args.folder is None:
        print(f"캐시 — {root}")
        for folder in sorted(p for p in root.iterdir() if p.is_dir()):
            files = [p for p in folder.rglob("*") if p.is_file()]
            newest = max((p.stat().st_mtime for p in files), default=None)
            size = sum(p.stat().st_size for p in files)
            print(
                f"  {folder.name:24s} {len(files):>6,}파일  {size:>14,} B  "
                f"{_at(newest) if newest else '—'}"
            )
        return 0

    folder = root / args.folder
    if not folder.is_dir():
        print(f"폴더가 없습니다 — {folder}")
        return 1
    files = sorted(
        (p for p in folder.glob(args.glob) if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    shown = files[: args.last] if args.last > 0 else files
    print(f"{folder} — {len(files):,}파일 가운데 새것부터 {len(shown):,}")
    for path in shown:
        stat = path.stat()
        print(f"  {_at(stat.st_mtime)}  {stat.st_size:>12,} B  {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
