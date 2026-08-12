"""Word 보고서 (요구사항서 10.5).

Excel 여덟 시트는 **분석자용 데이터**다. 의사결정자가 읽을 문서가 따로 필요하다.

    각 절은 **결론부터** 쓴다. 데이터를 앞에 놓지 않는다.
    표는 **Word 표 객체**로 만든다. 이미지로 넣으면 제안서에 복사해 쓸 수 없다.
    차트만 png 다 (:mod:`kwise.report.figures`).
    감도는 **범위**로 적는다. 3열 나열은 하지 않는다 (9.2).

**수단을 하나도 켜지 않아도 보고서가 나온다.** 진단만 보고 받아 가는 것이 정상
경로다 (8세션에서 같은 종류의 결함을 Excel 에서 잡았다). 그때는 3·4장을 빼고
장 번호를 당기며, 무엇을 보지 않았는지는 마지막 장의 「검토 범위」가 밝힌다.

파일명에 날짜·시각 접미사를 붙인다 — Word 가 파일을 열고 있으면 덮어쓰기가
실패한다 (Excel 과 같은 이유).
"""

from __future__ import annotations

import datetime as dt
import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from docx.document import Document as DocumentType
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Inches, Pt

from kwise.compare import SCENARIO_NAME_CAVEAT, ComparisonResult, SensitivityRange
from kwise.diagnose import Diagnosis
from kwise.io import UsageData
from kwise.measures import (
    MEASURE_CATALOG,
    Certainty,
    ContractAdjustment,
    DemandResponseResult,
    EssResult,
    MeasureKind,
    PowerFactorResult,
    SolarPoint,
    SurplusResult,
    TariffSwitchResult,
    measure_kind,
)
from kwise.report import figures
from kwise.report.notices import (
    CONTRACT_CHANGE_WARNING,
    DATA_SOURCES,
    KNOWN_LIMITS,
    NOT_INCLUDED_NOTICE,
    TRUNCATION_FOOTNOTE,
    format_won,
)
from kwise.tariff import BillingResult

__all__ = [
    "CHAPTER_COMPARISON",
    "CHAPTER_DIAGNOSIS",
    "CHAPTER_MEASURES",
    "CHAPTER_SCOPE",
    "CHAPTER_SUMMARY",
    "DEFAULT_OUTPUT_DIR",
    "DOCUMENT_TITLE",
    "KOREAN_FONT",
    "TABLE_STYLE",
    "DocumentSections",
    "MeasureEntry",
    "build_document",
    "document_bytes",
    "document_path",
    "export_document",
    "measure_entries",
]

DOCUMENT_TITLE = "전력 비용 진단 보고서"
KOREAN_FONT = "Malgun Gothic"
TABLE_STYLE = "Table Grid"
DEFAULT_OUTPUT_DIR = Path("output")

CHAPTER_SUMMARY = "요약"
CHAPTER_DIAGNOSIS = "진단"
CHAPTER_MEASURES = "개선 수단별 검토"
CHAPTER_COMPARISON = "조합 비교"
CHAPTER_SCOPE = "검토 범위와 한계"

_FIGURE_WIDTH = Inches(6.3)
_UNPRICED = "미산출"


# ===================================================================== 수단 한 항목


@dataclass(frozen=True)
class MeasureEntry:
    """3장의 수단 하나. **모든 수단을 같은 틀로 적는다.**

    결론 → 절감액 → 투자비 → 회수기간 → 확실성 → 주의사항.

    금액 칸은 **문자열**이다. 산출하지 못한 항목에 빈칸이나 0원을 넣지 않고
    사유를 적기 위해서다 (7.5·7.6).
    """

    kind: MeasureKind
    conclusion: str
    saving: str
    investment: str
    payback: str
    certainty: str
    cautions: tuple[str, ...] = field(default=())

    @property
    def title(self) -> str:
        return self.kind.title


