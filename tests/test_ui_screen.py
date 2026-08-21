"""화면 표시 시험 (요구사항서 10.7 — 12세션).

**계산이 아니라 화면을 지킨다.** 여기서 막는 것 여섯.

    ① 내려받기 단추를 눌러도 계산 결과가 날아가지 않는다 (rerun 문제)
    ② [자세히] 가 실제로 열리는 파일을 가리킨다 — 죽은 링크를 내느니 비활성이 낫다
    ③ 참고 등급은 화면에 없고 보고서에만 있다
    ④ 코드 식별자(general_b, prior_peaks …)가 화면 문구에 없다
    ⑤ 개선 수단 카드는 모두 접힌 채로 시작한다
    ⑥ 감도 차트·원자료 표는 화면에 없고 Excel 에는 있다

소스 문자열을 훑는 시험이 몇 개 있다. 화면을 띄우지 않고도 "이 문구가 화면에
나가는가" 를 물을 수 있는 가장 싼 방법이고, 되돌아가는 것을 막는 것이 목적이다.
"""

from __future__ import annotations

import ast
import datetime as dt
import functools
import inspect
import re
from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from kwise.compare import CombinationSpec
from kwise.io import load_usage
from kwise.measures import AppliedMeasure, EssResult, measure_kind
from kwise.notices import Notice, basis, block, dedupe, info, warn
from kwise.report.excel import SHEET_ORDER
from kwise.tariff import TariffSelection, TariffTable, load_tariff
from kwise.ui import text
from kwise.ui.labels import contract_label, option_label, selection_label, voltage_label
from kwise.ui.notices import (
    Severity,
    appendix_notices,
    report_notices,
    screen_notices,
    tooltip_text,
)
from kwise.ui.pipeline import ContractForm
from kwise.ui.spec import MEASURES
from kwise.ui.views.diagnose import missing_lines

VIEWS = Path("src") / "kwise" / "ui" / "views"
APP = Path("src") / "kwise" / "ui" / "app.py"


@pytest.fixture(scope="module")
def table() -> TariffTable:
    return load_tariff()


HANGUL = re.compile(r"[가-힣]")


def _strings(path: Path) -> list[str]:
    """화면에 나갈 수 있는 문구만 고른다.

    한글이 든 문자열만 본다. ``"tariff_switch" in enabled`` 같은 **키 비교는
    화면 문구가 아니다** — 그것까지 막으면 사전 키를 못 쓴다. 문서 문자열은
    화면에 나가지 않으므로 뺀다.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    kinds = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    docs = {
        node.body[0].value.value
        for node in ast.walk(tree)
        if isinstance(node, kinds)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value not in docs
        and HANGUL.search(node.value)
    ]


# ======================================================== ① 내려받기와 rerun


DOWNLOAD_APP = """
import streamlit as st

from kwise.ui.artifacts import recall, remember

st.session_state.setdefault("runs", 0)
st.session_state["runs"] += 1

# 계산 결과. 매 rerun 마다 다시 그려진다.
st.metric("절감액", "3,152만원")

if st.button("만들기", key="build"):
    remember("excel", b"payload-bytes", "result.xlsx", token="t1")

artifact = recall("excel", token="t1")
if artifact is not None:
    st.download_button("내려받기", data=artifact.payload, file_name=artifact.filename, key="dl")
