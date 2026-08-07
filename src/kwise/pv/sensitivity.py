"""감도 3종 — **첨예도 조정** (요구사항서 9.2).

확률 표기(P90 등)를 쓰지 않는다. 부하가 1년 실측으로 고정되어 연도별 변동의
절반만 반영되므로 확률 표기가 통계적으로 정직하지 않기 때문이다. 대신 세 시나리오를
나란히 놓는다.

**계수를 출력 전체에 곱하지 않는다.** 9.1 이 말하는 편향의 성격이 "평탄화" 이기
때문이다. 재분석 일사 데이터는 공간·시간 평균화로 프로파일이 눌려서, 총량은 실측과
거의 맞는데 맑은 날 정오의 첨예도가 낮게 나온다 (시간별 R² 약 0.8, 총량은 수렴).
총량이 맞는데 일률 계수를 곱하면 총량을 ±30% 흔들게 되고, 그러면 전력량요금
절감액의 밴드와 회수기간 밴드가 함께 부풀려진다.

편향이 평탄화이므로 감도도 **평탄화 정도**를 조정한다.

    adjusted(t) = 일평균발전 + (base(t) − 일평균발전) × s

평균을 축으로 진폭만 늘리고 줄이므로 **일별 총 발전량이 보존된다.** 음수는 0 으로
자르고, 자른 뒤 일별 총량이 원본과 같도록 재정규화한다.

    평탄형  s = 0.85    정오가 낮고 어깨가 넓다
    기준    s = 1.00    시뮬레이션 그대로
    첨예형  s = 1.25    정오가 높고 어깨가 좁다

**이 축은 낙관·비관이 아니다.** 총량이 보존되므로 첨예도를 올리면 정오가
높아지는 대신 아침·저녁 어깨가 낮아진다. 부하 피크가 정오에서 벗어난 건물
(오전 피크형·오후 피크형)에서는 첨예형의 기본요금 절감액이 **오히려 작다.**
이름이 그 축을 가리켜야 오독이 없어서 표시 명칭을 평탄형/기준/첨예형으로 둔다
(내부 키는 ``conservative``/``base``/``optimistic`` 그대로다).

이 방식이면 지표별 특수 처리가 필요 없다. **총량 기반 지표(전력량요금 절감액)는
거의 불변이고, 피크 시각 기반 지표(기본요금 절감액·요금적용전력)만 흔들린다.**
기본요금이 kWise 가 재는 값이므로 감도가 재야 할 것을 정확히 잰다.

s 값은 ``data\\pv_presets.json`` 에서 읽는다 — 하드코딩하지 않는다.

**일평균발전은 그날 발전이 있는 슬롯만의 평균이고, 조정도 그 슬롯에만 건다.**
야간 0 을 평균에 넣으면 밤의 편차(0 − 평균)가 전체를 지배해, s 를 키웠는데
재정규화 후 정오 출력이 오히려 낮아진다. 야간 슬롯에 조정을 걸면 s < 1 일 때
0 이 평균 쪽으로 끌려 올라가 **한밤중에 발전이 생긴다.** 낮의 곡선만 평균을 축으로
회전시켜야 두 문제가 함께 사라지고, 발전 슬롯 합이 곧 일 발전량이라 총량도
정확히 보존된다.

**요금제 전환·계약전력 조정·역률 개선에는 감도를 적용하지 않는다.** 실측 데이터와
요금표만으로 확정되는 계산이다.

시나리오는 순차 처리한다. :func:`iter_scenarios` 는 제너레이터이고
:func:`summarize_scenarios` 는 요약만 남긴다. 세 시계열을 동시에 들고 있지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pandas as pd

from kwise.rules import assumption

__all__ = [
    "SCENARIO_KEYS",
    "ScenarioSummary",
    "SharpnessFactors",
    "iter_scenarios",
    "load_sharpness_factors",
    "sharpen",
    "summarize_scenarios",
]


# 내부 키. **바꾸지 않는다** — 설정 파일과 케이스 정의가 이 이름을 쓴다.
SCENARIO_KEYS: tuple[str, str, str] = ("conservative", "base", "optimistic")


@dataclass(frozen=True)
class SharpnessFactors:
    """첨예도 계수 3종. 사용자가 조정할 수 있다.

    실측 발전 데이터가 있으면 맑은 날 정오 구간의 예측 대비 실측 비율에서
    산출한다. **총량 비율이 아니라 피크/일평균 비의 비율**을 본다.

    **내부 키와 표시 라벨을 분리한다.** 필드 이름(``conservative``/``optimistic``)은
    호환을 위해 그대로 두고, 산출물에 나가는 이름은 :attr:`labels` 다 —
    이 축은 낙관·비관이 아니라 프로파일이 얼마나 뾰족한가이기 때문이다.
    """

    # **기본값을 두지 않는다.** 값은 assumptions.json 이 준다 (요구사항서 12장).
    conservative: float
    base: float
    optimistic: float
    labels: tuple[str, str, str]

    def __post_init__(self) -> None:
        values = (self.conservative, self.base, self.optimistic)
        if any(value < 0 for value in values):
            raise ValueError(f"첨예도 계수는 음수일 수 없습니다: {values}")
        if not self.conservative <= self.base <= self.optimistic:
            raise ValueError(f"첨예도 계수는 평탄 ≤ 기준 ≤ 첨예 여야 합니다: {values}")

    @property
    def values(self) -> tuple[float, float, float]:
        return (self.conservative, self.base, self.optimistic)

    def items(self) -> tuple[tuple[str, float], ...]:
        """``(표시 라벨, s)``. 표의 인덱스가 된다."""
        return tuple(zip(self.labels, self.values, strict=True))

    def keyed_items(self) -> tuple[tuple[str, str, float], ...]:
        """``(내부 키, 표시 라벨, s)``. 키로 찾아야 할 때 쓴다."""
        return tuple(zip(SCENARIO_KEYS, self.labels, self.values, strict=True))

    @property
    def base_label(self) -> str:
        return self.labels[1]


def load_sharpness_factors() -> SharpnessFactors:
    """``data\assumptions.json`` 에서 첨예도 계수와 라벨을 읽는다.

    **코드에 기본값을 두지 않는다.** 파일이 없거나 항목이 빠지면
    :class:`~kwise.rules.RuleDataError` 로 멈춘다 — 기본값을 남겨 두면 파일을
    고쳐도 반영되지 않는 사고가 나고, 값이 그럴듯해서 발견이 늦다.
    """
    labels = assumption("sensitivity.labels")
    return SharpnessFactors(
        conservative=float(assumption("sensitivity.sharpness.conservative")),
        base=float(assumption("sensitivity.sharpness.base")),
        optimistic=float(assumption("sensitivity.sharpness.optimistic")),
        labels=(str(labels[0]), str(labels[1]), str(labels[2])),
    )


def sharpen(kw: pd.Series, sharpness: float) -> pd.Series:
    """일별 총량을 보존한 채 발전 곡선의 첨예도만 조정한다 (요구사항서 9.2).

    ``s = 1.0`` 이면 **원본을 그대로 돌려준다** — 부동소수 오차조차 만들지 않는다.
    기준 시나리오가 시뮬레이션 결과와 완전히 같아야 회귀값이 흔들리지 않는다.

    Args:
        kw: 발전 출력 시계열. 인덱스는 :class:`~pandas.DatetimeIndex` 여야 한다.
        sharpness: 첨예도 계수. 1 보다 작으면 평탄해지고 크면 뾰족해진다.

    Returns:
        같은 인덱스의 시계열. **일별 합계가 원본과 같다.**
    """
    if sharpness < 0:
        raise ValueError(f"첨예도 계수는 음수일 수 없습니다: {sharpness}")
    if sharpness == 1.0 or kw.empty:
        return kw

    index = pd.DatetimeIndex(kw.index)
    day = index.normalize().to_numpy()
    values = kw.astype("float64")

    # 발전이 있는 슬롯만의 일평균. 야간 0 을 넣으면 밤의 편차가 전체를 지배한다.
    generating = values > 0
    mean = values.where(generating).groupby(day).transform("mean").fillna(0.0)

    # 발전이 없는 슬롯은 0 에 둔다. s < 1 이면 0 이 평균 쪽으로 끌려 올라가
    # **한밤중에 발전이 생긴다.** 조정 대상은 낮의 곡선뿐이다.
    adjusted = (mean + (values - mean) * sharpness).where(generating, 0.0).clip(lower=0.0)

    # 클램프로 늘어난 만큼을 되돌려 일별 총량을 원본과 같게 맞춘다.
    original_daily = values.groupby(day).transform("sum")
    adjusted_daily = adjusted.groupby(day).transform("sum")
    scale = (original_daily / adjusted_daily).where(adjusted_daily > 0, 1.0)
    return (adjusted * scale).rename(kw.name)


@dataclass(frozen=True)
class ScenarioSummary:
    """시나리오 하나의 요약. 시계열은 들고 있지 않는다."""

    name: str
    sharpness: float
    total_kwh: float
    peak_kw: float


def iter_scenarios(
    kw: pd.Series,
    factors: SharpnessFactors | None = None,
) -> Iterator[tuple[str, float, pd.Series]]:
    """시나리오를 하나씩 낸다. 호출자가 요약만 챙기면 메모리가 한 벌로 유지된다."""
    items = load_sharpness_factors() if factors is None else factors
    for name, sharpness in items.items():
        yield name, sharpness, sharpen(kw, sharpness).rename(f"pv_kw_{name}")


def summarize_scenarios(
    kw: pd.Series,
    interval_minutes: int,
    factors: SharpnessFactors | None = None,
) -> tuple[ScenarioSummary, ...]:
    """감도 3종의 발전량·피크만 뽑는다. **발전량은 세 시나리오가 같아야 한다.**"""
    slot_hours = interval_minutes / 60.0
    summaries: list[ScenarioSummary] = []
    for name, sharpness, adjusted in iter_scenarios(kw, factors):
        summaries.append(
            ScenarioSummary(
                name=name,
                sharpness=sharpness,
                total_kwh=float(adjusted.sum()) * slot_hours,
                peak_kw=float(adjusted.max()) if len(adjusted) else 0.0,
            )
        )
    return tuple(summaries)
