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
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from docx import Document
from docx.document import Document as DocumentType
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Inches, Pt
from docx.text.paragraph import Paragraph

from kwise.compare import SCENARIO_NAME_CAVEAT, ComparisonResult, SensitivityRange
from kwise.diagnose import Diagnosis
from kwise.diagnose.dr import DR_OFF_DAYS_FACT, DrProfile
from kwise.io import UsageData
from kwise.measures import (
    BASE_FEE_UNCHANGED,
    MEASURE_CATALOG,
    NOT_VIABLE_CONCLUSION,
    Certainty,
    ContractAdjustment,
    DemandResponseResult,
    EssOptimum,
    EssResult,
    EssTargetCurve,
    MeasureKind,
    PowerFactorResult,
    SolarPoint,
    SurplusResult,
    TariffSwitchResult,
    measure_kind,
)
from kwise.measures import surplus as surplus_module
from kwise.notices import Notice, Severity, report_appendix, report_body, texts
from kwise.report import figures, frames, narrative
from kwise.report.appendix import APPENDIX_TITLES, AppendixData, known_limits, reference_rows
from kwise.report.days import RepresentativeDay
from kwise.report.notices import (
    CONTRACT_CHANGE_WARNING,
    DATA_SOURCES,
    NOT_INCLUDED_NOTICE,
    TRUNCATION_FOOTNOTE,
    format_won,
    plain_text,
)
from kwise.report.worksheet import COLUMNS, Worksheet
from kwise.tariff import BillingResult, TariffTable

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
    "MeasureFigure",
    "build_document",
    "document_bytes",
    "document_path",
    "export_document",
    "measure_entries",
]

DOCUMENT_TITLE = "전력 비용 진단 보고서"

#: Word 본문 글꼴 **이름**.
#:
#: 차트 png(:mod:`kwise.report.figures`)와 달리 **여기서 글자를 그리지 않는다** —
#: 이름만 문서에 적고 그리는 것은 읽는 사람의 Word 다. 그 PC 에 없으면 Word 가
#: 알아서 대체하므로, 서버에 이 폰트가 깔려 있을 필요가 없다. 받는 사람이 대개
#: 한국 사무실이라 맑은 고딕으로 둔다.
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
class MeasureFigure:
    """수단 한 장에 나란히 놓을 그림 하나 (38세션 3절).

    **PPT 가 쓴다.** 화면은 역률·태양광·ESS 에 그림을 둘씩 그리는데 PPT 는
    하나씩만 실어, 「무엇을 보고 그렇게 판단했나」 가 슬라이드에서 빠져 있었다.
    차례가 곧 좌→우다.

    Word 는 :attr:`MeasureEntry.figure` 하나만 쓴다 — 문서는 절마다 세로로
    쌓이는 자리라 좌우로 나눌 칸이 없고, 36세션에 화면에서 감춘 산출물이다.
    """

    png: bytes
    caption: str


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
    notices: tuple[Notice, ...] = field(default=())
    """이 수단이 낸 안내 **원본**. 부록이 참고 등급만 골라 쓴다 (19세션 1절)."""
    figure: bytes | None = None
    """수단 차트 png (15세션). **화면과 같은 프레임을 본다** — 각자 만들면 어긋난다."""
    figure_caption: str = ""
    figures: tuple[MeasureFigure, ...] = field(default=())
    """PPT 가 좌우로 나란히 놓을 그림들 (38세션 3절). 비면 :attr:`figure` 한 장이다."""
    facts: tuple[tuple[str, str], ...] = field(default=())
    """**여지가 없다는 것을 보이는 지표** (39세션 4-2·4-3).

    결론이 「하향 여지 없음」·「잉여 0」 이면 실행 주의사항은 실을 자리가 아니다 —
    하지도 않을 일을 조심하라는 글이 결론보다 길어진다. 대신 **왜 없는지 보이는
    숫자**를 그 자리에 세운다. 화면 카드가 같은 판단으로 세운 지표들이다
    (31세션 2절·6절)."""
    actionable: bool = True
    """실행할 것이 있는가. **거짓이면 슬라이드가 주의사항을 싣지 않는다** (4-2)."""
    has_saving: bool = True
    """절감액이 **0도 미산출도 아닌가.** 금액 문자열을 되파싱하지 않으려고
    숫자를 아는 자리에서 정해 둔다 — 부록이 실을 수단을 이것으로 고른다
    (39세션 5절)."""
    slide_note: str = ""
    """슬라이드 맨 아래 작은 글씨 한 줄 (39세션). **숫자가 왜 달라졌는지**를
    되짚어야 하는 사실이 여기 온다 — 사람이 쉬는 날을 빼면 감축 가능량이 다시
    계산되므로 그 사실이 산출물에 남아야 한다 (29세션)."""
    saving_annual: str = ""
    """**12개월 환산 한 값만** (39세션 2-2). 슬라이드가 쓴다 — 비면 :attr:`saving`.

    Word 는 :attr:`saving` 을 그대로 쓴다. 문서는 관측 기간 값과 환산값을 나란히
    두고 대조하는 자리라 두 값이 함께 있어야 한다."""
    spec_table: tuple[tuple[str, ...], ...] = field(default=())
    """산출물에 **폭 전체로** 놓을 표. 머리글이 첫 줄이다 (46세션).

    ESS 가 쓴다 — 회수기간 곡선을 목표별 사양 표로 바꾸면서 생겼다. PPT 는
    **반 칸에 놓지 않는다**: 열이 아홉이라 좌우로 나누면 글자가 읽히지 않는다.
    표가 있으면 그림은 표 아래로 내려간다. Word 는 절 안에 그대로 쌓는다."""
    spec_caption: str = ""
    """표 아래 한 줄. 화면 캡션과 **같은 문장이다.**"""

    @property
    def slide_saving(self) -> str:
        """슬라이드에 적을 절감액. **한 칸에 한 값이다.**"""
        return self.saving_annual or self.saving

    @property
    def slide_figures(self) -> tuple[MeasureFigure, ...]:
        """슬라이드에 실을 그림 차례. **둘을 주지 않았으면 있는 하나를 쓴다.**"""
        if self.figures:
            return self.figures
        if self.figure is None:
            return ()
        return (MeasureFigure(self.figure, self.figure_caption),)

    @property
    def title(self) -> str:
        return self.kind.title


