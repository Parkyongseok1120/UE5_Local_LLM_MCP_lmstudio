#!/usr/bin/env python
"""Bounded subprocess probes for Windows process identity and lock checks."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence

ProcessAlive = Literal["alive", "dead", "unknown"]

DEFAULT_PROBE_TIMEOUT_SEC = 10.0


@dataclass(frozen=True)
class ProbeTimeout:
    timed_out: bool = True
    command: tuple[str, ...] = ()


def run_probe(
    command: Sequence[str],
    *,
    timeout_sec: float = DEFAULT_PROBE_TIMEOUT_SEC,
) -> subprocess.CompletedProcess[str] | ProbeTimeout:
    try:
        return subprocess.run(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=max(0.1, float(timeout_sec)),
        )
    except subprocess.TimeoutExpired:
        return ProbeTimeout(command=tuple(str(part) for part in command))


def probe_process_alive(pid: int) -> ProcessAlive:
    import os

    if pid <= 0:
        return "dead"
    if sys.platform == "win32":
        result = run_probe(["tasklist", "/FI", f"PID eq {pid}"])
        if isinstance(result, ProbeTimeout):
            return "unknown"
        return "alive" if str(pid) in (result.stdout or "") else "dead"
    try:
        os.kill(pid, 0)
        return "alive"
    except ProcessLookupError:
        return "dead"
    except PermissionError:
        return "unknown"
    except OSError:
        return "unknown"


def probe_process_start_identity(pid: int) -> str:
    """Return a stable birth identity so PID reuse cannot impersonate an owner."""

    if pid <= 0:
        return ""
    if sys.platform == "win32":
        try:
            import ctypes

            query_limited_information = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                query_limited_information, False, pid
            )
            if not handle:
                return ""
            creation = ctypes.c_ulonglong()
            exit_time = ctypes.c_ulonglong()
            kernel = ctypes.c_ulonglong()
            user = ctypes.c_ulonglong()
            try:
                ok = ctypes.windll.kernel32.GetProcessTimes(
                    handle,
                    ctypes.byref(creation),
                    ctypes.byref(exit_time),
                    ctypes.byref(kernel),
                    ctypes.byref(user),
                )
                return f"filetime:{creation.value}" if ok else ""
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except (AttributeError, OSError, TypeError, ValueError):
            return ""
    try:
        stat_path = Path(f"/proc/{pid}/stat")
        if stat_path.is_file():
            stat = stat_path.read_text(encoding="utf-8")
            close = stat.rfind(")")
            fields = stat[close + 1 :].split() if close >= 0 else stat.split()
            return f"ticks:{int(fields[19])}"
    except (OSError, IndexError, TypeError, ValueError):
        return ""
    try:
        result = run_probe(["ps", "-o", "lstart=", "-p", str(pid)])
    except OSError:
        return ""
    if isinstance(result, ProbeTimeout) or result.returncode != 0:
        return ""
    started = str(result.stdout or "").strip()
    return f"ps:{started}" if started else ""

