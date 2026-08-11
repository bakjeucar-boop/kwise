"""개선 수단 목록 (요구사항서 7장). **투자비 순이다.**

번호와 순서를 여기 한 곳에 둔다. 화면(:mod:`kwise.ui.spec`)과 보고서
(:mod:`kwise.report.document`)가 각자 목록을 들고 있으면 **한쪽만 고쳤을 때
어긋나고, 그 어긋남은 산출물을 나란히 놓고 봐야 드러난다.**

순서는 8차 세션에서 확정했다. 수단을 더할 때마다 번호가 밀리면 코드 docstring 과
문서 참조가 계속 어긋나므로 **바꾸지 않는다.**
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "MEASURE_CATALOG",
    "TIER_INVESTMENT",
    "TIER_LOW",
    "TIER_NONE",
    "MeasureKind",
    "measure_kind",
    "measure_numbers",
]

TIER_NONE = "투자 0원"
TIER_LOW = "저투자 (콘덴서·APFR)"
TIER_INVESTMENT = "투자"


@dataclass(frozen=True)
class MeasureKind:
    """수단 한 종류. 화면·보고서가 함께 쓰는 최소 정보다."""

    key: str
    number: str
    label: str
    tier: str

    @property
    def title(self) -> str:
        return f"{self.number} {self.label}"


MEASURE_CATALOG: tuple[MeasureKind, ...] = (
    MeasureKind("tariff_switch", "7.1", "선택요금 전환", TIER_NONE),
    MeasureKind("contract", "7.2", "계약전력 조정", TIER_NONE),
    MeasureKind("demand_response", "7.3", "경제성DR", TIER_NONE),
    MeasureKind("power_factor", "7.4", "역률 개선", TIER_LOW),
    MeasureKind("solar", "7.5", "태양광", TIER_INVESTMENT),
    MeasureKind("ess", "7.6", "ESS", TIER_INVESTMENT),
    MeasureKind("surplus", "7.7", "잉여 활용", TIER_INVESTMENT),
)

_BY_KEY: dict[str, MeasureKind] = {item.key: item for item in MEASURE_CATALOG}


def measure_kind(key: str) -> MeasureKind:
    try:
        return _BY_KEY[key]
    except KeyError as exc:
        raise KeyError(f"등록되지 않은 개선 수단입니다: {key!r}") from exc


def measure_numbers() -> tuple[str, ...]:
    return tuple(item.number for item in MEASURE_CATALOG)
