"""태양광 간략 입력 (요구사항서 3.3).

**기본 입력은 셋뿐이다 — 설치 가능 면적 · 설치 밀도 · 지역.**

요금 절감 검토 단계에서 사용자가 아는 것은 유휴 면적 정도다. 방위·경사·손실률을
정밀하게 받아도 발전량 예측 R² 가 0.8 이라 **기상 데이터가 지배하는 오차 앞에서
입력 정밀도는 효과가 없다.** 게다가 PV 용량 곡선이 이미 용량을 20단계로 훑으므로,
면적→용량 환산이 ±20% 틀려도 곡선 위의 다른 점을 보는 것일 뿐 결론이 바뀌지 않는다.

나머지(방위·경사·손실률·벽면 PV·다중 어레이)는 :mod:`kwise.pv.config` 에 그대로
있고 기본값으로 자동 적용된다. 8세션 UI 는 **확장 패널에 접어 둔다.**
**모드를 나누지 않는다** — 상세 설계 모드를 만들면 pvsim 과 경계가 흐려진다.

설치 밀도 프리셋이 GCR 과 경사각을 **함께** 정한다. 고밀도 배치는 이격을 좁히는
대신 경사를 낮춰 음영을 줄이는 것이 실무 관행이다. 프리셋 값은 다른 기준 데이터와
같은 자리 — ``data\\assumptions.json`` 의 ``pv.*`` 항목 — 에 있다. 하드코딩하지
않는다. 별도 파일로 떼면 편집·원복·만료 감지(:mod:`kwise.rules`)가 닿지 않는다.

**방위도 프리셋이다** (15세션). 각도 숫자 대신 8방위로 받는다 — 45도 간격이면
최대 오차 22.5도이고 발전량 차이가 1~2% 라 예측 오차(R² 0.8) 안에 묻힌다.
상대 발전량은 **하드코딩하지 않는다**. 경사각이 낮으면 방위 영향이 줄어들기
때문에(밀도 '높음'은 경사 15°다) 선택된 지역·경사각으로 그때그때 계산한다.

**내부 키(``high``/``normal``/``low``)와 표시 라벨(높음/보통/낮음)을 분리한다.**
그리고 선택지마다 **상충 관계를 한 줄로 병기한다** — 밀도만 보아서는 "높으면 좋은
것인지" 를 알 수 없기 때문이다. 밀도를 높이면 면적당 용량이 커지는 대신 kWp당
발전량이 작아진다. 화면에서는 고르는 즉시 환산 용량을 함께 보여 준다
(:func:`capacity_preview`).

    설치 용량(kWp) ≈ 설치 가능 면적 × GCR ÷ 5

모듈 효율 20% 기준 1 kWp 당 약 5 m² 다. 이 상수도 설정 파일에 있다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kwise.pv.config import ArrayConfig, PvSystemConfig
from kwise.pv.region import Region
from kwise.rules import assumption

__all__ = [
    "AzimuthPreset",
    "DensityPreset",
    "PvPresetError",
    "PvPresets",
    "PvQuickInput",
    "area_from_capacity_m2",
    "capacity_from_area_kwp",
    "capacity_preview",
    "load_pv_presets",
]


class PvPresetError(ValueError):
    """프리셋 설정을 읽지 못했을 때 발생한다."""


@dataclass(frozen=True)
class DensityPreset:
    """설치 밀도 하나. **GCR 과 경사각이 함께 움직인다.**

    Attributes:
        key: 내부 키 (``high``/``normal``/``low``). 코드와 케이스 정의가 쓴다.
        label: 표시 라벨 (높음/보통/낮음). 산출물에 나가는 이름이다.
        tradeoff: 상충 관계 한 줄. **밀도만으로는 좋고 나쁨을 알 수 없다.**
    """

    key: str
    label: str
    gcr: float
    tilt_deg: float
    tradeoff: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        if not 0.0 < self.gcr <= 1.0:
            raise ValueError(f"GCR 은 0 초과 1 이하여야 합니다: {self.gcr}")
        if not 0.0 <= self.tilt_deg <= 90.0:
            raise ValueError(f"경사각은 0~90° 여야 합니다: {self.tilt_deg}")


@dataclass(frozen=True)
class AzimuthPreset:
    """방위 하나 (15세션).

    **각도가 아니라 방위로 고른다.** 45도 간격의 오차(최대 22.5도)는 발전량
    1~2% 차이라 예측 오차 안에 묻히고, 사용자는 각도를 모른다.

    Attributes:
        caution: 북향 계열에 붙는 주의. **고를 수는 있게 두고 사실만 적는다.**
    """

    key: str
    label: str
    azimuth_deg: float
    caution: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.azimuth_deg < 360.0:
            raise ValueError(f"방위각은 0 이상 360 미만이어야 합니다: {self.azimuth_deg}")

    @property
    def is_northward(self) -> bool:
        """북향 계열인가. 주의 문구가 붙어 있으면 그렇다."""
        return bool(self.caution)


@dataclass(frozen=True)
class PvPresets:
    """프리셋 한 벌. 드롭다운을 이걸로 만든다."""

    densities: tuple[DensityPreset, ...]
    area_per_kwp_m2: float
    default_azimuth_deg: float
    default_density: str
    density_label: str = "설치 밀도"
    azimuths: tuple[AzimuthPreset, ...] = field(default=())
    default_azimuth: str = ""

    def density(self, key: str) -> DensityPreset:
        for item in self.densities:
            if item.key == key:
                return item
        raise PvPresetError(
            f"없는 설치 밀도입니다: {key!r} "
            f"(가능: {', '.join(item.key for item in self.densities)})"
        )

    def azimuth(self, key: str) -> AzimuthPreset:
        for item in self.azimuths:
            if item.key == key:
                return item
        raise PvPresetError(
            f"없는 방위입니다: {key!r} (가능: {', '.join(item.key for item in self.azimuths)})"
        )

    def azimuth_of(self, degrees: float) -> AzimuthPreset | None:
        """각도에 해당하는 방위. 45도 격자에 없는 각도면 ``None`` 이다."""
        for item in self.azimuths:
            if abs(item.azimuth_deg - degrees) < 1e-9:
                return item
        return None

    @property
    def default(self) -> DensityPreset:
        return self.density(self.default_density)

    @property
    def default_azimuth_preset(self) -> AzimuthPreset:
        return self.azimuth(self.default_azimuth)


def load_pv_presets() -> PvPresets:
    """``data\assumptions.json`` 에서 프리셋을 읽는다.

    **코드에 기본값을 두지 않는다** (요구사항서 12장). 항목이 빠지면
    :class:`~kwise.rules.RuleDataError` 로 멈춘다.
    """
    raw = assumption("pv.densities")
    if not raw:
        raise PvPresetError("설치 밀도 프리셋이 비어 있습니다 (assumptions.json: pv.densities).")
    densities = tuple(
        DensityPreset(
            key=str(item["key"]),
            label=str(item.get("label", item["key"])),
            gcr=float(item["gcr"]),
            tilt_deg=float(item["tilt_deg"]),
            tradeoff=str(item.get("tradeoff", "")),
            description=str(item.get("description", "")),
        )
        for item in raw
    )
    area_per_kwp = float(assumption("pv.area_per_kwp_m2"))
    if area_per_kwp <= 0:
        raise PvPresetError(f"kWp 당 면적은 양수여야 합니다: {area_per_kwp}")
    azimuths = tuple(
        AzimuthPreset(
            key=str(item["key"]),
            label=str(item.get("label", item["key"])),
            azimuth_deg=float(item["azimuth_deg"]),
            caution=str(item.get("caution", "")),
        )
        for item in assumption("pv.azimuths") or ()
    )
    if not azimuths:
        raise PvPresetError("방위 선택지가 비어 있습니다 (assumptions.json: pv.azimuths).")
    presets = PvPresets(
        densities=densities,
        area_per_kwp_m2=area_per_kwp,
        default_azimuth_deg=float(assumption("pv.default_azimuth_deg")),
        default_density=str(assumption("pv.default_density")),
        density_label=str(assumption("pv.density_label")),
        azimuths=azimuths,
        default_azimuth=str(assumption("pv.default_azimuth")),
    )
    # 기본 밀도·기본 방위가 목록에 있는지 여기서 확인한다.
    _ = presets.default, presets.default_azimuth_preset
    if presets.azimuth_of(presets.default_azimuth_deg) is None:
        raise PvPresetError(
            f"기본 방위각 {presets.default_azimuth_deg:g}° 가 방위 목록에 없습니다. "
            "pv.default_azimuth_deg 와 pv.azimuths 가 어긋납니다."
        )
    return presets


def capacity_preview(
    area_m2: float,
    presets: PvPresets | None = None,
) -> tuple[tuple[DensityPreset, float], ...]:
    """밀도별 환산 용량. **고르는 즉시 함께 보여 준다** (8세션 UI).

    1,000 m² 면 높음 110 / 보통 80 / 낮음 60 kWp 다. 숫자를 함께 보여 주지 않으면
    "밀도 높음" 이 무엇을 뜻하는지 알 수 없다.
    """
    items = presets if presets is not None else load_pv_presets()
    return tuple(
        (
            preset,
            capacity_from_area_kwp(area_m2, gcr=preset.gcr, area_per_kwp_m2=items.area_per_kwp_m2),
        )
        for preset in items.densities
    )


def capacity_from_area_kwp(
    area_m2: float,
    *,
    gcr: float,
    area_per_kwp_m2: float,
) -> float:
    """면적 → 용량. ``면적 × GCR ÷ (kWp 당 면적)``.

    GCR 이 모듈이 덮는 비율이고, 모듈 면적을 효율로 나누면 용량이다.
    """
    if area_m2 < 0:
        raise ValueError(f"설치 가능 면적은 음수일 수 없습니다: {area_m2}")
    return area_m2 * gcr / area_per_kwp_m2


def area_from_capacity_m2(
    capacity_kwp: float,
    *,
    gcr: float,
    area_per_kwp_m2: float,
) -> float:
    """용량 → 면적. :func:`capacity_from_area_kwp` 의 역이다 (26세션 3-2).

    「잉여가 생기지 않는 최대 용량」 을 낼 때 **그 용량이 몇 평인지**를 함께 적는다.
    kWp 는 설비 규격이라 지붕을 보고 판단할 수 없다.
    """
    if capacity_kwp < 0:
        raise ValueError(f"용량은 음수일 수 없습니다: {capacity_kwp}")
    if gcr <= 0:
        raise ValueError(f"GCR 은 양수여야 합니다: {gcr}")
    return capacity_kwp * area_per_kwp_m2 / gcr


@dataclass(frozen=True)
class PvQuickInput:
    """간략 입력 한 벌.

    앞의 셋이 기본 입력이고, 나머지는 **확장 패널**에 접어 두는 값이다.
    기본값 그대로 두면 3개만 물어도 시뮬레이션이 돈다.

    Attributes:
        capacity_override_kwp: 확장 패널에서 용량을 직접 지정한 값.
            주면 면적 환산을 **덮어쓴다** — 견적서에 용량이 적혀 있는 경우다.
    """

    area_m2: float
    density: str | None = None
    region: Region | None = None
    latitude: float | None = None
    longitude: float | None = None

    # --- 확장 패널 (기본값으로 자동 적용된다)
    capacity_override_kwp: float | None = None
    azimuth_deg: float | None = None
    tilt_override_deg: float | None = None
    gcr_override: float | None = None
    system_loss_ratio: float | None = None
    altitude_m: float = 0.0
    timezone: str = "Asia/Seoul"
    array_name: str = "지붕"
    presets: PvPresets | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.area_m2 < 0:
            raise ValueError(f"설치 가능 면적은 음수일 수 없습니다: {self.area_m2}")
        if self.capacity_override_kwp is not None and self.capacity_override_kwp < 0:
            raise ValueError(f"설치 용량은 음수일 수 없습니다: {self.capacity_override_kwp}")
        if self.region is None and (self.latitude is None or self.longitude is None):
            raise ValueError(
                "지역을 정하지 못했습니다. 시군구(region)를 고르거나 "
                "위경도(latitude·longitude)를 직접 넣어 주십시오."
            )

    @property
    def _presets(self) -> PvPresets:
        return self.presets if self.presets is not None else load_pv_presets()

    @property
    def preset(self) -> DensityPreset:
        """고른 설치 밀도. **GCR 과 경사각을 함께 들고 있다.**"""
        presets = self._presets
        return presets.default if self.density is None else presets.density(self.density)

    @property
    def gcr(self) -> float:
        return self.gcr_override if self.gcr_override is not None else self.preset.gcr

    @property
    def tilt_deg(self) -> float:
        if self.tilt_override_deg is not None:
            return self.tilt_override_deg
        return self.preset.tilt_deg

    @property
    def azimuth(self) -> float:
        return (
            self.azimuth_deg if self.azimuth_deg is not None else self._presets.default_azimuth_deg
        )

    @property
    def area_capacity_kwp(self) -> float:
        """면적에서 환산한 용량. 화면에 그대로 보여 준다."""
        return capacity_from_area_kwp(
            self.area_m2, gcr=self.gcr, area_per_kwp_m2=self._presets.area_per_kwp_m2
        )

    @property
    def capacity_kwp(self) -> float:
        """실제로 쓸 용량. **직접 지정한 값이 면적 환산을 덮어쓴다.**"""
        if self.capacity_override_kwp is not None:
            return self.capacity_override_kwp
        return self.area_capacity_kwp

    @property
    def coordinate(self) -> tuple[float, float]:
        """위경도. 시군구를 골랐으면 그 중심점이다."""
        if self.latitude is not None and self.longitude is not None:
            return (self.latitude, self.longitude)
        assert self.region is not None  # __post_init__ 이 보장한다
        return (self.region.latitude, self.region.longitude)

    def to_system(self) -> PvSystemConfig:
        """시뮬레이션에 넘길 설비 구성. 확장 패널 값이 없으면 기본값이 들어간다."""
        latitude, longitude = self.coordinate
        options: dict[str, float] = {
            "tilt_deg": self.tilt_deg,
            "azimuth_deg": self.azimuth,
            "gcr": self.gcr,
        }
        if self.system_loss_ratio is not None:
            options["system_loss_ratio"] = self.system_loss_ratio
        return PvSystemConfig(
            latitude=latitude,
            longitude=longitude,
            arrays=(ArrayConfig.roof(self.array_name, self.capacity_kwp, **options),),
            altitude_m=self.altitude_m,
            timezone=self.timezone,
        )

    def describe(self) -> str:
        """화면에 그대로 쓸 한 줄. 환산 근거를 밝힌다."""
        preset = self.preset
        source = (
            "직접 입력"
            if self.capacity_override_kwp is not None
            else f"{self.area_m2:,.0f} m² × GCR {self.gcr:.2f} ÷ "
            f"{self._presets.area_per_kwp_m2:.0f} m²/kWp"
        )
        where = self.region.label if self.region is not None else "좌표 직접 입력"
        return (
            f"{where} · 밀도 '{preset.key}' (GCR {preset.gcr:.2f}, 경사 {self.tilt_deg:.0f}°) · "
            f"설치 용량 {self.capacity_kwp:,.0f} kWp ({source})"
        )