"""


def _app() -> AppTest:
    return AppTest.from_string(DOWNLOAD_APP, default_timeout=30)


def test_내려받기를_눌러도_결과가_남는다() -> None:
    """**단추를 누르면 rerun 이 돈다.** 그 rerun 에서도 결과와 단추가 있어야 한다."""
    app = _app().run()
    assert not app.button(key="build").value
    assert len(app.download_button) == 0

    app.button(key="build").click().run()
    assert len(app.download_button) == 1, "만든 직후 내려받기 단추가 있어야 합니다."
    assert app.metric[0].value == "3,152만원"

    app.download_button(key="dl").click().run()
    assert len(app.download_button) == 1, "내려받기 rerun 에서 단추가 사라졌습니다."
    assert app.metric[0].value == "3,152만원", "내려받기 rerun 에서 결과가 날아갔습니다."


def test_입력이_바뀌면_묵은_산출물을_버린다() -> None:
    """옛 바이트를 내려받게 두면 화면 숫자와 파일 내용이 어긋난다."""
    app = _app().run()
    app.button(key="build").click().run()
    assert len(app.download_button) == 1

    app.session_state["_kwise_artifacts"]["excel"] = app.session_state["_kwise_artifacts"][
        "excel"
    ].__class__(payload=b"stale", filename="old.xlsx", token="t0")
    app.run()
    assert len(app.download_button) == 0


def test_비교_화면이_바이트를_세션에_담는다() -> None:
    """만들기 분기 안에서 단추를 그리면 안 된다 — 소스로 못박는다."""
    source = (VIEWS / "compare.py").read_text(encoding="utf-8")
    assert "remember(" in source
    assert "recall(" in source
    build_at = source.index('if st.button("Excel 만들기"')
    offer_at = source.index('_offer(\n            slot="excel"')
    assert build_at < offer_at
    # 내려받기 호출이 만들기 분기 밖(들여쓰기 두 칸)에 있어야 한다.
    assert "\n        _offer(\n" in source


# ======================================================== ② 화면에 링크가 없다


def test_화면_소스에_하이퍼링크가_없다() -> None:
    """**화면에서 링크를 전면 제거했다** (16세션 4절).

    마크다운 링크(``[글](주소)``)도, 주소를 그대로 적은 자리도 없어야 한다.
    요지는 ``help=`` 툴팁으로 나간다.
    """
    pattern = re.compile(r"\]\(\s*(?:https?://|app/static/|[A-Za-z0-9_.-]+\.html)")
    offenders: list[str] = []
    for path in sorted((Path("src") / "kwise" / "ui").rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue  # 주석은 화면에 나가지 않는다
            if pattern.search(line):
                offenders.append(f"{path}:{number} {line.strip()}")
    assert offenders == [], "화면에 하이퍼링크가 남아 있습니다: " + " / ".join(offenders)


def test_링크_기계장치를_걷어냈다() -> None:
    """정적 사본·앵커 주소를 만드는 함수가 남아 있으면 다시 쓰이게 된다."""
    import kwise.ui.anchors as anchors

    for name in ("manual_href", "detail_suffix", "app_static_dir", "static_manual_path"):
        assert not hasattr(anchors, name), f"{name} 이 남아 있습니다."
    assert not (Path("src") / "kwise" / "ui" / "static").exists()


# ======================================================== ③ 세 등급


def test_등급마다_가는_자리가_다르다() -> None:
    """**넷을 넷으로 나눈다** (19세션 1절).

    차단·주의  화면 본문
    근거      화면 툴팁 · 보고서 본문
    참고      화면에 없음 · 보고서 부록
    """
    items = (
        block("계약 정보가 없어 요금을 산출하지 않았습니다.", fact="diagnose.no_contract"),
        warn("2023-11 결측률 32.3% — 신뢰 제한", fact="quality.month_missing_rate:2023-11"),
        basis(
            "투자비는 용량(kWp) × 1,200,000 원/kWp 로 냈습니다 (출처: 사용자 입력).",
            fact="solar.investment_unit_cost",
        ),
        info("기후환경요금과 연료비조정요금은 미포함입니다.", fact="tariff.not_included"),
    )

    on_screen = screen_notices(items)
    assert [item.severity for item in on_screen] == [Severity.BLOCK, Severity.WARN]

    grounds = tooltip_text(items)
    assert "투자비는 용량(kWp)" in grounds
    assert "기후환경요금" not in grounds, "참고는 툴팁에도 없다"
    assert "신뢰 제한" not in grounds, "주의는 본문이 자리다"

    # **화면에서 사라진 문구는 반드시 산출물에 있다.**
    body = tuple(item.text for item in report_notices(items))
    appendix = tuple(item.text for item in appendix_notices(items))
    assert len(body) == 3 and len(appendix) == 1
    assert set(body) | set(appendix) == {item.text for item in items}


def test_등급은_발신처가_붙인다() -> None:
    """계산 모듈이 :class:`Notice` 를 직접 만든다 (19세션 2절).

    문자열 리스트가 남아 있으면 폴백이 주의로 밀어 넣는다 — 18세션까지 82%가
    그렇게 떨어졌다. 실제 결과 객체가 등급을 들고 오는지 본다.
    """
    from kwise.quality import check_quality

    quality = check_quality(load_usage(SAMPLE))
    assert quality.notices, "품질 검사가 안내를 내지 않았습니다."
    assert all(isinstance(item, Notice) for item in quality.notices)
    grades = {item.severity for item in quality.notices}
    assert Severity.BASIS in grades, "근거 등급이 하나도 없습니다."
    assert not hasattr(quality, "warnings"), "문자열 리스트가 남아 있습니다."


def test_같은_사실을_두_번_내지_않는다() -> None:
    same = (
        warn("2023-11 결측률 32.3% — 품질 점검", fact="quality.month_missing_rate:2023-11"),
        warn(
            "2023-11 결측률 32.3% — 요금 계산에서 제외",
            fact="quality.month_missing_rate:2023-11",
        ),
    )
    assert len(dedupe(same)) == 1


def test_등급_추정_폴백이_남아_있지_않다() -> None:
    """**추정 장치를 지웠다** (21세션 0절).

    18세션까지는 문구를 부분 일치로 훑어 등급을 매겼고 82%가 기본값으로
    떨어졌다. 19·20세션에 등급과 사실 ID 를 발신처로 옮겼으므로 추정할 일이
    없다. 되살아나면 같은 병이 다시 시작된다.
    """
    from kwise import notices as core
    from kwise.ui import notices as view

    for module in (core, view):
        source = Path(module.__file__ or "").read_text(encoding="utf-8")
        for banned in ("WARN_PATTERNS:", "INFO_PATTERNS:", "def classify(", "def as_notice("):
            assert banned not in source, f"{module.__name__} 에 {banned} 가 남아 있습니다."
        assert "def _fingerprint(" not in source, "지문 폴백이 남아 있습니다."
    assert not hasattr(view, "classify")
    assert not hasattr(core, "as_notice")


def test_사실_ID_없이는_안내를_만들_수_없다() -> None:
    """``fact`` 가 **필수**다 (21세션 0절). 런타임 경고보다 mypy 가 강하다."""
    with pytest.raises(TypeError):
        warn("사실 ID 없는 안내")  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="사실 ID"):
        Notice(Severity.WARN, "빈 사실 ID", "")


# ======================================================== ④ 개발자 언어


CODE_WORDS = re.compile(
    r"\b(general_b|general_a|educational|industrial|high_a|high_b|high_c|low_voltage"
    r"|prior_peaks|verified=|tariff_switch|power_factor=|billing_options)\b"
)
SCREEN_VIEWS = ("diagnose.py", "measures.py", "compare.py")


@pytest.mark.parametrize("name", SCREEN_VIEWS)
def test_화면_문구에_코드_식별자가_없다(name: str) -> None:
    """``최적은 general_b/high_a/II 입니다`` 같은 문구를 막는다."""
    offenders = [item for item in _strings(VIEWS / name) if CODE_WORDS.search(item)]
    assert not offenders, offenders


@pytest.mark.parametrize("name", SCREEN_VIEWS)
def test_개발자에게_시키는_말이_화면에_없다(name: str) -> None:
    banned = ("로 주입", "요구사항서", "verified")
    offenders = [item for item in _strings(VIEWS / name) if any(word in item for word in banned)]
    assert not offenders, offenders


def test_선택요금을_사람말로_적는다(table: TariffTable) -> None:
    selection = TariffSelection(contract_type="general_b", voltage="high_a", option="II")
    label = selection_label(table, selection)
    assert "general_b" not in label and "high_a" not in label and "II" not in label
    assert "선택Ⅱ" in label
    assert option_label("II") == "선택Ⅱ"
    assert contract_label(table, "general_b")
    assert voltage_label(table, "general_b", "high_a")


# ======================================================== ⑤ 카드는 접혀 있다


def test_켜지_않은_카드는_펼쳐지지_않는다() -> None:
    """일곱 개가 펼쳐져 있으면 화면이 스크롤 두 배가 된다.

    16세션 0-2 로 **켠 카드는 펼친 채로 남는다.** 켜지 않은 카드는 확장 패널
    자체가 만들어지지 않으므로, 카드 본문을 여는 자리는 토글 하나뿐이다.
    """
    source = (VIEWS / "measures.py").read_text(encoding="utf-8")
    assert "st.session_state[opened_key] = False" in source
    body = source[source.index("def _card(") : source.index("def _caution(")]
    assert body.index("if not enabled:") < body.index("with st.expander(")


def test_켠_카드는_다시_그려도_펼쳐져_있다() -> None:
    """± 를 세 번 눌러도 카드가 접히지 않아야 한다 (16세션 0-2)."""
    screen = _running(measure_on_power_factor=True)
    assert not screen.exception, screen.exception
    assert screen.session_state["_kwise_opened_power_factor"] is True
    target = screen.number_input(key="measure_power_factor_target")
    for _ in range(3):
        target = screen.number_input(key="measure_power_factor_target")
        screen = target.increment().run(timeout=600)
        assert not screen.exception, screen.exception
        assert screen.session_state["_kwise_opened_power_factor"] is True
        assert screen.number_input(key="measure_power_factor_target") is not None


def _tooltips() -> list[tuple[str, str]]:
    """화면 툴팁(``help=``)을 전부 뽑는다. **소스를 훑는다** — 띄우지 않고도 본다."""
    found: list[tuple[str, str]] = []

    def literal(node: ast.expr) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            parts = [item.value if isinstance(item, ast.Constant) else "{}" for item in node.values]
            return "".join(str(part) for part in parts)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = literal(node.left), literal(node.right)
            if left is not None and right is not None:
                return left + right
        return None

    for path in sorted((Path("src") / "kwise" / "ui").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "help":
                    continue
                text = literal(keyword.value)
                if text and HANGUL.search(text):
                    found.append((f"{path.name}:{node.lineno}", text))
    return found


def test_툴팁에_오타와_구두점_오류가_없다() -> None:
    """**툴팁을 전수로 훑는다** (21세션 2절).

    19세션에 근거가 툴팁으로 내려가면서 수가 크게 늘었다. 한 번에 하나씩
    눈으로 보던 방식으로는 오타와 구두점 오류가 남는다.
    """
    from kwise.ui.anchors import ANCHORS

    # 화면 문구와 매뉴얼 요지를 **한 잣대로** 본다. 둘 다 물음표 안에 뜬다.
    tooltips = _tooltips() + [(f"anchor:{item.key}", item.covers) for item in ANCHORS]
    assert len(tooltips) >= 40, "툴팁을 못 찾았습니다. 추출기가 깨졌습니다."
    for where, tip in tooltips:
        body = tip.strip()
        assert "평잉" not in body, f"{where} 오타"
        assert "  " not in body, f"{where} 이중 공백: {body[:40]}"
        assert not body.endswith((",", "·", "및", "로", "과")), f"{where} 끊긴 문장: {body[-20:]}"
        assert body.endswith((".", ")", "]", "%")), f"{where} 종결 부호가 없습니다: {body[-20:]}"


def test_지표_툴팁이_산식과_의미_두_줄이다() -> None:
    """**산식 한 줄 + 의미 한 줄** (21세션 2절).

    산식만 적으면 "그래서 어떻다는 것인가" 가 없어 읽고도 할 일이 없다.
    주말 부하 비율이 그랬다 — 나눗셈 한 줄이 전부였다.
    """
    from kwise.ui.views.diagnose import _pattern_formulas

    tips = _pattern_formulas(object())
    assert set(tips) == {
        "load_factor",
        "base_load_ratio",
        "weekend_ratio",
        "off_hours_energy_share",
    }
    for key, tip in tips.items():
        formula, _, meaning = tip.partition("\n\n")
        assert "÷" in formula, f"{key} 에 산식이 없습니다: {formula}"
        assert meaning.strip(), f"{key} 에 의미 줄이 없습니다."
        assert meaning.strip().endswith("다."), f"{key} 의미 줄이 문장이 아닙니다: {meaning}"


def test_확인사항이_한_묶음이다() -> None:
    source = (VIEWS / "measures.py").read_text(encoding="utf-8")
    assert "확인사항" in source


# ======================================================== ⑥ 감도


def test_감도_차트와_원자료가_화면에_없다() -> None:
    source = (VIEWS / "compare.py").read_text(encoding="utf-8")
    assert "sensitivity_chart" not in source, "감도 차트는 화면에서 뺐습니다."
    assert "시나리오 3행" not in source


def test_감도_상세는_excel_에_남는다() -> None:
    assert "감도 상세" in SHEET_ORDER and "감도" in SHEET_ORDER


def test_월별_명세의_값이_한글이다() -> None:
    """**열 이름만 옮기면 값 칸에 코드가 남는다** (21세션 3-1).

    `계절` 칸에 ``spring_fall`` 이 그대로 있었다. 12세션 규약(화면에 코드
    식별자를 내지 않는다)을 값이 깨고 있던 자리다.
    """
    import pandas as pd

    from kwise.report import localize
    from kwise.report.columns import VALUE_LABELS

    frame = pd.DataFrame(
        {"season": ["spring_fall", "summer", "winter"], "total_won": [1.0, 2.0, 3.0]}
    )
    shown = localize(frame)
    assert list(shown["계절"]) == ["봄·가을", "여름", "겨울"]
    assert set(VALUE_LABELS["season"]) == {"spring_fall", "summer", "winter"}
    # 번역표가 없는 열은 건드리지 않는다.
    assert list(shown["합계(원)"]) == [1.0, 2.0, 3.0]


def test_월별_명세는_결론_열만_낸다() -> None:
    """중간값 넷은 Excel 로 보낸다. **지운 것이 아니다** (21세션 3-2)."""
    from kwise.report.columns import column_label
    from kwise.ui.views.diagnose import SCREEN_MONTHLY_COLUMNS

    for hidden in (
        "demand_basis_kw",
        "demand_before_floor_kw",
        "base_demand_kw",
        "base_fee_factor",
    ):
        assert hidden not in SCREEN_MONTHLY_COLUMNS, hidden
    assert "billing_demand_kw" in SCREEN_MONTHLY_COLUMNS, "기본요금의 근거는 남긴다."

    source = (Path("src") / "kwise" / "report" / "excel.py").read_text(encoding="utf-8")
    detail = source[source.index('sheets["요금 계산 명세"]') :]
    for hidden in ("demand_basis_kw", "demand_before_floor_kw", "base_demand_kw"):
        assert f'"{hidden}"' in detail, f"{column_label(hidden)} 가 Excel 에서도 사라졌습니다."


def test_부분_월에_뜻을_붙인다() -> None:
    """체크만 있고 뜻이 없으면 무엇을 보라는 것인지 알 수 없다 (21세션 3-3)."""
    source = (VIEWS / "diagnose.py").read_text(encoding="utf-8")
    assert "검침 기간이 한 달에 못 미치는 달입니다." in source
    assert "CheckboxColumn" in source


def test_감도를_기준값_괄호_범위로_적는다() -> None:
    assert text.money_range(31_518_402, 28_968_918, 32_657_891) == "3,152만원 (2,897 – 3,266만원)"


# ======================================================== 표기


def test_기간을_자르지_않는다() -> None:
    """``2023-04-25 ~ 2024-04-2`` 처럼 끝이 잘리면 안 된다."""
    start, end = dt.date(2023, 4, 25), dt.date(2024, 4, 27)
    assert text.period(start, end, 369) == "2023-04-25 – 2024-04-27 (369일)"


@pytest.mark.parametrize(
    ("rendered", "expected"),
    [
        (text.won(123_400_000), "123,400,000원"),
        (text.won_short(123_400_000), "1억 2,340만원"),
        (text.kw(12_345.6), "12,345.6 kW"),
        (text.kwh(1_234_567), "1,234,567 kWh"),
        (text.kwp(1_234), "1,234 kWp"),
        (text.count(1_234), "1,234"),
    ],
)
def test_모든_숫자에_세_자리_콤마가_붙는다(rendered: str, expected: str) -> None:
    assert rendered == expected


@pytest.mark.parametrize("name", SCREEN_VIEWS)
def test_숫자를_직접_찍지_않는다(name: str) -> None:
    """콤마 없는 ``{value}`` 서식을 막는다. 표기는 ``ui.text`` 한 곳에서 한다."""
    source = (VIEWS / name).read_text(encoding="utf-8")
    raw = re.findall(r"\{[a-z_.()\[\]]+:\.\d+f\}", source)
    assert not raw, raw


# ======================================================== 세 화면 실주행


SAMPLE = Path("input") / "사용량조회_20240429.csv"


def _running(*, option: str = "II", contract_kw: float = 6_000.0, **state: object) -> AppTest:
    """실제 파일을 올린 상태로 앱을 띄운다.

    **화면 코드는 순수 모듈 시험으로 다 잡히지 않는다.** 배치를 바꾸다 이름 하나를
    빠뜨리면 여기서만 드러난다.
    """
    running = AppTest.from_file(str(APP.resolve()), default_timeout=600)
    running.session_state["upload_bytes"] = SAMPLE.read_bytes()
    running.session_state["upload_name"] = SAMPLE.name
    running.session_state["contract_form"] = ContractForm(
        contract_type="general_b", voltage="high_a", option=option, contract_kw=contract_kw
    )
    for key, value in state.items():
        running.session_state[key] = value
    # **3단계는 「합산효과 계산」 을 누른 뒤에 그린다** (33세션 5절). 시험마다
    # 단추를 눌러 한 벌 더 돌리면 시험 시간이 두 배가 되므로, 누른 것과 같은
    # 상태를 세션에 미리 심는다 — 단추가 하는 일이 그것뿐이다.
    if "combination_pick" not in state:
        running.session_state["combination_pick"] = tuple(
            item.key for item in MEASURES if state.get(f"measure_on_{item.key}")
        )
    return running.run()


# **탭 구조라 세 화면이 한 번에 그려진다** (16세션 1절). 지표 목록도 셋이 이어
# 붙으므로, 어느 탭의 지표인지 경계로 갈라 본다. 1단계의 마지막 지표는
# 「기본요금 비중」이고 3단계의 첫 지표는 「단순 합」이다.
_DIAGNOSE_LAST = "기본요금 비중"
_STAGE3_FIRST = "단순 합"


def _labels(screen: AppTest) -> list[str]:
    return [str(item.label) for item in screen.metric]


def _stage2_metrics(screen: AppTest) -> list[tuple[str, str]]:
    """2단계 카드가 낸 지표만. 앞의 진단과 뒤의 조합을 잘라 낸다."""
    pairs = [(str(item.label), str(item.value)) for item in screen.metric]
    labels = [label for label, _ in pairs]
    start = labels.index(_DIAGNOSE_LAST) + 1 if _DIAGNOSE_LAST in labels else 0
    end = labels.index(_STAGE3_FIRST) if _STAGE3_FIRST in labels else len(pairs)
    return pairs[start:end]


def _stage3_metrics(screen: AppTest) -> list[str]:
    """3단계가 낸 지표만."""
    labels = _labels(screen)
    return labels[labels.index(_STAGE3_FIRST) :] if _STAGE3_FIRST in labels else []


@pytest.fixture(scope="module")
def app() -> AppTest:
    return _running()


def test_진단_화면이_지표부터_낸다(app: AppTest) -> None:
    assert not app.exception, app.exception
    labels = [item.label for item in app.metric]
    assert labels[:4] == ["분석 기간", "최대수요", "부하율", "연간 사용량"]
    assert "1단계" in app.header[0].value


def test_진단_지표에_세_자리_콤마가_있다(app: AppTest) -> None:
    values = {item.label: item.value for item in app.metric}
    assert "," in values["연간 사용량"]


def test_분석_기간이_잘리지_않는다(app: AppTest) -> None:
    """지표 값 자리에 두면 글꼴이 커서 `2023-04-25 – 2024-` 까지만 보였다 (13세션).

    일수를 값에, 날짜 범위를 **delta 자리**(작은 글씨)에 둔다.
    """
    period = next(item for item in app.metric if item.label == "분석 기간")
    assert period.value.endswith("일")
    assert period.delta is not None
    assert text.RANGE in period.delta
    assert period.delta.startswith("2023-04-25") and period.delta.endswith("2024-04-27")


def test_진단_순서가_16세션_3절_그대로다(app: AppTest) -> None:
    """④ 품질 → ⑤ 부하 패턴 → ⑥ 피크 → ⑦ 요금 구조."""
    headings = [str(item.value) for item in app.subheader]
    order = ["데이터 품질", "부하 패턴", "피크 특성", "현재 요금 구조"]
    assert [item for item in headings if item in order] == order


def test_진단에_계약전력_적정성과_개선_여지가_없다(app: AppTest) -> None:
    """**같은 금액을 두 화면에 다른 이름으로 두지 않는다** (16세션 3절).

    적정성은 2단계 7.2 로 옮겼고 개선 여지는 7.1·7.2 와 겹쳐 지웠다.
    """
    headings = [str(item.value) for item in app.subheader]
    assert "계약전력 적정성" not in headings
    assert not [item for item in headings if item.startswith("개선 여지")]


def test_화면에_참고_등급이_없다(app: AppTest) -> None:
    """미포함 요금요소·제도 설명 같은 참고 문구는 산출물에만 있다.

    21세션에 등급 추정 폴백을 지웠으므로 **문구를 훑어 등급을 되묻지 않는다.**
    참고 등급으로 나가는 문구 상수를 발신처에서 가져와 화면에 없음을 본다.
    """
    from kwise.measures.demand_response import DR_ADVISORY
    from kwise.measures.pv_cost import PV_COST_BASIS_NOTE, PV_REFERENCE_NOTE, SCALE_ECONOMY_NOTE
    from kwise.measures.surplus import ELIGIBILITY_NOTICE
    from kwise.tariff import NOT_INCLUDED_NOTICE

    shown = "\n".join(
        [item.value for item in app.warning]
        + [item.value for item in app.error]
        + [item.value for item in app.info]
        + [item.value for item in app.markdown]
    )
    for notice in (
        NOT_INCLUDED_NOTICE,
        DR_ADVISORY,
        ELIGIBILITY_NOTICE,
        PV_COST_BASIS_NOTE,
        SCALE_ECONOMY_NOTE,
        PV_REFERENCE_NOTE,
    ):
        assert notice[:30] not in shown, notice[:30]


def test_화면_어디에도_코드_식별자가_없다(app: AppTest) -> None:
    body = "\n".join(
        [item.value for item in app.markdown]
        + [item.value for item in app.warning]
        + [item.value for item in app.error]
        + [item.value for item in app.info]
        + [str(item.value) for item in app.metric]
    )
    assert not CODE_WORDS.search(body), CODE_WORDS.findall(body)


def test_세_화면이_한_번에_그려진다() -> None:
    """**탭 구조다** (16세션 1절). 옆단 이동 목록도 하단 단추도 없다."""
    screen = _running()
    assert not screen.exception, screen.exception
    headers = [str(item.value) for item in screen.header]
    assert any("1단계" in item for item in headers)
    assert any("2단계" in item for item in headers)
    assert any("3단계" in item for item in headers)
    keys = {item.key for item in screen.button}
    assert "go_measures" not in keys and "go_compare" not in keys


@pytest.fixture(scope="module")
def compare_app() -> AppTest:
    """요금제 전환·계약전력 조정을 켜고 3단계로 간다.

    현행을 **선택Ⅰ** 로 둔다. 이미 가장 유리한 요금제면 전환 조합이 만들어지지 않는다.
    """
    # **계약전력 조정을 함께 켠다.** 파라미터가 붙는 수단이라 13세션의 KeyError
    # 재현 경로다 — 표시 문자열이 키 자리로 흘러들면 여기서 화면이 죽는다.
    return _running(
        option="I",
        measure_on_tariff_switch=True,
        measure_on_contract=True,
    )


def test_비교_화면이_합산효과_지표를_낸다(compare_app: AppTest) -> None:
    assert not compare_app.exception, compare_app.exception
    labels = [str(item.label) for item in compare_app.metric]
    assert _stage3_metrics(compare_app)[:3] == ["단순 합", "합산효과", "차이"]
    # **권장안 지표는 없앴다** (16세션 5절) — 미리 정의된 조합 세트가 사라졌다.
    assert "12개월 환산 절감액" not in labels


def test_비교_화면에_감도가_없다(compare_app: AppTest) -> None:
    """**감도를 화면에서 뺐다** (28세션 5절). 계산은 돌고 산출물에만 실린다."""
    headings = [str(item.value) for item in compare_app.subheader]
    assert "조합 비교" not in headings
    assert "감도" not in headings
    body = " ".join(
        str(item.value) for group in (compare_app.markdown, compare_app.caption) for item in group
    )
    assert "감도 범위" not in body


def test_엑셀을_내려받아도_결과가_남는다(compare_app: AppTest) -> None:
    """**실제 화면에서** 내려받기 rerun 을 견디는지 본다 (12세션·16세션 0-1)."""
    built = compare_app.button(key="build_excel").click().run(timeout=600)
    assert not built.exception, built.exception
    assert len(built.download_button) == 1

    after = built.download_button(key="dl_excel").click().run(timeout=600)
    assert not after.exception, after.exception
    assert len(after.download_button) == 1, "내려받기 뒤 단추가 사라졌습니다."
    assert _stage3_metrics(after)[:3] == ["단순 합", "합산효과", "차이"], (
        "내려받기 뒤 계산 결과가 사라졌습니다."
    )


def test_수단_화면이_기준선을_한_번_밝힌다() -> None:
    """**독립 평가라는 사실과 기준선**이 화면에 한 번은 적혀 있어야 한다 (14세션 2절).

    배경색 상자(``st.info``) 대신 본문 글로 낸다 — 등급(차단·주의)이 색을 쓴다.
    """
    screen = _running(nav_page="2단계 · 개선 수단", measure_on_tariff_switch=True)
    assert not screen.exception, screen.exception
    body = [item.value for item in screen.markdown]
    # 31세션 1-2 에 문구를 다시 썼다. **지켜야 할 사실 셋은 그대로다** —
    # 독립 평가라는 것, 기준선이 현재 요금제와 사용량이라는 것, 합산은 3단계라는 것.
    assert any("독립적으로 평가합니다" in str(item) for item in body), body
    assert any("현재 요금제와 사용량" in str(item) for item in body), body
    assert any("3단계 합산효과" in str(item) for item in body), body
    # 머리말 캡션은 뺐다 (31세션 1-1) — 화면을 보면 아는 사실이었다.
    assert not [item for item in screen.caption if "차례로 놓았습니다" in str(item.value)], (
        "머리말 캡션이 남아 있습니다."
    )


# ======================================================== 13세션


def test_조합에는_등록_키가_들어간다() -> None:
    """**표시 문자열을 키 자리에 담지 않는다** (13세션).

    `계약전력 5,500 kW` 가 키로 흘러들어 라벨 조회가 막히고 3단계 화면이
    통째로 죽었다. 키는 등록 키, 파라미터는 따로, 라벨은 조회로.
    """
    spec = CombinationSpec(
        name="선택요금 전환 + 계약전력",
        selection=TariffSelection("general_b", "high_a", "II"),
        contract_kw=5_500.0,
        pv_capacity_kwp=1_000.0,
        ess_target_kw=4_800.0,
    )
    assert spec.measure_keys == ("solar", "ess", "contract")
    for key in spec.measure_keys:
        measure_kind(key)  # 등록되지 않았으면 여기서 KeyError 가 난다
    assert "계약전력 조정 (5,500 kW)" in spec.measure_labels
    assert "태양광 (1,000 kWp)" in spec.measure_labels


def test_등록되지_않은_키는_화면을_죽이지_않는다() -> None:
    """라벨을 못 만들면 **키를 그대로 보인다.** 표 한 칸 때문에 화면을 잃지 않는다."""
    unknown = AppliedMeasure("계약전력 5,500 kW")
    assert unknown.label == "계약전력 5,500 kW"


TILDE = re.compile(r"(?<!\\)~")


@pytest.mark.parametrize("name", SCREEN_VIEWS)
def test_화면_문자열에_맨_물결표가_없다(name: str) -> None:
    """물결표 둘이 한 줄에 있으면 Streamlit 이 그 사이를 취소선으로 그린다 (13세션).

    화면 문구는 en dash 를 쓰고, 계산 모듈이 낸 문구는 렌더 직전에 escape 한다.
    """
    offenders = [item for item in _strings(VIEWS / name) if TILDE.search(item)]
    assert not offenders, offenders


def test_계산_모듈_문구를_escape_한다() -> None:
    notices = screen_notices(
        [warn("야간 22~8시 · 운영 9~18시 를 지키지 못했습니다", fact="ess.target_unmet")]
    )
    assert notices
    assert not TILDE.search(notices[0].text)


def test_화면에_영문_열_이름이_없다(app: AppTest) -> None:
    """`days_in_month` 같은 코드 열 이름을 그대로 내지 않는다 (13세션)."""
    for frame in app.dataframe:
        columns = [str(name) for name in frame.value.columns]
        assert not [name for name in columns if re.fullmatch(r"[a-z][a-z0-9_]*", name)], columns


def test_렌더된_문자열에_맨_물결표가_없다(app: AppTest) -> None:
    rendered = (
        [item.value for item in app.markdown]
        + [item.value for item in app.warning]
        + [item.value for item in app.info]
        + [item.value for item in app.error]
    )
    offenders = [item for item in rendered if TILDE.search(item)]
    assert not offenders, offenders


def test_결측_안내가_한_블록이다(app: AppTest) -> None:
    """세 줄 한 묶음이던 것을 **두 줄 + 접힘**으로 다시 짰다 (30세션 2절).

    본문은 전체와 「결측이 있는 달」 이라는 덩어리까지만 말하고, 달마다 다른 값은
    「결측 구간」 표가 낸다.
    """
    from kwise.quality import check_quality

    quality = check_quality(load_usage(SAMPLE))
    lines = missing_lines(quality)
    assert len(lines) == 2
    assert "보간하지 않고" in lines[0]
    assert lines[1].startswith("결측이 있는 달")

    # 위쪽 경고 목록에는 결측 문구가 남아 있지 않다.
    shown = [item.value for item in app.warning]
    assert not [item for item in shown if "결측" in item], shown


def test_결측_구간_접힘이_달마다_낸다(app: AppTest) -> None:
    """**최장 연속과 결측률 높은 달을 본문에서 표로 옮겼다** (30세션 2절).

    한 줄로 뭉뚱그리면 어느 달을 조심하라는 것인지 알 수 없었다. 열 넷이 달마다
    「몇 구간·몇 %·최장 몇 일」 을 낸다.
    """
    from kwise.quality import check_quality
    from kwise.ui.views.diagnose import missing_month_frame

    quality = check_quality(load_usage(SAMPLE))
    frame = missing_month_frame(quality)
    assert list(frame.columns) == ["월", "결측 구간 수", "비율", "최장 연속 구간"]
    # 결측이 있는 달만 낸다 — 0 인 달로 표를 채우지 않는다.
    assert len(frame) == len([month for month in quality.monthly if month.missing_slots])
    assert int(frame["결측 구간 수"].sum()) == quality.missing_slots
    assert "구간 (" in frame["최장 연속 구간"].iloc[0]

    assert [box for box in app.expander if box.label == "결측 구간"]


def _screen_lines(screen: AppTest) -> list[str]:
    """화면에 실제로 그려진 문구 전부. **접힌 상자 안까지 본다.**

    확인사항은 ``st.expander`` 안이라 최상위 목록만 훑으면 잡히지 않는다 —
    18세션 2절의 중복이 그 사각지대에서 살아남았다.
    """
    groups = (screen.markdown, screen.caption, screen.warning, screen.info, screen.error)
    lines = [str(item.value).strip() for group in groups for item in group]
    for box in screen.expander:
        lines += [str(item.value).strip() for item in list(box.markdown) + list(box.caption)]
    return lines


def test_결측_안내가_화면에_두_줄뿐이다(app: AppTest) -> None:
    """**같은 사실이 다섯 번 나왔다** (18세션 2절).

    본문 줄에 더해 「데이터 품질」 확인사항 두 건이 최장 연속 결측과 월별 결측률을
    되풀이했다. 13세션이 위쪽 경고에서 확인사항으로 내리고, 16세션이 같은 사실을
    세 줄로 정리한 두 조치가 겹친 자리다. 30세션에 두 줄로 줄였다.

    ``partition(…, MISSING_MARKERS)`` 은 제대로 걸러 왔다 — 걸러 낸 쪽을 품질
    블록이 **다시 그린 것**이 원인이었다. 패턴을 늘려 막는 방식이 아니므로,
    문구가 바뀌어도 이 시험은 계속 유효하다.
    """
    assert not app.exception, app.exception
    from kwise.quality import check_quality

    quality = check_quality(load_usage(SAMPLE))
    lines = missing_lines(quality)
    assert len(lines) == 2

    rendered = _screen_lines(app)
    for line in lines:
        assert rendered.count(line) == 1, f"{line} 이(가) {rendered.count(line)}번 나왔다"

    # 세 줄 말고 결측을 말하는 안내가 없다. 짧은 꼬리말(`결측은 보간하지
    # 않습니다.`)과 지표 라벨은 안내 문구가 아니므로 길이로 거른다.
    others = [
        item
        for item in rendered
        if item not in lines and len(item) >= 20 and ("결측" in item or "보간" in item)
    ]
    assert others == [], others


def _metric(app: AppTest, label: str) -> Any:
    """라벨로 지표 하나를 집는다. **첫 자리를 쓴다** — 같은 이름이 둘이면 둘 다 본다."""
    found = [item for item in app.metric if item.label == label]
    assert found, f"「{label}」 지표가 없습니다"
    return found


def test_부하율_툴팁이_두_자리에서_같다(app: AppTest) -> None:
    """**같은 지표면 같은 문구다** (30세션 1-1).

    지표 카드와 부하 패턴 절에 부하율이 각각 있는데 한쪽에만 물음표가 있었다.
    문구를 두 벌로 적으면 어느 쪽이 정의인지 알 수 없으므로, 카드 쪽이 부하 패턴
    절과 **같은 함수**(:func:`~kwise.ui.views.diagnose._pattern_formulas`)를 본다.
    """
    from kwise.ui.views.diagnose import _pattern_formulas

    tips = {item.help for item in _metric(app, "부하율")}
    assert len(tips) == 1, tips
    assert tips == {_pattern_formulas(object())["load_factor"]}


def test_결측과_정전_지표에_툴팁이_있다(app: AppTest) -> None:
    """**「결측」 과 「정전 추정」 도 뜻을 물을 수 있어야 한다** (30세션 1-2·1-3)."""
    missing = _metric(app, "결측")[0]
    assert missing.help and "데이터가 없는 구간" in missing.help

    outage = _metric(app, "정전 추정")[0]
    assert outage.help and "정전 흔적" in outage.help


def test_정전_추정에_지속_시간이_보인다(app: AppTest) -> None:
    """**건수만으로는 규모를 알 수 없다** (30세션 1-3).

    15분짜리 하나도 1건이고 며칠이 통째로 빈 것도 1건이다. 지속 시간을 지표의
    작은 글씨(delta)에 함께 낸다 — 「분석 기간」 이 기간을 그 자리에 두는 것과 같다.
    """
    from kwise.quality import check_quality

    quality = check_quality(load_usage(SAMPLE))
    assert quality.outages, "샘플에 정전 추정이 있어야 이 시험이 뜻을 가진다"
    total = sum(event.duration_hours for event in quality.outages)

    outage = _metric(app, "정전 추정")[0]
    assert outage.value == f"{len(quality.outages):,}건"
    assert outage.delta == f"{total:,.1f}시간"


def test_피크_특성_제목에_툴팁이_없다() -> None:
    """**절 제목은 자리 이름이지 지표가 아니다** (30세션 1-4).

    매뉴얼 앵커는 지운 것이 아니라 「상위 구간 주말 비중」 지표로 옮겼다.
    """
    source = (VIEWS / "diagnose.py").read_text(encoding="utf-8")
    assert 'st.subheader("피크 특성")' in source
    assert 'st.subheader("피크 특성", help=' not in source


def test_상위_구간_캡션에서_태양광_문구를_뺐다(app: AppTest) -> None:
    """캡션은 그림 이름만 적는다 (30세션 6절). 읽는 법은 물음표 안에 있다."""
    captions = [str(item.value) for item in app.caption]
    assert "상위 100구간 발생 시각" in captions
    assert not [item for item in captions if "태양광 기여 가능성" in item], captions


def test_계절_갈래가_두_그래프에_모두_있다(app: AppTest) -> None:
    """시간대별 평균 부하와 계시별 사용량 구성을 넷으로 가른다 (30세션 5절).

    계절 구분은 **요금표의 정의 그대로**이며, 갈래는 자료에 있는 계절만 만든다.

    **가르는 방법이 둘이다** (34세션 1절). 시간대별 평균 부하는 탭이고, 계시별
    사용량 구성은 **한 줄에 원 넷**이다 — 뒤엣것은 계절 사이의 차이가 볼 것이라
    갈아 끼우면 안 된다. 넷이 그려지는지는 그림 제목으로 본다.
    """
    import json

    tabs = [item.label for item in app.tabs]
    donuts = [spec["title"]["text"] for spec in _rendered_specs(app) if '"arc"' in json.dumps(spec)]
    for season in ("전체", "봄·가을", "여름", "겨울"):
        assert tabs.count(season) == 1, (season, tabs)
        assert donuts.count(season) == 1, (season, donuts)


def test_계절_갈래는_자료에_있는_계절만_낸다() -> None:
    """**없는 계절 탭을 만들지 않는다** — 빈 그림은 「그 계절엔 안 쓴다」 로 읽힌다."""
    from kwise.ui.views.diagnose import season_choices

    assert season_choices(["summer", "winter"]) == (
        ("전체", None),
        ("여름", "summer"),
        ("겨울", "winter"),
    )
    # 계절이 갈리지 않으면 「전체」 하나 — 부르는 쪽이 갈래를 아예 그리지 않는다.
    assert season_choices([]) == (("전체", None),)


def test_계절별_사용량이_전체와_맞는다() -> None:
    """**접는 방향이 달라도 값은 같다** (30세션 5-2).

    계절 × 시간대 표는 요금 계산이 쓴 분류기 한 벌에서 나오므로, 열로 접으면
    시간대별 합계가 되고 행으로 접으면 계절별 합계가 된다. 어긋나면 화면의
    계절 탭이 요금과 다른 수를 그린다는 뜻이다.
    """
    import pandas as pd

    from kwise.diagnose import ContractInfo, diagnose

    usage = load_usage(SAMPLE)
    table = load_tariff()
    result = diagnose(
        usage,
        table,
        ContractInfo(selection=TariffSelection("general_b", "high_a", "II"), contract_kw=6_000.0),
    )
    structure = result.structure
    assert structure is not None
    wide = structure.band_season_kwh
    pd.testing.assert_series_equal(
        wide.sum(axis=0).rename("kwh").rename_axis("band"),
        structure.band_kwh.astype(float),
        check_names=False,
    )
    pd.testing.assert_series_equal(
        wide.sum(axis=1).rename("kwh").rename_axis("season"),
        structure.season_kwh.astype(float),
        check_names=False,
    )


def test_계절별_시각_프로파일이_계절마다_다르다() -> None:
    """계절을 갈랐는데 같은 선이 나오면 가른 뜻이 없다."""
    from kwise.diagnose import ContractInfo, diagnose
    from kwise.report.frames import hourly_profile_frame

    usage = load_usage(SAMPLE)
    table = load_tariff()
    result = diagnose(
        usage,
        table,
        ContractInfo(selection=TariffSelection("general_b", "high_a", "II"), contract_kw=6_000.0),
    )
    peak = result.peak
    assert set(peak.hourly_profile_by_season.columns) == {"spring_fall", "summer", "winter"}
    summer = hourly_profile_frame(peak, season="summer")
    winter = hourly_profile_frame(peak, season="winter")
    assert len(summer) == len(winter) == 24
    assert not summer["평균 부하(kW)"].equals(winter["평균 부하(kW)"])
    # 없는 계절은 빈 표다 — 0 으로 채워 그리지 않는다.
    assert hourly_profile_frame(peak, season="없는계절").empty


def test_기온_그래프가_부하_패턴에_있다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    r"""**연간 일별 사용량과 일평균 기온을 함께 낸다** (30세션 4절).

    **망을 타지 않는다.** 취득을 실패시켜 저장소의 사전 취득분(``data\weather``)
    으로만 그리게 한다 — 여기서 확인할 것은 기상 취득이 아니라 「지역이 있으면
    그림이 나오는가」 이고, 망 사정에 시험 결과가 흔들리면 안 된다.
    """
    from kwise.pv.weather import WeatherUnavailableError
    from kwise.ui.cache import cached_daily_temperature

    monkeypatch.setenv("KWISE_WEATHER_DIR", str(Path("data") / "weather"))
    monkeypatch.setenv("PROJECT_CACHE", str(tmp_path))

    def offline(request: object) -> object:
        raise WeatherUnavailableError("시험에서는 망을 타지 않는다")

    monkeypatch.setattr("kwise.pv.weather.fetch_open_meteo", offline)
    cached_daily_temperature.clear()

    screen = _running()
    assert not screen.exception, screen.exception
    captions = [str(item.value) for item in screen.caption]
    # **출처를 태양광 카드와 같은 이름으로 적는다** (31세션 4-2). 여기서는 취득을
    # 실패시켰으므로 사전 취득분으로 물러선 사실이 이름에 드러나야 한다.
    assert "일별 사용량과 일평균 기온 · 아카이브(Open-Meteo)" in captions, captions
    assert "옆단에서 지역을 고르면 일평균 기온을 함께 그립니다." not in captions
    cached_daily_temperature.clear()


def test_기온_그래프의_두_축에_이름이_있다() -> None:
    """**축이 둘이면 어느 선이 어느 축인지 라벨이 말해야 한다** (17세션 0절 · 30세션 4절)."""
    import pandas as pd

    from kwise.ui.charts import daily_temperature_chart

    usage = load_usage(SAMPLE)
    index = pd.date_range(usage.meta.start, usage.meta.end, freq="h")
    temperature = pd.Series(range(len(index)), index=index, dtype=float) % 30.0
    spec = daily_temperature_chart(usage, temperature).to_dict()

    # **기온 쪽은 층이 하나 더 깊다** (32세션 1절) — 기준선·라벨이 기온 축을
    # 함께 써야 해서 한 층으로 묶었다. 바깥 층은 사용량과 기온 둘이다.
    load, right = spec["layer"]
    temp = right["layer"][0]
    titles = [load["encoding"]["y"]["title"], temp["encoding"]["y"]["title"]]
    assert titles == ["일 사용량 (kWh)", "일평균 기온 (℃)"]
    # 축 둘이 각자 눈금을 쓴다 — 한 축에 얹으면 사용량 옆에서 기온이 뭉개진다.
    assert spec["resolve"]["scale"]["y"] == "independent"
    # 범례 이름이 어느 쪽 축인지 적는다.
    domains = [layer["encoding"]["color"]["scale"]["domain"] for layer in (load, temp)]
    assert domains[0] == ["일 사용량 (왼쪽 축)", "일평균 기온 (오른쪽 축)"]
    assert domains[0] == domains[1]


def test_기온_그래프에_평균_기준선과_값이_있다() -> None:
    """**평균과의 거리로 냉난방 몫을 읽는다** (32세션 1절).

    선만 그으면 그 선이 무엇인지 알 수 없다. 값을 **선 위 라벨**로 적고,
    범례는 늘리지 않는다 (23세션 1절).
    """
    import pandas as pd

    from kwise.ui.charts import daily_temperature_chart

    usage = load_usage(SAMPLE)
    index = pd.date_range(usage.meta.start, usage.meta.end, freq="h")
    temperature = pd.Series(range(len(index)), index=index, dtype=float) % 30.0
    spec = daily_temperature_chart(usage, temperature).to_dict()

    right = spec["layer"][1]["layer"]
    marks = [layer["mark"]["type"] for layer in right]
    assert marks == ["line", "rule", "text"], marks
    # 기준선과 라벨은 기온과 **같은 축**을 쓴다. 이 층에는 resolve 가 없다.
    assert "resolve" not in spec["layer"][1]
    for layer in right[1:]:
        assert layer["encoding"]["y"]["field"] == "평균 기온(℃)"
    # 값이 라벨로 적힌다 — 샘플은 1년을 넘으므로 「연평균」 이다.
    label = right[2]["encoding"]["text"]["field"]
    assert label == "기준선"
    data = right[2]["data"]
    values = data.get("values") or spec["datasets"][data["name"]]
    assert values[0]["기준선"].startswith("연평균 ")
    assert values[0]["기준선"].endswith("℃")
    # 범례는 늘지 않는다 — 기준선·라벨에 color 인코딩이 없다.
    assert "color" not in right[1]["encoding"]
    assert "color" not in right[2]["encoding"]


def test_관측이_1년에_못_미치면_기간_평균으로_적는다() -> None:
    """**반년치 평균을 「연평균」 이라 적으면 평년값처럼 읽힌다** (32세션 1절)."""
    import pandas as pd

    from kwise.report.frames import temperature_mean_frame

    def frame(days: int) -> pd.DataFrame:
        dates = pd.date_range("2024-01-01", periods=days, freq="D")
        return pd.DataFrame(
            {
                "날짜": [value.date() for value in dates],
                "사용량(kWh)": [1_000.0] * days,
                "일평균 기온(℃)": [13.2] * days,
            }
        )

    assert temperature_mean_frame(frame(365)).loc[0, "기준선"] == "연평균 13.2℃"
    assert temperature_mean_frame(frame(364)).loc[0, "기준선"] == "기간 평균 13.2℃"
    # 값은 일별 기온의 평균이다.
    assert float(temperature_mean_frame(frame(30)).loc[0, "평균 기온(℃)"]) == pytest.approx(13.2)


# ======================================================== 33세션 · 날짜 표기와 원 넷


def _rendered_specs(screen: AppTest) -> list[dict[str, Any]]:
    """화면에 실제로 그려진 vega 스펙 전부. **모듈 시험이 못 보는 자리다.**"""
    import json

    return [json.loads(item.proto.spec) for item in screen.get("vega_lite_chart")]


def _encodings(node: object) -> list[dict[str, Any]]:
    """스펙 어디에 묻혀 있든 ``encoding`` 을 전부 끌어낸다 (층·묶음 상관없이)."""
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if isinstance(node.get("encoding"), dict):
            found.append(node["encoding"])
        for value in node.values():
            found.extend(_encodings(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_encodings(value))
    return found


def test_모든_날짜_축이_한국식이다(stage3: AppTest) -> None:
    """**vega 의 기본 로케일은 영어다** (33세션 1절).

    ``May`` · ``Apr 30`` 이 축에 찍히던 자리다. 차트마다 ``format`` 을 적으면
    다음에 새 차트가 또 영어로 나오므로, **라벨 식 한 벌**을 날짜 축 전부가
    쓰는지 화면에 그려진 스펙으로 훑는다.
    """
    from kwise.ui.charts import DATE_FORMAT, DATE_LABEL_EXPR, TIME_FORMAT, TIME_TOOLTIP_FORMAT

    offenders: list[str] = []
    seen = 0
    for spec in _rendered_specs(stage3):
        for encoding in _encodings(spec):
            for channel in ("x", "y"):
                item = encoding.get(channel)
                if not isinstance(item, dict) or item.get("type") != "temporal":
                    continue
                seen += 1
                axis = item.get("axis") or {}
                if axis.get("labelExpr") != DATE_LABEL_EXPR and axis.get("format") != TIME_FORMAT:
                    offenders.append(f"{channel} 축 {item.get('field')}")
            tooltip = encoding.get("tooltip")
            for item in tooltip if isinstance(tooltip, list) else []:
                if not isinstance(item, dict) or item.get("type") != "temporal":
                    continue
                seen += 1
                if item.get("format") not in (DATE_FORMAT, TIME_TOOLTIP_FORMAT):
                    offenders.append(f"툴팁 {item.get('field')} — {item.get('format')}")
    assert seen, "날짜 축이 하나도 안 그려졌습니다 — 시험이 아무것도 못 봤습니다."
    assert offenders == [], " / ".join(sorted(set(offenders)))


def test_화면에_영문_달_이름이_없다(stage3: AppTest) -> None:
    """**표기 규약을 빠뜨린 자리는 서식 문자로 드러난다** (33세션 1절)."""
    import json
    import re

    english = re.compile(r"%[-_0]?[abhB]|(?:Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)")
    for spec in _rendered_specs(stage3):
        rendered = json.dumps(spec, ensure_ascii=False)
        # 자료 값(ISO 날짜)은 영어가 아니다. 서식 문자와 달 이름만 본다.
        assert not english.search(rendered), english.search(rendered)


def test_보고서_png_도_같은_날짜_규약이다() -> None:
    """**화면만 고치면 어긋난다** (13세션). matplotlib 도 기본이 영어다."""
    import datetime as date_module

    from kwise.report.figures import korean_date_label

    assert korean_date_label(date_module.datetime(2024, 1, 1)) == "2024년"
    assert korean_date_label(date_module.datetime(2024, 5, 1)) == "5월"
    assert korean_date_label(date_module.datetime(2024, 4, 30)) == "4월 30일"
    source = (Path("src") / "kwise" / "report" / "figures.py").read_text(encoding="utf-8")
    assert source.count("date_axis(axes)") == 3, "날짜 축 png 셋이 모두 규약을 타야 합니다."
    assert source.count("time_axis(axes)") == 2


def test_월_축_라벨이_한국식이다() -> None:
    """**해는 바뀔 때만 적는다** (33세션 1절). 열세 달에 「4월」 이 둘 생긴다."""
    from kwise.report.frames import month_labels, monthly_peak_frame

    assert month_labels(["2023-12", "2024-01", "2024-02"]) == ["2023년 12월", "2024년 1월", "2월"]
    peak = _diagnosis().peak
    months = list(monthly_peak_frame(peak)["월"])
    assert months[0] == "2023년 4월"
    assert not [item for item in months if any(ch.isascii() and ch.isalpha() for ch in item)]


def test_기온_그래프_오른쪽_축에_눈금과_단위가_있다() -> None:
    """**32세션에 오른쪽 눈금이 사라졌다** (33세션 2절).

    기준선·라벨에 ``axis=None`` 을 주었더니 vega 가 층의 y 축을 합칠 때 null 이
    이겨, 기온 곡선은 그려지는데 범위와 단위(℃)를 읽을 수 없었다.
    """
    import pandas as pd

    from kwise.ui.charts import daily_temperature_chart

    usage = load_usage(SAMPLE)
    index = pd.date_range(usage.meta.start, usage.meta.end, freq="h")
    temperature = pd.Series(range(len(index)), index=index, dtype=float) % 30.0
    spec = daily_temperature_chart(usage, temperature).to_dict()

    right = spec["layer"][1]["layer"]
    for layer in right:
        axis = layer["encoding"]["y"].get("axis")
        assert axis is not None, "축을 지운 층이 있습니다 — 합칠 때 null 이 이깁니다."
        assert axis["orient"] == "right"
        assert layer["encoding"]["y"]["title"] == "일평균 기온 (℃)"
    # 세 층의 축 정의가 **똑같아야** 합쳐도 다툴 것이 없다.
    axes = [layer["encoding"]["y"]["axis"] for layer in right]
    assert axes[0] == axes[1] == axes[2]


def test_화면에_계시별_원_넷이_그려진다(app: AppTest) -> None:
    """**원 넷을 한 줄에, 탭 없이** (34세션 1절).

    갈래는 계절 탭과 같되 **갈아 끼우지 않는다** — 여기서 볼 것이 계절 사이의
    차이인데 탭은 하나씩만 보여 준다.
    """
    import json

    arcs = [spec for spec in _rendered_specs(app) if '"arc"' in json.dumps(spec)]
    assert len(arcs) == 4, f"원이 {len(arcs)}개입니다 — 전체·봄·가을·여름·겨울 넷이어야 합니다."
    titles = [spec["title"]["text"] for spec in arcs]
    assert titles == ["전체", "봄·가을", "여름", "겨울"], titles
    for spec in arcs:
        assert spec["title"]["subtitle"].endswith("MWh"), "합계 사용량이 제목 아래에 없습니다."
        # 조각마다 이름과 비중을 적는다 — 범례 대신이다.
        assert spec["layer"][1]["encoding"]["text"]["field"] == "라벨"
        assert spec["layer"][0]["encoding"]["color"]["legend"] is None

    # **월별 요금 구성은 막대로 돌아왔다.** 같은 화면에 둘이 함께 있어야 한다.
    bars = [
        spec
        for spec in _rendered_specs(app)
        if spec.get("mark", {}) and spec.get("mark", {}).get("type") == "bar"
    ]
    assert any(spec.get("width") == {"step": 60} for spec in bars), (
        "월별 요금 구성 막대가 없습니다."
    )

    # 계시별 구성에 **탭이 없다.** 계절 탭은 시간대별 평균 부하 하나뿐이다.
    labels = [str(item.label) for item in app.get("tab")]
    assert labels.count("여름") == 1, labels


def test_계시별_사용량_구성이_원_넷이다() -> None:
    """**비중을 견주는 그림은 원이 낫다** (33세션 3절 → 34세션 1절에 자리 이동).

    33세션은 원을 **월별 요금 구성**에 붙였는데 지시가 잘못 전달된 것이었다.
    제자리는 계시별 사용량 구성이다.
    """
    from kwise.ui.charts import band_donut_chart

    structure = _structure()
    spec = band_donut_chart(
        structure, season="summer", title="여름", subtitle="6,668.5 MWh"
    ).to_dict()
    # 조각(arc)과 라벨(text) 두 층.
    assert [layer["mark"]["type"] for layer in spec["layer"]] == ["arc", "text"]
    assert spec["layer"][0]["mark"]["innerRadius"] > 0, "도넛이 아니라 원입니다."
    # 제목이 계절, 그 아래가 합계 사용량이다.
    assert spec["title"]["text"] == "여름"
    assert spec["title"]["subtitle"] == "6,668.5 MWh"
    # 조각마다 이름과 비중을 적는다 — 범례를 달지 않는다.
    assert spec["layer"][1]["encoding"]["text"]["field"] == "라벨"
    assert spec["layer"][0]["encoding"]["color"]["legend"] is None
    # 33세션이 요금 구성에 붙였던 것은 남기지 않는다.
    from kwise.ui import charts

    assert not hasattr(charts, "charge_donut_chart"), "요금 구성 도넛이 남아 있습니다."
    assert not hasattr(charts, "band_chart"), "옛 가로 막대가 남아 있습니다."


def test_계시별_사용량_구성의_세_조각이_그_계절_안에서_1이_된다() -> None:
    """**비중은 그 계절 안에서 다시 잰다** (30세션 5-2).

    전체 대비로 두면 세 계절의 원이 각각 3분의 1 만 칠해져 무엇의 구성인지
    알 수 없다.
    """
    from kwise.report.frames import BAND_LABELS, band_frame

    structure = _structure()
    for season in structure.band_season_kwh.index:
        frame = band_frame(structure, season=str(season))
        assert list(frame["시간대"]) == list(BAND_LABELS.values())
        assert float(frame["비중"].sum()) == pytest.approx(1.0)
        # 라벨에 이름과 비중이 함께 적힌다.
        assert frame.loc[0, "라벨"].startswith("경부하 ")
        assert frame.loc[0, "라벨"].endswith("%")
    # 계절을 다 더하면 전체 사용량이다.
    total = float(band_frame(structure)["사용량(kWh)"].sum())
    parts = sum(
        float(band_frame(structure, season=str(season))["사용량(kWh)"].sum())
        for season in structure.band_season_kwh.index
    )
    assert parts == pytest.approx(total)


def test_월별_요금_구성이_다시_막대다() -> None:
    """**33세션에 원으로 바꿨다가 34세션에 되돌렸다** (지시가 잘못 전달됐다).

    달마다의 높이를 비교하는 그림은 막대가 맞다. 17세션 축 규약과 32세션 칸 폭도
    함께 되살린다.
    """
    from kwise.ui.charts import MONTH_BAR_STEP, _month_step, monthly_charge_chart

    assert MONTH_BAR_STEP == 60  # vega-lite 기본 20px 의 세 배
    spec = monthly_charge_chart(_structure()).to_dict()
    assert spec["mark"]["type"] == "bar"
    assert spec["width"] == {"step": MONTH_BAR_STEP}
    # **막대는 자르지 않는다** (17세션 0절 · 23세션 2절). 길이가 곧 금액이다.
    assert spec["encoding"]["y"].get("scale", {}).get("zero") is not False
    assert spec["encoding"]["y"]["stack"] == "zero"
    # 달이 많으면 칸을 줄여 가로 스크롤을 막는다.
    assert _month_step(12) == MONTH_BAR_STEP
    assert _month_step(36) < MONTH_BAR_STEP
    assert _month_step(500) == 20


def test_ESS_절감액_툴팁이_전력량요금_절감의_까닭을_밝힌다() -> None:
    """**「충전을 하는 ESS 에서 전력량요금 절감이 가능한가」** 에 답한다 (32세션 3절).

    **33세션에 부호로 문장을 갈랐다.** 왕복효율 손실이 단가차익보다 크면
    전력량요금 절감이 음수가 되고 기본요금 비중이 100%를 넘는다 — 사용자가
    화면에서 100.4% 를 보고 물었다.
    """
    from kwise.ui.text import ess_saving_line

    source = (VIEWS / "measures.py").read_text(encoding="utf-8")
    assert "경부하에 충전해 최대부하에 방전하니" in source
    assert "왕복효율 손실만큼 총 사용량이" in source
    # 「거의 상쇄됩니다」 는 틀린 말이었다 — 손실이 더 크면 늘어난다.
    assert "둘이 거의 상쇄됩니다" not in source
    assert "맞부딪힙니다" in source
    # 31세션 문구는 남지 않는다 — 합만 적으면 물음이 되돌아온다.
    assert "기본요금 절감 + 전력량요금 절감입니다." not in source
    # 차익거래가 빠져 있다는 사실은 이 자리 하나에만 있다 (중복 금지).
    assert source.count("충방전 차익거래는 들어 있지 않습니다") == 1

    # 전력량요금이 줄어든 경우 — 비중을 적는다. 이 갈래는 100% 를 넘지 않는다.
    plus = ess_saving_line(10_409_000, 16_000, 16_000)
    assert "거의 전부 기본요금 절감입니다" in plus and "(99.8%)" in plus
    assert "2만원" in plus
    # 전력량요금이 늘어난 경우 — **비중을 적지 않는다.** 100.4% 가 나오던 자리다.
    minus = ess_saving_line(10_409_000, -40_000, -42_000)
    assert "오히려" in minus and "늘었습니다" in minus
    assert "%" not in minus, minus
    # 반반이면 비중을 그대로 적는다.
    assert "60.0%" in ess_saving_line(6_000_000, 4_000_000, 4_200_000)


def test_대표일은_ESS_절감액을_바꾸지_않는다() -> None:
    """**대표일은 그림에만 쓴다** (33세션 4절).

    사용자가 물었다 — 대표일을 결측일로 잡으면 값이 달라지나. 달라지면 결함이다.
    :func:`~kwise.measures.evaluate_ess` 는 날을 인자로 받지 않지만, 화면이
    어딘가에서 날을 섞어 넣을 수도 있어 **화면으로 확인한다.**
    """
    import inspect

    from kwise.measures import evaluate_ess

    assert "day" not in inspect.signature(evaluate_ess).parameters

    import datetime as date_module

    from kwise.report.days import MAX_DEMAND_KEY

    def saving(**day: object) -> str:
        screen = _running(option="I", measure_on_ess=True, **day)  # type: ignore[arg-type]
        assert not screen.exception, screen.exception
        return next(str(item.value) for item in screen.metric if item.label == "절감액")

    # 최대수요일과 **결측 구간 한가운데의 날** — 절감액은 같아야 한다.
    peak_day = saving(measure_common_ref_day=MAX_DEMAND_KEY)
    missing_day = saving(
        measure_common_ref_day="custom",
        measure_common_ref_day_custom=date_module.date(2023, 11, 6),
    )
    assert peak_day == missing_day


def test_지역이_없으면_기온을_구하지_않는다() -> None:
    """**옆단 지역은 선택 입력이다** (30세션 4절).

    지역이 없거나 기상 자료가 없으면 ``None`` 이고, 화면은 그림을 감춘 채 사유
    한 줄만 남긴다. 1단계는 설비 정보 없이 돌아야 하는 화면이라 **여기서 예외를
    올리면 진단이 통째로 죽는다.**
    """
    from kwise.ui.pipeline import daily_temperature

    usage = load_usage(SAMPLE)
    assert daily_temperature(usage, "") is None
    # 좌표를 못 찾는 지역 이름도 「없음」 이다 — 예외로 올라오지 않는다.
    assert daily_temperature(usage, "없는시도/없는구") is None

    source = (VIEWS / "diagnose.py").read_text(encoding="utf-8")
    assert "옆단에서 지역을 고르면 일평균 기온을 함께 그립니다." in source


def test_같은_값이면_피크_지표를_한_줄로_접는다(app: AppTest) -> None:
    """샘플은 연간 최대가 중간부하 시간대라 관측 최대와 요금적용전력이 같다."""
    labels = [item.label for item in app.metric]
    assert "최대수요 = 요금적용전력" in labels
    assert "관측 최대수요" not in labels


def test_계약전력_여유가_없으면_슬라이더를_감춘다() -> None:
    """움직여도 0% 인 슬라이더는 고장으로 보인다 (13세션)."""
    # 샘플의 실제 계약전력 5,500 kW 는 요금적용전력 5,293 kW 대비 여유가 3.8% 다.
    screen = _running(contract_kw=5_500.0, nav_page="2단계 · 개선 수단", measure_on_contract=True)
    assert not screen.exception, screen.exception
    assert not [item for item in screen.slider if "여유율" in item.label]
    body = " ".join(item.value for item in screen.markdown)
    assert "하향 여지가 없습니다" in body


def test_태양광에_계산_단추가_있다() -> None:
    """값 하나만 바꿔도 다시 도는 구간이라 단추를 눌러야 돈다 (13세션)."""
    screen = _running(nav_page="2단계 · 개선 수단", measure_on_solar=True)
    assert not screen.exception, screen.exception
    assert screen.button(key="solar_run")
    # 안내는 배경 없는 작은 글씨다 (15세션 4절) — ``st.info`` 상자를 쓰지 않는다.
    body = " ".join(str(item.value) for item in screen.caption)
    assert "「태양광 계산」 을 누르십시오" in body
    source = (VIEWS / "measures.py").read_text(encoding="utf-8")
    assert "입력이 변경되었습니다 — 다시 계산하십시오" in source


def test_비교_화면은_요약이_먼저다(compare_app: AppTest) -> None:
    """**개선안별 요약 → 조합 구성 → 합산효과** 순서다 (16세션 5절)."""
    headings = [str(item.value) for item in compare_app.subheader]
    assert headings.index("개선안별 요약") < headings.index("조합 구성")
    assert headings.index("조합 구성") < headings.index("합산효과")


# ======================================================== 14세션 · 2단계 독립 평가


def test_잉여_활용_카드는_태양광이_없어도_열린다() -> None:
    """**다른 카드 때문에 비활성이 되는 카드는 없다** (14세션 2-3).

    태양광을 켜지 않으면 잉여가 0 인 것이지 검토할 수 없는 것이 아니다.
    잠그면 "쓸 수 없는 수단" 으로 읽힌다.
    """
    screen = _running(nav_page="2단계 · 개선 수단", measure_on_surplus=True)
    assert not screen.exception, screen.exception
    body = " ".join(str(item.value) for item in screen.markdown)
    assert "태양광을 켜지 않아 잉여가 0 입니다." in body
    # 입력이 잠기지 않는다 — 단가는 그대로 받는다.
    assert any("잉여 판매 단가" in str(item.label) for item in screen.number_input)
    # **잉여량 지표는 7.5 로 옮겼다** (26세션 3-2). 같은 사실을 두 카드에 두지 않는다.
    assert not [item for item in screen.metric if str(item.label) == "잉여 전력량"]


def test_카드_개요가_결과_위에_나온다() -> None:
    """무엇을 어떻게 개선하는지 두세 줄 (14세션 2-2)."""
    from kwise.ui.spec import measure

    screen = _running(nav_page="2단계 · 개선 수단", measure_on_tariff_switch=True)
    assert not screen.exception, screen.exception
    body = " ".join(str(item.value) for item in screen.markdown)
    assert measure("tariff_switch").overview in body


def test_계약전력_카드가_3단계를_가리킨다() -> None:
    """7.2 는 현재 부하 기준이고, 조합 기준 추가 하향은 3단계다 (14세션 2-4)."""
    screen = _running(nav_page="2단계 · 개선 수단", measure_on_contract=True)
    assert not screen.exception, screen.exception
    body = " ".join(str(item.value) for item in screen.caption)
    assert "현재 부하 기준의 하향 여지" in body, body
    assert "3단계 합산효과에서 추가 하향 여지가 계산됩니다" in body, body


def test_폐기된_배치_지시가_코드에_없다() -> None:
    """«투자비 순으로 배치»·«종속 항목» 은 14세션에 폐기됐다."""
    banned = ("투자비 순으로 배치", "투자비 순이다", "투자비 순으로 놓았습니다", "종속 항목")
    offenders: list[str] = []
    for path in Path("src").rglob("*.py"):
        text_body = path.read_text(encoding="utf-8")
        offenders.extend(f"{path}: {word}" for word in banned if word in text_body)
    assert not offenders, offenders


# 화면을 실제로 띄워 **켜고 끄며** 값이 흔들리지 않는지 본다. 순수 함수 시험으로는
# "다른 카드의 결과를 입력으로 쓰지 않는다" 를 증명하지 못한다 — 배선이 문제다.
INDEPENDENT_MEASURES = ("tariff_switch", "contract", "power_factor", "ess")


def _metrics(*keys: str) -> list[tuple[str, str]]:
    state: dict[str, object] = {f"measure_on_{key}": True for key in keys}
    screen = _running(**state)  # type: ignore[arg-type]
    assert not screen.exception, screen.exception
    return _stage2_metrics(screen)


def _contains(whole: list[tuple[str, str]], part: list[tuple[str, str]]) -> bool:
    """``part`` 가 ``whole`` 안에 **잇달아** 들어 있는가."""
    span = len(part)
    return any(whole[start : start + span] == part for start in range(len(whole) - span + 1))


@pytest.mark.parametrize("key", INDEPENDENT_MEASURES)
def test_수단을_함께_켜도_카드_값이_불변이다(key: str) -> None:
    """**독립 평가** (14세션 2절) — 다른 수단을 켜고 끄든 이 카드의 숫자가 같다."""
    alone = _metrics(key)
    together = _metrics(*INDEPENDENT_MEASURES)
    assert alone, key
    assert _contains(together, alone), (key, alone, together)


# ======================================================== 14세션 · ESS 목표 재설계


@pytest.fixture(scope="module")
def ess_screen() -> AppTest:
    return _running(nav_page="2단계 · 개선 수단", measure_on_ess=True)


def test_ESS_목표_슬라이더가_없다(ess_screen: AppTest) -> None:
    """**목표를 사용자가 찍게 두면 대개 틀린 자리를 찍는다** (14세션 3-2).

    곡선이 목표를 정하므로 슬라이더도 목표 입력칸도 두지 않는다.
    """
    assert not ess_screen.exception, ess_screen.exception
    labels = [str(item.label) for item in ess_screen.number_input]
    assert not [item for item in labels if "목표 요금적용전력" in item], labels
    assert not [item for item in ess_screen.slider if "목표" in str(item.label)]
    source = (VIEWS / "measures.py").read_text(encoding="utf-8")
    assert 'input_key("ess", "unit_cost")' not in source, "kW당 단가 입력이 남아 있습니다."


def test_ESS_는_그림_하나로_고른다(ess_screen: AppTest) -> None:
    """**곡선 하나뿐이다** (26세션 1절).

    23세션까지는 곡선 아래에 대표 지점 표와 U자 설명 세 줄이 함께 있었다. 그림
    하나로 고르는 자리에 읽을 것을 셋이나 두니 정작 최소 지점이 묻혔다. 오해를
    푸는 설명(「목표를 낮추면 용량이 더 빨리 는다」)은 툴팁으로 내렸다.
    """
    assert not ess_screen.exception, ess_screen.exception
    assert len(ess_screen.get("vega_lite_chart")) >= 1, "회수기간 곡선이 없습니다."
    body = " ".join(str(item.value) for item in ess_screen.caption)
    assert "용량별 회수기간" in body
    for banned in ("가장 유리한 목표는", "조달", "왼쪽은 최소 규모", "표의 출력·정격 용량"):
        assert banned not in body, banned
    # 목표 선택 표가 없다 — 사양은 아래 지표 카드가 낸다.
    frames = [item.value for item in ess_screen.dataframe]
    picked = [item for item in frames if "목표(kW)" in list(item.columns)]
    assert not picked, "목표 선택 표가 남아 있습니다."
    # 오해를 푸는 설명은 툴팁에 있다.
    tip = text.chart_tip("chart.ess_target")
    assert "필요 용량이 훨씬 빠르게" in tip and "균형점" in tip


def test_ESS_최소점_표식이_검산값과_맞는다() -> None:
    """곡선의 표식은 최소 지점의 사양을 그대로 적는다 (14세션 3-2 · 18세션 1절).

    **용량은 정격이고 회수기간은 적지 않는다.** 표식의 회수기간은 기본요금만 본
    개략치라 카드의 결론(30.8년)과 어긋난 숫자가 화면에 남았다.
    """
    from kwise.measures import ess_target_curve
    from kwise.tariff import TariffSelection

    usage = load_usage(SAMPLE)
    table = load_tariff()
    selection = TariffSelection("general_b", "high_a", "I")
    curve = ess_target_curve(
        usage.kw,
        15,
        baseline_demand_kw=5_293.44,
        base_fee_won_per_kw=float(table.rates(selection).base_won_per_kw),
    )
    assert curve.best is not None
    label = curve.best.spec_label
    assert label.startswith("5,170 kW · 저감 123 kW")
    assert "120 kWh" in label, label  # 정격 용량 — 카드와 같은 값
    assert "년" not in label, label  # 회수기간은 카드만 낸다


def test_ESS_계수를_둘로_받는다(ess_screen: AppTest) -> None:
    """**kW당 단가로는 표현할 수 없다** (14세션 3-4)."""
    assert not ess_screen.exception, ess_screen.exception
    labels = [str(item.label) for item in ess_screen.number_input]
    assert any("고정비 (원)" in item for item in labels), labels
    assert any("용량단가 (원/kWh)" in item for item in labels), labels
    assert any("견적 총액 직접 입력" in item for item in labels), labels
    body = " ".join(str(item.value) for item in ess_screen.caption)
    assert "도입 사례 4건 기준" in body and "적합" in body


def test_ESS_출력과_용량이_잘리지_않는다(ess_screen: AppTest) -> None:
    """``529 kW / 4,094...`` 로 잘리던 자리다. 방전시간까지 보인다 (14세션 3-5)."""
    assert not ess_screen.exception, ess_screen.exception
    card = next(item for item in ess_screen.metric if item.label == "출력 / 용량")
    assert " kW / " in str(card.value) and "kWh" in str(card.value)
    assert "방전" in str(card.delta) and "시간" in str(card.delta)


def test_ESS_판정_문장을_쓰지_않는다() -> None:
    """「어느 목표에서도 성립하지 않습니다」 같은 단정을 쓰지 않는다 (14세션 3-3)."""
    banned = ("성립하지 않습니다", "경제성 없음", "어느 목표에서도")
    for path in (VIEWS / "measures.py", Path("src/kwise/measures/ess_cost.py")):
        body = path.read_text(encoding="utf-8")
        assert not [word for word in banned if word in body], path


# ======================================================== 14세션 · 3단계 세 부분


STAGE3_MEASURES: dict[str, object] = {
    "measure_on_tariff_switch": True,
    "measure_on_contract": True,
    "measure_on_power_factor": True,
    "measure_on_ess": True,
    "measure_ess_target": 5_170.0,
}


@pytest.fixture(scope="module")
def stage3() -> AppTest:
    """요금에 영향을 주는 수단 넷을 켠 3단계 화면."""
    return _running(
        option="I",
        measure_on_tariff_switch=True,
        measure_on_contract=True,
        measure_on_power_factor=True,
        measure_on_ess=True,
        measure_ess_target=5_170.0,
    )


def test_3단계가_세_부분으로_나뉜다(stage3: AppTest) -> None:
    """개선안별 요약 → 조합 구성 → 합산효과 → 내려받기 (16세션 5절)."""
    assert not stage3.exception, stage3.exception
    headings = [str(item.value) for item in stage3.subheader]
    order = ["개선안별 요약", "조합 구성", "합산효과", "내려받기"]
    assert [item for item in headings if item in order] == order
    assert "조합 비교" not in headings, "미리 정의된 조합 세트 비교는 없앴다 (16세션 5절)."


def test_개선안마다_체크박스가_있다(stage3: AppTest) -> None:
    """**조합은 사용자가 짠다** (16세션 5절). 기본은 2단계에서 켠 수단 전부다."""
    picks = [item for item in stage3.checkbox if str(item.key or "").startswith("combo_pick_")]
    assert {str(item.key) for item in picks} == {
        "combo_pick_tariff_switch",
        "combo_pick_contract",
        "combo_pick_power_factor",
        "combo_pick_ess",
    }
    assert all(item.value for item in picks), "기본은 전부 체크다."


def test_체크를_풀고_계산을_눌러야_합산효과가_바뀐다() -> None:
    """뺀 만큼 줄어야 한다 — 화면이 옛 값을 들고 있으면 안 된다.

    **33세션 5절에 계산 버튼이 생겼다.** 체크만 풀면 묵은 결과가 흐리게 남고
    「선택이 변경되었습니다」 가 뜬다. 눌러야 다시 계산된다.

    **한 벌을 따로 띄운다.** 묶음 fixture 를 건드리면 뒤따르는 시험이 뺀 조합을
    보게 된다.
    """
    screen = _running(option="I", **STAGE3_MEASURES)  # type: ignore[arg-type]
    assert not screen.exception, screen.exception
    before = {str(item.label): str(item.value) for item in screen.metric}

    dropped = screen.checkbox(key="combo_pick_ess").set_value(False).run(timeout=600)
    assert not dropped.exception, dropped.exception
    # 아직은 묵은 결과다. 사유가 화면에 있다.
    stale = {str(item.label): str(item.value) for item in dropped.metric}
    assert stale["합산효과"] == before["합산효과"]
    assert any("선택이 변경되었습니다" in str(item.value) for item in dropped.markdown)

    run = dropped.button(key="combo_run").click().run(timeout=600)
    assert not run.exception, run.exception
    after = {str(item.label): str(item.value) for item in run.metric}
    assert after["합산효과"] != before["합산효과"]
    assert not any("선택이 변경되었습니다" in str(item.value) for item in run.markdown)
    body = " ".join(str(item.value) for item in run.caption)
    assert "조합에서 뺀 개선안" in body, body


def test_체크만_바꾸면_계산이_돌지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """**계산 버튼을 두었으면 체크에는 계산이 없어야 한다** (34세션 2절).

    Streamlit 은 위젯을 건드리면 스크립트를 다시 돌린다 — 재실행 자체는 피할 수
    없다. 그때 **계산까지 도는지**가 다른 문제이고, 그것이 이 시험이 보는 것이다.
    """
    from kwise.compare import combination as combo
    from kwise.ui import cache

    calls: list[str] = []

    def counted(name: str, original: object) -> object:
        def inner(*args: object, **kwargs: object) -> object:
            calls.append(name)
            return original(*args, **kwargs)  # type: ignore[operator]

        return inner

    monkeypatch.setattr(
        cache, "compare_combinations", counted("compare", cache.compare_combinations)
    )
    monkeypatch.setattr(
        combo, "evaluate_combination", counted("evaluate", combo.evaluate_combination)
    )

    screen = _running(option="I", **STAGE3_MEASURES)  # type: ignore[arg-type]
    assert not screen.exception, screen.exception
    assert calls, "첫 실행에서는 조합을 계산해야 합니다."

    calls.clear()
    toggled = screen.checkbox(key="combo_pick_ess").set_value(False).run(timeout=600)
    assert not toggled.exception, toggled.exception
    assert calls == [], f"체크만 바꿨는데 계산이 돌았습니다: {calls}"

    # 단추를 누르면 그때 돈다.
    ran = toggled.button(key="combo_run").click().run(timeout=600)
    assert not ran.exception, ran.exception
    assert "compare" in calls


def test_산출_근거_툴팁에_앞머리가_없다(stage3: AppTest) -> None:
    """**캡션이 이미 「산출 근거 N건」 이다** (34세션 3절).

    「이 숫자가 어디서 나왔나」 를 세 자리에서 똑같이 얹고 있었다 — 같은 말을
    두 번 한다.
    """
    tips = [str(item.help) for item in stage3.caption if item.help]
    grounds = [tip for tip in tips if "조합의 절감액은" in tip]
    assert grounds, "조합 근거 툴팁이 없습니다."
    assert not any("이 숫자가 어디서 나왔나" in tip for tip in tips), tips
    # **33세션이 놓친 자리다** — 소스만 고치고 화면을 안 봤다.
    assert "수단별 절감액의 단순 합이 아니라" in grounds[0], grounds[0]
    assert "각 조합의 부하를 재구성하여 처음부터 다시 산출한 값입니다" in grounds[0]
    source = (Path("src") / "kwise" / "ui" / "views").glob("*.py")
    for path in source:
        assert "이 숫자가 어디서 나왔나" not in path.read_text(encoding="utf-8"), path.name


def test_코드를_고치면_계산_캐시_키가_달라진다() -> None:
    """**33세션이 걸린 덫** (34세션 3절).

    ``st.cache_data`` 는 감싼 함수의 소스만 해시한다. 그 함수가 부르는 모듈이
    바뀌어도 키가 그대로라, 앱이 떠 있는 채로 코드를 고치면 화면이 옛 값을 낸다.
    """
    from kwise.ui.cache import code_stamp, rules_stamp

    stamp = code_stamp()
    assert stamp and len(stamp) == 12
    assert stamp in rules_stamp(), "코드 지문이 캐시 키에 물려 있지 않습니다."
    # 프로세스마다 한 번만 잰다 — 재실행마다 100여 파일을 훑으면 안 된다.
    assert code_stamp() is stamp


def test_계산을_누르기_전에는_합산효과가_없다() -> None:
    """**체크마다 조합 전부의 요금이 다시 돌던 자리다** (33세션 5절)."""
    screen = _running(option="I", combination_pick=None, **STAGE3_MEASURES)  # type: ignore[arg-type]
    assert not screen.exception, screen.exception
    assert "합산효과" not in _labels(screen)
    body = " ".join(str(item.value) for item in screen.caption)
    assert "「합산효과 계산」 을 누르십시오" in body, body

    run = screen.button(key="combo_run").click().run(timeout=600)
    assert not run.exception, run.exception
    assert "합산효과" in _labels(run)


def test_단순_합과_합산효과와_차이를_모두_보인다(stage3: AppTest) -> None:
    """**이것이 3단계의 존재 이유다** (14세션 5-2)."""
    assert not stage3.exception, stage3.exception
    assert _stage3_metrics(stage3)[:3] == ["단순 합", "합산효과", "차이"]
    gap = next(item for item in stage3.metric if item.label == "차이")
    assert str(gap.delta).endswith("%"), gap.delta  # 차이를 비율로도 낸다


def test_차이가_생기는_이유를_적는다(stage3: AppTest) -> None:
    """**실제로 발생한 상호작용만** 적는다 (14세션 5-2 · 22세션 2절).

    22세션에 본문에서 **계산 근거 접힘**으로 옮겼다 — 「왜 단순 합과 다른가」 는
    산출 근거이지 결론이 아니고, 본문 여섯 줄이 예산을 넘겼다. 지운 것이 아니다.
    """
    assert not stage3.exception, stage3.exception
    tables = " ".join(
        " ".join(str(value) for value in frame.value.astype(str).to_numpy().ravel())
        for frame in stage3.dataframe
    )
    assert "기본요금 기반이 달라집니다" in tables
    assert "이유 1" in tables


def test_계약전력_추가_하향_여지가_나온다(stage3: AppTest) -> None:
    """2단계 7.2 는 현재 부하 기준이고, 조합 기준 추가 여지는 여기서만 낸다 (5-2)."""
    assert not stage3.exception, stage3.exception
    body = " ".join(str(item.value) for item in stage3.markdown)
    # **소제목을 없애고 한 줄로 합쳤다** (22세션 1절).
    assert "계약전력 추가 하향 여지" in body
    assert "이 조합이면" in body


def _after(metrics: list[tuple[str, str]], anchor: str, offset: int) -> str:
    """카드 안에서 ``anchor`` 로부터 몇 칸 뒤의 지표 값. 카드 경계를 잡는 방법이다."""
    index = next(position for position, (label, _) in enumerate(metrics) if label == anchor)
    return metrics[index + offset][1]


def test_개선안별_요약이_2단계_카드와_같은_값이다() -> None:
    """**재계산하지 않는다** (14세션 5-1). 두 화면이 다르면 어느 쪽을 믿나.

    회수기간은 두 화면이 같은 서식(``0.0년``)으로 찍으므로 문자열째로 견줄 수 있다.
    """
    screen = _running(option="I", **STAGE3_MEASURES)  # type: ignore[arg-type]
    assert not screen.exception, screen.exception
    metrics = _stage2_metrics(screen)
    # 화면 표는 절 번호가 아니라 순번으로 적는다 (27세션 2절).
    card_payback = {
        "4. 역률 개선": _after(metrics, "현재 역률", 3),
        "6. ESS": _after(metrics, "출력 / 용량", 3),
    }

    frame = next(item.value for item in screen.dataframe if "수단" in list(item.value.columns))
    rows = {str(row["수단"]): row for _, row in frame.iterrows()}

    for title in ("1. 선택요금 전환", "2. 계약전력 조정", "4. 역률 개선", "6. ESS"):
        assert title in rows, rows.keys()
    assert "단순 합" in rows
    for title, expected in card_payback.items():
        assert str(rows[title]["회수기간"]) == expected, title


def test_합계_행에_단순_합이라고_적는다() -> None:
    """수단별 절감액의 합은 최종 효과가 아니다 (14세션 5-1)."""
    from kwise.report import SIMPLE_SUM_LABEL, SIMPLE_SUM_NOTE

    assert SIMPLE_SUM_LABEL == "단순 합"
    assert "최종 효과가 아닙니다" in SIMPLE_SUM_NOTE


# ======================================================== 14세션 · 금액 표기


WON_ON_SCREEN = re.compile(r"(?<![만억\d])(\d[\d,]*)원(?![/\w])")


def _screen_amounts(screen: AppTest) -> list[str]:
    """화면에 찍힌 **원 단위 금액** 을 모두 긁는다. 만원·억원 표기는 뺀다.

    표(``st.dataframe``)까지 훑는다 — 금액은 대개 표 안에 있다.
    """
    blobs = [
        str(item.value)
        for group in (screen.metric, screen.markdown, screen.caption, screen.warning)
        for item in group
    ]
    blobs += [str(item.delta) for item in screen.metric if item.delta]
    blobs += [frame.value.to_string() for frame in screen.dataframe]
    return [match.group(1) for blob in blobs for match in WON_ON_SCREEN.finditer(blob)]


def test_화면의_원_단위_금액이_모두_천의_배수다(stage3: AppTest) -> None:
    """**정규식 검사** (14세션 1절). 한 자리라도 새면 여기서 잡힌다."""
    assert not stage3.exception, stage3.exception
    amounts = _screen_amounts(stage3)
    assert amounts, "화면에서 원 단위 금액을 찾지 못했습니다."
    offenders = [item for item in amounts if int(item.replace(",", "")) % 1_000]
    assert not offenders, offenders


def test_반올림_각주가_화면에_있다(stage3: AppTest) -> None:
    """항목 합과 합계 표시가 어긋날 수 있다는 사실을 적는다 (14세션 1절).

    **표기가 만원 반올림으로 바뀌었으므로 각주도 바뀐다** (28세션 1-3).
    산출물(Excel·Word)은 원 단위 절사 그대로라 각주도 그대로다.
    """
    assert not stage3.exception, stage3.exception
    body = " ".join(str(item.value) for item in stage3.caption)
    assert text.ROUNDING_FOOTNOTE in body
    assert text.TRUNCATION_FOOTNOTE not in body


def test_조합_기준_하향_여지를_합산효과에서_낸다(stage3: AppTest) -> None:
    """조합이 피크를 얼마나 낮추느냐에 따라 여지가 달라진다 (14세션 5-2·5-3).

    조합 비교 표를 없앴으므로(16세션 5절) 이 사실은 합산효과 절의 글로 남는다.
    """
    assert not stage3.exception, stage3.exception
    body = " ".join(str(item.value) for item in stage3.markdown)
    assert "계약전력 추가 하향 여지" in body
    assert "kW" in body


# ======================================================== 16세션 · 중복과 금지어


def test_같은_지문이_화면에_두_번_나오지_않는다() -> None:
    """**동일 지문 전수 검사** (16세션 3절).

    같은 문장이 두 자리에 있으면 읽는 사람은 둘이 다른 사실인 줄 알고 차이를
    찾는다. 세 탭이 한 번에 그려지므로 겹침이 그대로 드러난다.
    """
    from collections import Counter

    screen = _running(**{f"measure_on_{key}": True for key in INDEPENDENT_MEASURES})
    assert not screen.exception, screen.exception
    lines = [
        str(item.value).strip()
        for group in (screen.markdown, screen.caption, screen.warning, screen.error)
        for item in group
        if len(str(item.value).strip()) >= 20
    ]
    repeated = [line for line, count in Counter(lines).items() if count > 1]
    assert repeated == [], repeated


def test_결측_안내가_두_줄을_넘지_않는다() -> None:
    """편중된 달마다 한 줄씩 붙어 열두 줄이 되던 자리다 (16세션 3절 · 30세션 2절)."""
    import dataclasses

    from kwise.quality import check_quality
    from kwise.ui.views.diagnose import MISSING_LINE_LIMIT

    quality = check_quality(load_usage(SAMPLE))
    # 열두 달이 모두 편중된 자료. 달마다 한 줄씩 붙던 자리다.
    flagged = tuple(dataclasses.replace(month, flagged=True) for month in quality.monthly[:12])
    many = dataclasses.replace(quality, monthly=flagged)
    assert len(many.flagged_months) == 12
    lines = missing_lines(many)
    assert len(lines) <= MISSING_LINE_LIMIT == 2
    assert lines[-1].startswith("결측이 있는 달")


def test_계약전력_변경_경고가_7_2_카드에_있다() -> None:
    """**바꾸자고 제안하는 자리가 이 경고의 제자리다** (16세션 3절)."""
    from kwise.report import CONTRACT_CHANGE_WARNING

    screen = _running(measure_on_contract=True)
    assert not screen.exception, screen.exception
    body = " ".join(
        str(item.value) for group in (screen.markdown, screen.caption) for item in group
    )
    assert CONTRACT_CHANGE_WARNING in body
    # 1단계에는 없다 — 같은 경고를 두 자리에 두지 않는다.
    plain = _running()
    assert CONTRACT_CHANGE_WARNING not in " ".join(
        str(item.value) for group in (plain.markdown, plain.caption) for item in group
    )


def test_적정성_지표가_7_2_카드로_옮겨왔다() -> None:
    screen = _running(measure_on_contract=True)
    labels = [label for label, _ in _stage2_metrics(screen)]
    assert "이용률" in labels
    assert "하향 여지" in labels


BANNED_WORDS = ("콘덴서", "APFR")


def test_콘덴서와_자동역률조정장치라는_말이_없다() -> None:
    """**「역률 개선」 으로만 적는다** (16세션 6-2). 화면·산출물·문서 전부다."""
    roots = (Path("src") / "kwise", Path("docs"))
    offenders: list[str] = []
    for root in roots:
        for path in sorted(root.rglob("*")):
            if path.suffix not in {".py", ".md"} or path.name == "REQUIREMENTS_kwise.md":
                continue
            body = path.read_text(encoding="utf-8")
            offenders.extend(f"{path}: {word}" for word in BANNED_WORDS if word in body)
    assert offenders == [], offenders


def test_투자_구분_이름에도_없다() -> None:
    from kwise.measures.catalog import TIER_LOW

    assert TIER_LOW == "저투자 (역률 개선)"


def test_탭을_오가도_입력이_남는다() -> None:
    """**한 실행에서 셋이 함께 그려지므로 옮길 때 잃을 것이 없다** (16세션 1절).

    기준 데이터 화면으로 나갔다 돌아와도 켠 수단과 넣은 값이 그대로여야 한다.
    """
    screen = _running(measure_on_power_factor=True)
    target = screen.number_input(key="measure_power_factor_target")
    before = target.value
    screen = target.set_value(95.0).run(timeout=600)
    assert not screen.exception, screen.exception
    assert before != 95.0

    screen.sidebar.button(key="nav_rules").click().run(timeout=600)
    screen.sidebar.button(key="nav_analysis").click().run(timeout=600)
    assert not screen.exception, screen.exception
    assert screen.session_state["measure_on_power_factor"] is True
    assert screen.session_state["measure_power_factor_target"] == 95.0
    assert screen.number_input(key="measure_power_factor_target").value == 95.0


def test_축이_0에서_시작하지_않는다는_문구가_없다() -> None:
    """**눈금이 0 이 아닌 것은 보면 안다** (21세션 5절).

    17세션에 축을 자르기로 하면서 축 제목에 그 사실을 적었다. 화면·보고서
    어디에도 남기지 않는다 — 자른 축은 눈금이 말한다.
    """
    roots = (Path("src") / "kwise" / "ui", Path("src") / "kwise" / "report")
    offenders: list[str] = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            # 문서 문자열과 주석은 화면에 나가지 않는다. :func:`_strings` 가 걸러 준다.
            for value in _strings(path):
                if "0 부터 시작하지 않" in value or "0부터 시작하지 않" in value:
                    offenders.append(f"{path.name}: {value[:40]}")
    assert offenders == [], "축 안내 문구가 남아 있습니다: " + ", ".join(offenders)


# ======================================================== 화면 예산 (22세션 1절)


@pytest.fixture(scope="module")
def budget_sections() -> list[object]:
    """**앱을 한 번만 띄운다.** 수단 일곱을 다 켠 실행이라 무겁다 (ESS 곡선)."""
    import sys

    sys.path.insert(0, str(Path("tools").resolve()))
    from screen_budget import measure  # type: ignore[import-not-found]

    return measure()


def test_예산_한도가_기준_데이터에서_온다() -> None:
    """**코드에 한도를 두지 않는다** (요구사항서 12장). 판단값이다."""
    import sys

    sys.path.insert(0, str(Path("tools").resolve()))
    from screen_budget import screen_budget  # type: ignore[import-not-found]

    from kwise.rules import assumption

    budget = screen_budget()
    assert budget.body_lines == int(assumption("ui.body_line_budget")) == 3
    assert budget.notices == int(assumption("ui.notice_budget")) == 3


def test_카드와_절이_예산을_지킨다(budget_sections: list[object]) -> None:
    r"""본문 3줄 · 확인사항 3항목 (22세션 1절).

    **넘으면 옮긴다 — 한도를 고치지 않는다.** 한도를 넘었을 때 세는 규칙이나
    값을 고치면 장치가 무력해지고, 다음에 또 넘으면 또 고치게 된다.

    도구(``tools\screen_budget.py``)와 **같은 함수**를 쓴다. 잣대가 갈리면
    도구가 통과시킨 것이 여기서 깨진다.
    """
    from screen_budget import over_budget  # type: ignore[import-not-found]

    assert len(budget_sections) >= 15, "화면을 못 읽었습니다. 측정기가 깨졌습니다."
    assert over_budget(budget_sections) == []


def test_앵커가_모두_화면에서_쓰인다() -> None:
    """**놀고 있는 앵커를 두지 않는다** (22세션 5절).

    16세션에 링크를 걷어내면서 앵커 목록만 남았고, 22세션에 세어 보니 32개 중
    아홉이 화면 어디에서도 불리지 않았다 — 매뉴얼에는 자리가 있는데 화면에서
    갈 길이 없던 것이다. 아홉을 제자리(지표·입력·표 열)에 붙였다.

    **매뉴얼 문서와 앵커 id 는 유지한다.** 나중에 링크를 되살릴 수 있어야 한다.
    """
    import re

    from kwise.ui.anchors import ANCHORS

    used: set[str] = set()
    for path in sorted((Path("src") / "kwise" / "ui").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "manual_tip"):
                continue
            if node.args and isinstance(node.args[0], ast.Constant):
                used.add(str(node.args[0].value))
    # 수단 카드는 `manual_tip(spec.anchor)` 로 부른다 — 키가 spec 에 있다.
    spec = (Path("src") / "kwise" / "ui" / "spec.py").read_text(encoding="utf-8")
    used |= set(re.findall(r'"(measure-[a-z-]+)"', spec))

    keys = {item.key for item in ANCHORS}
    assert keys - used == set(), f"화면에서 부르지 않는 앵커: {sorted(keys - used)}"
    assert used - keys == set(), f"목록에 없는 앵커를 부릅니다: {sorted(used - keys)}"


# ======================================================== 그래프 규약 (23세션)


def _chart_specs() -> dict[str, object]:
    """화면 차트를 **실제로 만들어** 돌려준다. 사양을 훑어 규약을 본다."""
    import pandas as pd

    from kwise.diagnose import ContractInfo, diagnose
    from kwise.measures import (
        EssCostInput,
        ess_target_curve,
        evaluate_ess,
        evaluate_power_factor,
        evaluate_tariff_switch,
        light_band_mask,
    )
    from kwise.quality import check_quality
    from kwise.report.days import representative_days
    from kwise.ui import charts

    usage = load_usage(SAMPLE)
    table = load_tariff()
    quality = check_quality(usage)
    selection = TariffSelection("general_b", "high_a", "I")
    diagnosis = diagnose(
        usage, table, ContractInfo(selection, contract_kw=5_500.0), quality=quality
    )
    bill = diagnosis.structure.bill if diagnosis.structure is not None else None
    assert bill is not None
    switch = evaluate_tariff_switch(usage, table, selection, quality=quality)
    power_factor = evaluate_power_factor(usage, table, selection, baseline=bill, quality=quality)
    ess = evaluate_ess(
        usage,
        table,
        selection,
        target_kw=5_200.0,
        cost=EssCostInput.of_unit_cost(615_231.0),
        charge_mask=light_band_mask(usage, table, selection=selection),
        baseline=bill,
        quality=quality,
    )
    curve = ess_target_curve(
        usage.kw,
        usage.meta.interval_minutes,
        baseline_demand_kw=bill.billing_demand_kw,
        base_fee_won_per_kw=bill.base_rate_won_per_kw,
    )
    day = representative_days(usage)[0]
    generation = pd.Series(1.0, index=usage.kw.index)
    assert diagnosis.dr is not None and diagnosis.structure is not None
    return {
        "chart.monthly_peak": charts.monthly_peak_chart(diagnosis.peak),
        "chart.top_hour": charts.top_hour_chart(diagnosis.peak),
        "chart.hourly_profile": charts.hourly_profile_chart(diagnosis.peak),
        "chart.band": charts.band_donut_chart(diagnosis.structure),
        "chart.monthly_charge": charts.monthly_charge_chart(diagnosis.structure),
        "chart.tariff_option": charts.tariff_option_chart(switch),
        "chart.tariff_delta": charts.tariff_delta_chart(switch),
        "chart.dr_daily": charts.dr_daily_chart(diagnosis.dr),
        "chart.power_triangle": charts.power_triangle_chart(power_factor),
        "chart.power_factor_day": charts.power_factor_day_chart(
            usage, day, current_pct=92.0, target_pct=97.0
        ),
        "chart.solar_annual": charts.solar_annual_chart(usage, generation),
        "chart.solar_day": charts.solar_day_chart(usage, generation, day, zoom=True),
        "chart.ess_target": charts.ess_target_chart(curve),
        "chart.ess_day": charts.ess_day_chart(usage, ess.dispatch, day),
        "chart.surplus_daily": charts.surplus_daily_chart(
            usage, pd.Series(0.5, index=usage.kw.index)
        ),
    }


@pytest.fixture(scope="module")
def chart_specs() -> dict[str, object]:
    """차트 한 벌. **모듈에서 한 번만 만든다** — 요금 계산이 여러 번 돈다."""
    return _chart_specs()


#: **범례를 아래에 두는 차트** (27세션 6절). 도형 옆에 설명 글자를 직접 적어
#: 바깥 오른쪽 범례와 자리를 다투는 그림만이다. 늘리려면 이유를 적는다.
LEGEND_BELOW_CHARTS = {"chart.power_triangle"}

#: **범례를 아예 달지 않는 차트** (33세션 3절 · 34세션 1절). 계시별 사용량 구성은
#: 원이 넷이라 범례를 달면 같은 이름 넷이 네 번 실린다 — 조각마다 이름과 비중을
#: 적는 편이 짧고 정확하다. 늘리려면 이유를 적는다.
NO_LEGEND_CHARTS = {"chart.band"}


def test_전_차트가_같은_범례_규약을_쓴다(chart_specs: dict[str, object]) -> None:
    """**바깥 오른쪽 · 배경 없음** (23세션 1절 · 27세션 6절).

    17세션이 준 ``fillColor="white"`` 가 다크 모드에서 흰 상자에 회색 글씨를
    만들었다. 배경을 칠하지 않으면 테마가 그대로 비쳐 두 모드에서 다 읽힌다.

    **예외는 :data:`LEGEND_BELOW_CHARTS` 뿐이다** — 그림 안의 글자가 오른쪽으로
    뻗는 차트는 아래로 내린다. 배경 없음·안쪽 금지는 예외 없이 지킨다.
    """
    import json

    offenders: list[str] = []
    for name, chart in chart_specs.items():
        spec = json.dumps(chart.to_dict(), ensure_ascii=False, default=str)  # type: ignore[attr-defined]
        if '"fillColor": "white"' in spec:
            offenders.append(f"{name}: 범례에 흰 배경")
        if '"orient": "bottom-right"' in spec:
            offenders.append(f"{name}: 범례가 그림 안쪽")
        if '"legend"' not in spec:
            continue
        if name in NO_LEGEND_CHARTS:
            assert '"legend": null' in spec, f"{name}: 범례를 달지 않기로 한 차트입니다."
            continue
        wanted = '"orient": "bottom"' if name in LEGEND_BELOW_CHARTS else '"orient": "right"'
        if wanted not in spec:
            offenders.append(f"{name}: 범례 자리가 규약과 다름")
    assert offenders == [], " / ".join(offenders)


def test_범례_설정이_두_벌뿐이다() -> None:
    """차트마다 따로 주면 또 갈라진다 — 상수 둘을 모두가 나눠 쓴다 (27세션 6절)."""
    from kwise.ui.charts import LEGEND, LEGEND_BELOW

    source = (Path("src") / "kwise" / "ui" / "charts.py").read_text(encoding="utf-8")
    assert "_LEGEND_BOTTOM" not in source, "옛 범례 상수가 남아 있습니다."
    assert source.count("alt.Legend(") == 2, "범례 정의는 기본과 아래 둘뿐이어야 합니다."
    assert LEGEND.to_dict()["orient"] == "right"
    assert LEGEND_BELOW.to_dict()["orient"] == "bottom"
    for legend in (LEGEND, LEGEND_BELOW):
        assert legend.to_dict().get("fillColor") is None


def test_보고서_png_도_같은_범례_규약이다() -> None:
    """**화면만 고치면 어긋난다** — 나란히 놓고서야 드러난다 (13세션)."""
    from kwise.report.figures import LEGEND_STYLE

    assert LEGEND_STYLE["frameon"] is False, "png 범례에 배경 상자가 있습니다."
    assert LEGEND_STYLE["bbox_to_anchor"] == (1.01, 1.0), "png 범례가 그림 바깥이 아닙니다."
    source = (Path("src") / "kwise" / "report" / "figures.py").read_text(encoding="utf-8")
    assert ".legend(fontsize=" not in source, "add_legend 를 거치지 않는 범례가 있습니다."


def test_선과_면_차트는_0에서_시작하지_않는다(chart_specs: dict[str, object]) -> None:
    """**막대는 자르지 않는다** (23세션 2절).

    선·면은 두 값의 간격이 뜻이라 축을 자르면 그 간격이 드러난다. 막대는 길이가
    곧 값이라 자르면 길이가 거짓말을 하고, 개수를 세는 막대는 0 이 기준점이다.
    """
    cut = {
        "chart.hourly_profile",
        "chart.power_factor_day",
        "chart.dr_daily",
        "chart.solar_day",
        "chart.ess_day",
    }
    for name in cut:
        spec = chart_specs[name].to_dict()  # type: ignore[attr-defined]
        rendered = str(spec)
        assert '"zero": False' in rendered or "'zero': False" in rendered, (
            f"{name} 이 0 부터입니다."
        )


def test_모든_그래프에_설명_툴팁이_있다(chart_specs: dict[str, object]) -> None:
    """물음표를 눌러 **무엇을 읽어야 하는지** 알 수 있어야 한다 (23세션 3절)."""
    from kwise.ui.text import CHART_TIPS, chart_tip

    for name in chart_specs:
        assert name in CHART_TIPS, f"{name} 툴팁이 없습니다."
    for key, tip in CHART_TIPS.items():
        assert key.startswith("chart."), key
        # **지표 툴팁과 형식이 다르다** — 산식이 아니라 「무엇을 그렸나 + 무엇을 읽나」다.
        first, _, meaning = tip.partition("\n\n")
        assert first.strip().endswith(("다.", "요.", "니다.")), key
        assert meaning.strip(), f"{key} 에 읽는 법이 없습니다."
    with pytest.raises(KeyError, match="등록되지 않은"):
        chart_tip("chart.없는것")


def test_화면이_그래프마다_툴팁을_단다() -> None:
    """차트를 그린 자리 옆에 ``chart_tip`` 이 있어야 한다."""
    keys: set[str] = set()
    charts_drawn = 0
    for path in sorted(VIEWS.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        charts_drawn += source.count("st.altair_chart(")
        keys |= set(re.findall(r'chart_tip\("([a-z_.]+)"\)', source))
    assert charts_drawn >= 12, "차트를 못 찾았습니다."
    # 화면에 그리는 차트 수만큼 툴팁 열쇠가 있어야 한다 (같은 차트를 두 번 그리지 않는다).
    assert len(keys) >= 12, sorted(keys)


def test_피크_특성은_프로파일을_먼저_보인다() -> None:
    """**하루 모양을 본 뒤에 상위 구간을 읽는다** (23세션 4절).

    상위 100구간 분포부터 보면 무엇에 견주는 분포인지 알 수 없다.
    """
    source = (VIEWS / "diagnose.py").read_text(encoding="utf-8")
    body = source[source.index("def _peak_block(") : source.index("def _structure_block(")]
    profile_at = body.index("hourly_profile_chart")
    top_at = body.index("top_hour_chart")
    assert profile_at < top_at, "시간대별 프로파일이 상위 구간보다 뒤에 있습니다."


def test_태양광_일별은_발전량만_그린다() -> None:
    """**한 그림은 한 가지만 말한다** (23세션 5절).

    사용량(일 60 MWh 대)과 함께 그리니 발전량(3 MWh 대)이 바닥에 눌렸고,
    호버도 날짜와 수전량을 내놓아 정작 발전량을 읽을 수 없었다.
    """
    import pandas as pd

    from kwise.ui.charts import solar_annual_chart

    usage = load_usage(SAMPLE)
    spec = solar_annual_chart(usage, pd.Series(1.0, index=usage.kw.index)).to_dict()
    assert spec["encoding"]["y"]["field"] == "발전량(kWh)"
    fields = [item["field"] for item in spec["encoding"]["tooltip"]]
    assert fields == ["날짜", "발전량(kWh)"], fields
    assert "계통 수전(kWh)" not in str(spec), "수전량이 남아 있습니다."


def test_피크_그래프는_확대본_하나뿐이다() -> None:
    """하루 스물넷을 다 그리면 저감 구간이 손톱만 해진다 (23세션 5-3·6-1)."""
    source = (VIEWS / "measures.py").read_text(encoding="utf-8")
    solar_calls = re.findall(r"charts\.solar_day_chart\((.*?)\)\n", source, re.S)
    assert len(solar_calls) == 1, f"태양광 대표일 차트가 {len(solar_calls)}개입니다."
    assert "zoom=True" in solar_calls[0]
    # ESS 는 기본이 확대다 — 부르는 쪽에서 켜지 않아도 된다.
    from kwise.ui.charts import ess_day_chart

    assert inspect.signature(ess_day_chart).parameters["zoom"].default is True


def test_ESS_충방전이_문구로_나온다() -> None:
    """**그림이 둘일 필요가 없다** (23세션 6-2). 위 칸과 종속이다."""
    from kwise.ui.charts import ess_day_chart

    source = (VIEWS / "measures.py").read_text(encoding="utf-8")
    assert "dispatch_schedule" in source, "충·방전 시각 문구가 없습니다."
    assert "충전" in source and "방전" in source
    # 차트는 한 칸이다 — vconcat(2단)이 아니다.
    assert "VConcatChart" not in inspect.signature(ess_day_chart).return_annotation


def test_감도는_계산만_하고_화면에_그리지_않는다() -> None:
    """**결론이 움직이지 않는 값은 화면에 두지 않는다** (28세션 5절).

    계산은 그대로 돈다 — Excel 「감도 상세」·「감도 범위」 와 보고서가 같은
    프레임을 받는다. 없앤 것은 화면 표기뿐이다.
    """
    source = (VIEWS / "compare.py").read_text(encoding="utf-8")
    body = source[source.index("def _sensitivity_data(") : source.index("def _download_block(")]
    assert "cached_sensitivity(" in body, "감도 계산까지 사라졌습니다."
    for banned in ("st.subheader", "st.expander", "st.write", "st.caption", "callout."):
        assert banned not in body, f"감도가 화면에 무언가를 그립니다: {banned}"
    # Excel 시트는 그대로다.
    from kwise.report.excel import SHEET_ORDER

    assert "감도 상세" in SHEET_ORDER


# ======================================================== 25세션 · 실주행 감사
#
# **소스 훑기로는 툴팁까지 못 본다** (25세션 2절). ``help=`` 로 가는 글은 대부분
# 함수가 만들어 내므로 문자열 상수를 훑어서는 잡히지 않는다 — 물결표 escape 가
# 툴팁에만 빠져 있던 것이 그래서 세 세션을 살아남았다. 도구
# (``tools\screen_audit.py``)와 **같은 함수**로 화면을 실제로 띄워 본다.


def _audit() -> Any:
    """``tools\\screen_audit.py`` 를 불러온다. **도구와 같은 함수를 쓴다.**"""
    import sys

    sys.path.insert(0, str(Path("tools").resolve()))
    import screen_audit  # type: ignore[import-not-found]

    return screen_audit


@pytest.fixture(scope="module")
def screen_lines() -> tuple[object, ...]:
    """수단 일곱을 켠 화면 한 벌의 문구 전부. **앱을 한 번만 띄운다.**

    태양광 입력은 넣지 않는다 — 시험은 기상 사전 취득분에서 격리되어 있어
    (``conftest.isolated_weather_archive``) 발전량을 계산할 수 없다. 태양광 카드의
    문구는 소스 훑기(:func:`test_보고서와_excel_문구도_같은_잣대다`)가 함께 본다.
    """
    return _audit().collect(solar=False)


def test_화면_문구를_실주행으로_모은다(screen_lines: tuple[object, ...]) -> None:
    """수집기가 깨지면 아래 시험이 **조용히 통과한다.** 먼저 잡는다."""
    slots = {getattr(item, "slot", "") for item in screen_lines}
    assert len(screen_lines) >= 300, "화면 문구를 못 모았습니다. 수집기가 깨졌습니다."
    assert {"본문", "툴팁", "라벨", "지표", "표"} <= slots, slots


def test_툴팁까지_escape_한다(screen_lines: tuple[object, ...]) -> None:
    """**렌더 직전 문자열에 맨 물결표가 없다** (25세션 2절).

    ``st.caption`` 만 escape 하고 그 옆 ``help=`` 를 빠뜨리면 물음표 안에서
    ``3~6월·10~11월`` 이 취소선으로 그려진다. 마크다운을 해석하는 자리를 모두 본다.
    """
    offenders = _audit().offenders(screen_lines)
    assert not offenders.get("맨 물결표"), [
        f"[{item.slot}] {item.where} :: {item.text[:80]}" for item in offenders["맨 물결표"]
    ]


@pytest.mark.parametrize(
    "rule", ["코드 식별자", "요구사항서 참조", "규정 이름 없는 조문", "규정 이름 없는 별표"]
)
def test_화면에_개발자_언어가_없다(rule: str, screen_lines: tuple[object, ...]) -> None:
    """코드 식별자·내부 문서 번호·규정 이름 없는 조문 (25세션 4절)."""
    offenders = _audit().offenders(screen_lines)
    assert not offenders.get(rule), [
        f"[{item.slot}] {item.where} :: {item.text[:80]}" for item in offenders[rule]
    ]


@pytest.mark.parametrize(
    "rule", ["코드 식별자", "요구사항서 참조", "규정 이름 없는 조문", "규정 이름 없는 별표"]
)
def test_보고서와_excel_문구도_같은_잣대다(rule: str) -> None:
    """**화면만으로는 닿지 않는다** (25세션 4-3). 보고서 본문·부록과 Excel 비고는
    화면에 없지만 사용자가 읽는 글이다. 소스의 «사용자에게 가는» 문자열을 훑는다.
    """
    audit = _audit()
    lines = audit.source_lines()
    assert len(lines) >= 1_000, "소스 문구를 못 모았습니다. 추출기가 깨졌습니다."
    offenders = audit.offenders(lines)
    assert not offenders.get(rule), [
        f"{item.where} :: {item.text[:80]}" for item in offenders[rule]
    ]


# ======================================================== 25세션 · 입력 끝값


def test_역률_목표를_현재보다_낮춰도_화면이_살아_있다() -> None:
    """**예외를 화면에 던지지 않는다** (25세션 1절).

    「도입 후 지상역률」 을 현재값 아래로 내리면 ``ValueError`` 가 화면에 그대로
    떴다. 이제는 늘어나는 요금을 낸다.

    **현재 역률을 넣은 사람이 겪는다.** 목표 입력의 하한은 기준 92% 라 기본값
    (간주 92%)에서는 내려갈 자리가 없다. 청구서를 보고 97% 를 넣으면 그 아래
    전 구간이 「현재보다 낮은 목표」 가 된다 — 사용자가 겪은 것이 96.9% 였다.

    **위젯을 실제로 조작한다.** 세션에 값을 미리 넣는 방식은 통하지 않는다 —
    이 입력은 ``value=`` 를 함께 주므로 첫 그리기에서 그 기본값이 이긴다.
    """
    screen = _running(
        measure_on_power_factor=True,
        contract_form=ContractForm(
            contract_type="general_b",
            voltage="high_a",
            option="II",
            contract_kw=6_000.0,
            power_factor_pct=97.0,
        ),
    )
    target = screen.number_input(key="measure_power_factor_target")
    lowered = target.set_value(92.0).run(timeout=600)
    assert not lowered.exception, lowered.exception
    assert lowered.number_input(key="measure_power_factor_target").value == 92.0

    body = " ".join(_screen_lines(lowered))
    assert "개선이 아니라 악화" in body, "요금이 늘어난다는 사실을 적어야 합니다."


def test_관측이_없는_날을_대표일로_골라도_화면이_살아_있다() -> None:
    """**결측이 온종일인 날도 고를 수 있다** (25세션 1절).

    「일일 곡선 대표일」 의 날짜 입력은 분석 기간 전체를 허용하는데, 그 안에는
    관측이 하나도 없는 날이 있다 (샘플은 2023-11-04 부터 아흐레). 그 날을 고르면
    ``peak_window`` 가 ``ValueError: Encountered all NA values`` 를 던져 화면이
    통째로 죽었다.
    """
    screen = _running(
        measure_on_power_factor=True,
        measure_on_ess=True,
        measure_common_ref_day="custom",
        measure_common_ref_day_custom=dt.date(2023, 11, 4),
    )
    assert not screen.exception, screen.exception


#: 수치 입력의 **끝값**. 위젯이 허용하는 값이면 계산이 받아 주어야 한다.
EDGE_VALUES: tuple[tuple[str, float], ...] = (
    ("measure_power_factor_target", 92.0),  # 하한 — 기준 역률
    ("measure_power_factor_target", 100.0),  # 상한 — 감액 상한을 넘는다
    ("measure_power_factor_investment", 0.0),
    ("measure_contract_margin", 0.0),  # 여유율 하한
    ("measure_contract_margin", 0.3),  # 여유율 상한
    ("measure_ess_fixed_cost", 0.0),
    ("measure_ess_per_kwh_cost", 0.0),  # 두 계수가 함께 0 이 된다
    ("measure_ess_total_cost", 1_000_000_000.0),
    ("measure_surplus_price", 0.0),
)


def test_입력_끝값을_훑어도_화면이_죽지_않는다() -> None:
    """**사용자 입력으로 예외가 나는 자리를 훑는다** (25세션 1절).

    역률 목표가 그랬듯, 위젯이 내주는 값을 계산이 거절하면 그 예외가 화면에
    그대로 뜬다. 받을 수 없는 값이면 **안내를 내지, 화면을 죽이지 않는다.**

    한 화면에서 값을 **차례로 쌓는다** — 끝값이 겹쳤을 때가 가장 험한 조합이고,
    카드를 다시 그리는 비용도 한 번으로 끝난다.
    """
    # 태양광만 뺀다 — 시험은 기상 사전 취득분에서 격리되어 있다.
    keys = ("tariff_switch", "contract", "demand_response", "power_factor", "ess", "surplus")
    screen = _running(**{f"measure_on_{key}": True for key in keys})
    assert not screen.exception, screen.exception

    touched = 0
    for key, value in EDGE_VALUES:
        try:
            widget = screen.number_input(key=key)
        except KeyError:  # 그 화면에 없는 입력은 건너뛴다
            continue
        screen = widget.set_value(value).run(timeout=900)
        touched += 1
        assert not screen.exception, f"{key} = {value}: {screen.exception}"
    assert touched >= 7, f"끝값을 넣은 입력이 {touched}개뿐입니다. 키가 바뀌었습니다."


# ======================================================== 26세션 · 용어와 단위


def test_무인시간이라는_말이_사라졌다() -> None:
    """**「운영시간 외」 로 바꿨다** (26세션 0-2).

    「무인시간」 은 그 시간에 아무도 없다고 단정하는 말이라 연장 근무·야간 당직이
    있는 건물에서는 사실이 아니다. 설정한 운영시간 밖이라는 **사실만** 말하도록
    이름을 고쳤다 — 설명을 덧붙이는 대신 이름을 고치는 것이 규약이다 (CLAUDE.md).
    """
    # 이 시험 파일만 예외다 — 막으려는 말을 적어야 막을 수 있다.
    banned = "무" + "인시간"
    offenders: list[str] = []
    for root in (Path("src") / "kwise", Path("docs")):
        for path in sorted(root.rglob("*")):
            if path.suffix not in {".py", ".md"}:
                continue
            if banned in path.read_text(encoding="utf-8"):
                offenders.append(str(path))
    assert offenders == [], offenders
    # 코드 식별자도 함께 옮겼다.
    import inspect

    from kwise.quality import load_pattern

    assert "unattended" not in inspect.getsource(load_pattern)


def test_화면_문구_원칙이_규약에_있다() -> None:
    """**앞으로의 판단 기준이다** (26세션 0-1). 규약 파일에 없으면 잊힌다."""
    body = Path("CLAUDE.md").read_text(encoding="utf-8")
    assert "## 화면 문구" in body
    for rule in (
        "화면 문구는 늘리지 않는다",
        "용어나 이름을 고쳐서 해결되는지",
        "무엇을 뺄지 함께 정한다",
        "screen_audit.py",
    ):
        assert rule in body, rule


def test_12개월_환산값에_기간_단위가_붙는다() -> None:
    """**896만원 → 896만원/년** (26세션 2-3).

    라벨만 보고는 한 달인지 한 해인지 알 수 없다. 값이 없어 사유가 들어온 자리에는
    붙이지 않는다 — ``미산출 — 단가 미입력/년`` 은 말이 되지 않는다.
    """
    assert text.won_year(8_960_000.0) == "896만원/년"
    assert text.won_year(None, reason="미산출 — 단가 미입력") == "미산출 — 단가 미입력"
    assert text.per_year(text.mwh(1_940_781.0)) == "1,940.8 MWh/년"
    assert text.per_year(text.DASH) == text.DASH


def test_카드_절감액이_3단계_표와_같은_기준이다() -> None:
    """**둘 다 12개월 환산이다** (26세션 2-3).

    2단계 카드는 기간 절감액을, 3단계 표는 12개월 환산을 내고 있었다 — 「2단계
    카드 값을 그대로 옮긴다」 고 적어 두고 실제로는 다른 값이었다. 카드를 환산
    기준으로 맞춰 둘을 같게 했다.
    """
    screen = _running(option="I", **STAGE3_MEASURES)  # type: ignore[arg-type]
    assert not screen.exception, screen.exception
    savings = [value for label, value in _stage2_metrics(screen) if label == "절감액"]
    assert savings, "카드 절감액을 못 찾았습니다."
    for value in savings:
        assert value.endswith("/년"), value

    frame = next(item.value for item in screen.dataframe if "수단" in list(item.value.columns))
    rows = {str(row["수단"]): str(row["연간 절감액"]) for _, row in frame.iterrows()}
    # 역률 카드는 언제나 값이 있다 — 두 화면의 금액이 같은 크기여야 한다.
    card = next(value for label, value in _stage2_metrics(screen) if label == "절감액")
    assert card.rstrip("/년"), card
    assert rows["1. 선택요금 전환"], rows


def test_잉여_판정이_태양광_카드에_있다() -> None:
    """**판단의 갈림길은 잉여다** (26세션 3-2).

    기상 사전 취득분에서 격리되어 있어 실주행으로는 태양광 결과를 만들 수 없다.
    화면이 무엇을 내는지는 소스로, 값이 맞는지는 순수 함수 시험으로 나눠 본다.
    """
    source = (VIEWS / "measures.py").read_text(encoding="utf-8")
    body = source[source.index("def _surplus_verdict(") : source.index("def _share(")]
    for label in ("연간 잉여", "평일 잉여", "토·일·공휴일 잉여", "잉여 없는 최대 용량"):
        assert f'"{label}"' in body, label
    # 잉여 활용 카드에는 같은 지표가 없다 — 한 곳에만 둔다.
    start = source.index("def _surplus(")
    surplus_card = source[start : source.index("# ------", start)]
    for banned in ('"잉여 전력량"', '"발전량 대비"', '"주말 비중"'):
        assert banned not in surplus_card, banned


def test_잉여가_나지_않는_최대_용량() -> None:
    """발전이 있는 구간의 ``부하 ÷ 단위발전`` 최솟값이다 (26세션 3-2)."""
    import tempfile

    import pandas as pd

    from kwise.measures import apply_generation, surplus_free_capacity_kwp
    from tests._synthetic import make_labels, month_dates, write_csv

    rows: list[tuple[str, float]] = []
    for date in month_dates(2024, 3):
        for label in make_labels(date):
            rows.append((label, 100.0))
    usage = load_usage(write_csv(Path(tempfile.mkdtemp()) / "flat.csv", rows))

    # 15분마다 100 kWh 이므로 부하는 400 kW 다. 정오에만 1 kWp 당 0.5 kW 를 내는
    # 프로파일이면 상한은 400 ÷ 0.5 = 800 kWp.
    index = pd.DatetimeIndex(usage.kw.index)
    unit = pd.Series(0.0, index=index)
    unit[index.hour == 12] = 0.5
    limit = surplus_free_capacity_kwp(usage, unit)
    assert float(usage.kw.iloc[0]) == pytest.approx(400.0)
    assert limit == pytest.approx(800.0)

    # 그 용량까지는 역송이 없고, 넘기면 생긴다.
    assert apply_generation(usage, unit * limit).surplus_kwh == pytest.approx(0.0)
    assert apply_generation(usage, unit * (limit * 1.1)).surplus_kwh > 0.0


def test_발전량을_MWh_로_낸다() -> None:
    """**kWh 는 백만 자리라 읽히지 않는다** (26세션 3-3)."""
    source = (VIEWS / "measures.py").read_text(encoding="utf-8")
    body = source[source.index("def _solar(") : source.index("def _surplus_verdict(")]
    assert 'metric("발전량", fmt.per_year(fmt.mwh(' in body, "발전량 지표가 MWh 가 아닙니다."
    assert "fmt.kwh(point.generation_kwh)" not in body
    view = source[source.index("def _capacity_view(") : source.index("def _azimuth_picker(")]
    assert '"발전량": [fmt.per_year(fmt.mwh(' in view, "용량 표 발전량이 MWh 가 아닙니다."


# ======================================================== 27세션 · 조작과 표시


@functools.lru_cache(maxsize=1)
def _diagnosis() -> Any:
    """샘플의 진단 한 벌. **여러 시험이 나눠 쓴다** — 요금 계산이 무겁다."""
    from kwise.diagnose import ContractInfo, diagnose
    from kwise.quality import check_quality

    usage = load_usage(SAMPLE)
    table_ = load_tariff()
    quality = check_quality(usage)
    return diagnose(
        usage,
        table_,
        ContractInfo(TariffSelection("general_b", "high_a", "I"), contract_kw=5_500.0),
        quality=quality,
    )


def _structure() -> Any:
    """샘플의 요금 구조."""
    structure = _diagnosis().structure
    assert structure is not None
    return structure


def test_개선안을_체크박스로_고른다() -> None:
    """**표적이 제목 전체다** (27세션 1절).

    슬라이드 단추는 스위치 하나만 표적이라 손가락으로 맞히기 어려웠다. 이름을
    체크박스 라벨로 삼으면 제목 줄 전체가 누를 자리가 된다.
    """
    screen = _running(measure_on_tariff_switch=True)
    assert not screen.exception, screen.exception
    keys = {str(item.key) for item in screen.checkbox}
    assert {f"measure_on_{key}" for key in INDEPENDENT_MEASURES} <= keys, keys
    assert not [item for item in screen.toggle if str(item.key or "").startswith("measure_on_")]
    # 라벨이 곧 카드 이름이다.
    label = screen.checkbox(key="measure_on_tariff_switch").label
    assert "선택요금 전환" in label
    assert "검토에 포함" not in " ".join(str(item.label) for item in screen.checkbox)


def test_체크를_켜고_끄면_카드와_조합이_따라온다() -> None:
    """**켜고 끄는 동작이 슬라이드 단추 때와 같아야 한다** (27세션 1절).

    16세션에 잡은 것 — 위젯을 다시 그리지 않으면 세션 값이 버려진다 — 이
    재발하지 않는지 **실제로 눌러** 본다.
    """
    screen = _running(nav_page="2단계 · 개선 수단", measure_on_tariff_switch=True)
    assert any(item.label == "가장 유리한 요금제" for item in screen.metric)
    off = screen.checkbox(key="measure_on_tariff_switch").set_value(False).run(timeout=600)
    assert not off.exception, off.exception
    assert not [item for item in off.metric if item.label == "가장 유리한 요금제"]
    assert "미검토 — 1. 선택요금 전환" in " ".join(str(item.value) for item in off.markdown)
    on = off.checkbox(key="measure_on_tariff_switch").set_value(True).run(timeout=600)
    assert not on.exception, on.exception
    assert any(item.label == "가장 유리한 요금제" for item in on.metric)
    assert on.session_state["measure_on_tariff_switch"] is True


def test_화면에_절_번호가_없다(screen_lines: tuple[object, ...]) -> None:
    """**7.1~7.7 은 요구사항서 절 번호다** (27세션 2절).

    사용자에게는 뜻이 없고 화면 어디에도 7장이 없다. 순번 1~7 로만 적는다.
    코드 주석·문서·산출물의 7.x 는 그대로 둔다 — 거기서는 맞물려 있어야 한다.
    """
    offenders = [
        f"[{item.slot}] {item.where} :: {item.text[:60]}"
        for item in screen_lines
        if re.search(r"7\.[1-7]", str(item.text))
    ]
    assert offenders == [], offenders
    labels = [str(item.text) for item in screen_lines if item.slot == "라벨"]
    assert any(label.startswith("1. 선택요금 전환") for label in labels), labels[:20]


def test_절_번호는_화면에서만_순번이_된다() -> None:
    """정본은 그대로다 — 보고서·Excel 이 같은 목록을 쓴다 (27세션 2절)."""
    from kwise.measures import measure_kind
    from kwise.ui.labels import measure_title

    assert measure_kind("surplus").title == "7.7 잉여 활용"
    assert measure_title("7.7 잉여 활용") == "7. 잉여 활용"
    assert measure_title("7.1 선택요금 전환") == "1. 선택요금 전환"
    # 절 번호가 없는 이름은 건드리지 않는다.
    assert measure_title("확실성 등급") == "확실성 등급"


def test_요금_구조에_합계와_월별_그래프가_있다(app: AppTest) -> None:
    """**둘만 보이고 합계가 없었다** (27세션 3-1·3-2)."""
    labels = _labels(app)
    for name in ("기본요금", "전력량요금", "합계", "기본요금 비중"):
        assert name in labels, labels
    captions = [str(item.value) for item in app.caption]
    assert "월별 요금 구성" in captions, captions  # 34세션 1절에 되돌렸다


def test_월별_요금_구성이_네_조각이다() -> None:
    """**기본요금 + 계시별 전력량요금**, 그리고 그 합이 청구액이다 (27세션 3-2).

    기본요금이 달마다 같은 값으로 이어지는 것은 요금적용전력 12개월 규칙의
    모습이다 — 그 사실을 보이려고 선으로 빼지 않고 막대에 넣는다.
    """
    from kwise.report.frames import MONTHLY_CHARGE_PARTS, monthly_charge_frame

    structure = _structure()
    frame = monthly_charge_frame(structure)
    assert list(dict.fromkeys(frame["구분"])) == list(MONTHLY_CHARGE_PARTS)
    monthly = structure.monthly
    assert len(frame) == len(monthly) * 4
    for month, group in frame.groupby("월"):
        row = monthly.loc[next(item for item in monthly.index if str(item) == month)]
        assert float(group["원"].sum()) == pytest.approx(float(row["total_won"]))
    # 밑단은 기본요금이고, 요금적용전력이 자리를 잡은 뒤로는 같은 값이 이어진다.
    base = [float(value) for value in frame[frame["구분"] == "기본요금"]["원"]]
    assert len(set(round(value) for value in base)) < len(base)


def test_계시별_사용량_구성은_각을_자르지_않는다() -> None:
    """**17세션 축 규약은 막대에 대한 것이다** (33세션 3절 · 34세션 1절).

    원에는 축이 없어 「자르지 않는다」 가 성립하지 않는다 — 대신 **각이 곧
    비중**이라 조각을 쌓는 것으로 같은 뜻을 지킨다. 규약을 고칠 일이 아니라
    해당 없음이다.
    """
    from kwise.ui.charts import band_donut_chart

    spec = band_donut_chart(_structure()).to_dict()
    theta = spec["layer"][0]["encoding"]["theta"]
    assert theta["stack"] is True
    assert "y" not in spec["layer"][0]["encoding"], "원에는 축이 없습니다."


def test_선택요금_전환에_중복_문구가_없다() -> None:
    """툴팁이 이미 말하는 것을 본문이 되풀이하지 않는다 (27세션 4-2·4-3·4-4).

    샘플의 현행은 선택Ⅱ 이고 그것이 이미 최적이라, **갈아탈 것이 있는 상태**를
    만들려고 선택Ⅰ 로 띄운다.
    """
    screen = _running(option="I", measure_on_tariff_switch=True)
    assert not screen.exception, screen.exception
    body = " ".join(
        str(item.value) for group in (screen.markdown, screen.caption) for item in group
    )
    assert "왼쪽(초록)이 절감" not in body
    # 반올림 각주는 3단계 「개선안별 요약」 한 곳뿐이다 (25세션 4-5 · 28세션 1-3).
    assert body.count("항목 합과 차이가 날 수 있습니다") == 1
    # 결측 안내는 1단계에서 한 번만 (27세션 4-3 · 30세션 2절에 문구를 다시 짰다).
    assert body.count("결측이 있는 달") == 1
    assert "결측 보정 기준을 함께 봅니다" not in body
    # 최적 요금제 안내는 앞 문장만 (27세션 4-4).
    assert "가장 유리한 요금제는" in body
    assert "다른 수단의 기준선을" not in body


def test_최적_요금제_안내가_한_문장이다() -> None:
    """뒷문장은 계산 모듈에서 지웠다 — 산출물에도 없다 (27세션 4-4)."""
    path = Path("src") / "kwise" / "measures" / "tariff_switch.py"
    body = "".join(_strings(path))
    assert "다른 수단의 기준선을" not in body, "문구가 남아 있습니다."


def test_하향_여지가_없어도_지표는_낸다() -> None:
    """**경고는 감추고 지표는 되살린다** (27세션 5절 → 31세션 2절).

    샘플의 실제 계약전력 5,500 kW 는 요금적용전력 5,293 kW 대비 여유가 3.8% 라
    하향 여지가 0 kW 다. 27세션에 한 줄로 줄였더니 이 카드만 큰 글자 숫자가 없어
    대시보드 구실을 못 했다 — 「할 일이 없다」 와 「값을 알 수 없다」 는 다르다.

    보이는 넷은 **왜 여지가 없는지 말하는 값들**이다. 하지도 못할 하향을 조심하라는
    경고와 산출 근거는 그대로 감춘다.
    """
    from kwise.report import CONTRACT_CHANGE_WARNING

    screen = _running(contract_kw=5_500.0, nav_page="2단계 · 개선 수단", measure_on_contract=True)
    assert not screen.exception, screen.exception
    body = " ".join(
        str(item.value) for group in (screen.markdown, screen.caption) for item in group
    )
    assert "하향 여지가 없습니다" in body
    assert CONTRACT_CHANGE_WARNING not in body
    assert "현재 부하 기준의 하향 여지" not in body

    labels = [label for label, _ in _stage2_metrics(screen)]
    assert labels == ["현재 계약전력", "요금적용전력", "여유", "하향 여지"], labels
    values = dict(_stage2_metrics(screen))
    assert values["하향 여지"] == "0.0 kW"
    # 여지가 0 일 때는 적정성 지표(권장·이용률)와 절감액을 내지 않는다 — 셋 다
    # 같은 사실을 다른 이름으로 되풀이한다.
    for banned in ("권장", "이용률", "절감액"):
        assert banned not in labels, labels

    # 여유가 있으면 권장·절감액까지 그대로 나온다.
    wide = [label for label, _ in _stage2_metrics(_running(measure_on_contract=True))]
    assert "권장" in wide and "하향 여지" in wide


def test_버린_행_안내가_결측_옆에_나온다(tmp_path: Path) -> None:
    """**「결측 구간」 옆 한 줄** (31세션 0-2).

    값이 조용히 빠지는 것은 결측과 함께 읽어야 「빠진 값이 얼마나 되나」 가 한 번에
    잡힌다. 위쪽 확인사항에 두면 결측률과 떨어진다.
    """
    from kwise.quality import check_quality
    from kwise.ui.views.diagnose import DROPPED_ROWS_FACT, dropped_row_lines
    from tests._synthetic import make_labels, month_dates, write_raw_csv

    rows = [(label, "100.00") for date in month_dates(2024, 3) for label in make_labels(date)]
    rows.append(("읽을 수 없는 날짜", "100.00"))
    rows.append(("2024-03-15 06:30", "-50.00"))
    quality = check_quality(load_usage(write_raw_csv(tmp_path / "bad.csv", rows)))

    lines = dropped_row_lines(quality)
    assert len(lines) == 1
    assert "검침일을 읽지 못한 행 1건" in lines[0]
    assert "음수 전력량 행 1건" in lines[0]
    # **문구를 새로 짓지 않는다** — 품질 검사가 낸 안내를 그대로 낸다.
    source = next(item.text for item in quality.notices if item.fact == DROPPED_ROWS_FACT)
    assert lines[0] == text.markdown_safe(source)

    # 위쪽 확인사항으로 새지 않는다 (`MISSING_FACTS` 가 걸러 낸다).
    from kwise.ui.views.diagnose import MISSING_FACTS

    assert DROPPED_ROWS_FACT in MISSING_FACTS


def test_버린_행이_없으면_줄이_없다(app: AppTest) -> None:
    """샘플에는 그런 행이 없다 — 「0건」 이 화면 한 줄을 차지하면 안 된다."""
    from kwise.quality import check_quality
    from kwise.ui.views.diagnose import dropped_row_lines

    assert dropped_row_lines(check_quality(load_usage(SAMPLE))) == ()
    rendered = _screen_lines(app)
    assert not [item for item in rendered if "읽지 못한 행" in item], rendered


def test_견주다를_화면에_쓰지_않는다() -> None:
    """**'견주다' 를 '비교' 로 바꿨다** (31세션 1-3).

    사용자에게 나가는 글만 본다 — 코드 주석과 독스트링은 우리끼리 쓰는 말이라
    화면 문구 규약의 대상이 아니다.
    """
    import ast

    banned = re.compile(r"견주|견줍|견줘|견줄|견준|견줌")
    offenders: list[str] = []
    for path in sorted((Path("src") / "kwise").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docs = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docs
                and banned.search(node.value)
            ):
                offenders.append(f"{path.name}:{node.lineno} {node.value[:50]}")
    assert offenders == [], offenders

    for name in ("MANUAL.md", "TECHNICAL.md"):
        body = (Path("docs") / name).read_text(encoding="utf-8")
        assert not banned.search(body), name


def test_경제성DR_지표_넷이_한_줄이다() -> None:
    """**다른 개선안은 넷이 한 줄이다** (31세션 3-1). 이 카드만 3+1 로 갈려 있었다."""
    screen = _running(nav_page="2단계 · 개선 수단", measure_on_demand_response=True)
    assert not screen.exception, screen.exception
    labels = [label for label, _ in _stage2_metrics(screen)]
    assert labels[:4] == ["거래 가능일", "저부하 평일", "등록 권장 용량", "연간 감축 가능량"]


def test_경제성DR_지표에_툴팁이_있다() -> None:
    """이름만으로는 무엇을 센 것인지 알 수 없다 (31세션 3-2)."""
    screen = _running(nav_page="2단계 · 개선 수단", measure_on_demand_response=True)
    tips = {item.label: item.help for item in screen.metric}
    assert "보수적인 감축 가능 용량" in str(tips["등록 권장 용량"])
    assert "연간 감축 잠재량" in str(tips["연간 감축 가능량"])
    for label in ("거래 가능일", "저부하 평일"):
        assert tips[label], f"{label} 에 툴팁이 없습니다."


def test_등록_권장_용량에_소수점이_없다() -> None:
    """분위수라 소수 자리에 뜻이 없고, 계약서에 적는 값도 정수다 (31세션 3-3)."""
    screen = _running(nav_page="2단계 · 개선 수단", measure_on_demand_response=True)
    value = next(item.value for item in screen.metric if item.label == "등록 권장 용량")
    assert re.fullmatch(r"[\d,]+ kW", str(value)), value


def test_기상_출처를_이름으로_적는다() -> None:
    """``cache``·``network``·``archive`` 가 그대로 화면에 나가고 있었다 (31세션 4-2)."""
    from kwise.ui.views.measures import WEATHER_SOURCE_LABELS, weather_source_label

    assert weather_source_label("network") == "Open-Meteo"
    assert weather_source_label("cache") == "Open-Meteo"
    assert weather_source_label("archive") == "아카이브(Open-Meteo)"
    # 새 출처가 생기면 옛 이름 대신 눈에 띄는 값으로 떨어진다.
    assert weather_source_label("없는출처") == "기타"
    assert set(WEATHER_SOURCE_LABELS) == {"network", "cache", "archive"}

    source = (VIEWS / "measures.py").read_text(encoding="utf-8")
    assert 'f"기상 출처 — {source}."' not in source
    assert 'help=manual_tip("weather-source")' in source, "앵커는 지역 캡션으로 옮겼다."


def test_잉여_지점_용량을_따로_계산한다() -> None:
    """**곡선 밖의 용량이다** (31세션 4-1).

    곡선은 설치 가능 면적이 허용하는 용량까지만 돈다. 잉여가 처음 생기는 용량이
    그 위에 있으면 곡선 어디에도 없으므로 점을 따로 낸다 — 곡선에 얹으면 최적
    판정이 설치할 수 없는 용량을 고를 수 있다.
    """
    import pandas as pd

    from kwise.measures import surplus_free_capacity_kwp, surplus_share_capacity_kwp

    usage = load_usage(SAMPLE)
    index = pd.DatetimeIndex(usage.kw.index)
    # 낮에만 발전하는 단순 프로파일. 값은 중요하지 않고 **단조성**만 본다.
    unit = pd.Series(
        [(0.001 if 9 <= stamp.hour < 16 else 0.0) for stamp in index], index=index, dtype=float
    )
    onset = surplus_free_capacity_kwp(usage, unit)
    heavy = surplus_share_capacity_kwp(usage, unit, share=0.1)
    assert onset > 0
    assert heavy is not None and heavy > onset, (onset, heavy)
    # 비중이 클수록 필요한 용량도 크다 — 단조 증가여야 이분법이 성립한다.
    heavier = surplus_share_capacity_kwp(usage, unit, share=0.3)
    assert heavier is not None and heavier > heavy


def test_잉여_비중은_0과_1_사이여야_한다() -> None:
    from kwise.measures import surplus_share_capacity_kwp

    usage = load_usage(SAMPLE)
    with pytest.raises(ValueError):
        surplus_share_capacity_kwp(usage, usage.kw * 0.0, share=0.0)


def test_ESS_절감액_툴팁이_구성을_밝힌다() -> None:
    """사용자가 이 숫자가 피크저감인지 충방전 차익인지 몰랐다 (31세션 5-3).

    **32세션에 「왜 그렇게 나뉘는가」 로 다시 썼다** — 합만 적었더니 「충전을
    하는 ESS 에서 전력량요금 절감이 가능한가」 가 되물어졌다.
    """
    screen = _running(nav_page="2단계 · 개선 수단", measure_on_ess=True)
    assert not screen.exception, screen.exception
    tips = [item.help for item in screen.metric if item.label == "절감액"]
    ess_tip = next(str(item) for item in tips if item and "기본요금 절감" in str(item))
    # 샘플은 99.8% 가 기본요금이다 — 비중이 그 사실을 낸다 (33세션 4절에 굵게).
    assert "거의 전부 기본요금 절감입니다** (99.8%)" in ess_tip, ess_tip
    # 전력량요금이 왜 줄어드는지가 적힌다 (옮겨 담기 ↔ 왕복효율 손실).
    assert "싼 시간으로" in ess_tip
    assert "왕복효율 손실" in ess_tip
    assert "차익거래는 들어 있지 않습니다" in ess_tip
    # 금액 둘은 같은 화면의 「계산 근거」 표가 낸다 — 툴팁에 다시 적지 않는다.
    grounds = [str(item.value) for item in screen.dataframe]
    assert any("기본요금 절감" in text and "전력량요금 절감" in text for text in grounds)


def test_ESS_절감액이_기본요금과_전력량요금의_합이다(sample_ess: EssResult) -> None:
    """**툴팁이 적은 그대로여야 한다** (31세션 5-1).

    샘플에서는 99% 넘게 기본요금 절감이다 — 피크를 깎아 요금적용전력을 낮춘 몫이고,
    충방전 차익거래는 여기 들어 있지 않다.
    """
    assert sample_ess.total_saving_won == pytest.approx(
        sample_ess.base_saving_won + sample_ess.energy_saving_won
    )
    assert sample_ess.arbitrage is not None
    assert sample_ess.arbitrage.annual_won > 0
    # 차익거래는 절감액에 더해지지 않는다 — 더하면 회수기간이 달라진다.
    assert sample_ess.payback_with_arbitrage_years is not None
    assert sample_ess.payback_years is not None
    assert sample_ess.payback_with_arbitrage_years < sample_ess.payback_years


def test_차익거래_안내가_무엇에_더하지_않았는지_적는다() -> None:
    """「피크저감 절감액」 은 화면 어디에도 없는 이름이었다 (31세션 5-2)."""
    from kwise.measures.arbitrage import ESS_SAVING_LABEL

    screen = _running(nav_page="2단계 · 개선 수단", measure_on_ess=True)
    body = " ".join(
        str(item.value) for group in (screen.markdown, screen.caption) for item in group
    )
    assert f"{ESS_SAVING_LABEL}에 더하지 않은 값입니다" in body, body
    assert "피크저감 절감액에 더하지 않았습니다" not in body


def test_잉여_활용이_0일_때도_지표를_낸다() -> None:
    """**계약전력 조정과 같은 원칙이다** (31세션 6절).

    태양광을 켜지 않으면 잉여가 0 인데, 27세션까지는 글만 남아 이 카드에 큰 글자
    숫자가 하나도 없었다 — 「할 일이 없다」 와 「값을 알 수 없다」 는 다르다.
    """
    screen = _running(nav_page="2단계 · 개선 수단", measure_on_surplus=True)
    assert not screen.exception, screen.exception
    labels = [label for label, _ in _stage2_metrics(screen)]
    assert "연간 잉여" in labels, labels
    value = next(item.value for item in screen.metric if item.label == "연간 잉여")
    assert str(value).startswith("0.0 MWh"), value
    body = " ".join(str(item.value) for item in screen.markdown)
    assert "태양광을 켜지 않아 잉여가 0 입니다." in body


def test_잉여_시나리오가_둘이다() -> None:
    """**버림은 언제나 0원이라 고를 것이 없다** (27세션 7-3)."""
    from kwise.measures import EXTERNAL_SCENARIO, OFFSET_SCENARIO

    assert (OFFSET_SCENARIO, EXTERNAL_SCENARIO) == ("상계거래(한전)", "외부 판매")
    source = (Path("src") / "kwise" / "measures" / "surplus.py").read_text(encoding="utf-8")
    assert '"버림"' not in source
    assert "외부 신재생에너지 구매 연계" not in source


def test_잉여_단가_이름이_하나다() -> None:
    """입력칸과 표가 같은 이름을 쓴다 (27세션 7-2)."""
    screen = _running(nav_page="2단계 · 개선 수단", measure_on_surplus=True)
    assert not screen.exception, screen.exception
    labels = [str(item.label) for item in screen.number_input]
    assert any("잉여 판매 단가" in label for label in labels), labels
    from kwise.report.notices import UNPRICED_REASONS

    assert "잉여 판매 단가" in UNPRICED_REASONS["external_price"]
    assert "외부 단가" not in UNPRICED_REASONS["external_price"]


def test_토일공휴일_용어가_두_카드에서_같다() -> None:
    """7.5 지표와 잉여 결과가 같은 말을 쓴다 (27세션 7-1 · 26세션 3-2)."""
    from kwise.measures.surplus import SurplusResult

    source = (VIEWS / "measures.py").read_text(encoding="utf-8")
    assert '"토·일·공휴일 잉여"' in source
    assert not hasattr(SurplusResult, "weekend_share"), "옛 이름이 남아 있습니다."
    assert hasattr(SurplusResult, "off_day_share")


# ======================================================== 28세션 · 3단계 표시


def test_개선안별_요약_열이_개선_방안이다(stage3: AppTest) -> None:
    """**「절감량」 이라 적어 두고 절감량이 아닌 것을 담고 있었다** (28세션 1-1)."""
    frame = next(item.value for item in stage3.dataframe if "수단" in list(item.value.columns))
    columns = list(frame.columns)
    assert columns == ["수단", "개선 방안", "연간 절감액", "투자비", "회수기간"], columns
    assert "절감량" not in columns
    # **확실성 열은 뺐다** (28세션 4절).
    assert "확실성" not in columns


def test_개선_방안에_동사가_붙는다() -> None:
    """**무엇을 하는 것인지 드러나야 한다** (28세션 1-2)."""
    from kwise.measures import Certainty, measure_kind
    from kwise.report import StandaloneRow, standalone_frame

    screen = _running(
        option="I",
        measure_on_tariff_switch=True,
        measure_on_contract=True,
        measure_on_demand_response=True,
        measure_on_power_factor=True,
        measure_on_ess=True,
        measure_ess_target=5_170.0,
    )
    assert not screen.exception, screen.exception
    frame = next(item.value for item in screen.dataframe if "수단" in list(item.value.columns))
    plans = {str(row["수단"]): str(row["개선 방안"]) for _, row in frame.iterrows()}
    assert plans["1. 선택요금 전환"].endswith("전환"), plans
    assert plans["2. 계약전력 조정"].endswith("하향"), plans
    assert plans["3. 경제성DR"].endswith("입찰"), plans
    assert plans["4. 역률 개선"].endswith("개선"), plans
    assert "설치" in plans["6. ESS"], plans
    # 태양광·잉여는 기상에서 격리되어 이 실행에 줄이 없다 (25세션). 만드는 함수로
    # 직접 본다 — 값이 아니라 **동사가 붙는지**를 보는 시험이다.
    row = StandaloneRow(
        kind=measure_kind("solar"),
        reduction="80 kWp 설치",
        annual_saving_won=1.0,
        investment_won=0.0,
        payback_years=0.0,
        certainty=Certainty.MEDIUM,
    )
    assert standalone_frame((row,)).iloc[0]["개선 방안"] == "80 kWp 설치"


def test_요약표_금액이_만원_단위다(stage3: AppTest) -> None:
    """**원 단위 아홉 자리가 여섯 줄 늘어서면 자릿수를 세어 읽는다** (28세션 1-3)."""
    frame = next(item.value for item in stage3.dataframe if "수단" in list(item.value.columns))
    values = [
        str(row[column]) for _, row in frame.iterrows() for column in ("연간 절감액", "투자비")
    ]
    shown = [item for item in values if not item.startswith("미산출")]
    assert shown, values
    for item in shown:
        assert item.endswith(("만원", "억원", "원")), item
        # 원 단위 여섯 자리 이상이 그대로 남아 있으면 안 된다.
        assert not re.fullmatch(r"[\d,]{7,}원", item), item


def test_미산출_문구가_한_칸에_들어간다() -> None:
    """**사유는 「무엇이 없어서 못 냈나」 가 전부다** (28세션 1-4).

    왜 지어내지 않는지·무엇을 넣어야 하는지는 그 자리의 입력 라벨·툴팁·차단
    안내·보고서가 이미 말한다. 표 안에서 되풀이하면 표가 문단이 된다.
    """
    from kwise.measures.demand_response import UNPRICED_REASON
    from kwise.report.notices import UNPRICED_REASONS

    reasons = {
        "dr": UNPRICED_REASON,
        "contract": UNPRICED_REASONS["contract"],
        "external_price": UNPRICED_REASONS["external_price"],
        "pv_price": UNPRICED_REASONS["pv_price"],
    }
    for name, reason in reasons.items():
        assert reason.startswith("미산출 — "), name
        assert len(reason) <= 24, f"{name}: {len(reason)}자 — {reason}"
        assert "." not in reason, f"{name}: 문장이 둘 이상입니다 — {reason}"
    assert UNPRICED_REASON == "미산출 — 정산 단가 미입력"


def test_합산효과에_회수기간이_있다(stage3: AppTest) -> None:
    """**조합의 결론은 회수기간이다** (28세션 2절). 투자비 합 ÷ 12개월 환산 절감액."""
    labels = _stage3_metrics(stage3)
    assert labels[:4] == ["단순 합", "합산효과", "차이", "회수기간"], labels
    payback = [item for item in stage3.metric if str(item.label) == "회수기간"][-1]
    assert str(payback.value).endswith(("년", "즉시")) or "미산출" in str(payback.value)
    assert "투자비" in str(payback.delta)


def test_투자가_없는_조합은_즉시다() -> None:
    """투자비가 0 인 수단만 고르면 회수기간은 「즉시」 다 (28세션 2절)."""
    from kwise.measures import payback_years

    assert payback_years(0.0, 1_000.0) == 0.0
    assert text.payback(0.0, investment_won=0.0) == "즉시"


def test_3단계_금액_열이_모두_12개월_환산이다() -> None:
    """**한 칸이라도 기간 값이면 열 이름이 거짓말을 한다** (28세션 3절).

    잉여 상계 수익만 기간 값이었다. 샘플은 정확히 12.00개월이라 환산 계수가
    1.0 이지만, 짧은 기간 자료에서는 값이 달라진다 — 여기서 그것을 잰다.
    """
    import dataclasses

    import pandas as pd

    from kwise.measures import Certainty, SurplusResult, SurplusScenario
    from kwise.measures.surplus import OFFSET_SCENARIO
    from kwise.report import standalone_rows

    surplus = SurplusResult(
        total_kwh=1_200.0,
        generation_kwh=10_000.0,
        share_of_generation=0.12,
        hour_distribution=pd.Series(dtype=float),
        weekday_kwh=1_000.0,
        weekend_kwh=100.0,
        holiday_kwh=100.0,
        scenarios=(SurplusScenario(OFFSET_SCENARIO, 600_000.0, "근거", "행정"),),
    )
    full = standalone_rows(surplus=surplus, base_fee_months=12.0)[0]
    half = standalone_rows(surplus=surplus, base_fee_months=6.0)[0]
    assert full.annual_saving_won == pytest.approx(600_000.0)
    assert half.annual_saving_won == pytest.approx(1_200_000.0), "환산이 걸리지 않았습니다."
    assert "2.4 MWh 상계" in half.reduction
    assert full.certainty is Certainty.MEDIUM_LOW
    # **기준을 모르면 만들지 않는다.** 기본값을 두면 지어낸 가정이 금액이 된다.
    with pytest.raises(ValueError, match="base_fee_months"):
        standalone_rows(surplus=surplus)
    # 다른 수단은 이미 12개월 환산이다 — 기간 값을 쓰는 줄이 남아 있지 않다.
    source = (Path("src") / "kwise" / "report" / "standalone.py").read_text(encoding="utf-8")
    body = source[source.index("def standalone_rows(") : source.index("def simple_sum_won(")]
    assert "period_" not in body, "기간 값을 쓰는 줄이 있습니다."
    assert dataclasses.is_dataclass(surplus)


def test_확실성이_화면에_없다(screen_lines: tuple[object, ...]) -> None:
    """**등급을 화면에서 뺐다** (28세션 4절). Excel·Word 에는 그대로 있다."""
    offenders = [
        f"[{item.slot}] {item.where} :: {item.text[:60]}"
        for item in screen_lines
        if "확실성" in str(item.text)
    ]
    assert offenders == [], offenders
    from kwise.report.excel import measure_summary_frame

    assert "확실성" in list(measure_summary_frame().columns)


def test_조합_차트가_색으로_등급을_나누지_않는다() -> None:
    """색이 무엇을 가리키는지 알 수 없었다 (28세션 4절)."""
    source = (Path("src") / "kwise" / "ui" / "charts.py").read_text(encoding="utf-8")
    body = source[source.index("def combination_chart(") : source.index("def sensitivity_chart(")]
    assert "확실성" not in body.split('"""')[2], body


