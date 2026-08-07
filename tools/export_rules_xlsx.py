"""기준 데이터 → 엑셀 내보내기 (요구사항서 12장).

    .venv\\Scripts\\python.exe tools\\export_rules_xlsx.py
    .venv\\Scripts\\python.exe tools\\export_rules_xlsx.py --output output\\rules.xlsx

**엑셀 왕복은 항목이 많고 표 구조인 것에만 쓴다.** 요금 단가(`build_tariff.py`)와
시군구 좌표가 그렇다. 기준 데이터는 항목이 적고 근거가 함께 보여야 하므로 원래
**화면 편집**(`kwise.rules.set_value`)이 주 경로다. 이 도구는

    · 한 번에 여러 항목을 훑어 보거나
    · 검토 의견을 주고받거나
    · 갱신분을 한꺼번에 반영할 때

쓰는 보조 경로다. 되읽기는 `tools\\build_rules.py` 가 맡는다.

**값 열만 고친다.** 항목 키는 편집 이력과 항목별 원복이 걸린 식별자이므로
바꾸면 안 된다. 되읽기가 키를 대조해 막는다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd
from rich.console import Console

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from kwise.report.excel import write_workbook  # noqa: E402
from kwise.rules import RuleOrigin, describe_items, expiry_warnings  # noqa: E402

SHEETS = {RuleOrigin.STATUTORY: "법령 유래", RuleOrigin.JUDGEMENT: "판단값"}
DEFAULT_OUTPUT = PROJECT_ROOT / "output" / "rules.xlsx"


def rules_frame(origin: RuleOrigin, today: dt.date | None = None) -> pd.DataFrame:
    """한 갈래의 항목 표. **값 열만 고쳐서 되돌려준다.**"""
    rows = []
    for view in describe_items(origin, today=today):
        row = view.as_row()
        # 리스트·사전은 셀에 넣을 수 없으므로 JSON 문자열로 편다.
        for column in ("값", "출고값"):
            if isinstance(row[column], (list, dict)):
                row[column] = json.dumps(row[column], ensure_ascii=False)
        rows.append(row)
    return pd.DataFrame(rows).set_index("항목")


def expiry_frame(today: dt.date | None = None) -> pd.DataFrame:
    warnings = expiry_warnings(today=today)
    if not warnings:
        return pd.DataFrame([{"구분": "—", "내용": "만료 임계를 넘긴 항목이 없습니다."}]).set_index(
            "구분"
        )
    return pd.DataFrame(
        [
            {
                "구분": item.scope,
                "항목": item.key,
                "이름": item.label,
                "기준": item.basis,
                "경과(개월)": round(item.months, 1),
                "임계(개월)": item.threshold_months,
                "근거": item.source,
                "원문 확인처": item.link,
            }
            for item in warnings
        ]
    ).set_index("구분")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="기준 데이터를 엑셀로 내보낸다 (12장)")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)
    console = Console()

    sheets = {SHEETS[origin]: rules_frame(origin) for origin in SHEETS}
    sheets["만료 경고"] = expiry_frame()
    sheets["안내"] = pd.DataFrame(
        [
            {
                "구분": "편집 방법",
                "내용": "**값 열만** 고치십시오. 항목 키를 바꾸면 되읽기가 거부합니다.",
            },
            {
                "구분": "되읽기",
                "내용": ".venv\\Scripts\\python.exe tools\\build_rules.py --source <이 파일>",
            },
            {"구분": "확인일", "내용": "값을 고치면 되읽기가 확인일을 오늘로 자동 갱신합니다."},
            {"구분": "이력", "내용": "변경은 data\\rules_history.jsonl 에 한 줄씩 남습니다."},
            {
                "구분": "원복",
                "내용": "직전 상태·출고 상태·항목별 원복은 화면(기준 데이터 관리)에서 합니다.",
            },
        ]
    ).set_index("구분")

    target = Path(args.output)
    write_workbook(sheets, target)
    console.print(f"[green]저장[/green] {target}")
    for origin, name in SHEETS.items():
        console.print(f"  {name} {len(sheets[name])}개 항목 ({origin.filename})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
