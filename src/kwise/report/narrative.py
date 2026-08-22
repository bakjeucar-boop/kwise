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

from kwise.diagnose import ChargeStructure, Diagnosis
from kwise.diagnose.dr import DrProfile
from kwise.diagnose.summary import PvPotential
from kwise.quality import DEFAULT_NIGHT_HOURS, DEFAULT_OPERATING_HOURS, LoadPattern, QualityReport
from kwise.rules import assumption

__all__ = [
    "GLOSSARY_KEYS",
    "Term",
    "base_fee_share_high",
    "base_load_high",
    "building_lead",
    "combination_lead",
    "dr_lead",
    "glossary_note",
    "load_factor_flat",
    "measure_summary_lead",
    "pattern_lead",
    "peak_detail_lead",
    "peak_summary_lead",
    "structure_lead",
    "surplus_lead",
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
    """이 위면 **밤에도 부하가 남는다**고 본다. ESS 충전 여력이 좁다."""
    return float(assumption("narrative.base_load_high"))


def base_fee_share_high() -> float:
    """이 위면 **기본요금 비중이 크다**고 본다. 피크 저감의 값어치가 크다."""
    return float(assumption("narrative.base_fee_share_high"))


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
            "높을수록 밤에도 도는 설비가 있어 ESS 충전 여력이 좁습니다.",
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
    "measure_summary": ("payback", "certainty"),
    "combination": (),
    "appendix": (),
}


def glossary_note(keys: tuple[str, ...], pattern: LoadPattern | None = None) -> str:
    """슬라이드 아래 작은 글씨 한 줄. 없으면 빈 문자열이다."""
    table = terms(pattern)
    return " · ".join(table[key].line for key in keys if key in table)


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
    worst = max(
        (month for month in quality.monthly if month.missing_slots),
        key=lambda month: month.ratio,
        default=None,
    )
    head = (
        f"결측 {_pct(quality.missing_ratio)}를 보간하지 않고 계산에서 뺐습니다."
        if quality.missing_slots
        else "결측이 없어 관측 전 구간으로 계산했습니다."
    )
    if worst is not None and worst.ratio >= quality.missing_ratio * 2:
        head += (
            f" {worst.month} 은 {_pct(worst.ratio)}가 비어 그 달의 최대수요는 "
            "낮게 잡혔을 수 있습니다."
        )
    return head


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
        second = f"기저부하 비율 {_pct(base)}로 밤에도 설비가 돌아 ESS 충전 여력은 좁습니다."
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


def peak_summary_lead(diagnosis: Diagnosis) -> str:
    """5장 — **진단이 이미 내린 판정을 문장으로 바꾼다** (39세션 1-1).

    화면 「개선 여지 요약」 의 「태양광 피크 기여 가능성 높음/보통/낮음」 이
    그것이고, 근거 숫자가 바로 아래 지표의 정오 비중이다.
    """
    summary = diagnosis.summary
    share = f"{summary.pv_midday_share * 100:,.0f}%"
    return _PV_LEAD[summary.pv_potential].format(share=share)


#: 6장 — **상위 구간을 왜 보는지.** 화면 그래프 툴팁에 있던 문장이다
#: (``chart.top_hour``). 슬라이드에는 물음표를 달 자리가 없어 본문으로 낸다.
PEAK_DETAIL_LEAD = (
    "연간 최대수요 상위 구간이 하루 중 언제 발생했는지 봅니다. "
    "낮에 몰리면 태양광이, 밤에 몰리면 ESS 가 피크를 낮출 수단입니다."
)


def peak_detail_lead(diagnosis: Diagnosis) -> str:
    """6장 — 고정 문장에 **구간 수만** 채운다."""
    return PEAK_DETAIL_LEAD.replace("상위 구간", f"상위 {diagnosis.peak.top_n}구간", 1)


def structure_lead(structure: ChargeStructure) -> str:
    """7장 — **기본요금 비중으로 갈린다.** 피크 저감의 값어치가 여기서 정해진다."""
    base_won = structure.base_won + structure.bill.total_power_factor_won
    total = structure.total_won
    if not total:
        return "요금 구성을 산출하지 못했습니다."
    share = base_won / total
    if share >= base_fee_share_high():
        return f"요금의 {_pct(share)}가 기본요금이라 피크를 낮추는 수단의 값어치가 큽니다."
    return (
        f"요금의 {_pct(share)}만 기본요금이고 나머지는 쓴 양에 붙습니다 — "
        "단가가 낮은 시간대로 옮기거나 사용량을 줄이는 쪽을 먼저 봅니다."
    )


def measure_summary_lead(diagnosis: Diagnosis, saving_text: str) -> str:
    """8장 — **투자 없이 가능한 절감액이 먼저다** (39세션 6-1).

    화면 1단계 맨 위에 있는 숫자인데 슬라이드에 없었다. 고객이 가장 먼저 보고
    싶은 값이고, 개선 수단이 처음 나오는 이 장이 그 자리다.
    """
    summary = diagnosis.summary
    if summary.no_investment_saving_won > 0:
        return f"설비 투자 없이 {saving_text}을 줄일 수 있습니다 — 요금제와 계약전력 조정입니다."
    return "설비 투자 없이 줄일 수 있는 몫은 없습니다 — 현행 요금제와 계약전력이 이미 적정합니다."


#: 16장 — **조합은 다시 계산한다.** 캡션에 있던 사실을 해석 줄로 올렸다.
COMBINATION_LEAD = (
    "조합마다 요금을 처음부터 다시 계산했습니다. "
    "수단을 함께 쓰면 효과가 겹치므로 개별 절감액의 단순 합이 아닙니다."
)


def combination_lead() -> str:
    return COMBINATION_LEAD


#: 17장 — 고정 문장.
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
    return (
        f"거래 가능일 {profile.eligible_days:,}일 가운데 "
        f"{len(profile.low_load_days):,}일만 부하가 쉬는 날 수준까지 내려옵니다 — "
        "그 날에만 감축을 입찰할 수 있습니다."
    )


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
