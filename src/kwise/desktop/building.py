"""건물 공통 정보 — 데스크톱 옆단이 쥔다.

``kwise.ui.building`` 의 순수한 부분(``BuildingInfo`` · 용도 목록 · 종별 좁히기 · 원단위)과
``ui\\views\\measures.py`` 의 기상 출처 이름을 **옮겨 적었다** — 그 두 모듈은 streamlit 을
끌어와 부를 수 없다. **저쪽을 고치면 여기도 고친다.** 값(용도 목록)은 같은 기준 데이터
(``assumptions.json`` 의 ``building.uses``)에서 읽으므로 두 화면이 갈리지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass

from kwise.quality import DEFAULT_OPERATING_HOURS
from kwise.rules import assumption
from kwise.ui.pipeline import ContractChoice

#: 건물명을 넣지 않았을 때의 이름 — Streamlit 과 같다.
NAME_MISSING = "건물명 미입력"

#: 기상 출처 이름 — Streamlit ``WEATHER_SOURCE_LABELS`` 와 같다.
_WEATHER_SOURCE_LABELS = {
    "network": "Open-Meteo",
    "cache": "Open-Meteo",
    "archive": "아카이브(Open-Meteo)",
}


@dataclass(frozen=True)
class BuildingUse:
    key: str
    label: str
    contract_prefixes: tuple[str, ...]
    old_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class BuildingInfo:
    """옆단의 건물 정보. 지역 말고는 다 선택 항목이다."""

    region_key: str = ""
    name: str = ""
    use_key: str = ""
    floor_area_m2: float | None = None
    built_year: int | None = None
    operating_hours: tuple[int, int] = DEFAULT_OPERATING_HOURS

    @property
    def title(self) -> str:
        return self.name.strip() or NAME_MISSING

    @property
    def jeju(self) -> bool:
        """제주면 DR 입찰 창이 갈린다."""
        return self.region_key.startswith("제주")

    @property
    def education(self) -> bool:
        return resolve_use_key(self.use_key) == "education"


def building_uses() -> tuple[BuildingUse, ...]:
    return tuple(
        BuildingUse(
            key=str(item["key"]),
            label=str(item.get("label", item["key"])),
            contract_prefixes=tuple(str(name) for name in item.get("contract_prefixes", ())),
            old_keys=tuple(str(name) for name in item.get("old_keys", ())),
        )
        for item in assumption("building.uses") or ()
    )


def resolve_use_key(use_key: str) -> str:
    """옛 열쇠(「공장」 · 「학교」)는 합친 쪽으로 읽는다."""
    return next(
        (item.key for item in building_uses() if use_key == item.key or use_key in item.old_keys),
        use_key,
    )


def narrow_contract_types(
    choices: tuple[ContractChoice, ...], use_key: str
) -> tuple[ContractChoice, ...]:
    """용도로 계약종별 후보를 좁힌다 — 좁히기이지 판정이 아니다. 못 좁히면 전부."""
    key = resolve_use_key(use_key)
    prefixes = next((item.contract_prefixes for item in building_uses() if item.key == key), ())
    if not prefixes:
        return choices
    narrowed = tuple(
        item for item in choices if any(item.contract_type.startswith(p) for p in prefixes)
    )
    return narrowed or choices


def intensity_kwh_per_m2(total_kwh: float, building: BuildingInfo | None) -> float | None:
    """기간 원단위 (기간 사용량 ÷ 연면적). 연면적이 없으면 ``None``."""
    if building is None or not building.floor_area_m2:
        return None
    return total_kwh / building.floor_area_m2


def weather_source_label(source: str) -> str:
    """``cache`` → ``Open-Meteo``. 모르는 값은 「기타」 — 코드 이름을 화면에 안 낸다."""
    return _WEATHER_SOURCE_LABELS.get(source, "기타")
