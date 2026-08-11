"""안내 문구의 심각도 (요구사항서 10.7).

**모든 안내를 같은 무게로 쏟으면 무엇이 중요한지 알 수 없다.** 세 등급으로
나누고 **화면에는 위 둘만** 남긴다.

    차단(BLOCK)  계산이 진행되지 않는 것 — 데이터 없음, 기상 실패, 필수 입력 누락
    주의(WARN)   결과 해석이 크게 달라지는 것 — 결측 편중, 역률 미달, 잠정 기준
    참고(INFO)   전제·한계·출처 — 미포함 요금요소, 요금표 검증 상태, 산출 근거

**참고 등급은 화면에 띄우지 않는다.** Excel 요약 시트와 Word 보고서 5장에만
싣는다 — 사용자가 결과를 쓸 때 함께 가는 문서에 남으면 된다.

**모르는 문구는 주의로 본다.** 계산 쪽에 경고가 새로 생겼을 때 조용히 사라지는
것보다 한 번 더 보이는 편이 낫다 ("조용히 빼지 않는다").

같은 사실이 품질·진단·수단 여러 곳에서 되풀이되므로 **발생 지점 하나만
남기고 지운다.** 이 모듈은 Streamlit 을 import 하지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from kwise.ui.text import markdown_safe

__all__ = [
    "INFO_PATTERNS",
    "WARN_PATTERNS",
    "Notice",
    "Severity",
    "classify",
    "dedupe",
    "partition",
    "report_notices",
    "screen_notices",
]


class Severity(Enum):
    """심각도. 값이 화면 표기다."""

    BLOCK = "차단"
    WARN = "주의"
    INFO = "참고"

    def __str__(self) -> str:
        return self.value

    @property
    def on_screen(self) -> bool:
        """화면에 띄우는가. **참고는 띄우지 않는다.**"""
        return self is not Severity.INFO


@dataclass(frozen=True)
class Notice:
    """안내 하나."""

    severity: Severity
    text: str

    @property
    def on_screen(self) -> bool:
        return self.severity.on_screen


# **주의로 올릴 것.** 결과 해석이 달라지는 것들이다. 먼저 본다.
WARN_PATTERNS: tuple[str, ...] = (
    "몰려 있습니다",  # 결측 편중
    "편중 배수",
    "신뢰 제한",
    "최장 연속 결측",
    "한 번의 초과가 12개월간",  # 계약전력 하향 위험 (9.4)
    "잠정입니다",  # 갑 종별 기본요금 기준 (미해결)
    # **"미달" 하나로 잡지 않는다.** 설명문 안의 "기준 95% 미달 시…" 까지 걸려
    # 참고여야 할 안내가 주의로 올라온다. 단언하는 어미만 본다.
    "에 미달합니다",
    "새 피크",
    "고출력 셀",
    "초과사용부가금",
    "지키지 못했습니다",
    "종별을 확인하십시오",
    "계산하지 않았습니다",
)

# **참고로 내릴 것.** 전제·한계·출처다. 화면에서 뺀다.
INFO_PATTERNS: tuple[str, ...] = (
    "기후환경요금",  # 미포함 요금요소 (5.1)
    "미포함",
    "청구서로 검증되지 않았습니다",  # 요금표 검증 상태 — 기준 데이터 화면에만
    "직전 12개월 최대수요 이력이 없어",  # 청구서가 있어야 풀리는 것
    "부분 계량",  # 그리드 이탈 행 처리
    "그리드 이탈",
    "kW 미만 구간",  # 저부하 구간 관측
    "정전 추정",  # 처리했다는 사실의 보고
    "야간 진상 여부를 확인하지 않았습니다",  # 모르면 지상 간주 — 추가 0 이다
    "하한을 적용하지 않았습니다",
    "요금적용전력은 중간·최대부하",  # 산출 근거 설명
    "감도를 적용하지 않습니다",
    "설비 도입과 무관한 확정 계산",
    "결측 보정 기준을 함께 봅니다",
    "12개월 미만입니다",
    "보간하지 않으며",
    "참고 문턱",
    "자격요건",
)


def classify(message: str) -> Severity:
    """문구 하나의 심각도. **모르면 주의다.**"""
    text = message.strip()
    for pattern in WARN_PATTERNS:
        if pattern in text:
            return Severity.WARN
    for pattern in INFO_PATTERNS:
        if pattern in text:
            return Severity.INFO
    return Severity.WARN


def _fingerprint(message: str) -> str:
    """같은 사실인지 가리는 지문.

    같은 사실이 꼬리만 다르게 여러 곳에서 온다 — ``2023-11 결측률 32.3% — …``
    가 품질 검사와 요금 엔진에서 각각 나오는 식이다. **줄표 앞까지**를 지문으로
    삼아 하나만 남긴다.
    """
    head = re.split(r"\s[—-]\s", message.strip(), maxsplit=1)[0]
    return re.sub(r"\s+", " ", head).strip().rstrip(".")


def dedupe(messages: Iterable[str]) -> tuple[str, ...]:
    """같은 사실을 한 번만 남긴다. **처음 나온 것을 남긴다** (발생 지점)."""
    seen: set[str] = set()
    kept: list[str] = []
    for message in messages:
        text = message.strip()
        if not text:
            continue
        key = _fingerprint(text)
        if key in seen:
            continue
        seen.add(key)
        kept.append(text)
    return tuple(kept)


def _notices(messages: Iterable[str]) -> tuple[Notice, ...]:
    return tuple(Notice(classify(text), text) for text in dedupe(messages))


def screen_notices(*groups: Iterable[str]) -> tuple[Notice, ...]:
    """화면에 띄울 것 — **차단과 주의만.** 참고는 보고서로 간다.

    문구는 :func:`kwise.ui.text.markdown_safe` 를 거친다. 계산 모듈이 내는
    ``08~22시`` 같은 표기가 한 줄에 둘 있으면 취소선이 되기 때문이다 (13세션).
    산출물로 가는 :func:`report_notices` 는 escape 하지 않는다 — Excel·Word 는
    마크다운을 해석하지 않는다.
    """
    merged = [text for group in groups for text in group]
    return tuple(
        Notice(item.severity, markdown_safe(item.text))
        for item in _notices(merged)
        if item.on_screen
    )


def partition(
    notices: Iterable[Notice], patterns: Iterable[str]
) -> tuple[tuple[Notice, ...], tuple[Notice, ...]]:
    """(패턴에 걸린 것, 나머지). **같은 사실을 두 곳에 내지 않으려고** 쓴다.

    결측 안내처럼 전용 블록이 따로 있는 것들은 위쪽 경고 목록에서 빼고 그 블록의
    확인사항으로 내린다 (13세션).
    """
    keys = tuple(patterns)
    matched = tuple(item for item in notices if any(key in item.text for key in keys))
    rest = tuple(item for item in notices if not any(key in item.text for key in keys))
    return matched, rest


def report_notices(*groups: Iterable[str]) -> tuple[Notice, ...]:
    """산출물에 실을 것 — **전부.** 등급 순으로 정렬한다."""
    merged = [text for group in groups for text in group]
    order = {Severity.BLOCK: 0, Severity.WARN: 1, Severity.INFO: 2}
    return tuple(sorted(_notices(merged), key=lambda item: order[item.severity]))
