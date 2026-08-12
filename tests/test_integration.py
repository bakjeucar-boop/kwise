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

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from kwise.report import SHEET_ORDER
from kwise.ui.nav import PAGES, STEP_PAGES
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


@pytest.mark.parametrize("page", PAGES)
def test_모든_화면이_예외_없이_뜬다(page: str) -> None:
    app = _app(nav_page=page)
    assert not app.exception, app.exception


@pytest.mark.parametrize("count", [0, 1, len(BILLED_MEASURES)])
def test_수단을_0개_1개_전부_켜도_3단계가_돈다(count: int) -> None:
    """**수단 0개가 정상 경로다** — 진단만 보고 받아 가는 경우다."""
    app = _app(nav_page=PAGES[2], **_on(*BILLED_MEASURES[:count]))
    assert not app.exception, app.exception
    body = _text(app)
    assert "개선안별 요약" in body
    if count == 0:
        assert "수단을 하나도 켜지 않았습니다" in body
    else:
        assert "단순 합" in body


def test_모든_수단을_켜도_2단계가_돈다() -> None:
    app = _app(nav_page=PAGES[1], **_on(*ALL_MEASURES))
    assert not app.exception, app.exception
    body = _text(app)
    # 태양광은 계산 전이라 안내만, 잉여는 0 이라 사실만 적는다.
    assert "「태양광 계산」 을 누르십시오" in body
    assert "태양광을 켜지 않아 잉여가 0 입니다." in body


def test_수단을_켜고_끄기를_반복해도_상태가_꼬이지_않는다() -> None:
    """토글을 되풀이해도 카드가 살아 있고 예외가 없어야 한다."""
    app = _app(nav_page=PAGES[1], **_on("tariff_switch"))
    for _ in range(2):
        app.toggle(key="measure_on_tariff_switch").set_value(False).run()
        assert not app.exception, app.exception
        app.toggle(key="measure_on_tariff_switch").set_value(True).run()
        assert not app.exception, app.exception
    assert any(item.label == "가장 유리한 요금제" for item in app.metric)


def test_사이드바와_하단_단추가_같은_상태를_쓴다() -> None:
    """**두 벌을 두면 단추로 옮긴 뒤 옆단 표시가 어긋난다** (12세션)."""
    app = _app(nav_page=PAGES[0])
    app.button(key="go_measures").click().run()
    assert app.session_state["nav_page"] == PAGES[1]

    app.sidebar.button(key=f"nav_{PAGES[2]}").click().run()
    assert app.session_state["nav_page"] == PAGES[2]
    assert not app.exception, app.exception


def test_옆단이_단계_상태를_보인다() -> None:
    app = _app(nav_page=PAGES[1], **_on("tariff_switch"))
    labels = [str(item.label) for item in app.sidebar.button]
    assert labels[0].startswith("① 진단") and "완료" in labels[0]
    assert labels[1].startswith("② 개선 수단") and "현재" in labels[1]
    assert labels[2].startswith("③ 조합 비교") and "대기" in labels[2]
    # **기준 데이터는 단계가 아니다** — 번호 배지가 없다.
    assert labels[3] == PAGES[3]


def test_진행할_수_없는_단계는_눌리지_않는다() -> None:
    app = _blank()
    buttons = {str(item.label): item for item in app.sidebar.button}
    locked = [item for label, item in buttons.items() if "진행 불가" in label]
    assert len(locked) == 2, list(buttons)
    assert all(item.disabled for item in locked)


@pytest.mark.parametrize("key", BILLED_MEASURES)
def test_수단을_함께_켜도_카드_값이_불변이다(key: str) -> None:
    """**독립 평가** (14세션 2절) — 다른 수단을 켜고 끄든 이 카드의 숫자가 같다."""
    alone = [(str(m.label), str(m.value)) for m in _app(nav_page=PAGES[1], **_on(key)).metric]
    both = [
        (str(m.label), str(m.value))
        for m in _app(nav_page=PAGES[1], **_on(*BILLED_MEASURES)).metric
    ]
    span = len(alone)
    assert alone
    assert any(both[start : start + span] == alone for start in range(len(both) - span + 1)), (
        key,
        alone,
    )


def test_대표일을_바꾸면_곡선_차트가_따라_바뀐다() -> None:
    """**세 곡선이 같은 날을 본다** (15세션 2절)."""
    app = _app(nav_page=PAGES[1], **_on("power_factor", "ess"))
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
    app = _app(nav_page=PAGES[1], **_on("solar"))
    assert not app.exception, app.exception
    assert "입력이 변경되었습니다" not in _text(app)


@pytest.mark.usefixtures("real_weather")
def test_입력을_바꾸면_묵은_결과라고_적는다() -> None:
    """계산 단추를 누르기 전 값은 **이전 계산의 것**이다. 그 사실을 적는다 (13세션)."""
    app = _app(nav_page=PAGES[1], **_on("solar"))
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
    app = _app(nav_page=PAGES[1], **_on("solar"))
    app.button(key="solar_run").click().run(timeout=900)
    assert not app.exception, app.exception
    body = _text(app)
    assert "용량 판정" in body
    assert "설치 가능 면적 전체" in body
    assert "20단계 상세는 Excel" in body


@pytest.mark.usefixtures("real_weather")
def test_방위_라벨에_상대_발전량이_붙는다() -> None:
    """**하드코딩하지 않는다** — 지역·경사각으로 계산한 값이다 (15세션 1-1)."""
    app = _app(nav_page=PAGES[1], **_on("solar"))
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
    running.session_state["nav_page"] = PAGES[2]
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
    assert [item.label for item in after.metric][:1] == ["단순 합"], (
        "내려받기 뒤 계산 결과가 사라졌습니다."
    )


