"""화면이 쥔 값 — 입력과 고른 것. **작업 파일 저장 · 불러오기가 이것을 그대로 적고 읽는다.**

Streamlit 은 같은 값을 세션 상태(``kwise.ui.state``)에 둔다 — 그 모듈은 streamlit 을 끌어와
이 판에서 못 부르므로 칸을 따로 둔다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from kwise.measures import (
    CURTAIL_SCENARIO,
    default_external_price_won_per_kwh,
    default_smp_price_won_per_kwh,
)
from kwise.pv import PvPresets
from kwise.ui.pipeline import SolarInputs


@dataclass
class SolarForm:
    """태양광 카드의 입력칸 값."""

    area: float = 1000.0
    density: str = ""
    azimuth: str = ""
    unit_cost: float = 0.0
    capacity: float = 0.0
    azimuth_override: float = 0.0
    loss: float = 0.14
    total_known: bool = False
    total: float = 0.0
    wall_area: float = 0.0
    wall_azimuth: str = ""
    #: 남는 전기 처리 — 기본은 아무 절차도 필요 없는 출력제어다 (Streamlit 과 같다).
    surplus_use: str = CURTAIL_SCENARIO
    #: 단가 둘 — ``None`` 이면 아직 손대지 않아 기준 데이터 기본값, 0 이면 미산출.
    surplus_price: float | None = None
    smp_price: float | None = None
    #: 「태양광 계산」 을 누른 적이 있는가 — 있으면 그때의 칸 값이 ``ran_form`` 이다.
    ran_form: dict[str, object] | None = None

    def prices(self) -> tuple[float | None, float | None]:
        """(외부 판매, 상계 SMP) — 안 만졌으면 기본값, 0 은 미산출 (Streamlit 과 같다)."""
        external = (
            default_external_price_won_per_kwh()
            if self.surplus_price is None
            else self.surplus_price
        )
        smp = default_smp_price_won_per_kwh() if self.smp_price is None else self.smp_price
        return (external or None), (smp or None)

    def fields(self) -> dict[str, object]:
        """계산에 닿는 칸만 — 묵은 결과 판정 · 마지막 계산 입력에 쓴다."""
        return {
            name: getattr(self, name)
            for name in (
                "area",
                "density",
                "azimuth",
                "unit_cost",
                "capacity",
                "azimuth_override",
                "loss",
                "total_known",
                "total",
                "wall_area",
                "wall_azimuth",
            )
        }

    def inputs(self, region_key: str, presets: PvPresets, *, ran: bool = False) -> SolarInputs:
        """공용 ``SolarInputs`` — ``ran`` 이면 마지막으로 계산한 칸 값으로 짓는다."""
        values = dict(self.fields())
        if ran and self.ran_form is not None:
            values.update(self.ran_form)
        density = str(values["density"] or presets.default.key)
        azimuth = presets.azimuth(str(values["azimuth"] or presets.default_azimuth)).azimuth_deg
        wall_area = float(values["wall_area"] or 0)  # type: ignore[arg-type]
        wall = (
            presets.azimuth(str(values["wall_azimuth"] or presets.default_azimuth)).azimuth_deg
            if wall_area > 0
            else None
        )
        total_known = bool(values["total_known"])
        return SolarInputs(
            region_key=region_key,
            area_m2=float(values["area"] or 0),  # type: ignore[arg-type]
            density_key=density,
            capacity_kwp=float(values["capacity"] or 0) or None,  # type: ignore[arg-type]
            azimuth_deg=float(values["azimuth_override"] or 0) or azimuth,  # type: ignore[arg-type]
            system_loss_ratio=float(values["loss"]),  # type: ignore[arg-type]
            wall_area_m2=wall_area,
            wall_azimuth_deg=wall,
            unit_cost_won_per_kwp=float(values["unit_cost"] or 0) or None,  # type: ignore[arg-type]
            total_investment_won=(float(values["total"] or 0) or None) if total_known else None,  # type: ignore[arg-type]
        )

    @property
    def stale(self) -> bool:
        """계산한 뒤 칸을 바꿨는가 — 「입력이 변경되었습니다」."""
        return self.ran_form is not None and self.ran_form != self.fields()


@dataclass
class Choices:
    """2 · 3단계에서 사람이 고른 것."""

    enabled: set[str] = field(default_factory=set)
    day_key: str = ""
    custom_day: dt.date | None = None
    pf_target: float | None = None
    pf_investment: float | None = None
    dr_priced: bool = False
    dr_price: float | None = None
    #: 쉬는 날이어서 뺄 날짜(ISO) — 1단계 진단이 읽는다.
    dr_off_days: tuple[str, ...] = ()
    #: ESS 가 고른 목표 요금적용전력 — 3단계가 같은 값을 읽는다 (고를 것이 없으면 0).
    ess_target: float = 0.0
    #: 「기준 데이터」 의 ESS 단가 경로 · 두 계수 — 비었으면 출고값.
    ess_path: str = ""
    ess_fixed: float | None = None
    ess_per_kwh: float | None = None
    solar: SolarForm = field(default_factory=SolarForm)
    #: 3단계 — 조합 체크(기본 참) · 「합산효과 계산」 을 누른 그 선택 · 시계열 시트 포함.
    combo_checked: dict[str, bool] = field(default_factory=dict)
    combo_computed: tuple[str, ...] | None = None
    include_timeseries: bool = True

    @property
    def ess_quote(self) -> float | None:
        """ESS 견적 — 화면 입력을 뺐다, 늘 ``None`` (2026-11-06 · Streamlit 과 같다)."""
        return None

    @property
    def dr_unit_price(self) -> float | None:
        """「정산 단가를 안다」 를 체크했을 때만 — 0 은 미입력 (Streamlit ``dr_unit_price``)."""
        return (self.dr_price or None) if self.dr_priced else None
