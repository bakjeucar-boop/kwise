"""기상 데이터 사전 취득 CLI (요구사항서 7.5).

    .venv\\Scripts\\python.exe tools\\fetch_weather.py                     # 전국 3개년
    .venv\\Scripts\\python.exe tools\\fetch_weather.py --province 서울특별시
    .venv\\Scripts\\python.exe tools\\fetch_weather.py --region 서울특별시/강남구
    .venv\\Scripts\\python.exe tools\\fetch_weather.py --status            # 현황만
    .venv\\Scripts\\python.exe tools\\fetch_weather.py --dry-run           # 계획만

Open-Meteo 는 호출 제한이 있다. 실패해도 검토가 멈추지 않도록 최근 3개년치를
미리 받아 ``data\\weather\\`` 에 둔다. 취득 로직은
:mod:`kwise.pv.archive` 에 있고 여기는 얇은 진입점이다 — 그래야 격자 중복 제거와
백오프 규칙을 단위테스트로 고정할 수 있다.

**중단해도 같은 명령으로 재개된다.** 이미 받은 셀·연도는 색인을 보고 건너뛴다.
실패한 셀은 목록에 남기고 계속 진행하며, 끝에 요약과 함께 출력한다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from kwise.pv.archive import (  # noqa: E402
    ATTRIBUTION,
    DEFAULT_ARCHIVE_END,
    DEFAULT_ARCHIVE_START,
    FetchTask,
    Pacer,
    RetryPolicy,
    WeatherHttpError,
    archive_root,
    archive_status,
    fetch_cell_year,
    grid_cells_for,
    pending_tasks,
    store_cell_year,
)
from kwise.pv.region import Region, find_region, list_sigungu, load_regions  # noqa: E402

FAILURE_FILENAME = "fetch_failures.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Open-Meteo 기상 데이터 사전 취득 (대한민국 시군구 격자)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    scope = parser.add_argument_group("지역 범위 — 주지 않으면 전국")
    scope.add_argument("--province", action="append", default=[], help="시도 (여러 번 가능)")
    scope.add_argument(
        "--region", action="append", default=[], help="시군구 키 '서울특별시/강남구' (여러 번 가능)"
    )

    parser.add_argument(
        "--start", default=DEFAULT_ARCHIVE_START.isoformat(), help="시작일 YYYY-MM-DD"
    )
    parser.add_argument("--end", default=DEFAULT_ARCHIVE_END.isoformat(), help="종료일 YYYY-MM-DD")
    parser.add_argument("--root", default=None, help="저장 경로 (기본 data\\weather)")
    parser.add_argument(
        "--min-interval", type=float, default=1.0, help="호출 간 최소 간격(초). 호출 제한 대응"
    )
    parser.add_argument("--max-attempts", type=int, default=5, help="429·5xx 재시도 횟수")
    parser.add_argument("--backoff", type=float, default=2.0, help="지수 백오프 기준(초)")
    parser.add_argument("--timeout", type=float, default=120.0, help="요청 타임아웃(초)")
    parser.add_argument("--refresh", action="store_true", help="이미 받은 셀도 다시 받는다")
    parser.add_argument("--dry-run", action="store_true", help="계획만 보여 주고 받지 않는다")
    parser.add_argument("--status", action="store_true", help="확보 현황만 출력한다")
    return parser.parse_args(argv)


def select_regions(provinces: list[str], keys: list[str]) -> tuple[Region, ...]:
    """지역 범위를 정한다. 아무것도 주지 않으면 전국이다."""
    if not provinces and not keys:
        return load_regions()
    picked: dict[str, Region] = {}
    for province in provinces:
        for region in list_sigungu(province):
            picked[region.key] = region
    for key in keys:
        region = find_region(key)
        picked[region.key] = region
    return tuple(picked.values())


def print_status(console: Console, root: Path | None) -> None:
    status = archive_status(root)
    console.print(f"[bold]확보 현황[/bold] — {status.summary_text()}")
    if not status.cells:
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("연도")
    table.add_column("격자 수", justify="right")
    table.add_column("행 수", justify="right")
    table.add_column("용량", justify="right")
    for year in status.years:
        entries = [entry for cell in status.cells for entry in cell.entries if entry.year == year]
        table.add_row(
            str(year),
            f"{len(entries):,}",
            f"{sum(entry.rows for entry in entries):,}",
            f"{sum(entry.bytes for entry in entries) / 1_048_576:.1f} MB",
        )
    console.print(table)
    console.print(f"[dim]{status.attribution}[/dim]")


def write_failures(root: Path, failures: list[dict[str, object]]) -> Path:
    target = root / FAILURE_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as stream:
        json.dump(failures, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return target


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    console = Console()
    root = archive_root(Path(args.root) if args.root else None)

    if args.status:
        print_status(console, root)
        return 0

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    regions = select_regions(args.province, args.region)
    cells = grid_cells_for(regions)
    todo, done = pending_tasks(cells, start, end, root=root, refresh=args.refresh)

    console.print(
        f"[bold]지역[/bold] 시군구 {len(regions)}개 → 격자 {len(cells)}개 "
        f"(0.25° 중복 제거) · [bold]기간[/bold] {start} ~ {end}"
    )
    console.print(
        f"[bold]작업[/bold] 받을 것 {len(todo)}개 · 이미 확보 {len(done)}개 · 저장 {root}"
    )
    if args.dry_run or not todo:
        if not todo:
            console.print("[green]받을 것이 없습니다.[/green]")
        print_status(console, root)
        return 0

    policy = RetryPolicy(max_attempts=args.max_attempts, backoff_base_sec=args.backoff)
    pacer = Pacer(min_interval_sec=args.min_interval)
    failures: list[dict[str, object]] = []
    stored_rows = 0
    stored_bytes = 0

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        bar = progress.add_task("취득", total=len(todo))
        for task in todo:
            assert isinstance(task, FetchTask)
            label = f"{task.cell[0]:.2f},{task.cell[1]:.2f} {task.year}"
            progress.update(bar, description=f"취득 {label}")
            pacer.wait()

            def on_retry(
                attempt: int, delay: float, error: WeatherHttpError, name: str = label
            ) -> None:
                progress.console.print(
                    f"[yellow]재시도[/yellow] {name} — {attempt}회차 실패"
                    f"(HTTP {error.status}), {delay:.0f}초 후 다시 시도"
                )

            try:
                frame = fetch_cell_year(
                    task.cell,
                    task.year,
                    start=task.start,
                    end=task.end,
                    policy=policy,
                    timeout=args.timeout,
                    on_retry=on_retry,
                )
                entry = store_cell_year(frame, task.cell, task.year, root=root)
            except Exception as exc:  # 실패한 셀은 기록하고 계속 진행한다
                status_code = exc.status if isinstance(exc, WeatherHttpError) else None
                failures.append(
                    {
                        "cell": [task.cell[0], task.cell[1]],
                        "year": task.year,
                        "status": status_code,
                        "error": str(exc)[:300],
                    }
                )
                progress.console.print(f"[red]실패[/red] {label} — {exc}")
            else:
                stored_rows += entry.rows
                stored_bytes += entry.bytes
            progress.advance(bar)

    console.rule("요약")
    console.print(
        f"성공 {len(todo) - len(failures)}개 · 실패 {len(failures)}개 · "
        f"{stored_rows:,} 행 · {stored_bytes / 1_048_576:.1f} MB"
    )
    if failures:
        target = write_failures(root, failures)
        console.print(f"[red]실패 셀 목록[/red] {target}")
        for item in failures[:20]:
            console.print(f"  {item['cell']} {item['year']} — {item['error']}")
        if len(failures) > 20:
            console.print(f"  … 외 {len(failures) - 20}건")
    print_status(console, root)
    console.print(f"[dim]{ATTRIBUTION}[/dim]")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
