"""Excel · PPT 를 짓는다 — 재료는 3단계가 모은 결과 한 벌, 조립은 공용 ``kwise.report``.

Streamlit 3단계 내려받기와 같은 인자를 넘긴다. 데스크톱이라 내려받기가
없어 **사용자 「다운로드」 폴더에 바로 저장한다** (날짜 · 시각이 붙은 이름이라 겹치지 않는다).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from kwise.measures import load_ess_cost_model
from kwise.report import DocumentSections, ReportSections, no_pv_sensitivity_frame, slides_bytes
from kwise.report.excel import sensitivity_items
from kwise.report.notices import SCHOOL_HIGH_VOLTAGE_NOTICE
from kwise.ui.pipeline import daily_temperature
from kwise.ui.session import build_report_bytes
from kwise.ui.spec import review_scope

from . import stage3

if TYPE_CHECKING:
    from .api import _Session
    from .stage2 import Context

#: 산출물을 두는 자리.
DOWNLOADS = Path.home() / "Downloads"
#: 이 창의 이름 — Excel 을 잠깐 쓰는 임시 폴더 이름에 쓴다 (공용 ``session_dir``).
_SESSION = uuid.uuid4().hex


def make(s: _Session, ctx: Context | None, kind: str, *, include_timeseries: bool) -> Path:
    """``kind`` 는 ``excel`` 또는 ``ppt``. 저장한 경로를 돌려준다."""
    if ctx is None or s.results is None:
        raise RuntimeError("3단계 결과가 없습니다 — 「개선안 조합」 탭을 먼저 여십시오.")
    if stage3.stale_lines(ctx, s.choices):
        raise RuntimeError("태양광 입력이 변경되었습니다 — 2단계에서 다시 계산하십시오.")
    c = s.choices
    results = s.results
    structure = ctx.diagnosis.structure
    assert structure is not None
    reviewed = stage3.enabled(c)
    computed = c.combo_computed
    comparison = sensitivity = None
    if computed is not None:
        keys = tuple(k for k in computed if k in reviewed)
        pair = s.combos.get(keys)
        if pair is not None:
            comparison, sensitivity = pair
    if sensitivity is None:
        sensitivity = no_pv_sensitivity_frame()
    billed = ctx.form.power_factor_pct is not None
    building = ctx.building
    if kind == "excel":
        sections = ReportSections(
            usage=ctx.usage,
            bill=structure.bill,
            diagnosis=ctx.diagnosis,
            comparison=comparison,
            sensitivity=sensitivity,
            measure_rows=results.excel_frame(),
            solar_curve=results.solar_curve,
            include_timeseries=include_timeseries,
            worksheets=results.worksheets(),
            tariff_table=ctx.table,
            ess_cases=load_ess_cost_model().case_table(),
            measure_notices=results.notice_groups(),
            solar=results.solar,
            peer_savings=results.peer_savings(),
            floor_area_m2=building.floor_area_m2,
            power_factor_billed=billed,
        )
        payload, filename = build_report_bytes(sections, session_id=_SESSION)
    elif kind == "ppt":
        scope = review_scope(reviewed)
        loaded = daily_temperature(ctx.usage, building.region_key)
        document = DocumentSections(
            usage=ctx.usage,
            bill=structure.bill,
            diagnosis=ctx.diagnosis,
            comparison=comparison,
            sensitivity=sensitivity_items(
                sensitivity, comparison, results.peer_savings(), results.solar
            ),
            measures=results.entries(),
            surplus=results.surplus_page(),
            worksheets=results.worksheets(),
            tariff_table=ctx.table,
            ess_cases=load_ess_cost_model().case_table(),
            temperature=loaded[0] if loaded is not None else None,
            building_name=building.title,
            reviewed_labels=scope.reviewed_labels,
            skipped_labels=scope.skipped_labels,
            peer_savings=results.peer_savings(),
            school_notice=SCHOOL_HIGH_VOLTAGE_NOTICE if building.education else "",
            power_factor_billed=billed,
            dr_period_won=(
                results.demand_response.period_settlement_won
                if results.demand_response is not None
                else None
            ),
        )
        payload, filename = slides_bytes(document)
    else:
        raise ValueError(f"모르는 산출물입니다: {kind}")
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    path = DOWNLOADS / filename
    path.write_bytes(payload)
    return path
