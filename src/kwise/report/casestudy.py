"""케이스 스터디 (요구사항서 11.3).

**실측이 1건뿐이라 샘플을 변형한 6종으로 돈다** (`tools\\make_cases.py` 가 만든다).
UI 없이 순수 함수로 계산하고 결과를 표로 낸다.

케이스마다 요금적용전력 3규칙(경부하 제외·대상월 한정·계약전력 하한)이 **다르게**
작동해야 한다. 같은 결과가 나오면 규칙이 실제로는 걸리지 않고 있다는 뜻이다.

    C1 오전 피크형   PV 최성기와 피크가 어느 정도 겹친다
    C2 오후 피크형   최대부하 시간대에 피크가 있다
    C3 평탄형        피크가 없어 기본요금 절감 여지가 거의 없다
    C4 주말 가동형   산업용(을) 봄·가을 주말 할인 특례
    C5 겨울 피크형   대상월인데 PV 발전이 약하다
    C6 야간 피크형   **관측 최대는 밤인데 요금적용전력은 낮에서 나온다**

케이스는 **순차로** 돈다. 여섯 벌의 시계열을 동시에 들지 않는다 (메모리 규약).
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from kwise.compare import (
    CombinationSpec,
    sensitivity_comparison,
    sensitivity_ranges,
)
from kwise.diagnose import ContractInfo, Diagnosis, diagnose
from kwise.io import UsageData, load_usage
from kwise.measures import (
    PvCostInput,
    evaluate_demand_response,
    evaluate_power_factor,
    evaluate_tariff_switch,
    solar_curve,
    unit_generation_kw,
)
from kwise.measures.ess import (
    EssOptimum,
    EssOptimumPoint,
    ess_target_curve,
    refine_ess_target,
)
from kwise.notices import texts
from kwise.progress import ProgressReporter, StageRunner, record
from kwise.pv import (
    ArrayConfig,
    PvSystemConfig,
    WeatherData,
    WeatherRequest,
    load_weather,
)
from kwise.pv.region import find_region
from kwise.quality import QualityReport, check_quality
from kwise.tariff import (
    BillingResult,
    TariffSelection,
    TariffTable,
    calculate_bill,
    switchable_selections,
)

__all__ = [
    "CASE_REGION_KEY",
    "DEFAULT_CAPACITIES_KWP",
    "CaseDefinition",
    "CaseResult",
    "CaseStudy",
    "build_case_definitions",
    "run_case_study",
    "run_one_case",
]

# 케이스 좌표. 사전 취득분 격자(37.50, 127.00)에 걸린다 — 네트워크를 타지 않는다.
CASE_REGION_KEY = "서울특별시/강남구"

# PV 용량 축 (11.3). 0 이 있어야 "PV 0 이면 절감 0" 을 확인할 수 있다.
DEFAULT_CAPACITIES_KWP: tuple[float, ...] = (0.0, 500.0, 1_000.0, 2_000.0)

# 계약전력은 케이스마다 관측 최대의 1.1배로 잡는다. 실제 계약값을 모르므로
# **하한 규정이 걸리지 않는 값**을 써서 계약전력 조정 결과를 읽을 수 있게 한다.
CONTRACT_MARGIN = 1.1


@dataclass(frozen=True)
class CaseDefinition:
    """케이스 하나의 정의."""

    key: str
    name: str
    usage_path: Path
    contract_type: str
    voltage: str = "high_a"
    option: str = "I"
    note: str = ""

    @property
    def selection(self) -> TariffSelection:
        return TariffSelection(self.contract_type, self.voltage, self.option)

    @property
    def label(self) -> str:
        return f"{self.key} {self.name}"


@dataclass(frozen=True, eq=False)
class CaseResult:
    """케이스 하나의 결과. **시계열은 들고 있지 않는다.**"""

    definition: CaseDefinition
    usage: UsageData
    quality: QualityReport
    diagnosis: Diagnosis
    baseline: BillingResult
    contract_kw: float
    pv_rows: tuple[dict[str, object], ...]
    sensitivity_rows: tuple[dict[str, object], ...]
    selection_rows: tuple[dict[str, object], ...]
    measure_rows: tuple[dict[str, object], ...]
    ess: EssOptimum | None
    """ESS 정밀화 결과 (54세션). **타당성 판정이 이것을 본다** — 행만 남기면
    「목표를 냈는데 절감액이 음수」 같은 어긋남을 볼 수가 없다."""
    elapsed_sec: float
    weather_source: str
    warnings: tuple[str, ...] = field(default=())

    @property
    def label(self) -> str:
        return self.definition.label

    def summary_row(self) -> dict[str, float | str]:
        pattern = self.diagnosis.pattern
        return {
            "케이스": self.label,
            "계약종별": self.definition.contract_type,
            "기간": f"{self.usage.meta.start:%Y-%m-%d} ~ {self.usage.meta.end:%Y-%m-%d}",
            "총 사용량(MWh)": self.usage.meta.total_kwh / 1000.0,
            "관측 최대수요(kW)": float(self.usage.kw.max()),
            "요금적용전력(kW)": self.baseline.billing_demand_kw,
            # 부하율·기저부하 비율은 관측 슬롯이 없으면 None 이다. 그럴 일은 없지만
            # 0 으로 때우지 않고 그대로 둔다.
            "부하율(%)": (pattern.load_factor or 0.0) * 100.0,
            "기저부하 비율(%)": (pattern.base_load_ratio or 0.0) * 100.0,
            "기본요금(원)": self.baseline.total_base_won,
            "전력량요금(원)": self.baseline.total_energy_won,
            "총 요금(원)": self.baseline.total_won,
            "기본요금 비중(%)": (
                self.baseline.total_base_won / self.baseline.total_won * 100.0
                if self.baseline.total_won
                else 0.0
            ),
            "기상": self.weather_source,
            "소요(초)": self.elapsed_sec,
        }


@dataclass(frozen=True, eq=False)
class CaseStudy:
    """케이스 전체 결과. 시트별 표를 낸다."""

    results: tuple[CaseResult, ...]
    elapsed_sec: float
    weather_calls: int

    def summary_frame(self) -> pd.DataFrame:
        return pd.DataFrame([item.summary_row() for item in self.results]).set_index("케이스")

    def pv_frame(self) -> pd.DataFrame:
        rows = [row for item in self.results for row in item.pv_rows]
        return pd.DataFrame(rows).set_index(["케이스", "용량(kWp)"])

    def sensitivity_frame(self) -> pd.DataFrame:
        rows = [row for item in self.results for row in item.sensitivity_rows]
        return pd.DataFrame(rows).set_index(["케이스", "용량(kWp)", "지표"])

    def selection_frame(self) -> pd.DataFrame:
        rows = [row for item in self.results for row in item.selection_rows]
        return pd.DataFrame(rows).set_index(["케이스", "선택요금"])

    def measure_frame(self) -> pd.DataFrame:
        rows = [row for item in self.results for row in item.measure_rows]
        return pd.DataFrame(rows).set_index(["케이스", "수단"])

    def performance_frame(self) -> pd.DataFrame:
        rows: list[dict[str, object]] = [
            {"항목": f"{item.label} 소요", "값": f"{item.elapsed_sec:,.1f} 초"}
            for item in self.results
        ]
        rows.append({"항목": "전체 소요", "값": f"{self.elapsed_sec:,.1f} 초"})
        rows.append({"항목": "기상 취득 호출", "값": f"{self.weather_calls} 회"})
        rows.append(
            {
                "항목": "기상 캐시 적중",
                "값": (
                    f"{len(self.results) - self.weather_calls}/{len(self.results)} "
                    "(모든 케이스가 같은 좌표·기간이라 첫 건만 취득한다)"
                ),
            }
        )
        return pd.DataFrame(rows).set_index("항목")

    def find(self, key: str) -> CaseResult:
        for item in self.results:
            if item.definition.key == key:
                return item
        raise KeyError(f"없는 케이스입니다: {key!r}")


def build_case_definitions(directory: Path) -> tuple[CaseDefinition, ...]:
    """``input\\cases\\`` 의 파일에서 케이스 정의를 만든다.

    **C4 만 산업용(을)이다** — 봄·가을 주말 할인 특례를 태우기 위해서다.
    """
    plan = (
        ("C1", "오전 피크형", "general_b", "원본. 10~13시 집중"),
        ("C2", "오후 피크형", "general_b", "프로파일 5시간 이동 — 최대부하 시간대에 피크"),
        ("C3", "평탄형", "general_b", "부하율 85% — 피크 저감 여지가 거의 없다"),
        ("C4", "주말 가동형", "industrial_b", "산업용(을) 봄·가을 주말 할인 특례"),
        ("C5", "겨울 피크형", "general_b", "대상월(12·1·2)인데 PV 가 약하다"),
        ("C6", "야간 피크형", "general_b", "경부하 최대 — 요금적용전력 대상이 아니다"),
    )
    definitions: list[CaseDefinition] = []
    for key, name, contract_type, note in plan:
        path = directory / f"{key}_{name}.csv"
        if not path.is_file():
            raise FileNotFoundError(f"케이스 파일이 없습니다: {path} (tools\\make_cases.py 실행)")
        definitions.append(
            CaseDefinition(
                key=key, name=name, usage_path=path, contract_type=contract_type, note=note
            )
        )
    return tuple(definitions)


def _weather_for(usage: UsageData, region_key: str = CASE_REGION_KEY) -> WeatherData:
    region = find_region(region_key)
    request = WeatherRequest.for_index(
        pd.DatetimeIndex(usage.kw.index), region.latitude, region.longitude
    )
    return load_weather(request)


def _pv_config(region_key: str = CASE_REGION_KEY) -> PvSystemConfig:
    region = find_region(region_key)
    return PvSystemConfig(
        latitude=region.latitude,
        longitude=region.longitude,
        arrays=(ArrayConfig.roof("지붕", 1_000.0),),
        altitude_m=50.0,
        timezone="Asia/Seoul",
    )


def _case_ess(
    usage: UsageData,
    table: TariffTable,
    definition: CaseDefinition,
    baseline: BillingResult,
    quality: QualityReport,
) -> EssOptimum | None:
    """케이스 하나의 ESS 정밀화 (54세션). **화면과 같은 경로다.**

    개략 곡선이 목표를 못 그리면 ``None`` 이다 — 어떤 목표에서도 초과 구간이
    없는 자료다.
    """
    peak = float(baseline.billing_demand_kw)
    if peak <= 0:
        return None
    curve = ess_target_curve(
        usage.kw,
        usage.meta.interval_minutes,
        baseline_demand_kw=peak,
        base_fee_won_per_kw=float(table.rates(definition.selection).base_won_per_kw),
    )
    if curve.best is None:
        return None
    return refine_ess_target(
        usage,
        table,
        definition.selection,
        curve=curve,
        baseline=baseline,
        quality=quality,
    )


def _ess_point(optimum: EssOptimum) -> EssOptimumPoint | None:
    """고른 목표의 점. 성립하지 않으면 ``None``."""
    return next(
        (item for item in optimum.points if item.target_kw == optimum.target_kw), None
    )


def _ess_remark(optimum: EssOptimum | None, baseline_demand_kw: float) -> str:
    """비고 한 줄 — **왜 그런지**가 판정의 근거다."""
    if optimum is None:
        return "초과 구간이 없어 곡선을 그리지 못했다"
    if optimum.below_minimum:
        return (
            f"최소 규격 미달 — 필요 출력 {optimum.required_power_kw:,.1f} kW "
            f"< 상업용 최소 {optimum.minimum_power_kw:,.0f} kW"
        )
    if not optimum.viable:
        negatives = sum(1 for item in optimum.points if item.annual_saving_won <= 0)
        return (
            f"성립하는 목표 없음 — 참고 지점 {len(optimum.points)}개 중 "
            f"절감액 0 이하 {negatives}개"
        )
    point = _ess_point(optimum)
    # **없을 때의 갈래를 걷어냈다** (60세션 12절). 고른 목표는 언제나 점 목록에
    # 있다 — 아니면 탐색이 어긋난 것이므로 비고 한 줄로 덮지 않는다.
    #
    # **맨 예외로 죽이지는 않는다.** 걷어내기만 하면
    # ``AttributeError: 'NoneType' object has no attribute 'achieved_demand_kw'``
    # 가 나는데, 어느 목표에서 난 것인지 짚지 못한다 — 케이스 스터디는 여섯을
    # 잇달아 도는 자리라 그것만으로는 어디를 볼지 모른다.
    assert point is not None, (
        f"고른 목표 {optimum.target_kw:,.0f} kW 가 점 목록"
        f"({len(optimum.points)}개)에 없다 — ESS 탐색이 어긋났다"
    )
    return (
        f"목표 {optimum.target_kw:,.0f} kW "
        f"(기준 {baseline_demand_kw:,.1f} → 실제 {point.achieved_demand_kw:,.1f}) · "
        f"{point.grid_power_kw:,.0f} kW / {point.grid_capacity_kwh:,.0f} kWh · "
        f"회수 {optimum.payback_years:,.1f}년"
    )


def run_one_case(
    definition: CaseDefinition,
    table: TariffTable,
    *,
    capacities_kwp: Sequence[float] = DEFAULT_CAPACITIES_KWP,
    unit_pv: pd.Series | None = None,
    weather_source: str = "cache",
    pv_unit_cost_won_per_kwp: float | None = None,
    progress: ProgressReporter | None = None,
) -> CaseResult:
    """케이스 하나를 처음부터 끝까지 돌린다. **요금은 매번 재계산한다.**

    ``progress`` 는 선택 인자다 (10.6). CLI 는 여기에 rich 를 붙이고 화면은
    Streamlit 을 붙인다 — **이 함수는 어느 쪽인지 모른다.**
    """
    started = time.perf_counter()
    runner = StageRunner(record(progress))

    with runner.running("read"):
        usage = load_usage(definition.usage_path)
    with runner.running("quality"):
        quality = check_quality(usage)
    contract_kw = float(usage.kw.max()) * CONTRACT_MARGIN
    contract = ContractInfo(definition.selection, contract_kw=contract_kw)
    with runner.running("diagnose"):
        diagnosis = diagnose(usage, table, contract, quality=quality)
        baseline = calculate_bill(usage, table, definition.selection, quality=quality)

    profile = unit_pv
    if profile is None:
        with runner.running("weather"):
            weather = _weather_for(usage)
            weather_source = weather.source
            profile = unit_generation_kw(usage, weather, _pv_config())
        if weather_source == "cache":
            runner.skip("weather", "캐시 적중")
    else:
        runner.skip("weather", "단위 프로파일을 넘겨받음")
    profile = profile.reindex(pd.DatetimeIndex(usage.kw.index)).fillna(0.0)

    # ---- PV 용량 축
    cost = (
        PvCostInput.of_unit_cost(pv_unit_cost_won_per_kwp)
        if pv_unit_cost_won_per_kwp is not None
        else PvCostInput.unpriced()
    )
    pv_rows: list[dict[str, object]] = []
    sensitivity_rows: list[dict[str, object]] = []
    reporter = record(progress)
    reporter.stage("solar", len(capacities_kwp))
    for index, capacity in enumerate(capacities_kwp):
        reporter.step(index + 1, f"용량 {capacity:,.0f} kWp ({index + 1}/{len(capacities_kwp)})")
        curve = solar_curve(
            usage,
            table,
            definition.selection,
            profile,
            max_capacity_kwp=capacity,
            cost=cost,
            steps=1,
            baseline=baseline,
            quality=quality,
        )
        point = curve.points[-1]
        pv_rows.append(
            {
                "케이스": definition.label,
                "용량(kWp)": capacity,
                "발전량(kWh)": point.generation_kwh,
                "자가소비율(%)": (
                    point.self_consumption_ratio * 100.0
                    if point.self_consumption_ratio is not None
                    else None
                ),
                "잉여(kWh)": point.surplus_kwh,
                "요금적용전력(kW)": point.billing_demand_kw,
                "기본요금 절감액(원)": point.base_saving_won,
                "전력량요금 절감액(원)": point.energy_saving_won,
                "총 절감액(원)": point.total_saving_won,
                "12개월 환산(원)": point.annual_saving_won,
                "투자비(원)": point.investment_won,
                "회수기간(년)": point.payback_years,
            }
        )

        # ---- 감도 (용량 0 은 세 시나리오가 모두 0 이라 건너뛴다)
        if capacity <= 0:
            continue
        frame = sensitivity_comparison(
            usage,
            table,
            CombinationSpec(
                name=f"{definition.key} PV {capacity:,.0f}",
                selection=definition.selection,
                pv_capacity_kwp=capacity,
                pv_unit_cost_won_per_kwp=pv_unit_cost_won_per_kwp,
            ),
            baseline_bill=baseline,
            unit_pv_kw_per_kwp=profile,
            quality=quality,
        )
        for item in sensitivity_ranges(frame):
            sensitivity_rows.append(
                {
                    "케이스": definition.label,
                    "용량(kWp)": capacity,
                    "지표": item.metric,
                    "기준값": item.base,
                    "범위 하한": item.low,
                    "범위 상한": item.high,
                    "하한 시나리오": item.low_scenario,
                    "상한 시나리오": item.high_scenario,
                    "범위 폭(%)": None if item.spread_ratio is None else item.spread_ratio * 100.0,
                }
            )

    reporter.done("solar")

    # ---- 선택요금 (현행 종별·전압 안에서만)
    reporter.stage("measures")
    selection_rows: list[dict[str, object]] = []
    for candidate in switchable_selections(table, definition.selection):
        total = diagnosis.option_totals.get(candidate.option)
        if total is None:
            total = calculate_bill(usage, table, candidate, quality=quality).total_won
        selection_rows.append(
            {
                "케이스": definition.label,
                "선택요금": candidate.option,
                "총 요금(원)": total,
                "현행 대비(원)": baseline.total_won - total,
                "현행": candidate.option == definition.option,
            }
        )

    # ---- 수단 (감도와 무관해야 하는 확정 계산들)
    switch = evaluate_tariff_switch(
        usage, table, definition.selection, quality=quality, option_totals=diagnosis.option_totals
    )
    power_factor = evaluate_power_factor(
        usage, table, definition.selection, baseline=baseline, quality=quality
    )
    measure_rows: list[dict[str, object]] = [
        {
            "케이스": definition.label,
            "수단": "7.1 선택요금 전환",
            "절감액(원)": switch.saving_won,
            "12개월 환산(원)": switch.annual_saving_won,
            "확실성": str(switch.certainty),
            "비고": f"{switch.current.selection.option} → {switch.best.selection.option}",
        },
        {
            "케이스": definition.label,
            "수단": "7.2 계약전력 조정",
            "절감액(원)": diagnosis.summary.contract_saving_won,
            "12개월 환산(원)": None,
            "확실성": "높음",
            "비고": f"계약 {contract_kw:,.0f} kW 가정 (관측 최대 × {CONTRACT_MARGIN})",
        },
        {
            "케이스": definition.label,
            "수단": "7.4 역률 개선 (92→97%)",
            "절감액(원)": power_factor.saving_won,
            "12개월 환산(원)": power_factor.annual_saving_won,
            "확실성": str(power_factor.certainty),
            "비고": "요금표와 약관만으로 확정",
        },
    ]
    # **ESS 도 돌린다** (54세션). 여태 케이스 스터디가 손대지 않던 자리다 —
    # 요금·진단·태양광·감도는 여섯 케이스를 다 훑는데 **ESS 만 한 번도 돌지
    # 않았다.** 그래서 「고를 수 있는 목표가 없는데 목표를 내고 절감액이 음수로
    # 나오는」 갈래가 66/66 을 통과한 채 실물에만 나왔다.
    #
    # **화면과 같은 경로다** — 개략 곡선 → 정밀화. 그 경로가 깨졌던 자리다.
    ess = _case_ess(usage, table, definition, baseline, quality)
    chosen = _ess_point(ess) if ess is not None and ess.viable else None
    measure_rows.append(
        {
            "케이스": definition.label,
            "수단": "7.6 ESS",
            "절감액(원)": chosen.annual_saving_won if chosen is not None else None,
            "12개월 환산(원)": None,
            "확실성": "중간~낮음",
            "비고": _ess_remark(ess, diagnosis.peak.billing_demand_kw),
        }
    )

    if diagnosis.dr is not None:
        response = evaluate_demand_response(diagnosis.dr)
        measure_rows.append(
            {
                "케이스": definition.label,
                "수단": "7.3 경제성DR",
                "절감액(원)": None,
                "12개월 환산(원)": None,
                "확실성": str(response.certainty),
                "비고": (
                    f"거래 가능일 {diagnosis.dr.eligible_days}일 · "
                    f"등록 권장 {diagnosis.dr.registered_capacity_kw:,.0f} kW · "
                    f"연간 감축 {response.annual_reducible_kwh:,.0f} kWh"
                ),
            }
        )

    reporter.done("measures")

    elapsed = time.perf_counter() - started
    return CaseResult(
        definition=definition,
        usage=usage,
        quality=quality,
        diagnosis=diagnosis,
        baseline=baseline,
        contract_kw=contract_kw,
        pv_rows=tuple(pv_rows),
        sensitivity_rows=tuple(sensitivity_rows),
        selection_rows=tuple(selection_rows),
        measure_rows=tuple(measure_rows),
        ess=ess,
        elapsed_sec=elapsed,
        weather_source=weather_source,
        warnings=texts(quality.notices),
    )


def run_case_study(
    definitions: Sequence[CaseDefinition],
    table: TariffTable,
    *,
    capacities_kwp: Sequence[float] = DEFAULT_CAPACITIES_KWP,
    pv_unit_cost_won_per_kwp: float | None = None,
    progress: ProgressReporter | None = None,
) -> CaseStudy:
    """케이스를 **순차로** 돌린다. 여섯 벌의 시계열을 동시에 들지 않는다.

    단위 발전 프로파일은 케이스마다 다시 만든다 (부하 인덱스에 정렬해야 한다).
    기상 자체는 좌표·기간이 같아 **첫 건만 취득하고 나머지는 캐시**를 탄다.
    """
    started = time.perf_counter()
    results: list[CaseResult] = []
    weather_calls = 0
    for definition in definitions:
        usage_index_source = load_usage(definition.usage_path)
        weather = _weather_for(usage_index_source)
        if weather.source != "cache":
            weather_calls += 1
        profile = unit_generation_kw(usage_index_source, weather, _pv_config())
        del usage_index_source
        results.append(
            run_one_case(
                definition,
                table,
                capacities_kwp=capacities_kwp,
                unit_pv=profile,
                weather_source=weather.source,
                pv_unit_cost_won_per_kwp=pv_unit_cost_won_per_kwp,
                progress=progress,
            )
        )
    return CaseStudy(
        results=tuple(results),
        elapsed_sec=time.perf_counter() - started,
        weather_calls=weather_calls,
    )
