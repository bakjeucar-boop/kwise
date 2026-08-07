"""요금적용전력 규칙 (요구사항서 5.2, 한전 기본공급약관 제68조).

세 조건이 겹친다. 하나라도 빠뜨리면 결과가 크게 어긋난다.

    ① 대상 시간대   중간부하·최대부하만. 경부하(22:00~08:00)는 제외한다.
                    공휴일은 최대수요전력을 경부하로 계량하므로 자동 제외된다.
    ② 대상 월       max(7·8·9월, 12·1·2월, 검침 당월) — 직전 12개월 범위 내.
                    3~6월과 10~11월 피크는 그 달 당월분으로만 잡히고 이월되지 않는다.
    ③ 하한          계약전력의 30% (종별 속성. 교육용 등은 15% 특례).

**"하계·동계"와 전력량요금의 "계절"은 다르다.**

    요금적용전력 대상월   7·8·9 · 12·1·2
    전력량요금 계절       여름 6·7·8 / 봄가을 3·4·5·9·10 / 겨울 11·12·1·2

9월은 전력량요금상 봄·가을철이지만 대상월이고, 6월·11월은 여름·겨울 단가를 쓰지만
대상월이 아니다. 그래서 :func:`is_demand_month` 를 :meth:`TariffTable.season_of` 와
**따로 둔다.** 같은 함수로 판정하면 두 규칙이 조용히 섞인다.
"""

from __future__ import annotations

import math
from collections.abc import Hashable, Mapping, Sequence
from typing import Any

import pandas as pd

from kwise.rules import rule_value

__all__ = [
    "apply_contract_floor",
    "billing_demands",
    "default_contract_floor_ratio",
    "default_demand_bands",
    "default_demand_months",
    "demand_eligible_mask",
    "demand_window_months",
    "is_demand_month",
    "monthly_demand_basis",
]

# 값은 ``data\rules_kr.json`` 에 있다 (요구사항서 12장). **모듈 상수로 붙잡지
# 않는다** — import 시점에 고정하면 파일을 고쳐도 옛 값으로 계산된다.


def default_demand_bands() -> tuple[str, ...]:
    """요금적용전력 대상 시간대. 중간·최대부하만이다."""
    return tuple(str(band) for band in rule_value("demand.bands"))


def default_demand_months() -> tuple[int, ...]:
    """요금적용전력 대상월. **전력량요금의 계절 정의와 다르다.**"""
    return tuple(int(month) for month in rule_value("demand.months"))


def default_contract_floor_ratio() -> float:
    """요금적용전력 하한 비율 (일반). 교육용(을)은 별도 항목이다."""
    return float(rule_value("demand.contract_floor_ratio.default"))


def demand_window_months() -> int:
    """직전 몇 개월 중 최대를 쓰는가."""
    return int(rule_value("demand.window_months"))


def is_demand_month(
    month: int,
    demand_months: Sequence[int] | None = None,
) -> bool:
    """요금적용전력 **대상월**인가.

    전력량요금의 계절 판정(:meth:`TariffTable.season_of`)과 **다른 규칙이다.**
    9월은 봄·가을철 단가를 쓰지만 대상월이고, 6월·11월은 여름·겨울 단가를 쓰지만
    대상월이 아니다.
    """
    months = default_demand_months() if demand_months is None else demand_months
    return int(month) in set(months)


def demand_eligible_mask(
    bands: pd.Series,
    *,
    demand_bands: Sequence[str] | None = None,
) -> pd.Series:
    """요금적용전력 대상 슬롯 마스크 — 중간부하·최대부하만 참.

    공휴일은 요일 규칙에 따라 전량 경부하로 계량되므로 여기서 자동으로 빠진다.
    """
    allowed = set(default_demand_bands() if demand_bands is None else demand_bands)
    return pd.Series(
        [str(band) in allowed for band in bands], index=bands.index, name="demand_eligible"
    )


def monthly_demand_basis(
    kw: pd.Series,
    months: pd.Series,
    eligible: pd.Series,
) -> dict[pd.Period, float]:
    """월별 요금적용전력 대상 최대수요. 경부하 슬롯을 뺀 최대값이다.

    대상 슬롯이 하나도 없는 달은 0 이 된다 (관측 자체가 없는 경우).
    """
    frame = pd.DataFrame(
        {
            "month": months.to_numpy(),
            "kw": kw.to_numpy(dtype=float),
            "eligible": eligible.to_numpy(dtype=bool),
        }
    )
    selected = frame[frame["eligible"]]
    grouped = selected.groupby("month", observed=True)["kw"].max()
    result: dict[pd.Period, float] = {}
    for month in pd.unique(frame["month"]):
        period = pd.Period(month, freq="M") if not isinstance(month, pd.Period) else month
        value = grouped.get(period, float("nan"))
        result[period] = 0.0 if pd.isna(value) else float(value)
    return result


def _as_period(value: Hashable) -> pd.Period:
    if isinstance(value, pd.Period):
        return value
    return pd.Period(pd.Timestamp(str(value)), freq="M")


def billing_demands(
    monthly_peaks: Mapping[Any, float],
    *,
    prior_peaks: Mapping[Any, float] | None = None,
    window: int | None = None,
    demand_months: Sequence[int] | None = None,
) -> dict[pd.Period, float]:
    """요금적용전력 = max(대상월 피크, 당월 피크) — 직전 12개월 범위 내.

    Args:
        monthly_peaks: 월별 **대상 시간대** 최대수요 (경부하 제외).
        prior_peaks: 데이터 이전 기간의 이력. 없으면 첫 몇 달이 과소 산출된다.
        demand_months: 대상월. 기본은 7·8·9·12·1·2.

    3~6월·10~11월 피크는 그 달의 당월분으로만 쓰이고 다음 달로 이월되지 않는다.
    """
    peaks = {_as_period(key): float(value) for key, value in monthly_peaks.items()}
    history = dict(peaks)
    if prior_peaks:
        for key, value in prior_peaks.items():
            period = _as_period(key)
            history[period] = max(history.get(period, float("-inf")), float(value))

    span = demand_window_months() if window is None else window
    span = demand_window_months() if window is None else window
    result: dict[pd.Period, float] = {}
    for month in sorted(peaks):
        candidates: list[float] = []
        current = history.get(month)
        if current is not None and not math.isnan(current):
            candidates.append(current)  # 당월은 대상월이 아니어도 항상 센다
        for offset in range(1, span):
            past = month - offset
            if not is_demand_month(past.month, demand_months):
                continue  # 3~6월·10~11월 피크는 이월되지 않는다
            past_peak = history.get(past)
            if past_peak is not None and not math.isnan(past_peak):
                candidates.append(past_peak)
        result[month] = max(candidates) if candidates else float("nan")
    return result


def apply_contract_floor(
    demands: Mapping[Any, float],
    *,
    contract_kw: float | None,
    floor_ratio: float | None = None,
) -> dict[pd.Period, float]:
    """계약전력 하한을 씌운다. 계약전력이나 비율을 모르면 그대로 둔다.

    하한은 종별 속성이다. 일반용(을)은 30%, 교육용 등 일부는 15% 특례가 있다.
    비율이 확인되지 않은 종별은 요금 데이터에 ``null`` 로 두면 하한을 적용하지 않는다.
    """
    if contract_kw is None or floor_ratio is None:
        return {month: float(value) for month, value in demands.items()}
    if not 0.0 <= floor_ratio <= 1.0:
        raise ValueError(f"하한 비율은 0~1 이어야 합니다: {floor_ratio}")
    floor = contract_kw * floor_ratio
    return {month: max(float(value), floor) for month, value in demands.items()}
