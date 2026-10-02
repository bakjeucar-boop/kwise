"""품질 검사 단위테스트 (요구사항서 4장, 6.1, 부록 B)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from kwise.diagnose import ContractInfo, diagnose
from kwise.io import SHORT_PERIOD_WARNING, UsageData, load_usage
from kwise.notices import texts
from kwise.quality import (
    QualityReport,
    check_quality,
    detect_outages,
    fill_missing,
    find_missing_gaps,
    load_pattern,
    monthly_missing,
    peak_hour_skew,
)
from kwise.tariff import TariffSelection, TariffTable, calculate_bill
from kwise.tariff.engine import _short_period_direction
from tests._synthetic import (
    label_timestamps,
    make_labels,
    march_2024_dates,
    month_rows,
    one_day,
    parse_label,
    to_rows,
    write_csv,
    write_raw_csv,
)

NOVEMBER_GAP = (pd.Timestamp("2023-11-03 17:30"), pd.Timestamp("2023-11-13 09:45"), 930)
APRIL_GAP = (pd.Timestamp("2024-04-06 19:30"), pd.Timestamp("2024-04-07 05:45"), 42)


# --------------------------------------------------------------------- 결측 (부록 B)


def test_sample_missing_counts(sample_report: QualityReport) -> None:
    assert sample_report.expected_slots == 35_328
    assert sample_report.observed_slots == 34_356
    assert sample_report.missing_slots == 972
    assert sample_report.missing_ratio == pytest.approx(0.028, abs=0.001)


def test_sample_has_exactly_two_gaps(sample_report: QualityReport) -> None:
    """부록 B 의 결측 구간 2개. 개수·경계·길이가 모두 맞아야 한다."""
    assert len(sample_report.gaps) == 2
    november, april = sample_report.gaps
    assert (november.start, november.end, november.slots) == NOVEMBER_GAP
    assert november.days == pytest.approx(9.69, abs=0.01)
    assert (april.start, april.end, april.slots) == APRIL_GAP
    assert april.days == pytest.approx(0.44, abs=0.01)


def test_sample_longest_gap_is_flagged(sample_report: QualityReport) -> None:
    gap = sample_report.longest_gap
    assert gap is not None
    assert gap.slots == 930
    assert gap.is_long  # 1일 초과
    assert any("최장 연속 결측" in message for message in texts(sample_report.notices))


def test_sample_november_missing_rate(sample_report: QualityReport) -> None:
    """11월 결측률 약 32% → 신뢰 제한 표시."""
    monthly = {month.month: month for month in sample_report.monthly}
    november = monthly[pd.Period("2023-11", freq="M")]
    assert november.missing_slots == 930
    assert november.expected_slots == 2_880
    assert november.ratio == pytest.approx(0.323, abs=0.005)
    assert november.flagged


def test_sample_only_november_exceeds_monthly_threshold(sample_report: QualityReport) -> None:
    assert [month.month for month in sample_report.flagged_months] == [
        pd.Period("2023-11", freq="M")
    ]
    assert any("신뢰 제한" in message for message in texts(sample_report.notices))


def test_monthly_attribution_uses_slot_start() -> None:
    """라벨이 구간 끝이므로 03-01 00:00 슬롯은 2월치다."""
    index = pd.DatetimeIndex(["2024-03-01 00:00", "2024-03-01 00:15"])
    kw = pd.Series([100.0, float("nan")], index=index)
    months = {month.month: month for month in monthly_missing(kw, 15)}
    assert set(months) == {pd.Period("2024-02", freq="M"), pd.Period("2024-03", freq="M")}
    assert months[pd.Period("2024-03", freq="M")].missing_slots == 1


# --------------------------------------------------------------------- 정전 검출


def test_sample_detects_single_outage(sample_report: QualityReport) -> None:
    """정전은 1건. 11월 통신 장애(930슬롯)를 정전으로 오인하지 않는다."""
    assert len(sample_report.outages) == 1
    outage = sample_report.outages[0]
    assert (outage.start, outage.end, outage.slots) == APRIL_GAP
    assert outage.duration_hours == pytest.approx(10.5)
    assert pd.Timestamp("2023-11-03 17:30") not in [event.start for event in sample_report.outages]


def test_sample_outage_evidence(sample_report: QualityReport) -> None:
    """흔적 3종이 모두 잡히되, 연속 결측은 근거로 세지 않는다."""
    outage = sample_report.outages[0]
    assert len(outage.evidence) == 3
    assert outage.decisive_evidence == 2
    assert "연속 결측" in outage.evidence[0]
    assert outage.partial_rows == (
        pd.Timestamp("2024-04-06 19:29"),
        pd.Timestamp("2024-04-07 03:51"),
    )
    assert outage.partial_kwh == pytest.approx(43.20)
    assert outage.recovery_at == pd.Timestamp("2024-04-07 06:00")
    assert outage.recovery_kw == pytest.approx(2.88)


def test_continuous_missing_alone_is_not_an_outage(sample_usage: UsageData) -> None:
    """연속 결측은 모든 결측이 갖는 성질이라 근거가 되지 못한다.

    11월 공백은 930슬롯(9.69일)이나 되지만 다른 흔적이 없어 정전이 아니다.
    """
    gaps = find_missing_gaps(sample_usage.kw, 15)
    november = gaps[0]
    assert november.slots == 930
    outages = detect_outages(sample_usage, (november,))
    assert outages == ()


def test_min_evidence_is_configurable(sample_usage: UsageData) -> None:
    """흔적 3개를 요구하면 샘플의 정전도 걸러진다 (결정적 흔적은 2개뿐)."""
    gaps = find_missing_gaps(sample_usage.kw, 15)
    assert len(detect_outages(sample_usage, gaps, min_evidence=2)) == 1
    assert len(detect_outages(sample_usage, gaps, min_evidence=3)) == 0


# --------------------------------------------------------------------- 편중 판정


def test_peak_window_follows_label_convention() -> None:
    """평일 10~16시 = 라벨 10:15~16:00 (24슬롯). 10:00 라벨은 09시대다."""
    kw = pd.Series(float("nan"), index=label_timestamps("2024-03-06"))  # 수요일
    skew = peak_hour_skew(kw, 15)
    assert skew.peak_expected == 24
    assert skew.peak_missing == 24

    weekend = pd.Series(float("nan"), index=label_timestamps("2024-03-09"))  # 토요일
    assert peak_hour_skew(weekend, 15).peak_expected == 0


def test_sample_skew_changes_when_outage_excluded(sample_report: QualityReport) -> None:
    """정전 슬롯을 분자·분모 양쪽에서 빼면 배수가 달라진다.

    샘플에서는 어느 쪽도 임계를 넘지 않아 판정이 뒤집히지는 않는다.
    뒤집히는 경로는 아래 합성 픽스처로 검증한다.
    """
    kept = sample_report.skew
    included = sample_report.skew_including_outages
    assert included.overall_expected - kept.overall_expected == 42  # 정전 42슬롯
    assert included.overall_missing - kept.overall_missing == 42
    assert kept.excluded_slots == 42
    assert included.excluded_slots == 0
    assert kept.multiple != pytest.approx(included.multiple)
    assert not kept.flagged
    assert not included.flagged


def outage_month_usage(tmp_path: Path) -> UsageData:
    """편중 판정이 뒤집히는 합성 데이터.

    2024-03 한 달. 평일 피크(라벨 10:15~16:00) 24슬롯이 정전으로 비고,
    피크 밖 결측 10슬롯과 피크 안 결측 2슬롯이 따로 있다.

    정전 포함: 피크 26/504 vs 전체 36/2880 → 배수 4.13 → 위험
    정전 제외: 피크  2/480 vs 전체 12/2856 → 배수 0.99 → 정상
    """
    values = month_rows(march_2024_dates())

    # 정전 — 수요일 10:15~16:00 (24슬롯)
    first, last = pd.Timestamp("2024-03-06 10:15"), pd.Timestamp("2024-03-06 16:00")
    outage_labels = [
        label for label in make_labels("2024-03-06") if first <= parse_label(label) <= last
    ]
    assert len(outage_labels) == 24
    for label in outage_labels:
        del values[label]
    values["2024-03-06 16:15"] = 0.50  # 복전 후 저부하 2 kW

    # 피크 안 결측 2슬롯 (정전 아님)
    del values["2024-03-13 12:00"]
    del values["2024-03-13 12:15"]
    # 피크 밖 결측 10슬롯 (야간)
    for label in make_labels("2024-03-20")[3:13]:
        del values[label]

    rows = to_rows(values)
    rows.append(("2024-03-06 10:07", 40.0))  # 정전 직전 부분 적산 행
    return load_usage(write_csv(tmp_path / "outage_month.csv", rows))


def test_synthetic_outage_is_detected(tmp_path: Path) -> None:
    usage = outage_month_usage(tmp_path)
    report = check_quality(usage)
    assert report.missing_slots == 36
    assert len(report.outages) == 1
    outage = report.outages[0]
    assert outage.start == pd.Timestamp("2024-03-06 10:15")
    assert outage.slots == 24
    assert outage.partial_rows == (pd.Timestamp("2024-03-06 10:07"),)
    assert outage.recovery_kw == pytest.approx(2.0)


def test_synthetic_skew_verdict_flips_when_outage_excluded(tmp_path: Path) -> None:
    """정전을 빼지 않으면 없는 위험을 만들어낸다."""
    report = check_quality(outage_month_usage(tmp_path))

    included = report.skew_including_outages
    assert (included.peak_missing, included.peak_expected) == (26, 504)
    assert (included.overall_missing, included.overall_expected) == (36, 2880)
    assert included.multiple == pytest.approx(4.13, abs=0.01)
    assert included.flagged

    kept = report.skew
    assert (kept.peak_missing, kept.peak_expected) == (2, 480)
    assert (kept.overall_missing, kept.overall_expected) == (12, 2856)
    assert kept.multiple == pytest.approx(0.99, abs=0.01)
    assert not kept.flagged
    assert not any("과소평가 위험" in message for message in texts(report.notices))


def test_skew_flag_reaches_warnings(tmp_path: Path) -> None:
    """정전으로 판정되지 않는 편중은 그대로 경고로 나가야 한다.

    **다만 월별 신뢰도와 한 기준이다** (S257 결정 6 ㄱ) — 편중 구간 결측이 「정상」
    달(결측률 5% 이하)에만 들면 세우지 않는다. 사흘(2.4%)은 정상 달 · 이레(5.6%)는 신뢰 제한 달.
    """

    def skewed(name: str, dates: tuple[str, ...]) -> QualityReport:
        values = month_rows(march_2024_dates())
        for date in dates:
            for label in make_labels(date):
                stamp = parse_label(label)
                if pd.Timestamp(f"{date} 10:15") <= stamp <= pd.Timestamp(f"{date} 16:00"):
                    del values[label]
        return check_quality(load_usage(write_csv(tmp_path / name, to_rows(values))))

    normal = skewed("normal.csv", ("2024-03-06", "2024-03-13", "2024-03-20"))
    assert normal.outages == ()  # 흔적이 없으니 정전이 아니다
    assert normal.skew.multiple > 1.5  # 재료 — 배수만 보면 편중이다
    assert normal.flagged_months == ()
    assert not normal.skew.flagged
    assert not any("과소평가 위험" in message for message in texts(normal.notices))

    days = tuple(f"2024-03-{day:02d}" for day in (4, 5, 6, 7, 8, 11, 12))
    limited = skewed("limited.csv", days)
    assert limited.outages == ()
    assert [str(month.month) for month in limited.flagged_months] == ["2024-03"]
    assert limited.skew.flagged
    assert limited.skew.multiple > 1.5
    assert any("최대수요 과소평가 위험" in message for message in texts(limited.notices))


# --------------------------------------------------------------------- 이상치·일관성


def test_sample_outliers(sample_usage: UsageData) -> None:
    """부록 B — 0 kW 0건, 100 kW 미만 1건 (2024-04-07 06:00, 2.88 kW)."""
    report = check_quality(sample_usage, contract_kw=5_500)
    outliers = report.outliers
    assert outliers.zero_kw_slots == 0
    assert outliers.low_load_count == 1
    assert outliers.low_load_slots == (pd.Timestamp("2024-04-07 06:00"),)
    assert sample_usage.kw.loc[outliers.low_load_slots[0]] == pytest.approx(2.88)
    assert outliers.over_contract_slots == 0


def test_over_contract_is_counted(sample_usage: UsageData) -> None:
    report = check_quality(sample_usage, contract_kw=5_000)
    assert report.outliers.over_contract_slots > 0
    assert report.outliers.over_contract_max_kw == pytest.approx(5_293.44)
    assert any("계약전력" in message for message in texts(report.notices))


def test_sample_consistency_reports_partial_metering(sample_report: QualityReport) -> None:
    consistency = sample_report.consistency
    assert consistency.partial_metering_rows == 2
    assert consistency.partial_metering_kwh == pytest.approx(43.20)
    assert consistency.duplicate_rows == 0
    assert not consistency.uniform
    assert any("부분 계량" in message for message in texts(sample_report.notices))


def test_missing_ratio_warning_threshold(tmp_path: Path) -> None:
    """결측률 3% 초과 시 경고. 샘플(2.8%)은 경고 대상이 아니다."""
    values = month_rows(march_2024_dates())
    for label in make_labels("2024-03-20")[:96]:  # 하루 통째 = 96/2880 = 3.3%
        del values[label]
    report = check_quality(load_usage(write_csv(tmp_path / "gappy.csv", to_rows(values))))
    assert report.missing_ratio > 0.03
    assert any("결측률" in message for message in texts(report.notices))


def test_short_period_warning(tmp_path: Path) -> None:
    report = check_quality(load_usage(one_day(tmp_path / "day.csv")))
    assert not report.has_full_year
    assert any("12개월 미만" in message for message in texts(report.notices))


@pytest.mark.parametrize(("interval", "선다"), [(60, True), (15, False)])
def test_1시간_자료의_주의는_품질_검사가_한_줄_내고_15분_자료에는_없다(
    tmp_path: Path, interval: int, 선다: bool
) -> None:
    """**1시간 평균은 15분 최대수요보다 낮다** (S268 결정 2).

    품질 검사가 주의 등급 한 줄을 내야 화면 데이터 품질 절 · PPT 3장 · Excel 요약이 그 줄을
    받는다 — 앞서는 업로드 경고 칸(``UsageMeta.warnings``)에만 들어 아무 데도 안 떴다.
    글자는 업로드 경고와 한 글자다.
    """
    usage = load_usage(one_day(tmp_path / "day.csv", interval=interval))
    stood = [
        item for item in check_quality(usage).notices if item.fact == "quality.hourly_interval"
    ]
    assert len(stood) == (1 if 선다 else 0)
    if 선다:
        assert str(stood[0].severity) == "주의"
        assert stood[0].text in usage.meta.warnings
        assert "실제보다 낮을 수 있습니다" in stood[0].text


def test_12개월_미만_경고가_네_자리에서_한_꼴이다(tmp_path: Path, tariff: TariffTable) -> None:
    """**같은 사실은 한 꼴로 선다** (S214 1-4 · 울타리 ㄴ1~ㄴ3).

    `quality\\checks.py` 와 `tariff\\engine.py` 가 **같은 사실 ID**
    (``quality.short_period``)를 쓰는데 글자가 갈려 있었다 — 122일 벌의 Excel
    「요약」 시트에서 「… 연간 환산 결과에 경고를 붙여야 합니다」 와 「… 12개월 환산
    시 경고를 붙이십시오」 가 **나란히** 섰다. `notices.dedupe` 는 사실 ID 로 거르므로
    두 묶음이 따로 그려지는 이 자리는 **글자가 같아야만** 한 꼴이 된다.

    **둘째 문장은 코드에게 하는 말이었다** — 「경고를 붙여야 합니다」·「붙이십시오」 는
    만드는 쪽에 하는 지시라 고객이 읽는 자리에서 참이 아니다 (S207 기준).

    **「환산」 자리는 S268 에 걷혔다** (결정 3) — `tariff\\engine.py` 의 12개월 환산 길은
    `src\\` 에서 부른 적이 없어 길째 걷었다. 남은 자리는 셋이다.

    `test_꼭_365일치_자료는_12개월_미만으로_판정되지_않는다` 와 **같은 세 자리**를
    본다 — 그쪽은 「안 뜬다」 를, 이쪽은 「뜨면 한 꼴이다」 를 문다.

    **S271 결정 1(사람 결정)이 문장을 구체적으로 바꿨다** — 분석 기간(날짜) · 계절별 일수 ·
    「12개월로 늘린 값」 · 빠진 계절 · 방향(요금표 단가만으로 갈릴 때만). 그 문장을 만드는
    자리는 요금 엔진 하나(`short_period_warning`)이고, 요금표를 모르는 로더와 품질 검사는
    **머리 문장 한 상수**만 낸다. 진단이 품질 쪽 글자를 완성 문장으로 갈아 끼우므로
    그려지는 자리에서는 여전히 **한 꼴**이다 — 그것을 진단 결과로 문다.
    """
    rows = [(label, 100.0) for date in march_2024_dates() for label in make_labels(date)]
    usage = load_usage(write_csv(tmp_path / "short.csv", rows))
    report = check_quality(usage)
    selection = TariffSelection("general_b", "high_a", "I")
    bill = calculate_bill(usage, tariff, selection)
    said = {
        "품질": texts(report.notices),
        "업로드": list(usage.meta.warnings),
        "요금": texts(bill.notices),
    }
    assert not report.has_full_year  # 전제 — 12개월 미만 자료다

    선말 = {
        where: sorted({m for m in messages if "12개월 미만" in m})
        for where, messages in said.items()
    }
    빈자리 = [where for where, ms in 선말.items() if not ms]
    assert 빈자리 == [], f"12개월 미만인데 경고가 없는 자리: {빈자리}"

    # 요금표를 모르는 두 자리는 머리 문장 한 상수다.
    assert 선말["품질"] == 선말["업로드"] == [SHORT_PERIOD_WARNING]
    # 요금 엔진이 완성 문장을 만든다 — 머리 · 날짜 · 계절 일수 · 늘린 값 · 빠진 계절.
    (완성,) = 선말["요금"]
    span = f"{usage.meta.start:%Y-%m-%d} ~ {usage.meta.end:%Y-%m-%d}"
    assert 완성.startswith(f"{SHORT_PERIOD_WARNING} 12개월 환산값은 {span} ")
    days = f"봄·가을 {usage.meta.period_days:.0f}일"  # 3월 한 달이 다 봄·가을이다
    assert f"(여름 0일, {days}, 겨울 0일)의 값을 12개월로 늘린 값이라" in 완성
    assert "여름, 겨울이 반영되지 않았고, " in 완성
    # 방향은 요금표 단가만으로 가른다 — 3월은 봄·가을이고 이 요금제는 세 시간대 다 봄·가을
    # 단가가 12개월 일수 가중 평균보다 낮다(전제를 요금표에서 읽어 확인한다).
    rates = tariff.rates(selection)
    year_days = {"summer": 92, "spring_fall": 153, "winter": 120}
    for band in ("light", "mid", "peak"):
        year = sum(days * rates.rate(key, band) for key, days in year_days.items()) / 365
        assert rates.rate("spring_fall", band) < year, band
    assert 완성.endswith("실제보다 작을 수 있습니다.")

    # 그려지는 자리에서는 한 꼴이다 — 진단이 품질 쪽 글자를 청구 쪽 글자로 간다.
    billed = diagnose(usage, tariff, ContractInfo(selection))
    assert {item.text for item in billed.notices if item.fact == "quality.short_period"} == {완성}
    assert [
        item.text for item in billed.quality.notices if item.fact == "quality.short_period"
    ] == [완성]
    # 계약 정보가 없으면 방향을 적지 않는다(단가를 모른다) — 기간과 계절 일수는 선다.
    bare = diagnose(usage, tariff)
    (계약없음,) = [item.text for item in bare.notices if item.fact == "quality.short_period"]
    assert span in 계약없음 and days in 계약없음
    assert 계약없음.endswith("실제 12개월 값과 다를 수 있습니다.")

    # **어느 자리도 코드에게 말하지 않고 「연간」 이라 부르지 않는다.**
    샌말 = [
        f"{where} 「{m}」"
        for where, ms in 선말.items()
        for m in ms
        if "경고를 붙" in m or "연간" in m
    ]
    assert 샌말 == [], 샌말


class _Rates:
    """계절 · 시간대 단가만 든 대역 — 방향 판정의 재료다."""

    def __init__(self, table: dict[str, dict[str, float]]) -> None:
        self.table = table

    def rate(self, season: str, band: str) -> float:
        return self.table[season][band]


@pytest.mark.parametrize(
    ("days", "table", "expected"),
    [
        # 여름만 든 기간 · 여름 단가가 세 시간대 다 높다 → 크다.
        ({"summer": 30.0}, {"summer": (3, 3, 3), "spring_fall": (1, 1, 1), "winter": (2, 2, 2)}, 1),
        # 봄·가을만 든 기간 · 그 단가가 세 시간대 다 낮다 → 작다.
        (
            {"spring_fall": 30.0},
            {"summer": (3, 3, 3), "spring_fall": (1, 1, 1), "winter": (2, 2, 2)},
            -1,
        ),
        # 시간대마다 쪽이 갈린다 → 방향을 적지 않는다.
        ({"summer": 30.0}, {"summer": (3, 1, 3), "spring_fall": (1, 3, 1), "winter": (2, 2, 2)}, 0),
        # 계절 구성이 12개월과 같다 → 같아서 방향이 없다.
        (
            {"summer": 92.0, "spring_fall": 153.0, "winter": 120.0},
            {"summer": (3, 3, 3), "spring_fall": (1, 1, 1), "winter": (2, 2, 2)},
            0,
        ),
    ],
)
def test_12개월_미만_방향은_요금표_단가만으로_갈릴_때만_선다(
    days: dict[str, float], table: dict[str, tuple[float, float, float]], expected: int
) -> None:
    """**방향은 계절 일수로 가중한 평균 단가로만 가른다** (S271 결정 1 · 사람 결정).

    세 시간대가 다 같은 쪽일 때만 「크다」 · 「작다」 이고 섞이거나 같으면 0(「다를 수
    있습니다」)이다. 사용량은 안 쓴다.
    """
    bands = ("light", "mid", "peak")
    rates: Any = _Rates({key: dict(zip(bands, row, strict=True)) for key, row in table.items()})
    period = {key: days.get(key, 0.0) for key in table}
    year = {"summer": 92.0, "spring_fall": 153.0, "winter": 120.0}
    assert _short_period_direction(period, year, rates) == expected


def test_꼭_365일치_자료는_12개월_미만으로_판정되지_않는다(
    tmp_path: Path, tariff: TariffTable
) -> None:
    """**365일을 빠짐없이 올리면 「12개월 미만」 이 아니다** (S155 3절 · S182 5절 xfail · S184 1절).

    라벨이 구간 끝이라 첫 라벨이 ``00:15`` 인데 기간을 첫 라벨과 끝 라벨 사이로 재
    364.989… 일이 됐다. S184 가 길 ㄱ(기간에 한 슬롯을 더해 잰다 · `io\\usage.py` 의
    ``period_days``)으로 고쳤고 문턱(365)은 그대로다.

    **품질 문턱만이 아니라 같은 판정을 내는 자리를 다 문다** (S183 2-2). 앞서는
    품질 하나만 물어 그 문턱만 갈아도 XPASS 였다 — 업로드 경고 · 요금 안내(Excel
    「요금」 · PPT 기간 각주 · Word 부록이 읽는다)가 그대로 남는다. 넷째 자리였던
    12개월 환산 안내는 S268 에 길째 걷혔다(결정 3).
    """
    dates = pd.date_range("2025-01-01", "2025-12-31").strftime("%Y-%m-%d")
    rows = [(label, 100.0) for date in dates for label in make_labels(date)]
    usage = load_usage(write_csv(tmp_path / "year.csv", rows))
    report = check_quality(usage)
    assert len(dates) == 365 and report.missing_slots == 0  # 전제 — 꼭 365일치다
    bill = calculate_bill(usage, tariff, TariffSelection("general_b", "high_a", "I"))
    said = {
        "품질": texts(report.notices),
        "업로드": usage.meta.warnings,
        "요금": texts(bill.notices),
    }
    short = {where for where, messages in said.items() if any("12개월 미만" in m for m in messages)}
    assert report.has_full_year and short == set(), short


def test_clean_data_has_no_warnings(tmp_path: Path) -> None:
    """문제가 없으면 조용하다. 기간 경고만 남는다."""
    rows = [(label, 100.0) for date in march_2024_dates() for label in make_labels(date)]
    report = check_quality(load_usage(write_csv(tmp_path / "clean.csv", rows)))
    assert report.missing_slots == 0
    assert report.gaps == ()
    assert report.longest_gap is None
    assert report.outages == ()
    assert [message for message in texts(report.notices) if "12개월 미만" not in message] == []


# --------------------------------------------------------------------- 버린 행 (31세션 0-2)


def _bad_rows(path: Path) -> Path:
    """검침일·전력량을 읽지 못하는 행과 음수 행을 섞은 한 달치.

    **한전 사이버지점 내려받기에서는 나오지 않는 행이다.** 실물을 본 적이 없어
    합성으로 만든다 — 그래서 문구도 건수만 적고 원인은 짐작하지 않는다.
    """
    rows = [(label, "100.00") for date in march_2024_dates() for label in make_labels(date)]
    rows.append(("읽을 수 없는 날짜", "100.00"))
    rows.append(("읽을 수 없는 날짜 2", "100.00"))
    rows.append(("2024-03-15 06:15", "값없음"))
    rows.append(("2024-03-15 06:30", "-50.00"))
    return write_raw_csv(path, rows)


def test_버린_행을_세어_둔다(tmp_path: Path) -> None:
    usage = load_usage(_bad_rows(tmp_path / "bad.csv"))
    assert usage.meta.invalid_datetime_rows == 2
    assert usage.meta.invalid_energy_rows == 1
    assert usage.meta.negative_energy_rows == 1


def test_버린_행이_결측률에_잡히지_않는다(tmp_path: Path) -> None:
    """**이것이 이 안내를 만든 이유다** (31세션 0-2).

    값이 조용히 빠지는데 결측률에도 안 잡히고 총 사용량만 줄어든다. 검침일을 읽지
    못한 행은 시각 자체가 없어 어느 슬롯이 비었는지도 알 수 없고, 전력량 쪽은 같은
    시각에 성한 행이 있으면 그 행이 슬롯을 채워 결측이 되지 않는다.
    """
    usage = load_usage(_bad_rows(tmp_path / "bad.csv"))
    assert usage.meta.missing_rows == 0
    assert usage.meta.missing_ratio == 0.0


def test_버린_행이_주의로_나온다(tmp_path: Path) -> None:
    """**근거가 아니라 주의다.** 결과 해석을 바꾸므로 화면에 남아야 한다."""
    report = check_quality(load_usage(_bad_rows(tmp_path / "bad.csv")))
    dropped = [item for item in report.notices if item.fact == "quality.dropped_rows"]
    assert len(dropped) == 1, "한 줄로 묶어 낸다 — 셋을 따로 내면 같은 말을 세 번 한다."
    notice = dropped[0]
    assert str(notice.severity) == "주의"
    assert "검침일을 읽지 못한 행 2건" in notice.text
    assert "전력량을 읽지 못한 행 1건" in notice.text
    assert "음수 전력량 행 1건" in notice.text
    # **원인을 짐작해 적지 않는다** — 실물을 본 적이 없다.
    for guess in ("계기", "통신", "정전", "오류로"):
        assert guess not in notice.text, notice.text


def test_버린_행이_없으면_안내도_없다(tmp_path: Path) -> None:
    """0 건을 늘 띄우면 「0건」 이 화면 한 줄을 영영 차지한다."""
    rows = [(label, 100.0) for date in march_2024_dates() for label in make_labels(date)]
    report = check_quality(load_usage(write_csv(tmp_path / "clean.csv", rows)))
    assert report.consistency.dropped_rows == ()
    assert not [item for item in report.notices if item.fact == "quality.dropped_rows"]


# --------------------------------------------------------------------- 결측 처리 (4.2)


def test_fill_missing_defaults_to_no_interpolation(sample_usage: UsageData) -> None:
    result = fill_missing(sample_usage.kw)
    assert result.method == "none"
    assert result.filled_slots == 0
    assert not result.interpolated
    assert result.remaining_missing == 972
    assert result.kw.isna().sum() == 972


def test_linear_fill_is_opt_in(sample_usage: UsageData) -> None:
    result = fill_missing(sample_usage.kw, method="linear")
    assert result.filled_slots == 972
    assert result.remaining_missing == 0
    assert result.kw.isna().sum() == 0
    # 원본은 건드리지 않는다
    assert sample_usage.kw.isna().sum() == 972


def test_linear_fill_respects_limit(sample_usage: UsageData) -> None:
    """며칠짜리 공백까지 메우지 않으려면 limit 을 준다."""
    result = fill_missing(sample_usage.kw, method="linear", limit=4)
    assert result.filled_slots == 8  # 공백 2개 × 앞쪽 4슬롯 (limit 은 정방향으로 센다)
    assert result.remaining_missing == 964


def test_unknown_fill_method_raises(sample_usage: UsageData) -> None:
    with pytest.raises(ValueError, match="지원하지 않는"):
        fill_missing(sample_usage.kw, method="spline")  # type: ignore[arg-type]


# --------------------------------------------------------------------- 부하 패턴 (6.1)


def test_sample_load_pattern(sample_usage: UsageData) -> None:
    pattern = load_pattern(sample_usage.kw, sample_usage.meta.interval_minutes)
    assert pattern.observed_slots == 34_356
    assert pattern.max_kw == pytest.approx(5_293.44)
    assert pattern.mean_kw == pytest.approx(2_594.6, abs=0.1)
    assert pattern.load_factor == pytest.approx(0.490, abs=0.001)
    # 사무 건물 성격 — 야간·주말 부하가 주간·평일보다 낮다
    assert pattern.base_load_ratio is not None
    assert pattern.weekend_ratio is not None
    assert pattern.base_load_ratio < 1.0
    assert pattern.weekend_ratio < 1.0


def test_load_pattern_ratios_are_computed_from_slot_start(tmp_path: Path) -> None:
    """야간(22~08)만 부하를 절반으로 낮춘 합성 데이터로 기저부하 비율을 확인한다."""
    values = month_rows(march_2024_dates(), kwh=100.0)
    for label in list(values):
        start_hour = (parse_label(label) - pd.Timedelta(minutes=15)).hour
        if start_hour >= 22 or start_hour < 8:
            values[label] = 50.0
    usage = load_usage(write_csv(tmp_path / "night.csv", to_rows(values)))

    pattern = load_pattern(usage.kw, 15)
    assert pattern.night_mean_kw == pytest.approx(200.0)
    assert pattern.day_mean_kw == pytest.approx(400.0)
    assert pattern.base_load_ratio == pytest.approx(0.5)
    assert pattern.weekend_ratio == pytest.approx(1.0)


def test_load_pattern_needs_observations() -> None:
    empty = pd.Series(float("nan"), index=label_timestamps("2024-03-06"))
    with pytest.raises(ValueError, match="관측된 수요가 없어"):
        load_pattern(empty, 15)