def test_워드를_만들고_내려받아도_결과가_남는다(compare_screen: AppTest) -> None:
    built = compare_screen.button(key="build_word").click().run(timeout=900)
    assert not built.exception, built.exception
    after = built.download_button(key="dl_word").click().run(timeout=900)
    assert not after.exception, after.exception
    assert after.download_button(key="dl_word"), "내려받기 뒤 단추가 사라졌습니다."
    assert [item.label for item in after.metric][:1] == ["단순 합"]


def test_수단이_없어도_두_산출물이_만들어진다() -> None:
    """진단만 보고 받아 가는 것이 정상 경로다 (8세션에 Excel 에서 잡았다)."""
    app = _app(nav_page=PAGES[2])
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


@pytest.mark.parametrize("page", STEP_PAGES[1:])
def test_데이터가_없으면_2·3단계가_막힌다(page: str) -> None:
    app = _blank(nav_page=page)
    assert not app.exception, app.exception
    assert any("1단계에서 파일을 올리고" in str(item.value) for item in app.error)


def test_계약_정보가_없으면_금액을_내지_않는다() -> None:
    running = AppTest.from_file(str(APP), default_timeout=900)
    running.session_state["upload_bytes"] = SAMPLE.read_bytes()
    running.session_state["upload_name"] = SAMPLE.name
    app = running.run()
    assert not app.exception, app.exception
    assert "계약 정보를 확정하기 전까지는" in _text(app)


def test_잉여가_0_이어도_카드는_활성이다() -> None:
    """**독립 평가 원칙** — 다른 카드 때문에 비활성이 되지 않는다 (14세션 2-3)."""
    app = _app(nav_page=PAGES[1], **_on("surplus"))
    assert not app.exception, app.exception
    assert "태양광을 켜지 않아 잉여가 0 입니다." in _text(app)
    assert any("잉여 판매 단가" in str(item.label) for item in app.number_input)


def test_하향_여지가_없으면_슬라이더를_감춘다() -> None:
    """움직여도 0% 인 슬라이더는 고장으로 보인다 (13세션)."""
    running = AppTest.from_file(str(APP), default_timeout=900)
    running.session_state["upload_bytes"] = SAMPLE.read_bytes()
    running.session_state["upload_name"] = SAMPLE.name
    running.session_state["contract_form"] = ContractForm(
        contract_type="general_b", voltage="high_a", option="I", contract_kw=5_400.0
    )
    running.session_state["nav_page"] = PAGES[1]
    running.session_state["measure_on_contract"] = True
    app = running.run()
    assert not app.exception, app.exception
    assert "하향 여지가 없습니다" in _text(app)
    assert not [item for item in app.slider if "여유율" in str(item.label)]


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

    app = _app(nav_page=PAGES[1], **_on("solar"))
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
    card = _app(nav_page=PAGES[1], **_on(*BILLED_MEASURES))
    metrics = [(str(item.label), str(item.value)) for item in card.metric]
    index = next(pos for pos, (label, _) in enumerate(metrics) if label == "출력 / 용량")
    ess_payback = metrics[index + 3][1]

    summary = _app(nav_page=PAGES[2], **_on(*BILLED_MEASURES))
    frame = summary.dataframe[0].value
    rows = {str(row["수단"]): row for _, row in frame.iterrows()}
    assert str(rows["7.6 ESS"]["회수기간"]) == ess_payback


def test_단순_합과_합산효과가_다르다() -> None:
    """**상호작용이 실제로 계산되는지** — 같으면 조합 재계산이 도는지 의심해야 한다."""
    app = _app(nav_page=PAGES[2], **_on(*BILLED_MEASURES))
    metrics = {str(item.label): str(item.value) for item in app.metric}
    assert metrics["단순 합"] != metrics["합산효과"]
    gap = next(item for item in app.metric if item.label == "차이")
    assert str(gap.delta).endswith("%")


@pytest.mark.parametrize("page", PAGES[:3])
def test_화면에_코드_식별자가_없다(page: str) -> None:
    banned = re.compile(r"\b(general_b|high_a|tariff_switch|prior_peaks|verified=)\b")
    app = _app(nav_page=page, **_on(*BILLED_MEASURES))
    offenders = [line for line in _text(app).splitlines() if banned.search(line)]
    assert not offenders, offenders


@pytest.mark.parametrize("page", PAGES[:3])
def test_이스케이프되지_않은_물결표가_없다(page: str) -> None:
    """물결표 둘이 한 줄에 있으면 그 사이가 취소선이 된다 (13세션)."""
    app = _app(nav_page=page, **_on(*BILLED_MEASURES))
    offenders = [
        line for line in _text(app).splitlines() if len(re.findall(r"(?<!\\)~", line)) >= 2
    ]
    assert not offenders, offenders


@pytest.mark.parametrize("page", PAGES[:3])
def test_원_단위_금액이_모두_천의_배수다(page: str) -> None:
    """**표시만 절사하고 내부 계산은 원 단위다** (14세션 1절)."""
    pattern = re.compile(r"(?<![만억\d])(\d[\d,]*)원(?![/\w])")
    app = _app(nav_page=page, **_on(*BILLED_MEASURES))
    blobs = [_text(app)] + [frame.value.to_string() for frame in app.dataframe]
    amounts = [match.group(1) for blob in blobs for match in pattern.finditer(blob)]
    assert amounts, "원 단위 금액을 찾지 못했습니다."
    offenders = [item for item in amounts if int(item.replace(",", "")) % 1_000]
    assert not offenders, offenders
