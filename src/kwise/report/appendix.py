"""보고서 부록 A·B·C (22세션 3절).

화면에서 뺀 것이 어디로 가는지 **한자리에 모은다.** 19세션이 참고 등급을 5.5절
로 보냈는데, 근거(계산 근거·산식)와 기준 데이터는 갈 곳이 없었다.

    부록 A  산출 근거 상세 — 계산 근거 표 · 근거 등급 문구 · 조달 사례
    부록 B  적용 기준 데이터 — 요금표·법령 유래 값·판단값
    부록 C  알려진 한계와 전제 — 부록 D 목록 · 참고 등급 문구

**부록 B 는 손으로 옮겨 적지 않는다.** ``rules_kr.json`` 과
``assumptions.json`` 에서 만든다 — 값을 고치면 보고서가 따라오고, 옮겨 적으면
그 순간부터 갈라진다 (요구사항서 12장).

**5.5절은 부록 C 로 옮겼다.** 참고 등급이 두 곳에 있으면 어느 쪽이 정본인지
알 수 없다. 화면에서 뺀 문구는 여기 한 곳에 있다.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from kwise.notices import Notice, report_appendix, texts
from kwise.report.notices import KNOWN_LIMITS
from kwise.report.worksheet import COLUMNS, Worksheet
from kwise.rules import RuleItem, assumptions, rules
from kwise.tariff import TariffTable

__all__ = [
    "APPENDIX_TITLES",
    "AppendixData",
    "basis_data_frame",
    "known_limits",
    "reference_rows",
    "worksheet_frame",
]

#: 부록 제목. **Word·Excel 이 같은 이름을 쓴다.**
APPENDIX_TITLES: dict[str, str] = {
    "A": "부록 A 산출 근거 상세",
    "B": "부록 B 적용 기준 데이터",
    "C": "부록 C 알려진 한계와 전제",
}


@dataclass(frozen=True)
class AppendixData:
    """부록 셋의 재료. **만드는 곳은 하나다.**"""

    worksheets: tuple[Worksheet, ...] = ()
    grounds: tuple[tuple[str, tuple[str, ...]], ...] = ()
    """(수단 이름, 근거 등급 문구). 계산 근거 표 옆에 붙는다."""
    cases: pd.DataFrame | None = None
    """ESS 조달 사례. **17세션에 화면에서 뺀 표가 여기 온다.**"""
    limits: tuple[str, ...] = ()
    assumptions_rows: tuple[tuple[str, ...], ...] = ()


def worksheet_frame(sheets: tuple[Worksheet, ...]) -> pd.DataFrame:
    """부록 A 를 표 하나로. **Excel 시트가 이것을 그대로 쓴다.**"""
    rows: list[dict[str, str]] = []
    for sheet in sheets:
        for _, row in sheet.frame().iterrows():
            rows.append({"수단": sheet.title, **{name: row[name] for name in COLUMNS}})
    return pd.DataFrame(rows, columns=["수단", *COLUMNS])


def reference_rows(table: TariffTable | None = None) -> tuple[tuple[str, ...], ...]:
    """부록 B — **기준 데이터 파일에서 만든다.**

    (구분, 항목, 값, 근거, 확인일) 다섯 열이다. 법령 유래와 판단값을 섞지 않고
    구분 열로 가른다 — 어느 것이 약관이고 어느 것이 우리 판단인지가 이 보고서를
    읽는 사람에게 가장 중요한 갈래다.
    """
    rows: list[tuple[str, ...]] = []
    if table is not None:
        rows.append(
            (
                "요금표",
                table.source,
                f"시행일 {table.effective_date}" + ("" if table.verified else " · 청구서 미검증"),
                table.source,
                "",
            )
        )

    def add(kind: str, item: RuleItem) -> None:
        rows.append(
            (
                kind,
                item.label or item.key,
                _short(item.value),
                item.source or "—",
                item.verified_on.isoformat() if item.verified_on else "—",
            )
        )

    for key in rules().item_keys():
        add("법령 유래", rules()[key])
    for key in assumptions().item_keys():
        add("판단값", assumptions()[key])
    return tuple(rows)


def _short(value: object) -> str:
    """값 한 칸. **길면 자른다** — 표 한 칸에 목록 전체를 넣지 않는다."""
    if isinstance(value, list | tuple):
        text = ", ".join(_short(item) for item in value)
    elif isinstance(value, dict):
        text = ", ".join(f"{key}={_short(item)}" for key, item in value.items())
    elif isinstance(value, float):
        text = f"{value:,.6g}"
    else:
        text = str(value)
    return text if len(text) <= 60 else text[:57] + "…"


def basis_data_frame(table: TariffTable | None = None) -> pd.DataFrame:
    """부록 B 표."""
    return pd.DataFrame(
        list(reference_rows(table)), columns=["구분", "항목", "값", "근거", "확인일"]
    )


def known_limits(*notices: tuple[Notice, ...]) -> tuple[str, ...]:
    """부록 C — 알려진 한계 + **참고 등급 문구** (5.5절에서 옮겼다).

    같은 말을 두 번 싣지 않는다. 부록 D 목록과 참고 문구가 겹치는 자리가 있어
    (미포함 요금요소·역률 추정이 그렇다) 앞 30자로 견주어 걷어낸다.
    """
    out: list[str] = list(KNOWN_LIMITS)
    seen = {line[:30] for line in out}
    for line in texts(report_appendix(*notices)):
        if line[:30] in seen:
            continue
        seen.add(line[:30])
        out.append(line)
    return tuple(out)
