"""진행 표시와 실측 (요구사항서 10.6·11.4).

**여기서 지키는 것 여섯**

    ① 콜백 없이 불러도 그대로 돈다 (``progress`` 기본값 ``None``)
    ② **계산 모듈이 streamlit 을 import 하지 않는다**
    ③ 단계 콜백이 순서대로 온다
    ④ 건너뛴 단계는 ``skipped`` 로 오고 진행률도 채운다
    ⑤ CLI 가 같은 콜백에 rich 를 붙인다
    ⑥ 요금 계산을 빠르게 고쳤어도 **회귀값이 한 자리도 바뀌지 않았다**
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import pytest

from kwise.benchmark import BenchmarkResult, bill_call_counter, current_rss_bytes, measure
from kwise.compare import CombinationSpec, compare_combinations, sensitivity_comparison
from kwise.io import UsageData
from kwise.measures import PvCostInput, solar_curve
from kwise.progress import (
    NULL_PROGRESS,
    STAGE_WEIGHT_KEYS,
    STAGES,
    CallbackProgress,
    NullProgress,
    ProgressReporter,
    ProgressState,
    RichProgress,
    StageRunner,
    expected_seconds,
    measure_total,
    record,
    stage_by_key,
    stage_weights,
    total_seconds,
)
from kwise.quality import QualityReport
from kwise.tariff import BillingOptions, BillingResult, TariffSelection, TariffTable

SELECTION = TariffSelection("general_b", "high_a", "I")


# ===================================================================== 기록용 보고자


@dataclass
class Recorder:
    """콜백이 온 순서를 그대로 담는다."""

    calls: list[tuple[str, object, object]] = field(default_factory=list)

    def stage(self, name: str, total_steps: int = 0) -> None:
        self.calls.append(("stage", name, total_steps))

    def step(self, current: int, detail: str | None = None) -> None:
        self.calls.append(("step", current, detail))

    def done(self, name: str) -> None:
        self.calls.append(("done", name, None))

    def skipped(self, name: str, reason: str) -> None:
        self.calls.append(("skipped", name, reason))

    @property
    def kinds(self) -> list[str]:
        return [item[0] for item in self.calls]

    def of(self, kind: str) -> list[tuple[str, object, object]]:
        return [item for item in self.calls if item[0] == kind]


# ===================================================================== ① 콜백 없이


def test_콜백_없이_용량_곡선이_돈다(
    sample_usage: UsageData, tariff: TariffTable, sample_bill: BillingResult
) -> None:
    """``progress`` 는 **선택 인자다.** 주지 않는 것이 여전히 정상 경로다."""
    curve = solar_curve(
        sample_usage,
        tariff,
        SELECTION,
        pd.Series(0.0, index=sample_usage.kw.index),
        max_capacity_kwp=100.0,
        steps=2,
        baseline=sample_bill,
    )
    assert len(curve.points) == 3


def test_콜백_없이_조합_비교가_돈다(
    sample_usage: UsageData, tariff: TariffTable, sample_bill: BillingResult
) -> None:
    result = compare_combinations(
        sample_usage,
        tariff,
        (CombinationSpec(name="기준선", selection=SELECTION),),
        baseline_bill=sample_bill,
    )
    assert len(result.combinations) == 1


def test_record_가_None_을_받아_준다() -> None:
    assert record(None) is NULL_PROGRESS
    recorder = Recorder()
    assert record(recorder) is recorder


def test_아무것도_하지_않는_보고자() -> None:
    null = NullProgress()
    null.stage("read")
    null.step(1, "x")
    null.done("read")
    null.skipped("read", "이유")
    assert isinstance(null, ProgressReporter)


# ===================================================================== ② streamlit 격리


def _imports(path: Path, *, module_level_only: bool = False) -> set[str]:
    """import 하는 최상위 패키지 이름.

    ``module_level_only`` 이면 **모듈 최상단만** 본다 — 함수 안에서 늦게 부르는
    것은 "import 만으로 끌어오지 않는다" 의 반대가 아니다.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = tree.body if module_level_only else list(ast.walk(tree))
    found: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found


