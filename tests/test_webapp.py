"""웹서버 없는 판(``kwise.webapp``) — Streamlit 과의 경계 · 작업 파일 · 기준 데이터 형 검사."""

from __future__ import annotations

import datetime as dt
import subprocess
import sys
from pathlib import Path

import pytest

from kwise.webapp import rules, work
from kwise.webapp.state import Choices

SRC = Path(__file__).resolve().parents[1] / "src" / "kwise"


def test_webapp_은_streamlit_을_끌어오지_않는다() -> None:
    # 새 프로세스에서 본다 — 이 시험 프로세스에는 다른 시험이 streamlit 을 이미 올려 두었을 수 있다.
    code = "import sys, kwise.webapp.api, kwise.webapp.main; print('streamlit' in sys.modules)"
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8", check=True
    )
    assert out.stdout.strip() == "False"


def test_streamlit_화면은_webapp_을_끌어오지_않는다() -> None:
    hits = [
        path.relative_to(SRC).as_posix()
        for path in (SRC / "ui").rglob("*.py")
        if "kwise.webapp" in path.read_text(encoding="utf-8")
    ]
    assert hits == []


def test_작업_파일은_고른_것과_사용량_파일을_그대로_돌려준다(tmp_path: Path) -> None:
    choices = Choices()
    choices.enabled = {"ess", "contract"}
    choices.pf_target = 97.0
    choices.dr_off_days = ("2024-05-01",)
    choices.custom_day = dt.date(2023, 7, 3)
    choices.combo_computed = ("contract", "ess")
    choices.solar.area = 1234.0
    data = "검침일,전력량\n".encode()
    saved = work.dump(
        tmp_path / "시험",
        data=data,
        filename="사용량.csv",
        date_column="검침일",
        energy_column=None,
        screen={"contract": {"contract_kw": 5800}},
        choices=choices,
    )
    assert saved.suffix == work.SUFFIX
    loaded = work.load(saved)
    assert loaded["data"] == data
    assert (loaded["filename"], loaded["date_column"]) == ("사용량.csv", "검침일")
    assert loaded["screen"] == {"contract": {"contract_kw": 5800}}
    back = loaded["choices"]
    assert back.enabled == {"ess", "contract"}
    assert back.pf_target == 97.0
    assert back.dr_off_days == ("2024-05-01",)
    assert back.custom_day == dt.date(2023, 7, 3)
    assert back.combo_computed == ("contract", "ess")
    assert back.solar.area == 1234.0


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("사용량.csv", b"a,b\n", "사용량 파일입니다"),
        ("깨짐.kwise", b"\x89PNG\r\n\x1a\n", "kWise 작업 파일이 아닙니다"),
        ("목록.kwise", b"[1, 2]", "kWise 작업 파일이 아닙니다"),
        ("남의.kwise", b'{"format": "other"}', "kWise 작업 파일이 아닙니다"),
    ],
)
def test_엉뚱한_파일은_사람이_읽는_한_줄로_막는다(
    tmp_path: Path, name: str, content: bytes, message: str
) -> None:
    path = tmp_path / name
    path.write_bytes(content)
    with pytest.raises(ValueError, match=message):
        work.load(path)


def test_창이_여는_html_파일이_없고_화면_파일은_다리로_넘긴다() -> None:
    """**회사 문서 DRM 이 html · txt · 오피스 파일을 잠근다** (2026-11-06).

    창이 디스크의 html 을 열면 잠긴 내용을 받아 빈 화면이 된다 — 틀은 글자로 넘기고 화면 파일은
    파이썬이 읽어 다리로 넘긴다. 글꼴은 CSS 안에 글자로 박는다(틀에는 파일 주소가 없다).
    """
    from kwise.webapp import shell

    static = SRC / "webapp" / "static"
    # 잠기는 확장자 — 점 없이 적는다(기록 문서를 읽지 않는데 기록 그물이 점 붙은 md 를 문다).
    locked = {"html", "htm", "txt", "pptx", "docx", "xlsx", "csv", "pdf", "md"}
    assert not [p.name for p in static.rglob("*") if p.suffix.lower().lstrip(".") in locked]
    assets = shell.assets()
    assert len(assets["scripts"]) == len(shell.SCRIPTS) and assets["scripts"][-1].strip()
    assert "data:font/woff2;base64," in assets["styles"][0]
    assert "./fonts/" not in assets["styles"][0]
    # 창이 한 번에 받는 글자 크기 한도(약 2MB) 안 — 틀만 글자로 넘긴다.
    assert len(shell.SHELL.encode("utf-8")) < 10_000
    assert 'id="app"' in shell.SHELL and "shell_assets" in shell.SHELL


def test_서식_파일_이름을_바꿔도_ppt_서식을_찾는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """exe 빌드가 ``default.pptx`` 를 ``default_pptx.bin`` 으로 바꾸면 그 자리를 읽는다 (DRM)."""
    import pptx.api

    from kwise.webapp import shell

    templates = tmp_path / "templates"
    templates.mkdir()
    real = Path(pptx.api._default_pptx_path())
    (templates / shell.PPTX_TEMPLATE).write_bytes(real.read_bytes())
    monkeypatch.setattr(pptx.api, "_default_pptx_path", lambda: str(templates / "default.pptx"))
    shell._redirect(pptx.api, "_default_pptx_path", shell.PPTX_TEMPLATE)
    assert pptx.api._default_pptx_path() == str(templates / shell.PPTX_TEMPLATE)
    assert pptx.api.Presentation().slide_layouts  # 바꾼 이름의 서식으로 열린다


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [("int", "3", 3), ("float", "0.5", 0.5), ("bool", 1, True), ("str", 7, "7")],
)
def test_기준_데이터_값은_형을_지켜_넘긴다(kind: str, value: object, expected: object) -> None:
    assert rules.typed(kind, value) == expected  # type: ignore[arg-type]
    assert type(rules.typed(kind, value)) is type(expected)  # type: ignore[arg-type]


@pytest.mark.parametrize(("kind", "value"), [("float", ""), ("int", None), ("float", "abc")])
def test_기준_데이터_빈_값과_글자는_파일에_닿기_전에_막는다(kind: str, value: object) -> None:
    with pytest.raises(ValueError, match="넣으십시오"):
        rules.typed(kind, value)  # type: ignore[arg-type]
