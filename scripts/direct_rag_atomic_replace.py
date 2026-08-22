#!/usr/bin/env python
"""Bounded Windows sharing-violation retry for atomic RAG file promotion."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable


def atomic_replace(
    source: Path,
    destination: Path,
    *,
    replace: Callable[[Path, Path], None] = os.replace,
    attempts: int = 40,
    delay_seconds: float = 0.05,
) -> None:
    for attempt in range(max(1, attempts)):
        try:
            replace(source, destination)
            return
        except PermissionError:
            if os.name != "nt" or attempt + 1 >= attempts:
                raise
            time.sleep(delay_seconds)


__all__ = ["atomic_replace"]
