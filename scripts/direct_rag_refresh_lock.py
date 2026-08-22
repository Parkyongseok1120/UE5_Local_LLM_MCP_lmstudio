#!/usr/bin/env python
"""Cross-process lock for one Direct RAG index generation."""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator


class DirectRagRefreshBusyError(RuntimeError):
    """Raised when another process or thread owns the index refresh lock."""


_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.Lock] = {}


def _index_directory(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    is_index_file = resolved.is_file() or resolved.suffix.casefold() in {
        ".db",
        ".sqlite",
        ".sqlite3",
    }
    return resolved.parent if is_index_file else resolved


def refresh_lock_path(index: Path) -> Path:
    target = _index_directory(index)
    return target.parent / f".{target.name}.direct-rag-refresh.lock"


def _thread_lock(path: Path) -> threading.Lock:
    key = os.path.normcase(str(path.resolve()))
    with _LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.Lock())


def _try_lock_file(handle: BinaryIO) -> None:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
        os.fsync(handle.fileno())
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def index_refresh_lock(index: Path) -> Iterator[Path]:
    """Acquire a crash-released, non-blocking exclusive lock for one index."""

    lock_path = refresh_lock_path(index)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    local = _thread_lock(lock_path)
    if not local.acquire(blocking=False):
        raise DirectRagRefreshBusyError(
            f"Another Direct RAG refresh is already active for {_index_directory(index)}"
        )
    handle: BinaryIO | None = None
    locked = False
    try:
        handle = lock_path.open("a+b")
        try:
            _try_lock_file(handle)
            locked = True
        except OSError as exc:
            raise DirectRagRefreshBusyError(
                f"Another Direct RAG process owns the refresh lock for {_index_directory(index)}"
            ) from exc
        yield lock_path
    finally:
        if handle is not None:
            if locked:
                try:
                    _unlock_file(handle)
                except OSError:
                    pass
            handle.close()
        local.release()


__all__ = [
    "DirectRagRefreshBusyError",
    "index_refresh_lock",
    "refresh_lock_path",
]
