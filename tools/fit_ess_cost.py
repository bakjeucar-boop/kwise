"""ESS 투자비 모델 재적합 (요구사항서 7.6).

    .venv\\Scripts\\python.exe tools\\fit_ess_cost.py
    .venv\\Scripts\\python.exe tools\\fit_ess_cost.py --check

``data\\ess_cost_cases.json`` 의 조달 사례로 설비비 회귀를 다시 맞춰
``data\\ess_cost_model.json`` 에 쓴다. **계수를 코드에 손으로 적지 않는다** —
사례가 늘면 이 스크립트를 다시 돌리는 것이 갱신 절차다.

    설비비(원) = 고정비 + 용량단가 × 용량(kWh)

**kW 는 설명 변수가 아니다.** PCS 가 50·75·100 kW 로 흩어져 있는데도 kWh 만으로
R² 가 0.999 를 넘는다. 넣으면 자유도만 잃는다 (사례가 넷뿐이다).

**단일 kWh당 단가로 표현하지 않는다.** 고정비가 1억을 넘어 총액÷용량이 50 kWh
440만원, 400 kWh 146만원으로 세 배 변한다. 그 값을 단가라 부르면 작은 설비의
경제성이 실제보다 나쁘게, 큰 설비가 좋게 보인다.

홀드아웃(한 건씩 빼고 예측)을 함께 낸다. 사례가 넷이라 R² 만으로는 과적합을
가릴 수 없다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

CASES_PATH = PROJECT_ROOT / "data" / "ess_cost_cases.json"
MODEL_PATH = PROJECT_ROOT / "data" / "ess_cost_model.json"
SMALL_BAND_MAX_KWH = 200.0
COEFFICIENT_UNIT_WON = 1_000
"""계수를 반올림할 단위 (14세션 1절).

