"""태양광 투자비 단가 (요구사항서 7.5).

ESS 와 같은 규약으로 **용량 기준 단가** 하나를 받는다. 태양광은 시장 관행이
kWp당 단가이므로 자연스럽다.

    투자비 = 설치 용량(kWp) × 입력단가(원/kWp)

견적서를 받았으면 총액을 그대로 넣는 경로(:meth:`PvCostInput.of_total`)도 있다.

**참고단가를 만들지 않는다.** ESS 는 에너지경제연구원 LCOS 연구라는 인용 가능한
출처가 있지만 태양광은 확보하지 못했다. 근거 없는 기본값을 넣으면 그 값이 견적으로
둔갑한다. 단가를 주지 않으면 **투자비 대신 사유를 돌려준다**
(:meth:`PvCostInput.unpriced`). 공개 자료를 확보하면
:class:`PvCostReference` 를 채워 넣을 수 있도록 자리만 열어 두었다 — 지금은
비어 있고, 비어 있다는 사실이 :data:`PV_REFERENCE_NOTE` 로 나간다.

**kWp 는 모듈 직류(DC) 정격이다.** 인버터 용량(kW-ac)과 다르다. 부대비용(옥상
가대·계통연계·인허가)의 포함 여부는 견적서 기준을 그대로 따르며 이 도구가
가정하지 않는다 (:data:`PV_COST_BASIS_NOTE`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "PV_COST_BASIS_NOTE",
    "PV_REFERENCE_NOTE",
    "PV_UNPRICED_REASON",
    "SCALE_ECONOMY_NOTE",
    "PvCostInput",
    "PvCostReference",
]

# 입력 라벨과 산출물에 함께 싣는다. 무엇을 기준으로 한 단가인지 밝히지 않으면
# 견적서마다 다른 값을 같은 칸에 넣게 된다.
PV_COST_BASIS_NOTE = (
    "설치 단가는 **kWp당(원/kWp)** 입력이며 kWp 는 **모듈 직류(DC) 정격**입니다 — "
    "인버터 용량(kW-ac)과 다릅니다. 옥상 가대·계통연계·인허가 등 부대비용의 포함 "
    "여부는 입력하신 견적 기준을 그대로 따르며 본 도구가 가정하지 않습니다. "
    "견적서에 총액만 있으면 총액 입력 경로를 쓰십시오."
)

# 용량 곡선의 모든 점에 같은 단가를 적용한다. 그 사실을 결과에 적는다.
SCALE_ECONOMY_NOTE = (
    "용량 곡선의 모든 점에 **같은 kWp당 단가**를 적용했습니다. 실제로는 소규모일수록 "
    "kWp당 단가가 높으므로 작은 용량 구간의 투자비가 과소 산출되고 회수기간이 "
    "낙관적으로 나옵니다. **규모의 경제는 반영하지 않았습니다.**"
)

# 참고단가가 없다는 사실 자체를 밝힌다. 빈칸으로 두지 않는다.
PV_REFERENCE_NOTE = (
    "태양광 설치 단가의 참고값은 제공하지 않습니다. ESS 와 달리 인용할 공개 자료를 "
    "확보하지 못했습니다 — **근거 없는 기본값을 만들지 않습니다.** 견적 단가(원/kWp) "
    "또는 총액을 직접 넣어 주십시오."
)

PV_UNPRICED_REASON = "미산출 — 태양광 설치 단가 미입력 (원/kWp 또는 총액을 넣으십시오)"


@dataclass(frozen=True)
class PvCostReference:
    """참고단가 자리. **지금은 비어 있다.**

    공개 자료(예: 신재생에너지 보급통계, 공공조달 단가)를 확보하면 ESS 와 같은
    방식으로 ``data\\pv_cost_reference.json`` 을 만들어 채운다. 확보 전에는
    :attr:`available` 이 False 이고 아무 값도 만들지 않는다.
    """

    entries: tuple[tuple[str, float], ...] = field(default=())
    citation: str = ""

    @property
    def available(self) -> bool:
        return bool(self.entries)

    @property
    def note(self) -> str:
        return self.citation if self.available else PV_REFERENCE_NOTE


EMPTY_PV_REFERENCE = PvCostReference()


@dataclass(frozen=True)
class PvCostInput:
    """설치 단가 입력. **세 상태가 있다.**

    - kWp당 단가 (기본) — ``투자비 = 용량(kWp) × 단가``
    - 총액 직접 입력 — 견적서를 받은 경우
    - **미입력** — 투자비를 만들지 않고 사유를 돌려준다

    ESS(:class:`~kwise.measures.ess_cost.EssCostInput`)와 달리 미입력이 오류가
    아니다. 태양광은 참고단가가 없어서 "모르는 채로 두는" 상태가 정상이다.
    """

    unit_cost_won_per_kwp: float | None = None
    total_won: float | None = None
    source: str = "사용자 입력"

    def __post_init__(self) -> None:
        if self.unit_cost_won_per_kwp is not None and self.total_won is not None:
            raise ValueError(
                "kWp당 단가와 총액을 함께 줄 수 없습니다 "
                f"(단가={self.unit_cost_won_per_kwp}, 총액={self.total_won})."
            )
        for value in (self.unit_cost_won_per_kwp, self.total_won):
            if value is not None and value < 0:
                raise ValueError(f"단가·총액은 음수일 수 없습니다: {value}")

    @classmethod
    def of_unit_cost(cls, won_per_kwp: float, *, source: str = "사용자 입력") -> PvCostInput:
        return cls(unit_cost_won_per_kwp=won_per_kwp, source=source)

    @classmethod
    def of_total(cls, won: float, *, source: str = "견적서 총액") -> PvCostInput:
        return cls(total_won=won, source=source)

    @classmethod
    def unpriced(cls) -> PvCostInput:
        """단가를 모르는 상태. **0원이 아니다.**"""
        return cls(source=PV_UNPRICED_REASON)

    @property
    def is_priced(self) -> bool:
        return self.unit_cost_won_per_kwp is not None or self.total_won is not None

    @property
    def is_total(self) -> bool:
        return self.total_won is not None

    def investment_won(self, capacity_kwp: float) -> float | None:
        """투자비. **모르면 0 이 아니라 None 이다.**

        총액을 넣었으면 용량과 무관하게 그 값이다 — 견적은 특정 용량에 대한
        것이므로 곡선의 다른 점에 쓰면 뜻이 달라진다. 그 경고는 곡선 쪽에서 낸다.
        """
        if self.total_won is not None:
            return self.total_won
        if self.unit_cost_won_per_kwp is None:
            return None
        return capacity_kwp * self.unit_cost_won_per_kwp

    @property
    def reason(self) -> str:
        """금액을 내지 못한 사유. 빈칸으로 두지 않는다."""
        return PV_UNPRICED_REASON if not self.is_priced else ""
