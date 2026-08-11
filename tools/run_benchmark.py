"""실행 시간·메모리 실측 CLI (요구사항서 11.4).

    .venv\\Scripts\\python.exe tools\\run_benchmark.py

샘플 데이터 한 벌로 파이프라인 전 구간을 순차로 돌며 소요·RSS·요금 재계산 횟수를
잰다. **진행률 가중치의 근거**이므로 결과를 그대로 ``assumptions.json`` 에 넣는다.

기상은 저장소의 사전 취득분을 쓴다. 캐시 적중·미적중을 따로 재되 **네트워크를
타지 않는다** — 미적중은 사전 취득분(``data\\weather\\``)에서 읽는 비용이다.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.table import Table

from kwise.benchmark import MB, BenchmarkResult, measure
from kwise.compare import CombinationSpec, compare_combinations, sensitivity_comparison
from kwise.diagnose import ContractInfo, diagnose
from kwise.io import load_usage
from kwise.measures import (
    EssCostInput,
    PvCostInput,
    apply_generation,
    evaluate_contract_adjustment,
    evaluate_demand_response,
    evaluate_ess,
    evaluate_power_factor,
    evaluate_surplus,
    evaluate_tariff_switch,
    solar_curve,
    unit_generation_kw,
)
from kwise.progress import STAGE_WEIGHT_KEYS, WEIGHT_SOURCE
from kwise.pv import (
    ArrayConfig,
    PvSystemConfig,
    WeatherRequest,
    load_weather,
)
from kwise.pv.archive import load_archive
from kwise.pv.region import find_region
from kwise.quality import check_quality
from kwise.report import DocumentSections, ReportSections, build_sheets, measure_entries
from kwise.report.document import build_document
from kwise.tariff import BillingOptions, TariffSelection, load_tariff

console = Console()

SAMPLE = Path("input") / "사용량조회_20240429.csv"
REGION = "서울특별시/강남구"
CONTRACT_KW = 5_800.0
CAPACITY_KWP = 960.0
SELECTION = TariffSelection("general_b", "high_a", "I")

# 구간 → 진행 단계. 가중치는 이 묶음으로 접는다 (요구사항서 10.6).
STAGE_PARTS: dict[str, tuple[str, ...]] = {
    "read": ("데이터 읽기",),
    "quality": ("품질 검사",),
    "diagnose": ("진단",),
    "weather": ("기상 — 캐시 적중",),
    "solar": ("PV 단위 발전량 (pvlib)", "PV 용량 곡선 20단계"),
    "measures": (
        "7.1 선택요금 전환",
        "7.2 계약전력 조정",
        "7.3 경제성DR",
        "7.4 역률 개선",
        "7.6 ESS",
        "7.7 잉여 활용",
    ),
    "compare": ("조합 비교 4종", "감도 3종"),
    "export": ("Excel 시트 생성", "Word 보고서 생성"),
}


def run() -> BenchmarkResult:
    result = BenchmarkResult()

    with measure(result, "요금표 읽기"):
        table = load_tariff()

    with measure(result, "데이터 읽기", detail=f"{SAMPLE.name}"):
        usage = load_usage(SAMPLE)
    result.notes.append(
        f"원본 {usage.meta.raw_rows:,}행 · 그리드 {usage.meta.expected_rows:,}구간 · "
        f"{SAMPLE.stat().st_size / 1024:,.0f} KB"
    )

    with measure(result, "품질 검사"):
        quality = check_quality(usage, contract_kw=CONTRACT_KW)

    options = BillingOptions(contract_kw=CONTRACT_KW)
    contract = ContractInfo(SELECTION, contract_kw=CONTRACT_KW)
    with measure(result, "진단", detail="부하·피크·요금 구조·계약 적정성"):
        diagnosis = diagnose(usage, table, contract, quality=quality, options=options)
    baseline = diagnosis.structure.bill if diagnosis.structure is not None else None
    assert baseline is not None

    # ---- 기상. 네트워크를 타지 않는다.
    region = find_region(REGION)
    request = WeatherRequest.for_index(
        pd.DatetimeIndex(usage.kw.index), region.latitude, region.longitude
    )
    with measure(result, "기상 — 캐시 미적중 (사전 취득분 읽기)", detail="data\\weather\\ parquet"):
        load_archive(request)
    load_weather(request)  # 캐시를 데운다
    with measure(result, "기상 — 캐시 적중", detail="PROJECT_CACHE parquet"):
        weather = load_weather(request)

    config = PvSystemConfig(
        latitude=region.latitude,
        longitude=region.longitude,
        arrays=(ArrayConfig.roof("지붕", 1_000.0),),
        altitude_m=50.0,
    )
    with measure(result, "PV 단위 발전량 (pvlib)", detail="1 kWp 프로파일 — 1회"):
        unit = unit_generation_kw(usage, weather, config)

    with measure(result, "PV 발전량 스케일 (용량 곱셈)", detail="960 kWp"):
        generation = unit * CAPACITY_KWP
        apply_generation(usage, generation)

    with measure(result, "PV 용량 곡선 20단계", detail="요금 21회 재계산"):
        curve = solar_curve(
            usage,
            table,
            SELECTION,
            unit,
            max_capacity_kwp=CAPACITY_KWP,
            cost=PvCostInput.of_unit_cost(1_300_000.0),
            steps=20,
            baseline=baseline,
            quality=quality,
            options=options,
        )

    # ---- 수단 7종
    with measure(result, "7.1 선택요금 전환"):
        evaluate_tariff_switch(
            usage,
            table,
            SELECTION,
            quality=quality,
            options=options,
            option_totals=diagnosis.option_totals,
        )
    with measure(result, "7.2 계약전력 조정"):
        evaluate_contract_adjustment(usage, baseline, contract_kw=CONTRACT_KW)
    with measure(result, "7.3 경제성DR"):
        assert diagnosis.dr is not None
        evaluate_demand_response(diagnosis.dr)
    with measure(result, "7.4 역률 개선"):
        power_factor = evaluate_power_factor(
            usage, table, SELECTION, baseline=baseline, quality=quality, options=options
        )
    result.notes.append("7.5 태양광은 위의 「PV 용량 곡선」이 그 자리다.")
    target = baseline.billing_demand_kw * 0.9
    with measure(result, "7.6 ESS", detail=f"목표 {target:,.0f} kW"):
        evaluate_ess(
            usage,
            table,
            SELECTION,
            target_kw=target,
            cost=EssCostInput.of_unit_cost(600_000.0),
            baseline=baseline,
            quality=quality,
            options=options,
        )
    with measure(result, "7.7 잉여 활용"):
        net = apply_generation(usage, unit * CAPACITY_KWP)
        evaluate_surplus(
            usage,
            table,
            SELECTION,
            net.surplus_kw,
            generation_kwh=net.generated_kwh,
            options=options,
        )

    # ---- 조합·감도
    best = TariffSelection("general_b", "high_a", "II")
    specs = (
        CombinationSpec(name="기준선 (현행)", selection=SELECTION),
        CombinationSpec(name="선택요금 전환", selection=best),
        CombinationSpec(name="+ 태양광", selection=best, pv_capacity_kwp=CAPACITY_KWP),
        CombinationSpec(
            name="+ ESS", selection=best, pv_capacity_kwp=CAPACITY_KWP, ess_target_kw=target
        ),
    )
    with measure(result, "조합 비교 4종", detail="조합마다 요금 재계산"):
        comparison = compare_combinations(
            usage,
            table,
            specs,
            baseline_bill=baseline,
            unit_pv_kw_per_kwp=unit,
            quality=quality,
            options=options,
        )
    with measure(result, "감도 3종", detail="한 조합 × 첨예도 3"):
        sensitivity = sensitivity_comparison(
            usage,
            table,
            specs[2],
            baseline_bill=baseline,
            unit_pv_kw_per_kwp=unit,
            quality=quality,
            options=options,
        )

    # ---- 산출물
    sections = ReportSections(
        usage=usage,
        bill=baseline,
        diagnosis=diagnosis,
        comparison=comparison,
        sensitivity=sensitivity,
        include_timeseries=True,
    )
    with measure(result, "Excel 시트 생성", detail="9시트 · 15분 시계열 포함"):
        sheets = build_sheets(sections)
    with measure(result, "Excel 파일 쓰기"):
        from kwise.report import write_workbook

        with tempfile.TemporaryDirectory() as folder:
            write_workbook(sheets, Path(folder) / "bench.xlsx")

    entries = measure_entries(switch=None, power_factor=power_factor, solar=curve.points[-1])
    with measure(result, "Word 보고서 생성", detail="5장 · 차트 4종"):
        document = build_document(
            DocumentSections(
                usage=usage,
                bill=baseline,
                diagnosis=diagnosis,
                comparison=comparison,
                measures=entries,
            )
        )
        with tempfile.TemporaryDirectory() as folder:
            document.save(str(Path(folder) / "bench.docx"))

    return result


def render(result: BenchmarkResult) -> None:
    table = Table(title="구간별 실측")
    table.add_column("구간")
    table.add_column("소요(초)", justify="right")
    table.add_column("비중", justify="right")
    table.add_column("RSS 증감(MB)", justify="right")
    table.add_column("요금 재계산", justify="right")
    table.add_column("비고")
    for item in result.stages:
        share = item.seconds / result.total_seconds * 100 if result.total_seconds else 0.0
        table.add_row(
            item.name,
            f"{item.seconds:,.3f}",
            f"{share:,.1f}%",
            "—" if item.rss_delta_mb is None else f"{item.rss_delta_mb:+,.1f}",
            f"{item.bill_calls:,}" if item.bill_calls else "—",
            item.detail,
        )
    console.print(table)

    console.print(
        f"[bold]전체[/bold] {result.total_seconds:,.1f} 초 · "
        f"요금 재계산 {result.total_bill_calls:,} 회"
    )
    if result.peak_rss_bytes is not None:
        peak = result.peak_rss_bytes / MB
        base = (result.baseline_rss_bytes or 0.0) / MB
        console.print(f"[bold]최대 RSS[/bold] {peak:,.0f} MB (시작 {base:,.0f} MB)")
        limit = 1024.0
        if peak > limit / 2:
            console.print(
                f"[yellow]Streamlit Cloud 한도({limit:,.0f} MB)의 절반을 넘습니다.[/yellow]"
            )
        else:
            console.print(
                f"[green]Streamlit Cloud 한도({limit:,.0f} MB)의 절반 이내입니다.[/green]"
            )
    for note in result.notes:
        console.print(f"[dim]· {note}[/dim]")

    weights = result.weights(STAGE_PARTS)
    console.print("\n[bold]진행률 가중치[/bold] (assumptions.json)")
    for key in STAGE_WEIGHT_KEYS:
        console.print(f"  {key:<10} {weights.get(key, 0.0):.4f}")
    console.print(f"[dim]합 {sum(weights.values()):.4f} · 키 {WEIGHT_SOURCE}[/dim]")


def main() -> None:
    parser = argparse.ArgumentParser(description="실행 시간·메모리 실측")
    parser.add_argument("--json", type=Path, help="측정 결과를 JSON 으로도 남긴다")
    args = parser.parse_args()

    os.environ.setdefault("KWISE_BENCHMARK", "1")
    result = run()
    render(result)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "stages": [
                {
                    "name": item.name,
                    "seconds": item.seconds,
                    "rss_delta_mb": item.rss_delta_mb,
                    "bill_calls": item.bill_calls,
                }
                for item in result.stages
            ],
            "total_seconds": result.total_seconds,
            "peak_rss_mb": (None if result.peak_rss_bytes is None else result.peak_rss_bytes / MB),
            "weights": result.weights(STAGE_PARTS),
        }
        args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"[dim]JSON — {args.json}[/dim]")


if __name__ == "__main__":
    main()
