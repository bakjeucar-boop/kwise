"""기준 데이터 (요구사항서 12장).

**코드에 기본값이 남아 있으면 파일을 고쳐도 반영되지 않는다.** 값이 그럴듯해서
발견이 매우 늦다. 그 사고를 여기서 막는다.

원복·손상 복구는 **조용히 넘어가면 안 된다.** 갱신한 값으로 계산되는 줄 알고
결과를 쓰게 되기 때문이다.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from kwise.rules import (
    BACKUP_KEEP,
    RuleDataError,
    RuleOrigin,
    assumption,
    assumptions,
    confirm,
    describe_items,
    diff_from_defaults,
    expiry_warnings,
    load_defaults,
    read_history,
    reload_rules,
    restore_defaults,
    restore_item,
    restore_previous,
    rule_value,
    rules,
    set_value,
)
from kwise.rules.expiry import check_expiry
from kwise.rules.store import backup_dir, list_backups, write_backup
from kwise.rules.validate import validate_ruleset

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """저장소를 건드리지 않는 복사본. **출고값까지 함께 옮긴다.**"""
    root = tmp_path / "data"
    (root / "defaults").mkdir(parents=True)
    for name in ("rules_kr.json", "assumptions.json"):
        shutil.copy2(DATA_DIR / name, root / name)
        shutil.copy2(DATA_DIR / "defaults" / name, root / "defaults" / name)
    monkeypatch.setenv("KWISE_TARIFF_DIR", str(root))
    reload_rules()
    yield root
    reload_rules()


# --------------------------------------------------------------------- 외부화


def test_statutory_and_judgement_live_in_different_files(sandbox: Path) -> None:
    """**법령 유래와 판단값을 섞지 않는다.**

    섞으면 "이 숫자를 우리가 정한 것인가 법이 정한 것인가" 를 되물을 수 없다.
    """
    statutory = rules()
    judgement = assumptions()
    assert statutory.path.name == "rules_kr.json"
    assert judgement.path.name == "assumptions.json"
    assert not set(statutory.item_keys()) & set(judgement.item_keys())

    # 법령 유래는 근거 조문이 반드시 있다.
    for key in statutory.item_keys():
        assert statutory[key].source and statutory[key].source != "판단값", key
    for key in judgement.item_keys():
        assert judgement[key].source == "판단값", key


def test_law_derived_values_are_where_they_should_be(sandbox: Path) -> None:
    assert rule_value("power_factor.lagging_standard_pct") == 92.0
    assert rule_value("power_factor.leading_standard_pct") == 95.0
    assert rule_value("power_factor.adjustment_per_percent") == 0.002
    assert rule_value("demand.months") == [7, 8, 9, 12, 1, 2]
    assert rule_value("demand.contract_floor_ratio.default") == 0.30
    assert rule_value("demand.contract_floor_ratio.education_b") == 0.15
    assert rule_value("contract_type.threshold_kw.education") == 1000
    assert rule_value("dr.reference_capacity_kw") == 100.0
    assert rule_value("dr.national_max_contract_kw") == 200.0
    assert rule_value("dr.small_medium_industrial_max_kw") == 2000.0

    assert assumption("sensitivity.sharpness.conservative") == 0.85
    assert assumption("ess.round_trip") == 0.88
    assert assumption("ess.dod") == 0.90
    assert assumption("ess.payback_target_years") == 10.0
    assert assumption("dr.registration_percentile") == 0.10
    assert assumption("dr.low_load_multiple") == 1.2
    assert assumption("pv.area_per_kwp_m2") == 5.0


def test_missing_file_fails_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """**파일이 없으면 명확히 실패한다.** 코드 기본값으로 조용히 넘어가지 않는다."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("KWISE_TARIFF_DIR", str(empty))
    reload_rules()
    try:
        with pytest.raises(RuleDataError, match="코드에 기본값을 두지 않으므로"):
            rule_value("power_factor.lagging_standard_pct")
    finally:
        reload_rules()


def test_missing_file_is_created_from_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """출고값이 있으면 복사해 만든다."""
    root = tmp_path / "data"
    (root / "defaults").mkdir(parents=True)
    for name in ("rules_kr.json", "assumptions.json"):
        shutil.copy2(DATA_DIR / "defaults" / name, root / "defaults" / name)
    monkeypatch.setenv("KWISE_TARIFF_DIR", str(root))
    reload_rules()
    try:
        assert rule_value("power_factor.lagging_standard_pct") == 92.0
        assert (root / "rules_kr.json").is_file()
    finally:
        reload_rules()


