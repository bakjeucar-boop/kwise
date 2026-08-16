"""화면 예산 측정 — 본문 줄 수와 확인사항 개수 (22세션 0·1절).

**화면을 실제로 띄워 순서대로 훑는다.** 소스 훑기로는 어느 카드의 글인지 가릴
수 없다. 진단 절은 ``st.subheader`` 로, 수단 카드는 절 번호 머리글로 경계를
잡는다.

    본문      st.markdown / st.write 로 나간 문단 (헤더·안내 제외)
    확인사항  차단(st.error) + 주의(⚠ 굵은 글씨)
    한도      ``assumptions.json`` 의 ``ui.body_line_budget`` · ``ui.notice_budget``

**툴팁·캡션·접힘은 예산 밖이다.** 접어 둔 계산 근거와 물음표 안의 근거는 매번
읽는 글이 아니다. 예산은 «펼치지 않아도 눈에 들어오는 글»만 센다.

    .venv\\Scripts\\python.exe tools\\screen_budget.py
    .venv\\Scripts\\python.exe tools\\screen_budget.py --detail

시험(``tests\\test_ui_screen.py``)이 같은 함수를 불러 한도를 강제한다 — 도구와
시험이 다른 잣대를 쓰면 도구가 통과시킨 것이 시험에서 깨진다.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from streamlit.testing.v1 import AppTest  # noqa: E402

from kwise.rules import assumption  # noqa: E402
from kwise.ui.callout import CAUTION_ICON  # noqa: E402
from kwise.ui.pipeline import ContractForm  # noqa: E402

__all__ = ["Budget", "Section", "measure", "over_budget", "screen_budget"]

APP = PROJECT_ROOT / "src" / "kwise" / "ui" / "app.py"
SAMPLE = PROJECT_ROOT / "input" / "사용량조회_20240429.csv"
MEASURE_KEYS = (
    "tariff_switch",
    "contract",
    "demand_response",
    "power_factor",
    "solar",
    "ess",
    "surplus",
)
#: 수단 카드 머리글. **개선안 이름이 곧 체크박스 라벨이다** (27세션 1절).
#: 26세션까지는 ``font-size:1.5rem`` 짜리 markdown 이었다 — 그것으로 자리를
#: 갈랐으므로 여기도 함께 옮긴다. 못 옮기면 카드 셋이 한 자리로 합쳐져 예산이
#: 카드별로 재어지지 않는다.
_CARD_TOGGLE_PREFIX = "measure_on_"
_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class Budget:
    """한도. **판단값이라 ``assumptions.json`` 에서 온다.**"""

    body_lines: int
    notices: int


@dataclass
class Section:
    """화면 한 자리 — 진단 절 하나 또는 수단 카드 하나."""

    name: str
    body: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    captions: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.body or self.notices or self.captions)


def screen_budget() -> Budget:
    """한도를 기준 데이터에서 읽는다."""
    return Budget(
        body_lines=int(assumption("ui.body_line_budget")),
        notices=int(assumption("ui.notice_budget")),
    )


def _run(**state: object) -> AppTest:
    app = AppTest.from_file(str(APP), default_timeout=900)
    app.session_state["upload_bytes"] = SAMPLE.read_bytes()
    app.session_state["upload_name"] = SAMPLE.name
    app.session_state["contract_form"] = ContractForm(
        contract_type="general_b", voltage="high_a", option="II", contract_kw=6_000.0
    )
    for key in MEASURE_KEYS:
        app.session_state[f"measure_on_{key}"] = True
    for key, value in state.items():
        app.session_state[key] = value
    return app.run()


def _walk(block: object) -> list[tuple[str, str]]:
    """요소를 **화면 순서대로** 편다. ``children`` 이 사전이라 값 순서로 내려간다."""
    out: list[tuple[str, str]] = []
    children = getattr(block, "children", None)
    kids = list(children.values()) if isinstance(children, dict) else list(children or [])
    for child in kids:
        if getattr(child, "children", None):
            out.extend(_walk(child))
            continue
        try:  # 세션 값이 없는 위젯은 value 접근에서 KeyError 가 난다
            value = getattr(child, "value", None)
        except Exception:
            value = None
        label = getattr(child, "label", None)
        key = str(getattr(child, "key", "") or "")
        if key.startswith(_CARD_TOGGLE_PREFIX):
            # 개선안 카드의 시작. **라벨이 카드 이름이다.**
            out.append(("CardHeader", str(label or "")))
            continue
        out.append((type(child).__name__, str(value if value is not None else (label or ""))))
    return out


def measure(app: AppTest | None = None) -> list[Section]:
    """화면을 자리별로 갈라 센다."""
    running = app if app is not None else _run()
    sections: list[Section] = [Section("머리말")]

    def current() -> Section:
        return sections[-1]

    for kind, text in _walk(running.main):
        clean = text.strip()
        if not clean:
            continue
        if kind in {"Subheader", "Header", "CardHeader"}:
            sections.append(Section(_TAG.sub("", clean).strip().strip("*")))
            continue
        slot = current()
        if kind == "Error":
            slot.notices.append(f"[차단] {clean}")
        elif kind == "Markdown" and clean.startswith(CAUTION_ICON):
            slot.notices.append(f"[주의] {clean}")
        elif kind in {"Markdown", "Text"}:
            slot.body.append(clean)
        elif kind == "Caption":
            slot.captions.append(clean)
    return [item for item in sections if not item.empty]


def over_budget(sections: list[Section], budget: Budget | None = None) -> list[str]:
    """한도를 넘는 자리. **비어 있어야 한다.**"""
    limit = budget if budget is not None else screen_budget()
    out: list[str] = []
    for item in sections:
        if len(item.body) > limit.body_lines:
            out.append(f"{item.name} 본문 {len(item.body)}줄 (한도 {limit.body_lines})")
        if len(item.notices) > limit.notices:
            out.append(f"{item.name} 확인사항 {len(item.notices)}건 (한도 {limit.notices})")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="화면 예산 측정")
    parser.add_argument("--detail", action="store_true", help="자리마다 문구를 함께 낸다")
    args = parser.parse_args()

    limit = screen_budget()
    sections = measure()
    head = f"{'자리':28s} {'본문':>4s} {'확인':>4s} {'캡션':>4s}"
    print(f"{head}   한도 {limit.body_lines}/{limit.notices}")
    print("-" * 72)
    for item in sections:
        flag = ""
        if len(item.body) > limit.body_lines:
            flag += " 본문초과"
        if len(item.notices) > limit.notices:
            flag += " 확인초과"
        print(
            f"{item.name[:28]:28s} {len(item.body):4d} {len(item.notices):4d} "
            f"{len(item.captions):4d}  {flag}"
        )
        if args.detail:
            for line in item.body:
                print(f"    본문 · {line[:70]}")
            for line in item.notices:
                print(f"    확인 · {line[:70]}")

    exceeded = over_budget(sections, limit)
    if exceeded:
        print("\n한도를 넘는 자리:")
        for line in exceeded:
            print(f"  · {line}")
        return 1
    print("\n모든 자리가 한도 안입니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
