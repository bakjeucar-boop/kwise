"""기준 데이터 스키마 (요구사항서 12장).

**법령 유래 수치와 우리 판단값을 다른 파일에 둔다.**

    data\\rules_kr.json        법령·약관·규칙에서 온 값. 근거 조문이 있다
    data\\assumptions.json     우리가 정한 값. 근거는 "판단값" 이다

섞으면 "이 숫자를 우리가 정한 것인가 법이 정한 것인가" 를 되물을 수 없게 된다.
그 물음이 갱신 판단의 전부다 — 법령 유래는 조문이 바뀌면 반드시 고쳐야 하고,
판단값은 우리가 근거를 대면 바꿀 수 있다.

항목은 **점으로 구분한 평평한 키**로 둔다. 중첩 구조로 두면 UI 표를 만들 때마다
펼쳐야 하고 편집 이력의 항목 식별자가 흔들린다.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

__all__ = [
    "ASSUMPTIONS_FILENAME",
    "RULES_FILENAME",
    "RuleChange",
    "RuleDataError",
    "RuleItem",
    "RuleOrigin",
    "RuleSet",
]

RULES_FILENAME = "rules_kr.json"
ASSUMPTIONS_FILENAME = "assumptions.json"

# 판단값의 근거 표기. 조문 자리에 이 문자열이 들어간다.
JUDGEMENT_SOURCE = "판단값"


class RuleDataError(ValueError):
    """기준 데이터를 읽지 못했거나 스키마를 따르지 않을 때 발생한다."""


class RuleOrigin(StrEnum):
    """값의 출신. **이것으로 갱신 책임이 갈린다.**"""

    STATUTORY = "법령"  # 조문이 바뀌면 반드시 고쳐야 한다
    JUDGEMENT = "판단값"  # 근거를 대면 우리가 바꿀 수 있다

    @property
    def filename(self) -> str:
        return RULES_FILENAME if self is RuleOrigin.STATUTORY else ASSUMPTIONS_FILENAME


def _as_date(value: object, field_name: str, key: str) -> dt.date | None:
    if value in (None, ""):
        return None
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError as exc:
        raise RuleDataError(f"{key}.{field_name} 날짜 형식이 아닙니다: {value!r}") from exc


@dataclass(frozen=True)
class RuleItem:
    """기준 데이터 한 항목.

    Attributes:
        key: 점으로 구분한 식별자 (``power_factor.lagging_standard_pct``).
            **편집 이력과 항목별 원복이 이 키로 걸린다. 바꾸지 않는다.**
        source: 근거 조문. 판단값은 ``"판단값"``.
        source_date: 근거 문서의 시행일. 만료 판정의 축이다.
        verified_on: 사람이 마지막으로 확인한 날. 값을 고치면 자동으로 갱신된다.
    """

    key: str
    value: Any
    label: str
    origin: RuleOrigin
    source: str = ""
    source_date: dt.date | None = None
    verified_on: dt.date | None = None
    note: str = ""

    @property
    def is_statutory(self) -> bool:
        return self.origin is RuleOrigin.STATUTORY

    def months_since_verified(self, today: dt.date | None = None) -> float | None:
        """마지막 확인일로부터 경과 개월. 확인일이 없으면 None."""
        return _months_between(self.verified_on, today)

    def months_since_source(self, today: dt.date | None = None) -> float | None:
        """근거 문서 시행일로부터 경과 개월."""
        return _months_between(self.source_date, today)

    def to_payload(self) -> dict[str, Any]:
        """파일에 쓸 모양. **키는 바깥에서 붙인다.**"""
        payload: dict[str, Any] = {"value": self.value, "label": self.label}
        if self.source:
            payload["source"] = self.source
        if self.source_date is not None:
            payload["source_date"] = self.source_date.isoformat()
        if self.verified_on is not None:
            payload["verified_on"] = self.verified_on.isoformat()
        if self.note:
            payload["note"] = self.note
        return payload

    @classmethod
    def from_payload(cls, key: str, payload: Mapping[str, Any], origin: RuleOrigin) -> RuleItem:
        if "value" not in payload:
            raise RuleDataError(f"{key} 에 value 가 없습니다.")
        label = str(payload.get("label", "")).strip()
        if not label:
            raise RuleDataError(f"{key} 에 label 이 없습니다. 사람이 읽을 이름이 필요합니다.")
        source = str(payload.get("source", "")).strip()
        if origin is RuleOrigin.STATUTORY and not source:
            raise RuleDataError(
                f"{key} 는 법령 유래인데 근거 조문(source)이 비어 있습니다. "
                "근거를 댈 수 없는 값은 판단값이므로 assumptions.json 으로 옮기십시오."
            )
        return cls(
            key=key,
            value=payload["value"],
            label=label,
            origin=origin,
            source=source or JUDGEMENT_SOURCE,
            source_date=_as_date(payload.get("source_date"), "source_date", key),
            verified_on=_as_date(payload.get("verified_on"), "verified_on", key),
            note=str(payload.get("note", "")),
        )


def _months_between(start: dt.date | None, today: dt.date | None) -> float | None:
    if start is None:
        return None
    reference = today if today is not None else dt.date.today()
    days = (reference - start).days
    return days / 30.4375  # 평균 월 길이. 달 경계를 세는 것이 목적이 아니다


@dataclass(frozen=True)
class RuleSet:
    """한 파일 분량의 기준 데이터."""

    origin: RuleOrigin
    items: Mapping[str, RuleItem]
    schema_version: str = "1.0"
    region: str = "kr"
    note: str = ""
    path: Any = None
    recovered_from: str = ""
    """손상 복구로 읽었으면 그 출처. **비어 있지 않으면 사용자에게 알린다.**"""

    def __getitem__(self, key: str) -> RuleItem:
        try:
            return self.items[key]
        except KeyError as exc:
            raise RuleDataError(
                f"기준 데이터에 없는 항목입니다: {key!r} ({self.origin.filename} 을 확인하십시오)"
            ) from exc

    def __contains__(self, key: str) -> bool:
        return key in self.items

    def item_keys(self) -> Sequence[str]:
        return tuple(sorted(self.items))

    def value(self, key: str) -> Any:
        return self[key].value

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "region": self.region,
            "origin": str(self.origin),
            "note": self.note,
            "items": {key: item.to_payload() for key, item in sorted(self.items.items())},
        }

    @classmethod
    def from_payload(
        cls, payload: Mapping[str, Any], origin: RuleOrigin, path: Any = None
    ) -> RuleSet:
        raw = payload.get("items")
        if not isinstance(raw, dict) or not raw:
            raise RuleDataError(f"{origin.filename} 의 items 가 비어 있습니다.")
        items = {
            str(key): RuleItem.from_payload(str(key), value, origin) for key, value in raw.items()
        }
        return cls(
            origin=origin,
            items=items,
            schema_version=str(payload.get("schema_version", "1.0")),
            region=str(payload.get("region", "kr")),
            note=str(payload.get("note", "")),
            path=path,
        )


@dataclass(frozen=True)
class RuleChange:
    """편집 이력 한 줄 (``data\\rules_history.jsonl``).

    **UI 로 고친 값은 git 에 남지 않는다.** 이 파일이 유일한 추적 수단이다.
    """

    changed_at: dt.datetime
    file: str
    key: str
    before: Any
    after: Any
    action: str = "edit"
    note: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "changed_at": self.changed_at.isoformat(timespec="seconds"),
            "file": self.file,
            "key": self.key,
            "before": self.before,
            "after": self.after,
            "action": self.action,
            "note": self.note,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> RuleChange:
        return cls(
            changed_at=dt.datetime.fromisoformat(str(payload["changed_at"])),
            file=str(payload.get("file", "")),
            key=str(payload.get("key", "")),
            before=payload.get("before"),
            after=payload.get("after"),
            action=str(payload.get("action", "edit")),
            note=str(payload.get("note", "")),
        )
