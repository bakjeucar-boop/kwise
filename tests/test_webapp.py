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
