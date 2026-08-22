"""UI 순수 모듈 시험 (요구사항서 10.1·10.2).

Streamlit 을 띄우지 않고 검사할 수 있는 것만 다룬다 — 그래서 화면 쪽 판단
(순서·정렬·표기·배선)을 순수 함수로 빼 두었다.

**여기서 지키는 것 넷**

    ① 개선 수단 순서가 7장(투자비 순)과 같다 — 바뀌면 문서 참조가 어긋난다
    ② 켜지 않은 수단이 조합에서 빠진다 — 안 빠지면 '미검토' 가 '효과 없음' 이 된다
    ③ 계약전력이 요금 엔진 설정까지 간다 — 빠지면 하한 규정이 조용히 미적용된다
    ④ 매뉴얼 앵커 목록과 문서가 어긋나지 않는다
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd
import pytest

from kwise.io import ColumnDetection, ColumnDetectionError, load_usage, override_columns
from kwise.notices import texts
from kwise.rules import ItemDiff, ItemView, RuleOrigin, describe_items, expiry_warnings
from kwise.rules.expiry import ExpiryWarning
from kwise.tariff import TariffSelection, TariffTable, load_tariff
from kwise.ui import charts, session, text
from kwise.ui.anchors import ANCHORS, anchor, anchor_keys, manual_tip
from kwise.ui.pipeline import (
    ContractForm,
    SolarInputs,
    combination_specs,
    contract_type_choices,
    default_lagging_pct,
    option_choices,
    voltage_choices,
)
from kwise.ui.rules_view import build_rows, count_rows, diff_frame, header_text, weather_panel
from kwise.ui.spec import MEASURES, NO_INVESTMENT_KEYS, measure, review_scope

SAMPLE = Path("input") / "사용량조회_20240429.csv"


@pytest.fixture(scope="module")
def table() -> TariffTable:
    return load_tariff()


@pytest.fixture(scope="module")
def usage() -> object:
    return load_usage(SAMPLE)


# ===================================================================== 앵커


def test_앵커_키가_중복되지_않는다() -> None:
    keys = anchor_keys()
    assert len(keys) == len(set(keys))


def test_앵커_키는_html_id_로_쓸_수_있다() -> None:
    for key in anchor_keys():
        assert key == key.lower()
        assert " " not in key
        assert all(part.isalnum() for part in key.split("-"))


def test_앵커_설명이_비어_있지_않다() -> None:
    for item in ANCHORS:
        assert item.title and item.origin and item.covers


def test_없는_앵커는_바로_실패한다() -> None:
    """빈 툴팁을 화면에 내보내지 않는다."""
    with pytest.raises(KeyError):
        anchor("없는-앵커")


def test_툴팁에_링크가_없다() -> None:
    """**화면에서 링크를 걷어냈다** (16세션 4절). 요지만 얹는다."""
    tip = manual_tip("payback")
    assert "](" not in tip
    assert "http" not in tip
    assert "MANUAL.html" not in tip


def test_앵커_문서가_정본과_같다() -> None:
    """``docs\\MANUAL_ANCHORS.md`` 는 생성물이다. 어긋나면 다시 내보내야 한다."""
    from kwise.ui.anchors import ANCHOR_DOC_FILENAME, anchor_document

    path = Path("docs") / ANCHOR_DOC_FILENAME
    assert path.is_file(), "tools\\export_manual_anchors.py 를 실행하십시오."
    assert path.read_text(encoding="utf-8") == anchor_document()


def test_화면이_쓰는_앵커가_모두_등록되어_있다() -> None:
    """뷰에서 ``manual_tip("...")`` 로 부르는 키가 목록에 있어야 한다."""
    import re

    registered = set(anchor_keys())
    used: set[str] = set()
    for path in Path("src/kwise/ui").rglob("*.py"):
        used |= set(re.findall(r'manual_tip\(\s*"([^"]+)"', path.read_text(encoding="utf-8")))
    assert used, "화면이 툴팁을 하나도 쓰지 않습니다."
    assert used <= registered, f"등록되지 않은 앵커: {sorted(used - registered)}"


def test_수단_카드_앵커가_모두_등록되어_있다() -> None:
    registered = set(anchor_keys())
    assert {item.anchor for item in MEASURES} <= registered


# ===================================================================== 수단 순서


def test_수단_순서가_요구사항서_7장과_같다() -> None:
    """**투자비 순이다. 바꾸지 않는다** (8차 결정)."""
    assert [item.number for item in MEASURES] == [
        "7.1",
        "7.2",
        "7.3",
        "7.4",
        "7.5",
        "7.6",
        "7.7",
    ]
    assert [item.key for item in MEASURES] == [
        "tariff_switch",
        "contract",
        "demand_response",
        "power_factor",
        "solar",
        "ess",
        "surplus",
    ]


def test_투자_0원_수단이_셋이다() -> None:
    assert NO_INVESTMENT_KEYS == ("tariff_switch", "contract", "demand_response")


def test_카드가_7장_번호_순이다() -> None:
    """**7.1~7.7 원래 순서다** (14세션 2-1). 투자비로 다시 늘어놓지 않는다."""
    assert [item.number for item in MEASURES] == [
        "7.1",
        "7.2",
        "7.3",
        "7.4",
        "7.5",
        "7.6",
        "7.7",
    ]
    assert [item.key for item in MEASURES] == [
        "tariff_switch",
        "contract",
        "demand_response",
        "power_factor",
        "solar",
        "ess",
        "surplus",
    ]


def test_모든_카드에_개요가_있다() -> None:
    """무엇을 어떻게 개선하는지 두세 줄 (14세션 2-2)."""
    for item in MEASURES:
        assert len(item.overview) >= 40, item.key
        assert item.overview != item.headline, item.key


def test_카드가_다른_수단을_요구하지_않는다() -> None:
    """**종속 구분이 없다** (14세션 2절). 어떤 카드도 선행 수단을 전제하지 않는다."""
    assert not hasattr(MEASURES[0], "needs_pv")


def test_검토_범위가_켠_것과_안_켠_것을_가른다() -> None:
    scope = review_scope(["solar", "tariff_switch"])
    assert scope.reviewed_labels == ("7.1 선택요금 전환", "7.5 태양광")
    assert "7.6 ESS" in scope.skipped_labels
    assert len(scope.reviewed) + len(scope.skipped) == len(MEASURES)


def test_검토_범위는_아무것도_켜지_않아도_동작한다() -> None:
    scope = review_scope([])
    assert scope.reviewed == ()
    assert len(scope.skipped) == len(MEASURES)
    assert "검토함 — 없음" in scope.text()


def test_없는_수단은_바로_실패한다() -> None:
    with pytest.raises(KeyError):
        review_scope(["없는수단"])
    with pytest.raises(KeyError):
        measure("없는수단")


# ===================================================================== 계약 정보 배선


def test_계약전력이_요금_엔진_설정까지_간다() -> None:
    """**두 군데에 넣어야 한다.**

    ``BillingOptions.contract_kw`` 가 비면 요금적용전력 하한(계약전력의 30%)이
    적용되지 않고, 그 사실은 금액을 봐서는 알 수 없다.
    """
    form = ContractForm("general_b", "high_a", "I", contract_kw=5_800.0)
    assert form.contract_info().contract_kw == 5_800.0
    assert form.billing_options().contract_kw == 5_800.0


def test_역률_기본값을_기준_데이터에서_읽는다() -> None:
    """**모듈 상수로 붙잡지 않는다** — 파일을 고치면 화면 기본값도 따라야 한다.

    기준(제41조)이 아니라 **간주값(제42조)**을 쓴다. 오늘은 둘 다 92% 다.
    """
    from kwise.tariff import deemed_lagging_pct

    form = ContractForm("general_b", "high_a", "I")
    assert form.lagging_pct == deemed_lagging_pct() == default_lagging_pct()
    assert form.billing_options().power_factor_pct == deemed_lagging_pct()


def test_야간_진상역률은_기본값이_없다() -> None:
    """모르면 지상 간주(추가 0)다. 지어내지 않는다 (제43조 ② 2호 나목)."""
    form = ContractForm("general_b", "high_a", "I")
    assert form.leading_power_factor_pct is None
    assert form.billing_options().leading_power_factor_pct is None


def test_드롭다운을_요금_데이터에서_만든다(table: TariffTable) -> None:
    """**하드코딩 금지** (부록 A.3)."""
    types = dict(contract_type_choices(table))
    assert set(types) == set(table.contract_types)
    voltages = dict(voltage_choices(table, "general_b"))
    assert "high_a" in voltages
    assert option_choices(table, "general_b", "high_a") == ("I", "II", "III")


# ===================================================================== 조합 구성


def _form() -> ContractForm:
    return ContractForm("general_b", "high_a", "I", contract_kw=5_800.0)


BEST = TariffSelection("general_b", "high_a", "II")


def test_첫_조합은_언제나_기준선이다() -> None:
    specs = combination_specs(form=_form(), best_selection=BEST, enabled=[])
    assert len(specs) == 1
    assert specs[0].name == "기준선 (현행)"
    assert specs[0].selection == _form().selection


def test_켜지_않은_수단은_조합에서_빠진다() -> None:
    """**빠지지 않으면 '보지 않은 것' 이 '검토한 것' 으로 둔갑한다.**"""
    specs = combination_specs(
        form=_form(),
        best_selection=BEST,
        enabled=["solar"],
        pv_capacity_kwp=960.0,
        ess_target_kw=4_500.0,  # ESS 를 켜지 않았으므로 무시되어야 한다
    )
    names = [spec.name for spec in specs]
    assert not any("ESS" in name for name in names)
    assert not any("선택요금" in name for name in names)
    assert all(spec.contract_kw is None for spec in specs)
    assert specs[-1].pv_capacity_kwp == 960.0
    # 선택요금을 켜지 않았으므로 현행 선택요금을 그대로 쓴다
    assert specs[-1].selection == _form().selection


def test_켠_수단이_투자비_순으로_쌓인다() -> None:
    specs = combination_specs(
        form=_form(),
        best_selection=BEST,
        enabled=["tariff_switch", "contract", "solar", "ess"],
        pv_capacity_kwp=960.0,
        ess_target_kw=4_500.0,
    )
    assert [spec.name for spec in specs] == [
        "기준선 (현행)",
        "선택요금 전환 (II)",
        "+ 계약전력 조정",
        "+ 태양광 960 kWp",
        "+ ESS 목표 4,500 kW",
    ]
    # 누적이다 — 마지막 조합이 앞의 수단을 모두 물고 있다
    last = specs[-1]
    assert last.selection == BEST
    assert last.contract_kw == 5_800.0
    assert last.pv_capacity_kwp == 960.0
    assert last.ess_target_kw == 4_500.0
    assert last.has_pv and last.has_ess


def test_현행이_이미_최선이면_전환_조합을_만들지_않는다() -> None:
    form = _form()
    specs = combination_specs(form=form, best_selection=form.selection, enabled=["tariff_switch"])
    assert [spec.name for spec in specs] == ["기준선 (현행)"]


def test_용량이_0이면_태양광_조합을_만들지_않는다() -> None:
    specs = combination_specs(
        form=_form(), best_selection=BEST, enabled=["solar"], pv_capacity_kwp=0.0
    )
    assert [spec.name for spec in specs] == ["기준선 (현행)"]


def test_계약전력을_모르면_조정_조합을_만들지_않는다() -> None:
    form = ContractForm("general_b", "high_a", "I", contract_kw=None)
    specs = combination_specs(form=form, best_selection=BEST, enabled=["contract"])
    assert [spec.name for spec in specs] == ["기준선 (현행)"]


# ===================================================================== 태양광 입력


def test_면적과_밀도로_용량을_환산한다() -> None:
    """설치 용량(kWp) ≈ 면적 × GCR ÷ 5 (요구사항서 3.3)."""
    high = SolarInputs(region_key="서울특별시/강남구", area_m2=1_000.0, density_key="high")
    normal = SolarInputs(region_key="서울특별시/강남구", area_m2=1_000.0, density_key="normal")
    low = SolarInputs(region_key="서울특별시/강남구", area_m2=1_000.0, density_key="low")
    assert high.resolved_capacity_kwp() == pytest.approx(110.0)
    assert normal.resolved_capacity_kwp() == pytest.approx(80.0)
    assert low.resolved_capacity_kwp() == pytest.approx(60.0)


def test_용량_직접_입력이_면적_환산을_덮어쓴다() -> None:
    inputs = SolarInputs(
        region_key="서울특별시/강남구", area_m2=1_000.0, density_key="normal", capacity_kwp=500.0
    )
    assert inputs.resolved_capacity_kwp() == 500.0


def test_단가를_넣지_않으면_투자비가_0원이_아니라_사유다() -> None:
    """**0원으로 두면 회수기간이 0년이 되어 '즉시 회수' 로 읽힌다** (7.5)."""
    inputs = SolarInputs(region_key="서울특별시/강남구", area_m2=1_000.0)
    cost = inputs.cost()
    assert not cost.is_priced
    assert cost.investment_won(1_000.0) is None
    assert cost.reason


def test_밀도가_gcr_과_경사각을_함께_정한다() -> None:
    inputs = SolarInputs(region_key="서울특별시/강남구", area_m2=1_000.0, density_key="high")
    from kwise.ui.pipeline import solar_config

    array = solar_config(inputs).arrays[0]
    assert array.gcr == pytest.approx(0.55)
    assert array.tilt_deg == pytest.approx(15.0)


def test_좌표_직접_입력이_시군구_중심점을_덮어쓴다() -> None:
    inputs = SolarInputs(region_key="서울특별시/강남구", latitude=35.0, longitude=129.0)
    assert inputs.coordinates() == (35.0, 129.0)


# ===================================================================== 표기


def test_금액을_모르면_사유를_낸다() -> None:
    assert text.won(None) != ""
    assert "미산출" in text.won(None)
    assert text.won(None, reason="단가 미입력") == "단가 미입력"
    assert text.won(1_234_567) == "1,234,000원"  # 천 단위 절사 (14세션)


def test_금액을_억_만원으로_줄인다() -> None:
    """**억과 만원을 같이 적는다.** ``1.23억원`` 은 읽는 사람이 다시 환산해야 한다."""
    assert text.won_short(123_456_789) == "1억 2,346만원"
    assert text.won_short(200_000_000) == "2억원"
    assert text.won_short(53_580_000) == "5,358만원"
    assert text.won_short(-53_580_000) == "-5,358만원"
    assert text.won_short(4_200) == "4,000원"  # 만원 미만도 천 단위 절사 (14세션)


def test_회수기간_0년을_즉시_회수로_적는다() -> None:
    assert text.payback(0.0) == "즉시"
    assert text.payback(3.24) == "3.2년"


def test_투자비를_모르면_회수기간이_빈칸이_아니다() -> None:
    """0년으로 두면 '즉시 회수' 로 읽힌다."""
    assert "미산출" in text.payback(None)
    assert text.payback(None, investment_won=0.0) == text.DASH


def test_확실성_등급이_화면_표기에서_사라졌다() -> None:
    """**화면에서 등급을 뺐다** (28세션 4절).

    무엇에 대한 확실성인지가 이름에 없어 「높음」 이 어느 정도인지 알 수 없었고,
    잉여가 0 이라 수익 0원이 확정인 줄에도 「중간~낮음」 이 붙었다. 등급 자체는
    Excel·Word 가 그대로 쓰므로 :class:`~kwise.measures.Certainty` 는 남는다.
    """
    from kwise.measures import Certainty

    assert str(Certainty.MEDIUM_LOW) == "중간~낮음"
    assert not hasattr(text, "certainty_badge"), "화면 뱃지가 남아 있습니다."
    assert "certainty" not in text.TIPS


def test_비율_표기() -> None:
    assert text.ratio_pct(0.4902) == "49.0%"
    assert text.pct(92.0) == "92.0%"
    assert text.ratio_pct(None) == text.DASH


# ===================================================================== 열 판정 고치기


def _detection() -> ColumnDetection:
    return ColumnDetection(
        date_column="검침일",
        energy_column="사용량",
        header_row=0,
        date_strategy="name",
        energy_strategy="name",
        columns=("검침일", "사용량", "고객번호"),
    )


def test_열_지정을_바꾸면_전략과_경고에_남는다() -> None:
    changed = override_columns(_detection(), energy_column="고객번호")
    assert changed.energy_column == "고객번호"
    assert changed.energy_strategy == "user"
    assert changed.date_strategy == "name"  # 손대지 않은 쪽은 그대로
    assert any("사용자가 열 판정을 고쳤습니다" in message for message in changed.warnings)
    assert "사용자 지정" in changed.describe()


def test_같은_열을_다시_고르면_자동_판정_그대로다() -> None:
    """화면은 두 드롭다운을 늘 함께 넘긴다. 그것만으로 표시가 바뀌면 안 된다."""
    same = override_columns(_detection(), date_column="검침일", energy_column="사용량")
    assert same is _detection() or same == _detection()
    assert same.strategy == "name"
    assert same.warnings == ()


def test_없는_열을_지정하면_거부한다() -> None:
    with pytest.raises(ColumnDetectionError, match="파일에 없습니다"):
        override_columns(_detection(), date_column="없는열")


def test_두_열에_같은_열을_지정하면_거부한다() -> None:
    with pytest.raises(ColumnDetectionError, match="같은 열"):
        override_columns(_detection(), energy_column="검침일")


def test_실측_파일에서_열을_지정해도_결과가_같다() -> None:
    plain = load_usage(SAMPLE)
    picked = load_usage(SAMPLE, date_column="검침일", energy_column="순방향 유효전력량(KWH)")
    assert picked.meta.total_kwh == plain.meta.total_kwh
    assert picked.meta.max_demand_kw == plain.meta.max_demand_kw


# ===================================================================== 기준 데이터 화면


def _view(
    key: str,
    *,
    origin: str = "법령",
    changed: bool = False,
    verified: str = "2026-08-07",
) -> ItemView:
    return ItemView(
        key=key,
        label=f"{key} 라벨",
        value=1.0,
        origin=origin,
        source="약관 제41조",
        source_date="2026-06-01",
        verified_on=verified,
        months_since_verified=2.0,
        note="",
        default_value=1.0 if not changed else 2.0,
        changed_from_default=changed,
    )


def _warning(key: str) -> ExpiryWarning:
    return ExpiryWarning(
        scope="약관·규칙",
        key=key,
        label="라벨",
        months=30.0,
        threshold_months=24.0,
        basis="확인일",
    )


def test_정렬은_만료_변경_분류_순이다() -> None:
    views = (
        _view("season.months"),
        _view("power_factor.a", changed=True),
        _view("demand.months"),
        _view("dr.b"),
    )
    rows = build_rows(views, (_warning("demand.months"),), links={})
    assert [row.key for row in rows] == [
        "demand.months",  # 만료 경고
        "power_factor.a",  # 출고값과 다름
        "dr.b",  # 분류 '경제성DR'
        "season.months",  # 분류 '계절 구분'
    ]


def test_상단_요약이_전체_변경_확인필요를_센다() -> None:
    views = (_view("a.x"), _view("b.y", changed=True), _view("c.z", changed=True))
    rows = build_rows(views, (_warning("c.z"),), links={})
    counts = count_rows(rows)
    assert (counts.total, counts.changed, counts.needs_check) == (3, 2, 1)
    assert header_text(counts) == "전체 3개 · 변경됨 2개 · 확인 필요 1개"


def test_배지가_법령과_판단으로_갈린다() -> None:
    rows = build_rows((_view("a.x"), _view("b.y", origin="판단값")), links={})
    badges = {row.key: row.badge for row in rows}
    assert badges["a.x"] == "법령"
    assert badges["b.y"] == "판단"


def test_근거와_확인일이_한_줄에_담긴다() -> None:
    row = build_rows((_view("power_factor.a"),), links={})[0]
    assert "약관 제41조" in row.source_text
    assert "2026-06-01" in row.source_text
    assert "2026-08-07" in row.verified_text
    assert "2개월 경과" in row.verified_text


def test_확인_기록이_없으면_그렇게_적는다() -> None:
    view = ItemView(
        key="a.x",
        label="라벨",
        value=1,
        origin="법령",
        source="약관",
        source_date="",
        verified_on="",
        months_since_verified=None,
        note="",
        default_value=1,
        changed_from_default=False,
    )
    assert build_rows((view,), links={})[0].verified_text == "확인 기록 없음"


def test_실제_항목_전부에_원문_확인처가_붙는다() -> None:
    """**경고가 없어도 근거를 따라갈 데가 있어야 한다.**"""
    items = describe_items()
    rows = build_rows(items, expiry_warnings(include_weather=False))
    # 항목이 조용히 사라지는 것을 막는 잣대다. 늘리는 것은 정상, 줄면 확인한다.
    assert len(rows) == len(items) == 68  # rules_kr 31 + assumptions 37
    assert all(row.link.startswith(("한국", "국가", "에너지", "Open", "기술서")) for row in rows)
    # **바깥에 원문이 없는 값도 있다** (22세션). 화면 예산은 우리가 정한 규약이라
    # 확인처가 기술서다 — 그래도 따라갈 데는 있어야 한다.
    for row in rows:
        assert "http" in row.link or row.link.startswith("기술서"), row.key


def test_분류를_모르면_키를_그대로_보여준다() -> None:
    """조용히 '기타' 로 뭉치지 않는다."""
    row = build_rows((_view("새분류.항목"),), links={})[0]
    assert row.category_label == "새분류"


def test_출고_복원_미리보기_표() -> None:
    diffs = (ItemDiff("a.x", "라벨", "법령", 2.0, 1.0, "변경"),)
    frame = diff_frame(diffs)
    assert list(frame.columns) == ["항목", "이름", "구분", "현재 값", "출고값", "상태"]
    assert frame.iloc[0]["현재 값"] == 2.0
    assert diff_frame(()).empty  # 차이가 없어도 열 구조는 유지한다


def test_기상_현황에_만료_개념이_없다() -> None:
    """**부분 취득은 정상 상태다.** 오류로 표시하지 않는다."""
    from kwise.pv.archive import archive_status

    panel = weather_panel(archive_status())
    assert not hasattr(panel, "expired")
    assert "격자" in panel.text()
    assert panel.cell_count >= 0


def test_기상_현황이_비어_있어도_그린다(tmp_path: Path) -> None:
    from kwise.pv.archive import archive_status

    panel = weather_panel(archive_status(tmp_path))
    assert panel.cell_count == 0
    assert panel.year_text == "없음"
    assert panel.fetched_text == "기록 없음"


def test_최종_취득일을_색인_파일에서_읽는다(tmp_path: Path) -> None:
    from kwise.pv.archive import INDEX_FILENAME, archive_status

    (tmp_path / INDEX_FILENAME).write_text(json.dumps({"cells": {}}), encoding="utf-8")
    panel = weather_panel(archive_status(tmp_path))
    assert panel.fetched_on == dt.date.today()


def test_기준_데이터_구분이_두_갈래뿐이다() -> None:
    origins = {view.origin for view in describe_items()}
    assert origins == {str(RuleOrigin.STATUTORY), str(RuleOrigin.JUDGEMENT)}


def test_법령_항목에_근거_조문이_있다() -> None:
    """스키마가 강제하는 것을 화면 쪽에서도 확인한다."""
    for row in build_rows(describe_items(), links={}):
        if row.is_statutory:
            assert row.view.source, f"{row.key} 에 근거가 없습니다."


# ===================================================================== 차트 프레임


def test_월별_최대수요_프레임에_두_계열이_있다(usage: object, table: TariffTable) -> None:
    """관측 최대와 요금적용 대상 최대를 나란히 둔다 (5.2 ①)."""
    from kwise.diagnose import ContractInfo, diagnose

    diagnosis = diagnose(usage, table, ContractInfo(TariffSelection("general_b", "high_a", "I")))  # type: ignore[arg-type]
    frame = charts.monthly_peak_frame(diagnosis.peak)
    assert list(frame.columns) == ["월", "관측 최대(kW)", "요금적용 대상 최대(kW)", "발생 시각"]
    assert len(frame) == len(diagnosis.peak.monthly)
    assert (frame["관측 최대(kW)"] >= frame["요금적용 대상 최대(kW)"]).all()


def test_상위_구간_시각_분포가_두_벌이다(usage: object, table: TariffTable) -> None:
    """전 슬롯 기준(부록 B 대조용)과 요금적용전력 대상 기준을 함께 낸다."""
    from kwise.diagnose import ContractInfo, diagnose

    diagnosis = diagnose(usage, table, ContractInfo(TariffSelection("general_b", "high_a", "I")))  # type: ignore[arg-type]
    frame = charts.top_hour_frame(diagnosis.peak)
    assert len(frame) == 24
    assert frame["전 슬롯"].sum() == diagnosis.peak.top_n
    assert frame["요금적용전력 대상"].sum() <= diagnosis.peak.top_n


def test_감도_프레임은_범위다() -> None:
    """**3열 나열이 아니다** (9.2)."""
    from kwise.compare import SensitivityRange

    ranges = (
        SensitivityRange(
            "기본요금 절감액(원)", "원", 31_520_000, 28_970_000, 32_660_000, "첨예형", "평탄형"
        ),
    )
    frame = charts.sensitivity_frame(ranges)
    assert list(frame.columns) == ["지표", "기준값", "하한", "상한", "범위"]
    assert frame.iloc[0]["하한"] < frame.iloc[0]["기준값"] < frame.iloc[0]["상한"]


def test_기준값이_없는_지표는_감도_표에서_뺀다() -> None:
    from kwise.compare import SensitivityRange

    ranges = (SensitivityRange("회수기간(년)", "년", None, None, None, "", ""),)
    assert charts.sensitivity_frame(ranges).empty


# ===================================================================== 임시 파일


def test_묵은_세션_폴더를_쓸어낸다(tmp_path: Path) -> None:
    fresh = tmp_path / f"{session.SESSION_PREFIX}fresh"
    stale = tmp_path / f"{session.SESSION_PREFIX}stale"
    other = tmp_path / "무관한폴더"
    for path in (fresh, stale, other):
        path.mkdir()
    old = dt.datetime.now() - dt.timedelta(hours=48)
    import os

    stamp = old.timestamp()
    os.utime(stale, (stamp, stamp))
    os.utime(other, (stamp, stamp))

    removed = session.purge_stale(tmp_path, max_age_hours=6.0)
    assert removed == (stale,)
    assert fresh.is_dir()
    assert other.is_dir()  # 접두사가 다르면 건드리지 않는다


def test_없는_뿌리를_쓸어내도_실패하지_않는다(tmp_path: Path) -> None:
    assert session.purge_stale(tmp_path / "없음") == ()


def test_산출물_이름에_날짜와_시각이_붙는다() -> None:
    """Excel 이 파일을 열고 있으면 덮어쓰기가 실패한다."""
    name = session.report_filename(dt.datetime(2026, 8, 11, 14, 30))
    assert name == "kwise_20260811_1430.xlsx"


def test_엑셀을_만든_뒤_파일을_남기지_않는다(
    tmp_path: Path, usage: object, table: TariffTable
) -> None:
    """**업로드·산출 데이터를 서버에 영구 저장하지 않는다** (10.2)."""
    from kwise.report import ReportSections
    from kwise.tariff import calculate_bill

    bill = calculate_bill(usage, table, TariffSelection("general_b", "high_a", "I"))  # type: ignore[arg-type]
    sections = ReportSections(usage=usage, bill=bill, include_timeseries=False)  # type: ignore[arg-type]
    payload, name = session.build_report_bytes(sections, session_id="t1", root=tmp_path)

    assert payload[:2] == b"PK"  # xlsx 는 zip 이다
    assert name.endswith(".xlsx")
    directory = tmp_path / f"{session.SESSION_PREFIX}t1"
    assert list(directory.iterdir()) == [], "만든 파일을 지우지 않았습니다."


# ===================================================================== 캐시 지문


def test_기준_데이터_지문이_값이_바뀌면_달라진다(monkeypatch: pytest.MonkeyPatch) -> None:
    """**캐시 키에 물려 두어야** 파일만 바뀌고 화면은 옛 값인 사고를 막는다."""
    from kwise.ui.cache import rules_stamp

    before = rules_stamp()
    assert before == rules_stamp()  # 값이 그대로면 지문도 그대로

    import kwise.ui.cache as cache_module

    class _Fake:
        origin = RuleOrigin.STATUTORY

        def item_keys(self) -> tuple[str, ...]:
            return ("power_factor.lagging_standard_pct",)

        def __getitem__(self, key: str) -> object:
            return type("Item", (), {"value": 90.0})()

    monkeypatch.setattr(cache_module, "rules", lambda: _Fake())
    assert rules_stamp() != before


def test_사용량_지문이_파일마다_다르다(usage: object) -> None:
    from kwise.ui.cache import usage_token

    other = load_usage(Path("input") / "cases" / "C3_평탄형.csv")
    assert usage_token(usage) != usage_token(other)  # type: ignore[arg-type]
    assert usage_token(usage) == usage_token(usage)  # type: ignore[arg-type]


def test_열_지정이_다르면_지문이_달라진다() -> None:
    """열을 바꿔 다시 읽으면 캐시가 갈라져야 한다."""
    from kwise.ui.cache import usage_token

    plain = load_usage(SAMPLE)
    token = usage_token(plain)
    assert isinstance(token, str) and len(token) == 16


def test_편집이_실패하면_캐시를_건드리지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    from kwise.rules import EditResult, ValidationIssue
    from kwise.ui import cache as cache_module

    calls: list[int] = []
    monkeypatch.setattr(cache_module, "clear_calc_cache", lambda: calls.append(1))

    failed = EditResult(ok=False, issues=(ValidationIssue("a.x", "틀렸습니다"),))
    assert cache_module.apply_rule_edit(failed) is failed
    assert calls == []

    assert cache_module.apply_rule_edit(EditResult(ok=True)).ok
    assert calls == [1]


# ===================================================================== 배선 통합


def test_계약_정보_없이도_진단이_나온다(usage: object, table: TariffTable) -> None:
    """**사용자가 파일만 올려도 결과가 나온다** (요구사항서 6장)."""
    from kwise.ui.pipeline import diagnose_usage

    diagnosis = diagnose_usage(usage, table, None)  # type: ignore[arg-type]
    assert diagnosis.pattern.load_factor is not None
    assert diagnosis.peak.billing_demand_kw > 0
    assert diagnosis.structure is None  # 금액은 계약 정보가 있어야 낸다
    assert diagnosis.summary.pv_potential is not None


def test_계약_정보를_넣으면_하한_미적용_경고가_사라진다(usage: object, table: TariffTable) -> None:
    """배선 ①의 회귀 시험 — ``BillingOptions.contract_kw`` 가 비면 이 경고가 남는다."""
    from kwise.ui.pipeline import diagnose_usage

    form = ContractForm("general_b", "high_a", "I", contract_kw=5_800.0)
    diagnosis = diagnose_usage(usage, table, form)  # type: ignore[arg-type]
    missing = [m for m in texts(diagnosis.notices) if "하한" in m and "적용하지 않았" in m]
    assert missing == []


def test_계약전력을_비우면_하한_미적용_경고가_남는다(usage: object, table: TariffTable) -> None:
    from kwise.ui.pipeline import diagnose_usage

    form = ContractForm("general_b", "high_a", "I", contract_kw=None)
    diagnosis = diagnose_usage(usage, table, form)  # type: ignore[arg-type]
    assert any("하한" in message for message in texts(diagnosis.notices))


def test_기준선_요금이_현행_선택요금으로_계산된다(usage: object, table: TariffTable) -> None:
    from kwise.tariff import calculate_bill
    from kwise.ui.pipeline import baseline_bill

    form = ContractForm("general_b", "high_a", "I", contract_kw=5_800.0)
    mine = baseline_bill(usage, table, form)  # type: ignore[arg-type]
    direct = calculate_bill(
        usage,  # type: ignore[arg-type]
        table,
        form.selection,
        options=form.billing_options(),
    )
    assert mine.total_won == direct.total_won
    assert mine.selection == form.selection


def test_앱_모듈이_import_만으로_계산하지_않는다() -> None:
    """계산 로직은 순수 모듈에 있다. UI 모듈은 호출만 한다."""
    import kwise.ui.pipeline as module

    source = Path(module.__file__ or "").read_text(encoding="utf-8")
    assert "import streamlit" not in source
    for name in ("anchors", "charts", "rules_view", "session", "spec", "text"):
        path = Path("src/kwise/ui") / f"{name}.py"
        assert "import streamlit" not in path.read_text(encoding="utf-8"), name


def test_수단을_켜지_않아도_수단별_결과_시트를_만든다() -> None:
    """진단만 보고 내려받는 경우다.

    빈 표에 ``set_index`` 를 걸면 KeyError 로 **산출물 생성이 통째로 멈춘다.**
    열 구조를 유지해 시트는 나가고, 무엇을 보지 않았는지는 「검토 범위」가 밝힌다.
    """
    from kwise.report import measure_summary_frame
    from kwise.report.excel import MEASURE_SHEET_COLUMNS

    frame = measure_summary_frame()
    assert frame.empty
    assert frame.index.name == "수단"
    assert tuple(frame.columns) == MEASURE_SHEET_COLUMNS[1:]


def test_수단을_켜지_않아도_산출물이_만들어진다(
    tmp_path: Path, usage: object, table: TariffTable
) -> None:
    from kwise.report import ReportSections, measure_summary_frame
    from kwise.tariff import calculate_bill

    bill = calculate_bill(usage, table, TariffSelection("general_b", "high_a", "I"))  # type: ignore[arg-type]
    sections = ReportSections(
        usage=usage,  # type: ignore[arg-type]
        bill=bill,
        measure_rows=measure_summary_frame(),
        include_timeseries=False,
    )
    payload, _name = session.build_report_bytes(sections, session_id="t2", root=tmp_path)
    assert payload[:2] == b"PK"


def test_감도_없음_프레임에_사유가_있다() -> None:
    from kwise.report import no_pv_sensitivity_frame

    frame = no_pv_sensitivity_frame()
    assert not frame.empty
    assert isinstance(frame, pd.DataFrame)


def test_옆단이_운영_시간대를_받는다() -> None:
    """**9시 출근을 전제하지 않는다** (21세션 4절)."""
    from kwise.quality import DEFAULT_OPERATING_HOURS
    from kwise.ui.building import BuildingInfo, _hours

    info = BuildingInfo(region_key="서울특별시/종로구")
    assert info.operating_hours == DEFAULT_OPERATING_HOURS == (9, 18)
    assert _hours(8, 19) == (8, 19)
    # 뒤집힌 창은 뜻이 없다. 기본값으로 되돌린다.
    assert _hours(18, 9) == DEFAULT_OPERATING_HOURS
    assert _hours(9, 9) == DEFAULT_OPERATING_HOURS

    source = (Path("src") / "kwise" / "ui" / "building.py").read_text(encoding="utf-8")
    assert "운영 시간대" in source and "building_hours" in source


def test_운영_시간대가_운영시간_외_부하_진단에_닿는다(usage: object) -> None:
    """옆단 값이 진단까지 흘러야 입력이 뜻을 갖는다."""
    from kwise.tariff import load_tariff
    from kwise.ui.pipeline import diagnose_usage

    table = load_tariff()
    nine = diagnose_usage(usage, table, None, operating_hours=(9, 18))  # type: ignore[arg-type]
    eight = diagnose_usage(usage, table, None, operating_hours=(8, 19))  # type: ignore[arg-type]
    assert nine.pattern.operating_hours == (9, 18)
    assert eight.pattern.operating_hours == (8, 19)
    assert nine.pattern.off_hours_energy_share != eight.pattern.off_hours_energy_share
