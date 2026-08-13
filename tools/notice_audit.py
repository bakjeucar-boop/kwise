"""안내 사실 ID 감사 — **ID 없는 Notice 를 센다** (20세션).

소스 훑기로는 닿지 않는 자리가 있다. 계산 모듈이 문자열을 그대로 안내 목록에
넣거나, 이관하지 않은 결과 객체가 문자열 리스트를 들고 화면으로 나가는 길이다
(15세션에 ``general_b`` 가 이 경로로 새어 나갔다). **실제로 한 벌 돌려 보고**
발신처가 낸 안내를 전부 모아 센다.

    .venv\\Scripts\\python.exe tools\\notice_audit.py

내는 것 셋.

    ① 사실 ID 가 없는 안내 — **0 이어야 한다**
    ② 사실별 발신 횟수 — 두 곳 이상에서 나는 사실이 중복 후보다
    ③ 중복 제거 전후 건수 — 사실 ID 가 실제로 무엇을 접었는지
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from kwise.compare import compare_combinations, default_combinations
from kwise.diagnose import ContractInfo, diagnose
from kwise.io import load_usage
from kwise.measures import (
    EssCostInput,
    evaluate_contract_adjustment,
    evaluate_demand_response,
    evaluate_ess,
    evaluate_power_factor,
    evaluate_tariff_switch,
    light_band_mask,
)
from kwise.notices import Notice, dedupe, unidentified
from kwise.quality import check_quality
from kwise.tariff import TariffSelection, calculate_bill, load_tariff

SAMPLE = Path("input") / "사용량조회_20240429.csv"
SELECTION = TariffSelection("general_b", "high_a", "I")
CONTRACT_KW = 5_500.0
ESS_COST_WON_PER_KW = 615_231.0


def collect(path: Path) -> dict[str, tuple[Notice, ...]]:
    """샘플 한 벌을 돌려 결과 객체마다 안내를 모은다. **순차 처리다.**"""
    usage = load_usage(path)
    table = load_tariff()
    quality = check_quality(usage)
    bill = calculate_bill(usage, table, SELECTION, quality=quality)
    diagnosis = diagnose(
        usage, table, ContractInfo(SELECTION, contract_kw=CONTRACT_KW), quality=quality
    )
    switch = evaluate_tariff_switch(usage, table, SELECTION, quality=quality)
    contract = evaluate_contract_adjustment(
        usage, bill, contract_kw=CONTRACT_KW, contract_floor_ratio=0.3
    )
    power_factor = evaluate_power_factor(usage, table, SELECTION, baseline=bill, quality=quality)
    ess = evaluate_ess(
        usage,
        table,
        SELECTION,
        target_kw=5_200.0,
        cost=EssCostInput.of_unit_cost(ESS_COST_WON_PER_KW),
        charge_mask=light_band_mask(usage, table, selection=SELECTION),
        baseline=bill,
        quality=quality,
    )
    dr = evaluate_demand_response(diagnosis.dr) if diagnosis.dr is not None else None
    specs = default_combinations(
        current_selection=SELECTION,
        best_selection=TariffSelection("general_b", "high_a", "II"),
        ess_target_kw=5_000.0,
        ess_unit_cost_won_per_kw=ESS_COST_WON_PER_KW,
        contract_kw=CONTRACT_KW,
        contract_floor_ratio=0.3,
    )
    comparison = compare_combinations(usage, table, specs, baseline_bill=bill, quality=quality)

    sources: dict[str, object | None] = {
        "quality": quality,
        "bill": bill,
        "diagnosis": diagnosis,
        "tariff_switch": switch,
        "contract": contract,
        "power_factor": power_factor,
        "ess": ess,
        "demand_response": dr,
        "comparison": comparison,
    }
    return {
        name: tuple(getattr(source, "notices", ()))
        for name, source in sources.items()
        if source is not None
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="안내 사실 ID 감사")
    parser.add_argument("--usage", type=Path, default=SAMPLE)
    args = parser.parse_args()

    groups = collect(args.usage)
    everything = [item for items in groups.values() for item in items]
    blank = unidentified(everything)

    print(f"발신처가 낸 안내 {len(everything)}건 · 결과 객체 {len(groups)}개")
    print(f"사실 ID 없는 안내 {len(blank)}건")
    for item in blank:
        print(f"    [{item.severity}] {item.text[:70]}")

    print("\n같은 사실을 두 곳 이상에서 냄 (중복 후보):")
    counts = Counter(item.fact_base for item in everything if item.fact)
    for fact, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])):
        if count > 1:
            print(f"    {count}회  {fact}")

    print("\n결과 객체별 중복 제거 전 → 후:")
    for name, items in groups.items():
        print(f"    {name:16s} {len(items):3d} → {len(dedupe(items)):3d}")
    return 1 if blank else 0


if __name__ == "__main__":
    raise SystemExit(main())
