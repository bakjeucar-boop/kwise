"""안내 등급 체계 (요구사항서 10.7 · 19세션).

**등급은 발신처가 정한다.** 18세션까지는 문자열 부분 일치로 추정했는데, 45건을
통과시켜 보니 82%가 어느 패턴에도 걸리지 않아 기본값(주의)으로 떨어졌다 —
3등급 체계가 이름만 있고 실제로는 전부 화면에 떴다.

여기서 지키는 것 넷.

    ① 계산 모듈이 :class:`Notice` 를 직접 만든다 (문자열 리스트가 남아 있지 않다)
    ② 근거·참고가 화면 본문에 나가지 않는다
    ③ 근거가 툴팁과 보고서 본문에 나온다
    ④ **화면에서 사라진 문구가 산출물에 반드시 있다** (대응표)
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from kwise.compare import ComparisonResult
from kwise.diagnose import Diagnosis
from kwise.io import UsageData
from kwise.measures import EssResult, SolarCurve, TariffSwitchResult
from kwise.notices import (
    Notice,
    Severity,
    basis,
    block,
    dedupe,
    info,
    report_appendix,
    report_body,
    screen_body,
    texts,
    tooltip,
    warn,
)
from kwise.quality import QualityReport
from kwise.tariff import BillingResult, TariffTable
from kwise.ui.notices import screen_notices, tooltip_text

# 이관을 마친 결과 객체들. **여기 있는 것은 문자열 리스트를 들고 있으면 안 된다.**
MIGRATED = (
    "kwise/quality/checks.py",
    "kwise/tariff/engine.py",
    "kwise/tariff/power_factor.py",
    "kwise/diagnose/contract.py",
    "kwise/diagnose/dr.py",
    "kwise/diagnose/report.py",
    "kwise/measures/arbitrage.py",
    "kwise/measures/contract.py",
    "kwise/measures/demand_response.py",
    "kwise/measures/ess.py",
    "kwise/measures/ess_cost.py",
    "kwise/measures/power_factor.py",
    "kwise/measures/solar.py",
    "kwise/measures/surplus.py",
    "kwise/measures/tariff_switch.py",
    "kwise/compare/combination.py",
)


# ============================================================ ① 발신처가 등급을 붙인다


@pytest.mark.parametrize("name", MIGRATED)
def test_계산_모듈에_문자열_안내_리스트가_없다(name: str) -> None:
    """``warnings: tuple[str, ...]`` 이 남아 있으면 폴백이 주의로 밀어 넣는다.

    필드 이름으로 등급을 짐작하게 두지 않는다 — 성격이 뒤섞여 있었기 때문이다
    (warnings 에 참고성 문구가, notes 에 주의성 문구가 들어 있었다).
    """
    source = (Path("src") / name).read_text(encoding="utf-8")
    for banned in (
        "warnings: tuple[str, ...]",
        "notes: tuple[str, ...]",
        "warnings: list[str]",
        "notes: list[str]",
    ):
        assert banned not in source, f"{name} 에 {banned} 가 남아 있습니다."


@pytest.mark.parametrize(
    ("fixture_name", "attribute"),
    [
        ("sample_report", "notices"),
        ("sample_bill", "notices"),
        ("sample_diagnosis", "notices"),
        ("sample_switch", "notices"),
        ("sample_ess", "notices"),
    ],
)
def test_결과_객체가_Notice_를_들고_온다(
    request: pytest.FixtureRequest, fixture_name: str, attribute: str
) -> None:
    result = request.getfixturevalue(fixture_name)
    items = getattr(result, attribute)
    assert items, f"{fixture_name} 이 안내를 내지 않았습니다."
    assert all(isinstance(item, Notice) for item in items)
    assert not hasattr(result, "warnings"), f"{fixture_name} 에 warnings 가 남아 있습니다."


def test_근거_등급이_실제로_쓰인다(
    sample_report: QualityReport,
    sample_bill: BillingResult,
    sample_ess: EssResult,
) -> None:
    """**근거가 이 체계의 核이다.** 하나도 없으면 이관이 형식만 바뀐 것이다."""
    for result in (sample_report, sample_bill, sample_ess):
        grades = {item.severity for item in result.notices}
        assert Severity.BASIS in grades, f"{type(result).__name__} 에 근거가 없습니다."


def test_네_등급이_모두_쓰인다(
    sample_usage: UsageData,
    sample_report: QualityReport,
    sample_diagnosis: Diagnosis,
    sample_ess: EssResult,
    sample_curve: SolarCurve,
    tariff: TariffTable,
) -> None:
    """차단·주의·근거·참고가 실제 산출에서 모두 나온다.

    차단은 **계약 정보가 없을 때** 나온다 — 파일만 올린 상태다. 샘플 진단은
    계약 정보를 넣은 것이라 차단이 없으므로 그 경로를 따로 부른다.
    """
    from kwise.diagnose import diagnose

    blank = diagnose(sample_usage, tariff, quality=sample_report)
    grades = {
        item.severity
        for result in (sample_report, sample_diagnosis, sample_ess, sample_curve, blank)
        for item in result.notices
    }
    assert grades == set(Severity), sorted(str(item) for item in grades)


# ================================================================ ② 화면 본문


def test_근거와_참고는_화면_본문에_없다(sample_diagnosis: Diagnosis, sample_ess: EssResult) -> None:
    """본문은 **차단과 주의만**이다."""
    for result in (sample_diagnosis, sample_ess):
        shown = screen_notices(result.notices)
        assert shown, "본문 안내가 하나도 없습니다."
        assert all(item.severity.on_screen for item in shown)
        assert not [item for item in shown if item.severity in (Severity.BASIS, Severity.INFO)]


def test_차단이_먼저_온다() -> None:
    """읽는 순서를 등급이 정한다."""
    items = (info("참고"), basis("근거"), warn("주의"), block("차단"))
    assert [item.severity for item in screen_body(items)] == [Severity.BLOCK, Severity.WARN]
    assert [item.severity for item in report_body(items)] == [
        Severity.BLOCK,
        Severity.WARN,
        Severity.BASIS,
    ]


# ============================================================ ③ 근거는 툴팁과 보고서


def test_근거는_툴팁으로_간다(sample_ess: EssResult) -> None:
    grounds = tooltip(sample_ess.notices)
    assert grounds, "ESS 근거가 툴팁에 하나도 없습니다."
    # **지문 중복을 걷어낸 뒤로 견준다.** 성립 조건 문구는 성립하지 않을 때
    # 주의로도 한 번 나오는데, 먼저 나온 주의가 이겨 툴팁에서 빠진다.
    deduped = {item.text for item in dedupe(sample_ess.notices) if item.severity is Severity.BASIS}
    assert set(grounds) == deduped

    rendered = tooltip_text(sample_ess.notices, header="**이 숫자가 어디서 나왔나**")
    assert rendered.startswith("**이 숫자가 어디서 나왔나**")
    for line in grounds:
        assert line in rendered


def test_근거는_보고서_본문으로_간다(sample_ess: EssResult) -> None:
    """화면에서 접힌 것이 보고서에서는 펼쳐진다 — 나중에 혼자 읽는 문서다."""
    body = texts(report_body(sample_ess.notices))
    for line in texts(sample_ess.notices, Severity.BASIS):
        assert line in body


def test_참고는_보고서_부록에만_있다(sample_ess: EssResult) -> None:
    appendix = texts(report_appendix(sample_ess.notices))
    body = texts(report_body(sample_ess.notices))
    assert appendix, "ESS 참고가 하나도 없습니다."
    assert set(appendix) == set(texts(sample_ess.notices, Severity.INFO))
    assert not set(appendix) & set(body), "참고가 본문에도 실렸습니다."
    assert not set(appendix) & {item.text for item in screen_notices(sample_ess.notices)}


# ================================================== ④ 대응표 — 사라진 문구가 없다


@pytest.mark.parametrize(
    "fixture_name", ["sample_report", "sample_bill", "sample_diagnosis", "sample_ess"]
)
def test_화면에서_뺀_문구가_산출물에_남는다(
    request: pytest.FixtureRequest, fixture_name: str
) -> None:
    """**화면에서 사라진 문구가 보고서에 없으면 안 된다.**

    네 등급이 어디로 가는지 한 자리에서 확인한다. 본문·툴팁·부록을 합치면
    발신처가 낸 문구 전부와 같아야 한다 — 어느 것도 조용히 사라지지 않는다.
    """
    notices = request.getfixturevalue(fixture_name).notices
    everything = {item.text for item in dedupe(notices)}

    on_screen = {item.text for item in screen_body(notices)}
    in_tooltip = set(tooltip(notices))
    in_report = set(texts(report_body(notices))) | set(texts(report_appendix(notices)))

    # 화면에서 빠진 것은 전부 산출물에 있다.
    dropped = everything - on_screen
    assert dropped <= in_report, sorted(dropped - in_report)

    # 본문·툴팁·부록을 합치면 원본 전부다.
    assert on_screen | in_tooltip | set(texts(report_appendix(notices))) == everything


def test_보고서_부록이_참고를_싣는다(
    sample_usage: UsageData,
    sample_bill: BillingResult,
    sample_diagnosis: Diagnosis,
    sample_comparison: ComparisonResult,
) -> None:
    """Word 5.5 절이 **참고 등급이 도착하는 자리**다 (19세션 1절)."""
    from kwise.report.document import DocumentSections, build_document

    sections = DocumentSections(
        usage=sample_usage,
        bill=sample_bill,
        diagnosis=sample_diagnosis,
        comparison=sample_comparison,
    )
    appendix = sections.appendix()
    assert appendix, "부록에 실을 참고가 하나도 없습니다."

    expected = set(
        texts(
            report_appendix(
                sample_bill.notices, sample_diagnosis.notices, sample_comparison.notices
            )
        )
    )
    assert set(appendix) == expected

    document = build_document(sections)
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "참고 — 전제와 제도 설명" in text
    for line in appendix:
        assert line in text, line


def test_Excel_요약이_등급을_적는다(
    sample_usage: UsageData, sample_bill: BillingResult, sample_diagnosis: Diagnosis
) -> None:
    """등급이 열에 남아야 **왜 화면에 없었는지**를 찾을 수 있다."""
    from kwise.report.excel import ReportSections, _summary_rows

    rows = _summary_rows(
        ReportSections(usage=sample_usage, bill=sample_bill, diagnosis=sample_diagnosis)
    )
    labels = {label for label, _kind, _text in rows}
    assert "안내 · 근거" in labels
    assert "안내 · 주의" in labels

    graded = {text for label, _kind, text in rows if label.startswith("안내 · ")}
    for line in texts(sample_bill.notices, Severity.BASIS, Severity.INFO):
        assert line in graded, line


# ==================================================== 폴백 — 이관 누락을 드러낸다


def test_문자열은_주의로_두되_로그를_남긴다(caplog: pytest.LogCaptureFixture) -> None:
    """조용히 등급을 매기면 이관 누락이 드러나지 않는다 (20세션에 지운다)."""
    import logging

    with caplog.at_level(logging.WARNING, logger="kwise.notices"):
        items = dedupe(("등급 없이 들어온 문구입니다",))
    assert [item.severity for item in items] == [Severity.WARN]
    assert any("등급 없는 문자열" in record.message for record in caplog.records)


def test_빈_문구는_만들_수_없다() -> None:
    with pytest.raises(ValueError, match="빈 안내"):
        warn("   ")


def test_Notice_는_얼어_있다() -> None:
    """등급을 나중에 바꿔치기하지 못하게 한다."""
    item = basis("근거 한 줄")
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.severity = Severity.WARN  # type: ignore[misc]


# ======================================================== 등급 판정 대응표 (문서)


def test_등급_분포를_기록한다(
    sample_report: QualityReport,
    sample_bill: BillingResult,
    sample_diagnosis: Diagnosis,
    sample_ess: EssResult,
    sample_switch: TariffSwitchResult,
) -> None:
    """**82% 가 기본값으로 떨어지던 상태로 돌아가지 않는다.**

    등급이 한쪽으로 쏠리면 체계가 다시 이름만 남는다. 주의가 전체의 3분의 2를
    넘지 않고, 근거가 최소 4분의 1은 되어야 한다.
    """
    everything = [
        item
        for result in (sample_report, sample_bill, sample_diagnosis, sample_ess, sample_switch)
        for item in result.notices
    ]
    total = len(everything)
    counts = {grade: sum(1 for item in everything if item.severity is grade) for grade in Severity}
    assert counts[Severity.WARN] / total < 0.67, counts
    assert counts[Severity.BASIS] / total >= 0.25, counts