def test_계산_모듈이_streamlit_을_import_하지_않는다() -> None:
    """**이것이 진행 표시 설계의 전부다.** 화면은 콜백을 넘길 뿐이다."""
    offenders: list[str] = []
    for path in Path("src/kwise").rglob("*.py"):
        if "ui" in path.parts:  # 화면만 예외다
            continue
        if "streamlit" in _imports(path):
            offenders.append(str(path))
    assert offenders == [], f"계산 모듈이 streamlit 을 끌어옵니다: {offenders}"


def test_진행_모듈_자체도_streamlit_과_무관하다() -> None:
    assert "streamlit" not in _imports(Path("src/kwise/progress.py"))


def test_rich_를_늦게_불러온다() -> None:
    """``import kwise.progress`` 만으로 rich 를 끌어오지 않는다."""
    path = Path("src/kwise/progress.py")
    assert "rich" not in _imports(path, module_level_only=True)
    assert "rich" in _imports(path), "CLI 보고자는 함수 안에서 rich 를 부른다"


# ===================================================================== ③ 순서


def test_용량_곡선이_단계마다_보고한다(
    sample_usage: UsageData, tariff: TariffTable, sample_bill: BillingResult
) -> None:
    recorder = Recorder()
    solar_curve(
        sample_usage,
        tariff,
        SELECTION,
        pd.Series(0.0, index=sample_usage.kw.index),
        max_capacity_kwp=100.0,
        steps=4,
        baseline=sample_bill,
        progress=recorder,
    )
    steps = recorder.of("step")
    assert [item[1] for item in steps] == [0, 1, 2, 3, 4]  # 0 kWp 포함
    assert "용량 곡선 4/4" in str(steps[-1][2])


def test_조합_비교가_조합마다_보고한다(
    sample_usage: UsageData, tariff: TariffTable, sample_bill: BillingResult
) -> None:
    recorder = Recorder()
    specs = (
        CombinationSpec(name="기준선", selection=SELECTION),
        CombinationSpec(
            name="선택요금 전환", selection=TariffSelection("general_b", "high_a", "II")
        ),
    )
    compare_combinations(sample_usage, tariff, specs, baseline_bill=sample_bill, progress=recorder)
    details = [str(item[2]) for item in recorder.of("step")]
    assert details == ["1/2 기준선", "2/2 선택요금 전환"]


def test_감도가_시나리오마다_보고한다(
    sample_usage: UsageData,
    tariff: TariffTable,
    sample_bill: BillingResult,
    sample_unit_pv: pd.Series,
) -> None:
    recorder = Recorder()
    sensitivity_comparison(
        sample_usage,
        tariff,
        CombinationSpec(name="PV", selection=SELECTION, pv_capacity_kwp=100.0),
        baseline_bill=sample_bill,
        unit_pv_kw_per_kwp=sample_unit_pv,
        progress=recorder,
    )
    assert len(recorder.of("step")) == 3
    assert all("/3 " in str(item[2]) for item in recorder.of("step"))


def test_단계_진행자가_시작과_완료를_짝지운다() -> None:
    recorder = Recorder()
    runner = StageRunner(recorder)
    with runner.running("read"):
        pass
    with runner.running("solar", total_steps=20) as report:
        report.step(10, "절반")
    assert recorder.kinds == ["stage", "done", "stage", "step", "done"]
    assert recorder.calls[0][1] == "read"
    assert recorder.calls[2][2] == 20


def test_예외가_나도_단계를_닫는다() -> None:
    """닫지 않으면 진행률이 그 자리에서 멈춘다."""
    recorder = Recorder()
    runner = StageRunner(recorder)
    with pytest.raises(RuntimeError), runner.running("solar"):
        raise RuntimeError("계산 실패")
    assert recorder.kinds == ["stage", "done"]


