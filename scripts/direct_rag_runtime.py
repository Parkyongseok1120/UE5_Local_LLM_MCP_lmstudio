#!/usr/bin/env python
"""Minimal runtime context shared with Direct RAG capability functions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class DirectRagRuntime:
    index: Path
    workspace: Path
    _notifier: Callable[[str, str], None]

    def notify(self, message: str, level: str = "info") -> None:
        self._notifier(str(message), str(level or "info"))


__all__ = ["DirectRagRuntime"]
