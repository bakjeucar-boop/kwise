r"""판 개시 값 — **PC 와 남의 python** 을 한 명령으로 낸다 (S227).

``CLAUDE.md`` 9항 2번이 판 앞 · 회귀 앞 · 뒤(2번 PC 는 갈래 사이까지) 남의
python 을 세게 하는데 **세는 도구가 없어** 판마다 PowerShell 한 줄이나 스크래치로
셌다(S206 ~ S225 열여덟 판 · S226 1-2). 0-1 의 PC 값도 판마다 스크래치였다.

    .venv\Scripts\python.exe tools\run_tool.py open_values

내는 줄.

    PC <이름> · <CPU> · cpu <n> · RAM <바이트> B (<GB> GB) · -n auto 일꾼 <w>
    남의 python <N>
      <pid> ← <부모 pid> · <프로젝트> · <명령줄>        (N 만큼)

**「남의 python」 은 9항 2번 명령의 행 수에서 제 프로세스와 그 python 조상**
(venv 스텁 · ``run_tool``)**을 뺀 것이다.** 스텁과 자식은 따로 센다(79세션 ·
``.venv\Scripts\python.exe`` 는 런처 스텁이고 일은 자식이 한다). 프로젝트는
``ExecutablePath`` 의 ``.venv`` 앞 폴더이고, 자식은 부모 스텁의 경로로 본다.
**세고 적기만 한다 — 죽이거나 기다리지 않는다.**
"""

from __future__ import annotations

import ctypes
import json
import os
import platform
import subprocess
import winreg
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from xdist.plugin import pytest_xdist_auto_num_workers

__all__ = ["main", "others", "pc_line"]

#: ``CLAUDE.md`` 9항 2번 명령 그대로에 받을 칸만 고른다.
_PS = (
    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
    "Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine | ConvertTo-Json"
)


class _Memory(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def pc_line() -> str:
    memory = _Memory()
    memory.dwLength = ctypes.sizeof(_Memory)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory))
    key = winreg.OpenKey(
        winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
    )
    cpu = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
    # ``-n auto`` 가 부르는 xdist 훅을 그대로 부른다 — 수를 따로 짓지 않는다.
    workers = pytest_xdist_auto_num_workers(
        SimpleNamespace(option=SimpleNamespace(numprocesses="auto"))
    )
    ram = memory.ullTotalPhys
    return (
        f"PC {platform.node()} · {cpu} · cpu {os.cpu_count()} · "
        f"RAM {ram} B ({ram / 2**30:.1f} GB) · -n auto 일꾼 {workers}"
    )


def others() -> list[dict[str, Any]]:
    """남의 python 행. 칸마다 ``project`` 를 붙여 낸다."""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", _PS],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    ).stdout
    rows = json.loads(out) if out.strip() else []
    rows = [rows] if isinstance(rows, dict) else rows
    by_pid = {row["ProcessId"]: row for row in rows}
    mine = set()
    pid = os.getpid()
    while pid in by_pid and pid not in mine:  # 나와 내 python 조상
        mine.add(pid)
        pid = by_pid[pid]["ParentProcessId"]

    def project(row: dict[str, Any]) -> str:
        for candidate in (row, by_pid.get(row["ParentProcessId"])):
            parts = Path((candidate or {}).get("ExecutablePath") or "").parts
            if ".venv" in parts:
                return parts[parts.index(".venv") - 1]
        return "모름"

    return [{**row, "project": project(row)} for row in rows if row["ProcessId"] not in mine]


def main() -> int:
    print(pc_line())
    rows = others()
    print(f"남의 python {len(rows)}")
    for row in rows:
        print(
            f"  {row['ProcessId']} ← {row['ParentProcessId']} · {row['project']} · "
            f"{row['CommandLine']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
