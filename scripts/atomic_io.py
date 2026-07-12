#!/usr/bin/env python
"""Atomic text file writes for shared config and agent state."""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from pathlib import Path

STALE_TEMP_AGE_SEC = 60.0


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unique_temp_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.{os.getpid()}.{int(time.time() * 1000)}.{uuid.uuid4().hex}.tmp")


def _cleanup_stale_temp_files(path: Path) -> None:
    directory = path.parent
    prefix = f"{path.name}."
    my_pid = str(os.getpid())
    now = time.time()
    try:
        for entry in directory.iterdir():
            if not entry.name.startswith(prefix) or not entry.name.endswith(".tmp"):
                continue
            middle = entry.name[len(prefix) : -4]
            owner_pid = middle.split(".", 1)[0]
            try:
                age = now - entry.stat().st_mtime
                stale = age > STALE_TEMP_AGE_SEC
            except OSError:
                stale = True
            owned = owner_pid == my_pid
            if owned and not stale:
                continue
            if not owned and not stale:
                continue
            try:
                entry.unlink(missing_ok=True)
            except OSError:
                pass
    except OSError:
        pass


def _fsync_file(handle) -> None:
    handle.flush()
    os.fsync(handle.fileno())


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    path = path.resolve()
    _cleanup_stale_temp_files(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = _unique_temp_path(path)
    with temp.open("w", encoding=encoding) as handle:
        handle.write(content)
        _fsync_file(handle)
    os.replace(temp, path)


def atomic_create_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(path, flags)
    except FileExistsError:
        raise FileExistsError(f"file already exists: {path}") from None
    try:
        data = content.encode(encoding)
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path = path.resolve()
    _cleanup_stale_temp_files(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = _unique_temp_path(path)
    with temp.open("wb") as handle:
        handle.write(data)
        _fsync_file(handle)
    os.replace(temp, path)
