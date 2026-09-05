# 실행 환경

kWise 프로젝트의 개발·실행 환경. 클로드코드는 작업 시작 전 이 문서를 읽는다.

## 0. 개발과 배포를 갈라 읽는다

**이 문서의 대부분은 개발 환경 규약이다.** 제목이 「Windows 11」 하나였을 때
「이 프로젝트는 Windows 전용」 으로 읽혔다 — 코드 주석 하나가 실제로 그렇게
적고 있었다 (60세션 13절에 고쳤다).

| | 무엇 | 그래서 |
|---|---|---|
| **개발·성능 측정** | **Windows 11** | `.venv\Scripts\python.exe` 로 실행 · 기본 인코딩이 cp949 라 `encoding=` 명시 · matplotlib 은 **Malgun Gothic** · `.ps1` 에 한글을 넣으면 **UTF-8 BOM** |
| **배포** | **Streamlit Cloud (리눅스)** | `packages.txt` 가 `fonts-nanum` 을 깐다 · 한글 폰트를 이름으로 박지 않고 **설치된 것 중에서 고른다** (`report\figures.py`) |
| **코드** | **둘 다에서 돌아야 한다** | 경로는 `pathlib.Path` · 폰트는 후보 목록 · Windows API 를 쓰는 자리(RSS 측정)는 **다른 데서 `None` 을 돌려준다** |

**아래 「Windows 고유 주의사항」 은 개발 환경 규약이다** — `CLAUDE.md` 의
Windows 규약도 같다. 배포지에서 성립하지 않는다는 뜻이 아니라, **거기서는
그 자리를 밟지 않는다**는 뜻이다.

---

## 1. 플랫폼

| 항목 | 값 |
|---|---|
| OS | Windows 11 64-bit |
| 셸 | PowerShell (Claude Code는 Git Bash를 내부 셸로 사용) |
| Python | 3.12.x |
| 패키지 관리 | pip (표준 venv) |
| 가상환경 | 프로젝트 내부 `.venv` |
| 프로젝트 경로 | `C:\work\kwise` |

---

## 2. Windows 고유 주의사항

이 항목들은 리눅스에서 넘어온 코드가 깨지는 지점이다.

### 2.1 인코딩 — 가장 자주 걸린다

Windows의 Python 기본 인코딩은 **cp949**다. UTF-8 파일을 읽거나 쓸 때 깨진다.

- 환경변수 `PYTHONUTF8=1` 을 설정한다 (아래 3.4 참조)
- 파일을 열 때는 **항상 `encoding=` 을 명시**한다. 생략 금지.
  ```python
  open(path, encoding="utf-8")  # 읽기
  open(path, "w", encoding="utf-8")  # 쓰기
  df.to_csv(path, encoding="utf-8-sig")  # Excel에서 열 CSV
  ```
- 한전 CSV는 cp949 또는 utf-8-sig 로 온다. 로더의 인코딩 4종 순차 시도를 유지한다.

### 2.2 경로

- **문자열 결합 금지.** `pathlib.Path` 만 사용한다.
- 경로 구분자를 하드코딩하지 않는다. `/` 도 `\\` 도 쓰지 않는다.
- 경로 길이 260자 제한이 있다. 프로젝트를 깊은 폴더에 두지 않는다.
- 폴더명에 공백·한글을 넣지 않는다.

### 2.3 한글 폰트

Windows에는 **맑은 고딕(Malgun Gothic)** 이 기본 설치되어 있다. 별도 폰트 설치나 shim 이 필요 없다.

```python
import matplotlib

matplotlib.rcParams["font.family"] = "Malgun Gothic"
matplotlib.rcParams["axes.unicode_minus"] = False
```

Streamlit 차트는 altair 또는 plotly 를 우선 사용한다. 이 둘은 폰트 설정이 필요 없다.

### 2.4 파일 잠금

**Excel 이 파일을 열고 있으면 덮어쓰기가 실패한다.** `PermissionError` 가 난다.

- 결과 파일명에 날짜·시각 접미사를 붙인다. `result_20260806_1430.xlsx`
- 저장 실패 시 사용자에게 "Excel 에서 파일을 닫아 주세요" 를 안내한다.

### 2.5 줄바꿈

`git config core.autocrlf` 를 건드리지 않는다. `.gitattributes` 로 텍스트 파일을 LF 로 고정한다.

---

## 3. 설치 절차