def body_lines(notices: tuple[Notice, ...]) -> tuple[str, ...]:
    """보고서 **본문**에 실을 줄 — 차단·주의·근거 (19세션 1절).

    **근거가 본문으로 온다.** 화면에서는 툴팁으로 접혀 있던 산식·출처·계수가
    여기서는 펼쳐져야 한다 — 보고서는 나중에 혼자 읽는 문서이고, 그때 물을 것이
    "이 숫자가 어디서 나왔나" 이기 때문이다. 참고는 5장 부록으로 간다.
    """
    return texts(report_body(notices))


#: 수단 한 장에 그림을 **둘 나란히** 놓을 때 굽는 크기 (38세션 3절).
#:
#: 반 칸(약 6in)에 세로 3.2in 남짓이라, 기본 비율(9:3.6)로 구우면 폭이 먼저 차서
#: 슬라이드에서 다시 줄어들고 축 눈금이 뭉개진다. 칸의 가로세로에서 나온 값이다.
MEASURE_PAIR_FIGURE = (6.0, 3.2)

#: 그림이 **하나뿐인** 수단 장의 크기 (38세션 4절). 폭을 다 쓰는 칸이다.
#:
#: 기본 비율(9:3.6)로 구우면 슬라이드에서 높이가 먼저 차서 폭의 3분의 2만 쓴다.
MEASURE_FULL_FIGURE = (12.0, 3.5)

#: 요금제 전환은 **위·아래 두 칸짜리** 한 장이다 (그룹 막대 + 현행 대비 차액).
#: 세로가 더 필요해 위 값을 그대로 쓸 수 없다.
TARIFF_FIGURE = (12.0, 4.4)

#: 그림 캡션. **한 자리에 둔다** — 같은 그림이 :attr:`MeasureEntry.figure` 와
#: :attr:`MeasureEntry.figures` 두 자리에 실리므로, 문구를 두 벌로 적으면 한쪽만
#: 고쳐진다.
_PF_TRIANGLE_CAPTION = "전력삼각형 — 각이 좁아질수록 역률이 좋습니다."
_PF_DAY_CAPTION = "대표일 부하 — 주황 점이 역률을 판정하는 주간 구간입니다."
_SOLAR_ANNUAL_CAPTION = "일별 발전량 — 여름에 높고 겨울에 낮습니다."
_SOLAR_DAY_CAPTION = "대표일의 부하 — 두 선 사이가 태양광으로 줄어든 몫입니다."
_ESS_DAY_CAPTION = "대표일의 부하 — 두 선 사이가 ESS 로 깎은 몫입니다."


def _contract_saving(contract: ContractAdjustment, value: float | None) -> str:
    """계약전력 조정의 절감액 칸 — **셋으로 갈린다** (48세션).

    미산출        하한 규정을 모른다
    기본요금 변화없음  하향 여지는 있는데 하한에 걸리지 않는다
    금액          실제로 준다
    """
    if value is None:
        return f"{_UNPRICED} — {contract.saving_basis}"
    if contract.base_fee_unchanged:
        return f"{BASE_FEE_UNCHANGED} — {contract.saving_basis}"
    return _won(value)


def _shortest_discharge_hours(curve: EssTargetCurve) -> float:
    """곡선에서 가장 짧은 방전시간. 성립 한계와 나란히 놓아 격차를 보인다."""
    return min((item.discharge_hours for item in curve.points), default=0.0)


def _pair(*items: tuple[bytes | None, str]) -> tuple[MeasureFigure, ...]:
    """구운 그림들을 슬라이드 차례로 묶는다. **못 구운 것은 빠진다.**

    하나만 남으면 슬라이드가 그 하나를 크게 그린다 — 빈 칸을 남기지 않는다.
    """
    return tuple(MeasureFigure(png, caption) for png, caption in items if png is not None)


def _safe_figure(make: Callable[[], bytes]) -> bytes | None:
    """그림을 굽되 **실패해도 보고서를 죽이지 않는다.**

    차트 하나 때문에 문서 전체를 잃는 편보다 그림 없이 표만 내는 편이 낫다
    (13세션에 3단계 화면에서 같은 종류의 결함을 겪었다).
    """
    try:
        return make()
    except Exception:
        return None


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
        return f"{_UNPRICED} — 투자비 미입력"
    if not investment_won:
        return "즉시 (투자 없음)"
    return f"{years:,.1f}년" if years is not None else f"{_UNPRICED} — 절감액 없음"


