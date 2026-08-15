# kWise 프로젝트 규약

태양광 피크저감·전기요금 분석 도구. 15분 실측 사용량 데이터로 태양광 도입 시
전기요금 절감 효과를 산출한다.

## 작업 시작 전 필독

0. **`git pull` 을 먼저 한다.** PC 두 대를 GitHub 를 사이에 두고 번갈아 쓴다.
   당기지 않고 시작하면 다른 PC 의 작업 위에 덮어쓰게 된다.
1. `docs\ENVIRONMENT.md` — 실행 환경 제약 (Windows 11)
2. `docs\REQUIREMENTS_kwise.md` — 요구사항
3. `PROCEED.md` — 지금까지의 진행 이력과 미해결 항목

## 절대 규칙

- 가상환경은 프로젝트 내 `.venv` 를 쓴다. 패키지 설치는 `pip install`.
  새 패키지가 필요하면 **먼저 물어본다.**
- **python 실행은 `.venv\Scripts\python.exe` 로 한다.** 셸이 `.venv` 를 활성화하지 않은 채
  실행될 수 있으므로 시스템 python 을 쓰면 패키지를 못 찾는다. `pytest` 도
  `.venv\Scripts\python.exe -m pytest` 로 실행한다.
- **파일을 열 때 `encoding=` 을 반드시 명시한다.** Windows 기본이 cp949 라 생략하면 한글이 깨진다.
  읽기는 `encoding="utf-8"`, Excel 에서 열 CSV 는 `encoding="utf-8-sig"`.
- **경로는 `pathlib.Path` 만 쓴다.** 문자열 결합과 구분자 하드코딩 금지.
- 캐시·중간 산출물은 `PROJECT_CACHE` 환경변수 경로(기본값 `.\cache`), 최종 산출물만 `output\`.
- **Excel 저장 전 tz-aware 컬럼을 `dt.tz_localize(None)` 로 해제한다.** pvlib 결과는 항상 tz-aware다.
- 결과 파일에 날짜·시각 접미사를 붙인다. `result_20260806_1430.xlsx`
  Excel 이 파일을 열고 있으면 덮어쓰기가 실패하므로 필수다.
- matplotlib 사용 시 `font.family = "Malgun Gothic"`. Streamlit 차트는 altair/plotly 우선.
- 코드 주석과 응답은 한국어.

## 화면 문구

**화면 문구는 늘리지 않는다.**

- 새 안내를 붙이기 전에 **용어나 이름을 고쳐서 해결되는지** 먼저 본다.
  설명이 필요하다는 것은 대개 이름이 틀렸다는 뜻이다.
- 배경·근거·제도 설명은 **매뉴얼(`docs\MANUAL.md`)에 적는다.** 화면에 두지 않는다.
- 화면에 남기는 것은 둘뿐이다 — **없으면 입력을 못 하는 것, 없으면 결과를 오독하는 것.**
- 문구를 하나 더할 때는 **무엇을 뺄지 함께 정한다.**
  예산(본문 3줄·확인사항 3항목)이 그 장치다 — `tools\screen_budget.py`.

**고치기 전과 후에 `tools\screen_audit.py` 를 돌린다.** 화면에 실제로 그려진
문구를 전부 모아 규칙 위반(코드 식별자·내부 문서 번호·규정 이름 없는 조문·
맨 물결표)과 중복 후보를 센다. 수가 늘었으면 늘린 이유를 적는다.

## 코드 규약

- src 레이아웃. `pip install -e .`
- 타입힌트 사용. ruff, mypy 통과.
- 모든 계산 함수에 단위테스트.
- **요금 계산은 순수 함수로 작성한다.** Streamlit 을 import 하지 않는다.
- 계산 로직과 UI 를 분리한다. UI 는 순수 함수를 호출만 한다.

- PowerShell 스크립트(`.ps1`)에 한글을 넣을 때는 **UTF-8 BOM** 으로 저장한다.
  BOM 없으면 Windows PowerShell 5.1 이 cp949 로 읽어 깨진다.

## 금지

- `pybuildingenergy` 사용 — 실측 부하를 쓰므로 불필요하다. 신축 부하 예측은 범위 밖이다.
- 결측값 자동 보간 — 기본은 미보간. 결측 구간은 계산에서 제외하고 명시한다.
- 요금 데이터 하드코딩 — 반드시 `data\tariff_*.json` 에서 읽는다.
  드롭다운 선택지도 이 파일에서 생성한다.
- 태양광을 기본 전제로 두는 설계 — 진단 단계는 설비 정보 없이 동작해야 한다.
  사용자가 파일만 올려도 결과가 나온다.
- 조합 절감액을 단순 합산 — 수단마다 요금을 다시 계산한다. 태양광이 사용량을 줄이면
  최적 선택요금이 바뀌고, ESS 가 피크를 낮추면 기본요금 기반이 달라진다.
- `reference\` 폴더 수정 — 읽기 전용 참조 코드다.
- 메모리 여유가 생겼다고 다중 시나리오를 동시 적재하지 않는다. 순차 처리를 유지한다.

## reference\ 폴더

기존 `pv_peak_cut` 코드다. **참조만 하고 수정하지 않는다.** 이식할 때는
`src\kwise\` 아래에 새로 작성하면서 타입힌트와 테스트를 붙인다.

| 파일 | 쓸 곳 |
|---|---|
| `mg_pv_core.py` | 4세션 — PV 계산 엔진 이식 |
| `mg_weather_openmeteo.py` | 4세션 — 기상 취득 이식 |
| `streamlit_app.py` | 1세션 — `parse_usage_datetime`, `read_uploaded_usage_file`, `match_usage_column` 참조<br>8세션 — `hourly_pattern_table` 참조 |

`parse_usage_datetime` 의 `24:00` 처리는 실측 데이터로 검증된 로직이다. 그대로 가져간다.
`mg_weather_openmeteo` 의 PowerShell 폴백은 프록시 우회용이다. Windows 에서는 동작하지만
`requests` 경로가 정상이면 쓰이지 않는다. 유지 여부는 4세션에서 판단한다.

## 세션 종료 시

작업을 마치면 `PROCEED.md` 에 아래를 추가하고 커밋한다.

- 완료한 항목
- 만든 파일 목록
- 미해결 항목과 그 이유
- 다음 세션이 알아야 할 결정 사항

이 기록이 다음 세션의 출발점이다. 빠뜨리지 않는다.

**커밋 후 `git push` 로 원격에 올린다. 다른 PC 에서 이어받는다.**
올리지 않으면 그 PC 에만 남는다 — 다음 세션이 다른 PC 에서 열리면 없는 일이 된다.