# ===================================================================== ④ 건너뜀


def test_건너뛴_단계도_진행률을_채운다() -> None:
    """**막대가 멈춘 것처럼 보이면 안 된다.**"""
    seen: list[ProgressState] = []
    reporter = CallbackProgress(sink=seen.append)
    reporter.skipped("weather", "캐시 적중")
    assert seen[-1].skipped
    assert seen[-1].detail == "캐시 적중"
    assert seen[-1].fraction == pytest.approx(stage_weights()["weather"])


def test_건너뜀에_사유가_함께_온다() -> None:
    recorder = Recorder()
    StageRunner(recorder).skip("weather", "캐시 적중")
    assert recorder.of("skipped") == [("skipped", "weather", "캐시 적중")]


def test_캐시_적중이면_건너뜀으로_보고한다(sample_usage: UsageData, tariff: TariffTable) -> None:
    """케이스 스터디 CLI 의 실제 경로 — 단위 프로파일을 넘겨받으면 기상을 건너뛴다."""
    from kwise.report.casestudy import CaseDefinition, run_one_case

    recorder = Recorder()
    definition = CaseDefinition(
        key="C1",
        name="오전 피크형",
        usage_path=Path("input") / "cases" / "C1_오전 피크형.csv",
        contract_type="general_b",
    )
    run_one_case(
        definition,
        tariff,
        capacities_kwp=(0.0,),
        unit_pv=pd.Series(0.0, index=sample_usage.kw.index),
        progress=recorder,
    )
    skipped = recorder.of("skipped")
    assert [item[1] for item in skipped] == ["weather"]
    assert "넘겨받" in str(skipped[0][2])


def test_케이스_실행이_여덟_단계_순서를_지킨다(
    sample_usage: UsageData, tariff: TariffTable
) -> None:
    from kwise.report.casestudy import CaseDefinition, run_one_case

    recorder = Recorder()
    run_one_case(
        CaseDefinition(
            key="C1",
            name="오전 피크형",
            usage_path=Path("input") / "cases" / "C1_오전 피크형.csv",
            contract_type="general_b",
        ),
        tariff,
        capacities_kwp=(0.0,),
        unit_pv=pd.Series(0.0, index=sample_usage.kw.index),
        progress=recorder,
    )
    opened = [item[1] for item in recorder.calls if item[0] in {"stage", "skipped"}]
    assert opened == ["read", "quality", "diagnose", "weather", "solar", "measures"]
    # 순서가 STAGES 정의와 어긋나지 않는다
    order = {stage.key: stage.number for stage in STAGES}
    assert [order[str(key)] for key in opened] == sorted(order[str(key)] for key in opened)


# ===================================================================== 진행률 계산


def test_가중치가_실측값이고_합이_1이다() -> None:
    """**균등 분할하지 않는다** — 태양광 한 구간이 전체의 절반이다."""
    weights = stage_weights()
    assert set(weights) == set(STAGE_WEIGHT_KEYS)
    assert sum(weights.values()) == pytest.approx(1.0)
    even = 1.0 / len(STAGES)
    assert weights["solar"] > even * 2, "실측이면 태양광이 균등분보다 훨씬 크다"
    assert weights["quality"] < even


def test_예상_소요가_가중치에서_나온다() -> None:
    assert expected_seconds("solar") == pytest.approx(stage_weights()["solar"] * total_seconds())
    assert expected_seconds("solar") > expected_seconds("quality")


def test_진행률이_가중치를_따른다() -> None:
    seen: list[ProgressState] = []
    reporter = CallbackProgress(sink=seen.append)
    weights = stage_weights()
    reporter.stage("read")
    reporter.done("read")
    assert seen[-1].fraction == pytest.approx(weights["read"])
    reporter.stage("solar", total_steps=10)
    reporter.step(5)
    assert seen[-1].fraction == pytest.approx(weights["read"] + weights["solar"] * 0.5)


