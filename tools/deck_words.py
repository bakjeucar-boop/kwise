r"""덱 벌의 **화면에 그려진 글자**를 뜨고, 낱말이 몇 벌에 서는지 세고, 두 판을 맞댄다 (S207 4절).

판마다 같은 스크래치를 새로 지었다 — S192(실물 어긋남 열여덟) · S196(잣대
열셋) · S200 · S204(자리 열아홉) · S205(자리 열아홉) · S206(다섯 자리) ·
S207(네 자리). 하는 일은 늘 셋이었다.

    뜬다   ``tools\\render_deck.py`` 의 벌마다 앱을 띄워 그려진 글자를 모은다
           (``tools\\screen_audit.py`` 의 :func:`collect` 를 그대로 쓴다)
    센다   준 낱말이 **어느 벌 몇 자리**에 서는가
    맞댄다 앞 판 스냅과 줄 단위로 대어 **갈린 줄**과 **수치 조각의 차**를 낸다

**소스 리터럴을 찾지 않는다.** 실물에 그려진 요소만 본다.

    .venv\\Scripts\\python.exe tools\\deck_words.py                       벌 목록과 스냅 자리
    .venv\\Scripts\\python.exe tools\\deck_words.py --snap 앞.json         19벌을 떠 담는다
    .venv\\Scripts\\python.exe tools\\deck_words.py --snap 뒤.json --case small-ind-a1
    .venv\\Scripts\\python.exe tools\\deck_words.py --count 도입 후 --count 목표 역률
    .venv\\Scripts\\python.exe tools\\deck_words.py --read 앞.json --count 도입 후
    .venv\\Scripts\\python.exe tools\\deck_words.py --diff 앞.json 뒤.json

19벌을 다 뜨는 데 1번 PC 에서 **5분 남짓** 걸린다 (S207 2절 · 324.8초).
``--case`` 로 좁히면 벌마다 5~40초다. **담아 둔 스냅이 있으면 ``--read`` 로
읽는다** — 그 판은 앱을 안 띄운다 (S209 2절 · 앞 판이 ``--count`` 로 300.3초를
버린 자리다).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

import render_deck  # noqa: E402
import screen_audit  # noqa: E402

__all__ = ["MONEY", "Diff", "count_words", "diff", "snap", "snap_dir"]

#: 금액·수치 조각. **고치는 판마다 「금액이 한 원도 안 움직였다」 를 이것으로 센다.**
MONEY = re.compile(r"-?[\d,]*\d(?:\.\d+)?\s*(?:원/년|만원/년|억원|만원|원|kW|kWh|kWp|%p|%)")


def snap_dir() -> Path:
    raw = os.environ.get("PROJECT_CACHE")
    return (Path(raw) if raw else PROJECT_ROOT / "cache") / "deck_words"


def _cases(picked: list[str] | None) -> tuple[render_deck.Case, ...]:
    if not picked:
        return render_deck.CASES
    return tuple(render_deck.BY_KEY[key] for key in picked)


def snap(picked: list[str] | None = None) -> dict[str, list[list[str]]]:
    """벌마다 앱을 띄워 그려진 글자를 모은다. 죽은 벌은 건너뛰고 그 사실을 찍는다."""
    out: dict[str, list[list[str]]] = {}
    for case in _cases(picked):
        started = time.time()
        app = render_deck.build_app(case)
        app.run()
        if app.exception:
            print(f"!! {case.key} 화면이 죽었다: {app.exception}")
            continue
        rows = [[ln.where, ln.kind, ln.slot, ln.text] for ln in screen_audit.collect(app)]
        out[case.key] = rows
        print(f"    {case.key}\t{len(rows):,}줄\t{time.time() - started:,.1f}초")
    return out


def count_words(data: dict[str, list[list[str]]], words: list[str]) -> dict[str, Counter[str]]:
    """낱말마다 «벌 → 그 벌에서 선 자리 수»."""
    return {
        word: Counter(
            {
                key: hits
                for key, rows in data.items()
                if (hits := sum(word in row[3] for row in rows))
            }
        )
        for word in words
    }


@dataclass(frozen=True)
class Diff:
    """두 스냅을 맞댄 값."""

    맞댄줄: int
    벌별: Counter[str]
    """벌 → 그 벌에서 갈린 줄 수."""
    갈린짝: Counter[tuple[str, str]]
    """(옛 글자, 새 글자) → 그 짝이 선 벌 수."""
    어긋난벌: tuple[str, ...]
    """한쪽에만 있거나 줄 수가 다른 벌. **맞대지 않았다.**"""
    조각앞: Counter[str]
    조각뒤: Counter[str]

    @property
    def 갈린줄(self) -> int:
        return sum(self.벌별.values())


def diff(before: dict[str, list[list[str]]], after: dict[str, list[list[str]]]) -> Diff:
    """두 스냅을 줄 단위로 맞댄다. **줄 수가 다른 벌은 따로 적는다.**"""
    갈린: Counter[tuple[str, str]] = Counter()
    벌별: Counter[str] = Counter()
    어긋난벌: list[str] = []
    맞댄줄 = 0
    for key in sorted(set(before) | set(after)):
        b, a = before.get(key), after.get(key)
        if b is None or a is None or len(b) != len(a):
            어긋난벌.append(key)
            continue
        맞댄줄 += len(b)
        for rb, ra in zip(b, a, strict=True):
            if rb != ra:
                갈린[(rb[3], ra[3])] += 1
                벌별[key] += 1

    def 조각(data: dict[str, list[list[str]]]) -> Counter[str]:
        counter: Counter[str] = Counter()
        for rows in data.values():
            for row in rows:
                counter.update(MONEY.findall(row[3]))
        return counter

    return Diff(맞댄줄, 벌별, 갈린, tuple(어긋난벌), 조각(before), 조각(after))


def _load(path: Path) -> dict[str, list[list[str]]]:
    data: dict[str, list[list[str]]] = json.loads(path.read_text(encoding="utf-8"))
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="덱 벌 화면 글자를 뜨고 세고 맞댄다")
    parser.add_argument("--snap", type=Path, help="스냅을 담을 파일 (없는 이름이면 새로 만든다)")
    parser.add_argument("--case", action="append", help="벌 이름. 여러 번 줄 수 있다")
    parser.add_argument("--count", action="append", help="셀 낱말. 여러 번 줄 수 있다")
    parser.add_argument("--diff", nargs=2, type=Path, metavar=("앞", "뒤"), help="두 스냅을 맞댄다")
    parser.add_argument("--read", type=Path, help="뜨지 않고 담아 둔 스냅을 읽는다")
    args = parser.parse_args()

    if args.diff:
        before, after = (_load(path) for path in args.diff)
        report = diff(before, after)
        print(f"맞댄 줄 {report.맞댄줄:,} · 갈린 줄 {report.갈린줄}")
        if report.어긋난벌:
            print(f"!! 줄 수가 어긋난 벌 — {', '.join(report.어긋난벌)}")
        print("\n갈린 줄이 있는 벌")
        for key, count in report.벌별.most_common():
            print(f"    {key}\t{count}줄")
        print("\n갈린 글자 짝")
        for (old, new), count in report.갈린짝.most_common():
            print(f"    {count:3d}벌  「{old[:110]}」")
            print(f"          → 「{new[:110]}」")
        앞, 뒤 = sum(report.조각앞.values()), sum(report.조각뒤.values())
        print(f"\n수치 조각 — 앞 {앞:,}개 · 뒤 {뒤:,}개")
        print(f"    준 것 {dict(report.조각앞 - report.조각뒤) or '없다'}")
        print(f"    는 것 {dict(report.조각뒤 - report.조각앞) or '없다'}")
        return 0

    if args.snap is None and args.read is None and not args.count:
        # **인자 없이 돌면 그 판의 값이 다 나온다** — 벌 목록과 담는 자리.
        print(f"스냅 자리 — {snap_dir()}")
        print(f"\n덱 {len(render_deck.CASES)}벌")
        for case in render_deck.CASES:
            pct = case.power_factor_pct
            꼬리 = f"역률 {pct:,.1f}%" if pct is not None else "역률 간주"
            print(f"    {case.key:30s} {case.contract_type} · {case.voltage} · {꼬리}")
        found = sorted(snap_dir().glob("*.json")) if snap_dir().is_dir() else []
        print(f"\n담아 둔 스냅 {len(found)}개")
        for path in found:
            print(f"    {path.name}")
        return 0

    # **읽는 길** (S209 2절). ``--count`` 가 늘 19벌을 다시 떠 5분 남짓을
    # 버렸다 — 읽는 자리가 ``--diff`` 하나뿐이었다.
    if args.read is not None:
        path = args.read if args.read.is_absolute() else snap_dir() / args.read
        data = _load(path)
        줄 = sum(len(rows) for rows in data.values())
        print(f"읽었다 — {path}\n합 {len(data)}벌 · {줄:,}줄")
    else:
        started = time.time()
        print(f"뜬다 — {len(_cases(args.case))}벌")
        data = snap(args.case)
        줄 = sum(len(rows) for rows in data.values())
        print(f"합 {len(data)}벌 · {줄:,}줄 · {time.time() - started:,.1f}초")

    if args.snap is not None:
        path = args.snap if args.snap.is_absolute() else snap_dir() / args.snap
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
        print(f"담았다 — {path}")

    for word, hits in count_words(data, args.count or []).items():
        print(f"\n「{word}」 — {len(hits)}벌 · 자리 {sum(hits.values())}")
        for key, count in hits.most_common():
            print(f"    {key}\t{count}자리")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
