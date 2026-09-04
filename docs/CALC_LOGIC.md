# 요금 계산 산식 목록

**설명 문서가 아니라 산식 목록이다.** `TECHNICAL.md` 는 구조를 적고 이 문서는
**금액이 어떻게 만들어지는가**만 적는다. 문장을 늘리지 않고 표로 적는다.

읽은 것은 **코드와 기준 데이터뿐이다** (110세션). 약관 원문과 대조하지 않았다 —
「빠진 것 같다」 는 전부 **부록의 의심 목록**으로 갔고, 판정은 다음 세션이 한다.

| 부 | 무엇 |
|---|---|
| 1부 | 요금 한 장을 만드는 산식 |
| 2부 | 계약전력 조정 판정 |
| 3부 | 선택요금 전환 판정 |
| 4부 | 둘이 물리는 자리 |
| 부록 | 의심 목록 |

읽은 파일 — `tariff\engine.py` · `tariff\demand.py` · `tariff\excess.py` ·
`tariff\power_factor.py` · `tariff\school.py` · `tariff\tou.py` ·
`tariff\pending.py` · `tariff\schema.py` · `measures\contract.py` ·
`measures\tariff_switch.py` · `compare\combination.py` · `ui\pipeline.py` ·
`data\rules_kr.json` · `data\tariff_kr_20260601.json`.

---

## 1부. 요금 한 장을 만드는 산식

**총액을 만드는 것은 네 항이다.** 나머지는 표에 열로 있지만 총액에 더해지지
않는다 — 「총액에 실리나」 칸이 그것을 가른다.

    total_won = base_won + power_factor_won + energy_won + excess_won

산식·함수·기준 데이터는 `calculate_bill` (`tariff\engine.py`) 이 만드는 월별 표
`monthly` 의 열 이름으로 적는다.

### 총액에 실리는 넉 줄

| 항 | 산식 | 어느 함수 | 읽는 기준 데이터 | 종별·전압 갈림 | 의심 |
|---|---|---|---|---|---|
| 기본요금<br>`base_won` | `기본요금 기준 kW × 기본요금 단가 × 부분월 계수` | `engine.calculate_bill` | 요금표 `base_won_per_kw` (선택요금별) | **전압이 가른다** — `base_fee_on_contract_at()` 이 참이면 기준 kW = 계약전력, 거짓이면 요금적용전력 | ①② |
| 역률요금<br>`power_factor_won` | `그 달 base_won × (지상 조정률 + 진상 조정률)` | `power_factor.power_factor_charge` | `power_factor.*` (기준 92·95%, 하한 60%, 감액상한 97%, 1%p당 0.002) | 없음 — 전 종별 같다 | ③④⑤ |
| 전력량요금<br>`energy_won` | `Σ(시간대 kWh × 단가 × (1−특례할인율)) × (1−학교특례 할인율)` | `engine.calculate_bill` · `tou.classify_slots` · `school.school_discount_rates_by_month` | 요금표 `energy`·`tou_definition`·`season_definition`·`special_rules`, `season.months`, `tou.hours.mainland`, `school_exception.*` | 계절·시간대 단가가 종별×전압×선택요금마다 다르다. `special_rules` 는 `applies_to` 로 종별을 가른다 (지금은 산업용(을) 봄·가을 주말 11~14시 50%) | ⑥⑦ |
| 초과사용<br>부가금<br>`excess_won` | `(그 달 관측 최대수요 − 계약전력) × 기본요금 단가 × 배수` | `excess.excess_charges` | `excess_charge.ratio_tiers` (0/0.2/0.3/0.4/0.5/0.6 → 1.5/2.0/2.5/3.0/3.5/4.0), `excess_charge.grace_months`=1 | **계약전력 기준 종별(제68조 ②)은 산출하지 않는다** — `ExcessCharge(applicable=False)` | ⑧⑨⑩ |

### 총액에 안 실리는 것

