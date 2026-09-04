"""기준 데이터 검증 (요구사항서 12장).

**저장 전에 반드시 통과시킨다.** 실패하면 저장하지 않고 어느 항목이 왜 틀렸는지
돌려준다. 역률 하한이 상한보다 크거나 대상월에 13월이 들어간 파일로 계산하면
숫자가 그럴듯하게 나오기 때문에 발견이 늦다.

검증 **규칙**은 코드에 둔다 (3세션 결정과 같은 이유다). 규칙은 요금 데이터가
아니라 데이터에 대한 우리의 기대이고, 데이터를 갱신했다고 규칙이 먼저 깨져서는
안 된다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from kwise.rules.schema import RuleSet

__all__ = [
    "ValidationIssue",
    "validate_ruleset",
]


@dataclass(frozen=True)
class ValidationIssue:
    """검증 실패 하나. **어느 항목이 왜 틀렸는지**를 담는다."""

    key: str
    reason: str

    def __str__(self) -> str:
        return f"{self.key}: {self.reason}"


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _ratio(key: str, value: Any, *, low: float = 0.0, high: float = 1.0) -> list[ValidationIssue]:
    number = _number(value)
    if number is None:
        return [ValidationIssue(key, f"비율은 숫자여야 합니다: {value!r}")]
    if not low <= number <= high:
        return [ValidationIssue(key, f"비율은 {low}~{high} 여야 합니다: {number}")]
    return []


def _percent(key: str, value: Any) -> list[ValidationIssue]:
    number = _number(value)
    if number is None:
        return [ValidationIssue(key, f"역률은 숫자여야 합니다: {value!r}")]
    if not 0 < number <= 100:
        return [ValidationIssue(key, f"역률은 0 초과 100 이하여야 합니다: {number}")]
    return []


def _positive(key: str, value: Any) -> list[ValidationIssue]:
    number = _number(value)
    if number is None:
        return [ValidationIssue(key, f"숫자여야 합니다: {value!r}")]
    if number <= 0:
        return [ValidationIssue(key, f"양수여야 합니다: {number}")]
    return []


def _months(key: str, value: Any) -> list[ValidationIssue]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return [ValidationIssue(key, f"월 목록이어야 합니다: {value!r}")]
    issues: list[ValidationIssue] = []
    for month in value:
        if not isinstance(month, int) or not 1 <= month <= 12:
            issues.append(ValidationIssue(key, f"월은 1~12 여야 합니다: {month!r}"))
    if len(set(value)) != len(list(value)):
        issues.append(ValidationIssue(key, f"월이 중복되었습니다: {value!r}"))
    return issues


def _names(key: str, value: Any) -> list[ValidationIssue]:
    """비지 않은 이름 목록. 계약종별 키처럼 **코드가 대조하는** 값이다."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        return [ValidationIssue(key, f"비지 않은 이름 목록이어야 합니다: {value!r}")]
    return [
        ValidationIssue(key, f"이름은 빈 문자열이 아니어야 합니다: {item!r}")
        for item in value
        if not isinstance(item, str) or not item
    ]


def _month_map(key: str, value: Any) -> list[ValidationIssue]:
    """월분 → 기준월 목록. **어긋나면 할인이 엉뚱한 달에 붙는다.**"""
    if not isinstance(value, Mapping) or not value:
        return [ValidationIssue(key, f"월분별 기준월 사전이어야 합니다: {value!r}")]
    issues: list[ValidationIssue] = []
    for target, months in value.items():
        try:
            number = int(target)
        except (TypeError, ValueError):
            issues.append(ValidationIssue(key, f"월분은 1~12 여야 합니다: {target!r}"))
            continue
        if not 1 <= number <= 12:
            issues.append(ValidationIssue(key, f"월분은 1~12 여야 합니다: {target!r}"))
        issues.extend(_months(key, months))
    return issues


def _weights(key: str, value: Any) -> list[ValidationIssue]:
    """진행률 가중치. **합이 1 에서 크게 벗어나면 막대가 끝까지 안 간다.**"""
    if not isinstance(value, Mapping):
        return [ValidationIssue(key, f"단계별 가중치 사전이어야 합니다: {value!r}")]
    issues: list[ValidationIssue] = []
    for stage, weight in value.items():
        number = _number(weight)
        if number is None or not 0.0 <= number <= 1.0:
            issues.append(ValidationIssue(key, f"{stage} 가중치는 0~1 이어야 합니다: {weight!r}"))
    total = sum(_number(weight) or 0.0 for weight in value.values())
    if value and abs(total - 1.0) > 0.01:
        issues.append(ValidationIssue(key, f"가중치 합이 1.0 이어야 합니다: {total:.4f}"))
    return issues


def _hour_window(key: str, value: Any) -> list[ValidationIssue]:
    if not isinstance(value, Sequence) or len(value) != 2:
        return [ValidationIssue(key, f"[시작, 끝] 두 값이어야 합니다: {value!r}")]
    start, end = value
    issues: list[ValidationIssue] = []
    for hour in (start, end):
        if not isinstance(hour, int) or not 0 <= hour <= 24:
            issues.append(ValidationIssue(key, f"시각은 0~24 여야 합니다: {hour!r}"))
    return issues


