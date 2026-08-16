"""배포에서 겪은 결함을 시험으로 못박는다 (2026-08-15).

23세션까지 시험 1,128건이 통과했는데도 배포지에서 결함 넷이 나왔다. 시험이
약해서가 아니라 **시험이 보지 않던 자리**였다 — 진입점의 재실행, 서버에 없는
글꼴, 판 고정, 패키지 인식. 어느 것도 계산 로직이 아니라서 계산 시험이 잡을
수 없었다.

여기 있는 것은 모두 **소스와 설정을 읽는 시험**이다. 계산을 돌리지 않으므로
빠르고, 같은 종류의 결함이 다시 들어오면 커밋 전에 걸린다.

관련 기록은 ``PROCEED.md`` 의 배포 절이다.
"""

from __future__ import annotations

import ast
import re
import runpy
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src" / "kwise"
ENTRY_POINT = PROJECT_ROOT / "streamlit_app.py"
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
REQUIREMENTS = PROJECT_ROOT / "requirements.txt"


def _pyproject() -> dict[str, object]:
    with PYPROJECT.open("rb") as stream:
        return tomllib.load(stream)


def _source_files(root: Path) -> Iterator[Path]:
    """``__pycache__`` 를 뺀 파이썬 원본."""
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" not in path.parts:
            yield path


def _read(path: Path) -> str:
    """**encoding 을 반드시 명시한다** (CLAUDE.md). 생략하면 cp949 로 읽힌다."""
    return path.read_text(encoding="utf-8")


# ===================================================================== 진입점 재실행
#
# 배포 결함 ①  — 두 번째 실행부터 화면이 비었다.
#
# Streamlit 은 조작할 때마다 진입점 파일을 처음부터 다시 실행한다. 그런데
# 파이썬은 이미 불러온 모듈을 다시 실행하지 않으므로, 그리는 일을 모듈
# 최상위(=import 부작용)에 맡기면 첫 실행만 그려지고 그 뒤로는 빈 화면이 된다.
# 로컬에서 app.py 를 직접 돌리면 app.py 자신이 진입점이라 이 결함이 드러나지
# 않는다. 그래서 **뿌리 진입점으로 두 번 돌려 본다.**


def test_entry_point_draws_on_every_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """``streamlit_app.py`` 를 두 번 실행하면 ``main()`` 도 두 번 불린다.

    그리는 일이 import 부작용으로 옮겨 가면 두 번째가 0 이 되어 여기서 걸린다.
    """
    import kwise.ui.app as app_module

    calls: list[int] = []
    monkeypatch.setattr(app_module, "main", lambda: calls.append(1))

    original_path = list(sys.path)
    try:
        for _ in range(2):
            runpy.run_path(str(ENTRY_POINT), run_name="__main__")
    finally:
        sys.path[:] = original_path

    assert calls == [1, 1], (
        "진입점을 다시 실행했는데 main() 이 다시 불리지 않았습니다. "
        "그리는 일이 import 최상위로 새어 나갔는지 보십시오."
    )


def test_entry_point_import_is_cached_between_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """**왜 main() 을 명시적으로 불러야 하는지**를 시험으로 남긴다.

    두 번 실행해도 ``kwise.ui.app`` 은 같은 모듈 객체다 — 두 번째 실행에서
    모듈 최상위 코드는 돌지 않는다. 이 사실이 위 시험의 근거다.
    """
    import kwise.ui.app as app_module

    monkeypatch.setattr(app_module, "main", lambda: None)
    before = sys.modules["kwise.ui.app"]

    original_path = list(sys.path)
    try:
        runpy.run_path(str(ENTRY_POINT), run_name="__main__")
    finally:
        sys.path[:] = original_path

    assert sys.modules["kwise.ui.app"] is before


