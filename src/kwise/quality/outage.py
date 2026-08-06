"""정전 구간 검출 (요구사항서 4.1).

정전 중에는 피크가 발생할 수 없다. 그래서 편중 판정에서 정전 슬롯을 빼야 정확하다.
반면 계량기 통신 장애로 인한 결측은 실제 피크를 놓쳤을 수 있으므로 빼면 안 된다.
둘을 가르는 것이 이 모듈의 일이다.

판정 규칙 — 흔적 3종 중 **연속 결측을 뺀 나머지 2개 이상**을 요구한다.

    1. 연속 결측            (근거로 세지 않는다. 모든 결측이 갖는 성질이다)
    2. 그리드 이탈 흔적      정전 직전의 부분 적산 행, 또는 복전 시점 재등록 행
    3. 복전 후 저부하        공백 직후 관측치가 평소보다 현저히 낮다

샘플의 2023-11 공백(930슬롯)은 2·3번 흔적이 없어 정전으로 잡히지 않는다.
통신 장애를 정전으로 오인하면 그 구간이 편중 판정에서 빠져 피크 누락 위험이 가려진다.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from kwise.io import UsageData
from kwise.quality.missing import MissingGap

__all__ = [
    "DEFAULT_MIN_EVIDENCE",
    "OutageEvent",
    "detect_outages",
    "outage_slot_mask",
]

DEFAULT_MIN_EVIDENCE = 2
_RECOVERY_SLOTS = 4
_LOW_LOAD_RATIO = 0.05  # 최대수요 대비


@dataclass(frozen=True)
class OutageEvent:
    """정전으로 판정된 구간."""

    start: pd.Timestamp
    end: pd.Timestamp
    slots: int
    duration_hours: float
    partial_rows: tuple[pd.Timestamp, ...]
    partial_kwh: float
    recovery_at: pd.Timestamp | None
    recovery_kw: float | None
    evidence: tuple[str, ...]

    @property
    def decisive_evidence(self) -> int:
        """연속 결측을 뺀 흔적 수."""
        return len(self.evidence) - 1


def detect_outages(
    usage: UsageData,
    gaps: tuple[MissingGap, ...],
    *,
    low_load_kw: float | None = None,
    recovery_slots: int = _RECOVERY_SLOTS,
    min_evidence: int = DEFAULT_MIN_EVIDENCE,
) -> tuple[OutageEvent, ...]:
    """연속 결측 구간 중 정전으로 볼 수 있는 것만 골라낸다.

    Args:
        low_load_kw: 복전 후 저부하 판정 임계. 기본은 최대수요의 5%.
        recovery_slots: 공백 직후 몇 슬롯까지 저부하를 살필지.
        min_evidence: 연속 결측을 뺀 흔적이 몇 개 이상이어야 정전으로 볼지.
    """
    if not gaps:
        return ()

    interval = pd.Timedelta(minutes=usage.meta.interval_minutes)
    threshold = (
        low_load_kw if low_load_kw is not None else usage.meta.max_demand_kw * _LOW_LOAD_RATIO
    )
    observed = usage.kw.dropna()
    off_grid = usage.off_grid

    events: list[OutageEvent] = []
    for gap in gaps:
        evidence = [f"연속 결측 {gap.slots:,}슬롯 ({gap.days:.2f}일)"]

        # 흔적 2 — 그리드 이탈 행. 직전 부분 적산(첫 결측 슬롯을 품는 행)과
        # 공백 안의 복전 재등록 행을 함께 본다.
        trace = off_grid[
            (off_grid["timestamp"] > gap.start - interval) & (off_grid["timestamp"] <= gap.end)
        ]
        partial_rows = tuple(pd.Timestamp(stamp) for stamp in trace["timestamp"])
        partial_kwh = float(trace["kwh"].sum())
        if partial_rows:
            stamps = ", ".join(str(stamp) for stamp in partial_rows)
            evidence.append(
                f"그리드 이탈 흔적 {len(partial_rows)}건 ({stamps}, {partial_kwh:g} kWh)"
            )

        # 흔적 3 — 복전 후 저부하
        after = observed[observed.index > gap.end].iloc[:recovery_slots]
        recovery_at: pd.Timestamp | None = None
        recovery_kw: float | None = None
        if not after.empty and float(after.min()) < threshold:
            recovery_at = pd.Timestamp(after.idxmin())
            recovery_kw = float(after.min())
            evidence.append(
                f"복전 후 저부하 {recovery_kw:,.2f} kW @ {recovery_at} (임계 {threshold:,.1f} kW)"
            )

        if len(evidence) - 1 < min_evidence:
            continue  # 연속 결측만으로는 정전이라 하지 않는다

        events.append(
            OutageEvent(
                start=gap.start,
                end=gap.end,
                slots=gap.slots,
                duration_hours=gap.slots * usage.meta.interval_minutes / 60.0,
                partial_rows=partial_rows,
                partial_kwh=partial_kwh,
                recovery_at=recovery_at,
                recovery_kw=recovery_kw,
                evidence=tuple(evidence),
            )
        )
    return tuple(events)


def outage_slot_mask(index: pd.DatetimeIndex, outages: tuple[OutageEvent, ...]) -> pd.Series:
    """정전 구간에 해당하는 슬롯 마스크. 편중 판정에서 분자·분모 양쪽에 쓴다."""
    mask = pd.Series(False, index=index)
    for event in outages:
        mask.loc[event.start : event.end] = True
    return mask