def test_28세션_중복_셋이_사라졌다() -> None:
    """25세션 도구로 다시 세어 골라낸 셋이다 (28세션 6절)."""
    from kwise.measures import ELIGIBILITY_NOTICE
    from kwise.ui.spec import measure

    compare_source = (VIEWS / "compare.py").read_text(encoding="utf-8")
    # ② 조합 구성 캡션 — 바로 아래 「합산효과에 넣지 않은 수단」 이 이름까지 적는다.
    assert compare_source.count("요금이 아니라 별도 정산") == 1
    # ③ 잉여 카드 개요 — 같은 문장이 아래 자격요건 안내에 있다.
    overview = measure("surplus").overview
    assert "자격요건" not in overview, overview
    assert "자격요건" in ELIGIBILITY_NOTICE
    # ① 감도 목록 두 줄 (「절감액」 과 「12개월 환산 절감액」) — 목록이 사라졌다.
    assert 'st.expander("지표별 감도 범위"' not in compare_source


# ======================================================== 29세션 · 공휴일 보정


def test_저부하_평일_목록에_날짜와_요일이_있다() -> None:
    """**사용자가 알아볼 수 있어야 한다** (29세션). 날짜만으로는 무슨 날인지 모른다."""
    screen = _running(nav_page="2단계 · 개선 수단", measure_on_demand_response=True)
    assert not screen.exception, screen.exception
    frame = next(
        item.value for item in screen.dataframe if "감축 여력(kW)" in list(item.value.columns)
    )
    assert list(frame.columns)[:2] == ["날짜", "요일"]
    assert list(frame["날짜"]) == ["2023-05-01", "2023-10-02"]
    assert list(frame["요일"]) == ["월", "월"]


