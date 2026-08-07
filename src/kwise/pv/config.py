"""태양광 설비 구성 (요구사항서 3.3, 7.5).

다중 어레이를 지원한다. 지붕과 벽면을 함께 올릴 수 있고 벽면은 경사각 90°다.
어레이 상호 음영은 GCR(이격거리비) 하나로 :mod:`pvlib.bifacial.infinite_sheds`
에 넘긴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "DEFAULT_ALBEDO",
    "DEFAULT_AZIMUTH_DEG",
    "DEFAULT_DC_AC_RATIO",
    "DEFAULT_GCR",
    "DEFAULT_SYSTEM_LOSS",
    "DEFAULT_TILT_DEG",
    "WALL_TILT_DEG",
    "ArrayConfig",
    "Mounting",
    "PvSystemConfig",
]

# 요구사항서 3.3 의 기본값
DEFAULT_TILT_DEG = 30.0
DEFAULT_AZIMUTH_DEG = 180.0
DEFAULT_GCR = 0.4
DEFAULT_SYSTEM_LOSS = 0.14
DEFAULT_ALBEDO = 0.2
DEFAULT_DC_AC_RATIO = 1.2
WALL_TILT_DEG = 90.0


class Mounting(StrEnum):
    """설치 방식. 셀 온도 모델 계수를 고른다."""

    OPEN_RACK = "open_rack"
    CLOSE_MOUNT = "close_mount"

    @property
    def sapm_key(self) -> str:
        return (
            "close_mount_glass_glass" if self is Mounting.CLOSE_MOUNT else "open_rack_glass_glass"
        )


@dataclass(frozen=True)
class ArrayConfig:
    """어레이 하나.

    Attributes:
        capacity_kwp: 직류 정격 (kWp).
        tilt_deg: 경사각. 벽면은 90°.
        azimuth_deg: 방위각 (남 180°).
        gcr: 이격거리비. 어레이 상호 음영을 이 값 하나로 표현한다.
        height_to_pitch: 열 중심 높이 ÷ 열 간격. infinite_sheds 는 이 비율에만
            의존하므로 pitch 를 1 로 두고 이 값을 높이로 넘긴다.
        bifaciality: 양면 계수. 0 이면 단면 모듈이다.
        system_loss_ratio: 시스템 손실 (기본 14%).
        dc_ac_ratio: 직류/교류 비. 인버터 용량 = 정격 ÷ 이 값.
    """

    name: str
    capacity_kwp: float
    tilt_deg: float = DEFAULT_TILT_DEG
    azimuth_deg: float = DEFAULT_AZIMUTH_DEG
    gcr: float = DEFAULT_GCR
    height_to_pitch: float = 0.5
    albedo: float = DEFAULT_ALBEDO
    bifaciality: float = 0.0
    mounting: Mounting = Mounting.OPEN_RACK
    gamma_pdc_per_c: float = -0.0035
    system_loss_ratio: float = DEFAULT_SYSTEM_LOSS
    dc_ac_ratio: float = DEFAULT_DC_AC_RATIO

    def __post_init__(self) -> None:
        if self.capacity_kwp < 0:
            raise ValueError(f"설치 용량은 음수일 수 없습니다: {self.capacity_kwp}")
        if not 0.0 <= self.tilt_deg <= 90.0:
            raise ValueError(f"경사각은 0~90° 여야 합니다: {self.tilt_deg}")
        if not 0.0 < self.gcr <= 1.0:
            raise ValueError(f"GCR 은 0 초과 1 이하여야 합니다: {self.gcr}")
        if not 0.0 <= self.system_loss_ratio < 1.0:
            raise ValueError(f"시스템 손실은 0 이상 1 미만이어야 합니다: {self.system_loss_ratio}")
        if self.dc_ac_ratio <= 0:
            raise ValueError(f"직류/교류 비는 양수여야 합니다: {self.dc_ac_ratio}")

    @classmethod
    def roof(cls, name: str, capacity_kwp: float, **kwargs: float) -> ArrayConfig:
        """지붕 어레이. 경사각 30°, 방위각 180° 가 기본이다."""
        return cls(name=name, capacity_kwp=capacity_kwp, **kwargs)  # type: ignore[arg-type]

    @classmethod
    def wall(cls, name: str, capacity_kwp: float, **kwargs: float) -> ArrayConfig:
        """벽면 어레이. 경사각을 90° 로 고정한다."""
        options: dict[str, float] = {"tilt_deg": WALL_TILT_DEG}
        options.update(kwargs)
        return cls(name=name, capacity_kwp=capacity_kwp, **options)  # type: ignore[arg-type]

    @property
    def inverter_ac_kw(self) -> float:
        return self.capacity_kwp / self.dc_ac_ratio

    @property
    def is_wall(self) -> bool:
        return self.tilt_deg >= WALL_TILT_DEG - 1e-9


@dataclass(frozen=True)
class PvSystemConfig:
    """설비 전체. 위치와 어레이 목록."""

    latitude: float
    longitude: float
    arrays: tuple[ArrayConfig, ...] = field(default=())
    altitude_m: float = 0.0
    timezone: str = "Asia/Seoul"

    def __post_init__(self) -> None:
        names = [array.name for array in self.arrays]
        if len(names) != len(set(names)):
            raise ValueError(f"어레이 이름이 겹칩니다: {names}")

    @property
    def total_capacity_kwp(self) -> float:
        return sum(array.capacity_kwp for array in self.arrays)

    def scaled(self, capacity_kwp: float) -> PvSystemConfig:
        """전체 용량을 바꾼 사본. 용량 곡선(7.3)에서 쓴다.

        어레이 비율은 그대로 두고 크기만 조절한다. 현재 용량이 0 이면 만들 수 없다.
        """
        current = self.total_capacity_kwp
        if current <= 0:
            raise ValueError("용량이 0 인 구성은 비례 확대할 수 없습니다.")
        ratio = capacity_kwp / current
        from dataclasses import replace

        return replace(
            self,
            arrays=tuple(
                replace(array, capacity_kwp=array.capacity_kwp * ratio) for array in self.arrays
            ),
        )
