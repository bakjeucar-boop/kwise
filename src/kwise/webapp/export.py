"""「파일 → 결과 저장 (JSON)」 — 지금 계산한 결과를 사람이 읽는 JSON 한 파일로 (그 시점 그대로).

작업 파일(``work.py``)과 다르다 — 그쪽은 입력을 담아 다시 계산하고, 이쪽은 **숫자를 담는다**.
화면과 같은 공용 함수를 같은 인자로 불러 적으므로 파일의 숫자는 화면과 같다.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from kwise import __version__, money
from kwise.measures import payback_years
from kwise.report import localize, simple_sum_won, standalone_frame
from kwise.report.columns import display_frame
from kwise.ui.labels import measure_title, option_label
from kwise.ui.spec import measure

from . import stage3

if TYPE_CHECKING:
    from .stage2 import Context
    from .state import Choices


def _plain(value: object) -> object:
    """JSON 에 그대로 들어가는 값으로 — 월(``Period``) 같은 칸은 글로 적는다."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, (int, float)):
        return None if pd.isna(value) else value
    if hasattr(value, "item"):  # numpy 수
        return _plain(value.item())
    return None if pd.isna(value) else str(value)


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    shown = display_frame(frame)
    return [
        {str(column): _plain(value) for column, value in row.items()}
        for row in shown.to_dict(orient="records")
    ]


def build(ctx: Context, choices: Choices, filename: str) -> dict[str, Any]:
    form, usage, diagnosis = ctx.form, ctx.usage, ctx.diagnosis
    meta = usage.meta
    structure = diagnosis.structure
    payload: dict[str, Any] = {
        "도구": f"kWise {__version__}",
        "저장 시각": dt.datetime.now().isoformat(timespec="seconds"),
        "사용량 파일": filename,
        "건물 정보": asdict(ctx.building),
        "계약 정보": {
            "계약종별": form.contract_type,
            "전압구분": form.voltage,
            "선택요금": option_label(form.option),
            "계약전력(kW)": form.contract_kw,
            "주간 지상역률(%)": form.lagging_pct,
            "야간 진상역률(%)": form.leading_power_factor_pct,
        },
        "1단계 진단": {
            "분석 시작": str(meta.start.date()),
            "분석 끝": str(meta.end.date()),
            "분석 일수": meta.period_days,
            "최대수요(kW)": meta.max_demand_kw,
            "부하율": diagnosis.pattern.load_factor,
            "기간 사용량(kWh)": meta.total_kwh,
        },
    }
    if structure is not None:
        payload["1단계 진단"]["현재 요금 구조(원)"] = {
            "기본요금": structure.base_with_power_factor_won,
            "전력량요금": structure.energy_won,
            "초과사용부가금": structure.excess_won,
            "합계": structure.total_won,
        }
        payload["1단계 진단"]["월별 명세"] = _records(
            localize(structure.monthly, index_name="월").reset_index()
        )
    keys = stage3.enabled(choices)
    payload["2단계 개선 수단"] = {"켠 개선안": [measure_title(measure(k).title) for k in keys]}
    if structure is None or not keys:
        return payload
    results = stage3.measure_results(ctx, choices)
    rows = results.standalone()
    payload["2단계 개선 수단"]["개선안별 요약"] = _records(standalone_frame(rows))
    computed = choices.combo_computed
    if computed:
        picked = tuple(k for k in computed if k in keys)
        combo = stage3.specs(ctx, choices, results, picked)
        if len(combo) > 1:
            comparison = stage3.compare(ctx, results, combo)
            combined = comparison.combinations[-1]
            simple = simple_sum_won(tuple(row for row in rows if row.key in set(picked)))
            actual = combined.annual_saving_won
            payload["3단계 개선안 조합"] = {
                "조합": [measure_title(measure(k).title) for k in picked],
                "단순 합(원/년)": money.truncate_won(simple),
                "합산효과(원/년)": money.truncate_won(actual),
                "차이(원/년)": money.truncate_won(actual - simple),
                "투자비(원)": combined.investment_won,
                "회수기간(년)": (
                    payback_years(combined.investment_won, actual)
                    if combined.investment_won is not None
                    else None
                ),
                "적용 선택요금": option_label(combined.selection.option),
            }
    return payload


def save(payload: dict[str, Any], path: Path) -> Path:
    if path.suffix.lower() != ".json":
        path = path.with_suffix(".json")
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return path
