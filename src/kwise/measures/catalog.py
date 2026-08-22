"""개선 수단 목록 (요구사항서 7장). **7장 번호 순(7.1~7.7)이다.**

번호와 순서를 여기 한 곳에 둔다. 화면(:mod:`kwise.ui.spec`)과 보고서
(:mod:`kwise.report.document`)가 각자 목록을 들고 있으면 **한쪽만 고쳤을 때
어긋나고, 그 어긋남은 산출물을 나란히 놓고 봐야 드러난다.**

순서는 8차 세션에서 확정했다. 수단을 더할 때마다 번호가 밀리면 코드 docstring 과
문서 참조가 계속 어긋나므로 **바꾸지 않는다.** 투자 구분(:data:`TIER_NONE` 등)은
읽기를 돕는 딱지일 뿐 배치 기준이 아니다.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "MEASURE_CATALOG",
    "TIER_INVESTMENT",
    "TIER_LOW",
    "TIER_NONE",
    "AppliedMeasure",
    "MeasureKind",
    "measure_kind",
    "measure_numbers",
]

TIER_NONE = "투자 0원"
TIER_LOW = "저투자 (역률 개선)"
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


# 파라미터 이름 → 단위. 라벨을 만들 때만 쓴다.
_PARAM_UNITS: dict[str, str] = {
    "capacity_kwp": "kWp",
    "target_kw": "kW",
    "contract_kw": "kW",
    "capacity_kwh": "kWh",
    # **단위가 빠지면 숫자가 무엇인지 알 수 없다** (39세션 2-5). 「역률 개선 (97)」
    # 이 그렇게 나가고 있었다.
    "power_factor_pct": "%",
}


@dataclass(frozen=True)
class AppliedMeasure:
    """조합에 들어간 수단 하나 — **등록 키와 파라미터**.

    표시용 문자열을 키 자리에 담으면 안 된다. 「계약전력 5,500 kW」 같은 값이
    키로 흘러들면 :func:`measure_kind` 가 막혀 화면 전체가 죽는다 (13세션).
    키와 파라미터를 갈라 두고 라벨은 **조회로** 만든다.
    """

    key: str
    params: tuple[tuple[str, float], ...] = ()

    @property
    def label(self) -> str:
        """``계약전력 조정 (5,500 kW)``. 등록되지 않은 키면 키를 그대로 낸다."""
        try:
            name = measure_kind(self.key).label
        except KeyError:
            return self.key
        if not self.params:
            return name
        return f"{name} ({' · '.join(self.param_texts)})"

    @property
    def param_texts(self) -> tuple[str, ...]:
        """파라미터를 **단위와 함께** 적은 조각들."""
        return tuple(
            # 퍼센트는 값과 기호를 붙여 적는다 — 「97 %」 는 우리말 표기가 아니다.
            f"{value:,.0f}{unit}"
            if (unit := _PARAM_UNITS.get(field, "")) == "%"
            else f"{value:,.0f} {unit}".strip()
            for field, value in self.params
        )

    @property
    def short_label(self) -> str:
        """괄호 없이 이어 적는 이름 — ``태양광 240 kWp`` (39세션 3-3).

        조합 구성을 한 줄로 이을 때 쓴다. 괄호를 겹쳐 적으면 「태양광 (240 kWp)」
        가 나열되어 무엇이 묶였는지가 오히려 흐려진다.
        """
        try:
            name = measure_kind(self.key).label
        except KeyError:
            return self.key
        return f"{name} {' · '.join(self.param_texts)}" if self.params else name