def test_진행률이_1을_넘지_않는다() -> None:
    seen: list[ProgressState] = []
    reporter = CallbackProgress(sink=seen.append)
    for stage in STAGES:
        reporter.stage(stage.key)
        reporter.done(stage.key)
    assert seen[-1].fraction == pytest.approx(1.0)
    assert seen[-1].percent == 100


def test_한_줄_표기() -> None:
    """``"5/8 태양광 발전량 — 용량 곡선 12/20"``."""
    state = ProgressState(
        stage=stage_by_key("solar"), fraction=0.5, detail="용량 곡선 12/20", total_steps=20
    )
    assert state.line() == "5/8 태양광 발전량 — 용량 곡선 12/20"
    assert state.percent == 50


def test_없는_단계는_바로_실패한다() -> None:
    with pytest.raises(KeyError):
        stage_by_key("없는단계")


def test_켠_수단만_세어_6단계_total_을_잡는다() -> None:
    """**목록을 두 벌 두지 않는다** — MEASURE_CATALOG 를 센다."""
    assert measure_total(["solar", "ess"]) == 2
    assert measure_total([]) == 0
    assert measure_total(["없는수단"]) == 0


# ===================================================================== ⑤ CLI


def test_cli_가_같은_콜백에_rich_를_붙인다() -> None:
    from rich.console import Console

    console = Console(record=True, width=100)
    reporter = RichProgress(console)
    runner = StageRunner(reporter)
    with runner.running("read"):
        pass
    runner.skip("weather", "캐시 적중")
    with runner.running("solar", total_steps=20) as report:
        report.step(12, "용량 곡선 12/20")

    text = console.export_text()
    assert "1/8 데이터 읽기" in text
    assert "4/8 기상 데이터 확보" in text
    assert "캐시 적중" in text and "건너뜀" in text
    assert "용량 곡선 12/20" in text
    assert "%" in text  # 진행률이 나온다


def test_rich_보고자가_프로토콜을_만족한다() -> None:
    from rich.console import Console

    assert isinstance(RichProgress(Console(record=True)), ProgressReporter)


# ===================================================================== ⑥ 성능·회귀


def test_요금_계산_회귀값이_그대로다(
    sample_usage: UsageData, tariff: TariffTable, sample_report: QualityReport
) -> None:
    """월 라벨을 한 번만 푸는 최적화(10세션) 뒤에도 값이 같아야 한다.

    부록 B 의 확정 수치다. **한 자리라도 달라지면 최적화가 계산을 바꾼 것이다.**
    """
    from kwise.tariff import calculate_bill

    bill = calculate_bill(
        sample_usage,
        tariff,
        SELECTION,
        options=BillingOptions(contract_kw=5_800.0),
        quality=sample_report,
    )
    assert bill.total_base_won == pytest.approx(452_832_624.0)
    assert bill.billing_demand_kw == pytest.approx(5_293.44)
    assert bill.total_won == pytest.approx(3_351_117_349.0080004)


