"""CLI 진입점 (요구사항서 10.4).

    python -m kwise.cli run --cases cases.yaml

케이스는 순차 처리한다. 중간 결과가 캐시에 남아 ``--resume`` 으로 이어 돌릴 수 있다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from kwise import __version__
from kwise.report.batch import BatchResult, load_batch_config, run_batch

__all__ = ["app", "main"]

app = typer.Typer(help="kWise 배치 실행기 — 전력 비용 진단과 개선안 비교", add_completion=False)
console = Console()


def _render(result: BatchResult) -> None:
    table = Table(title="배치 결과")
    table.add_column("케이스")
    table.add_column("최선 조합")
    table.add_column("절감액(원)", justify="right")
    table.add_column("투자비(원)", justify="right")
    table.add_column("회수(년)", justify="right")
    table.add_column("확실성")
    for summary in result.summaries:
        table.add_row(
            summary.name,
            summary.best_combination,
            f"{summary.saving_won:,.0f}",
            f"{summary.investment_won:,.0f}",
            "즉시"
            if summary.payback_years == 0
            else f"{summary.payback_years:.1f}"
            if summary.payback_years is not None
            else "—",
            summary.certainty,
        )
    console.print(table)
    console.print(f"[bold]요약 CSV[/bold]: {result.summary_csv}")
    for summary in result.summaries:
        if summary.note:
            console.print(f"[yellow]{summary.name}[/yellow]: {summary.note}")
    if result.skipped:
        console.print(f"[dim]재개로 건너뛴 케이스: {', '.join(result.skipped)}[/dim]")


@app.command()
def run(
    cases: Annotated[Path, typer.Option(help="YAML 케이스 정의 파일")],
    output_dir: Annotated[
        Path | None, typer.Option(help="산출물 폴더. 기본은 정의 파일의 값")
    ] = None,
    resume: Annotated[bool, typer.Option(help="중간 결과가 있는 케이스를 건너뛴다")] = False,
    timeseries: Annotated[bool, typer.Option(help="15분 시계열 시트를 포함한다")] = True,
) -> None:
    """YAML 케이스를 순차 실행하고 케이스별 Excel 과 요약 CSV 를 낸다."""
    from dataclasses import replace

    config = load_batch_config(cases)
    if output_dir is not None:
        config = replace(config, output_dir=output_dir)

    total = len(config.cases)
    console.print(f"[bold]{total}개 케이스[/bold]를 순차 실행합니다 — {cases}")

    def report(name: str, index: int, count: int) -> None:
        console.print(f"  [{index}/{count}] {name}")

    result = run_batch(config, resume=resume, include_timeseries=timeseries, on_progress=report)
    _render(result)


@app.command()
def version() -> None:
    """버전을 출력한다."""
    console.print(f"kWise {__version__}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