def test_unknown_key_names_the_file(sandbox: Path) -> None:
    with pytest.raises(RuleDataError, match=r"rules_kr\.json"):
        rule_value("없는.항목")


# --------------------------------------------------------------------- 편집


def test_edit_updates_verified_on_and_records_history(sandbox: Path) -> None:
    """값을 고치면 확인일이 오늘로 갱신되고 이력이 남는다."""
    before = rules()["power_factor.lagging_standard_pct"]
    today = dt.date(2026, 9, 1)
    result = set_value("power_factor.lagging_standard_pct", 93.0, today=today)
    assert result.ok, result.message

    after = rules()["power_factor.lagging_standard_pct"]
    assert after.value == 93.0
    assert after.verified_on == today
    assert after.source == before.source  # 근거는 그대로다

    history = read_history()
    assert history[0].key == "power_factor.lagging_standard_pct"
    assert history[0].before == 92.0 and history[0].after == 93.0
    assert history[0].action == "edit"


def test_confirm_records_without_changing_the_value(sandbox: Path) -> None:
    """값은 그대로 두고 "확인함"만 기록하는 경로.

    이 경로가 없으면 만료 경고를 끄려고 값을 무의미하게 고치게 된다.
    """
    today = dt.date(2026, 9, 1)
    assert confirm("power_factor.lagging_standard_pct", today=today).ok
    item = rules()["power_factor.lagging_standard_pct"]
    assert item.value == 92.0  # 그대로
    assert item.verified_on == today
    assert read_history()[0].action == "confirm"


def test_validation_failure_does_not_save(sandbox: Path) -> None:
    """**검증에 실패하면 저장하지 않고 사유를 돌려준다.**"""
    result = set_value("power_factor.lagging_floor_pct", 99.0)  # 기준 92% 보다 크다
    assert not result.ok
    assert result.issues
    assert "하한 < 기준" in result.message
    assert rules()["power_factor.lagging_floor_pct"].value == 60.0  # 그대로다

    payload = json.loads((sandbox / "rules_kr.json").read_text(encoding="utf-8"))
    assert payload["items"]["power_factor.lagging_floor_pct"]["value"] == 60.0


@pytest.mark.parametrize(
    ("key", "value", "reason"),
    [
        ("demand.months", [7, 13], "월은 1~12"),
        ("demand.contract_floor_ratio.default", 1.5, "비율은 0.0~1.0"),
        ("dr.national_max_contract_kw", -1.0, "양수여야"),
        ("power_factor.lagging_rebate_cap_pct", 90.0, "기준 < 상한"),
        ("contract_type.threshold_kw.education", 100, "교육용"),
    ],
)
def test_validation_catches_broken_values(
    sandbox: Path, key: str, value: object, reason: str
) -> None:
    result = set_value(key, value)
    assert not result.ok
    assert reason in result.message


def test_judgement_validation_also_runs(sandbox: Path) -> None:
    """판단값에도 검증이 걸린다 — 첨예도 순서가 뒤집히면 저장하지 않는다."""
    result = set_value("sensitivity.sharpness.optimistic", 0.5)
    assert not result.ok
    assert "평탄 ≤ 기준 ≤ 첨예" in result.message


def test_shipped_defaults_are_valid() -> None:
    """저장소에 커밋된 출고값 자체가 검증을 통과해야 한다."""
    for origin in (RuleOrigin.STATUTORY, RuleOrigin.JUDGEMENT):
        assert not validate_ruleset(load_defaults(origin))


def test_rules_match_the_tariff_file() -> None:
    """계절·시간대·요일 규칙을 **두 벌로 두지 않는다.**

    요금표 파일에서 읽어 넣은 값이므로 어긋나면 어느 쪽이 맞는지 알 수 없게 된다.
    """
    from kwise.tariff import load_tariff

    source = load_tariff().source_path
    assert source is not None
    payload = json.loads(Path(source).read_text(encoding="utf-8"))
    factory = load_defaults(RuleOrigin.STATUTORY)
    assert factory["season.months"].value == payload["season_definition"]
    assert factory["tou.hours.mainland"].value == payload["tou_definition"]["mainland"]
    assert factory["day_rules.saturday"].value == payload["day_rules"]["saturday"]
    assert factory["day_rules.sunday"].value == payload["day_rules"]["sunday"]


