"""화면이 부르는 파이썬 쪽 — pywebview ``js_api`` 로 건넨다.

**돌려주는 것은 JSON 으로 바뀌는 값뿐이다** (사전 · 목록 · 글 · 수). 결과는 ``blocks`` 가 적은
화면 조각 목록이고, 그래프는 Altair 차트의 Vega-Lite 사양이다 — 화면이 vega-embed 로 그린다.

pywebview 는 부름마다 따로 실(thread)을 띄운다 — 무거운 계산이 겹치지 않게 한 자물쇠로 묶는다.
**바깥에 보이는 속성은 메서드뿐이다** — 다리가 속성을 따라 들어가 훑으므로 상태는 밑줄 이름에 둔다.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kwise import __version__
from kwise.diagnose import Diagnosis
from kwise.io import UsageData, load_usage_bytes
from kwise.io.columns import ColumnDetection
from kwise.pv import list_provinces, list_sigungu
from kwise.quality import DEFAULT_OPERATING_HOURS, QualityReport
from kwise.report import notices
from kwise.tariff import TariffTable, load_tariff
from kwise.tariff.power_factor import leading_charge_applies
from kwise.ui.labels import option_label
from kwise.ui.pipeline import (
    ContractChoice,
    ContractForm,
    contract_type_choices,
    daily_temperature,
    default_lagging_pct,
    diagnose_usage,
    load_quality,
    option_choices,
    voltage_choices,
)

from . import blocks as b
from . import stage1
from .building import BuildingInfo, building_uses, narrow_contract_types
from .state import Choices

#: 자가 점검 — 이 환경 변수에 경로를 주면 화면이 다 선 뒤 그 상태를 거기에 적는다 (시험용).
SELFTEST_ENV = "KWISE_SELFTEST"


def _jsonable(value: dict[str, Any]) -> dict[str, Any]:
    """다리가 JSON 으로 못 바꾸는 값(날짜 따위)을 글로 — 못 바꾸면 화면에 아무것도 안 온다."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


@dataclass
class _Session:
    """창 하나의 작업 — 입력 · 고른 것 · 마지막 결과."""

    table: TariffTable
    data: bytes | None = None
    filename: str = ""
    form: ContractForm | None = None
    date_column: str | None = None
    energy_column: str | None = None
    building: BuildingInfo = field(default_factory=BuildingInfo)
    choices: Choices = field(default_factory=Choices)
    usage: UsageData | None = None
    quality: QualityReport | None = None
    diagnosis: Diagnosis | None = None


