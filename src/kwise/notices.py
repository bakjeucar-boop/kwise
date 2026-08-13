"""안내 문구와 그 등급 (요구사항서 10.7 · 19세션).

**등급은 발신처가 정한다.** 계산 모듈이 문구를 만들 때 등급을 함께 붙인다.
18세션까지는 :func:`classify` 가 문자열을 부분 일치로 훑어 등급을 매겼는데,
45건을 통과시켜 보니 82%가 어느 패턴에도 걸리지 않아 기본값(주의)으로
떨어졌다. **3등급 체계가 이름만 있고 실제로는 전부 화면에 떴다.**

등급 넷과 그것이 가는 자리다.

    차단(BLOCK)  계산이 안 됨.          화면 본문 · 색
    주의(WARN)   결과 해석이 달라짐.    화면 본문 · 아이콘
    근거(BASIS)  이 숫자가 어디서 왔나. **화면은 툴팁**, 보고서는 본문
    참고(INFO)   전제·한계·제도.        **화면에 없음**, 보고서 부록

**근거가 이 체계의 核이다.** 매번 볼 필요는 없지만 결과를 신뢰할지 판단하는 데
필요한 것들이다 — 산식, 출처, 적용한 계수, 판정 기준. 이것을 참고로 묶어
보고서로 보내면 "이 숫자가 어디서 나왔나" 를 화면에서 물을 길이 사라지고,
주의로 올리면 경고 스물이 쌓여 정작 위험한 것이 묻힌다.

**이 모듈은 Streamlit 도 pandas 도 import 하지 않는다.** 계산 모듈이 부르는
자리이므로 UI 쪽으로 의존이 생기면 안 된다. 마크다운 escape 같은 표시 처리는
:mod:`kwise.ui.notices` 가 맡는다.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "Notice",
    "Severity",
    "as_notice",
    "basis",
    "block",
    "dedupe",
    "info",
    "partition",
    "report_appendix",
    "report_body",
    "screen_body",
    "texts",
    "tooltip",
    "warn",
]

_log = logging.getLogger(__name__)


class Severity(Enum):
    """심각도. **값이 화면 표기다.**"""

    BLOCK = "차단"
    WARN = "주의"
    BASIS = "근거"
    INFO = "참고"

    def __str__(self) -> str:
        return self.value

    @property
    def on_screen(self) -> bool:
        """화면 **본문**에 띄우는가. 차단과 주의만이다."""
        return self in (Severity.BLOCK, Severity.WARN)

    @property
    def in_tooltip(self) -> bool:
        """화면 **툴팁**으로 가는가. 근거만이다."""
        return self is Severity.BASIS

    @property
    def in_report_body(self) -> bool:
        """보고서 **본문**에 싣는가. 참고를 뺀 셋이다."""
        return self is not Severity.INFO


#: 정렬 순서. 차단이 먼저고 참고가 끝이다.
_ORDER = {Severity.BLOCK: 0, Severity.WARN: 1, Severity.BASIS: 2, Severity.INFO: 3}


@dataclass(frozen=True)
class Notice:
    """안내 하나. **문구와 등급을 함께 낸다.**"""

    severity: Severity
    text: str

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("빈 안내 문구입니다.")

    @property
    def on_screen(self) -> bool:
        return self.severity.on_screen


def block(text: str) -> Notice:
    """차단 — 계산이 진행되지 않는다."""
    return Notice(Severity.BLOCK, text)


def warn(text: str) -> Notice:
    """주의 — 결과 해석이 크게 달라진다."""
    return Notice(Severity.WARN, text)


def basis(text: str) -> Notice:
    """근거 — 이 숫자가 어디서 나왔는가. 산식·출처·계수·판정 기준."""
    return Notice(Severity.BASIS, text)


def info(text: str) -> Notice:
    """참고 — 전제·한계·제도 설명."""
    return Notice(Severity.INFO, text)


def as_notice(item: Notice | str) -> Notice:
    """**하위 호환 폴백.** 문자열이 들어오면 주의로 보고 로그를 남긴다.

    이관이 끝나면 지운다 (20세션). 조용히 등급을 매기면 이관 누락이 드러나지
    않으므로 **반드시 로그를 남긴다** — 18세션까지의 패턴 매칭이 정확히 그렇게
    82%를 기본값으로 흘려보냈다.
    """
    if isinstance(item, Notice):
        return item
    _log.warning("등급 없는 문자열 안내입니다 (발신처에서 Notice 로 바꾸십시오): %.60s", item)
    return Notice(Severity.WARN, item)


def _fingerprint(message: str) -> str:
    """같은 사실인지 가리는 지문. **줄표 앞까지**다.

    18세션이 약점을 적어 두었다 — 줄표가 없으면 문장 전체가 지문이 된다.
    **사실 ID 로 바꾸는 것은 20세션 몫이다.** 여기서는 손대지 않는다.
    """
    head = re.split(r"\s[—-]\s", message.strip(), maxsplit=1)[0]
    return re.sub(r"\s+", " ", head).strip().rstrip(".")


def dedupe(items: Iterable[Notice | str]) -> tuple[Notice, ...]:
    """같은 사실을 한 번만 남긴다. **처음 나온 것을 남긴다** (발생 지점)."""
    seen: set[str] = set()
    kept: list[Notice] = []
    for item in items:
        notice = as_notice(item)
        text = notice.text.strip()
        if not text:
            continue
        key = _fingerprint(text)
        if key in seen:
            continue
        seen.add(key)
        kept.append(Notice(notice.severity, text))
    return tuple(kept)


def screen_body(*groups: Iterable[Notice | str]) -> tuple[Notice, ...]:
    """화면 **본문** — 차단과 주의만. 등급 순으로 정렬한다."""
    merged = [item for group in groups for item in group]
    return tuple(
        sorted(
            (item for item in dedupe(merged) if item.severity.on_screen),
            key=lambda item: _ORDER[item.severity],
        )
    )


def tooltip(*groups: Iterable[Notice | str]) -> tuple[str, ...]:
    """화면 **툴팁** — 근거만. 문구만 돌려준다."""
    merged = [item for group in groups for item in group]
    return tuple(item.text for item in dedupe(merged) if item.severity.in_tooltip)


def report_body(*groups: Iterable[Notice | str]) -> tuple[Notice, ...]:
    """보고서 **본문** — 참고를 뺀 셋. 등급 순이다."""
    merged = [item for group in groups for item in group]
    return tuple(
        sorted(
            (item for item in dedupe(merged) if item.severity.in_report_body),
            key=lambda item: _ORDER[item.severity],
        )
    )


def report_appendix(*groups: Iterable[Notice | str]) -> tuple[Notice, ...]:
    """보고서 **부록** — 참고만."""
    merged = [item for group in groups for item in group]
    return tuple(item for item in dedupe(merged) if item.severity is Severity.INFO)


def texts(items: Iterable[Notice | str], *severities: Severity) -> tuple[str, ...]:
    """등급으로 걸러 문구만 뽑는다. 등급을 주지 않으면 전부."""
    wanted = set(severities)
    return tuple(
        item.text
        for item in (as_notice(entry) for entry in items)
        if not wanted or item.severity in wanted
    )


def partition(
    items: Iterable[Notice], patterns: Iterable[str]
) -> tuple[tuple[Notice, ...], tuple[Notice, ...]]:
    """(패턴에 걸린 것, 나머지). **같은 사실을 두 곳에 내지 않으려고** 쓴다.

    등급 판정과는 무관하다 — 전용 블록이 따로 있는 문구를 그 블록으로 내리는
    자리 배치용이다.
    """
    keys = tuple(patterns)
    matched = tuple(item for item in items if any(key in item.text for key in keys))
    rest = tuple(item for item in items if not any(key in item.text for key in keys))
    return matched, rest