def _measure_saving(annual_won: float | None, period_won: float | None) -> str:
    if period_won is None:
        return _won(None)
    if annual_won is None:
        return _won(period_won)
    return f"{_won(period_won)} (12개월 환산 {_won(annual_won)})"


def _annual_saving(annual_won: float | None, period_won: float | None) -> str:
    """**12개월 환산 한 값만** (39세션 2-2).

    슬라이드는 한 칸에 두 값을 담지 않는다 — 「9,050,000원 (12개월 환산
    9,050,000원)」 은 같은 값을 두 번 적어 두 줄로 흐르고, 읽는 사람은 어느 쪽이
    답인지 되묻는다. 환산 기준이라는 사실은 **각주가 한 번** 적는다.
    """
    if annual_won is None and period_won is None:
        return _won(None)
    return _won(annual_won if annual_won is not None else period_won)


def _notice_text(notices: tuple[Notice, ...], fact: str) -> str:
    """사실 ID 로 안내 한 건을 꺼낸다. 없으면 빈 글이다.

    **문구를 새로 짓지 않는다** — 계산 모듈이 낸 글을 그대로 낸다 (31세션 0-2 의
    ``dropped_rows`` 와 같은 규약).
    """
    return next((item.text for item in notices if item.fact == fact), "")


def _dr_conclusion(result: DemandResponseResult, profile: DrProfile | None) -> str:
    """경제성DR 결론 (39세션 4-1).

    **0인 까닭이 함께 있어야 한다.** 거래 가능일과 저부하 평일만 적으면 왜 그
    날이 며칠뿐인지 알 수 없고, 결과가 0이면 「검토하지 않았다」 로 읽힌다 —
    저부하 판정은 **부하가 쉬는 날 수준까지 내려온 평일**을 세는 것이므로,
    그런 날이 없다는 사실이 곧 「추가로 줄일 여지가 없다」 는 답이다.

    **사람이 뺀 날은 이름으로 적는다** (29세션). 저부하로 잡혔다가 쉬는 날로
    판정되어 빠진 날이 있으면 숫자가 왜 달라졌는지 되짚을 수 있어야 한다.
    """
    head = narrative.dr_lead(profile)
    if not result.low_load_days:
        return head
    tail = (
        f" 등록 권장 {result.registered_capacity_kw:,.0f} kW, 연간 감축 가능량 "
        f"{result.annual_reducible_kwh:,.0f} kWh 입니다."
    )
    return head + tail


def _solar_conclusion(
    solar: SolarPoint, surplus_free_kwp: float | None, area_m2: float | None
) -> str:
    """태양광 결론 한 줄 (39세션 3-1).

    **용량을 정한 근거가 함께 있어야 한다** — 얼마나 넓은 자리가 드는지,
    어디서부터 잉여가 생기는지가 그 근거다 (31세션 4-1 이 화면에 세운 값이다).
    「올리면」 은 우리끼리 쓰는 말이라 「설치하면」 으로 적는다.
    """
    head = (
        f"태양광 {solar.capacity_kwp:,.0f} kWp 를 설치하면 연 "
        f"{solar.generation_kwh:,.0f} kWh 를 발전해 "
        f"{_won(solar.total_saving_won)} 줄어듭니다."
    )
    tail: list[str] = []
    if area_m2:
        tail.append(f"설치 면적 {area_m2:,.0f} m²")
    if surplus_free_kwp is not None and surplus_free_kwp > 0:
        tail.append(f"{surplus_free_kwp:,.0f} kWp 까지는 전량 자가소비")
    return f"{head} ({' · '.join(tail)})" if tail else head


