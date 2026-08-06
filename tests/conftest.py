"""테스트 공용 픽스처."""

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_USAGE_CSV = PROJECT_ROOT / "input" / "사용량조회_20240429.csv"


@pytest.fixture(scope="session")
def sample_usage_path() -> Path:
    """부록 B 회귀 테스트용 실측 샘플 파일."""
    if not SAMPLE_USAGE_CSV.is_file():
        pytest.skip(f"샘플 파일이 없습니다: {SAMPLE_USAGE_CSV}")
    return SAMPLE_USAGE_CSV
