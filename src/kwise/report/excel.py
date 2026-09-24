"""Excel 출력 (요구사항서 10.3).

시트 구성은 요약 / 진단 / 월별 집계 / 15분 시계열 / 요금 계산 명세 / 수단별 결과 /
조합 비교 / 감도 / 감도 상세 아홉 장이다.

**저장 직전 tz-aware 컬럼을 반드시 해제한다.** pvlib 결과는 항상 tz-aware 이고,
openpyxl 은 tz 가 붙은 시각을 쓰지 못해 ValueError 를 낸다.

**파일명에 날짜·시각 접미사를 붙인다.** Excel 이 파일을 열고 있으면 덮어쓰기가
실패하기 때문이다. 그래도 실패하면 "Excel 에서 파일을 닫아 주세요" 를 안내한다.
"""

from __future__ import annotations

import datetime as dt
import itertools
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from kwise import money
from kwise.compare import (
    SCENARIO_NAME_CAVEAT,
    SENSITIVITY_NOTE,
    ComparisonResult,
    sensitivity_range_frame,
)
from kwise.compare.sensitivity import (
    ANNUAL_SAVING,
    BASE_SAVING,
    ENERGY_SAVING,
    METRIC_LABELS,
    SAVING,
)
from kwise.diagnose import Diagnosis
from kwise.diagnose.dr import JUDGE_WINDOW
from kwise.io import UsageData
from kwise.measures import (
    DR_ADVISORY,
    MARGIN_FACT,
    NO_HEADROOM_LABEL,
    NO_SAVING,
    OFFSET_SCENARIO,
    ContractAdjustment,
    DemandResponseResult,
    EssResult,
    PowerFactorResult,
    SolarCurve,
    SolarPoint,
    SurplusResult,
    TariffSwitchResult,
    annualize,
    payback_label,
    payback_years,
)
from kwise.notices import Notice, dedupe
from kwise.report import narrative
from kwise.report.appendix import basis_data_frame, known_limits, worksheet_frame
from kwise.report.columns import display_frame, localize, season_label, value_label
from kwise.report.notices import (
    CONTRACT_CHANGE_WARNING,
    DATA_SOURCES,
    KNOWN_LIMITS,
    NOT_INCLUDED_NOTICE,
    TRUNCATION_FOOTNOTE,
    UNPRICED_REASONS,
    Peers,
    bill_lines,
    billing_demand_text,
    combination_annual_saving,
    combination_saving,
    contract_annual_saving,
    contract_saving,
    ess_capacity_text,
    format_mwh,
    format_won,
    max_demand_text,
    power_factor_charges,
    rules_basis_line,
    solar_lines,
    surplus_kwh_text,
    switch_annual_saving,
    switch_saving,
)
from kwise.report.worksheet import Worksheet, low_load_threshold_line
from kwise.tariff import BillingResult, TariffTable
from kwise.tariff.labels import option_label

if TYPE_CHECKING:
    from openpyxl.worksheet.worksheet import Worksheet as Sheet

__all__ = [
    "DEFAULT_OUTPUT_DIR",
    "MEASURE_SHEET_COLUMNS",
    "NO_PV_SENSITIVITY_NOTE",
    "SHEET_ORDER",
    "ReportSections",
    "ReportWriteError",
    "build_sheets",
    "export_report",
    "measure_summary_frame",
    "no_pv_sensitivity_frame",
    "result_path",
    "solar_curve_sheet",
    "strip_timezone",
    "truncate_money_columns",
    "write_workbook",
]

NO_PV_SENSITIVITY_NOTE = (
    "태양광이 없어 감도를 적용할 항목이 없습니다. 감도는 PV 출력의 첨예도에만 적용하며, "
    "요금제 전환·계약전력 조정·역률 개선은 확정 계산이라 감도를 쓰지 않습니다."
)

# 「수단별 결과」 시트의 열. 켠 수단이 없어도 이 구조는 유지한다. 절감 열은 곁에
# 12개월 환산 열이 서므로 「기간」 을 단다 (S219 규칙 다).
MEASURE_SHEET_COLUMNS: tuple[str, ...] = (
    "수단",
    "투자비(원)",
    "기간 절감액(원)",
    "12개월 환산(원)",
    "회수기간",
    "비고",
)
# **확실성 열을 뺐다** (53세션 1-4). 화면은 28세션에 걷어냈는데 산출물에만 남아
# 있었다. 계산은 그대로다 — :class:`~kwise.measures.Certainty` 가 살아 있어
# 되살릴 때 열만 되돌리면 된다.

DEFAULT_OUTPUT_DIR = Path("output")
SHEET_ORDER: tuple[str, ...] = (
    "요약",
    "진단",
    "월별 집계",
    "15분 시계열",
    "요금 계산 명세",
    "수단별 결과",
    "태양광 용량 곡선",
    "조합 비교",
    "감도",
    "감도 상세",
    # **부록 셋** (22세션 3절). Word 와 같은 것을 싣는다 — 만드는 자리도 하나다.
    "부록 A 산출 근거",
    "부록 B 기준 데이터",
    "부록 C 한계와 전제",
)
_CLOSE_EXCEL = "Excel 에서 파일을 닫아 주세요."


class ReportWriteError(RuntimeError):
    """Excel 파일을 쓰지 못했을 때 발생한다."""


def strip_timezone(frame: pd.DataFrame) -> pd.DataFrame:
    """tz-aware 컬럼과 인덱스의 tz 를 해제한다. Excel 은 tz 를 쓰지 못한다."""
    result = frame.copy()
    for column in result.columns:
        if isinstance(result[column].dtype, pd.DatetimeTZDtype):
            result[column] = result[column].dt.tz_localize(None)
    index = result.index
    if isinstance(index, pd.DatetimeIndex) and index.tz is not None:
        result.index = index.tz_localize(None)
    if isinstance(index, pd.PeriodIndex):
        result.index = index.astype(str)
    return result


def result_path(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    prefix: str = "result",
    now: dt.datetime | None = None,
) -> Path:
    """``result_YYYYMMDD_HHMM.xlsx``. 접미사가 없으면 덮어쓰기 충돌이 난다."""
    stamp = (now if now is not None else dt.datetime.now()).strftime("%Y%m%d_%H%M")
    return output_dir / f"{prefix}_{stamp}.xlsx"


#: 수 칸의 소수 자리 — **열 이름 꼬리로 가른다. 위에서부터 처음 맞는 것** (S235 ①).
#: 사실의 글자를 만드는 함수가 있으면 그 자릿수다 — 요금적용전력은
#: :func:`~kwise.report.notices.billing_demand_text` · 최대수요는
#: :func:`~kwise.report.notices.max_demand_text` · kWh 는
#: :func:`~kwise.report.notices.surplus_kwh_text` · 원은 :func:`kwise.money.won`.
EXCEL_DECIMALS: tuple[tuple[str, int], ...] = (
    ("요금적용전력(kW)", 0),
    ("기준전력(kW)", 0),
    ("(kW)", 1),
    ("(kWh)", 0),
    ("(kWp)", 0),
    ("(원)", 0),
    ("(년)", 1),
    ("(%)", 1),
)