class Api:
    """창 하나의 다리. 메서드 이름이 곧 화면이 부르는 이름이다 (``pywebview.api.<이름>``)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._s = _Session(table=load_tariff())

    # ------------------------------------------------------------------ 점검

    def ready(self, info: dict[str, Any]) -> None:
        """화면이 다 섰다는 알림 — 시험할 때만 그 상태를 파일로 남긴다 (평소에는 안 한다)."""
        target = os.environ.get(SELFTEST_ENV)
        if target:
            Path(target).write_text(
                json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8"
            )

    # ------------------------------------------------------------------ 고를 것

    def options(self) -> dict[str, Any]:
        """처음 한 번 — 계약종별 · 용도 · 시도 목록과 기본값."""
        types = contract_type_choices(self._s.table)
        return {
            "version": __version__,
            "types": [{"value": c.key, "label": c.label} for c in types],
            "uses": [{"value": "", "label": "선택 안 함"}]
            + [{"value": u.key, "label": u.label} for u in building_uses()],
            "provinces": [{"value": "", "label": "선택 안 함"}]
            + [{"value": p, "label": p} for p in list_provinces()],
            "lagging_default": default_lagging_pct(),
            "hours_default": list(DEFAULT_OPERATING_HOURS),
            "school_notice": notices.SCHOOL_HIGH_VOLTAGE_NOTICE,
        }

    def contract_types(self, use_key: str) -> list[dict[str, str]]:
        """용도로 좁힌 계약종별 — 좁히기이지 판정이 아니다. 못 좁히면 전부."""
        narrowed = narrow_contract_types(contract_type_choices(self._s.table), use_key)
        return [{"value": c.key, "label": c.label} for c in narrowed]

    def voltages(self, type_key: str) -> list[dict[str, str]]:
        choice = self._choice(type_key)
        return [
            {"value": k, "label": label}
            for k, label in voltage_choices(self._s.table, choice.contract_type)
        ]

    def tariff_options(self, type_key: str, voltage: str) -> dict[str, Any]:
        choice = self._choice(type_key)
        opts = option_choices(self._s.table, choice.contract_type, voltage)
        return {
            "options": [{"value": o, "label": option_label(o)} for o in opts],
            # 저압은 야간 진상역률 요금 대상이 아니다 — 결과에 안 닿는 칸을 그리지 않는다.
            "leading": leading_charge_applies(voltage),
        }

    def sigungu(self, province: str) -> list[dict[str, str]]:
        return (
            [{"value": r.key, "label": r.name} for r in list_sigungu(province)] if province else []
        )

    def _choice(self, type_key: str) -> ContractChoice:
        return next(c for c in contract_type_choices(self._s.table) if c.key == type_key)

    # ------------------------------------------------------------------ 1단계

    def upload(self, name: str, content_b64: str) -> dict[str, Any]:
        """올린 파일 — 화면이 바이트를 base64 로 건넨다. 새 파일은 열 판정부터 자동."""
        self._s.data = base64.b64decode(content_b64)
        self._s.filename = name
        self._s.date_column = self._s.energy_column = None
        return {"ok": True, "filename": name, "kilobytes": len(self._s.data) / 1024}

    def analyse(self, request: dict[str, Any]) -> dict[str, Any]:
        """1단계를 돌린다 — 건물 · 계약 · 열 지정을 받아 결과 조각을 돌려준다."""
        with self._lock:
            try:
                return _jsonable(self._analyse(request))
            except Exception as exc:  # 화면에 사유를 낸다 — 다리가 예외를 삼키면 아무 말이 없다
                traceback.print_exc()
                return {"ok": False, "stage": "error", "error": str(exc)}

    def _analyse(self, request: dict[str, Any]) -> dict[str, Any]:
        s = self._s
        s.building = _building(request.get("building") or {})
        s.form = self._form(request.get("form"))
        columns = request.get("columns") or {}
        if columns.get("reset"):
            s.date_column = s.energy_column = None
        elif columns.get("date") or columns.get("energy"):
            s.date_column, s.energy_column = columns.get("date"), columns.get("energy")
        tentative = stage1.tentative(s.table, s.form)
        if s.data is None:
            return {"ok": True, "stage": "no_file", "tentative": tentative}
        try:
            usage = load_usage_bytes(
                s.data, s.filename, date_column=s.date_column, energy_column=s.energy_column
            )
        except Exception as exc:
            return {
                "ok": False,
                "stage": "load",
                "error": f"파일을 읽지 못했습니다. {exc}",
                "overridden": bool(s.date_column or s.energy_column),
                "tentative": tentative,
            }
        try:
            quality = load_quality(usage, contract_kw=s.form.contract_kw if s.form else None)
            diagnosis = diagnose_usage(
                usage,
                s.table,
                s.form,
                quality=quality,
                operating_hours=s.building.operating_hours,
                dr_off_days=s.choices.dr_off_days,
                jeju=s.building.jeju,
            )
            temperature = daily_temperature(usage, s.building.region_key)
        except Exception as exc:
            return {
                "ok": False,
                "stage": "compute",
                "error": f"계산하지 못했습니다. {exc}",
                "tentative": tentative,
            }
        s.usage, s.quality, s.diagnosis = usage, quality, diagnosis
        detection = usage.meta.columns
        return {
            "ok": True,
            "stage": "done",
            "filename": s.filename,
            "tentative": tentative,
            "columns": {
                "describe": detection.describe(),
                "names": list(detection.columns)
                or [detection.date_column, detection.energy_column],
                "date": detection.date_column,
                "energy": detection.energy_column,
                "candidates": _candidates(detection),
            },
            "blocks": stage1.results(usage, diagnosis, temperature, s.building),
            # 2 · 3단계는 계약 정보를 확정해야 선다.
            "ready": s.form is not None and diagnosis.structure is not None,
        }

    def _form(self, raw: dict[str, Any] | None) -> ContractForm | None:
        if not raw:
            return None
        choice = self._choice(str(raw["type"]))
        voltage = str(raw["voltage"])
        lead = raw.get("leading")
        return ContractForm(
            contract_type=choice.contract_type,
            voltage=voltage,
            option=str(raw["option"]),
            contract_kw=float(raw["contract_kw"]) if raw.get("contract_kw") else None,
            power_factor_pct=(
                float(raw["lagging"])
                if raw.get("lagging") not in (None, "")
                else default_lagging_pct()
            ),
            leading_power_factor_pct=(
                float(lead)
                if lead is not None and lead != "" and leading_charge_applies(voltage)
                else None
            ),
            school_exception=choice.school_exception,
        )


def _building(raw: dict[str, Any]) -> BuildingInfo:
    hours = raw.get("hours") or list(DEFAULT_OPERATING_HOURS)
    start, end = int(hours[0]), int(hours[1])
    return BuildingInfo(
        region_key=str(raw.get("sigungu") or "") if raw.get("province") else "",
        name=str(raw.get("name") or ""),
        use_key=str(raw.get("use") or ""),
        floor_area_m2=float(raw["area"]) if raw.get("area") else None,
        built_year=int(raw["year"]) if raw.get("year") else None,
        # 시작이 끝보다 늦거나 같으면 뜻이 없다 — 기본값으로 (Streamlit ``_hours``).
        operating_hours=(start, end) if start < end else DEFAULT_OPERATING_HOURS,
    )


def _candidates(detection: ColumnDetection) -> list[b.Block]:
    import pandas as pd

    with b.collect() as page:
        rows = [
            {"역할": role, "열": item.column, "점수": item.score, "근거": item.reason}
            for role, ranked in (
                ("검침일", detection.date_candidates),
                ("전력량", detection.energy_candidates),
            )
            for item in ranked
        ]
        if rows:
            b.table(pd.DataFrame(rows), compact=True)
    return page