| 이름 | 무엇인가 | 왜 안 실리나 |
|---|---|---|
| `light_won`·`mid_won`·`peak_won` | 전력량요금을 시간대로 쪼갠 금액 | **쪼갠 것이다.** 셋의 합이 곧 `energy_won` 이라 더하면 두 번 센다 |
| `discount_won` | 특례 할인으로 **깎인** 금액 (산업용(을) 주말) | 기록용. `energy_won` 은 이미 깎은 뒤의 값이다 |
| `school_discount_won` | 학교 특례로 **깎인** 금액 | 같음. `energy_won` 에서 이미 빠져 있다 |
| `energy_won_adjusted` | 결측 보정 기준 전력량요금 `energy_won ÷ (1−결측률)` | **다른 기준이다.** `total_won_adjusted` 쪽으로 간다 — 회수기간 참고용이고 정본은 관측 기준이다 |
| 기후환경요금·연료비조정요금·부가가치세·전력산업기반기금 | — | **계산하지 않는다.** `NOT_INCLUDED_NOTICE` 가 그 사실을 낸다 |

### 적용 순서

앞 항의 결과가 뒷 항의 입력이 되는 자리를 화살표로 적는다.

    구간 분류        classify_slots → 계절 · 시간대 · 특례할인율 · 요일규칙
                     ↓
    ① 요금적용전력   경부하 제외 최대 → 대상월 12개월 굴림 max → 계약전력 하한
                     (school 특례면 굴림 없이 당월분, 하한 15%)
                     ↓
    ② 기본요금 기준  전압이 계약전력 기준이면 계약전력, 아니면 ①
                     ↓
    ③ base_won       ② × 단가 × 부분월 계수
                     ↓
    ④ 역률요금       Σ base_won 에 한 번 산출 → 비율을 달마다 base_won 에 곱함
                     ↓
    ⑤ 전력량요금     Σ(kWh × 단가 × (1−특례할인)) → 학교 특례 할인율 곱
                     ↓
    ⑥ 초과사용부가금 관측 최대수요와 계약전력으로 산출 (①과 무관)
                     ↓
    ⑦ total_won      ③ + ④ + ⑤ + ⑥
                     ↓
    ⑧ 경과조치       기간 안의 달만 짝 선택요금과 견주어 **싼 쪽 금액으로 갈아 끼움**
                     (_MONEY_COLUMNS 열 전부) → 역률요금 기준을 다시 잡음

**⑧은 산식이 아니라 갈아 끼우기다.** 부칙 (2026. 5. 22) 제2항 제1호 —
일반용(갑)Ⅱ 2026년 6월분~11월분에 선택Ⅰ↔Ⅲ · 선택Ⅱ↔Ⅳ 를 견준다. 신청과
무관하게 걸린다. `calculate_bill` 이 짝으로 자기를 한 번 더 부른다.

### 달 단위인가 기간 단위인가

| 항 | 단위 | 부분 월 | 결측 |
|---|---|---|---|
| 기본요금 | **달** | `base_fee_factor` 를 곱한다. 조각 둘이면 합쳐 1개월(`merge`), 아니면 일수 비례(`prorate`) | 결측률이 5%(`MISSING_LIMIT_RATIO`)를 넘으면 최대수요를 「신뢰 제한」 으로만 표시. **금액은 그대로** |
| 역률요금 | **기간** → 달 | 기간 전체 기본요금으로 비율을 낸 뒤 달별 `base_won` 에 곱하므로 자동으로 안분된다 | 없음 |
| 전력량요금 | **달** | 계수를 곱하지 않는다 — 쓴 만큼이다 | `energy_won_adjusted` 를 **함께** 낸다. 보간하지 않는다 |
| 초과사용부가금 | **달** | **안분하지 않는다.** 초과는 안분되는 요금이 아니라 그 달에 일어났거나 아닌 사실이다 | 보간하지 않는다 — 남은 자료의 최대로 판정하므로 과소 산출될 수 있다 |
| 요금적용전력 | **달** (12개월 굴림) | — | 결측 달은 그 달 관측분의 최대로 잡힌다 |

**「12개월 환산」 은 계산이 아니라 곱셈이다** — `annualize()` 가
`12 ÷ base_fee_months` 를 네 총액에 곱한다. 365일 미만이면 경고를 붙인다.

## 2부. 계약전력 조정 판정

(3절에서 채운다)

## 3부. 선택요금 전환 판정

(3절에서 채운다)

## 4부. 둘이 물리는 자리

(4절에서 채운다)

## 부록. 의심 목록

(5절에서 채운다)
