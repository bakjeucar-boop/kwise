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

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

import pandas as pd

from kwise.compare import CombinationSpec
from kwise.diagnose import ContractInfo, Diagnosis, diagnose
from kwise.io import UsageData
from kwise.measures import (
    PvCostInput,
    SolarCurve,
    SolarPoint,
    solar_curve,
    solar_point,
    surplus_free_capacity_kwp,
    surplus_heavy_share,
    surplus_share_capacity_kwp,
    unit_generation_kw,
)
from kwise.progress import ProgressReporter
from kwise.pv import (
    ArrayConfig,
    AzimuthPreset,
    PvPresets,
    PvSystemConfig,
    WeatherData,
    WeatherRequest,
    capacity_from_area_kwp,
    find_region,
    load_pv_presets,
    load_weather,
)
from kwise.quality import DEFAULT_OPERATING_HOURS, QualityReport, check_quality
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
    "SURPLUS_HEAVY_LABEL",
    "SURPLUS_ONSET_LABEL",
    "AzimuthOption",
    "ContractForm",
    "SolarInputs",
    "azimuth_options",
    "baseline_bill",
    "combination_specs",
    "contract_type_choices",
    "daily_temperature",
    "default_lagging_pct",
    "diagnose_usage",
    "option_choices",
    "solar_config",
    "solar_result",
    "surplus_capacity_points",
    "unit_pv_profile",
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


# --------------------------------------------------------------------- 1단계


def load_quality(usage: UsageData, *, contract_kw: float | None = None) -> QualityReport:
    return check_quality(usage, contract_kw=contract_kw)


