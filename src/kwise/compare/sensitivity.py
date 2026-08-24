"""감도 비교 (요구사항서 9.2, 8장).

**PV 출력의 첨예도만 조정해** 조합을 세 번 평가한다. 일별 총 발전량은 세
시나리오에서 같으므로 **전력량요금 절감액은 거의 움직이지 않고, 기본요금
절감액만 벌어진다.** 발전량 예측의 실제 불확실성이 "총량"이 아니라 "맑은 날
정오의 첨예도"에 있기 때문이다 (9.1 — 재분석 데이터의 평탄화 편향).

**결과는 3열 나열이 아니라 범위로 보여 준다.**

    기본요금 절감  3,152만원 (프로파일 감도 범위 2,897 ~ 3,266만원)

세 값을 나란히 놓으면 읽는 사람이 "어느 쪽이 좋은 값인가" 를 찾게 되는데, 이
축에는 좋고 나쁨이 없다. 최댓값이 평탄형에서 나올지 첨예형에서 나올지는 **건물
부하 형태가 정한다** — 정오 피크형이면 첨예형이, 오전·오후 피크형이면 평탄형이
크게 나온다. 그래서 min/max 로 범위만 뽑는다.

**요금제 전환과 계약전력 조정에는 감도를 적용하지 않는다** — 실측과 요금표만으로
확정되는 계산이다. 역률 개선도 같다.

시나리오는 순차 처리하고 요약 행만 남긴다. 세 벌의 시계열을 동시에 들지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from kwise.compare.combination import CombinationSpec, evaluate_combination
from kwise.io import UsageData
from kwise.progress import ProgressReporter, record
from kwise.pv import SharpnessFactors, load_sharpness_factors
from kwise.quality import QualityReport
from kwise.tariff import BillingOptions, BillingResult, TariffTable

__all__ = [
    "RANGE_METRICS",
    "SCENARIO_NAME_CAVEAT",
    "SENSITIVITY_NOTE",
    "SensitivityRange",
    "sensitivity_comparison",
    "sensitivity_range_frame",
    "sensitivity_ranges",
]

SENSITIVITY_NOTE = (
    "감도는 태양광 출력의 **첨예도**만 조정합니다. "
    "adjusted(t) = 일평균발전 + (기준(t) − 일평균발전) × s 이므로 일별 총 발전량이 "
    "보존되고, 전력량요금 절감액은 세 시나리오에서 거의 같습니다. 벌어지는 것은 "
    "피크에 걸린 기본요금 절감액입니다. 발전량 예측의 불확실성이 총량이 아니라 "
    "정오 첨예도에 있기 때문입니다. 요금제 전환·계약전력 조정·역률 개선은 "
    "실측 데이터와 요금표만으로 확정되는 계산이라 감도를 적용하지 않습니다. "
    "확률 표기(P90 등)는 쓰지 않습니다."
)

# 이름을 오해하기 쉬운 지점이다. 반드시 함께 낸다.
SCENARIO_NAME_CAVEAT = (
    "시나리오 이름은 **발전 프로파일의 모양**을 가리킵니다. 좋고 나쁨의 축이 "
    "아닙니다. '첨예형'은 정오 출력을 더 뾰족하게 본다는 뜻이며, 일별 총량이 "
    "보존되므로 그만큼 아침·저녁 어깨 시간대 출력이 낮아집니다. 부하 피크가 정오에 "
    "있으면 첨예형의 기본요금 절감액이 크고, 정오에서 벗어나 있으면(오전 피크형·"
    "오후 피크형) 평탄형이 큽니다. **어느 쪽이 최댓값인지는 건물 부하 형태가 "
    "정합니다.** 그래서 결과를 세 값의 나열이 아니라 범위로 표시합니다."
)

# 범위로 낼 지표. (열 이름, 단위 표기, 소수 자리)
RANGE_METRICS: tuple[tuple[str, str, int], ...] = (
    ("기본요금 절감액(원)", "원", 0),
    ("전력량요금 절감액(원)", "원", 0),
    ("절감액(원)", "원", 0),
    ("12개월 환산 절감액(원)", "원", 0),
    ("요금적용전력(kW)", "kW", 1),
    ("발전량(kWh)", "kWh", 0),
    ("회수기간(년)", "년", 1),
)


@dataclass(frozen=True)
class SensitivityRange:
    """지표 하나의 감도 범위.

    Attributes:
        low_scenario, high_scenario: 최소·최대가 나온 시나리오 이름.
            **건물 유형에 따라 서로 바뀐다.** 어느 쪽이 최댓값인지 고정하지 않는다.
    """

    metric: str
    unit: str
    base: float | None
    low: float | None
    high: float | None
    low_scenario: str
    high_scenario: str
    decimals: int = 0

    @property
    def spread_ratio(self) -> float | None:
        """범위 폭 ÷ 최댓값 절대값. 총량 기반 지표는 0 에 가깝다."""
        if self.low is None or self.high is None:
            return None
        scale = max(abs(self.low), abs(self.high))
        return (self.high - self.low) / scale if scale > 0 else 0.0

    def range_text(self) -> str:
        """지표 이름을 뺀 값과 범위. **이름은 부르는 쪽이 붙인다** (25세션 3절).

        화면 목록이 ``- **요금적용전력(kW)** {text()}`` 로 적었더니 이름이 한 줄에
        두 번 나왔다. 이름을 이미 굵게 낸 자리에는 이쪽을 쓴다.
        """
        if self.base is None:
            return "미산출"
        digits = self.decimals
        body = f"{self.base:,.{digits}f}{self.unit}"
        if self.low is None or self.high is None:
            return body
        return (
            f"{body} "
            f"(프로파일 감도 범위 {self.low:,.{digits}f} ~ {self.high:,.{digits}f}{self.unit})"
        )

    def text(self) -> str:
        """``기본요금 절감액 31,518,402원 (프로파일 감도 범위 28,968,918 ~ 32,657,891원)``."""
        if self.base is None:
            return f"{self.metric}: 미산출"
        return f"{self.metric} {self.range_text()}"


def sensitivity_comparison(
    usage: UsageData,
    table: TariffTable,
    spec: CombinationSpec,
    *,
    baseline_bill: BillingResult,
    unit_pv_kw_per_kwp: pd.Series | None = None,
    charge_mask: pd.Series | None = None,
    factors: SharpnessFactors | None = None,
    quality: QualityReport | None = None,
    options: BillingOptions | None = None,
    progress: ProgressReporter | None = None,
) -> pd.DataFrame:
    """한 조합을 감도 3종으로 평가한 **원자료** 표.

    표시는 :func:`sensitivity_range_frame` 의 범위 쪽을 쓴다. 이 표는 근거다.
    ``progress`` 는 선택 인자다 (10.6) — 시나리오마다 요금을 다시 계산한다.
    """
    from dataclasses import replace

    report = record(progress)
    items = load_sharpness_factors() if factors is None else factors
    rows: list[dict[str, object]] = []
    for index, (label, sharpness) in enumerate(items.items()):
        report.step(index + 1, f"{index + 1}/{len(items.labels)} {label}")
        scenario = replace(spec, sharpness=sharpness)
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
                "시나리오": label,
                "첨예도 s": sharpness,
                "발전량(kWh)": result.generation_kwh,
                "잉여(kWh)": result.surplus_kwh,
                "요금적용전력(kW)": result.billing_demand_kw,
                "기본요금 절감액(원)": baseline_bill.total_base_won - result.bill.total_base_won,
                "전력량요금 절감액(원)": (
                    baseline_bill.total_energy_won - result.bill.total_energy_won
                ),
                "절감액(원)": result.saving_won,
                "12개월 환산 절감액(원)": result.annual_saving_won,
                "투자비(원)": result.investment_won,
                "회수기간(년)": result.payback_years,
            }
        )
    return pd.DataFrame(rows).set_index("시나리오")


def sensitivity_ranges(
    frame: pd.DataFrame,
    *,
    metrics: tuple[tuple[str, str, int], ...] = RANGE_METRICS,
    base_label: str | None = None,
) -> tuple[SensitivityRange, ...]:
    """감도 표를 **범위**로 접는다. 최댓값·최솟값은 ``min``/``max`` 로 뽑는다.

    어느 시나리오가 최댓값인지 미리 정하지 않는다 — 건물 유형에 따라 평탄형에서
    나올 수도 첨예형에서 나올 수도 있다.
    """
    reference = load_sharpness_factors().base_label if base_label is None else base_label
    ranges: list[SensitivityRange] = []
    for metric, unit, decimals in metrics:
        if metric not in frame.columns:
            continue
        series = frame[metric].dropna()
        base = (
            float(frame.loc[reference, metric])
            if reference in frame.index and pd.notna(frame.loc[reference, metric])
            else None
        )
        if series.empty:
            ranges.append(
                SensitivityRange(metric, unit, base, None, None, "—", "—", decimals=decimals)
            )
            continue
        ranges.append(
            SensitivityRange(
                metric=metric,
                unit=unit,
                base=base,
                low=float(series.min()),
                high=float(series.max()),
                low_scenario=str(series.idxmin()),
                high_scenario=str(series.idxmax()),
                decimals=decimals,
            )
        )
    return tuple(ranges)


def sensitivity_range_frame(
    frame: pd.DataFrame,
    *,
    metrics: tuple[tuple[str, str, int], ...] = RANGE_METRICS,
    base_label: str | None = None,
) -> pd.DataFrame:
    """산출물에 싣는 감도 표. **3열 나열이 아니라 범위다.**"""
    rows = [
        {
            "지표": item.metric,
            "기준값": item.base,
            "범위 하한": item.low,
            "범위 상한": item.high,
            "하한 시나리오": item.low_scenario,
            "상한 시나리오": item.high_scenario,
            "범위 폭(%)": None if item.spread_ratio is None else item.spread_ratio * 100.0,
            "표시": item.text(),
        }
        for item in sensitivity_ranges(frame, metrics=metrics, base_label=base_label)
    ]
    return pd.DataFrame(rows).set_index("지표")
