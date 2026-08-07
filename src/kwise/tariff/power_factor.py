"""역률요금 (요구사항서 5.7, 한전 기본공급약관 제41·42·43조).

**추가와 감액을 모두 계산한다.** 금액이 기본요금의 −1.0% ~ +6.4% 라 경고로만
둘 수 없다. 기본요금이 연 4.5억 원인 샘플에서 1%는 450만 원이다.

원문 요약 (``data\\source\\기본공급약관.pdf``)

    제41조   고객은 전체 사용설비의 역률을 **지상역률 92% 이상**으로 유지한다.
    제42조   역률은 **30분 단위 누적 계량값**으로 계산한다.
             무효전력계 미설치 고객은 **지상역률 92%로 본다.**
    제43조 ① 대상은 무효전력계가 설치된 고압 이상 일반용·교육용·산업용 등.
             — 우리 대상 종별 전부가 여기 든다.
           ② 08~22시  **지상역률 기준 92%**
                       미달 시 매 1%당 기본요금의 0.2% **추가** (역률 60%까지)
                       초과 시 매 1%당 기본요금의 0.2% **감액** (역률 97%까지)
              22~08시  **진상역률 기준 95%**
                       미달 시 매 1%당 기본요금의 0.2% 추가
           ③ 추가요금이 발생하는 첫 달은 **예고**, 두 번째 달부터 청구.

**③은 계산에 넣지 않는다.** 첫 달을 빼면 기간에 따라 금액이 달라져 도입 전후
비교(Δ)가 흔들린다. 12개월 기준의 정상 상태를 보는 것이 이 도구의 목적이므로
주석으로만 남긴다. 실제 청구서와 첫 달분이 어긋날 수 있다.

**우리 데이터의 한계.** 15분 계량이고 무효전력이 없다. 제42조의 30분 누적 계량을
그대로 재현할 수 없으므로 추정 역률을 쓴다. 기본값 92%는 지어낸 값이 아니라
**제42조가 무효전력계 미설치 고객에게 적용하는 간주값**이다. 이 값에서는
추가·감액이 정확히 0 이 되어 역률을 모르는 채로 금액을 만들어내지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "ADJUSTMENT_PER_PERCENT",
    "DAY_WINDOW",
    "LAGGING_FLOOR_PCT",
    "LAGGING_REBATE_CAP_PCT",
    "LAGGING_STANDARD_PCT",
    "LEADING_FLOOR_PCT",
    "LEADING_STANDARD_PCT",
    "PowerFactorCharge",
    "lagging_adjustment_ratio",
    "leading_adjustment_ratio",
    "power_factor_charge",
]

# 제43조 ② — 매 1%당 기본요금의 0.2%
ADJUSTMENT_PER_PERCENT = 0.002

# 08~22시 지상역률 (구간 시작 시각 기준)
DAY_WINDOW: tuple[int, int] = (8, 22)
LAGGING_STANDARD_PCT = 92.0
LAGGING_FLOOR_PCT = 60.0  # 이보다 낮아도 60%로 본다 → 추가 상한 6.4%
LAGGING_REBATE_CAP_PCT = 97.0  # 이보다 높아도 97%로 본다 → 감액 상한 1.0%

# 22~08시 진상역률
LEADING_STANDARD_PCT = 95.0
# 원문에 야간 하한이 명시되지 않아 주간과 같은 60%를 적용한다. 값이 터무니없이
# 커지는 것을 막기 위한 보수적 처리이며, 약관 재확인 시 바꿀 수 있게 인자로 뺐다.
LEADING_FLOOR_PCT = 60.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def lagging_adjustment_ratio(
    power_factor_pct: float,
    *,
    standard_pct: float = LAGGING_STANDARD_PCT,
    floor_pct: float = LAGGING_FLOOR_PCT,
    rebate_cap_pct: float = LAGGING_REBATE_CAP_PCT,
    per_percent: float = ADJUSTMENT_PER_PERCENT,
) -> float:
    """주간(08~22시) 지상역률의 기본요금 조정 비율 (제43조 ②).

    Returns:
        기본요금에 곱할 비율. **양수가 추가, 음수가 감액이다.**
        92% 에서 0, 60% 에서 +0.064, 97% 에서 −0.010 이 된다.
    """
    if not 0 < power_factor_pct <= 100:
        raise ValueError(f"역률은 0~100% 여야 합니다: {power_factor_pct}")
    effective = _clamp(power_factor_pct, floor_pct, rebate_cap_pct)
    return (standard_pct - effective) * per_percent


def leading_adjustment_ratio(
    power_factor_pct: float,
    *,
    standard_pct: float = LEADING_STANDARD_PCT,
    floor_pct: float = LEADING_FLOOR_PCT,
    per_percent: float = ADJUSTMENT_PER_PERCENT,
) -> float:
    """야간(22~08시) 진상역률의 기본요금 조정 비율 (제43조 ②).

    **추가만 있고 감액은 없다.** 기준 95% 를 넘겨도 0 이다.

    ESS 야간 충전과 PV 인버터의 진상 무효전력이 여기 걸린다. 낮에 좋아 보이던
    설비가 밤에 요금을 늘리는 경로다.
    """
    if not 0 < power_factor_pct <= 100:
        raise ValueError(f"역률은 0~100% 여야 합니다: {power_factor_pct}")
    effective = _clamp(power_factor_pct, floor_pct, standard_pct)
    return (standard_pct - effective) * per_percent


@dataclass(frozen=True)
class PowerFactorCharge:
    """역률요금 산출 결과. 추가는 양수, 감액은 음수다.

    Attributes:
        lagging_pct: 주간 지상역률 (추정).
        leading_pct: 야간 진상역률. 모르면 None — 추가를 산출하지 않고 경고한다.
        total_won: ``lagging_won + leading_won``. 기본요금에 더할 금액이다.
    """

    base_won: float
    lagging_pct: float
    leading_pct: float | None
    lagging_ratio: float
    leading_ratio: float
    lagging_won: float
    leading_won: float
    warnings: tuple[str, ...] = field(default=())
    notes: tuple[str, ...] = field(default=())

    @property
    def total_ratio(self) -> float:
        return self.lagging_ratio + self.leading_ratio

    @property
    def total_won(self) -> float:
        return self.lagging_won + self.leading_won

    @property
    def is_rebate(self) -> bool:
        """감액인가. 92% 를 넘겨 돈을 돌려받는 상태다."""
        return self.total_won < 0


def power_factor_charge(
    base_won: float,
    *,
    lagging_pct: float = LAGGING_STANDARD_PCT,
    leading_pct: float | None = None,
) -> PowerFactorCharge:
    """기본요금에 대한 역률 추가·감액을 낸다 (제43조).

    Args:
        base_won: 대상 기본요금. 부분 월 계수가 이미 곱해진 값을 넘긴다.
        lagging_pct: 주간 지상역률. 기본값 92% 는 제42조의 간주값이라 조정이 0 이다.
        leading_pct: 야간 진상역률. None 이면 산출하지 않고 경고만 낸다 —
            무효전력 실측 없이 진상역률을 지어낼 근거가 없다.
    """
    lagging_ratio = lagging_adjustment_ratio(lagging_pct)
    warnings: list[str] = []
    notes: list[str] = [
        "역률요금은 기본요금에 대한 추가·감액입니다 (기본공급약관 제43조). "
        "주간(08~22시) 지상 92%, 야간(22~08시) 진상 95% 가 기준이며 "
        "매 1%당 0.2% 입니다.",
        "추가요금이 발생하는 첫 달은 약관상 예고이고 청구는 두 번째 달부터입니다 "
        "(제43조 ③). 기간에 따라 결과가 흔들리지 않도록 이 규칙은 계산에 넣지 "
        "않았습니다 — 실제 첫 달 청구서와 어긋날 수 있습니다.",
    ]

    if lagging_pct <= LAGGING_STANDARD_PCT:
        notes.append(
            f"주간 지상역률 {lagging_pct:.1f}% — 기준 92% 대비 "
            f"{max(0.0, LAGGING_STANDARD_PCT - lagging_pct):.1f}%p 미달, "
            f"기본요금의 {lagging_ratio:+.1%} 추가."
        )
    else:
        notes.append(
            f"주간 지상역률 {lagging_pct:.1f}% — 기준 92% 초과, "
            f"기본요금의 {lagging_ratio:+.1%} 감액 (97% 초과분은 인정되지 않습니다)."
        )
    if lagging_pct < LAGGING_FLOOR_PCT:
        warnings.append(
            f"주간 지상역률 {lagging_pct:.1f}% 가 {LAGGING_FLOOR_PCT:.0f}% 미만입니다. "
            "약관상 추가요금은 60% 까지만 계산되므로 실제 부담이 더 클 수 있고, "
            "역률 유지 의무(제41조) 위반으로 별도 조치 대상이 될 수 있습니다."
        )
    if lagging_pct < LAGGING_STANDARD_PCT:
        warnings.append(
            f"주간 지상역률이 기준 92% 에 미달합니다 ({lagging_pct:.1f}%). "
            f"기본요금의 {lagging_ratio:.1%} 가 추가됩니다. 콘덴서 용량 조정을 "
            "검토하십시오 (기본공급약관 제41·43조)."
        )

    leading_ratio = 0.0
    if leading_pct is None:
        warnings.append(
            "야간(22~08시) 진상역률을 알 수 없어 추가요금을 산출하지 않았습니다. "
            "기준은 95% 이며 미달 시 매 1%당 기본요금의 0.2% 가 추가됩니다. "
            "ESS 야간 충전과 태양광 인버터의 진상 무효전력이 여기 걸립니다 "
            "(기본공급약관 제43조)."
        )
    else:
        leading_ratio = leading_adjustment_ratio(leading_pct)
        if leading_ratio > 0:
            warnings.append(
                f"야간 진상역률 {leading_pct:.1f}% 가 기준 95% 에 미달합니다. "
                f"기본요금의 {leading_ratio:.1%} 가 추가됩니다. 콘덴서 과보상 여부를 "
                "확인하십시오 — 야간에는 진상이 문제입니다."
            )
        else:
            notes.append(f"야간 진상역률 {leading_pct:.1f}% — 기준 95% 이상이라 추가 없음.")

    return PowerFactorCharge(
        base_won=base_won,
        lagging_pct=lagging_pct,
        leading_pct=leading_pct,
        lagging_ratio=lagging_ratio,
        leading_ratio=leading_ratio,
        lagging_won=base_won * lagging_ratio,
        leading_won=base_won * leading_ratio,
        warnings=tuple(warnings),
        notes=tuple(notes),
    )
