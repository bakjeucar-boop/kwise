"""케이스 스터디 (요구사항서 11.3) 와 경계 케이스.

**타당성 판정이 하나라도 실패하면 계산 오류다.** 여기서 막는다.

케이스 6종을 다 돌리면 100초쯤 걸린다. 회귀에서는 **판정 결과만** 확인하고,
케이스 생성·경계 케이스는 가벼운 경로로 따로 본다.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from kwise.io import load_usage, slot_start
from kwise.pv import WeatherRequest, WeatherUnavailableError, load_weather
from kwise.quality import check_quality
from kwise.report.casestudy import (
    DEFAULT_CAPACITIES_KWP,
    CaseStudy,
    build_case_definitions,
    run_case_study,
)
from kwise.report.validity import check_case_study
from kwise.tariff import TariffSelection, TariffTable, calculate_bill

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CASE_DIR = PROJECT_ROOT / "input" / "cases"
GENERAL_B = TariffSelection("general_b", "high_a", "I")


@pytest.fixture(scope="module")
def case_dir() -> Path:
    if not CASE_DIR.is_dir() or not any(CASE_DIR.glob("C*.csv")):
        pytest.skip(f"케이스 파일이 없습니다: {CASE_DIR} (tools\\make_cases.py 실행)")
    return CASE_DIR


@pytest.fixture(scope="module")
def study(case_dir: Path, tariff: TariffTable) -> CaseStudy:
    """케이스 6종 × PV 4단계 × 감도 3종. **순차로 돈다.**"""
    return run_case_study(build_case_definitions(case_dir), tariff)


# --------------------------------------------------------------------- 케이스 정의


def test_six_cases_with_one_industrial(case_dir: Path) -> None:
    """C4 만 산업용(을)이다 — 봄·가을 주말 할인 특례를 태우기 위해서다."""
    definitions = build_case_definitions(case_dir)
    assert [item.key for item in definitions] == ["C1", "C2", "C3", "C4", "C5", "C6"]
    industrial = [item.key for item in definitions if item.contract_type == "industrial_b"]
    assert industrial == ["C4"]


def test_case_profiles_differ_where_they_should(case_dir: Path) -> None:
    """**변형이 실제로 시간대 분포를 바꿨는지** 확인한다.

    같은 분포가 나오면 케이스가 서로 다른 규칙을 태우지 못한다.
    """

    def peak_hours(key: str, name: str) -> set[int]:
        usage = load_usage(case_dir / f"{key}_{name}.csv")
        top = usage.kw.dropna().nlargest(200)
        return set(slot_start(pd.DatetimeIndex(top.index), 15).hour)

    def peak_centre(key: str, name: str) -> float:
        usage = load_usage(case_dir / f"{key}_{name}.csv")
        top = usage.kw.dropna().nlargest(200)
        return float(pd.Series(slot_start(pd.DatetimeIndex(top.index), 15).hour).mean())

    assert peak_centre("C1", "오전 피크형") == pytest.approx(11.7, abs=1.0)
    # 5시간 뒤로 밀었다. 중심이 정확히 5시간 옮겨져야 한다.
    assert peak_centre("C2", "오후 피크형") - peak_centre("C1", "오전 피크형") == pytest.approx(
        5.0, abs=0.5
    )
    assert peak_hours("C6", "야간 피크형") <= {6, 7}  # 야간 창의 끝자락이 가장 높다


def test_flat_case_hits_the_target_load_factor(case_dir: Path) -> None:
    usage = load_usage(case_dir / "C3_평탄형.csv")
    load_factor = float(usage.kw.mean() / usage.kw.max())
    assert load_factor == pytest.approx(0.85, abs=0.01)


def test_weekend_case_raises_weekend_load(case_dir: Path) -> None:
    from kwise.quality.pattern import load_pattern

    usage = load_usage(case_dir / "C4_주말 가동형.csv")
    pattern = load_pattern(usage.kw, usage.meta.interval_minutes)
    assert pattern.weekend_ratio is not None
    assert pattern.weekend_ratio > 0.85  # 평일의 90% 를 목표로 올렸다


# --------------------------------------------------------------------- 타당성 판정


def test_every_validity_check_passes(study: CaseStudy) -> None:
    """**하나라도 실패하면 계산 오류다.** 실패 내역을 그대로 보여 준다."""
    failed = [item for item in check_case_study(study) if not item.passed]
    assert not failed, "\n".join(f"{item.scope} · {item.name} — {item.detail}" for item in failed)


def test_pv_zero_saves_exactly_nothing(study: CaseStudy) -> None:
    for result in study.results:
        frame = pd.DataFrame(list(result.pv_rows)).set_index("용량(kWp)")
        assert float(frame.loc[0.0, "총 절감액(원)"]) == 0.0
        assert float(frame.loc[0.0, "발전량(kWh)"]) == 0.0


def test_night_peak_case_proves_the_light_band_rule(study: CaseStudy) -> None:
    """**C6 — 관측 최대는 밤인데 요금적용전력은 낮에서 나온다** (5.2 ①)."""
    result = study.find("C6")
    observed = float(result.usage.kw.max())
    assert result.baseline.billing_demand_kw < observed * 0.5
    # 그리고 PV 기여가 오히려 커야 한다 — 대상 슬롯이 주간뿐이다.
    frame = pd.DataFrame(list(result.pv_rows)).set_index("용량(kWp)")
    reduction = result.baseline.billing_demand_kw - float(frame.loc[1000.0, "요금적용전력(kW)"])
    c1 = study.find("C1")
    c1_frame = pd.DataFrame(list(c1.pv_rows)).set_index("용량(kWp)")
    c1_reduction = c1.baseline.billing_demand_kw - float(c1_frame.loc[1000.0, "요금적용전력(kW)"])
    assert reduction / result.baseline.billing_demand_kw > (
        c1_reduction / c1.baseline.billing_demand_kw
    )


def test_afternoon_peak_saturates_immediately(study: CaseStudy) -> None:
    """C2 — PV 가 오후 피크를 깎으면 저녁 슬롯이 새 최대가 되어 더 줄지 않는다."""
    frame = pd.DataFrame(list(study.find("C2").pv_rows)).set_index("용량(kWp)")
    savings = frame.loc[[500.0, 1000.0, 2000.0], "기본요금 절감액(원)"]
    assert savings.nunique() == 1  # 500 kWp 에서 이미 포화


def test_sensitivity_upper_bound_is_not_pinned(study: CaseStudy) -> None:
    """**단조성을 요구하지 않는다.** 상한 시나리오가 케이스·용량에 따라 바뀐다.

    실제로 6케이스 × 3용량 어디에서도 '첨예형'이 상한이 아니다 — 총량이 보존되므로
    첨예도를 올리면 어깨가 낮아지고, 피크 저감은 어깨 출력에 걸리기 때문이다.
    """
    rows = [row for result in study.results for row in result.sensitivity_rows]
    frame = pd.DataFrame(rows)
    base_fee = frame[frame["지표"] == "기본요금 절감액(원)"]
    assert not base_fee.empty
    assert "첨예형" not in set(base_fee["상한 시나리오"])
    assert set(base_fee["상한 시나리오"]) <= {"평탄형", "기준"}


def test_generation_is_preserved_across_scenarios(study: CaseStudy) -> None:
    rows = [row for result in study.results for row in result.sensitivity_rows]
    frame = pd.DataFrame(rows)
    generation = frame[frame["지표"] == "발전량(kWh)"]
    spread = (generation["범위 상한"] - generation["범위 하한"]) / generation["범위 상한"]
    assert float(spread.max()) < 0.01


def test_case_study_runs_sequentially_and_hits_the_weather_cache(study: CaseStudy) -> None:
    """모든 케이스가 같은 좌표·기간이라 기상은 첫 건만 취득한다."""
    assert study.weather_calls == 0  # 사전 취득분·캐시가 이미 채워져 있다
    assert len(study.results) == 6
    assert study.elapsed_sec > 0


def test_capacities_axis_matches_the_requirement() -> None:
    assert DEFAULT_CAPACITIES_KWP == (0.0, 500.0, 1_000.0, 2_000.0)


# --------------------------------------------------------------------- 경계 케이스 (11.3)


def _uniform_rows(
    start: str, days: int, kwh: float = 100.0, interval: int = 15
) -> list[tuple[str, float]]:
    from tests._synthetic import make_labels

    rows: list[tuple[str, float]] = []
    for offset in range(days):
        day = (pd.Timestamp(start) + pd.Timedelta(days=offset)).strftime("%Y-%m-%d")
        rows.extend((label, kwh) for label in make_labels(day, interval))
    return rows


def test_short_period_under_twelve_months(tmp_path: Path, tariff: TariffTable) -> None:
    """12개월 미만 — '연간' 이라 부르지 않고 12개월 환산으로 표기한다 (5.5)."""
    from tests._synthetic import write_csv

    usage = load_usage(write_csv(tmp_path / "short.csv", _uniform_rows("2023-07-01", 90)))
    result = calculate_bill(usage, tariff, GENERAL_B)
    assert result.base_fee_months < 12.0
    assert result.base_fee_months == pytest.approx(3.0, abs=0.1)
    assert "2023-07" in result.period_label


def test_non_calendar_period(tmp_path: Path, tariff: TariffTable) -> None:
    """역년이 아닌 기간 — 앞뒤가 부분 월이다 (5.3.2)."""
    from tests._synthetic import write_csv

    usage = load_usage(write_csv(tmp_path / "partial.csv", _uniform_rows("2023-07-15", 60)))
    result = calculate_bill(usage, tariff, GENERAL_B)
    factors = result.monthly["base_fee_factor"]
    assert len(factors) == 3  # 7·8·9월에 걸친다
    assert factors.iloc[0] < 1.0 and factors.iloc[-1] < 1.0  # 앞뒤가 부분 월
    assert result.base_fee_months == pytest.approx(float(factors.sum()))


def test_hourly_interval_data(tmp_path: Path, tariff: TariffTable) -> None:
    """1시간 간격 — 간격을 자동 판정하고 kW 환산이 달라진다."""
    from tests._synthetic import write_csv

    rows = _uniform_rows("2023-07-01", 31, kwh=400.0, interval=60)
    usage = load_usage(write_csv(tmp_path / "hourly.csv", rows))
    assert usage.meta.interval_minutes == 60
    assert float(usage.kw.max()) == pytest.approx(400.0)  # 400 kWh/1h = 400 kW
    result = calculate_bill(usage, tariff, GENERAL_B)
    assert result.billing_demand_kw == pytest.approx(400.0)


def test_ten_percent_missing(tmp_path: Path, tariff: TariffTable) -> None:
    """결측률 10% — 결측은 보간하지 않고 계산에서 뺀다 (4.2)."""
    from tests._synthetic import write_csv

    rows = _uniform_rows("2023-07-01", 31)
    kept = [row for index, row in enumerate(rows) if index % 10 != 0]
    usage = load_usage(write_csv(tmp_path / "missing.csv", kept))
    assert usage.meta.missing_ratio == pytest.approx(0.10, abs=0.01)
    report = check_quality(usage)
    assert report.warnings
    result = calculate_bill(usage, tariff, GENERAL_B, quality=report)
    assert result.total_won > 0
    assert result.notes or result.warnings


def test_contract_excess_is_flagged(tmp_path: Path, tariff: TariffTable) -> None:
    """계약전력 초과 구간 — 요금적용전력과 **별개로** 검사한다 (6세션 결정).

    03:00(경부하)에만 초과를 넣는다. 요금적용전력에는 영향이 없지만
    **초과사용부가금 대상**이므로 별도로 세어야 한다.
    """
    from kwise.diagnose.contract import assess_contract
    from tests._synthetic import make_labels, write_csv

    rows: list[tuple[str, float]] = []
    for offset in range(31):
        day = (pd.Timestamp("2023-07-01") + pd.Timedelta(days=offset)).strftime("%Y-%m-%d")
        for label in make_labels(day):
            rows.append((label, 300.0 if label.endswith(" 03:00") else 100.0))
    usage = load_usage(write_csv(tmp_path / "excess.csv", rows))
    result = calculate_bill(usage, tariff, GENERAL_B)

    adequacy = assess_contract(
        usage.kw,
        contract_kw=1_000.0,
        billing_demand_kw=result.billing_demand_kw,
        base_rate_won_per_kw=result.base_rate_won_per_kw,
        base_fee_months=result.base_fee_months,
        contract_floor_ratio=0.30,
    )
    assert adequacy.over_contract_slots > 0  # 계약 1,000 kW 를 넘는 슬롯이 있다
    assert adequacy.max_demand_kw == pytest.approx(1_200.0)
    assert result.billing_demand_kw == pytest.approx(400.0)  # 경부하는 대상이 아니다


def test_period_outside_the_weather_archive_stops(tmp_path: Path) -> None:
    """**사전 취득 범위 밖 + API 실패 → 중단하고 안내한다** (7.5).

    0 으로 계산하거나 인접 격자로 대체하지 않는다.
    """

    def dead(request: WeatherRequest) -> pd.DataFrame:
        raise WeatherUnavailableError("Open-Meteo 연결에 실패했습니다. (모의)")

    request = WeatherRequest(37.5, 127.0, dt.date(2022, 1, 1), dt.date(2022, 12, 31))
    with pytest.raises(WeatherUnavailableError) as caught:
        load_weather(request, fetch=dead, cache_dir=tmp_path / "cache")
    message = str(caught.value)
    assert "2022-01 ~ 2022-12" in message
    assert "tools\\fetch_weather.py" in message
