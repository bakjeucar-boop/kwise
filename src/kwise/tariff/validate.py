"""요금 데이터 검증 (요구사항서 부록 A.2).

단가를 수기로 옮길 때의 오타는 결과가 그럴듯하게 나와 발견하기 어렵다.
요금표 **자체의 구조적 규칙**을 검사해 자동 검출한다. 이 규칙들이 오타를 잡는
유일한 수단이므로 빠짐없이 구현한다.

    규칙 1  순서 관계
    규칙 2  선택요금 간 단가 차이의 균일성 (적용 범위 한정)
    규칙 3  손익분기 이용시간
    규칙 4  시간대 정의의 완전성

**단가를 임의로 고치지 않는다.** 검증 실패는 경고로 남기고 규칙 쪽을 검토한다
(요금표 조작이 되기 때문이다).

규칙 2·3 의 적용 범위와 허용오차는 요금 데이터가 아니라 **검증 정책**이므로
여기에 둔다. 종별을 넓힐 때 :data:`DEFAULT_POLICY` 에 항목을 더한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise

from kwise.tariff.schema import BANDS, ContractType, TariffTable, VoltageRates

__all__ = [
    "DEFAULT_POLICY",
    "DEFAULT_UNIFORM_TOLERANCE",
    "FLAT_RATE_POLICY",
    "OptionPairPolicy",
    "ValidationFinding",
    "option_pair_diffs",
    "validate_tariff",
]

DEFAULT_UNIFORM_TOLERANCE = 0.05


@dataclass(frozen=True)
class OptionPairPolicy:
    """선택요금 쌍 하나에 적용할 검증 정책.

    Attributes:
        uniform_tolerance: 전 계절·시간대 단가 차이의 균일성 허용오차 (원).
            None 이면 균일성 검사를 하지 않는다.
        breakeven_range: 손익분기 이용시간 허용 범위 (h/월). None 이면 검사하지 않는다.
        peak_focused: 최대부하에 할인이 집중된 설계인지. True 면 균일성 대신
            '최대부하 할인폭 > 중간·경부하 할인폭' 을 검사한다.
    """

    uniform_tolerance: float | None = DEFAULT_UNIFORM_TOLERANCE
    breakeven_range: tuple[float, float] | None = (150.0, 250.0)
    peak_focused: bool = False


# 전체시간(단일 단가) 종별의 기본 정책.
#
# 균일성 검사를 끈다. 전체시간 종별은 계절마다 단가가 하나뿐이라 검사 대상 칸이
# 9개가 아니라 3개이고, 선택Ⅰ→Ⅱ 할인폭을 계절별로 다르게 설계한다
# (일반용(갑)Ⅰ 고압A: 여름 4.0 / 봄가을 4.3 / 겨울 5.3원). 요금표가 약속하지
# 않은 것을 검사하는 셈이라 규칙 쪽이 틀린 것이다. 손익분기는 그대로 본다.
FLAT_RATE_POLICY = OptionPairPolicy(uniform_tolerance=None)

# 부록 A.2 의 표를 그대로 옮긴 것이다.
#   고압A Ⅰ→Ⅱ  5.5 원 ±0.05,  손익분기 1,100 ÷ 5.5 = 200 h
#   고압B Ⅰ→Ⅱ  3.8 원 ±0.05,  손익분기   750 ÷ 3.8 = 197 h
#   고압B Ⅱ→Ⅲ  1.6~1.7 원 ±0.15 (반올림 폭), 손익분기 810 ÷ 1.65 ≈ 491 h
#   고압A Ⅱ→Ⅲ  0.6~12.4 원 — 균일성·손익분기 모두 미적용. 최대부하 집중 설계다.
#
# 산업용(을)의 선택요금 차이 구조는 일반용(을)과 **완전히 같다** (고압A·B 의
# 기본요금과 단가 차이가 원 단위까지 일치한다). 고압C 만 산업용(을)에 있다.
DEFAULT_POLICY: Mapping[tuple[str, str, str, str], OptionPairPolicy] = {
    ("general_b", "high_a", "I", "II"): OptionPairPolicy(),
    ("general_b", "high_a", "II", "III"): OptionPairPolicy(
        uniform_tolerance=None, breakeven_range=None, peak_focused=True
    ),
    ("general_b", "high_b", "I", "II"): OptionPairPolicy(),
    ("general_b", "high_b", "II", "III"): OptionPairPolicy(
        uniform_tolerance=0.15, breakeven_range=(400.0, 600.0)
    ),
    # 산업용(을) — 고압A·B 는 일반용(을)과 같은 정책, 고압C 는 이 종별에만 있다.
    ("industrial_b", "high_a", "I", "II"): OptionPairPolicy(),
    ("industrial_b", "high_a", "II", "III"): OptionPairPolicy(
        uniform_tolerance=None, breakeven_range=None, peak_focused=True
    ),
    ("industrial_b", "high_b", "I", "II"): OptionPairPolicy(),
    ("industrial_b", "high_b", "II", "III"): OptionPairPolicy(
        uniform_tolerance=0.15, breakeven_range=(400.0, 600.0)
    ),
    ("industrial_b", "high_c", "I", "II"): OptionPairPolicy(),
    # 고압C Ⅱ→Ⅲ 는 1.1 원으로 균일하다. 손익분기 810 ÷ 1.1 = 736 h 가 아니라
    # 570 ÷ 1.1 ≈ 518 h — 고압B Ⅱ→Ⅲ 와 같은 '월 500시간 초과 유리' 대역이다.
    ("industrial_b", "high_c", "II", "III"): OptionPairPolicy(breakeven_range=(400.0, 600.0)),
}


@dataclass(frozen=True)
class ValidationFinding:
    """검증에 걸린 항목. 값을 고치지 않고 알리기만 한다."""

    rule: str
    target: str
    message: str

    def __str__(self) -> str:
        return f"[{self.rule}] {self.target} — {self.message}"


def option_pair_diffs(
    table: TariffTable, contract_type: str, voltage: str, lower: str, upper: str
) -> dict[tuple[str, str], float]:
    """선택요금 두 개의 전력량요금 차이. ``(계절, 시간대) → 차이(원)``."""
    contract = table.contract(contract_type)
    options = contract.voltages[voltage].options
    low, high = options[lower], options[upper]
    return {
        (season, band): low.rate(season, band) - high.rate(season, band)
        for season in sorted(low.energy)
        for band in BANDS
    }


def _ordered_options(contract: ContractType, voltage: VoltageRates) -> list[str]:
    return [option for option in contract.options if option in voltage.options]


def _comparable_pairs(contract: ContractType, voltage: VoltageRates) -> list[tuple[str, str]]:
    """견줄 수 있는 선택요금 쌍. **갈래가 다르면 번호가 잇달아도 안 견준다.**

    갑Ⅱ 는 선택Ⅰ·Ⅱ 가 시간대별이고 선택Ⅲ·Ⅳ 가 전체시간이다. 번호순으로
    Ⅱ→Ⅲ 를 견주면 「기본요금은 오름차순」(8,230 → 7,170)과 「전력량요금은
    내림차순」(84.1 → 142.6)이 둘 다 깨진다 — **깨진 것은 요금표가 아니라
    견주는 방법이다.** 갈래 안에서 Ⅰ→Ⅱ 와 Ⅲ→Ⅳ 만 본다.
    """
    ordered = _ordered_options(contract, voltage)
    pairs: list[tuple[str, str]] = []
    for time_of_use in (True, False):
        family = [
            option for option in ordered if voltage.options[option].time_of_use is time_of_use
        ]
        pairs.extend(pairwise(family))
    return pairs


def _check_order(table: TariffTable) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for contract_key, contract in table.contract_types.items():
        for voltage_key, voltage in contract.voltages.items():
            ordered = _ordered_options(contract, voltage)
            target = f"{contract_key}/{voltage_key}"

            for option in ordered:
                rates = voltage.options[option]
                for season, energy in rates.energy.items():
                    if rates.time_of_use:
                        if not energy.light < energy.mid < energy.peak:
                            findings.append(
                                ValidationFinding(
                                    "규칙 1",
                                    f"{target}/{option}/{season}",
                                    f"경부하 < 중간부하 < 최대부하 가 깨졌습니다: "
                                    f"{energy.light}, {energy.mid}, {energy.peak}",
                                )
                            )
                    # 전체시간 종별은 시간대 구분이 없다. 세 칸이 같아야 정상이다.
                    elif not energy.light == energy.mid == energy.peak:
                        findings.append(
                            ValidationFinding(
                                "규칙 1",
                                f"{target}/{option}/{season}",
                                f"전체시간 종별인데 시간대별 단가가 다릅니다: "
                                f"{energy.light}, {energy.mid}, {energy.peak}",
                            )
                        )

            for lower, upper in _comparable_pairs(contract, voltage):
                low, high = voltage.options[lower], voltage.options[upper]
                if not low.base_won_per_kw < high.base_won_per_kw:
                    findings.append(
                        ValidationFinding(
                            "규칙 1",
                            f"{target}/{lower}→{upper}",
                            f"기본요금은 선택{lower} < 선택{upper} 여야 합니다: "
                            f"{low.base_won_per_kw} → {high.base_won_per_kw}",
                        )
                    )
                for season in sorted(low.energy):
                    for band in BANDS:
                        if not low.rate(season, band) > high.rate(season, band):
                            findings.append(
                                ValidationFinding(
                                    "규칙 1",
                                    f"{target}/{lower}→{upper}/{season}/{band}",
                                    f"전력량요금은 선택{lower} > 선택{upper} 여야 합니다: "
                                    f"{low.rate(season, band)} → {high.rate(season, band)}",
                                )
                            )

            for option in ordered:
                rates = voltage.options[option]
                if "summer" in rates.energy and "spring_fall" in rates.energy:
                    summer = rates.energy["summer"].peak
                    spring_fall = rates.energy["spring_fall"].peak
                    if not summer > spring_fall:
                        findings.append(
                            ValidationFinding(
                                "규칙 1",
                                f"{target}/{option}",
                                f"여름철 최대부하 > 봄·가을철 최대부하 가 깨졌습니다: "
                                f"{summer} vs {spring_fall}",
                            )
                        )
    return findings


def _check_option_pairs(
    table: TariffTable, policy: Mapping[tuple[str, str, str, str], OptionPairPolicy]
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for contract_key, contract in table.contract_types.items():
        for voltage_key, voltage in contract.voltages.items():
            for lower, upper in _comparable_pairs(contract, voltage):
                # **정책도 선택요금의 갈래로 고른다** — 갑Ⅱ 안에 시간대별 쌍과
                # 전체시간 쌍이 함께 산다.
                fallback = (
                    OptionPairPolicy() if voltage.options[lower].time_of_use else FLAT_RATE_POLICY
                )
                key = (contract_key, voltage_key, lower, upper)
                rule = policy.get(key, fallback)
                target = f"{contract_key}/{voltage_key}/{lower}→{upper}"
                diffs = option_pair_diffs(table, contract_key, voltage_key, lower, upper)
                values = list(diffs.values())
                mean_diff = sum(values) / len(values)
                base_diff = (
                    voltage.options[upper].base_won_per_kw - voltage.options[lower].base_won_per_kw
                )

                if rule.uniform_tolerance is not None:
                    spread = max(abs(value - mean_diff) for value in values)
                    if spread > rule.uniform_tolerance:
                        findings.append(
                            ValidationFinding(
                                "규칙 2",
                                target,
                                f"단가 차이가 균일하지 않습니다. 평균 {mean_diff:.2f} 원, "
                                f"최대 편차 {spread:.2f} 원 > 허용 {rule.uniform_tolerance:.2f} 원",
                            )
                        )

                if rule.peak_focused:
                    # 최대부하에 할인이 집중된 설계다. 균일할 이유가 없다.
                    for season in sorted(voltage.options[lower].energy):
                        peak = diffs[(season, "peak")]
                        for band in ("light", "mid"):
                            other = diffs[(season, band)]
                            if not peak > other:
                                findings.append(
                                    ValidationFinding(
                                        "규칙 2",
                                        f"{target}/{season}",
                                        f"최대부하 할인폭({peak:.2f})이 "
                                        f"{band} 할인폭({other:.2f})보다 커야 합니다.",
                                    )
                                )

                if rule.breakeven_range is not None:
                    if mean_diff <= 0:
                        findings.append(
                            ValidationFinding(
                                "규칙 3",
                                target,
                                "전력량요금 차이가 0 이하라 손익분기를 낼 수 없습니다.",
                            )
                        )
                        continue
                    hours = base_diff / mean_diff
                    low, high = rule.breakeven_range
                    if not low <= hours <= high:
                        findings.append(
                            ValidationFinding(
                                "규칙 3",
                                target,
                                f"손익분기 이용시간 {hours:.0f} h/월 이 허용 범위 "
                                f"{low:.0f}~{high:.0f} h 를 벗어납니다 "
                                f"(기본요금 차 {base_diff:.0f} ÷ 전력량요금 차 {mean_diff:.2f}).",
                            )
                        )
    return findings


def _check_tou_completeness(table: TariffTable) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for region_group, seasons in table.tou.items():
        for season, bands in seasons.items():
            target = f"{region_group}/{season}"
            covered: dict[int, list[str]] = {}
            for band, ranges in bands.items():
                for start, end in ranges:
                    if not (0 <= start <= 24 and 0 <= end <= 24):
                        findings.append(
                            ValidationFinding(
                                "규칙 4",
                                target,
                                f"시간 구간이 0~24 를 벗어납니다: [{start}, {end}]",
                            )
                        )
                        continue
                    hours = _hours_in(start, end)
                    for hour in hours:
                        covered.setdefault(hour, []).append(band)

            duplicated = {hour: names for hour, names in covered.items() if len(names) > 1}
            if duplicated:
                findings.append(
                    ValidationFinding(
                        "규칙 4",
                        target,
                        "시간대가 겹칩니다: "
                        + ", ".join(
                            f"{hour}시={'/'.join(names)}"
                            for hour, names in sorted(duplicated.items())
                        ),
                    )
                )
            uncovered = sorted(set(range(24)) - set(covered))
            if uncovered:
                findings.append(
                    ValidationFinding(
                        "규칙 4", target, f"정의되지 않은 시각이 있습니다: {uncovered}"
                    )
                )
            total = sum(
                len(_hours_in(start, end)) for ranges in bands.values() for start, end in ranges
            )
            if total != 24:
                findings.append(
                    ValidationFinding("규칙 4", target, f"구간 합이 24시간이 아닙니다: {total}시간")
                )
    return findings


def _hours_in(start: int, end: int) -> tuple[int, ...]:
    if start == end:
        return ()
    if start < end:
        return tuple(range(start, end))
    return (*range(start, 24), *range(0, end))


def validate_tariff(
    table: TariffTable,
    *,
    policy: Mapping[tuple[str, str, str, str], OptionPairPolicy] | None = None,
) -> tuple[ValidationFinding, ...]:
    """부록 A.2 의 네 규칙을 모두 돌린다. 빈 튜플이면 통과다."""
    rules = policy if policy is not None else DEFAULT_POLICY
    findings = [
        *_check_order(table),
        *_check_option_pairs(table, rules),
        *_check_tou_completeness(table),
    ]
    return tuple(findings)
