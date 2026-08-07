"""케이스 스터디 실행 CLI (요구사항서 11.3).

    .venv\\Scripts\\python.exe tools\\run_casestudy.py
    .venv\\Scripts\\python.exe tools\\run_casestudy.py --cases C1 C6
    .venv\\Scripts\\python.exe tools\\run_casestudy.py --pv-unit-cost 1200000

**UI 는 만들지 않는다.** 계산 로직은 :mod:`kwise.report.casestudy` 에 있고
판정은 :mod:`kwise.report.validity` 에 있다. 여기는 얇은 진입점이다.

결과는 ``output\\casestudy_YYYYMMDD.xlsx`` 로 저장한다.
**타당성 판정이 하나라도 실패하면 종료 코드가 1 이다** — 계산 오류이므로
다음 단계로 넘어가면 안 된다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.table import Table

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from kwise.report.casestudy import (  # noqa: E402
    DEFAULT_CAPACITIES_KWP,
    build_case_definitions,
    run_case_study,
)
from kwise.report.excel import write_workbook  # noqa: E402
from kwise.report.notices import DATA_SOURCES, KNOWN_LIMITS  # noqa: E402
from kwise.report.validity import check_case_study, checks_frame  # noqa: E402
from kwise.tariff import load_tariff  # noqa: E402

DEFAULT_CASE_DIR = PROJECT_ROOT / "input" / "cases"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="케이스 스터디 실행 (요구사항서 11.3)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--cases", nargs="*", default=None, help="돌릴 케이스 키 (기본 전체)")
    parser.add_argument("--case-dir", default=str(DEFAULT_CASE_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument(
        "--capacities",
        nargs="*",
        type=float,
        default=list(DEFAULT_CAPACITIES_KWP),
        help="PV 용량 축 (kWp)",
    )
    parser.add_argument(
        "--pv-unit-cost",
        type=float,
        default=None,
        help="태양광 kWp당 단가. 주지 않으면 투자비·회수기간을 산출하지 않는다 (7.5)",
    )
    return parser.parse_args(argv)


def notice_frame() -> pd.DataFrame:

    rows = [{"구분": "데이터 출처", "내용": item} for item in DATA_SOURCES]
    rows += [
        {"구분": f"알려진 한계 {number}", "내용": item}
        for number, item in enumerate(KNOWN_LIMITS, 1)
    ]
    return pd.DataFrame(rows).set_index("구분")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    console = Console()

    definitions = build_case_definitions(Path(args.case_dir))
    if args.cases:
        wanted = set(args.cases)
        definitions = tuple(item for item in definitions if item.key in wanted)
        if not definitions:
            console.print(f"[red]해당하는 케이스가 없습니다: {sorted(wanted)}[/red]")
            return 2

    console.print(
        f"[bold]케이스 {len(definitions)}건[/bold] × PV {args.capacities} kWp × 감도 3종 — "
        "순차 실행"
    )
    table = load_tariff()
    study = run_case_study(
        definitions,
        table,
        capacities_kwp=tuple(args.capacities),
        pv_unit_cost_won_per_kwp=args.pv_unit_cost,
    )

    checks = check_case_study(study)
    failed = [item for item in checks if not item.passed]

    summary = Table(show_header=True, header_style="bold")
    summary.add_column("케이스")
    summary.add_column("요금적용전력", justify="right")
    summary.add_column("부하율", justify="right")
    summary.add_column("기본요금 비중", justify="right")
    summary.add_column("소요", justify="right")
    for result in study.results:
        summary.add_row(
            result.label,
            f"{result.baseline.billing_demand_kw:,.1f} kW",
            f"{result.diagnosis.pattern.load_factor:.1%}",
            f"{result.baseline.total_base_won / result.baseline.total_won:.1%}",
            f"{result.elapsed_sec:.1f}s",
        )
    console.print(summary)

    sheets = {
        "케이스": study.summary_frame(),
        "PV 매트릭스": study.pv_frame(),
        "감도 범위": study.sensitivity_frame(),
        "선택요금": study.selection_frame(),
        "수단": study.measure_frame(),
        "타당성 판정": checks_frame(checks),
        "성능": study.performance_frame(),
        "안내": notice_frame(),
    }
    stamp = dt.datetime.now().strftime("%Y%m%d")
    target = Path(args.output_dir) / f"casestudy_{stamp}.xlsx"
    write_workbook(sheets, target)
    console.print(f"[green]저장[/green] {target}")

    console.rule("타당성 판정")
    console.print(f"통과 {len(checks) - len(failed)} / {len(checks)}")
    for item in failed:
        console.print(f"[red]실패[/red] {item.scope} · {item.name} — {item.detail}")
    console.print(
        f"[bold]전체 소요 {study.elapsed_sec:,.1f} 초 · "
        f"기상 취득 {study.weather_calls} 회 (나머지는 캐시)[/bold]"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
