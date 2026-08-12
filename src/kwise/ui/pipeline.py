"""화면이 부르는 계산 배선 (요구사항서 10.2).

**계산은 여기서 하지 않는다.** 순수 함수를 어떤 순서로, 어떤 인자로 부를지만
정한다. Streamlit 을 import 하지 않으므로 테스트가 그대로 닿는다 —
:mod:`kwise.ui.cache` 가 이 함수들을 ``st.cache_data`` 로 감싼다.

배선에서 조용히 틀리기 쉬운 곳이 둘 있다.

**① 계약전력을 두 군데에 넣어야 한다.** :class:`~kwise.diagnose.ContractInfo`
쪽만 채우고 :class:`~kwise.tariff.BillingOptions` 를 비우면 요금 엔진이 계약전력을
모르는 채로 돌아 **요금적용전력 하한(계약전력의 30%)이 적용되지 않는다.** 금액은
그럴듯하게 나오고 저부하 사업장에서만 과소 산출된다.

**② 켜지 않은 수단은 조합에서 빠져야 한다.** :func:`combination_specs` 가 켠
수단만으로 조합을 쌓는다. 켜지 않은 수단을 조합에 넣으면 "검토하지 않은 것" 이
"검토했더니 이만큼" 으로 둔갑한다.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

import pandas as pd

from kwise.compare import CombinationSpec
from kwise.diagnose import ContractInfo, Diagnosis, diagnose
from kwise.io import UsageData
from kwise.measures import (
    PvCostInput,
    SolarCurve,
    solar_curve,
    unit_generation_kw,
)
from kwise.progress import ProgressReporter
from kwise.pv import (
    ArrayConfig,
    PvPresets,
    PvSystemConfig,
    WeatherData,
    WeatherRequest,
    capacity_from_area_kwp,
    find_region,
    load_pv_presets,
    load_weather,
)
from kwise.quality import QualityReport, check_quality
from kwise.tariff import (
    BillingOptions,
    BillingResult,
    TariffSelection,
    TariffTable,
    calculate_bill,
    deemed_lagging_pct,
    list_contract_types,
    list_options,
    list_voltages,
)

__all__ = [
    "ContractForm",
    "ContractGuess",
    "SolarInputs",
    "baseline_bill",
    "combination_specs",
    "contract_type_choices",
    "default_lagging_pct",
    "diagnose_usage",
    "guess_contract",
    "option_choices",
    "solar_config",
    "solar_result",
    "unit_pv_profile",
    "utilization_hours",
    "voltage_choices",
]


# --------------------------------------------------------------------- 계약 정보


def default_lagging_pct() -> float:
    """주간 지상역률 입력의 기본값 = 약관 제42조의 **간주값**.

    **모듈 상수로 붙잡지 않는다.** 기준 데이터를 화면에서 고치면 그 다음 조회부터
    새 값이 나와야 한다 (12장). 기준(제41조)이 아니라 간주값(제42조)을 쓴다 —
    오늘은 둘 다 92% 라 조정액이 0 이지만 근거 조문이 다르다.
    """
    return deemed_lagging_pct()


@dataclass(frozen=True)
class ContractForm:
    """화면이 받는 계약 정보 (요구사항서 3.2).

    **진단 단계에서 묻는 것은 이것뿐이다.** 설비 정보는 2단계에서 켤 때만 묻는다.
    """

    contract_type: str
    voltage: str
    option: str
    contract_kw: float | None = None
    power_factor_pct: float | None = None
    """주간 지상역률. ``None`` 이면 약관 간주값(:func:`default_lagging_pct`)."""
    leading_power_factor_pct: float | None = None
    """야간 진상역률. **기본값이 없다** — 모르면 지상 간주(추가 0)로 둔다."""
    sunday_is_holiday: bool = True

    @property
    def selection(self) -> TariffSelection:
        return TariffSelection(self.contract_type, self.voltage, self.option)

    @property
    def lagging_pct(self) -> float:
        return self.power_factor_pct if self.power_factor_pct is not None else default_lagging_pct()

    def contract_info(self) -> ContractInfo:
        return ContractInfo(
            selection=self.selection,
            contract_kw=self.contract_kw,
            power_factor_pct=self.lagging_pct,
        )

    def billing_options(self) -> BillingOptions:
        """요금 엔진 설정. **계약전력을 반드시 함께 넘긴다** (모듈 docstring ①)."""
        return BillingOptions(
            contract_kw=self.contract_kw,
            power_factor_pct=self.lagging_pct,
            leading_power_factor_pct=self.leading_power_factor_pct,
            sunday_is_holiday=self.sunday_is_holiday,
        )


# --------------------------------------------------------------------- 드롭다운


def contract_type_choices(table: TariffTable) -> tuple[tuple[str, str], ...]:
    """계약종별 (키, 표시명). **요금 데이터에서 만든다. 하드코딩 금지** (부록 A.3)."""
    return list_contract_types(table)


def voltage_choices(table: TariffTable, contract_type: str) -> tuple[tuple[str, str], ...]:
    return list_voltages(table, contract_type)


def option_choices(table: TariffTable, contract_type: str, voltage: str) -> tuple[str, ...]:
    return list_options(table, contract_type, voltage)


# --------------------------------------------------------------------- 추정 보조


@dataclass(frozen=True)
class ContractGuess:
    """업로드 직후 제시하는 추정치 (요구사항서 3.2 — 추정 보조).

    **추정일 뿐이므로 사용자가 확정한다.** 계약종별은 데이터로 정할 수 없다.
    """

    contract_kw: float
    max_demand_kw: float
    utilization_hours: float
    tier_hint: str
    threshold_kw: float | None
    notes: tuple[str, ...]


def utilization_hours(usage: UsageData) -> float:
    """연간 이용시간 = 사용량 ÷ 최대수요. 선택요금 후보를 가늠하는 값이다."""
    peak = usage.meta.max_demand_kw
    if peak <= 0:
        return 0.0
    return usage.meta.total_kwh / peak


def _threshold_for(contract_type: str) -> float | None:
    """갑/을 임계 계약전력. 종별 앞머리로 찾는다 — 값은 ``rules_kr.json`` 에 있다."""
    from kwise.rules import RuleDataError, rule_value

    family = contract_type.split("_", 1)[0]
    try:
        return float(rule_value(f"contract_type.threshold_kw.{family}"))
    except RuleDataError:
        return None


def guess_contract(
    usage: UsageData, contract_type: str, *, margin_ratio: float = 0.1
) -> ContractGuess:
    """계약전력·갑을 구분·이용시간을 가늠한다.

    계약전력은 **청구서 기재값이 정본**이다. 여기 값은 관측 최대에 여유를 얹은
    가늠이며, 확정 전에는 계약 적정성 진단이 이 값에 끌려간다는 사실을 함께 낸다.
    """
    peak = usage.meta.max_demand_kw
    suggested = math.ceil(peak * (1.0 + margin_ratio)) if peak > 0 else 0.0
    hours = utilization_hours(usage)
    threshold = _threshold_for(contract_type)

    if threshold is None:
        tier = "판정 불가"
    elif suggested >= threshold:
        tier = f"을 (임계 {threshold:,.0f} kW 이상)"
    else:
        tier = f"갑 (임계 {threshold:,.0f} kW 미만)"

    notes = (
        f"관측 최대 {peak:,.1f} kW 에 여유 {margin_ratio:.0%} 를 얹은 가늠입니다. "
        "**청구서의 계약전력을 넣으십시오** — 계약 적정성 진단이 이 값을 전제로 합니다.",
        f"연간 이용시간 {hours:,.0f} 시간. 길수록 기본요금이 낮은 선택요금이 유리합니다.",
    )
    return ContractGuess(
        contract_kw=float(suggested),
        max_demand_kw=peak,
        utilization_hours=hours,
        tier_hint=tier,
        threshold_kw=threshold,
        notes=notes,
    )


# --------------------------------------------------------------------- 1단계


def load_quality(usage: UsageData, *, contract_kw: float | None = None) -> QualityReport:
    return check_quality(usage, contract_kw=contract_kw)


def diagnose_usage(
    usage: UsageData,
    table: TariffTable,
    form: ContractForm | None,
    *,
    quality: QualityReport | None = None,
) -> Diagnosis:
    """1단계 진단. ``form`` 이 ``None`` 이면 **계약 정보 없이** 부하·피크만 낸다.

    "사용자가 파일만 올려도 결과가 나온다" 를 여기서 지킨다 (요구사항서 6장).
    """
    if form is None:
        return diagnose(usage, table, None, quality=quality)
    return diagnose(
        usage,
        table,
        form.contract_info(),
        quality=quality,
        options=form.billing_options(),
    )


def baseline_bill(
    usage: UsageData,
    table: TariffTable,
    form: ContractForm,
    *,
    quality: QualityReport | None = None,
) -> BillingResult:
    """현행 요금. 모든 절감액의 기준선이다."""
    return calculate_bill(
        usage, table, form.selection, options=form.billing_options(), quality=quality
    )


# --------------------------------------------------------------------- 2단계 · 태양광


@dataclass(frozen=True)
class SolarInputs:
    """태양광 기본 입력 셋 + 확장 (요구사항서 3.3).

    기본은 **면적·설치 밀도·지역** 셋이다. 나머지는 확장 패널로 접는다 —
    발전량 예측 R² 가 0.8 이라 입력 정밀도가 기상 오차를 이기지 못한다.
    """

    region_key: str
    area_m2: float = 0.0
    density_key: str = ""
    capacity_kwp: float | None = None
    """직접 입력하면 면적 환산을 덮어쓴다."""
    azimuth_deg: float | None = None
    tilt_deg: float | None = None
    gcr: float | None = None
    system_loss_ratio: float = 0.14
    altitude_m: float = 50.0
    latitude: float | None = None
    longitude: float | None = None
    """시군구 중심점 대신 쓸 좌표. 산악·해안 지형용."""
    unit_cost_won_per_kwp: float | None = None
    total_investment_won: float | None = None
    steps: int = 20

    def preset(self, presets: PvPresets | None = None) -> object:
        table = presets if presets is not None else load_pv_presets()
        return table.density(self.density_key) if self.density_key else table.default

    def coordinates(self) -> tuple[float, float]:
        if self.latitude is not None and self.longitude is not None:
            return self.latitude, self.longitude
        region = find_region(self.region_key)
        return region.latitude, region.longitude

    def resolved_capacity_kwp(self, presets: PvPresets | None = None) -> float:
        """면적 환산 용량. 직접 입력이 있으면 그쪽이 이긴다."""
        if self.capacity_kwp is not None:
            return self.capacity_kwp
        table = presets if presets is not None else load_pv_presets()
        preset = self.preset(table)
        gcr = self.gcr if self.gcr is not None else preset.gcr  # type: ignore[attr-defined]
        return capacity_from_area_kwp(self.area_m2, gcr=gcr, area_per_kwp_m2=table.area_per_kwp_m2)

    def cost(self) -> PvCostInput:
        """투자비 입력. **없으면 0원이 아니라 미산출**이고 사유가 붙는다 (7.5)."""
        if self.total_investment_won is not None:
            return PvCostInput.of_total(self.total_investment_won)
        if self.unit_cost_won_per_kwp is not None:
            return PvCostInput.of_unit_cost(self.unit_cost_won_per_kwp)
        return PvCostInput.unpriced()


def solar_config(inputs: SolarInputs, *, presets: PvPresets | None = None) -> PvSystemConfig:
    """PV 시스템 설정. 밀도 프리셋이 GCR 과 경사각을 **함께** 정한다 (3.3)."""
    table = presets if presets is not None else load_pv_presets()
    preset = inputs.preset(table)
    latitude, longitude = inputs.coordinates()
    capacity = inputs.resolved_capacity_kwp(table)
    array = ArrayConfig(
        name="지붕",
        capacity_kwp=max(capacity, 1.0),  # 단위 프로파일용. 실제 용량은 곡선이 훑는다.
        tilt_deg=inputs.tilt_deg if inputs.tilt_deg is not None else preset.tilt_deg,  # type: ignore[attr-defined]
        azimuth_deg=(
            inputs.azimuth_deg if inputs.azimuth_deg is not None else table.default_azimuth_deg
        ),
        gcr=inputs.gcr if inputs.gcr is not None else preset.gcr,  # type: ignore[attr-defined]
        system_loss_ratio=inputs.system_loss_ratio,
    )
    return PvSystemConfig(
        latitude=latitude,
        longitude=longitude,
        arrays=(array,),
        altitude_m=inputs.altitude_m,
        timezone="Asia/Seoul",
    )


def load_weather_for(usage: UsageData, inputs: SolarInputs) -> WeatherData:
    """기상 취득. 캐시 → API → 사전 취득분 순은 :func:`load_weather` 가 정한다."""
    latitude, longitude = inputs.coordinates()
    request = WeatherRequest.for_index(pd.DatetimeIndex(usage.kw.index), latitude, longitude)
    return load_weather(request)


def unit_pv_profile(
    usage: UsageData,
    inputs: SolarInputs,
    *,
    weather: WeatherData | None = None,
    presets: PvPresets | None = None,
) -> tuple[pd.Series, str]:
    """1 kWp 당 발전 프로파일과 기상 출처.

    부하 인덱스에 맞춰 돌려주므로 용량을 곱하기만 하면 된다.
    """
    data = weather if weather is not None else load_weather_for(usage, inputs)
    profile = unit_generation_kw(usage, data, solar_config(inputs, presets=presets))
    aligned = profile.reindex(pd.DatetimeIndex(usage.kw.index)).fillna(0.0)
    return aligned, data.source


def solar_result(
    usage: UsageData,
    table: TariffTable,
    form: ContractForm,
    inputs: SolarInputs,
    unit_profile: pd.Series,
    *,
    baseline: BillingResult | None = None,
    quality: QualityReport | None = None,
    presets: PvPresets | None = None,
    progress: ProgressReporter | None = None,
) -> SolarCurve:
    """용량 곡선. 시뮬레이션은 한 번, 용량은 곱셈이다 (5세션 결정).

    **파이프라인에서 가장 오래 걸리는 구간이다** (실측 43%). 진행을 넘긴다.
    """
    return solar_curve(
        usage,
        table,
        form.selection,
        unit_profile,
        max_capacity_kwp=inputs.resolved_capacity_kwp(presets),
        cost=inputs.cost(),
        steps=inputs.steps,
        power_factor_pct=form.lagging_pct,
        baseline=baseline,
        quality=quality,
        options=form.billing_options(),
        progress=progress,
    )


# --------------------------------------------------------------------- 3단계 · 조합


def combination_specs(
    *,
    form: ContractForm,
    best_selection: TariffSelection,
    enabled: Iterable[str],
    pv_capacity_kwp: float = 0.0,
    pv_unit_cost_won_per_kwp: float | None = None,
    pv_total_investment_won: float | None = None,
    ess_target_kw: float | None = None,
    ess_unit_cost_won_per_kw: float | None = None,
    ess_total_investment_won: float | None = None,
    ess_fixed_won: float | None = None,
    ess_per_kwh_won: float | None = None,
    contract_floor_ratio: float | None = None,
    sharpness: float = 1.0,
) -> tuple[CombinationSpec, ...]:
    """켠 수단만으로 조합을 쌓는다. **7장 번호 순(7.1~7.7)으로 누적한다.**

    첫 조합은 언제나 기준선(현행)이다 — :func:`compare_combinations` 가 그렇게 읽는다.
    켜지 않은 수단은 여기 들어오지 않으므로 비교표와 산출물에서 함께 빠진다.
    """
    chosen = set(enabled)
    common: dict[str, object] = {
        "pv_unit_cost_won_per_kwp": pv_unit_cost_won_per_kwp,
        "pv_total_investment_won": pv_total_investment_won,
        "ess_unit_cost_won_per_kw": ess_unit_cost_won_per_kw,
        "ess_total_investment_won": ess_total_investment_won,
        "ess_fixed_won": ess_fixed_won,
        "ess_per_kwh_won": ess_per_kwh_won,
        "contract_floor_ratio": contract_floor_ratio,
        "sharpness": sharpness,
    }
    baseline = CombinationSpec(name="기준선 (현행)", selection=form.selection, **common)  # type: ignore[arg-type]
    specs = [baseline]

    # 7.1 — 켜지 않으면 현행 선택요금을 그대로 들고 간다.
    selection = form.selection
    if "tariff_switch" in chosen and best_selection != form.selection:
        selection = best_selection
        specs.append(
            replace(baseline, name=f"선택요금 전환 ({best_selection.option})", selection=selection)
        )

    cursor = specs[-1]
    # 7.2 — 계약전력을 spec 에 넣는 것 자체가 '조정을 검토한다' 는 뜻이다.
    if "contract" in chosen and form.contract_kw is not None:
        cursor = replace(
            cursor,
            name=f"{_plus(specs)}계약전력 조정",
            selection=selection,
            contract_kw=form.contract_kw,
        )
        specs.append(cursor)

    # 7.5
    if "solar" in chosen and pv_capacity_kwp > 0:
        cursor = replace(
            cursor,
            name=f"{_plus(specs)}태양광 {pv_capacity_kwp:,.0f} kWp",
            pv_capacity_kwp=pv_capacity_kwp,
        )
        specs.append(cursor)

    # 7.6
    if "ess" in chosen and ess_target_kw is not None and ess_target_kw > 0:
        cursor = replace(
            cursor,
            name=f"{_plus(specs)}ESS 목표 {ess_target_kw:,.0f} kW",
            ess_target_kw=ess_target_kw,
        )
        specs.append(cursor)

    return tuple(specs)


def _plus(specs: Sequence[CombinationSpec]) -> str:
    """두 번째 조합부터 ``+`` 를 붙인다. 누적이라는 것이 이름에서 보여야 한다."""
    return "+ " if len(specs) > 1 else ""
