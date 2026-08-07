"""케이스 스터디용 합성 부하 생성 (요구사항서 11.3).

    .venv\\Scripts\\python.exe tools\\make_cases.py

**실측이 1건뿐이라 샘플을 변형해 6종을 만든다.** 원본의 계절성·결측·그리드 이탈은
그대로 두고 **시간대별 분포만** 바꾼다. 그래야 요금적용전력 3규칙(경부하 제외,
대상월 한정, 계약전력 하한)이 케이스마다 다르게 작동하는 것을 볼 수 있다.

| 케이스 | 변형 | 종별 | 노리는 것 |
|---|---|---|---|
| C1 오전 피크형 | 원본 그대로 | 일반용(을) | 기준. 10~13시 집중 |
| C2 오후 피크형 | 프로파일 5시간 뒤로 | 일반용(을) | PV 최성기와 피크가 어긋난다 |
| C3 평탄형 | 부하율 85% | 일반용(을) | 기본요금 절감 여지가 거의 없다 |
| C4 주말 가동형 | 주말을 평일의 90% | **산업용(을)** | 봄·가을 주말 할인 특례 |
| C5 겨울 피크형 | 여름↓ 겨울 오전↑ | 일반용(을) | 대상월인데 PV 가 약하다 |
| C6 야간 피크형 | 야간(22~08시) 최대 | 일반용(을) | **경부하는 요금적용전력 대상이 아니다** |

**하루 총량은 변형 전후로 보존한다** (C3·C6 은 정의상 총량이 달라진다).
총량이 함께 흔들리면 전력량요금까지 움직여 어느 규칙이 작동했는지 알 수 없다.

라벨 규약은 원본과 같다 — **구간 끝**, 자정은 ``24:00``.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from kwise.io import load_usage, slot_start  # noqa: E402

DEFAULT_SOURCE = PROJECT_ROOT / "input" / "사용량조회_20240429.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "input" / "cases"
HEADER = ("검침일", "순방향 유효전력량(KWH)")

# 부하율 목표 (C3). 데이터센터 성격이다.
FLAT_LOAD_FACTOR = 0.85
# 주말 부하를 평일 대비 이 비율로 올린다 (C4). 공장 성격이다.
WEEKEND_RATIO = 0.90
# 야간 창 (C6). 라벨이 구간 끝이므로 구간 시작 시각으로 판정한다.
NIGHT_START, NIGHT_END = 22, 8


@dataclass(frozen=True)
class CaseRecipe:
    """케이스 하나의 변형 규칙."""

    key: str
    name: str
    contract_type: str
    description: str
    transform: Callable[[pd.Series, pd.DatetimeIndex], pd.Series]


def _starts(index: pd.DatetimeIndex, interval_minutes: int = 15) -> pd.DatetimeIndex:
    """귀속 판정용 구간 시작 시각. 라벨이 구간 끝이라 한 칸 뺀다."""
    return slot_start(index, interval_minutes)


def _preserve_daily_total(before: pd.Series, after: pd.Series, day: np.ndarray) -> pd.Series:
    """일별 총량을 원본과 같게 되돌린다.

    시간대 분포만 바꾸는 변형에서는 총량이 흔들리면 안 된다. 총량이 함께 움직이면
    전력량요금까지 변해 어느 규칙이 작동했는지 구분할 수 없다.
    """
    original = before.groupby(day).transform("sum")
    changed = after.groupby(day).transform("sum")
    scale = (original / changed).where(changed > 0, 1.0)
    return after * scale


# --------------------------------------------------------------------- 변형


def keep(kwh: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """C1 — 원본 그대로."""
    _ = index
    return kwh


def shift_afternoon(kwh: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """C2 — 프로파일을 5시간 뒤로 민다 (같은 날 안에서 순환).

    날짜를 넘기지 않고 **하루 안에서 돌린다.** 날짜를 넘기면 계절 귀속이 흔들려
    변형의 뜻이 흐려진다.
    """
    starts = _starts(index)
    day = starts.normalize().to_numpy()
    minutes = (starts.hour * 60 + starts.minute).to_numpy()
    # 새 프로파일의 m 시각에는 **5시간 전**의 값이 온다. 그래야 10시 피크가
    # 15시로 간다. 부호를 뒤집으면 오전이 새벽으로 가서 뜻이 정반대가 된다.
    origin = (minutes - 5 * 60) % (24 * 60)
    frame = pd.DataFrame({"day": day, "from": minutes, "value": kwh.to_numpy()})
    lookup = frame.set_index(["day", "from"])["value"]
    target = pd.MultiIndex.from_arrays([day, origin])
    moved = lookup.reindex(target).to_numpy()
    return pd.Series(moved, index=kwh.index, name=kwh.name)


def flatten(kwh: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """C3 — 부하율 85%. 평균 쪽으로 끌어당긴다.

    부하율 = 평균 ÷ 최대. 목표 부하율을 맞추려면 편차를 줄이면 된다.
    전체 평균을 축으로 진폭을 줄이되 **총량은 보존**한다 (평균이 유지된다).
    """
    _ = index
    values = kwh.astype("float64")
    observed = values.dropna()
    if observed.empty:
        return kwh
    mean = float(observed.mean())
    peak = float(observed.max())
    if peak <= mean:
        return kwh
    # 진폭을 s 배 하면 최대가 mean + (peak-mean)*s 가 된다.
    # 목표: mean / (mean + (peak-mean)*s) = 0.85
    target_peak = mean / FLAT_LOAD_FACTOR
    scale = (target_peak - mean) / (peak - mean)
    return (mean + (values - mean) * scale).clip(lower=0.0)


def weekend_operation(kwh: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """C4 — 주말 부하를 평일 평균의 90% 수준으로 올린다.

    공장 성격이다. **주말 총량이 늘어난다** — 가동일이 늘어난 것이므로 총량 보존을
    적용하지 않는다.
    """
    starts = _starts(index)
    is_weekend = np.isin(starts.weekday.to_numpy(), (5, 6))
    values = kwh.astype("float64")
    weekday_mean = float(values[~is_weekend].dropna().mean())
    weekend_mean = float(values[is_weekend].dropna().mean())
    if weekend_mean <= 0:
        return kwh
    factor = WEEKEND_RATIO * weekday_mean / weekend_mean
    scaled = values.copy()
    scaled[is_weekend] = values[is_weekend] * factor
    return scaled


def winter_peak(kwh: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """C5 — 여름을 낮추고 겨울 오전을 높인다.

    **겨울(12·1·2)은 요금적용전력 대상월인데 PV 발전이 약하다.** 그 조합에서
    기본요금 절감이 어떻게 나오는지 보려는 케이스다.
    """
    starts = _starts(index)
    month = starts.month.to_numpy()
    hour = starts.hour.to_numpy()
    values = kwh.astype("float64").to_numpy()

    winter = np.isin(month, (12, 1, 2))
    factor = np.ones(len(values))
    factor[np.isin(month, (6, 7, 8))] = 0.70  # 여름을 낮춘다
    # **난방 램프 시간대만 올린다 — 07~09시와 17~20시.** 겨울 정오까지 올리면
    # 그 시각에는 PV 가 (약하게나마) 발전하므로 "PV 로 못 깎는 피크" 라는
    # 케이스의 뜻이 사라진다. 실제 겨울 피크 건물도 새벽 난방 기동과 저녁에 걸린다.
    factor[winter & (hour >= 7) & (hour < 9)] = 1.60
    factor[winter & (hour >= 17) & (hour < 20)] = 1.60
    return pd.Series(values * factor, index=kwh.index, name=kwh.name)


def night_peak(kwh: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """C6 — 야간(22~08시)에 최대, 주간은 그보다 낮게.

    **경부하 시간대는 요금적용전력 대상이 아니다** (5.2 ①). 관측 최대수요는 밤에
    나오지만 요금적용전력은 낮의 최대로 결정되어야 한다. 그 규칙이 전체
    파이프라인에서 작동하는지 보려는 케이스다.

    하루 총량은 보존한다 — 낮에서 뺀 만큼 밤에 얹는다.
    """
    starts = _starts(index)
    hour = starts.hour.to_numpy()
    is_night = (hour >= NIGHT_START) | (hour < NIGHT_END)
    values = kwh.astype("float64")

    scaled = values.copy()
    scaled[is_night] = values[is_night] * 2.2
    scaled[~is_night] = values[~is_night] * 0.55
    return _preserve_daily_total(values, scaled, starts.normalize().to_numpy())


RECIPES: tuple[CaseRecipe, ...] = (
    CaseRecipe("C1", "오전 피크형", "general_b", "원본 그대로 (10~13시 집중)", keep),
    CaseRecipe("C2", "오후 피크형", "general_b", "프로파일 5시간 이동", shift_afternoon),
    CaseRecipe("C3", "평탄형", "general_b", "부하율 85% (데이터센터 성격)", flatten),
    CaseRecipe(
        "C4", "주말 가동형", "industrial_b", "주말을 평일의 90% (공장 성격)", weekend_operation
    ),
    CaseRecipe("C5", "겨울 피크형", "general_b", "여름↓ 겨울 오전↑", winter_peak),
    CaseRecipe("C6", "야간 피크형", "general_b", "야간(22~08시) 최대", night_peak),
)


def build_case(source: pd.DataFrame, recipe: CaseRecipe) -> pd.DataFrame:
    """원본 프레임을 변형해 같은 양식으로 돌려준다.

    **결측은 결측인 채로 둔다** (2세션 결정). 그리드 이탈 행도 건드리지 않는다.
    """
    index = pd.DatetimeIndex(source["label"])
    changed = recipe.transform(source["kwh"], index)
    result = source.copy()
    result["kwh"] = changed
    return result


def read_source(path: Path) -> pd.DataFrame:
    """원본 CSV 를 라벨·값 두 열로 읽는다. 결측 표기와 ``24:00`` 을 보존한다."""
    raw = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
    label_column = raw.columns[-2] if len(raw.columns) > 2 else raw.columns[0]
    value_column = raw.columns[-1]
    text = raw[label_column].astype(str)
    values = pd.to_numeric(raw[value_column].str.replace(",", "", regex=False), errors="coerce")
    # 판정용 시각. ``24:00`` 은 다음 날 ``00:00`` 이다.
    stamps = pd.to_datetime(text.str.replace(" 24:00", " 00:00", regex=False))
    stamps = stamps + pd.to_timedelta(text.str.contains(" 24:00").astype(int), unit="D")
    return pd.DataFrame({"text": text, "label": stamps, "kwh": values})


def write_case(frame: pd.DataFrame, path: Path) -> Path:
    """원본과 같은 2열 양식으로 쓴다. ``24:00`` 표기를 그대로 되살린다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [",".join(HEADER)]
    for text, value in zip(frame["text"], frame["kwh"], strict=True):
        lines.append(f"{text}," if pd.isna(value) else f"{text},{value:.2f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="케이스 스터디용 합성 부하 생성 (11.3)")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)

    source_path = Path(args.source)
    output_dir = Path(args.output)
    source = read_source(source_path)
    print(f"원본 {source_path.name} — {len(source):,}행, 결측 {int(source['kwh'].isna().sum()):,}")

    for recipe in RECIPES:
        frame = build_case(source, recipe)
        target = output_dir / f"{recipe.key}_{recipe.name}.csv"
        write_case(frame, target)
        usage = load_usage(target)
        print(
            f"{recipe.key} {recipe.name:8s} {recipe.contract_type:12s} "
            f"총 {usage.meta.total_kwh / 1000:>9,.0f} MWh · "
            f"최대 {usage.kw.max():>8,.1f} kW · "
            f"부하율 {usage.kw.mean() / usage.kw.max():.1%} · {recipe.description}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
