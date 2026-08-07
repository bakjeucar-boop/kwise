"""수단을 적용한 뒤의 부하 만들기.

절감액은 빼기가 아니라 재계산이다. 그러려면 수단 적용 후의 15분 부하를 만들어
요금 엔진에 다시 넣어야 한다. 이 모듈이 그 부하를 만든다.

규약
    결측 슬롯은 결측인 채로 둔다. 발전량을 알아도 그 시각의 부하를 모르므로
    자가소비 판정이 불가능하다.
    역송(발전 > 부하)은 계통 사용량이 음수가 될 수 없으므로 0 으로 자르고,
    잘린 몫이 잉여다 (요구사항서 7.6).
    그리드 이탈(부분 적산) 행은 건드리지 않는다. 그 구간의 발전량을 배분할 근거가
    없고, 샘플에서 43.2 kWh — 전체의 0.0002% 라 보수적으로 남기는 편이 낫다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import pandas as pd

from kwise.io import GridKwhSeries, UsageData

__all__ = ["NetLoad", "apply_generation", "with_load"]


@dataclass(frozen=True, eq=False)
class NetLoad:
    """수단 적용 후의 부하와 그 부산물.

    Attributes:
        usage: 요금 엔진에 그대로 넣을 수 있는 :class:`UsageData`.
        generation_kw: 적용한 발전 출력 (부하 라벨 정렬).
        surplus_kw: 역송된 출력. 결측 슬롯은 NaN.
        self_consumption_ratio: 자가소비율. 발전량이 0 이면 None.
    """

    usage: UsageData
    generation_kw: pd.Series
    surplus_kw: pd.Series
    generated_kwh: float
    self_consumed_kwh: float
    surplus_kwh: float

    @property
    def self_consumption_ratio(self) -> float | None:
        if self.generated_kwh <= 0:
            return None
        return self.self_consumed_kwh / self.generated_kwh

    @property
    def surplus_ratio(self) -> float | None:
        if self.generated_kwh <= 0:
            return None
        return self.surplus_kwh / self.generated_kwh


def with_load(usage: UsageData, load_kw: pd.Series, *, source_suffix: str = "") -> UsageData:
    """kW 시계열을 갈아끼운 :class:`UsageData` 를 만든다.

    메타데이터의 수요·사용량 항목을 다시 계산한다. 결측·이탈 관련 항목은 그대로다.
    """
    meta = usage.meta
    index = pd.DatetimeIndex(usage.kw.index)
    kw = load_kw.reindex(index).astype(float).rename("kw")
    slot_hours = meta.slot_hours

    grid_kwh = kw * slot_hours
    kwh_grid = GridKwhSeries(grid_kwh.to_numpy(dtype=float), index=index, name="kwh")
    kwh_grid.off_grid_kwh = meta.off_grid_kwh

    total_kwh = float(grid_kwh.sum()) + meta.off_grid_kwh
    max_demand = float(kw.max()) if kw.notna().any() else 0.0
    mean_kw = float(kw.mean()) if kw.notna().any() else 0.0
    new_meta = replace(
        meta,
        source_name=f"{meta.source_name}{source_suffix}",
        total_kwh=total_kwh,
        max_demand_kw=max_demand,
        max_demand_at=pd.Timestamp(kw.idxmax()) if kw.notna().any() else pd.NaT,
        mean_kw=mean_kw,
        load_factor=mean_kw / max_demand if max_demand else 0.0,
    )
    return UsageData(kw=kw, kwh_grid=kwh_grid, off_grid=usage.off_grid, meta=new_meta)


def apply_generation(
    usage: UsageData,
    generation_kw: pd.Series,
    *,
    source_suffix: str = " + PV",
) -> NetLoad:
    """발전을 부하에서 뺀다. 역송은 잉여로 분리한다.

    Args:
        generation_kw: 부하 라벨에 정렬된 발전 출력. :func:`kwise.pv.align_simulation`
            을 거친 시계열이어야 한다. 정렬이 어긋나면 자가소비율이 통째로 틀린다.
    """
    index = pd.DatetimeIndex(usage.kw.index)
    load = usage.kw
    generation = generation_kw.reindex(index).fillna(0.0).astype(float)
    observed = load.notna()

    net = (load - generation).clip(lower=0.0)
    surplus = (generation - load).clip(lower=0.0).where(observed)
    slot_hours = usage.meta.slot_hours

    generated_kwh = float(generation[observed].sum()) * slot_hours
    surplus_kwh = float(surplus.sum()) * slot_hours
    return NetLoad(
        usage=with_load(usage, net, source_suffix=source_suffix),
        generation_kw=generation.rename("pv_kw"),
        surplus_kw=surplus.rename("surplus_kw"),
        generated_kwh=generated_kwh,
        self_consumed_kwh=generated_kwh - surplus_kwh,
        surplus_kwh=surplus_kwh,
    )
