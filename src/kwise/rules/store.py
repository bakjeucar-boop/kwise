"""기준 데이터 저장소 — 세 층 (요구사항서 12장).

    data\\defaults\\   출고 기본값. 저장소에 커밋한다. **읽기 전용**
    data\\            현재 값. 사용자가 편집한다
    data\\backup\\     편집 직전 자동 스냅샷. ``.gitignore`` 대상

**코드에 기본값을 두지 않는다.** 파일이 없으면 ``defaults`` 에서 복사해 만들고,
``defaults`` 마저 없으면 :class:`RuleDataError` 로 **명확히 실패**한다. 코드에
기본값이 남아 있으면 파일을 고쳐도 반영되지 않는 사고가 나고, 그 사고는 값이
그럴듯하기 때문에 발견이 매우 어렵다.

**손상되면 조용히 넘어가지 않는다.** JSON 파싱에 실패하면 최근 백업 → 출고값
순으로 복구를 시도하고, 어느 쪽이든 :attr:`RuleSet.recovered_from` 에 남겨
사용자에게 알린다. 조용히 출고값으로 되돌아가면 **갱신한 값으로 계산되는 줄 알고**
결과를 쓰게 된다.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from kwise.rules.schema import (
    ASSUMPTIONS_FILENAME,
    RULES_FILENAME,
    RuleChange,
    RuleDataError,
    RuleOrigin,
    RuleSet,
)

__all__ = [
    "BACKUP_KEEP",
    "HISTORY_FILENAME",
    "append_history",
    "backup_dir",
    "data_dir",
    "defaults_dir",
    "ensure_initialised",
    "list_backups",
    "read_history",
    "read_ruleset",
    "restore_defaults_file",
    "restore_latest_backup",
    "write_backup",
    "write_ruleset",
]

HISTORY_FILENAME = "rules_history.jsonl"
# 백업 보관 개수. 더 두면 어느 것이 직전인지 고르기 어려워진다.
BACKUP_KEEP = 10

_BACKUP_STAMP = "%Y%m%d_%H%M"


def data_dir(root: Path | None = None) -> Path:
    """현재 값이 있는 곳. ``KWISE_TARIFF_DIR`` 로 옮길 수 있다."""
    if root is not None:
        return Path(root)
    override = os.environ.get("KWISE_TARIFF_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "data"


def defaults_dir(root: Path | None = None) -> Path:
    """출고 기본값. **절대 덮어쓰지 않는다.**"""
    return data_dir(root) / "defaults"


def backup_dir(root: Path | None = None) -> Path:
    """편집 직전 스냅샷."""
    return data_dir(root) / "backup"


def history_path(root: Path | None = None) -> Path:
    return data_dir(root) / HISTORY_FILENAME


def _filename(origin: RuleOrigin) -> str:
    return RULES_FILENAME if origin is RuleOrigin.STATUTORY else ASSUMPTIONS_FILENAME


def current_path(origin: RuleOrigin, root: Path | None = None) -> Path:
    return data_dir(root) / _filename(origin)


def default_path(origin: RuleOrigin, root: Path | None = None) -> Path:
    return defaults_dir(root) / _filename(origin)


# --------------------------------------------------------------------- 초기화


def ensure_initialised(origin: RuleOrigin, root: Path | None = None) -> Path:
    """현재 값 파일이 없으면 출고값에서 복사해 만든다.

    Raises:
        RuleDataError: 출고값마저 없을 때. **여기서 명확히 멈춘다.**
    """
    target = current_path(origin, root)
    if target.is_file():
        return target
    source = default_path(origin, root)
    if not source.is_file():
        raise RuleDataError(
            f"기준 데이터가 없습니다: {target}\n"
            f"출고 기본값({source})도 없어 만들 수 없습니다. "
            "저장소에서 data\\defaults\\ 를 복원하십시오. "
            "**코드에 기본값을 두지 않으므로 이 파일 없이는 계산할 수 없습니다.**"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


# --------------------------------------------------------------------- 읽기


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        payload = json.load(stream)
    if not isinstance(payload, dict):
        raise RuleDataError(f"기준 데이터가 객체가 아닙니다: {path}")
    return payload


def read_ruleset(origin: RuleOrigin, root: Path | None = None) -> RuleSet:
    """현재 값을 읽는다. 손상되면 **백업 → 출고값** 순으로 복구한다.

    복구했으면 :attr:`RuleSet.recovered_from` 이 채워진다. 호출자는 그 사실을
    반드시 사용자에게 보여야 한다.
    """
    path = ensure_initialised(origin, root)
    try:
        return RuleSet.from_payload(_load_json(path), origin, path=path)
    except (json.JSONDecodeError, RuleDataError, OSError) as first:
        broken = str(first)

    for candidate, label in _recovery_candidates(origin, root):
        try:
            payload = _load_json(candidate)
            recovered = RuleSet.from_payload(payload, origin, path=path)
        except (json.JSONDecodeError, RuleDataError, OSError):
            continue
        # 복구본을 현재 값으로 되돌려 놓는다. 손상 파일은 옆에 남긴다.
        damaged = path.with_suffix(path.suffix + ".damaged")
        try:
            shutil.copy2(path, damaged)
        except OSError:  # pragma: no cover - 원본을 못 읽는 경우
            damaged = path
        shutil.copy2(candidate, path)
        from dataclasses import replace

        return replace(
            recovered,
            recovered_from=(
                f"{label}({candidate.name}) 에서 복구했습니다. "
                f"손상된 파일은 {damaged.name} 으로 남겼습니다. 사유: {broken}"
            ),
        )

    raise RuleDataError(
        f"기준 데이터가 손상되었고 복구할 백업·출고값도 없습니다: {path}\n사유: {broken}"
    )


def _recovery_candidates(origin: RuleOrigin, root: Path | None) -> Iterator[tuple[Path, str]]:
    """복구 후보. **최근 백업 먼저, 그 다음 출고값.**"""
    for backup in list_backups(origin, root):
        yield backup, "백업"
    fallback = default_path(origin, root)
    if fallback.is_file():
        yield fallback, "출고 기본값"


# --------------------------------------------------------------------- 백업


def list_backups(origin: RuleOrigin, root: Path | None = None) -> list[Path]:
    """최신순 백업 목록."""
    folder = backup_dir(root)
    if not folder.is_dir():
        return []
    stem = Path(_filename(origin)).stem
    return sorted(folder.glob(f"{stem}_*.json"), reverse=True)


def write_backup(
    origin: RuleOrigin,
    root: Path | None = None,
    *,
    now: dt.datetime | None = None,
    keep: int = BACKUP_KEEP,
) -> Path | None:
    """현재 값을 백업한다. 현재 값이 없으면 아무것도 하지 않는다."""
    source = current_path(origin, root)
    if not source.is_file():
        return None
    stamp = (now or dt.datetime.now()).strftime(_BACKUP_STAMP)
    folder = backup_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    stem = Path(_filename(origin)).stem
    target = folder / f"{stem}_{stamp}.json"
    # **같은 분에 두 번 저장해도 덮어쓰지 않는다.** 덮어쓰면 편집 직전 상태가
    # 사라져 '직전으로 되돌리기' 가 방금 만든 상태를 되돌리게 된다.
    suffix = 0
    while target.exists():
        suffix += 1
        target = folder / f"{stem}_{stamp}_{suffix}.json"
    shutil.copy2(source, target)
    _prune_backups(origin, root, keep=keep)
    return target


def _prune_backups(origin: RuleOrigin, root: Path | None, *, keep: int) -> None:
    for stale in list_backups(origin, root)[keep:]:
        stale.unlink(missing_ok=True)


# --------------------------------------------------------------------- 쓰기


def write_ruleset(ruleset: RuleSet, root: Path | None = None) -> Path:
    """현재 값 파일을 쓴다. **백업은 호출자가 먼저 남긴다.**"""
    target = current_path(ruleset.origin, root)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as stream:
        json.dump(ruleset.to_payload(), stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    return target


def restore_defaults_file(origin: RuleOrigin, root: Path | None = None) -> Path:
    """출고값을 현재 값으로 되돌린다. **출고값은 손대지 않는다.**"""
    source = default_path(origin, root)
    if not source.is_file():
        raise RuleDataError(f"출고 기본값이 없습니다: {source}")
    target = current_path(origin, root)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def restore_latest_backup(origin: RuleOrigin, root: Path | None = None) -> Path:
    """가장 최근 백업으로 되돌린다."""
    backups = list_backups(origin, root)
    if not backups:
        raise RuleDataError(f"되돌릴 백업이 없습니다: {backup_dir(root)}")
    target = current_path(origin, root)
    shutil.copy2(backups[0], target)
    return target


# --------------------------------------------------------------------- 이력


def append_history(change: RuleChange, root: Path | None = None) -> Path:
    """편집 이력을 한 줄 덧붙인다.

    **UI 로 고친 값은 git 에 남지 않으므로 이 파일이 유일한 추적 수단이다.**
    """
    target = history_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as stream:
        json.dump(change.to_payload(), stream, ensure_ascii=False)
        stream.write("\n")
    return target


def read_history(root: Path | None = None, *, limit: int | None = None) -> list[RuleChange]:
    """편집 이력을 최신순으로 읽는다."""
    target = history_path(root)
    if not target.is_file():
        return []
    changes: list[RuleChange] = []
    with target.open(encoding="utf-8") as stream:
        for line in stream:
            text = line.strip()
            if not text:
                continue
            try:
                changes.append(RuleChange.from_payload(json.loads(text)))
            except (json.JSONDecodeError, KeyError, ValueError):
                continue  # 깨진 줄은 건너뛴다. 이력 때문에 계산이 멈추면 안 된다
    changes.reverse()
    return changes[:limit] if limit is not None else changes
