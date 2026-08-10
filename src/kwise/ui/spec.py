"""개선 수단 카드 정의 (요구사항서 7장·10.1).

**투자비 순으로 배치한다. 이 순서를 바꾸지 않는다.** 수단을 더할 때마다 번호가
밀리면 코드 docstring 과 문서 참조가 계속 어긋난다 (8차 결정).

    투자 0원   7.1 선택요금 전환 · 7.2 계약전력 조정 · 7.3 경제성DR
    저투자     7.4 역률 개선
    투자       7.5 태양광 · 7.6 ESS · 7.7 잉여 활용

카드는 **접힌 상태로 시작**하고 켠 것만 3단계 조합 비교와 산출물에 들어간다.
켜지 않은 수단은 "미검토" 로 남으며, 그 사실을 3단계 「검토 범위」가 밝힌다 —
빠진 것을 조용히 빼면 "검토했더니 효과가 없었다" 로 읽힌다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

__all__ = [
    "MEASURES",
    "NO_INVESTMENT_KEYS",
    "MeasureSpec",
    "ReviewScope",
    "measure",
    "measure_keys",
    "review_scope",
]


@dataclass(frozen=True)
class MeasureSpec:
    """카드 하나.

    Attributes:
        key: 화면 상태 키. 세션 상태와 조합 구성에 쓴다.
        number: 요구사항서 절 번호. 카드 제목에 그대로 붙인다.
        label: 카드 제목.
        tier: 투자 구분. 카드를 묶는 소제목이 된다.
        anchor: [자세히] 링크가 갈 매뉴얼 앵커.
        headline: **화면에 두는 한 줄.** 없으면 입력을 못 하거나 결과를 오독하는 것만.
        needs_pv: 태양광 결과가 있어야 켤 수 있는 수단인가.
    """

    key: str
    number: str
    label: str
    tier: str
    anchor: str
    headline: str
    needs_pv: bool = False

    @property
    def title(self) -> str:
        return f"{self.number} {self.label}"


MEASURES: tuple[MeasureSpec, ...] = (
    MeasureSpec(
        key="tariff_switch",
        number="7.1",
        label="선택요금 전환",
        tier="투자 0원",
        anchor="measure-tariff-switch",
        headline="현행 선택요금과 같은 종별·전압의 다른 선택요금을 모두 다시 계산해 견줍니다.",
    ),
    MeasureSpec(
        key="contract",
        number="7.2",
        label="계약전력 조정",
        tier="투자 0원",
        anchor="measure-contract",
        headline="여유율과 하향 여지를 봅니다. 하향은 되돌리기 어렵고 초과 시 위약이 따릅니다.",
    ),
    MeasureSpec(
        key="demand_response",
        number="7.3",
        label="경제성DR",
        tier="투자 0원",
        anchor="measure-dr",
        headline="감축 가능량까지만 냅니다. 정산 단가는 사업자 상담이 필요합니다.",
    ),
    MeasureSpec(
        key="power_factor",
        number="7.4",
        label="역률 개선",
        tier="저투자 (콘덴서·APFR)",
        anchor="measure-power-factor",
        headline="현재 역률과 도입 후 역률로 각각 요금을 다시 계산합니다.",
    ),
    MeasureSpec(
        key="solar",
        number="7.5",
        label="태양광",
        tier="투자",
        anchor="measure-solar",
        headline="면적·설치 밀도·지역 셋만 받아 용량 곡선을 훑습니다.",
    ),
    MeasureSpec(
        key="ess",
        number="7.6",
        label="ESS",
        tier="투자",
        anchor="measure-ess",
        headline="목표 요금적용전력에서 출력·용량·방전시간을 역산합니다.",
    ),
    MeasureSpec(
        key="surplus",
        number="7.7",
        label="잉여 활용",
        tier="투자",
        anchor="measure-surplus",
        headline="태양광 잉여 전력의 활용 시나리오입니다. 자격요건은 판정하지 않습니다.",
        needs_pv=True,
    ),
)

NO_INVESTMENT_KEYS: tuple[str, ...] = tuple(
    item.key for item in MEASURES if item.tier == "투자 0원"
)

_BY_KEY: dict[str, MeasureSpec] = {item.key: item for item in MEASURES}


def measure_keys() -> tuple[str, ...]:
    return tuple(item.key for item in MEASURES)


def measure(key: str) -> MeasureSpec:
    try:
        return _BY_KEY[key]
    except KeyError as exc:
        raise KeyError(f"등록되지 않은 개선 수단입니다: {key!r}") from exc


@dataclass(frozen=True)
class ReviewScope:
    """검토 범위 (요구사항서 10.1 — 3단계).

    **미검토를 빈칸으로 두지 않는다.** 켜지 않은 수단은 "효과가 없다" 가 아니라
    "보지 않았다" 이며, 둘은 결론이 다르다.
    """

    reviewed: tuple[MeasureSpec, ...]
    skipped: tuple[MeasureSpec, ...]

    @property
    def reviewed_labels(self) -> tuple[str, ...]:
        return tuple(item.title for item in self.reviewed)

    @property
    def skipped_labels(self) -> tuple[str, ...]:
        return tuple(item.title for item in self.skipped)

    def text(self) -> str:
        checked = ", ".join(self.reviewed_labels) or "없음"
        missing = ", ".join(self.skipped_labels) or "없음"
        return f"검토함 — {checked} / 미검토 — {missing}"


def review_scope(enabled: Iterable[str]) -> ReviewScope:
    """켠 수단과 켜지 않은 수단을 **7장 순서 그대로** 가른다."""
    chosen = set(enabled)
    unknown = chosen - set(_BY_KEY)
    if unknown:
        raise KeyError(f"등록되지 않은 개선 수단입니다: {sorted(unknown)}")
    return ReviewScope(
        reviewed=tuple(item for item in MEASURES if item.key in chosen),
        skipped=tuple(item for item in MEASURES if item.key not in chosen),
    )
