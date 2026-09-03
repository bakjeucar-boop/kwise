"""요금표 엑셀 → JSON 변환 (요구사항서 부록 A.1·A.4).

``data\\source\\2026-06-01_KEPCO_Electricity_Tariff.xlsx`` 를 읽어
``data\\tariff_*.json`` 을 만든다. **단가를 수기로 옮기지 않는다.**

시트 하나를 그대로 믿으면 안 되는 이유가 둘 있다.

**① 시트마다 계절 열 순서가 다르다.**

    일반용·교육용   여름철 / 봄·가을철 / 겨울철
    산업용          **봄·가을철 / 여름철** / 겨울철

위치로 읽으면 산업용의 여름과 봄·가을이 통째로 뒤바뀐다. 산업용(을) 고압A
선택Ⅰ 최대부하가 여름 234.5 / 봄가을 156.4 인데, 뒤바뀌면 봄·가을 단가가
여름보다 비싸져 태양광 평가가 정반대로 나온다. 그래서 **3행 헤더를 읽어
이름으로 매핑한다** (:func:`season_columns`).

**② 엑셀에 없는 규칙이 많다.**

엑셀에는 단가 테이블과 계절·시간대 구분만 있다. 아래는 없으므로 코드가
:data:`CONTRACT_RULES` 로 들고 있고, 변환이 이를 덮어쓰지 않는다.

    토요일·일요일·공휴일 계량 규칙          :data:`DAY_RULES`
    산업용(을) 봄·가을 주말 할인 특례        :data:`SPECIAL_RULES`
    요금적용전력 3규칙 (경부하 제외·대상월·하한)
    계약전력 갑·을 임계값과 기본요금 기준
    종별 ``contract_floor_ratio``

변환한 뒤에는 반드시 부록 A.2 검증(:mod:`kwise.tariff.validate`)을 통과시킨다.
사람이 옮긴 표에는 오차가 섞인다 — 실제로 이 엑셀의 농사용(을) 고압 여름철
단가(66.6원)가 요금표 원본(68.6원)과 다르다. 범위 밖 종별이라 넣지 않지만
검증을 건너뛰면 안 된다는 증거다.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

__all__ = [
    "BAND_ALIASES",
    "CONTRACT_RULES",
    "DAY_RULES",
    "FLAT_BAND",
    "HEADER_ROW",
    "INFO_SHEET",
    "OPTION_ALIASES",
    "RATE_SHEETS",
    "SEASON_ALIASES",
    "SPECIAL_RULES",
    "VOLTAGE_ALIASES",
    "BorrowedOption",
    "ContractRule",
    "RateRow",
    "TariffSourceError",
    "Transition",
    "build_payload",
    "read_rate_rows",
    "read_time_bands",
    "season_columns",
]

HEADER_ROW = 2  # 3행이 헤더다. 1행은 제목, 2행은 빈 줄.
INFO_SHEET = "부가정보 및 시간대구분"
RATE_SHEETS: tuple[str, ...] = ("일반용 전력", "산업용 전력", "교육용·농사용·기타")

FLAT_BAND = "전체시간"

SEASON_ALIASES: Mapping[str, str] = {
    "여름철": "summer",
    "하계": "summer",
    "봄·가을철": "spring_fall",
    "봄가을철": "spring_fall",
    "기타계절": "spring_fall",
    "겨울철": "winter",
    "동계": "winter",
}
BAND_ALIASES: Mapping[str, str] = {
    "경부하": "light",
    "중간부하": "mid",
    "최대부하": "peak",
}
VOLTAGE_ALIASES: Mapping[str, str] = {
    "저압": "low",
    "고압A": "high_a",
    "고압B": "high_b",
    "고압C": "high_c",
}
OPTION_ALIASES: Mapping[str, str] = {
    "단일": "single",
    "선택I": "I",
    "선택II": "II",
    "선택III": "III",
    "선택IV": "IV",
}

# 엑셀에 없는 규칙. 기존 JSON·코드의 값을 그대로 옮겨 적은 것이다.
DAY_RULES: Mapping[str, Any] = {
    "saturday": "peak_to_mid",
    "sunday": "all_to_light",
    "holiday": "all_to_light",
    "exclude_temporary_holiday": True,
}
SPECIAL_RULES: Mapping[str, Any] = {
    "industrial_b_weekend_discount": {
        "applies_to": ["industrial_b"],
        "seasons": ["spring_fall"],
        "days": ["saturday", "sunday", "holiday"],
        "hours": [[11, 14]],
        "discount_rate": 0.5,
    }
}


class TariffSourceError(ValueError):
    """엑셀 요금표를 읽지 못했을 때 발생한다."""


@dataclass(frozen=True)
class BorrowedOption:
    """엑셀에 없는 선택요금. **단가를 코드에 적지 않고 같은 엑셀의 다른 행을 쓴다.**

    일반용(갑)Ⅱ 선택Ⅲ·Ⅳ 가 그렇다. 약관 부칙 (2026. 5. 22) 로 신설됐는데
    우리가 읽는 6-01 판 엑셀에는 행이 없다. 8월 요금표 원문(정본,
    ``data\\source\\2026-08-01_전기요금표(종합).pdf`` 1쪽)에서 값을 확인해 보니
    **일반용(갑)Ⅰ 고압 선택Ⅰ·Ⅱ 와 열여섯 자리가 모두 같다** — 기본요금
    7,170 / 8,230 원, 전체시간 단가가 고압A 142.6·98.6·130.3 과
    138.6·94.3·125.0, 고압B 140.5·97.5·127.3 과 135.2·92.2·122.0 이다.
    제도로도 같은 자리다. 갑Ⅱ 는 시간대별 계량 고객이고 선택Ⅲ·Ⅳ 는 그
    고객에게 **전체시간 단가**를 주는 요금제라 갑Ⅰ 고압과 겹친다.

    그래서 값을 옮겨 적지 않고 **그 행을 그대로 쓴다.** 옮겨 적으면 요금표가
    바뀔 때 한쪽만 고쳐진다. 두 자리가 갈라지면
    ``tests\\test_tariff_source.py`` 의 못이 그것을 알린다.

    Attributes:
        effective_date: **이 선택요금**의 시행일. 약관이 「2026년 12월분
            요금부터」 (부칙 제2항 제3호)로 정하므로 **날이 아니라 요금월**
            (``2026-12``)로 적는다. 날로 적으면 없는 사실을 적는 것이다 —
            검침 기간이 달을 걸치므로 12월분이 시작하는 날은 고객마다 다르다.
        from_contract: 값을 가져올 종별의 **엑셀 표기**.
        from_option: 그 종별 안의 선택요금 키.
    """

    option: str
    from_contract: str
    from_option: str
    effective_date: str


@dataclass(frozen=True)
class Transition:
    """부칙의 경과조치. 엑셀에도 요금표 각주에도 없다 — **약관을 봐야 나온다.**

    일반용전력(갑)Ⅱ 의 부칙 (2026. 5. 22) 제2항 제1호가 그것이고, 요금표 각주는
    제3호(「선택요금Ⅲ, Ⅳ는 ’26.12월부터 적용 가능」)만 적는다.

    Attributes:
        first_billing_month: 첫 요금월 (``2026-06``).
        last_billing_month: 마지막 요금월 (``2026-11``).
        counterpart: 견줄 짝. **한쪽 방향만 적는다.**
    """

    first_billing_month: str
    last_billing_month: str
    counterpart: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ContractRule:
    """엑셀에 없는 종별 속성. **변환이 이 값을 만들어내지 않는다.**

    Attributes:
        base_fee_basis: 기본요금 기준의 **기본값**. ``"billing_demand"`` 는
            요금적용전력, ``"contract"`` 는 계약전력이다. **갑/을 구분이
            아니다** — 갈림길은 최대수요전력계 설치 여부이고, 그것을 정하는
            것은 공급전압이다 (제38조 제2항 · 제68조 제1항 · 제2항). 61세션에
            약관 원문으로 확인했다: 고압 이상 고객에게는 최대수요전력계를
            설치하므로, **저압이 없는 종별(을·갑Ⅱ)은 언제나 요금적용전력
            기준**이다.
        voltage_base_fee_basis: 전압별 예외. 여기 적힌 전압만 위 기본값을
            덮는다 (89세션).
        time_of_use: 시간대별 요금제인지. 갑Ⅰ·교육용(갑)은 '전체시간' 단일 단가다.
        contract_floor_ratio: 요금적용전력 하한 비율. 제68조 제1항의 30% 는
            최대수요전력계 고객 전체에 걸리므로 **갑Ⅱ에도 교육용에도 있다**
            (90세션). 종별로 갈리지 않으므로 값은 30% 하나뿐이고, 전압이
            전부 계약전력 기준인 종별(갑Ⅰ)만 하한 개념이 없어 None 이다.
            **전압마다 기준이 갈리는 종별(교육용(갑))은 값을 들고 있고**
            계약전력 기준 전압에서 그것을 쓰지 않는 것은 요금 엔진이 한다.
    """

    key: str
    label: str
    threshold_kw: float | None
    threshold_direction: str | None
    base_fee_basis: str
    time_of_use: bool
    contract_floor_ratio: float | None
    below_threshold_key: str | None = None
    """계약전력이 문턱 **아래로** 내려가면 적용되는 종별 (98세션).

    조문이 「…고객에게 적용합니다」 로 적으므로 **신청이 아니라 계약전력이
    정한다** — 제57조 ② (일반용 300 kW) · 제58조 ② (교육용 1,000 kW) ·
    제59조 ② (산업용 300 kW). 고압 고객이 갑으로 내려가면 제57조 ④ ·
    제59조 ⑤ 가 「갑Ⅱ를 적용한다」 고 못을 박으므로 갑Ⅰ 이 아니라 갑Ⅱ 다.

    **``"above"`` 종별에만 값이 있다.** 계약전력 조정은 낮추는 권고만 하므로
    문턱 **위로** 넘는 갈래는 실물에 설 자리가 없다 — 없는 것을 짓지 않는다.
    """
    demand_bands: tuple[str, ...] = ("mid", "peak")
    demand_months: tuple[int, ...] = (7, 8, 9, 12, 1, 2)
    voltage_base_fee_basis: tuple[tuple[str, str], ...] = ()
    borrowed_options: tuple[BorrowedOption, ...] = ()
    transition: Transition | None = None


# 엑셀의 종별 표기 → 종별 규칙. 부록 A.4 의 확장 순서대로 적었다.
# 농사용·가로등·심야·전기차·보완전력은 범위 밖이라 넣지 않는다.
CONTRACT_RULES: Mapping[str, ContractRule] = {
    "일반용(을)": ContractRule(
        key="general_b",
        label="일반용전력(을)",
        threshold_kw=300,
        threshold_direction="above",
        base_fee_basis="billing_demand",
        time_of_use=True,
        contract_floor_ratio=0.3,
        # 제57조 ② 1. 이 「4kW 이상 300kW 미만」 을 갑으로 정하고, 제57조 ④ 가
        # 「일반용전력(갑) 고압 고객은 갑Ⅱ를 적용한다」 고 못을 박는다.
        below_threshold_key="general_a_2",
    ),
    # 갑Ⅰ 은 **저압과 고압을 함께 가지는데 둘 다 계약전력이 맞다** (89세션).
    # 제57조 ④·제59조 ⑤가 「갑 고압 고객은 갑Ⅱ를 적용한다」 고 못을 박아
    # 요금표의 갑Ⅰ 고압A·B 행이 쓰이는 자리는 **저압계량 예외 경로**뿐이고,
    # 저압계량이면 최대수요전력계가 없어 제68조 ②가 맞다. 그래서 전압별
    # 예외를 두지 않는다 — 교육용(갑)과 묶어 고치지 마라.
    "일반용(갑) I": ContractRule(
        key="general_a_1",
        label="일반용전력(갑)Ⅰ",
        threshold_kw=300,
        threshold_direction="below",
        base_fee_basis="contract",
        time_of_use=False,
        contract_floor_ratio=None,
    ),
    # 갑Ⅱ 는 **저압이 없다** (고압A·고압B 뿐). 제38조 제2항에 따라 고압 이상
    # 고객에게는 최대수요전력계를 설치하므로 제68조 제1항 — 요금적용전력이다.
    # 용인 소규모 건물 청구서로 확인했다 (61세션): 8,230원/kW × 118 kW.
    "일반용(갑) II": ContractRule(
        key="general_a_2",
        label="일반용전력(갑)Ⅱ",
        threshold_kw=300,
        threshold_direction="below",
        base_fee_basis="billing_demand",
        time_of_use=True,
        contract_floor_ratio=0.3,
        # 선택Ⅲ·Ⅳ 는 6-01 판 엑셀에 없다. :class:`BorrowedOption` 을 본다.
        # **시행일로 후보를 막지 않는다** — 고객은 고를 수 있고, 계산은 고른
        # 하나로 기간 전체를 간다. 그 오차는 안내로 낸다.
        borrowed_options=(
            BorrowedOption("III", "일반용(갑) I", "I", "2026-12"),
            BorrowedOption("IV", "일반용(갑) I", "II", "2026-12"),
        ),
        # 부칙 (2026. 5. 22) 제2항 제1호 — 6월분~11월분은 신청 없이 낮은 쪽이다.
        transition=Transition("2026-06", "2026-11", (("I", "III"), ("II", "IV"))),
    ),
    "산업용(갑) I": ContractRule(
        key="industrial_a_1",
        label="산업용전력(갑)Ⅰ",
        threshold_kw=300,
        threshold_direction="below",
        base_fee_basis="contract",
        time_of_use=False,
        contract_floor_ratio=None,
    ),
    # 산업용(갑)Ⅱ 도 저압이 없다. 위 일반용(갑)Ⅱ 와 같은 조문이다.
    "산업용(갑) II": ContractRule(
        key="industrial_a_2",
        label="산업용전력(갑)Ⅱ",
        threshold_kw=300,
        threshold_direction="below",
        base_fee_basis="billing_demand",
        time_of_use=True,
        contract_floor_ratio=0.3,
    ),
    "산업용(을)": ContractRule(
        key="industrial_b",
        label="산업용전력(을)",
        threshold_kw=300,
        threshold_direction="above",
        base_fee_basis="billing_demand",
        time_of_use=True,
        contract_floor_ratio=0.3,
        # 제59조 ② · ⑤ 가 일반용의 제57조 ② · ④ 와 같은 모양이다.
        # **고압C 는 갑Ⅱ 에 없다** — 그 전압은 못 넘고, 그 사실은 안내로 나간다.
        below_threshold_key="industrial_a_2",
    ),
    # 교육용(갑)은 **고압이 기본값이고 저압이 예외**다 (89세션에 갈랐다).
    # 제38조 ②로 고압A·B 고객에게는 최대수요전력계를 설치하므로 제68조 ①이고,
    # 저압은 제38조 ③이 「설치할 수 있다」(재량)라 제68조 ②가 기본값이다.
    # **일반용·산업용 갑Ⅰ 과 다르다** — 제57조 ④·제59조 ⑤ 같은 단서가
    # 교육용에는 없다 (교육용에는 갑Ⅱ 자체가 없다).
    "교육용(갑)": ContractRule(
        key="education_a",
        label="교육용전력(갑)",
        threshold_kw=1_000,  # 교육용은 임계값이 1,000 kW 다. 300 이 아니다
        threshold_direction="below",
        base_fee_basis="billing_demand",
        time_of_use=False,
        # 제68조 ①의 30% 다 (90세션에 세칙 원문으로 갈랐다). 그 항의 하한은
        # **최대수요전력계 고객 전체**에 걸리고 종별로 갈리지 않는다 — 고압에서
        # 제68조 ①을 타는 이상 하한도 함께 온다. 저압은 제68조 ②라 하한이
        # 없는데, 그 가르기는 :func:`kwise.tariff.engine.compute_bill` 이 기본요금
        # 기준으로 한다 (계약전력 기준이면 하한을 적용하지 않는다).
        contract_floor_ratio=0.3,
        voltage_base_fee_basis=(("low", "contract"),),
    ),
    "교육용(을)": ContractRule(
        key="education_b",
        label="교육용전력(을)",
        threshold_kw=1_000,
        threshold_direction="above",
        base_fee_basis="billing_demand",
        time_of_use=True,
        # **여기도 30% 다** (90세션). 6세션부터 0.15 였는데 그 15% 는 종별
        # 속성이 아니다 — 시행세칙 별표4 8.「초·중·고교 및 유치원 전기요금
        # 적용」 은 약관 제58조 적용대상 **중** 초·중등교육법 제2조와
        # 유아교육법 제2조 제2호 시설에만 붙고 「고객의 신청일이 속하는
        # 월분부터」 적용한다. 갑/을을 가르지도 않는다.
        contract_floor_ratio=0.3,
        # 제58조 ② 1. — 교육용은 1,000 kW 가 문턱이고 **갑Ⅱ 가 없다.**
        # 그래서 넘어가는 곳이 교육용전력(갑) 하나다.
        below_threshold_key="education_a",
    ),
}


@dataclass(frozen=True)
class RateRow:
    """엑셀 한 줄. 계절 단가는 **이름으로** 매핑해 담는다."""

    contract: str
    voltage: str
    option: str
    base_won_per_kw: float
    band: str
    rates: Mapping[str, float] = field(default_factory=dict)


# --------------------------------------------------------------------- 헤더


def _normalize(value: object) -> str:
    """전각·공백·기호를 접어 비교 가능한 꼴로 만든다."""
    text = unicodedata.normalize("NFKC", str(value)).strip()
    return re.sub(r"[\s]", "", text)


def season_columns(header: Sequence[object]) -> dict[int, str]:
    """헤더 행에서 ``열 번호 → 계절 키`` 를 만든다.

    **여기가 이 모듈의 핵심이다.** 위치로 읽으면 산업용 시트에서 여름과
    봄·가을이 뒤바뀐다. 헤더에 적힌 이름만 믿는다.
    """
    mapping: dict[int, str] = {}
    for position, raw in enumerate(header):
        if raw is None or (isinstance(raw, float) and pd.isna(raw)):
            continue
        text = _normalize(raw)
        for alias, season in SEASON_ALIASES.items():
            if _normalize(alias) in text:
                if position in mapping:
                    continue
                mapping[position] = season
                break
    if not mapping:
        raise TariffSourceError(f"계절 열을 찾지 못했습니다: {list(header)}")
    duplicated = [key for key in set(mapping.values()) if list(mapping.values()).count(key) > 1]
    if duplicated:
        raise TariffSourceError(f"계절 열이 중복됩니다: {duplicated} — 헤더 {list(header)}")
    return mapping


def _column_of(header: Sequence[object], *keywords: str) -> int:
    for position, raw in enumerate(header):
        text = _normalize(raw)
        if all(_normalize(word) in text for word in keywords):
            return position
    raise TariffSourceError(f"'{'+'.join(keywords)}' 열을 찾지 못했습니다: {list(header)}")


# --------------------------------------------------------------------- 단가 시트


def read_rate_rows(path: Path, sheet: str) -> tuple[RateRow, ...]:
    """단가 시트 한 장을 :class:`RateRow` 목록으로 읽는다."""
    raw = pd.read_excel(path, sheet_name=sheet, header=None)
    if len(raw) <= HEADER_ROW:
        raise TariffSourceError(f"'{sheet}' 시트가 비어 있습니다.")
    header = list(raw.iloc[HEADER_ROW])
    seasons = season_columns(header)
    contract_col = 0
    voltage_col = _column_of(header, "전압") if _has(header, "전압") else 1
    option_col = _column_of(header, "선택요금")
    base_col = _column_of(header, "기본요금")
    band_col = _column_of(header, "시간대")

    rows: list[RateRow] = []
    for position in range(HEADER_ROW + 1, len(raw)):
        line = raw.iloc[position]
        contract = _normalize(line.iloc[contract_col])
        if not contract or contract == "nan":
            continue
        base_raw = line.iloc[base_col]
        if pd.isna(base_raw):
            continue
        rates: dict[str, float] = {}
        for column, season in seasons.items():
            value = line.iloc[column]
            if pd.isna(value):
                continue
            rates[season] = float(value)
        if not rates:
            continue
        rows.append(
            RateRow(
                contract=contract,
                voltage=_normalize(line.iloc[voltage_col]),
                option=_normalize(line.iloc[option_col]),
                base_won_per_kw=float(base_raw),
                band=_normalize(line.iloc[band_col]),
                rates=rates,
            )
        )
    return tuple(rows)


def _has(header: Sequence[object], keyword: str) -> bool:
    return any(_normalize(keyword) in _normalize(value) for value in header)


# --------------------------------------------------------------------- 시간대 시트


def read_time_bands(
    path: Path,
) -> tuple[dict[str, list[int]], dict[str, dict[str, list[list[int]]]]]:
    """부가정보 시트에서 계절 정의와 시간대 구분을 읽는다."""
    raw = pd.read_excel(path, sheet_name=INFO_SHEET, header=None)
    header_row = None
    for position in range(len(raw)):
        if _normalize(raw.iat[position, 0]) == "계절구분":
            header_row = position
            break
    if header_row is None:
        raise TariffSourceError(f"'{INFO_SHEET}' 에서 계절 구분 표를 찾지 못했습니다.")

    header = list(raw.iloc[header_row])
    band_columns: dict[int, str] = {}
    for position, value in enumerate(header):
        text = _normalize(value)
        for alias, band in BAND_ALIASES.items():
            if text.startswith(_normalize(alias)):
                band_columns[position] = band
                break
    if set(band_columns.values()) != set(BAND_ALIASES.values()):
        raise TariffSourceError(f"시간대 열이 모자랍니다: {list(header)}")

    seasons: dict[str, list[int]] = {}
    tou: dict[str, dict[str, list[list[int]]]] = {}
    for position in range(header_row + 1, len(raw)):
        label = _normalize(raw.iat[position, 0])
        season = next(
            (key for alias, key in SEASON_ALIASES.items() if _normalize(alias) == label), None
        )
        if season is None:
            break
        seasons[season] = _parse_months(str(raw.iat[position, 1]))
        tou[season] = {
            band: _parse_hour_ranges(str(raw.iat[position, column]))
            for column, band in sorted(band_columns.items())
        }
    if not seasons:
        raise TariffSourceError(f"'{INFO_SHEET}' 에서 계절 행을 읽지 못했습니다.")
    return seasons, tou


def _parse_months(text: str) -> list[int]:
    """``'3월 1일 ~ 5월 31일, 9월 1일 ~ 10월 31일'`` → ``[3, 4, 5, 9, 10]``."""
    months: list[int] = []
    for part in text.split(","):
        found = re.findall(r"(\d{1,2})\s*월", part)
        if len(found) < 2:
            continue
        start, end = int(found[0]), int(found[-1])
        current = start
        while True:
            months.append(current)
            if current == end:
                break
            current = current % 12 + 1  # 11월 → 2월처럼 해를 넘는 구간
    if not months:
        raise TariffSourceError(f"적용월을 읽지 못했습니다: {text!r}")
    return months


def _parse_hour_ranges(text: str) -> list[list[int]]:
    """``'08:00~15:00, 21:00~22:00'`` → ``[[8, 15], [21, 22]]``."""
    ranges: list[list[int]] = []
    for start, end in re.findall(r"(\d{1,2}):\d{2}\s*~\s*(\d{1,2}):\d{2}", text):
        ranges.append([int(start), int(end)])
    if not ranges:
        raise TariffSourceError(f"시간대 구간을 읽지 못했습니다: {text!r}")
    return ranges


# --------------------------------------------------------------------- 조립


def _energy_block(rows: Sequence[RateRow], rule: ContractRule) -> dict[str, dict[str, float]]:
    """한 (종별·전압·선택요금) 의 계절×시간대 단가.

    '전체시간' 은 시간대 구분이 없다는 뜻이므로 세 시간대에 같은 단가를 채운다.
    검증 규칙 1 의 ``경<중간<최대`` 는 이 종별에 적용하지 않는다
    (:data:`ContractRule.time_of_use` 가 False 다).
    """
    energy: dict[str, dict[str, float]] = {}
    for row in rows:
        if row.band == FLAT_BAND:
            if rule.time_of_use:
                raise TariffSourceError(f"{rule.key}: 시간대 요금제인데 '전체시간' 행이 있습니다.")
            for season, value in row.rates.items():
                # 전체시간은 시간대 구분이 없다는 뜻이므로 세 칸을 같은 값으로 채운다.
                energy[season] = dict.fromkeys(BAND_ALIASES.values(), value)
            continue
        band = BAND_ALIASES.get(row.band)
        if band is None:
            raise TariffSourceError(f"{rule.key}: 알 수 없는 시간대입니다: {row.band!r}")
        if not rule.time_of_use:
            raise TariffSourceError(f"{rule.key}: 전체시간 종별인데 '{row.band}' 행이 있습니다.")
        for season, value in row.rates.items():
            energy.setdefault(season, {})[band] = value
    for season, bands in energy.items():
        missing = set(BAND_ALIASES.values()) - set(bands)
        if missing:
            raise TariffSourceError(
                f"{rule.key}/{season}: 시간대 단가가 빠졌습니다: {sorted(missing)}"
            )
    return energy


def _contract_payload(
    rule: ContractRule, rows: Sequence[RateRow], effective_date: str
) -> dict[str, Any]:
    voltages: dict[str, Any] = {}
    option_order: list[str] = []
    for row in rows:
        voltage = VOLTAGE_ALIASES.get(row.voltage)
        option = OPTION_ALIASES.get(row.option)
        if voltage is None:
            raise TariffSourceError(f"{rule.key}: 알 수 없는 전압구분입니다: {row.voltage!r}")
        if option is None:
            raise TariffSourceError(f"{rule.key}: 알 수 없는 선택요금입니다: {row.option!r}")
        if option not in option_order:
            option_order.append(option)
        voltages.setdefault(voltage, {"label": row.voltage, "_rows": {}})
        voltages[voltage]["_rows"].setdefault(option, []).append(row)

    overrides = dict(rule.voltage_base_fee_basis)
    # **조용히 지나가지 않게 한다** — 전압 이름을 잘못 적으면 기본요금 기준이
    # 통째로 틀리는데 값은 그럴듯하게 나온다 (89세션이 고친 병이 그 모양이다).
    if unknown := sorted(set(overrides) - set(voltages)):
        raise TariffSourceError(f"{rule.key}: 요금표에 없는 전압의 기본요금 기준입니다: {unknown}")
    for voltage, payload in voltages.items():
        if voltage in overrides:
            payload["base_fee_basis"] = overrides[voltage]
        grouped: dict[str, list[RateRow]] = payload.pop("_rows")
        for option, option_rows in grouped.items():
            bases = {row.base_won_per_kw for row in option_rows}
            if len(bases) != 1:
                raise TariffSourceError(
                    f"{rule.key}/{voltage}/{option}: 기본요금이 하나가 아닙니다: {sorted(bases)}"
                )
            payload[option] = {
                "base_won_per_kw": bases.pop(),
                # 선택요금별 시행일 (스키마 0.3). 엑셀에 없는 값이라 규칙이 든다.
                "effective_date": effective_date,
                "time_of_use": rule.time_of_use,
                "energy": _energy_block(option_rows, rule),
            }

    payload = {
        "label": rule.label,
        "threshold_kw": rule.threshold_kw,
        "threshold_direction": rule.threshold_direction,
        "effective_date": effective_date,
        "base_fee_basis": rule.base_fee_basis,
        "time_of_use": rule.time_of_use,
        "options": option_order,
        "demand_bands": list(rule.demand_bands),
        "demand_months": list(rule.demand_months),
        "contract_floor_ratio": rule.contract_floor_ratio,
        "voltages": voltages,
    }
    # 문턱 아래로 내려가는 종별도 **없으면 칸을 두지 않는다** (98세션).
    # 갑 종별에는 값이 없다 — 위로 넘는 권고를 하지 않기 때문이다.
    if rule.below_threshold_key is not None:
        payload["below_threshold_key"] = rule.below_threshold_key
    # 경과조치가 없는 종별이 대부분이다. **없으면 칸도 두지 않는다** — 빈 칸은
    # 「안 걸린다」 와 「아직 안 읽었다」 를 구별해 주지 않는다.
    if rule.transition is not None:
        payload["transition"] = {
            "first_billing_month": rule.transition.first_billing_month,
            "last_billing_month": rule.transition.last_billing_month,
            "counterpart": dict(rule.transition.counterpart),
        }
    return payload


def _add_borrowed_options(contract_types: dict[str, Any]) -> None:
    """엑셀에 없는 선택요금을 **같은 엑셀의 다른 종별 행에서** 채운다.

    없는 것을 지어내지 않는다 — 가져올 종별이 이번 변환에 없으면 멈춘다.
    """
    for rule in CONTRACT_RULES.values():
        target = contract_types.get(rule.key)
        if target is None:
            continue
        for borrowed in rule.borrowed_options:
            source_key = CONTRACT_RULES[borrowed.from_contract].key
            source = contract_types.get(source_key)
            if source is None:
                raise TariffSourceError(
                    f"{rule.key}/{borrowed.option}: 값을 가져올 종별이 "
                    f"이번 변환에 없습니다: {borrowed.from_contract!r}"
                )
            for voltage, payload in target["voltages"].items():
                rates = source["voltages"].get(voltage, {}).get(borrowed.from_option)
                if rates is None:
                    raise TariffSourceError(
                        f"{rule.key}/{voltage}/{borrowed.option}: "
                        f"{source_key}/{voltage}/{borrowed.from_option} 이 없습니다."
                    )
                payload[borrowed.option] = {
                    "base_won_per_kw": rates["base_won_per_kw"],
                    "effective_date": borrowed.effective_date,
                    "time_of_use": rates["time_of_use"],
                    "energy": deepcopy(rates["energy"]),
                }
            target["options"].append(borrowed.option)


def build_payload(
    path: Path,
    *,
    effective_date: str,
    source: str = "한국전력공사 전기요금표(종합)",
    schema_version: str = "0.5",
    contracts: Sequence[str] | None = None,
) -> dict[str, Any]:
    """엑셀 한 권을 요금 데이터 JSON 페이로드로 바꾼다.

    Args:
        contracts: 넣을 종별의 엑셀 표기. None 이면 :data:`CONTRACT_RULES` 전부.
            종별을 하나씩 넣어 가며 검증을 통과시킬 때 쓴다 (부록 A.4).
    """
    wanted = tuple(contracts) if contracts is not None else tuple(CONTRACT_RULES)
    unknown = [name for name in wanted if name not in CONTRACT_RULES]
    if unknown:
        raise TariffSourceError(f"규칙이 정의되지 않은 종별입니다: {unknown}")

    collected: dict[str, list[RateRow]] = {name: [] for name in wanted}
    for sheet in RATE_SHEETS:
        for row in read_rate_rows(path, sheet):
            for name in wanted:
                if _normalize(row.contract) == _normalize(name):
                    collected[name].append(row)
    empty = [name for name, rows in collected.items() if not rows]
    if empty:
        raise TariffSourceError(f"엑셀에서 찾지 못한 종별입니다: {empty}")

    seasons, tou = read_time_bands(path)
    contract_types = {
        CONTRACT_RULES[name].key: _contract_payload(
            CONTRACT_RULES[name], collected[name], effective_date
        )
        for name in wanted
    }
    _add_borrowed_options(contract_types)
    # **가리키는 종별이 이번 변환에 있는지 여기서 본다** (98세션). 없는 키를
    # 그대로 내보내면 계약전력 조정이 조용히 안 넘어간다 — 뜨지 않는 갈래가 된다.
    # **부록 A.4 의 「한 종별씩 넣기」 는 예외다** — 짝이 아직 없는 것이 정상이라
    # 그때는 칸을 뺀다. 전체 변환에서 짝이 없으면 그것은 규칙이 틀린 것이다.
    for key, payload in contract_types.items():
        below = payload.get("below_threshold_key")
        if below is None or below in contract_types:
            continue
        if contracts is None:
            raise TariffSourceError(f"{key}: 문턱 아래 종별이 이번 변환에 없습니다: {below!r}")
        del payload["below_threshold_key"]
    return {
        "schema_version": schema_version,
        "region": "kr",
        "source": source,
        "source_file": path.name,
        "effective_date": effective_date,
        "verified": False,
        "season_definition": seasons,
        "tou_definition": {"mainland": tou},
        "day_rules": dict(DAY_RULES),
        "contract_types": contract_types,
        "special_rules": {key: dict(value) for key, value in SPECIAL_RULES.items()},
    }


def rule_table() -> pd.DataFrame:
    """종별 규칙을 표로. 산출물·문서에 그대로 싣는다."""
    return pd.DataFrame([asdict(rule) for rule in CONTRACT_RULES.values()]).set_index("key")
