"""1단계 · 진단 — 결과를 화면 조각(``blocks``)으로 적는다.

Streamlit 1단계(``kwise.ui.views.diagnose``)와 같은 차례 · 같은 글이다.
계산은 공용 ``kwise.ui.pipeline`` 이 하고, 여기는 놓기만 한다.
"""

from __future__ import annotations

import pandas as pd

from kwise.diagnose import Diagnosis
from kwise.io import UsageData
from kwise.quality import QualityReport, monthly_longest_gaps
from kwise.quality.checks import HOURLY_INTERVAL_FACT
from kwise.report import localize, narrative, notices
from kwise.tariff import AMI_BASIS_NOTICE, TENTATIVE_BASE_FEE_BASIS_WARNING, TariffTable
from kwise.tariff.labels import SEASON_LABELS
from kwise.ui import charts
from kwise.ui import text as fmt
from kwise.ui.notices import partition_facts, screen_notices
from kwise.ui.pipeline import ContractForm

from . import blocks as b
from .building import BuildingInfo, intensity_kwh_per_m2, weather_source_label

#: 계절 갈래 — Streamlit ``SEASON_CHOICES`` 와 같다 (그 모듈은 streamlit 을 끌어와 못 부른다).
SEASONS: tuple[tuple[str, str | None], ...] = (
    ("전체", None),
    *((label, key) for key, label in SEASON_LABELS.items()),
)
#: 위쪽 안내에서 뺄 사실 — 결측은 「데이터 품질」 이, 계약 경고는 2단계 카드가, 잠정 경고는
#: 계약 정보 바로 아래가 낸다 (Streamlit ``diagnose.py`` 의 같은 이름 상수 사본).
_MISSING_FACTS = (
    "quality.missing_total",
    "quality.longest_gap",
    "quality.month_missing_rate",
    "quality.peak_skew",
    "quality.dropped_rows",
    HOURLY_INTERVAL_FACT,
)
_DROPPED_ROWS_FACT = "quality.dropped_rows"
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
#: 월별 명세에 낼 열 — Streamlit ``SCREEN_MONTHLY_COLUMNS`` 사본.
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

Temperature = tuple[pd.Series, str] | None


def seasons(available: object) -> tuple[tuple[str, str | None], ...]:
    keys = set(map(str, available))  # type: ignore[call-overload]
    return tuple(item for item in SEASONS if item[1] is None or item[1] in keys)


def tentative(table: TariffTable, form: ContractForm | None) -> list[b.Block]:
    """기본요금을 계약전력으로 매긴 자리에 전제를 알린다 — 계약 정보 바로 아래."""
    with b.collect() as page:
        if form is not None:
            contract_type = table.contract(form.contract_type)
            if contract_type.base_fee_on_contract_at(form.voltage):
                b.caution(
                    f"**{contract_type.label}** — 계약전력 {fmt.kw(form.contract_kw)} 기준으로 "
                    f"계산했습니다. {TENTATIVE_BASE_FEE_BASIS_WARNING}"
                )
    return page


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


def _notices(diagnosis: Diagnosis) -> None:
    merged = (*diagnosis.quality.notices, *diagnosis.notices)
    _missing, rest = partition_facts(screen_notices(merged), _MISSING_FACTS)
    _contract, rest = partition_facts(rest, _CONTRACT_FACTS)
    _tentative, rest = partition_facts(rest, _TENTATIVE_FACTS)
    b.notice_block(rest, merged)


