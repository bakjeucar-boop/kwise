"""진단 — 부하 패턴, 피크 특성, 요금 구조, 계약 적정성 (요구사항서 6장).

    diagnose(usage, table, contract)   진단 한 벌
    peak_profile()                     6.2 피크 특성
    charge_structure()                 6.3 요금 구조 (tariff 호출)
    assess_contract()                  6.4 계약전력 적정성
    judge_pv_potential()               6.5 태양광 피크 기여 가능성

**설비 정보를 받지 않는다.** 부하 패턴(6.1)은 2세션 `quality.load_pattern` 을
호출만 한다.
"""

from kwise.diagnose.contract import (
    DEFAULT_MARGIN_RATIO,
    DEFAULT_POWER_FACTOR_PCT,
    ContractAdequacy,
    ContractInfo,
    assess_contract,
)
from kwise.diagnose.peak import DEFAULT_TOP_N, PeakProfile, peak_profile
from kwise.diagnose.report import Diagnosis, diagnose
from kwise.diagnose.structure import ChargeStructure, charge_structure
from kwise.diagnose.summary import (
    DEFAULT_HIGH_SHARE,
    DEFAULT_MEDIUM_SHARE,
    MIDDAY_HOURS,
    ImprovementSummary,
    PvPotential,
    build_lines,
    judge_pv_potential,
    pv_basis_label,
)

__all__ = [
    "DEFAULT_HIGH_SHARE",
    "DEFAULT_MARGIN_RATIO",
    "DEFAULT_MEDIUM_SHARE",
    "DEFAULT_POWER_FACTOR_PCT",
    "DEFAULT_TOP_N",
    "MIDDAY_HOURS",
    "ChargeStructure",
    "ContractAdequacy",
    "ContractInfo",
    "Diagnosis",
    "ImprovementSummary",
    "PeakProfile",
    "PvPotential",
    "assess_contract",
    "build_lines",
    "charge_structure",
    "diagnose",
    "judge_pv_potential",
    "peak_profile",
    "pv_basis_label",
]
