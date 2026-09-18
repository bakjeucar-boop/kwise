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

출력은 ``PROJECT_CACHE\\runs\\<이름>_<타임스탬프>.txt`` 다 — **실행마다 이름이
달라 낡은 결과가 새 결과로 보이지 않는다**(``CLAUDE.md`` 9항 5번). 화면에는
파일 자리와 꼬리 몇 줄만 낸다. 꼬리를 늘리려면 ``--tail`` 이다.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import os
import subprocess
import sys
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


def _run_module(name: str, argv: list[str]) -> tuple[str, int]:
    """``tools\\`` 의 도구를 제 프로세스에서 돌리고 (출력, 종료 코드) 를 낸다."""
    module = importlib.import_module(name)
    main = getattr(module, "main", None)
    if main is None:
        raise SystemExit(f"{name} 에 main() 이 없습니다")
    buffer = io.StringIO()
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


def _run_pytest(argv: list[str]) -> tuple[str, int]:
    buffer = io.StringIO()
    import pytest

    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        code = int(pytest.main(argv))
    return buffer.getvalue(), code


def _run_command(argv: list[str]) -> tuple[str, int]:
    """그 밖의 명령. **stderr 를 함께 받는다** — 셸에 ``2>&1`` 을 안 붙이려고."""
    done = subprocess.run(
        argv,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return (done.stdout or "") + (done.stderr or ""), done.returncode


def run(name: str, argv: list[str] | None = None) -> tuple[Path, int, float]:
    """``name`` 을 돌려 출력을 파일에 담고 (파일, 종료 코드, 소요초) 를 낸다."""
    argv = list(argv or ())
    started = time.time()
    if name in tool_names():
        text, code = _run_module(name, argv)
    elif name == "pytest":
        text, code = _run_pytest(argv)
    else:
        text, code = _run_command([name, *argv])
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
