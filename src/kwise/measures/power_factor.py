"""역률 개선 (요구사항서 7.4) — 투자비가 작다 (역률 개선 설비·자동제어형 역률 개선 설비).

**역률 개선 설비 조정만으로 기본요금이 줄어든다.** 약관 제43조 ②는 주간(08~22시)
지상역률이 기준 92%를 넘으면 매 1%당 기본요금의 0.2%를 감액한다. 감액 상한인
97%까지 올리면 **기본요금의 1.0%** 다. 기본요금이 연 4.5억 원인 샘플에서
450만 원이고, 진상 보상 설비 증설비는 보통 그보다 훨씬 작다.

거꾸로 92% 미만이면 매 1%당 0.2%가 **추가**된다. 92%에서 60%까지 최대 6.4%다.
이쪽이 더 자주 문제가 되며, 태양광 도입이 역률을 떨어뜨려 여기 걸리는 일이 잦다
(:func:`kwise.measures.solar.power_factor_after_pct`).

**야간 진상 위험이 이 수단의 유일한 부작용이다.** 제43조 ② 2호 나목에 따라
야간이 지상이면 역률이 100%로 간주되어 추가가 0이다. 즉 야간 진상 페널티는
**역률 개선 설비 과투자의 결과**로만 생긴다. 주간 97%를 맞추려 고정형 역률 개선 설비 한 벌를
키우면 부하가 줄어든 야간에 진상으로 넘어가 되돌려 주게 된다.
**자동제어형 역률 개선 설비** 는 부하에 따라 투입 단수를 조절해 이를 피한다.
설치비는 설비 구성에 따라 달라 본 도구가 만들지 않는다 — 사용자 입력이다.

**확실성 등급은 '높음'이다.** 요금표와 약관만으로 확정되는 계산이고, 발전량
예측 같은 불확실성이 없다. 감도(9.2)도 적용하지 않는다 — PV 출력에만 붙는다.

**남는 불확실성은 역률 자체다.** 약관 제42조는 30분 누적 계량을 요구하는데 우리
데이터는 15분이고 무효전력이 없다. 추정 역률의 기본값 92%는 제42조가 무효전력계
미설치 고객에게 적용하는 간주값이라 근거가 있고, 그 값에서 조정액이 정확히 0이
되어 모르는 채로 금액을 만들어내지 않는다. 실측 역률을 넣으면 바로 재계산된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from kwise.io import UsageData
from kwise.measures.base import Certainty, annualize, payback_years
from kwise.notices import Notice, basis, info, warn
from kwise.quality import QualityReport
from kwise.tariff import (
    BillingOptions,
    BillingResult,
    TariffSelection,
    TariffTable,
    calculate_bill,
    lagging_rebate_cap_pct,
    lagging_standard_pct,
    leading_floor_pct,
    leading_standard_pct,
)

__all__ = [
    "PowerFactorResult",
    "default_target_pct",
    "evaluate_power_factor",
]


def default_target_pct() -> float:
    """역률 개선의 기본 목표 — 감액 상한(97%)이다.

    이보다 올려도 요금은 더 내려가지 않는다 (제43조 ②). 값은
    ``data\rules_kr.json`` 에 있다.
    """
    return lagging_rebate_cap_pct()


@dataclass(frozen=True, eq=False)
class PowerFactorResult:
    """역률 개선 평가.

    Attributes:
        current_pct / target_pct: 개선 전후 주간 지상역률.
        saving_won: 요금을 다시 계산해 얻은 절감액. 빼기로 어림하지 않는다.
        current_charge_won: 현재 역률요금. 양수면 추가, 음수면 감액이다.
    """

    current_pct: float
    target_pct: float
    current_charge_won: float
    target_charge_won: float
    saving_won: float
    annual_saving_won: float
    investment_won: float
    payback_years: float | None
    base_fee_months: float
    period_label: str
    current_bill: BillingResult
    target_bill: BillingResult
    certainty: Certainty = Certainty.HIGH
    notices: tuple[Notice, ...] = field(default=())

    @property
    def improvement_pct(self) -> float:
        """올리는 역률 폭 (%p)."""
        return self.target_pct - self.current_pct

    @property
    def is_penalty_removal(self) -> bool:
        """추가요금을 없애는 쪽인가. 감액을 받는 쪽보다 금액이 크다."""
        return self.current_charge_won > 0


def evaluate_power_factor(
    usage: UsageData,
    table: TariffTable,
    selection: TariffSelection,
    *,
    current_pct: float | None = None,
    target_pct: float | None = None,
    investment_won: float = 0.0,
    baseline: BillingResult | None = None,
    quality: QualityReport | None = None,
    options: BillingOptions | None = None,
) -> PowerFactorResult:
    """역률 개선 설비 조정으로 역률을 올렸을 때의 절감액을 **재계산해서** 낸다.

    Args:
        current_pct: 현재 주간(08~22시) 지상역률. 기본값 92% 는 약관 제42조의
            무효전력계 미설치 간주값이며, 이 값에서는 추가·감액이 0 이다.
        target_pct: 목표 역률. 기본값 97% 는 감액 상한이다 — 더 올려도 요금은
            내려가지 않으므로 과보상만 남는다.
        investment_won: 역률 개선 설비 증설·조정 투자비. 사용자 입력이며 기본값이 없다시피
            0 이다 — 0 이면 회수기간을 '즉시' 로 본다.
    """
    # 기본값은 파일에서 온다 (요구사항서 12장). 코드에 두지 않는다.
    standard = lagging_standard_pct()
    cap = lagging_rebate_cap_pct()
    current_pct = standard if current_pct is None else current_pct
    target_pct = default_target_pct() if target_pct is None else target_pct
    if target_pct < current_pct:
        raise ValueError(
            f"목표 역률({target_pct}%)이 현재({current_pct}%)보다 낮습니다. "
            "역률 개선 수단은 올리는 방향만 다룹니다."
        )
    opts = options if options is not None else BillingOptions()
    current_options = replace(opts, power_factor_pct=current_pct)
    target_options = replace(opts, power_factor_pct=target_pct)

    # 조합마다 요금을 다시 계산한다. 절감액을 빼기로 어림하지 않는다.
    current_bill = (
        baseline
        if baseline is not None and baseline.power_factor.lagging_pct == current_pct
        else calculate_bill(usage, table, selection, options=current_options, quality=quality)
    )
    target_bill = calculate_bill(usage, table, selection, options=target_options, quality=quality)

    saving = current_bill.total_won - target_bill.total_won
    annual = annualize(saving, current_bill.base_fee_months)

    effective_target = min(target_pct, cap)
    notices: list[Notice] = []
    if target_pct > cap:
        notices.append(
            warn(
                f"목표 역률 {target_pct:.1f}% 가 감액 상한 {cap:.0f}% 를 "
                f"넘습니다. {effective_target:.0f}% 를 넘는 만큼은 요금이 더 줄지 않으므로 "
                "역률 개선 설비 과투자입니다.",
                fact="power_factor.target_over_cap",
            )
        )
    if current_pct < standard:
        notices.append(
            warn(
                f"현재 역률 {current_pct:.1f}% 는 약관 제41조의 유지 의무(지상 92% 이상)에 "
                "미달합니다. 절감이 아니라 이미 나가고 있는 추가요금을 없애는 것입니다.",
                fact="power_factor.duty_unmet",
            )
        )
    # **주의** — 추정값이라는 사실은 금액을 그대로 믿으면 안 된다는 뜻이다.
    notices.append(
        warn(
            "무효전력 실측이 없어 현재 역률은 추정값입니다 (약관 제42조는 30분 누적 "
            "계량을 요구합니다). 청구서의 역률 항목을 확인하면 current_pct 로 넣어 "
            "바로 재계산됩니다.",
            fact="power_factor.estimated_only",
        )
    )
    notices += [
        # **근거** — 어느 기준·어느 창에서 나온 값인가.
        basis(
            f"주간(08~22시) 지상역률 {current_pct:.1f}% → {target_pct:.1f}% 기준입니다. "
            f"기준 {standard:.0f}%, 매 1%당 기본요금의 0.2% "
            "(기본공급약관 제43조 ②).",
            fact="power_factor.standard_window",
        ),
        basis(
            "요금을 두 역률에서 각각 다시 계산했습니다. 기본요금 비율만 곱해 어림하지 않았습니다.",
            fact="power_factor.recalculated",
        ),
        # **참고** — 설비 선택과 제도 설명. 숫자를 만들지 않는다.
        info(
            "역률 개선은 요금표와 약관만으로 확정되는 계산입니다. 감도를 적용하지 "
            "않습니다 (요구사항서 9.2).",
            fact="power_factor.no_sensitivity",
        ),
        info(
            "**고정형 역률 개선 설비를 키우면 야간 경부하에서 진상으로 넘어갑니다.** 주간 부하에 "
            f"맞춘 용량이 야간에는 과다해지기 때문입니다. 야간(22~08시) 진상역률 기준은 "
            f"{leading_standard_pct():.0f}% 이며 미달 시 매 1%당 기본요금의 0.2% 가 "
            f"추가됩니다 (하한 {leading_floor_pct():.0f}%, 제43조 ② 2호). "
            "지상으로 유지되는 한 야간 역률은 100% 로 간주되어 추가가 0 이므로 "
            "(같은 조 나목), 이 추가요금은 곧 **역률 개선 설비 과투자의 신호**입니다.",
            fact="power_factor.leading_overshoot",
        ),
        info(
            "**자동제어형 역률 개선 설비를 쓰면 부하에 따라 투입 단수가 조절되어 야간 "
            "진상을 피할 수 있습니다.** 고정형 역률 개선 설비 한 벌로 주간 97% 를 맞추면 "
            "야간에 되돌려 주게 됩니다. 설치비는 설비 구성에 따라 달라 본 도구가 "
            "산출하지 않습니다 — investment_won 으로 넣으면 회수기간이 재계산됩니다.",
            fact="power_factor.auto_control",
        ),
    ]
    return PowerFactorResult(
        current_pct=current_pct,
        target_pct=target_pct,
        current_charge_won=current_bill.total_power_factor_won,
        target_charge_won=target_bill.total_power_factor_won,
        saving_won=saving,
        annual_saving_won=annual,
        investment_won=investment_won,
        payback_years=payback_years(investment_won, annual),
        base_fee_months=current_bill.base_fee_months,
        period_label=current_bill.period_label,
        current_bill=current_bill,
        target_bill=target_bill,
        notices=tuple(notices),
    )
