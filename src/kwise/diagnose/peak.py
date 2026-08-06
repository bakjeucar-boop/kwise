"""피크 특성 (요구사항서 6.2).

**단일 최대값이 아니라 월별 최대수요를 산출한다.** 요금은 월별로 매겨지고
요금적용전력은 그 이력의 최대치이기 때문이다.

상위 구간의 시각 분포는 **태양광의 피크 기여 가능성을 즉시 보여주는 지표**다.
건물마다 정반대 결과가 나오므로 반드시 표시한다.

시각 표기 규약 — 이 모듈은 '언제 피크가 났나' 를 답하므로 **검침 라벨**로 적는다.
한전 청구서와 부록 B 가 그렇게 적는다 (최대수요 5,293.4 kW @ 09:30). 반면
요금 귀속(계절·시간대·월)은 구간 시작 시각으로 판정한다 — tariff 모듈의 일이다.
두 규약은 15분 차이가 나며, 섞으면 조용히 틀린다. ``top_slots`` 에 두 값을 모두 담아
어느 쪽이 필요하든 꺼내 쓸 수 있게 했다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import pandas as pd

from kwise.io import slot_start
from kwise.tariff import (
    DEFAULT_CONTRACT_FLOOR_RATIO,
    DEFAULT_DEMAND_MONTHS,
    apply_contract_floor,
    billing_demands,
    monthly_demand_basis,
)

__all__ = ["DEFAULT_TOP_N", "PeakProfile", "peak_profile"]

DEFAULT_TOP_N = 100
_WEEKDAY_NAMES = ("월", "화", "수", "목", "금", "토", "일")


@dataclass(frozen=True, eq=False)
class PeakProfile:
    """피크 특성.

    Attributes:
        monthly: 월별 최대수요와 발생 시각, 요금적용전력.
        billing_demand_kw: 기간 전체의 요금적용전력 (최대값).
        top_slots: 상위 N 구간. 라벨 시각·요일과 구간 시작 시각을 함께 담는다.
        hour_counts: 상위 구간의 시각 분포 (라벨 기준, 0~23 전부).
        weekday_counts: 상위 구간의 요일 분포 (월~일).
        hourly_profile: 시각별 평균 부하 (kW).
    """

    monthly: pd.DataFrame
    billing_demand_kw: float
    billing_demand_before_floor_kw: float
    top_slots: pd.DataFrame
    hour_counts: pd.Series
    weekday_counts: pd.Series
    hourly_profile: pd.Series
    top_n: int
    observed_slots: int
    demand_months: tuple[int, ...] = DEFAULT_DEMAND_MONTHS

    @property
    def weekend_slots(self) -> int:
        """상위 구간 중 주말에 든 것. 0 이면 평일 집중형이다."""
        return int(self.top_slots["is_weekend"].sum())

    @property
    def peak_kw(self) -> float:
        return float(self.monthly["max_demand_kw"].max())

    def hour_share(self, hours: range) -> float:
        """주어진 시간대에 든 상위 구간의 비율."""
        total = int(self.hour_counts.sum())
        if total == 0:
            return 0.0
        return float(self.hour_counts.reindex(hours, fill_value=0).sum()) / total


def peak_profile(
    kw: pd.Series,
    interval_minutes: int,
    *,
    top_n: int = DEFAULT_TOP_N,
    prior_peaks: Mapping[str, float] | None = None,
    demand_eligible: pd.Series | None = None,
    demand_months: tuple[int, ...] = DEFAULT_DEMAND_MONTHS,
    contract_kw: float | None = None,
    contract_floor_ratio: float | None = DEFAULT_CONTRACT_FLOOR_RATIO,
) -> PeakProfile:
    """월별 최대수요, 상위 구간 분포, 시각별 평균 부하를 낸다.

    Args:
        kw: 15분 평균 수요. 결측은 NaN 인 채로 넘긴다.
        prior_peaks: 데이터 이전 기간의 최대수요 이력.
        demand_eligible: 요금적용전력 대상 슬롯 마스크 (중간·최대부하만).
            주지 않으면 모든 슬롯을 대상으로 본다 — 요금적용전력이 과대 산출되므로
            요금과 함께 볼 때는 반드시 넘긴다 (요구사항서 5.2 ①).
        demand_months: 요금적용전력 대상월. 전력량요금의 계절과 다르다 (5.2 ②).
        contract_kw, contract_floor_ratio: 하한 규정 (5.2 ③).
    """
    observed = kw.dropna()
    if observed.empty:
        raise ValueError("관측된 수요가 없어 피크 특성을 산출할 수 없습니다.")

    labels = pd.DatetimeIndex(observed.index)
    starts = slot_start(labels, interval_minutes)
    months = starts.to_period("M")  # 월 귀속은 구간 시작 기준 (tariff 와 같은 규약)

    grouped = observed.groupby(months, observed=True)
    peaks = grouped.max()
    peak_at = grouped.idxmax()

    month_series = pd.Series(months, index=observed.index)
    eligible = (
        demand_eligible.reindex(observed.index).fillna(False).astype(bool)
        if demand_eligible is not None
        else pd.Series(True, index=observed.index)
    )
    basis = monthly_demand_basis(observed, month_series, eligible)
    before_floor = billing_demands(basis, prior_peaks=prior_peaks, demand_months=demand_months)
    demands = apply_contract_floor(
        before_floor,
        contract_kw=contract_kw,
        floor_ratio=contract_floor_ratio,
    )

    monthly = pd.DataFrame(
        {
            "max_demand_kw": peaks,
            "max_demand_at": peak_at,
            "demand_basis_kw": pd.Series(basis),
            "demand_before_floor_kw": pd.Series(before_floor),
            "billing_demand_kw": pd.Series(demands),
        }
    )
    monthly.index.name = "month"
    at_index = pd.DatetimeIndex(monthly["max_demand_at"])
    monthly["weekday"] = [_WEEKDAY_NAMES[day] for day in at_index.weekday]
    monthly["hour"] = at_index.hour

    ranked = observed.nlargest(min(top_n, len(observed)))
    top_labels = pd.DatetimeIndex(ranked.index)
    top_starts = slot_start(top_labels, interval_minutes)
    top_slots = pd.DataFrame(
        {
            "kw": ranked.to_numpy(dtype=float),
            "hour": top_labels.hour,  # 검침 라벨 기준 (부록 B 규약)
            "slot_start": top_starts,
            "slot_start_hour": top_starts.hour,
            "weekday": [_WEEKDAY_NAMES[day] for day in top_labels.weekday],
            "is_weekend": top_labels.weekday >= 5,
        },
        index=top_labels,
    )
    top_slots.index.name = "timestamp"

    hour_counts = top_slots["hour"].value_counts().reindex(range(24), fill_value=0).sort_index()
    hour_counts.index.name = "hour"
    hour_counts.name = "slots"

    weekday_counts = (
        top_slots["weekday"].value_counts().reindex(list(_WEEKDAY_NAMES), fill_value=0).astype(int)
    )
    weekday_counts.index.name = "weekday"
    weekday_counts.name = "slots"

    hourly_profile = observed.groupby(labels.hour, observed=True).mean()
    hourly_profile.index.name = "hour"
    hourly_profile.name = "mean_kw"

    return PeakProfile(
        monthly=monthly,
        billing_demand_kw=float(max(demands.values())),
        billing_demand_before_floor_kw=float(max(before_floor.values())),
        demand_months=demand_months,
        top_slots=top_slots,
        hour_counts=hour_counts,
        weekday_counts=weekday_counts,
        hourly_profile=hourly_profile,
        top_n=top_n,
        observed_slots=len(observed),
    )
