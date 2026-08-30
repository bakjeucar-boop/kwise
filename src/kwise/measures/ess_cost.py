"""ESS 단가 입력과 참고단가 (요구사항서 7.6).

**단가 입력은 kW당 하나로 받는다.**

    투자비 = 필요 출력(kW) × 입력단가(원/kW)

2성분(원/kW + 원/kWh)이 물리적으로는 정확하지만 사용자가 견적서를 그 형태로 받는
일이 드물다. 입력은 단순하게 간다. 방전시간은 이미 사양(kW당 단가)에 반영되어
있으므로 **이중으로 곱하지 않는다.** 견적서를 받았으면 총액을 그대로 넣는 경로도
있다 (:class:`EssCostInput.of_total`).

**투자비는 조달 사례 모델이 낸다** (13세션에 교체했다).

    설비비 = 고정비 + 용량단가 × 용량(kWh)
    투자비 = 설비비 + 전기공사비

계수는 ``data\\ess_cost_model.json`` 에 있고 ``tools\\fit_ess_cost.py`` 가 조달
사례에서 다시 맞춘다. **코드에 손으로 적지 않는다.**

**단일 kWh당 단가로 표현하지 않는다.** 고정비가 1억을 넘어 총액÷용량이 50 kWh
440만원, 400 kWh 146만원으로 세 배 변한다.

판정은 **성립 조건**(:class:`Feasibility`)에서 나온다. 회수기간 하나로는 "왜 안
되는가" 를 알 수 없고, 값과 무관하게 늘 같은 문장이 나오는 판정은 판정이 아니다.

LCOS 참고단가(``data\\ess_cost_reference.json``)는 차익거래 에너지 단가와 2030년
전망 회수기간에만 남겨 두었다. **기본값으로 자동 적용하지 않는다.**
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from kwise import money
from kwise.notices import Notice, warn

__all__ = [
    "GRID_FILENAME",
    "MODEL_FILENAME",
    "PRICING_BANDS",
    "PRICING_FORMULA",
    "PRICING_QUOTED",
    "REFERENCE_FILENAME",
    "CapacityBand",
    "ElectricalBand",
    "EssCostInput",
    "EssCostModel",
    "EssCostReference",
    "EssCostReferenceError",
    "EssQuote",
    "EssSpecGrid",
    "EssTechnologyCost",
    "Feasibility",
    "load_ess_cost_model",
    "load_ess_cost_reference",
    "load_ess_spec_grid",
    "model_data_path",
    "reference_data_path",
    "reference_table",
    "spec_grid_data_path",
]

REFERENCE_FILENAME = "ess_cost_reference.json"
GRID_FILENAME = "ess_spec_grid.json"

PRICING_FORMULA = "2항식"
"""설비비 = 고정비 + 용량단가 × 용량. **기본 경로이며 계산에 쓰인다** (50세션)."""

PRICING_BANDS = "kWh 구간 단가"
"""용량 구간마다 kWh당 단가 하나. **기준 데이터 화면에서 고른다** (50세션)."""

PRICING_QUOTED = "견적 총액"
"""사용자가 받은 견적을 그대로 넣은 경우. **ESS 카드에 남는 유일한 단가 입력이다.**"""


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


@dataclass(frozen=True)
class EssSpecGrid:
    """실제로 조달되는 **규격 격자** (50세션 3-1).

    123 kW / 120 kWh 처럼 기성품에 없는 값으로 산출하면 설득력이 없다. 도입 사례
    여섯 건에서 관찰한 규격을 격자로 옮겨, 산출값을 **올려 잡는다**.

        PCS      50 · 75 · 100 kW, 100 kW 를 넘으면 병렬 50 kW 단위
        배터리   50 kWh 단위 (랙 100 kWh · 반 랙 50 kWh)

    **내려 잡지 않는다.** 내리면 목표를 못 지킨다. 올리면 투자비가 늘어 회수기간이
    길어지는데 그것이 정직한 방향이다.

    값은 ``data\\ess_spec_grid.json`` 에 있다 — **코드에 박지 않는다.** 사례가
    늘면 그 파일을 갱신한다.
    """

    unit_kw: tuple[float, ...]
    parallel_step_kw: float
    battery_step_kwh: float
    source: dict[str, Any] = field(default_factory=dict)

    def snap_power_kw(self, power_kw: float) -> float:
        """필요 출력을 살 수 있는 PCS 로 올린다. 123 kW → 150 kW."""
        if power_kw <= 0:
            return 0.0
        for unit in self.unit_kw:
            if power_kw <= unit + 1e-9:
                return unit
        step = self.parallel_step_kw
        return float(math.ceil(power_kw / step - 1e-9) * step)

    def snap_capacity_kwh(self, capacity_kwh: float) -> float:
        """필요 용량을 살 수 있는 배터리로 올린다. 120 kWh → 150 kWh.

        **0 보다 크면 최소 한 단위를 산다** — 6 kWh 짜리 배터리는 없다.
        """
        if capacity_kwh <= 0:
            return 0.0
        step = self.battery_step_kwh
        return float(max(step, math.ceil(capacity_kwh / step - 1e-9) * step))

    @property
    def minimum_capacity_kwh(self) -> float:
        """살 수 있는 가장 작은 배터리. 구간 단가의 비활성 경계가 여기서 나온다."""
        return self.battery_step_kwh


def spec_grid_data_path() -> Path:
    """규격 격자 파일. 요금표·사례와 같은 ``data\\`` 폴더에 둔다."""
    override = os.environ.get("KWISE_TARIFF_DIR")
    base = Path(override) if override else Path(__file__).resolve().parents[3] / "data"
    return base / GRID_FILENAME


@lru_cache(maxsize=1)
def load_ess_spec_grid(path: str | None = None) -> EssSpecGrid:
    """규격 격자를 읽는다. **코드에 값을 두지 않는다.**"""
    target = Path(path) if path is not None else spec_grid_data_path()
    if not target.is_file():
        raise EssCostReferenceError(f"ESS 규격 격자 파일이 없습니다: {target}")
    payload: dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))
    pcs = payload["pcs"]
    units = tuple(sorted(float(value) for value in pcs["unit_kw"]))
    if not units:
        raise EssCostReferenceError(f"PCS 규격이 비어 있습니다: {target}")
    step = float(pcs["parallel_step_kw"])
    battery = float(payload["battery"]["step_kwh"])
    if step <= 0 or battery <= 0:
        raise EssCostReferenceError(f"격자 단위는 양수여야 합니다: {target}")
    return EssSpecGrid(
        unit_kw=units,
        parallel_step_kw=step,
        battery_step_kwh=battery,
        source=dict(payload.get("source", {})),
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
        if self.unit_cost_won_per_kw is not None and self.total_won is not None:
            raise ValueError(
                "kW당 단가와 총액을 함께 줄 수 없습니다 "
                f"(단가={self.unit_cost_won_per_kw}, 총액={self.total_won})."
            )
        for value in (self.unit_cost_won_per_kw, self.total_won):
            if value is not None and value < 0:
                raise ValueError(f"단가·총액은 음수일 수 없습니다: {value}")

    @classmethod
    def unpriced(cls) -> EssCostInput:
        """입력이 없는 상태. **조달 사례 모델이 대신 산정한다** (13세션)."""
        return cls(source="조달 사례 모델")

    @property
    def is_unpriced(self) -> bool:
        return self.unit_cost_won_per_kw is None and self.total_won is None

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
        if self.unit_cost_won_per_kw is None:
            raise ValueError("단가도 총액도 없습니다. 조달 사례 모델로 산정하십시오.")
        return power_kw * self.unit_cost_won_per_kw

    @property
    def is_total(self) -> bool:
        return self.total_won is not None


# ===================================================================== 조달 사례 모델


MODEL_FILENAME = "ess_cost_model.json"


def model_data_path() -> Path:
    override = os.environ.get("KWISE_TARIFF_DIR")
    base = Path(override) if override else Path(__file__).resolve().parents[3] / "data"
    return base / MODEL_FILENAME


@dataclass(frozen=True)
class CapacityBand:
    """kWh 구간 단가 한 줄 (50세션 3-5 ②).

    **kW 가 아니라 kWh 로 가른다.** 사례에 100 kW 가 둘인데 156.4 kWh 는 2.35억,
    400 kWh 는 4.43억으로 kW당 단가가 1.9배 차이난다 — 값을 정하는 것은 용량이다.

    기본값은 2항식을 **구간 중앙**에서 환산해 채운다 (``tools\fit_ess_cost.py``).
    설비비만이며 전기공사비는 들어 있지 않다 — 2항식과 같은 층위라야 견줄 수 있다.

    Attributes:
        active: 조달되는 구간인가. 규격 격자의 최소 배터리 미만 구간은 **비활성**
            으로 목록에 남긴다 — 향후 상업용 소용량 제품이 나오면 살릴 자리다.
    """

    min_kwh: float
    max_kwh: float
    active: bool
    won_per_kwh: float | None
    midpoint_kwh: float = 0.0
    note: str = ""

    @property
    def label(self) -> str:
        return f"{self.min_kwh:,.0f}~{self.max_kwh:,.0f} kWh"


@dataclass(frozen=True)
class ElectricalBand:
    """전기공사비 구간. **점이 아니라 범위다** — 사례가 흩어져 있다."""

    max_kwh: float
    low_won: float
    high_won: float
    typical_won: float
    sample_size: int


@dataclass(frozen=True)
class EssQuote:
    """용량 하나에 대한 조달 사례 기준 견적.

    Attributes:
        applied_kwh: 실제로 산정에 쓴 용량. **50세션부터 산출 용량과 같다** —
            하한으로 올리는 일은 규격 격자가 앞에서 한다.
        in_range: 적용 구간 안인가. 밖이면 화면에 참고값이라고 적는다.
    """

    capacity_kwh: float
    applied_kwh: float
    equipment_won: float
    electrical_won: float
    electrical_low_won: float
    electrical_high_won: float
    in_range: bool
    pricing_path: str = PRICING_FORMULA
    """어느 단가 경로로 냈는가 (50세션). **결과에 한 줄로 표시한다.**"""
    notices: tuple[Notice, ...] = field(default=())

    @property
    def total_won(self) -> float:
        return self.equipment_won + self.electrical_won


@dataclass(frozen=True)
class Feasibility:
    """**성립 조건** — 이 사양으로 회수가 되려면 얼마나 낮춰야 하는가 (7.6).

    회수기간 하나로는 "왜 안 되는가" 를 알 수 없다. 배터리 비용이 kW당 절감액을
    넘어서면 규모를 어떻게 잡아도 회수되지 않는데, 그 사실이 회수기간 숫자에는
    드러나지 않는다. **판정은 하지 않는다** (14세션 3-3) — 두 값을 나란히 낸다.

        kW당 배터리비 = 용량단가 × 방전시간
        10년 절감/kW  = 기본요금단가 × 12 × 목표연수
        마진/kW       = 절감 − 배터리비
        필요 저감량   = (설비 고정비 + 전기공사) ÷ 마진
    """

    discharge_hours: float
    base_fee_won_per_kw: float
    target_years: float
    battery_won_per_kw: float
    saving_won_per_kw: float
    margin_won_per_kw: float
    fixed_won: float
    required_reduction_kw: float | None
    actual_reduction_kw: float

    @property
    def feasible(self) -> bool:
        """마진이 있고 실제 저감량이 필요 저감량 이상인가."""
        if self.required_reduction_kw is None:
            return False
        return self.actual_reduction_kw >= self.required_reduction_kw

    def message(self) -> str:
        """화면과 산출물이 같이 쓰는 한 문장.

        **판정 문장을 쓰지 않는다** (14세션 3-3). 단정은 사용자가 할 일이고,
        여기서는 두 값을 나란히 놓는 데서 그친다.
        """
        if self.required_reduction_kw is None:
            return (
                f"방전시간 {self.discharge_hours:,.2f}시간에서 kW당 배터리비 "
                f"{money.won(self.battery_won_per_kw, reason='—')}이 "
                f"{self.target_years:,.0f}년 기본요금 절감액 "
                f"{money.won(self.saving_won_per_kw, reason='—')}을 넘습니다."
            )
        return (
            f"현재 사양(방전 {self.discharge_hours:,.2f}시간)에서 "
            f"{self.target_years:,.0f}년 회수에 필요한 저감량은 "
            f"{self.required_reduction_kw:,.0f} kW 이고, 산출된 저감량은 "
            f"{self.actual_reduction_kw:,.0f} kW 입니다."
        )


@dataclass(frozen=True)
class EssCostModel:
    """조달 사례로 맞춘 투자비 모델 (tools 의 fit_ess_cost.py 가 만든다).

        설비비 = 고정비 + 용량단가 × 용량(kWh)

    **단일 kWh당 단가로 표현하지 않는다.** 고정비가 1억을 넘어 총액÷용량이
    50 kWh 440만원, 400 kWh 146만원으로 세 배 변한다. 그 값을 단가라 부르면
    작은 설비가 실제보다 나쁘게, 큰 설비가 좋게 보인다.

    **kW 는 설명 변수가 아니다.** PCS 가 50·75·100 kW 로 흩어져 있는데도 kWh
    하나로 R² 가 0.999 를 넘는다.
    """

    fixed_won: float
    per_kwh_won: float
    r2: float
    sample_size: int
    bands: tuple[ElectricalBand, ...]
    indoor_ratio: float | None
    min_kwh: float
    max_kwh: float
    fitted_on: str
    capacity_bands: tuple[CapacityBand, ...] = field(default=())
    pricing_path: str = PRICING_FORMULA
    """어느 단가 경로로 산정하는가 (50세션). 기본은 2항식이다."""
    holdout: tuple[Mapping[str, float], ...] = field(default=())
    rounding: tuple[Mapping[str, float], ...] = field(default=())
    cases: tuple[Mapping[str, Any], ...] = field(default=())
    adjusted: bool = False
    """사용자가 계수를 조정했는가 (14세션 3-4). 화면이 「계수 조정됨」을 표시한다."""

    # ------------------------------------------------------------- 계수 조정

    def with_coefficients(self, *, fixed_won: float, per_kwh_won: float) -> EssCostModel:
        """두 계수만 갈아 끼운다 (14세션 3-4).

        **kW당 단가로는 표현할 수 없다.** 같은 100 kW 인데 용량이 156.4 kWh 면
        2.35억, 400 kWh 면 4.43억이다 — kW 가 설명 변수가 아니다. 단가가 바뀌면
        이 두 값만 갱신하면 된다.
        """
        if fixed_won < 0 or per_kwh_won < 0:
            raise ValueError(f"계수는 음수일 수 없습니다: {fixed_won}, {per_kwh_won}")
        changed = fixed_won != self.fixed_won or per_kwh_won != self.per_kwh_won
        return replace(
            self,
            fixed_won=fixed_won,
            per_kwh_won=per_kwh_won,
            adjusted=self.adjusted or changed,
        )

    # **「시장 최소 규모」 를 뺐다** (50세션 3-3·3-4). 100 kWh 로 올려 잡던 규칙이
    # 사양 표 다섯 줄에 모두 「최소 규모」 표식을 달아 구별하는 힘이 없었고,
    # 투자비도 다섯 줄이 같았다. **규격 격자가 그 자리를 대신한다** — 살 수 있는
    # 최소 구성(50 kWh)이 자연히 하한이 되므로 따로 하한을 걸 것이 없다.
    # 조달되지 않는 규모는 최소 PCS 출력(``ess.min_pcs_power_kw``)이 가른다.

    def with_pricing_path(self, path: str) -> EssCostModel:
        """단가 경로를 갈아 끼운다 (50세션 3-5). **기준 데이터 화면에서 고른다.**"""
        if path not in (PRICING_FORMULA, PRICING_BANDS):
            raise ValueError(f"알 수 없는 단가 경로입니다: {path!r}")
        return replace(self, pricing_path=path)

    def capacity_band(self, capacity_kwh: float) -> CapacityBand | None:
        """용량이 드는 구간. **상한 포함**(``용량 ≤ 상한``)으로 가른다.

        전기공사 구간과 같은 규약이다. 구간 저변에서 투자비를 비싸게 잡아
        회수기간이 길게 나오므로 안전한 방향이기도 하다.

        **사례 최대(400 kWh)를 넘으면 마지막 구간 값을 그대로 쓴다** — kWh당
        단가는 용량이 커질수록 내려가므로 과대 추정이지만, 그 역시 안전한
        방향이다. 대신 :meth:`quote` 가 「사례 범위 초과」 를 표시한다.
        """
        active = [band for band in self.capacity_bands if band.active]
        if not active:
            return None
        for band in active:
            if capacity_kwh <= band.max_kwh + 1e-9:
                return band
        return active[-1]

    @property
    def coefficient_source(self) -> str:
        """계수 출처와 최종 적합일. 화면이 그대로 싣는다 (14세션 3-4)."""
        return f"도입 사례 {self.sample_size}건 기준, {self.fitted_on} 적합"

    @property
    def max_rounding_error(self) -> float:
        """천 원 반올림으로 생긴 재현 오차의 최대 절대값."""
        if not self.rounding:
            return 0.0
        return max(abs(float(row["error_ratio"])) for row in self.rounding)

    # ------------------------------------------------------------- 설비·공사

    def equipment_won(self, capacity_kwh: float) -> float:
        """설비비. **고른 단가 경로를 따른다** (50세션 3-5).

        기본은 2항식이다 — 도입 사례 넷을 1.4% 이내로 재현한다. 구간 단가를
        고르면 그 구간의 kWh당 단가를 곱한다.
        """
        if self.pricing_path == PRICING_BANDS:
            band = self.capacity_band(capacity_kwh)
            if band is not None and band.won_per_kwh is not None:
                return band.won_per_kwh * capacity_kwh
        return self.fixed_won + self.per_kwh_won * capacity_kwh

    def electrical_band(self, capacity_kwh: float) -> ElectricalBand:
        for band in self.bands:
            if capacity_kwh <= band.max_kwh:
                return band
        return self.bands[-1]

    def electrical_won(self, capacity_kwh: float, *, indoor: bool = False) -> float:
        """전기공사비. 구간 사이는 **선형 보간**한다."""
        value = self._interpolated(capacity_kwh)
        if indoor and self.indoor_ratio is not None:
            return value * self.indoor_ratio
        return value

    def _interpolated(self, capacity_kwh: float) -> float:
        first = self.bands[0]
        if capacity_kwh <= first.max_kwh or len(self.bands) == 1:
            return first.typical_won
        low, high = self.bands[0], self.bands[1]
        span = high.max_kwh - low.max_kwh
        if span <= 0:
            return high.typical_won
        ratio = min(1.0, (capacity_kwh - low.max_kwh) / span)
        return low.typical_won + (high.typical_won - low.typical_won) * ratio

    def quote(self, capacity_kwh: float, *, indoor: bool = False) -> EssQuote:
        """용량 하나의 견적. **적용 구간을 벗어나면 그 사실을 적는다.**

        **하한으로 올려 잡지 않는다** (50세션 3-3). 그 일은 규격 격자가 이미
        했다 — 여기 들어오는 용량은 살 수 있는 값이다.
        """
        if capacity_kwh < 0:
            raise ValueError(f"용량은 음수일 수 없습니다: {capacity_kwh}")
        notices: list[Notice] = []
        applied = capacity_kwh
        in_range = True
        if capacity_kwh > self.max_kwh:
            in_range = False
            # **주의** — 회귀 범위 밖이라 투자비 신뢰도가 떨어진다.
            notices.append(
                warn(
                    f"용량 {capacity_kwh:,.1f} kWh 는 사례 범위 "
                    f"{self.min_kwh:,.0f}–{self.max_kwh:,.0f} kWh 를 넘습니다. 참고값입니다.",
                    fact="ess.capacity_out_of_range",
                )
            )
        band = self.electrical_band(applied)
        scale = (self.indoor_ratio or 1.0) if indoor else 1.0
        return EssQuote(
            capacity_kwh=capacity_kwh,
            applied_kwh=applied,
            equipment_won=self.equipment_won(applied),
            electrical_won=self.electrical_won(applied, indoor=indoor),
            electrical_low_won=band.low_won * scale,
            electrical_high_won=band.high_won * scale,
            in_range=in_range,
            pricing_path=self.pricing_path,
            notices=tuple(notices),
        )

    # ------------------------------------------------------------- 성립 조건

    def feasibility(
        self,
        *,
        discharge_hours: float,
        base_fee_won_per_kw: float,
        target_years: float,
        actual_reduction_kw: float,
        quote: EssQuote,
    ) -> Feasibility:
        """성립 조건. **기본요금 단가는 계약종별에서 온다** — 하드코딩하지 않는다."""
        battery = self.per_kwh_won * discharge_hours
        saving = base_fee_won_per_kw * 12.0 * target_years
        margin = saving - battery
        fixed = self.fixed_won + quote.electrical_won
        required = fixed / margin if margin > 0 else None
        return Feasibility(
            discharge_hours=discharge_hours,
            base_fee_won_per_kw=base_fee_won_per_kw,
            target_years=target_years,
            battery_won_per_kw=battery,
            saving_won_per_kw=saving,
            margin_won_per_kw=margin,
            fixed_won=fixed,
            required_reduction_kw=required,
            actual_reduction_kw=actual_reduction_kw,
        )

    # ------------------------------------------------------------- 표

    def case_table(self) -> pd.DataFrame:
        """조달 사례 표. **회귀에 쓴 것과 참고만 하는 것을 갈라 적는다.**"""
        kinds = {"regression": "회귀", "reference": "참고", "catalog": "카탈로그"}
        installs = {"outdoor": "옥외", "indoor": "실내"}
        rows = [
            {
                "사례": str(case["label"]),
                "출력": f"{float(case['power_kw']):,.0f} kW",
                "용량": f"{float(case['capacity_kwh']):,.1f} kWh",
                "설비비": money.won(float(case["equipment_won"]), reason="—"),
                "전기공사": (
                    money.won(float(case["electrical_won"]), reason="—")
                    if case.get("electrical_won") is not None
                    else "설비비에 포함"
                ),
                "설치": installs.get(str(case.get("install")), str(case.get("install"))),
                "쓰임": kinds.get(str(case.get("category")), str(case.get("category"))),
                "비고": str(case.get("note", "")),
            }
            for case in self.cases
        ]
        return pd.DataFrame(rows)

    @property
    def formula(self) -> str:
        """**산식만** 적는다 (50세션 4절).

        49세션까지는 「(도입 사례 4건 기준, 2026-08-12 적합, R² 0.9996)」 가 이
        문장에 붙어 화면 툴팁까지 따라 나갔다. **출처는 신뢰의 문제이고 산식은
        결과를 읽는 문제다** — 갈라서 앞의 것은 :attr:`provenance` 로 보냈다.
        """
        adjusted = " · **계수 조정됨**" if self.adjusted else ""
        return (
            f"설비비 = {money.won(self.fixed_won, reason='—')} + "
            f"{self.per_kwh_won:,.0f}원/kWh × 용량(kWh){adjusted}"
        )

    @property
    def provenance(self) -> str:
        """계수의 **출처와 적합 품질** (50세션 4절).

        화면에는 두지 않는다. 매뉴얼·보고서 부록·기준 데이터 화면이 쓴다 —
        앞의 둘은 「이 값을 믿을 만한가」 를 따지는 자리이고, 기준 데이터 화면은
        **근거를 값 옆에 두는 것이 존재 이유**인 자리다.
        """
        return f"{self.coefficient_source}, R² {self.r2:.4f}"


@lru_cache(maxsize=1)
def load_ess_cost_model(path: str | None = None) -> EssCostModel:
    """투자비 모델을 읽는다. **코드에 계수를 두지 않는다.**"""
    target = Path(path) if path is not None else model_data_path()
    if not target.is_file():
        raise EssCostReferenceError(
            f"ESS 투자비 모델 파일이 없습니다: {target}. 재적합 스크립트를 실행하십시오."
        )
    payload: dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))
    equipment = payload["equipment"]
    work = payload.get("electrical_work", {})
    bands = tuple(
        ElectricalBand(
            max_kwh=float(item["max_kwh"]),
            low_won=float(item["low_won"]),
            high_won=float(item["high_won"]),
            typical_won=float(item["typical_won"]),
            sample_size=int(item.get("sample_size", 0)),
        )
        for item in work.get("bands", ())
    )
    if not bands:
        raise EssCostReferenceError(f"전기공사 구간이 비어 있습니다: {target}")
    scope = payload.get("applicable_range", {})
    indoor = work.get("indoor_ratio")
    capacity_bands = tuple(
        CapacityBand(
            min_kwh=float(item["min_kwh"]),
            max_kwh=float(item["max_kwh"]),
            active=bool(item.get("active", True)),
            won_per_kwh=(None if item.get("won_per_kwh") is None else float(item["won_per_kwh"])),
            midpoint_kwh=float(item.get("midpoint_kwh", 0.0)),
            note=str(item.get("note", "")),
        )
        for item in payload.get("capacity_bands", ())
    )
    return EssCostModel(
        fixed_won=float(equipment["fixed_won"]),
        per_kwh_won=float(equipment["per_kwh_won"]),
        r2=float(equipment.get("r2", 0.0)),
        sample_size=int(equipment.get("sample_size", 0)),
        bands=bands,
        indoor_ratio=None if indoor is None else float(indoor),
        min_kwh=float(scope.get("min_kwh", bands[0].max_kwh)),
        max_kwh=float(scope.get("max_kwh", bands[-1].max_kwh)),
        fitted_on=str(payload.get("fitted_on", "")),
        capacity_bands=capacity_bands,
        holdout=tuple(payload.get("holdout", ())),
        rounding=tuple(payload.get("rounding", ())),
        cases=tuple(payload.get("cases", ())),
    )
