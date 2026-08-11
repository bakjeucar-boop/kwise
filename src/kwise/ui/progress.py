"""화면 진행 표시 (요구사항서 10.6).

:mod:`kwise.progress` 의 콜백을 Streamlit 위젯에 붙인다. **계산 쪽은 이 파일을
모른다** — 콜백만 받는다.

    st.status    단계별 상태. 접었다 펼 수 있다
    st.progress  전체 진행률(%)
    한 줄        "5/8 태양광 발전량 — 용량 곡선 12/20"

**오래 걸리는 단계는 시작 전에 예상 소요를 알린다.** 실측상 태양광 한 구간이
전체의 절반이라, 아무 말 없이 몇 초를 멈추면 사용자는 멈춘 줄 안다.
"""

from __future__ import annotations

from types import TracebackType

import streamlit as st

from kwise.progress import (
    CallbackProgress,
    ProgressState,
    Stage,
    StageRunner,
    expected_seconds,
    slow_stage_seconds,
)

__all__ = ["StreamlitProgress", "progress_panel"]


class StreamlitProgress:
    """단계 상자 하나와 진행 막대 하나를 그린다.

    ``with`` 로 쓰면 끝날 때 상자를 접는다 — 끝난 계산의 진행 기록이 화면
    절반을 차지하면 정작 결과가 밀린다.
    """

    def __init__(self, label: str = "계산 중…", *, expanded: bool = True) -> None:
        self._status = st.status(label, expanded=expanded)
        self._bar = self._status.progress(0.0)
        self._line = self._status.empty()
        self._log = self._status.container()
        self._announced: set[str] = set()
        self._inner = CallbackProgress(sink=self._render)

    # ---- 컨텍스트 --------------------------------------------------------

    def __enter__(self) -> StreamlitProgress:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self._bar.progress(1.0)
            self._status.update(label="계산 완료", state="complete", expanded=False)
        else:
            self._status.update(label="계산이 멈췄습니다", state="error", expanded=True)

    # ---- 그리기 ----------------------------------------------------------

    def _announce(self, stage: Stage) -> None:
        """**시작 전에** 오래 걸릴 단계임을 알린다."""
        if stage.key in self._announced:
            return
        self._announced.add(stage.key)
        seconds = expected_seconds(stage.key)
        if seconds >= slow_stage_seconds():
            self._log.caption(f"{stage.title} — 약 {seconds:,.0f}초 걸립니다.")

    def _render(self, state: ProgressState) -> None:
        self._bar.progress(min(max(state.fraction, 0.0), 1.0))
        if state.skipped:
            # 건너뛴 단계는 **사유와 함께** 남긴다.
            self._log.caption(f"{state.stage.title} — {state.detail}, 건너뜀")
            self._line.write(f"{state.stage.title} — {state.detail}, 건너뜀")
            return
        if not state.finished:
            self._announce(state.stage)
        self._line.write(f"{state.line()}  ·  {state.percent}%")

    # ---- ProgressReporter -------------------------------------------------

    def stage(self, name: str, total_steps: int = 0) -> None:
        self._inner.stage(name, total_steps)

    def step(self, current: int, detail: str | None = None) -> None:
        self._inner.step(current, detail)

    def done(self, name: str) -> None:
        self._inner.done(name)

    def skipped(self, name: str, reason: str) -> None:
        self._inner.skipped(name, reason)


def progress_panel(label: str = "계산 중…") -> tuple[StreamlitProgress, StageRunner]:
    """진행 표시와 단계 진행자를 함께 만든다.

    panel, runner = progress_panel()
    with panel:
        with runner.running("solar", total_steps=20) as report:
            solar_curve(..., progress=report)
    """
    panel = StreamlitProgress(label)
    return panel, StageRunner(panel)