def test_쉬는_날을_고르면_감축량이_다시_계산된다() -> None:
    """**목록을 보여 주고 끝내지 않는다** (29세션).

    고른 날은 거래 가능일에서 빠지고 DR 프로파일을 처음부터 다시 만든다 —
    기준선·문턱·감축량이 함께 움직인다.
    """
    screen = _running(nav_page="2단계 · 개선 수단", measure_on_demand_response=True)
    before = {str(item.label): str(item.value) for item in screen.metric}
    assert before["저부하 평일"] == "2일"

    picker = screen.multiselect(key="measure_demand_response_off_days")
    assert "쉬는 날" in str(picker.label)
    assert "근로자의 날이나 임시공휴일" in str(picker.help)
    # 값은 ISO 날짜다 — 요일은 라벨로만 붙인다 (한 번 더 눌러야 반영되던 자리).
    after = picker.set_value(["2023-05-01", "2023-10-02"]).run(timeout=600)
    assert not after.exception, after.exception

    metrics = {str(item.label): str(item.value) for item in after.metric}
    assert metrics["저부하 평일"] == "0일"
    assert metrics["연간 감축 가능량"] == "0 kWh"
    assert metrics["거래 가능일"] != before["거래 가능일"]
    # **되돌릴 수 있어야 한다** — 목록이 비어도 고르는 칸은 남는다.
    assert after.multiselect(key="measure_demand_response_off_days").value == [
        "2023-05-01",
        "2023-10-02",
    ]


