"""건물 공통 정보 — 옆단 (요구사항서 3.2 · 16세션 2절).

**옆단은 건물 이야기다.** 건물명·용도·지역·연면적·준공연도는 어느 탭에서나
같은 값이고, 화면을 옮길 때마다 다시 묻거나 카드마다 흩어 두면 같은 사실을
두 곳에 넣게 된다. 계약 정보(계약종별·전압·계약전력·선택요금)는 **건물이 아니라
계약**이라 1단계 진단 탭에 둔다.

    건물명      선택. 없으면 산출물 제목이 「건물명 미입력」 이다
    용도        계약종별 후보를 좁힌다 — **판정이 아니라 좁히기다**
    지역        태양광 기상 격자. 태양광 카드에서 여기로 올렸다
    운영 시간대  무인시간 판정과 DR 저부하일 판정에 쓴다. 기본 09~18시
    연면적      선택. 있으면 연간 원단위(kWh/m²·년)를 진단에 한 줄 얹는다
    준공연도    선택. 지금은 기록만 한다

**운영 시간대는 경제성DR 의 시장 운영 시간대와 다른 값이다** (21세션 4절).
이쪽은 사람이 그 시간에 일하느냐이고, 저쪽은 제도가 정한 입찰 가능 시간대다
(``dr.market_hours``, 평일 09~12·13~20시). 이름을 갈라 두지 않으면 8시 출근
사업장의 사정이 제도 값을 흔들게 된다.

**용도로 계약종별을 확정하지 않는다.** 대응은 흔한 경우를 적은 판단값이고
(``assumptions.json`` 의 ``building.uses``), 최종 판단은 청구서 기재값이다.
고르지 않으면 전 종별을 보인다.
"""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

from kwise.pv import list_provinces, list_sigungu
from kwise.quality import DEFAULT_OPERATING_HOURS
from kwise.rules import assumption

__all__ = [
    "BUILDING_KEY",
    "NAME_MISSING",
    "BuildingInfo",
    "BuildingUse",
    "building_uses",
    "get_building",
    "intensity_kwh_per_m2",
    "narrow_contract_types",
    "render_sidebar",
]

BUILDING_KEY = "building_info"

#: 건물명을 넣지 않았을 때 산출물 제목에 쓴다. **빈 칸으로 두지 않는다** —
#: 표지가 비어 있으면 만들다 만 문서로 보인다.
NAME_MISSING = "건물명 미입력"

_UNSET = ""
_UNSET_LABEL = "선택 안 함"


@dataclass(frozen=True)
class BuildingUse:
    """건물 용도 하나와 그에 흔히 붙는 계약종별."""

    key: str
    label: str
    contract_prefixes: tuple[str, ...]


@dataclass(frozen=True)
class BuildingInfo:
    """옆단이 쥐고 있는 건물 공통 정보. **셋은 선택 항목이다.**"""

    region_key: str
    name: str = ""
    use_key: str = ""
    floor_area_m2: float | None = None
    built_year: int | None = None
    operating_hours: tuple[int, int] = DEFAULT_OPERATING_HOURS
    """**건물** 운영 시간대. DR 시장 시간대(제도 규정)와 다른 값이다."""

    @property
    def title(self) -> str:
        """산출물 제목에 쓸 이름. 비었으면 :data:`NAME_MISSING`."""
        return self.name.strip() or NAME_MISSING

    @property
    def named(self) -> bool:
        return bool(self.name.strip())


def building_uses() -> tuple[BuildingUse, ...]:
    """용도 목록. **코드에 표를 두지 않는다** (요구사항서 12장)."""
    return tuple(
        BuildingUse(
            key=str(item["key"]),
            label=str(item.get("label", item["key"])),
            contract_prefixes=tuple(str(name) for name in item.get("contract_prefixes", ())),
        )
        for item in assumption("building.uses") or ()
    )


def narrow_contract_types(
    choices: tuple[tuple[str, str], ...], use_key: str
) -> tuple[tuple[str, str], ...]:
    """용도로 계약종별 후보를 좁힌다. **좁힐 수 없으면 전부 돌려준다.**

    고르지 않았거나(``""``) 모르는 용도이거나 남는 것이 없으면 전 종별이다 —
    좁히기가 실패했다고 고를 것이 사라지면 입력 자체를 못 한다.
    """
    prefixes = next((item.contract_prefixes for item in building_uses() if item.key == use_key), ())
    if not prefixes:
        return choices
    narrowed = tuple(
        item for item in choices if any(item[0].startswith(prefix) for prefix in prefixes)
    )
    return narrowed or choices


