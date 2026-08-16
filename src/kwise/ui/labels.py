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

import re

from kwise.tariff import (
    OPTION_LABELS,
    contract_label,
    option_label,
    selection_label,
    voltage_label,
)

__all__ = [
    "OPTION_LABELS",
    "contract_label",
    "measure_title",
    "option_label",
    "selection_label",
    "voltage_label",
]

#: 개선안 앞에 붙은 **요구사항서 절 번호**. 화면에서는 순번으로 바꾼다.
_SECTION = re.compile(r"^7\.([1-7])(?=\s)")


def measure_title(title: str) -> str:
    """개선안 이름을 화면 표기로 (27세션 2절).

        7.1 선택요금 전환  →  1. 선택요금 전환

    **절 번호는 우리 문서의 자리 표시이지 사용자에게 뜻이 있는 값이 아니다.**
    「7.」 이 무엇인지 화면 어디에도 적혀 있지 않아, 읽는 사람은 없는 7장을
    찾게 된다. 순서를 알리는 몫만 남겨 1~7 로 적는다.

    **바꾸는 것은 화면뿐이다.** 코드·문서·보고서·Excel 의 7.x 는 그대로 둔다 —
    거기서는 요구사항서와 맞물려 있어야 하는 번호다. 그래서 정본
    (:attr:`kwise.measures.MeasureKind.title`)을 고치지 않고 **낼 때만** 바꾼다.
    """
    return _SECTION.sub(lambda match: f"{match.group(1)}.", title)
