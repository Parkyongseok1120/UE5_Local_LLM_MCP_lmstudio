#!/usr/bin/env python
"""Bounded subprocess probes for Windows process identity and lock checks."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Sequence

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
