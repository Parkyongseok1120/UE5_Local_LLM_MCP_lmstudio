#!/usr/bin/env python
"""Atomic text file writes for shared config and agent state."""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unique_temp_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")


def _fsync_file(handle) -> None:
    handle.flush()
    os.fsync(handle.fileno())


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = _unique_temp_path(path)
    with temp.open("w", encoding=encoding) as handle:
        handle.write(content)
        _fsync_file(handle)
    os.replace(temp, path)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = _unique_temp_path(path)
    with temp.open("wb") as handle:
        handle.write(data)
        _fsync_file(handle)
    os.replace(temp, path)
