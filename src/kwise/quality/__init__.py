"""데이터 품질 검사, 정전 검출, 부하 패턴 (요구사항서 4장·6.1).

    check_quality(usage)   품질 검사 일체 — 결측·편중·연속 공백·정전·이상치
    fill_missing(kw)       결측 처리. 기본은 미보간
    load_pattern(kw, ...)  부하 패턴 지표 (6.1). diagnose 가 호출한다

모두 순수 함수다. Streamlit 을 import 하지 않는다.
"""

from kwise.quality.checks import (
    DEFAULT_LOW_LOAD_KW,
    MISSING_RATIO_THRESHOLD,
    IntervalConsistency,
    OutlierSummary,
    QualityReport,
    check_quality,
)
from kwise.quality.fill import FillMethod, FillResult, fill_missing
from kwise.quality.missing import (
    DEFAULT_PEAK_HOURS,
    LONG_GAP_DAYS,
    MONTHLY_MISSING_THRESHOLD,
    PEAK_SKEW_THRESHOLD,
    MissingGap,
    MonthlyMissing,
    PeakHourSkew,
    find_missing_gaps,
    longest_gap,
    monthly_missing,
    peak_hour_skew,
)
from kwise.quality.outage import (
    DEFAULT_MIN_EVIDENCE,
    OutageEvent,
    detect_outages,
    outage_slot_mask,
)
from kwise.quality.pattern import (
    DEFAULT_NIGHT_HOURS,
    DEFAULT_OPERATING_HOURS,
    LoadPattern,
    load_pattern,
)

__all__ = [
    "DEFAULT_LOW_LOAD_KW",
    "DEFAULT_MIN_EVIDENCE",
    "DEFAULT_NIGHT_HOURS",
    "DEFAULT_OPERATING_HOURS",
    "DEFAULT_PEAK_HOURS",
    "LONG_GAP_DAYS",
    "MISSING_RATIO_THRESHOLD",
    "MONTHLY_MISSING_THRESHOLD",
    "PEAK_SKEW_THRESHOLD",
    "FillMethod",
    "FillResult",
    "IntervalConsistency",
    "LoadPattern",
    "MissingGap",
    "MonthlyMissing",
    "OutageEvent",
    "OutlierSummary",
    "PeakHourSkew",
    "QualityReport",
    "check_quality",
    "detect_outages",
    "fill_missing",
    "find_missing_gaps",
    "load_pattern",
    "longest_gap",
    "monthly_missing",
    "outage_slot_mask",
    "peak_hour_skew",
]
