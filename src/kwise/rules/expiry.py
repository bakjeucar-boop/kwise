"""만료 감지 (요구사항서 12장).

``verified_on`` 또는 근거 문서의 시행일로부터 경과 기간을 재고 임계를 넘으면
경고한다.

    요금 단가    12개월
    약관·규칙    24개월
    참고단가     24개월
    기상         전년도 미확보 시

임계값도 ``assumptions.json`` 에 있다 — 코드에 두면 "왜 12개월인가" 를 물을 곳이
없다.

**자동 수집은 하지 않는다.** 한전 요금표는 API 가 없고 연 1~2회 개정이라 수동
갱신이 현실적이다. 자동화하면 원문이 바뀐 것을 알아채지 못한 채 파싱만 실패하고,
그 실패는 조용하다. 대신 경고와 함께 **원문 확인처**를 안내한다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from kwise.rules.schema import RuleItem, RuleSet

__all__ = [
    "SOURCE_LINKS",
    "ExpiryWarning",
    "check_expiry",
    "weather_expiry",
]

# 원문 확인처. 갱신 안내에 그대로 싣는다.
SOURCE_LINKS: dict[str, str] = {
    "요금 단가": "한국전력공사 전기요금표 — https://cyber.kepco.co.kr/ckepco/front/jsp/CY/E/E/CYEEHP00101.jsp",
    "기본공급약관": "국가법령정보센터 / 한전 기본공급약관 — https://cyber.kepco.co.kr/ckepco/front/jsp/CY/D/C/CYDCHP00201.jsp",
    "전력시장운영규칙": "한국전력거래소 — https://www.kpx.or.kr/menu.es?mid=a10301020000",
    "ESS 참고단가": "에너지경제연구원 발간물 — https://www.keei.re.kr",
    "기상": "Open-Meteo — https://open-meteo.com/ (tools\\fetch_weather.py 로 갱신)",
}


@dataclass(frozen=True)
class ExpiryWarning:
    """만료 경고 하나."""

    scope: str
    key: str
    label: str
    months: float
    threshold_months: float
    basis: str
    """무엇으로부터 잰 기간인지 — ``확인일`` 또는 ``시행일``."""
    source: str = ""
    link: str = ""
    detail: str = ""

    @property
    def overdue_months(self) -> float:
        return self.months - self.threshold_months

    def message(self) -> str:
        return (
            f"[{self.scope}] {self.label} — {self.basis}로부터 {self.months:.0f}개월 "
            f"경과 (임계 {self.threshold_months:.0f}개월). "
            f"{('근거: ' + self.source + '. ') if self.source else ''}"
            f"원문 확인: {self.link}"
        )


def _threshold(assumptions: RuleSet, key: str, fallback_key: str) -> float:
    """임계값을 설정에서 읽는다. 없으면 다른 임계로 물러선다."""
    if key in assumptions:
        return float(assumptions.value(key))
    return float(assumptions.value(fallback_key))


def _scope_of(item: RuleItem) -> tuple[str, str, str]:
    """항목이 어느 임계를 쓰는지. (구분, 임계 키, 확인처 키)"""
    if item.key.startswith("tariff.") or "unit_rate" in item.key:
        return "요금 단가", "expiry.tariff_months", "요금 단가"
    if item.key.startswith("dr."):
        return "전력시장운영규칙", "expiry.statute_months", "전력시장운영규칙"
    if not item.is_statutory:
        return "판단값", "expiry.reference_months", "ESS 참고단가"
    return "약관·규칙", "expiry.statute_months", "기본공급약관"


def check_expiry(
    rules: RuleSet,
    assumptions: RuleSet,
    *,
    today: dt.date | None = None,
) -> tuple[ExpiryWarning, ...]:
    """임계를 넘긴 항목을 모은다.

    **확인일이 있으면 확인일을, 없으면 시행일을 축으로 잰다.** 확인일이 곧
    "사람이 원문을 보고 아직 맞다고 판단한 날" 이므로 그쪽이 우선이다.
    """
    reference = today if today is not None else dt.date.today()
    warnings: list[ExpiryWarning] = []
    for ruleset in (rules, assumptions):
        for key in ruleset.item_keys():
            item = ruleset[key]
            scope, threshold_key, link_key = _scope_of(item)
            threshold = _threshold(assumptions, threshold_key, "expiry.statute_months")

            months = item.months_since_verified(reference)
            basis = "확인일"
            if months is None:
                months = item.months_since_source(reference)
                basis = "시행일"
            if months is None or months <= threshold:
                continue
            warnings.append(
                ExpiryWarning(
                    scope=scope,
                    key=key,
                    label=item.label,
                    months=months,
                    threshold_months=threshold,
                    basis=basis,
                    source=item.source,
                    link=SOURCE_LINKS.get(link_key, ""),
                )
            )
    return tuple(sorted(warnings, key=lambda item: -item.overdue_months))


def weather_expiry(
    *,
    today: dt.date | None = None,
    root: Path | None = None,
) -> ExpiryWarning | None:
    """**전년도 기상이 없으면** 경고한다.

    기상은 조문이 아니라 연도로 만료한다. 케이스 기간이 최근일수록 전년도 자료가
    있어야 하므로 "직전 연도 확보 여부" 하나만 본다.
    """
    from kwise.pv.archive import archive_status

    reference = today if today is not None else dt.date.today()
    last_year = reference.year - 1
    status = archive_status(root)
    if last_year in status.years:
        return None
    return ExpiryWarning(
        scope="기상",
        key="weather.archive",
        label=f"사전 취득 기상 {last_year}년분",
        months=12.0,
        threshold_months=0.0,
        basis="연도",
        source="Open-Meteo ERA5",
        link=SOURCE_LINKS["기상"],
        detail=(
            f"확보 연도 {status.years or '없음'} 에 {last_year}년이 없습니다. "
            f"tools\\fetch_weather.py --start {last_year}-01-01 --end {last_year}-12-31"
        ),
    )