def _module_level_streamlit_calls(path: Path) -> list[int]:
    """모듈 최상위에서 ``st.*()`` 를 부르는 줄 번호."""
    tree = ast.parse(_read(path), filename=str(path))
    found: list[int] = []
    for node in tree.body:  # 최상위만 본다. 함수·클래스 안은 실행되지 않는다
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        target = node.value.func
        while isinstance(target, ast.Attribute):
            target = target.value
        if isinstance(target, ast.Name) and target.id in {"st", "streamlit"}:
            found.append(node.lineno)
    return found


def test_ui_modules_draw_nothing_at_import_time() -> None:
    """화면 모듈은 import 만으로 아무것도 그리지 않는다.

    최상위에서 그리면 **두 번째 실행부터 그 부분이 사라진다.** 배포 결함 ①의
    일반형이라 화면 패키지 전체에 건다.
    """
    offenders = {
        path.relative_to(PROJECT_ROOT).as_posix(): lines
        for path in _source_files(SRC_ROOT / "ui")
        if (lines := _module_level_streamlit_calls(path))
    }
    assert not offenders, (
        f"모듈 최상위에서 Streamlit 을 부릅니다: {offenders}. "
        "그리는 일은 함수 안으로 옮기고 진입점이 부르게 하십시오."
    )


# ===================================================================== 글꼴 이름
#
# 배포 결함 ② — 서버에 맑은 고딕이 없어 차트 한글이 깨졌다.
#
# OS 전용 글꼴 이름을 코드에 박으면 그 OS 밖에서 깨진다. 후보를 여러 OS 로
# 늘어놓고 **있는 것을 골라 쓰는 것**이 규약이다 (figures.korean_font).

#: OS 마다 있고 없고가 갈리는 글꼴 이름.
OS_SPECIFIC_FONTS = (
    "Malgun Gothic",
    "맑은 고딕",
    "Apple SD Gothic Neo",
    "AppleGothic",
    "NanumGothic",
    "NanumBarunGothic",
    "Noto Sans CJK KR",
    "Gulim",
    "굴림",
    "Batang",
    "바탕",
    "Dotum",
    "돋움",
)

#: **따옴표 안에 홀로 있을 때만** 글꼴로 친다.
#:
#: 한글 글꼴 이름 몇은 예사말이기도 하다 — "바탕" 은 배경, "돋움" 은 도드라짐이다.
#: 주석에 그 말이 나왔다고 잡으면 한국어로 쓴 코드에서 거짓 경보만 난다
#: (실제로 ``ui\\callout.py``·``ui\\charts.py`` 가 걸렸다). 글꼴로 쓰일 때는
#: 반드시 문자열 리터럴이므로 따옴표로 좁힌다.
_FONT_LITERAL = re.compile("|".join(f"[\"']{re.escape(name)}[\"']" for name in OS_SPECIFIC_FONTS))

#: 글꼴 이름이 **있어도 되는** 자리와 그 이유. 여기 없는 파일에서 나오면 실패한다.
FONT_NAME_ALLOWED = {
    "src/kwise/report/figures.py": (
        "여러 OS 후보를 늘어놓고 korean_font() 가 실제로 있는 것을 고른다. 이것이 규약이다."
    ),
    "src/kwise/docsite.py": (
        "CSS font stack 이다. 브라우저가 있는 것을 고르므로 여러 OS 이름이 함께 있어야 한다."
    ),
    "src/kwise/report/document.py": (
        "Word 글꼴 **이름만** 적는다 — 글자를 그리는 것은 읽는 사람의 Word 이고, "
        "없으면 그쪽이 대체한다. 서버에 깔려 있을 필요가 없다."
    ),
}


