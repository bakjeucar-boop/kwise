"""웹서버 없는 데스크톱 화면을 exe 폴더로 묶는다 → ``output\\webapp\\kwise\\kwise.exe``.

    .venv\\Scripts\\python.exe tools\\build_webapp.py            # 배포판 (콘솔 없음)
    .venv\\Scripts\\python.exe tools\\build_webapp.py --console  # 시작하다 터질 때 오류를 보는 판

``tools\\build_desktop.py`` (NiceGUI 판)와 같은 규약이다 — 폴더 배포(onedir)만 되고 ``data\\`` 를
exe 옆에 복사한다(공용 코드가 기준 데이터를 ``parents[3] / "data"`` 로 찾는다). 중간물은
``PROJECT_CACHE`` 에, exe 는 ``output\\`` 에 둔다. **nicegui 를 묶지 않는다** — 이 판은 쓰지 않는다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "webapp"
DIST = OUT / "kwise"
WORK = Path(os.environ.get("PROJECT_CACHE", ROOT / "cache")) / "pyinstaller_webapp"
STATIC = ROOT / "src" / "kwise" / "webapp" / "static"
#: 앱이 읽지 않는 것 — 규정 원문(``source``)과 편집 직전 스냅샷(``backup``).
SKIP = shutil.ignore_patterns("source", "backup", "*.damaged", "fetch_failures.json")


def main() -> int:
    windowed = [] if "--console" in sys.argv else ["--windowed"]
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
        f"{STATIC};kwise/webapp/static",
        # NiceGUI 판이 같은 환경에 깔려 있어도 이 판에는 넣지 않는다.
        "--exclude-module",
        "nicegui",
        str(ROOT / "kwise_webapp.py"),
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
