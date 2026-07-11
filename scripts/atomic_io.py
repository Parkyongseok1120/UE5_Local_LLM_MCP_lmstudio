#!/usr/bin/env python
"""Atomic text file writes for shared config and agent state."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(content, encoding=encoding)
    if hasattr(os, "replace"):
        os.replace(temp, path)
    else:
        temp.replace(path)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes(data)
    if hasattr(os, "replace"):
        os.replace(temp, path)
    else:
        temp.replace(path)