def _won(value: float | None, *, reason: str | None = None) -> str:
    """금액 한 칸. **모르면 빈칸이 아니라 사유다** (7.5).

    :func:`kwise.report.notices.format_won` 은 Excel 용이라 단위를 붙이지 않는다
    (열 이름이 ``(원)`` 을 달고 있다). Word 표는 열 이름에 단위가 없으므로
    값에 붙인다 — 숫자만 있는 칸은 읽는 사람이 단위를 되물어야 한다.
    """
    if value is None:
        return format_won(None) if reason is None else reason
    return f"{format_won(value)}원"


def _payback_text(years: float | None, investment_won: float | None) -> str:
    if investment_won is None:
        return f"{_UNPRICED} — 투자비를 모릅니다"
    if not investment_won:
        return "즉시 (투자 없음)"
    return f"{years:,.1f}년" if years is not None else f"{_UNPRICED} — 절감액이 없습니다"


def _measure_saving(annual_won: float | None, period_won: float | None) -> str:
    if period_won is None:
        return _won(None)
    if annual_won is None:
        return _won(period_won)
    return f"{_won(period_won)} (12개월 환산 {_won(annual_won)})"


def measure_entries(
    *,
    switch: TariffSwitchResult | None = None,
    contract: ContractAdjustment | None = None,
    demand_response: DemandResponseResult | None = None,
    power_factor: PowerFactorResult | None = None,
    solar: SolarPoint | None = None,
    solar_certainty: Certainty | None = None,
    solar_unpriced_reason: str = "",
    ess: EssResult | None = None,
    surplus: SurplusResult | None = None,
) -> tuple[MeasureEntry, ...]:
    """검토한 수단을 **7장 순서 그대로** 항목으로 만든다.

    주지 않은 수단은 만들지 않는다 — 켜지 않은 수단이 보고서에 들어가면
    "검토하지 않은 것" 이 "검토했더니 이만큼" 으로 둔갑한다.
    """
    entries: dict[str, MeasureEntry] = {}

    if switch is not None:
        now_option = switch.current.selection.option
        best_option = switch.best.selection.option
        entries["tariff_switch"] = MeasureEntry(
            kind=measure_kind("tariff_switch"),
            conclusion=(
                f"선택{now_option} → 선택{best_option} 로 바꾸면 "
                f"{_won(switch.saving_won)} 줄어듭니다."
                if switch.switch_needed
                else f"현행 선택{now_option} 이 이미 최선입니다. 바꿀 이유가 없습니다."
            ),
            saving=_measure_saving(switch.annual_saving_won, switch.saving_won),
            investment=_won(0.0),
            payback=_payback_text(0.0, 0.0),
            certainty=str(switch.certainty),
            cautions=(
                "설비 도입과 무관한 확정 계산입니다. 감도를 적용하지 않습니다.",
                *switch.warnings,
            ),
        )

    if contract is not None:
        entries["contract"] = MeasureEntry(
            kind=measure_kind("contract"),
            conclusion=(
                f"계약전력을 {contract.contract_kw:,.0f} → "
                f"{contract.suggested_contract_kw:,.0f} kW 로 낮출 여지가 "
                f"{contract.reduction_kw:,.0f} kW 있습니다."
                if contract.is_over_contracted
                else f"계약전력 {contract.contract_kw:,.0f} kW 는 적정합니다. 하향 여지가 없습니다."
            ),
            saving=(
                _measure_saving(contract.annual_saving_won, contract.saving_won)
                if contract.saving_won is not None
                else f"{_UNPRICED} — {contract.saving_basis}"
            ),
            investment=_won(0.0),
            payback=_payback_text(0.0, 0.0),
            certainty=str(contract.certainty),
            cautions=(CONTRACT_CHANGE_WARNING, *contract.warnings),
        )

    if demand_response is not None:
        entries["demand_response"] = MeasureEntry(
            kind=measure_kind("demand_response"),
            conclusion=(
                f"거래 가능일 {demand_response.eligible_days}일 가운데 저부하 평일 "
                f"{demand_response.low_load_days}일에 등록 권장 "
                f"{demand_response.registered_capacity_kw:,.0f} kW, "
                f"연간 감축 가능량 {demand_response.annual_reducible_kwh:,.0f} kWh 입니다."
            ),
            saving=(
                _won(demand_response.settlement_won)
                if demand_response.is_priced
                else f"{_UNPRICED} — {demand_response.settlement_label}"
            ),
            investment=_won(0.0),
            payback=_payback_text(0.0, 0.0),
            certainty=str(demand_response.certainty),
            cautions=(
                "정산 단가는 전력거래소 월별 순편익가격과 사업자 수수료로 정해집니다. "
                "**수요관리사업자 상담이 필요합니다.**",
                *demand_response.warnings,
            ),
        )

    if power_factor is not None:
        entries["power_factor"] = MeasureEntry(
            kind=measure_kind("power_factor"),
            conclusion=(
                f"지상역률을 {power_factor.current_pct:,.0f}% → "
                f"{power_factor.target_pct:,.0f}% 로 올리면 "
                f"{_won(power_factor.saving_won)} 줄어듭니다."
            ),
            saving=_measure_saving(power_factor.annual_saving_won, power_factor.saving_won),
            investment=_won(power_factor.investment_won),
            payback=_payback_text(power_factor.payback_years, power_factor.investment_won),
            certainty=str(power_factor.certainty),
            cautions=power_factor.warnings,
        )

    if solar is not None:
        cautions = [
            "발전량 예측은 피크 발전량을 과소 산출하는 경향이 있어 피크 절감량이 "
            "보수적으로 나옵니다.",
        ]
        if solar.investment_won is None and solar_unpriced_reason:
            cautions.append(solar_unpriced_reason)
        entries["solar"] = MeasureEntry(
            kind=measure_kind("solar"),
            conclusion=(
                f"{solar.capacity_kwp:,.0f} kWp 를 올리면 연 "
                f"{solar.generation_kwh:,.0f} kWh 를 발전해 "
                f"{_won(solar.total_saving_won)} 줄어듭니다."
            ),
            saving=_measure_saving(solar.annual_saving_won, solar.total_saving_won),
            investment=(
                _won(solar.investment_won)
                if solar.investment_won is not None
                else f"{_UNPRICED} — {solar_unpriced_reason or '단가 미입력'}"
            ),
            payback=_payback_text(solar.payback_years, solar.investment_won),
            certainty=str(solar_certainty)
            if solar_certainty is not None
            else str(Certainty.MEDIUM),
            cautions=tuple(cautions),
        )

    if ess is not None:
        entries["ess"] = MeasureEntry(
            kind=measure_kind("ess"),
            conclusion=(
                f"목표 {ess.excess.target_kw:,.0f} kW 를 지키려면 "
                f"{ess.power_kw:,.0f} kW / {ess.capacity_kwh:,.0f} kWh "
                f"(방전 {ess.discharge_hours:,.2f}h) 가 필요합니다."
            ),
            saving=_measure_saving(ess.annual_saving_won, ess.total_saving_won),
            investment=_won(ess.investment_won),
            payback=_payback_text(ess.payback_years, ess.investment_won),
            certainty=str(ess.certainty),
            cautions=(
                "규칙기반 단일 디스패치이며 OPEX·열화·교체비를 넣지 않은 단순 회수기간입니다.",
                *ess.warnings,
            ),
        )

    if surplus is not None:
        priced = [item for item in surplus.scenarios if item.is_priced]
        richest = max(priced, key=lambda item: item.revenue_won or 0.0) if priced else None
        entries["surplus"] = MeasureEntry(
            kind=measure_kind("surplus"),
            conclusion=(
                f"잉여 {surplus.total_kwh:,.0f} kWh (발전량의 "
                f"{(surplus.share_of_generation or 0.0) * 100:,.1f}%) 가 남습니다."
            ),
            saving=(
                f"{richest.name} {_won(richest.revenue_won)}"
                if richest is not None
                else f"{_UNPRICED} — 단가를 넣지 않았습니다"
            ),
            investment=f"{_UNPRICED} — 계통 연계·설비 조건에 따릅니다",
            payback=f"{_UNPRICED} — 투자비를 모릅니다",
            certainty=str(Certainty.MEDIUM_LOW),
            cautions=(
                "상계거래·외부 구매의 **자격요건은 판정하지 않았습니다.** 금액만 참고하십시오.",
                *surplus.notes,
            ),
        )

    return tuple(entries[kind.key] for kind in MEASURE_CATALOG if kind.key in entries)


