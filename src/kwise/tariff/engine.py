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

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

import pandas as pd

from kwise.io import UsageData
from kwise.quality import QualityReport, monthly_missing
from kwise.tariff.demand import (
    apply_contract_floor,
    billing_demands,
    demand_eligible_mask,
    demand_window_months,
    monthly_demand_basis,
)
from kwise.tariff.holiday import DateLike, build_calendar
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
    "MISSING_LIMIT_RATIO",
    "NOT_INCLUDED_NOTICE",
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
    warnings: tuple[str, ...] = field(default=())


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
    warnings: tuple[str, ...] = field(default=())
    notes: tuple[str, ...] = field(default=())

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
                else "야간(22~08시) 지상 간주 100% (약관 제43조 ② 2호 나목)"
            ),
            f"산출 기간: {self.period_label}",
        )

    def annualize(self) -> AnnualEstimate:
        """12개월로 환산한다. 12개월 미만이면 경고를 붙인다."""
        if self.base_fee_months <= 0:
            raise ValueError("기본요금 개월수가 0 이라 환산할 수 없습니다.")
        factor = demand_window_months() / self.base_fee_months
        warnings: list[str] = []
        if self.period_days < 365:
            warnings.append(
                f"기간이 {self.period_days:.0f}일로 12개월 미만입니다. "
                f"×{factor:.3f} 환산값은 계절 편중이 있어 신뢰도가 낮습니다."
            )
        return AnnualEstimate(
            factor=factor,
            base_won=(self.total_base_won + self.total_power_factor_won) * factor,
            energy_won=self.total_energy_won * factor,
            total_won=self.total_won * factor,
            energy_won_adjusted=self.total_energy_won_adjusted * factor,
            total_won_adjusted=self.total_won_adjusted * factor,
            warnings=tuple(warnings),
        )


# --------------------------------------------------------------------- 요금적용전력


def _as_period(value: PeriodLike) -> pd.Period:
    if isinstance(value, pd.Period):
        return value
    return pd.Period(pd.Timestamp(value), freq="M")


# --------------------------------------------------------------------- 부분 월 (5.5)


