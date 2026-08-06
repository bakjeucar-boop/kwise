"""요금 엔진, 시간대 분류 (요구사항서 5장·부록 A).

    load_tariff()        요금 데이터 읽기. 단가는 절대 하드코딩하지 않는다
    validate_tariff()    부록 A.2 의 구조적 검증 (수기 입력 오타 검출)
    build_calendar()     공휴일 달력. 일요일도 공휴일로 계량한다
    classify_slots()     계절·시간대·요일 분류. 귀속은 구간 시작 시각 기준
    calculate_bill()     기본요금 + 전력량요금

모두 순수 함수다. Streamlit 을 import 하지 않는다.
"""

from kwise.tariff.demand import (
    DEFAULT_CONTRACT_FLOOR_RATIO,
    DEFAULT_DEMAND_BANDS,
    DEFAULT_DEMAND_MONTHS,
    apply_contract_floor,
    billing_demands,
    demand_eligible_mask,
    is_demand_month,
    monthly_demand_basis,
)
from kwise.tariff.engine import (
    DEMAND_WINDOW_MONTHS,
    MISSING_LIMIT_RATIO,
    NOT_INCLUDED_NOTICE,
    AnnualEstimate,
    BillingOptions,
    BillingResult,
    PartialMonthPolicy,
    calculate_bill,
)
from kwise.tariff.holiday import (
    DEFAULT_COUNTRY,
    HolidayCalendar,
    build_calendar,
)
from kwise.tariff.schema import (
    BANDS,
    DEFAULT_REGION_GROUP,
    ContractType,
    DayRules,
    EnergyRates,
    OptionRates,
    SpecialRule,
    TariffDataError,
    TariffSelection,
    TariffTable,
    VoltageRates,
    available_tariff_files,
    default_tariff_dir,
    list_contract_types,
    list_options,
    list_selections,
    list_voltages,
    load_tariff,
    parse_tariff,
)
from kwise.tariff.tou import Band, DayType, classify_slots
from kwise.tariff.validate import (
    DEFAULT_POLICY,
    DEFAULT_UNIFORM_TOLERANCE,
    OptionPairPolicy,
    ValidationFinding,
    option_pair_diffs,
    validate_tariff,
)

__all__ = [
    "BANDS",
    "DEFAULT_CONTRACT_FLOOR_RATIO",
    "DEFAULT_COUNTRY",
    "DEFAULT_DEMAND_BANDS",
    "DEFAULT_DEMAND_MONTHS",
    "DEFAULT_POLICY",
    "DEFAULT_REGION_GROUP",
    "DEFAULT_UNIFORM_TOLERANCE",
    "DEMAND_WINDOW_MONTHS",
    "MISSING_LIMIT_RATIO",
    "NOT_INCLUDED_NOTICE",
    "AnnualEstimate",
    "Band",
    "BillingOptions",
    "BillingResult",
    "ContractType",
    "DayRules",
    "DayType",
    "EnergyRates",
    "HolidayCalendar",
    "OptionPairPolicy",
    "OptionRates",
    "PartialMonthPolicy",
    "SpecialRule",
    "TariffDataError",
    "TariffSelection",
    "TariffTable",
    "ValidationFinding",
    "VoltageRates",
    "apply_contract_floor",
    "available_tariff_files",
    "billing_demands",
    "build_calendar",
    "calculate_bill",
    "classify_slots",
    "default_tariff_dir",
    "demand_eligible_mask",
    "is_demand_month",
    "list_contract_types",
    "list_options",
    "list_selections",
    "list_voltages",
    "load_tariff",
    "monthly_demand_basis",
    "option_pair_diffs",
    "parse_tariff",
    "validate_tariff",
]
