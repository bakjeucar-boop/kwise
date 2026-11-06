"""데스크톱 진단 화면 (시제품 · 1단계만).

계산·그래프·문구는 공용 함수를 부른다. 여기에 있는 것은 **화면 연결**뿐이다 —
입력 받기, 공용 함수 부르기, 결과 놓기.

    .venv\\Scripts\\python.exe kwise_desktop.py
"""

from __future__ import annotations

from dataclasses import dataclass

from nicegui import run, ui

from kwise.diagnose import Diagnosis
from kwise.io import UsageData, load_usage_bytes
from kwise.quality import DEFAULT_OPERATING_HOURS
from kwise.report import notices
from kwise.tariff import (
    AMI_BASIS_NOTICE,
    TENTATIVE_BASE_FEE_BASIS_WARNING,
    TariffTable,
    load_tariff,
)
from kwise.tariff.labels import SEASON_LABELS
from kwise.ui import charts
from kwise.ui import text as fmt
from kwise.ui.labels import option_label
from kwise.ui.notices import Severity, partition_facts, screen_notices
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
#: 옮겼다 (그 모듈은 streamlit 을 끌어와 못 부른다). 계약 경고는 2단계 카드 자리라 이 화면엔 없고,
#: 잠정 경고는 계약 정보 바로 아래에 따로 낸다. **결측 안내는 걸러내지 않는다** — Streamlit 은
#: 「데이터 품질」 절에서 내는데 이 시제품엔 그 절이 없어, 거르면 아무 데도 안 뜬다.
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
.k-caution p { margin: 0; font-size: 0.9rem; }
.q-btn:active { transform: scale(0.97); transition: transform 100ms ease-out; }
@media (prefers-reduced-motion: reduce) { .q-btn:active { transform: none; } }
"""


@dataclass
class _State:
    table: TariffTable
    data: bytes | None = None
    filename: str = ""
    form: ContractForm | None = None


def _seasons(available: object) -> tuple[tuple[str, str | None], ...]:
    keys = set(map(str, available))  # type: ignore[call-overload]
    return tuple(item for item in _SEASONS if item[1] is None or item[1] in keys)


def _load(state: _State) -> UsageData:
    assert state.data is not None
    return load_usage_bytes(state.data, state.filename)


def _diagnose(state: _State, usage: UsageData) -> tuple[UsageData, Diagnosis]:
    """무거운 일 — 일꾼 스레드에서 돈다. 공용 함수만 부른다."""
    contract_kw = state.form.contract_kw if state.form else None
    quality = load_quality(usage, contract_kw=contract_kw)
    diagnosis = diagnose_usage(
        usage, state.table, state.form, quality=quality, operating_hours=DEFAULT_OPERATING_HOURS
    )
    return usage, diagnosis


# --------------------------------------------------------------------- 놓는 조각


def _metric(label: str, value: str, sub: str = "") -> None:
    with ui.card().tight().classes("k-card p-4 min-w-[10rem] flex-1"):
        ui.label(label).classes("k-label")
        ui.label(value).classes("k-value")
        if sub:
            ui.label(sub).classes("k-sub")


def _metrics(cells: list[tuple[str, str, str]]) -> None:
    with ui.row().classes("w-full gap-3 flex-wrap"):
        for cell in cells:
            _metric(*cell)


def _caption(text: str) -> None:
    ui.label(text).classes("k-note")


def _caution(text: str) -> None:
    """주의 — Streamlit 의 ``callout.caution`` 과 같다: 배경 없이 ⚠ 와 굵기로만."""
    body = fmt.markdown_safe(text)
    ui.markdown(f"⚠ {body if '**' in body else f'**{body}**'}").classes("k-caution")


def _notices(diagnosis: Diagnosis) -> None:
    """차단과 주의만 본문에 — 등급 판정은 공용 ``screen_notices`` 가 한다."""
    merged = (*diagnosis.quality.notices, *diagnosis.notices)
    _contract, rest = partition_facts(screen_notices(merged), _CONTRACT_FACTS)
    _tentative, rest = partition_facts(rest, _TENTATIVE_FACTS)
    for item in rest:
        # 안내 글은 굵게(``**``)를 품고 있어 마크다운으로 그린다.
        if item.severity is Severity.BLOCK:
            ui.markdown(f"⛔ {fmt.markdown_safe(item.text)}").classes("k-caution text-negative")
        elif item.severity is Severity.WARN:
            _caution(item.text)
        else:
            ui.markdown(f"· {fmt.markdown_safe(item.text)}").classes("k-caution k-note")


def _tentative(table: TariffTable, form: ContractForm | None) -> None:
    """기본요금을 계약전력으로 매긴 자리에 그 전제를 알린다 (Streamlit ``_tentative_basis_block``)."""
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
            ("부하율", fmt.ratio_pct(diagnosis.pattern.load_factor), ""),
            ("기간 사용량", fmt.mwh(meta.total_kwh), ""),
        ]
    )
    _notices(diagnosis)

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


# --------------------------------------------------------------------- 입력


def _contract_inputs(state: _State, on_confirm: object) -> ui.button:
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

        def refresh_options() -> None:
            ct = by_key[type_sel.value].contract_type
            opts = option_choices(table, ct, volt_sel.value) if volt_sel.value else ()
            opt_sel.set_options({o: option_label(o) for o in opts}, value=opts[0] if opts else None)

        def refresh_voltages() -> None:
            volts = voltage_choices(table, by_key[type_sel.value].contract_type)
            volt_sel.set_options(dict(volts), value=volts[0][0] if volts else None)
            refresh_options()

        type_sel.on_value_change(lambda _: refresh_voltages())
        volt_sel.on_value_change(lambda _: refresh_options())
        refresh_voltages()

        async def confirm() -> None:
            choice = by_key[type_sel.value]
            state.form = ContractForm(
                contract_type=choice.contract_type,
                voltage=volt_sel.value,
                option=opt_sel.value,
                contract_kw=float(kw_in.value) if kw_in.value else None,
                power_factor_pct=default_lagging_pct(),
                school_exception=choice.school_exception,
            )
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
            say(f"{state.filename} — 분석하는 중…", spin=True)
            try:
                try:
                    usage = await run.io_bound(_load, state)
                except Exception as exc:  # 형식 오류 등 — 공용 코드의 사유를 그대로 보인다
                    say(f"파일을 읽지 못했습니다. {exc}", error=True)
                    return
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
                confirm_btn.props(remove="loading")
                busy = False

        confirm_btn = _contract_inputs(state, analyse)
        tentative = ui.column().classes("w-full")

        async def received(e: object) -> None:
            file = e.file  # type: ignore[attr-defined]
            state.data, state.filename = await file.read(), file.name
            # 목록이 쌓이면 지금 결과가 어느 파일 것인지 흐려진다 — 비우고 이름은 상태 줄에 둔다.
            e.sender.reset()  # type: ignore[attr-defined]
            await analyse()

        ui.upload(
            label="사용량 파일 (csv · xls · xlsx)",
            auto_upload=True,
            on_upload=received,
        ).props("accept=.csv,.xls,.xlsx flat bordered").classes("w-full")
        status = ui.row().classes("items-center gap-2 min-h-[1.5rem]")
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