### 3.1 Git for Windows

Claude Code 가 Git Bash 를 셸로 쓴다. 없으면 PowerShell 로 대체되지만 일부 기능이 제한된다.

```powershell
winget install --id Git.Git -e
```

### 3.2 Python 3.12

```powershell
winget install --id Python.Python.3.12 -e
```

설치 후 새 PowerShell 창에서 확인한다.

```powershell
python --version
```

### 3.3 환경변수

사용자 환경변수로 등록한다. PowerShell 뿐 아니라 CMD, VS Code 터미널,
Claude Code 가 띄우는 Git Bash 에서도 동일하게 적용된다.

```powershell
[Environment]::SetEnvironmentVariable("PYTHONUTF8", "1", "User")
[Environment]::SetEnvironmentVariable("PROJECT_CACHE", "$env:LOCALAPPDATA\kwise\cache", "User")
```

설정 후 PowerShell 창을 닫았다 다시 연다.

### 3.4 Claude Code

```powershell
irm https://claude.ai/install.ps1 | iex
```

새 PowerShell 창을 열고 확인한다.

```powershell
claude doctor
```

`claude` 명령을 못 찾으면 `%USERPROFILE%\.local\bin` 을 PATH 에 추가한다.

---

## 4. 프로젝트 환경

### 4.1 가상환경

**리눅스 환경과 달라진 점이다.** 안드로이드에서는 용량 문제로 공용 `~/venv` 를 썼지만,
Windows 에서는 프로젝트 내부에 만든다.

```powershell
cd C:\work\kwise
py -3.12 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
```

활성화가 거부되면 실행 정책을 한 번만 완화한다.

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

### 4.2 패키지 설치

```powershell
pip install -e ".[dev]"
```

안드로이드에서 쓰던 `uvx` 격리 실행은 필요 없다. 개발 도구도 `.venv` 에 직접 넣는다.

**`pip install pytest ruff mypy` 로 낱개로 깔지 않는다.** `.[dev]` 로 깔아야
`pyproject.toml` 의 판 상한(`mypy>=1.11,<2`)과 `types-PyYAML` 이 함께 따라온다.
낱개로 깔면 mypy 2.x 가 들어와 PC 마다 검사 결과가 갈린다 (2026-08-15).

**python 실행은 `.venv\Scripts\python.exe` 로 한다.** 셸이 `.venv` 를 활성화하지 않은 채
실행될 수 있어 시스템 python 을 쓰면 패키지를 못 찾는다.

### 4.3 주요 패키지

| 용도 | 패키지 |
|---|---|
| 계산 | numpy, pandas, scipy |
| 태양광 | pvlib |
| 날짜 | holidays |
| UI | streamlit, altair, plotly |
| 출력 | openpyxl, XlsxWriter |
| 캐시 | pyarrow |
| CLI | typer, rich |
| 테스트 | pytest, ruff, mypy |
| 화면 실물 확인 | playwright (개발 전용) |
| 규정 원문 확인 | pypdf (개발 전용) |

### 4.4 브라우저 바이너리는 따로 받는다

`playwright` 는 **꾸러미만으로는 안 돈다.** `tools\capture_screen.py` 가 앱을
진짜 브라우저로 열어 찍으므로 크로미움을 한 번 받아야 한다.

```powershell
.venv\Scripts\python.exe -m playwright install chromium
```