def test_용량_곡선이_시뮬레이션을_되풀이하지_않는다(
    sample_usage: UsageData, tariff: TariffTable, sample_bill: BillingResult
) -> None:
    """**발전량은 1회 계산 후 곱셈이다** (5세션 결정).

    단위 프로파일을 그대로 받으므로 pvlib 은 곡선 안에서 한 번도 돌지 않는다.
    되풀이한다면 20단계 × 감도 3 = 60회가 되어 그것이 최대 병목이 된다.
    """
    import sys

    simulate_module = sys.modules["kwise.pv.simulate"]
    calls = 0
    original = simulate_module.simulate

    def counting(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    simulate_module.simulate = counting  # type: ignore[attr-defined]
    try:
        solar_curve(
            sample_usage,
            tariff,
            SELECTION,
            pd.Series(0.0, index=sample_usage.kw.index),
            max_capacity_kwp=100.0,
            steps=20,
            baseline=sample_bill,
            cost=PvCostInput.of_unit_cost(1_000_000.0),
        )
    finally:
        simulate_module.simulate = original  # type: ignore[attr-defined]
    assert calls == 0


def test_용량_곡선이_요금을_단계마다_다시_계산한다(
    sample_usage: UsageData, tariff: TariffTable, sample_bill: BillingResult
) -> None:
    """되풀이되는 것은 발전량이 아니라 **요금**이다 — 실측에서 여기가 43% 다."""
    with bill_call_counter() as count:
        solar_curve(
            sample_usage,
            tariff,
            SELECTION,
            pd.Series(0.0, index=sample_usage.kw.index),
            max_capacity_kwp=100.0,
            steps=5,
            baseline=sample_bill,
        )
    assert count() == 6  # 0 kWp 포함 단계 수만큼. 그 이상이면 곱해진 것이다


def test_조합_비교가_조합당_요금을_한_번만_계산한다(
    sample_usage: UsageData, tariff: TariffTable, sample_bill: BillingResult
) -> None:
    """조합 × 감도 × 선택요금이 곱해지면 급증한다. 곱해지지 않는지 센다."""
    specs = tuple(CombinationSpec(name=f"조합{index}", selection=SELECTION) for index in range(4))
    with bill_call_counter() as count:
        compare_combinations(sample_usage, tariff, specs, baseline_bill=sample_bill)
    assert count() == len(specs)


# ===================================================================== 실측 도구


def test_구간을_재고_표로_낸다() -> None:
    result = BenchmarkResult()
    with measure(result, "가짜 구간", detail="설명"):
        sum(range(1000))
    assert len(result.stages) == 1
    frame = result.frame()
    assert list(frame.columns) == [
        "구간",
        "소요(초)",
        "비중(%)",
        "RSS 증감(MB)",
        "요금 재계산",
        "비고",
    ]
    assert frame.iloc[0]["구간"] == "가짜 구간"


def test_요금_호출_수를_센다(
    sample_usage: UsageData, tariff: TariffTable, sample_report: QualityReport
) -> None:
    """**모듈 속성으로 부른다.** 미리 묶어 둔 이름은 바꿔 끼워지지 않는다."""
    import kwise.tariff as tariff_package

    with bill_call_counter() as count:
        assert count() == 0
        tariff_package.calculate_bill(sample_usage, tariff, SELECTION, quality=sample_report)
        assert count() == 1


def test_세는_동안에만_바꿔_끼운다() -> None:
    """끝나면 원래 함수로 되돌린다 — 측정이 계산을 오염시키면 안 된다."""
    from kwise.tariff import engine

    original = engine.calculate_bill
    with bill_call_counter():
        assert engine.calculate_bill is not original
    assert engine.calculate_bill is original


def test_메모리를_읽거나_모른다고_한다() -> None:
    """**0 을 지어내지 않는다.** 못 읽으면 None 이다."""
    value = current_rss_bytes()
    assert value is None or value > 0


def test_가중치를_실측에서_뽑는다() -> None:
    result = BenchmarkResult()
    with measure(result, "느린 구간"):
        sum(range(200_000))
    with measure(result, "빠른 구간"):
        pass
    weights = result.weights({"solar": ("느린 구간",), "quality": ("빠른 구간",)})
    assert sum(weights.values()) == pytest.approx(1.0, abs=0.01)
    assert weights["solar"] > weights["quality"]


@pytest.fixture
def _no_stages() -> Iterator[None]:
    yield None


def test_잰_구간이_없으면_균등분으로_물러선다() -> None:
    empty = BenchmarkResult()
    weights = empty.weights({"read": ("없는 구간",), "solar": ("없는 구간2",)})
    assert weights == {"read": 0.5, "solar": 0.5}