def test_os_specific_font_names_stay_in_their_allowed_places() -> None:
    """OS 전용 글꼴 이름이 **새로운 자리**에 들어오는 것을 막는다.

    새 파일에서 나오면 실패한다. 반대로 허용 목록의 파일이 더는 글꼴을 쓰지
    않아도 실패한다 — 목록이 사실과 어긋난 채 남는 것을 막기 위해서다.
    """
    found = {
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in _source_files(SRC_ROOT)
        if _FONT_LITERAL.search(_read(path))
    }
    unexpected = found - set(FONT_NAME_ALLOWED)
    assert not unexpected, (
        f"OS 전용 글꼴 이름이 박혀 있습니다: {sorted(unexpected)}. "
        "matplotlib 이면 kwise.report.figures.korean_font() 를 쓰고, "
        "그럴 수 없는 사정이면 FONT_NAME_ALLOWED 에 이유와 함께 올리십시오."
    )
    stale = set(FONT_NAME_ALLOWED) - found
    assert not stale, (
        f"허용 목록이 낡았습니다 — 이 파일들은 이제 글꼴 이름을 쓰지 않습니다: {sorted(stale)}"
    )


def _rcparams_font_literals(path: Path) -> list[int]:
    """``rcParams["font.family"]`` 에 **문자열을 직접** 넣는 줄 번호."""
    tree = ast.parse(_read(path), filename=str(path))
    found: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Attribute)
                and target.value.attr == "rcParams"
                and isinstance(target.slice, ast.Constant)
                and target.slice.value == "font.family"
            ):
                found.append(node.lineno)
    return found


def test_matplotlib_font_is_resolved_not_hardcoded() -> None:
    """matplotlib 글꼴은 **고른 결과**를 넣는다. 이름을 직접 넣지 않는다."""
    offenders = {
        path.relative_to(PROJECT_ROOT).as_posix(): lines
        for path in _source_files(SRC_ROOT)
        if (lines := _rcparams_font_literals(path))
    }
    assert not offenders, (
        f'rcParams["font.family"] 에 글꼴 이름을 직접 넣었습니다: {offenders}. '
        "korean_font() 가 고른 값을 넣으십시오."
    )


# ===================================================================== 판 고정
#
# 배포 결함 ③ — 배포지가 최신 판을 깔아 로컬과 조합이 달라졌다.


