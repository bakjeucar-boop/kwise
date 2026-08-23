r"""소형 사무빌딩 시험 자료 생성 (45세션).

    .venv\Scripts\python.exe tools\make_office_case.py

**샘플 하나로는 상계거래 경로를 볼 수 없다.** 지금 자료는 대형 건물(22,285 MWh,
최대 5,293 kW)이고 주말 부하가 평일의 64%라, 태양광을 아무리 키워도 낮 부하가
발전을 다 먹어 잉여가 나지 않는다. 잉여가 없으면 상계거래 화면이 열리지 않는다.

**세 가지를 바꾼다.**

    ① 규모      15분 값에 0.05 를 곱한다 — 최대 약 265 kW
    ② 주말      토·일·공휴일을 평일의 25~30% 로 내린다 (경비·서버만)
    ③ 품질      결측과 정전 흔적을 채운다 — 품질 경고가 화면을 채우지 않게

②가 핵심이다. 주말 낮 부하가 50 kW 안팎이 되므로 160 kWp 태양광(맑은 날 약
120 kW)이 남는다 — **잉여 70 kW 가 상계거래 경로를 연다.**

**단차를 만들지 않는다.** 금요일 밤과 토요일 새벽 사이에 부하가 뚝 떨어지면
사람이 만든 자료라는 것이 그림에서 바로 보인다. 평일/휴일 계수를 만든 뒤
**여섯 시간 창으로 부드럽게 이어** 실제 건물의 감속·기동을 흉내 낸다.

라벨 규약은 원본과 같다 — **구간 끝**, 자정은 ``24:00``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from kwise.io import load_usage, slot_start  # noqa: E402
from kwise.tariff.holiday import build_calendar  # noqa: E402

DEFAULT_SOURCE = PROJECT_ROOT / "input" / "사용량조회_20240429.csv"
DEFAULT_TARGET = PROJECT_ROOT / "input" / "사용량조회_소형사무빌딩.csv"
HEADER = ("검침일", "순방향 유효전력량(KWH)")

#: 규모 축소 비율. 5,293 kW → 265 kW.
SCALE = 0.05
#: 목표 주말 부하 비율 (평일 평균 대비). 소형 사무빌딩의 통상 범위 한가운데다.
TARGET_WEEKEND_RATIO = 0.27
#: 휴일 계수를 부드럽게 잇는 창. 15분 슬롯 24칸 = 6시간.
SMOOTH_SLOTS = 24


def read_source(path: Path) -> pd.DataFrame:
    """원본 CSV 를 라벨·값 두 열로 읽는다. ``24:00`` 표기를 보존한다.

    :func:`tools.make_cases.read_source` 와 같은 규약이다 — 원본 양식이 하나이므로
    읽는 방법도 하나여야 한다.
    """
    raw = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
    label_column = raw.columns[-2] if len(raw.columns) > 2 else raw.columns[0]
    value_column = raw.columns[-1]
    text = raw[label_column].astype(str)
    values = pd.to_numeric(raw[value_column].str.replace(",", "", regex=False), errors="coerce")
    stamps = pd.to_datetime(text.str.replace(" 24:00", " 00:00", regex=False))
    stamps = stamps + pd.to_timedelta(text.str.contains(" 24:00").astype(int), unit="D")
    return pd.DataFrame({"text": text, "label": stamps, "kwh": values})


def complete_index(labels: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """빠진 행까지 포함한 온전한 15분 라벨.

    **원본의 결측은 빈 칸이 아니라 빠진 행이다.** 972구간이 통째로 없다
    (2023-11-03 17:30 부터가 가장 긴 자리다). 값을 채우려면 자리를 먼저 만들어야
    한다.
    """
    return pd.date_range(labels.min(), labels.max(), freq="15min")


def fill_gaps(kwh: pd.Series, starts: pd.DatetimeIndex) -> pd.Series:
    """결측을 채운다 — **같은 요일 같은 시각의 중앙값**을 먼저 쓴다.

    앞뒤 평균만 쓰면 972구간(최장 9.7일)짜리 공백에서 직선이 그어져 그 구간만
    부자연스럽게 평평해진다. 요일·시각 프로파일을 먼저 얹고, 그래도 남는 자리만
    앞뒤로 잇는다.

    **이 자료에서만 채운다.** 도구의 기본은 미보간이다 (CLAUDE.md 금지 항목) —
    여기서 채우는 까닭은 상계거래 화면을 보는 것이 목적이고, 품질 경고가 화면을
    채우면 볼 것이 가려지기 때문이다. 그 사실은 안내 문서에 적는다.
    """
    key = pd.MultiIndex.from_arrays(
        [starts.dayofweek, starts.hour * 60 + starts.minute], names=("dow", "minute")
    )
    profile = kwh.groupby(key).median()
    filled = kwh.copy()
    missing = filled.isna()
    filled[missing] = profile.reindex(key[missing]).to_numpy()
    return filled.interpolate(limit_direction="both")


def label_text(label: pd.Timestamp) -> str:
    """구간 끝 라벨. **자정은 앞날의 ``24:00``** 이다 — 원본 규약이다."""
    if label.hour == 0 and label.minute == 0:
        return f"{(label - pd.Timedelta(days=1)):%Y-%m-%d} 24:00"
    return f"{label:%Y-%m-%d %H:%M}"


def off_day_factor(starts: pd.DatetimeIndex, ratio: float) -> np.ndarray:
    """휴일 계수 — 휴일은 ``ratio``, 평일은 1. **이어 붙일 때 부드럽게.**

    계단으로 두면 금요일 24:00 과 토요일 00:15 사이에 부하가 수직으로 떨어진다.
    6시간 창의 이동평균으로 눌러 실제 건물의 감속·기동처럼 만든다.
    """
    calendar = build_calendar(sorted({int(year) for year in starts.year}))
    days = pd.DatetimeIndex(starts.normalize())
    off = days.isin(calendar.holiday_index()) | (starts.dayofweek >= 5)
    raw = pd.Series(np.where(off, ratio, 1.0), index=starts)
    return raw.rolling(SMOOTH_SLOTS, center=True, min_periods=1).mean().to_numpy()


def _weekend_ratio(kwh: pd.Series, starts: pd.DatetimeIndex) -> float:
    weekend = starts.dayofweek >= 5
    return float(kwh[weekend].mean() / kwh[~weekend].mean())


def shrink_off_days(kwh: pd.Series, starts: pd.DatetimeIndex, target: float) -> pd.Series:
    """주말 평균이 평일 평균의 ``target`` 이 되도록 휴일 계수를 맞춘다.

    **한 번에 못 맞춘다.** 이어 붙이는 창이 평일 가장자리까지 함께 눌러 실제
    비율이 계수보다 높게 나온다. 이분법으로 몇 번 훑어 맞춘다 — 자료가 하나뿐이라
    식을 세우는 것보다 빠르고, 결과를 눈으로 확인할 수 있다.
    """
    low, high = 0.05, 1.0
    result = kwh
    for _ in range(40):
        middle = (low + high) / 2
        result = kwh * off_day_factor(starts, middle)
        if _weekend_ratio(result, starts) > target:
            high = middle
        else:
            low = middle
    return result


def build(source: pd.DataFrame, *, scale: float, target_ratio: float) -> pd.DataFrame:
    """자리 채우기 → 값 채우기 → 축소 → 휴일 낮추기. **순서가 있다.**

    빠진 행을 먼저 되살려야 값을 채울 자리가 생기고, 결측을 채워야 휴일 계수가
    빈 자리를 건너뛰지 않는다. 축소는 선형이라 어디서 하든 같다. 휴일 낮추기는
    마지막이다 — 그 결과가 목표 비율이다.
    """
    labels = complete_index(pd.DatetimeIndex(source["label"]))
    kwh = pd.Series(source["kwh"].to_numpy(), index=pd.DatetimeIndex(source["label"]))
    kwh = kwh.reindex(labels).astype("float64")
    starts = slot_start(labels, 15)
    filled = fill_gaps(kwh, starts)
    shrunk = shrink_off_days(filled * scale, starts, target_ratio)
    return pd.DataFrame({"text": [label_text(label) for label in labels], "kwh": shrunk.to_numpy()})


def write_case(frame: pd.DataFrame, path: Path) -> Path:
    """원본과 같은 2열 양식으로 쓴다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [",".join(HEADER)]
    for text, value in zip(frame["text"], frame["kwh"], strict=True):
        lines.append(f"{text}," if pd.isna(value) else f"{text},{value:.2f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="소형 사무빌딩 시험 자료 생성 (45세션)")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--target", default=str(DEFAULT_TARGET))
    parser.add_argument("--scale", type=float, default=SCALE)
    parser.add_argument("--weekend-ratio", type=float, default=TARGET_WEEKEND_RATIO)
    args = parser.parse_args(argv)

    source_path = Path(args.source)
    source = read_source(source_path)
    blanks = int(source["kwh"].isna().sum())
    print(f"원본 {source_path.name} — {len(source):,}행, 결측 {blanks:,}")

    frame = build(source, scale=args.scale, target_ratio=args.weekend_ratio)
    path = write_case(frame, Path(args.target))

    usage = load_usage(path)
    starts = slot_start(pd.DatetimeIndex(usage.kw.index), usage.meta.interval_minutes)
    weekend = starts.dayofweek >= 5
    print(
        f"{path.name} — 최대 {usage.meta.max_demand_kw:,.1f} kW · "
        f"{usage.meta.total_kwh / 1000:,.1f} MWh · 결측 {usage.meta.missing_rows:,}\n"
        f"    주말 평균 {usage.kw[weekend].mean():,.1f} kW / "
        f"평일 평균 {usage.kw[~weekend].mean():,.1f} kW = "
        f"{usage.kw[weekend].mean() / usage.kw[~weekend].mean():.1%} · "
        f"부하율 {usage.kw.mean() / usage.kw.max():.1%}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
