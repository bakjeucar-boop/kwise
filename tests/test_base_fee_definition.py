r"""「기본요금」 이라는 이름이 어느 금액을 가리키는가 (S141 3절).

**사람이 정했다 — 「기본요금」 은 역률 가감이 반영된 뒤의 금액이다.** 근거 셋 —
한전 고지서의 기본요금 칸이 그 몫이라 고객이 청구서와 맞대 본다 · 역률 개선
카드가 그 기준 위에 선다 (S133 이 약관 제41~43조로 검산) · 산식이 이미
``ChargeStructure.base_with_power_factor_share`` 한 자리에 모여 있다.

**이 못이 서는 까닭.** ``tools\fact_sites.json`` 의 「「기본요금」 금액」 그물은
**자리가 몇인지**를 셀 뿐 **그 자리가 어느 쪽 금액을 내는지**는 안 문다. 정의가
다시 갈리면 그물은 조용하고 이 시험만 빨개진다.

**벌은 역률 85% 하나뿐이다.** 저장소의 케이스 여덟과 덱 벌 열셋이 전부 약관
제42조 간주값(92%)이라 **역률요금이 0원**이고, 그러면 접기 전과 후가 같은 값이라
**어긋남이 뜰 수가 없다** (결함 유형 ② — 뜨지 않는 갈래는 없는 갈래와 같다).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kwise.diagnose import Diagnosis, diagnose
from kwise.diagnose.contract import ContractInfo
from kwise.io import UsageData
from kwise.quality import QualityReport
from kwise.report.casestudy import CaseDefinition, CaseResult
from kwise.report.excel import ReportSections, _summary_rows
from kwise.tariff import BillingOptions, TariffSelection, TariffTable

CURRENT = TariffSelection("general_b", "high_a", "I")
#: `render_deck.py` 의 `large-b-pf85` 와 같은 조건 (S132 2절).
CONTRACT_KW = 6_000.0
POWER_FACTOR_PCT = 85.0


@pytest.fixture(scope="module")
def pf85(sample_usage: UsageData, sample_report: QualityReport, tariff: TariffTable) -> Diagnosis:
    options = BillingOptions(contract_kw=CONTRACT_KW, power_factor_pct=POWER_FACTOR_PCT)
    return diagnose(
        sample_usage,
        tariff,
        ContractInfo(CURRENT, contract_kw=CONTRACT_KW),
        quality=sample_report,
        options=options,
    )


def _won_of(rows: list[tuple[str, str, str]], item: str) -> float:
    """요약 시트 한 줄의 금액. **없는 줄이면 그 자리에서 죽는다.**"""
    for _group, name, text in rows:
        if name == item:
            return float(text.replace(",", "").replace(" 원", ""))
    raise AssertionError(f"요약 시트에 「{item}」 줄이 없습니다.")


def test_산출물_넷의_기본요금이_같은_금액이다(
    sample_usage: UsageData, sample_report: QualityReport, pf85: Diagnosis
) -> None:
    """화면·PPT·Word · Excel 요약 · 케이스 스터디가 한 금액을 적는다.

    앞서 **Excel 요약 시트와 케이스 스터디 xlsx 둘만 역률을 안 접었다**
    (129세션이 값으로 보고 이름을 올린 자리). 이 조건에서 역률요금이
    6,339,264원이라 452,804,556 대 459,143,820원으로 갈렸다.

    화면·PPT·Word 셋은 ``ChargeStructure.base_with_power_factor_won`` 하나를
    읽으므로 **그 속성을 여기서 대표로 문다** — 셋이 각자 나누거나 더하면
    S133·S140 이 모은 자리가 다시 흩어진다.
    """
    structure = pf85.structure
    assert structure is not None
    bill = structure.bill
    assert bill.total_power_factor_won > 0, "역률요금이 서는 벌이어야 어긋남이 뜬다"

    want = bill.base_with_power_factor_won
    assert want == pytest.approx(bill.total_base_won + bill.total_power_factor_won)

    # ① 화면 · PPT · Word — 셋이 함께 읽는 재료.
    assert structure.base_with_power_factor_won == pytest.approx(want)

    # ② Excel 요약 시트.
    rows = _summary_rows(ReportSections(usage=sample_usage, bill=bill, diagnosis=pf85))
    assert _won_of(rows, "기본요금") == pytest.approx(want, abs=1_000.0)

    # ③ 케이스 스터디 xlsx 의 요약 행.
    row = _case_result(sample_usage, sample_report, pf85).summary_row()
    assert float(row["기본요금(원)"]) == pytest.approx(want)


def test_요약_시트의_조각_합이_합계와_맞는다(sample_usage: UsageData, pf85: Diagnosis) -> None:
    """**역률을 두 번 세지 않는다.**

    요약 시트는 기본요금·전력량요금·초과사용부가금을 줄로 세우고 그 아래
    「합계 (관측 기준)」 을 적는다 — 읽는 사람이 더해 맞대는 자리다. 기본요금이
    역률을 담게 됐으므로 **역률요금 줄을 따로 세우면 합이 6,339,264원 넘친다.**
    조각 구성을 화면·PPT·Word 셋과 같게 두는 것이 그 장치다.

    **금액은 천 원 단위로 절사해 적는다** (14세션) — 조각마다 최대 999원이
    깎이므로 넷을 더하면 3,996원까지 벌어질 수 있다. 빠진 조각(역률요금
    6,339,264원)과는 세 자리 떨어져 있어 이 잣대로도 걸러진다.
    """
    structure = pf85.structure
    assert structure is not None
    rows = _summary_rows(ReportSections(usage=sample_usage, bill=structure.bill, diagnosis=pf85))
    names = {name for _group, name, _text in rows}
    assert "역률요금" not in names, "역률요금 줄이 서면 합계가 역률을 두 번 셉니다."

    parts = sum(_won_of(rows, name) for name in ("기본요금", "전력량요금") if name in names) + (
        _won_of(rows, "초과사용부가금") if "초과사용부가금" in names else 0.0
    )
    total = _won_of(rows, "합계 (관측 기준)")
    assert parts == pytest.approx(total, abs=4_000.0), (
        f"조각 합이 합계와 {parts - total:,.0f}원 어긋납니다."
    )


def _case_result(usage: UsageData, quality: QualityReport, diagnosis: Diagnosis) -> CaseResult:
    """케이스 스터디 결과 한 벌. **시계열 계산은 안 돌린다** — 요약 행만 본다."""
    structure = diagnosis.structure
    assert structure is not None
    return CaseResult(
        definition=CaseDefinition(
            key="PF85",
            name="역률 85%",
            usage_path=Path(usage.meta.source_name),
            contract_type="general_b",
        ),
        usage=usage,
        quality=quality,
        diagnosis=diagnosis,
        baseline=structure.bill,
        contract_kw=CONTRACT_KW,
        pv_rows=(),
        sensitivity_rows=(),
        selection_rows=(),
        measure_rows=(),
        ess=None,
        elapsed_sec=0.0,
        weather_source="시험",
    )
