"""요금 데이터 스키마와 로딩 (요구사항서 부록 A).

요금 단가는 **반드시 이 모듈을 통해 JSON 에서 읽는다.** 코드에 하드코딩하지 않는다.
드롭다운 선택지도 이 파일에서 생성한다 (부록 A.3) — 데이터에 없는 조합은
선택지에 나타나지 않아야 한다.

PoC 범위는 일반용전력(을) 고압A·B 다 (부록 A.4).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kwise.tariff.demand import (
    default_contract_floor_ratio,
    default_demand_bands,
    default_demand_months,
)

__all__ = [
    "BANDS",
    "BASE_FEE_BASES",
    "BASE_FEE_BILLING_DEMAND",
    "BASE_FEE_CONTRACT",
    "DEFAULT_REGION_GROUP",
    "ContractType",
    "DayRules",
    "EnergyRates",
    "OptionRates",
    "SpecialRule",
    "TariffDataError",
    "TariffSelection",
    "TariffTable",
    "VoltageRates",
    "available_tariff_files",
    "default_tariff_dir",
    "list_contract_types",
    "list_options",
    "list_selections",
    "list_voltages",
    "load_tariff",
    "switchable_selections",
]

BANDS: tuple[str, ...] = ("light", "mid", "peak")
DEFAULT_REGION_GROUP = "mainland"
_TARIFF_GLOB = "tariff_*.json"

# 기본요금 기준 — 갈림길은 갑/을이 아니라 **최대수요전력계가 섰는가**다
# (기본공급약관 제68조 ①·②). 그것을 정하는 것은 공급전압이라(제38조 ②·③)
# **종별이 아니라 전압으로 가른다** (89세션).
BASE_FEE_BILLING_DEMAND = "billing_demand"
BASE_FEE_CONTRACT = "contract"
BASE_FEE_BASES: tuple[str, ...] = (BASE_FEE_BILLING_DEMAND, BASE_FEE_CONTRACT)


class TariffDataError(ValueError):
    """요금 데이터 파일이 스키마를 따르지 않을 때 발생한다."""


@dataclass(frozen=True)
class EnergyRates:
    """한 계절의 시간대별 전력량요금 단가 (원/kWh)."""

    light: float
    mid: float
    peak: float

    def of(self, band: str) -> float:
        if band not in BANDS:
            raise TariffDataError(f"알 수 없는 시간대입니다: {band!r}")
        return float(getattr(self, band))


@dataclass(frozen=True)
class OptionRates:
    """선택요금 하나의 기본요금·전력량요금."""

    option: str
    base_won_per_kw: float
    energy: Mapping[str, EnergyRates]

    def rate(self, season: str, band: str) -> float:
        if season not in self.energy:
            raise TariffDataError(f"요금표에 없는 계절입니다: {season!r}")
        return self.energy[season].of(band)


@dataclass(frozen=True)
class VoltageRates:
    """전압구분 하나의 선택요금 모음.

    Attributes:
        base_fee_basis: 이 전압에서만 다른 기본요금 기준. ``None`` 이면 종별
            기본값(:attr:`ContractType.base_fee_basis`)을 따른다.
    """

    voltage: str
    label: str
    options: Mapping[str, OptionRates]
    base_fee_basis: str | None = None


@dataclass(frozen=True)
class ContractType:
    """계약종별.

    Attributes:
        demand_bands: 요금적용전력 대상 시간대. 일반용(을) 등은 중간·최대부하만이다.
        demand_months: 요금적용전력 대상월 (하계 7·8·9, 동계 12·1·2).
            **전력량요금의 계절과 다르다** (요구사항서 5.2 ②).
        contract_floor_ratio: 요금적용전력의 계약전력 대비 하한. 일반용(을)·산업용(을)
            30%, 교육용(을) 15% 특례. 확인되지 않은 종별은 ``null`` 로 두면
            하한을 적용하지 않고 절감액도 산출하지 않는다.
        base_fee_basis: 기본요금의 기준 **기본값**. ``"billing_demand"`` 는
            요금적용전력(제68조 ①), ``"contract"`` 는 계약전력(제68조 ②)이다.
            **갑/을 구분이 아니다** — 갈림길은 최대수요전력계 설치 여부이고
            그것을 정하는 것은 공급전압이다. 전압마다 다르면
            :attr:`VoltageRates.base_fee_basis` 가 이 값을 덮는다.
            **읽을 때는 반드시 :meth:`base_fee_on_contract_at` 을 쓴다** —
            전압을 빼고 읽으면 기본요금이 통째로 틀린다.
        time_of_use: 시간대별 요금제인지. 갑Ⅰ·교육용(갑)은 '전체시간' 단일 단가라
            세 시간대 단가가 모두 같다. 검증 규칙 1 의 ``경<중간<최대`` 를
            적용하지 않는다.
    """

    key: str
    label: str
    threshold_kw: float | None
    threshold_direction: str | None
    effective_date: str | None
    options: tuple[str, ...]
    voltages: Mapping[str, VoltageRates]
    # 기본값을 두지 않는다. 요금표가 값을 주지 않으면 파싱 시점에
    # ``rules_kr.json`` 에서 채운다 (요구사항서 12장).
    demand_bands: tuple[str, ...]
    demand_months: tuple[int, ...]
    contract_floor_ratio: float | None
    base_fee_basis: str = BASE_FEE_BILLING_DEMAND
    time_of_use: bool = True

    def base_fee_on_contract_at(self, voltage: str) -> bool:
        """이 전압에서 기본요금이 계약전력 기준인가 (제38조 ②·③ · 제68조 ①·②).

        **이름에 전압이 붙어 있는 것이 장치다** (89세션). 앞서는 종별 속성
        하나를 읽는 프로퍼티라 전압을 빼고 읽어도 조용히 지나갔고, 그 바람에
        교육용(갑) 고압 기본요금이 14.5배로 나왔다.
        """
        rates = self.voltages.get(voltage)
        basis = rates.base_fee_basis if rates is not None else None
        return (basis if basis is not None else self.base_fee_basis) == BASE_FEE_CONTRACT


@dataclass(frozen=True)
class DayRules:
    """요일 규칙 (요구사항서 5.3)."""

    saturday: str
    sunday: str
    holiday: str
    exclude_temporary_holiday: bool


@dataclass(frozen=True)
class SpecialRule:
    """특례 (요구사항서 5.6). 예: 산업용(을) 봄·가을철 주말 할인."""

    key: str
    applies_to: tuple[str, ...]
    seasons: tuple[str, ...]
    days: tuple[str, ...]
    hours: tuple[tuple[int, int], ...]
    discount_rate: float


@dataclass(frozen=True)
class TariffSelection:
    """요금 계산에 필요한 조합. 목록은 요금 데이터에서 생성한다."""

    contract_type: str
    voltage: str
    option: str

    def __str__(self) -> str:
        return f"{self.contract_type}/{self.voltage}/{self.option}"


@dataclass(frozen=True)
class TariffTable:
    """요금 데이터 한 벌."""

    schema_version: str
    region: str
    source: str
    effective_date: str
    verified: bool
    seasons: Mapping[str, tuple[int, ...]]
    month_seasons: Mapping[int, str]
    tou: Mapping[str, Mapping[str, Mapping[str, tuple[tuple[int, int], ...]]]]
    hour_bands: Mapping[str, Mapping[str, tuple[str | None, ...]]]
    day_rules: DayRules
    contract_types: Mapping[str, ContractType]
    special_rules: Mapping[str, SpecialRule]
    source_path: Path | None = None

    # ------------------------------------------------------------- 조회

    def season_of(self, month: int) -> str:
        """월 → 계절. 귀속 판정은 구간 시작 시각의 월로 한다."""
        try:
            return self.month_seasons[int(month)]
        except KeyError as exc:
            raise TariffDataError(f"계절 정의에 없는 월입니다: {month}") from exc

    def contract(self, key: str) -> ContractType:
        try:
            return self.contract_types[key]
        except KeyError as exc:
            raise TariffDataError(
                f"요금표에 없는 계약종별입니다: {key!r} "
                f"(가능: {', '.join(sorted(self.contract_types))})"
            ) from exc

    def rates(self, selection: TariffSelection) -> OptionRates:
        """조합 하나의 단가. 없는 조합이면 TariffDataError."""
        contract = self.contract(selection.contract_type)
        try:
            voltage = contract.voltages[selection.voltage]
        except KeyError as exc:
            raise TariffDataError(
                f"{contract.label} 에 없는 전압구분입니다: {selection.voltage!r} "
                f"(가능: {', '.join(contract.voltages)})"
            ) from exc
        try:
            return voltage.options[selection.option]
        except KeyError as exc:
            raise TariffDataError(
                f"{contract.label} {voltage.label} 에 없는 선택요금입니다: {selection.option!r} "
                f"(가능: {', '.join(voltage.options)})"
            ) from exc

    def band_of(self, season: str, hour: int, *, region_group: str = DEFAULT_REGION_GROUP) -> str:
        """계절·시각 → 시간대. ``hour`` 는 구간 **시작** 시각의 시(hour)다."""
        try:
            band = self.hour_bands[region_group][season][hour]
        except KeyError as exc:
            raise TariffDataError(f"시간대 정의가 없습니다: {region_group}/{season}") from exc
        if band is None:
            raise TariffDataError(f"{region_group}/{season} 의 {hour}시가 시간대 정의에 없습니다.")
        return band

    @property
    def label(self) -> str:
        return f"{self.source} ({self.effective_date} 시행)"


# --------------------------------------------------------------------- 파싱
# JSON 페이로드는 본질적으로 Any 다. 여기서 검사해 타입을 붙인다.


def _require(payload: Mapping[str, Any], key: str, context: str) -> Any:  # noqa: ANN401
    if key not in payload:
        raise TariffDataError(f"{context} 에 '{key}' 가 없습니다.")
    return payload[key]


def _parse_ranges(raw: Any, context: str) -> tuple[tuple[int, int], ...]:  # noqa: ANN401
    ranges: list[tuple[int, int]] = []
    for item in raw:
        if len(item) != 2:
            raise TariffDataError(f"{context} 의 시간 구간은 [시작, 끝] 이어야 합니다: {item!r}")
        ranges.append((int(item[0]), int(item[1])))
    return tuple(ranges)


def _hours_in(start: int, end: int) -> tuple[int, ...]:
    """``[start, end)`` 의 시(hour) 목록. 자정을 넘는 구간(22~8)을 허용한다."""
    if start == end:
        return ()
    if start < end:
        return tuple(range(start, end))
    return (*range(start, 24), *range(0, end))


def _build_hour_bands(
    tou: Mapping[str, Mapping[str, Mapping[str, tuple[tuple[int, int], ...]]]],
) -> dict[str, dict[str, tuple[str | None, ...]]]:
    """계절별 24시간 시간대 표를 만든다. 중복·공백은 부록 A.2 규칙 4 가 검사한다."""
    built: dict[str, dict[str, tuple[str | None, ...]]] = {}
    for region_group, seasons in tou.items():
        built[region_group] = {}
        for season, bands in seasons.items():
            hours: list[str | None] = [None] * 24
            for band, ranges in bands.items():
                for start, end in ranges:
                    for hour in _hours_in(start, end):
                        hours[hour] = band  # 중복은 마지막 정의가 이긴다. 검증에서 잡는다
            built[region_group][season] = tuple(hours)
    return built


def _parse_option(option: str, payload: Mapping[str, Any], context: str) -> OptionRates:
    energy_raw = _require(payload, "energy", context)
    energy = {
        season: EnergyRates(
            light=float(_require(rates, "light", f"{context}/{season}")),
            mid=float(_require(rates, "mid", f"{context}/{season}")),
            peak=float(_require(rates, "peak", f"{context}/{season}")),
        )
        for season, rates in energy_raw.items()
    }
    return OptionRates(
        option=option,
        base_won_per_kw=float(_require(payload, "base_won_per_kw", context)),
        energy=energy,
    )


def _parse_base_fee_basis(payload: Mapping[str, Any], context: str) -> str | None:
    """``base_fee_basis`` 를 검사해 돌려준다. 적혀 있지 않으면 ``None``."""
    raw = payload.get("base_fee_basis")
    if raw is None:
        return None
    basis = str(raw)
    if basis not in BASE_FEE_BASES:
        raise TariffDataError(
            f"{context}: 알 수 없는 기본요금 기준입니다: {basis!r} "
            f"(가능: {', '.join(BASE_FEE_BASES)})"
        )
    return basis


def _parse_contract(key: str, payload: Mapping[str, Any]) -> ContractType:
    context = f"contract_types/{key}"
    options = tuple(str(item) for item in _require(payload, "options", context))
    voltages: dict[str, VoltageRates] = {}
    for voltage, voltage_payload in _require(payload, "voltages", context).items():
        voltage_context = f"{context}/voltages/{voltage}"
        parsed = {
            option: _parse_option(option, voltage_payload[option], f"{voltage_context}/{option}")
            for option in options
            if option in voltage_payload
        }
        if not parsed:
            raise TariffDataError(f"{voltage_context} 에 선택요금 단가가 없습니다.")
        voltages[voltage] = VoltageRates(
            voltage=voltage,
            label=str(voltage_payload.get("label", voltage)),
            options=parsed,
            base_fee_basis=_parse_base_fee_basis(voltage_payload, voltage_context),
        )
    base_fee_basis = _parse_base_fee_basis(payload, context) or BASE_FEE_BILLING_DEMAND
    return ContractType(
        key=key,
        label=str(_require(payload, "label", context)),
        threshold_kw=(
            float(payload["threshold_kw"]) if payload.get("threshold_kw") is not None else None
        ),
        threshold_direction=payload.get("threshold_direction"),
        effective_date=payload.get("effective_date"),
        options=options,
        voltages=voltages,
        demand_bands=tuple(
            str(band) for band in payload.get("demand_bands", default_demand_bands())
        ),
        demand_months=tuple(
            int(month) for month in payload.get("demand_months", default_demand_months())
        ),
        contract_floor_ratio=(
            None
            if "contract_floor_ratio" in payload and payload["contract_floor_ratio"] is None
            else float(payload.get("contract_floor_ratio", default_contract_floor_ratio()))
        ),
        base_fee_basis=base_fee_basis,
        time_of_use=bool(payload.get("time_of_use", True)),
    )


def parse_tariff(payload: Mapping[str, Any], *, source_path: Path | None = None) -> TariffTable:
    """JSON 사전을 :class:`TariffTable` 로 바꾼다."""
    seasons_raw = _require(payload, "season_definition", "요금 데이터")
    seasons = {
        season: tuple(int(month) for month in months) for season, months in seasons_raw.items()
    }

    month_seasons: dict[int, str] = {}
    for season, months in seasons.items():
        for month in months:
            if month in month_seasons:
                raise TariffDataError(f"{month}월이 계절 정의에 두 번 나옵니다.")
            month_seasons[month] = season
    missing = sorted(set(range(1, 13)) - set(month_seasons))
    if missing:
        raise TariffDataError(f"계절 정의에 빠진 월이 있습니다: {missing}")

    tou_raw = _require(payload, "tou_definition", "요금 데이터")
    tou = {
        region_group: {
            season: {
                band: _parse_ranges(ranges, f"tou_definition/{region_group}/{season}/{band}")
                for band, ranges in bands.items()
            }
            for season, bands in seasons_payload.items()
        }
        for region_group, seasons_payload in tou_raw.items()
    }

    day_rules_raw = _require(payload, "day_rules", "요금 데이터")
    day_rules = DayRules(
        saturday=str(_require(day_rules_raw, "saturday", "day_rules")),
        sunday=str(_require(day_rules_raw, "sunday", "day_rules")),
        holiday=str(_require(day_rules_raw, "holiday", "day_rules")),
        exclude_temporary_holiday=bool(day_rules_raw.get("exclude_temporary_holiday", True)),
    )

    contract_types = {
        key: _parse_contract(key, value)
        for key, value in _require(payload, "contract_types", "요금 데이터").items()
    }

    special_rules = {
        key: SpecialRule(
            key=key,
            applies_to=tuple(str(item) for item in value.get("applies_to", ())),
            seasons=tuple(str(item) for item in value.get("seasons", ())),
            days=tuple(str(item) for item in value.get("days", ())),
            hours=_parse_ranges(value.get("hours", ()), f"special_rules/{key}"),
            discount_rate=float(value.get("discount_rate", 0.0)),
        )
        for key, value in payload.get("special_rules", {}).items()
    }

    return TariffTable(
        schema_version=str(payload.get("schema_version", "0")),
        region=str(payload.get("region", "kr")),
        source=str(payload.get("source", "")),
        effective_date=str(_require(payload, "effective_date", "요금 데이터")),
        verified=bool(payload.get("verified", False)),
        seasons=seasons,
        month_seasons=month_seasons,
        tou=tou,
        hour_bands=_build_hour_bands(tou),
        day_rules=day_rules,
        contract_types=contract_types,
        special_rules=special_rules,
        source_path=source_path,
    )


# --------------------------------------------------------------------- 파일


def default_tariff_dir() -> Path:
    """요금 데이터 폴더. 환경변수 ``KWISE_TARIFF_DIR`` 로 바꿀 수 있다."""
    override = os.environ.get("KWISE_TARIFF_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "data"


def available_tariff_files(directory: Path | None = None) -> tuple[Path, ...]:
    """``tariff_*.json`` 목록. 파일명 순으로 돌려주므로 마지막이 최신이다."""
    folder = directory if directory is not None else default_tariff_dir()
    if not folder.is_dir():
        return ()
    return tuple(sorted(folder.glob(_TARIFF_GLOB)))


def load_tariff(path: str | Path | None = None) -> TariffTable:
    """요금 데이터를 읽는다. 경로를 주지 않으면 가장 최근 시행일 파일을 쓴다."""
    if path is None:
        candidates = available_tariff_files()
        if not candidates:
            raise TariffDataError(f"요금 데이터 파일이 없습니다: {default_tariff_dir()}")
        target = candidates[-1]
    else:
        target = Path(path)
    if not target.is_file():
        raise TariffDataError(f"요금 데이터 파일이 없습니다: {target}")
    with target.open(encoding="utf-8") as stream:
        payload = json.load(stream)
    return parse_tariff(payload, source_path=target)


# --------------------------------------------------------------------- 선택지 생성 (부록 A.3)


def list_contract_types(table: TariffTable) -> tuple[tuple[str, str], ...]:
    """``(키, 표시명)`` 목록. 드롭다운을 이걸로 만든다."""
    return tuple((key, contract.label) for key, contract in sorted(table.contract_types.items()))


def list_voltages(table: TariffTable, contract_type: str) -> tuple[tuple[str, str], ...]:
    contract = table.contract(contract_type)
    return tuple((key, voltage.label) for key, voltage in contract.voltages.items())


def list_options(table: TariffTable, contract_type: str, voltage: str) -> tuple[str, ...]:
    contract = table.contract(contract_type)
    if voltage not in contract.voltages:
        raise TariffDataError(f"{contract.label} 에 없는 전압구분입니다: {voltage!r}")
    # 요금표에 적힌 선택요금 순서를 지킨다 (I, II, III).
    available = contract.voltages[voltage].options
    return tuple(option for option in contract.options if option in available)


def list_selections(
    table: TariffTable,
    *,
    contract_types: Iterable[str] | None = None,
    voltages: Iterable[str] | None = None,
) -> tuple[TariffSelection, ...]:
    """가능한 모든 조합. 5세션의 선택요금 비교가 이 목록을 돈다.

    Args:
        contract_types: 계약종별을 가둔다. 종별은 용도로 정해지는 계약이지
            고를 수 있는 요금제가 아니다.
        voltages: 전압구분을 가둔다. **수전설비로 정해지므로 전환 대상이 아니다.**

    드롭다운(부록 A.3)을 그릴 때는 인자를 주지 않고 전부 받아 쓴다. 반면
    **선택요금 전환 비교는 반드시 현행 종별·전압으로 가둔다** —
    :func:`switchable_selections` 를 쓰면 된다.
    """
    keys: Sequence[str] = (
        tuple(contract_types) if contract_types is not None else tuple(sorted(table.contract_types))
    )
    allowed = set(voltages) if voltages is not None else None
    return tuple(
        TariffSelection(contract_type=key, voltage=voltage, option=option)
        for key in keys
        for voltage, _ in list_voltages(table, key)
        if allowed is None or voltage in allowed
        for option in list_options(table, key, voltage)
    )


def switchable_selections(
    table: TariffTable, current: TariffSelection
) -> tuple[TariffSelection, ...]:
    """현행 조합에서 **실제로 갈아탈 수 있는** 조합 목록.

    바꿀 수 있는 것은 선택요금(Ⅰ·Ⅱ·Ⅲ)뿐이다. 나머지 둘은 고객이 고르는 값이
    아니다.

        계약종별   용도로 정해진다. 일반용 건물에 산업용을 권할 수 없다.
        전압구분   **수전설비로 정해진다.** 고압A 는 3,300~66,000 V,
                   고압B 는 154,000 V, 고압C 는 345,000 V 수전이다.
                   154 kV 수전 건물이 22.9 kV 로 바꾸려면 변전설비를 새로
                   지어야 하므로 요금제 비교에 끼울 대상이 아니다.

    가둬 두지 않으면 "고압B 로 바꾸면 연 ○○원 절감" 같은 실행 불가능한 권고가
    나온다. 단가만 보면 그럴듯해서 더 위험하다.
    """
    return list_selections(
        table, contract_types=[current.contract_type], voltages=[current.voltage]
    )
