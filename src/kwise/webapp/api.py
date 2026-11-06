"""화면이 부르는 파이썬 쪽 — pywebview ``js_api`` 로 건넨다.

**돌려주는 것은 JSON 으로 바뀌는 값뿐이다** (사전 · 목록 · 글 · 수). 결과는 ``blocks`` 가 적은
화면 조각 목록이고, 그래프는 Altair 차트의 Vega-Lite 사양이다 — 화면이 vega-embed 로 그린다.

pywebview 는 부름마다 따로 실(thread)을 띄운다 — 무거운 계산이 겹치지 않게 한 자물쇠로 묶는다.
**바깥에 보이는 속성은 메서드뿐이다** — 다리가 속성을 따라 들어가 훑으므로 상태는 밑줄 이름에 둔다.
"""

from __future__ import annotations

import base64
import datetime as dt
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
from kwise.ui.spec import measure

from . import blocks as b
from . import export, reports, rules, stage1, stage2, stage3, work
from .building import BuildingInfo, building_uses, narrow_contract_types
from .stage2 import Context  # 메서드 이름 ``stage2`` 가 클래스 안에서 모듈 이름을 가린다
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
    last_request: dict[str, Any] = field(default_factory=dict)
    #: 3단계 — 켠 수단 결과 한 벌과 계산한 조합의 (비교, 감도) 기억.
    results: stage3.Results | None = None
    combos: dict[tuple[str, ...], tuple[Any, Any]] = field(default_factory=dict)


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
        s.last_request = {**request, "columns": None}  # 2단계가 1단계를 다시 돌릴 때 쓴다
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

    # ------------------------------------------------------------------ 2단계

    def _ctx(self) -> Context | None:
        s = self._s
        if s.usage is None or s.diagnosis is None or s.quality is None or s.form is None:
            return None
        if s.diagnosis.structure is None:
            return None
        return Context(
            usage=s.usage,
            table=s.table,
            form=s.form,
            diagnosis=s.diagnosis,
            quality=s.quality,
            building=s.building,
        )

    def stage2(self) -> dict[str, Any]:
        """2단계 머리 — 계약 정보를 확정하기 전에는 ``ready`` 가 거짓이다."""
        ctx = self._ctx()
        if ctx is None:
            return {"ready": False}
        return _jsonable({"ready": True, **stage2.frame(ctx, self._s.choices)})

    def set_day(self, key: str, custom: str = "") -> None:
        """일일 곡선 대표일 — 역률 · 태양광 · ESS 의 하루 곡선이 모두 이 날을 쓴다."""
        self._s.choices.day_key = key
        try:
            self._s.choices.custom_day = dt.date.fromisoformat(custom) if custom else None
        except ValueError:
            self._s.choices.custom_day = None

    def toggle(self, key: str, on: bool) -> None:
        """카드를 켜고 끈다 — 켠 것만 3단계 조합에 들어간다."""
        (self._s.choices.enabled.add if on else self._s.choices.enabled.discard)(key)

    def card(self, key: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """카드 한 장을 계산한다 — ``params`` 는 그 카드의 입력칸 값이다."""
        with self._lock:
            try:
                rediagnosed = self._apply(key, params or {})
                ctx = self._ctx()
                if ctx is None:
                    return {"ok": False, "error": "1단계 결과가 없습니다."}
                return _jsonable(
                    {
                        "ok": True,
                        "rediagnosed": rediagnosed,
                        **stage2.card(ctx, self._s.choices, key),
                    }
                )
            except Exception as exc:
                traceback.print_exc()
                return {"ok": False, "error": f"계산하지 못했습니다. {exc}"}

    def _apply(self, key: str, params: dict[str, Any]) -> bool:
        """카드 입력칸 값을 고른 것에 담는다. 1단계를 다시 돌렸으면 참 (DR 쉬는 날)."""
        c = self._s.choices
        if key == "power_factor":
            if "target" in params:
                c.pf_target = _number(params.get("target"))
            if "investment" in params:
                c.pf_investment = _number(params.get("investment")) or None
        elif key == "demand_response":
            if "priced" in params:
                c.dr_priced = bool(params["priced"])
            if "price" in params:
                c.dr_price = _number(params.get("price")) or None
            if "off_days" in params:
                off = tuple(sorted(str(d) for d in params["off_days"] or ()))
                if off != c.dr_off_days:
                    # 쉬는 날은 1단계 진단이 읽는다 — 그 값으로 1단계부터 다시 (Streamlit 과 같다).
                    c.dr_off_days = off
                    self._analyse(self._s.last_request)
                    return True
        elif key == "ess":
            if "known" in params:
                c.ess_known = bool(params["known"])
            if "total" in params:
                c.ess_total = _number(params.get("total")) or None
        elif key == "solar":
            form = c.solar
            for name, value in (params.get("form") or {}).items():
                if hasattr(form, name):
                    setattr(form, name, value)
            if params.get("calculate"):
                form.ran_form = form.fields()
            if "surplus_use" in params:
                form.surplus_use = str(params["surplus_use"])
            if "surplus_price" in params:
                form.surplus_price = _number(params["surplus_price"])
            if "smp_price" in params:
                form.smp_price = _number(params["smp_price"])
        return False

    def solar_options(self, form: dict[str, Any]) -> dict[str, Any]:
        """태양광 칸을 고칠 때 — 밀도 라벨(환산 용량) · 방위 라벨 · 묵은 결과 여부만 다시."""
        ctx = self._ctx()
        if ctx is None:
            return {}
        for name, value in form.items():
            if hasattr(self._s.choices.solar, name):
                setattr(self._s.choices.solar, name, value)
        return _jsonable(stage2.solar_options(ctx, self._s.choices))

    # ------------------------------------------------------------------ 3단계

    def stage3(self) -> dict[str, Any]:
        """3단계를 연다 — 켠 수단을 2단계와 같은 함수로 다시 모은다 (몇 초 걸린다)."""
        with self._lock:
            try:
                return _jsonable(self._stage3())
            except Exception as exc:
                traceback.print_exc()
                return {"ready": False, "error": f"계산하지 못했습니다. {exc}"}

    def _stage3(self) -> dict[str, Any]:
        ctx = self._ctx()
        if ctx is None:
            return {"ready": False}
        c = self._s.choices
        _scope, scope_blocks = stage3.scope_blocks(c)
        results = stage3.measure_results(ctx, c)
        self._s.results = results
        self._s.combos = {}
        reviewed = stage3.enabled(c)
        out: dict[str, Any] = {
            "ready": True,
            "scope": scope_blocks,
            "summary": stage3.standalone_blocks(results.standalone(), results.solar_error),
            "reviewed": [{"key": k, "label": measure(k).label} for k in reviewed],
            "checked": {k: c.combo_checked.get(k, True) for k in reviewed},
            "stale": list(stage3.stale_lines(ctx, c)),
            "computed": list(c.combo_computed) if c.combo_computed is not None else None,
        }
        out.update(self._combined(ctx))
        return out

    def combine(self, checked: dict[str, bool], compute: bool) -> dict[str, Any]:
        """조합 체크 · 「합산효과 계산」 — 체크만 바꿀 때는 다시 비교하지 않고 흐림만 바꾼다."""
        with self._lock:
            try:
                ctx = self._ctx()
                if ctx is None or self._s.results is None:
                    return {"ok": False}
                c = self._s.choices
                c.combo_checked.update({k: bool(v) for k, v in checked.items()})
                if compute:
                    reviewed = stage3.enabled(c)
                    c.combo_computed = tuple(k for k in reviewed if c.combo_checked.get(k, True))
                return _jsonable({"ok": True, **self._combined(ctx)})
            except Exception as exc:
                traceback.print_exc()
                return {"ok": False, "error": f"계산하지 못했습니다. {exc}"}

    def _combined(self, ctx: Context) -> dict[str, Any]:
        """합산효과 조각 — 계산한 조합이 없으면 안내 한 줄."""
        s, c = self._s, self._s.choices
        reviewed = stage3.enabled(c)
        picked = tuple(k for k in reviewed if c.combo_checked.get(k, True))
        stale = list(stage3.stale_lines(ctx, c))
        computed = c.combo_computed
        with b.collect() as note:
            if computed is None and reviewed:
                b.note("개선안을 고르고 「합산효과 계산」 을 누르십시오.")
        if computed is None or s.results is None:
            return {"combined": note, "downloads_blocked": stale, "has_comparison": False}
        keys = tuple(k for k in computed if k in reviewed)
        combo = stage3.specs(ctx, c, s.results, keys)
        if len(combo) == 1:
            with b.collect() as note:
                b.note("요금에 영향을 주는 개선안을 하나 이상 고르면 합산효과를 계산합니다.")
            return {"combined": note, "downloads_blocked": stale, "has_comparison": False}
        if keys not in s.combos:
            comparison = stage3.compare(ctx, s.results, combo)
            s.combos[keys] = (comparison, stage3.sensitivity(ctx, s.results, combo))
        comparison, _sensitivity = s.combos[keys]
        selection_stale = keys != picked
        blocks = stage3.combined_blocks(
            ctx,
            c,
            s.results,
            s.results.standalone(),
            keys,
            comparison,
            selection_stale=selection_stale,
        )
        blocked = stale + ([stage3.SELECTION_STALE] if selection_stale else [])
        return {"combined": blocks, "downloads_blocked": blocked, "has_comparison": True}

    def make_report(self, kind: str, include_timeseries: bool = True) -> dict[str, Any]:
        """Excel(분석자용) · PPT(의사결정자용) — 「다운로드」 폴더에 저장하고 경로를 돌려준다."""
        with self._lock:
            try:
                path = reports.make(
                    self._s, self._ctx(), kind, include_timeseries=include_timeseries
                )
                return {"ok": True, "path": str(path), "kilobytes": path.stat().st_size / 1024}
            except Exception as exc:
                traceback.print_exc()
                return {"ok": False, "error": f"만들지 못했습니다. {exc}"}

    # ------------------------------------------------------------------ 파일 메뉴

    def save_work(self, screen: dict[str, Any], path: str | None = None) -> dict[str, Any]:
        """작업 저장 — 입력 · 고른 것 · 올린 사용량 파일. ``path`` 가 없으면 저장 창을 띄운다."""
        target = path or _dialog("save", f"{Path(self._s.filename).stem or 'kwise'}{work.SUFFIX}")
        if not target:
            return {"ok": False, "cancelled": True}
        s = self._s
        try:
            saved = work.dump(
                Path(target),
                data=s.data,
                filename=s.filename,
                date_column=s.date_column,
                energy_column=s.energy_column,
                screen=screen,
                choices=s.choices,
            )
            return {"ok": True, "path": str(saved)}
        except Exception as exc:
            traceback.print_exc()
            return {"ok": False, "error": f"저장하지 못했습니다. {exc}"}

    def open_work(self, path: str | None = None) -> dict[str, Any]:
        """작업 열기 — 세션을 그 파일 값으로 갈고, 화면 값과 카드 입력칸 값을 돌려준다.

        계산은 화면이 ``analyse`` 를 불러 다시 한다 (결과는 파일에 없다)."""
        target = path or _dialog("open")
        if not target:
            return {"ok": False, "cancelled": True}
        try:
            loaded = work.load(Path(target))
        except Exception as exc:
            return {"ok": False, "error": f"열지 못했습니다. {exc}"}
        with self._lock:
            stage2.forget()
            self._s = _Session(
                table=self._s.table,
                data=loaded["data"],
                filename=loaded["filename"],
                date_column=loaded["date_column"],
                energy_column=loaded["energy_column"],
                choices=loaded["choices"],
            )
        c = self._s.choices
        return _jsonable(
            {
                "ok": True,
                "path": str(target),
                "filename": self._s.filename,
                "saved_at": loaded["saved_at"],
                "screen": loaded["screen"],
                # 여섯 장 다 — 입력칸이 없는 카드도 켬 · 끔을 맞춰야 한다.
                "cards": {
                    "tariff_switch": None,
                    "contract": None,
                    "power_factor": {"target": c.pf_target, "investment": c.pf_investment or 0},
                    "demand_response": {
                        "priced": c.dr_priced,
                        "price": c.dr_price or 0,
                        "off_days": list(c.dr_off_days),
                    },
                    "ess": {"known": c.ess_known, "total": c.ess_total or 0},
                    "solar": c.solar.fields(),
                },
            }
        )

    def save_result(self, path: str | None = None) -> dict[str, Any]:
        """결과 저장 (JSON) — 지금 계산한 숫자를 그 시점 그대로."""
        with self._lock:
            ctx = self._ctx()
            if ctx is None:
                return {"ok": False, "error": "계약 정보를 확정하고 파일을 올린 뒤에 저장합니다."}
            name = f"{Path(self._s.filename).stem}_결과_{dt.datetime.now():%Y%m%d_%H%M}.json"
            target = path or _dialog("save", name, json_file=True)
            if not target:
                return {"ok": False, "cancelled": True}
            try:
                payload = export.build(ctx, self._s.choices, self._s.filename)
                saved = export.save(payload, Path(target))
                return {"ok": True, "path": str(saved)}
            except Exception as exc:
                traceback.print_exc()
                return {"ok": False, "error": f"저장하지 못했습니다. {exc}"}

    def alerts(self) -> list[b.Block]:
        """탭 위 알림 — 기준 데이터 손상 복구 · 만료 경고."""
        return rules.alerts()

    def rules_page(self) -> dict[str, Any]:
        """기준 데이터 창 한 벌."""
        return _jsonable(rules.page(self._s.choices))

    def rule_act(
        self, action: str, key: str, kind: str = "", value: rules.Value = None
    ) -> dict[str, Any]:
        """기준 데이터 고치기 — 성공하면 화면이 1단계부터 다시 돈다 (``ok``)."""
        with self._lock:
            try:
                result = rules.act(
                    action, key, rules.typed(kind, value) if action == "set" else None
                )
            except ValueError as exc:  # 형 검사 — 파일에 닿기 전에 멈췄다
                return {"ok": False, "message": str(exc), "blocks": []}
            except Exception as exc:
                traceback.print_exc()
                return {"ok": False, "message": str(exc), "blocks": []}
            if result.ok:
                self._s.results = None
                self._s.combos = {}
            return _jsonable(
                {
                    "ok": result.ok,
                    "message": result.message,
                    "blocks": rules.result_blocks(result),
                    "page": rules.page(self._s.choices),
                }
            )

    def rules_preview(self, origin: str) -> dict[str, Any]:
        """출고 상태로 — 차이 목록 먼저."""
        return _jsonable(rules.preview(origin))

    def set_ess_pricing(
        self, path: str, fixed: float | str | None, per_kwh: float | str | None
    ) -> dict[str, Any]:
        """ESS 단가 경로 · 두 계수 — 이 창을 연 동안만 쓴다. ESS 카드가 다시 계산한다."""
        rules.set_ess(self._s.choices, path, _number(fixed) or 0.0, _number(per_kwh) or 0.0)
        return _jsonable(rules.ess(self._s.choices))

    def open_path(self, path: str) -> dict[str, Any]:
        """파일이나 폴더를 그 파일에 정해진 프로그램으로 연다 (Excel · PowerPoint · 탐색기)."""
        try:
            os.startfile(path)
            return {"ok": True}
        except OSError as exc:
            return {"ok": False, "error": f"열지 못했습니다. {exc}"}

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


def _dialog(kind: str, name: str = "", *, json_file: bool = False) -> str | None:
    """파일 고르는 창 (Windows 기본 창) — 취소하면 ``None``. 처음 여는 폴더는 「다운로드」."""
    import webview

    kinds = {"save": webview.FileDialog.SAVE, "open": webview.FileDialog.OPEN}
    types = ("결과 JSON (*.json)",) if json_file else (f"kWise 작업 (*{work.SUFFIX})",)
    picked = webview.windows[0].create_file_dialog(
        kinds[kind],
        directory=str(reports.DOWNLOADS),
        save_filename=name,
        file_types=(*types, "모든 파일 (*.*)"),
    )
    if not picked:
        return None
    return str(picked if isinstance(picked, str) else picked[0])


def _number(value: object) -> float | None:
    """칸 값 — 비었으면 ``None``."""
    if value is None or value == "":
        return None
    return float(value)  # type: ignore[arg-type]


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
