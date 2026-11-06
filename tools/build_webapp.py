"""웹서버 없는 데스크톱 화면을 exe 폴더로 묶는다 → ``output\\webapp\\kwise\\kwise.exe``.

    .venv\\Scripts\\python.exe tools\\build_webapp.py            # 배포판 (콘솔 없음) + zip
    .venv\\Scripts\\python.exe tools\\build_webapp.py --console  # 시작하다 터질 때 오류를 보는 판
    .venv\\Scripts\\python.exe tools\\build_webapp.py --no-zip   # zip 없이 폴더만

폴더 배포(onedir)만 된다 — ``data\\`` 를 exe 옆에 복사한다(공용 코드가 기준 데이터를
``parents[3] / "data"`` 로 찾는다 · onefile 은 임시 폴더라 빗나간다). 중간물은 ``PROJECT_CACHE`` 에,
exe 는 ``output\\`` 에 둔다.

**나눠 줄 것은 zip 하나다** (2026-11-06 사람 결정) — ``output\\webapp\\kwise_<날짜>_<시각>.zip``.
풀면 ``kwise\\`` 폴더 하나가 나오고 그 안에 ``kwise.exe`` · ``_internal\\``(프로그램 · 화면 파일) ·
``data\\``(기준 데이터 · 사전 취득 기상)가 다 있다 — 폴더째 두고 ``kwise.exe`` 를 누른다.
이름에 시각을 붙여 앞 zip 을 덮어쓰지 않는다(열려 있으면 덮어쓰기가 실패한다).
"""

from __future__ import annotations

import datetime as dt
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
        str(ROOT / "kwise_webapp.py"),
    ]
    print(" ".join(command), flush=True)
    done = subprocess.run(command, cwd=ROOT, check=False)
    if done.returncode != 0:
        return done.returncode
    shutil.copytree(ROOT / "data", DIST / "data", ignore=SKIP, dirs_exist_ok=True)
    # **회사 문서 DRM 이 오피스 파일을 잠근다** (2026-11-06) — PPT · Word 를 지을 때 라이브러리가
    # 읽는 빈 서식을 오피스 확장자가 아닌 이름으로 바꾼다. 앱은 ``kwise.webapp.shell`` 이 그
    # 이름(``PPTX_TEMPLATE`` · ``DOCX_TEMPLATE``)을 읽게 한다.
    for package in ("pptx", "docx"):
        template = DIST / "_internal" / package / "templates" / f"default.{package}"
        if template.exists():
            template.replace(template.with_name(f"default_{package}.bin"))
            print(f"서식 이름 바꿈: {package}\\templates\\default_{package}.bin", flush=True)
    print(f"완료: {DIST / 'kwise.exe'}", flush=True)
    if "--no-zip" not in sys.argv:
        # 압축 안 맨 위가 ``kwise\\`` 폴더가 되게 — 풀자마자 exe 와 하부 폴더가 한자리에 선다.
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M")
        archive = shutil.make_archive(
            str(OUT / f"kwise_{stamp}"), "zip", root_dir=OUT, base_dir=DIST.name
        )
        size = Path(archive).stat().st_size / 1024 / 1024
        print(f"zip: {archive} ({size:,.0f} MB)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