# --------------------------------------------------------------------- 원복


def test_defaults_are_never_touched_by_edits(sandbox: Path) -> None:
    """**출고값은 읽기 전용이다.**"""
    before = (sandbox / "defaults" / "rules_kr.json").read_bytes()
    assert set_value("power_factor.lagging_standard_pct", 93.0).ok
    assert (sandbox / "defaults" / "rules_kr.json").read_bytes() == before


def test_backup_keeps_only_ten(sandbox: Path) -> None:
    """백업은 최근 10개만 남긴다. 더 두면 어느 것이 직전인지 고르기 어렵다."""
    for minute in range(BACKUP_KEEP + 5):
        write_backup(RuleOrigin.STATUTORY, now=dt.datetime(2026, 9, 1, 10, minute))
    assert len(list_backups(RuleOrigin.STATUTORY)) == BACKUP_KEEP


def test_restore_previous_returns_the_last_state(sandbox: Path) -> None:
    """① 직전 상태로 되돌리기."""
    assert set_value("power_factor.lagging_standard_pct", 93.0).ok
    assert rule_value("power_factor.lagging_standard_pct") == 93.0

    result = restore_previous(RuleOrigin.STATUTORY)
    assert result.ok, result.message
    assert rule_value("power_factor.lagging_standard_pct") == 92.0
    assert read_history()[0].action == "restore_previous"


def test_restore_defaults_previews_before_running(sandbox: Path) -> None:
    """② 출고 복원 — **무엇이 달라지는지 보여주고 확인받는다.**"""
    assert set_value("power_factor.lagging_standard_pct", 93.0).ok
    assert set_value("demand.window_months", 6).ok

    preview = restore_defaults(RuleOrigin.STATUTORY)
    assert not preview.ok  # 확인 전에는 실행하지 않는다
    assert "확인한 뒤" in preview.message
    keys = {change.key for change in preview.changes}
    assert keys == {"power_factor.lagging_standard_pct", "demand.window_months"}
    assert rule_value("demand.window_months") == 6  # 아직 그대로다

    done = restore_defaults(RuleOrigin.STATUTORY, confirmed=True)
    assert done.ok, done.message
    assert rule_value("power_factor.lagging_standard_pct") == 92.0
    assert rule_value("demand.window_months") == 12
    assert done.backup is not None and done.backup.is_file()  # 복원 직전도 남긴다


def test_restore_item_touches_only_one(sandbox: Path) -> None:
    """③ 항목별 원복 — 실무에서 가장 많이 쓰인다."""
    assert set_value("power_factor.lagging_standard_pct", 93.0).ok
    assert set_value("demand.window_months", 6).ok

    result = restore_item("demand.window_months")
    assert result.ok, result.message
    assert rule_value("demand.window_months") == 12
    assert rule_value("power_factor.lagging_standard_pct") == 93.0  # 건드리지 않았다
    assert read_history()[0].action == "restore_item"


def test_diff_from_defaults_lists_changed_items(sandbox: Path) -> None:
    assert not diff_from_defaults(RuleOrigin.STATUTORY)
    assert set_value("power_factor.lagging_standard_pct", 93.0).ok
    diffs = diff_from_defaults(RuleOrigin.STATUTORY)
    assert [item.key for item in diffs] == ["power_factor.lagging_standard_pct"]
    assert diffs[0].current == 93.0 and diffs[0].default == 92.0
    assert diffs[0].status == "변경"


def test_describe_items_marks_changed_rows(sandbox: Path) -> None:
    """UI 표 — 값·근거·확인일·경과가 한 줄에 있어야 고칠 근거가 생긴다."""
    assert set_value("power_factor.lagging_standard_pct", 93.0, today=dt.date(2026, 9, 1)).ok
    views = {item.key: item for item in describe_items(today=dt.date(2026, 9, 1))}
    changed = views["power_factor.lagging_standard_pct"]
    assert changed.changed_from_default
    assert changed.default_value == 92.0
    assert changed.source.startswith("기본공급약관")
    assert changed.months_since_verified == pytest.approx(0.0, abs=0.1)
    row = changed.as_row()
    assert set(row) >= {"항목", "이름", "값", "구분", "근거", "확인일", "출고값", "변경됨"}


# --------------------------------------------------------------------- 손상 복구


