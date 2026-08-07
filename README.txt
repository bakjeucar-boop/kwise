kWise - Windows 시작 파일 묶음
==============================

건물 전력 비용 진단 및 개선안 비교

적용 범위
---------
kWise 는 대한민국 전용 도구입니다. 요금 체계(한전 기본공급약관·전기요금표),
역률 규정(약관 제41~43조), 경제성DR(전력시장운영규칙 제12장), 공휴일,
시군구 좌표 229개가 모두 한국 제도에 묶여 있습니다. 해외 부지에는
적용할 수 없습니다. 자세한 내용은 docs\REQUIREMENTS_kwise.md 1.4 절.

데이터 출처
-----------
기상 자료: Open-Meteo (https://open-meteo.com/) ERA5 재분석, CC BY 4.0.
          Copernicus Climate Change Service 정보를 포함합니다.
          data\weather\ 에 2023-01 ~ 2025-12 전국 135개 격자분을 미리 받아 둡니다.
          tools\fetch_weather.py 로 갱신·확장할 수 있습니다.
요금 자료: 한국전력공사 전기요금표 (data\source\, data\tariff_*.json)
약관·규칙: 한전 기본공급약관 / 전력거래소 전력시장운영규칙
          (data\source\*.txt - PDF 원본은 저장소에 두지 않습니다)

배치 방법
---------
1. C:\work\kwise 폴더를 만든다
2. 이 묶음의 파일을 폴더에 그대로 푼다 (docs 폴더 구조 유지)
3. SETUP.html 을 브라우저로 열어 Step 0 부터 순서대로 진행

포함 파일
---------
SETUP.html                   설치 안내 (먼저 읽으세요)
PROJECT_GUIDE.html           세션별 프롬프트와 순서
CLAUDE.md                    프로젝트 규약 - 클로드코드가 자동으로 읽음
PROCEED.md                   진행 이력 - 이전 결정사항이 채워져 있음
docs\ENVIRONMENT.md          Windows 11 실행 환경
docs\REQUIREMENTS_kwise.md   요구사항서

별도로 준비할 것
----------------
reference\  mg_pv_core.py, mg_weather_openmeteo.py, streamlit_app.py
input\      사용량조회_20240429.csv