# ===================================================================== 보고서 재료


@dataclass(frozen=True)
class DocumentSections:
    """보고서 한 벌의 재료.

    ``measures`` 가 비면 3·4장을 만들지 않는다 — 진단만 보는 정상 경로다.
    """

    usage: UsageData
    bill: BillingResult
    diagnosis: Diagnosis | None = None
    comparison: ComparisonResult | None = None
    sensitivity: tuple[SensitivityRange, ...] = ()
    measures: tuple[MeasureEntry, ...] = ()
    building_name: str = ""
    prepared_on: dt.date | None = None
    reviewed_labels: tuple[str, ...] = ()
    skipped_labels: tuple[str, ...] = ()

    @property
    def prepared(self) -> dt.date:
        return self.prepared_on if self.prepared_on is not None else dt.date.today()

    @property
    def building(self) -> str:
        return self.building_name or self.usage.meta.source_name

    def scope(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """(검토함, 미검토). 넘겨받지 않았으면 수단 목록에서 만든다."""
        if self.reviewed_labels or self.skipped_labels:
            return self.reviewed_labels, self.skipped_labels
        done = {entry.kind.key for entry in self.measures}
        return (
            tuple(kind.title for kind in MEASURE_CATALOG if kind.key in done),
            tuple(kind.title for kind in MEASURE_CATALOG if kind.key not in done),
        )


# ===================================================================== 문서 골격


def _set_korean_font(document: DocumentType) -> None:
    """본문 폰트를 한글 폰트로. **동아시아 글꼴을 따로 지정해야** Word 가 쓴다."""
    for name in ("Normal", "Title", "Heading 1", "Heading 2", "Heading 3"):
        try:
            style = document.styles[name]
        except KeyError:  # pragma: no cover - 기본 템플릿에는 모두 있다
            continue
        style.font.name = KOREAN_FONT
        rpr = style.element.get_or_add_rPr()
        rpr.rFonts.set(qn("w:eastAsia"), KOREAN_FONT)


def _add_table(
    document: DocumentType, rows: Sequence[Sequence[str]], *, header: bool = True
) -> None:
    """**Word 표 객체**로 넣는다. 이미지로 넣으면 제안서에 복사해 쓸 수 없다 (10.5)."""
    if not rows:
        return
    table = document.add_table(rows=len(rows), cols=len(rows[0]))
    table.style = TABLE_STYLE
    for row_index, row in enumerate(rows):
        for col_index, value in enumerate(row):
            cell = table.cell(row_index, col_index)
            cell.text = str(value)
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(9)
                    if header and row_index == 0:
                        run.font.bold = True
    document.add_paragraph()


def _add_figure(document: DocumentType, png: bytes, caption: str) -> None:
    document.add_picture(io.BytesIO(png), width=_FIGURE_WIDTH)
    document.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    label = document.add_paragraph(caption)
    label.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in label.runs:
        run.font.size = Pt(9)
        run.font.italic = True


def _add_bullets(document: DocumentType, lines: Iterable[str]) -> None:
    for line in lines:
        document.add_paragraph(line, style="List Bullet")


def _conclusion(document: DocumentType, text: str) -> None:
    """**결론 한 줄.** 절의 첫 문단이며 굵게 쓴다 — 데이터를 앞에 놓지 않는다."""
    paragraph = document.add_paragraph()
    run = paragraph.add_run(text)
    run.bold = True


# ===================================================================== 장별


def _cover(document: DocumentType, sections: DocumentSections) -> None:
    title = document.add_heading(DOCUMENT_TITLE, level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle = document.add_paragraph(sections.building)
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    document.add_paragraph()
    bill = sections.bill
    _add_table(
        document,
        [
            ["항목", "내용"],
            ["건물명", sections.building],
            [
                "분석 기간",
                f"{bill.period_start:%Y-%m-%d} ~ {bill.period_end:%Y-%m-%d} "
                f"({bill.period_days:.0f}일, 기본요금 {bill.base_fee_months:.2f}개월분)",
            ],
            ["작성일", f"{sections.prepared:%Y-%m-%d}"],
            ["적용 요금표 시행일", f"{bill.effective_date}"],
            ["계약종별", f"{bill.contract_label} {bill.voltage_label} 선택{bill.selection.option}"],
        ],
    )
    document.add_page_break()


def _chapter_summary(document: DocumentType, sections: DocumentSections, number: int) -> None:
    """**이 장만 읽어도 판단이 되어야 한다.**"""
    document.add_heading(f"{number}장 {CHAPTER_SUMMARY}", level=1)
    diagnosis = sections.diagnosis
    summary = diagnosis.summary if diagnosis is not None else None

    free = summary.no_investment_saving_won if summary is not None else None
    _conclusion(
        document,
        (
            f"투자 없이 {_won(free)} 를 줄일 수 있습니다."
            if free is not None
            else "투자 없이 가능한 절감액은 계약 정보가 있어야 산출됩니다."
        ),
    )

    rows = [["항목", "값"]]
    rows.append(["투자 없이 가능한 절감액", _won(free)])
    if summary is not None:
        rows.append(
            [
                "선택요금 전환",
                _won(summary.tariff_switch_saving_won),
            ]
        )
        rows.append(["계약전력 조정", _won(summary.contract_saving_won)])
        rows.append(["태양광 피크 기여 가능성", str(summary.pv_potential)])

    best = sections.comparison.best if sections.comparison is not None else None
    if best is not None:
        rows.append(["권장 조합", best.name])
        rows.append(["총 절감액", _won(best.saving_won)])
        rows.append(["투자비", _won(best.investment_won)])
        rows.append(["회수기간", _payback_text(best.payback_years, best.investment_won)])
        rows.append(["확실성", str(best.certainty)])
    _add_table(document, rows)

    # 확실성 등급과 주의사항 한 줄.
    caution = (
        f"확실성 {best.certainty} — 조합의 등급은 **가장 낮은 구성 요소**를 따릅니다. "
        if best is not None
        else ""
    )
    document.add_paragraph(caution + NOT_INCLUDED_NOTICE + " 자세한 한계는 마지막 장에 있습니다.")
    # 금액은 천 원 단위로 절사해 적는다 (14세션 1절). 항목 합과 합계가 어긋날 수 있다.
    document.add_paragraph(TRUNCATION_FOOTNOTE)
    document.add_page_break()


def _chapter_diagnosis(document: DocumentType, sections: DocumentSections, number: int) -> None:
    document.add_heading(f"{number}장 {CHAPTER_DIAGNOSIS}", level=1)
    diagnosis = sections.diagnosis
    meta = sections.usage.meta

    # ---- 데이터 개요와 품질
    document.add_heading(f"{number}.1 데이터 개요와 품질", level=2)
    quality = diagnosis.quality if diagnosis is not None else None
    missing = f"{quality.missing_ratio:.1%}" if quality is not None else "—"
    _conclusion(
        document,
        f"{meta.start:%Y-%m-%d} ~ {meta.end:%Y-%m-%d} 의 {meta.interval_minutes}분 실측 "
        f"{meta.expected_rows:,}구간을 썼습니다. 결측률 {missing} 입니다.",
    )
    rows = [
        ["항목", "값"],
        ["기간", f"{meta.start:%Y-%m-%d} ~ {meta.end:%Y-%m-%d} ({meta.period_days:.0f}일)"],
        ["검침 간격", f"{meta.interval_minutes}분"],
        ["총 사용량", f"{meta.total_kwh / 1000:,.1f} MWh"],
        ["결측", f"{meta.missing_rows:,}구간 ({meta.missing_ratio:.1%}) — 보간하지 않았습니다"],
    ]
    if quality is not None:
        rows.append(["정전 추정", f"{len(quality.outages)}건"])
        if quality.longest_gap is not None:
            gap = quality.longest_gap
            rows.append(
                [
                    "최장 연속 결측",
                    f"{gap.slots:,}구간 ({gap.start:%Y-%m-%d %H:%M} ~ {gap.end:%Y-%m-%d %H:%M})",
                ]
            )
    _add_table(document, rows)

    if diagnosis is None:
        return

    # ---- 부하 패턴
    document.add_heading(f"{number}.2 부하 패턴", level=2)
    pattern = diagnosis.pattern
    _conclusion(
        document,
        f"부하율 {(pattern.load_factor or 0) * 100:,.1f}%, "
        f"기저부하 비율 {(pattern.base_load_ratio or 0) * 100:,.1f}% 입니다.",
    )
    _add_table(
        document,
        [
            ["지표", "값", "의미"],
            ["부하율", f"{(pattern.load_factor or 0) * 100:,.1f}%", "평균 ÷ 최대"],
            [
                "기저부하 비율",
                f"{(pattern.base_load_ratio or 0) * 100:,.1f}%",
                "야간 평균 ÷ 주간 평균",
            ],
            [
                "주말 부하 비율",
                f"{(pattern.weekend_ratio or 0) * 100:,.1f}%",
                "주말 평균 ÷ 평일 평균",
            ],
            [
                "무인시간 부하 비중",
                f"{(pattern.unattended_energy_share or 0) * 100:,.1f}%",
                f"운영 {pattern.operating_hours[0]}~{pattern.operating_hours[1]}시 밖",
            ],
        ],
    )
    _add_figure(
        document,
        figures.hourly_profile_png(diagnosis.peak),
        f"그림 {number}-1. 시간대별 평균 부하 프로파일",
    )

    # ---- 피크 특성
    document.add_heading(f"{number}.3 피크 특성", level=2)
    peak = diagnosis.peak
    _conclusion(
        document,
        f"관측 최대수요는 {peak.peak_kw:,.1f} kW, 요금적용전력은 "
        f"{peak.billing_demand_kw:,.1f} kW 입니다."
        + (
            " 경부하 시간대의 피크는 요금적용전력이 되지 않습니다."
            if peak.billing_demand_kw < peak.peak_kw * 0.99
            else ""
        ),
    )
    _add_figure(
        document,
        figures.monthly_peak_png(peak),
        f"그림 {number}-2. 월별 최대수요와 요금적용전력",
    )
    _add_figure(
        document,
        figures.top_hour_png(peak),
        f"그림 {number}-3. 상위 {peak.top_n}구간 시각 분포 — 태양광 기여 가능성의 지표",
    )

    # ---- 현재 요금 구조
    structure = diagnosis.structure
    if structure is not None:
        document.add_heading(f"{number}.4 현재 요금 구조", level=2)
        _conclusion(
            document,
            f"기본요금이 총액의 {structure.base_share:.1%} 입니다 "
            f"({_won(structure.base_won)} / {_won(structure.total_won)}).",
        )
        share = structure.band_share
        _add_table(
            document,
            [
                ["구분", "금액·비중"],
                ["기본요금", f"{_won(structure.base_won)} ({structure.base_share:.1%})"],
                [
                    "전력량요금",
                    f"{_won(structure.energy_won)} ({structure.energy_share:.1%})",
                ],
                ["경부하 사용량 비중", f"{float(share.get('light', 0.0)):.1%}"],
                ["중간부하 사용량 비중", f"{float(share.get('mid', 0.0)):.1%}"],
                ["최대부하 사용량 비중", f"{float(share.get('peak', 0.0)):.1%}"],
            ],
        )

    # ---- 계약전력 적정성
    adequacy = diagnosis.contract
    if adequacy is not None:
        document.add_heading(f"{number}.5 계약전력 적정성", level=2)
        _conclusion(
            document,
            (
                f"계약전력 {adequacy.contract_kw:,.0f} kW 대비 이용률이 "
                f"{adequacy.utilization:.1%} 이고 "
                f"{adequacy.reduction_kw:,.0f} kW 하향 여지가 있습니다."
                if adequacy.is_over_contracted
                else f"계약전력 {adequacy.contract_kw:,.0f} kW 는 적정합니다 "
                f"(이용률 {adequacy.utilization:.1%})."
            ),
        )
        _add_table(
            document,
            [
                ["항목", "값"],
                ["계약전력", f"{adequacy.contract_kw:,.0f} kW"],
                ["요금적용전력", f"{adequacy.billing_demand_kw:,.1f} kW"],
                ["이용률", f"{adequacy.utilization:.1%}"],
                ["권장 계약전력", f"{adequacy.suggested_contract_kw:,.0f} kW"],
                ["하향 여지", f"{adequacy.reduction_kw:,.0f} kW"],
                [
                    "예상 절감액",
                    _won(adequacy.saving_won)
                    if adequacy.saving_won is not None
                    else f"{_UNPRICED} — {adequacy.saving_basis}",
                ],
            ],
        )
        document.add_paragraph(CONTRACT_CHANGE_WARNING)
    document.add_page_break()


def _chapter_measures(document: DocumentType, sections: DocumentSections, number: int) -> None:
    document.add_heading(f"{number}장 {CHAPTER_MEASURES}", level=1)
    document.add_paragraph(
        "검토한 수단만 싣습니다. 투자비 순이며, 보지 않은 수단은 마지막 장의 "
        "「검토 범위」에 있습니다."
    )
    for index, entry in enumerate(sections.measures, start=1):
        document.add_heading(f"{number}.{index} {entry.title}", level=2)
        _conclusion(document, entry.conclusion)
        _add_table(
            document,
            [
                ["항목", "값"],
                ["절감액", entry.saving],
                ["투자비", entry.investment],
                ["회수기간", entry.payback],
                ["확실성", entry.certainty],
            ],
        )
        if entry.cautions:
            document.add_paragraph("주의사항")
            _add_bullets(document, entry.cautions)
    document.add_page_break()


def _chapter_comparison(document: DocumentType, sections: DocumentSections, number: int) -> None:
    document.add_heading(f"{number}장 {CHAPTER_COMPARISON}", level=1)
    comparison = sections.comparison
    assert comparison is not None  # build_document 가 없으면 이 장을 부르지 않는다
    best = comparison.best
    _conclusion(
        document,
        f"권장안은 「{best.name}」 입니다. {_won(best.saving_won)} 를 줄이고 "
        f"투자비는 {_won(best.investment_won)}, 회수기간은 "
        f"{_payback_text(best.payback_years, best.investment_won)} 입니다.",
    )
    rows = [["조합", "절감액", "투자비", "회수기간", "확실성"]]
    for item in comparison.combinations:
        rows.append(
            [
                item.name,
                _won(item.saving_won),
                _won(item.investment_won),
                _payback_text(item.payback_years, item.investment_won),
                str(item.certainty),
            ]
        )
    _add_table(document, rows)
    document.add_paragraph(
        "**조합마다 요금을 다시 계산했습니다.** 수단별 절감액의 단순 합이 아닙니다 — "
        "태양광이 사용량을 줄이면 최적 선택요금이 바뀌고, ESS 가 피크를 낮추면 "
        "기본요금 기반이 달라집니다."
    )
    _add_figure(
        document, figures.combination_png(comparison), f"그림 {number}-1. 조합별 절감액과 투자비"
    )

    if sections.sensitivity:
        document.add_heading(f"{number}.1 감도", level=2)
        _conclusion(
            document,
            "태양광 출력의 첨예도만 흔들어 본 범위입니다. 요금제 전환·계약전력 조정·"
            "역률 개선은 확정 계산이라 감도를 적용하지 않습니다.",
        )
        # **범위로 적는다. 3열 나열을 하지 않는다** (9.2).
        rows = [["지표", "기준값과 범위"]]
        rows.extend(
            [item.metric, item.text()] for item in sections.sensitivity if item.base is not None
        )
        _add_table(document, rows)
        document.add_paragraph(SCENARIO_NAME_CAVEAT)
    document.add_page_break()


def _chapter_scope(document: DocumentType, sections: DocumentSections, number: int) -> None:
    document.add_heading(f"{number}장 {CHAPTER_SCOPE}", level=1)
    reviewed, skipped = sections.scope()
    _conclusion(
        document,
        f"검토한 수단은 {len(reviewed)}개, 보지 않은 수단은 {len(skipped)}개입니다. "
        "**보지 않은 것은 '효과가 없다' 가 아닙니다.**",
    )
    _add_table(
        document,
        [
            ["구분", "수단"],
            ["검토함", ", ".join(reviewed) or "없음"],
            ["미검토", ", ".join(skipped) or "없음"],
        ],
    )

    document.add_heading(f"{number}.1 미포함 요금요소", level=2)
    document.add_paragraph(NOT_INCLUDED_NOTICE)
    document.add_paragraph(TRUNCATION_FOOTNOTE)

    document.add_heading(f"{number}.2 계약전력 변경 시 주의", level=2)
    document.add_paragraph(CONTRACT_CHANGE_WARNING)

    document.add_heading(f"{number}.3 알려진 한계", level=2)
    _add_bullets(document, KNOWN_LIMITS)

    document.add_heading(f"{number}.4 추적성", level=2)
    _add_bullets(document, sections.bill.traceability())
    _add_bullets(document, DATA_SOURCES)


# ===================================================================== 조립


def build_document(sections: DocumentSections) -> DocumentType:
    """보고서를 만든다.

    **내용이 있는 장만 넣고 번호를 당긴다.** 수단을 하나도 켜지 않으면 3·4장이
    빠져 요약·진단·검토 범위 셋으로 끝난다 — 진단만 보고 받아 가는 정상 경로다.
    비어 있는 장을 제목만 남겨 두면 "검토했는데 결과가 없다" 로 읽힌다.
    """
    document = Document()
    _set_korean_font(document)
    _cover(document, sections)

    number = 1
    _chapter_summary(document, sections, number)
    number += 1
    _chapter_diagnosis(document, sections, number)
    if sections.measures:
        number += 1
        _chapter_measures(document, sections, number)
    if sections.comparison is not None:
        number += 1
        _chapter_comparison(document, sections, number)
    number += 1
    _chapter_scope(document, sections, number)
    return document


def document_path(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    prefix: str = "kwise_report",
    now: dt.datetime | None = None,
) -> Path:
    """**날짜·시각 접미사를 붙인다.** Word 가 파일을 열고 있으면 덮어쓰기가 실패한다."""
    stamp = (now if now is not None else dt.datetime.now()).strftime("%Y%m%d_%H%M")
    return output_dir / f"{prefix}_{stamp}.docx"


def export_document(
    sections: DocumentSections,
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    prefix: str = "kwise_report",
    now: dt.datetime | None = None,
) -> Path:
    """파일로 쓴다."""
    path = document_path(output_dir, prefix=prefix, now=now)
    path.parent.mkdir(parents=True, exist_ok=True)
    build_document(sections).save(str(path))
    return path


def document_bytes(
    sections: DocumentSections, *, now: dt.datetime | None = None, prefix: str = "kwise_report"
) -> tuple[bytes, str]:
    """내려받기용 바이트와 파일명. **디스크에 남기지 않는다** (10.2)."""
    buffer = io.BytesIO()
    build_document(sections).save(buffer)
    return buffer.getvalue(), document_path(Path(), prefix=prefix, now=now).name
