"""데스크톱 화면 — 옆단 건물 정보 + 세 단계 탭 (1단계 진단 · 2단계 개선 수단 · 3단계 조합).

2단계는 ``measures.py`` 가 짓는다. 3단계는 아직 없다.

계산·그래프·문구는 공용 함수를 부른다. 여기에 있는 것은 **화면 연결**뿐이다 —
입력 받기, 공용 함수 부르기, 결과 놓기.

    .venv\\Scripts\\python.exe kwise_desktop.py
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
from nicegui import run, ui

from kwise.diagnose import Diagnosis
from kwise.diagnose.dr import BID_WINDOW, dr_market_windows
from kwise.io import UsageData, load_usage_bytes
from kwise.pv import list_provinces, list_sigungu
from kwise.quality import DEFAULT_OPERATING_HOURS, QualityReport, monthly_longest_gaps
from kwise.quality.checks import HOURLY_INTERVAL_FACT
from kwise.report import localize, narrative, notices
from kwise.tariff import (
    AMI_BASIS_NOTICE,
    TENTATIVE_BASE_FEE_BASIS_WARNING,
    TariffTable,
    load_tariff,
)
from kwise.tariff.labels import SEASON_LABELS
from kwise.tariff.power_factor import leading_charge_applies
from kwise.ui import charts
from kwise.ui import text as fmt
from kwise.ui.labels import option_label
from kwise.ui.notices import partition_facts, screen_notices
from kwise.ui.pipeline import (
    ContractForm,
    contract_type_choices,
    daily_temperature,
    default_lagging_pct,
    diagnose_usage,
    load_quality,
    option_choices,
    voltage_choices,
)

from . import measures, vega
from . import widgets as w
from .building import (
    NAME_MISSING,
    BuildingInfo,
    building_uses,
    intensity_kwh_per_m2,
    narrow_contract_types,
    weather_source_label,
)
from .widgets import CSS as _CSS
from .widgets import TIP_SIDE as _TIP_SIDE
from .widgets import caption as _caption
from .widgets import caution as _caution
from .widgets import metrics as _metrics
from .widgets import notice_block
from .widgets import table as _table
from .widgets import tip as _tip

#: Streamlit 화면의 ``SEASON_CHOICES`` 와 같은 갈래 (그쪽 모듈은 streamlit 을 끌어와 못 부른다).
_SEASONS: tuple[tuple[str, str | None], ...] = (
    ("전체", None),
    *((label, key) for key, label in SEASON_LABELS.items()),
)

#: 화면 자리를 가르는 사실 묶음 — Streamlit 화면(``ui\\views\\diagnose.py``)의 같은 이름 상수를
#: 옮겼다 (그 모듈은 streamlit 을 끌어와 못 부른다). 결측 안내는 「데이터 품질」 절이, 계약 경고는
#: 2단계 카드가, 잠정 경고는 계약 정보 바로 아래가 낸다 — 위쪽 안내에서는 뺀다.
_MISSING_FACTS = (
    "quality.missing_total",
    "quality.longest_gap",
    "quality.month_missing_rate",
    "quality.peak_skew",
    "quality.dropped_rows",
    HOURLY_INTERVAL_FACT,
)
_DROPPED_ROWS_FACT = "quality.dropped_rows"
#: Streamlit 화면의 ``SCREEN_MONTHLY_COLUMNS`` — 월별 명세에 낼 열.
_MONTHLY_COLUMNS = (
    "season",
    "covered_days",
    "is_partial",
    "missing_ratio",
    "demand_confidence",
    "max_demand_kw",
    "billing_demand_kw",
    "total_kwh",
    "base_won",
    "power_factor_won",
    "energy_won",
    "excess_won",
    "total_won",
)
_CONTRACT_FACTS = (
    "contract.margin",
    "contract.penalty",
    "contract.over_limit",
    "contract.floor_unknown",
    "contract.saving_basis",
    "contract.energy_unchanged",
    "contract.floor_not_binding",
    "quality.over_contract",
    "tariff.floor_no_contract",
    "tariff.contract_type_threshold",
    "diagnose.no_contract_kw",
)
_TENTATIVE_FACTS = ("tariff.tentative_base_fee_basis",)

#: 2·3단계가 아직 못 설 때 — Streamlit ``ui\\app.py`` 의 ``_NOT_READY`` 와 같은 글.
_NOT_READY = (
    "「1단계 · 진단」 탭에서 사용량 파일을 올리고 계약 정보를 확정하면 여기에 결과가 나옵니다."
)


@dataclass
class _State:
    table: TariffTable
    data: bytes | None = None
    filename: str = ""
    form: ContractForm | None = None
    #: 열 지정 — ``None`` 이면 자동 판정을 따른다.
    date_column: str | None = None
    energy_column: str | None = None
    building: BuildingInfo = field(default_factory=BuildingInfo)
    #: 2단계에서 켠 카드와 넣은 값 — 1단계를 다시 계산해도 남는다.
    choices: measures.Choices = field(default_factory=measures.Choices)


def _seasons(available: object) -> tuple[tuple[str, str | None], ...]:
    keys = set(map(str, available))  # type: ignore[call-overload]
    return tuple(item for item in _SEASONS if item[1] is None or item[1] in keys)


def _load(state: _State) -> UsageData:
    assert state.data is not None
    return load_usage_bytes(
        state.data,
        state.filename,
        date_column=state.date_column,
        energy_column=state.energy_column,
    )


# ------------------------------------------- 데이터 품질 문구 (Streamlit 화면 함수의 사본)
# ``ui\views\diagnose.py`` 의 ``missing_lines`` · ``dropped_row_lines`` · ``missing_month_frame``
# 을 옮겼다 — 그 모듈이 streamlit 을 끌어와 부를 수 없다. 저쪽을 고치면 여기도 고친다.


def _missing_lines(quality: QualityReport) -> tuple[str, ...]:
    lines = [
        f"결측 {fmt.count(quality.missing_slots, '구간')} / "
        f"{fmt.count(quality.expected_slots, '구간')} "
        f"({fmt.ratio_pct(quality.missing_ratio)}) · 보간하지 않고 계산에서 제외"
    ]
    months = tuple(month for month in quality.monthly if month.missing_slots)
    if months:
        missing = sum(month.missing_slots for month in months)
        expected = sum(month.expected_slots for month in months)
        lines.append(
            f"결측이 있는 달 {fmt.count(len(months), '개')} — "
            f"{fmt.count(missing, '구간')} / {fmt.count(expected, '구간')} "
            f"({fmt.ratio_pct(missing / expected if expected else None)})"
        )
    return tuple(lines[:2])


def _missing_month_frame(quality: QualityReport) -> pd.DataFrame:
    longest = monthly_longest_gaps(quality.gaps, quality.interval_minutes)
    rows = [
        {
            "월": str(month.month),
            "결측 구간 수": int(month.missing_slots),
            "비율": fmt.ratio_pct(month.ratio),
            "최장 연속 구간": (
                f"{fmt.count(gap.slots, '구간')} ({fmt.count(gap.days, '일', decimals=2)})"
                if (gap := longest.get(month.month)) is not None
                else fmt.DASH
            ),
        }
        for month in quality.monthly
        if month.missing_slots
    ]
    return pd.DataFrame(rows, columns=["월", "결측 구간 수", "비율", "최장 연속 구간"])


#: 기온 — (일평균 기온 계열, 기상 출처) 또는 못 받았으면 ``None``.
_Temperature = tuple[pd.Series, str] | None


def _diagnose(state: _State, usage: UsageData) -> tuple[QualityReport, Diagnosis, _Temperature]:
    """무거운 일 — 일꾼 스레드에서 돈다. 공용 함수만 부른다.

    운영 시간대와 제주 여부는 옆단 건물 정보에서 온다 (Streamlit 과 같다). 기온은 지역을
    골랐을 때만 받고, 못 받으면 ``None`` 이다 (공용 ``daily_temperature`` 가 예외를 삼킨다).
    """
    contract_kw = state.form.contract_kw if state.form else None
    quality = load_quality(usage, contract_kw=contract_kw)
    building = state.building
    diagnosis = diagnose_usage(
        usage,
        state.table,
        state.form,
        quality=quality,
        operating_hours=building.operating_hours,
        jeju=building.jeju,
    )
    # 품질은 진단 앞의 것을 함께 넘긴다 — Streamlit 2단계가 받는 것과 같은 값이다.
    return quality, diagnosis, daily_temperature(usage, building.region_key)


# --------------------------------------------------------------------- 놓는 조각


def _notices(diagnosis: Diagnosis) -> None:
    """차단과 주의만 본문에 — 등급 판정은 공용 ``screen_notices`` 가 한다."""
    merged = (*diagnosis.quality.notices, *diagnosis.notices)
    _missing, rest = partition_facts(screen_notices(merged), _MISSING_FACTS)
    _contract, rest = partition_facts(rest, _CONTRACT_FACTS)
    _tentative, rest = partition_facts(rest, _TENTATIVE_FACTS)
    notice_block(rest, merged)


def _quality(usage: UsageData, quality: QualityReport) -> None:
    """데이터 품질 — 경고는 위 안내가 냈다. 여기는 사실만 (Streamlit ``_quality_block``)."""
    ui.label("데이터 품질").classes("k-section")
    outage_hours = sum(event.duration_hours for event in quality.outages)
    _metrics(
        [
            ("검침 간격", f"{usage.meta.interval_minutes}분"),
            (
                "결측",
                fmt.ratio_pct(quality.missing_ratio),
                "",
                "데이터가 없는 구간 ÷ 전체 구간.\n\n"
                "높을수록 덜 본 자료입니다 — 그 달의 최대수요를 그대로 믿기 어렵습니다.",
            ),
            (
                "정전 추정",
                fmt.count(len(quality.outages), "건"),
                fmt.count(outage_hours, "시간", decimals=1) if quality.outages else "",
                "연속 결측 중 정전 흔적(그리드 이탈 행·복전 후 저부하)이 둘 다 있는 구간입니다.\n\n"
                "아래 작은 숫자가 합친 지속 시간입니다. 정전 중에는 피크가 날 수 없어 "
                "편중 판정에서 뺍니다.",
            ),
        ]
    )
    lines = list(_missing_lines(quality))
    if quality.skew.flagged:
        lines.append(
            f"피크 시간대 편중 배수 {fmt.count(quality.skew.multiple, decimals=2)} → "
            "그 달의 최대수요가 실제보다 낮게 잡혔을 수 있음"
        )
    lines += [
        n.text for n in quality.notices if n.fact in (_DROPPED_ROWS_FACT, HOURLY_INTERVAL_FACT)
    ]
    with ui.card().classes("k-card w-full gap-1"):
        for line in lines:
            ui.markdown(fmt.markdown_safe(line)).classes("k-caution")
    frame = _missing_month_frame(quality)
    if not frame.empty:
        with ui.expansion("결측 구간").classes("w-full"):
            _table(frame, width="w-auto")  # 열 넷 — 폭 전체로 늘이면 한 줄을 눈으로 못 따라간다
    _caption("결측은 보간하지 않습니다.")


def _pattern(diagnosis: Diagnosis, usage: UsageData, temperature: _Temperature) -> None:
    """부하 패턴 — 지표 넷과 기준 시간대 (Streamlit ``_pattern_block``). 풀이는 ``narrative``."""
    ui.label("부하 패턴").classes("k-section")
    pattern = diagnosis.pattern
    terms = narrative.terms(pattern)
    _metrics(
        [
            (label, fmt.ratio_pct(getattr(pattern, key)), "", terms[key].tooltip)
            for label, key in (
                ("부하율", "load_factor"),
                ("기저부하 비율", "base_load_ratio"),
                ("주말 부하 비율", "weekend_ratio"),
                ("운영시간 외 부하 비중", "off_hours_energy_share"),
            )
        ]
    )
    _caption(
        f"야간 {pattern.night_hours[0]}{fmt.RANGE}{pattern.night_hours[1]}시 · "
        f"운영 {pattern.operating_hours[0]}{fmt.RANGE}{pattern.operating_hours[1]}시 기준."
    )
    # 지역이 없거나 기상을 못 받으면 빈 축을 남기지 않고 한 줄로 갈음한다 (Streamlit 과 같다).
    if temperature is None or temperature[0].empty:
        _caption("옆단에서 지역을 고르면 일평균 기온을 함께 그립니다.")
        return
    series, source = temperature
    vega.chart(charts.daily_temperature_chart(usage, series))
    _caption(f"일별 사용량과 일평균 기온 · {weather_source_label(source)}")


def _monthly_table(monthly: pd.DataFrame) -> None:
    """월별 명세 — 사용자가 확인할 열만 (Streamlit ``_monthly_table``)."""
    columns = [name for name in _MONTHLY_COLUMNS if name in monthly.columns]
    if "excess_won" in columns and not monthly["excess_won"].any():
        columns.remove("excess_won")
    _table(
        localize(monthly[columns], index_name="월").reset_index(),
        tips={"부분 월": "검침 기간이 한 달에 못 미치는 달입니다."},
    )
    _caption("계산에 쓴 중간값(하한 적용 전 수요·기본요금 기준전력 등)은 Excel 에 있습니다.")


def _tentative(table: TariffTable, form: ContractForm | None) -> None:
    """기본요금을 계약전력으로 매긴 자리에 전제를 알린다 (Streamlit ``_tentative_basis_block``)."""
    if form is None:
        return
    contract_type = table.contract(form.contract_type)
    if contract_type.base_fee_on_contract_at(form.voltage):
        _caution(
            f"**{contract_type.label}** — 계약전력 {fmt.kw(form.contract_kw)} 기준으로 "
            f"계산했습니다. {TENTATIVE_BASE_FEE_BASIS_WARNING}"
        )


def _season_chart(draw: object, available: object, caption: str) -> None:
    """계절 갈래를 눌러 바꾼다 — 단추가 그림 바로 위에 있다 (바꾸는 것 곁에 두는 것)."""
    choices = _seasons(available)
    labels = {label: key for label, key in choices}
    toggle = ui.toggle(list(labels), value="전체").props("no-caps unelevated rounded")
    toggle.set_visibility(len(choices) > 1)
    box = ui.column().classes("w-full")

    def show(key: str | None) -> None:
        box.clear()
        with box:
            vega.chart(draw(key))  # type: ignore[operator]

    toggle.on_value_change(lambda e: show(labels[e.value]))
    show(None)
    _caption(caption)


def _results(
    usage: UsageData, diagnosis: Diagnosis, temperature: _Temperature, building: BuildingInfo
) -> None:
    meta = usage.meta
    _metrics(
        [
            ("분석 기간", fmt.days(meta.period_days), fmt.period(meta.start, meta.end)),
            ("최대수요", notices.max_demand_text(meta.max_demand_kw), ""),
            (
                "부하율",
                fmt.ratio_pct(diagnosis.pattern.load_factor),
                "",
                narrative.terms(diagnosis.pattern)["load_factor"].tooltip,
            ),
            ("기간 사용량", fmt.mwh(meta.total_kwh), ""),
        ]
    )
    _notices(diagnosis)
    _quality(usage, diagnosis.quality)
    _pattern(diagnosis, usage, temperature)

    # ---- 피크 특성
    ui.label("피크 특성").classes("k-section")
    peak = diagnosis.peak
    split = notices.demand_split(peak.peak_kw, peak.billing_demand_kw)
    weekend = fmt.ratio_pct(peak.weekend_slots / peak.top_n if peak.top_n else None)
    if split:
        cells = [
            ("관측 최대수요", notices.max_demand_text(peak.peak_kw), ""),
            ("요금적용전력", notices.billing_demand_text(peak.billing_demand_kw), ""),
        ]
    else:
        cells = [
            ("최대수요 = 요금적용전력", notices.billing_demand_text(peak.billing_demand_kw), ""),
            ("상위 구간 정오 비중", fmt.ratio_pct(diagnosis.summary.pv_midday_share), ""),
        ]
    _metrics([*cells, ("상위 구간 주말 비중", weekend, "")])
    if split:
        _caption(
            f"관측 최대 {fmt.kw(peak.peak_kw)} 중 경부하 시간대 구간은 요금적용전력 "
            f"대상에서 제외되어 {fmt.kw(peak.billing_demand_kw)} 가 적용됩니다."
        )
    vega.chart(charts.monthly_peak_chart(peak, split=split))
    _caption("월별 최대수요")
    _season_chart(
        lambda season: charts.hourly_profile_chart(peak, season=season),
        peak.hourly_profile_by_season.columns,
        "시간대별 평균 부하",
    )
    vega.chart(charts.top_hour_chart(peak, split=split))
    _caption(f"상위 {peak.top_n}구간 발생 시각")

    # ---- 현재 요금 구조 (계약 정보가 있어야 나온다)
    structure = diagnosis.structure
    if structure is None:
        return
    ui.label("현재 요금 구조").classes("k-section")
    total = structure.total_won
    unmeasured = bool(notices.excess_not_measured_line(structure.bill))
    excess = structure.excess_won
    _metrics(
        [
            ("기본요금", fmt.won_short(structure.base_with_power_factor_won), ""),
            ("전력량요금", fmt.won_short(structure.energy_won), ""),
            *(
                [("초과사용부가금", fmt.won_short(excess) if excess else notices.UNPRICED, "")]
                if excess or unmeasured
                else []
            ),
            ("합계", fmt.won_short(total), ""),
            (
                "기본요금 비중",
                fmt.ratio_pct(structure.base_with_power_factor_share if total else None),
                "",
            ),
        ]
    )
    _caption(AMI_BASIS_NOTICE)
    vega.chart(charts.monthly_charge_chart(structure))
    _caption("월별 요금 구성")
    with ui.row().classes("w-full gap-3 no-wrap"):
        for label, key in _seasons(structure.band_season_kwh.index):
            kwh = float(charts.band_frame(structure, season=key)["사용량(kWh)"].sum())
            with ui.column().classes("flex-1 min-w-0 items-center"):
                ui.label(f"{label} · {fmt.mwh(kwh)}").classes("k-label")
                vega.chart(charts.band_donut_chart(structure, season=key))
    _caption("계시별 사용량 구성")
    # 연면적을 넣었을 때만 한 줄 — 국내 평균과 견주지 않는다 (Streamlit ``_intensity_line``).
    intensity = intensity_kwh_per_m2(meta.total_kwh, building)
    if intensity is not None and building.floor_area_m2 is not None:
        ui.markdown(
            f"기간 원단위 **{fmt.count(intensity, 'kWh/m²', decimals=1)}** "
            f"(연면적 {fmt.count(building.floor_area_m2, 'm²', decimals=0)} 기준)"
        )
    _caption(
        "기본요금과 전력량요금만 계산합니다. 그 밖의 요금요소는 미포함이며 실제 절감액은 "
        "이보다 큽니다."
    )
    with ui.expansion("월별 명세").classes("w-full"):
        _monthly_table(structure.monthly)


# --------------------------------------------------------------------- 입력


def _columns(usage: UsageData, on_pick: object) -> None:
    """열 인식 결과와 고치는 칸 — 자동 판정이 빗나가면 고친다 (Streamlit ``_column_block``)."""
    detection = usage.meta.columns
    names = list(detection.columns) or [detection.date_column, detection.energy_column]
    title = f"열 인식 결과 — {detection.describe()}"
    # 회색 글은 머리에만 — 안의 입력칸까지 물려받으면 보조글처럼 보인다.
    with ui.expansion(title).classes("w-full").props('header-class="k-note"'):
        with ui.grid(columns=2).classes("w-full gap-x-4"):
            date_sel = ui.select(names, value=detection.date_column, label="검침일 열")
            energy_sel = ui.select(names, value=detection.energy_column, label="전력량 열")

        # 고르면 바로 다시 읽는다 — Streamlit 과 같다.
        async def picked() -> None:
            await on_pick(date_sel.value, energy_sel.value)  # type: ignore[operator]

        date_sel.on_value_change(picked)
        energy_sel.on_value_change(picked)
        _caption("자동 판정이 빗나갔으면 여기서 고칩니다.")
        if detection.date_candidates or detection.energy_candidates:
            _table(
                pd.DataFrame(
                    [
                        {"역할": role, "열": item.column, "점수": item.score, "근거": item.reason}
                        for role, ranked in (
                            ("검침일", detection.date_candidates),
                            ("전력량", detection.energy_candidates),
                        )
                        for item in ranked
                    ]
                )
            )


def _sidebar(on_change: object) -> None:
    """옆단 — 건물 정보 (Streamlit ``render_sidebar`` 와 같은 칸 · 같은 풀이).

    글을 치는 칸(건물명 · 연면적 · 준공연도)은 **칸을 떠날 때** 반영한다 — 한 글자마다 다시
    계산하지 않는다. 운영 시간대는 손을 뗄 때 반영한다.
    """
    unset = "선택 안 함"
    uses = building_uses()
    provinces = list_provinces()

    ui.label("건물 정보").classes("k-label")
    with ui.input("건물명 (선택)", placeholder=NAME_MISSING).classes("w-full") as name_in:
        _tip("산출물 표지와 본문에 들어갑니다. 넣지 않으면 「건물명 미입력」 으로 나갑니다.")
    with ui.select(
        {"": unset} | {u.key: u.label for u in uses}, value="", label="용도 (선택)"
    ).classes("w-full") as use_sel:
        _tip(
            "계약종별 드롭다운의 후보를 좁힙니다. 판정이 아니라 좁히기이며, "
            "고르지 않으면 전 종별을 보입니다. 최종 판단은 청구서입니다."
        )
    # 「안 고른 상태」 가 있어야 한다 — 첫 지역이 고른 것처럼 보이면 일사량이 조용히 틀린다.
    with ui.select({"": unset} | {p: p for p in provinces}, value="", label="시도 (선택)").classes(
        "w-full"
    ) as province_sel:
        _tip("고르지 않으면 태양광과 기온 그래프를 계산하지 않습니다.")
    with ui.select({}, label="시군구").classes("w-full") as sigungu_sel:
        _tip("태양광 기상 격자를 고릅니다. 격자가 25–31 km 라 같은 격자면 결과가 같습니다.")
    sigungu_sel.set_visibility(False)

    hours_label = ui.label().classes("k-label mt-2")
    with ui.range(min=0, max=24, step=1, value={"min": 9, "max": 18}).props(
        "label markers snap"
    ) as hours_in:
        hours_tip = ui.tooltip().classes("k-tip").props(_TIP_SIDE)
    area_in = ui.number("연면적 (m², 선택)", min=0, step=100).classes("w-full")
    with area_in:
        _tip("넣으면 기간 원단위(kWh/m²)를 요금 구조에 한 줄 더합니다.")
    year_in = ui.number("준공연도 (선택)", min=0, max=2100, step=1, format="%d").classes("w-full")
    with year_in:
        _tip("비워 두면 넣지 않은 것으로 봅니다.")

    def info() -> BuildingInfo:
        start, end = int(hours_in.value["min"]), int(hours_in.value["max"])
        # 시작이 끝보다 늦거나 같으면 뜻이 없다 — 기본값으로 되돌린다 (Streamlit ``_hours``).
        hours = (start, end) if start < end else DEFAULT_OPERATING_HOURS
        return BuildingInfo(
            region_key=(sigungu_sel.value or "") if province_sel.value else "",
            name=name_in.value or "",
            use_key=use_sel.value or "",
            floor_area_m2=float(area_in.value) if area_in.value else None,
            built_year=int(year_in.value) if year_in.value else None,
            operating_hours=hours,
        )

    def hours_text() -> None:
        start, end = info().operating_hours
        hours_label.set_text(f"운영 시간대 {start}{fmt.RANGE}{end}시")
        windows = dr_market_windows(jeju=info().jeju)
        bid = "·".join(f"{s:02d}–{e:02d}시" for s, e in windows)
        hours_tip.clear()
        with hours_tip:
            ui.markdown(
                "평일 이 시간대 밖의 부하를 따로 셉니다. 경제성DR 의 저부하일 판정에도 씁니다.\n\n"
                f"DR {BID_WINDOW}(평일 {bid})와는 다른 값입니다."
            )

    async def changed(*_: object) -> None:
        hours_text()
        await on_change(info())  # type: ignore[operator]

    async def province_changed(*_: object) -> None:
        regions = list_sigungu(province_sel.value) if province_sel.value else ()
        sigungu_sel.set_options(
            {r.key: r.name for r in regions}, value=regions[0].key if regions else None
        )
        sigungu_sel.set_visibility(bool(regions))
        await changed()

    use_sel.on_value_change(changed)
    province_sel.on_value_change(province_changed)
    sigungu_sel.on_value_change(changed)
    hours_in.on("change", changed)  # 손을 뗄 때
    for typed in (name_in, area_in, year_in):
        typed.on("blur", changed)
        typed.on("keydown.enter", changed)
    hours_text()


def _contract_inputs(
    state: _State, on_confirm: object, on_dirty: object
) -> tuple[ui.button, object]:
    """계약 정보 넷과 역률. (확정 단추, 옆단이 용도를 바꿨을 때 부를 종별 새로 고침)."""
    table = state.table
    types = contract_type_choices(table)
    by_key = {c.key: c for c in types}

    with ui.card().classes("k-card w-full"):
        ui.label("계약 정보").classes("k-label")
        with ui.grid(columns=2).classes("w-full gap-x-4"):
            with ui.column().classes("gap-0"):
                type_sel = ui.select(
                    {c.key: c.label for c in types}, value=types[0].key, label="계약종별"
                ).classes("w-full")
                # 교육시설일 때만 한 줄 — 계산에 넣지 않은 갈래를 후보 곁에 적는다.
                school = ui.label(notices.SCHOOL_HIGH_VOLTAGE_NOTICE).classes("k-note")
                school.set_visibility(False)
            kw_in = ui.number("계약전력 (kW)", min=0, step=1, placeholder="청구서 기재값")
            volt_sel = ui.select({}, label="전압구분")
            opt_sel = ui.select({}, label="선택요금")

        # 역률은 아는 사람만 넣는 값이라 접어 둔다 (Streamlit 「역률 (선택)」 과 같다).
        with (
            ui.expansion("역률 (선택)").classes("w-full"),
            ui.grid(columns=2).classes("w-full gap-x-4"),
        ):
            with ui.number(
                "주간 지상역률 (%)", value=default_lagging_pct(), min=1, max=100, step=0.1
            ) as lag_in:
                _tip("모르면 그대로 두십시오. 이 값에서 조정액이 0원입니다.")
            with ui.column().classes("gap-0") as lead_box:
                lead_in = ui.number("야간 진상역률 (%)", min=1, max=100, step=0.1)
                _caption(
                    "야간 진상역률을 모르면 비워 두십시오 — "
                    "지상으로 간주해 야간 추가요금을 매기지 않습니다."
                )

        # 확정 뒤 입력을 바꾸면 아래 금액이 아직 옛 입력의 값이라는 것을 알린다 — 주의 꼴로,
        # 옛 결과도 함께 흐린다. 확정한 값으로 되돌리면 다시 꺼진다.
        dirty = ui.markdown(
            "⚠ **바꾼 값은 「계약 정보 확정」 을 눌러야 계산에 들어갑니다.**"
        ).classes("k-caution")
        dirty.set_visibility(False)

        def current() -> ContractForm:
            choice = by_key[type_sel.value]
            leads = lead_in.value is not None and leading_charge_applies(volt_sel.value)
            return ContractForm(
                contract_type=choice.contract_type,
                voltage=volt_sel.value,
                option=opt_sel.value,
                contract_kw=float(kw_in.value) if kw_in.value else None,
                power_factor_pct=(
                    float(lag_in.value) if lag_in.value is not None else default_lagging_pct()
                ),
                leading_power_factor_pct=float(lead_in.value) if leads else None,
                school_exception=choice.school_exception,
            )

        def touched() -> None:
            changed = state.form is not None and current() != state.form
            dirty.set_visibility(changed)
            on_dirty(changed)  # type: ignore[operator]

        def refresh_options() -> None:
            ct = by_key[type_sel.value].contract_type
            opts = option_choices(table, ct, volt_sel.value) if volt_sel.value else ()
            opt_sel.set_options({o: option_label(o) for o in opts}, value=opts[0] if opts else None)
            # 저압은 야간 진상역률 요금 대상이 아니다 — 결과에 안 닿는 칸을 그리지 않는다.
            lead_box.set_visibility(
                volt_sel.value is None or leading_charge_applies(volt_sel.value)
            )

        def refresh_voltages() -> None:
            volts = voltage_choices(table, by_key[type_sel.value].contract_type)
            volt_sel.set_options(dict(volts), value=volts[0][0] if volts else None)
            refresh_options()

        type_sel.on_value_change(lambda _: refresh_voltages())
        volt_sel.on_value_change(lambda _: refresh_options())
        refresh_voltages()
        for field in (type_sel, kw_in, volt_sel, opt_sel, lag_in, lead_in):
            field.on_value_change(lambda _: touched())

        def narrow() -> None:
            """옆단 용도로 종별 후보를 좁힌다 — 판정이 아니라 좁히기다. 고른 값은 남으면 둔다."""
            narrowed = narrow_contract_types(types, state.building.use_key)
            keys = [c.key for c in narrowed]
            keep = type_sel.value if type_sel.value in keys else keys[0]
            type_sel.set_options({c.key: c.label for c in narrowed}, value=keep)
            school.set_visibility(state.building.education)

        async def confirm() -> None:
            state.form = current()
            dirty.set_visibility(False)
            on_dirty(False)  # type: ignore[operator]
            await on_confirm()  # type: ignore[operator]

        button = ui.button("계약 정보 확정", on_click=confirm).props("unelevated no-caps")
        return button, narrow


def build(state: _State) -> None:
    ui.add_css(_CSS)
    ui.dark_mode(value=None)  # OS 설정을 따른다

    # 옆단은 건물 이야기다 — 어느 단계에서나 같은 값이다 (Streamlit 옆단과 같은 자리).
    # 서랍은 페이지 맨 위에 있어야 해서 먼저 짓는다. 아래 이름들은 부를 때 찾는다.
    async def building_changed(info: BuildingInfo) -> None:
        state.building = info
        narrow_types()  # type: ignore[operator]
        await analyse()

    with ui.left_drawer(value=True, bordered=True).classes("p-4 gap-2"):
        _sidebar(building_changed)

    with ui.column().classes("w-full max-w-5xl mx-auto px-6 pt-4 gap-0"):
        ui.label("kWise").classes("k-title")
        # 세 단계를 탭으로 — 번호를 남긴다 (Streamlit ``TABS`` 와 같은 이름).
        with ui.tabs().props("align=left no-caps inline-label").classes("w-full") as tabs:
            tab1 = ui.tab("1단계 · 진단")
            tab2 = ui.tab("2단계 · 개선 수단")
            tab3 = ui.tab("3단계 · 개선안 조합")
    panels = ui.tab_panels(tabs, value=tab1).classes("w-full max-w-5xl mx-auto bg-transparent")
    with panels, ui.tab_panel(tab2):
        stage2 = ui.column().classes("w-full gap-3")
        with stage2:
            w.note(_NOT_READY)
    with panels, ui.tab_panel(tab3):
        ui.label("3단계 · 개선안 조합").classes("k-section")
        w.note("3단계 화면은 데스크톱에 아직 없습니다. 지금은 Streamlit 화면에서 보십시오.")

    def stages(usage: UsageData, quality: QualityReport, diagnosis: Diagnosis) -> None:
        """1단계가 끝날 때마다 2단계를 새로 짓는다 — 계약 정보를 확정해야 선다."""
        stage2.clear()
        with stage2:
            if state.form is None:
                w.note(_NOT_READY)
                return
            ctx = measures.Context(
                usage=usage,
                table=state.table,
                form=state.form,
                diagnosis=diagnosis,
                quality=quality,
                building=state.building,
            )
            measures.render(ctx, state.choices)

    with panels, ui.tab_panel(tab1), ui.column().classes("w-full gap-4 p-0"):
        _caption("파일만 올려도 결과가 나옵니다. 금액은 계약 정보를 확정하면 나옵니다.")
        # 위에서 아래로: 계약 정보 → 잠정 경고 → 파일 → 상태 한 줄 → 결과.
        busy = pending = False

        def say(text: str, *, spin: bool = False, error: bool = False) -> None:
            status.clear()
            with status:
                if spin:
                    ui.spinner(size="sm")
                ui.label(text).classes("text-negative" if error else "k-note")

        async def analyse() -> None:
            nonlocal busy, pending
            tentative.clear()
            with tentative:
                _tentative(state.table, state.form)
            if state.data is None:
                if state.form is not None:
                    say("계약 정보를 확정했습니다. 파일을 올리면 금액이 나옵니다.")
                return
            if busy:  # 도는 중에 또 부르면 겹치지 않게 — 끝난 뒤 한 번 더 돈다
                pending = True
                return
            busy = True
            confirm_btn.props("loading")
            # 옛 결과는 지우지 않고 흐리게 둔다 — 스크롤이 튀지 않고, 무엇이 바뀌는지 견줄 수 있다.
            results.classes(add="k-stale")
            columns_box.classes(add="k-stale")  # 도는 중에 열을 바꾸면 화면과 계산이 갈린다
            say(f"{state.filename} — 분석하는 중…", spin=True)
            try:
                try:
                    usage = await run.io_bound(_load, state)
                except Exception as exc:  # 형식 오류 등 — 공용 코드의 사유를 그대로 보인다
                    say(f"파일을 읽지 못했습니다. {exc}", error=True)
                    if state.date_column or state.energy_column:
                        with status:
                            ui.button("열 지정 되돌리기", on_click=reset_columns).props(
                                "flat no-caps"
                            )
                    return
                columns_box.clear()
                with columns_box:
                    _columns(usage, pick_columns)
                try:
                    quality, diagnosis, temperature = await run.io_bound(_diagnose, state, usage)
                except Exception as exc:  # 계약 정보와 자료가 안 맞는 경우 등
                    say(f"계산하지 못했습니다. {exc}", error=True)
                    return
                results.clear()
                try:
                    with results:
                        _results(usage, diagnosis, temperature, state.building)
                except Exception as exc:  # 그리는 중에 터져도 「분석하는 중」 에 멈춰 두지 않는다
                    say(f"결과를 그리지 못했습니다. {exc}", error=True)
                    return
                say(state.filename)
                stages(usage, quality, diagnosis)
            finally:
                results.classes(remove="k-stale")
                columns_box.classes(remove="k-stale")
                confirm_btn.props(remove="loading")
                busy = False
            if pending:  # 도는 동안 옆단 · 열을 바꿨으면 그 값으로 다시
                pending = False
                await analyse()

        async def pick_columns(date_column: str, energy_column: str) -> None:
            state.date_column, state.energy_column = date_column, energy_column
            await analyse()

        async def reset_columns() -> None:
            state.date_column = state.energy_column = None
            await analyse()

        def dim(on: bool) -> None:
            results.classes(add="k-dim") if on else results.classes(remove="k-dim")

        confirm_btn, narrow_types = _contract_inputs(state, analyse, dim)
        tentative = ui.column().classes("w-full")

        async def received(e: object) -> None:
            file = e.file  # type: ignore[attr-defined]
            state.data, state.filename = await file.read(), file.name
            state.date_column = state.energy_column = None  # 새 파일은 자동 판정부터
            # 목록이 쌓이면 지금 결과가 어느 파일 것인지 흐려진다 — 비우고 이름은 상태 줄에 둔다.
            e.sender.reset()  # type: ignore[attr-defined]
            await analyse()

        ui.upload(
            label="사용량 파일 (csv · xls · xlsx)",
            auto_upload=True,
            on_upload=received,
        ).props("accept=.csv,.xls,.xlsx flat bordered").classes("w-full")
        status = ui.row().classes("items-center gap-2 min-h-[1.5rem]")
        columns_box = ui.column().classes("w-full")
        results = ui.column().classes("w-full gap-3")


def main(*, native: bool = True) -> None:
    table = load_tariff()
    vega.setup()

    @ui.page("/")
    def index() -> None:
        build(_State(table=table))  # 화면을 열 때마다 새 입력 — 앞 판 값이 남지 않는다

    ui.run(
        title="kWise",
        host="127.0.0.1",  # 이 PC 안에서만 — 망에 열지 않는다
        native=native,
        window_size=(1280, 900) if native else None,
        reload=False,
        show=not native,
        language="ko-KR",
    )