def _hour_windows(key: str, value: Any) -> list[ValidationIssue]:
    """[[시작, 끝], …] 구간 목록. 경제성DR 운영 시간대처럼 구간이 둘 이상인 값."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        return [ValidationIssue(key, f"구간 목록이어야 합니다: {value!r}")]
    issues: list[ValidationIssue] = []
    for window in value:
        issues.extend(_hour_window(key, window))
    return issues


def _excess_tiers(key: str, value: Any) -> list[ValidationIssue]:
    """[[초과비율 하한, 기본요금 단가 배수], …] — 제67조의3 ③ 의 구간표.

    **하한이 올라가는 차례로 서 있어야 한다.** 뒤엉키면 판정이 첫 줄에서 멈춰
    배수 하나가 전 구간에 걸리는데, 금액이 그럴듯해서 발견이 늦다.
    """
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        return [ValidationIssue(key, f"구간 목록이어야 합니다: {value!r}")]
    issues: list[ValidationIssue] = []
    previous: tuple[float, float] | None = None
    for tier in value:
        if not isinstance(tier, Sequence) or isinstance(tier, (str, bytes)) or len(tier) != 2:
            issues.append(ValidationIssue(key, f"[하한, 배수] 두 값이어야 합니다: {tier!r}"))
            continue
        floor, multiplier = _number(tier[0]), _number(tier[1])
        if floor is None or not 0.0 <= floor < 1.0:
            issues.append(ValidationIssue(key, f"초과비율 하한은 0 이상 1 미만이어야 합니다: {tier[0]!r}"))
            continue
        if multiplier is None or multiplier <= 0:
            issues.append(ValidationIssue(key, f"배수는 양수여야 합니다: {tier[1]!r}"))
            continue
        if previous is not None and (floor <= previous[0] or multiplier <= previous[1]):
            issues.append(
                ValidationIssue(key, f"하한과 배수가 함께 커져야 합니다: {previous!r} → {tier!r}")
            )
        previous = (floor, multiplier)
    if previous is not None and _number(value[0][0]) != 0.0:
        issues.append(ValidationIssue(key, "첫 구간의 하한은 0 이어야 합니다 (초과가 곧 부가금이다)."))
    return issues


def _ratio_range(key: str, value: Any) -> list[ValidationIssue]:
    """[하한, 상한] 비율. 하한이 상한보다 크면 화면 권장 구간이 뒤집힌다."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 2:
        return [ValidationIssue(key, f"[하한, 상한] 두 값이어야 합니다: {value!r}")]
    issues = _ratio(key, value[0]) + _ratio(key, value[1])
    low, high = _number(value[0]), _number(value[1])
    if low is not None and high is not None and low > high:
        issues.append(ValidationIssue(key, f"하한이 상한보다 큽니다: {low} > {high}"))
    return issues


# 항목별 단일 검사. 키가 없으면 그 검사는 건너뛴다 (항목이 늘어도 깨지지 않는다).
_SINGLE: Mapping[str, Callable[[str, Any], list[ValidationIssue]]] = {
    "demand.contract_floor_ratio.default": _ratio,
    "demand.contract_floor_ratio.school_exception": _ratio,
    "demand.months": _months,
    "demand.window_months": _positive,
    "school_exception.base_discount_ratio": _ratio,
    "school_exception.hvac_discount_ratio": _ratio,
    "school_exception.contract_types": _names,
    "school_exception.base_reference_months": _month_map,
    "power_factor.lagging_standard_pct": _percent,
    "power_factor.deemed_lagging_pct": _percent,
    "power_factor.lagging_floor_pct": _percent,
    "power_factor.lagging_rebate_cap_pct": _percent,
    "power_factor.leading_standard_pct": _percent,
    "power_factor.leading_floor_pct": _percent,
    "power_factor.leading_lagging_deemed_pct": _percent,
    "power_factor.adjustment_per_percent": _ratio,
    "power_factor.day_window": _hour_window,
    # 초과사용부가금 (제67조의3 ③·④ · 109세션).
    "excess_charge.ratio_tiers": _excess_tiers,
    "excess_charge.grace_months": _positive,
    "contract_type.threshold_kw.general": _positive,
    "contract_type.threshold_kw.industrial": _positive,
    "contract_type.threshold_kw.education": _positive,
    "dr.reference_capacity_kw": _positive,
    "dr.national_max_contract_kw": _positive,
    "dr.small_medium_industrial_max_kw": _positive,
    "dr.day_hours": _hour_window,
    "sensitivity.sharpness.conservative": _positive,
    "sensitivity.sharpness.base": _positive,
    "sensitivity.sharpness.optimistic": _positive,
    "dr.registration_percentile": _ratio,
    "dr.low_load_multiple": _positive,
    "ess.round_trip": _ratio,
    "ess.dod": _ratio,
    "ess.payback_target_years": _positive,
    "ess.target_step_ratio": _ratio,
    "ess.target_search_ratio": _ratio,
    # 최적 목표 정밀화 (40세션). **계산 시간을 정하는 값이라 기준 데이터가 쥔다.**
    "ess.refine_window_ratio": _ratio,
    "ess.refine_max_widen": _positive,
    "pv.area_per_kwp_m2": _positive,
    "pv.surplus_heavy_share": _ratio,
    # 보고서 해석 문장의 갈림값 (39세션). **계산에 쓰이지 않는다** — 문장을
    # 고르는 데만 쓰지만, 사람이 정한 경계라 기준 데이터가 쥔다.
    "narrative.load_factor_flat": _ratio,
    "narrative.base_load_high": _ratio,
    "narrative.base_fee_share_high": _ratio,
    "narrative.base_fee_share_low": _ratio,
    "narrative.peak_month_close": _ratio,
    # 잉여가 어느 날에 몰렸는지로 문장이 갈린다 (53세션 3절).
    "narrative.surplus_off_day_high": _ratio,
    "narrative.surplus_off_day_low": _ratio,
    "dr.bid_restriction_months": _positive,
    "dr.max_events_per_day": _positive,
    "dr.market_hours": _hour_windows,
    "dr.event_hours": _hour_window,
    "progress.total_seconds": _positive,
    "progress.slow_stage_seconds": _positive,
    "progress.weights": _weights,
    "expiry.tariff_months": _positive,
    "expiry.statute_months": _positive,
    "expiry.reference_months": _positive,
}