받는 곳은 `%LOCALAPPDATA%\ms-playwright\` 이고 `.venv` 밖이다 — **가상환경을
지웠다 다시 만들어도 남는다.** 115 MB 이고 **1번 PC 에서 36초** 걸렸다.

**2번 PC 에서는 아직 안 받았다.** 그 PC 를 처음 쓰는 세션이 위 한 줄을 돌리고
소요를 여기 적는다.

---

## 5. 폴더 규약

| 용도 | 경로 |
|---|---|
| 프로젝트 | `C:\work\kwise` |
| 캐시·중간 산출물 | `%LOCALAPPDATA%\kwise\cache` (환경변수 `PROJECT_CACHE`) |
| 최종 산출물 | `<프로젝트>\output` |
| 입력 데이터 | `<프로젝트>\input` |

캐시를 프로젝트 밖에 두는 이유는 git 오염과 백업 용량을 피하기 위함이다.
환경변수가 없으면 프로젝트 내 `.\cache` 를 기본값으로 쓰고, 이 경로는 `.gitignore` 에 있다.

---

## 6. 실행

### 6.1 테스트

```powershell
pytest -n auto --dist load
ruff check .
ruff format --check .
mypy
```

**pytest 를 어떻게 도는지는 `CLAUDE.md` 「백그라운드 실행」 9항이 정본이다** —
`-n` 에 수를 박지 않는 까닭, PC 별 실측 소요와 메모리, 갈래로 나누는 갈림길이
거기 한 자리에 있다. **여기 옮겨 적지 않는다** (69세션 규약: 같은 말이 두 곳에
있으면 어긋난다 — 실제로 이 절이 `-n 4` 를 권하는 동안 9항은 `-n auto` 로
가 있었다).

**`--dist loadfile` 은 쓰지 않는다.** 파일 단위로 배분해서 무거운 단일 파일
(`test_ui_screen.py`)을 못 쪼갠다 — 그 파일이 그대로 남아 이득이 없다.

**mypy 는 맨 `mypy` 다** (70세션 3절). `pyproject.toml` 의
`files = ["src", "tests", "tools"]` 를 그대로 돌린다 — 166파일을 본다.
**지금은 통과가 아니다** — 121세션에 재니 `tests\test_measures.py` 하나에서
6건이 난다 (`PROCEED.md` 「mypy 범위」).

70세션 전에는 `mypy src` 로 판단했다. `tests\` 가 검사 대상에 들어 있는데
통과한 적이 없어(70세션 착수 시점 **142건**) 설정과 문서가 서로 다른 말을
하고 있었다. 142건을 풀어 그 어긋남을 없앴다 — **범위를 좁혀서가 아니라
범위를 지켜서 맞췄다.**

**대가는 이렇다.** 이제 **시험을 더할 때 mypy 도 함께 봐야 한다.** 형이 없는
대역을 쓰려면 `object` 가 아니라 `Any` 로 적는다 — `object` 는 「속성이 하나도
없다」 는 뜻이라 사실과 다르고, 부르는 자리마다 억제를 붙이게 된다.

### 6.2 Streamlit

```powershell
streamlit run streamlit_app.py
```

브라우저가 자동으로 열린다. 안드로이드 환경과 달리 헤드리스 제약이 없다.

**뿌리 진입점 하나로만 띄운다.** `src\kwise\ui\app.py` 를 직접 돌리면 그 파일이
진입점이 되어 매 실행에 통째로 다시 돌아간다 — 배포지에서 잡은 "두 번째 실행부터
빈 화면" 결함이 로컬에서 보이지 않는다. Streamlit Cloud 와 **같은 경로**로 띄워야
같은 것을 확인하는 셈이 된다 (2026-08-15).

### 6.3 CLI 배치

```powershell
python -m kwise.cli run --cases cases.yaml
```

---

## 7. 성능

| 항목 | 값 |
|---|---|
| RAM | 안드로이드(8 GB) 대비 여유 있음 |
| 8,760행 처리 | 제약 없음 |
| 다중 시나리오 | 동시 적재 가능하나, 순차 처리 구조를 유지한다 |

메모리 제약이 풀렸다고 구조를 바꾸지 않는다. 순차 처리가 디버깅에도 유리하다.

---

## 8. 알려진 함정

| 증상 | 원인 | 조치 |
|---|---|---|
| 한글이 `???` 로 출력 | cp949 기본 인코딩 | `PYTHONUTF8=1` 설정, `encoding=` 명시 |
| `PermissionError` (xlsx) | Excel 이 파일을 열고 있음 | 파일 닫기, 날짜 접미사 사용 |
| `.\.venv\Scripts\Activate.ps1` 거부 | 실행 정책 | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `claude` 명령 없음 | PATH 미등록 | `%USERPROFILE%\.local\bin` 추가 후 터미널 재시작 |
| `pytest` 가 패키지를 못 찾음 | `.venv` 미활성화 | `.venv\Scripts\activate` 또는 `.venv\Scripts\python.exe -m pytest` |
| `.ps1` 한글 깨짐 | BOM 없는 UTF-8 | UTF-8 BOM 으로 저장 (PowerShell 5.1 제약) |
| matplotlib 한글 깨짐 | 폰트 미지정 | `font.family = "Malgun Gothic"` |
| 경로 관련 오류 | 문자열 결합, 260자 초과 | `pathlib.Path` 사용, 짧은 경로 |
