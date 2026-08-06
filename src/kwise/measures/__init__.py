"""개선 수단 평가 (요구사항서 7장). **투자비 순으로 배치한다.**

    투자 0원  evaluate_tariff_switch()        7.1 선택요금 전환
              evaluate_contract_adjustment()  7.2 계약전력 조정
    투자      solar_curve()                   7.3 태양광 용량 곡선
              evaluate_ess()                  7.4 ESS 목표 피크 역산
              evaluate_surplus()              7.5 잉여 활용

절감액은 언제나 재계산이다. 수단을 적용한 15분 부하를 만들어
:func:`kwise.tariff.calculate_bill` 을 다시 부른다. 그 부하를 만드는 일은
:func:`apply_generation`·:func:`with_load` 가 맡는다.
"""

from kwise.measures.base import Certainty, annualize, lowest_certainty, payback_years
from kwise.measures.contract import (
    MARGIN_NOTICE,
    ContractAdjustment,
    ContractStatus,
    evaluate_contract_adjustment,
)
from kwise.measures.ess import (
    DEFAULT_DOD,
    DEFAULT_PAYBACK_TARGET_YEARS,
    DEFAULT_ROUND_TRIP,
    DispatchResult,
    EssResult,
    PeakExcess,
    analyze_peak_excess,
    dispatch_peak_shaving,
    evaluate_ess,
    excess_slots_by_day,
    excess_table,
    light_band_mask,
    size_for_target,
)
from kwise.measures.netload import NetLoad, apply_generation, with_load
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
    "DEFAULT_DOD",
    "DEFAULT_MODULE_DENSITY_KWP_PER_M2",
    "DEFAULT_PAYBACK_TARGET_YEARS",
    "DEFAULT_ROUND_TRIP",
    "DEFAULT_STEPS",
    "DEFAULT_USABLE_RATIO",
    "ELIGIBILITY_NOTICE",
    "MARGIN_NOTICE",
    "POWER_FACTOR_FLOOR_PCT",
    "Certainty",
    "ContractAdjustment",
    "ContractStatus",
    "DispatchResult",
    "EssResult",
    "NetLoad",
    "OptionQuote",
    "PeakExcess",
    "SolarCurve",
    "SolarPoint",
    "SurplusResult",
    "SurplusScenario",
    "TariffSwitchResult",
    "analyze_peak_excess",
    "annualize",
    "apply_generation",
    "dispatch_peak_shaving",
    "evaluate_contract_adjustment",
    "evaluate_ess",
    "evaluate_surplus",
    "evaluate_tariff_switch",
    "excess_slots_by_day",
    "excess_table",
    "light_band_mask",
    "lowest_certainty",
    "payback_years",
    "power_factor_after_pct",
    "roof_capacity_limit_kwp",
    "size_for_target",
    "solar_curve",
    "unit_generation_kw",
    "with_load",
]
