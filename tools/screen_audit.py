r"""화면 문구 실주행 감사 — **화면에 나가는 모든 문자열을 모은다** (25세션 3절).

소스 훑기로는 「어느 자리에 무엇이 나오는가」 를 알 수 없고, 결과 객체만 세는
``tools\notice_audit.py`` 로는 **화면이 직접 쓰는 글**(캡션·툴팁·표·차트)이 잡히지
않는다. 여기서는 앱을 실제로 띄워 **그려진 문자열을 전부** 모은다.

    본문   ``st.write`` · ``st.markdown`` · ``st.caption`` · 머리글
    지표   ``st.metric`` 의 값 (delta 는 ``증감`` 으로 따로 센다)
    라벨   위젯 이름과 선택지, **접힘·탭의 라벨**
    툴팁   ``help=`` — 물음표 안의 글. **여기가 사각지대였다**
    표     ``st.dataframe`` 의 열 이름·셀·열 도움말
    그림   차트 제목과 축 이름 (vega 스펙에서 뽑는다)

접힘(``st.expander``)·탭 안까지 내려가고, 자리(``where``)에 그 경로를 적는다.
화면 밖으로 나가는 글(보고서 본문·부록, Excel 비고)은 :func:`source_lines` 가
소스에서 함께 모은다 — 사용자가 읽는 글은 같은 잣대로 본다.

    .venv\\Scripts\\python.exe tools\\screen_audit.py             규칙 위반과 중복 후보
    .venv\\Scripts\\python.exe tools\\screen_audit.py --list      모은 문구 전부
    .venv\\Scripts\\python.exe tools\\screen_audit.py --pairs 40  중복 후보를 40쌍까지

내는 것 넷.

    ① 맨 물결표    escape 되지 않은 ``~``. 둘이 한 줄에 있으면 취소선이 된다
    ② 개발자 언어  코드 식별자 · 요구사항서 참조 · 규정 이름 없는 조문
    ③ 중복 후보    같은 사실을 두 번 말하는 짝. **사람이 읽고 판정한다**
    ④ 자리별 건수

시험(``tests\\test_ui_screen.py``)이 ①②를 같은 함수로 강제한다 — 도구와 시험이
다른 잣대를 쓰면 도구가 통과시킨 것이 시험에서 깨진다.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from streamlit.testing.v1 import AppTest  # noqa: E402
from streamlit.testing.v1.element_tree import Block, Element  # noqa: E402

from kwise.pv import list_provinces, list_sigungu  # noqa: E402
from kwise.ui.building import BuildingInfo  # noqa: E402
from kwise.ui.pipeline import ContractForm, SolarInputs  # noqa: E402

__all__ = [
    "BARE_ARTICLE",
    "BARE_SCHEDULE",
    "BASELINE_CASE",
    "CASES",
    "CODE_WORDS",
    "REQUIREMENT_REF",
    "TILDE",
    "Line",
    "collect",
    "duplicate_pairs",
    "offenders",
    "run",
    "source_lines",
]

SRC = PROJECT_ROOT / "src" / "kwise"
APP = PROJECT_ROOT / "src" / "kwise" / "ui" / "app.py"
SAMPLE = PROJECT_ROOT / "input" / "사용량조회_20240429.csv"
MEASURE_KEYS = (
    "tariff_switch",
    "contract",
    "demand_response",
    "power_factor",
    "solar",
    "ess",
    "surplus",
)
HANGUL = re.compile(r"[가-힣]")
_TAG = re.compile(r"<[^>]+>")


# ===================================================================== 모으기


@dataclass(frozen=True)
class Line:
    """화면에 그려진 문자열 하나."""

    where: str
    """자리 — 탭·접힘 라벨을 ``›`` 로 이은 경로."""
    kind: str
    """요소 종류 (``Caption`` · ``Metric`` …)."""
    slot: str
    """본문 · 지표 · 라벨 · 툴팁 · 표 · 그림."""
    text: str

    @property
    def korean(self) -> bool:
        return bool(HANGUL.search(self.text))


def _clean(value: object) -> str:
    return _TAG.sub("", str(value)).strip()


def _chart_labels(spec: str) -> Iterator[str]:
    """차트 제목과 축 이름. **화면에 글자로 나오는 것만** 본다.

    ``labelExpr`` 은 예외다 (33세션 1절). 눈금 이름을 만드는 **vega 표현식**이라
    글자 그대로 화면에 나오지 않는다 — 세면 날짜 축 하나당 문구가 하나씩 늘고,
    코드가 문구로 잡혀 규칙 검사까지 흐려진다. 그것이 내는 글자(「5월」)는
    자료에서 나오므로 축 이름과 같은 갈래가 아니다.
    """
    try:
        parsed = json.loads(spec)
    except (TypeError, ValueError):
        return
    stack: list[object] = [parsed]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"title", "text"} and isinstance(value, str):
                    yield value
                elif key in {"title", "text"} and isinstance(value, list):
                    yield from (item for item in value if isinstance(item, str))
                else:
                    stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)


def _column_help(columns: str) -> Iterator[str]:
    """``column_config`` 의 열 도움말. 표 머리의 물음표다."""
    try:
        parsed = json.loads(columns)
    except (TypeError, ValueError):
        return
    if not isinstance(parsed, dict):
        return
    for config in parsed.values():
        if isinstance(config, dict):
            for key in ("help", "label"):
                value = config.get(key)
                if isinstance(value, str):
                    yield value


def _tooltip_parts(help_text: str) -> Iterator[str]:
    """툴팁을 **항목 단위로 쪼갠다.**

    「산출 근거」 툴팁은 근거 여럿을 한 덩이로 묶은 글이라, 통째로 견주면 머리글이
    같다는 이유로 서로 닮아 보이고 정작 **본문 경고와 같은 말을 하는 근거 한 줄**은
    묻힌다. 중복은 항목 대 항목으로 봐야 드러난다.
    """
    if not help_text:
        return
    for chunk in help_text.split("\n"):
        text = chunk.strip()
        if not text:
            continue
        yield text[1:].strip() if text.startswith("- ") else text


def _element_lines(node: Element, where: str) -> Iterator[Line]:
    kind = type(node).__name__
    proto = getattr(node, "proto", None)

    def field(name: str) -> str:
        return _clean(getattr(proto, name, "")) if proto is not None else ""

    if kind == "Metric":
        # **delta 는 마크다운을 해석하지 않는다.** escape 하면 역슬래시가 그대로
        # 보이므로 물결표 규칙에서 뺀다 (:data:`MARKDOWN_SLOTS`).
        for name, slot in (("label", "라벨"), ("body", "지표"), ("delta", "증감")):
            if field(name):
                yield Line(where, kind, slot, field(name))
    elif field("body"):
        yield Line(where, kind, "본문", field("body"))
    elif field("label"):
        yield Line(where, kind, "라벨", field("label"))

    for text in _tooltip_parts(field("help")):
        yield Line(where, kind, "툴팁", text)

    # 선택지 — 드롭다운·라디오는 **표시 문자열**이 프로토에 실려 온다.
    for option in getattr(proto, "options", ()) or ():
        if isinstance(option, str) and option.strip():
            yield Line(where, kind, "라벨", _clean(option))

    if kind == "Dataframe":
        yield from _table_lines(node, where)
        for text in _column_help(getattr(proto, "columns", "") or ""):
            yield Line(where, kind, "툴팁", _clean(text))

    spec = getattr(proto, "spec", "")
    if isinstance(spec, str) and spec:
        for text in _chart_labels(spec):
            if HANGUL.search(text):
                yield Line(where, "Chart", "그림", _clean(text))


def _table_lines(node: Element, where: str) -> Iterator[Line]:
    """표의 열 이름과 문자열 셀. **금액·판정은 대개 표 안에 있다.**"""
    try:
        frame = node.value
    except Exception:  # pragma: no cover - 표를 읽지 못해도 감사는 이어 간다
        return
    for name in getattr(frame, "columns", ()):
        if isinstance(name, str) and name.strip():
            yield Line(where, "Dataframe", "표", name.strip())
    for column in getattr(frame, "columns", ()):
        # dtype 을 보지 않고 **값의 형으로** 가른다. ``dtype == object`` 는
        # numpy dtype 비교라 ``is`` 로는 늘 어긋난다.
        for value in frame[column].tolist():
            if isinstance(value, str) and value.strip():
                yield Line(where, "Dataframe", "표", value.strip())


def _label_of(node: Block) -> str:
    label = getattr(node, "label", "")
    return _clean(label) if isinstance(label, str) else ""


def _walk(node: object, where: str) -> Iterator[Line]:
    if isinstance(node, Element):
        yield from _element_lines(node, where)
        return
    label = _label_of(node) if isinstance(node, Block) else ""
    path = f"{where} › {label}" if label and where else (label or where)
    if label:
        # **접힘·탭의 라벨도 화면 글이다.** 자리 이름으로만 쓰고 지나가면 「상세
        # (접어 둠)」 같은 군더더기가 규칙 검사에서 빠진다 (25세션 4-4).
        yield Line(where, type(node).__name__, "라벨", label)
    children = getattr(node, "children", None)
    kids = list(children.values()) if isinstance(children, dict) else list(children or [])
    for child in kids:
        yield from _walk(child, path)


#: 감사가 도는 조건. **한 벌이 아니다** (90세션).
#:
#: 89세션까지 감사는 `을` 하나로만 돌았다. 그런데 화면은 **기본요금 기준**으로
#: 크게 갈린다 — 계약전력 기준(제68조 ②) 종별에는 요금적용전력 갈래에 없는
#: 문구가 뜨고, 그 갈래의 문구는 **아무도 세지 않았다.** 잠정 경고가 화면에
#: 두 번 뜨는 것을 감사가 못 잡은 까닭이 그것이다.
#:
#: 고른 잣대는 「같은 화면이 다른 글을 쓰게 하는 축」 이다 — **셋이 축이고,
#: 조건 하나가 그 셋의 한 조합이다.**
#:
#:     기본요금 기준   계약전력(제68조 ②)이냐 요금적용전력(제68조 ①)이냐.
#:                     **종별이 아니라 전압이 정한다** (89세션)
#:     시간대별 요금제  경·중간·최대 단가가 갈리느냐 단일이냐 (``time_of_use``)
#:     하한            요금표에 하한 비율이 있느냐 (``contract_floor_ratio``)
#:
#: 여덟 종별의 전압을 다 펼치면 이 셋의 조합은 **넷**뿐이고 아래 조건 넷이
#: 하나씩 맡는다. 조합이 늘면
#: ``tests\test_ui_screen.py::test_감사_조건이_화면을_가르는_축을_전부_덮는다``
#: 가 빨개진다 — 사람이 다시 세지 않아도 된다.
#:
#: **91세션에 여섯 갈래를 더 돌려 봤다** (3절 2번). 안 덮는 종별(갑Ⅱ·산업용
#: 셋·교육용(을)·교육용(갑) 저압)의 문구를 통째로 떠서 맞대니, 숫자와
#: **종별·전압·선택요금 이름만 지우면 새 문구 틀이 하나**(「자원 유형 —
#: 표준DR」, 산업용(을) 고압C 6,000 kW 에서 중소형DR 이 빠진다)뿐이었다.
#: 그것도 종별이 아니라 감축 가능량이 가르는 갈래다. **그래서 종별을 더
#: 늘리지 않는다** — 조건 하나가 감사에 10초, 시험에 시험 여섯을 더한다.
CASES: Mapping[str, ContractForm] = {
    # 요금적용전력 기준 · 시간대별 · 하한 있음. 89세션까지의 유일한 조건이다.
    "을": ContractForm(
        contract_type="general_b", voltage="high_a", option="II", contract_kw=6_000.0
    ),
    # 계약전력 기준(제68조 ②) · 단일 단가 · 하한 없음. **잠정 경고가 여기서 뜬다.**
    "갑Ⅰ": ContractForm(
        contract_type="general_a_1", voltage="low", option="single", contract_kw=200.0
    ),
    # 요금적용전력 기준인데 단일 단가다. 89·90세션이 고친 자리다.
    "교육갑": ContractForm(
        contract_type="education_a", voltage="high_a", option="I", contract_kw=300.0
    ),
    # 계약전력 기준인데 **하한 값이 있는** 유일한 자리다 (90세션 2절이 만들었다).
    # 값이 있어도 안 걸어야 하고, 걸면 없는 하한으로 계약전력 조정 목표를 세운다.
    # 그래서 이 조건의 문구는 `갑Ⅰ` 과 같은 틀이어야 맞다 — 그 같음이 곧 확인이다.
    "교육갑저압": ContractForm(
        contract_type="education_a", voltage="low", option="single", contract_kw=200.0
    ),
}

#: 89세션까지의 유일한 조건. **수를 견줄 때의 기준선**이라 이름을 남긴다.
BASELINE_CASE = "을"


def run(*, solar: bool = True, steps: int = 4, case: str = BASELINE_CASE) -> AppTest:
    """수단 일곱을 모두 켠 화면 한 벌.

    ``solar`` 를 켜면 태양광 입력까지 세션에 넣어 **결과가 나온 상태**로 띄운다 —
    기상 사전 취득분(``data\\weather\\``)이나 네트워크가 필요하다. 시험은 기상에서
    격리되므로 끄고 부른다.

    ``case`` 는 :data:`CASES` 의 이름이다. 기본값은 89세션까지 유일했던 조건이라
    **옛 수와 그대로 견줄 수 있다.**
    """
    app = AppTest.from_file(str(APP), default_timeout=900)
    app.session_state["upload_bytes"] = SAMPLE.read_bytes()
    app.session_state["upload_name"] = SAMPLE.name
    app.session_state["contract_form"] = CASES[case]
    for key in MEASURE_KEYS:
        app.session_state[f"measure_on_{key}"] = True
    # **3단계는 「합산효과 계산」 을 누른 뒤에 그린다** (33세션 5절). 누르지 않으면
    # 조합 문구가 통째로 빠져 감사가 「줄었다」 고 잘못 세운다.
    app.session_state["combination_pick"] = tuple(MEASURE_KEYS)
    if solar:
        province = list_provinces()[0]
        region_key = list_sigungu(province)[0].key
        app.session_state["building_info"] = BuildingInfo(region_key=region_key)
        app.session_state["solar_inputs"] = SolarInputs(
            region_key=region_key,
            area_m2=20_000.0,
            unit_cost_won_per_kwp=1_200_000.0,
            steps=steps,
        )
    return app.run()


def collect(app: AppTest | None = None, **options: object) -> tuple[Line, ...]:
    """화면 문구 한 벌. **접힘·탭 안까지 내려간다.**"""
    running = app if app is not None else run(**options)  # type: ignore[arg-type]
    lines = list(_walk(running.main, "본문")) + list(_walk(running.sidebar, "옆단"))
    return tuple(item for item in lines if item.text)


# ===================================================================== 소스 훑기
#
# **화면만으로는 닿지 않는 자리가 있다.** 보고서 본문·부록과 Excel 시트의 비고는
# 화면에 나오지 않지만 사용자가 읽는 글이다 (25세션 4-2·4-3). 그쪽까지 한 잣대로
# 보려고 소스의 문자열을 함께 훑는다.
#
# 빼는 것 둘.
#
#     문서 문자열   설명용이라 화면에 나가지 않는다 (홑 문장식 상수 전부)
#     예외 메시지   ``raise`` 안의 글은 개발자에게 가는 말이다


#: 사람에게 읽히는 글이 아닌 파일. **사용자 문구가 아니라 기계가 읽는 글이다.**
_NOT_PROSE = ("docsite.py", "archive.py")

#: 주소는 그대로가 맞다 — 코드 식별자로 세지 않는다.
_URL = re.compile(r"https?://\S+")


def _visible(path: Path) -> Iterator[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            skip.add(id(node.value))
        if isinstance(node, ast.Raise):
            skip.update(id(inner) for inner in ast.walk(node))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in skip
            and HANGUL.search(node.value)
        ):
            yield node.lineno, node.value


def source_lines(root: Path = SRC) -> tuple[Line, ...]:
    """소스에 있는 **사용자에게 가는 한글 문자열.** 화면·보고서·Excel 전부다."""
    return tuple(
        Line(
            f"{path.relative_to(PROJECT_ROOT).as_posix()}:{lineno}",
            "Source",
            "소스",
            _URL.sub("", text),
        )
        for path in sorted(root.rglob("*.py"))
        if path.name not in _NOT_PROSE
        for lineno, text in _visible(path)
    )


# ===================================================================== 규칙

#: escape 되지 않은 물결표. 한 줄에 둘이면 그 사이가 취소선이 된다 (13세션).
TILDE = re.compile(r"(?<!\\)~")

#: 코드 식별자. **파일 이름은 뺀다** — ``tools\\fetch_weather.py`` 는 사용자가
#: 실제로 실행하는 것이라 이름 그대로가 맞다.
CODE_WORDS = re.compile(
    r"(?<![\w.\\/])(?:"
    r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+"  # snake_case
    r"|[A-Za-z_][A-Za-z0-9_]*=(?!=)"  # 인자명=
    r")(?![\w]*\.[a-z]{2,4}\b)"
)

#: 내부 문서 번호. 사용자에게 뜻이 없다 (25세션 4-2).
REQUIREMENT_REF = re.compile(r"요구사항서")

#: 규정 이름 없는 조문 (25세션 4-3).
BARE_ARTICLE = re.compile(r"제\s*\d+[\d·~]*\s*조")
BARE_SCHEDULE = re.compile(r"별표\s*\d+")
#: 조문 앞에 있어야 할 규정 이름.
ARTICLE_OWNERS = ("기본공급약관", "전력시장운영규칙")
#: 별표 앞에 있어야 할 규정 이름. **둘이다** (97세션 6절) — 앞서는 하나였고,
#: 그래서 「기본공급약관시행세칙 별표4」 를 규정 이름이 없는 것으로 읽었다.
#: 규칙의 뜻은 그대로다: 별표 번호만 적지 않는다.
SCHEDULE_OWNERS = ("전력시장운영규칙", "기본공급약관시행세칙")


def _bare_article(text: str) -> bool:
    """조문 참조에 규정 이름이 붙어 있는가. **한 문자열 안에 한 번이면 된다.**"""
    if not BARE_ARTICLE.search(text):
        return False
    return not any(owner in text for owner in ARTICLE_OWNERS)


def _bare_schedule(text: str) -> bool:
    if not BARE_SCHEDULE.search(text):
        return False
    return not any(owner in text for owner in SCHEDULE_OWNERS)


#: **마크다운을 해석하는 자리.** 물결표 규칙은 여기에만 건다 — 표 셀·차트 라벨·
#: 지표 delta 는 글자 그대로 그려지므로 escape 하면 역슬래시가 보인다.
MARKDOWN_SLOTS = frozenset({"본문", "툴팁", "라벨", "지표"})

#: 규칙 이름 → 판정. **한글이 든 문구만 본다** — 열쇠 비교는 화면 문구가 아니다.
RULES: dict[str, object] = {
    "맨 물결표": lambda text: bool(TILDE.search(text)),
    "코드 식별자": lambda text: bool(CODE_WORDS.search(text)),
    "요구사항서 참조": lambda text: bool(REQUIREMENT_REF.search(text)),
    "규정 이름 없는 조문": _bare_article,
    "규정 이름 없는 별표": _bare_schedule,
}


def offenders(lines: tuple[Line, ...]) -> dict[str, list[Line]]:
    """규칙별 위반 목록. **전부 비어 있어야 한다.**"""
    found: dict[str, list[Line]] = {}
    for name, judge in RULES.items():
        scope = [
            item
            for item in lines
            if item.korean and (name != "맨 물결표" or item.slot in MARKDOWN_SLOTS)
        ]
        hits = [item for item in scope if judge(item.text)]  # type: ignore[operator]
        if hits:
            found[name] = hits
    return found


# ===================================================================== 중복 후보

_NOISE = re.compile(r"[\d,.\s*`_—·()\[\]{}<>~%/–]+")


def _normalized(text: str) -> str:
    """숫자와 서식을 지운 알맹이. **금액이 달라도 같은 말은 같게 본다.**"""
    return _NOISE.sub("", text)


def _bigrams(text: str) -> set[str]:
    body = _normalized(text)
    return {body[index : index + 2] for index in range(len(body) - 1)}


def _dice(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return 2 * len(left & right) / (len(left) + len(right))


def duplicate_pairs(
    lines: tuple[Line, ...], *, threshold: float = 0.45, minimum: int = 16
) -> list[tuple[float, Line, Line]]:
    """같은 사실을 말하는 짝 후보. **판정은 사람이 한다.**

    글자 이음(bigram) 겹침으로 잰다. 한국어는 어미가 바뀌어도 어간이 남으므로
    낱말 나누기 없이도 「같은 말을 다르게 적은 것」 이 위로 올라온다.
    """
    unique: dict[str, Line] = {}
    for item in lines:
        if item.korean and len(_normalized(item.text)) >= minimum:
            unique.setdefault(item.text, item)
    entries = [(line, _bigrams(text)) for text, line in unique.items()]
    pairs = [
        (score, left, right)
        for index, (left, left_grams) in enumerate(entries)
        for right, right_grams in entries[index + 1 :]
        if (score := _dice(left_grams, right_grams)) >= threshold
    ]
    return sorted(pairs, key=lambda item: -item[0])


def repeated(lines: tuple[Line, ...], *, minimum: int = 16) -> list[tuple[int, str]]:
    """**똑같은 문장이 두 자리에 있는 것.** 유사도를 볼 것도 없다."""
    counts = Counter(
        item.text for item in lines if item.korean and len(_normalized(item.text)) >= minimum
    )
    return [(count, text) for text, count in counts.items() if count > 1]


# ===================================================================== 실행


def _print_offenders(found: dict[str, list[Line]]) -> None:
    if not found:
        print("    없음")
    for name, hits in found.items():
        print(f"    {name} {len(hits)}건")
        for item in hits:
            print(f"        [{item.slot}] {item.where}")
            print(f"              {item.text[:120]}")


def _audit_case(case: str, args: argparse.Namespace) -> tuple[int, dict[str, list[Line]]] | None:
    """조건 하나를 돌고 **화면 쪽 결과만** 낸다. 화면이 죽으면 None 이다.

    소스 훑기는 조건과 무관하므로 :func:`main` 이 한 번만 돌린다.
    """
    screen = run(solar=not args.no_solar, case=case)
    if screen.exception:
        print(f"[{case}] 화면이 죽었습니다:")
        for failure in screen.exception:
            print(f"    {failure.value}")
        return None
    lines = collect(screen)
    korean = [item for item in lines if item.korean]
    print(f"\n━━━ {case}  {_case_label(case)}")
    print(f"모은 문구 {len(lines)}건 (한글 {len(korean)}건)")

    print("\n자리별 · 갈래별 건수")
    by_slot = Counter(item.slot for item in korean)
    print("    " + " · ".join(f"{slot} {count}" for slot, count in by_slot.most_common()))
    by_where = Counter(item.where for item in korean)
    for where, count in by_where.most_common():
        print(f"    {count:4d}  {where}")

    if args.list:
        print("\n모은 문구")
        for item in lines:
            print(f"    [{item.slot}] {item.where}")
            print(f"          {item.text}")

    print("\n① · ② 규칙 위반 — 화면")
    on_screen = offenders(lines)
    _print_offenders(on_screen)

    same = repeated(lines)
    if same:
        print(f"\n③-1 똑같은 문장 {len(same)}건")
        for count, text in sorted(same, key=lambda item: -item[0]):
            print(f"    {count}회  {text[:110]}")

    pairs = duplicate_pairs(lines, threshold=args.threshold)
    print(f"\n③-2 중복 후보 {len(pairs)}쌍 (문턱 {args.threshold})")
    for score, left, right in pairs[: args.pairs]:
        print(f"    {score:.2f}")
        print(f"        [{left.slot}] {left.where}")
        print(f"              {left.text[:120]}")
        print(f"        [{right.slot}] {right.where}")
        print(f"              {right.text[:120]}")

    return len(lines), on_screen


def _case_label(case: str) -> str:
    form = CASES[case]
    return f"{form.contract_type} · {form.voltage} · {form.option} · {form.contract_kw:,.0f} kW"


def main() -> int:
    parser = argparse.ArgumentParser(description="화면 문구 실주행 감사")
    parser.add_argument("--list", action="store_true", help="모은 문구를 전부 낸다")
    parser.add_argument("--pairs", type=int, default=25, help="중복 후보를 몇 쌍까지 낼지")
    parser.add_argument("--threshold", type=float, default=0.45, help="중복 판정 문턱")
    parser.add_argument("--no-solar", action="store_true", help="태양광 입력을 넣지 않는다")
    parser.add_argument(
        "--case",
        action="append",
        choices=sorted(CASES),
        help=f"돌 조건. 여러 번 줄 수 있다. 없으면 {len(CASES)} 조건을 모두 돈다",
    )
    args = parser.parse_args()

    # **기본값이 전부다** (90세션). 하나만 돌면 안 도는 갈래의 문구를 아무도
    # 세지 않는다 — 그것을 고치려고 조건을 늘렸는데 기본값이 하나면 도로 같다.
    cases = args.case or list(CASES)
    counts: dict[str, int] = {}
    failed = False
    for case in cases:
        result = _audit_case(case, args)
        if result is None:
            failed = True
            continue
        total, on_screen = result
        counts[case] = total
        failed = failed or bool(on_screen)

    print("\n━━━ 소스 (보고서·Excel 포함) — 조건과 무관하다")
    print("\n① · ② 규칙 위반")
    in_source = offenders(source_lines())
    _print_offenders(in_source)

    if len(counts) > 1:
        print("\n━━━ 조건별 문구 수")
        for case, total in counts.items():
            print(f"    {total:5d}  {case}  {_case_label(case)}")

    return 1 if (failed or in_source) else 0


if __name__ == "__main__":
    raise SystemExit(main())