def _tail_decimals(name: object) -> int | None:
    label = str(name)
    return next((digits for tail, digits in EXCEL_DECIMALS if label.endswith(tail)), None)


def _places(value: float) -> int:
    text = repr(float(value))
    return 0 if "e" in text else len(text.partition(".")[2].rstrip("0"))


def _number_formats(sheet: Sheet) -> None:
    """수 칸에 **셀 서식으로** 세 자리 쉼표와 열마다 한 소수 자리를 단다 (S235 ①).

    **셀의 수는 그대로다** — 서식만 달므로 값 · 합 · 정렬이 안 바뀐다. 꼬리가 없는
    열은 사실마다 행이 갈리는 시트(「지표」 열)면 행 사실의 최빈, 아니면 열 안에서
    소수가 있는 칸의 최빈이다 — 정수 칸은 어느 자리로 적어도 안 잃으므로 안 센다.
    """
    heads = [cell.value for cell in sheet[1]]
    facts = heads.index("지표") if "지표" in heads else None
    for column in sheet.iter_cols(min_row=2):
        cells = [
            cell
            for cell in column
            if isinstance(cell.value, int | float) and not isinstance(cell.value, bool)
        ]
        if not cells:
            continue
        decimals = _tail_decimals(heads[cells[0].column - 1])
        if decimals is None and facts is not None:
            found = [_tail_decimals(sheet.cell(cell.row, facts + 1).value) for cell in cells]
            common = Counter(d for d in found if d is not None).most_common(1)
            decimals = common[0][0] if common else None
        if decimals is None:
            fractions = Counter(p for p in map(_places, (c.value for c in cells)) if p)
            decimals = fractions.most_common(1)[0][0] if fractions else 0
        pattern = "#,##0" + ("." + "0" * decimals if decimals else "")
        for cell in cells:
            cell.number_format = pattern