def _requirement_lines() -> list[str]:
    return [
        line.strip()
        for line in _read(REQUIREMENTS).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _normalize(name: str) -> str:
    return name.lower().replace("_", "-")


def _version_tuple(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in text.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def test_requirements_are_pinned_exactly() -> None:
    """배포지가 매번 최신을 깔지 않도록 **== 로** 못박혀 있어야 한다."""
    loose = [line for line in _requirement_lines() if "==" not in line]
    assert not loose, f"== 로 고정되지 않은 줄이 있습니다: {loose}"


def test_requirements_cover_exactly_the_declared_dependencies() -> None:
    """``requirements.txt`` 와 ``pyproject`` 의 의존성 **목록이 같아야** 한다.

    한쪽에만 있으면 배포지에서만 나는 ``ModuleNotFoundError`` 가 된다.
    """
    project = _pyproject()["project"]
    assert isinstance(project, dict)
    declared = {
        _normalize(re.split(r"[<>=!~\[]", item, maxsplit=1)[0].strip())
        for item in project["dependencies"]
    }
    pinned = {_normalize(line.split("==", 1)[0].strip()) for line in _requirement_lines()}
    assert pinned == declared, (
        f"pyproject 에만 있는 것: {sorted(declared - pinned)} / "
        f"requirements.txt 에만 있는 것: {sorted(pinned - declared)}"
    )


def test_pinned_versions_satisfy_the_declared_lower_bounds() -> None:
    """못박은 판이 ``pyproject`` 의 하한을 **실제로 만족**해야 한다.

    하한만 올리고 고정판을 그대로 두면, 배포지에는 요구보다 낮은 판이 깔린다.
    """
    project = _pyproject()["project"]
    assert isinstance(project, dict)
    bounds = {}
    for item in project["dependencies"]:
        if ">=" not in item:
            continue
        name, _, floor = item.partition(">=")
        bounds[_normalize(name.strip())] = floor.strip()

    violations = []
    for line in _requirement_lines():
        name, _, version = line.partition("==")
        key = _normalize(name.strip())
        floor = bounds.get(key)
        if floor is not None and _version_tuple(version) < _version_tuple(floor):
            violations.append(f"{key}=={version} < {floor}")
    assert not violations, f"고정판이 pyproject 하한보다 낮습니다: {violations}"


# ===================================================================== 패키지 인식
#
# 배포 결함 ④ — __init__.py 가 없어 네임스페이스 패키지가 되면서, 엉뚱한
# 자리에서 import 가 실패했다.


def test_every_package_folder_has_an_init() -> None:
    """``src\\kwise`` 아래 모든 폴더에 ``__init__.py`` 가 있어야 한다."""
    missing = [
        folder.relative_to(PROJECT_ROOT).as_posix()
        for folder in sorted(SRC_ROOT.rglob("*"))
        if folder.is_dir()
        and "__pycache__" not in folder.parts
        and not (folder / "__init__.py").is_file()
    ]
    assert not missing, (
        f"__init__.py 가 없습니다: {missing}. 네임스페이스 패키지가 되어 오류가 엉뚱한 데서 납니다."
    )


def test_setuptools_finds_the_package_under_src() -> None:
    """src 레이아웃 설정이 그대로여야 한다 — 흔들리면 배포지에서 못 찾는다."""
    tool = _pyproject()["tool"]
    assert isinstance(tool, dict)
    setuptools = tool["setuptools"]
    assert isinstance(setuptools, dict)
    assert setuptools["package-dir"] == {"": "src"}
    assert setuptools["packages"]["find"]["where"] == ["src"]


# ===================================================================== 문서와 설정
#
# 지난 세션에 mypy 검사 범위가 문서와 설정 사이에서 갈려 있던 것이 드러났다.
# 기록은 "pass" 인데 실제로는 tests\ 를 한 번도 보지 않았다. **문서가 말하는
# 것과 코드가 하는 것이 갈리면 기록이 거짓이 된다.** 갈릴 만한 자리를 건다.

#: 사람이 읽고 그대로 따라 치는 문서.
INSTRUCTION_DOCS = (
    "README.txt",
    "PROCEED.md",
    "CLAUDE.md",
    "docs/ENVIRONMENT.md",
    "docs/MANUAL.md",
    "docs/TECHNICAL.md",
)

#: 화면을 띄우는 **유일한** 경로. 배포지(Streamlit Cloud)와 같아야 한다.
CANONICAL_ENTRY_POINT = "streamlit_app.py"

#: 마크다운 인라인 코드의 닫는 백틱·표 구분자는 경로가 아니다. 빼고 잡는다.
_RUN_COMMAND = re.compile(r"streamlit(?:\.exe)?\s+run\s+([^\s`|]+)")


def _instruction_texts() -> Iterator[tuple[str, str]]:
    for name in INSTRUCTION_DOCS:
        yield name, _read(PROJECT_ROOT / name)


def test_docs_point_at_one_entry_point() -> None:
    """모든 문서가 **뿌리 진입점 하나**를 가리켜야 한다.

    ``src\\kwise\\ui\\app.py`` 를 직접 돌리면 그 파일이 진입점이 되어 매 실행에
    다시 돌아간다. 그러면 "두 번째 실행부터 빈 화면" 결함이 로컬에서 재현되지
    않는다 — 배포지와 다른 것을 확인하는 셈이다.
    """
    wrong: dict[str, list[str]] = {}
    for name, text in _instruction_texts():
        targets = [t for t in _RUN_COMMAND.findall(text) if t != CANONICAL_ENTRY_POINT]
        if targets:
            wrong[name] = targets
    assert not wrong, (
        f"뿌리 진입점이 아닌 실행 경로가 문서에 남아 있습니다: {wrong}. "
        f"모두 `streamlit run {CANONICAL_ENTRY_POINT}` 로 맞추십시오."
    )


def test_the_entry_point_the_docs_name_actually_exists() -> None:
    """문서가 가리키는 파일이 실제로 있어야 한다."""
    assert (PROJECT_ROOT / CANONICAL_ENTRY_POINT).is_file()


def test_dev_tools_are_installed_through_the_dev_extra() -> None:
    """개발 도구는 ``.[dev]`` 로 깐다 — 낱개로 깔면 판 상한이 무의미해진다.

    명령 줄만 본다. "낱개로 깔지 마십시오" 같은 **금지 문장은 잡지 않는다.**
    """
    dev_tools = ("pytest", "ruff", "mypy")
    # 실제 명령 줄만 — 산문 속 언급은 백틱·별표 같은 문장부호로 시작한다.
    # **``python.exe -m pip install`` 꼴을 반드시 포함해야 한다** — 이 프로젝트
    # 문서가 실제로 쓰는 형태다. 처음에 빠뜨려 시험이 헛돌 뻔했다.
    command = re.compile(r"^[\w.\\/:-]*\s*(?:-m\s+)?pip install\b")
    offenders: dict[str, list[str]] = {}
    for name, text in _instruction_texts():
        bad = [
            line
            for raw in text.splitlines()
            if command.match(line := raw.strip()) and "[dev]" not in line
            if any(re.search(rf"\b{tool}\b", line) for tool in dev_tools)
        ]
        if bad:
            offenders[name] = bad
    assert not offenders, (
        f"개발 도구를 낱개로 까는 명령이 남아 있습니다: {offenders}. "
        '`pip install -e ".[dev]"` 로 바꾸십시오.'
    )


def test_documented_python_version_matches_the_project() -> None:
    """문서가 말하는 파이썬 판과 ``requires-python`` 이 같아야 한다."""
    project = _pyproject()["project"]
    assert isinstance(project, dict)
    floor = project["requires-python"].lstrip(">=").strip()
    series = ".".join(floor.split(".")[:2])
    text = _read(PROJECT_ROOT / "docs" / "ENVIRONMENT.md")
    assert series in text, f"ENVIRONMENT.md 가 파이썬 {series} 를 말하지 않습니다."


def test_environment_doc_repeats_the_actual_mypy_scope() -> None:
    """**mypy 검사 범위를 문서가 그대로 옮겨 적어야 한다.**

    지난 세션에 드러난 어긋남을 다시 만들지 않기 위한 것이다. ``pyproject`` 의
    ``files`` 를 고치면 이 시험이 깨지고, 문서를 함께 고치게 된다.

    범위를 언제 어떻게 맞출지는 ``tests\\`` 형 정리와 함께 정한다 (미해결).
    여기서 요구하는 것은 **어긋남을 숨기지 않는 것**뿐이다.
    """
    tool = _pyproject()["tool"]
    assert isinstance(tool, dict)
    files = tool["mypy"]["files"]
    text = _read(PROJECT_ROOT / "docs" / "ENVIRONMENT.md")
    rendered = "[" + ", ".join(f'"{item}"' for item in files) + "]"
    assert rendered in text, (
        f"ENVIRONMENT.md 에 mypy 검사 범위 {rendered} 가 그대로 적혀 있지 않습니다. "
        "설정을 바꿨으면 문서도 함께 고치십시오."
    )


def test_documented_tool_paths_exist() -> None:
    """문서가 시키는 ``tools\\*.py`` 가 실제로 있어야 한다.

    이름을 바꾸거나 지운 뒤 문서를 안 고치면, 시키는 대로 쳤을 때 실패한다.
    """
    referenced: set[str] = set()
    for _, text in _instruction_texts():
        referenced.update(re.findall(r"tools[\\/]([A-Za-z_][\w]*\.py)", text))
    missing = sorted(name for name in referenced if not (PROJECT_ROOT / "tools" / name).is_file())
    assert not missing, f"문서가 가리키는 도구가 없습니다: {missing}"
