"""품질 검사 단위테스트 (요구사항서 4장, 6.1, 부록 B)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from kwise.io import UsageData, load_usage
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
from tests._synthetic import (
    label_timestamps,
    make_labels,
    march_2024_dates,
    month_rows,
    one_day,
    parse_label,
    to_rows,
    write_csv,
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
    """정전으로 판정되지 않는 편중은 그대로 경고로 나가야 한다."""
    values = month_rows(march_2024_dates())
    for date in ("2024-03-06", "2024-03-13", "2024-03-20"):
        for label in make_labels(date):
            stamp = parse_label(label)
            if pd.Timestamp(f"{date} 10:15") <= stamp <= pd.Timestamp(f"{date} 16:00"):
                del values[label]
    report = check_quality(load_usage(write_csv(tmp_path / "skewed.csv", to_rows(values))))

    assert report.outages == ()  # 흔적이 없으니 정전이 아니다
    assert report.skew.flagged
    assert report.skew.multiple > 1.5
    assert any("최대수요 과소평가 위험" in message for message in texts(report.notices))


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


def test_clean_data_has_no_warnings(tmp_path: Path) -> None:
    """문제가 없으면 조용하다. 기간 경고만 남는다."""
    rows = [(label, 100.0) for date in march_2024_dates() for label in make_labels(date)]
    report = check_quality(load_usage(write_csv(tmp_path / "clean.csv", rows)))
    assert report.missing_slots == 0
    assert report.gaps == ()
    assert report.longest_gap is None
    assert report.outages == ()
    assert [message for message in texts(report.notices) if "12개월 미만" not in message] == []


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
    assert pattern.unattended_ratio is not None


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