def _quality(usage: UsageData, quality: QualityReport) -> None:
    b.h2("데이터 품질")
    outage_hours = sum(event.duration_hours for event in quality.outages)
    b.metrics(
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
    with b.card():
        for line in lines:
            b.text(line)
    frame = _missing_month_frame(quality)
    if not frame.empty:
        with b.expansion("결측 구간"):
            b.table(frame, compact=True)
    b.caption("결측은 보간하지 않습니다.")


def _pattern(diagnosis: Diagnosis, usage: UsageData, temperature: Temperature) -> None:
    b.h2("부하 패턴")
    pattern = diagnosis.pattern
    terms = narrative.terms(pattern)
    b.metrics(
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
    b.caption(
        f"야간 {pattern.night_hours[0]}{fmt.RANGE}{pattern.night_hours[1]}시 · "
        f"운영 {pattern.operating_hours[0]}{fmt.RANGE}{pattern.operating_hours[1]}시 기준."
    )
    if temperature is None or temperature[0].empty:
        b.caption("옆단에서 지역을 고르면 일평균 기온을 함께 그립니다.")
        return
    series, source = temperature
    b.chart(charts.daily_temperature_chart(usage, series))
    b.caption(f"일별 사용량과 일평균 기온 · {weather_source_label(source)}")


def _monthly_table(monthly: pd.DataFrame) -> None:
    columns = [name for name in _MONTHLY_COLUMNS if name in monthly.columns]
    if "excess_won" in columns and not monthly["excess_won"].any():
        columns.remove("excess_won")
    b.table(
        localize(monthly[columns], index_name="월").reset_index(),
        tips={"부분 월": "검침 기간이 한 달에 못 미치는 달입니다."},
    )
    b.caption("계산에 쓴 중간값(하한 적용 전 수요·기본요금 기준전력 등)은 Excel 에 있습니다.")


def results(
    usage: UsageData, diagnosis: Diagnosis, temperature: Temperature, building: BuildingInfo
) -> list[b.Block]:
    """1단계 결과 한 벌 — 지표 · 안내 · 데이터 품질 · 부하 패턴 · 피크 특성 · 요금 구조."""
    with b.collect() as page:
        meta = usage.meta
        b.metrics(
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

        b.h2("피크 특성")
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
                (
                    "최대수요 = 요금적용전력",
                    notices.billing_demand_text(peak.billing_demand_kw),
                    "",
                ),
                ("상위 구간 정오 비중", fmt.ratio_pct(diagnosis.summary.pv_midday_share), ""),
            ]
        b.metrics([*cells, ("상위 구간 주말 비중", weekend, "")])
        if split:
            b.caption(
                f"관측 최대 {fmt.kw(peak.peak_kw)} 중 경부하 시간대 구간은 요금적용전력 "
                f"대상에서 제외되어 {fmt.kw(peak.billing_demand_kw)} 가 적용됩니다."
            )
        b.chart(charts.monthly_peak_chart(peak, split=split))
        b.caption("월별 최대수요")
        b.chart_choices(
            [
                (label, charts.hourly_profile_chart(peak, season=key))
                for label, key in seasons(peak.hourly_profile_by_season.columns)
            ],
            "시간대별 평균 부하",
        )
        b.chart(charts.top_hour_chart(peak, split=split))
        b.caption(f"상위 {peak.top_n}구간 발생 시각")

        structure = diagnosis.structure
        if structure is not None:
            b.h2("현재 요금 구조")
            total = structure.total_won
            unmeasured = bool(notices.excess_not_measured_line(structure.bill))
            excess = structure.excess_won
            b.metrics(
                [
                    ("기본요금", fmt.won_short(structure.base_with_power_factor_won), ""),
                    ("전력량요금", fmt.won_short(structure.energy_won), ""),
                    *(
                        [
                            (
                                "초과사용부가금",
                                fmt.won_short(excess) if excess else notices.UNPRICED,
                                "",
                            )
                        ]
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
            b.caption(AMI_BASIS_NOTICE)
            b.chart(charts.monthly_charge_chart(structure))
            b.caption("월별 요금 구성")
            donuts = seasons(structure.band_season_kwh.index)
            with b.columns(len(donuts)) as cols:
                for index, (label, key) in enumerate(donuts):
                    kwh = float(charts.band_frame(structure, season=key)["사용량(kWh)"].sum())
                    with cols.column(index):
                        b.caption(f"{label} · {fmt.mwh(kwh)}")
                        b.chart(charts.band_donut_chart(structure, season=key))
            b.caption("계시별 사용량 구성")
            intensity = intensity_kwh_per_m2(meta.total_kwh, building)
            if intensity is not None and building.floor_area_m2 is not None:
                b.text(
                    f"기간 원단위 **{fmt.count(intensity, 'kWh/m²', decimals=1)}** "
                    f"(연면적 {fmt.count(building.floor_area_m2, 'm²', decimals=0)} 기준)"
                )
            b.caption(
                "기본요금과 전력량요금만 계산합니다. 그 밖의 요금요소는 미포함이며 실제 "
                "절감액은 이보다 큽니다."
            )
            with b.expansion("월별 명세"):
                _monthly_table(structure.monthly)
    return page
