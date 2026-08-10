"""기준 데이터 관리 화면의 표 구성 (요구사항서 12장·10.1).

**근거를 값 옆에 항상 보이게 두는 것이 이 화면의 존재 이유다.** 법령 항목은
함부로 고치면 안 되고 판단값은 조정하라고 있는 것인데, 값만 있으면 둘을 구분할
길이 없다. 그래서 한 줄에 이만큼을 함께 둔다.

    구분 배지 · 라벨 · 값(편집) · 출고값(다를 때만) · 근거(조문·시행일) ·
    확인일과 경과 개월 · [확인함] · 비고 · 원문 확인처

정렬은 **만료 경고 → 출고값과 다른 항목 → 분류** 순이다. 손댈 것이 위로 온다.

이 모듈은 Streamlit 을 import 하지 않는다 — 화면은 여기서 만든 행을 그리기만 한다.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from kwise.pv.archive import INDEX_FILENAME, ArchiveStatus
from kwise.rules import ExpiryWarning, ItemDiff, ItemView

__all__ = [
    "CATEGORY_LABELS",
    "RuleCounts",
    "RuleRow",
    "WeatherPanel",
    "build_rows",
    "count_rows",
    "diff_frame",
    "header_text",
    "weather_panel",
]

# 분류 표시 이름. **값이 아니라 화면 묶음 이름**이라 여기 둔다. 모르는 앞머리는
# 키를 그대로 보여 준다 — 항목이 늘었을 때 조용히 '기타' 로 뭉치면 안 된다.
CATEGORY_LABELS: dict[str, str] = {
    "contract_type": "계약종별",
    "day_rules": "요일·공휴일 계량",
    "demand": "요금적용전력",
    "dr": "경제성DR",
    "ess": "ESS",
    "expiry": "만료 임계",
    "power_factor": "역률",
    "pv": "태양광",
    "season": "계절 구분",
    "sensitivity": "감도",
    "tariff": "요금 단가",
    "tou": "시간대 구분",
}

_ORIGIN_BADGES: dict[str, str] = {"법령": "법령", "판단값": "판단"}


@dataclass(frozen=True)
class RuleRow:
    """화면 한 줄."""

    view: ItemView
    scope: str
    """만료 임계를 결정하는 구분 — ``약관·규칙`` / ``요금 단가`` / ``판단값`` 등."""
    link: str
    """원문 확인처. 근거 줄 옆에 둔다."""
    warning: ExpiryWarning | None = None

    @property
    def key(self) -> str:
        return self.view.key

    @property
    def badge(self) -> str:
        """``법령`` / ``판단``."""
        return _ORIGIN_BADGES.get(self.view.origin, self.view.origin)

    @property
    def is_statutory(self) -> bool:
        return self.view.origin == "법령"

    @property
    def category(self) -> str:
        return self.view.key.split(".", 1)[0]

    @property
    def category_label(self) -> str:
        return CATEGORY_LABELS.get(self.category, self.category)

    @property
    def changed(self) -> bool:
        return self.view.changed_from_default

    @property
    def needs_check(self) -> bool:
        return self.warning is not None

    @property
    def source_text(self) -> str:
        """근거 한 줄. 조문과 시행일, 또는 판단 근거."""
        source = self.view.source or ("판단값" if not self.is_statutory else "근거 미기재")
        if self.view.source_date:
            return f"{source} (시행 {self.view.source_date})"
        return source

    @property
    def verified_text(self) -> str:
        """확인일과 경과 개월."""
        if not self.view.verified_on:
            return "확인 기록 없음"
        elapsed = self.view.months_since_verified
        if elapsed is None:
            return self.view.verified_on
        return f"{self.view.verified_on} ({elapsed:,.0f}개월 경과)"

    def sort_key(self) -> tuple[int, int, str, str]:
        """만료 경고 → 출고값과 다른 항목 → 분류 → 키."""
        return (
            0 if self.needs_check else 1,
            0 if self.changed else 1,
            self.category_label,
            self.view.key,
        )


def build_rows(
    views: Iterable[ItemView],
    warnings: Iterable[ExpiryWarning] = (),
    *,
    links: dict[str, tuple[str, str]] | None = None,
) -> tuple[RuleRow, ...]:
    """화면 행을 만들고 정렬한다.

    Args:
        views: :func:`kwise.rules.describe_items` 결과.
        warnings: :func:`kwise.rules.expiry_warnings` 결과. 항목 키로 맞붙인다.
        links: 항목 키 → (구분, 확인처 링크). 주지 않으면
            :func:`kwise.rules.source_link_of` 로 채운다.
    """
    by_key = {item.key: item for item in warnings}
    resolved = links if links is not None else _links_for(views)
    rows = [
        RuleRow(
            view=view,
            scope=resolved.get(view.key, ("", ""))[0],
            link=resolved.get(view.key, ("", ""))[1],
            warning=by_key.get(view.key),
        )
        for view in views
    ]
    return tuple(sorted(rows, key=lambda row: row.sort_key()))


def _links_for(views: Iterable[ItemView]) -> dict[str, tuple[str, str]]:
    """항목별 구분·확인처를 기준 데이터에서 읽어 온다."""
    from kwise.rules import RuleOrigin, assumptions, rules, source_link_of

    resolved: dict[str, tuple[str, str]] = {}
    for view in views:
        ruleset = rules() if view.origin == str(RuleOrigin.STATUTORY) else assumptions()
        if view.key in ruleset:
            resolved[view.key] = source_link_of(ruleset[view.key])
    return resolved


@dataclass(frozen=True)
class RuleCounts:
    """상단 요약 — "전체 ○개 · 변경됨 ○개 · 확인 필요 ○개"."""

    total: int
    changed: int
    needs_check: int


def count_rows(rows: Iterable[RuleRow]) -> RuleCounts:
    items = tuple(rows)
    return RuleCounts(
        total=len(items),
        changed=sum(1 for row in items if row.changed),
        needs_check=sum(1 for row in items if row.needs_check),
    )


def header_text(counts: RuleCounts) -> str:
    return f"전체 {counts.total}개 · 변경됨 {counts.changed}개 · 확인 필요 {counts.needs_check}개"


def diff_frame(diffs: Iterable[ItemDiff]) -> pd.DataFrame:
    """출고 복원 미리보기 표. **실행 전에 무엇을 잃는지 보여 준다.**"""
    rows = [
        {
            "항목": item.key,
            "이름": item.label,
            "구분": item.origin,
            "현재 값": item.current,
            "출고값": item.default,
            "상태": item.status,
        }
        for item in diffs
    ]
    if not rows:
        return pd.DataFrame(columns=["항목", "이름", "구분", "현재 값", "출고값", "상태"])
    return pd.DataFrame(rows)


@dataclass(frozen=True)
class WeatherPanel:
    """기상 데이터 현황 (요구사항서 10.1 — 기준 데이터 화면).

    **만료 경고를 달지 않는다.** 그리고 **부분 취득은 정상 상태**다 — 필요한
    격자만 받아 두는 것이 설계이므로 빠진 격자를 오류로 표시하면 안 된다.
    """

    cell_count: int
    years: tuple[int, ...]
    megabytes: float
    fetched_on: dt.date | None
    root: Path
    range_text: str

    @property
    def year_text(self) -> str:
        if not self.years:
            return "없음"
        return f"{self.years[0]}~{self.years[-1]}" if len(self.years) > 1 else str(self.years[0])

    @property
    def fetched_text(self) -> str:
        return self.fetched_on.isoformat() if self.fetched_on else "기록 없음"

    def text(self) -> str:
        return (
            f"확보 격자 {self.cell_count}개 · {self.year_text} · "
            f"{self.megabytes:,.1f} MB · 최종 취득 {self.fetched_text}"
        )


def weather_panel(status: ArchiveStatus) -> WeatherPanel:
    """확보 현황을 화면용으로 접는다.

    최종 취득일은 색인 파일의 수정 시각으로 본다 — 취득이 끝날 때마다 색인을
    다시 쓰므로 그 시각이 곧 마지막 취득이다.
    """
    index = status.root / INDEX_FILENAME
    fetched: dt.date | None = None
    if index.is_file():
        fetched = dt.datetime.fromtimestamp(index.stat().st_mtime).date()
    return WeatherPanel(
        cell_count=status.cell_count,
        years=status.years,
        megabytes=status.bytes / 1_048_576,
        fetched_on=fetched,
        root=status.root,
        range_text=status.range_text,
    )