def _base_fee_factors(
    covered_days: Mapping[pd.Period, float], policy: PartialMonthPolicy
) -> tuple[dict[pd.Period, float], list[str]]:
    """부분 월의 기본요금 배분 계수. 13개월분을 부과하지 않기 위한 것이다."""
    factors: dict[pd.Period, float] = {}
    partials: list[pd.Period] = []
    for month, days in covered_days.items():
        full = float(month.days_in_month)
        if days < full - 1e-9:
            partials.append(month)
        factors[month] = 1.0

    notes: list[str] = []
    if not partials:
        return factors, notes

    if policy == "merge" and len(partials) == 2:
        total = sum(covered_days[month] for month in partials)
        for month in partials:
            factors[month] = covered_days[month] / total if total else 0.0
        notes.append(
            "부분 월 처리: 두 조각을 합쳐 한 달로 계산했습니다 "
            + " + ".join(f"{month} {covered_days[month]:.1f}일" for month in partials)
            + " = 1개월."
        )
        return factors, notes

    for month in partials:
        factors[month] = covered_days[month] / float(month.days_in_month)
    if policy == "merge":
        notes.append(
            "부분 월 처리: 합칠 짝이 없어 일수 비례로 안분했습니다 "
            + ", ".join(f"{month} {factors[month]:.3f}" for month in partials)
            + "."
        )
    else:
        notes.append(
            "부분 월 처리: 일수 비례로 안분했습니다 "
            + ", ".join(f"{month} {factors[month]:.3f}" for month in partials)
            + "."
        )
    return factors, notes


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

    energy = usage.energy_kwh()  # 그리드 이탈분이 포함된 시계열
    frame = pd.DataFrame(
        {
            "month": slots["month"].to_numpy(),
            "season": slots["season"].to_numpy(),
            "band": slots["band"].to_numpy(),
            "discount": slots["discount_rate"].to_numpy(),
            "kwh": energy.to_numpy(dtype=float),
        }
    )
    grouped = frame.groupby(["month", "season", "band", "discount"], observed=True)["kwh"].sum()

    months = sorted({_as_period(month) for month in slots["month"]})
    band_kwh: dict[pd.Period, dict[str, float]] = {
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
        energy_won[period] += value * rate * (1.0 - float(discount))
        discount_won[period] += value * rate * float(discount)
        season_of[period] = str(season)

    # 관측 최대수요 — 보고용. 경부하 슬롯도 들어간다.
    peaks = usage.kw.groupby(slots["month"].to_numpy(), observed=True).max()
    monthly_peaks = {_as_period(month): float(value) for month, value in peaks.items()}

    # 요금적용전력 대상 최대수요 — 경부하 제외 (요구사항서 5.2 ①).
    eligible = demand_eligible_mask(slots["band"], demand_bands=contract.demand_bands)
    basis = monthly_demand_basis(usage.kw, slots["month"], eligible)
    # 대상월 규칙 (5.2 ②) → 계약전력 하한 (5.2 ③)
    before_floor = billing_demands(
        basis, prior_peaks=opts.prior_peaks, demand_months=contract.demand_months
    )
    demands = apply_contract_floor(
        before_floor,
        contract_kw=opts.contract_kw,
        floor_ratio=contract.contract_floor_ratio,
    )

    # 기본요금 기준 — 갑 종별은 계약전력, 을 종별은 요금적용전력이다.
    # 섞으면 기본요금이 통째로 틀리므로 계약전력이 없으면 계산하지 않는다.
    if contract.base_fee_on_contract:
        if opts.contract_kw is None:
            raise TariffDataError(
                f"{contract.label} 은 기본요금을 계약전력으로 매깁니다. "
                "BillingOptions(contract_kw=...) 로 계약전력을 주십시오 "
                "(한전 기본공급약관 제68조)."
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
        month_mask = slots["month"].to_numpy() == month
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
                "demand_basis_kw": basis[month],
                "demand_before_floor_kw": before_floor[month],
                "billing_demand_kw": demands[month],
                "base_demand_kw": base_demand[month],  # 기본요금에 실제로 곱한 kW
                "base_fee_factor": factors[month],
                "base_won": base_won,
                "light_kwh": band_kwh[month]["light"],
                "mid_kwh": band_kwh[month]["mid"],
                "peak_kwh": band_kwh[month]["peak"],
                "total_kwh": sum(band_kwh[month].values()),
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
    limited_months = tuple(
        month for month in months if missing_ratio.get(month, 0.0) > opts.missing_limit_ratio
    )

    warnings: list[str] = []
    if contract.base_fee_on_contract:
        # 갑 종별이다. 요금적용전력 하한·이월 규칙은 기본요금에 관여하지 않는다.
        observed_peak = float(usage.kw.max())
        if observed_peak > (opts.contract_kw or 0.0):
            warnings.append(
                f"{contract.label} 의 관측 최대수요 {observed_peak:,.1f} kW 가 "
                f"계약전력 {opts.contract_kw:,.0f} kW 를 넘습니다. "
                "초과사용부가금 대상이며, 계약전력 재산정이 필요할 수 있습니다."
            )
        threshold = contract.threshold_kw
        if threshold is not None and (opts.contract_kw or 0.0) >= threshold:
            warnings.append(
                f"{contract.label} 은 계약전력 {threshold:,.0f} kW 미만 종별인데 "
                f"계약전력이 {opts.contract_kw:,.0f} kW 입니다. 종별을 확인하십시오."
            )
    elif contract.contract_floor_ratio is None:
        warnings.append(
            f"{contract.label} 의 요금적용전력 하한 비율이 요금 데이터에 없어 "
            "하한을 적용하지 않았습니다 (요구사항서 5.2 ③)."
        )
    elif opts.contract_kw is None:
        warnings.append(
            "계약전력을 주지 않아 요금적용전력 하한"
            f"(계약전력의 {contract.contract_floor_ratio:.0%})을 적용하지 않았습니다. "
            "저부하 사업장은 기본요금이 과소 산출됩니다 (요구사항서 5.2 ③)."
        )
    else:
        floor_kw = opts.contract_kw * contract.contract_floor_ratio
        bound = [month for month in months if before_floor[month] < floor_kw]
        if bound:
            warnings.append(
                f"요금적용전력 하한 {floor_kw:,.1f} kW "
                f"(계약전력의 {contract.contract_floor_ratio:.0%})가 "
                f"{len(bound)}개 월에 걸렸습니다."
            )
        over = usage.kw.dropna()
        over_slots = int((over > opts.contract_kw).sum())
        if over_slots:
            warnings.append(
                f"계약전력 {opts.contract_kw:,.0f} kW 를 넘은 구간이 {over_slots:,}건 "
                "있습니다. 경부하 초과는 요금적용전력에 영향을 주지 않지만 "
                "초과사용부가금 대상이므로 별도로 확인하십시오 (요구사항서 5.2)."
            )
    if not opts.prior_peaks and not contract.base_fee_on_contract:
        warnings.append(
            "직전 12개월 최대수요 이력이 없어 첫 11개월의 요금적용전력이 과소 산출됩니다. "
            "청구서를 확보하면 prior_peaks= 로 주입하십시오 (요구사항서 5.2)."
        )
    warnings.extend(power_factor.warnings)  # 역률 (제41·43조)
    for month in limited_months:
        warnings.append(
            f"{month} 결측률 {missing_ratio[month]:.1%} — 최대수요를 '신뢰 제한' 으로 "
            "표시하고 전력량요금은 결측 보정 기준을 함께 봅니다 (요구사항서 5.4)."
        )
    period_days = usage.meta.period_days
    if period_days < 365:
        warnings.append(
            f"기간이 {period_days:.0f}일로 12개월 미만입니다. 12개월 환산 시 경고를 붙이십시오."
        )
    if not table.verified:
        warnings.append(
            f"요금표 {table.effective_date} 는 아직 청구서로 검증되지 않았습니다 (verified=false)."
        )

    demand_note = (
        (
            f"{contract.label} 의 기본요금은 **계약전력** "
            f"{opts.contract_kw:,.0f} kW 기준입니다 (갑 종별). 요금적용전력은 참고용으로만 "
            "함께 싣습니다 — 피크를 낮춰도 계약전력을 낮추지 않으면 기본요금은 그대로입니다."
        )
        if contract.base_fee_on_contract
        else (
            "요금적용전력은 중간·최대부하 시간대의 최대수요만 대상으로 하며 "
            f"(경부하 제외), 대상월은 {'·'.join(str(m) for m in contract.demand_months)}월과 "
            "검침 당월입니다. 3~6월·10~11월 피크는 이월되지 않습니다 (요구사항서 5.2)."
        )
    )
    notes = [
        NOT_INCLUDED_NOTICE,
        demand_note,
        (
            "봄·가을 피크 저감은 기본요금 절감 가치가 거의 없습니다. 태양광 발전이 "
            "가장 강한 계절이 봄·가을이므로, PV 의 기본요금 기여는 7~9월에 집중되고 "
            "12~2월에는 발전이 약해 비대칭이 큽니다."
        ),
        *partial_notes,
        *power_factor.notes,
        "전력량요금은 관측 기준이 정본이고, 결측 보정 기준은 회수기간 산정 참고용입니다 "
        "(요구사항서 5.4). 도입 전후 차분(Δ)을 절대 금액보다 우선 신뢰하십시오.",
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
        contract_floor_ratio=contract.contract_floor_ratio,
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
        warnings=tuple(warnings),
        notes=tuple(notes),
    )
