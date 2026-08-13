"""안내 문구의 화면 처리 (요구사항서 10.7).

**등급 판정은 여기 없다.** 19세션에 :mod:`kwise.notices` 로 옮겼고, 등급은
계산 모듈이 문구를 만들 때 붙인다. 이 모듈에 남은 일은 **표시**뿐이다 —
마크다운 escape 와 자리 배치.

    화면 본문   차단·주의   :func:`screen_notices`
    화면 툴팁   근거        :func:`tooltip_text`
    보고서      전부        :func:`report_notices` (본문) · :func:`appendix_notices` (부록)

:func:`classify` 는 **하위 호환 폴백**이다. 18세션까지 쓰던 부분 문자열 매칭이며,
45건을 통과시켜 보니 82%가 어느 패턴에도 걸리지 않아 기본값(주의)으로 떨어졌다.
이관이 끝나면 지운다 (20세션).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from kwise.notices import (
    Notice,
    Severity,
    as_notice,
    dedupe,
    partition_facts,
    report_appendix,
    report_body,
    screen_body,
    tooltip,
)
from kwise.ui.text import markdown_safe

__all__ = [
    "INFO_PATTERNS",
    "WARN_PATTERNS",
    "Notice",
    "Severity",
    "appendix_notices",
    "classify",
    "dedupe",
    "partition_facts",
    "report_notices",
    "screen_notices",
    "tooltip_text",
]

# **하위 호환 폴백용이다.** 새 문구에 쓰지 말 것 — 등급은 발신처가 붙인다.
WARN_PATTERNS: tuple[str, ...] = (
    "몰려 있습니다",
    "편중 배수",
    "신뢰 제한",
    "최장 연속 결측",
    "한 번의 초과가 12개월간",
    "잠정입니다",
    "에 미달합니다",
    "새 피크",
    "고출력 셀",
    "초과사용부가금",
    "지키지 못했습니다",
    "종별을 확인하십시오",
    "계산하지 않았습니다",
)

INFO_PATTERNS: tuple[str, ...] = (
    "기후환경요금",
    "미포함",
    "청구서로 검증되지 않았습니다",
    "직전 12개월 최대수요 이력이 없어",
    "부분 계량",
    "그리드 이탈",
    "kW 미만 구간",
    "정전 추정",
    "야간 진상 여부를 확인하지 않았습니다",
    "하한을 적용하지 않았습니다",
    "요금적용전력은 중간·최대부하",
    "감도를 적용하지 않습니다",
    "설비 도입과 무관한 확정 계산",
    "결측 보정 기준을 함께 봅니다",
    "12개월 미만입니다",
    "보간하지 않으며",
    "참고 문턱",
    "자격요건",
)


def classify(message: str) -> Severity:
    """**하위 호환 폴백.** 문자열만 있을 때의 등급 추정 (18세션까지의 방식).

    새 코드는 :func:`kwise.notices.warn` 등으로 발신처에서 등급을 붙인다.
    """
    text = message.strip()
    for pattern in WARN_PATTERNS:
        if pattern in text:
            return Severity.WARN
    for pattern in INFO_PATTERNS:
        if pattern in text:
            return Severity.INFO
    return Severity.WARN


def _escaped(items: Iterable[Notice]) -> tuple[Notice, ...]:
    """화면으로 나가는 문구만 escape 한다.

    계산 모듈이 내는 ``08~22시`` 같은 표기가 한 줄에 둘 있으면 취소선이 되기
    때문이다 (13세션). 산출물은 escape 하지 않는다 — Excel·Word 는 마크다운을
    해석하지 않는다.
    """
    return tuple(replace(item, text=markdown_safe(item.text)) for item in items)


def screen_notices(*groups: Iterable[Notice | str]) -> tuple[Notice, ...]:
    """화면 **본문** — 차단과 주의만. 근거는 툴팁, 참고는 보고서로 간다."""
    return _escaped(screen_body(*groups))


def tooltip_text(*groups: Iterable[Notice | str], header: str = "") -> str:
    """화면 **툴팁** 한 덩이 — 근거만. 없으면 빈 문자열이다.

    Streamlit 의 ``help=`` 는 마크다운을 해석하므로 escape 하지 않는다 — 툴팁
    안에서는 굵게가 그대로 살아야 읽힌다.
    """
    lines = tooltip(*groups)
    if not lines:
        return ""
    body = "\n".join(f"- {line}" for line in lines)
    return f"{header}\n\n{body}" if header else body


def report_notices(*groups: Iterable[Notice | str]) -> tuple[Notice, ...]:
    """보고서 **본문** — 차단·주의·근거. 등급 순이다."""
    return report_body(*groups)


def appendix_notices(*groups: Iterable[Notice | str]) -> tuple[Notice, ...]:
    """보고서 **부록** — 참고만. 전제·한계·제도 설명이 여기 모인다."""
    return report_appendix(*groups)


# 폴백 경로에서만 쓴다. 문자열 리스트를 받는 옛 호출부가 남아 있는 동안 유효하다.
_ = as_notice
