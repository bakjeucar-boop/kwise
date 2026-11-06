"""작업 파일(``*.kwise``) — 저장하고 다시 불러온다.

**올린 사용량 파일을 통째로 담는다** (base64) — 다른 PC 에서 열어도 원본 파일이 없어도 된다.
결과(숫자)는 담지 않는다 — 불러올 때 같은 입력으로 다시 계산한다. 기준 데이터가 그 사이에
바뀌었으면 새 기준으로 계산되므로, 결과를 그 시점 그대로 남기려면 「결과 저장 (JSON)」 을 쓴다.

    {
      "format": "kwise-work", "version": 1, "app": "kWise 1.0.2", "saved_at": "…",
      "usage": {"name": "…csv", "base64": "…", "date_column": null, "energy_column": null},
      "screen": {...화면 입력칸 값 — 옆단 · 계약 정보 · 확정한 값...},
      "choices": {...2 · 3단계에서 고른 것...}
    }
"""

from __future__ import annotations

import base64
import datetime as dt
import json
from dataclasses import fields
from pathlib import Path
from typing import Any

from kwise import __version__

from .state import Choices, SolarForm

FORMAT = "kwise-work"
VERSION = 1
SUFFIX = ".kwise"


def _choices_dict(choices: Choices) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in fields(choices):
        value = getattr(choices, f.name)
        if isinstance(value, SolarForm):
            value = {sf.name: getattr(value, sf.name) for sf in fields(value)}
        elif isinstance(value, set):
            value = sorted(value)
        elif isinstance(value, tuple):
            value = list(value)
        elif isinstance(value, dt.date):
            value = value.isoformat()
        out[f.name] = value
    return out


def _choices_from(raw: dict[str, Any]) -> Choices:
    """모르는 칸은 버리고, 없는 칸은 기본값 — 판이 바뀌어도 열린다."""
    choices = Choices()
    names = {f.name for f in fields(choices)}
    for name, value in raw.items():
        if name not in names:
            continue
        if name == "solar":
            form = SolarForm()
            form_names = {f.name for f in fields(form)}
            for key, item in (value or {}).items():
                if key in form_names:
                    setattr(form, key, item)
            value = form
        elif name == "enabled":
            value = set(value or ())
        elif name in ("dr_off_days", "combo_computed") and value is not None:
            value = tuple(value)
        elif name == "custom_day" and value:
            value = dt.date.fromisoformat(value)
        setattr(choices, name, value)
    return choices


def dump(
    path: Path,
    *,
    data: bytes | None,
    filename: str,
    date_column: str | None,
    energy_column: str | None,
    screen: dict[str, Any],
    choices: Choices,
) -> Path:
    """작업 한 벌을 쓴다 — 이름에 ``.kwise`` 가 없으면 붙인다."""
    if path.suffix.lower() != SUFFIX:
        path = path.with_suffix(SUFFIX)
    payload = {
        "format": FORMAT,
        "version": VERSION,
        "app": f"kWise {__version__}",
        "saved_at": dt.datetime.now().isoformat(timespec="seconds"),
        "usage": {
            "name": filename,
            "base64": base64.b64encode(data).decode("ascii") if data else None,
            "date_column": date_column,
            "energy_column": energy_column,
        },
        "screen": screen,
        "choices": _choices_dict(choices),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def load(path: Path) -> dict[str, Any]:
    """작업 한 벌을 읽는다 — 사용량 바이트 · 열 지정 · 화면 값 · 고른 것."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("format") != FORMAT:
        raise ValueError("kWise 작업 파일이 아닙니다.")
    if int(payload.get("version", 0)) > VERSION:
        raise ValueError("더 새로운 kWise 에서 저장한 작업 파일입니다 — 새 판을 받으십시오.")
    usage = payload.get("usage") or {}
    encoded = usage.get("base64")
    return {
        "data": base64.b64decode(encoded) if encoded else None,
        "filename": str(usage.get("name") or ""),
        "date_column": usage.get("date_column"),
        "energy_column": usage.get("energy_column"),
        "screen": payload.get("screen") or {},
        "choices": _choices_from(payload.get("choices") or {}),
        "saved_at": payload.get("saved_at", ""),
    }