def test_corrupt_file_recovers_from_backup_and_says_so(sandbox: Path) -> None:
    """**조용히 넘어가지 않는다.** 복구 사실을 반드시 알린다."""
    assert set_value("power_factor.lagging_standard_pct", 93.0).ok  # 백업이 생긴다
    (sandbox / "rules_kr.json").write_text("{망가진 JSON", encoding="utf-8")
    reload_rules()

    recovered = rules()
    assert recovered.recovered_from
    assert "백업" in recovered.recovered_from
    assert (sandbox / "rules_kr.json.damaged").is_file()  # 손상본을 남긴다
    # 백업은 편집 **직전** 상태이므로 92% 다.
    assert recovered.value("power_factor.lagging_standard_pct") == 92.0


def test_corrupt_file_falls_back_to_defaults(sandbox: Path) -> None:
    """백업이 없으면 출고값으로. 이때도 알린다."""
    shutil.rmtree(backup_dir(), ignore_errors=True)
    (sandbox / "assumptions.json").write_text("[]", encoding="utf-8")
    reload_rules()

    recovered = assumptions()
    assert "출고 기본값" in recovered.recovered_from
    assert recovered.value("ess.round_trip") == 0.88


def test_corrupt_with_no_recovery_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "data"
    root.mkdir(parents=True)
    (root / "rules_kr.json").write_text("{", encoding="utf-8")
    monkeypatch.setenv("KWISE_TARIFF_DIR", str(root))
    reload_rules()
    try:
        with pytest.raises(RuleDataError, match="손상"):
            rules()
    finally:
        reload_rules()


# --------------------------------------------------------------------- 만료


