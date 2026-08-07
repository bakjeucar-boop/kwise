"""테스트 공용 픽스처.

실측 샘플을 쓰는 픽스처는 세션 범위다. 요금 계산이 무거워 매번 돌리면 느리다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from kwise.compare import ComparisonResult, compare_combinations, default_combinations
from kwise.diagnose import ContractInfo, Diagnosis, diagnose
from kwise.io import UsageData, load_usage
from kwise.measures import (
    EssCostInput,
    EssResult,
    SolarCurve,
    TariffSwitchResult,
    evaluate_ess,
    evaluate_tariff_switch,
    light_band_mask,
    roof_capacity_limit_kwp,
    solar_curve,
    unit_generation_kw,
)
from kwise.pv import ArrayConfig, PvSystemConfig
from kwise.quality import QualityReport, check_quality
from kwise.tariff import (
    BillingResult,
    TariffSelection,
    TariffTable,
    calculate_bill,
    load_tariff,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_USAGE_CSV = PROJECT_ROOT / "input" / "사용량조회_20240429.csv"

SAMPLE_SELECTION = TariffSelection("general_b", "high_a", "I")
SAMPLE_CONTRACT_KW = 5_500.0
PV_COST_WON_PER_KWP = 1_200_000.0
# ESS 단가는 **kW당 하나**로 받는다 (7.6). 방전시간은 단가에 이미 들어 있다.
ESS_COST_WON_PER_KW = 615_231.0  # 참고단가 LFP 2025 · 1h 방전 환산값


@pytest.fixture(autouse=True)
def isolated_weather_archive(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """사전 취득분(data\\weather)에서 테스트를 떼어 놓는다.

    저장소에 전국 3개년치가 들어 있으므로, 격리하지 않으면 "API 실패 시 멈추는가"
    같은 시험이 **조용히 폴백에 성공해** 통과해 버린다. 사전 취득분을 실제로
    쓰는 시험은 ``root=`` 나 자체 ``monkeypatch`` 로 경로를 직접 지정한다.
    """
    empty = tmp_path_factory.mktemp("no-weather-archive")
    monkeypatch.setenv("KWISE_WEATHER_DIR", str(empty))


@pytest.fixture(scope="session")
def sample_usage_path() -> Path:
    """부록 B 회귀 테스트용 실측 샘플 파일."""
    if not SAMPLE_USAGE_CSV.is_file():
        pytest.skip(f"샘플 파일이 없습니다: {SAMPLE_USAGE_CSV}")
    return SAMPLE_USAGE_CSV


@pytest.fixture(scope="session")
def sample_usage(sample_usage_path: Path) -> UsageData:
    return load_usage(sample_usage_path)


@pytest.fixture(scope="session")
def sample_report(sample_usage: UsageData) -> QualityReport:
    return check_quality(sample_usage)


@pytest.fixture(scope="session")
def tariff() -> TariffTable:
    """PoC 요금표 (일반용전력(을) 고압A·B)."""
    return load_tariff()


@pytest.fixture(scope="session")
def sample_bill(
    sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable
) -> BillingResult:
    """실측 샘플 × 일반용(을) 고압A 선택Ⅰ. 부록 B 회귀의 기준이다."""
    return calculate_bill(sample_usage, tariff, SAMPLE_SELECTION, quality=sample_report)


@pytest.fixture(scope="session")
def sample_diagnosis(
    sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable
) -> Diagnosis:
    """실측 샘플 × 고압A 선택Ⅰ × 계약전력 5,500 kW."""
    return diagnose(
        sample_usage,
        tariff,
        ContractInfo(SAMPLE_SELECTION, contract_kw=SAMPLE_CONTRACT_KW),
        quality=sample_report,
    )


@pytest.fixture(scope="session")
def sample_unit_pv(sample_usage: UsageData) -> pd.Series:
    """샘플 기간을 덮는 1 kWp 당 발전 프로파일 (합성 청천 기상).

    네트워크를 타지 않는다. 맑은 날만 이어 붙였으므로 발전량은 낙관적이지만,
    수단 평가의 단조성·정렬·재계산을 확인하는 데는 충분하다.
    """
    from tests._synthetic import clearsky_weather

    weather = clearsky_weather(start="2023-04-24", end="2024-04-28")
    config = PvSystemConfig(
        latitude=37.5,
        longitude=127.0,
        arrays=(ArrayConfig.roof("지붕", 1_000.0),),
        altitude_m=50.0,
        timezone="Asia/Seoul",
    )
    return unit_generation_kw(sample_usage, weather, config)


@pytest.fixture(scope="session")
def sample_switch(
    sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable
) -> TariffSwitchResult:
    return evaluate_tariff_switch(sample_usage, tariff, SAMPLE_SELECTION, quality=sample_report)


@pytest.fixture(scope="session")
def sample_curve(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
    sample_unit_pv: pd.Series,
) -> SolarCurve:
    """옥상 20,000 m² 상한(960 kWp)까지 4단계. 시험 시간을 줄이려 단계만 줄였다."""
    return solar_curve(
        sample_usage,
        tariff,
        SAMPLE_SELECTION,
        sample_unit_pv,
        max_capacity_kwp=roof_capacity_limit_kwp(20_000.0),
        unit_cost_won_per_kwp=PV_COST_WON_PER_KWP,
        steps=4,
        baseline=sample_bill,
        quality=sample_report,
    )


@pytest.fixture(scope="session")
def sample_ess(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
) -> EssResult:
    """목표 5,200 kW. 부록 B 초과 분석 표의 첫 줄과 같은 조건이다."""
    return evaluate_ess(
        sample_usage,
        tariff,
        SAMPLE_SELECTION,
        target_kw=5_200.0,
        cost=EssCostInput.of_unit_cost(ESS_COST_WON_PER_KW),
        charge_mask=light_band_mask(sample_usage, tariff, selection=SAMPLE_SELECTION),
        baseline=sample_bill,
        quality=sample_report,
    )


@pytest.fixture(scope="session")
def sample_comparison(
    sample_usage: UsageData,
    sample_report: QualityReport,
    tariff: TariffTable,
    sample_bill: BillingResult,
    sample_unit_pv: pd.Series,
) -> ComparisonResult:
    """기준선 / 요금제만 / +태양광 500 kWp / +ESS 목표 5,000 kW."""
    specs = default_combinations(
        current_selection=SAMPLE_SELECTION,
        best_selection=TariffSelection("general_b", "high_a", "II"),
        pv_capacity_kwp=500.0,
        pv_unit_cost_won_per_kwp=PV_COST_WON_PER_KWP,
        ess_target_kw=5_000.0,
        ess_unit_cost_won_per_kw=ESS_COST_WON_PER_KW,
    )
    return compare_combinations(
        sample_usage,
        tariff,
        specs,
        baseline_bill=sample_bill,
        unit_pv_kw_per_kwp=sample_unit_pv,
        quality=sample_report,
    )


@pytest.fixture(scope="session")
def synthetic_usage(tmp_path_factory: pytest.TempPathFactory) -> UsageData:
    """야간 기저 400 kW, 낮 스파이크가 있는 한 달치. ESS 야간 피크 시험용."""
    from tests._synthetic import make_labels, month_dates, write_csv

    rows: list[tuple[str, float]] = []
    for date in month_dates(2024, 3):
        for label in make_labels(date):
            spike = date == "2024-03-06" and " 12:" in label
            rows.append((label, 250.0 if spike else 100.0))
    return load_usage(write_csv(tmp_path_factory.mktemp("synthetic") / "flat.csv", rows))
