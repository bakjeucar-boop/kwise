"""슬라이드 해석 문장과 용어 풀이 (39세션).

**그래프만 나열하면 무엇을 보아야 하는지 알 수 없다.** 받는 사람은 이 보고서를
처음 보는 건물 에너지관리 담당자다 — 그림이 무엇을 말하는지 한 줄이 먼저 있어야
한다.

문장을 짓는 규칙은 셋이다.

    ① 진단이 **이미 판정한 것**은 그 판정을 문장으로 바꾼다
       (:class:`~kwise.diagnose.summary.PvPotential` 이 그렇다)
    ② 판정이 없으면 **값의 크기로 고른다.** 갈림값은 판단값이라
       ``data\\assumptions.json`` 에서 읽는다 — 코드에 두지 않는다
    ③ 값과 무관한 자리는 고정 문장

**판정에는 근거 숫자를 함께 적는다.** 「가능성 높음」 만으로는 왜 그런지 알 수
없다 — 「피크의 66%가 낮 시간에 발생해」 가 붙어야 판정과 근거가 함께 읽힌다.

용어 풀이(:data:`GLOSSARY`)는 **화면 툴팁에서 왔다.** 화면은 「산식 한 줄 + 의미
한 줄」 로 툴팁을 적는데(21세션 2절), 슬라이드에는 산식만 작은 글씨로 깐다.
**두 벌로 적지 않는다** — 화면(:mod:`kwise.ui.views.diagnose`)이 이 모듈에서
꺼내 쓰므로 한쪽만 고쳐지는 일이 없다.

**배경·제도 설명은 여기 두지 않는다.** 매뉴얼이 받는다 (``CLAUDE.md`` 화면 문구
절과 같은 규약이다).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from kwise import money
from kwise.diagnose import ChargeStructure, ImprovementSummary, PeakProfile
from kwise.diagnose.dr import DrProfile
from kwise.diagnose.summary import PvPotential
from kwise.quality import (
    DEFAULT_NIGHT_HOURS,
    DEFAULT_OPERATING_HOURS,
    LoadPattern,
    MonthlyMissing,
    QualityReport,
)
from kwise.rules import assumption
from kwise.tariff import TariffTable
from kwise.tariff.labels import season_label

__all__ = [
    "COMBINATION_LEAD",
    "FORMULA_SEPARATOR",
    "GLOSSARY_KEYS",
    "NOTE_JOIN",
    "NOTE_MARK",
    "SINGLE_MEASURE_LEAD",
    "FlaggedMonthSource",
    "PeakDetailSource",
    "PeakSource",
    "SummarySource",
    "Term",
    "base_fee_share_high",
    "base_fee_share_low",
    "base_load_high",
    "building_lead",
    "combination_lead",
    "dr_lead",
    "glossary_note",
    "load_factor_flat",
    "mark_note",
    "measure_summary_lead",
    "note_line",
    "pattern_lead",
    "peak_detail_lead",
    "peak_month_close",
    "peak_month_lead",
    "peak_summary_lead",
    "power_factor_adjusted_saving",
    "solar_saving_breakdown",
    "structure_lead",
    "surplus_lead",
    "surplus_off_day_high",
    "surplus_off_day_low",
    "surplus_page_lead",
    "terms",
]

#: 범위 기호. **물결표를 쓰지 않는다** — 화면 규약과 같다 (13세션).
_RANGE = "–"


# ===================================================================== 갈림값
#
# **코드에 두지 않는다** (31세션 4-1 과 같은 규약). 「부하율이 평탄하다」 처럼
# 사람이 정한 경계는 전부 판단값이므로 기준 데이터에서 읽는다.


def load_factor_flat() -> float:
    """이 위면 **평탄한 부하**로 본다. 피크 저감보다 사용량 절감을 먼저 본다."""
    return float(assumption("narrative.load_factor_flat"))


def base_load_high() -> float:
    """이 위면 **밤에도 부하가 남는다**고 본다. ESS 충전 여력이 제한적이다."""
    return float(assumption("narrative.base_load_high"))


def base_fee_share_high() -> float:
    """이 위면 **기본요금 비중이 크다**고 본다. 피크 저감의 값어치가 크다."""
    return float(assumption("narrative.base_fee_share_high"))


def base_fee_share_low() -> float:
    """이 아래면 **기본요금 비중이 작다**고 본다 (53세션 4-6). 전력량요금을 먼저 본다."""
    return float(assumption("narrative.base_fee_share_low"))


def peak_month_close() -> float:
    """으뜸 달과 다음 달의 차이가 이 안이면 **한 달이 아니라 계절**로 적는다."""
    return float(assumption("narrative.peak_month_close"))


def surplus_off_day_high() -> float:
    """이 위면 잉여가 **토·일·공휴일에 몰렸다**고 본다 (53세션 3절)."""
    return float(assumption("narrative.surplus_off_day_high"))


def surplus_off_day_low() -> float:
    """이 아래면 잉여가 **평일에 몰렸다**고 본다."""
    return float(assumption("narrative.surplus_off_day_low"))


# ===================================================================== 용어


@dataclass(frozen=True)
class Term:
    """용어 하나 — **이름·산식·의미.**

    화면 툴팁은 산식과 의미를 빈 줄로 나눠 함께 낸다 (21세션 2절). 슬라이드는
    자리가 좁아 **산식만** 깐다 — 의미는 위쪽 해석 한 줄이 이미 말한다.
    """

    name: str
    formula: str
    meaning: str
    short: str = ""
    """슬라이드 각주용 **짧은 산식.** 비면 :attr:`formula` 를 쓴다.

    화면 툴팁은 펼쳐 읽는 자리라 문장으로 적지만, 각주는 한 줄에 셋이 나란히
    앉아야 해서 마침표와 곁문장을 덜어낸다. **화면 문구는 건드리지 않는다.**
    """

    @property
    def line(self) -> str:
        """슬라이드 아래 작은 글씨 한 조각. **한 줄에 셋이 들어가야 한다.**"""
        return f"{self.name} = {self.short or self.formula.rstrip('.')}"

    @property
    def tooltip(self) -> str:
        """화면 툴팁. **산식 한 줄 + 의미 한 줄** (21세션 2절)."""
        return f"{self.formula}\n\n{self.meaning}"


def terms(pattern: LoadPattern | None = None) -> dict[str, Term]:
    """용어 풀이 한 벌.

    **시간대 경계는 자료가 정한다** — :class:`LoadPattern` 이 쓴 값을 그대로
    적는다. 자료가 없으면 기본값으로 적는다 (슬라이드는 늘 그려져야 한다).
    """
    night = pattern.night_hours if pattern is not None else DEFAULT_NIGHT_HOURS
    operating = pattern.operating_hours if pattern is not None else DEFAULT_OPERATING_HOURS
    night_text = f"{night[0]}시{_RANGE}{night[1]}시"
    operating_text = f"평일 {operating[0]}시{_RANGE}{operating[1]}시"
    night_short = f"{night[0]}{_RANGE}{night[1]}시"
    operating_short = f"평일 {operating[0]}{_RANGE}{operating[1]}시"
    return {
        "load_factor": Term(
            "부하율",
            "평균 수요 ÷ 최대 수요.",
            "낮을수록 짧은 피크 하나에 기본요금이 매여 피크 저감 여지가 큽니다.",
            "평균 수요 ÷ 최대 수요",
        ),
        "base_load_ratio": Term(
            "기저부하 비율",
            f"야간({night_text}) 평균 수요 ÷ 주간 평균 수요.",
            "높을수록 밤에도 도는 설비가 있어 ESS 충전 여력이 제한적입니다.",
            f"야간({night_short}) 평균 ÷ 주간 평균",
        ),
        "weekend_ratio": Term(
            "주말 부하 비율",
            "주말 평균 수요 ÷ 평일 평균 수요.",
            "낮을수록 주말 가동이 적어 감축 여지가 큽니다.",
        ),
        "off_hours_energy_share": Term(
            "운영시간 외 부하 비중",
            f"운영시간({operating_text}) 밖 사용량 ÷ 전체 사용량. 주말은 전부 밖입니다.",
            "높을수록 문 닫은 동안 쓰는 전기가 많아 운전 개선 여지가 큽니다.",
            f"운영시간({operating_short}) 밖 사용량 ÷ 전체",
        ),
        # 아래는 화면에서 지표 옆 매뉴얼 앵커가 받던 것들이다. 슬라이드에는
        # 매뉴얼로 보낼 물음표가 없으므로 **산식 한 줄만** 깐다.
        "billing_demand": Term(
            "요금적용전력",
            "기본요금을 매기는 전력. 직전 12개월의 최대수요로 정해집니다.",
            "경부하 시간대의 피크는 대상이 아닙니다.",
            "직전 12개월 최대수요로 매기는 기본요금의 기준 전력",
        ),
        "midday_share": Term(
            "상위 구간 정오 비중",
            "최대수요 상위 구간 가운데 정오 시간대에 든 비율.",
            "높을수록 태양광이 기본요금을 낮출 여지가 큽니다.",
            "상위 구간 중 정오 시간대 비율",
        ),
        "weekend_slot_share": Term(
            "상위 구간 주말 비중",
            "최대수요 상위 구간 가운데 토·일·공휴일에 든 비율.",
            "높으면 쉬는 날의 몇십 분이 1년 기본요금을 정한 것입니다.",
            "상위 구간 중 토·일·공휴일 비율",
        ),
        "base_fee": Term(
            "기본요금",
            "요금적용전력 × 단가 × 개월수.",
            "쓴 양과 무관하게 매달 같은 높이로 붙습니다.",
        ),
        "energy_fee": Term(
            "전력량요금",
            "계절·시간대별 단가 × 사용량.",
            "언제 쓰느냐에 따라 단가가 갈립니다.",
        ),
        "band": Term(
            "계시별",
            "계절과 시간대로 단가를 가르는 구간. 경부하·중간부하·최대부하 셋입니다.",
            "최대부하 비중이 크면 단가가 낮은 시간대로 옮길 여지를 먼저 봅니다.",
            "경부하/중간부하/최대부하로 단가가 갈리는 구간",
        ),
        "certainty": Term(
            "확실성",
            "산출 근거가 얼마나 확정적인지.",
            "요금표만으로 정해지면 높고, 설비·운영에 달렸으면 낮습니다.",
            "산출 근거가 얼마나 확정적인지",
        ),
        # 역률 장의 용어 셋 (53세션 8-1). **화면 툴팁에서 왔다** —
        # ``chart.power_triangle`` 이 유효·무효전력을 설명하고 있었는데,
        # 슬라이드에는 그 물음표를 달 자리가 없어 각주로 깐다.
        "lagging_pf": Term(
            "지상역률",
            "유효전력 ÷ 피상전력. 무효전력이 뒤진(지상) 상태에서 잰 값입니다.",
            "기준 92%를 넘으면 감액, 못 미치면 기본요금에 추가요금이 붙습니다.",
            "유효전력 ÷ 피상전력",
        ),
        "reactive_power": Term(
            "무효전력",
            "일을 하지 않고 계통과 설비 사이를 오가는 전력 (kVar).",
            "많을수록 역률이 낮아져 기본요금에 추가요금이 붙습니다.",
            "일을 하지 않고 계통을 오가는 전력",
        ),
        "active_power": Term(
            "유효전력",
            "실제로 일을 하는 전력 (kW). 요금을 매기는 것은 이쪽입니다.",
            "역률은 이 값이 피상전력에서 차지하는 몫입니다.",
            "실제로 일을 하는 전력",
        ),
        "payback": Term(
            "회수기간",
            "투자비 ÷ 연간 절감액.",
            "운영비와 교체비는 넣지 않은 단순 회수기간입니다.",
        ),
        "self_consumption": Term(
            "자가소비",
            "발전량 가운데 그 시각의 부하가 그대로 쓴 몫.",
            "자가소비분은 계통에서 사 오지 않아 그만큼 요금이 줄어듭니다.",
        ),
        "surplus": Term(
            "잉여",
            "발전이 부하를 넘어 계통으로 되돌아가는 몫.",
            "상계거래 계약과 역송 계량기가 따라옵니다.",
        ),
        "low_load_day": Term(
            "저부하 평일",
            "평일 가운데 운영시간대 부하가 쉬는 날 수준까지 내려온 날.",
            "연간 참여 일수 제한이 없어 이 날 수가 실질 제약입니다.",
        ),
    }


#: 장마다 깔 용어. **그 장에 나오는 것만 고른다** — 전부 깔면 각주가 본문이 된다.
GLOSSARY_KEYS: dict[str, tuple[str, ...]] = {
    "building": (),
    "usage_pattern": ("load_factor", "base_load_ratio", "off_hours_energy_share"),
    "peak_summary": ("billing_demand", "midday_share", "weekend_slot_share"),
    "peak_detail": (),
    "structure": ("base_fee", "energy_fee", "band"),
    "measure_summary": ("payback",),
    "combination": (),
    "appendix": (),
    # **수단 장에도 깐다** (53세션 8-1). 39세션은 진단 장에만 깔아 두어, 역률
    # 장이 「지상역률」 을 세 번 말하면서 그것이 무엇인지는 어디에도 없었다.
    #
    # **없는 장이 셋이다.** 각각 이유가 있다 —
    #     선택요금 전환   기본요금·전력량요금은 7장이 이미 깐다
    #     ESS            표식의 뜻이 표 아래 그 자리를 쓴다 (4-13)
    #     잉여 활용       각주가 이미 제도 요건 세 문장이다
    "measure_contract": ("billing_demand",),
    "measure_demand_response": ("low_load_day",),
    "measure_power_factor": ("lagging_pf", "reactive_power", "active_power"),
    "measure_solar": ("self_consumption", "surplus"),
}


#: 각주에서 **산식을 가르는 기호** (53세션 1-3).
#:
#: 「·」 를 쓰면 산식이 나열되는 자리에서 **수식 기호로 읽힌다** — 「부하율 =
#: 평균 수요 ÷ 최대 수요 · 기저부하 비율 = …」 은 나눗셈 뒤에 곱셈이 붙은
#: 것처럼 보인다. 산식이 아닌 자리의 「·」 는 그대로 둔다.
FORMULA_SEPARATOR = ", "


#: **참고용 작은 글씨 앞에 붙이는 표식** (53세션 1-1).
#:
#: 툴팁에서 옮긴 용어 풀이, 전제·한계 각주, 표 아래 참고 한 줄이 전부 이것을
#: 단다. **그림 캡션은 제외한다** — 캡션은 그림이 무엇인지 말하는 이름이지
#: 참고가 아니다.
#:
#: **여기가 정본이다** (60세션 1절). ``report.slides`` 가 다시 내보내므로
#: 두 이름 다 쓸 수 있지만 값은 한 자리에만 있다.
NOTE_MARK = "※ "


#: 각주 **한 줄 안에서** 조각을 잇는 기호 (60세션 1절).
#:
#: 산식 자리의 :data:`FORMULA_SEPARATOR` 와 다르다 — 이쪽은 「절감액 미산출 —
#: 사유 · 투자비 미산출 — 사유」 처럼 **성격이 같은 조각**을 잇는다.
NOTE_JOIN = " · "


#: 조각이 이미 문장으로 끝났다고 보는 글자 (60세션 2절).
_SENTENCE_END = ".。!?"


def mark_note(line: str) -> str:
    """참고 한 줄에 :data:`NOTE_MARK` 를 붙인다. **이미 붙었으면 그대로 둔다.**"""
    text = line.strip()
    if not text or text.startswith(NOTE_MARK.strip()):
        return text
    return f"{NOTE_MARK}{text}"


def note_line(*parts: str) -> str:
    """각주 **한 줄**을 조립한다 — 이 프로젝트에서 각주를 잇는 유일한 자리다.

    59세션까지는 부르는 쪽이 저마다 ``" · ".join(...)`` 을 했다. 그래서 같은
    결함이 자리마다 따로 났다 (60세션 1·2절).

    셋을 여기서만 한다.

    1. **빈 조각은 버린다.**
    2. **조각이 달고 온 :data:`NOTE_MARK` 는 뗀다.** 표식은 줄 맨 앞의 하나뿐이고
       그것은 :func:`mark_note` 가 단다 — 조각마다 달고 오면 한 줄에 「※」 가
       두 번 선다 (잉여 장이 그랬다).
    3. **앞 조각이 마침표로 끝나면 구분점을 두지 않는다.** 「… 않았습니다**. ·**
       투자비 미산출」 로 겹친다 — 문장이 이미 끝났으므로 빈칸이면 족하다.
       (ESS 성립 불가 장이 그랬다.)

    표식을 붙이지는 **않는다** — 줄로 세울 때 :func:`mark_note` 가 한다.
    """
    kept = [stripped for part in parts if (stripped := _strip_mark(part))]
    if not kept:
        return ""
    line = kept[0]
    for part in kept[1:]:
        join = " " if line[-1] in _SENTENCE_END else NOTE_JOIN
        line = f"{line}{join}{part}"
    return line


def _strip_mark(part: str) -> str:
    """조각 앞의 :data:`NOTE_MARK` 를 뗀다. 줄 맨 앞의 하나만 남기기 위함이다."""
    text = part.strip()
    mark = NOTE_MARK.strip()
    while text.startswith(mark):
        text = text[len(mark) :].strip()
    return text


def glossary_note(keys: tuple[str, ...], pattern: LoadPattern | None = None) -> str:
    """슬라이드 아래 작은 글씨 한 줄. 없으면 빈 문자열이다."""
    table = terms(pattern)
    return FORMULA_SEPARATOR.join(table[key].line for key in keys if key in table)


# ===================================================================== 해석 한 줄


def _pct(value: float | None) -> str:
    return f"{value * 100:,.1f}%" if value is not None else "—"


def building_lead(quality: QualityReport | None) -> str:
    """3장 — **자료가 얼마나 성한가.** 뒤 숫자를 어디까지 믿을지 정하는 자리다.

    **결측률이 높은 달을 이름으로 짚는다** (39세션 6-2). 화면은 그 달의 최대수요를
    그대로 믿기 어렵다고 적는데, 슬라이드에는 총 건수만 있었다.
    """
    if quality is None:
        return "업로드한 15분 실측 사용량으로 계산했습니다. 결측 구간은 보간하지 않았습니다."
    head = (
        f"결측 {_pct(quality.missing_ratio)}를 보간하지 않고 계산에서 뺐습니다."
        if quality.missing_slots
        else "결측이 없어 관측 전 구간으로 계산했습니다."
    )
    # **결측이 없으면 뒷문장이 없다.** 「그 달의 최대수요를 못 믿는다」 는 말은
    # 실제로 빈 달이 있을 때만 적을 말이다.
    heavy = sorted(
        (
            month
            for month in quality.monthly
            if month.missing_slots and month.ratio >= quality.missing_ratio * 2
        ),
        key=lambda month: month.ratio,
        reverse=True,
    )
    if not heavy:
        return head
    worst = heavy[0]
    if len(heavy) == 1:
        return (
            f"{head} {worst.month} 은 {_pct(worst.ratio)}가 비어 그 달의 최대수요는 "
            "낮게 잡혔을 수 있습니다."
        )
    # **여러 달이 높으면 달마다 적지 않는다** (53세션 4-1). 이름을 다 이어 붙이면
    # 해석 한 줄이 세 줄로 흐른다 — 가장 심한 달과 개수만 적는다.
    return (
        f"{head} {worst.month} 등 {len(heavy)}개 달이 크게 비어 그 달들의 "
        "최대수요는 낮게 잡혔을 수 있습니다."
    )


def power_factor_adjusted_saving(*, saving_won: float, extra_won: float) -> str:
    """역률 영향을 반영한 절감액 한 줄 (59세션 12절 · 목록 P6).

    **큰 글자는 조정 전 값이다.** 2단계 카드의 절감액은 「그 수단만 적용했을 때」
    여야 한다 (31세션) — 태양광이 역률을 떨어뜨려 역률요금이 느는 것은 사실이지만
    그것을 큰 글자에 녹이면 독립 평가가 깨진다. **둘을 나눠 보인다.**

    **원 단위로 반올림해 0 이면 빈 글이다** — 없는 조정을 적지 않는다. 발전이
    0 인 점에서도 부동소수 찌꺼기(1e-11 원)가 남아 「역률 영향 반영 시 0원」 이
    섰다.

    금액은 부르는 쪽이 **같은 기준으로** 넘긴다 (둘 다 관측 기간이거나 둘 다
    12개월 환산). 이 함수는 서식만 잡는다.
    """
    if round(extra_won) == 0:
        return ""
    return f"역률 영향 반영 시 {money.won(saving_won - extra_won, reason='—')}"


def solar_saving_breakdown(
    *, self_consumption_won: float, surplus_scenario: str, surplus_revenue_won: float | None
) -> str:
    """태양광 절감액에 **무엇이 들어 있는지** 한 줄 (48세션 · 57세션 · 59세션 5절).

    **화면 툴팁이 이미 쓰던 문장이다.** 57세션에 기준선(출력제어)이 기본이 되면서
    화면은 이 줄을 늘 붙이는데, PPT 태양광 장에는 그 정보가 없었다 — 큰 글씨
    절감액에 잉여 수익이 얹혀 있는데 그 사실이 어디에도 안 적혔다.

    **잉여 장과 겹치지 않는다** (59세션 4절). 잉여 장은 시나리오 표에서
    「어느 것을 골랐나」 를 :data:`~kwise.report.document.SURPLUS_CHOSEN_MARK` 로
    말하고, 이 줄은 **절감액이 무엇의 합인가**를 말한다.

    금액은 부르는 쪽이 **12개월로 환산해서** 넘긴다 — 이 함수는 서식만 잡는다.
    """
    if not surplus_scenario:
        return ""
    added = money.won(surplus_revenue_won, reason="—")
    return (
        f"절감액 = 자가소비로 줄인 요금 {money.won(self_consumption_won, reason='—')} "
        f"+ 잉여 {surplus_scenario} {added}"
    )


def pattern_lead(pattern: LoadPattern) -> str:
    """4장 — **부하율과 기저부하로 두 문장.** 어느 수단을 볼지가 여기서 갈린다."""
    factor = pattern.load_factor
    if factor is None:
        first = "부하율을 산출하지 못했습니다."
    elif factor >= load_factor_flat():
        first = (
            f"부하율 {_pct(factor)}로 하루 내내 고르게 써, 피크 저감보다 "
            "사용량을 줄이는 쪽의 여지가 큽니다."
        )
    else:
        first = (
            f"부하율 {_pct(factor)}로 짧은 피크 하나가 기본요금을 끌어올리고 있어 "
            "피크를 낮출 여지가 큽니다."
        )
    base = pattern.base_load_ratio
    if base is None:
        return first
    if base >= base_load_high():
        second = f"기저부하 비율 {_pct(base)}로 밤에도 설비가 돌아 ESS 충전 여력이 제한적입니다."
    else:
        second = f"기저부하 비율 {_pct(base)}로 밤 부하가 낮아 ESS 충전 여력이 있습니다."
    return f"{first} {second}"


#: 태양광 판정을 문장으로. **판정과 근거 숫자를 함께 적는다** (39세션 1-1).
_PV_LEAD: dict[PvPotential, str] = {
    PvPotential.HIGH: ("피크의 {share}가 낮 시간에 발생해 태양광이 기본요금을 줄일 여지가 큽니다."),
    PvPotential.MEDIUM: (
        "피크의 {share}가 낮 시간에 발생해 태양광이 기본요금을 줄일 여지가 일부 있습니다."
    ),
    PvPotential.LOW: ("피크의 {share}만 낮 시간에 발생해 피크가 태양광 발전 시간과 어긋납니다."),
}


# ========================================================= 판정을 옮기는 자리
#
# **어제 것을 그대로 쓸 수 없다** (72세션 2절). :class:`PeakSource` 가 요구하는
# 것은 ``peak`` 인데 아래 셋이 읽는 것은 ``summary`` 다 — **다른 것이라 새로
# 세운다.** 둘을 다 요구하는 형 하나로 뭉쳐 셋에 돌려 쓰면 좁힌 뜻이 없어진다:
# :func:`peak_summary_lead` 는 ``peak`` 를 읽지 않는다.
#
# **한 겹만 좁힌다** (71세션 판단 그대로). ``summary`` 아래는
# :class:`~kwise.diagnose.ImprovementSummary` 가 그대로 받으므로 더 내려갈
# 이유가 없다 — 짓기 전에 조각 시험으로 확인했다.


class SummarySource(Protocol):
    """:func:`peak_summary_lead`·:func:`measure_summary_lead` 가 읽는 것 — ``summary`` 뿐이다."""

    @property
    def summary(self) -> ImprovementSummary: ...


def peak_summary_lead(diagnosis: SummarySource) -> str:
    """**53세션에 6장으로 옮겼다** — :func:`peak_detail_lead` 를 보라.

    39세션은 이 문장을 5장에 두었는데, 5장 그림은 **월별 최대수요**이고 문장은
    **정오 비중**을 말해 둘이 어긋났다. 판정이 근거로 삼는 그림(상위 구간 시각
    분포)은 6장에 있다. 자리를 옮기고 5장은 :func:`peak_month_lead` 가 받는다.

    부르는 자리가 남아 있을 수 있어 함수는 둔다.
    """
    summary = diagnosis.summary
    share = f"{summary.pv_midday_share * 100:,.0f}%"
    return _PV_LEAD[summary.pv_potential].format(share=share)


#: 계절 이름에 달 범위를 붙일 때 쓰는 꼴 — 「여름(6~8월)」.
_SEASON_SPAN = "{label}({span}월)"


def _season_span(table: TariffTable, season: str) -> str:
    """계절 하나를 「여름(6~8월)」 로 (53세션 4-3③).

    **달 목록을 여기서 짓지 않는다** — 요금표의 계절 정의를 그대로 읽는다
    (``요금 데이터 하드코딩 금지``). 이어진 달이면 범위로, 끊기면 나열한다.
    """
    months = {month for month, key in table.month_seasons.items() if key == season}
    if not months:
        return season_label(season)
    # **한 해를 넘어가는 계절이 있다.** 겨울은 11·12·1·2월이라 그냥 정렬하면
    # 「1·2·11·12월」 이 된다 — 사람은 「11~2월」 로 읽고 쓴다.
    starts = [month for month in months if _previous_month(month) not in months]
    if len(starts) == 1:
        start = starts[0]
        walk = [start]
        while len(walk) < len(months):
            walk.append(_next_month(walk[-1]))
        span = f"{walk[0]}~{walk[-1]}" if set(walk) == months else ""
    else:
        span = ""
    if not span:
        span = "·".join(str(month) for month in sorted(months))
    return _SEASON_SPAN.format(label=season_label(season), span=span)


def _previous_month(month: int) -> int:
    return 12 if month == 1 else month - 1


def _next_month(month: int) -> int:
    return 1 if month == 12 else month + 1


# ============================================================= 5장이 읽는 것
#
# **요구하는 것과 쓰는 것을 맞춘다** (71세션 1절). :func:`peak_month_lead` 가
# 실제로 읽는 것은 진단의 ``peak`` 하나와 품질의 ``flagged_months`` 하나뿐인데,
# 서명은 :class:`~kwise.diagnose.Diagnosis` 와
# :class:`~kwise.quality.QualityReport` 를 통째로 요구했다. 그래서 그 둘만 채운
# 시험 대역이 형에 맞지 않아 ``cast`` 로 눌러야 했다 (70세션 2절).
#
# **시험을 위해 서명을 무르게 하는 것이 아니다** — 함수가 이미 이만큼만 쓴다.
#
# **형 모듈이 아니라 여기 둔다.** 「무엇을 읽는가」 는 부르는 쪽의 사정이고,
# `Diagnosis` 가 자기를 쓰는 함수를 알 이유가 없다.


class PeakSource(Protocol):
    """:func:`peak_month_lead` 가 진단에서 읽는 것 — ``peak`` 뿐이다."""

    @property
    def peak(self) -> PeakProfile: ...


class FlaggedMonthSource(Protocol):
    """:func:`peak_month_lead` 가 품질에서 읽는 것 — ``flagged_months`` 뿐이다."""

    @property
    def flagged_months(self) -> tuple[MonthlyMissing, ...]: ...


def peak_month_lead(
    diagnosis: PeakSource,
    quality: FlaggedMonthSource | None = None,
    table: TariffTable | None = None,
) -> str:
    """5장 — **월별 최대수요 그림이 말하는 것** (53세션 4-3).

    39세션은 이 자리에 정오 비중 판정을 적었는데 **그림은 월별 최대수요**였다.
    그림과 문장이 다른 것을 말하면 읽는 사람이 둘을 잇지 못한다.

    **세 가지로 갈린다.** 어느 것도 이 건물 값에 맞춘 고정 문장이 아니다.

        ② 신뢰 제한   으뜸 달의 결측이 많으면 **그 달을 근거로 쓰지 않는다.**
                     사실을 밝히고 결측이 적은 달 가운데 으뜸을 함께 적는다
        ① 비대상월    3~6·10~11월 피크는 다음 달로 이월되지 않는다. 그러면
                     요금적용전력을 **실제로 정한 달**을 함께 적는다
        ③ 계절       다음 달과 차이가 :func:`peak_month_close` 안이면 한 달을
                     짚지 않는다 — 「여름(6~8월)」 처럼 계절로 적는다

    셋 다 아니면 기본 문장이다. 차례는 **먼저 걸리는 것이 이긴다** — 못 믿을 달을
    두고 계절 이야기를 하는 것은 앞뒤가 바뀐 것이다.
    """
    peak = diagnosis.peak
    demands = peak.monthly["max_demand_kw"].dropna().sort_values(ascending=False)
    if demands.empty:
        return "월별 최대수요를 산출하지 못했습니다."
    top = demands.index[0]
    tail = "이 시기에 요금적용전력이 결정됩니다."

    # ② 신뢰 제한 — **못 믿을 달을 근거로 세우지 않는다.**
    flagged = {month.month for month in quality.flagged_months} if quality is not None else set()
    if top in flagged:
        clean = [month for month in demands.index if month not in flagged]
        head = f"{top.month}월에 최대수요가 가장 높으나 그 달은 결측이 많아 신뢰가 제한됩니다."
        if not clean:
            return f"{head} 다른 달도 사정이 같습니다."
        return f"{head} 결측이 적은 달 가운데는 {clean[0].month}월이 가장 높습니다."

    # ① 대상월이 아니면 그 피크는 이월되지 않는다.
    if top.month not in peak.demand_months:
        basis = peak.monthly["demand_basis_kw"]
        carried = [month for month in basis.index if month.month in peak.demand_months]
        if carried:
            decider = max(carried, key=lambda month: float(basis[month]))
            return (
                f"{top.month}월에 최대수요가 가장 높으나 그 달의 피크는 다음 달로 "
                f"이월되지 않아, 요금적용전력은 {decider.month}월 값으로 결정됩니다."
            )
        return (
            f"{top.month}월에 최대수요가 가장 높으나 그 달의 피크는 다음 달로 "
            "이월되지 않아, 요금적용전력은 그 달에만 적용됩니다."
        )

    # ③ 다음 달과 차이가 작으면 한 달을 짚지 않는다.
    if table is not None and len(demands) > 1:
        second = demands.index[1]
        gap = (float(demands.iloc[0]) - float(demands.iloc[1])) / float(demands.iloc[0] or 1.0)
        season = table.season_of(top.month)
        if gap <= peak_month_close() and table.season_of(second.month) == season:
            return f"{_season_span(table, season)}에 최대수요가 높고, {tail}"
    return f"{top.month}월에 최대수요가 가장 높고, {tail}"


#: 6장 앞문장 — **상위 구간을 왜 보는지.** 화면 그래프 툴팁에 있던 문장이다
#: (``chart.top_hour``). 슬라이드에는 물음표를 달 자리가 없어 본문으로 낸다.
PEAK_DETAIL_LEAD = "최대수요 상위 구간은 낮에 몰리면 태양광이, 밤에 몰리면 ESS 가 피크를 낮춥니다."


class PeakDetailSource(PeakSource, SummarySource, Protocol):
    """:func:`peak_detail_lead` 는 **둘 다** 읽는다 — ``peak.top_n`` 과, 속으로
    부르는 :func:`peak_summary_lead` 가 쓰는 ``summary``.

    **둘을 이어 붙일 뿐 새 요구는 없다.** 앞의 둘을 안 쓰고 여기에 다시 적으면
    같은 것이 두 벌이 되어 한쪽만 고쳐진다.
    """


def peak_detail_lead(diagnosis: PeakDetailSource) -> str:
    """6장 — **설명 뒤에 판정이 온다** (53세션 4-4).

    39세션까지는 「무엇을 보는 그림인가」 만 적고 **이 건물이 어느 쪽인지는 적지
    않았다.** 그림 둘을 보고 읽는 사람이 스스로 세어야 했다.

    뒷문장은 **진단이 이미 내린 판정**을 옮긴다 — 화면 「개선 여지 요약」 의
    「태양광 피크 기여 가능성 높음/보통/낮음」 이고, 39세션이 5장에 두었던
    바로 그 문장이다. 그림(상위 구간 시각 분포)이 여기 있으므로 판정도 여기
    온다. **PPT 가 제 문구를 따로 적지 않는다.**
    """
    head = PEAK_DETAIL_LEAD.replace("상위 구간", f"상위 {diagnosis.peak.top_n}구간", 1)
    return f"{head} {peak_summary_lead(diagnosis)}"


def structure_lead(structure: ChargeStructure) -> str:
    """7장 — **기본요금 비중으로 세 갈래** (53세션 4-6).

    39세션은 둘로만 갈랐다 — 「크다」 아니면 「작다」. 그런데 스물몇 퍼센트인
    건물은 어느 쪽도 아니고, 둘 중 하나를 고르게 하면 **가운데 자료에서 한쪽을
    권하는 문장이 나온다.** 가운데를 가운데라고 적는 갈래를 뒀다.

        낮음   :func:`base_fee_share_low` 미만 — 전력량요금 쪽을 먼저
        가운데  그 사이 — 둘을 같이
        높음   :func:`base_fee_share_high` 초과 — 최대수요를 먼저
    """
    base_won = structure.base_won + structure.bill.total_power_factor_won
    total = structure.total_won
    if not total:
        return "요금 구성을 산출하지 못했습니다."
    share = base_won / total
    if share > base_fee_share_high():
        return f"기본요금이 {_pct(share)}로 큽니다 — 최대수요를 낮추는 방안을 먼저 검토합니다."
    if share >= base_fee_share_low():
        return (
            f"기본요금 {_pct(share)}와 전력량요금이 함께 큽니다 — "
            "피크 저감과 사용량 절감을 같이 검토합니다."
        )
    # **뒷문장을 지침에서 결과로 바꿨다** (59세션 7절). 「단가가 낮은 시간대로
    # 부하를 옮기거나 사용량을 줄이는 방안을 먼저 검토합니다」 는 기본요금
    # 비중이 작다는 앞말에서 곧바로 따라오는 말이라 읽는 사람이 얻는 것이
    # 없었다. **높음 갈래의 거울 문장**으로 바꾼다 — 피크를 낮추는 수단을
    # 앞세우지 말라는 것이 이 갈래의 판단이고, 그 근거가 곧 비중이다.
    #
    # **갈래를 지우지 않는다** (54세션). 뒷문장이 셋을 가르는 자리라 통째로
    # 빼면 셋이 「기본요금은 ○○%입니다」 로 같아진다.
    return (
        f"전체 요금 중 기본요금은 {_pct(share)}이며 나머지는 전력량요금입니다 — "
        "피크를 낮춰도 줄어드는 몫이 작습니다."
    )


#: 투자 없이 되는 수단의 이름. **금액이 붙는 것만 근거로 든다** (53세션 4-7).
_FREE_MEASURE_LABELS: tuple[tuple[str, str], ...] = (
    ("tariff_switch_saving_won", "요금제 전환"),
    ("contract_saving_won", "계약전력 조정"),
)


def measure_summary_lead(diagnosis: SummarySource, saving_text: str) -> str:
    """8장 — **투자 없이 가능한 절감액이 먼저다** (39세션 6-1).

    **근거로 드는 수단이 사실과 같아야 한다** (53세션 4-7). 39세션은 「요금제와
    계약전력 조정입니다」 를 고정으로 적었는데, 대형 자료의 계약전력 조정은
    **0원**이다 — 5,358만원을 낸 것은 요금제 하나뿐인데 둘이 낸 것처럼 읽혔다.

    **0 이거나 미산출인 수단은 근거에서 뺀다.** 여럿이면 절감액 순으로 둘까지만
    적는다 — 셋을 이어 적으면 해석 한 줄이 두 줄로 흐른다.
    """
    summary = diagnosis.summary
    priced = sorted(
        (
            (float(getattr(summary, field) or 0.0), label)
            for field, label in _FREE_MEASURE_LABELS
            if (getattr(summary, field) or 0.0) > 0
        ),
        reverse=True,
    )
    if not priced:
        # **투자 없는 수단이 하나도 절감을 못 내면 문장이 달라야 한다.**
        return (
            "설비 투자 없이 줄일 수 있는 몫은 없습니다 — 현행 요금제와 계약전력이 이미 적정합니다."
        )
    names = " · ".join(label for _won, label in priced[:2])
    return f"설비 투자 없이 {saving_text}을 줄일 수 있습니다 — {names}입니다."


#: 조합 장 — **조합은 다시 계산한다.** 캡션에 있던 사실을 해석 줄로 올렸다.
COMBINATION_LEAD = (
    "조합마다 요금을 처음부터 다시 계산했습니다. "
    "수단을 함께 쓰면 효과가 겹치므로 개별 절감액의 단순 합이 아닙니다."
)

#: 수단이 하나뿐일 때 (53세션 4-14). **겹칠 것이 없다.**
#:
#: 「수단을 함께 쓰면 효과가 겹치므로」 는 조건절이라 거짓은 아니지만, 겹칠
#: 것이 하나도 없는 덱에서 **겹침을 설명하는 것은 없는 이야기를 하는 것**이다.
SINGLE_MEASURE_LEAD = (
    "켠 수단이 하나라 겹치는 효과가 없습니다. 그래도 요금은 처음부터 다시 계산했습니다."
)


def combination_lead(comparison: object | None = None) -> str:
    """조합 장 해석 한 줄. **수단 수로 갈린다** (53세션 4-14).

    ``comparison`` 을 주지 않으면 여러 수단을 전제한 문장을 낸다 — 옛 부름을
    깨지 않으려는 것이고, 슬라이드는 언제나 넘긴다.
    """
    combinations = getattr(comparison, "combinations", None)
    if combinations is not None and len(combinations) <= 2:
        return SINGLE_MEASURE_LEAD
    return COMBINATION_LEAD


#: 부록 — **슬라이드에는 쓰지 않는다** (53세션 1-6).
#:
#: 39세션에 부록마다 이 한 줄을 깔았는데, 부록이 수단마다 한 장 이상으로
#: 늘면서 **같은 문장이 예닐곱 번 되풀이**됐다. 부록은 근거를 펼치는 자리이지
#: 읽는 법을 일러 주는 자리가 아니다. Word 가 쓰면 그때 되살린다.
APPENDIX_LEAD = "각 수단의 절감액이 어떤 산식과 어떤 값에서 나왔는지 그대로 실었습니다."


# ===================================================================== 값이 0인 수단


def dr_lead(profile: DrProfile | None) -> str:
    """경제성DR — **0인 까닭을 적는다** (39세션 4-1).

    「거래 가능일 245일 · 저부하 평일 2일」 만 있으면 왜 2일뿐인지 알 수 없다.
    저부하 판정은 **쉬는 날 수준까지 내려온 평일**을 세는 것이므로, 그런 날이
    없다는 것이 곧 「추가로 줄일 여지가 없다」 는 뜻이다.
    """
    if profile is None:
        return ""
    if not profile.low_load_days:
        return (
            f"거래 가능일 {profile.eligible_days:,}일 가운데 부하가 쉬는 날 수준까지 "
            "내려오는 평일이 없어 추가로 줄일 여지가 없습니다."
        )
    # **「245일 가운데 245일만」 은 성립하지 않는다** (53세션 4-10). 평탄한 부하
    # (C3)에서는 거래 가능일이 전부 저부하로 잡힌다 — 「만」 은 적다는 뜻이라
    # 사실과 반대로 읽힌다.
    if len(profile.low_load_days) >= profile.eligible_days:
        return (
            f"거래 가능일 {profile.eligible_days:,}일 전부가 부하가 쉬는 날 수준까지 "
            "내려옵니다 — 어느 날에도 감축을 입찰할 수 있습니다."
        )
    return (
        f"거래 가능일 {profile.eligible_days:,}일 가운데 "
        f"{len(profile.low_load_days):,}일만 부하가 쉬는 날 수준까지 내려옵니다 — "
        "그 날에만 감축을 입찰할 수 있습니다."
    )


def surplus_page_lead(*, capacity_kwp: float, total_kwh: float, off_day_share: float | None) -> str:
    """잉여 활용 장 — **얼마가 언제 남는가** (53세션 3-2).

    뒷문장이 **비중으로 갈린다.** 소형 사무빌딩은 휴일이 99.6% 라 「대부분
    토·일·공휴일」 이 맞지만, 평일 낮에 문을 닫는 건물이 아니면 반대가 된다.
    **고정 문장으로 박으면 다른 건물에서 거짓이 된다.**

    갈림값은 :func:`surplus_off_day_high` · :func:`surplus_off_day_low` 다.
    """
    head = f"태양광 {capacity_kwp:,.0f} kWp 에서 연 {total_kwh:,.0f} kWh 가 남습니다."
    if off_day_share is None:
        return head
    if off_day_share >= surplus_off_day_high():
        return f"{head} 대부분 토·일·공휴일에 발생합니다."
    if off_day_share <= surplus_off_day_low():
        return f"{head} 대부분 평일에 발생합니다."
    return f"{head} 평일과 토·일·공휴일에 고르게 발생합니다."


def surplus_lead(
    *,
    total_kwh: float,
    generation_kwh: float,
    self_consumed_kwh: float,
    surplus_free_kwp: float | None,
) -> str:
    """잉여 활용 — **0이라는 것 자체가 정보다** (39세션 4-3).

    다만 왜 0인지가 있어야 한다. 샘플은 전부 자가소비다 — 낮 최저 부하가 발전
    최대 출력보다 훨씬 커서 한 구간도 역송이 나지 않는다.
    """
    if total_kwh > 0:
        return (
            f"발전량 {generation_kwh / 1000:,.0f} MWh 가운데 "
            f"{total_kwh / 1000:,.0f} MWh 가 자가소비하고 남아 계통으로 돌아갑니다."
        )
    head = (
        f"발전량 {generation_kwh / 1000:,.0f} MWh 전부가 자가소비되어 계통으로 "
        "내보낼 잉여가 없습니다."
    )
    if surplus_free_kwp is not None and surplus_free_kwp > 0:
        head += f" 잉여가 생기려면 {surplus_free_kwp:,.0f} kWp 이상이 필요합니다."
    return head
