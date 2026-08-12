"""탭 사이를 오가는 값 한 벌 (16세션 1절).

**1단계가 만든 것을 2·3단계가 그대로 받는다.** 화면마다 세션에서 다시 꺼내
캐시로 되살리던 방식은 두 가지를 못 지켰다 — 그리지 않은 화면의 위젯 값이
세션에서 사라졌고(0-1), 같은 값을 두 곳에서 되살리니 어느 쪽이 최신인지
알 수 없었다.

탭은 한 번의 실행에서 셋을 모두 그리므로 **객체를 그냥 넘기면 된다.**
산출물도 화면과 같은 객체를 본다.

이 모듈은 Streamlit 을 import 하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass

from kwise.diagnose import Diagnosis
from kwise.io import UsageData
from kwise.quality import QualityReport
from kwise.tariff import BillingResult
from kwise.ui.pipeline import ContractForm

__all__ = ["AnalysisContext"]


@dataclass(frozen=True)
class AnalysisContext:
    """1단계가 확정한 것. **여기까지 왔으면 금액을 낼 수 있다.**"""

    usage: UsageData
    quality: QualityReport
    form: ContractForm
    diagnosis: Diagnosis

    @property
    def baseline(self) -> BillingResult | None:
        """현행 요금. 계약 정보가 없으면 ``None`` 이다."""
        return self.diagnosis.structure.bill if self.diagnosis.structure is not None else None
