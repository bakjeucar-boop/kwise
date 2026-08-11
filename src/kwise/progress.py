"""진행 표시 (요구사항서 10.6).

**계산 모듈은 Streamlit 을 import 하지 않는다.** 화면은 콜백을 넘기고, CLI 는
같은 콜백에 rich 를 붙인다. 계산 쪽은 어느 쪽이 받는지 모른다.

    reporter.stage(name, total_steps)   단계 시작
    reporter.step(current, detail)      단계 내 진행
    reporter.done(name)                 단계 완료
    reporter.skipped(name, reason)      캐시 적중 등으로 건너뜀

**진행률 가중치를 균등 분할하지 않는다.** 여덟 단계를 12.5%씩 나누면 태양광
구간에서 몇 초 동안 막대가 멈춘 것처럼 보인다. 가중치는 실측값이고
``assumptions.json`` 의 ``progress.weights`` 에 있다 (``tools\\run_benchmark.py``).

콜백은 **선택 인자**다. 주지 않으면 :data:`NULL_PROGRESS` 가 들어가 아무 일도
하지 않는다 — 계산 함수를 콜백 없이 부르는 것이 여전히 정상 경로다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

__all__ = [
    "NULL_PROGRESS",
    "STAGES",
    "STAGE_WEIGHT_KEYS",
    "WEIGHT_SOURCE",
    "CallbackProgress",
    "NullProgress",
    "ProgressReporter",
    "ProgressState",
    "RichProgress",
    "Stage",
    "StageRunner",
    "expected_seconds",
    "measure_total",
    "record",
    "slow_stage_seconds",
    "stage_by_key",
    "stage_weights",
    "total_seconds",
]

WEIGHT_SOURCE = "progress.weights"


@dataclass(frozen=True)
class Stage:
    """진행 단계 하나. 번호는 1부터다."""

    key: str
    number: int
    label: str

    @property
    def title(self) -> str:
        return f"{self.number}/{len(STAGES)} {self.label}"


STAGES: tuple[Stage, ...] = (
    Stage("read", 1, "데이터 읽기"),
    Stage("quality", 2, "품질 검사"),
    Stage("diagnose", 3, "진단"),
    Stage("weather", 4, "기상 데이터 확보"),
    Stage("solar", 5, "태양광 발전량"),
    Stage("measures", 6, "개선 수단 평가"),
    Stage("compare", 7, "조합 비교"),
    Stage("export", 8, "산출물 생성"),
)

STAGE_WEIGHT_KEYS: tuple[str, ...] = tuple(item.key for item in STAGES)
_BY_KEY: dict[str, Stage] = {item.key: item for item in STAGES}


def stage_by_key(key: str) -> Stage:
    try:
        return _BY_KEY[key]
    except KeyError as exc:
        raise KeyError(f"등록되지 않은 진행 단계입니다: {key!r}") from exc


def stage_weights() -> dict[str, float]:
    """단계별 가중치. **실측값이며 파일에 있다** (12장 규약).

    파일에 없는 단계는 균등분으로 물러선다 — 단계를 늘렸는데 가중치를 빠뜨렸다고
    진행률이 멈추면 안 된다.
    """
    from kwise.rules import assumption

    stored = assumption(WEIGHT_SOURCE)
    even = 1.0 / len(STAGES)
    values = {key: float(stored.get(key, even)) for key in STAGE_WEIGHT_KEYS}
    total = sum(values.values())
    return {key: value / total for key, value in values.items()} if total else values


def total_seconds() -> float:
    """샘플 한 벌을 끝까지 도는 데 걸리는 실측 시간 (초)."""
    from kwise.rules import assumption

    return float(assumption("progress.total_seconds"))


def slow_stage_seconds() -> float:
    """이보다 오래 걸릴 단계는 **시작 전에 예상 소요를 알린다.**"""
    from kwise.rules import assumption

    return float(assumption("progress.slow_stage_seconds"))


def expected_seconds(key: str) -> float:
    """단계 하나의 예상 소요. 가중치 × 전체 실측이다."""
    return stage_weights().get(key, 0.0) * total_seconds()


# --------------------------------------------------------------------- 상태


@dataclass(frozen=True)
class ProgressState:
    """화면·CLI 가 그릴 한 줄."""

    stage: Stage
    fraction: float
    """전체 진행률 0.0~1.0. **가중치를 반영한 값이다.**"""
    detail: str = ""
    current: int = 0
    total_steps: int = 0
    skipped: bool = False
    finished: bool = False

    def line(self) -> str:
        """``"5/8 태양광 발전량 — 용량 곡선 12/20"`` 꼴."""
        text = self.stage.title
        if self.detail:
            text += f" — {self.detail}"
        elif self.total_steps:
            text += f" — {self.current}/{self.total_steps}"
        return text

    @property
    def percent(self) -> int:
        return round(self.fraction * 100)


# --------------------------------------------------------------------- 프로토콜


@runtime_checkable
class ProgressReporter(Protocol):
    """진행 보고자. **계산 함수는 이것만 안다.**"""

    def stage(self, name: str, total_steps: int = 0) -> None: ...

    def step(self, current: int, detail: str | None = None) -> None: ...

    def done(self, name: str) -> None: ...

    def skipped(self, name: str, reason: str) -> None: ...


class NullProgress:
    """아무 일도 하지 않는 보고자. **콜백 기본값이다.**"""

    def stage(self, name: str, total_steps: int = 0) -> None:
        return None

    def step(self, current: int, detail: str | None = None) -> None:
        return None

    def done(self, name: str) -> None:
        return None

    def skipped(self, name: str, reason: str) -> None:
        return None


NULL_PROGRESS: ProgressReporter = NullProgress()


# --------------------------------------------------------------------- 콜백 구현


@dataclass
class CallbackProgress:
    """가중치로 전체 진행률을 계산해 ``sink`` 에 넘긴다.

    화면과 CLI 가 이것을 공유하고 ``sink`` 만 다르게 준다. 진행률 계산이 한
    곳에 있어야 두 쪽이 다른 퍼센트를 보여 주지 않는다.
    """

    sink: Callable[[ProgressState], None]
    weights: dict[str, float] = field(default_factory=stage_weights)
    _completed: float = field(default=0.0, init=False)
    _current: Stage | None = field(default=None, init=False)
    _total_steps: int = field(default=0, init=False)

    def _weight(self, key: str) -> float:
        return self.weights.get(key, 1.0 / len(STAGES))

    def _emit(self, **kwargs: object) -> None:
        stage = self._current
        if stage is None:
            return
        self.sink(ProgressState(stage=stage, **kwargs))  # type: ignore[arg-type]

    def stage(self, name: str, total_steps: int = 0) -> None:
        self._current = stage_by_key(name)
        self._total_steps = total_steps
        self._emit(fraction=self._completed, current=0, total_steps=total_steps)

    def step(self, current: int, detail: str | None = None) -> None:
        stage = self._current
        if stage is None:
            return
        within = current / self._total_steps if self._total_steps else 0.0
        fraction = self._completed + self._weight(stage.key) * min(max(within, 0.0), 1.0)
        self._emit(
            fraction=min(fraction, 1.0),
            detail=detail or "",
            current=current,
            total_steps=self._total_steps,
        )

    def done(self, name: str) -> None:
        self._current = stage_by_key(name)
        self._completed = min(self._completed + self._weight(name), 1.0)
        self._emit(fraction=self._completed, total_steps=self._total_steps, finished=True)
        self._total_steps = 0

    def skipped(self, name: str, reason: str) -> None:
        """건너뛴 단계도 **진행률은 채운다.** 막대가 멈춘 것처럼 보이면 안 된다."""
        self._current = stage_by_key(name)
        self._completed = min(self._completed + self._weight(name), 1.0)
        self._emit(fraction=self._completed, detail=reason, skipped=True, finished=True)


# --------------------------------------------------------------------- CLI


class RichProgress:
    """CLI 용 보고자. **rich 를 늦게 불러온다** — import 만으로 끌어오지 않는다."""

    def __init__(self, console: object | None = None) -> None:
        from rich.console import Console

        self._console = console if console is not None else Console()
        self._inner = CallbackProgress(sink=self._write)

    def _write(self, state: ProgressState) -> None:
        from rich.console import Console

        assert isinstance(self._console, Console)
        if state.skipped:
            self._console.print(f"[dim]{state.line()} — 건너뜀[/dim]")
        elif state.finished:
            self._console.print(f"[green]✓[/green] {state.stage.title} ({state.percent}%)")
        elif state.detail or state.total_steps:
            self._console.print(f"  {state.line()}", highlight=False)
        else:
            self._console.print(f"[bold]{state.stage.title}[/bold] …")

    def stage(self, name: str, total_steps: int = 0) -> None:
        self._inner.stage(name, total_steps)

    def step(self, current: int, detail: str | None = None) -> None:
        self._inner.step(current, detail)

    def done(self, name: str) -> None:
        self._inner.done(name)

    def skipped(self, name: str, reason: str) -> None:
        self._inner.skipped(name, reason)


def record(reporter: ProgressReporter | None) -> ProgressReporter:
    """``None`` 을 :data:`NULL_PROGRESS` 로 바꾼다. **계산 함수 첫 줄에 쓴다.**

    콜백은 선택 인자이므로 계산 함수는 ``None`` 을 받을 수 있어야 하고, 그때
    ``if reporter is not None`` 을 곳곳에 흩뿌리면 빠뜨리는 자리가 생긴다.
    """
    return NULL_PROGRESS if reporter is None else reporter


def measure_total(enabled: Iterable[str]) -> int:
    """켠 수단 수. 6단계 ``total_steps`` 다. **목록을 두 벌 두지 않는다.**"""
    from kwise.measures import MEASURE_CATALOG

    chosen = set(enabled)
    return sum(1 for kind in MEASURE_CATALOG if kind.key in chosen)


@dataclass
class StageRunner:
    """단계 경계를 대신 그어 준다.

    ``stage`` 와 ``done`` 을 짝으로 부르는 일을 화면과 CLI 가 각자 하면 한쪽에서
    ``done`` 을 빠뜨려 진행률이 멈춘다. 여기 한 곳에 둔다.

        with runner.running("solar", total_steps=20) as report:
            solar_curve(..., progress=report)
    """

    reporter: ProgressReporter = NULL_PROGRESS

    @contextmanager
    def running(self, key: str, total_steps: int = 0) -> Iterator[ProgressReporter]:
        self.reporter.stage(key, total_steps)
        try:
            yield self.reporter
        finally:
            self.reporter.done(key)

    def skip(self, key: str, reason: str) -> None:
        """건너뛴 단계. **사유를 함께 낸다** — 빈 채로 넘기면 빠뜨린 것처럼 보인다."""
        self.reporter.skipped(key, reason)
