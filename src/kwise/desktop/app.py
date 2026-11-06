"""데스크톱 진단 화면 (시제품 · 1단계만).

계산·그래프·문구는 공용 함수를 부른다. 여기에 있는 것은 **화면 연결**뿐이다 —
입력 받기, 공용 함수 부르기, 결과 놓기.

    .venv\\Scripts\\python.exe kwise_desktop.py
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from nicegui import run, ui

from kwise.diagnose import Diagnosis
from kwise.io import UsageData, load_usage_bytes
from kwise.notices import tooltip
from kwise.quality import DEFAULT_OPERATING_HOURS, QualityReport, monthly_longest_gaps
from kwise.quality.checks import HOURLY_INTERVAL_FACT
from kwise.report import localize, narrative, notices
from kwise.report.columns import display_frame
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
from kwise.ui.notices import Severity, partition_facts, screen_notices, tooltip_text
from kwise.ui.pipeline import (
    ContractForm,
    contract_type_choices,
    default_lagging_pct,
    diagnose_usage,
    load_quality,
    option_choices,
    voltage_choices,
)

from . import vega

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

_CSS = """
:root { --k-muted: #6e6e73; }
.body--dark { --k-muted: #8e8e93; }
body { font-family: system-ui, sans-serif; line-height: 1.5; }
.k-title { font-size: 1.75rem; font-weight: 700; letter-spacing: -0.02em; line-height: 1.1; }
.k-section { font-size: 1.2rem; font-weight: 650; letter-spacing: -0.01em; margin-top: 1.5rem; }
.k-label { font-size: 0.8rem; color: var(--k-muted); letter-spacing: 0.01em; }
.k-value { font-size: 1.6rem; font-weight: 650; letter-spacing: -0.02em; line-height: 1.15; }
.k-sub { font-size: 0.8rem; color: var(--k-muted); }
.k-note { font-size: 0.85rem; color: var(--k-muted); }
.k-card { border: 1px solid rgba(142, 142, 147, 0.3); border-radius: 12px; box-shadow: none; }
.k-stale { opacity: 0.45; pointer-events: none; }
.k-dim { opacity: 0.6; }
.k-caution p { margin: 0; font-size: 0.9rem; }
.k-tip { white-space: pre-line; max-width: 22rem; font-size: 0.8rem; }
.q-btn:active { transform: scale(0.97); transition: transform 100ms ease-out; }
@media (prefers-reduced-motion: reduce) { .q-btn:active { transform: none; } }
"""


@dataclass
class _State:
    table: TariffTable
    data: bytes | None = None
    filename: str = ""
    form: ContractForm | None = None
    #: 열 지정 — ``None`` 이면 자동 판정을 따른다.
    date_column: str | None = None
    energy_column: str | None = None


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


def _diagnose(state: _State, usage: UsageData) -> tuple[UsageData, Diagnosis]:
    """무거운 일 — 일꾼 스레드에서 돈다. 공용 함수만 부른다."""
    contract_kw = state.form.contract_kw if state.form else None
    quality = load_quality(usage, contract_kw=contract_kw)
    diagnosis = diagnose_usage(
        usage, state.table, state.form, quality=quality, operating_hours=DEFAULT_OPERATING_HOURS
    )
    return usage, diagnosis


# --------------------------------------------------------------------- 놓는 조각


def _tip(text: str) -> None:
    """바로 앞 요소에 마우스를 올리면 뜨는 풀이 (Streamlit ``help=`` 자리 · 마크다운)."""
    with ui.tooltip().classes("k-tip"):
        ui.markdown(fmt.markdown_safe(text))


def _metric(label: str, value: str, sub: str = "", tip: str = "") -> None:
    with ui.card().tight().classes("k-card p-4 min-w-[10rem] flex-1"):
        with ui.label(f"{label} ⓘ" if tip else label).classes("k-label"):
            if tip:
                _tip(tip)
        ui.label(value).classes("k-value")
        if sub:
            ui.label(sub).classes("k-sub")


def _metrics(cells: list[tuple[str, ...]]) -> None:
    with ui.row().classes("w-full gap-3 flex-wrap"):
        for cell in cells:
            _metric(*cell)


def _caption(text: str, tip: str = "") -> None:
    with ui.label(f"{text} ⓘ" if tip else text).classes("k-note"):
        if tip:
            _tip(tip)


def _table(
    frame: pd.DataFrame, *, width: str = "w-full", tips: dict[str, str] | None = None
) -> None:
    """표 하나 — Excel · Streamlit 이 지나는 출구(``display_frame``)를 같이 지난다."""

    def cell(value: object) -> str:
        # 자릿수는 ``display_frame`` 이 접었다 — 여기서는 세 자리 쉼표만 (Streamlit 표와 같다).
        if isinstance(value, float):
            return f"{value:,.0f}" if value.is_integer() else f"{value:,}"
        return "" if pd.isna(value) else str(value)

    shown = display_frame(frame).astype(object).map(cell)
    table = ui.table.from_pandas(shown).props("flat dense bordered").classes(width)
    for column, tip in (tips or {}).items():  # 열 머리 풀이 (Streamlit 의 열 ``help``)
        table.add_slot(
            f"header-cell-{column}",
            f'<q-th :props="props">{{{{ props.col.label }}}} ⓘ<q-tooltip>{tip}</q-tooltip></q-th>',
        )


def _caution(text: str) -> None:
    """주의 — Streamlit 의 ``callout.caution`` 과 같다: 배경 없이 ⚠ 와 굵기로만."""
    body = fmt.markdown_safe(text)
    ui.markdown(f"⚠ {body if '**' in body else f'**{body}**'}").classes("k-caution")


def _notices(diagnosis: Diagnosis) -> None:
    """차단과 주의만 본문에 — 등급 판정은 공용 ``screen_notices`` 가 한다."""
    merged = (*diagnosis.quality.notices, *diagnosis.notices)
    _missing, rest = partition_facts(screen_notices(merged), _MISSING_FACTS)
    _contract, rest = partition_facts(rest, _CONTRACT_FACTS)
    _tentative, rest = partition_facts(rest, _TENTATIVE_FACTS)
    for item in rest:
        # 안내 글은 굵게(``**``)를 품고 있어 마크다운으로 그린다.
        if item.severity is Severity.BLOCK:
            ui.markdown(f"⛔ {fmt.markdown_safe(item.text)}").classes("k-caution text-negative")
        elif item.severity is Severity.WARN:
            _caution(item.text)
        else:
            ui.markdown(f"· {fmt.markdown_safe(item.text)}").classes("k-caution k-note")
    grounds = tooltip(merged)
    if grounds:
        _caption(f"산출 근거 {len(grounds)}건", tooltip_text(merged))


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


def _pattern(diagnosis: Diagnosis) -> None:
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


def _results(usage: UsageData, diagnosis: Diagnosis) -> None:
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
    _pattern(diagnosis)

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


def _contract_inputs(state: _State, on_confirm: object, on_dirty: object) -> ui.button:
    table = state.table
    types = contract_type_choices(table)
    by_key = {c.key: c for c in types}

    with ui.card().classes("k-card w-full"):
        ui.label("계약 정보").classes("k-label")
        with ui.grid(columns=2).classes("w-full gap-x-4"):
            type_sel = ui.select(
                {c.key: c.label for c in types}, value=types[0].key, label="계약종별"
            )
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

        async def confirm() -> None:
            state.form = current()
            dirty.set_visibility(False)
            on_dirty(False)  # type: ignore[operator]
            await on_confirm()  # type: ignore[operator]

        return ui.button("계약 정보 확정", on_click=confirm).props("unelevated no-caps")


def build(state: _State) -> None:
    ui.add_css(_CSS)
    ui.dark_mode(value=None)  # OS 설정을 따른다

    with ui.column().classes("w-full max-w-5xl mx-auto p-6 gap-4"):
        ui.label("kWise · 진단").classes("k-title")
        _caption("파일만 올려도 결과가 나옵니다. 금액은 계약 정보를 확정하면 나옵니다.")
        # 위에서 아래로: 계약 정보 → 잠정 경고 → 파일 → 상태 한 줄 → 결과.
        busy = False

        def say(text: str, *, spin: bool = False, error: bool = False) -> None:
            status.clear()
            with status:
                if spin:
                    ui.spinner(size="sm")
                ui.label(text).classes("text-negative" if error else "k-note")

        async def analyse() -> None:
            nonlocal busy
            tentative.clear()
            with tentative:
                _tentative(state.table, state.form)
            if state.data is None:
                if state.form is not None:
                    say("계약 정보를 확정했습니다. 파일을 올리면 금액이 나옵니다.")
                return
            if busy:  # 도는 중에 또 누르면 겹치지 않게 — 단추도 잠가 둔다
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
                    usage, diagnosis = await run.io_bound(_diagnose, state, usage)
                except Exception as exc:  # 계약 정보와 자료가 안 맞는 경우 등
                    say(f"계산하지 못했습니다. {exc}", error=True)
                    return
                results.clear()
                with results:
                    _results(usage, diagnosis)
                say(state.filename)
            finally:
                results.classes(remove="k-stale")
                columns_box.classes(remove="k-stale")
                confirm_btn.props(remove="loading")
                busy = False

        async def pick_columns(date_column: str, energy_column: str) -> None:
            state.date_column, state.energy_column = date_column, energy_column
            await analyse()

        async def reset_columns() -> None:
            state.date_column = state.energy_column = None
            await analyse()

        def dim(on: bool) -> None:
            results.classes(add="k-dim") if on else results.classes(remove="k-dim")

        confirm_btn = _contract_inputs(state, analyse, dim)
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
