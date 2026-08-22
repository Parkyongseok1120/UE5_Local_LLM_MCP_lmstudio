#!/usr/bin/env python
"""Open Direct RAG SQLite databases without ever creating a missing file."""

from __future__ import annotations

from pathlib import Path

from direct_rag_generation_identity import connect_consistent_readonly


def connect_readonly(index: Path, *, expected_generation: str | None = None):
    return connect_consistent_readonly(
        index,
        expected_generation=expected_generation,
    )


__all__ = ["connect_readonly"]
