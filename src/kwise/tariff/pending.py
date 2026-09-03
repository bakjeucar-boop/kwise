"""아직 못 쓰는 선택요금의 오차 안내 (93세션).

**후보를 막지 않는다.** 시행 전이라도 고객은 그 요금제를 고를 수 있고 개선안도
그쪽으로의 전환을 권고할 수 있다. 대신 계산에는 오차가 있다 — 도구는 고객이
고른 선택요금 **하나로 분석 기간 전체를 간다.** 기간이 시행 시점을 걸쳐도
달마다 갈아 끼우지 않는다.

그 오차를 산출물에 낸다. **뜨는 조건은 셋 다여야 한다.**

    ① 그 선택요금이 **요금표 판보다 늦게 선다** — 곧 아직 못 쓰는 요금제다
    ② 산출물에 그것이 등장한다 — 고객의 현행이거나, 개선안의 권고다
    ③ 분석 기간이 시행 시점 **이전을 포함한다**

기간 전체가 시행 뒤면 오차가 없으므로 안내를 내지 않는다. **없는 오차를
경고하면 있는 경고가 묻힌다.**

**①이 없으면 안내가 전부에 뜬다.** 분석 기간은 대개 요금표 시행일보다 앞서
시작하므로 ③만으로는 선택Ⅰ·Ⅱ 까지 걸린다 — 만들자마자 값으로 보고 잡았다.
요금표 판 자체를 기간 전체에 적용하는 것은 이 안내가 말하는 오차가 아니다.

배경·제도·조문은 여기 두지 않는다 — 매뉴얼로 간다 (`CLAUDE.md` 「화면 문구」).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from kwise.notices import Notice, warn
from kwise.tariff.labels import option_label, option_sort_key
from kwise.tariff.schema import TariffSelection, TariffTable

__all__ = ["pending_option_notices"]


def _starts(effective_date: str) -> date:
    """시행일을 견줄 수 있는 날로. **두 꼴이 온다.**

    요금표 판과 함께 서는 선택요금은 **날**(``2026-06-01``)이고, 약관이
    「N월분 요금부터」 로 정한 것은 **요금월**(``2026-12``)이다. 요금월이
    실제로 시작하는 날은 검침 기간에 따라 고객마다 다르므로 **날로 환산해
    적어 두지 않는다** — 여기서 그 달 첫날로 읽는 것은 견주는 데 쓰는
    하한일 뿐이다.
    """
    return date.fromisoformat(effective_date if len(effective_date) > 7 else f"{effective_date}-01")


def _billing_month(effective_date: str) -> str:
    """``2026-12`` → ``2026년 12월분``."""
    parsed = _starts(effective_date)
    return f"{parsed.year}년 {parsed.month}월분"


def pending_option_notices(
    table: TariffTable,
    selections: Iterable[TariffSelection],
    *,
    period_start: date,
) -> tuple[Notice, ...]:
    """산출물에 등장하는 선택요금 가운데 **아직 못 쓰는 것**의 안내.

    Args:
        selections: 산출물에 등장하는 조합. 현행과 권고 둘이면 둘 다 준다.
        period_start: 분석 기간의 시작일.
    """
    edition = _starts(table.effective_date)
    by_date: dict[str, set[str]] = {}
    for selection in selections:
        rates = table.rates(selection)
        starts = _starts(rates.effective_date)
        if starts <= edition or starts <= period_start:
            continue
        by_date.setdefault(rates.effective_date, set()).add(selection.option)

    notices: list[Notice] = []
    for effective_date, options in sorted(by_date.items()):
        names = "·".join(option_label(option) for option in sorted(options, key=option_sort_key))
        notices.append(
            warn(
                f"{names} 요금제는 {_billing_month(effective_date)} 요금부터 쓸 수 있습니다. "
                "분석 기간 전체에 이 요금제를 적용해 계산하므로 "
                "실제 청구와 다를 수 있습니다.",
                fact=f"tariff.pending_option:{effective_date}",
            )
        )
    return tuple(notices)
