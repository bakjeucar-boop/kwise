"""「파일 → 결과 저장」 — 지금 계산한 결과를 JSON 한 파일로 남긴다 (데스크톱만의 기능).

**새로 계산하지 않는다는 뜻이 아니다** — 화면이 쓰는 공용 함수를 같은 인자로 다시 불러
(``compare._measure_results`` · ``compare._compare``) 그 값을 적는다. 그래서 파일의 숫자는
화면과 같다. 표는 Excel 과 같은 출구(``display_frame``)를 지나 사람이 읽는 꼴로 적는다.

    입력       건물 정보 · 계약 정보 · 사용량 파일 이름 · 켠 개선안과 넣은 값
    1단계      분석 기간 · 최대수요 · 부하율 · 기간 사용량 · 현재 요금 구조 · 월별 명세
    2단계      개선안별 요약 (3단계 표와 같은 표)
    3단계      「합산효과 계산」 을 누른 조합이 있으면 단순 합 · 합산효과 · 차이 · 회수기간
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

from . import compare

if TYPE_CHECKING:
    from .measures import Choices, Context

#: 저장 자리 — 산출물과 같은 「다운로드」 폴더.
DOWNLOADS = compare.DOWNLOADS


def _plain(value: object) -> object:
    """JSON 에 그대로 들어가는 값으로 — 수 · 글 · 참거짓 · 빈 값 말고는 글로 적는다.

    ``to_json`` 에 맡기면 월(``Period``) 같은 칸에서 「Maximum recursion level reached」 로 죽는다.
    """
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, (int, float)):
        return None if pd.isna(value) else value
    if hasattr(value, "item"):  # numpy 수
        return _plain(value.item())
    return None if pd.isna(value) else str(value)


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """표를 사람이 읽는 줄 목록으로 — Excel 과 같은 자릿수."""
    shown = display_frame(frame)
    return [
        {str(column): _plain(value) for column, value in row.items()}
        for row in shown.to_dict(orient="records")
    ]


def build(ctx: Context, choices: Choices, filename: str) -> dict[str, Any]:
    """저장할 내용 한 벌 (일꾼 스레드에서 돈다 — 켠 개선안을 다시 모으는 데 몇 초 걸린다)."""
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

    enabled = compare._enabled(choices)
    payload["2단계 개선 수단"] = {"켠 개선안": [measure_title(_title(k)) for k in enabled]}
    if structure is None or not enabled:
        return payload
    results = compare._measure_results(ctx, choices)
    rows = results.standalone()
    payload["2단계 개선 수단"]["개선안별 요약"] = _records(standalone_frame(rows))

    computed = choices.combo_computed
    if computed:
        picked = tuple(key for key in computed if key in enabled)
        specs = compare._specs(ctx, choices, results, picked)
        if len(specs) > 1:
            comparison = compare._compare(ctx, results, specs)
            combined = comparison.combinations[-1]
            chosen = tuple(row for row in rows if row.key in set(picked))
            simple = simple_sum_won(chosen)
            actual = combined.annual_saving_won
            payback = (
                payback_years(combined.investment_won, actual)
                if combined.investment_won is not None
                else None
            )
            payload["3단계 개선안 조합"] = {
                "조합": [measure_title(_title(k)) for k in picked],
                "단순 합(원/년)": money.truncate_won(simple),
                "합산효과(원/년)": money.truncate_won(actual),
                "차이(원/년)": money.truncate_won(actual - simple),
                "투자비(원)": combined.investment_won,
                "회수기간(년)": payback,
                "적용 선택요금": option_label(combined.selection.option),
            }
    return payload


def _title(key: str) -> str:
    from kwise.ui.spec import measure

    return measure(key).title


def save(payload: dict[str, Any]) -> Path:
    """「다운로드」 폴더에 날짜 · 시각 붙은 이름으로 쓴다 (Excel 이 열고 있어도 안 겹친다)."""
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    path = DOWNLOADS / f"kwise_result_{dt.datetime.now():%Y%m%d_%H%M%S}.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return path
