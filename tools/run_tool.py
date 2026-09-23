r"""도구·명령의 출력을 **파일로 받는다** — 셸 파이프와 리다이렉트를 안 쓰려고 (S207 4절).

**여덟 판 연속 샌 자리다** (S199~S206). 샌 꼴은 늘 같았다 — 도구를 돌려 놓고
출력이 길면 ``| head``·``| Select-String``·``2>&1``·``> $null`` 을 덧붙여 잘랐다.
문자열을 셸에 한 겹 더 태우는 것이 병이고, 파이프와 리다이렉트가 같은 병이다
(``CLAUDE.md`` 「금지」 절). 판마다 같은 스크래치를 새로 지었으므로 여기로 내린다.

    .venv\\Scripts\\python.exe tools\\run_tool.py                    무엇을 돌릴 수 있나
    .venv\\Scripts\\python.exe tools\\run_tool.py screen_audit       도구 하나
    .venv\\Scripts\\python.exe tools\\run_tool.py screen_audit --list
    .venv\\Scripts\\python.exe tools\\run_tool.py pytest tests -m records
    .venv\\Scripts\\python.exe tools\\run_tool.py --tail 40 screen_audit

**``--tail`` 은 이름 앞에 둔다.** 이름 뒤의 인자는 한 자도 빼지 않고 그 도구에
그대로 넘어간다 — 그래야 도구의 인자와 이 도구의 인자가 안 부딪친다.

받는 것 셋.

    ① ``tools\\`` 의 도구      제 프로세스 안에서 ``main()`` 을 부른다
    ② ``pytest``               같은 프로세스에서 부른다 (일꾼 수는 인자가 정한다)
    ③ 그 밖의 명령             ``subprocess`` 로 돌리고 stdout·stderr 를 함께 받는다
                               ``.py`` 파일은 이 python 으로 돌린다 (S227)

출력은 ``PROJECT_CACHE\\runs\\<이름>_<타임스탬프>.txt`` 다 — **실행마다 이름이
달라 낡은 결과가 새 결과로 보이지 않는다**(``CLAUDE.md`` 9항 5번). 화면에는
파일 자리와 꼬리 몇 줄만 낸다. 꼬리를 늘리려면 ``--tail`` 이다.

**도는 동안은 ``runs\\도는중\\<이름>_<시작 타임스탬프>_<pid>.txt`` 가 자란다** (S228) —
뒤로 돌린 판의 진행을 그 파일로 본다. 끝나면 지우고 위 자리에 전과 같은 파일을
쓴다. 죽이거나 터진 판은 그 파일이 남는다. ``runs\\`` 를 훑는 도구는 아래 폴더를 안 본다.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

__all__ = ["OUT_DIR", "run", "tool_names"]


def _cache_root() -> Path:
    """캐시 뿌리. ``PROJECT_CACHE`` 가 있으면 그것, 없으면 ``.\\cache`` (규약)."""
    raw = os.environ.get("PROJECT_CACHE")
    return Path(raw) if raw else PROJECT_ROOT / "cache"


#: 받은 출력을 두는 자리. 최종 산출물이 아니라 중간 산출물이다.
OUT_DIR = _cache_root() / "runs"

#: 도는 동안 자라는 파일의 자리 (S228).
LIVE_DIR = OUT_DIR / "도는중"


class _Live(io.StringIO):
    """받는 글을 모으면서 **도는 동안 파일에도 바로 쓴다** (S228)."""

    def __init__(self, live: io.TextIOBase) -> None:
        super().__init__()
        self._live = live

    def write(self, text: str) -> int:
        self._live.write(text)
        self._live.flush()
        return super().write(text)


def tool_names() -> tuple[str, ...]:
    """``tools\\`` 에서 부를 수 있는 도구 이름. **자기 자신은 뺀다.**"""
    here = Path(__file__).stem
    return tuple(
        sorted(
            path.stem
            for path in (PROJECT_ROOT / "tools").glob("*.py")
            if path.stem != here and not path.stem.startswith("_")
        )
    )


def _run_module(name: str, argv: list[str], live: io.TextIOBase) -> tuple[str, int]:
    """``tools\\`` 의 도구를 제 프로세스에서 돌리고 (출력, 종료 코드) 를 낸다."""
    module = importlib.import_module(name)
    main = getattr(module, "main", None)
    if main is None:
        raise SystemExit(f"{name} 에 main() 이 없습니다")
    buffer = _Live(live)
    kept, sys.argv = sys.argv, [name, *argv]
    code = 0
    try:
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = main() or 0
    except SystemExit as exc:  # argparse 와 ``raise SystemExit(main())`` 둘 다
        code = exc.code if isinstance(exc.code, int) else 0
    finally:
        sys.argv = kept
    return buffer.getvalue(), code


def _run_pytest(argv: list[str], live: io.TextIOBase) -> tuple[str, int]:
    buffer = _Live(live)
    import pytest

    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        code = int(pytest.main(argv))
    return buffer.getvalue(), code


def _at_root(name: str) -> str:
    """상대 경로 실행 파일을 **저장소 뿌리에 댄다** (S210 3절).

    아래 ``cwd=PROJECT_ROOT`` 는 **자식이 시작할 자리**만 정하고, Windows 는
    실행 파일을 **부르는 쪽의 cwd** 에서 찾는다. 그래서 저장소 밖에서 부르면
    ``.venv\\Scripts\\ruff.exe`` 같은 상대 경로가 ``[WinError 2]`` 로 죽었다 —
    저장소 뿌리에서 부르면 지나가므로 **자리에 따라 있다 없다 했다.**
    """
    path = Path(name)
    if path.is_absolute():
        return name
    at_root = PROJECT_ROOT / path
    return str(at_root) if at_root.exists() else name


def _run_command(argv: list[str], live: io.TextIOBase) -> tuple[str, int]:
    """그 밖의 명령. **stderr 를 함께 받는다** — 셸에 ``2>&1`` 을 안 붙이려고.

    **``.py`` 는 이 python 으로 돌린다** (S227) — Windows 는 ``.py`` 를 실행 파일로
    안 받아 ``[WinError 193]`` 로 죽었고 받은 파일도 없었다(S212 · S219 · S220 · S224').

    받은 파일은 전처럼 **stdout 다음에 stderr** 다 (S228) — 도는 동안의 파일에만 둘이
    오는 차례대로 섞인다.
    """
    head = [sys.executable] if argv[0].lower().endswith(".py") else []
    with subprocess.Popen(
        [*head, _at_root(argv[0]), *argv[1:]],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    ) as proc:
        out, err = _Live(live), _Live(live)
        stderr = proc.stderr
        assert proc.stdout is not None and stderr is not None
        reader = threading.Thread(target=lambda: [err.write(line) for line in stderr])
        reader.start()
        for line in proc.stdout:
            out.write(line)
        reader.join()
        code = proc.wait()
    return out.getvalue() + err.getvalue(), code


def run(name: str, argv: list[str] | None = None) -> tuple[Path, int, float]:
    """``name`` 을 돌려 출력을 파일에 담고 (파일, 종료 코드, 소요초) 를 낸다."""
    argv = list(argv or ())
    started = time.time()
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    live_path = LIVE_DIR / f"{Path(name).stem}_{datetime.now():%Y%m%d_%H%M%S}_{os.getpid()}.txt"
    with live_path.open("w", encoding="utf-8") as live:
        if name in tool_names():
            text, code = _run_module(name, argv, live)
        elif name == "pytest":
            text, code = _run_pytest(argv, live)
        else:
            text, code = _run_command([name, *argv], live)
    live_path.unlink()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = OUT_DIR / f"{Path(name).stem}_{stamp}.txt"
    # **같은 초에 두 판이 끝나도 덮지 않는다** (규약 9항 5번). 빠른 명령 둘을
    # 잇달아 돌리면 초가 같아 뒤 판이 앞 판을 지웠다.
    바퀴 = 1
    while path.exists():
        바퀴 += 1
        path = OUT_DIR / f"{Path(name).stem}_{stamp}-{바퀴}.txt"
    path.write_text(text, encoding="utf-8")
    return path, code, time.time() - started


def main() -> int:
    parser = argparse.ArgumentParser(description="도구 출력을 파일로 받는다")
    parser.add_argument("name", nargs="?", help="tools\\ 의 도구 이름 · pytest · 그 밖의 명령")
    parser.add_argument("rest", nargs=argparse.REMAINDER, help="그 도구에 그대로 넘길 인자")
    parser.add_argument("--tail", type=int, default=12, help="화면에 낼 꼬리 줄 수")
    args = parser.parse_args()

    # **인자 없이 돌면 그 판의 값이 다 나온다** — 무엇을 돌릴 수 있는지와 받는 자리.
    if args.name is None:
        print(f"받는 자리 — {OUT_DIR}")
        names = tool_names()
        print(f"\ntools\\ 의 도구 {len(names)}개")
        for name in names:
            print(f"    {name}")
        print("\n그 밖 — pytest · 아무 명령 (stdout 과 stderr 를 함께 받는다)")
        return 0

    path, code, elapsed = run(args.name, args.rest)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    print(f"{args.name} 종료 {code} · {elapsed:,.1f}초 · {len(text):,}자 · {len(lines):,}줄")
    print(path)
    if args.tail > 0 and lines:
        print(f"\n--- 꼬리 {min(args.tail, len(lines))}줄")
        for line in lines[-args.tail :]:
            print(f"    {line}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
