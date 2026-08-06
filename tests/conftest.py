"""테스트 공용 픽스처."""

from __future__ import annotations

from pathlib import Path

import pytest

from kwise.diagnose import ContractInfo, Diagnosis, diagnose
from kwise.io import UsageData, load_usage
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
    return calculate_bill(
        sample_usage,
        tariff,
        TariffSelection("general_b", "high_a", "I"),
        quality=sample_report,
    )


@pytest.fixture(scope="session")
def sample_diagnosis(
    sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable
) -> Diagnosis:
    """실측 샘플 × 일반용(을) 고압A 선택Ⅰ × 계약전력 5,500 kW."""
    return diagnose(
        sample_usage,
        tariff,
        ContractInfo(TariffSelection("general_b", "high_a", "I"), contract_kw=5_500.0),
        quality=sample_report,
    )
