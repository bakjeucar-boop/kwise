"""테스트용 합성 사용량 파일 생성기.

실측 샘플로는 확인할 수 없는 경로(편중 판정이 뒤집히는 경우 등)를 만들기 위한 것이다.
라벨은 실제 파일과 같은 규약을 따른다 — 구간 끝, 자정은 ``24:00``.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

HEADER = ("검침일", "순방향 유효전력량(KWH)")


def make_labels(date: str, interval: int = 15) -> list[str]:
    """하루치 검침 라벨. ``00:15`` 로 시작해 ``24:00`` 으로 끝난다."""
    start = pd.Timestamp(date)
    labels: list[str] = []
    for step in range(1, 24 * 60 // interval + 1):
        stamp = start + pd.Timedelta(minutes=interval * step)
        if stamp.date() != start.date():
            labels.append(f"{start.date()} 24:00")  # 자정은 24:00 으로 적힌다
        else:
            labels.append(stamp.strftime("%Y-%m-%d %H:%M"))
    return labels


def write_csv(
    path: Path,
    rows: list[tuple[str, float]],
    *,
    encoding: str = "utf-8-sig",
    header: tuple[str, str] = HEADER,
) -> Path:
    text = ",".join(header) + "\n" + "".join(f"{label},{kwh:.2f}\n" for label, kwh in rows)
    path.write_text(text, encoding=encoding)
    return path


def one_day(
    path: Path,
    *,
    date: str = "2024-01-01",
    interval: int = 15,
    kwh: float = 100.0,
    encoding: str = "utf-8-sig",
    header: tuple[str, str] = HEADER,
) -> Path:
    rows = [(label, kwh) for label in make_labels(date, interval)]
    return write_csv(path, rows, encoding=encoding, header=header)


def month_rows(dates: list[str], *, kwh: float = 100.0) -> dict[str, float]:
    """날짜 목록의 모든 라벨을 기본값으로 채운 사전. 이후 개별 라벨을 지우거나 고친다."""
    return {label: kwh for date in dates for label in make_labels(date)}


def march_2024_dates() -> list[str]:
    """2024-03-01 ~ 03-30 (30일). 평일 21일 × 피크 24슬롯 = 504슬롯."""
    return [str(day.date()) for day in pd.date_range("2024-03-01", periods=30, freq="D")]


def parse_label(label: str) -> pd.Timestamp:
    """``24:00`` 규약을 지키며 라벨 하나를 파싱한다."""
    if label.endswith(" 24:00"):
        return pd.Timestamp(label[: -len(" 24:00")]) + pd.Timedelta(days=1)
    return pd.Timestamp(label)


def label_timestamps(date: str, interval: int = 15) -> pd.DatetimeIndex:
    """하루치 라벨을 파싱된 시각으로. ``00:15`` ~ 다음 날 ``00:00``."""
    start = pd.Timestamp(date) + pd.Timedelta(minutes=interval)
    return pd.date_range(start, periods=24 * 60 // interval, freq=f"{interval}min")


def to_rows(values: dict[str, float]) -> list[tuple[str, float]]:
    """라벨 사전을 시각 순으로 정렬한 행 목록으로 바꾼다."""
    return sorted(values.items(), key=lambda item: parse_label(item[0]))


def month_dates(year: int, month: int) -> list[str]:
    """해당 달의 모든 날짜. 한 달을 통째로 만들면 부분 월이 되지 않는다."""
    start = pd.Timestamp(year=year, month=month, day=1)
    return [str(day.date()) for day in pd.date_range(start, periods=start.days_in_month, freq="D")]


def write_month(path: Path, year: int, month: int, *, kwh: float = 100.0) -> Path:
    """한 달치 균일 부하 파일. 15분 100 kWh = 400 kW."""
    rows = [(label, kwh) for date in month_dates(year, month) for label in make_labels(date)]
    return write_csv(path, rows)
