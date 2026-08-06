"""산출물에 반드시 싣는 문구 (요구사항서 5.1, 5.8, 9.4, 부록 D).

숫자만 있는 산출물은 잘못 읽힌다. 무엇을 포함하지 않았고 무엇이 불확실한지를
같은 장에 적어야 한다.
"""

from __future__ import annotations

__all__ = [
    "CONTRACT_CHANGE_WARNING",
    "KNOWN_LIMITS",
    "NOT_INCLUDED_NOTICE",
    "UNPRICED_REASONS",
    "format_won",
]

from kwise.tariff import NOT_INCLUDED_NOTICE

# 요구사항서 9.4 — 필수 경고
CONTRACT_CHANGE_WARNING = (
    "기본요금은 직전 12개월 중 최대수요로 결정됩니다. 계약전력을 하향할 경우, "
    "예측 오차와 기상 변동을 고려하여 충분한 여유를 확보하십시오. "
    "한 번의 초과가 12개월간 적용됩니다."
)

# 요구사항서 부록 D — 알려진 한계
KNOWN_LIMITS: tuple[str, ...] = (
    "기본요금과 전력량요금만 계산합니다. 기타 요금요소는 미포함이며, 이들이 "
    "사용전력량에 비례하므로 실제 절감액은 본 결과보다 큽니다.",
    "태양광 발전량 예측은 피크 발전량을 과소 산출하는 경향이 있습니다. "
    "피크 절감량이 보수적으로 나옵니다.",
    "역률요금은 추정값 기반 참고 산출입니다. 무효전력 실측이 없습니다.",
    "결측 구간에 더 큰 수요가 있었을 수 있습니다.",
    "부하는 업로드한 데이터가 고정입니다. 향후 운영 변화(공실률, 증설, 용도 변경)는 "
    "반영하지 않았습니다.",
    "인증·신고용 산출물이 아닙니다.",
    "상계거래·외부 신재생에너지 구매의 자격요건은 판정하지 않습니다. 금액만 참고로 제시합니다.",
    "ESS 디스패치는 규칙기반 단일 전략입니다. 최적 운전과는 차이가 있습니다.",
)

# 금액을 내지 못한 항목의 사유. 빈칸으로 두지 않는다.
UNPRICED_REASONS: dict[str, str] = {
    "contract": "미산출 — 요금적용전력 하한 규정 미확인 (한전 기본공급약관 확인 필요)",
    "external_price": "미산출 — 외부 신재생에너지 구매 단가 미입력",
    "no_saving": "절감 없음",
}


def format_won(value: float | None, *, reason: str = UNPRICED_REASONS["contract"]) -> str:
    """금액을 문자열로. **None 이면 빈칸이 아니라 사유를 적는다.**"""
    if value is None:
        return reason
    return f"{value:,.0f}"