def diagnose_usage(
    usage: UsageData,
    table: TariffTable,
    form: ContractForm | None,
    *,
    quality: QualityReport | None = None,
    operating_hours: tuple[int, int] = DEFAULT_OPERATING_HOURS,
    dr_off_days: tuple[str, ...] = (),
) -> Diagnosis:
    """1단계 진단. ``form`` 이 ``None`` 이면 **계약 정보 없이** 부하·피크만 낸다.

    "사용자가 파일만 올려도 결과가 나온다" 를 여기서 지킨다 (요구사항서 6장).
    ``operating_hours`` 는 옆단 건물 정보에서 온다 (21세션 4절).
    ``dr_off_days`` 는 2단계 경제성DR 카드에서 고른 「쉬는 날」 이다 (29세션) —
    **DR 판정에만 쓴다.** 요금 계산의 공휴일은 법정 공휴일 그대로다.
    """
    if form is None:
        return diagnose(
            usage,
            table,
            None,
            quality=quality,
            operating_hours=operating_hours,
            dr_off_days=dr_off_days,
        )
    return diagnose(
        usage,
        table,
        form.contract_info(),
        quality=quality,
        options=form.billing_options(),
        operating_hours=operating_hours,
        dr_off_days=dr_off_days,
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
    wall_area_m2: float = 0.0
    """벽면 어레이 면적 (15세션). 0 이면 지붕 한 벌이다."""
    wall_azimuth_deg: float | None = None
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

    def resolved_gcr(self, presets: PvPresets | None = None) -> float:
        table = presets if presets is not None else load_pv_presets()
        preset = self.preset(table)
        return self.gcr if self.gcr is not None else float(preset.gcr)  # type: ignore[attr-defined]

    def resolved_tilt_deg(self, presets: PvPresets | None = None) -> float:
        table = presets if presets is not None else load_pv_presets()
        preset = self.preset(table)
        return self.tilt_deg if self.tilt_deg is not None else float(preset.tilt_deg)  # type: ignore[attr-defined]

    def roof_capacity_kwp(self, presets: PvPresets | None = None) -> float:
        table = presets if presets is not None else load_pv_presets()
        return capacity_from_area_kwp(
            self.area_m2, gcr=self.resolved_gcr(table), area_per_kwp_m2=table.area_per_kwp_m2
        )

    def wall_capacity_kwp(self, presets: PvPresets | None = None) -> float:
        """벽면 용량. **GCR 1.0 이다** — 벽은 앞열 그림자가 없어 면적을 그대로 채운다."""
        table = presets if presets is not None else load_pv_presets()
        return capacity_from_area_kwp(
            self.wall_area_m2, gcr=1.0, area_per_kwp_m2=table.area_per_kwp_m2
        )

    def resolved_capacity_kwp(self, presets: PvPresets | None = None) -> float:
        """면적 환산 용량. 직접 입력이 있으면 그쪽이 이긴다."""
        if self.capacity_kwp is not None:
            return self.capacity_kwp
        table = presets if presets is not None else load_pv_presets()
        return self.roof_capacity_kwp(table) + self.wall_capacity_kwp(table)

    def cost(self) -> PvCostInput:
        """투자비 입력. **없으면 0원이 아니라 미산출**이고 사유가 붙는다 (7.5)."""
        if self.total_investment_won is not None:
            return PvCostInput.of_total(self.total_investment_won)
        if self.unit_cost_won_per_kwp is not None:
            return PvCostInput.of_unit_cost(self.unit_cost_won_per_kwp)
        return PvCostInput.unpriced()


def solar_config(inputs: SolarInputs, *, presets: PvPresets | None = None) -> PvSystemConfig:
    """PV 시스템 설정. 밀도 프리셋이 GCR 과 경사각을 **함께** 정한다 (3.3).

    벽면 면적을 넣으면 **어레이가 둘**이 된다 (15세션). 방위를 따로 고를 수 있고,
    단위 프로파일은 두 어레이를 합친 것을 총용량으로 나눈 값이라 섞인 방위가
    그대로 반영된다.
    """
    table = presets if presets is not None else load_pv_presets()
    latitude, longitude = inputs.coordinates()
    roof_kwp = inputs.roof_capacity_kwp(table)
    wall_kwp = inputs.wall_capacity_kwp(table)
    arrays = [
        ArrayConfig(
            name="지붕",
            capacity_kwp=max(roof_kwp, 1.0) if wall_kwp <= 0 else roof_kwp,
            tilt_deg=inputs.resolved_tilt_deg(table),
            azimuth_deg=(
                inputs.azimuth_deg if inputs.azimuth_deg is not None else table.default_azimuth_deg
            ),
            gcr=inputs.resolved_gcr(table),
            system_loss_ratio=inputs.system_loss_ratio,
        )
    ]
    if wall_kwp > 0:
        arrays.append(
            ArrayConfig.wall(
                "벽면",
                wall_kwp,
                azimuth_deg=(
                    inputs.wall_azimuth_deg
                    if inputs.wall_azimuth_deg is not None
                    else table.default_azimuth_deg
                ),
                gcr=1.0,
                system_loss_ratio=inputs.system_loss_ratio,
            )
        )
    config = PvSystemConfig(
        latitude=latitude,
        longitude=longitude,
        arrays=tuple(arrays),
        altitude_m=inputs.altitude_m,
        timezone="Asia/Seoul",
    )
    # 단위 프로파일용으로 크기만 맞춘다. **어레이 비율은 그대로다** — 실제 용량은
    # 곡선이 훑는다.
    target = max(inputs.resolved_capacity_kwp(table), 1.0)
    return config.scaled(target) if config.total_capacity_kwp > 0 else config


@dataclass(frozen=True)
class AzimuthOption:
    """방위 하나의 상대 발전량 (15세션 1-1).

    **비율을 하드코딩하지 않는다.** 경사각이 낮으면 방위 영향이 줄어드는데
    밀도 '높음' 은 경사 15° 라 차이가 훨씬 작다 — 지역·경사각으로 그때그때
    계산해야 라벨이 거짓말을 하지 않는다.
    """

    preset: AzimuthPreset
    generation_kwh_per_kwp: float
    ratio: float
    """기준(남) 대비 비율. 남이면 1.0 이다."""

    @property
    def key(self) -> str:
        return self.preset.key

    @property
    def label(self) -> str:
        """``남 (기준)`` · ``남동 −4%``. 고르는 자리에서 대가가 보여야 한다."""
        if abs(self.ratio - 1.0) < 5e-4:
            return f"{self.preset.label} (기준)"
        delta = (self.ratio - 1.0) * 100.0
        sign = "+" if delta > 0 else "−"
        return f"{self.preset.label} {sign}{abs(delta):.0f}%"


def azimuth_options(
    usage: UsageData,
    inputs: SolarInputs,
    *,
    tilt_deg: float | None = None,
    gcr: float | None = None,
    weather: WeatherData | None = None,
    presets: PvPresets | None = None,
) -> tuple[AzimuthOption, ...]:
    """여덟 방위를 각각 돌려 상대 발전량을 낸다 (15세션 1-1).

    시뮬레이션 여덟 번이다 (한 번에 0.1초 남짓). 용량은 1 kWp 로 고정하므로
    **면적·밀도를 바꿔도 비율은 그대로**이고, 캐시가 지역·경사·손실로 걸린다.

    Args:
        tilt_deg: 경사각. 벽면 비교에 90° 를 넘긴다. 기본은 밀도 프리셋 값.
    """
    table = presets if presets is not None else load_pv_presets()
    data = weather if weather is not None else load_weather_for(usage, inputs)
    latitude, longitude = inputs.coordinates()
    tilt = inputs.resolved_tilt_deg(table) if tilt_deg is None else tilt_deg
    density = inputs.resolved_gcr(table) if gcr is None else gcr

    hours = usage.meta.interval_minutes / 60.0
    totals: list[tuple[AzimuthPreset, float]] = []
    for preset in table.azimuths:
        config = PvSystemConfig(
            latitude=latitude,
            longitude=longitude,
            arrays=(
                ArrayConfig(
                    name="비교",
                    capacity_kwp=1.0,
                    tilt_deg=tilt,
                    azimuth_deg=preset.azimuth_deg,
                    gcr=density,
                    system_loss_ratio=inputs.system_loss_ratio,
                ),
            ),
            altitude_m=inputs.altitude_m,
            timezone="Asia/Seoul",
        )
        profile = unit_generation_kw(usage, data, config)
        totals.append((preset, float(profile.sum()) * hours))

    reference = next(
        (value for preset, value in totals if preset.key == table.default_azimuth),
        max((value for _, value in totals), default=0.0),
    )
    return tuple(
        AzimuthOption(
            preset=preset,
            generation_kwh_per_kwp=value,
            ratio=value / reference if reference > 0 else 0.0,
        )
        for preset, value in totals
    )


def load_weather_for(usage: UsageData, inputs: SolarInputs) -> WeatherData:
    """기상 취득. 캐시 → API → 사전 취득분 순은 :func:`load_weather` 가 정한다."""
    latitude, longitude = inputs.coordinates()
    request = WeatherRequest.for_index(pd.DatetimeIndex(usage.kw.index), latitude, longitude)
    return load_weather(request)


def daily_temperature(usage: UsageData, region_key: str) -> tuple[pd.Series, str] | None:
    """부하 기간의 시간별 기온 (℃)과 **기상 출처**. 없으면 ``None`` (30세션 4절).

    태양광이 쓰는 것과 **같은 지역·기간·격자**를 쓴다 — 같은
    :class:`~kwise.pv.WeatherRequest` 를 만들므로 캐시 파일도 같은 것을 본다.
    한 화면에서 기온이 둘로 갈리지 않는다.

    **출처를 함께 돌려준다** (31세션 4-2). 태양광 카드가 출처를 적는데 기온
    그래프만 적지 않으면, 같은 자료를 쓴다는 사실이 화면에서 끊긴다.

    **예외를 밖으로 내보내지 않는다.** 진단은 설비 정보 없이 돌아야 하는 화면이라,
    기상을 못 받았다고 1단계가 통째로 죽으면 안 된다. 못 받으면 그림을 감춘다.
    """
    if not region_key:
        return None
    try:
        weather = load_weather_for(usage, SolarInputs(region_key=region_key))
    except Exception:
        # 좌표를 못 찾거나(지역 데이터 변경) 기상을 못 받은 경우 — 둘 다 「없음」이다.
        return None
    hourly = weather.hourly
    if "temp_air" not in hourly.columns:
        return None
    series = pd.Series(hourly["temp_air"].astype(float).to_numpy(), index=hourly.index)
    return series, weather.source


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


#: 용량 비교 표에 세우는 잉여 지점 둘의 표식 (31세션 4-1). **표식과 계산을 한
#: 자리에 묶는다** — 이름을 화면에서 따로 붙이면 어느 줄이 어느 값인지 어긋난다.
SURPLUS_ONSET_LABEL = "잉여 시작"
SURPLUS_HEAVY_LABEL = "잉여 다량"


def surplus_capacity_points(
    usage: UsageData,
    table: TariffTable,
    form: ContractForm,
    unit_profile: pd.Series,
    *,
    cost: PvCostInput | None = None,
    baseline: BillingResult | None = None,
    quality: QualityReport | None = None,
) -> tuple[tuple[str, SolarPoint], ...]:
    """**곡선 밖의 잉여 지점 둘** — 용량 비교 표가 세울 줄 (31세션 4-1).

    잉여가 처음 생기는 용량은 설치 가능 면적이 허용하는 용량보다 클 수 있다.
    그러면 곡선 어디에도 없으므로 :func:`~kwise.measures.solar_point` 로 그
    용량 하나만 따로 계산한다 — 요금 재계산 두 번이다.

    용량이 0 이거나 (부하가 0 인 슬롯에 발전이 있는 경우) 목표 비중에 닿지
    못하면 그 줄은 **없는 채로 둔다.** 지어내지 않는다.
    """
    onset = surplus_free_capacity_kwp(usage, unit_profile)
    heavy = surplus_share_capacity_kwp(usage, unit_profile, share=surplus_heavy_share())
    wanted = ((SURPLUS_ONSET_LABEL, onset), (SURPLUS_HEAVY_LABEL, heavy))

    points: list[tuple[str, SolarPoint]] = []
    for label, capacity in wanted:
        if capacity is None or capacity <= 0:
            continue
        points.append(
            (
                label,
                solar_point(
                    usage,
                    table,
                    form.selection,
                    unit_profile,
                    capacity,
                    # **곡선과 같은 단가를 쓴다.** 다른 값을 쓰면 한 표 안에서
                    # 회수기간 열의 잣대가 줄마다 달라진다.
                    cost=cost,
                    power_factor_pct=form.lagging_pct,
                    baseline=baseline,
                    quality=quality,
                    options=form.billing_options(),
                ),
            )
        )
    return tuple(points)


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
    power_factor_pct: float | None = None,
    power_factor_investment_won: float | None = None,
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

    # 7.4 — 역률은 부하를 바꾸지 않고 기본요금 조정액만 바꾼다. **조합에 넣어야
    # 상충이 보인다** — 태양광·ESS 가 기본요금을 낮추면 역률 감액도 함께 준다.
    if "power_factor" in chosen and power_factor_pct is not None:
        cursor = replace(
            cursor,
            name=f"{_plus(specs)}역률 {power_factor_pct:,.0f}%",
            power_factor_pct=power_factor_pct,
            power_factor_investment_won=power_factor_investment_won,
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
