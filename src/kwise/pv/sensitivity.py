"""감도 3종 (요구사항서 9.2).

확률 표기(P90 등)를 쓰지 않는다. 부하가 1년 실측으로 고정되어 연도별 변동의
절반만 반영되므로 확률 표기가 통계적으로 정직하지 않기 때문이다. 대신 PV 출력에
계수를 곱한 세 시나리오를 나란히 놓는다.

    보수  × 0.70
    기준  × 1.00
    낙관  × 1.20

**요금제 전환과 계약전력 조정에는 감도를 적용하지 않는다.** 실측 데이터와
요금표만으로 확정되는 계산이다.

시나리오는 순차 처리한다. :func:`iter_scenarios` 는 제너레이터이고
:func:`summarize_scenarios` 는 요약만 남긴다. 세 시계열을 동시에 들고 있지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

import pandas as pd

__all__ = [
    "DEFAULT_FACTORS",
    "ScenarioSummary",
    "SensitivityFactors",
    "iter_scenarios",
    "summarize_scenarios",
]


@dataclass(frozen=True)
class SensitivityFactors:
    """감도 계수. 사용자가 조정할 수 있다.

    실측 발전 데이터가 있으면 피크 시간대(10~15시)의 예측 대비 실측 비율 분포에서
    계수를 산출한다.
    """

    conservative: float = 0.70
    base: float = 1.00
    optimistic: float = 1.20
    names: tuple[str, str, str] = field(default=("보수", "기준", "낙관"))

    def __post_init__(self) -> None:
        values = (self.conservative, self.base, self.optimistic)
        if any(value < 0 for value in values):
            raise ValueError(f"감도 계수는 음수일 수 없습니다: {values}")
        if not self.conservative <= self.base <= self.optimistic:
            raise ValueError(f"감도 계수는 보수 ≤ 기준 ≤ 낙관 이어야 합니다: {values}")

    def items(self) -> tuple[tuple[str, float], ...]:
        return tuple(zip(self.names, (self.conservative, self.base, self.optimistic), strict=True))


DEFAULT_FACTORS = SensitivityFactors()


@dataclass(frozen=True)
class ScenarioSummary:
    """시나리오 하나의 요약. 시계열은 들고 있지 않는다."""

    name: str
    factor: float
    total_kwh: float
    peak_kw: float


def iter_scenarios(
    kw: pd.Series,
    factors: SensitivityFactors = DEFAULT_FACTORS,
) -> Iterator[tuple[str, float, pd.Series]]:
    """시나리오를 하나씩 낸다. 호출자가 요약만 챙기면 메모리가 한 벌로 유지된다."""
    for name, factor in factors.items():
        yield name, factor, (kw * factor).rename(f"pv_kw_{name}")


def summarize_scenarios(
    kw: pd.Series,
    interval_minutes: int,
    factors: SensitivityFactors = DEFAULT_FACTORS,
) -> tuple[ScenarioSummary, ...]:
    """감도 3종의 발전량·피크만 뽑는다."""
    slot_hours = interval_minutes / 60.0
    summaries: list[ScenarioSummary] = []
    for name, factor, scaled in iter_scenarios(kw, factors):
        summaries.append(
            ScenarioSummary(
                name=name,
                factor=factor,
                total_kwh=float(scaled.sum()) * slot_hours,
                peak_kw=float(scaled.max()) if len(scaled) else 0.0,
            )
        )
    return tuple(summaries)
