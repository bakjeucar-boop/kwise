# 실행 환경 (Windows 11)

kWise 프로젝트의 개발·실행 환경. 클로드코드는 작업 시작 전 이 문서를 읽는다.

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
pytest
ruff check .
mypy src
```

**`mypy src` 다 — 맨 `mypy` 가 아니다.** `pyproject.toml` 의
`files = ["src", "tests", "tools"]` 는 `tests\` 까지 검사하는데, 여기서 38건이
뜬다 (형 정리 미완, `PROCEED.md` 「도구·형 정리」 참조). `src\` 103파일과
`tools\` 12파일은 깨끗하다. 정리를 끝내면 이 어긋남도 함께 없앤다.

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
