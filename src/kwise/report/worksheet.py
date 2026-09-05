"""계산 근거 — **산식과 대입값을 나란히** (22세션 2절).

숫자만 내면 믿을지 말지를 판단할 수 없고, 산식만 적으면 이 자료에서 무엇이
들어갔는지 알 수 없다. **둘을 같은 줄에 둔다.**

    기본요금   5,293.4 kW × 8,320 원/kW × 12.07개월   531,478,000원

**LaTeX 을 쓰지 않는다.** 화면(Streamlit)·Excel·Word 셋이 같은 것을 보여야
하므로 표 하나로 낸다 — 구분 · 산식 · 값 세 열이다. :meth:`Worksheet.frame` 이
표를, :meth:`Worksheet.lines` 가 같은 내용의 텍스트를 낸다.

**여기서 계산하지 않는다.** 이미 나온 결과 객체의 값을 배치할 뿐이다 — 산식을
다시 적으면 화면의 숫자와 근거의 숫자가 갈라진다. 값이 없으면 그 줄을 만들지
않는다 (0 으로 채우지 않는다).

19세션의 **근거(BASIS) 등급 문구는 여기서 다시 만들지 않는다.** 카드가 툴팁에
이미 내고 있고, 보고서 부록 A 가 같은 문구를 이 표 옆에 싣는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from kwise import money
from kwise.measures.contract import NO_SAVING, ContractAdjustment
from kwise.measures.demand_response import DemandResponseResult
from kwise.measures.ess import EssResult, payback_text
from kwise.measures.power_factor import PowerFactorResult
from kwise.measures.solar import SolarCurve, SolarPoint
from kwise.measures.tariff_switch import TariffSwitchResult
from kwise.tariff import BillingResult
from kwise.tariff.labels import option_label

__all__ = [
    "COLUMNS",
    "WorkRow",
    "Worksheet",
    "combination_worksheet",
    "contract_worksheet",
    "demand_response_worksheet",
    "ess_worksheet",
    "power_factor_worksheet",
    "solar_worksheet",
    "tariff_switch_worksheet",
]

#: 표의 열 이름. **셋이 같은 이름을 쓴다** — 화면·Excel·Word.
COLUMNS: tuple[str, str, str] = ("구분", "산식", "값")

_INDENT = "  "


@dataclass(frozen=True)
class WorkRow:
    """근거 한 줄. 산식이 없으면 값만 있는 줄이다 (소계·합계)."""

    label: str
    formula: str = ""
    value: str = ""
    level: int = 0
    """들여쓰기. 계시별 사용량처럼 소계 아래 붙는 줄이 1 이다."""
    total: bool = False
    """합계 줄인가. 텍스트로 낼 때 앞에 구분선을 둔다."""


@dataclass(frozen=True)
class Worksheet:
    """수단 하나의 계산 근거."""

    key: str
    """수단 등록 키. 보고서 부록 A 가 이 키로 묶는다."""
    title: str
    rows: tuple[WorkRow, ...] = field(default=())

    def __bool__(self) -> bool:
        return bool(self.rows)

    def frame(self) -> pd.DataFrame:
        """구분 · 산식 · 값. **화면과 Excel 이 이것을 그대로 쓴다.**"""
        return pd.DataFrame(
            [
                {
                    COLUMNS[0]: f"{_INDENT * row.level}{row.label}",
                    COLUMNS[1]: row.formula,
                    COLUMNS[2]: row.value,
                }
                for row in self.rows
            ],
            columns=list(COLUMNS),
        )

    def lines(self) -> tuple[str, ...]:
        """같은 내용의 텍스트. Word 본문과 툴팁이 쓴다."""
        width = max((len(f"{_INDENT * row.level}{row.label}") for row in self.rows), default=0)
        out: list[str] = []
        for row in self.rows:
            label = f"{_INDENT * row.level}{row.label}".ljust(width)
            middle = f"  {row.formula}" if row.formula else ""
            tail = f"  = {row.value}" if row.formula and row.value else f"  {row.value}"
            out.append(f"{label}{middle}{tail}".rstrip())
        return tuple(out)


def _won(value: float | None) -> str:
    return money.won(value, reason="미산출")


def _kw(value: float | None, *, decimals: int = 1) -> str:
    return "미산출" if value is None else f"{value:,.{decimals}f} kW"


def _kwh(value: float | None, *, decimals: int = 0) -> str:
    return "미산출" if value is None else f"{value:,.{decimals}f} kWh"


# --------------------------------------------------------------------- 7.1


def _option(bill: BillingResult) -> str:
    """``일반용(을) 고압A 선택Ⅱ`` — **사람 말로 적는다** (12세션 규약)."""
    return f"{bill.contract_label} {bill.voltage_label} {option_label(bill.selection.option)}"


def _base_fee_row(bill: BillingResult, label: str = "기본요금") -> WorkRow:
    """기본요금 = 요금적용전력 × 단가 × 개월수. **셋이 다 결과 객체에 있다.**"""
    return WorkRow(
        label,
        f"{bill.billing_demand_kw:,.1f} kW × {bill.base_rate_won_per_kw:,.0f} 원/kW"
        f" × {bill.base_fee_months:,.2f}개월",
        _won(bill.total_base_won),
    )


def _energy_rows(bill: BillingResult) -> list[WorkRow]:
    """계시별 사용량과 전력량요금.

    **단가는 적지 않는다.** 계절·시간대마다 달라 한 줄로 적을 수 없고, 여기서
    다시 곱하면 요금 엔진과 갈라진다. 사용량과 합계를 나란히 둔다.
    """
    monthly = bill.monthly
    rows: list[WorkRow] = [WorkRow("전력량요금", "계절·시간대별 단가 × 사용량", "")]
    for column, name in (
        ("light_kwh", "경부하"),
        ("mid_kwh", "중간부하"),
        ("peak_kwh", "최대부하"),
    ):
        if column in monthly.columns:
            rows.append(WorkRow(name, "", _kwh(float(monthly[column].sum())), level=1))
    rows.append(WorkRow("소계", "", _won(bill.total_energy_won), level=1))
    return rows


def _bill_rows(bill: BillingResult, *, title: str) -> list[WorkRow]:
    rows = [WorkRow(title, "", ""), _base_fee_row(bill), *_energy_rows(bill)]
    if bill.total_power_factor_won:
        rows.append(
            WorkRow("역률 요금", "기본요금 × 역률 조정률", _won(bill.total_power_factor_won))
        )
    # **부가금이 있으면 반드시 선다** (109세션). 없으면 합계가 위 행들의 합과
    # 어긋나 계산 근거가 그 자리에서 거짓이 된다 — 교육용(갑) 300 kW 조건에서
    # 「기본 399,521,000 + 전력량 2,223,326,000」 인데 합계가 3,843,956,000 이었다.
    # 0원이면 안 세운다 (역률 요금 행과 같은 틀이다).
    if bill.total_excess_won:
        rows.append(
            WorkRow(
                "초과사용부가금",
                f"초과 {len(bill.excess.charged_months)}개 월 × 기본요금 단가 × 배수",
                _won(bill.total_excess_won),
            )
        )
    rows.append(WorkRow("합계", "", _won(bill.total_won), total=True))
    return rows


def tariff_switch_worksheet(result: TariffSwitchResult) -> Worksheet:
    """7.1 선택요금 전환 — **요금제마다 기본·전력량을 갈라 적는다.**"""
    rows: list[WorkRow] = []
    if result.current_bill is not None:
        rows.extend(_bill_rows(result.current_bill, title=f"현행 {_option(result.current_bill)}"))
    if result.best_bill is not None and result.best != result.current:
        rows.append(WorkRow("", "", ""))
        rows.extend(_bill_rows(result.best_bill, title=f"최적 {_option(result.best_bill)}"))
    shown = {str(result.current.selection), str(result.best.selection)}
    for quote in result.quotes:
        if str(quote.selection) in shown:
            continue
        # **부가금이 있으면 항을 더한다** (109세션). 안 적으면 이 줄에서만
        # 「기본 + 전력량」 이 합계와 안 맞는다.
        formula = (
            f"기본 {money.won_plain(quote.base_won, reason='—')}"
            f" + 전력량 {money.won_plain(quote.energy_won, reason='—')}"
        )
        if quote.excess_won:
            formula += f" + 부가금 {money.won_plain(quote.excess_won, reason='—')}"
        rows.append(
            WorkRow(f"참고 {option_label(quote.selection.option)}", formula, _won(quote.total_won))
        )
    if rows:
        rows.append(WorkRow("절감액", "현행 합계 − 최적 합계", _won(result.saving_won), total=True))
    return Worksheet("tariff_switch", "선택요금 전환 계산 근거", tuple(rows))


# --------------------------------------------------------------------- 7.2


def contract_worksheet(result: ContractAdjustment) -> Worksheet:
    """7.2 계약전력 조정 — **하한 판정과 요금 차액.**

    **차액의 상대가 갈래마다 다르다** (100세션). 같은 종별 안에서 낮추면
    기본요금 둘의 차이지만, 종별을 넘으면 전력량요금 단가까지 바뀌므로
    **총액 둘의 차이**다. 산식 칸에 앞엣것만 적어 두면 종별 갈래에서
    「현재 − 조정 후」 가 값과 어긋난다 — 소형 을 300 kW 에서
    22,642,000 − 25,809,000 이 21,810,000 이라고 적혀 있었다.
    """
    ratio = result.contract_floor_ratio
    rows: list[WorkRow] = [
        WorkRow("현재 계약전력", "", _kw(result.contract_kw, decimals=0)),
        WorkRow("최대수요", "직전 12개월 최대 (하한 적용 전)", _kw(result.demand_before_floor_kw)),
    ]
    if ratio is not None and result.floor_kw is not None:
        # **걸린 달을 적는다** (105세션 5절 · ②-13). 앞서는 「걸린다/걸리지
        # 않는다」 를 **연간 최대**로만 갈랐다 — 굴림 창을 못 채운 초기 달에만
        # 걸리는 판에서 근거표가 「걸리지 않는다」 라 적고 바로 아랫줄에
        # 목표와 절감액을 적었다. 세는 자리는 요금 안내와 같다.
        bound = len(result.floor_bound_months)
        rows.append(
            WorkRow(
                f"하한 판정 ({ratio:.0%})",
                f"계약전력 {result.contract_kw:,.0f} kW × {ratio:.0%}",
                f"{result.floor_kw:,.1f} kW"
                + (f" — {bound}개 월에 걸린다" if bound else " — 어느 달에도 안 걸린다"),
            )
        )
        rows.append(
            WorkRow(
                "목표 계약전력",
                f"{result.crossed_label} 문턱 바로 아래"
                if result.crosses_type
                else f"가장 작은 달 ÷ {ratio:.0%}",
                _kw(result.target_contract_kw, decimals=0)
                if result.target_contract_kw is not None
                else NO_SAVING,
            )
        )
    if result.crosses_type:
        rows.append(WorkRow("현행 종별 총 요금", "", _won(result.current_total_won)))
        rows.append(WorkRow(f"{result.crossed_label} 총 요금", "", _won(result.crossed_total_won)))
        rows.append(WorkRow("절감액", "현행 − 바뀐 종별", _won(result.saving_won), total=True))
    elif result.current_base_won is not None and result.adjusted_base_won is not None:
        rows.append(WorkRow("현재 기본요금", "", _won(result.current_base_won)))
        rows.append(WorkRow("조정 후 기본요금", "", _won(result.adjusted_base_won)))
        # **두 줄의 차가 절감액이 아니다** (S124 · ②-41). ``saving_won`` 은 역률요금
        # 몫까지 담는다 (S116 · ⑭ — 역률요금은 기본요금에 대한 비율이라 기본요금이
        # 줄면 함께 준다, 약관 제43조 ②). 시트 이름이 「계산 근거」 인데 **그 시트로
        # 되짚을 수 없었다** — large-b-over 에 역률 85% 를 걸면 519,840,000 −
        # 452,804,000 = 67,036,000 인데 절감액은 67,973,000원이라 937,000원이 뜬다.
        # 몫을 한 줄로 세우면 세 줄이 산수로 맞는다. **역률요금이 0원인 자료(약관
        # 제42조 간주 92%)에서는 줄이 안 생기므로 지금 벌의 줄 수는 그대로다.**
        base_cut = result.current_base_won - result.adjusted_base_won
        power_factor_cut = None if result.saving_won is None else result.saving_won - base_cut
        formula = "현재 − 조정 후"
        if power_factor_cut:
            rows.append(
                WorkRow("역률요금 절감", "기본요금이 줄면 함께 준다", _won(power_factor_cut))
            )
            formula = "현재 − 조정 후 + 역률요금 절감"
        # **0원과 「없음」 을 가른다** (S124 · ②-27). 낮출 자리가 없어 줄 것이
        # 없는 것(:attr:`~kwise.measures.contract.ContractAdjustment.no_saving`)은
        # 계산해서 0원이 나온 것과 다르다 — 같은 시트의 「목표 계약전력」 이 이미
        # 그 어휘를 쓰고 있었는데 **절감액 줄만 0원으로 남아 있었다.**
        rows.append(
            WorkRow(
                "절감액",
                formula,
                NO_SAVING if result.no_saving else _won(result.saving_won),
                total=True,
            )
        )
    return Worksheet("contract", "계약전력 조정 계산 근거", tuple(rows))


# --------------------------------------------------------------------- 7.3


def demand_response_worksheet(result: DemandResponseResult) -> Worksheet:
    """7.3 경제성DR — **기준선 → 문턱 → 저부하일 → 감축량.**"""
    baseline = result.weekend_baseline_kw
    threshold = result.low_load_threshold_kw
    multiple = (
        f"기준선 × {threshold / baseline:,.2g}"
        if baseline and threshold is not None
        else "기준선 × 배수"
    )
    rows: list[WorkRow] = [
        WorkRow("기준선", "주말·공휴일 판정 시간대 평균", _kw(baseline)),
        WorkRow("저부하 문턱", multiple, _kw(threshold)),
        WorkRow(
            "저부하 평일", f"거래 가능일 {result.eligible_days}일 중", f"{result.low_load_days}일"
        ),
        WorkRow("평일 정상 평균", "", _kw(result.normal_weekday_mean_kw)),
        WorkRow(
            "등록 권장 용량", "저부하일 여력 분포의 하위값", _kw(result.registered_capacity_kw)
        ),
        WorkRow(
            "감축 가능량",
            f"Σ(저부하일 여력 × 참여 시간 {result.participation_hours:,.0f}시간)",
            _kwh(result.period_reducible_kwh),
        ),
        WorkRow("연간 환산", "관측 기간 → 365일", _kwh(result.annual_reducible_kwh)),
    ]
    if result.unit_price_won_per_kwh is not None:
        rows.append(
            WorkRow(
                "정산금",
                f"{result.annual_reducible_kwh:,.0f} kWh × "
                f"{result.unit_price_won_per_kwh:,.0f} 원/kWh",
                _won(result.settlement_won),
                total=True,
            )
        )
    return Worksheet("demand_response", "경제성DR 계산 근거", tuple(rows))


# --------------------------------------------------------------------- 7.4


def power_factor_worksheet(result: PowerFactorResult) -> Worksheet:
    """7.4 역률 개선 — **92% 기준 대비 조정률.**"""
    rows = [
        WorkRow("현재 역률", "주간(08~22시) 지상", f"{result.current_pct:,.1f}%"),
        WorkRow("목표 역률", "", f"{result.target_pct:,.1f}%"),
        WorkRow(
            "조정률",
            "기준 92% 대비 매 1%당 기본요금의 0.2%",
            f"{(result.target_pct - result.current_pct) * 0.2:+,.1f}%p",
        ),
        WorkRow("현재 역률 요금", "", _won(result.current_charge_won)),
        WorkRow("목표 역률 요금", "", _won(result.target_charge_won)),
        WorkRow("절감액", "현재 − 목표 (요금 재계산)", _won(result.saving_won), total=True),
    ]
    return Worksheet("power_factor", "역률 개선 계산 근거", tuple(rows))


# --------------------------------------------------------------------- 7.5


def solar_worksheet(curve: SolarCurve, point: SolarPoint | None = None) -> Worksheet:
    """7.6 태양광 — **용량 → 발전량 → 절감액.**"""
    best = point if point is not None else curve.verdict().best
    if best is None:
        return Worksheet("solar", "태양광 계산 근거")
    rows = [
        WorkRow("설치 용량", "", f"{best.capacity_kwp:,.0f} kWp"),
        WorkRow("연간 발전량", "기상 자료 × 용량", _kwh(best.generation_kwh)),
        WorkRow(
            "자가소비",
            f"자가소비율 {best.self_consumption_ratio or 0:.0%}",
            _kwh(best.self_consumed_kwh),
            level=1,
        ),
        WorkRow("잉여", "", _kwh(best.surplus_kwh), level=1),
        WorkRow("기본요금 절감", "요금적용전력 저감 × 단가", _won(best.base_saving_won)),
        WorkRow("전력량요금 절감", "자가소비 × 계시별 단가", _won(best.energy_saving_won)),
        WorkRow("절감액", "기본 + 전력량", _won(best.total_saving_won), total=True),
    ]
    if best.investment_won is not None:
        rows.append(WorkRow("투자비", "용량 × kWp당 단가", _won(best.investment_won)))
    if best.payback_years is not None:
        rows.append(
            WorkRow("회수기간", "투자비 ÷ 12개월 환산 절감액", f"{best.payback_years:,.1f}년")
        )
    return Worksheet("solar", "태양광 계산 근거", tuple(rows))


# --------------------------------------------------------------------- 7.6


def ess_worksheet(result: EssResult) -> Worksheet:
    """7.6 ESS — **필요 사양과 투자비 계수.**"""
    excess = result.excess
    quote = result.quote
    rows = [
        WorkRow(
            "하루 최대 초과 에너지",
            "목표 초과분의 일별 최대",
            _kwh(excess.max_daily_excess_kwh, decimals=1),
        ),
        WorkRow("최대 초과 출력", "", _kw(excess.max_excess_kw)),
        # **필요 사양과 규격 사양을 나란히 낸다** (50세션 3-2). 표에 나가는 것은
        # 규격이고, 그것이 왜 그 값인지는 필요 사양과 견주어야 읽힌다.
        WorkRow("필요 출력", "", _kw(result.required_power_kw or result.power_kw)),
        WorkRow(
            "필요 용량",
            "정격 = 내보낼 에너지 ÷ √왕복효율 ÷ DoD",
            _kwh(result.required_capacity_kwh or result.capacity_kwh, decimals=1),
        ),
        WorkRow("규격 출력", "살 수 있는 PCS 로 올림", _kw(result.power_kw)),
        WorkRow(
            "규격 용량",
            f"살 수 있는 배터리로 올림 · 방전시간 {result.discharge_hours:,.2f}h",
            _kwh(result.capacity_kwh, decimals=1),
        ),
    ]
    if quote is not None:
        rows.extend(
            [
                WorkRow(
                    "설비비",
                    quote.formula if hasattr(quote, "formula") else "도입 사례 회귀",
                    _won(quote.equipment_won),
                ),
                WorkRow("전기공사", "옥외 기준 구간의 대표값", _won(quote.electrical_won)),
                WorkRow(
                    "투자비",
                    f"설비 + 전기공사 · 단가 {result.pricing_path}",
                    _won(quote.total_won),
                    total=True,
                ),
            ]
        )
    elif result.investment_won is not None:
        rows.append(WorkRow("투자비", "출력 × kW당 단가", _won(result.investment_won), total=True))
    rows.extend(
        [
            WorkRow("기본요금 절감", "요금적용전력 저감 × 단가", _won(result.base_saving_won)),
            WorkRow("전력량요금 절감", "충·방전 단가차", _won(result.energy_saving_won)),
            WorkRow("절감액", "요금 재계산 차액", _won(result.total_saving_won), total=True),
        ]
    )
    if result.payback_years is not None:
        rows.append(
            WorkRow(
                "회수기간",
                "투자비 ÷ 12개월 환산 절감액",
                payback_text(result.payback_years),
            )
        )
    return Worksheet("ess", "ESS 계산 근거", tuple(rows))


# --------------------------------------------------------------------- 3단계


def combination_worksheet(
    *,
    simple_won: float,
    combined_won: float,
    reasons: tuple[str, ...] = (),
    contract_extra_won: float | None = None,
) -> Worksheet:
    """3단계 합산효과 — **단순 합과의 차이, 그리고 그 이유.**

    22세션에 화면 본문에서 이리로 옮겼다 (예산 6줄 → 한도 안). 이유 세 줄은
    「왜 단순 합과 다른가」 의 산출 근거라 여기가 제자리다.
    """
    rows = [
        WorkRow("단순 합", "개선안별 절감액의 합", _won(simple_won)),
        WorkRow("합산효과", "조합 부하로 요금을 다시 계산", _won(combined_won)),
        WorkRow("차이", "합산효과 − 단순 합", _won(combined_won - simple_won), total=True),
    ]
    for index, reason in enumerate(reasons, start=1):
        rows.append(WorkRow(f"이유 {index}", "", reason, level=1))
    if contract_extra_won:
        rows.append(
            WorkRow("계약전력 추가 하향", "조합 부하 기준 재산정", _won(contract_extra_won))
        )
    return Worksheet("combination", "합산효과 계산 근거", tuple(rows))
