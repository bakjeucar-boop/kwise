"""기준 데이터 (요구사항서 12장).

법령 유래 수치와 우리 판단값을 **코드에서 뽑아 파일로** 옮긴 곳이다.

    rule_value("power_factor.lagging_standard_pct")   법령 유래 (rules_kr.json)
    assumption("ess.round_trip")                      판단값 (assumptions.json)

**코드에 기본값을 두지 않는다.** 파일이 없으면 출고값(``data\\defaults\\``)에서
복사해 만들고, 그마저 없으면 :class:`RuleDataError` 로 멈춘다. 기본값을 코드에
남겨 두면 파일을 고쳐도 반영되지 않는 사고가 나는데, 값이 그럴듯하기 때문에
**결과를 다 쓰고 나서야 발견된다.**

편집은 :func:`set_value` · :func:`confirm` 로 한다. 둘 다

    ① 검증 → 실패하면 **저장하지 않고** 사유를 돌려준다
    ② 편집 직전 상태를 ``data\\backup\\`` 에 남긴다 (최근 10개)
    ③ ``data\\rules_history.jsonl`` 에 한 줄 남긴다
    ④ 캐시를 비워 다음 조회부터 새 값이 나오게 한다

를 지킨다. **④가 없으면 파일은 바뀌었는데 계산은 옛 값으로 도는 일이 생긴다.**
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

from kwise.rules.expiry import (
    SOURCE_LINKS,
    ExpiryWarning,
    check_expiry,
    source_link_of,
    weather_expiry,
)
from kwise.rules.schema import (
    ASSUMPTIONS_FILENAME,
    RULES_FILENAME,
    RuleChange,
    RuleDataError,
    RuleItem,
    RuleOrigin,
    RuleSet,
)
from kwise.rules.store import (
    BACKUP_KEEP,
    HISTORY_FILENAME,
    append_history,
    backup_dir,
    current_path,
    data_dir,
    default_path,
    defaults_dir,
    list_backups,
    read_history,
    read_ruleset,
    restore_defaults_file,
    write_backup,
    write_ruleset,
)
from kwise.rules.validate import ValidationIssue, validate_ruleset

__all__ = [
    "ASSUMPTIONS_FILENAME",
    "BACKUP_KEEP",
    "HISTORY_FILENAME",
    "RULES_FILENAME",
    "SOURCE_LINKS",
    "EditResult",
    "ExpiryWarning",
    "ItemDiff",
    "RuleChange",
    "RuleDataError",
    "RuleItem",
    "RuleOrigin",
    "RuleSet",
    "ValidationIssue",
    "assumption",
    "assumptions",
    "backup_dir",
    "check_expiry",
    "confirm",
    "data_dir",
    "defaults_dir",
    "describe_items",
    "diff_from_defaults",
    "expiry_warnings",
    "load_defaults",
    "read_history",
    "reload_rules",
    "restore_defaults",
    "restore_item",
    "restore_previous",
    "rule_value",
    "rules",
    "set_value",
    "source_link_of",
    "weather_expiry",
]


# --------------------------------------------------------------------- 조회


@lru_cache(maxsize=8)
def _cached(origin: RuleOrigin, root_key: str) -> RuleSet:
    return read_ruleset(origin, Path(root_key) if root_key else None)


def _root_key(root: Path | None) -> str:
    return str(root) if root is not None else ""


def rules(root: Path | None = None) -> RuleSet:
    """법령 유래 기준 데이터 (``rules_kr.json``)."""
    return _cached(RuleOrigin.STATUTORY, _root_key(root))


def assumptions(root: Path | None = None) -> RuleSet:
    """우리 판단값 (``assumptions.json``)."""
    return _cached(RuleOrigin.JUDGEMENT, _root_key(root))


def reload_rules() -> None:
    """캐시를 비운다. **편집 뒤에는 반드시 부른다.**"""
    _cached.cache_clear()


def rule_value(key: str, root: Path | None = None) -> Any:
    """법령 유래 값 하나. 없으면 :class:`RuleDataError`."""
    return rules(root).value(key)


def assumption(key: str, root: Path | None = None) -> Any:
    """판단값 하나. 없으면 :class:`RuleDataError`."""
    return assumptions(root).value(key)


def load_defaults(origin: RuleOrigin, root: Path | None = None) -> RuleSet:
    """출고 기본값을 읽는다. **비교·원복에만 쓴다.**"""
    import json

    path = default_path(origin, root)
    if not path.is_file():
        raise RuleDataError(f"출고 기본값이 없습니다: {path}")
    with path.open(encoding="utf-8") as stream:
        return RuleSet.from_payload(json.load(stream), origin, path=path)


# --------------------------------------------------------------------- UI 표


@dataclass(frozen=True)
class ItemView:
    """UI 「기준 데이터 관리」 화면의 한 줄.

    **값만 보여 주면 고칠 근거가 없다.** 근거 조문·시행일·확인일·경과 개월을
    같은 줄에 둔다.
    """

    key: str
    label: str
    value: Any
    origin: str
    source: str
    source_date: str
    verified_on: str
    months_since_verified: float | None
    note: str
    default_value: Any
    changed_from_default: bool

    def as_row(self) -> dict[str, Any]:
        return {
            "항목": self.key,
            "이름": self.label,
            "값": self.value,
            "구분": self.origin,
            "근거": self.source,
            "출처 시행일": self.source_date,
            "확인일": self.verified_on,
            "경과(개월)": (
                None if self.months_since_verified is None else round(self.months_since_verified, 1)
            ),
            "출고값": self.default_value,
            "변경됨": self.changed_from_default,
            "비고": self.note,
        }


def describe_items(
    origin: RuleOrigin | None = None,
    *,
    root: Path | None = None,
    today: dt.date | None = None,
) -> tuple[ItemView, ...]:
    """항목을 UI 가 그릴 수 있는 모양으로 낸다.

    Args:
        origin: 주면 그 갈래만. 주지 않으면 **법령 유래와 판단값을 함께** 낸다.
            섞어 내되 ``구분`` 열로 갈라 볼 수 있다.
    """
    wanted = (RuleOrigin.STATUTORY, RuleOrigin.JUDGEMENT) if origin is None else (origin,)
    views: list[ItemView] = []
    for kind in wanted:
        current = rules(root) if kind is RuleOrigin.STATUTORY else assumptions(root)
        try:
            factory = load_defaults(kind, root)
            defaults = {key: factory[key].value for key in factory.item_keys()}
        except RuleDataError:
            defaults = {}
        for key in current.item_keys():
            item = current[key]
            default_value = defaults.get(key)
            views.append(
                ItemView(
                    key=key,
                    label=item.label,
                    value=item.value,
                    origin=str(item.origin),
                    source=item.source,
                    source_date=item.source_date.isoformat() if item.source_date else "",
                    verified_on=item.verified_on.isoformat() if item.verified_on else "",
                    months_since_verified=item.months_since_verified(today),
                    note=item.note,
                    default_value=default_value,
                    changed_from_default=key in defaults and default_value != item.value,
                )
            )
    return tuple(views)


@dataclass(frozen=True)
class ItemDiff:
    """출고값과 다른 항목 하나."""

    key: str
    label: str
    origin: str
    current: Any
    default: Any
    status: str
    """``변경`` / ``추가`` / ``삭제``."""


def diff_from_defaults(
    origin: RuleOrigin | None = None, *, root: Path | None = None
) -> tuple[ItemDiff, ...]:
    """출고값과 다른 항목을 모은다.

    **출고 복원 전에 무엇이 달라지는지 보여 주는 데 쓴다.** 보여 주지 않고
    복원하면 사용자가 어제 고친 값을 잃고도 모른다.
    """
    wanted = (RuleOrigin.STATUTORY, RuleOrigin.JUDGEMENT) if origin is None else (origin,)
    diffs: list[ItemDiff] = []
    for kind in wanted:
        current = rules(root) if kind is RuleOrigin.STATUTORY else assumptions(root)
        factory = load_defaults(kind, root)
        for key in current.item_keys():
            item = current[key]
            if key not in factory:
                diffs.append(ItemDiff(key, item.label, str(kind), item.value, None, "추가"))
            elif factory[key].value != item.value:
                diffs.append(
                    ItemDiff(key, item.label, str(kind), item.value, factory[key].value, "변경")
                )
        for key in factory.item_keys():
            if key not in current:
                diffs.append(
                    ItemDiff(key, factory[key].label, str(kind), None, factory[key].value, "삭제")
                )
    return tuple(diffs)


# --------------------------------------------------------------------- 편집


@dataclass(frozen=True)
class EditResult:
    """편집 결과. **실패하면 저장되지 않았다는 뜻이다.**"""

    ok: bool
    issues: tuple[ValidationIssue, ...] = ()
    backup: Path | None = None
    changes: tuple[RuleChange, ...] = ()
    message: str = ""

    def __bool__(self) -> bool:
        return self.ok


def _origin_of(key: str, root: Path | None) -> RuleOrigin:
    if key in rules(root):
        return RuleOrigin.STATUTORY
    if key in assumptions(root):
        return RuleOrigin.JUDGEMENT
    raise RuleDataError(
        f"기준 데이터에 없는 항목입니다: {key!r} "
        f"({RULES_FILENAME} 과 {ASSUMPTIONS_FILENAME} 을 확인하십시오)"
    )


def _commit(
    ruleset: RuleSet,
    changes: list[RuleChange],
    *,
    root: Path | None,
    action: str,
) -> EditResult:
    """검증 → 백업 → 저장 → 이력 → 캐시 비움. **순서를 바꾸지 않는다.**"""
    issues = validate_ruleset(ruleset)
    if issues:
        return EditResult(
            ok=False,
            issues=issues,
            message=(
                f"검증에 실패해 저장하지 않았습니다 ({len(issues)}건). "
                + " / ".join(str(issue) for issue in issues)
            ),
        )
    backup = write_backup(ruleset.origin, root)
    write_ruleset(ruleset, root)
    for change in changes:
        append_history(change, root)
    reload_rules()
    return EditResult(
        ok=True,
        backup=backup,
        changes=tuple(changes),
        message=f"{action} 완료 ({len(changes)}건). 직전 상태는 {backup.name if backup else '—'}",
    )


def set_value(
    key: str,
    value: Any,
    *,
    note: str | None = None,
    root: Path | None = None,
    today: dt.date | None = None,
    now: dt.datetime | None = None,
) -> EditResult:
    """값을 고친다. **확인일이 오늘로 자동 갱신된다.**

    값을 고쳤다는 것은 원문을 보고 고쳤다는 뜻이므로 확인일을 따로 누르게 하면
    빠뜨린다.
    """
    origin = _origin_of(key, root)
    current = rules(root) if origin is RuleOrigin.STATUTORY else assumptions(root)
    before = current[key]
    if before.value == value and note is None:
        return EditResult(ok=True, message=f"{key} 값이 그대로입니다. 저장하지 않았습니다.")

    updated = replace(
        before,
        value=value,
        verified_on=today or dt.date.today(),
        note=before.note if note is None else note,
    )
    items = dict(current.items)
    items[key] = updated
    change = RuleChange(
        changed_at=now or dt.datetime.now(),
        file=origin.filename,
        key=key,
        before=before.value,
        after=value,
        action="edit",
        note=updated.note,
    )
    return _commit(replace(current, items=items), [change], root=root, action="값 수정")


def confirm(
    key: str,
    *,
    root: Path | None = None,
    today: dt.date | None = None,
    now: dt.datetime | None = None,
) -> EditResult:
    """값은 그대로 두고 **"확인함"만 기록**한다.

    원문을 다시 보았는데 값이 그대로일 때 쓴다. 이 경로가 없으면 만료 경고를
    끄려고 값을 무의미하게 고치게 된다.
    """
    origin = _origin_of(key, root)
    current = rules(root) if origin is RuleOrigin.STATUTORY else assumptions(root)
    before = current[key]
    stamp = today or dt.date.today()
    items = dict(current.items)
    items[key] = replace(before, verified_on=stamp)
    change = RuleChange(
        changed_at=now or dt.datetime.now(),
        file=origin.filename,
        key=key,
        before=before.verified_on.isoformat() if before.verified_on else None,
        after=stamp.isoformat(),
        action="confirm",
        note="값 변경 없이 확인만 기록",
    )
    return _commit(replace(current, items=items), [change], root=root, action="확인 기록")


# --------------------------------------------------------------------- 원복


def restore_previous(
    origin: RuleOrigin,
    *,
    root: Path | None = None,
    now: dt.datetime | None = None,
) -> EditResult:
    """① 직전 상태로 되돌린다 (가장 최근 백업)."""
    backups = list_backups(origin, root)
    if not backups:
        return EditResult(ok=False, message=f"되돌릴 백업이 없습니다: {backup_dir(root)}")
    target = backups[0]
    # 되돌리기 자체도 백업한다 — 되돌린 것을 되돌릴 수 있어야 한다.
    # **안전 백업이 목록 맨 앞으로 올라오므로 되돌릴 대상을 미리 잡아 둔다.**
    safety = write_backup(origin, root, now=now)
    import shutil

    shutil.copy2(target, current_path(origin, root))
    reload_rules()
    change = RuleChange(
        changed_at=now or dt.datetime.now(),
        file=origin.filename,
        key="*",
        before=safety.name if safety else None,
        after=target.name,
        action="restore_previous",
        note="직전 상태로 복원",
    )
    append_history(change, root)
    return EditResult(
        ok=True,
        backup=safety,
        changes=(change,),
        message=f"{target.name} 으로 되돌렸습니다.",
    )


def restore_defaults(
    origin: RuleOrigin,
    *,
    root: Path | None = None,
    confirmed: bool = False,
    now: dt.datetime | None = None,
) -> EditResult:
    """② 출고 상태로 되돌린다.

    **``confirmed=False`` 면 실행하지 않고 달라지는 항목만 돌려준다.** 무엇을
    잃는지 보여 주지 않고 복원하면 어제 고친 값을 잃고도 모른다.
    """
    diffs = diff_from_defaults(origin, root=root)
    if not confirmed:
        return EditResult(
            ok=False,
            message=(
                f"출고 복원 대상 {len(diffs)}건입니다. 확인 후 confirmed=True 로 다시 부르십시오."
                if diffs
                else "출고값과 다른 항목이 없습니다. 복원할 것이 없습니다."
            ),
            changes=tuple(
                RuleChange(
                    changed_at=now or dt.datetime.now(),
                    file=origin.filename,
                    key=item.key,
                    before=item.current,
                    after=item.default,
                    action="preview_restore_defaults",
                    note=item.status,
                )
                for item in diffs
            ),
        )

    safety = write_backup(origin, root, now=now)
    restore_defaults_file(origin, root)
    reload_rules()
    changes = [
        RuleChange(
            changed_at=now or dt.datetime.now(),
            file=origin.filename,
            key=item.key,
            before=item.current,
            after=item.default,
            action="restore_defaults",
            note=item.status,
        )
        for item in diffs
    ]
    for change in changes:
        append_history(change, root)
    return EditResult(
        ok=True,
        backup=safety,
        changes=tuple(changes),
        message=(
            f"출고값으로 복원했습니다 ({len(changes)}건). "
            f"직전 상태는 {safety.name if safety else '—'}"
        ),
    )


def restore_item(
    key: str,
    *,
    root: Path | None = None,
    now: dt.datetime | None = None,
) -> EditResult:
    """③ 한 항목만 출고값으로 되돌린다. **실무에서 가장 많이 쓰인다.**"""
    origin = _origin_of(key, root)
    current = rules(root) if origin is RuleOrigin.STATUTORY else assumptions(root)
    factory = load_defaults(origin, root)
    if key not in factory:
        return EditResult(ok=False, message=f"출고값에 없는 항목입니다: {key}")
    before = current[key]
    target = factory[key]
    if before.value == target.value:
        return EditResult(ok=True, message=f"{key} 는 이미 출고값입니다.")

    items = dict(current.items)
    items[key] = replace(before, value=target.value, verified_on=dt.date.today())
    change = RuleChange(
        changed_at=now or dt.datetime.now(),
        file=origin.filename,
        key=key,
        before=before.value,
        after=target.value,
        action="restore_item",
        note="항목별 출고값 복원",
    )
    return _commit(replace(current, items=items), [change], root=root, action="항목 복원")


# --------------------------------------------------------------------- 만료


def expiry_warnings(
    *,
    root: Path | None = None,
    today: dt.date | None = None,
    include_weather: bool = True,
) -> tuple[ExpiryWarning, ...]:
    """만료 경고 전체. 기상까지 함께 본다."""
    found = list(check_expiry(rules(root), assumptions(root), today=today))
    if include_weather:
        weather = weather_expiry(today=today)
        if weather is not None:
            found.append(weather)
    return tuple(found)