def measure_entries(
    *,
    switch: TariffSwitchResult | None = None,
    contract: ContractAdjustment | None = None,
    demand_response: DemandResponseResult | None = None,
    power_factor: PowerFactorResult | None = None,
    solar: SolarPoint | None = None,
    solar_certainty: Certainty | None = None,
    solar_notices: tuple[Notice, ...] = (),
    solar_unpriced_reason: str = "",
    ess: EssResult | None = None,
    ess_curve: EssTargetCurve | None = None,
    ess_optimum: EssOptimum | None = None,
    surplus: SurplusResult | None = None,
    usage: UsageData | None = None,
    day: RepresentativeDay | None = None,
    dr_profile: DrProfile | None = None,
    solar_generation_kw: pd.Series | None = None,
    surplus_kw: pd.Series | None = None,
    surplus_free_kwp: float | None = None,
    area_m2: float | None = None,
) -> tuple[MeasureEntry, ...]:
    """검토한 수단을 **7장 순서 그대로** 항목으로 만든다.

    차트 재료(``usage`` 이하)를 주면 수단마다 그림을 함께 굽는다 (15세션 2절).
    주지 않으면 표만 나온다 — 그림이 없다고 보고서가 실패하지 않는다.

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
            saving_annual=_annual_saving(switch.annual_saving_won, switch.saving_won),
            has_saving=bool(switch.saving_won),
            investment=_won(0.0),
            payback=_payback_text(0.0, 0.0),
            certainty=str(switch.certainty),
            cautions=(
                "설비 도입과 무관한 확정 계산입니다. 감도를 적용하지 않습니다.",
                *body_lines(switch.notices),
            ),
            notices=switch.notices,
            figure=_safe_figure(lambda: figures.tariff_option_png(switch, size=TARIFF_FIGURE)),
            figure_caption="요금제별 요금 구성과 현행 대비 차액",
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
            # **0원이 아니라 결론이다** (48세션). 하향 여지가 있는데 기본요금이
            # 안 바뀌는 자리는 「0원」 이 계산이 덜 된 것처럼 읽힌다 — 화면과
            # 같은 말을 쓴다.
            saving=_contract_saving(contract, contract.saving_won),
            saving_annual=_contract_saving(contract, contract.annual_saving_won),
            has_saving=bool(contract.saving_won),
            investment=_won(0.0),
            payback=_payback_text(0.0, 0.0),
            certainty=str(contract.certainty),
            cautions=(CONTRACT_CHANGE_WARNING, *body_lines(contract.notices)),
            notices=contract.notices,
            # **여지가 없으면 왜 없는지 보인다** (39세션 4-2). 화면이 31세션에
            # 세운 지표 넷과 같은 값이다.
            actionable=contract.is_over_contracted,
            facts=(
                ("현재 계약전력", f"{contract.contract_kw:,.0f} kW"),
                ("요금적용전력", f"{contract.billing_demand_kw:,.0f} kW"),
                ("여유", f"{contract.headroom_kw:,.0f} kW"),
                ("하향 여지", f"{contract.reduction_kw:,.0f} kW"),
            ),
        )

    if demand_response is not None:
        entries["demand_response"] = MeasureEntry(
            kind=measure_kind("demand_response"),
            conclusion=_dr_conclusion(demand_response, dr_profile),
            saving=(
                # 사유는 이미 「미산출 — …」 꼴이다. 앞에 한 번 더 붙이지 않는다
                # (28세션 1-4 에 문구를 줄이면서 겹침이 드러났다).
                _won(demand_response.settlement_won)
                if demand_response.is_priced
                else demand_response.settlement_label
            ),
            investment=_won(0.0),
            payback=_payback_text(0.0, 0.0),
            certainty=str(demand_response.certainty),
            has_saving=demand_response.is_priced and bool(demand_response.settlement_won),
            cautions=(
                "정산 단가는 전력거래소 월별 순편익가격과 사업자 수수료로 정해집니다. "
                "**수요관리사업자 상담이 필요합니다.**",
                *body_lines(demand_response.notices),
            ),
            notices=demand_response.notices,
            slide_note=_notice_text(demand_response.notices, DR_OFF_DAYS_FACT),
            figure=(
                _safe_figure(lambda: figures.dr_daily_png(dr_profile, size=MEASURE_FULL_FIGURE))
                if dr_profile is not None
                else None
            ),
            figure_caption="일별 운영시간대 평균 부하 — 붉은 선 아래가 감축 가능일입니다.",
        )

    if power_factor is not None:
        # **화면은 그림이 둘이다** (38세션 3-1). 전력삼각형만 실으면 「어느
        # 시간대가 요금 대상인가」 가 슬라이드에서 빠진다.
        triangle = _safe_figure(lambda: figures.power_triangle_png(power_factor))
        pf_day = (
            _safe_figure(
                lambda: figures.power_factor_day_png(
                    usage,
                    day,
                    current_pct=power_factor.current_pct,
                    target_pct=power_factor.target_pct,
                    size=MEASURE_PAIR_FIGURE,
                )
            )
            if usage is not None and day is not None
            else None
        )
        entries["power_factor"] = MeasureEntry(
            kind=measure_kind("power_factor"),
            conclusion=(
                f"지상역률을 {power_factor.current_pct:,.0f}% → "
                f"{power_factor.target_pct:,.0f}% 로 올리면 "
                f"{_won(power_factor.saving_won)} 줄어듭니다."
            ),
            saving=_measure_saving(power_factor.annual_saving_won, power_factor.saving_won),
            saving_annual=_annual_saving(power_factor.annual_saving_won, power_factor.saving_won),
            has_saving=bool(power_factor.saving_won),
            investment=_won(power_factor.investment_won),
            payback=_payback_text(power_factor.payback_years, power_factor.investment_won),
            certainty=str(power_factor.certainty),
            cautions=body_lines(power_factor.notices),
            notices=power_factor.notices,
            figure=triangle,
            figure_caption=_PF_TRIANGLE_CAPTION,
            figures=_pair(
                (triangle, _PF_TRIANGLE_CAPTION),
                (pf_day, _PF_DAY_CAPTION),
            ),
        )

    if solar is not None:
        # **화면은 연간 발전량과 대표일 곡선을 함께 낸다** (38세션 3-2).
        # 「한 해에 얼마나 만드나」 와 「그날 피크가 얼마나 내려가나」 는 다른
        # 물음이라, 하나만 실으면 나머지 하나를 슬라이드에서 답하지 못한다.
        solar_annual = solar_day = None
        if usage is not None and solar_generation_kw is not None:
            load, generation = usage, solar_generation_kw
            solar_annual = _safe_figure(
                lambda: figures.solar_annual_png(load, generation, size=MEASURE_PAIR_FIGURE)
            )
            if day is not None:
                point = day
                solar_day = _safe_figure(
                    lambda: figures.solar_day_png(load, generation, point, size=MEASURE_PAIR_FIGURE)
                )
        cautions = [
            "발전량 예측은 피크 발전량을 과소 산출하는 경향이 있어 피크 절감량이 "
            "보수적으로 나옵니다.",
        ]
        if solar.investment_won is None and solar_unpriced_reason:
            cautions.append(solar_unpriced_reason)
        # **잉여를 여기로 녹였다** (41세션 2-1·2-6). 7.7 은 개선안이 아니라
        # 태양광의 결과다 — 장을 따로 세우면 같은 발전량 이야기를 두 번 한다.
        #
        # **잉여가 0 이면 아무것도 붙이지 않는다** (39세션 4-2). 팔 것이 없는데
        # 파는 절차를 조심하라는 말이 결론보다 길어진다.
        surplus_facts: tuple[tuple[str, str], ...] = ()
        surplus_note = ""
        if surplus is not None and surplus.total_kwh > 0:
            # **파는 길만 적는다** — 「버리기」 에는 자격요건이 없고, 상계거래는
            # 1,000 kW 를 넘으면 아예 선택지에 없다 (41세션 2-3).
            sellable = " · ".join(
                item.name
                for item in surplus.scenarios
                if item.name != surplus_module.DISCARD_SCENARIO
            )
            cautions.append(
                f"{sellable}의 **자격요건은 판정하지 않았습니다.** 금액만 참고하십시오."
            )
            surplus_facts = (
                ("자가소비", f"{(surplus.generation_kwh - surplus.total_kwh) / 1000:,.1f} MWh"),
                ("잉여", f"{surplus.total_kwh / 1000:,.1f} MWh"),
            )
            # **시나리오는 표가 아니라 한 줄이다** (39세션 4-3). 금액이 갈리는
            # 것은 「어디에 파느냐」 하나뿐이다.
            #
            # **고른 것을 표시한다** (48세션). 위 절감액·회수기간에 그 하나가
            # 이미 들어 있으므로, 셋을 나란히만 적으면 어느 값이 결론에 반영된
            # 것인지 알 수 없다.
            surplus_note = " · ".join(
                f"{item.name} {_won(item.revenue_won) if item.is_priced else _UNPRICED}"
                + (" (절감액에 반영)" if item.name == solar.surplus_scenario else "")
                for item in surplus.scenarios
            )
        entries["solar"] = MeasureEntry(
            kind=measure_kind("solar"),
            conclusion=_solar_conclusion(solar, surplus_free_kwp, area_m2),
            saving=_measure_saving(solar.annual_saving_won, solar.total_saving_won),
            saving_annual=_annual_saving(solar.annual_saving_won, solar.total_saving_won),
            has_saving=bool(solar.total_saving_won),
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
            notices=(*solar_notices, *(surplus.notices if surplus is not None else ())),
            figure=solar_day,
            figure_caption=_SOLAR_DAY_CAPTION,
            figures=_pair(
                (solar_annual, _SOLAR_ANNUAL_CAPTION),
                (solar_day, _SOLAR_DAY_CAPTION),
            ),
            facts=(
                *(
                    (("잉여 없는 최대 용량", f"{surplus_free_kwp:,.0f} kWp"),)
                    if surplus_free_kwp
                    else ()
                ),
                *surplus_facts,
            ),
            slide_note=surplus_note,
        )

    if ess is not None:
        # **목표가 어디서 나왔는지 답하는 그림이 빠져 있었다** (38세션 3-3).
        # 카드가 낸 목표는 이 U곡선의 최소 지점이다.
        # **회수기간 곡선을 뺐다** (46세션). 개략 산정이라 값이 카드와 달라
        # 한 산출물에 두 숫자가 남았다. 자리에 목표별 사양 표가 들어간다 —
        # 전부 카드 기준 참값이고 정밀화가 이미 잰 점이라 추가 계산이 없다.
        ess_table: tuple[tuple[str, ...], ...] = ()
        if ess_optimum is not None and ess_curve is not None:
            ess_table = frames.ess_spec_rows(
                frames.ess_spec_frame(
                    ess_optimum, baseline_demand_kw=ess_curve.baseline_demand_kw
                )
            )
        ess_day = (
            _safe_figure(
                lambda: figures.ess_day_png(usage, ess.dispatch, day, size=MEASURE_PAIR_FIGURE)
            )
            if usage is not None and day is not None
            else None
        )
        entries["ess"] = MeasureEntry(
            kind=measure_kind("ess"),
            conclusion=(
                f"피크를 {ess.excess.target_kw:,.0f} kW 까지 낮추려면 ESS "
                f"{ess.power_kw:,.0f} kW / {ess.capacity_kwh:,.0f} kWh 가 필요합니다. "
                "회수기간이 가장 짧은 지점을 목표로 잡았습니다."
            ),
            saving=_measure_saving(ess.annual_saving_won, ess.total_saving_won),
            saving_annual=_annual_saving(ess.annual_saving_won, ess.total_saving_won),
            has_saving=bool(ess.total_saving_won),
            investment=_won(ess.investment_won),
            payback=_payback_text(ess.payback_years, ess.investment_won),
            certainty=str(ess.certainty),
            cautions=(
                "규칙기반 단일 디스패치이며 OPEX·열화·교체비를 넣지 않은 단순 회수기간입니다.",
                *body_lines(ess.notices),
            ),
            notices=ess.notices,
            figure=ess_day,
            figure_caption=_ESS_DAY_CAPTION,
            figures=_pair((ess_day, _ESS_DAY_CAPTION)),
            spec_table=ess_table,
            spec_caption=frames.ESS_SPEC_CAPTION,
        )
    elif ess_optimum is not None and ess_optimum.below_minimum and ess_curve is not None:
        # **최소 규격에 못 미치면 사양 표를 싣지 않는다** (50세션 3-3). 회수기간도
        # 목표별 사양도 낼 것이 없다 — 살 물건이 없기 때문이다. 대신 이 건물의
        # 사실인 필요 출력·용량·방전시간은 낸다. **확인되지 않은 판정을 적지 않는다.**
        entries["ess"] = MeasureEntry(
            kind=measure_kind("ess"),
            conclusion=body_lines(ess_optimum.notices)[0]
            if ess_optimum.notices
            else NOT_VIABLE_CONCLUSION,
            saving=f"{_UNPRICED} — 최소 규격에 못 미쳐 사양을 정하지 않았습니다.",
            saving_annual=f"{_UNPRICED} — 최소 규격에 못 미쳐 사양을 정하지 않았습니다.",
            has_saving=False,
            investment=f"{_UNPRICED} — 사양 미정",
            payback=_UNPRICED,
            certainty=str(Certainty.HIGH),
            notices=ess_optimum.notices,
            actionable=False,
            facts=(
                ("요금적용전력", f"{ess_curve.baseline_demand_kw:,.0f} kW"),
                ("필요 출력", f"{ess_optimum.required_power_kw:,.1f} kW"),
                ("필요 용량", f"{ess_optimum.required_capacity_kwh:,.1f} kWh"),
                ("방전시간", f"{ess_optimum.required_discharge_hours:,.2f}h"),
                ("상업용 최소 규격", f"{ess_optimum.minimum_power_kw:,.0f} kW"),
            ),
        )
    elif ess_optimum is not None and not ess_optimum.viable and ess_curve is not None:
        # **켠 수단이 산출물에서 사라지면 안 된다** (48세션). 성립하는 목표가
        # 없어 카드가 목표를 제시하지 않은 경우인데, 장을 통째로 빼면 「검토하지
        # 않았다」 와 구분되지 않는다. 계약전력 「하향 여지 없음」·잉여 0 과 같은
        # 틀로 적는다 — 결론과 **왜 없는지 보이는 지표**, 실행 주의사항은 없다.
        entries["ess"] = MeasureEntry(
            kind=measure_kind("ess"),
            conclusion=NOT_VIABLE_CONCLUSION,
            saving=f"{_UNPRICED} — 성립하는 목표가 없어 사양을 정하지 않았습니다.",
            saving_annual=f"{_UNPRICED} — 성립하는 목표가 없어 사양을 정하지 않았습니다.",
            has_saving=False,
            investment=f"{_UNPRICED} — 사양 미정",
            payback=_UNPRICED,
            certainty=str(Certainty.HIGH),
            notices=ess_optimum.notices,
            actionable=False,
            facts=(
                ("요금적용전력", f"{ess_curve.baseline_demand_kw:,.0f} kW"),
                ("성립 한계 방전시간", f"{ess_curve.viable_limit_hours:,.2f}h"),
                ("가장 짧은 방전시간", f"{_shortest_discharge_hours(ess_curve):,.2f}h"),
            ),
            spec_table=frames.ess_spec_rows(
                frames.ess_spec_frame(
                    ess_optimum, baseline_demand_kw=ess_curve.baseline_demand_kw
                )
            ),
            spec_caption=frames.ESS_SPEC_CAPTION,
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
    worksheets: tuple[Worksheet, ...] = ()
    """계산 근거 표 (22세션 2절). 화면 카드가 접어 둔 것과 **같은 표**다."""
    tariff_table: TariffTable | None = None
    """부록 B 의 요금표 줄. 없으면 그 줄만 빠진다."""
    ess_cases: pd.DataFrame | None = None
    """ESS 조달 사례. 17세션에 화면에서 뺀 표가 부록 A 로 간다."""
    temperature: pd.Series | None = None
    """시간별 기온 (℃). PPT 「전력사용현황 및 부하패턴」이 사용량과 겹쳐 그린다
    (38세션 2-1). **없으면 사용량만 그린다** — 지역은 선택 입력이고, 고른 격자·
    기간을 사전 취득분이 덮지 못할 수도 있다 (화면과 같은 규칙)."""

    @property
    def prepared(self) -> dt.date:
        return self.prepared_on if self.prepared_on is not None else dt.date.today()

    @property
    def building(self) -> str:
        return self.building_name or self.usage.meta.source_name

    def appendix(self) -> tuple[str, ...]:
        """보고서 부록에 실을 **참고 등급** 전부 (19세션 1절).

        화면에서 뺀 문구가 어디에도 남지 않으면 그냥 사라진 것이다. 요금·진단·
        조합·수단이 낸 참고를 한자리에 모아 중복을 지우고 싣는다.
        """
        groups: list[tuple[Notice, ...]] = [self.bill.notices]
        if self.diagnosis is not None:
            groups.append(self.diagnosis.notices)
        if self.comparison is not None:
            groups.append(self.comparison.notices)
        groups.extend(entry.notices for entry in self.measures)
        return tuple(item.text for item in report_appendix(*groups))

    def appendix_data(self) -> AppendixData:
        """부록 A·B·C 재료를 **한 번에** 만든다 (22세션 3절).

        Word 와 Excel 이 같은 것을 실어야 하므로 만드는 자리를 하나로 둔다.
        """
        groups: list[tuple[Notice, ...]] = [self.bill.notices]
        if self.diagnosis is not None:
            groups.append(self.diagnosis.notices)
        if self.comparison is not None:
            groups.append(self.comparison.notices)
        groups.extend(entry.notices for entry in self.measures)
        grounds = tuple(
            (entry.kind.key, texts(report_body(entry.notices), Severity.BASIS))
            for entry in self.measures
            if texts(report_body(entry.notices), Severity.BASIS)
        )
        return AppendixData(
            worksheets=self.worksheets,
            grounds=grounds,
            cases=self.ess_cases,
            limits=known_limits(*groups),
            assumptions_rows=reference_rows(self.tariff_table),
        )

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


def _para(document: DocumentType, text: str = "", *, style: str | None = None) -> Paragraph:
    """문단 하나. **마크다운을 벗겨 적는다** (39세션 2-6).

    Word 도 PPT 와 같이 마크다운을 해석하지 않아 ``**`` 가 글자로 찍힌다. 벗기는
    자리는 :func:`kwise.report.notices.plain_text` 하나이고, 글자를 쓰는 함수가
    그것을 지나는지 시험이 훑는다.
    """
    if style is not None:
        return document.add_paragraph(plain_text(text), style=style)
    return document.add_paragraph(plain_text(text))


def _heading(document: DocumentType, text: str, *, level: int) -> Paragraph:
    """제목 하나. 문단과 같은 이유로 벗겨 적는다."""
    return document.add_heading(plain_text(text), level=level)


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
            cell.text = plain_text(str(value))
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(9)
                    if header and row_index == 0:
                        run.font.bold = True
    document.add_paragraph()


def _add_figure(document: DocumentType, png: bytes, caption: str) -> None:
    document.add_picture(io.BytesIO(png), width=_FIGURE_WIDTH)
    document.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    label = document.add_paragraph(plain_text(caption))
    label.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in label.runs:
        run.font.size = Pt(9)
        run.font.italic = True


def _add_bullets(document: DocumentType, lines: Iterable[str]) -> None:
    for line in lines:
        _para(document, line, style="List Bullet")


def _conclusion(document: DocumentType, text: str) -> None:
    """**결론 한 줄.** 절의 첫 문단이며 굵게 쓴다 — 데이터를 앞에 놓지 않는다."""
    paragraph = document.add_paragraph()
    run = paragraph.add_run(plain_text(text))
    run.bold = True


# ===================================================================== 장별


def _cover(document: DocumentType, sections: DocumentSections) -> None:
    title = _heading(document, DOCUMENT_TITLE, level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle = _para(document, sections.building)
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
    _heading(document, f"{number}장 {CHAPTER_SUMMARY}", level=1)
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
    _para(document, caution + NOT_INCLUDED_NOTICE + " 자세한 한계는 마지막 장에 있습니다.")
    # 금액은 천 원 단위로 절사해 적는다 (14세션 1절). 항목 합과 합계가 어긋날 수 있다.
    _para(document, TRUNCATION_FOOTNOTE)
    document.add_page_break()


def _chapter_diagnosis(document: DocumentType, sections: DocumentSections, number: int) -> None:
    _heading(document, f"{number}장 {CHAPTER_DIAGNOSIS}", level=1)
    diagnosis = sections.diagnosis
    meta = sections.usage.meta

    # ---- 데이터 개요와 품질
    _heading(document, f"{number}.1 데이터 개요와 품질", level=2)
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
    _heading(document, f"{number}.2 부하 패턴", level=2)
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
                "운영시간 외 부하 비중",
                f"{(pattern.off_hours_energy_share or 0) * 100:,.1f}%",
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
    _heading(document, f"{number}.3 피크 특성", level=2)
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
        _heading(document, f"{number}.4 현재 요금 구조", level=2)
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
        _heading(document, f"{number}.5 계약전력 적정성", level=2)
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
        _para(document, CONTRACT_CHANGE_WARNING)
    document.add_page_break()


def _chapter_measures(document: DocumentType, sections: DocumentSections, number: int) -> None:
    _heading(document, f"{number}장 {CHAPTER_MEASURES}", level=1)
    _para(
        document,
        "검토한 수단만 싣습니다. 투자비 순이며, 보지 않은 수단은 마지막 장의 "
        "「검토 범위」에 있습니다.",
    )
    for index, entry in enumerate(sections.measures, start=1):
        _heading(document, f"{number}.{index} {entry.title}", level=2)
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
        # **표가 있으면 그림보다 먼저다** (46세션). ESS 의 목표별 사양 표가
        # 「이 목표는 어디서 나왔나」 에 답하므로 그림보다 앞에 와야 한다.
        if entry.spec_table:
            _add_table(document, [list(row) for row in entry.spec_table])
            if entry.spec_caption:
                _para(document, entry.spec_caption)
        if entry.figure is not None:
            _add_figure(document, entry.figure, f"그림 {number}-{index}. {entry.figure_caption}")
        if entry.cautions:
            _para(document, "주의사항")
            _add_bullets(document, entry.cautions)
    document.add_page_break()


def _chapter_comparison(document: DocumentType, sections: DocumentSections, number: int) -> None:
    _heading(document, f"{number}장 {CHAPTER_COMPARISON}", level=1)
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
    _para(
        document,
        "**조합마다 요금을 다시 계산했습니다.** 수단별 절감액의 단순 합이 아닙니다 — "
        "태양광이 사용량을 줄이면 최적 선택요금이 바뀌고, ESS 가 피크를 낮추면 "
        "기본요금 기반이 달라집니다.",
    )
    _add_figure(
        document, figures.combination_png(comparison), f"그림 {number}-1. 조합별 절감액과 투자비"
    )

    if sections.sensitivity:
        _heading(document, f"{number}.1 감도", level=2)
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
        _para(document, SCENARIO_NAME_CAVEAT)
    document.add_page_break()


def _chapter_scope(document: DocumentType, sections: DocumentSections, number: int) -> None:
    _heading(document, f"{number}장 {CHAPTER_SCOPE}", level=1)
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

    _heading(document, f"{number}.1 미포함 요금요소", level=2)
    _para(document, NOT_INCLUDED_NOTICE)
    _para(document, TRUNCATION_FOOTNOTE)

    _heading(document, f"{number}.2 계약전력 변경 시 주의", level=2)
    _para(document, CONTRACT_CHANGE_WARNING)

    _heading(document, f"{number}.3 추적성", level=2)
    _add_bullets(document, sections.bill.traceability())
    _add_bullets(document, DATA_SOURCES)

    # **참고 등급은 부록 C 로 옮겼다** (22세션 3절). 19세션에 여기 5.5절을 두었는데
    # 부록 C(알려진 한계)와 성격이 같아 같은 말이 두 곳에 실릴 자리였다.


# ===================================================================== 조립


def _appendix_a(document: DocumentType, sections: DocumentSections) -> None:
    """부록 A — **계산 근거 표와 근거 등급 문구, 그리고 조달 사례.**

    화면은 접힘 안에 두고 보고서는 펼친다. 나중에 혼자 읽는 문서이고, 그때
    묻는 것이 바로 「이 숫자가 어떻게 나왔나」다 (19세션 1절).
    """
    data = sections.appendix_data()
    if not data.worksheets and not data.grounds:
        return
    document.add_page_break()
    _heading(document, APPENDIX_TITLES["A"], level=1)
    _para(document, "화면에서 접어 둔 계산 근거입니다. 산식과 대입한 값을 나란히 실었습니다.")
    grounds = dict(data.grounds)
    for sheet in data.worksheets:
        _heading(document, sheet.title, level=2)
        _add_table(document, [list(COLUMNS), *[list(row) for row in sheet.frame().to_numpy()]])
        lines = grounds.get(sheet.key, ())
        if lines:
            _add_bullets(document, lines)
    if data.cases is not None and not data.cases.empty:
        _heading(document, "ESS 조달 사례", level=2)
        _para(document, "투자비 회귀의 원자료입니다. 화면에서는 뺐고(17세션) 여기에 싣습니다.")
        _add_table(
            document,
            [
                [str(name) for name in data.cases.columns],
                *[[str(value) for value in row] for row in data.cases.to_numpy()],
            ],
        )


def _appendix_b(document: DocumentType, sections: DocumentSections) -> None:
    """부록 B — **기준 데이터에서 만든다.** 손으로 옮겨 적지 않는다."""
    rows = sections.appendix_data().assumptions_rows
    if not rows:
        return
    document.add_page_break()
    _heading(document, APPENDIX_TITLES["B"], level=1)
    _para(
        document,
        "이 산출에 쓴 기준 값입니다. 법령 유래와 우리 판단값을 구분해 실었으며, "
        "값은 기준 데이터 파일에서 그대로 가져옵니다.",
    )
    _add_table(
        document,
        [["구분", "항목", "값", "근거", "확인일"], *[list(row) for row in rows]],
    )


def _appendix_c(document: DocumentType, sections: DocumentSections) -> None:
    """부록 C — 알려진 한계와 전제. **19세션 5.5절이 여기로 왔다.**"""
    lines = sections.appendix_data().limits
    if not lines:
        return
    document.add_page_break()
    _heading(document, APPENDIX_TITLES["C"], level=1)
    _para(
        document,
        "결과를 읽는 데 필요한 한계와 전제입니다. 화면에서는 본문을 가리지 않도록 "
        "빼고 여기에 모았습니다.",
    )
    _add_bullets(document, lines)


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

    # **부록 셋** (22세션 3절). 본문에서 뺀 것이 여기 모인다.
    _appendix_a(document, sections)
    _appendix_b(document, sections)
    _appendix_c(document, sections)
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
