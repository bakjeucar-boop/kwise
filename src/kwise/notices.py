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

중복은 **문구가 아니라 사실로** 가린다 (20세션). 발신처가 ``fact="모듈.사실"``
을 붙이고 :func:`dedupe` 가 그 ID 로 접는다. 19세션까지는 문구의 줄표 앞을
지문으로 삼았는데, 같은 사실을 두 모듈이 조금 다르게 적으면 그대로 두 번
나갔다 — 계약전력 초과·저부하 평일 없음·경부하 새 피크 셋이 그랬다.

    ess.charge_new_peak    ← measures\\ess.py 와 compare\\combination.py 가 함께 쓴다

같은 사실이 여럿일 때(월별·정전 구간처럼)는 ``모듈.사실:판별자`` 로 적는다.
``:`` 앞이 사실이고 뒤가 그 사실의 어느 하나인지다 — :attr:`Notice.fact_base`
가 앞부분을 돌려준다.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import Enum

__all__ = [
    "Notice",
    "Severity",
    "as_notice",
    "basis",
    "block",
    "dedupe",
    "dedupe_key",
    "dedupe_keys",
    "info",
    "partition_facts",
    "prefixed",
    "report_appendix",
    "report_body",
    "screen_body",
    "texts",
    "tooltip",
    "unidentified",
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


#: 사실 ID 의 꼴. ``모듈.사실`` 이고 ``:판별자`` 를 붙일 수 있다.
_FACT = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+(?::\S+)?$")


@dataclass(frozen=True)
class Notice:
    """안내 하나. **문구와 등급과 사실 ID 를 함께 낸다.**

    ``fact`` 가 중복 판정의 기준이다. 비어 있으면 문구 지문으로 폴백하는데,
    그것이 19세션까지의 방식이고 같은 사실을 두 번 내보낸 원인이다.
    """

    severity: Severity
    text: str
    fact: str = ""

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("빈 안내 문구입니다.")
        if self.fact and not _FACT.match(self.fact):
            raise ValueError(f"사실 ID 형식이 아닙니다 (모듈.사실): {self.fact!r}")

    @property
    def on_screen(self) -> bool:
        return self.severity.on_screen

    @property
    def fact_base(self) -> str:
        """판별자를 뗀 사실. ``quality.month_missing_rate:2023-11`` → 앞부분."""
        return self.fact.split(":", 1)[0]


def block(text: str, *, fact: str = "") -> Notice:
    """차단 — 계산이 진행되지 않는다."""
    return Notice(Severity.BLOCK, text, fact)


def warn(text: str, *, fact: str = "") -> Notice:
    """주의 — 결과 해석이 크게 달라진다."""
    return Notice(Severity.WARN, text, fact)


def basis(text: str, *, fact: str = "") -> Notice:
    """근거 — 이 숫자가 어디서 나왔는가. 산식·출처·계수·판정 기준."""
    return Notice(Severity.BASIS, text, fact)


def info(text: str, *, fact: str = "") -> Notice:
    """참고 — 전제·한계·제도 설명."""
    return Notice(Severity.INFO, text, fact)


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
    """**폴백** 지문 — 줄표 앞까지다. 사실 ID 가 없을 때만 쓴다.

    약점 둘이 알려져 있다 (18세션). 줄표가 없으면 문장 전체가 지문이라 한
    글자만 달라도 중복을 놓치고, 반대로 줄표 앞이 뭉툭하면 서로 다른 사실이
    하나로 뭉개진다. **새 안내는 ``fact=`` 를 붙여 이 길로 오지 않게 한다.**
    """
    head = re.split(r"\s[—-]\s", message.strip(), maxsplit=1)[0]
    return re.sub(r"\s+", " ", head).strip().rstrip(".")


def dedupe_key(notice: Notice, *, base: bool = False) -> str:
    """중복 판정 열쇠. **사실 ID 가 먼저고, 없으면 지문이다.**

    ``base=True`` 면 판별자를 뗀 사실로 견준다. 화면 **사이**의 중복이 그 경우다
    — 2단계 카드가 이미 낸 사실을 3단계 조합이 되풀이하지 않는 자리에서, 조합
    판별자가 붙었다고 다른 사실로 볼 이유가 없다 (``ui.views.compare``).
    """
    fact = notice.fact_base if base else notice.fact
    return f"fact:{fact}" if fact else f"text:{_fingerprint(notice.text)}"


def dedupe_keys(*groups: Iterable[Notice], base: bool = False) -> frozenset[str]:
    """여러 묶음의 열쇠를 한 집합으로."""
    return frozenset(dedupe_key(item, base=base) for group in groups for item in group)


def dedupe(items: Iterable[Notice | str]) -> tuple[Notice, ...]:
    """같은 사실을 한 번만 남긴다. **처음 나온 것을 남긴다** (발생 지점)."""
    seen: set[str] = set()
    kept: list[Notice] = []
    for item in items:
        notice = as_notice(item)
        text = notice.text.strip()
        if not text:
            continue
        key = dedupe_key(notice)
        if key in seen:
            continue
        seen.add(key)
        kept.append(Notice(notice.severity, text, notice.fact))
    return tuple(kept)


def unidentified(*groups: Iterable[Notice | str]) -> tuple[Notice, ...]:
    """**사실 ID 가 없는 안내.** 이관 누락을 드러내는 자리다.

    비어 있어야 한다 — 하나라도 남으면 그 안내는 지문 폴백으로 중복을 가리고,
    그 길이 19세션까지 같은 사실을 두 번 내보낸 원인이다.
    """
    return tuple(
        notice
        for group in groups
        for notice in (as_notice(item) for item in group)
        if not notice.fact
    )


def prefixed(items: Iterable[Notice], prefix: str, *, tag: str = "") -> tuple[Notice, ...]:
    """**표시할 때만** 앞말을 붙인다 (조합명 따위).

    :class:`Notice` 자체는 내용만 갖는다. 앞말을 문구에 심어 두면 지문이 앞말에
    걸려 **그 앞말을 쓴 다른 안내가 통째로 빠진다** — 조합 화면이 그랬다.
    한 조합이 낸 경고 둘이 지문 ``'+ ESS 목표 …'`` 하나로 접혔다 (20세션 4절).

    ``tag`` 를 주면 사실 ID 에 판별자로 붙는다. 조합이 여럿일 때 같은 사실을
    조합마다 따로 남기려는 것이다 — 판별자가 없으면 첫 조합 것만 살아남는다.
    """
    head = prefix.strip()
    if not head:
        return tuple(items)
    return tuple(
        replace(
            item,
            text=f"{head} — {item.text}",
            fact=f"{item.fact}:{tag}" if item.fact and tag and ":" not in item.fact else item.fact,
        )
        for item in items
    )


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


def partition_facts(
    items: Iterable[Notice], facts: Iterable[str]
) -> tuple[tuple[Notice, ...], tuple[Notice, ...]]:
    """(그 사실인 것, 나머지). **같은 사실을 두 곳에 내지 않으려고** 쓴다.

    등급 판정과는 무관하다 — 전용 블록이 따로 있는 사실을 그 블록으로 내리는
    자리 배치용이다. 19세션까지는 부분 문자열로 걸렀는데, 문구가 한 글자만
    바뀌어도 새고 엉뚱한 문장까지 걸어 갔다 — 「결측」 하나로 결측 문구 다섯을
    통째로 가리던 자리가 그랬다 (18세션 2절).
    """
    wanted = set(facts)
    matched = tuple(item for item in items if item.fact_base in wanted)
    rest = tuple(item for item in items if item.fact_base not in wanted)
    return matched, rest
