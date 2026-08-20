"""테스트용 합성 사용량 파일 생성기.

실측 샘플로는 확인할 수 없는 경로(편중 판정이 뒤집히는 경우 등)를 만들기 위한 것이다.
라벨은 실제 파일과 같은 규약을 따른다 — 구간 끝, 자정은 ``24:00``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from kwise.pv import WeatherData

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


def write_raw_csv(
    path: Path,
    rows: list[tuple[str, str]],
    *,
    encoding: str = "utf-8-sig",
    header: tuple[str, str] = HEADER,
) -> Path:
    """두 칸을 **적힌 그대로** 쓴다 (31세션 0-2).

    :func:`write_csv` 는 전력량을 ``float`` 로 받아 서식을 씌우므로 「읽지 못하는
    값」 을 만들 수 없다. 읽기 실패·음수 행처럼 **원본이 망가진 경우**를 재현하려면
    문자열을 그대로 넣을 길이 있어야 한다.
    """
    text = ",".join(header) + "\n" + "".join(f"{label},{value}\n" for label, value in rows)
    path.write_text(text, encoding=encoding)
    return path


KEPCO_HEADER = ("계기번호", "고객번호", "검침일", "순방향 유효전력량(KWH)")


def write_kepco_file(
    path: Path,
    rows: list[tuple[str, float]],
    *,
    header: tuple[str, ...] = KEPCO_HEADER,
    title: str | None = None,
    meter: str = "55-282007100",
    customer: int = 196705100,
) -> Path:
    """한전ON 원본 4열 양식. 계기번호·고객번호가 앞에 붙는다.

    ``.xlsx`` 와 ``.csv`` 를 확장자로 갈라 쓴다. ``title`` 을 주면 헤더 앞에
    제목 행이 한 줄 붙는다 — 화면 저장본에서 실제로 나오는 모양이다.

    고객번호는 **전 행이 같은 상수**라 값 열 판정의 고유값 조건에 걸려야 한다.
    계기번호는 문자열이라 애초에 수치 변환이 실패한다.
    """
    body = [[meter, customer, label, kwh] for label, kwh in rows]
    frame = pd.DataFrame(body, columns=list(header))
    if path.suffix.lower() == ".csv":
        text = ""
        if title is not None:
            text += title + "\n"
        text += ",".join(str(name) for name in header) + "\n"
        text += "".join(f"{meter},{customer},{label},{kwh:.2f}\n" for label, kwh in rows)
        path.write_text(text, encoding="utf-8-sig")
        return path

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        start = 0 if title is None else 1
        frame.to_excel(writer, sheet_name="Sheet1", index=False, startrow=start)
        if title is not None:
            writer.sheets["Sheet1"].cell(row=1, column=1, value=title)
    return path


def kepco_month_rows(year: int, month: int, *, kwh: float = 100.0) -> list[tuple[str, float]]:
    """한 달치 (라벨, kWh). 실측처럼 값이 조금씩 다르게 흔든다.

    전부 같은 값이면 고유값 조건(상수 열 배제)에 스스로 걸린다. 실측 샘플의
    고유값 비율은 4.1% 인데, 여기서는 값이 거의 다 달라 그보다 후하다.
    """
    labels = [label for date in month_dates(year, month) for label in make_labels(date)]
    return [
        (label, round(kwh + (index % 97) * 0.31 + index * 0.01, 2))
        for index, label in enumerate(labels)
    ]


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


def night_peak_month(
    path: Path,
    year: int = 2024,
    month: int = 3,
    *,
    night_kwh: float = 500.0,
    midday_kwh: float = 300.0,
    other_kwh: float = 50.0,
) -> Path:
    """야간 피크형 한 달치. 경부하 제외 마스크의 효과를 뒤집어 보이기 위한 것이다.

    가장 큰 부하는 라벨 22:15~08:00 (경부하 구간, 구간 시작 22:00~07:45) 에 두고,
    그 다음이 라벨 10:15~15:00 (정오) 다. 전 슬롯을 모집단으로 삼으면 상위 구간이
    전부 야간이라 태양광 등급이 '낮음' 이지만, 요금적용전력 대상 슬롯만 남기면
    정오가 상위를 채워 '높음' 이 된다.
    """

    def bucket(label: str) -> float:
        stamp = parse_label(label)
        start = stamp - pd.Timedelta(minutes=15)  # 귀속은 구간 시작 기준이다
        if start.hour >= 22 or start.hour < 8:
            return night_kwh
        if 10 <= start.hour < 15:
            return midday_kwh
        return other_kwh

    rows = [
        (label, bucket(label)) for date in month_dates(year, month) for label in make_labels(date)
    ]
    return write_csv(path, rows)


def clearsky_weather(
    *,
    latitude: float = 37.5,
    longitude: float = 127.0,
    timezone: str = "Asia/Seoul",
    start: str = "2023-07-02",
    end: str = "2023-07-04",
    altitude_m: float = 50.0,
    temp_air: float = 25.0,
    wind_speed: float = 2.0,
) -> WeatherData:
    """맑은 날 합성 기상. 네트워크를 타지 않는다.

    Open-Meteo 규약(라벨 = 구간 시작)에 맞춰, 라벨 + 30분의 청천 일사를 그 시간의
    값으로 넣는다. 시각 정렬 시험의 기준이 되므로 규약을 어기면 안 된다.
    """
    from pvlib.location import Location

    from kwise.pv import WeatherData, WeatherRequest

    labels = pd.date_range(f"{start} 00:00", f"{end} 23:00", freq="1h", tz=timezone)
    location = Location(latitude, longitude, tz=timezone, altitude=altitude_m)
    clearsky = location.get_clearsky(labels + pd.Timedelta(minutes=30))
    hourly = pd.DataFrame(
        {
            "ghi": clearsky["ghi"].to_numpy(),
            "dni": clearsky["dni"].to_numpy(),
            "dhi": clearsky["dhi"].to_numpy(),
            "temp_air": temp_air,
            "wind_speed": wind_speed,
            "snowfall": 0.0,
        },
        index=labels,
    )
    request = WeatherRequest(
        latitude=latitude,
        longitude=longitude,
        start=pd.Timestamp(start).date(),
        end=pd.Timestamp(end).date(),
        timezone=timezone,
    )
    return WeatherData(hourly=hourly, request=request)
