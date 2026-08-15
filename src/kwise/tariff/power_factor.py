"""역률요금 (요구사항서 5.7, 한전 기본공급약관 제41·42·43조).

**추가와 감액을 모두 계산한다.** 금액이 기본요금의 −1.0% ~ +6.4% 라 경고로만
둘 수 없다. 기본요금이 연 4.5억 원인 샘플에서 1%는 450만 원이다.

원문 요약 (``data\\source\\기본공급약관.pdf``)

    제41조   고객은 전체 사용설비의 역률을 **지상역률 92% 이상**으로 유지한다.
    제42조   역률은 **30분 단위 누적 계량값**으로 계산한다.
             무효전력계 미설치 고객은 **지상역률 92%로 본다.**
    제43조 ① 대상은 무효전력계가 설치된 고압 이상 일반용·교육용·산업용 등.
             — 우리 대상 종별 전부가 여기 든다.
           ② 1호 08~22시  **지상역률 기준 92%**
                          미달 시 매 1%당 기본요금의 0.2% **추가** (역률 60%까지)
                          초과 시 매 1%당 기본요금의 0.2% **감액** (역률 97%까지)
              2호 22~08시  **진상역률 기준 95%**
                          미달 시 매 1%당 기본요금의 0.2% 추가
                 **나목**  30분 단위 역률이 진상 60% 미달이면 60%로,
                          **지상이면 100%로 간주**하여 1개월 평균역률을 계산한다.
           ③ 추가요금이 발생하는 첫 달은 **예고**, 두 번째 달부터 청구.

**야간 진상 페널티는 '역률 개선 설비 과투자의 결과'다.**

제43조 ② 2호 나목이 핵심이다. 야간에 **지상이면 역률을 100%로 간주**하므로
기준 95%를 넘어 추가요금이 0이 된다. 대부분의 건물은 야간 경부하에서 지상이고,
진상은 **고정형 역률 개선 설비가 부하 대비 과다할 때** 생긴다. 그래서 이 조항은
"야간에도 역률을 관리하라"가 아니라 "주간 역률을 맞추려 역률 개선 설비를 키우면
야간에 되돌려 받는다"는 뜻으로 읽어야 한다 — 역률 개선 수단(7.4)의 경고다.

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

from kwise.notices import Notice, basis, info, warn
from kwise.rules import rule_value

__all__ = [
    "PowerFactorCharge",
    "adjustment_per_percent",
    "day_window",
    "deemed_lagging_pct",
    "deemed_leading_pct",
    "lagging_adjustment_ratio",
    "lagging_floor_pct",
    "lagging_rebate_cap_pct",
    "lagging_standard_pct",
    "leading_adjustment_ratio",
    "leading_floor_pct",
    "leading_lagging_deemed_pct",
    "leading_standard_pct",
    "power_factor_charge",
]

# 값은 모두 ``data\rules_kr.json`` 에 있다 (요구사항서 12장).
# **모듈 상수로 붙잡지 않는다** — import 시점에 고정하면 파일을 고쳐도 그
# 프로세스에서는 옛 값으로 계산된다. 코드에 기본값을 남기는 것과 같은 사고다.


def adjustment_per_percent() -> float:
    """역률 1%p 당 기본요금 조정률 (제43조 ②)."""
    return float(rule_value("power_factor.adjustment_per_percent"))


def day_window() -> tuple[int, int]:
    """지상역률 판정 창 (구간 시작 시각 기준)."""
    start, end = rule_value("power_factor.day_window")
    return (int(start), int(end))


def lagging_standard_pct() -> float:
    """역률요금의 **기준** (제41조). 이 값을 넘으면 감액, 못 미치면 추가다."""
    return float(rule_value("power_factor.lagging_standard_pct"))


def deemed_lagging_pct() -> float:
    """무효전력계가 없을 때의 **간주** 지상역률 (제42조).

    기준(:func:`lagging_standard_pct`)과 우연히 같은 92% 지만 **근거 조문이
    다르다.** 한 값으로 묶어 두면, 기준만 개정됐을 때 "모르는 고객" 의 역률까지
    따라 움직여 조정액이 0 이 아니게 된다 — 실측하지 않은 값으로 금액이 생긴다.
    """
    return float(rule_value("power_factor.deemed_lagging_pct"))


def lagging_floor_pct() -> float:
    """이보다 낮아도 이 값으로 본다 → 추가 상한 6.4%."""
    return float(rule_value("power_factor.lagging_floor_pct"))


def lagging_rebate_cap_pct() -> float:
    """이보다 높아도 이 값으로 본다 → 감액 상한 1.0%."""
    return float(rule_value("power_factor.lagging_rebate_cap_pct"))


def leading_standard_pct() -> float:
    return float(rule_value("power_factor.leading_standard_pct"))


def leading_floor_pct() -> float:
    """제43조 ② 2호 나목 — 추정이 아니라 원문에 명시된 하한이다."""
    return float(rule_value("power_factor.leading_floor_pct"))


def leading_lagging_deemed_pct() -> float:
    """같은 나목 — 야간에 **지상**이면 이 역률로 간주한다. 추가요금이 0이 된다."""
    return float(rule_value("power_factor.leading_lagging_deemed_pct"))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def lagging_adjustment_ratio(
    power_factor_pct: float,
    *,
    standard_pct: float | None = None,
    floor_pct: float | None = None,
    rebate_cap_pct: float | None = None,
    per_percent: float | None = None,
) -> float:
    """주간(08~22시) 지상역률의 기본요금 조정 비율 (제43조 ②).

    Returns:
        기본요금에 곱할 비율. **양수가 추가, 음수가 감액이다.**
        92% 에서 0, 60% 에서 +0.064, 97% 에서 −0.010 이 된다.
    """
    if not 0 < power_factor_pct <= 100:
        raise ValueError(f"역률은 0~100% 여야 합니다: {power_factor_pct}")
    standard = lagging_standard_pct() if standard_pct is None else standard_pct
    floor = lagging_floor_pct() if floor_pct is None else floor_pct
    cap = lagging_rebate_cap_pct() if rebate_cap_pct is None else rebate_cap_pct
    rate = adjustment_per_percent() if per_percent is None else per_percent
    effective = _clamp(power_factor_pct, floor, cap)
    return (standard - effective) * rate


def deemed_leading_pct(
    power_factor_pct: float | None,
    *,
    is_leading: bool = True,
    floor_pct: float | None = None,
) -> float:
    """제43조 ② 2호 **나목**의 간주값을 적용한 야간 역률.

    ``is_leading`` 이 거짓이면(= 야간이 지상이면) **100% 로 간주**한다.
    진상이면서 하한 미만이면 하한(60%)으로 올린다. 알 수 없으면 지상으로 본다 —
    대부분의 건물이 야간 경부하에서 지상이고, 진상은 역률 개선 설비 과다의 결과다.
    """
    if power_factor_pct is None or not is_leading:
        return leading_lagging_deemed_pct()
    if not 0 < power_factor_pct <= 100:
        raise ValueError(f"역률은 0~100% 여야 합니다: {power_factor_pct}")
    return max(power_factor_pct, leading_floor_pct() if floor_pct is None else floor_pct)


def leading_adjustment_ratio(
    power_factor_pct: float,
    *,
    standard_pct: float | None = None,
    floor_pct: float | None = None,
    per_percent: float | None = None,
) -> float:
    """야간(22~08시) 진상역률의 기본요금 조정 비율 (제43조 ② 2호).

    **추가만 있고 감액은 없다.** 기준 95% 를 넘겨도 0 이다.

    하한 60% 는 나목에 명시된 간주값이다. **지상인 야간은 100% 로 간주되므로
    이 함수에 들어오지 않거나 0 을 돌려준다** — :func:`deemed_leading_pct` 참조.
    """
    if not 0 < power_factor_pct <= 100:
        raise ValueError(f"역률은 0~100% 여야 합니다: {power_factor_pct}")
    standard = leading_standard_pct() if standard_pct is None else standard_pct
    floor = leading_floor_pct() if floor_pct is None else floor_pct
    rate = adjustment_per_percent() if per_percent is None else per_percent
    effective = _clamp(power_factor_pct, floor, standard)
    return (standard - effective) * rate


@dataclass(frozen=True)
class PowerFactorCharge:
    """역률요금 산출 결과. 추가는 양수, 감액은 음수다.

    Attributes:
        lagging_pct: 주간 지상역률 (추정).
        leading_pct: 야간 **진상**역률. None 이면 지상으로 본다.
        leading_deemed_pct: 나목의 간주값을 적용한 야간 역률. 지상이면 100 이다.
        total_won: ``lagging_won + leading_won``. 기본요금에 더할 금액이다.
    """

    base_won: float
    lagging_pct: float
    leading_pct: float | None
    leading_deemed_pct: float
    lagging_ratio: float
    leading_ratio: float
    lagging_won: float
    leading_won: float
    notices: tuple[Notice, ...] = field(default=())

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
    lagging_pct: float | None = None,
    leading_pct: float | None = None,
) -> PowerFactorCharge:
    """기본요금에 대한 역률 추가·감액을 낸다 (제43조).

    Args:
        base_won: 대상 기본요금. 부분 월 계수가 이미 곱해진 값을 넘긴다.
        lagging_pct: 주간 지상역률. None 이면 제42조의 간주값(무효전력계 미설치)을
            쓴다. 그 값에서 추가·감액이 0 원이다.
        leading_pct: 야간 **진상**역률. None 이면 제43조 ② 2호 나목에 따라
            지상으로 보아 100% 로 간주하고 추가를 0 으로 둔다. 진상은 고정
            역률 개선 설비가 부하 대비 과다할 때 생기므로, 그 사실을 경고로 남긴다.
    """
    standard = lagging_standard_pct()
    floor = lagging_floor_pct()
    # **기준이 아니라 간주값으로 채운다** (제42조). 둘은 오늘 같은 92% 지만
    # 조문이 다르므로, 기준이 개정돼도 모르는 고객의 역률은 따라가지 않는다.
    lagging_pct = deemed_lagging_pct() if lagging_pct is None else lagging_pct
    lagging_ratio = lagging_adjustment_ratio(lagging_pct)
    notices: list[Notice] = [
        # 제도 설명은 **참고**, 계산에서 뺀 규칙은 **근거**다.
        info(
            "역률요금은 기본요금에 대한 추가·감액입니다 (한전 기본공급약관 제43조). "
            "주간(08~22시) 지상 92%, 야간(22~08시) 진상 95% 가 기준이며 "
            "매 1%당 0.2% 입니다.",
            fact="power_factor.rule",
        ),
        basis(
            "추가요금이 발생하는 첫 달은 약관상 예고이고 청구는 두 번째 달부터입니다 "
            "(한전 기본공급약관 제43조 ③). 기간에 따라 결과가 흔들리지 않도록 이 규칙은 "
            "계산에 넣지 않았습니다 — 실제 첫 달 청구서와 어긋날 수 있습니다.",
            fact="power_factor.first_month_notice",
        ),
    ]

    if lagging_pct <= standard:
        notices.append(
            basis(
                f"주간 지상역률 {lagging_pct:.1f}% — 기준 92% 대비 "
                f"{max(0.0, standard - lagging_pct):.1f}%p 미달, "
                f"기본요금의 {lagging_ratio:+.1%} 추가.",
                fact="power_factor.lagging_ratio",
            )
        )
    else:
        notices.append(
            basis(
                f"주간 지상역률 {lagging_pct:.1f}% — 기준 92% 초과, "
                f"기본요금의 {lagging_ratio:+.1%} 감액 (97% 초과분은 인정되지 않습니다).",
                fact="power_factor.lagging_ratio",
            )
        )
    if lagging_pct < floor:
        notices.append(
            warn(
                f"주간 지상역률 {lagging_pct:.1f}% 가 {floor:.0f}% 미만입니다. "
                "약관상 추가요금은 60% 까지만 계산되므로 실제 부담이 더 클 수 있고, "
                "역률 유지 의무(한전 기본공급약관 제41조) 위반으로 별도 조치 대상이 "
                "될 수 있습니다.",
                fact="power_factor.lagging_below_floor",
            )
        )
    if lagging_pct < standard:
        notices.append(
            warn(
                f"주간 지상역률이 기준 92% 에 미달합니다 ({lagging_pct:.1f}%). "
                f"기본요금의 {lagging_ratio:.1%} 가 추가됩니다. 역률 개선 설비 용량 조정을 "
                "검토하십시오 (한전 기본공급약관 제41·43조).",
                fact="power_factor.lagging_below_standard",
            )
        )

    # 제43조 ② 2호 나목 — 지상인 야간은 100% 로 간주되어 추가가 0 이다.
    deemed = deemed_leading_pct(leading_pct)
    leading_ratio = leading_adjustment_ratio(deemed)
    if leading_pct is None:
        notices.append(
            basis(
                "야간(22~08시)은 **지상으로 보아 역률 100% 간주**, 진상 추가요금 0원입니다 "
                "(한전 기본공급약관 제43조 ② 2호 나목). 야간 경부하에서 지상인 것이 보통이며, "
                "진상은 고정형 역률 개선 설비가 부하 대비 과다할 때 생깁니다.",
                fact="power_factor.leading_deemed",
            )
        )
        # **참고** — 확인하지 않았다는 사실이지 이 자료의 결과를 바꾸지 않는다
        # (지상이면 추가가 0 이다). 18세션 인벤토리에서도 참고로 잡혔다.
        notices.append(
            info(
                "야간 진상 여부를 확인하지 않았습니다. 고정형 역률 개선 설비를 크게 두었거나 "
                "역률 개선으로 키울 예정이라면 야간 경부하에서 진상으로 넘어갈 수 "
                "있습니다. 기준 95% 미달 시 매 1%당 기본요금의 0.2% 가 추가되며, "
                "하한은 60% 입니다 (한전 기본공급약관 제43조 ② 2호).",
                fact="power_factor.leading_unchecked",
            )
        )
    elif leading_ratio > 0:
        notices.append(
            warn(
                f"야간 진상역률 {leading_pct:.1f}% 가 기준 95% 에 미달합니다"
                + (f" (하한 {leading_floor_pct():.0f}% 적용)" if deemed != leading_pct else "")
                + f". 기본요금의 {leading_ratio:.1%} 가 추가됩니다. "
                "**역률 개선 설비 과보상입니다** — 부하가 줄어든 야간에 진상 무효전력이 남는 "
                "것이므로 고정형 역률 개선 설비 용량을 줄이거나 자동제어형 역률 개선 설비로 "
                "시간대별 투입 단수를 조절하십시오.",
                fact="power_factor.leading_below_standard",
            )
        )
    else:
        notices.append(
            basis(
                f"야간 진상역률 {leading_pct:.1f}% — 기준 95% 이상이라 추가 없음.",
                fact="power_factor.leading_ok",
            )
        )

    return PowerFactorCharge(
        base_won=base_won,
        lagging_pct=lagging_pct,
        leading_pct=leading_pct,
        leading_deemed_pct=deemed,
        lagging_ratio=lagging_ratio,
        leading_ratio=leading_ratio,
        lagging_won=base_won * lagging_ratio,
        leading_won=base_won * leading_ratio,
        notices=tuple(notices),
    )
