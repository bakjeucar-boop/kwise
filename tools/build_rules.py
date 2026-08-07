"""엑셀 → 기준 데이터 되읽기 (요구사항서 12장).

    .venv\\Scripts\\python.exe tools\\build_rules.py --source output\\rules.xlsx
    .venv\\Scripts\\python.exe tools\\build_rules.py --source output\\rules.xlsx --check

`export_rules_xlsx.py` 가 내보낸 표에서 **값 열만** 읽어 반영한다.

**항목 키를 대조한다.** 키는 편집 이력과 항목별 원복이 걸린 식별자다. 엑셀에서
행을 지우거나 키를 고치면 되읽기를 **거부**한다 — 조용히 항목이 사라지면 그
항목의 기본값이 코드에 없으므로 다음 실행이 멈추고, 원인을 찾기 어렵다.

반영은 :func:`kwise.rules.set_value` 를 거치므로 검증·백업·이력·확인일 갱신이
그대로 적용된다. **검증에 실패하면 아무것도 저장하지 않는다.**
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd
from rich.console import Console
from rich.table import Table

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from kwise.rules import (  # noqa: E402
    RuleDataError,
    RuleOrigin,
    assumptions,
    rules,
    set_value,
)

SHEETS = {"법령 유래": RuleOrigin.STATUTORY, "판단값": RuleOrigin.JUDGEMENT}


def _parse(value: Any) -> Any:
    """엑셀 셀을 원래 형으로 되돌린다. JSON 으로 편 값은 되읽는다."""
    if isinstance(value, str):
        text = value.strip()
        if text[:1] in "[{" or text in ("true", "false", "null"):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
        return text
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return value  # 0.3 같은 비율을 정수로 바꾸지 않는다
    return value


def collect_changes(source: Path) -> tuple[list[tuple[str, Any, Any]], list[str]]:
    """(바뀐 항목, 문제) 를 낸다. 문제가 있으면 반영하지 않는다."""
    changes: list[tuple[str, Any, Any]] = []
    problems: list[str] = []
    for sheet, origin in SHEETS.items():
        try:
            frame = pd.read_excel(source, sheet_name=sheet, index_col=0)
        except ValueError:
            problems.append(f"{source.name} 에 '{sheet}' 시트가 없습니다.")
            continue
        current = rules() if origin is RuleOrigin.STATUTORY else assumptions()
        seen = set()
        for key, row in frame.iterrows():
            name = str(key)
            seen.add(name)
            if name not in current:
                problems.append(
                    f"[{sheet}] 없는 항목 키입니다: {name!r}. "
                    "**키를 고치지 마십시오** — 편집 이력과 항목별 원복이 이 키로 걸립니다."
                )
                continue
            new_value = _parse(row["값"])
            if new_value != current[name].value:
                changes.append((name, current[name].value, new_value))
        missing = set(current.item_keys()) - seen
        if missing:
            problems.append(
                f"[{sheet}] 표에서 빠진 항목이 있습니다: {', '.join(sorted(missing))}. "
                "행을 지우지 마십시오 — 코드에 기본값이 없어 다음 실행이 멈춥니다."
            )
    return changes, problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="엑셀에서 기준 데이터를 되읽는다 (12장)")
    parser.add_argument("--source", required=True)
    parser.add_argument("--check", action="store_true", help="반영하지 않고 차이만 보여 준다")
    args = parser.parse_args(argv)
    console = Console()

    source = Path(args.source)
    if not source.is_file():
        console.print(f"[red]파일이 없습니다:[/red] {source}")
        return 2

    try:
        changes, problems = collect_changes(source)
    except RuleDataError as exc:
        console.print(f"[red]기준 데이터를 읽지 못했습니다:[/red] {exc}")
        return 2

    if problems:
        console.print("[red]되읽기를 거부합니다.[/red] 아래를 고친 뒤 다시 실행하십시오.")
        for problem in problems:
            console.print(f"  · {problem}")
        return 1

    if not changes:
        console.print("[green]바뀐 항목이 없습니다.[/green]")
        return 0

    table = Table(show_header=True, header_style="bold")
    table.add_column("항목")
    table.add_column("이전")
    table.add_column("이후")
    for key, before, after in changes:
        table.add_row(key, str(before), str(after))
    console.print(table)

    if args.check:
        console.print(f"[yellow]--check 이므로 반영하지 않았습니다[/yellow] ({len(changes)}건)")
        return 0

    applied = 0
    for key, _, after in changes:
        result = set_value(key, after)
        if not result:
            console.print(f"[red]검증 실패로 저장하지 않았습니다:[/red] {result.message}")
            return 1
        applied += 1
    console.print(f"[green]반영 완료[/green] {applied}건. 확인일이 오늘로 갱신되었습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
