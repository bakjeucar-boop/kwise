"""크기 표기 (31세션 0-1).

**0 으로 뭉개면 문구가 거짓말이 된다.** 「기준 92% 대비 0.0%p 미달」 이 그랬다 —
92.0% 에서는 미달이 아니었고, 91.96% 에서는 미달인데 폭이 안 보였다.
"""

from __future__ import annotations

import pytest

from kwise.magnitude import MAX_DECIMALS, magnitude, places


def test_보통_값은_한_자리로_적는다() -> None:
    assert magnitude(0.5, "%p") == "0.5%p"
    assert magnitude(32.0, "%p") == "32.0%p"


def test_진짜_0_은_늘리지_않는다() -> None:
    """0 은 0 으로 보이는 것이 맞다. 늘리면 ``0.0000`` 이 되어 읽기만 나빠진다."""
    assert magnitude(0.0, "%p") == "0.0%p"
    assert places(0.0) == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.04, "0.04%p"),
        (0.004, "0.004%p"),
        (0.0004, "0.0004%p"),
        (-0.04, "-0.04%p"),
    ],
)
def test_0_이_아니면_보일_때까지_늘린다(value: float, expected: str) -> None:
    assert magnitude(value, "%p") == expected


def test_상한에서_멈춘다() -> None:
    """더 잘게 적어도 읽는 사람이 쓸 수 없는 크기다 — 늘리기를 멈춘다."""
    assert places(1e-12) == MAX_DECIMALS


def test_세_자리_콤마가_들어간다() -> None:
    assert magnitude(1234.5, " kW") == "1,234.5 kW"


def test_부호는_골라_붙인다() -> None:
    """방향을 말로 적는 문구에서는 끈다 — 「+0.1% 추가」 는 같은 말을 두 번 한다."""
    assert magnitude(0.1, "%") == "0.1%"
    assert magnitude(0.1, "%", sign=True) == "+0.1%"
    assert magnitude(-0.1, "%") == "-0.1%"
