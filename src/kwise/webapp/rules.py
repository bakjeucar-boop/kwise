"""「파일 → 기준 데이터」 — 데스크톱 ``desktop\\rules.py`` 를 이 판(조각 + 화면)으로 옮긴 것.

**값을 고치는 것은 공용 ``kwise.rules`` 의 함수다** (``set_value`` · ``confirm`` · ``restore_*``).
여기는 부르기만 하고, 저장에 성공하면 기준 데이터를 다시 읽는다 — 계산은 화면이 새로 돌린다.
고치는 파일은 Streamlit 과 같다 — 저장소에서 띄우면 저장소 ``data\\``, exe 면 exe 옆 ``data\\``.

ESS 단가 경로 · 두 계수는 Streamlit 처럼 **이 창을 연 동안만** 쓴다(파일에 안 쓴다) — ``Choices``.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from kwise.measures import PRICING_BANDS, PRICING_FORMULA, load_ess_cost_model
from kwise.pv.archive import archive_status
from kwise.report.frames import capacity_band_frame
from kwise.rules import (
    EditResult,
    RuleOrigin,
    assumptions,
    confirm,
    describe_items,
    diff_from_defaults,
    expiry_warnings,
    read_history,
    reload_rules,
    restore_defaults,
    restore_item,
    restore_previous,
    rules,
    set_value,
)
from kwise.tariff import load_tariff
from kwise.ui.rules_view import (
    RuleRow,
    build_rows,
    count_rows,
    diff_frame,
    header_text,
    weather_panel,
)
from kwise.ui.text import bullet_list

from . import blocks as b
from . import stage2
from .state import Choices

ORIGINS = {"statutory": RuleOrigin.STATUTORY, "judgement": RuleOrigin.JUDGEMENT}
#: 화면에서 고치는 값 — 목록 · 사전은 고치지 않는다.
Value = bool | int | float | str | None


def alerts() -> list[b.Block]:
    """모든 화면 위에 거는 알림 — 손상 복구는 조용히 넘기지 않고, 만료 경고도 함께 본다."""
    with b.collect() as page:
        for ruleset in (rules(), assumptions()):
            if ruleset.recovered_from:
                b.blocked(
                    f"기준 데이터 **{ruleset.origin.filename}** 를 읽지 못해 "
                    f"`{ruleset.recovered_from}` 에서 복구했습니다. "
                    "값이 최신인지 「기준 데이터」 에서 확인하십시오."
                )
        warnings = expiry_warnings()
        if warnings:
            with b.expansion(f"⚠ 기준 데이터 확인 필요 {len(warnings)}건"):
                b.text(bullet_list(item.message() for item in warnings))
    return page


def page(choices: Choices) -> dict[str, Any]:
    """기준 데이터 한 벌 — 읽기 전용 조각과 고칠 줄(값 · 형 · 단추)을 함께."""
    warnings = expiry_warnings(include_weather=False)
    rows = build_rows(describe_items(), warnings)
    table = load_tariff()
    with b.collect() as head:
        b.caption(
            "법령 유래 값과 우리 판단값을 갈라 둡니다. 법령 항목은 함부로 고치지 말고, "
            "판단값은 조정하라고 있는 것입니다."
        )
        if warnings:
            b.caption(f"확인이 필요한 항목 {len(warnings)}건")
        b.h3(header_text(count_rows(rows)))
        with b.card():
            b.text(f"**요금표** — {table.source or '출처 미기재'} ({table.effective_date} 시행)")
            b.caption(
                "실제 청구서와 대조해 확인했습니다."
                if table.verified
                else "아직 실제 청구서와 대조하지 않았습니다. 단가는 공표 자료 그대로이며, "
                "청구서로 한 번 맞춰 보면 확신이 올라갑니다."
            )
    with b.collect() as tail:
        _weather()
        _history()
    return {
        "head": head,
        "ess": ess(choices),
        "origins": [
            {"value": k, "label": str(o), "filename": o.filename} for k, o in ORIGINS.items()
        ],
        "rows": [_row(row) for row in rows],
        "tail": tail,
    }


def ess(choices: Choices) -> dict[str, Any]:
    """ESS 투자비 단가 — 고른 경로 · 두 계수와 그 계수의 kWh 구간 표."""
    shipped = load_ess_cost_model()
    model = stage2.ess_model(choices)
    with b.collect() as detail:
        if model.fixed_won != shipped.fixed_won or model.per_kwh_won != shipped.per_kwh_won:
            b.caution("계수 조정됨 — 자동 산출값이 아닙니다.")
        b.table(capacity_band_frame(model))
        if model.pricing_path == PRICING_BANDS:
            b.caption("구간 단가로 산정합니다. 구간 안에서는 kWh당 단가가 일정합니다.")
    return {
        "formula": shipped.formula,
        "provenance": shipped.provenance,
        "paths": [PRICING_FORMULA, PRICING_BANDS],
        "path": model.pricing_path,
        "fixed": model.fixed_won,
        "per_kwh": model.per_kwh_won,
        "detail": detail,
    }


def set_ess(choices: Choices, path: str, fixed: float, per_kwh: float) -> None:
    """출고값과 같으면 「안 만졌다」 — Streamlit 이 0 · 미입력을 기본값으로 읽는 것과 같다."""
    shipped = load_ess_cost_model()
    choices.ess_path = path
    choices.ess_fixed = None if fixed == shipped.fixed_won else (fixed or None)
    choices.ess_per_kwh = None if per_kwh == shipped.per_kwh_won else (per_kwh or None)
    choices.ess_target = 0.0  # 단가가 바뀌면 ESS 목표를 다시 고른다


def act(action: str, key: str, value: Value = None) -> EditResult:
    """줄 단추 · 원복 단추 — 공용 함수 하나씩. 성공하면 기준 데이터를 다시 읽는다."""
    if action == "set":
        result = set_value(key, value)
    elif action == "confirm":
        result = confirm(key)
    elif action == "item":
        result = restore_item(key)
    elif action == "previous":
        result = restore_previous(ORIGINS[key])
    elif action == "defaults":
        result = restore_defaults(ORIGINS[key], confirmed=True)
    else:
        raise ValueError(action)
    if result.ok:
        reload_rules()  # Streamlit ``clear_calc_cache`` 의 첫 줄과 같다
        stage2.forget()
    return result


def preview(origin_key: str) -> dict[str, Any]:
    """출고 상태로 — 실행하지 않고 차이 목록을 먼저 보인다."""
    origin = ORIGINS[origin_key]
    result = restore_defaults(origin, confirmed=False)
    with b.collect() as shown:
        b.caution(result.message)
        diffs = diff_from_defaults(origin)
        if diffs:
            b.table(diff_frame(diffs))
    return {"blocks": shown, "origin": origin_key}


def result_blocks(result: EditResult) -> list[b.Block]:
    """실패 사유의 세부 목록 — 사유 한 줄(``message``)은 화면이 그 줄 아래에 따로 낸다."""
    with b.collect() as shown:
        if not result.ok and result.issues:
            b.text(bullet_list(str(issue) for issue in result.issues))
    return shown


def _row(row: RuleRow) -> dict[str, Any]:
    value = row.view.value
    kind = (
        "bool"
        if isinstance(value, bool)
        else "int"
        if isinstance(value, int)
        else "float"
        if isinstance(value, float)
        else "str"
        if isinstance(value, str)
        else "other"  # 목록 · 사전은 화면에서 고치지 않는다 — 엑셀 왕복 경로를 쓴다.
    )
    marks = ((row.needs_check, "⚠ 확인 필요"), (row.changed, "● 기본값과 다름"))
    flags = [f for on, f in marks if on]
    return {
        "key": row.key,
        "badge": "🟥 법령" if row.is_statutory else "🟦 판단",
        "label": row.view.label,
        "flags": " · ".join(flags),
        "source": row.source_text,
        "tip": _source_tip(row),
        "verified": f"확인일 — {row.verified_text}",
        "note": f"비고 — {row.view.note}" if row.view.note else "",
        "kind": kind,
        "value": value if kind != "other" else repr(value),
        "changed": row.changed,
        "default": repr(row.view.default_value),
    }


def typed(kind: str, value: Value) -> bool | int | float | str:
    """형을 지켜 되돌려 준다 (정수 항목을 실수로 저장하지 않는다)."""
    if kind == "bool":
        return bool(value)
    if value is None or value == "":
        raise ValueError("값을 넣으십시오.")
    try:
        if kind == "int":
            return int(value)
        if kind == "float":
            return float(value)
    except ValueError:
        raise ValueError("숫자를 넣으십시오.") from None
    return str(value)


def _weather() -> None:
    b.h2("기상 데이터 현황")
    panel = weather_panel(archive_status())
    b.metrics(
        [
            ("확보 격자", f"{panel.cell_count}개"),
            ("연도", panel.year_text),
            ("용량", f"{panel.megabytes:,.1f} MB"),
            ("최종 취득", panel.fetched_text),
        ]
    )
    b.caption(
        "필요한 격자만 받아 두는 구조라 일부만 있어도 정상입니다. 없는 격자는 "
        f"조회 시점에 Open-Meteo 에서 받습니다. 저장 위치 — {panel.root}."
    )


def _history() -> None:
    with b.expansion("변경 이력"):
        records = read_history()
        if not records:
            b.text("아직 변경이 없습니다.")
            return
        b.table(
            pd.DataFrame(
                [
                    {
                        "시각": record.changed_at,
                        "파일": record.file,
                        "항목": record.key,
                        "이전": str(record.before),
                        "이후": str(record.after),
                        "동작": record.action,
                        "비고": record.note,
                    }
                    for record in reversed(records)
                ]
            )
        )


def _source_tip(row: RuleRow) -> str:
    """근거 풀이 — 조문 번호와 시행일, 확인처 이름 (주소는 버린다 · Streamlit 과 같다)."""
    parts = [row.source_text]
    link = row.link
    index = link.find("http")
    where = (link[:index] if index >= 0 else link).strip().rstrip("—·-").strip()
    if where:
        parts.append(f"확인처 — {where}")
    if row.view.note:
        parts.append(row.view.note)
    return " · ".join(parts)