def test_expiry_warns_after_the_threshold(sandbox: Path) -> None:
    """확인일로부터 임계를 넘기면 경고한다."""
    assert not check_expiry(rules(), assumptions(), today=dt.date(2026, 9, 1))

    # 약관 임계는 24개월이다. 26개월 뒤로 보낸다.
    late = dt.date(2028, 10, 1)
    warnings = check_expiry(rules(), assumptions(), today=late)
    assert warnings
    keys = {item.key for item in warnings}
    assert "power_factor.lagging_standard_pct" in keys
    verified = next(item for item in warnings if item.key == "power_factor.lagging_standard_pct")
    assert verified.basis == "확인일"
    assert verified.link  # 원문 확인처를 함께 안내한다
    assert "확인" in verified.message()

    # **확인일이 없는 항목은 시행일로 잰다.** 지금은 모든 항목에 확인일이 있으므로
    # (14세션에 DR 네 항목을 확인해 채웠다) 하나를 지워 그 경로를 확인한다.
    stripped = json.loads((sandbox / "rules_kr.json").read_text(encoding="utf-8"))
    stripped["items"]["dr.max_events_per_day"].pop("verified_on")
    (sandbox / "rules_kr.json").write_text(
        json.dumps(stripped, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    reload_rules()
    pending = [
        item
        for item in check_expiry(rules(), assumptions(), today=late)
        if item.key == "dr.max_events_per_day"
    ]
    assert pending and pending[0].basis == "시행일"


def test_expiry_thresholds_come_from_assumptions(sandbox: Path) -> None:
    """임계값도 파일에 있다 — 코드에 두면 '왜 12개월인가' 를 물을 곳이 없다."""
    assert assumption("expiry.tariff_months") == 12
    assert assumption("expiry.statute_months") == 24
    assert assumption("expiry.reference_months") == 24

    # 임계를 늘리면 경고가 사라져야 한다.
    late = dt.date(2028, 10, 1)
    assert check_expiry(rules(), assumptions(), today=late)
    assert set_value("expiry.statute_months", 120).ok
    assert set_value("expiry.reference_months", 120).ok
    assert set_value("expiry.tariff_months", 120).ok
    assert not check_expiry(rules(), assumptions(), today=late)


def test_expiry_includes_weather(sandbox: Path) -> None:
    """기상은 **전년도 미확보**로 만료를 판정한다.

    회귀에서는 사전 취득분이 격리되어 비어 있으므로(conftest) 실제 저장소 경로를
    직접 준다 — 격리된 빈 경로로 재면 늘 경고가 나 시험이 되지 않는다.
    """
    from kwise.rules.expiry import weather_expiry

    archive = PROJECT_ROOT / "data" / "weather"
    if not archive.is_dir():
        pytest.skip("사전 취득분이 없습니다.")

    # 사전 취득분이 2023~2025 이므로 2027년 기준이면 2026년분이 없다.
    warning = weather_expiry(today=dt.date(2027, 3, 1), root=archive)
    assert warning is not None
    assert "2026" in warning.label
    assert "fetch_weather.py" in warning.detail

    # 2026년 기준이면 2025년분이 있으므로 경고가 없다.
    assert weather_expiry(today=dt.date(2026, 3, 1), root=archive) is None


def test_expiry_warnings_bundle_everything(sandbox: Path) -> None:
    """기준 데이터와 기상 경고를 한 번에 낸다."""
    warnings = expiry_warnings(today=dt.date(2028, 10, 1))
    assert warnings
    assert any(item.scope == "약관·규칙" for item in warnings)
    assert any(item.scope == "기상" for item in warnings)

    # 기상을 빼고 볼 수도 있어야 한다.
    only_rules = expiry_warnings(today=dt.date(2028, 10, 1), include_weather=False)
    assert not any(item.scope == "기상" for item in only_rules)


# ===================================================================== 9세션 — 남은 상수


def test_계약_기본값이_코드에_남아_있지_않다() -> None:
    r"""``diagnose\contract.py`` 의 모듈 상수 둘을 파일로 옮겼다 (9세션).

    **코드에 기본값이 남으면 파일을 고쳐도 반영되지 않는다.** 값이 그럴듯해서
    결과를 다 쓰고 나서야 발견된다 — 8세션 준비에서 스무남은 개를 옮길 때
    빠뜨렸던 둘이다.
    """
    import kwise.diagnose.contract as module

    assert not hasattr(module, "DEFAULT_POWER_FACTOR_PCT")
    assert not hasattr(module, "DEFAULT_MARGIN_RATIO")


def test_간주_역률과_여유율을_파일에서_읽는다() -> None:
    from kwise.diagnose import deemed_power_factor_pct, default_margin_ratio
    from kwise.rules import assumption, rule_value

    assert deemed_power_factor_pct() == float(rule_value("power_factor.deemed_lagging_pct"))
    assert default_margin_ratio() == float(assumption("contract.margin_ratio"))


def test_간주_역률은_기준과_별개_항목이다() -> None:
    """근거 조문이 다르다 — 제42조(간주) vs 제41조(기준).

    한 값으로 묶어 두면 기준만 개정됐을 때 **모르는 고객의 역률까지 따라 움직여**
    실측하지 않은 값으로 조정액이 생긴다.
    """
    from kwise.rules import rules

    deemed = rules()["power_factor.deemed_lagging_pct"]
    standard = rules()["power_factor.lagging_standard_pct"]
    assert deemed.value == standard.value == 92.0  # 오늘은 같다
    assert "제42조" in deemed.source
    assert deemed.source != standard.source  # 근거는 다르다


def test_역률을_모르면_간주값으로_채운다() -> None:
    """그 값에서 추가·감액이 정확히 0 원이다 (제42조)."""
    from kwise.tariff import power_factor_charge
    from kwise.tariff.power_factor import deemed_lagging_pct

    charge = power_factor_charge(1_000_000.0)
    assert charge.lagging_pct == deemed_lagging_pct()
    assert charge.total_won == 0.0


def test_계약_정보_기본_역률이_생성_시점에_읽힌다(sandbox: Path) -> None:
    """데이터클래스 필드 기본값으로 두면 **import 시점에 고정**된다.

    ``default_factory`` 라야 화면에서 파일을 고친 뒤 만든 객체가 새 값을 쓴다.
    """
    from kwise.diagnose import ContractInfo
    from kwise.rules import set_value
    from kwise.tariff import TariffSelection

    selection = TariffSelection("general_b", "high_a", "I")
    assert ContractInfo(selection).power_factor_pct == 92.0

    assert set_value("power_factor.deemed_lagging_pct", 88.0).ok
    assert ContractInfo(selection).power_factor_pct == 88.0


def test_여유율_기본값이_호출_시점에_읽힌다(sandbox: Path) -> None:
    from kwise.diagnose import default_margin_ratio
    from kwise.measures import evaluate_contract_adjustment
    from kwise.rules import set_value

    assert default_margin_ratio() == 0.1
    assert set_value("contract.margin_ratio", 0.25).ok
    assert default_margin_ratio() == 0.25
    _ = evaluate_contract_adjustment  # 서명이 None 기본값을 받는지는 mypy 가 본다
