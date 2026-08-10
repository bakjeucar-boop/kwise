"""세션 임시 파일 (요구사항서 10.2).

**업로드 데이터를 서버에 영구 저장하지 않는다.** 업로드 바이트는 메모리에만 두고,
디스크로 내려가는 것은 내려받기용 Excel 한 벌뿐이다. 그마저도 바이트로 읽은 즉시
지운다 — 브라우저로 내보낸 뒤 서버에 남길 이유가 없다.

Streamlit 은 "세션이 끝났다" 를 알려 주지 않으므로 정리를 두 갈래로 둔다.

    ① 만들자마자 지운다     :func:`build_report_bytes`
    ② 시작할 때 묵은 것을 쓸어낸다  :func:`purge_stale` — ①이 예외로 건너뛰었을 때의 그물

이 모듈은 Streamlit 을 import 하지 않는다.
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import tempfile
from collections.abc import Iterable
from pathlib import Path

from kwise.report import ReportSections, build_sheets, write_workbook

__all__ = [
    "SESSION_PREFIX",
    "STALE_AFTER_HOURS",
    "build_report_bytes",
    "cleanup",
    "purge_stale",
    "remove_all",
    "report_filename",
    "session_dir",
    "session_root",
]

SESSION_PREFIX = "kwise_ui_"
STALE_AFTER_HOURS = 6.0


def session_root() -> Path:
    """세션 임시 폴더의 뿌리. ``PROJECT_CACHE`` 를 따르고 없으면 OS 임시 폴더."""
    override = os.environ.get("PROJECT_CACHE")
    base = Path(override) if override else Path(tempfile.gettempdir())
    return base / "ui_sessions"


def session_dir(session_id: str, *, root: Path | None = None) -> Path:
    """세션 하나의 임시 폴더. 없으면 만든다."""
    target = (root if root is not None else session_root()) / f"{SESSION_PREFIX}{session_id}"
    target.mkdir(parents=True, exist_ok=True)
    return target


def cleanup(path: Path) -> None:
    """폴더를 지운다. 지우지 못해도 화면을 멈추지 않는다."""
    shutil.rmtree(path, ignore_errors=True)


def purge_stale(
    root: Path | None = None,
    *,
    max_age_hours: float = STALE_AFTER_HOURS,
    now: dt.datetime | None = None,
) -> tuple[Path, ...]:
    """묵은 세션 폴더를 쓸어낸다. 지운 것을 돌려준다.

    앱을 켤 때 한 번 부른다. 지난 실행이 예외로 끝나 남긴 것을 여기서 거둔다.
    """
    base = root if root is not None else session_root()
    if not base.is_dir():
        return ()
    reference = now if now is not None else dt.datetime.now()
    limit = reference - dt.timedelta(hours=max_age_hours)
    removed: list[Path] = []
    for path in sorted(base.iterdir()):
        if not path.is_dir() or not path.name.startswith(SESSION_PREFIX):
            continue
        modified = dt.datetime.fromtimestamp(path.stat().st_mtime)
        if modified < limit:
            cleanup(path)
            removed.append(path)
    return tuple(removed)


def report_filename(now: dt.datetime | None = None, *, prefix: str = "kwise") -> str:
    """**날짜·시각 접미사를 붙인다.** Excel 이 열고 있으면 덮어쓰기가 실패한다."""
    stamp = (now if now is not None else dt.datetime.now()).strftime("%Y%m%d_%H%M")
    return f"{prefix}_{stamp}.xlsx"


def build_report_bytes(
    sections: ReportSections,
    *,
    session_id: str,
    root: Path | None = None,
    now: dt.datetime | None = None,
) -> tuple[bytes, str]:
    """Excel 을 만들어 **바이트로 읽고 파일은 지운다**.

    Returns:
        (내용, 내려받기 파일명)
    """
    directory = session_dir(session_id, root=root)
    name = report_filename(now)
    path = directory / name
    try:
        write_workbook(build_sheets(sections), path)
        return path.read_bytes(), name
    finally:
        path.unlink(missing_ok=True)


def remove_all(paths: Iterable[Path]) -> None:
    for path in paths:
        cleanup(path)
