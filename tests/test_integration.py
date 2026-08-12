"""전체 기능 통합 시험 (15세션 5절).

**화면을 실제로 띄워 전 경로를 훑는다.** 순수 함수 시험이 다 통과해도 배선이
틀리면 화면에서만 드러난다 — 8·12·13세션에서 잡은 결함이 모두 그런 종류였다.

여기서 지키는 것 넷.

    5-1 화면 흐름   업로드 → 계약 → 1·2·3단계. 수단 0/1/전부. 켜고 끄기 반복
    5-2 산출물     Excel 시트·Word 장·내려받기 뒤 결과 유지
    5-3 경계       미업로드·미입력·잉여 0·기상 실패
    5-4 회귀       케이스 스터디 값·독립 평가·2↔3단계 일치·표기 규약

**기상은 격리되어 있다** (``conftest.isolated_weather_archive``). 태양광이 실제로
도는 경로는 ``weather`` 표식을 붙여 사전 취득분을 쓰게 한다 — 격리를 풀지 않으면
"API 실패 시 멈추는가" 같은 시험이 조용히 성공해 버린다.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from kwise.report import SHEET_ORDER
from kwise.ui.nav import RULES_PAGE, TABS
from kwise.ui.pipeline import ContractForm

APP = (Path("src") / "kwise" / "ui" / "app.py").resolve()
SAMPLE = Path("input") / "사용량조회_20240429.csv"
ALL_MEASURES = (
    "tariff_switch",
    "contract",
    "demand_response",
    "power_factor",
    "solar",
    "ess",
    "surplus",
)
# 요금에 영향을 주는 수단만. 태양광은 「계산」 단추를 눌러야 도는 별도 경로다.
BILLED_MEASURES = ("tariff_switch", "contract", "power_factor", "ess")


@pytest.fixture
def real_weather(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """사전 취득분을 쓴다. **격리를 푸는 유일한 자리다.**"""
    monkeypatch.delenv("KWISE_WEATHER_DIR", raising=False)
    yield


def _app(**state: object) -> AppTest:
    running = AppTest.from_file(str(APP), default_timeout=900)
    running.session_state["upload_bytes"] = SAMPLE.read_bytes()
    running.session_state["upload_name"] = SAMPLE.name
    running.session_state["contract_form"] = ContractForm(
        contract_type="general_b", voltage="high_a", option="I", contract_kw=6_000.0
    )
    for key, value in state.items():
        running.session_state[key] = value
    return running.run()


def _blank(**state: object) -> AppTest:
    """업로드도 계약 정보도 없는 상태."""
    running = AppTest.from_file(str(APP), default_timeout=900)
    for key, value in state.items():
        running.session_state[key] = value
    return running.run()


def _on(*keys: str) -> dict[str, object]:
    return {f"measure_on_{key}": True for key in keys}


# **탭 구조라 세 화면이 한 번에 그려진다** (16세션 1절). 지표 목록이 셋 이어
# 붙으므로 경계로 갈라 본다 — 1단계의 마지막은 「기본요금 비중」, 3단계의 처음은
# 「단순 합」이다.
_DIAGNOSE_LAST = "기본요금 비중"
_STAGE3_FIRST = "단순 합"


def _stage2_metrics(app: AppTest) -> list[tuple[str, str]]:
    """2단계 카드가 낸 지표만."""
    pairs = [(str(item.label), str(item.value)) for item in app.metric]
    labels = [label for label, _ in pairs]
    start = labels.index(_DIAGNOSE_LAST) + 1 if _DIAGNOSE_LAST in labels else 0
    end = labels.index(_STAGE3_FIRST) if _STAGE3_FIRST in labels else len(pairs)
    return pairs[start:end]


def _stage3_labels(app: AppTest) -> list[str]:
    labels = [str(item.label) for item in app.metric]
    return labels[labels.index(_STAGE3_FIRST) :] if _STAGE3_FIRST in labels else []


def _text(app: AppTest) -> str:
    parts = [
        str(item.value)
        for group in (app.markdown, app.caption, app.warning, app.error, app.info)
        for item in group
    ]
    parts += [f"{item.label} {item.value} {item.delta or ''}" for item in app.metric]
    parts += [str(item.value) for item in app.subheader]
    return "\n".join(parts)


# ===================================================================== 5-1 화면 흐름


def test_세_탭이_한_번에_뜬다() -> None:
    """**탭 구조다** (16세션 1절). 한 실행에서 셋이 모두 그려진다."""
    app = _app()
    assert not app.exception, app.exception
    headers = [str(item.value) for item in app.header]
    for tab in TABS:
        number = tab.split(" ", 1)[0]
        assert any(number in item for item in headers), (number, headers)


def test_기준_데이터_화면도_예외_없이_뜬다() -> None:
    app = _app(nav_page=RULES_PAGE)
    assert not app.exception, app.exception


@pytest.mark.parametrize("count", [0, 1, len(BILLED_MEASURES)])
def test_수단을_0개_1개_전부_켜도_3단계가_돈다(count: int) -> None:
    """**수단 0개가 정상 경로다** — 진단만 보고 받아 가는 경우다."""
    app = _app(**_on(*BILLED_MEASURES[:count]))
    assert not app.exception, app.exception
    body = _text(app)
    assert "개선안별 요약" in body
    if count == 0:
        assert "수단을 하나도 켜지 않았습니다" in body
    else:
        assert "단순 합" in body


def test_모든_수단을_켜도_2단계가_돈다() -> None:
    app = _app(**_on(*ALL_MEASURES))
    assert not app.exception, app.exception
    body = _text(app)
    # 태양광은 계산 전이라 안내만, 잉여는 0 이라 사실만 적는다.
    assert "「태양광 계산」 을 누르십시오" in body
    assert "태양광을 켜지 않아 잉여가 0 입니다." in body


def test_수단을_켜고_끄기를_반복해도_상태가_꼬이지_않는다() -> None:
    """토글을 되풀이해도 카드가 살아 있고 예외가 없어야 한다."""
    app = _app(**_on("tariff_switch"))
    for _ in range(2):
        app.toggle(key="measure_on_tariff_switch").set_value(False).run()
        assert not app.exception, app.exception
        app.toggle(key="measure_on_tariff_switch").set_value(True).run()
        assert not app.exception, app.exception
    assert any(item.label == "가장 유리한 요금제" for item in app.metric)


def test_옆단은_건물_정보다() -> None:
    """**계약 정보는 옆단에 없다** (16세션 2절). 건물이 아니라 계약이다."""
    app = _app()
    labels = [str(item.label) for item in app.sidebar.text_input]
    labels += [str(item.label) for item in app.sidebar.selectbox]
    labels += [str(item.label) for item in app.sidebar.number_input]
    assert set(labels) == {
        "건물명 (선택)",
        "용도 (선택)",
        "시도",
        "시군구",
        "연면적 (m², 선택)",
        "준공연도 (선택)",
    }
    banned = ("계약종별", "전압구분", "계약전력", "선택요금")
    assert not [item for item in labels if any(word in item for word in banned)], labels


def test_기준_데이터로_갔다_돌아온다() -> None:
    """**단계가 아니라 설정이다** — 옆단 하단의 별도 진입점 (16세션 1절)."""
    app = _app()
    app.sidebar.button(key="nav_rules").click().run()
    assert app.session_state["nav_page"] == RULES_PAGE
    assert not app.exception, app.exception
    app.sidebar.button(key="nav_analysis").click().run()
    assert not app.exception, app.exception
    assert any("1단계" in str(item.value) for item in app.header)


def test_용도를_고르면_계약종별_후보가_좁아진다() -> None:
    """좁히기이지 판정이 아니다 (16세션 2절)."""
    from kwise.tariff import load_tariff
    from kwise.tariff.schema import list_contract_types
    from kwise.ui.building import narrow_contract_types

    every = list_contract_types(load_tariff())
    assert len(narrow_contract_types(every, "")) == len(every)
    factory = narrow_contract_types(every, "factory")
    assert factory and all(key.startswith("industrial") for key, _label in factory)
    school = narrow_contract_types(every, "school")
    assert school and all(key.startswith("education") for key, _label in school)
    # 모르는 용도면 전 종별이다 — 고를 것이 사라지면 입력을 못 한다.
    assert narrow_contract_types(every, "없는용도") == every


def test_연면적을_넣으면_원단위가_나온다() -> None:
    """**없으면 줄 자체가 없다.** 국내 평균과 견주지 않는다 (16세션 2절)."""
    from kwise.ui.building import BuildingInfo, intensity_kwh_per_m2

    assert intensity_kwh_per_m2(1_000.0, None) is None
    assert intensity_kwh_per_m2(1_000.0, BuildingInfo(region_key="서울특별시/강남구")) is None
    value = intensity_kwh_per_m2(
        1_000.0, BuildingInfo(region_key="서울특별시/강남구", floor_area_m2=250.0)
    )
    assert value == pytest.approx(4.0)


def test_원단위가_화면에_한_줄로_나온다() -> None:
    from kwise.ui.building import BuildingInfo

    app = _app(building_info=BuildingInfo(region_key="서울특별시/강남구", floor_area_m2=20_000.0))
    assert not app.exception, app.exception
    assert "원단위" in _text(app)
    assert "국내 평균" not in _text(app)


def test_연면적이_없으면_원단위_줄이_없다() -> None:
    app = _app()
    assert "원단위" not in _text(app)


@pytest.mark.parametrize("key", BILLED_MEASURES)
def test_수단을_함께_켜도_카드_값이_불변이다(key: str) -> None:
    """**독립 평가** (14세션 2절) — 다른 수단을 켜고 끄든 이 카드의 숫자가 같다."""
    alone = _stage2_metrics(_app(**_on(key)))
    both = _stage2_metrics(_app(**_on(*BILLED_MEASURES)))
    span = len(alone)
    assert alone
    assert any(both[start : start + span] == alone for start in range(len(both) - span + 1)), (
        key,
        alone,
    )


def test_대표일을_바꾸면_곡선_차트가_따라_바뀐다() -> None:
    """**세 곡선이 같은 날을 본다** (15세션 2절)."""
    app = _app(**_on("power_factor", "ess"))
    assert not app.exception, app.exception
    assert "연간 최대수요일" in _text(app)

    app.selectbox(key="measure_common_ref_day").set_value("winter").run()
    assert not app.exception, app.exception
    body = _text(app)
    assert "겨울 대표일" in body
    assert "연간 최대수요일" not in body


# ===================================================================== 5-1 태양광 경로


@pytest.mark.usefixtures("real_weather")
def test_태양광_계산_전에는_묵은_결과_경고가_없다() -> None:
    app = _app(**_on("solar"))
    assert not app.exception, app.exception
    assert "입력이 변경되었습니다" not in _text(app)


@pytest.mark.usefixtures("real_weather")
def test_입력을_바꾸면_묵은_결과라고_적는다() -> None:
    """계산 단추를 누르기 전 값은 **이전 계산의 것**이다. 그 사실을 적는다 (13세션)."""
    app = _app(**_on("solar"))
    app.button(key="solar_run").click().run(timeout=900)
    assert not app.exception, app.exception
    assert "입력이 변경되었습니다" not in _text(app)

    app.radio(key="measure_solar_azimuth").set_value("east").run(timeout=900)
    assert not app.exception, app.exception
    body = _text(app)
    assert "입력이 변경되었습니다" in body
    assert "묵은 결과" in body


@pytest.mark.usefixtures("real_weather")
def test_최적이_면적_상한이면_곡선을_감춘다() -> None:
    """**한 줄 판정으로 충분하다** (15세션 1-3). 곡선은 접어 둔다."""
    app = _app(**_on("solar"))
    app.button(key="solar_run").click().run(timeout=900)
    assert not app.exception, app.exception
    body = _text(app)
    assert "용량 판정" in body
    assert "설치 가능 면적 전체" in body
    assert "20단계 상세는 Excel" in body


@pytest.mark.usefixtures("real_weather")
def test_계산_전에는_방위_라벨이_이름뿐이다() -> None:
    """**옆단에서 시군구를 바꾸는 것만으로 여덟 방위를 돌리지 않는다** (17세션 3-1).

    상대 발전량은 0.85초짜리 시뮬레이션 여덟 번이다. 태양광은 「계산」 단추를
    눌러야 도는 카드이고, 그 규약은 라벨에도 적용된다.
    """
    app = _app(**_on("solar"))
    assert not app.exception, app.exception
    options = list(app.radio(key="measure_solar_azimuth").options)
    assert options[0] == "남"
    assert not [item for item in options if re.search(r"[+−]\d+%$", str(item))], options
    assert "「태양광 계산」 을 누른 뒤 라벨에 붙습니다" in _text(app)


@pytest.mark.usefixtures("real_weather")
def test_계산한_뒤에는_방위_라벨에_상대_발전량이_붙는다() -> None:
    """**하드코딩하지 않는다** — 지역·경사각으로 계산한 값이다 (15세션 1-1)."""
    app = _app(**_on("solar"))
    app.button(key="solar_run").click().run(timeout=900)
    assert not app.exception, app.exception
    options = list(app.radio(key="measure_solar_azimuth").options)
    assert options[0] == "남 (기준)"
    assert any(re.search(r"[+−]\d+%$", str(item)) for item in options), options


# ===================================================================== 5-2 산출물


@pytest.fixture(scope="module")
def compare_screen() -> AppTest:
    running = AppTest.from_file(str(APP), default_timeout=900)
    running.session_state["upload_bytes"] = SAMPLE.read_bytes()
    running.session_state["upload_name"] = SAMPLE.name
    running.session_state["contract_form"] = ContractForm(
        contract_type="general_b", voltage="high_a", option="I", contract_kw=6_000.0
    )
    for key in BILLED_MEASURES:
        running.session_state[f"measure_on_{key}"] = True
    return running.run()


def test_엑셀을_만들고_내려받아도_결과가_남는다(compare_screen: AppTest) -> None:
    """**내려받기 단추는 rerun 을 일으킨다** (12세션). 그 rerun 에서도 결과가 남아야 한다."""
    built = compare_screen.button(key="build_excel").click().run(timeout=900)
    assert not built.exception, built.exception
    assert len(built.download_button) == 1

    after = built.download_button(key="dl_excel").click().run(timeout=900)
    assert not after.exception, after.exception
    assert len(after.download_button) == 1, "내려받기 뒤 단추가 사라졌습니다."
    assert _stage3_labels(after)[:3] == ["단순 합", "합산효과", "차이"], (
        "내려받기 뒤 계산 결과가 사라졌습니다."
    )
    assert _stage2_metrics(after), "내려받기 뒤 2단계 카드가 사라졌습니다."


def test_워드를_만들고_내려받아도_결과가_남는다(compare_screen: AppTest) -> None:
    built = compare_screen.button(key="build_word").click().run(timeout=900)
    assert not built.exception, built.exception
    after = built.download_button(key="dl_word").click().run(timeout=900)
    assert not after.exception, after.exception
    assert after.download_button(key="dl_word"), "내려받기 뒤 단추가 사라졌습니다."
    assert _stage3_labels(after)[:3] == ["단순 합", "합산효과", "차이"]
    assert _stage2_metrics(after), "내려받기 뒤 2단계 카드가 사라졌습니다."


def test_수단이_없어도_두_산출물이_만들어진다() -> None:
    """진단만 보고 받아 가는 것이 정상 경로다 (8세션에 Excel 에서 잡았다)."""
    app = _app()
    for build, download in (("build_excel", "dl_excel"), ("build_word", "dl_word")):
        after = app.button(key=build).click().run(timeout=900)
        assert not after.exception, after.exception
        assert after.download_button(key=download)


def test_엑셀_시트가_규약대로다(
    sample_usage: object, sample_bill: object, sample_diagnosis: object
) -> None:
    """시트 이름은 한글, 순서는 :data:`SHEET_ORDER`, tz 는 해제되어 있다."""
    from kwise.report import ReportSections, build_sheets

    sheets = build_sheets(
        ReportSections(
            usage=sample_usage,  # type: ignore[arg-type]
            bill=sample_bill,  # type: ignore[arg-type]
            diagnosis=sample_diagnosis,  # type: ignore[arg-type]
            include_timeseries=False,
        )
    )
    assert list(sheets) == [name for name in SHEET_ORDER if name in sheets]
    assert all(re.search(r"[가-힣]", name) for name in sheets)


def test_산출물_파일명에_날짜_시각이_붙는다() -> None:
    """Excel 이 파일을 열고 있으면 덮어쓰기가 실패한다."""
    from kwise.report import result_path

    assert re.search(r"_\d{8}_\d{4}\.xlsx$", result_path().name)


# ===================================================================== 5-3 경계


def test_데이터가_없어도_탭을_막지_않는다() -> None:
    """**진행 불가 탭도 막지 않고 안내만 낸다** (16세션 1절)."""
    app = _blank()
    assert not app.exception, app.exception
    headers = [str(item.value) for item in app.header]
    assert any("2단계" in item for item in headers)
    assert any("3단계" in item for item in headers)
    assert "「1단계 · 진단」 탭에서" in _text(app)


def test_계약_정보가_없으면_금액을_내지_않는다() -> None:
    running = AppTest.from_file(str(APP), default_timeout=900)
    running.session_state["upload_bytes"] = SAMPLE.read_bytes()
    running.session_state["upload_name"] = SAMPLE.name
    app = running.run()
    assert not app.exception, app.exception
    assert "계약 정보를 확정하기 전까지는" in _text(app)


def test_잉여가_0_이어도_카드는_활성이다() -> None:
    """**독립 평가 원칙** — 다른 카드 때문에 비활성이 되지 않는다 (14세션 2-3)."""
    app = _app(**_on("surplus"))
    assert not app.exception, app.exception
    assert "태양광을 켜지 않아 잉여가 0 입니다." in _text(app)
    assert any("잉여 판매 단가" in str(item.label) for item in app.number_input)


def test_하향_여지가_없으면_여유율_입력을_감춘다() -> None:
    """움직여도 0% 인 입력칸은 고장으로 보인다 (13세션)."""
    running = AppTest.from_file(str(APP), default_timeout=900)
    running.session_state["upload_bytes"] = SAMPLE.read_bytes()
    running.session_state["upload_name"] = SAMPLE.name
    running.session_state["contract_form"] = ContractForm(
        contract_type="general_b", voltage="high_a", option="I", contract_kw=5_400.0
    )
    running.session_state["measure_on_contract"] = True
    app = running.run()
    assert not app.exception, app.exception
    assert "하향 여지가 없습니다" in _text(app)
    assert not [item for item in app.number_input if "여유율" in str(item.label)]


def test_기상_취득에_실패해도_화면이_살아_있다(monkeypatch: pytest.MonkeyPatch) -> None:
    """**차단 등급으로 알리고 화면은 살아 있어야 한다.**

    실패를 모의로 만든다. 환경변수만 비우면 ``PROJECT_CACHE`` 에 남은 앞선
    시험의 결과나 API 가 대신 성공해 실패 경로를 타지 않는다 — 그러면 이 시험이
    조용히 통과한다.
    """
    import streamlit as st

    from kwise.pv import WeatherUnavailableError
    from kwise.ui import pipeline

    def refuse(*_args: object, **_kwargs: object) -> object:
        raise WeatherUnavailableError("기상 자료를 얻지 못했습니다 (모의 실패).")

    monkeypatch.setattr(pipeline, "load_weather", refuse)
    st.cache_data.clear()

    app = _app(**_on("solar"))
    app.button(key="solar_run").click().run(timeout=900)
    assert not app.exception, app.exception
    assert any("기상 자료를 얻지 못해" in str(item.value) for item in app.error)


def test_저부하_평일이_없으면_사유만_적는다(sample_diagnosis: object) -> None:
    """**차트 대신 사유**를 낸다. 없으면 없다고 적는다 (14세션 4절)."""
    from dataclasses import replace

    import pandas as pd

    from kwise.measures import evaluate_demand_response

    profile = sample_diagnosis.dr  # type: ignore[attr-defined]
    empty = replace(
        profile,
        low_load_days=(),
        daily_reducible_kw=pd.Series(dtype=float),
        daily_hours=pd.Series(dtype=float),
        registered_capacity_kw=0.0,
        period_reducible_kwh=0.0,
        annual_reducible_kwh=0.0,
    )
    result = evaluate_demand_response(empty)
    assert result.annual_reducible_kwh == 0.0
    assert not result.has_low_load_days
    assert any("저부하 평일이 없습니다" in item for item in result.warnings)


# ===================================================================== 5-4 회귀


def test_요금적용전력_회귀값이_그대로다(sample_diagnosis: object) -> None:
    """15세션은 표시만 고쳤다. **계산값이 바뀌면 그것은 회귀다.**"""
    peak = sample_diagnosis.peak  # type: ignore[attr-defined]
    assert peak.billing_demand_kw == pytest.approx(5_293.44)


def test_3단계_요약이_2단계_카드와_같다() -> None:
    """**재계산하지 않는다** (14세션 5-1)."""
    card = _app(**_on(*BILLED_MEASURES))
    metrics = [(str(item.label), str(item.value)) for item in card.metric]
    index = next(pos for pos, (label, _) in enumerate(metrics) if label == "출력 / 용량")
    ess_payback = metrics[index + 3][1]

    frame = next(item.value for item in card.dataframe if "수단" in list(item.value.columns))
    rows = {str(row["수단"]): row for _, row in frame.iterrows()}
    assert str(rows["7.6 ESS"]["회수기간"]) == ess_payback


def test_단순_합과_합산효과가_다르다() -> None:
    """**상호작용이 실제로 계산되는지** — 같으면 조합 재계산이 도는지 의심해야 한다."""
    app = _app(**_on(*BILLED_MEASURES))
    metrics = {str(item.label): str(item.value) for item in app.metric}
    assert metrics["단순 합"] != metrics["합산효과"]
    gap = next(item for item in app.metric if item.label == "차이")
    assert str(gap.delta).endswith("%")


def test_화면에_코드_식별자가_없다() -> None:
    banned = re.compile(r"\b(general_b|high_a|tariff_switch|prior_peaks|verified=)\b")
    app = _app(**_on(*BILLED_MEASURES))
    offenders = [line for line in _text(app).splitlines() if banned.search(line)]
    assert not offenders, offenders


def test_이스케이프되지_않은_물결표가_없다() -> None:
    """물결표 둘이 한 줄에 있으면 그 사이가 취소선이 된다 (13세션)."""
    app = _app(**_on(*BILLED_MEASURES))
    offenders = [
        line for line in _text(app).splitlines() if len(re.findall(r"(?<!\\)~", line)) >= 2
    ]
    assert not offenders, offenders


def test_원_단위_금액이_모두_천의_배수다() -> None:
    """**표시만 절사하고 내부 계산은 원 단위다** (14세션 1절)."""
    pattern = re.compile(r"(?<![만억\d])(\d[\d,]*)원(?![/\w])")
    app = _app(**_on(*BILLED_MEASURES))
    blobs = [_text(app)] + [frame.value.to_string() for frame in app.dataframe]
    amounts = [match.group(1) for blob in blobs for match in pattern.finditer(blob)]
    assert amounts, "원 단위 금액을 찾지 못했습니다."
    offenders = [item for item in amounts if int(item.replace(",", "")) % 1_000]
    assert not offenders, offenders


def test_건물명이_없으면_산출물_제목이_미입력이다() -> None:
    """**빈 표지는 만들다 만 문서로 보인다** (16세션 2절)."""
    from kwise.ui.building import NAME_MISSING, BuildingInfo

    blank = BuildingInfo(region_key="서울특별시/강남구")
    assert blank.title == NAME_MISSING
    assert not blank.named
    named = BuildingInfo(region_key="서울특별시/강남구", name=" 본사 ")
    assert named.title == "본사"

    app = _app(**_on(*BILLED_MEASURES))
    assert not app.exception, app.exception
    body = _text(app)
    assert f"표지 이름 — **{NAME_MISSING}**" in body
    # 같은 것을 두 곳에서 묻지 않는다 — 건물명 입력칸은 옆단 하나뿐이다.
    fields = [str(item.label) for item in app.text_input if "건물명" in str(item.label)]
    assert fields == ["건물명 (선택)"], fields


# ===================================================================== 17세션 · 그래프 표시
#
# **표시만 본다.** 계산값은 5-4 회귀 시험이 지킨다 — 여기서 보는 것은 "축이
# 잘렸는가 · 색이 있는가 · 자리를 옮겼는가" 다.


@functools.cache
def _material() -> tuple[object, object, object, object, object]:
    """차트 재료 한 벌. **한 번만 만든다** — 요금 계산이 매번 돌면 시험이 느려진다."""
    from kwise.io import load_usage
    from kwise.measures import evaluate_power_factor, evaluate_tariff_switch
    from kwise.report.days import find_day
    from kwise.tariff import calculate_bill, load_tariff
    from kwise.ui import pipeline

    usage = load_usage(SAMPLE)
    form = ContractForm(
        contract_type="general_b", voltage="high_a", option="I", contract_kw=6_000.0
    )
    table = load_tariff()
    quality = pipeline.load_quality(usage)
    baseline = calculate_bill(
        usage, table, form.selection, options=form.billing_options(), quality=quality
    )
    switch = evaluate_tariff_switch(
        usage, table, form.selection, options=form.billing_options(), quality=quality
    )
    power_factor = evaluate_power_factor(
        usage,
        table,
        form.selection,
        baseline=baseline,
        target_pct=97.0,
        options=form.billing_options(),
        quality=quality,
    )
    day = find_day(usage, "peak")
    return usage, switch, power_factor, day, baseline


def _rows(spec: dict[str, object], layer: dict[str, object]) -> list[dict[str, object]]:
    """altair 는 자료를 ``datasets`` 에 이름으로 담는다. 그것을 풀어 준다."""
    data = layer.get("data", {})
    values = data.get("values")
    if values is not None:
        return list(values)
    datasets = spec.get("datasets", {})
    return list(datasets.get(data.get("name"), []))  # type: ignore[union-attr]


def _fake_generation(usage: object) -> object:
    """정오에 솟는 가짜 발전 프로파일. **기상 없이도 그림을 볼 수 있다.**"""
    import numpy as np
    import pandas as pd

    index = pd.DatetimeIndex(usage.kw.index)  # type: ignore[attr-defined]
    hours = index.hour + index.minute / 60.0
    shape = np.clip(np.cos((hours - 12.5) / 6.0 * np.pi / 2.0), 0.0, None) ** 2
    peak = float(usage.kw.max()) * 0.12  # type: ignore[attr-defined]
    return pd.Series(shape * peak, index=index, name="발전량(kW)")


def test_선택요금이_제도_순서로_나온다() -> None:
    """**Ⅰ·Ⅱ·Ⅲ 순이다** (17세션 1-1). 절감액 순으로 정렬하면 자료마다 뒤섞인다."""
    from kwise.tariff import option_sort_key
    from kwise.ui.charts import tariff_option_frame

    assert sorted(["III", "I", "II"], key=option_sort_key) == ["I", "II", "III"]
    _usage, switch, _pf, _day, _base = _material()
    assert list(tariff_option_frame(switch)["요금제"]) == ["선택Ⅰ", "선택Ⅱ", "선택Ⅲ"]


def test_선택Ⅲ도_기본_전력량으로_갈라진다() -> None:
    """**「상세 미산출」 은 값이 없어서가 아니었다** (17세션 1-4).

    현행·최적 둘만 상세를 내던 최적화가 원인이다 — 갈아탈 수 있는 조합은 같은
    계약종별·전압 안의 선택요금뿐이라 많아야 셋이고, 늘어나는 계산은 한 벌이다.
    """
    from kwise.ui.charts import tariff_option_frame, tariff_option_long_frame

    _usage, switch, _pf, _day, _base = _material()
    frame = tariff_option_frame(switch)
    assert frame["기본요금(원)"].notna().all(), frame.to_string()
    assert frame["전력량요금(원)"].notna().all(), frame.to_string()
    assert set(tariff_option_long_frame(switch)["구분"]) == {"기본요금", "전력량요금", "합계"}


def test_요금제_그래프가_그룹_막대이고_차액_차트가_따로_있다() -> None:
    """쌓으면 항목별 비교가 안 된다 (17세션 1-2·1-3)."""
    from kwise.ui.charts import tariff_delta_chart, tariff_option_chart

    _usage, switch, _pf, _day, _base = _material()
    grouped = tariff_option_chart(switch).to_dict()
    assert "xOffset" in grouped["encoding"], list(grouped["encoding"])
    assert grouped["encoding"]["y"]["scale"]["zero"] is False
    assert "0 부터 시작하지 않습니다" in grouped["encoding"]["y"]["title"]

    delta = tariff_delta_chart(switch).to_dict()
    fields = {layer["encoding"]["x"].get("field") for layer in delta["layer"]}
    assert "현행 대비(원)" in fields


def test_화면에_상세_미산출이_없다() -> None:
    app = _app(**_on("tariff_switch"))
    assert not app.exception, app.exception
    body = _text(app)
    assert "상세 미산출" not in body
    assert "왼쪽(초록)이 절감" in body


def test_역률_범례가_우측_하단이다() -> None:
    """지상역률 값을 바꾸면 우측 상단 범례가 글자를 가렸다 (17세션 2절)."""
    from kwise.ui.charts import power_triangle_chart

    _usage, _switch, power_factor, _day, _base = _material()
    spec = power_triangle_chart(power_factor).to_dict()
    orients = {
        layer["encoding"]["color"]["legend"]["orient"]
        for layer in spec["layer"]
        if "legend" in layer["encoding"].get("color", {})
    }
    assert orients == {"bottom-right"}


def test_전력삼각형에_각도와_역률을_직접_적는다() -> None:
    """**범례 의존을 줄인다** (17세션 2절)."""
    from kwise.ui.charts import power_triangle_chart

    _usage, _switch, power_factor, _day, _base = _material()
    spec = power_triangle_chart(power_factor).to_dict()
    texts = [layer for layer in spec["layer"] if layer.get("mark", {}).get("type") == "text"]
    values = [row for layer in texts for row in _rows(spec, layer)]
    assert any(
        "역률" in str(row.get("설명", "")) and "°" in str(row.get("설명", "")) for row in values
    )
    assert any(str(row.get("각도라벨", ""))[-1:] == "°" for row in values)


@pytest.mark.usefixtures("real_weather")
def test_태양광_판정_근거를_적는다() -> None:
    """**왜 하필 그 용량인가** (17세션 3-2)."""
    app = _app(**_on("solar"))
    app.button(key="solar_run").click().run(timeout=900)
    assert not app.exception, app.exception
    body = _text(app)
    assert "회수기간이 가장 짧은 용량입니다" in body or "절감액이 가장 큰" in body
    assert "최적을 정한 것은" in body
    limiters = ("설치 가능 면적 상한", "잉여 발생", "기본요금 절감 포화")
    assert any(word in body for word in limiters), body


@pytest.mark.usefixtures("real_weather")
def test_태양광_용량_표가_다섯_줄이다() -> None:
    """20단계는 Excel 로, 화면에는 의미 있는 지점만 (17세션 3-3)."""
    from kwise.ui.charts import CAPACITY_ROWS

    app = _app(**_on("solar"))
    app.button(key="solar_run").click().run(timeout=900)
    assert not app.exception, app.exception
    frame = next(item.value for item in app.dataframe if "자가소비율" in list(item.value.columns))
    assert len(frame) == CAPACITY_ROWS == 5
    assert set(frame.columns) >= {
        "용량(kWp)",
        "연간 발전량",
        "자가소비율",
        "기본요금 절감",
        "전력량요금 절감",
        "투자비",
        "회수기간",
    }
    assert "면적 상한" in " ".join(str(value) for value in frame["표식"])


def test_태양광_연간_차트가_사용량_감소를_주인공으로_그린다() -> None:
    """발전량만 색으로 채우던 그림을 뒤집었다 (17세션 3-4)."""
    from kwise.ui.charts import solar_annual_chart, solar_saving_ratio

    usage, _switch, _pf, _day, _base = _material()
    generation = _fake_generation(usage)
    spec = solar_annual_chart(usage, generation).to_dict()
    filled = [
        layer
        for layer in spec["layer"]
        if layer.get("mark", {}).get("type") == "area" and "y2" in layer["encoding"]
    ]
    assert filled, "원래 사용량과 순사용량 사이를 채우지 않았습니다."
    assert filled[0]["encoding"]["y"]["field"] == "계통 수전(kWh)"
    assert filled[0]["encoding"]["y2"]["field"] == "사용량(kWh)"
    ratio = solar_saving_ratio(usage, generation)
    assert ratio is not None and 0.0 < ratio < 1.0


def test_일일_곡선의_축이_0부터가_아니고_저감분을_채운다() -> None:
    """5,000 kW 부하에 수백 kW 를 얹으면 0 부터 그린 축에서 두 선이 붙는다 (3-5)."""
    from kwise.ui.charts import PEAK_ZOOM_HOURS, solar_day_chart

    usage, _switch, _pf, day, _base = _material()
    generation = _fake_generation(usage)
    spec = solar_day_chart(usage, generation, day).to_dict()
    scales = [
        layer["encoding"]["y"].get("scale", {})
        for layer in spec["layer"]
        if "y" in layer["encoding"]
    ]
    assert any(scale.get("zero") is False for scale in scales), scales
    filled = [
        layer
        for layer in spec["layer"]
        if layer.get("mark", {}).get("type") == "area" and "y2" in layer["encoding"]
    ]
    assert filled, "원부하와 순부하 사이를 채우지 않았습니다."
    texts = [layer for layer in spec["layer"] if layer.get("mark", {}).get("type") == "text"]
    values = [row for layer in texts for row in _rows(spec, layer)]
    assert any("피크 −" in str(row.get("설명", "")) for row in values), values

    zoomed = solar_day_chart(usage, generation, day, zoom=True).to_dict()
    assert f"피크 앞뒤 {PEAK_ZOOM_HOURS}시간" in str(zoomed)


def test_ESS_그래프가_2단이고_충방전에_색이_있다() -> None:
    """겹쳐 그렸더니 온통 하얬다 (17세션 4-1)."""
    from kwise.ui.charts import ess_day_chart

    usage, _switch, _pf, day, dispatch = _dispatch()
    spec = ess_day_chart(usage, dispatch, day).to_dict()
    assert "vconcat" in spec, list(spec)
    assert len(spec["vconcat"]) == 2
    upper, lower = spec["vconcat"]
    assert any(
        layer["encoding"]["y"].get("scale", {}).get("zero") is False
        for layer in upper["layer"]
        if "y" in layer["encoding"]
    )
    assert lower["mark"]["type"] == "bar"
    assert lower["encoding"]["color"]["scale"]["range"] == ["#3182bd", "#e6550d"]
    assert "충전(+)" in lower["encoding"]["y"]["title"]


def test_ESS_본문에_투자비_상세와_성립_조건이_없다() -> None:
    """본문에는 투자비 합계·절감액·회수기간만 남긴다 (17세션 4-2)."""
    source = (Path("src") / "kwise" / "ui" / "views" / "measures.py").read_text(encoding="utf-8")
    body = source[source.index("def _ess(") : source.index("def _ess_details(")]
    # 주석은 화면에 나가지 않는다.
    body = " ".join(line for line in body.splitlines() if not line.lstrip().startswith("#"))
    for banned in ("설비 **", "성립 조건", "kW당 배터리비"):
        assert banned not in body, banned
    assert "_notes(result.warnings, result.notes, _ess_details(result, model))" in source


def test_화면에_조달_사례_표가_없다() -> None:
    """계수 산출 근거는 데이터와 재적합 스크립트에 남긴다 (17세션 4-3)."""
    source = (Path("src") / "kwise" / "ui" / "views" / "measures.py").read_text(encoding="utf-8")
    assert "case_table()" not in source
    app = _app(**_on("ess"))
    assert not app.exception, app.exception
    columns = {str(name) for item in app.dataframe for name in item.value.columns}
    assert "설치 형태" not in columns, columns


def test_ESS_확인사항에_투자비_상세가_있다() -> None:
    app = _app(**_on("ess"))
    assert not app.exception, app.exception
    inside = "\n".join(
        str(item.value)
        for exp in app.expander
        if "확인사항" in str(exp.label)
        for item in list(exp.markdown) + list(exp.caption)
    )
    assert "투자비 내역" in inside
    assert "회수에 필요한 저감량" in inside
    assert "설비비 = " in inside


@functools.cache
def _dispatch() -> tuple[object, object, object, object, object]:
    """ESS 디스패치 한 벌. 그림만 보므로 목표는 관측 최대의 97% 로 잡는다."""
    from kwise.measures import EssCostInput, evaluate_ess

    usage, switch, power_factor, day, baseline = _material()
    result = evaluate_ess(
        usage,
        _table(),
        _contract().selection,
        target_kw=float(usage.kw.max()) * 0.97,  # type: ignore[attr-defined]
        cost=EssCostInput(),
        baseline=baseline,  # type: ignore[arg-type]
        options=_contract().billing_options(),
    )
    return usage, switch, power_factor, day, result.dispatch


def _contract() -> ContractForm:
    return ContractForm(
        contract_type="general_b", voltage="high_a", option="I", contract_kw=6_000.0
    )


@functools.cache
def _table() -> object:
    from kwise.tariff import load_tariff

    return load_tariff()
