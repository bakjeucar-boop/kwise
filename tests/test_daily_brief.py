r"""아침 브리핑 시험 (69세션 3절).

**이 파일은 매 세션의 출발점을 만든다.** 여기가 조용히 깨지면 그날 아침이
통째로 어긋난다 — 08-30 에 실제로 그랬다. 그런데 68세션까지 **동작 시험이
0건**이었다: 있는 것은 파일이 있는지 보는 것과 ``mypy tools`` 뿐이었다.

**하루에 세 번 되물린 자리를 세운다.**

    ① 갈래 표식 셈  — 본문 안의 「①-4」 를 갈래로 세지 않는다 (68세션 GROUP)
    ② 건수          — 줄이 아니라 건. 「청구서 3」 같은 뭉침을 담는다
    ③ 자르지 않을 칸 셋이 온전히 나온다 (오늘 첫 작업 · 미해결 · 블로커)
    ④ 표 칸의 세로줄이 행을 지우면 **말한다** (68세션에 조용히 0을 냈다)

덧붙여 **갈래 번호가 앞 갈래에 안 밀리는 것**(69세션 1절)과 **갈래 하나만
사라질 때 말하는 것**(69세션 2절)을 세운다.

**`PROCEED.md` 원본을 자료로 쓰지 않는다.** 매 세션 바뀌는 파일이라 시험이
거기 매이면 다음 세션에 깨진다 — 여기 작은 표본을 따로 둔다.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _brief() -> ModuleType:
    """``tools\\daily_brief.py`` 를 불러온다 (다른 시험이 도구를 쓰는 방식과 같다)."""
    sys.path.insert(0, str(PROJECT_ROOT / "tools"))
    try:
        import daily_brief
    finally:
        sys.path.pop(0)
    return daily_brief


#: 자르면 사라지는 꼬리. 온전히 나오는지는 **끝 글자**로 본다.
TAIL_NEXT = "끝표시99"
TAIL_ITEM = "끝표시02"
TAIL_BLOCKER = "끝표시03"
#: **잘리는** 쪽의 꼬리. 이것이 나오면 자르기가 죽은 것이다.
TAIL_CLIPPED = "끝표시04"

_NEXT = (
    "**S99 후보 둘 — 순서대로.** 1. **첫째 후보** — 이 문장은 아흔여섯 글자를 훌쩍 넘도록 "
    "길게 이어진다. 잘리면 뒤가 사라지므로 시험이 끝 글자를 본다. "
    f"2. **둘째 후보** — 여기까지 온전히 나와야 한다. {TAIL_NEXT}"
)
_OPEN = (
    "**① 자료를 기다리는 것 5건** (청구서 3 · 참고단가 2). "
    "**①-4 「실측」 은 곁가리다** — 이 서술이 항목으로 새면 안 된다 (괄호 안 딴말). · "
    "**② 우리 손에 달린 것 2건** (형 정리 · 이름 고치기 — 이 항목은 아흔여섯 글자를 넘도록 "
    f"길게 이어져야 자르지 않는다는 것을 보일 수 있다. 끝까지 나와야 한다. {TAIL_ITEM}) · "
    "**③ 실물을 기다리는 것 1건** (1시간 자료). **블로커 없음**"
)
_BLOCKER = (
    "— (원인은 아직 모른다. 이 설명도 아흔여섯 글자를 넘도록 길게 이어진다. 잘리면 뒤가 "
    f"사라지므로 시험이 끝 글자를 본다. {TAIL_BLOCKER})"
)
_TESTS = (
    "표본 999건 통과. 이 칸은 **자르는 쪽**이라 아흔여섯 글자를 넘으면 꼬리가 사라져야 한다. "
    "근거 넷은 요약이고 위 셋은 인수인계다 — 그 둘을 가르는 것이 이 시험이다. "
    f"{TAIL_CLIPPED}"
)

SAMPLE = f"""# 진행 이력

| 실제 | 무엇 |
|---|---|
| **41세션** (01-02) | 앞선 세션이다 |
| **42세션** (01-03) | 표본 세션 — 첫 토막 · 둘째 토막 · 셋째 토막 · 넷째 토막 |

---

## 현재 상태

| 항목 | 값 |
|---|---|
| 최근 세션 | 표본이다 |
| 다음 작업 | {_NEXT} |
| 테스트 상태 | {_TESTS} |
| 미해결 | {_OPEN} |
| 블로커 | {_BLOCKER} |

---

