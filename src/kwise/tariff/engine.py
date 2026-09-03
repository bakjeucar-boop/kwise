"""요금 계산기 (요구사항서 5장).

**기본요금과 전력량요금만 계산한다.** 기후환경요금·연료비조정요금·부가가치세·
전력산업기반기금은 포함하지 않는다. 모두 사용전력량에 비례하므로 실제 절감액은
본 결과보다 다소 크게 나타난다 (5.1).

순수 함수다. Streamlit 을 import 하지 않는다.

핵심 규칙
    요금적용전력   당월 및 직전 11개월의 최대수요전력 중 최대값 (5.2).
                   여름 피크만 낮추고 겨울 피크가 그대로면 기본요금은 변하지 않는다.
    결측 월        관측 기준과 결측 보정 기준을 함께 낸다 (5.4).
    부분 월        기본요금을 13개월분 부과하지 않는다 (5.5).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import pandas as pd

from kwise.io import UsageData
from kwise.notices import Notice, basis, info, warn
from kwise.quality import QualityReport, monthly_missing
from kwise.tariff.demand import (
    apply_contract_floor,
    billing_demands,
    demand_eligible_mask,
    demand_window_months,
    monthly_demand_basis,
)
from kwise.tariff.holiday import DateLike, build_calendar
from kwise.tariff.labels import billing_month_label, option_label
from kwise.tariff.power_factor import (
    PowerFactorCharge,
    lagging_standard_pct,
    power_factor_charge,
)
from kwise.tariff.schema import (
    BANDS,
    DEFAULT_REGION_GROUP,
    TariffDataError,
    TariffSelection,
    TariffTable,
)
from kwise.tariff.tou import classify_slots

__all__ = [
    "AMI_BASIS_NOTICE",
    "MISSING_LIMIT_RATIO",
    "NOT_INCLUDED_NOTICE",
    "TENTATIVE_BASE_FEE_BASIS_WARNING",
    "AnnualEstimate",
    "BillingOptions",
    "BillingResult",
    "PartialMonthPolicy",
    "PowerFactorCharge",
    "billing_demands",
    "calculate_bill",
    "demand_window_months",
    "lagging_standard_pct",
]

MISSING_LIMIT_RATIO = 0.05
PartialMonthPolicy = Literal["merge", "prorate"]
# 월 지정은 Period·문자열·Timestamp 를 모두 받는다 (청구서에서 옮겨 적기 쉽게).
type PeriodLike = pd.Period | str | pd.Timestamp

NOT_INCLUDED_NOTICE = (
    "본 결과는 기본요금과 전력량요금만 산출한 값입니다. 기후환경요금, 연료비조정요금, "
    "부가가치세, 전력산업기반기금은 포함되지 않았습니다. 이들은 모두 사용전력량에 "
    "비례하므로 실제 절감액은 본 결과보다 다소 크게 나타납니다."
)

# **이 값이 어느 자료에서 나왔는가** (64세션). 요금적용전력과 기본요금은 청구서에
# 나오는 값이라 고객이 곧바로 대조한다. 61·62세션에 실측 한 건에서 두 값이
# 어긋나는 것을 확인했고, **우리가 자료를 읽는 쪽은 맞다** — 하루 96구간 불일치
# 0건이다. 남은 것은 올린 자료와 계기 레지스터가 다른 계열이라는 것 하나인데
# **원인은 모른다.** 그러므로 화면과 슬라이드는 **현상만** 적는다 — 원인도,
# 자료를 받은 서비스 이름도, 한 건물의 숫자도 적지 않는다. 배경은 매뉴얼이 받는다.
#
# **한 자리에 한 번이다.** 요금적용전력은 화면 스물세 자리, 기본요금은 마흔두
# 자리에 나온다 (64세션 2절). 툴팁으로 퍼뜨리면 그 전부가 후보가 된다.
AMI_BASIS_NOTICE = (
    "요금적용전력과 기본요금은 올려 주신 AMI 계량 자료로 계산한 값입니다. "
    "한전 청구서에 적힌 값과 다를 수 있습니다."
)

# 기본요금 기준의 갈림길은 **최대수요전력계 설치 여부**다 (제68조 ①·②).
# 그것을 정하는 것은 갑/을이 아니라 **공급전압**이다 — 제38조 ②는 「고압 이상의
# 전압으로 전기를 공급받는 고객에게는 최대수요전력과 무효전력을 계량할 수 있는
# 전력량계를 설치한다」 고 적었다. 61세션에 약관 원문으로 확인했다.
#
#     저압이 없는 종별 (을 · 갑Ⅱ)   언제나 고압 → 요금적용전력 (제68조 ①)
#     교육용(갑) 고압A·B            제38조 ②로 계량기가 선다 → 요금적용전력 (89세션)
#     교육용(갑) 저압 · 갑Ⅰ         계량기가 없는 것이 기본값 → 계약전력 (제68조 ②)
#
# **아래 문구는 계약전력 갈래에만 남았다.** 89세션에 전압별 기준이 서면서
# 「고압으로 공급받는다면」 이라는 가정이 사라졌다 — 전압은 이제 안다.
# 남은 잠정은 **계량기가 실제로 섰는가**뿐이다: 갑Ⅰ 고압 행은 저압계량 예외
# 경로(제57조 ④·제59조 ⑤)이고, 저압은 제38조 ③·④가 「설치할 수 있다」(재량)다.
TENTATIVE_BASE_FEE_BASIS_WARNING = (
    "기본요금을 계약전력으로 매겼습니다. 최대수요전력계가 설치된 고객이면 "
    "요금적용전력 기준이라 실제보다 크게 나옵니다 — 청구서로 확인하십시오."
)


@dataclass(frozen=True)
class BillingOptions:
    """계산 옵션."""

    partial_month_policy: PartialMonthPolicy = "merge"
    sunday_is_holiday: bool = True
    exclude_temporary_holiday: bool | None = None  # None 이면 요금 데이터의 day_rules 를 따른다
    extra_holidays: tuple[DateLike, ...] = ()
    excluded_holidays: tuple[DateLike, ...] = ()
    prior_peaks: Mapping[PeriodLike, float] | None = None
    region_group: str = DEFAULT_REGION_GROUP
    missing_limit_ratio: float = MISSING_LIMIT_RATIO
    contract_kw: float | None = None
    # 역률 (기본공급약관 제41·42·43조). 기본값 92% 는 무효전력계 미설치 고객의
    # 간주값이며 이 값에서 추가·감액이 정확히 0 이다 — 모르는 채로 금액을
    # 만들어내지 않는다. 야간 진상역률은 근거가 없으면 None 으로 두고 경고만 낸다.
    power_factor_pct: float | None = None
    """주간 지상역률. None 이면 약관 제42조의 간주값(rules_kr.json)을 쓴다."""
    leading_power_factor_pct: float | None = None


@dataclass(frozen=True)
class AnnualEstimate:
    """12개월 환산값. '연간' 이라는 말 대신 환산이라고 적는다 (5.5)."""

    factor: float
    base_won: float
    energy_won: float
    total_won: float
    energy_won_adjusted: float
    total_won_adjusted: float
    notices: tuple[Notice, ...] = field(default=())


@dataclass(frozen=True, eq=False)
class BillingResult:
    """요금 계산 결과.

    ``monthly`` 한 행이 한 월 버킷이다. 부분 월은 ``is_partial`` 로 표시되고
    기본요금에 ``base_fee_factor`` 가 곱해져 있다.
    """

    monthly: pd.DataFrame
    selection: TariffSelection
    contract_label: str
    voltage_label: str
    tariff_label: str
    effective_date: str
    base_rate_won_per_kw: float
    contract_floor_ratio: float | None
    demand_months: tuple[int, ...]
    contract_kw: float | None

    period_start: pd.Timestamp
    period_end: pd.Timestamp
    period_days: float
    base_fee_months: float
    partial_month_policy: PartialMonthPolicy

    billing_demand_kw: float
    total_base_won: float
    total_energy_won: float
    total_power_factor_won: float
    total_won: float
    total_energy_won_adjusted: float
    total_won_adjusted: float
    power_factor: PowerFactorCharge

    limited_months: tuple[pd.Period, ...]
    prior_peaks_supplied: bool
    transition_months: tuple[pd.Period, ...] = ()
    """부칙의 경과조치로 **짝 선택요금의 금액이 실린 달.** 없으면 빈 튜플이다."""
    notices: tuple[Notice, ...] = field(default=())

    @property
    def period_label(self) -> str:
        """'연간' 대신 쓰는 실제 기간 표기 (5.5)."""
        return (
            f"{self.period_start:%Y-%m-%d} ~ {self.period_end:%Y-%m-%d} "
            f"({self.period_days:.0f}일, 기본요금 {self.base_fee_months:.2f}개월분)"
        )

    def traceability(self) -> tuple[str, ...]:
        """산출물에 넣을 적용 근거 (5.8)."""
        return (
            f"적용 요금표: {self.effective_date} 시행",
            f"계약종별: {self.contract_label} {self.voltage_label} 선택{self.selection.option}",
            f"기본요금 단가: {self.base_rate_won_per_kw:,.0f} 원/kW",
            f"계절·시간대 구분: {self.tariff_label}",
            f"적용 역률: 주간(08~22시) 지상 {self.power_factor.lagging_pct:.1f}%, "
            + (
                f"야간(22~08시) 진상 {self.power_factor.leading_pct:.1f}%"
                if self.power_factor.leading_pct is not None
                else "야간(22~08시) 지상 간주 100% (한전 기본공급약관 제43조 ② 2호 나목)"
            ),
            f"산출 기간: {self.period_label}",
        )

    def annualize(self) -> AnnualEstimate:
        """12개월로 환산한다. 12개월 미만이면 경고를 붙인다."""
        if self.base_fee_months <= 0:
            raise ValueError("기본요금 개월수가 0 이라 환산할 수 없습니다.")
        factor = demand_window_months() / self.base_fee_months
        notices: list[Notice] = []
        if self.period_days < 365:
            notices.append(
                warn(
                    f"기간이 {self.period_days:.0f}일로 12개월 미만입니다. "
                    f"×{factor:.3f} 환산값은 계절 편중이 있어 신뢰도가 낮습니다.",
                    fact="quality.short_period",
                )
            )
        return AnnualEstimate(
            factor=factor,
            base_won=(self.total_base_won + self.total_power_factor_won) * factor,
            energy_won=self.total_energy_won * factor,
            total_won=self.total_won * factor,
            energy_won_adjusted=self.total_energy_won_adjusted * factor,
            total_won_adjusted=self.total_won_adjusted * factor,
            notices=tuple(notices),
        )


# --------------------------------------------------------------------- 요금적용전력


def _as_period(value: PeriodLike) -> pd.Period:
    if isinstance(value, pd.Period):
        return value
    return pd.Period(pd.Timestamp(value), freq="M")


# --------------------------------------------------------------------- 부분 월 (5.5)


def _base_fee_factors(
    covered_days: Mapping[pd.Period, float], policy: PartialMonthPolicy
) -> tuple[dict[pd.Period, float], list[Notice]]:
    """부분 월의 기본요금 배분 계수. 13개월분을 부과하지 않기 위한 것이다."""
    factors: dict[pd.Period, float] = {}
    partials: list[pd.Period] = []
    for month, days in covered_days.items():
        full = float(month.days_in_month)
        if days < full - 1e-9:
            partials.append(month)
        factors[month] = 1.0

    notes: list[Notice] = []
    if not partials:
        return factors, notes

    if policy == "merge" and len(partials) == 2:
        total = sum(covered_days[month] for month in partials)
        for month in partials:
            factors[month] = covered_days[month] / total if total else 0.0
        notes.append(
            basis(
                "부분 월 처리: 두 조각을 합쳐 한 달로 계산했습니다 "
                + " + ".join(f"{month} {covered_days[month]:.1f}일" for month in partials)
                + " = 1개월.",
                fact="tariff.partial_month",
            )
        )
        return factors, notes

    for month in partials:
        factors[month] = covered_days[month] / float(month.days_in_month)
    if policy == "merge":
        notes.append(
            basis(
                "부분 월 처리: 합칠 짝이 없어 일수 비례로 안분했습니다 "
                + ", ".join(f"{month} {factors[month]:.3f}" for month in partials)
                + ".",
                fact="tariff.partial_month",
            )
        )
    else:
        notes.append(
            basis(
                "부분 월 처리: 일수 비례로 안분했습니다 "
                + ", ".join(f"{month} {factors[month]:.3f}" for month in partials)
                + ".",
                fact="tariff.partial_month",
            )
        )
    return factors, notes


# --------------------------------------------------------------------- 부칙 경과조치


# **비금액 열은 두 선택요금에서 같다** — 같은 사용량을 같은 규칙으로 쪼갠 것이라
# kWh·최대수요·요금적용전력·안분 계수가 한 자리도 다르지 않고 단가만 다르다.
# 그래서 갈아 끼우는 것은 아래 열들뿐이다.
_MONEY_COLUMNS = (
    "base_won",
    "light_won",
    "mid_won",
    "peak_won",
    "discount_won",
    "power_factor_won",
    "energy_won",
    "energy_won_adjusted",
    "total_won",
    "total_won_adjusted",
)


def _lower_of_counterpart(
    monthly: pd.DataFrame, counterpart: pd.DataFrame, months: Sequence[pd.Period]
) -> tuple[pd.Period, ...]:
    """``months`` 가운데 **짝이 더 싼 달의 금액을 갈아 끼운다.** 바뀐 달을 돌려준다.

    ``monthly`` 를 그 자리에서 고친다 — 부른 쪽이 방금 만든 표라 남의 것을
    건드리지 않는다.
    """
    replaced: list[pd.Period] = []
    for month in months:
        if month not in counterpart.index:
            continue
        if float(counterpart.loc[month, "total_won"]) >= float(monthly.loc[month, "total_won"]):
            continue
        for column in _MONEY_COLUMNS:
            monthly.loc[month, column] = counterpart.loc[month, column]
        replaced.append(month)
    return tuple(replaced)


# --------------------------------------------------------------------- 본체


def calculate_bill(
    usage: UsageData,
    table: TariffTable,
    selection: TariffSelection,
    *,
    options: BillingOptions | None = None,
    quality: QualityReport | None = None,
) -> BillingResult:
    """주어진 조합으로 기본요금과 전력량요금을 계산한다.

    선택요금 비교(요구사항서 7.1)는 5세션의 measures 가 이 함수를 조합마다 호출한다.

    Args:
        usage: 1세션 로더의 결과. 전력량은 :meth:`UsageData.energy_kwh` 를 쓴다.
        quality: 2세션 품질 검사 결과. 월별 결측률을 여기서 가져온다.
            주지 않으면 같은 함수로 다시 산출한다.
    """
    opts = options if options is not None else BillingOptions()
    rates = table.rates(selection)
    contract = table.contract(selection.contract_type)
    # **전압을 빼고 읽지 않는다** (89세션). 아래 네 자리가 같은 값을 본다.
    base_on_contract = contract.base_fee_on_contract_at(selection.voltage)
    interval = usage.meta.interval_minutes
    index = pd.DatetimeIndex(usage.kw.index)

    exclude_temporary = (
        table.day_rules.exclude_temporary_holiday
        if opts.exclude_temporary_holiday is None
        else opts.exclude_temporary_holiday
    )
    calendar = build_calendar(
        range(index[0].year - 1, index[-1].year + 2),
        sunday_is_holiday=opts.sunday_is_holiday,
        exclude_temporary=exclude_temporary,
        extra_holidays=opts.extra_holidays,
        excluded_holidays=opts.excluded_holidays,
    )
    slots = classify_slots(
        index,
        interval,
        table,
        calendar,
        contract_type=selection.contract_type,
        region_group=opts.region_group,
    )

    # 월 라벨을 **한 번만** 풀어 둔다.
    #
    # ``slots["month"]`` 는 Period 열이다. ``to_numpy()`` 는 그때마다 3만 5천 개를
    # 파이썬 ``Period`` 객체로 상자에 넣는데(pandas 의 ``_box_func``), 아래에서
    # 월별 루프까지 돌면 같은 일을 열몇 번 되풀이한다. 실측에서 이 한 줄이
    # ``calculate_bill`` 의 **74%** 를 먹고 있었다 (0.63초 중 0.47초).
    # 요금 곡선이 이 함수를 21번 부르므로 그대로 21배가 된다.
    month_labels = slots["month"].to_numpy()

    energy = usage.energy_kwh()  # 그리드 이탈분이 포함된 시계열
    frame = pd.DataFrame(
        {
            "month": month_labels,
            "season": slots["season"].to_numpy(),
            "band": slots["band"].to_numpy(),
            "discount": slots["discount_rate"].to_numpy(),
            "kwh": energy.to_numpy(dtype=float),
        }
    )
    grouped = frame.groupby(["month", "season", "band", "discount"], observed=True)["kwh"].sum()

    months = sorted({_as_period(month) for month in month_labels})
    band_kwh: dict[pd.Period, dict[str, float]] = {
        month: dict.fromkeys(BANDS, 0.0) for month in months
    }
    # 시간대별 **금액**도 함께 쌓는다 (27세션 3-2). 화면의 월별 요금 구성 그래프가
    # 쓴다. ``light_kwh`` 와 단가만으로는 되돌릴 수 없다 — 할인 특례(산업용(을)
    # 봄·가을 주말)가 같은 시간대 안에서도 요일·시각에 따라 갈리기 때문이다.
    # **기존 값은 손대지 않는다.** ``energy_won`` 은 그대로 쌓고 여기에 더한다.
    band_won: dict[pd.Period, dict[str, float]] = {
        month: dict.fromkeys(BANDS, 0.0) for month in months
    }
    energy_won: dict[pd.Period, float] = dict.fromkeys(months, 0.0)
    discount_won: dict[pd.Period, float] = dict.fromkeys(months, 0.0)
    season_of: dict[pd.Period, str] = {}
    for (month, season, band, discount), kwh in grouped.items():
        period = _as_period(month)
        value = 0.0 if pd.isna(kwh) else float(kwh)
        rate = rates.rate(str(season), str(band))
        band_kwh[period][str(band)] += value
        band_won[period][str(band)] += value * rate * (1.0 - float(discount))
        energy_won[period] += value * rate * (1.0 - float(discount))
        discount_won[period] += value * rate * float(discount)
        season_of[period] = str(season)

    # 관측 최대수요 — 보고용. 경부하 슬롯도 들어간다.
    peaks = usage.kw.groupby(month_labels, observed=True).max()
    monthly_peaks = {_as_period(month): float(value) for month, value in peaks.items()}

    # 요금적용전력 대상 최대수요 — 경부하 제외 (요구사항서 5.2 ①).
    eligible = demand_eligible_mask(slots["band"], demand_bands=contract.demand_bands)
    demand_basis = monthly_demand_basis(usage.kw, month_labels, eligible)
    # 대상월 규칙 (5.2 ②) → 계약전력 하한 (5.2 ③)
    before_floor = billing_demands(
        demand_basis, prior_peaks=opts.prior_peaks, demand_months=contract.demand_months
    )
    # **하한은 제68조 ①을 타는 전압에서만 건다** (90세션). 그 항의 30% 는
    # 최대수요전력계 고객의 것이고, 계약전력 기준(제68조 ②) 고객에게는 없다 —
    # 교육용(갑)처럼 전압마다 기준이 갈리는 종별에서 이 가르기가 필요하다.
    # **결과에도 이 값을 싣는다** — 계약전력 조정(7.2)이 그것으로 목표를 잡으므로
    # 종별 값을 그대로 실으면 저압에서 없는 하한으로 목표를 세운다.
    floor_ratio = None if base_on_contract else contract.contract_floor_ratio
    demands = apply_contract_floor(
        before_floor,
        contract_kw=opts.contract_kw,
        floor_ratio=floor_ratio,
    )

    # 기본요금 기준 — 요금 데이터의 종별·전압 속성이 정한다 (제68조 ①·②).
    # 섞으면 기본요금이 통째로 틀리므로 계약전력이 없으면 계산하지 않는다.
    if base_on_contract:
        if opts.contract_kw is None:
            raise TariffDataError(
                f"{contract.label} 은 기본요금을 계약전력으로 매깁니다. "
                "BillingOptions(contract_kw=...) 로 계약전력을 주십시오 "
                "(한전 기본공급약관 제68조 제2항)."
            )
        base_demand = dict.fromkeys(months, float(opts.contract_kw))
    else:
        base_demand = dict(demands)

    slots_per_day = 1440 / interval
    counts = slots["month"].value_counts()
    covered_days = {
        _as_period(month): float(count) / slots_per_day for month, count in counts.items()
    }
    factors, partial_notes = _base_fee_factors(covered_days, opts.partial_month_policy)

    missing = (
        quality.monthly
        if quality is not None
        else monthly_missing(usage.kw, interval, threshold=opts.missing_limit_ratio)
    )
    missing_ratio = {_as_period(item.month): item.ratio for item in missing}

    # 역률요금 (제41·42·43조). 기본요금 총액에 대해 한 번 산출하고, 월별로는
    # 같은 비율을 그 달 기본요금에 곱한다.
    total_base_before_pf = sum(
        base_demand[month] * rates.base_won_per_kw * factors[month] for month in months
    )
    power_factor = power_factor_charge(
        total_base_before_pf,
        lagging_pct=opts.power_factor_pct,
        leading_pct=opts.leading_power_factor_pct,
    )
    power_factor_ratio = power_factor.total_ratio

    rows: list[dict[str, Any]] = []
    for month in months:
        month_mask = month_labels == month
        month_kw = usage.kw[month_mask]
        peak_kw = monthly_peaks.get(month, float("nan"))
        peak_at = pd.Timestamp(month_kw.idxmax()) if month_kw.notna().any() else pd.NaT
        ratio = missing_ratio.get(month, 0.0)
        limited = ratio > opts.missing_limit_ratio
        base_won = base_demand[month] * rates.base_won_per_kw * factors[month]
        # 역률요금은 그 달 기본요금에 대한 비율이다 (제43조). 부분 월 계수가 곱해진
        # 값에 붙이므로 부분 월도 자동으로 안분된다.
        power_factor_won = base_won * power_factor_ratio
        observed_energy_won = energy_won[month]
        adjusted_energy_won = observed_energy_won / (1.0 - ratio) if ratio < 1.0 else float("nan")
        rows.append(
            {
                "month": month,
                "season": season_of.get(month, table.season_of(month.month)),
                "days_in_month": month.days_in_month,
                "covered_days": covered_days[month],
                "is_partial": factors[month] < 1.0,
                "max_demand_kw": peak_kw,
                "max_demand_at": peak_at,
                "demand_basis_kw": demand_basis[month],
                "demand_before_floor_kw": before_floor[month],
                "billing_demand_kw": demands[month],
                "base_demand_kw": base_demand[month],  # 기본요금에 실제로 곱한 kW
                "base_fee_factor": factors[month],
                "base_won": base_won,
                "light_kwh": band_kwh[month]["light"],
                "mid_kwh": band_kwh[month]["mid"],
                "peak_kwh": band_kwh[month]["peak"],
                "total_kwh": sum(band_kwh[month].values()),
                "light_won": band_won[month]["light"],
                "mid_won": band_won[month]["mid"],
                "peak_won": band_won[month]["peak"],
                "discount_won": discount_won[month],
                "power_factor_won": power_factor_won,
                "energy_won": observed_energy_won,
                "energy_won_adjusted": adjusted_energy_won,
                "total_won": base_won + power_factor_won + observed_energy_won,
                "total_won_adjusted": base_won + power_factor_won + adjusted_energy_won,
                "missing_ratio": ratio,
                "demand_confidence": "신뢰 제한" if limited else "정상",
            }
        )

    monthly = pd.DataFrame(rows).set_index("month")

    # 부칙의 경과조치 — 일반용(갑)Ⅱ 는 부칙 (2026. 5. 22) 제2항 제1호다.
    # **고른 선택요금은 그대로 두고 그 기간에 청구되는 금액만 낮은 쪽으로 간다.**
    # 신청과 무관하게 걸리므로 고객이 아무것도 안 해도 이 값이 청구된다.
    #
    # 아래에서 자기를 다시 부르지만 **한 번에 멈춘다** — 짝은 한쪽 방향으로만
    # 적혀 있어(``{"I": "III", "II": "IV"}``) 짝의 짝이 없다.
    transition_months: tuple[pd.Period, ...] = ()
    transition = contract.transition
    counterpart_option = (
        transition.counterpart_of(selection.option) if transition is not None else None
    )
    covered = (
        [month for month in months if transition.covers(month.year, month.month)]
        if transition is not None and counterpart_option is not None
        else []
    )
    if covered and counterpart_option is not None:
        counterpart = calculate_bill(
            usage,
            table,
            TariffSelection(selection.contract_type, selection.voltage, counterpart_option),
            options=opts,
            quality=quality,
        )
        transition_months = _lower_of_counterpart(monthly, counterpart.monthly, covered)
        if transition_months:
            # 기본요금이 바뀌었으면 역률요금의 **기준**도 바뀐다 (제43조). 비율은
            # 역률만으로 정해지므로 그대로이고 금액만 다시 잡힌다 — 월별
            # ``power_factor_won`` 은 이미 짝의 것으로 갈아 끼워져 앞뒤가 맞는다.
            power_factor = power_factor_charge(
                float(monthly["base_won"].sum()),
                lagging_pct=opts.power_factor_pct,
                leading_pct=opts.leading_power_factor_pct,
            )

    limited_months = tuple(
        month for month in months if missing_ratio.get(month, 0.0) > opts.missing_limit_ratio
    )

    notices: list[Notice] = []
    if base_on_contract:
        # 계약전력 기준이다. 요금적용전력 하한·이월 규칙은 기본요금에 관여하지 않는다.
        # **기준이 잠정임을 먼저 밝힌다** — 뒤의 경고들은 그 전제 위에 있다.
        notices.append(
            warn(TENTATIVE_BASE_FEE_BASIS_WARNING, fact="tariff.tentative_base_fee_basis")
        )
        observed_peak = float(usage.kw.max())
        if observed_peak > (opts.contract_kw or 0.0):
            # 품질 점검이 내는 「계약전력 초과」 와 **같은 사실**이다.
            notices.append(
                warn(
                    f"{contract.label} 의 관측 최대수요 {observed_peak:,.1f} kW 가 "
                    f"계약전력 {opts.contract_kw:,.0f} kW 를 넘습니다. "
                    "초과사용부가금 대상이며, 계약전력 재산정이 필요할 수 있습니다.",
                    fact="quality.over_contract",
                )
            )
        threshold = contract.threshold_kw
        if threshold is not None and (opts.contract_kw or 0.0) >= threshold:
            notices.append(
                warn(
                    f"{contract.label} 은 계약전력 {threshold:,.0f} kW 미만 종별인데 "
                    f"계약전력이 {opts.contract_kw:,.0f} kW 입니다. 종별을 확인하십시오.",
                    fact="tariff.contract_type_threshold",
                )
            )
    elif contract.contract_floor_ratio is None:
        notices.append(
            basis(
                f"{contract.label} 의 요금적용전력 하한 비율이 요금 데이터에 없어 "
                "하한을 적용하지 않았습니다.",
                fact="tariff.floor_ratio_missing",
            )
        )
    elif opts.contract_kw is None:
        notices.append(
            warn(
                "계약전력을 주지 않아 요금적용전력 하한"
                f"(계약전력의 {contract.contract_floor_ratio:.0%})을 적용하지 않았습니다. "
                "저부하 사업장은 기본요금이 과소 산출됩니다.",
                fact="tariff.floor_no_contract",
            )
        )
    else:
        floor_kw = opts.contract_kw * contract.contract_floor_ratio
        bound = [month for month in months if before_floor[month] < floor_kw]
        if bound:
            notices.append(
                basis(
                    f"요금적용전력 하한 {floor_kw:,.1f} kW "
                    f"(계약전력의 {contract.contract_floor_ratio:.0%})가 "
                    f"{len(bound)}개 월에 걸렸습니다.",
                    fact="tariff.floor_bound_months",
                )
            )
        over = usage.kw.dropna()
        over_slots = int((over > opts.contract_kw).sum())
        if over_slots:
            notices.append(
                warn(
                    f"계약전력 {opts.contract_kw:,.0f} kW 를 넘은 구간이 {over_slots:,}건 "
                    "있습니다. 경부하 초과는 요금적용전력에 영향을 주지 않지만 "
                    "초과사용부가금 대상이므로 별도로 확인하십시오.",
                    fact="quality.over_contract",
                )
            )
    if not opts.prior_peaks and not base_on_contract:
        # **근거다.** 요금적용전력이 왜 그 값인지 설명한다 — 툴팁과 보고서로 간다.
        # 25세션에 코드 식별자(``prior_peaks=``)와 요구사항서 번호를 걷어냈다.
        # 사용자가 할 수 있는 일이 아닌 것을 시키지 않는다.
        notices.append(
            basis(
                "직전 12개월 최대수요 이력이 없어 첫 11개월의 요금적용전력이 과소 산출됩니다. "
                "올린 자료의 첫 달부터 이력을 쌓아 계산하기 때문입니다 "
                "(한전 기본공급약관 제68조).",
                fact="tariff.prior_peaks_missing",
            )
        )
    if transition_months and transition is not None and counterpart_option is not None:
        # **근거다.** 고른 선택요금의 단가로 검산하면 안 맞는 달이 생기므로,
        # 그 금액이 어디서 왔는지를 적는다. 조문 번호는 매뉴얼이 받는다.
        notices.append(
            basis(
                f"{billing_month_label(transition.first_billing_month)}부터 "
                f"{billing_month_label(transition.last_billing_month)}까지는 "
                f"{option_label(selection.option)}·{option_label(counterpart_option)} 중 "
                "낮은 요금이 신청 없이 적용되며, 분석 기간에서 "
                f"{len(transition_months)}개 월이 "
                f"{option_label(counterpart_option)} 단가로 계산됐습니다.",
                fact="tariff.transition_lower_of",
            )
        )
    notices.extend(power_factor.notices)  # 역률 (제41·43조)
    for month in limited_months:
        # **품질 점검이 내는 월별 결측률과 같은 사실**이다. 달이 판별자다.
        notices.append(
            warn(
                f"{month} 결측률 {missing_ratio[month]:.1%} — 최대수요를 '신뢰 제한' 으로 "
                "표시하고 전력량요금은 결측 보정 기준을 함께 봅니다.",
                fact=f"quality.month_missing_rate:{month}",
            )
        )
    period_days = usage.meta.period_days
    if period_days < 365:
        notices.append(
            warn(
                f"기간이 {period_days:.0f}일로 12개월 미만입니다. "
                "12개월 환산 시 경고를 붙이십시오.",
                fact="quality.short_period",
            )
        )
    if not table.verified:
        # 요금표의 **출처·검증 상태**다. 근거이지 경고가 아니다.
        #
        # **화면에서 뺐다** (50세션 4절). 출처는 신뢰의 문제라 매뉴얼과 보고서
        # 부록의 몫이고, 고칠 수 있는 자리인 **기준 데이터 화면**이 같은 사실을
        # 제 말로 이미 적는다 (12세션). 참고 등급이라 부록에는 그대로 실린다.
        notices.append(
            info(
                f"요금표 {table.effective_date} 는 공표 자료 그대로이며 아직 실제 "
                "청구서와 대조하지 않았습니다.",
                fact="tariff.unverified_table",
            )
        )

    demand_note = (
        (
            f"{contract.label} 의 기본요금은 **계약전력** "
            f"{opts.contract_kw:,.0f} kW 기준입니다 (한전 기본공급약관 제68조 제2항). "
            "요금적용전력은 참고용으로만 "
            "함께 싣습니다 — 피크를 낮춰도 계약전력을 낮추지 않으면 기본요금은 그대로입니다."
        )
        if base_on_contract
        else (
            "요금적용전력은 중간·최대부하 시간대의 최대수요만 대상으로 하며 "
            f"(경부하 제외), 대상월은 {'·'.join(str(m) for m in contract.demand_months)}월과 "
            "검침 당월입니다. 3~6월·10~11월 피크는 이월되지 않습니다 "
            "(한전 기본공급약관 제68조)."
        )
    )
    # 요금적용전력 규칙·안분 계수는 **근거**다 — 기본요금이 왜 그 값인지 그 자체다.
    notices += [
        basis(demand_note, fact="tariff.billing_demand_rule"),
        *partial_notes,
        basis(
            "전력량요금은 관측 기준이 정본이고, 결측 보정 기준은 회수기간 산정 "
            "참고용입니다. 도입 전후 차분(Δ)을 절대 금액보다 우선 신뢰하십시오.",
            fact="tariff.missing_adjusted_basis",
        ),
        # **참고** — 미포함 요금요소와 계절 비대칭. 전제 설명이다.
        info(NOT_INCLUDED_NOTICE, fact="tariff.not_included"),
        info(
            "봄·가을 피크 저감은 기본요금 절감 가치가 거의 없습니다. 태양광 발전이 "
            "가장 강한 계절이 봄·가을이므로, PV 의 기본요금 기여는 7~9월에 집중되고 "
            "12~2월에는 발전이 약해 비대칭이 큽니다.",
            fact="tariff.season_asymmetry",
        ),
    ]

    total_base = float(monthly["base_won"].sum())
    total_energy = float(monthly["energy_won"].sum())
    total_energy_adjusted = float(monthly["energy_won_adjusted"].sum())
    total_power_factor = float(monthly["power_factor_won"].sum())

    return BillingResult(
        monthly=monthly,
        selection=selection,
        contract_label=contract.label,
        voltage_label=contract.voltages[selection.voltage].label,
        tariff_label=table.label,
        effective_date=table.effective_date,
        base_rate_won_per_kw=rates.base_won_per_kw,
        contract_floor_ratio=floor_ratio,
        demand_months=contract.demand_months,
        contract_kw=opts.contract_kw,
        period_start=usage.meta.start,
        period_end=usage.meta.end,
        period_days=period_days,
        base_fee_months=float(sum(factors.values())),
        partial_month_policy=opts.partial_month_policy,
        billing_demand_kw=float(max(demands.values())),
        total_base_won=total_base,
        total_energy_won=total_energy,
        total_power_factor_won=total_power_factor,
        total_won=total_base + total_power_factor + total_energy,
        total_energy_won_adjusted=total_energy_adjusted,
        total_won_adjusted=total_base + total_power_factor + total_energy_adjusted,
        power_factor=power_factor,
        limited_months=limited_months,
        prior_peaks_supplied=bool(opts.prior_peaks),
        transition_months=transition_months,
        notices=tuple(notices),
    )
