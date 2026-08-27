"""실행 시간·메모리 실측 (요구사항서 11.4).

**Streamlit Cloud 한도가 약 1 GB 다.** 배포 판단에 추정이 아니라 실측이 필요하다.
그리고 진행률 가중치(2번 항목)를 균등 분할하면 **오래 걸리는 구간에서 막힌 것처럼
보이므로**, 여기서 잰 값을 그대로 가중치로 쓴다.

재는 것은 셋이다.

    구간별 소요        어디가 느린가
    RSS 증감과 최대     어느 구간이 메모리를 쥐는가
    ``calculate_bill`` 호출 수  **요금 재계산이 곱해지는 곳을 센다**

세 번째가 핵심이다. 조합 × 감도 × 용량 단계가 곱해지면 호출 수가 급증하는데,
시간만 봐서는 어느 축이 곱해졌는지 알 수 없다.

메모리는 Windows 작업 집합(working set)을 직접 읽는다. ``tracemalloc`` 은
파이썬 할당자만 보므로 **NumPy 배열이 빠진다** — 이 도구에서 메모리를 쥐는 것이
바로 그 배열이다.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import gc
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import pandas as pd

__all__ = [
    "MB",
    "BenchmarkResult",
    "StageMeasurement",
    "bill_call_counter",
    "current_rss_bytes",
    "measure",
    "peak_rss_bytes",
]

MB = 1024 * 1024


# --------------------------------------------------------------------- 메모리


class _ProcessMemoryCounters(ctypes.Structure):
    """``PROCESS_MEMORY_COUNTERS`` (psapi.h)."""

    _fields_ = (
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    )


@lru_cache(maxsize=1)
def _psapi() -> tuple[Any, Any] | None:
    """``GetProcessMemoryInfo`` 를 쓸 수 있게 준비한다.

    **argtypes 를 반드시 지정한다.** 지정하지 않으면 64비트에서 핸들과 포인터가
    32비트로 잘려 호출이 조용히 실패하고 0 이 돌아온다 — 메모리를 재지 못한 것을
    "0 MB 썼다" 로 읽게 된다.
    """
    # **주석이 거짓이었다** (60세션 13절). 「이 프로젝트는 Windows 전용이다」 라고
    # 적혀 있었는데 **배포는 리눅스다** — Streamlit Cloud 로 나가고 `packages.txt`
    # 가 `fonts-nanum` 을 깐다. Windows 인 것은 **개발과 성능 측정**이지 프로젝트가
    # 아니다. 성능 측정은 앞단에서 손으로 돌리는 일이라 배포지에서는 부르지 않는다.
    #
    # **동작은 그대로다** — RSS 를 재는 것이 Windows API 라 다른 데서는 잴 수 없다.
    if sys.platform != "win32":  # pragma: no cover - 측정은 Windows 에서만 한다
        return None
    kernel32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    return kernel32, psapi


def _memory_counters() -> _ProcessMemoryCounters | None:
    """Windows 밖이거나 조회에 실패하면 ``None``. **0 을 지어내지 않는다.**"""
    api = _psapi()
    if api is None:
        return None
    kernel32, psapi = api
    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(_ProcessMemoryCounters)
    ok = psapi.GetProcessMemoryInfo(
        kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    )
    return counters if ok else None


def current_rss_bytes() -> float | None:
    counters = _memory_counters()
    return float(counters.WorkingSetSize) if counters is not None else None


def peak_rss_bytes() -> float | None:
    """프로세스 시작 이후 최대 작업 집합. **되돌아가지 않는 값이다.**"""
    counters = _memory_counters()
    return float(counters.PeakWorkingSetSize) if counters is not None else None


# --------------------------------------------------------------------- 호출 수


@contextmanager
def bill_call_counter() -> Iterator[Callable[[], int]]:
    """``calculate_bill`` 호출 수를 센다.

    여러 모듈이 ``from ... import calculate_bill`` 로 **이름을 각자 묶어 두었으므로**
    원본 하나만 바꿔서는 세어지지 않는다. 이미 불러온 ``kwise.*`` 모듈을 훑어
    같은 함수를 가리키는 이름을 모두 바꿔 끼운다.
    """
    from kwise.tariff import engine

    original = engine.calculate_bill
    count = 0

    def counting(*args: Any, **kwargs: Any) -> Any:
        nonlocal count
        count += 1
        return original(*args, **kwargs)

    patched: list[tuple[Any, str]] = []
    for name, module in list(sys.modules.items()):
        if not name.startswith("kwise.") or module is None:
            continue
        if getattr(module, "calculate_bill", None) is original:
            setattr(module, "calculate_bill", counting)  # noqa: B010 — 이름을 바꿔 끼운다
            patched.append((module, "calculate_bill"))
    try:
        yield lambda: count
    finally:
        for module, attribute in patched:
            setattr(module, attribute, original)


# --------------------------------------------------------------------- 측정


@dataclass(frozen=True)
class StageMeasurement:
    """구간 하나."""

    name: str
    seconds: float
    rss_delta_bytes: float | None
    bill_calls: int
    detail: str = ""

    @property
    def rss_delta_mb(self) -> float | None:
        return None if self.rss_delta_bytes is None else self.rss_delta_bytes / MB


@dataclass
class BenchmarkResult:
    """측정 결과 한 벌."""

    stages: list[StageMeasurement] = field(default_factory=list)
    baseline_rss_bytes: float | None = None
    peak_rss_bytes: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def total_seconds(self) -> float:
        return sum(item.seconds for item in self.stages)

    @property
    def total_bill_calls(self) -> int:
        return sum(item.bill_calls for item in self.stages)

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "구간": item.name,
                    "소요(초)": round(item.seconds, 3),
                    "비중(%)": (
                        round(item.seconds / self.total_seconds * 100, 1)
                        if self.total_seconds
                        else 0.0
                    ),
                    "RSS 증감(MB)": (
                        None if item.rss_delta_mb is None else round(item.rss_delta_mb, 1)
                    ),
                    "요금 재계산": item.bill_calls,
                    "비고": item.detail,
                }
                for item in self.stages
            ]
        )

    def weights(self, mapping: dict[str, tuple[str, ...]]) -> dict[str, float]:
        """구간 소요를 진행 단계별 가중치(합 1.0)로 접는다.

        Args:
            mapping: 단계 이름 → 그 단계에 드는 구간 이름들.
        """
        by_name = {item.name: item.seconds for item in self.stages}
        totals = {
            stage: sum(by_name.get(part, 0.0) for part in parts) for stage, parts in mapping.items()
        }
        overall = sum(totals.values())
        if not overall:
            return dict.fromkeys(totals, 1.0 / len(totals)) if totals else {}
        return {stage: round(value / overall, 4) for stage, value in totals.items()}


@contextmanager
def measure(result: BenchmarkResult, name: str, *, detail: str = "") -> Iterator[None]:
    """구간 하나를 재서 ``result`` 에 붙인다.

    **재기 전에 gc 를 돌린다.** 앞 구간이 남긴 쓰레기가 이 구간의 증감으로
    잡히면 어느 구간이 메모리를 쥐는지 알 수 없다.
    """
    gc.collect()
    before = current_rss_bytes()
    if result.baseline_rss_bytes is None:
        result.baseline_rss_bytes = before
    with bill_call_counter() as calls:
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - started
            after = current_rss_bytes()
            result.stages.append(
                StageMeasurement(
                    name=name,
                    seconds=elapsed,
                    rss_delta_bytes=(None if before is None or after is None else after - before),
                    bill_calls=calls(),
                    detail=detail,
                )
            )
            result.peak_rss_bytes = peak_rss_bytes()
