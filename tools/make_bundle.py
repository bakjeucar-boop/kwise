r"""로컬 실행 묶음 만들기 (59세션).

    .venv\Scripts\python.exe tools\make_bundle.py

**돌리는 데 필요한 것만 담는다.** 저장소는 927 MB 인데 그 대부분이 가상환경·
캐시·git 이력·참조 PDF 다 — 받는 사람에게는 쓸모가 없고, 큰 파일이 섞이면
「무엇이 있어야 도는가」 가 흐려진다.

담는 것과 담지 않는 것의 기준은 **화면이 실행 중에 여는가** 하나다.

    담는다      진입점 · 패키지 · 기준 데이터 · 기상 사전 취득분 · 시험 자료 · 매뉴얼
    담지 않는다  `.venv` · `.git` · 캐시 · `output` · `reference` · `tests` ·
                `tools` · `data\source`(약관 원문, 코드가 읽지 않는다)

**기상 사전 취득분은 캐시이지만 담는다.** 없어도 프로그램은 돌지만 태양광
계산마다 Open-Meteo 를 타야 하고, 프록시 뒤에서는 거기서 멈춘다 — 그 자리가
`CLAUDE.md` 가 「네트워크가 끼면 반드시」 라고 적어 둔 자리다.

산출물은 ``output\kwise_local_<날짜>_<시각>.zip`` 이다. 날짜·시각 접미사를
붙이는 규약은 다른 산출물과 같다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import zipfile
from collections.abc import Iterator
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: 묶음 안의 최상위 폴더 이름. 압축을 풀면 이 폴더 하나가 생긴다.
BUNDLE_ROOT = "kwise"

#: 그대로 담는 파일 (뿌리 기준 상대경로).
FILES: tuple[str, ...] = (
    "streamlit_app.py",
    "requirements.txt",
    "pyproject.toml",
    "README.txt",
    ".streamlit/config.toml",
    # **화면은 매뉴얼을 열지 않는다** (16세션 4절). 그래도 담는다 — 받는 사람이
    # 읽을 유일한 설명서다.
    "docs/MANUAL.html",
    "docs/TECHNICAL.html",
)

#: 통째로 담는 폴더. 아래 :data:`SKIP_DIRS`·:data:`SKIP_SUFFIXES` 는 뺀다.
TREES: tuple[str, ...] = (
    "src/kwise",
    "data",
    # **사용자가 넣어 달라고 한 자리다.** 시험 자료가 없으면 무엇을 올려야 하는지
    # 알 수 없다 — 열 이름·라벨 규약이 여기 있다.
    "input",
)

#: 이름이 이것이면 그 아래를 통째로 건너뛴다.
SKIP_DIRS: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".ipynb_checkpoints",
        # 약관·규칙 원문 13 MB. **코드가 읽지 않는다** — 요금표는 이미
        # `data\tariff_*.json` 으로 옮겨져 있다.
        "source",
        # 기준 데이터를 고칠 때 생기는 것들이다. 받는 쪽에서 새로 쌓인다.
        "backup",
    }
)

SKIP_SUFFIXES: frozenset[str] = frozenset({".pyc", ".pyo"})


def _walk(root: Path) -> Iterator[Path]:
    """담을 파일만 훑는다. **건너뛸 폴더는 들어가지도 않는다.**"""
    for item in sorted(root.iterdir()):
        if item.is_dir():
            if item.name in SKIP_DIRS:
                continue
            yield from _walk(item)
        elif item.suffix.lower() not in SKIP_SUFFIXES:
            yield item


def bundle_members(root: Path = PROJECT_ROOT) -> list[tuple[Path, str]]:
    """``(실제 경로, 묶음 안 경로)`` 목록. **없는 것은 조용히 넘기지 않는다.**"""
    members: list[tuple[Path, str]] = []
    for name in FILES:
        source = root / name
        if not source.is_file():
            raise FileNotFoundError(f"묶음에 넣을 파일이 없습니다: {source}")
        members.append((source, f"{BUNDLE_ROOT}/{name}"))
    for name in TREES:
        base = root / name
        if not base.is_dir():
            raise FileNotFoundError(f"묶음에 넣을 폴더가 없습니다: {base}")
        for item in _walk(base):
            relative = item.relative_to(root).as_posix()
            members.append((item, f"{BUNDLE_ROOT}/{relative}"))
    return members


RUN_GUIDE = """kWise — 로컬 실행 (Windows)
================================

