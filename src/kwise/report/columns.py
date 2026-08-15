"""표 열 이름 (요구사항서 10.7).

**계산 결과의 열 이름은 영문 식별자다.** ``days_in_month``, ``billing_demand_kw``
처럼 코드가 쓰기 좋은 이름이라 그대로 화면에 내면 읽는 사람이 뜻을 짐작해야 한다.

번역표를 **한 곳에 둔다.** 화면·Excel·보고서·차트가 각자 이름을 붙이면 같은 열이
세 이름으로 불린다 — 산출물을 나란히 놓고서야 드러난다 (13세션).

계산 프레임 자체는 건드리지 않는다. 열 이름을 바꾸면 요금 엔진과 시험이 모두
흔들린다. **낼 때만 바꿔 낸다.**
"""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

from kwise.tariff.labels import OPTION_LABELS, SEASON_LABELS, option_label, season_label

__all__ = [
    "COLUMN_LABELS",
    "OPTION_LABELS",
    "SEASON_LABELS",
    "VALUE_LABELS",
    "column_label",
    "localize",
    "option_label",
    "season_label",
    "value_label",
]

# 선택요금 표기는 :mod:`kwise.tariff.labels` 에 있다 — **계산 모듈도 써야 한다**
# (선택요금 전환 노트에 종별 이름이 들어간다). 여기서는 다시 내보내기만 한다.
COLUMN_LABELS: dict[str, str] = {
    # 기간·계량
    "month": "월",
    "season": "계절",
    "days_in_month": "월 일수",
    "covered_days": "계량 일수",
    "is_partial": "부분 월",
    "missing_ratio": "결측률",
    "demand_confidence": "최대수요 신뢰도",
    # 수요
    "max_demand_kw": "관측 최대수요(kW)",
    "max_demand_at": "최대수요 발생 시각",
    "demand_basis_kw": "요금적용 대상 최대(kW)",
    "demand_before_floor_kw": "하한 적용 전 수요(kW)",
    "billing_demand_kw": "요금적용전력(kW)",
    "base_demand_kw": "기본요금 기준전력(kW)",
    "base_fee_factor": "기본요금 일할 계수",
    # 사용량
    "light_kwh": "경부하(kWh)",
    "mid_kwh": "중간부하(kWh)",
    "peak_kwh": "최대부하(kWh)",
    "total_kwh": "사용량(kWh)",
    # 금액
    "base_won": "기본요금(원)",
    "energy_won": "전력량요금(원)",
    "energy_won_adjusted": "전력량요금 보정(원)",
    "discount_won": "할인(원)",
    "power_factor_won": "역률 요금(원)",
    "total_won": "합계(원)",
    "total_won_adjusted": "합계 보정(원)",
}


#: **값도 한글로 낸다** (21세션 3-1). 열 이름만 옮기면 `계절` 칸에 `spring_fall`
#: 이 남는다 — 코드 식별자를 화면에 내지 않는다는 12세션 규약을 값이 깬 자리다.
#: 열 이름을 바꾸기 **전**의 이름으로 찾는다.
VALUE_LABELS: dict[str, dict[str, str]] = {
    # 계절 표기는 :mod:`kwise.tariff.labels` 에 있다 — 계산 모듈도 쓴다 (25세션 4-1).
    "season": SEASON_LABELS,
    "band": {"light": "경부하", "mid": "중간부하", "peak": "최대부하"},
    "day_type": {"weekday": "평일", "saturday": "토요일", "holiday": "휴일"},
}


def column_label(name: str) -> str:
    """모르는 이름은 **그대로 돌려준다.** 조용히 '기타' 로 뭉치지 않는다."""
    return COLUMN_LABELS.get(name, name)


def value_label(column: str, value: object) -> object:
    """열이 값 번역표를 가지면 값을 옮긴다. 없으면 **그대로 둔다.**"""
    table = VALUE_LABELS.get(column)
    if table is None:
        return value
    return table.get(str(value), value)


def localize(frame: pd.DataFrame, *, index_name: str | None = None) -> pd.DataFrame:
    """열 이름과 **값**을 한글로 바꾼 사본. 원본은 그대로 둔다."""
    localized = frame.copy()
    for name in localized.columns:
        table = VALUE_LABELS.get(str(name))
        if table is None:
            continue
        localized[name] = localized[name].map(
            lambda value, table=table: table.get(str(value), value)
        )
    renamed = localized.rename(
        columns={name: column_label(str(name)) for name in localized.columns}
    )
    if index_name is not None:
        renamed = renamed.rename_axis(index_name)
    elif frame.index.name:
        renamed = renamed.rename_axis(column_label(str(frame.index.name)))
    return renamed


def localized_columns(names: Iterable[str]) -> list[str]:
    """열 목록을 한글로. 순서를 지킨다."""
    return [column_label(name) for name in names]
