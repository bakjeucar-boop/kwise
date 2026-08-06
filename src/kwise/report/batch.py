"""CLI 배치 실행 (요구사항서 10.4).

YAML 케이스 정의를 받아 **순차로** 실행한다. 케이스별 Excel 과 요약 CSV 한 장을
낸다. 케이스를 전부 메모리에 올리지 않는다 — 한 건 끝나면 요약 행만 남긴다.

중간 결과를 ``PROJECT_CACHE\\batch\\<정의파일>\\<케이스>.json`` 에 남겨 재개할 수 있다.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from kwise.compare import compare_combinations, default_combinations
from kwise.compare.sensitivity import sensitivity_comparison
from kwise.diagnose import ContractInfo, diagnose
from kwise.io import load_usage
from kwise.measures import (
    evaluate_contract_adjustment,
    evaluate_ess,
    evaluate_tariff_switch,
    light_band_mask,
    unit_generation_kw,
)
from kwise.pv import (
    ArrayConfig,
    PvSystemConfig,
    WeatherRequest,
    WeatherUnavailableError,
    cache_root,
    load_weather,
)
from kwise.quality import check_quality
from kwise.report.excel import (
    DEFAULT_OUTPUT_DIR,
    ReportSections,
    export_report,
    measure_summary_frame,
    no_pv_sensitivity_frame,
)
from kwise.tariff import TariffSelection, TariffTable, calculate_bill, load_tariff

__all__ = [
    "BatchConfig",
    "BatchResult",
    "CaseSpec",
    "CaseSummary",
    "load_batch_config",
    "run_batch",
    "run_case",
    "summary_frame",
]


@dataclass(frozen=True)
class CaseSpec:
    """케이스 하나. YAML 한 항목이 이 구조가 된다."""

    name: str
    usage: Path
    contract_type: str = "general_b"
    voltage: str = "high_a"
    option: str = "I"
    contract_kw: float | None = None
    contract_floor_ratio: float | None = None
    pv_capacity_kwp: float = 0.0
    pv_unit_cost_won_per_kwp: float = 0.0
    ess_target_kw: float | None = None
    ess_unit_cost_won_per_kwh: float = 0.0
    latitude: float = 37.5
    longitude: float = 127.0
    altitude_m: float = 0.0
    timezone: str = "Asia/Seoul"
    tilt_deg: float = 30.0
    azimuth_deg: float = 180.0

    @property
    def selection(self) -> TariffSelection:
        return TariffSelection(self.contract_type, self.voltage, self.option)


@dataclass(frozen=True)
class BatchConfig:
    """배치 정의 전체."""

    cases: tuple[CaseSpec, ...]
    output_dir: Path = DEFAULT_OUTPUT_DIR
    tariff_path: Path | None = None
    source: Path | None = None

    @property
    def key(self) -> str:
        return self.source.stem if self.source is not None else "batch"


@dataclass(frozen=True)
class CaseSummary:
    """케이스 하나의 요약. 요약 CSV 한 줄이 된다."""

    name: str
    usage_file: str
    period: str
    max_demand_kw: float
    billing_demand_kw: float
    total_kwh: float
    baseline_won: float
    best_combination: str
    saving_won: float
    annual_saving_won: float
    investment_won: float
    payback_years: float | None
    certainty: str
    excel: str
    warnings: int
    note: str = ""


@dataclass(frozen=True)
class BatchResult:
    """배치 실행 결과."""

    summaries: tuple[CaseSummary, ...]
    summary_csv: Path
    skipped: tuple[str, ...] = field(default=())


def load_batch_config(path: Path) -> BatchConfig:
    """YAML 정의를 읽는다. 인코딩을 명시해 cp949 로 열리지 않게 한다."""
    with Path(path).open(encoding="utf-8") as stream:
        payload: dict[str, Any] = yaml.safe_load(stream) or {}
    raw_cases = payload.get("cases")
    if not raw_cases:
        raise ValueError(f"케이스 정의가 비어 있습니다: {path}")

    base_dir = Path(path).resolve().parent
    cases: list[CaseSpec] = []
    for index, item in enumerate(raw_cases, start=1):
        values = dict(item)
        name = str(values.pop("name", f"case{index}"))
        usage = Path(values.pop("usage"))
        if not usage.is_absolute():
            usage = base_dir / usage
        cases.append(CaseSpec(name=name, usage=usage, **values))

    output_dir = Path(payload.get("output_dir", DEFAULT_OUTPUT_DIR))
    if not output_dir.is_absolute():
        output_dir = base_dir / output_dir
    tariff_path = payload.get("tariff")
    return BatchConfig(
        cases=tuple(cases),
        output_dir=output_dir,
        tariff_path=Path(tariff_path) if tariff_path else None,
        source=Path(path),
    )


def _cache_dir(config: BatchConfig) -> Path:
    return cache_root() / "batch" / config.key


def run_case(
    case: CaseSpec,
    table: TariffTable,
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    include_timeseries: bool = True,
) -> CaseSummary:
    """케이스 하나를 처음부터 끝까지 돌린다."""
    usage = load_usage(case.usage)
    quality = check_quality(usage)
    contract = ContractInfo(case.selection, contract_kw=case.contract_kw)
    diagnosis = diagnose(usage, table, contract, quality=quality)
    baseline = calculate_bill(usage, table, case.selection, quality=quality)

    note = ""
    unit_pv: pd.Series | None = None
    if case.pv_capacity_kwp > 0:
        request = WeatherRequest.for_index(
            pd.DatetimeIndex(usage.kw.index),
            case.latitude,
            case.longitude,
            timezone=case.timezone,
        )
        try:
            weather = load_weather(request)
        except WeatherUnavailableError as exc:
            note = f"기상 자료를 얻지 못해 태양광을 제외했습니다: {exc}"
        else:
            pv_config = PvSystemConfig(
                latitude=case.latitude,
                longitude=case.longitude,
                arrays=(
                    ArrayConfig.roof(
                        "지붕",
                        1_000.0,
                        tilt_deg=case.tilt_deg,
                        azimuth_deg=case.azimuth_deg,
                    ),
                ),
                altitude_m=case.altitude_m,
                timezone=case.timezone,
            )
            unit_pv = unit_generation_kw(usage, weather, pv_config)

    best_selection = (
        diagnosis.summary.best_selection
        if diagnosis.summary.best_selection is not None
        else case.selection
    )
    specs = default_combinations(
        current_selection=case.selection,
        best_selection=best_selection,
        pv_capacity_kwp=case.pv_capacity_kwp if unit_pv is not None else 0.0,
        pv_unit_cost_won_per_kwp=case.pv_unit_cost_won_per_kwp,
        ess_target_kw=case.ess_target_kw,
        ess_unit_cost_won_per_kwh=case.ess_unit_cost_won_per_kwh,
        contract_kw=case.contract_kw,
        contract_floor_ratio=case.contract_floor_ratio,
    )
    comparison = compare_combinations(
        usage,
        table,
        specs,
        baseline_bill=baseline,
        unit_pv_kw_per_kwp=unit_pv,
        quality=quality,
    )

    sensitivity = (
        sensitivity_comparison(
            usage,
            table,
            specs[-1],
            baseline_bill=baseline,
            unit_pv_kw_per_kwp=unit_pv,
            quality=quality,
        )
        if unit_pv is not None
        else no_pv_sensitivity_frame()
    )

    # 수단별 결과 — 조합 합계를 재사용해 중복 계산을 피한다.
    switch = evaluate_tariff_switch(
        usage,
        table,
        case.selection,
        quality=quality,
        option_totals=diagnosis.option_totals,
    )
    contract_result = (
        evaluate_contract_adjustment(
            usage,
            baseline,
            contract_kw=case.contract_kw,
            contract_floor_ratio=case.contract_floor_ratio,
        )
        if case.contract_kw is not None
        else None
    )
    ess_result = (
        evaluate_ess(
            usage,
            table,
            case.selection,
            target_kw=case.ess_target_kw,
            unit_cost_won_per_kwh=case.ess_unit_cost_won_per_kwh,
            charge_mask=light_band_mask(usage, table, selection=case.selection),
            baseline=baseline,
            quality=quality,
        )
        if case.ess_target_kw is not None
        else None
    )

    sections = ReportSections(
        usage=usage,
        bill=baseline,
        diagnosis=diagnosis,
        comparison=comparison,
        sensitivity=sensitivity,
        measure_rows=measure_summary_frame(switch=switch, contract=contract_result, ess=ess_result),
        include_timeseries=include_timeseries,
    )
    excel = export_report(sections, output_dir=output_dir, prefix=f"result_{case.name}")

    best = comparison.best
    return CaseSummary(
        name=case.name,
        usage_file=usage.meta.source_name,
        period=baseline.period_label,
        max_demand_kw=usage.meta.max_demand_kw,
        billing_demand_kw=baseline.billing_demand_kw,
        total_kwh=usage.total_kwh,
        baseline_won=baseline.total_won,
        best_combination=best.name,
        saving_won=best.saving_won,
        annual_saving_won=best.annual_saving_won,
        investment_won=best.investment_won,
        payback_years=best.payback_years,
        certainty=str(best.certainty),
        excel=str(excel),
        warnings=len(comparison.warnings) + len(diagnosis.warnings),
        note=note,
    )


def summary_frame(summaries: tuple[CaseSummary, ...]) -> pd.DataFrame:
    return pd.DataFrame([asdict(item) for item in summaries]).set_index("name")


def run_batch(
    config: BatchConfig,
    *,
    resume: bool = False,
    include_timeseries: bool = True,
    on_progress: object = None,
    table: TariffTable | None = None,
) -> BatchResult:
    """케이스를 순차 실행한다. 요약만 모아 CSV 한 장을 낸다.

    Args:
        resume: 중간 결과가 있으면 그 케이스를 건너뛴다.
        on_progress: ``(case_name, index, total)`` 를 받는 호출 가능 객체.
    """
    tariff = table if table is not None else load_tariff(config.tariff_path)
    cache = _cache_dir(config)
    cache.mkdir(parents=True, exist_ok=True)

    summaries: list[CaseSummary] = []
    skipped: list[str] = []
    total = len(config.cases)
    for index, case in enumerate(config.cases, start=1):
        if callable(on_progress):
            on_progress(case.name, index, total)
        marker = cache / f"{case.name}.json"
        if resume and marker.is_file():
            with marker.open(encoding="utf-8") as stream:
                summaries.append(CaseSummary(**json.load(stream)))
            skipped.append(case.name)
            continue

        summary = run_case(
            case, tariff, output_dir=config.output_dir, include_timeseries=include_timeseries
        )
        with marker.open("w", encoding="utf-8") as stream:
            json.dump(asdict(summary), stream, ensure_ascii=False, indent=2)
        summaries.append(summary)

    config.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M")
    csv_path = config.output_dir / f"summary_{stamp}.csv"
    # Excel 에서 열 CSV 이므로 utf-8-sig 로 쓴다.
    summary_frame(tuple(summaries)).to_csv(csv_path, encoding="utf-8-sig")
    return BatchResult(summaries=tuple(summaries), summary_csv=csv_path, skipped=tuple(skipped))
