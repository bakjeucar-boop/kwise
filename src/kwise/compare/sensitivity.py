"""감도 비교 (요구사항서 9.2, 8장).

PV 출력에만 계수를 적용해 조합을 세 번 평가한다. **요금제 전환과 계약전력 조정에는
감도를 적용하지 않는다** — 실측과 요금표만으로 확정되는 계산이다.

시나리오는 순차 처리하고 요약 행만 남긴다. 세 벌의 시계열을 동시에 들지 않는다.
"""

from __future__ import annotations

import pandas as pd

from kwise.compare.combination import CombinationSpec, evaluate_combination
from kwise.io import UsageData
from kwise.pv import DEFAULT_FACTORS, SensitivityFactors
from kwise.quality import QualityReport
from kwise.tariff import BillingOptions, BillingResult, TariffTable

__all__ = ["SENSITIVITY_NOTE", "sensitivity_comparison"]

SENSITIVITY_NOTE = (
    "감도 계수는 태양광 출력에만 적용했습니다. 요금제 전환과 계약전력 조정은 "
    "실측 데이터와 요금표만으로 확정되는 계산이라 감도를 적용하지 않습니다 "
    "(요구사항서 9.2). 확률 표기(P90 등)는 쓰지 않습니다."
)


def sensitivity_comparison(
    usage: UsageData,
    table: TariffTable,
    spec: CombinationSpec,
    *,
    baseline_bill: BillingResult,
    unit_pv_kw_per_kwp: pd.Series | None = None,
    charge_mask: pd.Series | None = None,
    factors: SensitivityFactors = DEFAULT_FACTORS,
    quality: QualityReport | None = None,
    options: BillingOptions | None = None,
) -> pd.DataFrame:
    """한 조합을 감도 3종으로 평가한 표."""
    from dataclasses import replace

    rows: list[dict[str, object]] = []
    for name, factor in factors.items():
        scenario = replace(spec, sensitivity_factor=factor)
        result = evaluate_combination(
            usage,
            table,
            scenario,
            baseline_bill=baseline_bill,
            unit_pv_kw_per_kwp=unit_pv_kw_per_kwp,
            charge_mask=charge_mask,
            quality=quality,
            options=options,
        )
        rows.append(
            {
                "시나리오": name,
                "계수": factor,
                "발전량(kWh)": result.generation_kwh,
                "잉여(kWh)": result.surplus_kwh,
                "요금적용전력(kW)": result.billing_demand_kw,
                "절감액(원)": result.saving_won,
                "12개월 환산 절감액(원)": result.annual_saving_won,
                "투자비(원)": result.investment_won,
                "회수기간(년)": result.payback_years,
                "확실성": str(result.certainty),
            }
        )
    return pd.DataFrame(rows).set_index("시나리오")
