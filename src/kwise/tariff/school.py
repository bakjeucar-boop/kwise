"""초·중·고교·유치원 특례 (기본공급약관시행세칙 별표4 8.).

**종별 특례가 아니라 시설 특례다.** 조문 가~마 어디에도 갑·을·고압·저압이
없다 — 가르는 축은 「어떤 시설인가」 와 「신청했는가」 둘뿐이다. 6세션이 이것을
종별 속성으로 읽어 교육용(을) **전체**에 15% 를 얹고 있었고, 90세션이 그것을
뒤집었다. 그래서 이 모듈은 **종별을 판정하지 않는다** — 신청 여부는 사용자가
고르고, 여기서는 **바뀌는 것 셋**만 한 자리에 모은다 (97세션 4절).

    ① 요금적용전력의 창   직전 12개월 창이 아니라 **당월분** 하나 (나.(1))
    ② 하한 비율          계약전력의 30% 가 아니라 **15%** (나.(1))
    ③ 계절 할인          12~2월분·7~8월분에 기본사용 **6%** · 냉난방 **50%** (나.(2))

①·② 는 :func:`~kwise.tariff.engine.calculate_bill` 이 직접 쓰고, ③ 은
:func:`school_discount_rates_by_month` 가 낸다. **부르는 쪽마다 따로 계산하지
않는다** — 같은 자료에서 두 값이 나오는 자리를 83세션에 한 번 겪었다.

**③ 은 구조적으로 과소 산출이다.** 조문은 「전력량요금, 기후환경요금 및
연료비조정요금을 **합산한 금액**」 에 걸리는데 이 도구는 전력량요금만 계산한다.
뒤 둘이 빠진 만큼 실제 할인은 여기서 내는 값보다 크다 — 그 사실은 계산이
아니라 안내가 나른다.

**값은 ``data\\rules_kr.json`` 에 있다. 모듈 상수로 붙잡지 않는다.**
"""

from __future__ import annotations

import math
from collections.abc import Mapping

import pandas as pd

from kwise.rules import rule_value

__all__ = [
    "school_base_reference_months",
    "school_contract_types",
    "school_discount_rates",
    "school_discount_rates_by_month",
    "school_floor_ratio",
    "supports_school_exception",
]


def school_contract_types() -> tuple[str, ...]:
    """특례를 신청할 수 있는 계약종별. **코드에 박지 않는다.**"""
    return tuple(str(key) for key in rule_value("school_exception.contract_types"))


def supports_school_exception(contract_type: str) -> bool:
    """이 종별에 특례를 걸 수 있는가. **대상 시설인지는 사용자가 판정한다.**"""
    return contract_type in school_contract_types()


def school_floor_ratio() -> float:
    """특례의 요금적용전력 하한 비율 (나.(1))."""
    return float(rule_value("demand.contract_floor_ratio.school_exception"))


def school_discount_rates() -> tuple[float, float]:
    """(기본사용 할인율, 냉난방 사용 할인율) — 나.(2)."""
    return (
        float(rule_value("school_exception.base_discount_ratio")),
        float(rule_value("school_exception.hvac_discount_ratio")),
    )


def school_base_reference_months() -> dict[int, tuple[int, ...]]:
    """할인 대상 월분 → 기본 사용전력량의 기준월 (다.).

    **키가 곧 할인 대상 월분이다** — 7~8월분은 4~6월분, 12~2월분은 9~11월분.
    """
    table = rule_value("school_exception.base_reference_months")
    return {int(month): tuple(int(item) for item in months) for month, months in table.items()}


def _preceding(period: pd.Period, month: int) -> pd.Period:
    """``period`` **직전에 오는** 그 달.

    해가 걸치는 자리를 이 한 줄이 정한다 — 1월분·2월분의 9~11월분은 **전년도**
    것이고, 12월분의 9~11월분은 같은 해다. 조문이 「9∼11월분」 이라고만 적어
    연도를 말하지 않으므로 **직전에 오는 것**으로 읽는다.
    """
    year = period.year if month < period.month else period.year - 1
    return pd.Period(year=year, month=month, freq="M")


def school_discount_rates_by_month(
    monthly_kwh: Mapping[pd.Period, float],
    *,
    reference_months: frozenset[pd.Period] | set[pd.Period],
) -> tuple[dict[pd.Period, float], tuple[pd.Period, ...]]:
    """월분별 할인율과, **기준월이 없어 할인하지 못한 달** (나.(2) · 다.).

    할인율은 그 달 금액에 곱할 하나의 비율이다 — 조문이 금액을 기본사용과
    냉난방 사용의 **전력량 비중**으로 나눈 뒤 각각 6%·50% 를 깎으라고 하므로,
    합치면 ``기본비중 × 6% + 냉난방비중 × 50%`` 한 값이 된다.

    Args:
        monthly_kwh: 월별 총 사용전력량. 기준월도 이 안에서 찾는다.
        reference_months: 기준월로 쓸 수 있는 달. **부분 월은 넣지 않는다** —
            며칠치뿐인 달을 평균에 넣으면 기본 사용전력량이 통째로 내려가
            냉난방 비중이 부풀고 할인이 과대 산출된다.

    **조문이 정하지 않은 것 둘을 여기서 정한다.**

    - 냉난방이 음수인 달(기준월 평균이 그 달 사용량보다 큰 달)은 **0 으로 본다.**
      곧 그 달은 기본사용 할인율만 붙는다.
    - 기준월이 하나도 없으면 **할인하지 않고 그 달을 돌려준다.** 지어내지 않는다.
    """
    base_rate, hvac_rate = school_discount_rates()
    table = school_base_reference_months()
    rates: dict[pd.Period, float] = {}
    skipped: list[pd.Period] = []
    for period in sorted(monthly_kwh):
        targets = table.get(period.month)
        if targets is None:
            continue  # 할인 대상 월분이 아니다
        total = float(monthly_kwh[period])
        available = [
            float(monthly_kwh[reference])
            for reference in (_preceding(period, month) for month in targets)
            if reference in reference_months
            and reference in monthly_kwh
            and not math.isnan(float(monthly_kwh[reference]))
        ]
        if not available or math.isnan(total) or total <= 0:
            skipped.append(period)
            continue
        base_kwh = min(sum(available) / len(available), total)  # 냉난방 음수는 0
        base_share = base_kwh / total
        rates[period] = base_share * base_rate + (1.0 - base_share) * hvac_rate
    return rates, tuple(skipped)
