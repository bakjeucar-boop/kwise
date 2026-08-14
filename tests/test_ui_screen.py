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
import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from kwise.compare import CombinationSpec
from kwise.io import load_usage
from kwise.measures import AppliedMeasure, measure_kind
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
    body = source[source.index("def _card(") : source.index("def _band_series(")]
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
        "unattended_energy_share",
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


def test_비교_화면에_감도_원자료_표가_없다(compare_app: AppTest) -> None:
    """3단계 표는 개선안별 요약 하나뿐이다. 감도는 범위 한 줄로 적는다."""
    headings = [str(item.value) for item in compare_app.subheader]
    assert "조합 비교" not in headings
    assert "감도" in headings


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
    assert any("따로따로" in str(item) for item in body), body
    assert any("현재 요금제와 현재 사용량" in str(item) for item in body), body
    assert any("3단계 합산효과" in str(item) for item in body), body


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
    """같은 사실을 세 번 말하던 것을 세 줄 한 묶음으로 합쳤다 (13세션·16세션 3절)."""
    from kwise.quality import check_quality

    quality = check_quality(load_usage(SAMPLE))
    lines = missing_lines(quality)
    assert len(lines) == 3
    assert "보간하지 않고" in lines[0]
    assert lines[1].startswith("최장 연속")
    assert "신뢰 제한" in lines[2]

    # 위쪽 경고 목록에는 결측 문구가 남아 있지 않다.
    shown = [item.value for item in app.warning]
    assert not [item for item in shown if "결측" in item], shown


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


def test_결측_안내가_화면에_세_줄뿐이다(app: AppTest) -> None:
    """**같은 사실이 다섯 번 나왔다** (18세션 2절).

    세 줄(:func:`missing_lines`)에 더해 「데이터 품질」 확인사항 두 건이 최장 연속
    결측과 월별 결측률을 되풀이했다. 13세션이 위쪽 경고에서 확인사항으로 내리고,
    16세션이 같은 사실을 세 줄로 정리한 두 조치가 겹친 자리다.

    ``partition(…, MISSING_MARKERS)`` 은 제대로 걸러 왔다 — 걸러 낸 쪽을 품질
    블록이 **다시 그린 것**이 원인이었다. 패턴을 늘려 막는 방식이 아니므로,
    문구가 바뀌어도 이 시험은 계속 유효하다.
    """
    assert not app.exception, app.exception
    from kwise.quality import check_quality

    quality = check_quality(load_usage(SAMPLE))
    lines = missing_lines(quality)
    assert len(lines) == 3

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
    # 잉여 0 을 지표로 보인다. 빈 카드가 아니다.
    assert any(item.label == "잉여 전력량" for item in screen.metric)


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


def test_ESS_곡선과_표가_나온다(ess_screen: AppTest) -> None:
    """U곡선 + 대표 지점 표 (14세션 3-2)."""
    assert not ess_screen.exception, ess_screen.exception
    assert len(ess_screen.get("vega_lite_chart")) >= 1, "회수기간 곡선이 없습니다."
    body = " ".join(str(item.value) for item in ess_screen.caption)
    assert "가장 유리한 목표는" in body
    assert "물리적 최적이 아니라 조달 규격의 산물입니다" in body
    assert "왼쪽은 최소 규모" in body and "오른쪽은 용량이 급증" in body


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
    assert "조달 사례 4건 기준" in body and "적합" in body


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


def test_체크를_풀면_합산효과가_다시_계산된다() -> None:
    """뺀 만큼 줄어야 한다 — 화면이 옛 값을 들고 있으면 안 된다.

    **한 벌을 따로 띄운다.** 묶음 fixture 를 건드리면 뒤따르는 시험이 뺀 조합을
    보게 된다.
    """
    screen = _running(option="I", **STAGE3_MEASURES)  # type: ignore[arg-type]
    assert not screen.exception, screen.exception
    before = {str(item.label): str(item.value) for item in screen.metric}
    dropped = screen.checkbox(key="combo_pick_ess").set_value(False).run(timeout=600)
    assert not dropped.exception, dropped.exception
    after = {str(item.label): str(item.value) for item in dropped.metric}
    assert after["합산효과"] != before["합산효과"]
    body = " ".join(str(item.value) for item in dropped.caption)
    assert "조합에서 뺀 개선안" in body, body


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
    card_payback = {
        "7.4 역률 개선": _after(metrics, "현재 역률", 3),
        "7.6 ESS": _after(metrics, "출력 / 용량", 3),
    }

    frame = next(item.value for item in screen.dataframe if "수단" in list(item.value.columns))
    rows = {str(row["수단"]): row for _, row in frame.iterrows()}

    for title in ("7.1 선택요금 전환", "7.2 계약전력 조정", "7.4 역률 개선", "7.6 ESS"):
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


def test_절사_각주가_화면에_있다(stage3: AppTest) -> None:
    """항목 합과 합계 표시가 어긋날 수 있다는 사실을 적는다 (14세션 1절)."""
    assert not stage3.exception, stage3.exception
    body = " ".join(str(item.value) for item in stage3.caption)
    assert text.TRUNCATION_FOOTNOTE in body


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


def test_결측_안내가_세_줄을_넘지_않는다() -> None:
    """편중된 달마다 한 줄씩 붙어 열두 줄이 되던 자리다 (16세션 3절)."""
    import dataclasses

    from kwise.quality import check_quality
    from kwise.ui.views.diagnose import MISSING_LINE_LIMIT

    quality = check_quality(load_usage(SAMPLE))
    # 열두 달이 모두 편중된 자료. 달마다 한 줄씩 붙던 자리다.
    flagged = tuple(dataclasses.replace(month, flagged=True) for month in quality.monthly[:12])
    many = dataclasses.replace(quality, monthly=flagged)
    assert len(many.flagged_months) == 12
    lines = missing_lines(many)
    assert len(lines) <= MISSING_LINE_LIMIT == 3
    assert "결측률이 높은 달" in lines[-1]


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
