"""케이스 스터디 (요구사항서 11.3) 와 경계 케이스.

**타당성 판정이 하나라도 실패하면 계산 오류다.** 여기서 막는다.

케이스 일곱(합성 여섯 + 실측 하나)을 다 돌리면 100초쯤 걸린다. 회귀에서는
**판정 결과만** 확인하고, 케이스 생성·경계 케이스는 가벼운 경로로 따로 본다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest

from kwise.io import load_usage, slot_start
from kwise.notices import texts
from kwise.pv import (
    WeatherRequest,
    WeatherUnavailableError,
    load_weather,
    weather_cache_path,
)
from kwise.pv.region import find_region
from kwise.quality import check_quality
from kwise.report.casestudy import (
    CASE_REGION_KEY,
    DEFAULT_CAPACITIES_KWP,
    CaseStudy,
    build_case_definitions,
    run_case_study,
)
from kwise.report.validity import check_case_study
from kwise.tariff import (
    TariffSelection,
    TariffTable,
    apply_contract_floor,
    calculate_bill,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CASE_DIR = PROJECT_ROOT / "input" / "cases"
GENERAL_B = TariffSelection("general_b", "high_a", "I")


@pytest.fixture(scope="module")
def case_dir() -> Path:
    if not CASE_DIR.is_dir() or not any(CASE_DIR.glob("C*.csv")):
        pytest.skip(f"케이스 파일이 없습니다: {CASE_DIR} (tools\\make_cases.py 실행)")
    return CASE_DIR


@dataclass(frozen=True)
class WeatherCacheState:
    """스터디를 돌리기 **전** 기상 캐시가 어떤 상태였는지.

    :func:`run_case_study` 가 세는 ``weather_calls`` 의 기대값은 환경이 정한다.
    찬 캐시에서는 첫 건을 받아 와 1, 이미 차 있으면 0 이다. 상수로 박으면
    **새 PC 에서만 실패하는 시험**이 된다 (2026-08-15 에 실제로 겪었다).
    """

    requests: int
    """케이스들이 요구하는 **서로 다른** 기상 요청의 수."""
    cold: int
    """그중 실행 전에 캐시에 없던 것의 수 = 취득이 일어날 횟수."""


@pytest.fixture(scope="module")
def weather_cache_state(case_dir: Path) -> WeatherCacheState:
    """케이스들이 쓸 기상 캐시 경로를 **스터디보다 먼저** 들여다본다.

    ``study`` 픽스처가 이것에 의존하므로 순서가 보장된다 — 스터디가 캐시를
    채우기 전의 상태를 봐야 기대값이 맞는다.

    **좌표는 케이스마다 읽는다** (95세션 0절). 합성 여섯은 한 좌표를 나눠 쓰지만
    실측(R1)은 자기 소재지를 쓴다 — `CASE_REGION_KEY` 를 모두에 먹이면 실측
    벌의 요청을 **없는 것으로 세게 된다.**
    """
    paths = set()
    for definition in build_case_definitions(case_dir):
        region = find_region(definition.region_key)
        usage = load_usage(definition.usage_path)
        request = WeatherRequest.for_index(
            pd.DatetimeIndex(usage.kw.index), region.latitude, region.longitude
        )
        paths.add(weather_cache_path(request))
    return WeatherCacheState(
        requests=len(paths),
        cold=sum(1 for path in paths if not path.is_file()),
    )


@pytest.fixture(scope="module")
def study(case_dir: Path, tariff: TariffTable, weather_cache_state: WeatherCacheState) -> CaseStudy:
    """케이스 일곱 × PV 4단계 × 감도 3종. **순차로 돈다.**"""
    return run_case_study(build_case_definitions(case_dir), tariff)


# --------------------------------------------------------------------- 케이스 정의


def test_six_synthetic_cases_and_one_measured(case_dir: Path) -> None:
    """C4 만 산업용(을)이고 **R1 만 실측이자 갑Ⅱ 다**.

    C4 는 봄·가을 주말 할인 특례를 태우려고 갈랐고, R1 은 갑Ⅱ 경로를 회귀에
    세우려고 붙였다 (95세션 0절) — C1~C6 는 `general_b`·`industrial_b` 라
    **갑Ⅱ 가 매 판 확인되는 자리가 없었다.**
    """
    definitions = build_case_definitions(case_dir)
    assert [item.key for item in definitions] == ["C1", "C2", "C3", "C4", "C5", "C6", "R1"]
    industrial = [item.key for item in definitions if item.contract_type == "industrial_b"]
    assert industrial == ["C4"]
    type_a_2 = [item.key for item in definitions if item.contract_type == "general_a_2"]
    assert type_a_2 == ["R1"]

    # **좌표와 계약전력이 갈리는 것도 R1 하나다.** 합성 여섯은 사전 취득분
    # 격자에 걸리는 한 좌표를 나눠 쓰고 계약전력은 관측 최대의 1.1배 가정이다.
    synthetic = [item for item in definitions if item.key != "R1"]
    assert {item.region_key for item in synthetic} == {CASE_REGION_KEY}
    assert all(item.contract_kw is None for item in synthetic)
    measured = next(item for item in definitions if item.key == "R1")
    assert measured.region_key == "경기도/용인시"
    assert measured.contract_kw == 290.0  # 이 건물이 실제로 쓰는 계약전력


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


def test_케이스_스터디가_하한_갈래를_C6_에서_돈다(
    study: CaseStudy, tariff: TariffTable
) -> None:
    """**회귀가 하한 갈래를 밟는 유일한 자리다** (107세션 3절 · ②-15 · ②-30).

    105세션 6절이 박아 둔 못은 그 반대를 봤다 — 「케이스 스터디가 하한 갈래를
    **한 번도 안 돈다**」. 케이스 스터디의 기준선이 `BillingOptions` 에
    계약전력을 안 넣어 일곱 벌 전부 걸린 달이 0 이었고, 105세션이 지은
    「걸린 달이 판정을 가른다」 갈래를 104판정이 하나도 밟지 않았다.

    **107세션 3절이 그 아홉 자리를 닫자 그 못이 스스로 빨개졌다** —
    `{C1: 0 … C6: 13 … R1: 0}`. **사실이 뒤집혔으므로 걷고, 새 사실을 값으로
    보는 이 못으로 옮겼다.** 안 도는 갈래를 세던 자리가 이제 **도는 갈래를
    센다.**

    하한이 이기는 벌은 케이스 일곱 가운데 **C6 하나뿐이다** (107세션 2절 ㄴ).
    경부하가 요금적용전력 산정에서 빠지므로 대상 수요(2,801.0 kW)가 계약전력
    하한(12,012.7 × 30% = 3,603.81 kW) **아래**에 있다 — 그래서 열세 달이
    전부 걸린다.
    """
    bound = {
        result.definition.key: len(result.baseline.floor_bound_months)
        for result in study.results
    }
    c6 = study.find("C6")
    assert bound["C6"] == len(c6.baseline.monthly), f"C6 에서 하한이 빠졌다 — {bound}"
    others = {key: value for key, value in bound.items() if key != "C6"}
    assert set(others.values()) == {0}, f"C6 말고 하한이 걸린 벌이 생겼다 — {bound}"

    # **하한이 요금적용전력을 붙든다.** 걸린 달의 기준이 수요가 아니라 하한이
    # 라는 것을 값으로 못 박는다 — 이 성질이 C6 의 PV 기여를 0 으로 만든다.
    ratio = c6.baseline.contract_floor_ratio
    assert ratio is not None
    # **항등식이 조문을 다시 적지 않는다** (S119 ⑳). 요금적용전력은 하한을 씌운
    # 뒤 1kW 로 접은 값이고(제7조 ①), 그 식은 :func:`apply_contract_floor` 하나가
    # 들고 있다 — 여기 ``contract_kw * ratio`` 로 다시 적으면 **실물이 갈려도 이
    # 시험은 제 식으로 통과한다.** 수요를 0 으로 넣어 하한만 남긴다.
    pinned = apply_contract_floor({"C6": 0.0}, contract_kw=c6.contract_kw, floor_ratio=ratio)
    assert c6.baseline.billing_demand_kw == pytest.approx(pinned["C6"])

    # **계약전력을 빼면 도로 안 걸린다.** 걸리는 것이 자료의 성질만이 아니라
    # **밑둥이 계약전력을 주기 때문**이라는 것을 양쪽에서 못 박는다.
    without_contract = calculate_bill(c6.usage, tariff, c6.definition.selection)
    assert without_contract.floor_bound_months == ()
    assert without_contract.total_won < c6.baseline.total_won


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
    # **그리고 PV 가 한 칸도 못 내린다** (107세션 3절). 밑둥이 계약전력을 안
    # 주던 동안은 반대였다 — 대상 슬롯이 주간뿐이라 PV 기여가 C1 보다 컸다
    # (3.05% vs 1.80%). 밑둥을 닫으니 하한 3,603.81 kW 가 요금적용전력을
    # 붙들어 **용량을 아무리 키워도 값이 그대로다.** C1 은 그대로 내려간다.
    frame = pd.DataFrame(list(result.pv_rows)).set_index("용량(kWp)")
    reduction = result.baseline.billing_demand_kw - float(frame.loc[1000.0, "요금적용전력(kW)"])
    c1 = study.find("C1")
    c1_frame = pd.DataFrame(list(c1.pv_rows)).set_index("용량(kWp)")
    c1_reduction = c1.baseline.billing_demand_kw - float(c1_frame.loc[1000.0, "요금적용전력(kW)"])
    assert reduction == pytest.approx(0.0)
    assert c1_reduction > 0.0
    # 2,000 kWp 까지 키워도 그대로다 — 용량이 모자라서가 아니다.
    assert float(frame.loc[2000.0, "요금적용전력(kW)"]) == pytest.approx(
        result.baseline.billing_demand_kw
    )


def test_afternoon_peak_saturates_immediately(study: CaseStudy) -> None:
    """C2 — PV 가 오후 피크를 깎으면 저녁 슬롯이 새 최대가 되어 더 줄지 않는다."""
    frame = pd.DataFrame(list(study.find("C2").pv_rows)).set_index("용량(kWp)")
    savings = frame.loc[[500.0, 1000.0, 2000.0], "기본요금 절감액(원)"]
    assert savings.nunique() == 1  # 500 kWp 에서 이미 포화


def test_용인_실측_회귀값이_그대로다_청구서_118kW_와는_다른_계열이다(study: CaseStudy) -> None:
    """R1 용인 실측의 회귀값. **두 값이 다른 계열이라는 것을 이름에 적어 둔다.**

    여기 박는 **132.3 kW 는 도구가 자료에서 센 최대수요**다 (15분 실측,
    2026-01-20 09:15). 청구서의 **118 kW 는 한전 고지서의 월별 최대수요전력에서
    나온 요금적용전력**이고 (`docs\\BILL_CHECK.md` 1절), 두 계열이 어긋나는
    까닭은 아직 모른다 — 가설 일곱이 죽었다(같은 문서 2절). **그러므로 118 로는
    통과할 수 없다.** 118 을 박으면 도구가 아니라 한전 고지서를 시험하게 된다.

    셋을 박는 근거는 대형 정본 회귀값(관측 최대 5,293.44 kW · 요금적용전력
    5,293 kW · 49.0% · 13.5%)과 같다 — **바뀌면 계산을 건드린 것**이다.
    총 요금 액수는 요금표 개정에 딸려 움직이므로 여기 박지 않는다.

    **S119 에 관측 최대와 요금적용전력을 갈랐다** (⑳ · 제7조 ①). 앞서는 두
    줄이 다 `132.28` 이라 **한 이름처럼 서 있었는데**, 요금적용전력만 1kW 로
    접혀 **132** 가 된다. 기본요금 비중도 20.1 → **20.0%** 로 딸려 왔다.
    """
    result = study.find("R1")
    assert result.definition.contract_type == "general_a_2"
    assert result.definition.option == "II"
    assert result.contract_kw == 290.0

    assert float(result.usage.kw.max()) == pytest.approx(132.28, abs=0.01)  # 관측값 — 안 접는다
    assert result.baseline.billing_demand_kw == pytest.approx(132.0)  # 요금 — 1kW 단위
    assert result.usage.meta.total_kwh / 1000.0 == pytest.approx(476.0, abs=0.1)
    assert result.diagnosis.pattern.load_factor == pytest.approx(0.411, abs=0.001)
    base_share = result.baseline.total_base_won / result.baseline.total_won
    assert base_share == pytest.approx(0.200, abs=0.001)


def test_sensitivity_upper_bound_is_not_pinned(study: CaseStudy) -> None:
    """**단조성을 요구하지 않는다.** 상한 시나리오가 케이스·용량에 따라 바뀐다.

    실제로 7케이스 × 3용량 어디에서도 '첨예형'이 상한이 아니다 — 총량이 보존되므로
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


