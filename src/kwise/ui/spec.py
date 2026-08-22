"""개선 수단 카드 정의 (요구사항서 7장·10.1).

**7장 번호 순(7.1~7.7)으로 배치한다. 이 순서를 바꾸지 않는다.** 수단을 더할 때마다
번호가 밀리면 코드 docstring 과 문서 참조가 계속 어긋난다 (8차 결정).

    7.1 선택요금 전환 · 7.2 계약전력 조정 · 7.3 경제성DR · 7.4 역률 개선
    7.5 태양광 · 7.6 ESS · 7.7 잉여 활용

**모든 카드는 독립 평가다** (14세션). 각 카드의 숫자는 "지금 이 수단만 도입하면
얼마" 이고 기준선은 언제나 **현행 요금제·현행 사용량**이다. 어떤 수단을 켜고 끄든
다른 카드의 값이 바뀌지 않으며, **다른 카드 때문에 비활성이 되는 카드도 없다.**
상호작용은 3단계 합산효과에서만 다룬다.

카드는 **접힌 상태로 시작**하고 켠 것만 3단계 조합 비교와 산출물에 들어간다.
켜지 않은 수단은 "미검토" 로 남으며, 그 사실을 3단계 「검토 범위」가 밝힌다 —
빠진 것을 조용히 빼면 "검토했더니 효과가 없었다" 로 읽힌다.

:attr:`MeasureSpec.overview` 는 **무엇을 어떻게 개선하는지** 두세 줄로 적은
개요다. 입력 아래·결과 숫자 위에 놓는다 (14세션 2-2).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from kwise.measures import MEASURE_CATALOG, TIER_NONE, MeasureKind

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

    번호·라벨·투자 구분은 :data:`kwise.measures.MEASURE_CATALOG` 에서 온다 —
    보고서와 화면이 같은 목록을 봐야 어긋나지 않는다. 여기 더하는 것은 **화면에만
    필요한 것** 셋뿐이다.

    Attributes:
        kind: 공용 목록의 한 항목 (키·번호·라벨·투자 구분).
        anchor: [자세히] 링크가 갈 매뉴얼 앵커.
        headline: **화면에 두는 한 줄.** 없으면 입력을 못 하거나 결과를 오독하는 것만.
        overview: 무엇을 어떻게 개선하는지 두세 줄 (14세션 2-2). 입력 아래·결과 위.
    """

    kind: MeasureKind
    anchor: str
    headline: str
    overview: str

    @property
    def key(self) -> str:
        return self.kind.key

    @property
    def number(self) -> str:
        return self.kind.number

    @property
    def label(self) -> str:
        return self.kind.label

    @property
    def tier(self) -> str:
        return self.kind.tier

    @property
    def title(self) -> str:
        return self.kind.title


# 개요 (14세션 2-2). **무엇을 어떻게 개선하는지** 두세 줄이다. 제도 설명·근거
# 조문은 여기 넣지 않는다 — 매뉴얼로 보내고 화면에는 [자세히] 링크만 둔다.
_OVERVIEW: dict[str, str] = {
    "tariff_switch": (
        "같은 전기를 쓰면서 요금제만 바꿉니다. 기본요금 단가가 높고 전력량요금이 "
        "낮은 요금제로 옮기면, 사용시간이 긴 건물은 전체 요금이 줄어듭니다. "
        "설비 투자가 없고 한전에 신청만 하면 됩니다."
    ),
    "contract": (
        "계약전력을 낮추면 기본요금이 줄어듭니다. 다만 요금적용전력이 계약전력의 "
        "30% 하한에 걸리지 않아야 효과가 있습니다. 한 번 낮춘 뒤 초과하면 위약금과 "
        "12개월간 기본요금 상승이 따르므로 여유를 두어야 합니다."
    ),
    "demand_response": (
        "전력거래소가 감축을 요청할 때 자발적으로 입찰해 사용량을 줄이고 정산금을 "
        "받습니다. 설비 투자가 없습니다. 평일 09–20시에 하루 최대 2회 참여할 수 "
        "있으며, 낙찰 후 이행하지 못하면 6개월 입찰 제한을 받습니다."
    ),
    "power_factor": (
        "역률 개선 설비를 조정해 역률을 높이면 기본요금이 감액됩니다. 지상역률 92%를 넘으면 "
        "매 1%당 기본요금의 0.2%를 깎아주고, 97%까지 받을 수 있습니다. 미달하면 "
        "반대로 추가요금이 붙습니다."
    ),
    "solar": (
        "옥상 등 유휴 공간에 발전 설비를 설치해 사용량을 줄입니다. 전력량요금이 직접 "
        "줄고, 발전 시간대와 피크 시각이 겹치면 기본요금도 함께 줄어듭니다."
    ),
    "ess": (
        "배터리에 값싼 시간대 전기를 충전했다가 피크 시각에 방전해 최대수요를 "
        "낮춥니다. 기본요금이 줄고 시간대 단가차익도 생깁니다. 필요 용량이 목표에 "
        "매우 민감합니다."
    ),
}

# 화면에만 필요한 것 — 앵커와 한 줄 설명. 순서는 공용 목록이 정한다.
_SCREEN: dict[str, tuple[str, str]] = {
    "tariff_switch": (
        "measure-tariff-switch",
        "현행 선택요금과 같은 종별·전압의 다른 선택요금을 모두 다시 계산해 비교합니다.",
    ),
    "contract": (
        "measure-contract",
        # 뒷문장(하향의 위험)은 카드 본문의 경고와 같은 말이라 뺐다 (25세션 3-3 · C).
        "여유율과 하향 여지를 봅니다.",
    ),
    "demand_response": (
        "measure-dr",
        "감축 가능량까지만 냅니다. 정산 단가는 사업자 상담이 필요합니다.",
    ),
    "power_factor": (
        "measure-power-factor",
        "현재 역률과 도입 후 역률로 각각 요금을 다시 계산합니다.",
    ),
    "solar": ("measure-solar", "면적·설치 밀도·지역 셋만 받아 용량 곡선을 훑습니다."),
    "ess": ("measure-ess", "목표 요금적용전력에서 출력·용량·방전시간을 역산합니다."),
}

MEASURES: tuple[MeasureSpec, ...] = tuple(
    MeasureSpec(
        kind=kind,
        anchor=_SCREEN[kind.key][0],
        headline=_SCREEN[kind.key][1],
        overview=_OVERVIEW[kind.key],
    )
    for kind in MEASURE_CATALOG
)

NO_INVESTMENT_KEYS: tuple[str, ...] = tuple(item.key for item in MEASURES if item.tier == TIER_NONE)

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
