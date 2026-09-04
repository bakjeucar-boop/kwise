"""케이스 스터디 (요구사항서 11.3).

**샘플을 변형한 합성 6종과 실측 1종으로 돈다** (합성은 `tools\\make_cases.py` 가
만든다). UI 없이 순수 함수로 계산하고 결과를 표로 낸다.

케이스마다 요금적용전력 3규칙(경부하 제외·대상월 한정·계약전력 하한)이 **다르게**
작동해야 한다. 같은 결과가 나오면 규칙이 실제로는 걸리지 않고 있다는 뜻이다.

    C1 오전 피크형   PV 최성기와 피크가 어느 정도 겹친다
    C2 오후 피크형   최대부하 시간대에 피크가 있다
    C3 평탄형        피크가 없어 기본요금 절감 여지가 거의 없다
    C4 주말 가동형   산업용(을) 봄·가을 주말 할인 특례
    C5 겨울 피크형   대상월인데 PV 발전이 약하다
    C6 야간 피크형   **관측 최대는 밤인데 요금적용전력은 낮에서 나온다**

**일곱째는 합성이 아니라 실측이다** (95세션 0절).

    R1 용인 실측    일반용(갑)Ⅱ 고압A 선택Ⅱ · 계약전력 290 kW

61세션에 들어온 실측인데 **회귀 밖에 있었다** — 합성 여섯이 안에 있고 실측이
밖에 있는 상태를 되돌린다. 덤으로 **갑Ⅱ 경로가 회귀에 선다**: C1~C6 는
`general_b`·`industrial_b` 라 갑Ⅱ 를 매 판 확인하는 자리가 여태 없었다.

**이 벌만 좌표와 계약전력이 다르다.** 용인이고 계약전력은 실제 값(290 kW)이라
관측 최대의 1.1배 규칙을 쓰지 않는다.

케이스는 **순차로** 돈다. 일곱 벌의 시계열을 동시에 들지 않는다 (메모리 규약).
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
    BillingOptions,
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
    "weather_cache_gaps",
]

# 케이스 좌표. 사전 취득분 격자(37.50, 127.00)에 걸린다 — 네트워크를 타지 않는다.
CASE_REGION_KEY = "서울특별시/강남구"

# 용인 실측 벌의 좌표. **사전 취득분은 2023~2025 뿐이라 이 벌의 기간(2026년까지)을
# 덮지 못한다** — `PROJECT_CACHE` 캐시가 비어 있으면 Open-Meteo 를 탄다.
YONGIN_REGION_KEY = "경기도/용인시"

#: 용인 실측 자료. 합성 케이스와 달리 ``input\\cases\\`` 밖에 있다.
YONGIN_USAGE_NAME = "전기사용량_소형건물.xlsx"

#: 용인 건물이 실제로 쓰는 계약전력. **지어낸 값이 아니다** (`docs\\BILL_CHECK.md`).
YONGIN_CONTRACT_KW = 290.0

# PV 용량 축 (11.3). 0 이 있어야 "PV 0 이면 절감 0" 을 확인할 수 있다.
DEFAULT_CAPACITIES_KWP: tuple[float, ...] = (0.0, 500.0, 1_000.0, 2_000.0)

# 계약전력은 케이스마다 관측 최대의 1.1배로 잡는다. 실제 계약값을 모르기 때문이다.
#
# **「하한 규정이 걸리지 않는 값」 이 아니다** (108세션 3절에 고쳤다). C6 야간
# 피크형은 이 여유로 잡은 12,012.7 kW 의 하한 3,603.81 kW 가 요금적용전력을
# **13개 월 모두** 끌어올린다 — 주석이 네 세션 동안 사실과 반대를 적고 있었다.
#
# **뿌리는 여유를 「관측 최대」 에 두는 것이다.** 하한이 견주는 상대는 관측
# 최대가 아니라 **요금적용전력 산정 대상 수요**(경부하 시간대와 비대상월이
# 빠진 값)다. 부하 모양에 따라 둘이 크게 갈린다 — C6 는 관측 최대
# 10,920.64 kW 인데 대상 수요의 최대가 2,801.00 kW 로 **네 배 가깝다.**
# 곧 관측 최대에 1.1 을 곱해도 요금적용전력 쪽에는 여유가 한 푼도 안 생긴다.
#
# **값은 안 고친다** — C6 계약전력 12,012.7 kW 는 107세션에 확정됐다.
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
    region_key: str = CASE_REGION_KEY
    """기상·태양광 좌표. 실측 벌만 자기 소재지를 쓴다."""
    contract_kw: float | None = None
    """계약전력. ``None`` 이면 관측 최대의 :data:`CONTRACT_MARGIN` 배로 잡는다."""

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
                    "(합성 여섯은 좌표·기간이 같아 첫 건만 취득한다. 실측은 따로 선다)"
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
    """``input\\cases\\`` 의 합성 여섯과 **실측 하나**로 케이스 정의를 만든다.

    **C4 만 산업용(을)이다** — 봄·가을 주말 할인 특례를 태우기 위해서다.
    **R1 만 실측이고 갑Ⅱ 다** (95세션 0절). 자료가 ``input\\`` 바로 아래에
    있으므로 ``directory`` 의 어버이에서 찾는다.
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

    yongin = directory.parent / YONGIN_USAGE_NAME
    if not yongin.is_file():
        raise FileNotFoundError(f"용인 실측 자료가 없습니다: {yongin}")
    definitions.append(
        CaseDefinition(
            key="R1",
            name="용인 실측",
            usage_path=yongin,
            contract_type="general_a_2",
            option="II",
            note="실측 (61세션). 갑Ⅱ 경로가 회귀에 서는 유일한 자리다",
            region_key=YONGIN_REGION_KEY,
            contract_kw=YONGIN_CONTRACT_KW,
        )
    )
    return tuple(definitions)