def test_synthetic_cases_share_one_weather_request(
    weather_cache_state: WeatherCacheState,
) -> None:
    """**캐시 적중의 근거**는 합성 여섯이 같은 좌표·기간을 쓴다는 것이다.

    캐시가 차 있든 비었든 이 성질은 변하지 않는다. 캐시 적중 횟수보다 이쪽이
    본질이라 따로 세운다 — 합성 케이스의 기간이 갈라지면 여기서 먼저 걸린다.

    **둘째 요청은 실측(R1)이다** (95세션 0절). 좌표(용인)도 기간(2025-08~2026-08)도
    합성과 다르므로 요청이 하나 더 서는 것이 정상이다 — **하나로 돌아가면 실측이
    합성 좌표로 계산되고 있다는 뜻**이라 여기서 걸린다.
    """
    assert weather_cache_state.requests == 2


def test_case_study_runs_sequentially_and_hits_the_weather_cache(
    study: CaseStudy, weather_cache_state: WeatherCacheState
) -> None:
    """기상은 **캐시에 없던 것만** 취득한다. 나머지는 캐시를 탄다.

    기대값을 0 으로 박아 두었더니 캐시가 빈 새 PC 에서 1 이 나와 실패했다
    (2026-08-15). 코드가 아니라 시험이 환경에 기대고 있었다. 이제 실행 전
    캐시 상태에서 기대값을 끌어오므로 **찬 캐시에서도 더운 캐시에서도 같은
    성질을 잰다** — 취득은 요청 하나당 많아야 한 번이다.

    캐시가 고장 나면 일곱 케이스가 저마다 취득해 7 이 되고, 여기서 걸린다.
    """
    assert study.weather_calls == weather_cache_state.cold
    assert study.weather_calls <= weather_cache_state.requests
    assert len(study.results) == 7
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
    assert texts(report.notices)
    result = calculate_bill(usage, tariff, GENERAL_B, quality=report)
    assert result.total_won > 0
    assert texts(result.notices) or texts(result.notices)


def test_contract_excess_is_flagged(tmp_path: Path, tariff: TariffTable) -> None:
    """계약전력 초과 구간 — 요금적용전력과 **별개로** 검사한다 (6세션 결정).

    03:00(경부하)에만 초과를 넣는다. 요금적용전력에는 영향이 없지만
    **초과사용부가금 대상**이므로 별도로 세어야 한다.
    """
    from kwise.diagnose.contract import assess_contract
    from kwise.measures.contract import evaluate_contract_adjustment
    from tests._synthetic import make_labels, write_csv

    rows: list[tuple[str, float]] = []
    for offset in range(31):
        day = (pd.Timestamp("2023-07-01") + pd.Timedelta(days=offset)).strftime("%Y-%m-%d")
        for label in make_labels(day):
            rows.append((label, 300.0 if label.endswith(" 03:00") else 100.0))
    usage = load_usage(write_csv(tmp_path / "excess.csv", rows))
    result = calculate_bill(usage, tariff, GENERAL_B)

    adjustment = evaluate_contract_adjustment(
        usage, result, contract_kw=1_000.0, contract_floor_ratio=0.30
    )
    adequacy = assess_contract(adjustment, billing_demand_kw=result.billing_demand_kw)
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
