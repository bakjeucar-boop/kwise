"""안내 문구의 화면 처리 (요구사항서 10.7).

**등급 판정은 여기 없다.** 19세션에 :mod:`kwise.notices` 로 옮겼고, 등급은
계산 모듈이 문구를 만들 때 붙인다. 이 모듈에 남은 일은 **표시**뿐이다 —
마크다운 escape 와 자리 배치.

    화면 본문   차단·주의   :func:`screen_notices`
    화면 툴팁   근거        :func:`tooltip_text`
    보고서      전부        :func:`report_notices` (본문) · :func:`appendix_notices` (부록)

18세션까지 쓰던 부분 문자열 등급 추정(``classify`` · ``WARN_PATTERNS`` ·
``INFO_PATTERNS``)은 **21세션에 지웠다.** 등급도 사실 ID 도 발신처가 붙인다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from kwise.notices import (
    Notice,
    Severity,
    dedupe,
    partition_facts,
    report_appendix,
    report_body,
    screen_body,
    tooltip,
)
from kwise.ui.text import markdown_safe

__all__ = [
    "Notice",
    "Severity",
    "appendix_notices",
    "dedupe",
    "partition_facts",
    "report_notices",
    "screen_notices",
    "tooltip_text",
]


def _escaped(items: Iterable[Notice]) -> tuple[Notice, ...]:
    """화면으로 나가는 문구만 escape 한다.

    계산 모듈이 내는 ``08~22시`` 같은 표기가 한 줄에 둘 있으면 취소선이 되기
    때문이다 (13세션). 산출물은 escape 하지 않는다 — Excel·Word 는 마크다운을
    해석하지 않는다.
    """
    return tuple(replace(item, text=markdown_safe(item.text)) for item in items)


def screen_notices(*groups: Iterable[Notice]) -> tuple[Notice, ...]:
    """화면 **본문** — 차단과 주의만. 근거는 툴팁, 참고는 보고서로 간다."""
    return _escaped(screen_body(*groups))


def tooltip_text(*groups: Iterable[Notice], header: str = "") -> str:
    """화면 **툴팁** 한 덩이 — 근거만. 없으면 빈 문자열이다.

    Streamlit 의 ``help=`` 는 마크다운을 해석하므로 escape 하지 않는다 — 툴팁
    안에서는 굵게가 그대로 살아야 읽힌다.
    """
    lines = tooltip(*groups)
    if not lines:
        return ""
    body = "\n".join(f"- {line}" for line in lines)
    return f"{header}\n\n{body}" if header else body


def report_notices(*groups: Iterable[Notice]) -> tuple[Notice, ...]:
    """보고서 **본문** — 차단·주의·근거. 등급 순이다."""
    return report_body(*groups)


def appendix_notices(*groups: Iterable[Notice]) -> tuple[Notice, ...]:
    """보고서 **부록** — 참고만. 전제·한계·제도 설명이 여기 모인다."""
    return report_appendix(*groups)