def intensity_kwh_per_m2(total_kwh: float, building: BuildingInfo | None) -> float | None:
    """연간 원단위 ``kWh/m²·년``. **연면적이 없으면 ``None``** 이고 화면에 줄이 없다.

    국내 평균과 견주지 않는다 — 용도·기후·가동시간이 다른 건물의 평균은 이 건물의
    판단 기준이 되지 못한다 (16세션 2절).
    """
    if building is None or not building.floor_area_m2:
        return None
    return total_kwh / building.floor_area_m2


def _hours(start: int, end: int) -> tuple[int, int]:
    """**시작이 끝보다 늦으면 기본값으로 되돌린다.** 뒤집힌 창은 뜻이 없다."""
    if start >= end:
        return DEFAULT_OPERATING_HOURS
    return (int(start), int(end))


def get_building() -> BuildingInfo | None:
    """세션에 담긴 건물 정보. 옆단을 그리기 전에도 안전하게 읽는다."""
    value = st.session_state.get(BUILDING_KEY)
    return value if isinstance(value, BuildingInfo) else None


def render_sidebar() -> BuildingInfo:
    """옆단을 그리고 건물 정보를 돌려준다.

    **탭 구조라 옆단은 매 실행에 그려진다.** 위젯이 사라지지 않으므로 값이
    세션에서 지워지는 일이 없다 (16세션 0-1 이 그 반대 경우였다).
    """
    saved = get_building()
    st.sidebar.caption("건물 정보")

    name = st.sidebar.text_input(
        "건물명 (선택)",
        value=saved.name if saved else "",
        key="building_name",
        placeholder=NAME_MISSING,
        help="산출물 표지와 본문에 들어갑니다. 넣지 않으면 「건물명 미입력」 으로 나갑니다.",
    )

    uses = building_uses()
    use_keys = [_UNSET, *(item.key for item in uses)]
    use_labels = {_UNSET: _UNSET_LABEL} | {item.key: item.label for item in uses}
    default_use = saved.use_key if saved and saved.use_key in use_keys else _UNSET
    use_key = st.sidebar.selectbox(
        "용도 (선택)",
        use_keys,
        index=use_keys.index(default_use),
        format_func=lambda key: use_labels[key],
        key="building_use",
        help=(
            "계약종별 드롭다운의 후보를 좁힙니다. 판정이 아니라 좁히기이며, "
            "고르지 않으면 전 종별을 보입니다. 최종 판단은 청구서입니다."
        ),
    )

    provinces = list_provinces()
    saved_region = saved.region_key if saved else ""
    province_default = saved_region.split("/", 1)[0] if saved_region else provinces[0]
    province = st.sidebar.selectbox(
        "시도",
        provinces,
        index=provinces.index(province_default) if province_default in provinces else 0,
        key="building_province",
    )
    regions = list_sigungu(province)
    region_keys = [item.key for item in regions]
    region_labels = {item.key: item.name for item in regions}
    region_default = saved_region if saved_region in region_keys else region_keys[0]
    region_key = st.sidebar.selectbox(
        "시군구",
        region_keys,
        index=region_keys.index(region_default),
        format_func=lambda key: region_labels[key],
        key="building_sigungu",
        help="태양광 기상 격자를 고릅니다. 격자가 25–31 km 라 같은 격자면 결과가 같습니다.",
    )

    # **9시 출근을 전제하지 않는다** (21세션 4절). 8시에 여는 곳에서는 무인시간
    # 부하가 한 시간만큼 부풀고, DR 저부하일 판정도 그만큼 어긋난다.
    start_hour, end_hour = st.sidebar.select_slider(
        "운영 시간대",
        options=list(range(0, 25)),
        value=(saved.operating_hours if saved else DEFAULT_OPERATING_HOURS),
        format_func=lambda hour: f"{hour}시",
        key="building_hours",
        help=(
            "평일 이 시간대 밖이 무인시간입니다. 경제성DR 의 저부하일 판정에도 씁니다.\n\n"
            "제도가 정한 DR 입찰 시간대(평일 09~12시·13~20시)와는 다른 값입니다."
        ),
    )

    area = st.sidebar.number_input(
        "연면적 (m², 선택)",
        min_value=0.0,
        value=float(saved.floor_area_m2) if saved and saved.floor_area_m2 else 0.0,
        step=100.0,
        key="building_area",
        help="넣으면 연간 원단위(kWh/m²·년)를 요금 구조에 한 줄 더합니다.",
    )
    year = st.sidebar.number_input(
        "준공연도 (선택)",
        min_value=0,
        max_value=2100,
        value=int(saved.built_year) if saved and saved.built_year else 0,
        step=1,
        format="%d",
        key="building_year",
        help="0 이면 넣지 않은 것으로 봅니다.",
    )

    info = BuildingInfo(
        region_key=region_key,
        name=name,
        use_key=use_key,
        floor_area_m2=area or None,
        built_year=int(year) or None,
        operating_hours=_hours(start_hour, end_hour),
    )
    st.session_state[BUILDING_KEY] = info
    return info
