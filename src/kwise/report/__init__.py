"""Excel 출력, CLI 배치 (요구사항서 10.3·10.4).

    export_report()      여덟 장짜리 통합문서를 ``output\\`` 에 저장
    run_batch()          YAML 케이스를 순차 실행

저장 직전 tz 를 해제하고, 파일명에 날짜·시각 접미사를 붙인다.
"""

from kwise.report.batch import (
    BatchConfig,
    BatchResult,
    CaseSpec,
    CaseSummary,
    load_batch_config,
    run_batch,
    run_case,
    summary_frame,
)
from kwise.report.excel import (
    DEFAULT_OUTPUT_DIR,
    NO_PV_SENSITIVITY_NOTE,
    SHEET_ORDER,
    ReportSections,
    ReportWriteError,
    build_sheets,
    export_report,
    measure_summary_frame,
    no_pv_sensitivity_frame,
    result_path,
    strip_timezone,
    write_workbook,
)
from kwise.report.notices import (
    CONTRACT_CHANGE_WARNING,
    KNOWN_LIMITS,
    NOT_INCLUDED_NOTICE,
    UNPRICED_REASONS,
    format_won,
)

__all__ = [
    "CONTRACT_CHANGE_WARNING",
    "DEFAULT_OUTPUT_DIR",
    "KNOWN_LIMITS",
    "NOT_INCLUDED_NOTICE",
    "NO_PV_SENSITIVITY_NOTE",
    "SHEET_ORDER",
    "UNPRICED_REASONS",
    "BatchConfig",
    "BatchResult",
    "CaseSpec",
    "CaseSummary",
    "ReportSections",
    "ReportWriteError",
    "build_sheets",
    "export_report",
    "format_won",
    "load_batch_config",
    "measure_summary_frame",
    "no_pv_sensitivity_frame",
    "result_path",
    "run_batch",
    "run_case",
    "strip_timezone",
    "summary_frame",
    "write_workbook",
]
