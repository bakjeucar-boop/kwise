"""Word 보고서 시험 (요구사항서 10.5).

**여기서 지키는 것 여섯**

    ① 다섯 장이 모두 나온다 — 그리고 수단이 없으면 3·4장을 빼고 번호를 당긴다
    ② 차트 3종이 그림으로 들어간다
    ③ **표가 Word 표 객체다** — 이미지로 넣으면 제안서에 복사해 쓸 수 없다
    ④ 미산출 항목에 빈칸·0원이 아니라 사유가 들어간다
    ⑤ 검토 범위(검토함/미검토)가 마지막 장에 있다
    ⑥ 한글이 깨지지 않는다
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from docx import Document as ReadDocument
from docx.document import Document as DocumentType

from kwise.compare import ComparisonResult, SensitivityRange
from kwise.diagnose import Diagnosis
from kwise.io import UsageData
from kwise.measures import (
    MEASURE_CATALOG,
    ContractAdjustment,
    PowerFactorResult,
    SolarPoint,
    TariffSwitchResult,
)
from kwise.report import (
    CHAPTER_COMPARISON,
    CHAPTER_DIAGNOSIS,
    CHAPTER_MEASURES,
    CHAPTER_SCOPE,
    CHAPTER_SUMMARY,
    DOCUMENT_TITLE,
    DocumentSections,
    build_document,
    document_bytes,
    document_path,
    export_document,
    measure_entries,
)
from kwise.report.document import TABLE_STYLE
from kwise.tariff import BillingResult

# ===================================================================== 도우미


def _style_name(item: object) -> str:
    style = getattr(item, "style", None)
    return str(getattr(style, "name", "")) if style is not None else ""


def _headings(document: DocumentType, level: str = "Heading 1") -> list[str]:
    return [item.text for item in document.paragraphs if _style_name(item) == level]


def _all_text(document: DocumentType) -> str:
    parts = [item.text for item in document.paragraphs]
    for table in document.tables:
        parts.extend(cell.text for row in table.rows for cell in row.cells)
    return "\n".join(parts)


def _table_with_header(document: DocumentType, *header: str) -> object:
    for table in document.tables:
        if tuple(cell.text for cell in table.rows[0].cells) == header:
            return table
    raise AssertionError(f"머리글 {header} 인 표가 없습니다.")


@pytest.fixture(scope="module")
def entries(
    sample_switch: TariffSwitchResult,
    sample_bill: BillingResult,
    sample_usage: UsageData,
) -> tuple[object, ...]:
    from kwise.measures import evaluate_contract_adjustment

    contract: ContractAdjustment = evaluate_contract_adjustment(
        sample_usage, sample_bill, contract_kw=5_800.0
    )
    return measure_entries(switch=sample_switch, contract=contract)


@pytest.fixture(scope="module")
def full_sections(
    sample_usage: UsageData,
    sample_bill: BillingResult,
    sample_diagnosis: Diagnosis,
    sample_comparison: ComparisonResult,
    entries: tuple[object, ...],
) -> DocumentSections:
    return DocumentSections(
        usage=sample_usage,
        bill=sample_bill,
        diagnosis=sample_diagnosis,
        comparison=sample_comparison,
        measures=entries,  # type: ignore[arg-type]
        building_name="본사 사옥",
        prepared_on=dt.date(2026, 8, 11),
    )


@pytest.fixture(scope="module")
def full_document(full_sections: DocumentSections) -> DocumentType:
    return build_document(full_sections)


@pytest.fixture(scope="module")
def diagnosis_only(
    sample_usage: UsageData, sample_bill: BillingResult, sample_diagnosis: Diagnosis
) -> DocumentType:
    """**수단을 하나도 켜지 않은 보고서.** 진단만 보고 받아 가는 정상 경로다."""
    return build_document(
        DocumentSections(usage=sample_usage, bill=sample_bill, diagnosis=sample_diagnosis)
    )


# ===================================================================== ① 장 구성


def test_다섯_장이_모두_나온다(full_document: DocumentType) -> None:
    assert _headings(full_document) == [
        f"1장 {CHAPTER_SUMMARY}",
        f"2장 {CHAPTER_DIAGNOSIS}",
        f"3장 {CHAPTER_MEASURES}",
        f"4장 {CHAPTER_COMPARISON}",
        f"5장 {CHAPTER_SCOPE}",
    ]


def test_표지에_넷이_있다(full_document: DocumentType) -> None:
    text = _all_text(full_document)
    assert DOCUMENT_TITLE in text
    for label in ("건물명", "분석 기간", "작성일", "적용 요금표 시행일"):
        assert label in text
    assert "본사 사옥" in text
    assert "2026-08-11" in text


def test_수단이_없으면_세_장으로_줄고_번호를_당긴다(diagnosis_only: DocumentType) -> None:
    """**비어 있는 장을 제목만 남기지 않는다** — "검토했는데 결과가 없다" 로 읽힌다."""
    assert _headings(diagnosis_only) == [
        f"1장 {CHAPTER_SUMMARY}",
        f"2장 {CHAPTER_DIAGNOSIS}",
        f"3장 {CHAPTER_SCOPE}",
    ]


def test_수단이_없어도_보고서가_만들어진다(tmp_path: Path, diagnosis_only: DocumentType) -> None:
    """8세션에 Excel 에서 잡은 것과 같은 종류의 결함이다."""
    path = tmp_path / "empty.docx"
    diagnosis_only.save(str(path))
    assert path.stat().st_size > 0
    assert _headings(ReadDocument(str(path)))


def test_각_절이_결론부터_시작한다(full_document: DocumentType) -> None:
    """장 제목 바로 뒤 문단이 결론이다 — 데이터를 앞에 놓지 않는다."""
    paragraphs = full_document.paragraphs
    for index, item in enumerate(paragraphs):
        if _style_name(item) == "Heading 1" and CHAPTER_SUMMARY in item.text:
            first = paragraphs[index + 1]
            assert first.text
            assert any(run.bold for run in first.runs), "결론 문단은 굵게 쓴다."
            break
    else:  # pragma: no cover
        raise AssertionError("요약 장을 찾지 못했습니다.")


# ===================================================================== ② 차트


def test_차트_3종이_삽입된다(diagnosis_only: DocumentType) -> None:
    """부하 프로파일 · 월별 최대수요 · 상위 구간 시각 분포."""
    assert len(diagnosis_only.inline_shapes) == 3
    text = _all_text(diagnosis_only)
    assert "시간대별 평균 부하 프로파일" in text
    assert "월별 최대수요와 요금적용전력" in text
    assert "시각 분포" in text


def test_조합_차트가_더해진다(full_document: DocumentType) -> None:
    assert len(full_document.inline_shapes) == 4
    assert "조합별 절감액과 투자비" in _all_text(full_document)


def test_차트가_png_이고_한글_폰트를_쓴다() -> None:
    import matplotlib.pyplot as plt

    from kwise.report.figures import KOREAN_FONT, apply_style

    apply_style()
    assert plt.rcParams["font.family"] == [KOREAN_FONT]
    # 한글 폰트에는 유니코드 마이너스가 없다. 음수 축이 깨진다.
    assert plt.rcParams["axes.unicode_minus"] is False


def test_그림이_png_바이트다(sample_diagnosis: Diagnosis) -> None:
    from kwise.report import figures

    for png in (
        figures.hourly_profile_png(sample_diagnosis.peak),
        figures.monthly_peak_png(sample_diagnosis.peak),
        figures.top_hour_png(sample_diagnosis.peak),
    ):
        assert png.startswith(b"\x89PNG"), "png 매직 바이트가 아닙니다."
        assert len(png) > 1_000


# ===================================================================== ③ 표


def test_표가_word_표_객체다(full_document: DocumentType) -> None:
    """**이미지로 넣으면 제안서에 복사해 쓸 수 없다** (10.5)."""
    assert len(full_document.tables) >= 6
    for table in full_document.tables:
        assert table.style is not None
        assert table.style.name == TABLE_STYLE
        assert len(table.rows) >= 2
        assert table.rows[0].cells[0].text  # 머리글이 비어 있지 않다


def test_표_머리글이_굵다(full_document: DocumentType) -> None:
    header = full_document.tables[0].rows[0]
    assert any(run.font.bold for cell in header.cells for p in cell.paragraphs for run in p.runs)


def test_조합_비교가_표로_나온다(full_document: DocumentType) -> None:
    table = _table_with_header(full_document, "조합", "절감액", "투자비", "회수기간", "확실성")
    assert len(table.rows) >= 2  # type: ignore[attr-defined]
    body = [cell.text for cell in table.rows[1].cells]  # type: ignore[attr-defined]
    assert body[0]
    assert "원" in body[1]  # 단위를 값에 붙인다 (Word 표는 열 이름에 단위가 없다)


def test_금액에_단위가_붙는다(full_document: DocumentType) -> None:
    table = _table_with_header(full_document, "항목", "값")
    values = [row.cells[1].text for row in table.rows[1:]]  # type: ignore[attr-defined]
    assert any(value.endswith("원") for value in values)


# ===================================================================== ④ 미산출 사유


def test_미산출_항목에_사유가_들어간다(sample_usage: UsageData, sample_bill: BillingResult) -> None:
    """**빈칸도 0원도 아니다** (7.5). 0원은 '공짜' 로 읽힌다."""
    unpriced = SolarPoint(
        capacity_kwp=960.0,
        generation_kwh=1_125_000.0,
        self_consumed_kwh=1_000_000.0,
        surplus_kwh=125_000.0,
        self_consumption_ratio=0.89,
        billing_demand_kw=5_198.0,
        base_saving_won=1_000_000.0,
        energy_saving_won=2_000_000.0,
        total_saving_won=3_000_000.0,
        annual_saving_won=3_000_000.0,
        investment_won=None,  # 단가 미입력
        payback_years=None,
        power_factor_after_pct=92.0,
        power_factor_extra_won=0.0,
    )
    entry = measure_entries(solar=unpriced, solar_unpriced_reason="태양광 단가 미입력")[0]
    assert entry.investment != ""
    assert "0원" not in entry.investment
    assert "미산출" in entry.investment
    assert "단가" in entry.investment
    assert "미산출" in entry.payback


def test_보고서_본문에도_사유가_실린다(
    sample_usage: UsageData, sample_bill: BillingResult, sample_diagnosis: Diagnosis
) -> None:
    unpriced = SolarPoint(
        capacity_kwp=100.0,
        generation_kwh=1.0,
        self_consumed_kwh=1.0,
        surplus_kwh=0.0,
        self_consumption_ratio=1.0,
        billing_demand_kw=1.0,
        base_saving_won=0.0,
        energy_saving_won=0.0,
        total_saving_won=0.0,
        annual_saving_won=0.0,
        investment_won=None,
        payback_years=None,
        power_factor_after_pct=92.0,
        power_factor_extra_won=0.0,
    )
    document = build_document(
        DocumentSections(
            usage=sample_usage,
            bill=sample_bill,
            diagnosis=sample_diagnosis,
            measures=measure_entries(
                solar=unpriced, solar_unpriced_reason="단가를 넣지 않았습니다"
            ),
        )
    )
    assert "단가를 넣지 않았습니다" in _all_text(document)


def test_수단_항목이_모두_같은_틀이다(entries: tuple[object, ...]) -> None:
    for entry in entries:
        assert entry.conclusion  # type: ignore[attr-defined]
        assert entry.saving  # type: ignore[attr-defined]
        assert entry.investment  # type: ignore[attr-defined]
        assert entry.payback  # type: ignore[attr-defined]
        assert entry.certainty  # type: ignore[attr-defined]


def test_수단이_7장_순서로_나온다(
    sample_switch: TariffSwitchResult, sample_usage: UsageData, sample_bill: BillingResult
) -> None:
    from kwise.measures import evaluate_contract_adjustment, evaluate_power_factor
    from kwise.tariff import load_tariff

    table = load_tariff()
    power_factor: PowerFactorResult = evaluate_power_factor(
        sample_usage, table, sample_bill.selection, baseline=sample_bill
    )
    contract = evaluate_contract_adjustment(sample_usage, sample_bill, contract_kw=5_800.0)
    # 일부러 뒤죽박죽으로 넘긴다 — 순서는 목록이 정한다.
    built = measure_entries(power_factor=power_factor, switch=sample_switch, contract=contract)
    assert [item.kind.number for item in built] == ["7.1", "7.2", "7.4"]


def test_켜지_않은_수단은_보고서에_없다(entries: tuple[object, ...]) -> None:
    """**'보지 않은 것' 이 '검토했더니 이만큼' 으로 둔갑하면 안 된다.**"""
    numbers = {item.kind.number for item in entries}  # type: ignore[attr-defined]
    assert "7.5" not in numbers
    assert "7.6" not in numbers


# ===================================================================== ⑤ 검토 범위


def test_검토_범위가_마지막_장에_있다(full_document: DocumentType) -> None:
    table = _table_with_header(full_document, "구분", "수단")
    rows = {row.cells[0].text: row.cells[1].text for row in table.rows[1:]}  # type: ignore[attr-defined]
    assert rows["검토함"] == "7.1 선택요금 전환, 7.2 계약전력 조정"
    assert "7.5 태양광" in rows["미검토"]
    assert "7.6 ESS" in rows["미검토"]


def test_수단이_없으면_전부_미검토다(diagnosis_only: DocumentType) -> None:
    table = _table_with_header(diagnosis_only, "구분", "수단")
    rows = {row.cells[0].text: row.cells[1].text for row in table.rows[1:]}  # type: ignore[attr-defined]
    assert rows["검토함"] == "없음"
    for kind in MEASURE_CATALOG:
        assert kind.title in rows["미검토"]


def test_검토_범위를_넘겨받으면_그것을_쓴다(
    sample_usage: UsageData, sample_bill: BillingResult
) -> None:
    """화면의 :func:`review_scope` 결과를 그대로 싣는다."""
    sections = DocumentSections(
        usage=sample_usage,
        bill=sample_bill,
        reviewed_labels=("7.3 경제성DR",),
        skipped_labels=("7.1 선택요금 전환",),
    )
    assert sections.scope() == (("7.3 경제성DR",), ("7.1 선택요금 전환",))


def test_한계와_추적성이_마지막_장에_있다(full_document: DocumentType) -> None:
    text = _all_text(full_document)
    assert "기후환경요금" in text  # 미포함 요금요소 (5.1)
    assert "한 번의 초과가 12개월간 적용됩니다" in text  # 계약전력 경고 (9.4)
    assert "인증·신고용 산출물이 아닙니다" in text  # 알려진 한계 (부록 D)
    assert "적용 요금표:" in text  # 추적성 (5.8)
    assert "Open-Meteo" in text  # 출처


# ===================================================================== 감도


def test_감도를_범위로_적는다(
    sample_usage: UsageData, sample_bill: BillingResult, sample_diagnosis: Diagnosis
) -> None:
    """**3열 나열하지 않는다** (9.2)."""
    ranges = (
        SensitivityRange(
            "기본요금 절감액(원)", "원", 31_520_000, 28_970_000, 32_660_000, "첨예형", "평탄형"
        ),
    )
    document = build_document(
        DocumentSections(
            usage=sample_usage,
            bill=sample_bill,
            diagnosis=sample_diagnosis,
            comparison=None,
            sensitivity=ranges,
        )
    )
    text = _all_text(document)
    # 감도는 조합 비교 장에 붙는다. 조합이 없으면 실리지 않는다.
    assert "평탄형" not in text or "범위" in text


def test_감도_범위가_조합_장에_실린다(
    sample_usage: UsageData,
    sample_bill: BillingResult,
    sample_diagnosis: Diagnosis,
    sample_comparison: ComparisonResult,
) -> None:
    ranges = (
        SensitivityRange(
            "기본요금 절감액(원)", "원", 31_520_000, 28_970_000, 32_660_000, "첨예형", "평탄형"
        ),
    )
    document = build_document(
        DocumentSections(
            usage=sample_usage,
            bill=sample_bill,
            diagnosis=sample_diagnosis,
            comparison=sample_comparison,
            sensitivity=ranges,
        )
    )
    table = _table_with_header(document, "지표", "기준값과 범위")
    cell = table.rows[1].cells[1].text  # type: ignore[attr-defined]
    assert "범위" in cell
    assert "~" in cell
    # 시나리오 이름이 좋고 나쁨을 뜻하지 않는다는 안내가 함께 간다.
    assert "시나리오 이름" in _all_text(document)


# ===================================================================== ⑥ 한글


def test_한글이_깨지지_않는다(tmp_path: Path, full_sections: DocumentSections) -> None:
    """저장했다 다시 읽어도 한글이 그대로여야 한다."""
    path = export_document(full_sections, output_dir=tmp_path)
    text = _all_text(ReadDocument(str(path)))
    for expected in (
        DOCUMENT_TITLE,
        "전력 비용 진단 보고서",
        "요금적용전력",
        "검토 범위와 한계",
        "본사 사옥",
        "선택요금 전환",
    ):
        assert expected in text
    assert "?" not in text.replace("?", "", 0)  # 물음표 치환이 일어나지 않았다


def test_본문_글꼴이_한글_글꼴이다(full_document: DocumentType) -> None:
    """**동아시아 글꼴을 따로 지정해야** Word 가 한글에 쓴다."""
    from docx.oxml.ns import qn

    from kwise.report.document import KOREAN_FONT

    style = full_document.styles["Normal"]
    assert style.font.name == KOREAN_FONT
    assert style.element.rPr.rFonts.get(qn("w:eastAsia")) == KOREAN_FONT


# ===================================================================== 파일·바이트


def test_파일명에_날짜와_시각이_붙는다() -> None:
    """Word 가 파일을 열고 있으면 덮어쓰기가 실패한다."""
    path = document_path(Path("output"), now=dt.datetime(2026, 8, 11, 14, 30))
    assert path.name == "kwise_report_20260811_1430.docx"


def test_바이트로_받으면_디스크에_남지_않는다(
    tmp_path: Path, full_sections: DocumentSections
) -> None:
    payload, filename = document_bytes(full_sections, now=dt.datetime(2026, 8, 11, 9, 5))
    assert payload[:2] == b"PK"  # docx 는 zip 이다
    assert filename == "kwise_report_20260811_0905.docx"
    assert list(tmp_path.iterdir()) == []


def test_저장_경로에_폴더가_없으면_만든다(tmp_path: Path, full_sections: DocumentSections) -> None:
    target = tmp_path / "없던폴더"
    path = export_document(full_sections, output_dir=target)
    assert path.is_file()


# ===================================================================== 목록 동기화


def test_화면과_보고서가_같은_수단_목록을_본다() -> None:
    """각자 목록을 들고 있으면 한쪽만 고쳤을 때 조용히 어긋난다."""
    from kwise.ui.spec import MEASURES

    assert [item.kind for item in MEASURES] == list(MEASURE_CATALOG)
    assert [item.number for item in MEASURE_CATALOG] == [
        "7.1",
        "7.2",
        "7.3",
        "7.4",
        "7.5",
        "7.6",
        "7.7",
    ]
