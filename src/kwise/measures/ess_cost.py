"""ESS 단가 입력과 참고단가 (요구사항서 7.6).

**단가 입력은 kW당 하나로 받는다.**

    투자비 = 필요 출력(kW) × 입력단가(원/kW)

2성분(원/kW + 원/kWh)이 물리적으로는 정확하지만 사용자가 견적서를 그 형태로 받는
일이 드물다. 입력은 단순하게 간다. 방전시간은 이미 사양(kW당 단가)에 반영되어
있으므로 **이중으로 곱하지 않는다.** 견적서를 받았으면 총액을 그대로 넣는 경로도
있다 (:class:`EssCostInput.of_total`).

**참고단가는 2성분 원본을 저장하고 방전시간별로 환산해 제공한다.**

    kW당 단가 = CAPEX_Power + CAPEX_Energy × 방전시간

출처는 ``data\\ess_cost_reference.json`` 에 있다. 하드코딩하지 않는다.

**이 값은 추정치가 아니라 하한선이다.** 계통용 대형 ESS 기준이고 전용실·소화설비
같은 안전 규제 대응비와 수배전 연계공사비가 빠져 있다. 실제 견적은 반드시 이보다
높다. 그래서 손익분기 단가와의 비교는 **비대칭**이다.

    하한선 > 손익분기  →  경제성 없음        (강한 판정. 더 싸질 여지가 없다)
    하한선 < 손익분기  →  견적 받아볼 가치    (약한 판정. 실제 견적은 이보다 높다)

**참고값을 기본값으로 자동 적용하지 않는다.** 사용자가 명시적으로 골라야 한다.
자동 적용하면 출처가 다른 값이 견적으로 둔갑한다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

__all__ = [
    "REFERENCE_FILENAME",
    "EssCostInput",
    "EssCostReference",
    "EssCostReferenceError",
    "EssTechnologyCost",
    "load_ess_cost_reference",
    "reference_data_path",
    "reference_table",
]

REFERENCE_FILENAME = "ess_cost_reference.json"


class EssCostReferenceError(ValueError):
    """참고단가 파일을 읽지 못했을 때 발생한다."""


@dataclass(frozen=True)
class EssTechnologyCost:
    """기술 하나의 2성분 단가. **원본을 그대로 들고 있는다.**"""

    key: str
    label: str
    chemistry: str
    year: int
    capex_power_won_per_kw: float
    capex_energy_won_per_kwh: float
    opex_power_won_per_kw_year: float

    def unit_cost_won_per_kw(self, discharge_hours: float) -> float:
        """방전시간별 kW당 단가. ``CAPEX_Power + CAPEX_Energy × 방전시간``."""
        if discharge_hours < 0:
            raise ValueError(f"방전시간은 음수일 수 없습니다: {discharge_hours}")
        return self.capex_power_won_per_kw + self.capex_energy_won_per_kwh * discharge_hours

    def investment_won(self, power_kw: float, discharge_hours: float) -> float:
        return power_kw * self.unit_cost_won_per_kw(discharge_hours)


@dataclass(frozen=True)
class EssCostReference:
    """참고단가 한 벌. 출처와 성격을 함께 들고 다닌다."""

    technologies: tuple[EssTechnologyCost, ...]
    source: dict[str, Any]
    character: dict[str, Any]
    default_technology: str
    outlook_technology: str
    discharge_hours_shown: tuple[float, ...]
    auto_apply: bool = False

    def technology(self, key: str) -> EssTechnologyCost:
        for item in self.technologies:
            if item.key == key:
                return item
        raise EssCostReferenceError(
            f"없는 기술 키입니다: {key!r} (가능: {', '.join(i.key for i in self.technologies)})"
        )

    @property
    def default(self) -> EssTechnologyCost:
        """기본 표시 기술. **자동으로 단가에 적용되는 값이 아니다.**"""
        return self.technology(self.default_technology)

    @property
    def outlook(self) -> EssTechnologyCost:
        """전망 단가. '지금은 안 되지만 언제쯤 되는가' 를 답하는 데 쓴다."""
        return self.technology(self.outlook_technology)

    @property
    def citation(self) -> str:
        source = self.source
        return (
            f"{source.get('publisher', '')} 「{source.get('title', '')}」 "
            f"({source.get('basis_year', '')}년 기준, 환율 "
            f"{float(source.get('exchange_rate_won_per_usd', 0)):,.0f}원/달러)"
        )

    @property
    def lower_bound_note(self) -> str:
        return str(self.character.get("why", ""))

    def verdict(self, breakeven_won_per_kw: float | None, discharge_hours: float) -> str:
        """하한선과 손익분기 단가를 견준다. **판정이 비대칭이다.**"""
        if breakeven_won_per_kw is None:
            return "미산출 — 절감액이 없어 손익분기 단가를 낼 수 없습니다."
        lower_bound = self.default.unit_cost_won_per_kw(discharge_hours)
        asymmetry = self.character.get("asymmetry", {})
        if lower_bound > breakeven_won_per_kw:
            return (
                f"**경제성 없음** — 참고단가 하한선 {lower_bound:,.0f} 원/kW 가 손익분기 "
                f"{breakeven_won_per_kw:,.0f} 원/kW 를 이미 넘습니다. "
                f"{asymmetry.get('above_breakeven', '')} "
                "이 참고단가에는 안전 규제 대응비와 연계공사비가 빠져 있어 실제 견적은 "
                "더 높습니다. 강한 판정입니다."
            )
        return (
            f"**견적을 받아볼 가치가 있습니다** — 참고단가 하한선 {lower_bound:,.0f} 원/kW 가 "
            f"손익분기 {breakeven_won_per_kw:,.0f} 원/kW 보다 낮습니다. "
            f"{asymmetry.get('below_breakeven', '')} "
            "다만 이 값은 하한선이므로 실제 견적이 손익분기를 넘을 수 있습니다. "
            "약한 판정입니다."
        )


def reference_data_path() -> Path:
    """참고단가 파일. 요금표·프리셋과 같은 ``data\\`` 폴더에 둔다."""
    override = os.environ.get("KWISE_TARIFF_DIR")
    base = Path(override) if override else Path(__file__).resolve().parents[3] / "data"
    return base / REFERENCE_FILENAME


@lru_cache(maxsize=1)
def load_ess_cost_reference(path: str | None = None) -> EssCostReference:
    """참고단가를 읽는다. **코드에 값을 두지 않는다.**"""
    target = Path(path) if path is not None else reference_data_path()
    if not target.is_file():
        raise EssCostReferenceError(f"참고단가 파일이 없습니다: {target}")
    with target.open(encoding="utf-8") as stream:
        payload: dict[str, Any] = json.load(stream)

    raw = payload.get("technologies")
    if not raw:
        raise EssCostReferenceError(f"참고단가 목록이 비어 있습니다: {target}")
    technologies = tuple(
        EssTechnologyCost(
            key=str(item["key"]),
            label=str(item.get("label", item["key"])),
            chemistry=str(item.get("chemistry", "")),
            year=int(item["year"]),
            capex_power_won_per_kw=float(item["capex_power_won_per_kw"]),
            capex_energy_won_per_kwh=float(item["capex_energy_won_per_kwh"]),
            opex_power_won_per_kw_year=float(item.get("opex_power_won_per_kw_year", 0.0)),
        )
        for item in raw
    )
    reference = EssCostReference(
        technologies=technologies,
        source=dict(payload.get("source", {})),
        character=dict(payload.get("character", {})),
        default_technology=str(payload.get("default_technology", technologies[0].key)),
        outlook_technology=str(payload.get("outlook_technology", technologies[-1].key)),
        discharge_hours_shown=tuple(
            float(hour) for hour in payload.get("discharge_hours_shown", (0.5, 1.0, 2.0, 4.0))
        ),
        auto_apply=bool(payload.get("auto_apply", False)),
    )
    if reference.auto_apply:
        raise EssCostReferenceError(
            "참고값을 기본값으로 자동 적용할 수 없습니다 (auto_apply 는 false 여야 합니다). "
            "출처가 다른 값이 견적으로 둔갑합니다."
        )
    _ = reference.default, reference.outlook  # 키가 목록에 있는지 여기서 확인한다
    return reference


def reference_table(
    reference: EssCostReference | None = None,
    *,
    discharge_hours: tuple[float, ...] | None = None,
    highlight_hours: float | None = None,
) -> pd.DataFrame:
    """방전시간별 kW당 참고단가 표.

    Args:
        highlight_hours: 산출된 방전시간. 그 행에 표시를 남긴다 (8세션 UI 가 강조한다).
    """
    ref = reference if reference is not None else load_ess_cost_reference()
    hours = discharge_hours if discharge_hours is not None else ref.discharge_hours_shown
    rows: list[dict[str, object]] = []
    for hour in hours:
        row: dict[str, object] = {"방전시간(h)": hour}
        for technology in ref.technologies:
            row[f"{technology.label} (원/kW)"] = technology.unit_cost_won_per_kw(hour)
        row["산출 사양"] = (
            "◀ 산출된 방전시간"
            if highlight_hours is not None and abs(hour - highlight_hours) < 1e-9
            else ""
        )
        rows.append(row)
    return pd.DataFrame(rows).set_index("방전시간(h)")


@dataclass(frozen=True)
class EssCostInput:
    """단가 입력. **두 경로 중 하나다.**

    - kW당 단가 (기본) — ``투자비 = 출력(kW) × 단가``
    - 총액 직접 입력 — 견적서를 받은 경우

    참고단가에서 값을 가져오려면 :meth:`from_reference` 로 **명시적으로** 고른다.
    자동으로 채워지지 않는다.
    """

    unit_cost_won_per_kw: float | None = None
    total_won: float | None = None
    source: str = "사용자 입력"

    def __post_init__(self) -> None:
        if (self.unit_cost_won_per_kw is None) == (self.total_won is None):
            raise ValueError(
                "kW당 단가와 총액 중 정확히 하나를 주십시오 "
                f"(단가={self.unit_cost_won_per_kw}, 총액={self.total_won})."
            )
        for value in (self.unit_cost_won_per_kw, self.total_won):
            if value is not None and value < 0:
                raise ValueError(f"단가·총액은 음수일 수 없습니다: {value}")

    @classmethod
    def of_unit_cost(cls, won_per_kw: float, *, source: str = "사용자 입력") -> EssCostInput:
        return cls(unit_cost_won_per_kw=won_per_kw, source=source)

    @classmethod
    def of_total(cls, won: float, *, source: str = "견적서 총액") -> EssCostInput:
        return cls(total_won=won, source=source)

    @classmethod
    def from_reference(
        cls,
        discharge_hours: float,
        *,
        technology: str | None = None,
        reference: EssCostReference | None = None,
    ) -> EssCostInput:
        """참고단가에서 kW당 단가를 만든다. **명시적으로 부를 때만 쓰인다.**"""
        ref = reference if reference is not None else load_ess_cost_reference()
        item = ref.default if technology is None else ref.technology(technology)
        return cls(
            unit_cost_won_per_kw=item.unit_cost_won_per_kw(discharge_hours),
            source=f"참고단가 {item.label} · 방전 {discharge_hours:.2g}h — {ref.citation}",
        )

    def investment_won(self, power_kw: float) -> float:
        """투자비. **방전시간을 다시 곱하지 않는다** — 단가에 이미 들어 있다."""
        if self.total_won is not None:
            return self.total_won
        assert self.unit_cost_won_per_kw is not None  # __post_init__ 이 보장한다
        return power_kw * self.unit_cost_won_per_kw

    @property
    def is_total(self) -> bool:
        return self.total_won is not None
