"""ESS 차익거래 (요구사항서 7.6).

경부하에 충전해 최대부하에 방전하는 **단가차익**이다.

    수익/kWh·년 = Σ_계절 (최대부하단가 − 경부하단가 ÷ 왕복효율) × 계절별 평일수

**계시별 단가는 요금 엔진에서 자동으로 가져온다. 사용자 입력이 아니다.**
평일 수도 요금 달력에서 센다 — 최대부하 시간대가 실제로 존재하는 날만 세면
토요일(중간부하까지)과 일요일·공휴일(전량 경부하)이 자동으로 빠진다.

**수익 구조를 이해하는 것이 이 모듈의 목적이다.**

    피크저감 수익  ∝ 출력(kW)    용량을 늘려도 늘지 않는다
    차익거래 수익  ∝ 용량(kWh)
    투자비         ∝ 용량(kWh)   (kW당 단가 = CAPEX_Power + CAPEX_Energy × 방전시간)

그래서 **용량을 늘릴수록 회수기간이 나빠진다.** 그리고 차익거래만으로는 배터리
값을 뽑지 못한다 — 샘플에서 연 19,656원/kWh 이므로 CAPEX_Energy 369,936원/kWh
대비 18.8년이고, 배터리 수명(10~15년)을 넘는다. **"왜 안 되는가"에 대한 답이
이 숫자다.**

차익거래 수익을 피크저감 절감액에 **더하지 않는다.** 이유가 셋이다 — 41세션이
샘플을 실제로 돌려 확인했다.

**① 도구가 돌리지 않는 운전의 잠재값이다.** :mod:`kwise.measures.ess` 의 디스패치는
목표를 넘을 때만 방전한다. 샘플 최적점(5,170 kW)에서 359일 중 **12일**만 방전해
연 **2.7 사이클**을 돈다. 차익거래가 전제하는 평일 227 사이클과는 **84배** 차이다.

**② 예비 규칙이 없다. 없이 돌리면 오히려 나빠진다.** 최대부하에 보이는 대로
방전하면 정작 피크가 왔을 때 배터리가 비어 피크를 못 깎는다. 샘플을 그렇게 돌리면
달성 피크가 5,170.0 → 5,248.6 kW 로 되밀리고 기본요금 절감이 542만원 줄어
**회수기간이 30.75 → 50.61년**이 된다. 차익거래를 얹었더니 ESS 가 더 나빠지는 것이다.
그날 피크만큼 남겨 두면(완전 예지) 25.52년이 되지만, **예지를 전제한 값은 산출물에
낼 수 없다.** 전일 예측·계절별 보수 예비 같은 규칙을 먼저 정해야 한다.

**③ 열화·수명이 계산에 없다.** 사이클이 84배가 되는 대가가 어디에도 없다
(``ess.degradation_excluded``). ``battery_life_years`` 는 달력 수명이고 사이클
수명 모형은 없다.

26세션은 「피크컷 디스패치가 이미 일부를 실현해 이중 계산」 을 이유로 적었으나
41세션이 그 겹침을 재 보니 **사이클 1.06% · 금액 0.80%** (케이스 여섯에서 0.46~1.85%)
로 사실상 없는 몫이었다. **근거가 틀렸던 것이지 결론이 틀린 것은 아니다.**

여기서 내는 값은 **매 평일 한 사이클을 온전히 돌렸을 때의 잠재값**이며 별도 줄로
표시한다. 「이만큼의 여지가 있으나 지금 계산에는 넣지 않았다」 를 알리는 것이 목적이다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from kwise.io import UsageData
from kwise.notices import Notice, basis, warn
from kwise.quality import QualityReport
from kwise.rules import assumption
from kwise.tariff import (
    BillingOptions,
    TariffSelection,
    TariffTable,
    build_calendar,
    classify_slots,
)
from kwise.tariff.labels import season_label

__all__ = [
    "ESS_SAVING_LABEL",
    "ArbitrageValue",
    "SeasonSpread",
    "arbitrage_value",
    "c_rate",
    "default_cycles_per_day",
    "peak_days_by_season",
]

#: ESS 카드가 「절감액」 으로 내는 값의 **이름** (31세션 5-2).
#:
#: 차익거래 안내가 「무엇에 더하지 않았나」 를 말하려면 그 값을 부를 이름이
#: 있어야 한다. 이름을 여기 한 곳에 두어 화면·Excel·보고서가 같은 말을 쓴다.
ESS_SAVING_LABEL = "ESS 절감액(기본요금 + 전력량요금)"


def _season_days(days: dict[str, int]) -> str:
    """``봄·가을 103일 · 여름 64일 · 겨울 83일``. 계절 순서는 이름순으로 고정한다."""
    return " · ".join(
        f"{season_label(season)} {count:,}일" for season, count in sorted(days.items())
    )


def default_cycles_per_day() -> float:
    """평일 1사이클. 피크컷용 배터리를 매 평일 한 번 돌린다는 가정이다 (판단값)."""
    return float(assumption("ess.cycles_per_day"))


@dataclass(frozen=True)
class SeasonSpread:
    """계절 하나의 단가차익."""

    season: str
    peak_won_per_kwh: float
    light_won_per_kwh: float
    days: int
    round_trip: float

    @property
    def spread_won_per_kwh(self) -> float:
        """왕복효율을 반영한 1 kWh 방전당 차익. 충전은 효율만큼 더 사야 한다."""
        return self.peak_won_per_kwh - self.light_won_per_kwh / self.round_trip

    @property
    def won_per_kwh_period(self) -> float:
        return self.spread_won_per_kwh * self.days


@dataclass(frozen=True)
class ArbitrageValue:
    """차익거래 잠재 수익.

    Attributes:
        won_per_kwh_year: **12개월 환산** 1 kWh 당 수익. 이 숫자가 핵심이다.
        annual_won: 가용 용량 전체의 12개월 환산 수익.
        standalone_payback_years: 차익거래 **단독** 회수기간.
            CAPEX 의 에너지 성분(원/kWh)만으로 나눈다 — 차익거래는 용량이 만드는
            수익이므로 출력 성분을 부담시키지 않는 것이 가장 유리한 가정이다.
            그 유리한 가정에서도 성립하지 않는다는 것이 요점이다.
    """

    spreads: tuple[SeasonSpread, ...]
    won_per_kwh_period: float
    won_per_kwh_year: float
    usable_kwh: float
    annual_won: float
    cycles_per_day: float
    round_trip: float
    period_days: int
    capex_energy_won_per_kwh: float | None = None
    standalone_payback_years: float | None = None
    battery_life_years: tuple[float, float] = (10.0, 15.0)
    notices: tuple[Notice, ...] = field(default=())

    @property
    def outlives_battery(self) -> bool:
        """단독 회수기간이 배터리 수명을 넘는가. 넘으면 단독으로는 성립하지 않는다."""
        if self.standalone_payback_years is None:
            return True
        return self.standalone_payback_years > self.battery_life_years[1]

    def frame(self) -> pd.DataFrame:
        """계절별 내역. 어느 계절이 얼마를 벌어 주는지 보여 준다.

        **계절 이름을 한글로 낸다** (25세션 4-1). 이 표는 Excel 로 그대로 나가므로
        ``spring_fall`` 이 칸에 남으면 코드 식별자를 산출물에 싣는 셈이다.
        """
        return pd.DataFrame(
            [
                {
                    "계절": season_label(item.season),
                    "최대부하 단가(원/kWh)": item.peak_won_per_kwh,
                    "경부하 단가(원/kWh)": item.light_won_per_kwh,
                    "차익(원/kWh)": item.spread_won_per_kwh,
                    "평일수": item.days,
                    "기간 수익(원/kWh)": item.won_per_kwh_period,
                }
                for item in self.spreads
            ]
        ).set_index("계절")


def peak_days_by_season(
    usage: UsageData,
    table: TariffTable,
    *,
    selection: TariffSelection | None = None,
    options: BillingOptions | None = None,
) -> dict[str, int]:
    """계절별 **최대부하가 존재하는 날** 수를 센다.

    토요일은 최대부하가 중간부하로 계량되고 일요일·공휴일은 전량 경부하이므로
    이 방식이면 자동으로 빠진다. DR 거래일(6.6)과는 다른 규칙이라 따로 센다 —
    DR 은 토요일도 제외지만 여기서는 토요일에 중간부하 차익이 남는다.
    """
    opts = options if options is not None else BillingOptions()
    index = pd.DatetimeIndex(usage.kw.index)
    calendar = build_calendar(
        range(index[0].year - 1, index[-1].year + 2),
        sunday_is_holiday=opts.sunday_is_holiday,
        exclude_temporary=(
            table.day_rules.exclude_temporary_holiday
            if opts.exclude_temporary_holiday is None
            else opts.exclude_temporary_holiday
        ),
        extra_holidays=opts.extra_holidays,
        excluded_holidays=opts.excluded_holidays,
    )
    slots = classify_slots(
        index,
        usage.meta.interval_minutes,
        table,
        calendar,
        contract_type=selection.contract_type if selection else None,
        region_group=opts.region_group,
    )
    peak = slots[slots["band"] == "peak"]
    if peak.empty:
        return {}
    days = pd.DatetimeIndex(peak["slot_start"]).normalize()
    counted = pd.DataFrame({"season": peak["season"].to_numpy(), "day": days})
    return {
        str(season): int(group["day"].nunique())
        for season, group in counted.groupby("season", sort=True)
    }


def arbitrage_value(
    usage: UsageData,
    table: TariffTable,
    selection: TariffSelection,
    *,
    usable_kwh: float,
    round_trip: float,
    base_fee_months: float,
    cycles_per_day: float | None = None,
    capex_energy_won_per_kwh: float | None = None,
    quality: QualityReport | None = None,
    options: BillingOptions | None = None,
) -> ArbitrageValue:
    """경부하 충전 → 최대부하 방전의 단가차익을 낸다.

    Args:
        usable_kwh: 가용 용량 (정격 × DoD).
        capex_energy_won_per_kwh: 단독 회수기간을 낼 CAPEX 에너지 성분.
            주지 않으면 회수기간을 내지 않는다 — 지어낸 단가로 금액을 만들지 않는다.
        cycles_per_day: 평일 사이클 수. 기본 1.
    """
    if not 0 < round_trip <= 1:
        raise ValueError(f"왕복효율은 0 초과 1 이하여야 합니다: {round_trip}")
    cycles_per_day = default_cycles_per_day() if cycles_per_day is None else cycles_per_day
    if cycles_per_day < 0:
        raise ValueError(f"사이클 수는 음수일 수 없습니다: {cycles_per_day}")
    _ = quality  # 결측은 단가·달력 계산에 영향을 주지 않는다

    rates = table.rates(selection)
    days = peak_days_by_season(usage, table, selection=selection, options=options)
    spreads = tuple(
        SeasonSpread(
            season=season,
            peak_won_per_kwh=rates.rate(season, "peak"),
            light_won_per_kwh=rates.rate(season, "light"),
            days=count,
            round_trip=round_trip,
        )
        for season, count in sorted(days.items())
    )

    per_kwh_period = sum(item.won_per_kwh_period for item in spreads) * cycles_per_day
    per_kwh_year = per_kwh_period * 12.0 / base_fee_months if base_fee_months > 0 else 0.0
    annual = per_kwh_year * usable_kwh

    payback: float | None = None
    if capex_energy_won_per_kwh is not None and per_kwh_year > 0:
        payback = capex_energy_won_per_kwh / per_kwh_year

    notices = [
        basis(
            "계시별 단가는 요금표에서 가져왔습니다. 사용자 입력이 아닙니다.",
            fact="arbitrage.tariff_rates",
        ),
        basis(
            # **사전을 그대로 찍지 않는다** (25세션 4-1). ``{'spring_fall': 103}``
            # 이 화면에 나갔다 — 계절 이름은 표기 규약(:mod:`kwise.tariff.labels`)이다.
            f"최대부하가 존재하는 날만 셌습니다 (계절별 {_season_days(days)}). 토요일은 "
            "최대부하가 중간부하로 계량되고 일요일·공휴일은 전량 경부하라 자동으로 빠집니다.",
            fact="arbitrage.peak_days",
        ),
        basis(
            f"평일 {cycles_per_day:g} 사이클, 왕복효율 {round_trip:.0%} 가정입니다.",
            fact="arbitrage.cycle_assumption",
        ),
        # **주의다.** 그대로 더하면 금액을 읽는 방법 자체가 달라진다.
        warn(
            # **무엇에 더하지 않았는지를 이름으로 적는다** (31세션 5-2). 29세션까지는
            # 「피크저감 절감액에 더하지 않았습니다」 였는데, **「피크저감 절감액」 은
            # 화면 어디에도 없는 이름**이라 무엇을 가리키는지 알 수 없었다 — 카드가
            # 내는 이름은 그냥 「절감액」 이다. 자리(「위」)로 적으면 Excel·보고서에서
            # 틀리므로 값의 이름으로 적는다.
            #
            # **까닭을 41세션에 다시 썼다.** 26세션은 「피크컷 운전이 이미 일부를
            # 실현하고 있어 이중 계산」 이라고 적었는데, 그 「일부」 를 재 보니
            # 0.5~1.9% 였다 — 근거가 자기 숫자에 반박당하고 있었다. 실질은 **예비
            # 규칙**이다: 없이 돌리면 피크를 못 깎아 회수기간이 30.75 → 50.61년으로
            # 나빠진다 (샘플 실측). 화면에는 그것만 한 줄로 적고 나머지는 기술서로
            # 보낸다 (모듈 도크스트링 · ``docs\TECHNICAL.md`` 6.6).
            f"**{ESS_SAVING_LABEL}에 더하지 않은 값입니다.** 매 평일 한 사이클을 온전히 "
            "돌렸을 때의 잠재값인데, 그날 피크에 쓸 몫을 남기는 운전 규칙이 없으면 "
            "배터리가 비어 **피크를 못 깎아 오히려 회수기간이 나빠집니다.**",
            fact="arbitrage.not_additive",
        ),
    ]
    if payback is not None:
        life_low, life_high = 10.0, 15.0
        verdict = (
            f"배터리 수명({life_low:.0f}~{life_high:.0f}년)을 넘어 **단독으로는 "
            "성립하지 않습니다.**"
            if payback > life_high
            else "배터리 수명 안에 들어옵니다."
        )
        notices.append(
            basis(
                f"차익거래 단독 회수기간 {payback:,.1f}년 — 연 {per_kwh_year:,.0f}원/kWh 로 "
                f"CAPEX 에너지 성분 {capex_energy_won_per_kwh:,.0f}원/kWh 를 회수합니다. "
                f"{verdict}",
                fact="arbitrage.standalone_payback",
            )
        )

    return ArbitrageValue(
        spreads=spreads,
        won_per_kwh_period=per_kwh_period,
        won_per_kwh_year=per_kwh_year,
        usable_kwh=usable_kwh,
        annual_won=annual,
        cycles_per_day=cycles_per_day,
        round_trip=round_trip,
        period_days=sum(days.values()),
        capex_energy_won_per_kwh=capex_energy_won_per_kwh,
        standalone_payback_years=payback,
        notices=tuple(notices),
    )


def c_rate(discharge_hours: float) -> float:
    """방전시간 → C-rate. 0.5h 는 2C 다."""
    if discharge_hours <= 0:
        return math.inf
    return 1.0 / discharge_hours