def test_요금_계산은_쉬는_날에_흔들리지_않는다() -> None:
    """**DR 판정에만 쓴다** (29세션).

    근로자의 날은 2025년까지 법정공휴일이 아니라 한전 요금에서 평일이 맞다.
    사용자가 「쉬었다」 고 알려 준 것을 요금 달력에 넣으면 계산이 틀린다.
    """
    import inspect

    from kwise.diagnose import diagnose
    from kwise.tariff import BillingOptions

    body = inspect.getsource(diagnose)
    assert "dr_off_days" in body
    # 요금 옵션에는 들어가지 않는다 — 공휴일 목록을 건드리지 않는다.
    assert "dr_off_days" not in set(BillingOptions.__dataclass_fields__)
    # 쓰이는 자리는 DR 프로파일 하나뿐이다.
    assert body.count("off_days=dr_off_days") == 1
    start = body.index("off_days=dr_off_days")
    assert "dr_profile(" in body[:start], "요금 계산 쪽으로 새고 있습니다."
    # 달력을 만드는 자리에는 들어가지 않는다 — 넣으면 요금 시간대가 바뀐다.
    calendar_call = body[body.index("build_calendar(") : body.index("dr_profile(")]
    assert "dr_off_days" not in calendar_call, "달력에 섞였습니다."


def test_공휴일_한계를_문서에_남겼다() -> None:
    """**다음 사람이 같은 조사를 다시 하지 않도록** (29세션).

    화면에는 툴팁 한 줄만 두고 배경은 보고서 부록(참고 등급)과 매뉴얼이 받는다.
    """
    from kwise.diagnose.dr import LIBRARY_HOLIDAY_GAPS

    joined = " ".join(LIBRARY_HOLIDAY_GAPS)
    assert "근로자의 날" in joined and "2026" in joined
    manual = Path("docs") / "MANUAL.md"
    body = manual.read_text(encoding="utf-8")
    assert "근로자의 날" in body and "임시공휴일" in body
    # **결과에도 실려 간다** — 참고 등급이라 화면에는 없고 보고서 부록에 남는다.
    from kwise.diagnose.dr import dr_profile
    from kwise.notices import Severity
    from kwise.tariff import build_calendar

    profile = dr_profile(
        load_usage(SAMPLE).kw, 15, build_calendar(range(2023, 2026)), contract_type="general_b"
    )
    gaps = [item for item in profile.notices if item.fact == "dr.holiday_gaps"]
    assert len(gaps) == 1
    assert gaps[0].severity is Severity.INFO
    assert "근로자의 날" in gaps[0].text