## 오늘 (2026-01-03) 42세션 — 표본
"""


def _items(text: str = SAMPLE) -> list[object]:
    brief = _brief()
    return brief.open_items(brief.current_state(text))


def _built(text: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """``build()`` 를 표본으로 돌린다. git 은 부르지 않는다."""
    brief = _brief()
    monkeypatch.setattr(brief, "read_proceed", lambda: text)
    monkeypatch.setattr(brief, "git", lambda *args: "")
    return str(brief.build())


# ===================================================== ① 갈래 표식 셈


def test_본문_안의_표식을_갈래로_세지_않는다() -> None:
    """**「①-4」 는 곁가리지 갈래가 아니다** (68세션 2절).

    그냥 ``[①②③④⑤]`` 였을 때 이것을 갈래 머리말로 세어 ① 이 둘로 갈렸고,
    뒤 덩어리가 **유령 항목**이 되어 브리핑에 떴다.
    """
    items = _items()
    assert [item.sym for item in items] == ["①", "①", "②", "②", "③"]  # type: ignore[attr-defined]
    assert not any("곁가리" in item.text for item in items), items  # type: ignore[attr-defined]


def test_괄호가_닫힌_뒤의_서술이_항목에_안_붙는다() -> None:
    """``partition("(")`` + ``rsplit(")")`` 는 **마지막 닫는 괄호**까지 잡았다.

    그래서 ① 의 마지막 항목이 「참고단가 2). ①-4 「실측」 은 …」 이 됐다.
    괄호 **짝**을 세면 목록만 남는다.
    """
    items = _items()
    assert [item.text for item in items[:2]] == ["청구서 3", "참고단가 2"]  # type: ignore[attr-defined]


# ===================================================== ② 건수


def test_건수는_줄이_아니라_건으로_센다() -> None:
    """머리말이 5건이면 총계도 그 5를 담는다.

    「청구서 3」 한 줄이 셋을 담는다 — **뭉침이지 실종이 아니다**(3이 적혀
    있다). 그래서 펴지 않고 세는 쪽을 고쳤다. 줄은 다섯, 건은 5+2+1 = 8.
    """
    brief = _brief()
    items = _items()
    assert len(items) == 5
    assert brief.total_items(items) == 8


# ===================================================== 갈래 번호 (69세션 1절)


def test_갈래_번호는_앞_갈래가_줄어도_안_밀린다() -> None:
    """**통번호였다면 ② 가 전부 한 칸씩 밀린다** — 그것이 세션을 건너 가리키는
    말을 썩게 했다 (67세션 「미해결 10번」 ≠ 68세션 「미해결 10」).
    """
    assert [item.tag for item in _items()] == ["①-1", "①-2", "②-1", "②-2", "③-1"]  # type: ignore[attr-defined]

    shrunk = SAMPLE.replace("(청구서 3 · 참고단가 2)", "(청구서 3)")
    assert shrunk != SAMPLE
    tags = [item.tag for item in _items(shrunk)]  # type: ignore[attr-defined]
    assert tags == ["①-1", "②-1", "②-2", "③-1"], tags


def test_내가_밟아야_할_것도_같은_표식을_쓴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """한 문서에 표식이 두 벌이면 결함 유형 ③ 이다 (69세션 1절)."""
    out = _built(SAMPLE, monkeypatch)
    tail = out.split("## 내가 밟아야 할 것")[-1]
    assert "①-1 청구서 3" in tail, tail
    assert "③-1 1시간 자료" in tail, tail


# ===================================================== ③ 자르지 않을 칸 셋


def test_자르지_않을_칸_셋이_온전히_나온다(monkeypatch: pytest.MonkeyPatch) -> None:
    """오늘 첫 작업 · 미해결 항목 · 블로커는 **접기만 하고 자르지 않는다**.

    이틀 연속 이 셋이 잘려 원문을 다시 물어야 했다 (67·68세션).
    """
    out = _built(SAMPLE, monkeypatch)
    for tail in (TAIL_NEXT, TAIL_ITEM, TAIL_BLOCKER):
        assert tail in out, f"{tail} 가 잘렸습니다"


def test_잘라도_되는_칸은_그대로_자른다(monkeypatch: pytest.MonkeyPatch) -> None:
    """**상한을 없앤 것이 아니라 칸마다 다르게 둔 것이다.**

    이것이 없으면 「전부 온전히」 로 미끄러져 브리핑이 그냥 길어진다.
    """
    out = _built(SAMPLE, monkeypatch)
    evidence = next(line for line in out.splitlines() if line.startswith("  근거 · 테스트 상태"))
    assert evidence.endswith("…"), evidence
    assert TAIL_CLIPPED not in out, "자르는 칸의 꼬리가 남았습니다 — clip 이 죽었습니다"


# ===================================================== ④ 조용한 실패


def test_표_칸의_세로줄이_행을_지우면_말한다(monkeypatch: pytest.MonkeyPatch) -> None:
    """**조용한 0 을 막는다** (68세션 1절에 실제로 겪었다).

    미해결 칸에 세로줄이 하나 섞이면 그 표 행이 통째로 사라지는데, 그때
    브리핑이 아무 말 없이 「미해결 0건」 을 냈다 — B 를 등재하려고 쓴 문장이
    B 가 든 목록을 지웠다.
    """
    broken = SAMPLE.replace("(형 정리", "(형 | 정리")
    assert broken != SAMPLE
    out = _built(broken, monkeypatch)
    assert "칸을 못 읽었다" in out, out
    assert "미해결 0건" not in out


def test_갈래_하나만_사라져도_말한다(monkeypatch: pytest.MonkeyPatch) -> None:
    """**행 전체가 아니라 갈래 하나만 빠지면 수만 줄어 조용하다** (69세션 2절).

    ``GROUP`` 이 표식 뒤 빈칸을 요구하므로 붙여 쓰면 그 갈래가 통째로 빠진다 —
    68세션이 그 조건을 만들어 놓고 잡는 장치는 0 에만 걸어 두었다.
    """
    broken = SAMPLE.replace("**① 자료를", "**①자료를")
    assert broken != SAMPLE
    out = _built(broken, monkeypatch)
    assert "갈래 ① 를 못 읽었다" in out, out
