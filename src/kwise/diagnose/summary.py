"""개선 여지 요약 (요구사항서 6.5).

진단 화면 최상단에 표시한다. **사용자가 처음 보는 숫자다.**

    선택요금 전환    연 ○○○만원      투자 불필요
    계약전력 조정    연 ○○○만원      투자 불필요
    태양광 검토      피크 기여 가능성 높음 / 보통 / 낮음

태양광 등급은 상위 100구간의 시각 분포로 판정한다. 오전·정오 집중이면 PV 기여가
크고, 저녁·아침 집중이면 거의 없다. 건물마다 정반대 결과가 나오므로 도구가
판별해 주어야 한다. **설비 정보 없이 나오는 판정이다.**

**모집단은 요금적용전력 대상 슬롯(중간·최대부하)이다.** 경부하 구간은 아무리 큰
수요가 나도 요금적용전력이 되지 않으므로, 그 시각의 피크를 판정에 넣으면 태양광의
기본요금 기여를 잘못 읽는다 (요구사항서 5.2 ①). 야간 피크형 건물에서는 마스크
적용 여부로 등급이 뒤바뀐다. 부록 B 의 시각 분포는 전 슬롯 기준의 원값이며
:attr:`PeakProfile.hour_counts` 에 따로 남아 있다 — 두 값을 섞지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from kwise.diagnose.peak import PeakProfile
from kwise.tariff import TariffSelection

__all__ = [
    "DEFAULT_HIGH_SHARE",
    "DEFAULT_MEDIUM_SHARE",
    "MIDDAY_HOURS",
    "ImprovementSummary",
    "PvPotential",
    "judge_pv_potential",
    "pv_basis_label",
]

# 태양광이 실제로 힘을 쓰는 시간대. 라벨 기준 10:15~15:00 구간이 여기 든다.
MIDDAY_HOURS = range(10, 15)
DEFAULT_HIGH_SHARE = 0.50
DEFAULT_MEDIUM_SHARE = 0.25


class PvPotential(StrEnum):
    """태양광 피크 기여 가능성."""

    HIGH = "높음"
    MEDIUM = "보통"
    LOW = "낮음"


def judge_pv_potential(
    peak: PeakProfile,
    *,
    midday_hours: range = MIDDAY_HOURS,
    high_share: float = DEFAULT_HIGH_SHARE,
    medium_share: float = DEFAULT_MEDIUM_SHARE,
) -> tuple[PvPotential, float]:
    """상위 구간의 정오 집중도로 등급을 매긴다.

    모집단은 **요금적용전력 대상 슬롯**이다 (:meth:`PeakProfile.demand_hour_share`).
    마스크를 받지 않은 :class:`PeakProfile` 은 전 슬롯이 그대로 모집단이 된다.

    Returns:
        (등급, 정오 시간대 비율).
    """
    share = peak.demand_hour_share(midday_hours)
    if share >= high_share:
        return PvPotential.HIGH, share
    if share >= medium_share:
        return PvPotential.MEDIUM, share
    return PvPotential.LOW, share


def pv_basis_label(peak: PeakProfile) -> str:
    """등급 판정에 쓴 **모집단**을 한 줄로 적는다. 산출물에 그대로 싣는다."""
    counted = len(peak.demand_top_slots)
    if not peak.demand_eligible_applied:
        return (
            f"관측 전 슬롯 {peak.observed_slots:,}개 중 상위 {counted}구간 기준. "
            "요금적용전력 대상 시간대 마스크를 받지 않아 경부하 구간을 제외하지 "
            "못했습니다 (계약종별 미입력)."
        )
    return (
        f"요금적용전력 대상 슬롯(중간·최대부하) {peak.demand_eligible_slots:,}개 중 "
        f"상위 {counted}구간 기준. 경부하 구간은 요금적용전력 대상이 아니므로 "
        "판정에서 제외했습니다 (요구사항서 5.2 ①). 부록 B 의 시각 분포는 전 슬롯 "
        f"{peak.observed_slots:,}개 기준의 원값이며 따로 싣습니다."
    )


@dataclass(frozen=True)
class ImprovementSummary:
    """투자 없이 가능한 절감액과 태양광 검토 신호.

    Attributes:
        tariff_switch_saving_won: 현행 선택요금 대비 최적 선택요금의 절감액.
            조합마다 요금을 다시 계산해 얻는다. 감도를 적용하지 않는 확정 계산이다.
        contract_saving_won: 계약전력 조정 절감액. 하한 규정 미확인 시 None.
        pv_potential: 태양광 피크 기여 가능성 등급.
        pv_midday_share: 정오 시간대 비율. **요금적용전력 대상 슬롯 기준이다.**
        pv_basis: 등급 판정에 쓴 모집단 설명. 산출물에 그대로 싣는다.
    """

    current_selection: TariffSelection | None
    current_total_won: float | None
    best_selection: TariffSelection | None
    best_total_won: float | None
    tariff_switch_saving_won: float | None
    contract_saving_won: float | None
    contract_reduction_kw: float | None
    pv_potential: PvPotential
    pv_midday_share: float
    pv_basis: str = ""
    period_label: str | None = None
    lines: tuple[str, ...] = field(default=())

    @property
    def no_investment_saving_won(self) -> float:
        """투자 없이 나오는 절감액 합계. 모르는 항목은 0 으로 센다."""
        return (self.tariff_switch_saving_won or 0.0) + (self.contract_saving_won or 0.0)


def _won(value: float | None) -> str:
    if value is None:
        return "산출 보류"
    return f"{value / 10_000:,.0f}만원"


def build_lines(summary: ImprovementSummary) -> tuple[str, ...]:
    """화면 최상단에 그대로 쓸 수 있는 세 줄."""
    switch = _won(summary.tariff_switch_saving_won)
    if summary.best_selection is not None and summary.tariff_switch_saving_won == 0.0:
        switch = "현행이 최적"
    contract = _won(summary.contract_saving_won)
    if summary.contract_reduction_kw is not None and summary.contract_reduction_kw <= 0:
        contract = "여유 없음"
    return (
        f"선택요금 전환    {switch}      투자 불필요",
        f"계약전력 조정    {contract}      투자 불필요",
        f"태양광 검토      피크 기여 가능성 {summary.pv_potential} "
        f"(상위 구간의 {summary.pv_midday_share:.0%}가 정오 시간대)",
    )
