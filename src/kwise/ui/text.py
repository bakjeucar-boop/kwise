"""화면 표기 (요구사항서 10.2).

**금액·전력·기간을 한 곳에서 찍는다.** 화면마다 자릿수를 달리 쓰면 같은 값이
다르게 보인다.

모르는 값은 **빈칸이나 0 으로 두지 않는다.** 0원은 "공짜" 로, 회수기간 0년은
"즉시 회수" 로 읽힌다. 사유를 적는 것이 규약이다 (요구사항서 7.5).
"""

from __future__ import annotations

from kwise.measures import Certainty
from kwise.report.notices import format_won

__all__ = [
    "DASH",
    "certainty_badge",
    "kw",
    "kwh",
    "months",
    "mwh",
    "payback",
    "pct",
    "ratio_pct",
    "won",
    "won_short",
]

DASH = "—"

_EOK = 100_000_000
_MAN = 10_000


def won(value: float | None, *, reason: str | None = None) -> str:
    """원 단위 금액. ``None`` 이면 **사유**를 낸다 (빈칸·0원 금지)."""
    if value is None:
        return format_won(None) if reason is None else reason
    return f"{value:,.0f}원"


def won_short(value: float | None, *, reason: str | None = None) -> str:
    """억·만원으로 줄인 금액. KPI 타일처럼 자리가 좁은 곳에 쓴다."""
    if value is None:
        return format_won(None) if reason is None else reason
    sign = "-" if value < 0 else ""
    size = abs(value)
    if size >= _EOK:
        return f"{sign}{size / _EOK:,.2f}억원"
    if size >= _MAN:
        return f"{sign}{size / _MAN:,.0f}만원"
    return f"{sign}{size:,.0f}원"


def kw(value: float | None, *, decimals: int = 1) -> str:
    return DASH if value is None else f"{value:,.{decimals}f} kW"


def kwh(value: float | None, *, decimals: int = 0) -> str:
    return DASH if value is None else f"{value:,.{decimals}f} kWh"


def mwh(value: float | None, *, decimals: int = 1) -> str:
    return DASH if value is None else f"{value / 1000.0:,.{decimals}f} MWh"


def pct(value: float | None, *, decimals: int = 1) -> str:
    """이미 백분율인 값."""
    return DASH if value is None else f"{value:,.{decimals}f}%"


def ratio_pct(value: float | None, *, decimals: int = 1) -> str:
    """0~1 비율을 백분율로."""
    return DASH if value is None else f"{value * 100.0:,.{decimals}f}%"


def months(value: float | None, *, decimals: int = 1) -> str:
    return DASH if value is None else f"{value:,.{decimals}f}개월"


def payback(years: float | None, *, investment_won: float | None = None) -> str:
    """회수기간. **투자비를 모르면 0년이 아니라 사유**다.

    투자비가 0원인 무투자 수단만 '즉시' 로 적는다.
    """
    if years is None:
        if investment_won is None:
            return "미산출 — 투자비 미입력"
        return DASH
    if years <= 0:
        return "즉시"
    return f"{years:,.1f}년"


def certainty_badge(certainty: Certainty | str) -> str:
    """확실성 등급. **등급만 낸다** — 근거는 매뉴얼로 보낸다 (10.2)."""
    return f"확실성 {certainty}"
