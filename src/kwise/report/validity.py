"""케이스 결과 타당성 자동 판정 (요구사항서 11.3).

**물리적으로 성립해야 하는 관계만 검사한다.** 깨지면 계산 오류다.

여기 있는 규칙은 두 갈래다.

    공통 규칙   모든 케이스에서 성립해야 한다 (PV 0 이면 절감 0 등)
    분기 규칙   **케이스마다 갈려야 한다.** 같은 결과가 나오면 요금적용전력
                3규칙이 실제로는 걸리지 않고 있다는 뜻이다

**감도에는 단조성을 요구하지 않는다.** :func:`kwise.pv.sharpen` 은 총량을
보존하며 첨예도만 조정하므로 상한 시나리오가 건물 유형과 용량에 따라 바뀐다.
검사하는 것은 총량 보존과 기준값이 범위 안에 있는지뿐이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from kwise.report.casestudy import CaseResult, CaseStudy

__all__ = [
    "Check",
    "check_case_study",
    "checks_frame",
]

# 총량 보존 허용 오차 (11.2). 감도 세 시나리오의 발전량 차이가 이 안이어야 한다.
GENERATION_TOLERANCE = 0.01
# 확정 계산이 감도와 무관한지 볼 때 쓰는 허용 오차 (원).
EXACT_TOLERANCE_WON = 1.0

#: 「목표를 지켰다」 를 판정할 때 봐 주는 폭 (kW) (54세션).
#:
#: 요금적용전력은 월별 최대의 12개월 이월값이라 목표와 정확히 같지 않을 수 있다 —
#: **디스패치가 목표를 지켰다면 그 값을 넘지 않는다**는 것만 본다.
ACHIEVED_TOLERANCE_KW = 0.5


@dataclass(frozen=True)
class Check:
    """판정 하나."""

    scope: str
    name: str
    passed: bool
    detail: str

    @property
    def mark(self) -> str:
        return "통과" if self.passed else "**실패**"


def _pv(result: CaseResult) -> pd.DataFrame:
    return pd.DataFrame(list(result.pv_rows)).set_index("용량(kWp)").sort_index()


def _sensitivity(result: CaseResult) -> pd.DataFrame:
    if not result.sensitivity_rows:
        return pd.DataFrame()
    return pd.DataFrame(list(result.sensitivity_rows))


def _basic_checks(result: CaseResult) -> list[Check]:
    frame = _pv(result)
    label = result.label
    checks: list[Check] = []

    zero = frame.loc[0.0] if 0.0 in frame.index else None
    if zero is not None:
        total = float(zero["총 절감액(원)"])
        checks.append(
            Check(
                label,
                "PV 0 kWp 절감액이 정확히 0",
                total == 0.0,
                f"{total:,.6f} 원",
            )
        )

    energy = frame["전력량요금 절감액(원)"]
    checks.append(
        Check(
            label,
            "용량 증가 시 전력량요금 절감액 단조 증가",
            bool(energy.is_monotonic_increasing),
            " → ".join(f"{value:,.0f}" for value in energy),
        )
    )

    base = frame["기본요금 절감액(원)"]
    increments = base.diff().dropna()
    monotonic = bool(base.is_monotonic_increasing)
    # 포화 — 마지막 증분이 첫 증분보다 작아야 한다. 피크를 다 깎으면 더 줄지 않는다.
    saturating = bool(len(increments) >= 2 and increments.iloc[-1] <= increments.iloc[0] + 1e-6)
    checks.append(
        Check(
            label,
            "기본요금 절감액 단조 증가 후 포화",
            monotonic and saturating,
            "증분 " + " → ".join(f"{value:,.0f}" for value in increments),
        )
    )

    demand = frame["요금적용전력(kW)"]
    checks.append(
        Check(
            label,
            "용량 증가 시 요금적용전력 단조 감소",
            bool(demand.is_monotonic_decreasing),
            " → ".join(f"{value:,.1f}" for value in demand),
        )
    )
    return checks


def _sensitivity_checks(result: CaseResult) -> list[Check]:
    frame = _sensitivity(result)
    label = result.label
    if frame.empty:
        return []

    generation = frame[frame["지표"] == "발전량(kWh)"]
    worst = 0.0
    for _, row in generation.iterrows():
        high, low = float(row["범위 상한"]), float(row["범위 하한"])
        if high > 0:
            worst = max(worst, (high - low) / high)
    checks = [
        Check(
            label,
            "감도 세 시나리오의 발전량이 ±1% 이내 (총량 보존)",
            worst < GENERATION_TOLERANCE,
            f"최대 편차 {worst * 100:.3f}%",
        )
    ]

    inside = frame.dropna(subset=["기준값", "범위 하한", "범위 상한"])
    outliers = inside[
        (inside["기준값"] < inside["범위 하한"] - 1e-6)
        | (inside["기준값"] > inside["범위 상한"] + 1e-6)
    ]
    checks.append(
        Check(
            label,
            "기준값이 범위 안에 있음",
            outliers.empty,
            f"벗어난 지표 {len(outliers)}건",
        )
    )

    base_fee = frame[frame["지표"] == "기본요금 절감액(원)"]
    tops = sorted({str(value) for value in base_fee["상한 시나리오"]})
    checks.append(
        Check(
            label,
            "상한 시나리오 기록 (단조성은 요구하지 않는다)",
            True,
            f"기본요금 절감액 상한: {', '.join(tops) if tops else '—'}",
        )
    )
    return checks


def _ess_checks(result: CaseResult) -> list[Check]:
    """ESS 판정 (54세션). **여태 한 번도 안 보던 자리다.**

    요금·진단·태양광·감도는 여섯 케이스를 다 훑는데 ESS 만 케이스 스터디에
    들어 있지 않았다. 그래서 「고를 수 있는 목표가 없는데 목표를 내고, 그
    목표로 다시 계산한 절감액이 음수」 인 갈래가 **66/66 을 통과한 채** 실물에만
    나왔다.

    보는 것은 넷이다 — 전부 **표가 스스로 어긋나지 않는가**를 묻는다.
    """
    label = result.label
    optimum = result.ess
    if optimum is None:
        return [
            Check(label, "ESS — 초과 구간이 없어 곡선을 그리지 못했다", True, "판정할 것이 없다")
        ]
    checks: list[Check] = []
    chosen = next(
        (item for item in optimum.points if item.target_kw == optimum.target_kw), None
    )

    # ① 성립하지 않으면 목표를 내지 않는다.
    checks.append(
        Check(
            label,
            "ESS 성립하지 않으면 목표가 0 (없는 목표를 내지 않는다)",
            optimum.viable or optimum.target_kw == 0.0,
            f"viable={optimum.viable} · 목표 {optimum.target_kw:,.0f} kW",
        )
    )

    # ② 목표를 냈으면 그 목표의 절감액이 양수다.
    saving = chosen.annual_saving_won if chosen is not None else None
    checks.append(
        Check(
            label,
            "ESS 목표를 냈으면 절감액 > 0",
            not optimum.viable or (saving is not None and saving > 0),
            f"목표 {optimum.target_kw:,.0f} kW · 절감액 "
            + (f"{saving:,.0f} 원" if saving is not None else "—"),
        )
    )

    # ③ 「목표 미달」 은 실제로 미달일 때만 붙는다.
    mismatched = [
        item
        for item in optimum.points
        if item.target_met and item.achieved_demand_kw > item.target_kw + ACHIEVED_TOLERANCE_KW
    ]
    checks.append(
        Check(
            label,
            "ESS 목표 달성이면 실제 요금적용전력이 목표 이하",
            not mismatched,
            f"어긋난 점 {len(mismatched)}개 / {len(optimum.points)}개",
        )
    )

    # ④ 절감액이 0 이하인 점에는 회수기간이 없다 — 표식이 그 줄에 붙지 않는다.
    priced_but_unprofitable = [
        item
        for item in optimum.points
        if item.annual_saving_won <= 0 and item.payback_years is not None
    ]
    checks.append(
        Check(
            label,
            "ESS 절감액이 0 이하인 점에는 회수기간이 없다",
            not priced_but_unprofitable,
            f"어긋난 점 {len(priced_but_unprofitable)}개 / {len(optimum.points)}개",
        )
    )
    return checks


def _measure_checks(result: CaseResult) -> list[Check]:
    """감도와 무관해야 하는 확정 계산들.

    수단 값 자체는 감도 인자를 받지 않으므로 **구조적으로** 무관하다. 여기서는
    금액이 산출되었는지와 부호를 본다.
    """
    label = result.label
    rows: dict[str, dict[str, object]] = {
        str(row["수단"]): dict(row) for row in result.measure_rows
    }
    checks: list[Check] = []

    switch = rows.get("7.1 선택요금 전환")
    if switch is not None:
        value = float(switch["절감액(원)"] or 0.0)  # type: ignore[arg-type]
        checks.append(
            Check(
                label,
                "선택요금 전환 절감액 ≥ 0 (확정 계산)",
                value >= -EXACT_TOLERANCE_WON,
                f"{value:,.0f} 원 · {switch['비고']}",
            )
        )

    power_factor = rows.get("7.4 역률 개선 (92→97%)")
    if power_factor is not None:
        value = float(power_factor["절감액(원)"] or 0.0)  # type: ignore[arg-type]
        expected = result.baseline.total_base_won * 0.01
        checks.append(
            Check(
                label,
                "역률 개선 절감액 = 기본요금의 1.0% (감액 상한)",
                abs(value - expected) < max(1.0, expected * 1e-6),
                f"{value:,.0f} 원 (기본요금 {result.baseline.total_base_won:,.0f} 의 "
                f"{value / result.baseline.total_base_won * 100:.2f}%)",
            )
        )

    dr = rows.get("7.3 경제성DR")
    if dr is not None and result.diagnosis.dr is not None:
        profile = result.diagnosis.dr
        checks.append(
            Check(
                label,
                "DR 거래 가능일이 전체 일수보다 적음 (토·일·공휴일 제외)",
                profile.eligible_days < profile.total_days,
                f"{profile.eligible_days} / {profile.total_days}일",
            )
        )
    return checks


def _cross_case_checks(study: CaseStudy, capacity: float = 1_000.0) -> list[Check]:
    """**케이스마다 갈려야 하는 것들.** 요금적용전력 3규칙 검증이다.

    잣대를 **요금적용전력 저감 폭(kW)과 저감률**로 잡는다. "기본요금 절감액이
    기본요금에서 차지하는 비율" 로 재면 안 된다 — 기본요금 자체가 요금적용전력에
    비례하므로 피크가 낮은 케이스일수록 같은 kW 를 깎아도 비율이 커져 뜻이
    뒤집힌다. PV 가 실제로 하는 일은 **kW 를 깎는 것**이다.
    """
    checks: list[Check] = []

    def reduction_kw(key: str) -> float:
        result = study.find(key)
        frame = _pv(result)
        return result.baseline.billing_demand_kw - float(frame.loc[capacity, "요금적용전력(kW)"])

    def reduction_ratio(key: str) -> float:
        """저감 폭 ÷ 기준 요금적용전력. 규모가 다른 케이스를 견주는 잣대다."""
        return reduction_kw(key) / study.find(key).baseline.billing_demand_kw

    def base_share(key: str) -> float:
        """절감액 가운데 기본요금이 차지하는 비중. 피크 저감의 몫이다."""
        frame = _pv(study.find(key))
        base = float(frame.loc[capacity, "기본요금 절감액(원)"])
        total = float(frame.loc[capacity, "총 절감액(원)"])
        return base / total if total else 0.0

    c1_kw, c2_kw = reduction_kw("C1"), reduction_kw("C2")
    checks.append(
        Check(
            "교차",
            "C1 오전 피크형 > C2 오후 피크형 (요금적용전력 저감 폭)",
            c1_kw > c2_kw,
            f"C1 {c1_kw:,.1f} kW vs C2 {c2_kw:,.1f} kW — "
            "C2 는 피크가 늦어 PV 가 깎은 뒤 저녁 슬롯이 새 최대가 된다",
        )
    )

    c1_share, c3_share = base_share("C1"), base_share("C3")
    checks.append(
        Check(
            "교차",
            "C3 평탄형은 기본요금 절감이 거의 없다 (절감의 대부분이 전력량요금)",
            c3_share < c1_share / 2,
            f"기본요금 비중 C3 {c3_share:.1%} vs C1 {c1_share:.1%} "
            f"(C3 저감 {reduction_kw('C3'):,.1f} kW)",
        )
    )

    c1_ratio, c5_ratio = reduction_ratio("C1"), reduction_ratio("C5")
    checks.append(
        Check(
            "교차",
            "C5 겨울 피크형은 기본요금 절감이 매우 작다",
            c5_ratio < c1_ratio / 2,
            f"저감률 C5 {c5_ratio:.2%} vs C1 {c1_ratio:.2%} — "
            "겨울 대상월의 피크가 07~09시·17~20시라 PV 가 닿지 않는다",
        )
    )

    c6_result = study.find("C6")
    observed = float(c6_result.usage.kw.max())
    billing = c6_result.baseline.billing_demand_kw
    checks.append(
        Check(
            "교차",
            "C6 야간 피크형: 요금적용전력 < 관측 최대수요 (경부하 제외, 5.2 ①)",
            billing < observed - 1.0,
            f"관측 {observed:,.1f} kW vs 요금적용 {billing:,.1f} kW "
            f"({(1 - billing / observed):.1%} 낮다)",
        )
    )

    c6_ratio = reduction_ratio("C6")
    checks.append(
        Check(
            "교차",
            "C6 야간 피크형의 PV 기여가 오히려 크다 (저감률)",
            c6_ratio > c1_ratio,
            f"저감률 C6 {c6_ratio:.2%} vs C1 {c1_ratio:.2%} — "
            "대상 슬롯이 주간뿐이라 PV 가 그대로 듣는다",
        )
    )

    # C4 특례 — 봄·가을 토·일·공휴일 11~14시 할인이 PV 최성기와 겹친다.
    def energy_share(key: str) -> float:
        result = study.find(key)
        frame = _pv(result)
        return (
            float(frame.loc[capacity, "전력량요금 절감액(원)"]) / result.baseline.total_energy_won
        )

    c4_energy, c1_energy = energy_share("C4"), energy_share("C1")
    checks.append(
        Check(
            "교차",
            "C4 산업용(을) 주말 할인이 PV 최성기와 겹쳐 전력량요금 절감 비율이 낮다",
            c4_energy < c1_energy,
            f"C4 {c4_energy:.2%} vs C1 {c1_energy:.2%} "
            "(봄·가을 토·일·공휴일 11~14시 할인 구간의 단가가 이미 낮다)",
        )
    )
    return checks


# 교차 판정에 필요한 케이스. 하나라도 없으면 그 판정은 성립하지 않는다.
CROSS_CASE_KEYS = ("C1", "C2", "C3", "C4", "C5", "C6")


def check_case_study(study: CaseStudy) -> tuple[Check, ...]:
    """케이스 전체를 판정한다. **실패가 하나라도 있으면 계산 오류다.**"""
    checks: list[Check] = []
    for result in study.results:
        checks.extend(_basic_checks(result))
        checks.extend(_sensitivity_checks(result))
        checks.extend(_measure_checks(result))
        checks.extend(_ess_checks(result))

    present = {result.definition.key for result in study.results}
    missing = [key for key in CROSS_CASE_KEYS if key not in present]
    if missing:
        # **건너뛴 사실을 남긴다.** 조용히 빼면 "전부 통과" 로 읽힌다.
        checks.append(
            Check(
                "교차",
                "교차 판정 건너뜀 (일부 케이스만 실행)",
                True,
                f"빠진 케이스: {', '.join(missing)}. 요금적용전력 3규칙 검증은 "
                "여섯 케이스를 모두 돌려야 성립한다.",
            )
        )
    else:
        checks.extend(_cross_case_checks(study))
    return tuple(checks)


def checks_frame(checks: tuple[Check, ...]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"구분": item.scope, "판정 항목": item.name, "결과": item.mark, "근거": item.detail}
            for item in checks
        ]
    ).set_index(["구분", "판정 항목"])
