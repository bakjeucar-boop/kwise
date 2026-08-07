"""조합 비교 (요구사항서 8장).

    default_combinations()   기준선 / 요금제만 / +태양광 / +ESS
    compare_combinations()   조합을 순차로 평가해 비교표를 만든다

**조합마다 요금을 다시 계산한다.** 수단별 절감액을 더하지 않는다.
"""

from kwise.compare.combination import (
    CombinationResult,
    CombinationSpec,
    ComparisonResult,
    compare_combinations,
    default_combinations,
    evaluate_combination,
)
from kwise.compare.sensitivity import (
    RANGE_METRICS,
    SCENARIO_NAME_CAVEAT,
    SENSITIVITY_NOTE,
    SensitivityRange,
    sensitivity_comparison,
    sensitivity_range_frame,
    sensitivity_ranges,
)

__all__ = [
    "RANGE_METRICS",
    "SCENARIO_NAME_CAVEAT",
    "SENSITIVITY_NOTE",
    "CombinationResult",
    "CombinationSpec",
    "ComparisonResult",
    "SensitivityRange",
    "compare_combinations",
    "default_combinations",
    "evaluate_combination",
    "sensitivity_comparison",
    "sensitivity_range_frame",
    "sensitivity_ranges",
]
