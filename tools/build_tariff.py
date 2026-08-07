"""요금표 엑셀 → ``data\\tariff_*.json`` 변환기 (요구사항서 부록 A.1·A.4).

    .venv\\Scripts\\python.exe tools\\build_tariff.py
    .venv\\Scripts\\python.exe tools\\build_tariff.py --contracts "일반용(을)" "산업용(을)"
    .venv\\Scripts\\python.exe tools\\build_tariff.py --check      # 쓰지 않고 검증만

**단가를 수기로 입력하지 않는다.** 변환 로직은
:mod:`kwise.tariff.source_excel` 에 있고 여기는 얇은 진입점이다 — 그래야
계절 열 매핑 같은 핵심 규칙을 단위테스트로 고정할 수 있다.

변환 뒤에는 반드시

    ① 부록 A.2 검증 4규칙을 돌리고 (실패하면 쓰지 않는다)
    ② 기존 일반용(을) 고압A·B 60개 값이 그대로인지 대조한다

를 거친다. 사람이 옮긴 표에는 오차가 섞이기 때문이다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from kwise.tariff.schema import BANDS, TariffTable, parse_tariff  # noqa: E402
from kwise.tariff.source_excel import CONTRACT_RULES, build_payload  # noqa: E402
from kwise.tariff.validate import validate_tariff  # noqa: E402

DEFAULT_SOURCE = PROJECT_ROOT / "data" / "source" / "2026_KEPCO_Electricity_Tariff.xlsx"
DEFAULT_EFFECTIVE_DATE = "2026-06-01"

# 정정이 아닌 이상 바뀌어서는 안 되는 회귀 기준. 3세션 이후 이 값으로 요금을 냈다.
REGRESSION_CONTRACT = "general_b"
REGRESSION_VOLTAGES = ("high_a", "high_b")
REGRESSION_OPTIONS = ("I", "II", "III")


def compare_regression(before: TariffTable, after: TariffTable) -> list[str]:
    """일반용(을) 고압A·B 의 60개 값 (기본 6 + 전력량 54) 을 대조한다."""
    differences: list[str] = []
    old = before.contract_types[REGRESSION_CONTRACT]
    new = after.contract_types[REGRESSION_CONTRACT]
    for voltage in REGRESSION_VOLTAGES:
        for option in REGRESSION_OPTIONS:
            low, high = old.voltages[voltage].options[option], new.voltages[voltage].options[option]
            if low.base_won_per_kw != high.base_won_per_kw:
                differences.append(
                    f"{voltage}/{option}/기본요금: {low.base_won_per_kw} → {high.base_won_per_kw}"
                )
            for season in sorted(low.energy):
                for band in BANDS:
                    before_rate, after_rate = low.rate(season, band), high.rate(season, band)
                    if abs(before_rate - after_rate) > 1e-9:
                        differences.append(
                            f"{voltage}/{option}/{season}/{band}: {before_rate} → {after_rate}"
                        )
    return differences


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="요금표 엑셀")
    parser.add_argument(
        "--output", type=Path, default=None, help="출력 JSON. 기본은 data\\tariff_kr_<시행일>.json"
    )
    parser.add_argument("--effective-date", default=DEFAULT_EFFECTIVE_DATE)
    parser.add_argument(
        "--contracts",
        nargs="*",
        default=None,
        help=f"넣을 종별 (엑셀 표기). 기본 전부: {', '.join(CONTRACT_RULES)}",
    )
    parser.add_argument("--check", action="store_true", help="파일을 쓰지 않고 검증만 한다")
    args = parser.parse_args(argv)

    if not args.source.is_file():
        print(f"요금표 엑셀이 없습니다: {args.source}", file=sys.stderr)
        return 2

    payload: dict[str, Any] = build_payload(
        args.source, effective_date=args.effective_date, contracts=args.contracts
    )
    table = parse_tariff(payload)

    findings = validate_tariff(table)
    for finding in findings:
        print(f"  {finding}", file=sys.stderr)
    if findings:
        print(f"부록 A.2 검증에서 {len(findings)}건이 걸렸습니다. 쓰지 않습니다.", file=sys.stderr)
        return 1

    # 파일명 순 마지막이 최신이므로 시행일을 YYYYMMDD 로 붙인다 (load_tariff 규약).
    stamp = str(args.effective_date).replace("-", "")
    target: Path = args.output or (PROJECT_ROOT / "data" / f"tariff_kr_{stamp}.json")

    if target.is_file() and REGRESSION_CONTRACT in table.contract_types:
        with target.open(encoding="utf-8") as stream:
            previous = parse_tariff(json.load(stream))
        if REGRESSION_CONTRACT in previous.contract_types:
            differences = compare_regression(previous, table)
            if differences:
                print("기존 일반용(을) 값이 바뀌었습니다:", file=sys.stderr)
                for line in differences:
                    print(f"  {line}", file=sys.stderr)
                return 1
            print(f"회귀 대조 통과 — {REGRESSION_CONTRACT} 60개 값이 그대로입니다.")

    contracts = ", ".join(
        f"{key}({len(value.voltages)}전압)" for key, value in sorted(table.contract_types.items())
    )
    print(f"검증 통과 — {len(table.contract_types)}개 종별: {contracts}")

    if args.check:
        print("--check 이므로 파일을 쓰지 않았습니다.")
        return 0

    # 요금 데이터는 사람이 읽고 대조하는 파일이다. 들여쓰기와 한글을 유지한다.
    with target.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(f"썼습니다: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