def _pair_checks(values: Mapping[str, Any]) -> list[ValidationIssue]:
    """항목 사이의 관계. **하나만 보면 알 수 없는 오류를 여기서 잡는다.**"""
    issues: list[ValidationIssue] = []

    def get(key: str) -> float | None:
        return _number(values.get(key))

    floor = get("power_factor.lagging_floor_pct")
    standard = get("power_factor.lagging_standard_pct")
    cap = get("power_factor.lagging_rebate_cap_pct")
    if floor is not None and standard is not None and floor >= standard:
        issues.append(
            ValidationIssue(
                "power_factor.lagging_floor_pct",
                f"지상역률 하한({floor})이 기준({standard}) 이상입니다. 하한 < 기준 이어야 합니다.",
            )
        )
    if standard is not None and cap is not None and standard >= cap:
        issues.append(
            ValidationIssue(
                "power_factor.lagging_rebate_cap_pct",
                f"감액 상한({cap})이 기준({standard}) 이하입니다. 기준 < 상한 이어야 합니다.",
            )
        )
    leading_floor = get("power_factor.leading_floor_pct")
    leading_standard = get("power_factor.leading_standard_pct")
    if (
        leading_floor is not None
        and leading_standard is not None
        and leading_floor >= leading_standard
    ):
        issues.append(
            ValidationIssue(
                "power_factor.leading_floor_pct",
                f"진상역률 하한({leading_floor})이 기준({leading_standard}) 이상입니다.",
            )
        )

    small = get("dr.national_max_contract_kw")
    large = get("dr.small_medium_industrial_max_kw")
    if small is not None and large is not None and small >= large:
        issues.append(
            ValidationIssue(
                "dr.national_max_contract_kw",
                f"국민DR 상한({small})이 중소형DR 상한({large}) 이상입니다. "
                "자원 유형 임계는 작은 쪽이 먼저여야 합니다.",
            )
        )

    conservative = get("sensitivity.sharpness.conservative")
    base = get("sensitivity.sharpness.base")
    optimistic = get("sensitivity.sharpness.optimistic")
    if None not in (conservative, base, optimistic):
        assert conservative is not None and base is not None and optimistic is not None
        if not conservative <= base <= optimistic:
            issues.append(
                ValidationIssue(
                    "sensitivity.sharpness.base",
                    f"첨예도는 평탄 ≤ 기준 ≤ 첨예 여야 합니다: "
                    f"{conservative} / {base} / {optimistic}",
                )
            )

    education = get("contract_type.threshold_kw.education")
    general = get("contract_type.threshold_kw.general")
    if education is not None and general is not None and education <= general:
        issues.append(
            ValidationIssue(
                "contract_type.threshold_kw.education",
                f"교육용 임계({education})가 일반용({general}) 이하입니다. "
                "교육용은 1,000 kW 로 더 높습니다.",
            )
        )
    return issues


def validate_ruleset(ruleset: RuleSet) -> tuple[ValidationIssue, ...]:
    """한 벌 전체를 검증한다. **빈 튜플이면 저장해도 된다.**"""
    issues: list[ValidationIssue] = []
    values = {key: item.value for key, item in ruleset.items.items()}
    for key, check in _SINGLE.items():
        if key in values:
            issues.extend(check(key, values[key]))
    issues.extend(_pair_checks(values))
    return tuple(issues)
