# S130 리뷰 — 프로젝트 종합 리뷰 (한 줄도 안 고쳤다)

2026-09-06 · 2번 PC (Ryzen 5 4500U · 6코어 · 7.4 GB) · HEAD `8a3dee4` 에서 시작.

**이 판은 리뷰 판이다.** `src\**` · `data\**` · 문서를 한 줄도 안 고쳤다. 산출물은 이
문서 하나이고 `PROCEED.md` 기록이 예외다. 값마다 어디서 왔는지 한 낱말을 붙인다 —
**「저장소」**(코드·문서·git 에서 이 판이 직접 잰 값) · **「스크래치」**(이 판이
저장소 밖에서 돌린 스크립트 값) · **「문서에 적힌 값」**(`PROCEED.md`·`docs\` 가 적어
둔 값을 옮긴 것) · **「앞 세션 보고」**(지시서 3부 같은 이차 자료).

**지시서 3부는 이차 자료로 다뤘다.** 실물에서 먼저 확인하고 어긋난 것은 6항(보고)과
이 문서 2절 11번에 적었다. **반대 근거를 판정마다 함께 냈다** (4절).

---

## 0절 — 자리 확인 (대조표 1~3)

### 1. 어느 PC · 무엇이 도나

| 무엇 | 값 | 출처 |
|---|---|---|
| PC | **2번 PC** — `DESKTOP-L8O0EG1` · AMD Ryzen 5 4500U · 6코어 6스레드 · 7.4 GB | 저장소(PowerShell) |
| `git pull` | Already up to date | 저장소 |
| `git status` | `M data/source/SOURCES.md` **하나** — 13줄 삭제(113세션 4절 인용 블록). **그대로 두었다** | 저장소 |
| 다른 프로젝트 python | 시작 때 **0개** (`Win32_Process` `python.exe` 없음) | 저장소 |

### 2. 수집 건수 · 미해결 건수

| 무엇 | 값 | 출처 |
|---|---|---|
| `pytest tests --collect-only` (`-q` 없이) | **1,667 tests collected in 5.70s** (벽시계 28초 · 2번 PC) | 저장소 |
| 시험 파일 | **33** (`tests\` 의 `.py` 37 가운데 `__init__`·`conftest`·`_appmemo`·`_synthetic` 넷은 시험이 아니다) | 저장소 |
| 미해결 | **59건** — ① 7 · ② 51 · ③ 1 (`daily_brief.py`) | 저장소 |
| S129 말미 기록 | 59건 | 문서에 적힌 값 — **같다** |

### 3. 이 문서

`docs\REVIEW_S130.md`. 절마다 쓰고 절마다 커밋한다.

---

## 1절 — 코드가 방만한지 값으로 잰다 (대조표 4~7)

### 4. 규모

**`src\kwise\**` 폴더별** (`.py` 파일 수 · 줄 수 · 그 가운데 도크스트링·주석·빈 줄을 뺀 코드 줄. `tokenize` 로 셌다 — 스크래치)

| 폴더 | 파일 | 줄 | 도크스트링 | 주석 | 빈 줄 | **코드** | 산문 비율 |
|---|---|---|---|---|---|---|---|
| (뿌리) | 8 | 1,638 | 326 | 39 | 338 | 935 | 22% |
| `compare` | 3 | 1,168 | 227 | 50 | 129 | 762 | 24% |
| `diagnose` | 7 | 1,998 | 445 | 92 | 304 | 1,157 | 27% |
| `io` | 3 | 1,320 | 251 | 37 | 217 | 815 | 22% |
| `measures` | 14 | 7,142 | 1,676 | 363 | 951 | 4,152 | 29% |
| `pv` | 9 | 2,587 | 480 | 51 | 452 | 1,604 | 21% |
| `quality` | 6 | 1,033 | 141 | 20 | 153 | 719 | 16% |
| `report` | 17 | 11,965 | 1,902 | 1,030 | 1,524 | 7,509 | 25% |
| `rules` | 5 | 1,612 | 207 | 31 | 260 | 1,114 | 15% |
| `tariff` | 13 | 4,325 | 807 | 258 | 594 | 2,666 | 25% |
| `ui` (`views` 포함) | 25 | 9,749 | 1,559 | 918 | 1,343 | 5,929 | 25% |
| **합계** | **110** | **44,537** | 8,021 | 2,889 | 6,265 | **27,362** | **24%** |

**`tests\**` · `tools\**` · `docs\**`**

| 무엇 | 파일 | 줄 | 코드 줄 | 산문 비율 | 출처 |
|---|---|---|---|---|---|
| `tests\` | 37 (`.py`) | 29,381 | 17,013 | 22% | 스크래치 |
| `tools\` | 19 (`.py`) | 4,895 | 2,994 | 22% | 스크래치 |
| `docs\*.md` | 12 | 9,266 | — | — | 저장소(`wc -l`) |
| `PROCEED.md` | 1 | 29,216줄 · **1,163,597자** (2,157,436바이트) | — | — | 저장소 |

- 시험/소스 줄 비 **0.66** (29,381 / 44,537) · 코드 줄로는 **0.62** (17,013 / 27,362). 저장소.
- `docs\` 가운데 큰 넷 — `TECHNICAL.md` 2,249 · `MANUAL.md` 2,100 · `REQUIREMENTS_kwise.md` 1,886 · `CALC_LOGIC.md` 1,132. 저장소.

**가장 큰 파일 열 (src + tests + tools)** — 저장소

| 순 | 파일 | 줄 |
|---|---|---|
| 1 | `tests\test_ui_screen.py` | 4,594 |
| 2 | `tests\test_slides.py` | 3,557 |
| 3 | `tests\test_measures.py` | 2,658 |
| 4 | `src\kwise\report\slides.py` | **2,562** |
| 5 | `tests\test_ess_cost.py` | 2,093 |
| 6 | `src\kwise\measures\ess.py` | 1,971 |
| 7 | `src\kwise\report\document.py` | 1,867 |
| 8 | `src\kwise\ui\views\measures.py` | 1,824 |
| 9 | `tests\test_tariff_engine.py` | 1,317 |
| 10 | `src\kwise\ui\views\compare.py` | 1,259 |

**500줄을 넘는 소스 파일 — 28개 / 110** (25%). 저장소.

`report\slides.py` 2,562 · `measures\ess.py` 1,971 · `report\document.py` 1,867 ·
`ui\views\measures.py` 1,824 · `ui\views\compare.py` 1,259 · `report\frames.py` 1,184 ·
`ui\charts.py` 1,161 · `report\figures.py` 1,111 · `ui\views\diagnose.py` 1,000 ·
`measures\solar.py` 965 · `tariff\engine.py` 947 · `report\narrative.py` 903 ·
`report\excel.py` 846 · `compare\combination.py` 840 · `diagnose\dr.py` 821 ·
`measures\ess_cost.py` 805 · `pv\archive.py` 776 · `report\casestudy.py` 753 ·
`ui\pipeline.py` 742 · `measures\contract.py` 730 · `io\usage.py` 728 ·
`tariff\source_excel.py` 727 · `ui\cache.py` 712 · `tariff\schema.py` 697 ·
`measures\surplus.py` 588 · `rules\__init__.py` 554 · `io\columns.py` 523 · `docsite.py` 507.

**1,000줄을 넘는 것은 아홉이고 그 가운데 여섯이 `report\` 와 `ui\` 다** — 산출물을 그리는
자리다. 계산(`tariff`·`measures`)에서는 `ess.py` 하나만 1,000 을 넘는다.

### 5. 같은 일을 하는 코드가 여러 자리에 있는가

**기준 둘 — 못이 서 있다** (저장소)

| 모은 것 | 자리 | 부르는 곳 | 못 |
|---|---|---|---|
| 요금적용전력 | `tariff\demand.py::apply_contract_floor` | `tariff\engine.py:524` · `diagnose\peak.py:203` · `measures\contract.py:310` — **셋** | `test_요금적용전력을_만드는_자리는_한_곳이다` (S119) |
| 관측 최대 | `io\usage.py::observed_max_kw` | 로더(`usage.py:632`) · `measures\netload.py:72` — **둘**. 나머지는 `UsageData.observed_max_kw` 속성을 읽는다(9자리) | `test_관측_최대와_초과_구간을_세는_자리는_한_곳이다` (S120) |

**찾는 방법.** ㄱ 같은 이름의 비공개 도우미(`def _x`)가 둘 이상의 파일에 정의된 것을
셌다. ㄴ 한 사실(기본요금 비중 · 회수기간 문구 · MWh 표기 · 금액 표기)을 만드는 식을
`grep` 으로 전수로 뽑아 몸통을 맞댔다. ㄷ 산출물 모듈(`ui\views`·`report`)에서
`.max()`·`.mean()` 으로 값을 다시 세는 자리를 찾았다.

**찾은 것** (저장소)

| 같은 일 | 자리 | 꼴이 몇인가 | 못 |
|---|---|---|---|
| **기본요금 비중** | 화면 `ui\views\diagnose.py:899` (`base_with_pf / total` 직접 나눗셈) · PPT `report\slides.py:1393` (같은 나눗셈을 또 적음) · Excel `report\excel.py:688` (`structure.base_with_power_factor_share`) · Word `report\document.py:1540` (같은 속성) · **케이스 스터디 `report\casestudy.py:214` (`total_base_won / total_won` — 역률을 안 접는다)** | **5자리 · 산식 3꼴** | **없다.** S124 못(`test_산출물이_세는_기본요금_비중은_역률이_붙어도_백을_채운다`)은 Excel·Word 만 문다 |
| **회수기간 문구** | `ui\text.py:431 payback` · `report\slides.py:938 _payback` · `report\document.py:480 _payback_text` · `report\standalone.py:291 _payback` · `measures\ess.py:134 payback_text` | **5자리 · 「즉시」 조건이 둘로 갈린다** — 화면·standalone 은 `years <= 0`, PPT·Word 는 `investment == 0`. 미해결 ②-11(조합 표 회수기간이 절감 0 에도 「즉시」)이 이 갈림이다 | 없다 |
| **금액 한 칸** `_won` | `diagnose\summary.py:124` · `report\document.py:468` · `report\slides.py:932` · `report\worksheet.py:106` | 4자리. 셋은 `money.won*` 에 넘기고 **Word 만 `format_won`+「원」 을 제 손으로 붙인다** | 없다 |
| **구성비** `_share` | `report\document.py:636` · `ui\views\measures.py:1348` (`diagnose\peak.py:115` 는 뜻이 다르다) | 2자리 · 같은 뜻(0 이면 「—」) · 코드 둘 | 없다 |
| **MWh 표기** | `ui\text.py:328` 에 도우미가 있는데 `report\document.py` 3 · `report\excel.py` 2 · `report\slides.py` 2 · `report\narrative.py` 3 · `report\figures.py` 1 자리가 `/ 1000` 을 손으로 적는다 | **11자리** 가운데 도우미를 쓰는 것은 화면뿐 | 없다 |
| **부분 합 = 합계** (기본 + 역률 + 전력량 + 부가금) | 화면 월별 명세 · Excel 명세(S129) · Word 요금 구조 표(S124·S127) · **PPT·화면 월별 요금 구성 막대(②-32 · 아직)** | 4자리 · 셋은 자리마다 못을 박았고 하나는 열려 있다 | 자리별 못 셋 — **뿌리 못은 없다** |
| ESS 하루 절감 kW | `ui\views\measures.py:1716` 이 그림 프레임의 `max()` 차로 다시 센다 | 계산 모듈 밖에서 다시 세는 자리 **하나** (산출물 모듈 전수에서 `.max()` 로 값을 만드는 곳은 이것뿐) | 없다 |

**못이 없는 것 — 일곱 가운데 일곱.** 요금적용전력·관측 최대는 모은 뒤 못이 섰고 다시 안
흩어졌다(2절 8번). 위 일곱은 못이 없거나 자리별 못뿐이다.

**같은 이름 도우미 열아홉** (`__post_init__`·`__str__` 같은 던더 제외 열셋) — `_won` 4 ·
`_share` 3 · `_table`·`_store`·`_render`·`_ratio`·`_quote`·`_pct`·`_payback`·`_hours_in`·
`_heading`·`_constructor`·`_as_period`·`_as_date` 각 2. **이름이 같다고 다 같은 일은 아니다**
— 몸통을 본 것은 위 표의 넷이다.

### 6. 시험이 코드를 어떻게 덮는가

**시험 파일별 건수** (`--collect-only` 결과를 파일로 묶었다 — 저장소)

| 파일 | 건 | 대상 (import 한 `kwise` 모듈) |
|---|---|---|
| `test_ui_screen.py` | 260 | 화면 전부(AppTest) · 수단 · 산출물 |
| `test_slides.py` | 128 | `report.slides`·`design`·`figures`·`frames`·`narrative` |
| `test_measures.py` | 110 | `measures.contract`·`solar` · `compare` |
| `test_ess_cost.py` | 94 | `measures.ess`·`ess_cost` · `report.standalone` |
| `test_ui.py` | 81 | `ui.pipeline`·`cache`·`anchors`·`rules_view` |
| `test_tariff_engine.py` | 68 | `tariff` 엔진 |
| `test_tariff_data.py` · `test_integration.py` | 58 · 58 | 요금표 json · 통합 |
| `test_notices.py` | 51 | `notices` · `ui.callout` |
| `test_pv.py` · `test_power_factor.py` · `test_io_usage.py` · `test_dr.py` | 49 · 47 · 47 · 46 | |
| `test_report.py` · `test_tariff_tou.py` · `test_diagnose.py` · `test_document.py` | 42 · 41 · 41 · 40 | |
| `test_docsite.py` · `test_rules.py` · `test_tariff_source.py` · `test_progress.py` · `test_compare.py` | 38 · 37 · 35 · 35 · 35 | |
| `test_pv_input.py` · `test_quality.py` · `test_pv_archive.py` · `test_io_columns.py` · `test_casestudy.py` | 33 · 32 · 27 · 25 · 22 | |
| `test_deployment.py` · `test_daily_brief.py` · `test_doc_counts.py` · `test_money.py` · `test_excess.py` · `test_magnitude.py` | 19 · 19 · 16 · 14 · 10 · 9 | |
| **합** | **1,667** | |

**시험이 하나도 없는 소스 파일** — 두 잣대로 셌다 (스크래치. 커버리지를 돌리지 않았다 —
전체 pytest 8분을 안 쓰기로 한 판이라 정적으로 쟀다).

- 잣대 ㄱ **모듈 이름을 `import` 하는 시험이 없다** — 17개. 다만 이 잣대는 `from kwise.io
  import match_usage_column` 처럼 패키지에서 꺼내 쓰는 것을 놓친다.
- 잣대 ㄴ **파일의 공개 이름(함수·클래스)을 시험 본문이 한 번도 안 부른다** — **7개 / 99**
  (공개 이름이 있는 파일 99). `tariff\school.py` · `ui\context.py` · `ui\memo.py` · `ui\nav.py`
  · `ui\progress.py` · `ui\views\compare.py` · `ui\views\rules_admin.py`. 이 가운데
  `views\compare.py`·`rules_admin.py`·`nav.py`·`progress.py` 는 AppTest 가 화면을 돌리며
  지나므로 「덮이지 않는다」 가 아니라 「이름으로 안 부른다」 다. **`tariff\school.py` 는
  둘 다 해당한다** — 특례를 켠 벌이 없어(②-49) 화면 시험도 못 지난다.

**아무도 안 부르는 공개 이름** (시험·다른 소스·`tools\`·`streamlit_app.py` 어디서도 낱말로
안 나온다 — 스크래치. **정의 파일 안에서만 쓰이는 것**은 뺐다)

`tariff\source_excel.py::rule_table`(정의 1회뿐) · `ui\memo.py::memo_size` ·
`ui\session.py::remove_all` · `ui\artifacts.py::clear_artifacts` ·
`ui\charts.py::solar_curve_chart` · `rules\store.py::restore_latest_backup` ·
`ui\cache.py::cached_baseline_bill` · `ui\state.py::clear_upload` ·
`diagnose\dr.py::resource_type_labels` — **아홉**이 정의 파일 안에서 2회 이하다
(정의 + `__all__` 또는 도크스트링). **죽은 코드로 단정하지 않는다** — Streamlit 콜백처럼
문자열로 불리는 것이 있을 수 있어 「후보」 로 적는다.

**시험이 식을 다시 적는 자리** — 찾는 방법: 시험 본문에서 계산 상수·반올림·단위 환산이
손으로 적힌 것을 `grep` 으로 셌다 (저장소).

| 무엇 | 파일 · 건 | 판정 |
|---|---|---|
| `round_kw` | `test_tariff_engine.py` 8 · `test_measures.py` 6 | S119 가 `.map(round_kw)` 손질을 걷었고 지금 남은 것은 함수를 **부르는** 것이다 — 식을 다시 적은 것이 아니다 |
| `math.ceil`/`floor` | `test_measures.py` 7 | 목표 계약전력 후보를 시험이 제 손으로 올림한다 — **식을 다시 적는 자리 후보** |
| `* 0.3` (하한 30%) | `test_measures.py` 1 | 후보 하나 |
| `* 1.1` (관측 최대 × 1.1) | `test_ui_screen.py` 1 · `test_measures.py` 1 | 후보 둘 |
| 15분 → kW 환산(`* 4` / `/ 4` / `* 0.25`) | 여섯 파일 10건 | 자료를 **짓는** 자리가 대부분이다 — 결과를 맞대는 식이 아니다 |
| 역률 92 | `test_power_factor.py` 24 등 | 약관 간주값을 입력으로 주는 것이다 |

**결함 유형 ⑤ 로 볼 후보는 `test_measures.py` 의 `ceil` 일곱과 `* 0.3`·`* 1.1` 셋 — 열 자리.**
실물이 반올림 규칙을 바꾸면 시험도 함께 바뀌어야 초록이 된다. **깊이 파지 않았다** —
멈춤 규칙.

### 7. `PROCEED.md` 를 읽는 자리

**그 파일을 읽는 코드 — 셋** (저장소. 문자열로만 언급하는 `scan_ctrl.py`·`test_daily_brief.py`
는 읽지 않는다)

| 코드 | 무엇을 읽나 | 어떻게 |
|---|---|---|
| `tools\daily_brief.py` | 「현재 상태」 표 전체를 항목→값으로(`current_state`) · 세션 목록표 마지막 행(`latest_session`) · 「pytest 분할 실행」 | 미해결 칸 원문을 만지는 함수가 **여섯** — `group_chunks`·`first_paren`·`split_top`·`open_items`·`missing_groups`·`hijacked_groups` |
| `tests\test_doc_counts.py` | 「현재 상태」 표(「최근 세션」 행 제외)와 「pytest 분할 실행」 절 — `PROCEED_SECTIONS`·`STATE_ROWS_SKIPPED` | 문서의 수 못이 이 두 자리의 수를 실물과 맞댄다 |
| `tests\test_deployment.py` | 파일 전체 (`INSTRUCTION_DOCS`) | 진입점 문자열만 본다 — 서식 무관 |

**「현재 상태」 표의 칸 길이** (저장소 · Python `len` — **문자 수**. `awk length` 는 바이트라
같은 줄이 59,695 로 보인다)

| 칸 | 문자 | 바이트 |
|---|---|---|
| **미해결** | **31,415** | 59,695 |
| 최근 세션 (109세션에서 멈춘 채) | 33,235 | 61,529 |
| 테스트 상태 | 15,979 | 27,795 |
| 케이스 스터디 | 5,581 | 9,612 |
| 화면 감사 | 2,410 | 4,691 |
| 요금표 | 2,051 | 3,634 |
| 다시 열지 마라 | 1,998 | 3,801 |
| 다음 작업 | 1,686 | 3,434 |
| 표 전체 (30행) | **99,046** | — |

세션 목록표(12~120행)는 76,146자이고 **S121~S129 아홉 행이 17,859자**다.
**S117 이후 세션은 본문 절(`## 오늘 … N세션`)이 없다** — S126·S127 만 있고 S117~S125 ·
S128 · S129 는 목록표의 한 행(2,500~6,900바이트)이 기록의 전부다. 저장소(`grep "^## "`).

**미해결 칸을 표에서 빼 절로 옮기면 딸려 오는 것** (고치지 않았다 — 값만)

| 딸려 오는 것 | 무엇 |
|---|---|
| `daily_brief.py` | `current_state` 가 표 행만 읽으므로 **미해결 원문을 찾는 자리 하나**(339행 `state.get("미해결")`)와 그것을 넘기는 둘(525·531행)이 바뀐다. 파서 여섯은 원문만 받으므로 **그대로다** — 「(」 로 시작하는 갈래 머리 서식이 유지된다는 조건에서 |
| `tests\test_daily_brief.py` 19건 | `PROCEED.md` 원본을 자료로 쓰지 않는다(25행) — 그러나 「현재 상태」 표 모양의 픽스처를 쓴다면 미해결 행이 있는 픽스처가 그대로 통과하므로 **새 자리를 안 문다.** 새 시험이 필요하다 |
| `tests\test_doc_counts.py` | `PROCEED_SECTIONS` 에 새 절 제목을 더해야 미해결 안의 수(「일곱 파일」 같은 것)를 계속 문다 — **한 줄** |
| `CLAUDE.md` 3항 | 「미해결 칸 한 줄이 3만 자」 문장 — 낡는다 |
| `docs\2026-09-03_인수인계.md` | 「미해결 칸」 언급 — 낡는다 |
| 세션 기록 습관 | S121~S129 가 미해결 대조를 **표 행 안에** 적었다. 절로 옮기면 그 습관이 절을 향한다 — 규약 문장이 없다 |

**네 판 연속 `ctx` 의 「가장 크게 먹은 것」 이 이 줄이었다** 는 이 판에서도 같다 — 이
판이 미해결 칸을 파싱하려고 세 번 돌렸고 그때마다 3만 자가 컨텍스트에 들어왔다.

---
