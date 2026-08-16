"""선택요금 전환 (요구사항서 7.1) — 투자 0원.

가능한 모든 선택요금 조합을 계산해 표로 낸다. **설비 없이도 가치가 나오는
핵심 기능이다.** 선택지 목록은 요금 데이터 파일에서 생성한다. 하드코딩 금지.

두 기준을 모두 돌려준다.

    현행 유지 기준   지금 계약된 선택요금 그대로
    최적 전환 기준   전 조합 중 가장 싼 것

다른 수단(태양광·ESS)의 기준선을 어느 쪽으로 잡느냐에 따라 절감액이 달라지므로
둘 다 있어야 한다. 감도를 적용하지 않는 확정 계산이다 (요구사항서 9.2).

4세션 diagnose 가 이미 전 조합을 돌린다. 그 합계를 ``option_totals`` 로 넘기면
상세가 필요한 두 조합(현행·최적)만 다시 계산한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from kwise.io import UsageData
from kwise.measures.base import Certainty, annualize
from kwise.notices import Notice, info, warn
from kwise.quality import QualityReport
from kwise.tariff import (
    BillingOptions,
    BillingResult,
    TariffSelection,
    TariffTable,
    calculate_bill,
    selection_label,
    switchable_selections,
)

__all__ = ["OptionQuote", "TariffSwitchResult", "evaluate_tariff_switch"]


@dataclass(frozen=True)
class OptionQuote:
    """조합 하나의 요금. 상세를 계산하지 않은 조합은 합계만 있다."""

    selection: TariffSelection
    total_won: float
    base_won: float | None = None
    energy_won: float | None = None

    @property
    def key(self) -> str:
        return str(self.selection)


@dataclass(frozen=True, eq=False)
class TariffSwitchResult:
    """선택요금 전환 평가."""

    current: OptionQuote
    best: OptionQuote
    quotes: tuple[OptionQuote, ...]
    saving_won: float
    annual_saving_won: float
    base_fee_months: float
    period_label: str
    current_bill: BillingResult
    best_bill: BillingResult
    certainty: Certainty = Certainty.HIGH
    investment_won: float = 0.0
    notices: tuple[Notice, ...] = field(default=())

    @property
    def switch_needed(self) -> bool:
        return self.best.selection != self.current.selection

    @property
    def ranking(self) -> tuple[OptionQuote, ...]:
        """싼 순서. 화면 표를 이 순서로 그린다."""
        return tuple(sorted(self.quotes, key=lambda quote: quote.total_won))


def evaluate_tariff_switch(
    usage: UsageData,
    table: TariffTable,
    current_selection: TariffSelection,
    *,
    quality: QualityReport | None = None,
    options: BillingOptions | None = None,
    option_totals: Mapping[str, float] | None = None,
) -> TariffSwitchResult:
    """전 조합을 계산해 현행 기준과 최적 기준을 낸다.

    Args:
        option_totals: 이미 계산해 둔 조합별 합계. 4세션 ``Diagnosis.option_totals``
            를 그대로 넘기면 중복 계산을 피한다.
    """
    opts = options if options is not None else BillingOptions()
    # 요금 데이터에서 생성하되 **현행 계약종별·전압구분 안에서만** 비교한다.
    # 종별은 용도로, 전압구분은 수전설비로 정해진다. 154 kV 수전 건물에
    # "고압A 로 바꾸면 절감" 을 권하는 것은 변전설비를 새로 지으라는 말이다.
    selections = switchable_selections(table, current_selection)
    if current_selection not in selections:
        raise ValueError(
            f"요금표에 없는 조합입니다: {current_selection} "
            f"(가능: {', '.join(str(item) for item in selections)})"
        )

    totals: dict[str, float] = dict(option_totals) if option_totals else {}
    missing = [item for item in selections if str(item) not in totals]
    for selection in missing:  # 순차 처리. 합계만 남긴다
        totals[str(selection)] = calculate_bill(
            usage, table, selection, options=opts, quality=quality
        ).total_won

    # **갈아탈 수 있는 조합 안에서만 고른다.** ``option_totals`` 에 다른 전압구분이
    # 섞여 들어와도 그것을 최적으로 뽑지 않는다.
    best_selection = min(selections, key=lambda item: totals[str(item)])
    best_key = str(best_selection)

    # 상세가 필요한 것은 두 조합뿐이다.
    current_bill = calculate_bill(usage, table, current_selection, options=opts, quality=quality)
    best_bill = (
        current_bill
        if best_selection == current_selection
        else calculate_bill(usage, table, best_selection, options=opts, quality=quality)
    )

    # **모든 선택요금의 기본·전력량 내역을 낸다** (17세션 1-4).
    #
    # 상세를 현행·최적 둘만 내던 것은 계산을 아끼려는 최적화였는데, 화면이
    # 나머지를 **「상세 미산출」** 로 그렸다. 값이 없는 것이 아니라 쪼개지 않았을
    # 뿐인데 「산출하지 못했다」 로 읽혔다 — 요금제 비교 그래프에서 막대 하나만
    # 통짜로 서 있으니 무엇이 다른지 볼 수 없었다.
    #
    # 갈아탈 수 있는 조합은 **같은 계약종별·전압 안의 선택요금뿐**이라 많아야
    # 셋이다. 합계는 이미 있으므로 늘어나는 것은 한 벌 남짓이고, 합계·최적·
    # 절감액은 그대로다.
    detailed = {
        str(current_selection): current_bill,
        str(best_selection): best_bill,
    }
    for selection in selections:
        if str(selection) in detailed:
            continue
        detailed[str(selection)] = calculate_bill(
            usage, table, selection, options=opts, quality=quality
        )
    quotes = tuple(
        OptionQuote(
            selection=selection,
            total_won=totals[str(selection)],
            base_won=(
                detailed[str(selection)].total_base_won if str(selection) in detailed else None
            ),
            energy_won=(
                detailed[str(selection)].total_energy_won if str(selection) in detailed else None
            ),
        )
        for selection in selections
    )
    quote_by_key = {quote.key: quote for quote in quotes}

    saving = current_bill.total_won - best_bill.total_won
    notices: list[Notice] = [
        # 둘 다 **참고**다 — 이 카드를 어떻게 읽는가에 대한 전제이지 산식이 아니다.
        info(
            "선택요금 전환은 실측 데이터와 요금표만으로 확정되는 계산입니다. "
            "감도를 적용하지 않습니다.",
            fact="tariff_switch.no_sensitivity",
        ),
        info(
            "설비 도입과 무관하게 나오는 절감액입니다. 투자가 필요하지 않습니다.",
            fact="tariff_switch.no_investment",
        ),
    ]
    if best_selection != current_selection:
        # **코드 식별자를 문구에 넣지 않는다** (12세션 규약, 15세션에 되살아난 것을
        # 통합 시험이 잡았다). 계산 모듈의 노트도 화면·산출물로 그대로 나간다.
        #
        # **뒷문장을 뺐다** (27세션 4-4) — 「다른 수단의 기준선을 현행으로 둘지
        # 최적으로 둘지에 따라 그 수단의 절감액이 달라진다」. 기준선을 고르는
        # 것은 사용자가 하는 일이 아니다. 2단계는 언제나 현행 기준이고 상호작용은
        # 3단계가 다시 계산한다 — 그 규약을 이 자리에서 되묻게 만들던 문장이다.
        notices.append(
            warn(
                f"가장 유리한 요금제는 {selection_label(table, best_selection)} 입니다.",
                fact="tariff_switch.best_selection",
            )
        )
    return TariffSwitchResult(
        current=quote_by_key[str(current_selection)],
        best=quote_by_key[best_key],
        quotes=quotes,
        saving_won=saving,
        annual_saving_won=annualize(saving, current_bill.base_fee_months),
        base_fee_months=current_bill.base_fee_months,
        period_label=current_bill.period_label,
        current_bill=current_bill,
        best_bill=best_bill,
        notices=(*current_bill.notices, *notices),
    )
