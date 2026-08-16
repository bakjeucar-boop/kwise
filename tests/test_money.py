"""금액 표기 (14세션 1절).

**표시는 천 원 단위로 절사하고 내부 계산은 원 단위를 유지한다.** 두 규칙이 함께
지켜져야 한다 — 절사한 값으로 계산하면 합계가 어긋나고, 절사하지 않고 표시하면
같은 값이 화면·Excel·Word 에서 다르게 보인다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from kwise import money
from kwise.report.notices import format_won
from kwise.ui import text as fmt

SRC = Path(__file__).resolve().parents[1] / "src" / "kwise"

# ===================================================================== 절사 규칙


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1_234_567.0, 1_234_000.0),
        (999.0, 0.0),
        (1_000.0, 1_000.0),
        (0.0, 0.0),
        (-1_234_567.0, -1_234_000.0),  # 0 쪽으로 자른다
    ],
)
def test_천_단위로_절사한다(value: float, expected: float) -> None:
    assert money.truncate_won(value) == expected


def test_절사한_표기는_언제나_천의_배수다() -> None:
    """**정규식 검사** — 원 단위 표기의 끝 세 자리는 항상 000 이다."""
    pattern = re.compile(r"^-?\d{1,3}(,\d{3})*원$")
    for value in (1, 999, 1_000, 1_234_567, 452_832_624, 3_351_117_349, -48_310_000):
        text = money.won(float(value), reason="—")
        assert pattern.match(text), text
        assert int(text.rstrip("원").replace(",", "")) % 1_000 == 0, text


def test_단위_없는_표기도_같은_절사를_쓴다() -> None:
    assert money.won_plain(1_234_567.0, reason="—") == "1,234,000"
    assert format_won(1_234_567.0) == "1,234,000"
    assert fmt.won(1_234_567) == "1,234,000원"


def test_억_만원_표기는_만원_자리에서_반올림한다() -> None:
    """만원 반올림이 천 원 절사보다 굵으므로 다시 절사하지 않는다."""
    assert money.won_short(31_518_402.0, reason="—") == "3,152만원"
    assert money.won_short(123_456_789.0, reason="—") == "1억 2,346만원"
    assert money.won_short(-53_580_000.0, reason="—") == "-5,358만원"


def test_모르는_금액은_빈칸도_0원도_아니다() -> None:
    """0원은 '공짜' 로 읽힌다. 사유를 적는다 (요구사항서 7.5)."""
    assert money.won(None, reason="단가 미입력") == "단가 미입력"
    assert money.won_plain(None, reason="단가 미입력") == "단가 미입력"
    assert money.won_short(None, reason="단가 미입력") == "단가 미입력"


# ===================================================================== 내부 계산


def test_내부_계산은_원_단위를_유지한다() -> None:
    """절사는 **표시 함수 안에서만** 일어난다. 원값이 바뀌면 합계가 어긋난다."""
    items = [1_234_567.0, 2_345_678.0, 3_456_789.0]
    total = sum(items)
    assert total == 7_037_034.0  # 절사되지 않은 원 단위 합계

    # 항목을 각각 절사하면 합계 표시와 어긋난다 — 그래서 각주를 단다.
    truncated_sum = sum(money.truncate_won(item) for item in items)
    assert truncated_sum != money.truncate_won(total)
    assert "천 원 단위로 절사" in money.TRUNCATION_FOOTNOTE
    # **각주는 표기 방식을 그대로 적는다** (28세션 1-3). 만원 표기(`won_short`)를
    # 쓰는 표에 절사 각주를 달면 어긋난 자리를 엉뚱하게 의심하게 된다.
    assert "만원 단위로 반올림" in money.ROUNDING_FOOTNOTE
    assert money.won_short(1_234_567.0, reason="—") == "123만원"


def test_요금_계산_결과는_절사되지_않는다(sample_bill: object) -> None:
    """요금 엔진이 내는 값은 원 단위 그대로다 — 회귀값이 여기 걸려 있다."""
    total = float(sample_bill.total_won)  # type: ignore[attr-defined]
    assert total != money.truncate_won(total)


# ===================================================================== 한 곳 규약


def test_금액_표기를_직접_찍는_곳이_없다() -> None:
    """``f"{value:,.0f}원"`` 을 손으로 쓰면 절사 규칙이 그 자리만 빠진다.

    단가(원/kW·원/kWh·원/kWp)는 금액이 아니므로 제외한다 — 천 원 미만 단가를
    절사하면 0 이 된다.
    """
    # ``report/validity.py`` 는 요금 계산을 원 단위로 대조하는 검증 리포트다.
    # 절사하면 대조가 성립하지 않으므로 규약에서 뺀다.
    exempt = {"money.py", "validity.py"}
    pattern = re.compile(r":,\.\d+f\} ?원(?!/)")
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        if path.name in exempt:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(SRC)}:{number} {line.strip()}")
    assert not offenders, "금액은 kwise.money 를 거쳐 찍는다:\n" + "\n".join(offenders)


# ===================================================================== 산출물


def test_엑셀의_금액_열이_절사되어_나간다() -> None:
    """**Excel 도 천 단위 절사다** (14세션 1절). 내보낼 때만 자른다."""
    import pandas as pd

    from kwise.report.excel import truncate_money_columns

    frame = pd.DataFrame(
        {
            "조합": ["기준선"],
            "절감액(원)": [1_234_567.0],
            "요금적용전력(kW)": [5_293.44],
            "확실성": ["높음"],
        }
    )
    trimmed = truncate_money_columns(frame)
    assert trimmed["절감액(원)"].iloc[0] == 1_234_000.0
    # 금액이 아닌 열은 건드리지 않는다.
    assert trimmed["요금적용전력(kW)"].iloc[0] == 5_293.44
    # 원본은 그대로다 — 계산 프레임을 손대면 회귀값이 흔들린다.
    assert frame["절감액(원)"].iloc[0] == 1_234_567.0


def test_결측_금액은_절사에서_건너뛴다() -> None:
    import pandas as pd

    from kwise.report.excel import truncate_money_columns

    frame = pd.DataFrame({"투자비(원)": [None, 2_345_678.0]})
    trimmed = truncate_money_columns(frame)
    assert pd.isna(trimmed["투자비(원)"].iloc[0])
    assert trimmed["투자비(원)"].iloc[1] == 2_345_000.0
