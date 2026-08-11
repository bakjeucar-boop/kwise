"""화면 표시 이름 (요구사항서 10.7).

**코드 식별자를 화면에 노출하지 않는다.** ``general_b/high_a/II`` 는 우리가
파일과 코드에서 쓰는 키이고, 사용자에게는 뜻이 없다.

    general_b/high_a/II  →  일반용전력(을) 고압A 선택Ⅱ

이름은 **요금 데이터 파일에서 가져온다.** 코드에 표를 두면 요금표를 갱신했을 때
어긋난다 (부록 A.3 의 드롭다운 규약과 같다).

이 모듈은 Streamlit 을 import 하지 않는다.
"""

from __future__ import annotations

from kwise.tariff import TariffSelection, TariffTable

__all__ = ["OPTION_LABELS", "contract_label", "option_label", "selection_label", "voltage_label"]

# 선택요금 표기. 요금표는 ``I`` 로 담고 청구서는 ``Ⅰ`` 로 적는다.
# 표기 규약이지 요금 데이터가 아니므로 여기 둔다.
OPTION_LABELS: dict[str, str] = {
    "I": "선택Ⅰ",
    "II": "선택Ⅱ",
    "III": "선택Ⅲ",
    "single": "전체시간",
}


def option_label(option: str) -> str:
    """``II`` → ``선택Ⅱ``. 모르는 값은 그대로 둔다 — 지어내지 않는다."""
    return OPTION_LABELS.get(option, option)


def contract_label(table: TariffTable, contract_type: str) -> str:
    """``general_b`` → ``일반용전력(을)``."""
    return table.contract(contract_type).label


def voltage_label(table: TariffTable, contract_type: str, voltage: str) -> str:
    """``high_a`` → ``고압A``."""
    for key, label in table.contract(contract_type).voltages.items():
        if key == voltage:
            return label.label
    return voltage


def selection_label(table: TariffTable, selection: TariffSelection) -> str:
    """``일반용전력(을) 고압A 선택Ⅱ`` — 화면과 산출물에 그대로 쓴다."""
    return " ".join(
        (
            contract_label(table, selection.contract_type),
            voltage_label(table, selection.contract_type, selection.voltage),
            option_label(selection.option),
        )
    )
