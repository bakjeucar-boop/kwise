"""결측 처리 (요구사항서 4.2).

**기본은 미보간이다.** 결측 구간은 계산에서 제외하고 그 사실을 명시한다.
선형보간은 옵션으로만 둔다. 최대수요는 어차피 관측된 값 중 최대이며,
결측 구간에 더 큰 값이 있었을 수 있음을 산출물에 표기해야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

__all__ = ["FillMethod", "FillResult", "fill_missing"]

FillMethod = Literal["none", "linear"]


@dataclass(frozen=True, eq=False)
class FillResult:
    """보간 결과. 무엇을 얼마나 채웠는지 산출물에 남길 수 있게 함께 돌려준다."""

    kw: pd.Series
    method: FillMethod
    filled_slots: int
    remaining_missing: int

    @property
    def interpolated(self) -> bool:
        return self.filled_slots > 0


def fill_missing(
    kw: pd.Series,
    *,
    method: FillMethod = "none",
    limit: int | None = None,
) -> FillResult:
    """결측을 처리한다. 기본값은 미보간이다.

    Args:
        method: ``"none"`` (기본) 또는 ``"linear"``.
        limit: 한 구간에서 연달아 채울 최대 슬롯 수. None 이면 제한 없음.
            며칠짜리 공백까지 메우지 않으려면 반드시 지정한다.
    """
    missing_before = int(kw.isna().sum())
    if method == "none":
        return FillResult(
            kw=kw.copy(),
            method="none",
            filled_slots=0,
            remaining_missing=missing_before,
        )
    if method != "linear":
        raise ValueError(f"지원하지 않는 보간 방법입니다: {method!r}")

    # limit_area="inside" — 앞뒤 끝을 외삽하지 않는다. 없는 기간을 만들어내면 안 된다.
    filled = kw.interpolate(method="time", limit=limit, limit_area="inside")
    missing_after = int(filled.isna().sum())
    return FillResult(
        kw=filled,
        method="linear",
        filled_slots=missing_before - missing_after,
        remaining_missing=missing_after,
    )
