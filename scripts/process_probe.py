#!/usr/bin/env python
"""Bounded subprocess probes for Windows process identity and lock checks."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
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
    import sys

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
    except OSError:
        return "dead"

