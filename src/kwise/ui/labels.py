"""화면 표시 이름 (요구사항서 10.7).

**코드 식별자를 화면에 노출하지 않는다.** ``general_b/high_a/II`` 는 우리가
파일과 코드에서 쓰는 키이고, 사용자에게는 뜻이 없다.

    general_b/high_a/II  →  일반용전력(을) 고압A 선택Ⅱ

**실제 구현은 :mod:`kwise.tariff.labels` 에 있다** (15세션). 계산 모듈이 내는
문구에도 종별 이름이 들어가는데(선택요금 전환 노트가 그랬다), 화면 쪽에만 두면
계산 모듈이 쓸 수 없어 코드 식별자가 그대로 화면에 나간다. 여기서는 화면·보고서가
익숙한 이름으로 **다시 내보내기만** 한다.

이 모듈은 Streamlit 을 import 하지 않는다.
"""

from __future__ import annotations

from kwise.tariff import (
    OPTION_LABELS,
    contract_label,
    option_label,
    selection_label,
    voltage_label,
)

__all__ = ["OPTION_LABELS", "contract_label", "option_label", "selection_label", "voltage_label"]
