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

from kwise.report.notices import plain_text
from kwise.tariff.labels import OPTION_LABELS, SEASON_LABELS, option_label, season_label

__all__ = [
    "COLUMN_LABELS",
    "DISPLAY_DECIMALS",
    "OPTION_LABELS",
    "SEASON_LABELS",
    "VALUE_LABELS",
    "column_label",
    "display_frame",
    "localize",
    "option_label",
    "round_columns",
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
    "light_won": "경부하 요금(원)",
    "mid_won": "중간부하 요금(원)",
    "peak_won": "최대부하 요금(원)",
    # **「기본요금」 이라고 부르지 않는다** (S142 1절). 그 이름은 S141 이
    # 역률 가감 반영 **후**로 정했고 Excel 요약 시트·화면·PPT·Word 가 그
    # 금액을 적는다 — 이 열은 역률요금을 뺀 값이라 같은 이름을 쓰면 한
    # 통합문서 안에서 「기본요금」 이 두 금액을 가리킨다 (결함 유형 ③ ·
    # `large-b-pf85` 에서 452,804,556 대 459,143,820원). 옆에 「역률 요금(원)」
    # 열이 따로 서므로 둘을 더하면 그 이름의 금액이 된다.
    # 꼴은 같은 표의 「하한 적용 전 수요(kW)」 를 따랐다.
    "base_won": "역률 조정 전 기본요금(원)",
    "energy_won": "전력량요금(원)",
    "energy_won_adjusted": "전력량요금 보정(원)",
    "discount_won": "할인(원)",
    "school_discount_won": "초·중·고교·유치원 특례 할인(원)",
    "power_factor_won": "역률 요금(원)",
    # **부가금은 기본요금에 접지 않는다** (109세션). 접으면 「기본요금 = 월평균
    # 기본요금 기준전력 × 단가 × 개월수」 라는 각주가 그 자리에서 거짓이 된다.
    "excess_won": "초과사용부가금(원)",
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


# ─────────────────────────────────── 표에 낼 때 (S161 2절)
#
# **사람이 읽는 표에 날값을 내지 않는다.** 열 이름은 이미 여기서 한글이 되고
# 값 번역표(:data:`VALUE_LABELS`)도 여기 있는데, **수와 표식은 그 문을 안
# 지났다** — 화면 월별 명세가 `118936.77419354838` · `0.0006944444444444445` 을
# 그대로 냈고 Excel 「조합 비교」 회수기간이 `15.47137949227301` 이었다.
#
# **자릿수는 열 이름 꼬리로 가른다.** 열마다 손으로 적으면 표가 늘 때마다
# 빠뜨리고, 그 빠뜨림이 조용하다 — 같은 자료가 화면과 Excel 에서 다른 꼴로
# 나오는 것이 곧 이 결함이다.

#: 열 이름 꼬리와 자릿수. **위에서부터 처음 맞는 것을 쓴다.**
#:
#: 자료가 소수 두 자리(15분 실측 kW·kWh)라 `(kW)`·`(kWh)` 는 **두 자리**다 —
#: 한 자리로 접으면 15분 칸의 0.04 kWh 가 0.0 이 되어 **표기가 아니라 값을
#: 잃는다.** `(원)` 은 이미 천 원 단위로 절사돼 오므로 0 이다.
DISPLAY_DECIMALS: tuple[tuple[str, int], ...] = (
    ("(원)", 0),
    ("(kW)", 2),
    ("(kWp)", 2),
    ("(kWh)", 2),
    ("(MWh)", 2),
    ("(년)", 1),
    ("(%)", 1),
    ("(h)", 2),
    ("(일)", 1),
    ("률", 4),
    ("율", 4),
)

# **불리언은 안 건드린다** (S161 2절에 값으로 보고 되돌렸다). 미해결이 「영어
# 불리언」 이라 적은 자리 둘은 둘 다 영문 글자가 아니었다 — 화면은
# `st.column_config.CheckboxColumn` 이 체크로 그리고, Excel 은 **셀 자체가
# 불리언**이라 `TRUE`/`FALSE` 는 Excel 이 붙이는 표기다. 「예」/「아니오」 로
# 바꾸면 자료가 글자가 되어 `COUNTIF`·필터가 죽고 시험의 `결측.sum()` 도 깨진다
# (`tests\test_report.py::test_timeseries_sheet_carries_every_slot` 가 그 자리다).


def round_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """수치 열을 **열 이름 꼬리가 정한 자릿수로 접은** 사본.

    **낼 때만 접는다.** 계산 프레임은 날값 그대로다 — 접은 값으로 계산하면
    합계가 어긋나고 회귀 시험이 무는 값이 흔들린다
    (:func:`kwise.report.excel.truncate_money_columns` 와 같은 자리·같은 이유).
    """
    rounded = frame.copy()
    for name in rounded.columns:
        if not pd.api.types.is_numeric_dtype(rounded[name]) or pd.api.types.is_bool_dtype(
            rounded[name]
        ):
            continue
        label = str(name)
        decimals = next((digits for tail, digits in DISPLAY_DECIMALS if label.endswith(tail)), None)
        if decimals is not None:
            rounded[name] = rounded[name].round(decimals)
    return rounded


def display_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """산출물 표의 **출구** — 자릿수를 접고 마크다운 표식을 벗긴다.

    Excel 은 마크다운을 그리지 않는다 — PPT·Word 가 적는 순간 벗기는 것
    (:func:`kwise.report.notices.plain_text`)과 같은 자리인데 **Excel 만 그
    문이 없었다.**
    """
    shown = round_columns(frame)
    for name in shown.columns:
        column = shown[name]
        if not (
            pd.api.types.is_numeric_dtype(column)
            or pd.api.types.is_bool_dtype(column)
            or pd.api.types.is_datetime64_any_dtype(column)
        ):
            # **`object` 로만 거르지 않는다** — pandas 가 글자 열을 `str` dtype 으로
            # 잡으면 그 조건이 거짓이 되어 표식이 그대로 실린다 (S161 2절에 겪었다).
            shown[name] = column.map(
                lambda value: plain_text(value) if isinstance(value, str) else value
            )
    return shown.rename(columns={name: plain_text(str(name)) for name in shown.columns})
