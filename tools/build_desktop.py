"""데스크톱 화면을 exe 폴더로 묶는다 → ``output\\desktop\\kwise\\kwise.exe``.

    .venv\\Scripts\\python.exe tools\\build_desktop.py            # 배포판 (콘솔 없음)
    .venv\\Scripts\\python.exe tools\\build_desktop.py --console  # 시작하다 터질 때 오류를 보는 판

exe 는 최종 산출물이라 ``output\\`` 에, PyInstaller 중간물은 ``PROJECT_CACHE`` 에 둔다 (규약).
배포는 ``output\\desktop\\kwise\\`` 폴더를 통째로 zip 하거나 설치본으로 감싼다.

**폴더 배포(onedir)만 된다.** 공용 코드가 기준 데이터를 ``Path(__file__).parents[3] / "data"``
로 찾는데, 묶인 모듈은 ``kwise\\_internal\\kwise\\...`` 에 서므로 그 자리가 ``kwise\\data`` 다
— 그래서 ``data\\`` 를 exe 옆에 복사한다 (공용 코드는 그대로 둔다).
파일 하나(onefile)는 임시 폴더에 풀려 그 경로가 빗나간다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "desktop"
DIST = OUT / "kwise"
WORK = Path(os.environ.get("PROJECT_CACHE", ROOT / "cache")) / "pyinstaller"
STATIC = ROOT / "src" / "kwise" / "desktop" / "static"
#: 앱이 읽지 않는 것 — 규정 원문(``source``)과 편집 직전 스냅샷(``backup``).
SKIP = shutil.ignore_patterns("source", "backup", "*.damaged", "fetch_failures.json")


def main() -> int:
    import nicegui

    windowed = [] if "--console" in sys.argv else ["--windowed"]
    # ``nicegui-pack`` 이 넘기는 인자와 같다. 그것을 쓰지 않는 까닭: PATH 의 ``pyinstaller`` 를
    # 불러 이 ``.venv`` 밖에서 묶이고 ``kwise`` 를 못 찾았다 (missing module named kwise).
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onedir",
        *windowed,
        "--noconfirm",
        "--name",
        "kwise",
        "--distpath",
        str(OUT),
        "--workpath",
        str(WORK),
        "--specpath",
        str(WORK),
        "--paths",
        str(ROOT / "src"),
        "--add-data",
        f"{Path(nicegui.__file__).parent};nicegui",
        "--add-data",
        f"{STATIC};kwise/desktop/static",
        str(ROOT / "kwise_desktop.py"),
    ]
    print(" ".join(command), flush=True)
    done = subprocess.run(command, cwd=ROOT, check=False)
    if done.returncode != 0:
        return done.returncode
    shutil.copytree(ROOT / "data", DIST / "data", ignore=SKIP, dirs_exist_ok=True)
    print(f"완료: {DIST / 'kwise.exe'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