def weather_cache_gaps(
    definitions: Sequence[CaseDefinition],
) -> tuple[tuple[str, bool], ...]:
    """기상 캐시가 비어 있는 벌과, 사전 취득분으로 물러설 수 있는지.

    **돌리기 전에 알아야 하는 사실이다** (96세션). 캐시가 없으면 계산 중간에
    Open-Meteo 를 타는데, 그 사실이 실행이 길어진 뒤에야 드러났다. 사전 취득분이
    덮지 못하는 벌(용인 — 2026년)은 취득에 실패하면 계산이 아예 멈춘다.

    **소요는 짐작하지 않는다** — 취득이 필요하다는 사실까지만 낸다.

    Returns:
        ``(벌 이름, 사전 취득분이 덮는가)`` 짝. 캐시가 있는 벌은 들지 않는다.
    """
    from kwise.pv.archive import archive_covers
    from kwise.pv.weather import weather_cache_path

    gaps: list[tuple[str, bool]] = []
    for definition in definitions:
        usage = load_usage(definition.usage_path)
        region = find_region(definition.region_key)
        request = WeatherRequest.for_index(
            pd.DatetimeIndex(usage.kw.index), region.latitude, region.longitude
        )
        if not weather_cache_path(request).is_file():
            gaps.append((definition.label, archive_covers(request)))
    return tuple(gaps)


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
    options: BillingOptions,
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
        options=options,
    )


def _ess_point(optimum: EssOptimum) -> EssOptimumPoint | None:
    """고른 목표의 점. 성립하지 않으면 ``None``."""
    return next((item for item in optimum.points if item.target_kw == optimum.target_kw), None)


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
            f"성립하는 목표 없음 — 참고 지점 {len(optimum.points)}개 중 절감액 0 이하 {negatives}개"
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
    contract_kw = (
        definition.contract_kw
        if definition.contract_kw is not None
        else float(usage.kw.max()) * CONTRACT_MARGIN
    )
    contract = ContractInfo(definition.selection, contract_kw=contract_kw)
    # **계약전력을 요금 옵션에도 넣는다** (107세션 3절 · ②-15). 배치(103세션
    # 3절)와 화면(`ui\\pipeline.py` 의 ``ContractForm.billing_options``)이
    # 하는 것과 같다 — 기준선·태양광·감도·선택요금·수단이 **한 밑둥** 위에
    # 선다. 앞서는 기준선만 계약전력 없이 잡혀 요금적용전력 하한이 안 걸린
    # 총액에서 하한이 걸린 절감액을 빼고 있었고, 엔진이 그 판마다
    # ``tariff.floor_no_contract`` 를 냈다.
    options = BillingOptions(contract_kw=contract_kw)
    with runner.running("diagnose"):
        diagnosis = diagnose(usage, table, contract, quality=quality, options=options)
        baseline = calculate_bill(
            usage, table, definition.selection, options=options, quality=quality
        )

    profile = unit_pv
    if profile is None:
        with runner.running("weather"):
            weather = _weather_for(usage, definition.region_key)
            weather_source = weather.source
            profile = unit_generation_kw(usage, weather, _pv_config(definition.region_key))
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
            options=options,
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
            options=options,
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
            total = calculate_bill(
                usage, table, candidate, options=options, quality=quality
            ).total_won
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
        usage,
        table,
        definition.selection,
        quality=quality,
        options=options,
        option_totals=diagnosis.option_totals,
    )
    power_factor = evaluate_power_factor(
        usage, table, definition.selection, baseline=baseline, quality=quality, options=options
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
            "비고": (
                f"계약 {contract_kw:,.0f} kW (실제 값)"
                if definition.contract_kw is not None
                else f"계약 {contract_kw:,.0f} kW 가정 (관측 최대 × {CONTRACT_MARGIN})"
            ),
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
    ess = _case_ess(usage, table, definition, baseline, quality, options)
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
    """케이스를 **순차로** 돌린다. 일곱 벌의 시계열을 동시에 들지 않는다.

    단위 발전 프로파일은 케이스마다 다시 만든다 (부하 인덱스에 정렬해야 한다).
    합성 여섯은 좌표·기간이 같아 **첫 건만 취득하고 나머지는 캐시**를 타고,
    **실측(R1)은 좌표도 기간도 달라 요청이 하나 더 선다.**
    """
    started = time.perf_counter()
    results: list[CaseResult] = []
    weather_calls = 0
    for definition in definitions:
        usage_index_source = load_usage(definition.usage_path)
        weather = _weather_for(usage_index_source, definition.region_key)
        if weather.source != "cache":
            weather_calls += 1
        profile = unit_generation_kw(usage_index_source, weather, _pv_config(definition.region_key))
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