def write_workbook(sheets: dict[str, pd.DataFrame], path: Path) -> Path:
    """시트를 하나의 통합문서로 쓴다. 시트마다 tz 를 해제하고 수 칸에 서식을 단다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            for name, frame in sheets.items():
                strip_timezone(frame).to_excel(writer, sheet_name=name[:31])
                _number_formats(writer.sheets[name[:31]])
    except PermissionError as exc:
        raise ReportWriteError(
            f"'{path}' 에 쓰지 못했습니다. {_CLOSE_EXCEL} "
            "파일명에 날짜·시각 접미사가 붙으므로 닫은 뒤 다시 실행하면 됩니다."
        ) from exc
    except OSError as exc:
        raise ReportWriteError(f"'{path}' 저장에 실패했습니다: {exc}") from exc
    return path


# --------------------------------------------------------------------- 시트


@dataclass(frozen=True, eq=False)
class ReportSections:
    """산출물에 담을 조각. 없는 것은 비워 둔다."""

    usage: UsageData
    bill: BillingResult
    diagnosis: Diagnosis | None = None
    comparison: ComparisonResult | None = None
    sensitivity: pd.DataFrame | None = None
    measure_rows: pd.DataFrame | None = None
    solar_curve: SolarCurve | None = None
    """태양광 용량 곡선. **20단계 상세는 화면이 아니라 여기로 보낸다** (15세션 1-3)."""
    include_timeseries: bool = True
    worksheets: tuple[Worksheet, ...] = ()
    """계산 근거 표 (22세션 2절). 화면 카드가 접어 둔 것과 **같은 표**다."""
    tariff_table: TariffTable | None = None
    ess_cases: pd.DataFrame | None = None
    measure_notices: tuple[tuple[Notice, ...], ...] = ()
    """수단이 낸 안내 원본. 부록 C 가 참고 등급을 골라 쓴다."""
    solar: SolarPoint | None = None
    """고른 태양광 지점 — 용량 곡선의 같은 용량 줄이 계산 근거와 한 글자다 (S234 ㄱ)."""
    peer_savings: Peers = ()
    """단독 수단의 기간 절감 (원값, 표기 값) — 조합이 원값이 같으면 그 글자다 (S234 ㄴ)."""


def _summary_rows(sections: ReportSections) -> list[tuple[str, str, str]]:
    bill = sections.bill
    rows: list[tuple[str, str, str]] = []

    for line in bill.traceability():  # 요구사항서 5.8
        label, _, value = line.partition(": ")
        rows.append(("적용 근거", label, value or line))

    usage = sections.usage
    rows.extend(
        [
            ("데이터", "원본 파일", usage.meta.source_name),
            ("데이터", "기간", bill.period_label),
            (
                "데이터",
                "총 사용량",
                f"{format_mwh(usage.total_kwh)} (그리드 이탈 "
                f"{usage.meta.off_grid_kwh:,.2f} kWh 포함)",
            ),
            ("데이터", "최대수요", max_demand_text(usage.meta.max_demand_kw)),
            (
                "데이터",
                "결측",
                f"{usage.meta.missing_rows:,}슬롯 ({usage.meta.missing_ratio:.1%}) — 미보간",
            ),
        ]
    )

    # **「기본요금」 은 역률 가감이 반영된 뒤의 금액이다** (S141 3절). 화면·PPT·
    # Word 셋은 109세션부터 이 몫을 세는데 **이 시트만 안 접고 있었다** —
    # 129세션이 「넷이 다른 값을 내는 자리가 하나 있다」 로 값을 본 자리이고,
    # `large-b-pf85` 에서 452,804,556 대 459,143,820원으로 6,339,264원 갈렸다.
    # 덧셈은 엔진이 한다 (:attr:`BillingResult.base_with_power_factor_won`).
    #
    # **역률요금 줄은 세우지 않는다.** 위 금액이 이미 담고 있어 따로 세우면
    # 아래 합계가 역률을 두 번 센다 — 조각 구성을 화면·PPT·Word 셋과 맞춘다
    # (기본요금 · 전력량요금 · 초과사용부가금 · 합계). **역률은 이 통합문서에서
    # 사라지지 않는다** — 위 「적용 근거」 의 「적용 역률」 줄, 「요금 계산 명세」
    # 시트의 역률요금(원) 열, 「부록 A 산출 근거」 의 역률 요금 줄 셋이 든다.
    # 그 셋은 역률을 제 항목으로 세우는 표라 거기서는 기본요금이 **역률의 밑**
    # 이다 (약관 제43조 · 「기본요금 × 역률 조정률」).
    #
    # **줄은 청구 표와 같은 표기 값이다** (S233 ㄱ · :func:`bill_lines`) — 줄마다 절사해
    # 합계와 1,000원 어긋났다(덱 10벌).
    lines = bill_lines(bill)
    rows.append(("요금", "기본요금", f"{format_won(lines.base_with_power_factor)} 원"))
    rows.append(("요금", "전력량요금", f"{format_won(lines.energy)} 원"))
    # **부가금이 붙은 벌에서만 선다** (109세션). 0원인 벌에 한 줄을 더 두면
    # 「없는 것을 있다고」 적는 꼴이고, 붙은 벌에 안 두면 합계가 안 맞는다.
    if bill.total_excess_won:
        rows.append(
            (
                "요금",
                "초과사용부가금",
                f"{format_won(lines.excess)} 원 "
                f"(청구 {len(bill.excess.charged_months)}개 월, "
                f"첫 초과 달은 예고)",
            )
        )
    rows.append(("요금", "합계 (관측 기준)", f"{format_won(bill.total_won)} 원"))
    rows.append(("요금", "합계 (결측 보정 기준)", f"{format_won(bill.total_won_adjusted)} 원"))
    # 항목을 각각 절사하므로 항목 합과 합계 표시가 어긋날 수 있다 (14세션).
    rows.append(("요금", "표기 안내", TRUNCATION_FOOTNOTE))

    diagnosis = sections.diagnosis
    if diagnosis is not None:
        summary = diagnosis.summary
        # 선택요금 절감은 적힌 두 합계의 차다 (S233 ㄴ · 수단별 결과 · 계산 근거와 한 글자).
        switch_won = format_won(
            money.gap_won(summary.current_total_won, summary.best_total_won)
            if summary.current_total_won is not None and summary.best_total_won is not None
            else summary.tariff_switch_saving_won,
            reason="미산출 — 계약 정보 없음",
        )
        # 관측 기간 값이다 — 「수단별 결과」 시트는 12개월 환산 열을 곁에 둔다 (S188).
        rows.append(("개선 여지", "선택요금 전환 (기간)", f"{switch_won} 원 (투자 불필요)"))
        rows.append(
            (
                "개선 여지",
                "계약전력 조정 (기간)",
                # **단위 없는 맨 「0」 이 서 있었다** (83세션 6). 다섯 자리를
                # 같은 말로 맞춘다. 「없음」 은 여지 판정 `no_saving` 하나가 가른다
                # (S205 2절) — 날값 0 으로 가르면 다른 사실을 같은 글자로 적는다.
                NO_SAVING
                if diagnosis.contract is not None and diagnosis.contract.adjustment.no_saving
                else format_won(
                    contract_saving(diagnosis.contract.adjustment)
                    if diagnosis.contract is not None
                    else summary.contract_saving_won,
                    reason=UNPRICED_REASONS["contract"],
                ),
            )
        )
        rows.append(
            (
                "개선 여지",
                "태양광 피크 기여",
                f"{summary.pv_potential} (상위 구간의 {summary.pv_midday_share:.0%}가 정오 시간대)",
            )
        )
        if summary.pv_basis:
            # 어느 모집단으로 판정했는지 밝힌다. 부록 B 원값과 섞이지 않게 한다.
            rows.append(("개선 여지", "태양광 판정 모집단", summary.pv_basis))

    # **재현 조건을 한 줄로** (56세션 3절). 계약종별·선택요금·계약전력은 위에
    # 이미 있고 ESS 단가 경로는 「수단별 결과」 비고가 적는다 — 빠진 것은
    # 「기준 데이터를 만졌는가」 하나였다. 전문은 부록 B 가 항목마다 싣는다.
    rows.append(("계산 조건", "기준 데이터", rules_basis_line()))
    rows.append(("미포함 요금요소", "안내", NOT_INCLUDED_NOTICE))  # 5.1
    rows.append(("계약전력 변경 경고", "필수 안내", CONTRACT_CHANGE_WARNING))  # 9.4
    for number, limit in enumerate(KNOWN_LIMITS, start=1):  # 부록 D
        rows.append(("알려진 한계", f"{number}", limit))
    for source in DATA_SOURCES:  # 출처 표기 (7.5)
        rows.append(("데이터 출처", "", source))
    if sections.sensitivity is not None:  # 9.2
        rows.append(("감도", "방식", SENSITIVITY_NOTE))
        rows.append(("감도", "이름 주의", SCENARIO_NAME_CAVEAT))

    # **등급을 열에 적는다** (19세션 1절). 화면에서 뺀 근거·참고가 여기 남는다 —
    # 어느 등급이라 화면에 없었는지까지 보여야 사용자가 찾을 수 있다.
    groups = [("요금", bill.notices)]
    if diagnosis is not None:
        groups.append(("품질·진단", diagnosis.notices))
    if sections.comparison is not None:
        groups.append(("조합", sections.comparison.notices))
    # **묶음을 넘어 같은 글자의 행은 처음 것만 둔다** (S220 2절). 「품질·진단」 묶음이
    # 「요금」 묶음의 안내를 같은 글자로 되풀이했다 — ``dedupe`` 는 묶음 안에서만
    # 거른다. **등급과 글자가 한 자도 안 다른 행만 접는다** — 사실 ID 로 넓히지
    # 않는다(글자가 다른 안내가 걸러질 수 있다).
    shown: set[tuple[str, str]] = set()
    for label, notices in groups:
        for item in dedupe(notices):
            # 위 「필수 안내」 줄과 같은 안내다 (S182 4-3). **글자가 아니라 사실
            # ID 로 견준다** (S210 2절) — 바로 윗줄이 이미 그 잣대다.
            key = (str(item.severity), item.text)
            if item.fact != MARGIN_FACT and key not in shown:
                shown.add(key)
                rows.append((f"안내 · {item.severity}", label, item.text))
    return rows


def measure_summary_frame(
    *,
    switch: TariffSwitchResult | None = None,
    contract: ContractAdjustment | None = None,
    demand_response: DemandResponseResult | None = None,
    power_factor: PowerFactorResult | None = None,
    ess: EssResult | None = None,
    solar: SolarPoint | None = None,
    surplus: SurplusResult | None = None,
    base_fee_months: float | None = None,
) -> pd.DataFrame:
    """수단별 결과 시트 (요구사항서 7장).

    **7장 번호 순(7.1~7.6)으로 배치한다** — 선택요금 → 계약전력 → 경제성DR →
    역률 → 태양광 → ESS.

    **잉여는 태양광 아래 딸린 줄이다** (41세션 2-1·2-6). 7.7 개선안이 없어졌으므로
    독립된 줄을 세우지 않는다 — ESS 아래 차익거래와 같은 자리다.

    각 행은 **독립 평가**다 (14세션 2절). 현행 요금제·현행 사용량을 기준선으로
    "이 수단만 도입하면 얼마" 를 낸 값이며, 조합 상호작용은 3단계 합산효과에서만
    다룬다.

    **금액을 내지 못한 항목은 빈칸으로 두지 않고 사유를 적는다.**
    """
    rows: list[dict[str, object]] = []
    if switch is not None:
        rows.append(
            {
                # **`TariffSelection.__str__` 은 `종별/전압/선택` 이라 셀에
                # 코드 열쇠가 그대로 나갔다** (S156 3-3). 전환은 **현행 종별·
                # 전압 안에서만** 고르므로(`evaluate_tariff_switch`) 양쪽에서
                # 갈리는 것은 선택요금 하나다 — 그것만 이름으로 적는다.
                "수단": (
                    f"선택요금 전환 ({option_label(switch.current.selection.option)} → "
                    f"{option_label(switch.best.selection.option)})"
                ),
                "투자비(원)": format_won(0.0),
                # 적힌 두 합계의 차 (S233 ㄴ) · 환산은 같은 값이면 같은 글자.
                "기간 절감액(원)": format_won(switch_saving(switch)),
                "12개월 환산(원)": format_won(switch_annual_saving(switch)),
                # **늘 「즉시」 였다** (S134 3절). 현행이 이미 최선이라 절감이
                # 0 인 벌에서도 그렇게 적혔다 — 판정을 ``payback_years`` 로 옮겼다.
                "회수기간": payback_label(payback_years(0.0, switch.annual_saving_won or 0.0), 0.0),
                "비고": "설비 도입과 무관합니다. 감도를 적용하지 않습니다.",
            }
        )
    if contract is not None:
        rows.append(
            {
                "수단": (
                    f"계약전력 조정 ({contract.contract_kw:,.0f} → "
                    f"{contract.target_contract_kw:,.0f} kW)"
                    if contract.target_contract_kw is not None
                    else f"계약전력 조정 ({contract.contract_kw:,.0f} kW 유지)"
                ),
                "투자비(원)": format_won(0.0),
                # **0 이 아니라 「없음」 이다** (48세션 · 83세션). 하한이 안 걸리면
                # 줄어들 몫 자체가 없다 — 계산해서 0원이 나온 것과 다르다.
                "기간 절감액(원)": (
                    NO_SAVING
                    if contract.no_saving
                    else format_won(contract_saving(contract), reason=UNPRICED_REASONS["contract"])
                ),
                "12개월 환산(원)": (
                    NO_SAVING
                    if contract.no_saving
                    else format_won(
                        contract_annual_saving(contract), reason=UNPRICED_REASONS["contract"]
                    )
                ),
                "회수기간": payback_label(
                    payback_years(0.0, contract.annual_saving_won or 0.0), 0.0
                ),
                "비고": contract.saving_basis,
            }
        )
    if demand_response is not None:
        rows.append(
            {
                "수단": f"경제성DR (등록 {demand_response.registered_capacity_kw:,.0f} kW)",
                "투자비(원)": format_won(0.0),
                # **정산금은 12개월 환산값이라 기간 칸에 싣지 않는다** (S219) —
                # 12개월 칸에만 선다. 빈 칸은 이 표의 꼴(「—」)을 따른다.
                "기간 절감액(원)": format_won(None, reason="—"),
                "12개월 환산(원)": demand_response.settlement_label,
                "회수기간": payback_label(
                    payback_years(0.0, demand_response.settlement_won or 0.0), 0.0
                ),
                "비고": (
                    f"거래 가능일 {demand_response.eligible_days}일 중 저부하 평일 "
                    # **「연간」 이 아니라 「12개월 환산」 이다** (S214).
                    f"{demand_response.low_load_days}일. 12개월 환산 감축 가능량 "
                    f"{demand_response.annual_reducible_kwh:,.0f} kWh "
                    f"= Σ(저부하일별 감축 여력 × 참여 가능 시간, 합 "
                    f"{demand_response.participation_hours:,.0f}시간, 하루 상한 "
                    f"{demand_response.daily_hours_cap:,.0f}시간). "
                    f"{demand_response.participation_notice} "
                    "투자비는 0원이지만 감축 미달 시 실적위약금이 있습니다 "
                    "(전력시장운영규칙 별표26). " + DR_ADVISORY
                ),
            }
        )
    if power_factor is not None:
        rows.append(
            {
                # **상한 이상이면 「개선」 이라 적지 않는다** (S155 1-3). 판정은
                # :func:`~kwise.measures.has_no_headroom` 한 자리가 쥔다.
                "수단": (
                    f"역률 개선 (현재 {power_factor.current_pct:.1f}% · {NO_HEADROOM_LABEL})"
                    if power_factor.no_headroom
                    else (
                        f"역률 개선 "
                        f"({power_factor.current_pct:.1f} → {power_factor.target_pct:.1f}%)"
                    )
                ),
                "투자비(원)": format_won(power_factor.investment_won),
                "기간 절감액(원)": format_won(power_factor.saving_won),
                "12개월 환산(원)": format_won(power_factor.annual_saving_won),
                "회수기간": payback_label(
                    power_factor.payback_years, power_factor.investment_won
                ),
                # **상한 이상이면 투입 제어를 권하지 않는다** (S206 2-2). 끝
                # 문장은 **설비를 들이는 쪽에 주는 권고**인데 상한 이상에서는
                # 들일 설비가 없다 — 위 「수단」 칸이 같은 판정으로 「개선 여지
                # 없음」 을 적고 금액 셋이 0 인 자리다. S155 가 「수단」 칸만
                # 갈랐고 비고가 안 따라왔다.
                "비고": (
                    f"주간(08~22시) 지상역률 기준 92%, 매 1%당 기본요금의 0.2% "
                    f"(한전 기본공급약관 제43조). 현재 역률요금 "
                    f"{format_won(power_factor_charges(power_factor)[0])} 원 → "
                    f"{format_won(power_factor_charges(power_factor)[1])} 원."
                    + (
                        ""
                        if power_factor.no_headroom
                        else " 야간 진상 95% 조항에 걸리지 않도록 시간대별 투입을 제어하십시오."
                    )
                ),
            }
        )
    if solar is not None:
        rows.append(
            {
                "수단": f"태양광 {solar.capacity_kwp:,.0f} kWp",
                "투자비(원)": format_won(solar.investment_won, reason=UNPRICED_REASONS["pv_price"]),
                "기간 절감액(원)": format_won(solar.total_saving_won),
                "12개월 환산(원)": format_won(solar.annual_saving_won),
                # **표시 상한을 여기서도 태운다** (S134 3절). 앞서는 손으로
                # 적어 500년·3,000년이 그대로 나갔다 — 태양광만 예외였다.
                "회수기간": payback_label(solar.payback_years, solar.investment_won),
                # **역률 조정값을 곁에 적는다** (59세션 12절 · 목록 P6). 금액
                # 칸은 조정 전 값이다 — 카드의 절감액은 「그 수단만 적용했을 때」
                # 여야 한다 (31세션). 문장은 화면·PPT·Word 와 같은 것을 쓴다.
                "비고": (
                    ", ".join(
                        part
                        for part in (
                            f"자가소비율 {solar.self_consumption_ratio:.0%}",
                            f"도입 후 역률 {solar.power_factor_after_pct:.1f}%",
                            narrative.power_factor_adjusted_saving(
                                saving_won=solar.total_saving_won,
                                extra_won=solar.power_factor_extra_won,
                                # 같은 줄에 12개월 환산값이 서면 기간을 단다 (S219 규칙 다).
                                basis="기간" if solar.annual_saving_won is not None else "",
                            ),
                        )
                        if part
                    )
                    if solar.self_consumption_ratio is not None
                    else "발전량 0"
                ),
            }
        )
        if surplus is not None and surplus.total_kwh > 0 and solar.surplus_scenario:
            # **고른 시나리오 하나만 낸다** (48세션). 셋을 다 세우면 화면(고른
            # 하나)과 산출물이 다른 것을 말한다. 위 태양광 줄의 절감액에 이 몫이
            # 이미 들어 있고, 이 줄은 그중 얼마가 역송분인지를 밝힌다.
            offset = surplus.offset
            scenario = surplus.scenario(solar.surplus_scenario)
            is_offset = scenario.name == OFFSET_SCENARIO
            rows.append(
                {
                    "수단": f"└ 잉여 {scenario.name}",
                    "투자비(원)": format_won(None, reason="—"),
                    "기간 절감액(원)": format_won(scenario.revenue_won, reason=scenario.basis),
                    # **12개월 환산을 낸다** (48세션). 다른 줄은 모두 환산값인데
                    # 이 칸만 「—」 였다 — 28세션이 `standalone_rows` 에서 고친
                    # 것과 같은 병이고, 41세션에 자리가 옮겨 오며 되살아났다.
                    "12개월 환산(원)": format_won(
                        annualize(scenario.revenue_won, base_fee_months)
                        if scenario.revenue_won is not None and base_fee_months
                        else None,
                        reason=scenario.basis,
                    ),
                    "회수기간": "—",
                    # **적용 단가를 여기 적는다** (58세션). 화면 캡션·PPT 각주·
                    # Word 부록과 **같은 문장**이다 —
                    # :meth:`SurplusResult.applied_price_note` 하나에서 온다.
                    "비고": (
                        # **「기간 잉여」 다** (S213) — PPT 잉여 장 지표와 같은 값이다.
                        # 자릿수도 같다 (S232 ㄴ) — MWh 한 자리로 접지 않는다.
                        f"기간 잉여 {surplus_kwh_text(surplus.total_kwh)} · "
                        + (
                            f"차감 {offset.deducted_kwh:,.0f} kWh · "
                            f"잔여 {offset.remaining_kwh:,.0f} kWh · "
                            "기본요금은 바뀌지 않습니다 — 전력량요금만 다시 "
                            "계산했습니다."
                            if is_offset and offset is not None
                            else scenario.admin_burden
                        )
                        + (f" {surplus.applied_price_note}" if surplus.applied_price_note else "")
                    ),
                }
            )
    if ess is not None:
        rows.append(
            {
                "수단": (
                    f"ESS 목표 {ess.excess.target_kw:,.0f} kW "
                    f"({ess.power_kw:,.0f} kW / {ess.capacity_kwh:,.0f} kWh)"
                ),
                "투자비(원)": format_won(ess.investment_won),
                "기간 절감액(원)": format_won(ess.total_saving_won),
                "12개월 환산(원)": format_won(ess.annual_saving_won),
                "회수기간": payback_label(ess.payback_years, ess.investment_won),
                "비고": (
                    f"방전시간 {ess.discharge_hours:.2f}h ({ess.c_rate:.1f}C, 규격 용량 ÷ 출력) · "
                    f"필요 사양 {ess.required_power_kw:,.1f} kW / "
                    f"{ess_capacity_text(ess.required_capacity_kwh)} 를 "
                    "조달 규격으로 올려 잡았습니다 · "
                    f"단가 경로 {ess.pricing_path} · "
                    f"손익분기 단가 "
                    f"{format_won(ess.breakeven_unit_cost_won_per_kw)} 원/kW "
                    f"(회수 {ess.payback_target_years:.0f}년 기준) · "
                    + (
                        f"{ess.outlook_label} 단가 기준 {ess.outlook_payback_years:,.1f}년"
                        if ess.outlook_payback_years is not None
                        else "전망 단가 회수기간 미산출"
                    )
                ),
            }
        )
        if ess.arbitrage is not None:
            # 차익거래는 **별도 줄**이다 — 도구가 돌리지 않는 운전의 잠재값이다.
            arbitrage = ess.arbitrage
            rows.append(
                {
                    "수단": "└ 차익거래 잠재 (경부하 충전 → 최대부하 방전)",
                    "투자비(원)": format_won(None, reason="—"),
                    "기간 절감액(원)": UNPRICED_REASONS["arbitrage_not_summed"],
                    "12개월 환산(원)": format_won(arbitrage.annual_won),
                    "회수기간": (
                        f"단독 {arbitrage.standalone_payback_years:,.1f}년"
                        if arbitrage.standalone_payback_years is not None
                        else UNPRICED_REASONS["no_saving"]
                    ),
                    "비고": (
                        # 「연」 을 안 쓴다 (S214) — 12개월 환산값이다 (S220 2절).
                        f"12개월 환산 {arbitrage.won_per_kwh_year:,.0f} 원/kWh · "
                        f"평일 {arbitrage.cycles_per_day:g} 사이클 · 계시별 단가는 요금표에서 "
                        "가져왔습니다. "
                        + (
                            "배터리 수명(10~15년)을 넘어 단독으로는 성립하지 않습니다."
                            if arbitrage.outlives_battery
                            else "배터리 수명 안에 들어옵니다."
                        )
                    ),
                }
            )
    if not rows:
        # 켠 수단이 하나도 없을 수 있다 (진단만 보는 경우). **열 구조는 유지한다** —
        # 빈 DataFrame 에 set_index 를 걸면 KeyError 로 산출물 생성이 통째로 멈춘다.
        return pd.DataFrame(columns=MEASURE_SHEET_COLUMNS).set_index("수단")
    return pd.DataFrame(rows).set_index("수단")


def solar_curve_sheet(curve: SolarCurve, chosen: SolarPoint | None = None) -> pd.DataFrame:
    """태양광 20단계 상세 (15세션 1-3).

    화면은 **한 줄 판정**만 내고 이 표를 여기로 보낸다. 최적 지점에 표식을 남겨
    화면의 판정과 대조할 수 있게 한다.

    ``chosen`` 은 계산 근거가 적는 고른 지점(잉여 수익을 실은 것)이다. 같은 용량
    줄의 기본 · 전력량 절감은 **그 지점의 글자다** (S234 ㄱ) — 곡선 지점은 잉여 수익이
    없어 절감액이 달라 올린 줄이 갈렸다(덱 `small-b-sell` 1,004,000 대 1,003,000).
    """
    verdict = curve.verdict()
    best = verdict.best.capacity_kwp if verdict.best is not None else None

    def lines(point: SolarPoint) -> tuple[float, float, float, float]:
        same = chosen is not None and abs(point.capacity_kwp - chosen.capacity_kwp) < 1e-9
        return solar_lines(chosen if same and chosen is not None else point)

    rows = [
        {
            "용량(kWp)": point.capacity_kwp,
            # **기간 값에 「기간」 을 단다** (S219 규칙 다 · S220 2절) — 곁에 「12개월
            # 환산(원)」 이 선다. 이 머리를 읽는 코드는 없다.
            "기간 발전량(kWh)": point.generation_kwh,
            "기간 자가소비(kWh)": point.self_consumed_kwh,
            "기간 잉여(kWh)": point.surplus_kwh,
            "자가소비율": point.self_consumption_ratio,
            "요금적용전력(kW)": point.billing_demand_kw,
            # **기본요금 절감이 늘다 마는 까닭을 가리킨다** (S126 · ②-26).
            # 하한에 닿은 달은 용량을 더 키워도 기본요금이 한 푼도 안 준다 —
            # 그 수를 안 적으면 읽는 사람이 「태양광이 효과 없다」 로 읽는다.
            # 하한 값(kW)은 「진단 요약」 시트의 「요금적용전력 하한」 이 낸다.
            "하한 걸린 달": point.floor_bound_months,
            # 계산 근거 표와 같은 표기 값이다 (S233 ㄱ · :func:`solar_lines`).
            "기간 기본요금 절감(원)": lines(point)[0],
            "기간 전력량요금 절감(원)": lines(point)[1],
            "기간 총 절감액(원)": point.total_saving_won,
            "12개월 환산(원)": point.annual_saving_won,
            "투자비(원)": point.investment_won,
            "회수기간(년)": point.payback_years,
            "도입 후 역률(%)": point.power_factor_after_pct,
            "판정": (
                "◀ " + verdict.pick_label
                if best is not None and abs(point.capacity_kwp - best) < 1e-9
                else ""
            ),
        }
        for point in curve.points
    ]
    return pd.DataFrame(rows).set_index("용량(kWp)")


def no_pv_sensitivity_frame() -> pd.DataFrame:
    """태양광이 없는 케이스의 감도 시트. 빈 시트 대신 사유를 적는다."""
    return pd.DataFrame([{"시나리오": "—", "내용": NO_PV_SENSITIVITY_NOTE}]).set_index("시나리오")


def _diagnosis_frame(diagnosis: Diagnosis) -> pd.DataFrame:
    pattern = diagnosis.pattern
    peak = diagnosis.peak
    rows: list[tuple[str, str]] = [
        ("부하율", f"{pattern.load_factor:.1%}" if pattern.load_factor else "—"),
        ("평균 수요", f"{pattern.mean_kw:,.1f} kW"),
        ("최대 수요", max_demand_text(pattern.max_kw)),
        (
            "기저부하 비율 (야간÷주간)",
            f"{pattern.base_load_ratio:.1%}" if pattern.base_load_ratio else "—",
        ),
        (
            "주말 부하 비율",
            f"{pattern.weekend_ratio:.1%}" if pattern.weekend_ratio else "—",
        ),
        # **이름이 제 식을 달고 간다** (S211 2-2). 이 값은 「밖 평균 ÷ 운영시간
        # 평균」 인데, 화면·PPT·Word 의 「운영시간 외 부하 **비중**」 은 「밖 사용량
        # ÷ 전체」 라 **한 글자 차 이름으로 두 정의가 섰다.** 덱 19벌에서 차가 0 인
        # 벌이 없고 부호까지 갈린다(−10.7 ~ +18.2%p). 두 줄 위 「기저부하 비율
        # (야간÷주간)」 과 같은 꼴로 식을 달아 가른다 — **값은 안 건드린다.**
        (
            "운영시간 외 부하 비율 (운영 외÷운영)",
            f"{pattern.off_hours_ratio:.1%}" if pattern.off_hours_ratio else "—",
        ),
        ("요금적용전력", billing_demand_text(peak.billing_demand_kw)),
        (f"상위 {peak.top_n}구간 주말 건수 (전 슬롯)", f"{peak.weekend_slots}"),
        (
            f"상위 {peak.top_n}구간 주말 건수 (요금적용전력 대상)",
            f"{peak.demand_weekend_slots}",
        ),
        ("관측 슬롯", f"{peak.observed_slots:,}"),
        ("요금적용전력 대상 슬롯", f"{peak.demand_eligible_slots:,}"),
    ]
    if diagnosis.contract is not None:
        contract = diagnosis.contract
        rows.extend(
            [
                ("계약전력", f"{contract.contract_kw:,.0f} kW"),
                # **「여유율」 이 아니라 「이용률」 이다** (83세션). 값은
                # 요금적용전력 ÷ 계약전력이라 이름이 값과 반대였다.
                ("계약전력 이용률", f"{contract.utilization:.1%}"),
                (
                    "요금적용전력 하한",
                    f"{contract.floor_kw:,.1f} kW" if contract.floor_kw is not None else "—",
                ),
                (
                    "목표 계약전력",
                    f"{contract.target_contract_kw:,.0f} kW"
                    if contract.target_contract_kw is not None
                    else NO_SAVING,
                ),
                (
                    "계약전력 조정 기간 절감액",
                    NO_SAVING
                    if contract.adjustment.no_saving
                    else format_won(
                        contract_saving(contract.adjustment), reason=UNPRICED_REASONS["contract"]
                    ),
                ),
            ]
        )
    if diagnosis.dr is not None:  # 6.6 경제성DR
        dr = diagnosis.dr
        rows.extend(
            [
                ("DR 거래 가능일", f"{dr.eligible_days}일 / 전체 {dr.total_days}일"),
                ("DR 제외일 (토·일·공휴일)", f"{dr.excluded_days}일"),
                (
                    "DR 등록 권장 용량 (저부하일 여력 하위값)",
                    f"{dr.registered_capacity_kw:,.0f} kW",
                ),
                ("DR 평균 기준 여력", f"{dr.mean_reducible_kw:,.0f} kW"),
                (f"DR {JUDGE_WINDOW}", dr.window_label),
                (
                    "DR 저부하 판정 기준선 (주말·공휴일 평균 × 배수)",
                    # **줄을 여기서 짓지 않는다** (S160 2-2) — 화면·PPT·부록 A 와
                    # 같은 자리에서 받는다. 여기서 따로 지었을 때 이 칸만 두 값을
                    # `,.0f` 로 접어 「36 kW × 1.2 = 44 kW」 가 됐다.
                    low_load_threshold_line(dr.weekend_baseline_kw, dr.low_load_threshold_kw)
                    or "산출 보류 — 주말·공휴일 관측치 없음",
                ),
                ("DR 저부하 평일", f"{dr.low_load_days_count}일"),
                (
                    # **「연간」 이 아니라 「12개월 환산」 이다** (S214).
                    f"DR 12개월 환산 감축 가능량 (참여 {dr.total_participation_hours:,.0f}시간, "
                    f"하루 상한 {dr.daily_hours_cap:,.0f}시간)",
                    f"{dr.annual_reducible_kwh:,.0f} kWh",
                ),
                ("DR 참여 안내", dr.notice),
                ("DR 자원 유형", ", ".join(str(item) for item in dr.resource_types)),
                ("DR 적합성", str(dr.potential)),
            ]
        )
    if diagnosis.structure is not None:
        structure = diagnosis.structure
        rows.extend(
            [
                # **화면·PPT·Word 와 같은 몫을 센다** (S124 · ②-40). `base_share`
                # 는 분자만 역률요금을 빼 **전력량요금 비중과 합해도 100% 가 안
                # 된다** — 역률 85% 에서 99.8% 다.
                ("기본요금 비중", f"{structure.base_with_power_factor_share:.1%}"),
                ("전력량요금 비중", f"{structure.energy_share:.1%}"),
                # **열쇠를 그대로 적지 않는다** (S161 2절). `light`·`spring_fall`
                # 은 코드 이름이고 사람이 읽는 이름은 번역표가 이미 쥔다
                # (:data:`~kwise.report.columns.VALUE_LABELS` · `SEASON_LABELS`).
                *(
                    (f"{value_label('band', band)} 사용량 비중", f"{share:.1%}")
                    for band, share in structure.band_share.items()
                ),
                *(
                    (f"{season_label(season)} 사용량 비중", f"{share:.1%}")
                    for season, share in structure.season_share.items()
                ),
            ]
        )
    frame = pd.DataFrame(rows, columns=["항목", "값"]).set_index("항목")

    # 시각 분포는 두 모집단을 나란히 싣되 라벨로 구분한다.
    # 전 슬롯 값이 부록 B 의 원값이고, 대상 슬롯 값이 태양광 등급의 근거다.
    distribution: list[tuple[str, str]] = [
        (f"상위 {peak.top_n}구간 {hour}시 (전 슬롯)", f"{count}건")
        for hour, count in peak.hour_counts.items()
        if count > 0
    ]
    distribution.extend(
        (f"상위 {peak.top_n}구간 {hour}시 (요금적용전력 대상)", f"{count}건")
        for hour, count in peak.demand_hour_counts.items()
        if count > 0
    )
    extra = pd.DataFrame(distribution, columns=["항목", "값"]).set_index("항목")
    return pd.concat([frame, extra])


def _timeseries_frame(usage: UsageData) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "kw": usage.kw,
            "kwh": usage.energy_kwh(),
            "결측": usage.kw.isna(),
        }
    )


def build_sheets(sections: ReportSections) -> dict[str, pd.DataFrame]:
    """시트 사전을 만든다. 순서는 :data:`SHEET_ORDER` 를 따른다."""
    summary = pd.DataFrame(_summary_rows(sections), columns=["구분", "항목", "내용"]).set_index(
        "구분"
    )

    monthly = sections.bill.monthly.copy()
    monthly.index = monthly.index.astype(str)

    sheets: dict[str, pd.DataFrame] = {"요약": summary}
    if sections.diagnosis is not None:
        sheets["진단"] = _diagnosis_frame(sections.diagnosis)
    sheets["월별 집계"] = localize(
        monthly[
            [
                "season",
                "covered_days",
                "is_partial",
                "max_demand_kw",
                "billing_demand_kw",
                "light_kwh",
                "mid_kwh",
                "peak_kwh",
                "total_kwh",
                "missing_ratio",
                "demand_confidence",
            ]
        ],
        index_name="월",
    )
    if sections.include_timeseries:
        sheets["15분 시계열"] = _timeseries_frame(sections.usage)
    # **화면에서 뺀 중간값이 여기 있다** (21세션 3-2). 월별 명세 화면은 결론
    # 하나(요금적용전력)만 내고, 그 값이 어떻게 나왔는지는 이 시트가 맡는다.
    sheets["요금 계산 명세"] = localize(
        _balance_monthly(monthly)[
            [
                "days_in_month",
                "max_demand_at",
                "demand_basis_kw",
                "demand_before_floor_kw",
                "billing_demand_kw",
                "base_demand_kw",
                "base_fee_factor",
                "base_won",
                "power_factor_won",
                # **부가금 열이 없으면 부분의 합이 합계에 못 미친다** (S129 2절).
                # C7 에서 달마다 최대 2,334만원이 설명되지 않았다 — S127 이
                # Word 요금 구조 표에서 고친 것과 같은 모양이다.
                "excess_won",
                "energy_won",
                "energy_won_adjusted",
                "total_won",
                "total_won_adjusted",
                "discount_won",
                "school_discount_won",
                "demand_confidence",
            ]
        ],
        index_name="월",
    )
    if sections.measure_rows is not None:
        sheets["수단별 결과"] = sections.measure_rows
    if sections.solar_curve is not None:
        # **20단계 상세를 여기 싣는다** (15세션 1-3). 화면은 한 줄 판정만 낸다 —
        # 곡선이 단조롭게 좋아지기만 하면 표가 아무것도 알려주지 않기 때문이다.
        sheets["태양광 용량 곡선"] = solar_curve_sheet(sections.solar_curve, sections.solar)
    if sections.comparison is not None:
        # **절감액은 적힌 두 요금의 차다** (S233 ㄴ) — 같은 줄의 기준선 요금 − 조합 요금이
        # 1,000원 어긋났다(덱 16벌 48줄). 계산 쪽 표를 받아 금액 칸만 표기 값으로 간다.
        comparison = sections.comparison
        peers = sections.peer_savings
        frame = comparison.frame()
        saving = [combination_saving(comparison, item, peers) for item in comparison.combinations]
        frame["기간 절감액(원)"] = saving
        # 조합 요금 = 적힌 기준선 요금 − 적힌 절감액 (사람 결정 A · S234 ㄴ). 절감액이 두
        # 요금의 차인 줄은 조합 요금의 절사 그대로이고, 단독 수단의 글자를 쓴 줄만 올라간다.
        bill = next(column for column in frame.columns if column.startswith("기간 요금"))
        frame[bill] = [
            money.truncate_won(comparison.baseline.total_won) - value for value in saving
        ]
        # 열 이름은 계산 쪽 표가 쥔다 — 감도 열쇠와 같은 글자라 여기 다시 적지 않는다
        # (`test_compare.py::test_감도_열쇠_글자는_한_자리에만_선다`).
        annual = next(column for column in frame.columns if column.startswith("12개월 환산"))
        frame[annual] = [
            combination_annual_saving(comparison, item, peers) for item in comparison.combinations
        ]
        sheets["조합 비교"] = frame
    # **부록 셋** — Word 와 같은 재료를 쓴다 (22세션 3절).
    if sections.worksheets:
        sheets["부록 A 산출 근거"] = worksheet_frame(sections.worksheets)
    sheets["부록 B 기준 데이터"] = basis_data_frame(sections.tariff_table)
    groups: list[tuple[Notice, ...]] = [sections.bill.notices, *sections.measure_notices]
    if sections.diagnosis is not None:
        groups.append(sections.diagnosis.notices)
    if sections.comparison is not None:
        groups.append(sections.comparison.notices)
    sheets["부록 C 한계와 전제"] = pd.DataFrame({"항목": list(known_limits(*groups))})
    if sections.sensitivity is not None:
        # **범위로 보여 준다.** 3열 나열은 근거표(감도 상세)로 내린다 (9.2).
        if "첨예도 s" in sections.sensitivity.columns:
            sheets["감도"] = sensitivity_range_frame(sections.sensitivity)
            # 원자료 열 이름은 열쇠다 — 보이는 이름으로 바꿔 싣는다 (S220 2절).
            sheets["감도 상세"] = _same_combination_saving(
                sections.sensitivity, sections.comparison, sections.peer_savings, sections.solar
            ).rename(columns=METRIC_LABELS)
        else:
            sheets["감도"] = sections.sensitivity
    # **표기는 한 문에서 한다** (S161 2절). 절사 뒤에 :func:`display_frame` 이
    # 자릿수를 접고 마크다운 표식을 벗기고 불리언을 한글로 적는다 — 시트마다
    # 하면 새 시트가 붙을 때 빠뜨리고, 그 빠뜨림이 조용하다.
    return {
        name: display_frame(truncate_money_columns(sheets[name]))
        for name in SHEET_ORDER
        if name in sheets
    }


def _same_combination_saving(
    frame: pd.DataFrame,
    comparison: ComparisonResult | None,
    peers: Peers = (),
    solar: SolarPoint | None = None,
) -> pd.DataFrame:
    """감도 상세의 절감액 — **조합 비교의 조합과 같은 값이면 그 글자다** (S233 ㄱ).

    기준 시나리오는 권장 조합을 다시 계산한 것이라 「조합 비교」 가 적힌 두 요금의
    차로 적은 값(:func:`combination_saving`)과 같은 사실이다. 같은 값(1원 안)이 아니면
    제 값 그대로다 — 다른 시나리오는 다른 사실이다.

    기본 · 전력량 절감도 고른 태양광 지점과 원값이 같으면 그 줄의 글자다 (S234 ㄴ ·
    :func:`solar_lines`) — 덱 `small-ind-a2` 897,000 대 898,000.
    """
    if comparison is None:
        return frame
    out = frame.copy()
    if solar is not None:
        base, energy, _, _ = solar_lines(solar)
        for column, raw, shown in (
            (BASE_SAVING, solar.base_saving_won, base),
            (ENERGY_SAVING, solar.energy_saving_won, energy),
        ):
            if column in out.columns:
                out[column] = [
                    shown if pd.notna(value) and abs(float(value) - raw) < 1 else value
                    for value in out[column]
                ]
    for column, raw_of, shown_of in (
        (SAVING, lambda item: item.saving_won, combination_saving),
        (ANNUAL_SAVING, lambda item: item.annual_saving_won, combination_annual_saving),
    ):
        if column not in out.columns:
            continue
        for index, value in out[column].items():
            if pd.isna(value):
                continue
            same = next(
                (item for item in comparison.combinations if abs(float(value) - raw_of(item)) < 1),
                None,
            )
            if same is not None:
                out.at[index, column] = shown_of(comparison, same, peers)
    return out


#: 「요금 계산 명세」 한 달의 셈 — 합계 열과 그것을 이루는 금액 열 (S232).
_SHARED = ("base_won", "power_factor_won", "excess_won")
_MONTHLY_SUMS = (
    ("total_won", (*_SHARED, "energy_won")),
    ("total_won_adjusted", (*_SHARED, "energy_won_adjusted")),
)


def _balance_monthly(monthly: pd.DataFrame) -> pd.DataFrame:
    """달마다 **단수 차이 조정** — 적힌 기본 · 역률 · 부가금 · 전력량의 합이 적힌 합계다.

    줄마다 절사해 덱 18벌 176달 셈이 1,000원 어긋났다(S231 3-3). 두 합계(관측 ·
    보정)가 기본 · 역률 · 부가금을 함께 쓰므로 **두 셈을 함께 세운다** (S233 ㄷ) —
    올릴 줄을 가장 적게 · 그 가운데 잘린 나머지가 큰 줄로 고른다. 관측 · 보정
    전력량이 같은 값인 달은 두 칸을 같게 올린다. S232 는 관측 셈을 먼저 맞추고
    보정 셈은 절사만 해 7달이 어긋났고 한 달은 같은 전력량이 두 칸에서 갈렸다.
    둘을 함께 못 세우는 달은 관측 셈만 맞춘다.
    """
    out = monthly.copy()
    for month, row in monthly.iterrows():
        raw = {
            name: float(row[name])
            for _, parts in _MONTHLY_SUMS
            for name in parts
            if not pd.isna(row[name])
        }
        sums = [
            (money.truncate_won(float(row[total])), parts)
            for total, parts in _MONTHLY_SUMS
            if not pd.isna(row[total])
            and all(part in raw for part in parts)
            and abs(sum(raw[part] for part in parts) - float(row[total])) < 1
        ]
        cut = {name: money.truncate_won(value) for name, value in raw.items()}
        movable = [name for name in raw if raw[name] != cut[name]]
        same_energy = raw.get("energy_won") == raw.get("energy_won_adjusted")
        best: tuple[tuple[int, float], dict[str, float]] | None = None
        for picks in itertools.product((0, 1), repeat=len(movable)):
            bump = dict(zip(movable, picks, strict=True))
            if same_energy and bump.get("energy_won", 0) != bump.get("energy_won_adjusted", 0):
                continue
            shown = {
                name: cut[name]
                + bump.get(name, 0) * math.copysign(money.TRUNCATION_UNIT_WON, raw[name])
                for name in raw
            }
            if all(sum(shown[part] for part in parts) == total for total, parts in sums):
                rank = (sum(picks), -sum(abs(raw[n] - cut[n]) for n in movable if bump[n]))
                if best is None or rank < best[0]:
                    best = (rank, shown)
        if best is None:
            best = ((0, 0.0), _observed_first(raw, row))
        for name, value in best[1].items():
            out.at[month, name] = value
    return out


def _observed_first(raw: dict[str, float], row: pd.Series) -> dict[str, float]:
    """두 셈을 함께 못 세우는 달 — 관측 셈만 맞춘다 (S232 의 규칙)."""
    total, parts = _MONTHLY_SUMS[0]
    if any(part not in raw for part in parts) or pd.isna(row[total]):
        return {name: money.truncate_won(value) for name, value in raw.items()}
    shown = dict(
        zip(parts, money.balance_won([raw[part] for part in parts], float(row[total])), strict=True)
    )
    return {name: shown.get(name, money.truncate_won(value)) for name, value in raw.items()}


def truncate_money_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """``(원)`` 으로 끝나는 수치 열을 **천 원 단위로 절사한다** (14세션 1절).

    **내보낼 때만 자른다.** 계산 프레임은 원 단위 그대로다 — 절사한 값으로
    계산하면 합계가 어긋나고, 회귀 시험이 물고 있는 값이 흔들린다.
    """
    money_columns = [
        name
        for name in frame.columns
        if str(name).endswith("(원)") and pd.api.types.is_numeric_dtype(frame[name])
    ]
    if not money_columns:
        return frame
    trimmed = frame.copy()
    for name in money_columns:
        trimmed[name] = trimmed[name].map(
            lambda value: value if pd.isna(value) else money.truncate_won(float(value))
        )
    return trimmed


def export_report(
    sections: ReportSections,
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    prefix: str = "result",
    now: dt.datetime | None = None,
) -> Path:
    """시트를 만들어 ``output\\result_YYYYMMDD_HHMM.xlsx`` 로 저장한다."""
    return write_workbook(build_sheets(sections), result_path(output_dir, prefix=prefix, now=now))
