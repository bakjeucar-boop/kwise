"""데스크톱 화면과 Streamlit 화면의 경계. 서로 끌어오지 않는다."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "kwise"


def test_데스크톱은_streamlit_을_끌어오지_않는다() -> None:
    # 새 프로세스에서 본다 — 이 시험 프로세스에는 다른 시험이 streamlit 을 이미 올려 두었을 수 있다.
    code = "import sys, kwise.desktop.app; print('streamlit' in sys.modules)"
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8", check=True
    )
    assert out.stdout.strip() == "False"


def test_streamlit_화면은_데스크톱을_끌어오지_않는다() -> None:
    hits = [
        path.relative_to(SRC).as_posix()
        for path in (SRC / "ui").rglob("*.py")
        if "kwise.desktop" in path.read_text(encoding="utf-8")
    ]
    assert hits == []
