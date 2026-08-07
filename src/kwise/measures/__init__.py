"""개선 수단 평가 (요구사항서 7장). **투자비 순으로 배치한다.**

    투자 0원  evaluate_tariff_switch()        7.1 선택요금 전환
              evaluate_contract_adjustment()  7.2 계약전력 조정
              evaluate_demand_response()      7.3 경제성DR 참여
    소액 투자 evaluate_power_factor()         7.4 역률 개선 (콘덴서·APFR)
    투자      solar_curve()                   7.5 태양광 용량 곡선
              evaluate_ess()                  7.6 ESS 목표 피크 역산
              evaluate_surplus()              7.7 잉여 활용

절감액은 언제나 재계산이다. 수단을 적용한 15분 부하를 만들어
:func:`kwise.tariff.calculate_bill` 을 다시 부른다. 그 부하를 만드는 일은
:func:`apply_generation`·:func:`with_load` 가 맡는다.
"""

from kwise.measures.arbitrage import (
    DEFAULT_CYCLES_PER_DAY,
    ArbitrageValue,
    SeasonSpread,
    arbitrage_value,
    c_rate,
    peak_days_by_season,
)
from kwise.measures.base import Certainty, annualize, lowest_certainty, payback_years
from kwise.measures.contract import (
    MARGIN_NOTICE,
    ContractAdjustment,
    ContractStatus,
    evaluate_contract_adjustment,
)
from kwise.measures.demand_response import (
    DEFAULT_BID_HOURS_PER_DAY,
    DR_ADVISORY,
    DemandResponseResult,
    evaluate_demand_response,
    shortfall_penalty_won,
)
from kwise.measures.ess import (
    DEFAULT_DOD,
    DEFAULT_PAYBACK_TARGET_YEARS,
    DEFAULT_ROUND_TRIP,
    HIGH_RATE_DISCHARGE_HOURS,
    DispatchResult,
    EssResult,
    PeakExcess,
    analyze_peak_excess,
    dispatch_peak_shaving,
    ess_payback_curve,
    evaluate_ess,
    excess_slots_by_day,
    excess_table,
    light_band_mask,
    required_discharge_hours,
    size_for_target,
)
from kwise.measures.ess_cost import (
    EssCostInput,
    EssCostReference,
    EssCostReferenceError,
    EssTechnologyCost,
    load_ess_cost_reference,
    reference_table,
)
from kwise.measures.netload import NetLoad, apply_generation, with_load
from kwise.measures.power_factor import (
    DEFAULT_TARGET_PCT,
    PowerFactorResult,
    evaluate_power_factor,
)
from kwise.measures.solar import (
    DEFAULT_MODULE_DENSITY_KWP_PER_M2,
    DEFAULT_STEPS,
    DEFAULT_USABLE_RATIO,
    POWER_FACTOR_FLOOR_PCT,
    SolarCurve,
    SolarPoint,
    power_factor_after_pct,
    roof_capacity_limit_kwp,
    solar_curve,
    unit_generation_kw,
)
from kwise.measures.surplus import (
    ELIGIBILITY_NOTICE,
    SurplusResult,
    SurplusScenario,
    evaluate_surplus,
)
from kwise.measures.tariff_switch import (
    OptionQuote,
    TariffSwitchResult,
    evaluate_tariff_switch,
)

__all__ = [
    "DEFAULT_BID_HOURS_PER_DAY",
    "DEFAULT_CYCLES_PER_DAY",
    "DEFAULT_DOD",
    "DEFAULT_MODULE_DENSITY_KWP_PER_M2",
    "DEFAULT_PAYBACK_TARGET_YEARS",
    "DEFAULT_ROUND_TRIP",
    "DEFAULT_STEPS",
    "DEFAULT_TARGET_PCT",
    "DEFAULT_USABLE_RATIO",
    "DR_ADVISORY",
    "ELIGIBILITY_NOTICE",
    "HIGH_RATE_DISCHARGE_HOURS",
    "MARGIN_NOTICE",
    "POWER_FACTOR_FLOOR_PCT",
    "ArbitrageValue",
    "Certainty",
    "ContractAdjustment",
    "ContractStatus",
    "DemandResponseResult",
    "DispatchResult",
    "EssCostInput",
    "EssCostReference",
    "EssCostReferenceError",
    "EssResult",
    "EssTechnologyCost",
    "NetLoad",
    "OptionQuote",
    "PeakExcess",
    "PowerFactorResult",
    "SeasonSpread",
    "SolarCurve",
    "SolarPoint",
    "SurplusResult",
    "SurplusScenario",
    "TariffSwitchResult",
    "analyze_peak_excess",
    "annualize",
    "apply_generation",
    "arbitrage_value",
    "c_rate",
    "dispatch_peak_shaving",
    "ess_payback_curve",
    "evaluate_contract_adjustment",
    "evaluate_demand_response",
    "evaluate_ess",
    "evaluate_power_factor",
    "evaluate_surplus",
    "evaluate_tariff_switch",
    "excess_slots_by_day",
    "excess_table",
    "light_band_mask",
    "load_ess_cost_reference",
    "lowest_certainty",
    "payback_years",
    "peak_days_by_season",
    "power_factor_after_pct",
    "reference_table",
    "required_discharge_hours",
    "roof_capacity_limit_kwp",
    "shortfall_penalty_won",
    "size_for_target",
    "solar_curve",
    "unit_generation_kw",
    "with_load",
]