**적합 결과를 그대로 두면 화면에 106,924,633원 같은 값이 나온다.** 다섯째 자리까지
맞는 척하는 숫자인데 사례가 넷이다. 천 원 단위로 반올림하면 재현 오차가
±1.5% 안이라 사실상 달라지지 않으면서, 사람이 읽고 옮겨 적을 수 있는 값이 된다.
"""


def round_coefficient(value: float) -> float:
    """천 원 단위 반올림. 절사가 아니라 **반올림**이다 — 계수는 표시값이 아니다."""
    return float(round(value / COEFFICIENT_UNIT_WON) * COEFFICIENT_UNIT_WON)


console = Console()


@dataclass(frozen=True)
class Case:
    key: str
    label: str
    power_kw: float
    capacity_kwh: float
    equipment_won: float
    electrical_won: float | None
    install: str
    category: str
    note: str = ""

    def electrical_ex_vat(self, vat_rate: float, vat_included: bool) -> float | None:
        if self.electrical_won is None:
            return None
        return self.electrical_won / (1.0 + vat_rate) if vat_included else self.electrical_won


def load_cases(path: Path) -> tuple[list[Case], float]:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    vat_rate = float(payload.get("vat_rate", 0.1))
    cases: list[Case] = []
    for raw in payload["cases"]:
        electrical = raw.get("electrical_won")
        vat_included = bool(raw.get("vat_included", False))
        case = Case(
            key=str(raw["key"]),
            label=str(raw["label"]),
            power_kw=float(raw["power_kw"]),
            capacity_kwh=float(raw["capacity_kwh"]),
            equipment_won=float(raw["equipment_won"]),
            electrical_won=None if electrical is None else float(electrical),
            install=str(raw.get("install", "outdoor")),
            category=str(raw.get("category", "reference")),
            note=str(raw.get("note", "")),
        )
        # 부가세 포함 값은 **회귀 전에** 뺀다. 섞으면 기울기가 10% 튄다.
        cases.append(
            Case(
                key=case.key,
                label=case.label,
                power_kw=case.power_kw,
                capacity_kwh=case.capacity_kwh,
                equipment_won=case.equipment_won,
                electrical_won=case.electrical_ex_vat(vat_rate, vat_included),
                install=case.install,
                category=case.category,
                note=case.note,
            )
        )
    return cases, vat_rate


def fit(points: list[tuple[float, float]]) -> tuple[float, float, float]:
    """최소제곱 직선. ``(고정비, 용량단가, R²)`` 를 낸다."""
    if len(points) < 2:
        raise ValueError("회귀에는 두 건 이상이 필요합니다.")
    count = float(len(points))
    mean_x = sum(x for x, _ in points) / count
    mean_y = sum(y for _, y in points) / count
    sxx = sum((x - mean_x) ** 2 for x, _ in points)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in points)
    if sxx == 0:
        raise ValueError("용량이 모두 같아 기울기를 낼 수 없습니다.")
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    ss_tot = sum((y - mean_y) ** 2 for _, y in points)
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in points)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
    return intercept, slope, r2


def holdout(points: list[tuple[float, float]]) -> list[dict[str, float]]:
    """한 건씩 빼고 나머지로 맞춰 그 한 건을 예측한다."""
    rows: list[dict[str, float]] = []
    for index, (x, y) in enumerate(points):
        rest = [item for position, item in enumerate(points) if position != index]
        intercept, slope, _r2 = fit(rest)
        predicted = intercept + slope * x
        rows.append(
            {
                "capacity_kwh": x,
                "actual_won": y,
                "predicted_won": predicted,
                "error_ratio": (predicted - y) / y if y else 0.0,
            }
        )
    return rows


def rounding_check(
    points: list[tuple[float, float]], fixed: float, per_kwh: float
) -> list[dict[str, float]]:
    """반올림한 계수로 사례를 다시 맞춰 본다. **±1.5% 안이어야 쓴다.**"""
    return [
        {
            "capacity_kwh": x,
            "actual_won": y,
            "predicted_won": fixed + per_kwh * x,
            "error_ratio": ((fixed + per_kwh * x) - y) / y if y else 0.0,
        }
        for x, y in points
    ]


def build_model(cases: list[Case], vat_rate: float) -> dict[str, Any]:
    regression = [case for case in cases if case.category == "regression"]
    points = [(case.capacity_kwh, case.equipment_won) for case in regression]
    raw_intercept, raw_slope, r2 = fit(points)
    # **천 원 단위로 반올림해 저장한다** (14세션 1절). 적합값을 원 단위로 두면
    # 사례 넷으로 다섯째 자리까지 맞는 척하는 숫자가 화면에 나간다.
    intercept = round_coefficient(raw_intercept)
    slope = round_coefficient(raw_slope)

    outdoor = [
        case for case in regression if case.install == "outdoor" and case.electrical_won is not None
    ]
    small = [case for case in outdoor if case.capacity_kwh <= SMALL_BAND_MAX_KWH]
    large = [case for case in outdoor if case.capacity_kwh > SMALL_BAND_MAX_KWH]
    if not small or not large:
        raise ValueError("전기공사 구간을 만들 사례가 모자랍니다.")
    small_values = [case.electrical_won or 0.0 for case in small]
    large_values = [case.electrical_won or 0.0 for case in large]

    # 실내 설치 비율 — 턴키 사례에서 설비비 예측치를 빼 설치비를 역산한다.
    indoor_ratio: float | None = None
    indoor_basis = ""
    turnkey = [case for case in cases if case.install == "indoor" and case.category == "reference"]
    if turnkey:
        case = turnkey[0]
        implied = case.equipment_won - (intercept + slope * case.capacity_kwh)
        outdoor_at_same = sum(small_values) / len(small_values)
        if implied > 0 and outdoor_at_same > 0:
            indoor_ratio = implied / outdoor_at_same
            indoor_basis = f"{case.label} 턴키 1건"

    return {
        "schema_version": "0.1",
        "fitted_on": dt.date.today().isoformat(),
        "source_file": CASES_PATH.name,
        "vat_rate": vat_rate,
        "equipment": {
            "fixed_won": intercept,
            "per_kwh_won": slope,
            "raw_fixed_won": round(raw_intercept, 2),
            "raw_per_kwh_won": round(raw_slope, 2),
            "rounded_to_won": COEFFICIENT_UNIT_WON,
            "r2": round(r2, 5),
            "sample_size": len(points),
            "note": (
                "설비비 = 고정비 + 용량단가 × 용량(kWh). 고정비가 지배적이라 "
                "단일 kWh당 단가로 표현하지 않는다. 계수는 천 원 단위로 "
                "반올림해 저장한다 — 사례가 넷이라 그 아래 자리는 뜻이 없다."
            ),
        },
        "rounding": [
            {key: round(value, 5) for key, value in row.items()}
            for row in rounding_check(points, intercept, slope)
        ],
        "electrical_work": {
            "bands": [
                {
                    "max_kwh": SMALL_BAND_MAX_KWH,
                    "low_won": round(min(small_values)),
                    "high_won": round(max(small_values)),
                    "typical_won": round(sum(small_values) / len(small_values)),
                    "sample_size": len(small),
                },
                {
                    "max_kwh": max(case.capacity_kwh for case in large),
                    "low_won": round(min(large_values)),
                    "high_won": round(max(large_values)),
                    "typical_won": round(sum(large_values) / len(large_values)),
                    "sample_size": len(large),
                },
            ],
            "indoor_ratio": None if indoor_ratio is None else round(indoor_ratio, 3),
            "indoor_basis": indoor_basis,
            "note": (
                "옥외 컨테이너 기준이다. 구간 사이는 선형 보간한다. 실내 설치는 "
                "비율을 곱하는데 근거가 한 건뿐이라 참고값이다."
            ),
        },
        "applicable_range": {
            "min_kwh": min(case.capacity_kwh for case in regression),
            "max_kwh": max(case.capacity_kwh for case in regression),
            "note": (
                "이 구간 밖은 사례가 없다. 아래는 하한 용량으로 올려 산정하고, "
                "위는 참고값으로 표시한다."
            ),
        },
        "holdout": [
            {key: round(value, 5) for key, value in row.items()} for row in holdout(points)
        ],
        "cases": [
            {
                "key": case.key,
                "label": case.label,
                "power_kw": case.power_kw,
                "capacity_kwh": case.capacity_kwh,
                "equipment_won": case.equipment_won,
                "electrical_won": case.electrical_won,
                "install": case.install,
                "category": case.category,
                "note": case.note,
            }
            for case in cases
        ],
    }


def show(model: dict[str, Any]) -> None:
    equipment = model["equipment"]
    console.print(
        f"설비비 = {equipment['fixed_won']:,.0f} + "
        f"{equipment['per_kwh_won']:,.0f} × 용량(kWh)   R² = {equipment['r2']:.5f} "
        f"(사례 {equipment['sample_size']}건)"
    )
    for name, title in (
        ("holdout", "홀드아웃 — 한 건을 빼고 예측"),
        ("rounding", "반올림 재현 — 천 원 단위 계수로 사례를 다시 맞춘다"),
    ):
        table = Table(title=title)
        for column in ("용량(kWh)", "실제", "예측", "오차"):
            table.add_column(column, justify="right")
        for row in model[name]:
            table.add_row(
                f"{row['capacity_kwh']:,.1f}",
                f"{row['actual_won']:,.0f}",
                f"{row['predicted_won']:,.0f}",
                f"{row['error_ratio']:+.2%}",
            )
        console.print(table)
    for band in model["electrical_work"]["bands"]:
        console.print(
            f"전기공사 ≤ {band['max_kwh']:,.0f} kWh — "
            f"{band['low_won']:,.0f} ~ {band['high_won']:,.0f}원 "
            f"(대표 {band['typical_won']:,.0f}원, 사례 {band['sample_size']}건)"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="ESS 투자비 모델 재적합")
    parser.add_argument("--source", type=Path, default=CASES_PATH)
    parser.add_argument("--target", type=Path, default=MODEL_PATH)
    parser.add_argument("--check", action="store_true", help="쓰지 않고 차이만 본다")
    args = parser.parse_args()

    cases, vat_rate = load_cases(args.source)
    model = build_model(cases, vat_rate)
    show(model)

    if args.check:
        if not args.target.is_file():
            console.print("[red]모델 파일이 없습니다.[/red]")
            return 1
        current = json.loads(args.target.read_text(encoding="utf-8"))
        same = current.get("equipment") == model["equipment"]
        console.print("계수 동일" if same else "[yellow]계수가 다릅니다 — 다시 쓰십시오[/yellow]")
        return 0 if same else 1

    args.target.write_text(json.dumps(model, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    console.print(f"저장 {args.target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