이 묶음은 **돌리는 데 필요한 것만** 담았습니다. 파이썬 3.12 가 있으면 됩니다.

1. 압축을 풉니다. `kwise` 폴더 하나가 생깁니다.

2. 그 폴더에서 명령 프롬프트를 열고 가상환경을 만듭니다.

       py -3.12 -m venv .venv
       .venv\\Scripts\\python.exe -m pip install --upgrade pip
       .venv\\Scripts\\python.exe -m pip install -r requirements.txt

   판을 못박아 두었습니다 — 다른 조합이 깔리면 여기서만 나는 문제가 생깁니다.

3. 화면을 띄웁니다.

       .venv\\Scripts\\streamlit.exe run streamlit_app.py

   브라우저가 열립니다. **뿌리의 `streamlit_app.py` 로만 띄우십시오.**
   `pip install -e .` 는 하지 않아도 됩니다 — 진입점이 `src` 를 스스로 경로에
   넣습니다.

4. 「1단계 · 진단」 에서 사용량 파일을 올립니다.

       input\\사용량조회_20240429.csv          대형 사업장 (22,285 MWh)
       input\\사용량조회_소형사무빌딩.csv       소형 사무빌딩 (984 MWh · 잉여가 납니다)

   계약 정보 넷(계약종별·전압·선택요금·계약전력)만 넣으면 결과가 나옵니다.
   설비 정보는 묻지 않습니다.


담긴 것
-------
  streamlit_app.py    진입점
  src\\kwise\\          계산·화면 전부
  data\\               요금표·규칙·판단값·기준 데이터 출고층
  data\\weather\\       기상 사전 취득분 (전국 135격자 x 2023~2025)
                      **이것이 있어 인터넷 없이도 태양광이 계산됩니다.**
  input\\              시험 자료 (실측 두 벌 + 케이스 여섯)
  docs\\MANUAL.html    매뉴얼 · docs\\TECHNICAL.html 기술서

담지 않은 것
------------
  .venv · 캐시 · git 이력 · 시험(tests) · 도구(tools) · 산출물(output)
  data\\source\\ (약관·요금표 원문 13 MB — 코드가 읽지 않습니다)

만들어지는 것
-------------
  cache\\    중간 산출물. 지워도 다시 만들어집니다
             (`PROJECT_CACHE` 환경변수로 자리를 옮길 수 있습니다)
  output\\   Excel · PPT. 파일명에 날짜·시각이 붙습니다

적용 범위 — **대한민국 전용**입니다. 요금 체계·역률 규정·경제성DR·공휴일이
모두 한국 제도에 묶여 있습니다.
"""


def build(target: Path, *, root: Path = PROJECT_ROOT) -> tuple[Path, int, int]:
    """묶음을 쓴다. ``(경로, 파일 수, 바이트)`` 를 돌려준다."""
    members = bundle_members(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for source, name in members:
            archive.write(source, name)
        # **실행 절차를 묶음 안에 둔다.** 받는 사람이 이 파일부터 연다.
        archive.writestr(f"{BUNDLE_ROOT}/실행하기.txt", RUN_GUIDE)
    return target, len(members) + 1, target.stat().st_size


def default_target(root: Path = PROJECT_ROOT, *, now: dt.datetime | None = None) -> Path:
    stamp = (now if now is not None else dt.datetime.now()).strftime("%Y%m%d_%H%M")
    return root / "output" / f"kwise_local_{stamp}.zip"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="로컬 실행 묶음 만들기")
    parser.add_argument("--out", type=Path, default=None, help="산출 zip 경로")
    args = parser.parse_args(argv)

    target, count, size = build(args.out or default_target())
    print(f"{target}")
    print(f"  파일 {count:,}개 · {size / 1024 / 1024:.1f} MB")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
