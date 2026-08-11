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

실행하는 법
-----------
화면 (Streamlit)
     .venv\Scripts\streamlit.exe run src\kwise\ui\app.py
     브라우저가 열립니다. 옆단에서 화면을 고릅니다.

       1단계 · 진단       파일만 올려도 결과가 나옵니다. 계약 정보 넷만 묻습니다
       2단계 · 개선 수단   카드로 켜고 끕니다. 투자비 순으로 놓았습니다
       3단계 · 비교       조합·감도·검토 범위·산출물 내려받기
       기준 데이터        값 옆에 근거·확인일을 두고 고칩니다

     산출물은 두 가지입니다.
       Excel   아홉 시트. 분석자용 데이터입니다
       Word    다섯 장. 의사결정자용이며 각 절이 결론부터 시작합니다.
               표는 Word 표 객체라 제안서에 그대로 복사해 쓸 수 있습니다

     업로드한 파일은 서버에 저장하지 않습니다. 내려받기용 산출물도 만든 즉시
     지웁니다. 앱을 켤 때 묵은 임시 폴더를 쓸어냅니다.

     화면의 [자세히] 링크는 docs\MANUAL.html 로 갑니다. 매뉴얼은 10세션에서
     쓰므로 지금은 비활성입니다. 앵커 이름은 docs\MANUAL_ANCHORS.md 에
     확정해 두었습니다.

배치 (CLI)
     .venv\Scripts\python.exe -m kwise.cli run --cases cases.yaml

기준 데이터 갱신 절차
---------------------
법령 유래 수치와 판단값은 코드가 아니라 파일에 있습니다.
1년 뒤의 본인이 읽을 문서이므로 순서를 그대로 따르십시오.

  data\rules_kr.json        법령·약관·규칙에서 온 값 (근거 조문이 있습니다)
  data\assumptions.json     우리가 정한 값 (근거는 "판단값" 입니다)
  data\defaults\           출고 기본값. 읽기 전용입니다. 손대지 마십시오
  data\backup\             편집 직전 자동 스냅샷 (최근 10개)
  data\rules_history.jsonl  편집 이력. 화면에서 고친 값은 여기에만 남습니다

무엇을 언제 갱신하는가
  요금 단가    12개월마다   한전이 연 1~2회 개정합니다
  약관·규칙    24개월마다
  참고단가     24개월마다
  기상         전년도분이 없으면

  만료 임계를 넘기면 화면과 산출물에 경고가 나옵니다. 임계값도
  data\assumptions.json 에 있습니다.

  자동 수집은 하지 않습니다 — 한전 요금표는 API 가 없고, 자동화하면 원문이
  바뀐 것을 알아채지 못한 채 파싱만 실패합니다. 그 실패는 조용합니다.

원문 확인처
  전기요금표       https://cyber.kepco.co.kr/ckepco/front/jsp/CY/E/E/CYEEHP00101.jsp
  기본공급약관     https://cyber.kepco.co.kr/ckepco/front/jsp/CY/D/C/CYDCHP00201.jsp
  전력시장운영규칙 https://www.kpx.or.kr/menu.es?mid=a10301020000
  ESS 참고단가     https://www.keei.re.kr (에너지경제연구원 LCOS 연구)

갱신하는 법 — 셋 중 하나
  1) 화면에서 (권장)
     8세션 UI 의 「기준 데이터 관리」에서 값·근거·확인일을 함께 보고 고칩니다.
     값을 고치면 확인일이 오늘로 자동 갱신되고 이력이 남습니다.
     원문을 다시 보았는데 값이 그대로면 "확인함"만 눌러 기록하십시오.

  2) 엑셀로 (여러 항목을 한꺼번에)
     .venv\Scripts\python.exe tools\export_rules_xlsx.py
     output\rules.xlsx 의 값 열만 고칩니다. 항목 키는 바꾸지 마십시오.
     .venv\Scripts\python.exe tools\build_rules.py --source output\rules.xlsx --check
     차이를 확인한 뒤 --check 를 빼고 다시 실행하면 반영됩니다.

  3) 요금 단가는 엑셀 원본에서
     data\source\ 의 요금표 xlsx 를 새 판으로 바꾸고
     .venv\Scripts\python.exe tools\build_tariff.py
     검증에 실패하면 파일을 쓰지 않습니다.

  기상은 별도입니다.
     .venv\Scripts\python.exe tools\fetch_weather.py --start 2026-01-01 --end 2026-12-31
     이미 받은 격자·연도는 건너뜁니다. 중단해도 같은 명령으로 재개됩니다.

되돌리기 — 세 가지
  직전 상태   편집 직전 스냅샷으로 되돌립니다 (최근 10개 보관)
  출고 상태   data\defaults\ 로 통째로 되돌립니다.
              실행 전에 무엇이 달라지는지 항목별로 보여주고 확인받습니다
  항목별      한 항목만 출고값으로 되돌립니다 (실무에서 가장 많이 쓰입니다)

파일이 깨졌을 때
  최근 백업 → 출고값 순으로 자동 복구하고 그 사실을 반드시 알립니다.
  손상된 파일은 data\<이름>.json.damaged 로 남습니다.
  알림을 못 보고 지나치면 갱신한 값으로 계산되는 줄 알고 결과를 쓰게 됩니다.

주의
  코드에는 기본값이 없습니다. 파일이 없으면 계산이 멈춥니다.
  이것은 의도한 설계입니다 — 코드에 기본값이 남아 있으면 파일을 고쳐도 반영되지
  않는 사고가 나고, 값이 그럴듯하기 때문에 결과를 다 쓰고 나서야 발견됩니다.

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
docs\MANUAL_ANCHORS.md       매뉴얼 앵커 목록 (생성물 - 10세션이 씁니다)

별도로 준비할 것
----------------
reference\  mg_pv_core.py, mg_weather_openmeteo.py, streamlit_app.py
input\      사용량조회_20240429.csv
