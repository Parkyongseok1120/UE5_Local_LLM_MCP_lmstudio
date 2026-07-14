#!/usr/bin/env python
"""Cross-process file locks compatible with lmstudio-unreal-agent-mcp write-locks.js."""

from __future__ import annotations

import hashlib
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from process_probe import ProcessAlive, probe_process_alive
from state_root import ensure_state_root_layout, resolve_agent_state_root

_OWNER = f"{os.getpid()}:{uuid.uuid4().hex}"
_HEARTBEAT_INTERVAL_SEC = 60.0
_PENDING_GUARD = threading.Lock()
_PENDING_PATHS: dict[str, int] = {}


def _canonical_lock_key(abs_path: Path) -> str:
    try:
        return os.path.realpath(abs_path).lower()
    except OSError:
        return abs_path.resolve().as_posix().lower()


def lock_file_path(abs_path: Path, state_root: Path | None = None) -> Path:
    root = ensure_state_root_layout(state_root or resolve_agent_state_root())
    digest = hashlib.sha256(_canonical_lock_key(abs_path).encode("utf-8")).hexdigest()
    return root / "locks" / f"{digest}.lock"


def _read_lock_owner(lock_path: Path) -> str:
    try:
        return lock_path.read_text(encoding="utf-8").splitlines()[0].strip()
    except OSError:
        return ""


def _process_alive(pid: int) -> ProcessAlive:
    return probe_process_alive(pid)


def _is_stale_lock(lock_path: Path) -> bool:
    if not lock_path.is_file():
        return True
    owner = _read_lock_owner(lock_path)
    if not owner:
        return True
    pid_part = owner.split(":", 1)[0]
    try:
        pid = int(pid_part)
    except ValueError:
        return True
    if pid <= 0:
        return True
    alive = _process_alive(pid)
    if alive == "dead":
        return True
    if alive == "unknown":
        return False
    return False


def _write_lock_payload(lock_path: Path, label: str) -> None:
    payload = f"{_OWNER}\n{label}\n{datetime.now(tz=timezone.utc).isoformat()}\n"
    lock_path.write_text(payload, encoding="utf-8")


def refresh_lock_heartbeat(abs_path: Path, *, label: str = "write", state_root: Path | None = None) -> None:
    lock_path = lock_file_path(abs_path, state_root)
    owner = _read_lock_owner(lock_path)
    if owner.startswith(_OWNER):
        _write_lock_payload(lock_path, label)


def try_acquire_cross_process_lock(abs_path: Path, label: str = "write", state_root: Path | None = None) -> dict:
    key = _canonical_lock_key(abs_path)
    thread_id = threading.get_ident()
    with _PENDING_GUARD:
        if key in _PENDING_PATHS:
            return {"ok": False, "holder": _OWNER, "scope": "in_process"}
        _PENDING_PATHS[key] = thread_id

    lock_path = lock_file_path(abs_path, state_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = f"{_OWNER}\n{label}\n{datetime.now(tz=timezone.utc).isoformat()}\n"
    acquired = False
    try:
        while True:
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    os.write(fd, payload.encode("utf-8"))
                finally:
                    os.close(fd)
                acquired = True
                return {"ok": True, "lockPath": str(lock_path)}
            except FileExistsError:
                if not _is_stale_lock(lock_path):
                    return {"ok": False, "holder": _read_lock_owner(lock_path), "scope": "cross_process"}
                try:
                    lock_path.unlink(missing_ok=True)
                except OSError:
                    return {"ok": False, "holder": _read_lock_owner(lock_path), "scope": "cross_process"}
            except OSError as exc:
                return {"ok": False, "error": str(exc)}
    finally:
        if not acquired:
            with _PENDING_GUARD:
                if _PENDING_PATHS.get(key) == thread_id:
                    _PENDING_PATHS.pop(key, None)


def release_cross_process_lock(abs_path: Path, state_root: Path | None = None) -> None:
    key = _canonical_lock_key(abs_path)
    thread_id = threading.get_ident()
    with _PENDING_GUARD:
        if _PENDING_PATHS.get(key) != thread_id:
            return
        _PENDING_PATHS.pop(key, None)

    lock_path = lock_file_path(abs_path, state_root)
    try:
        owner = _read_lock_owner(lock_path)
        if owner.startswith(_OWNER):
            lock_path.unlink(missing_ok=True)
    except OSError:
        pass


class cross_process_lock:
    """Context manager with optional heartbeat for long-held locks."""

    def __init__(self, abs_path: Path, *, label: str = "write", heartbeat: bool = False) -> None:
        self.abs_path = abs_path
        self.label = label
        self.heartbeat = heartbeat
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> dict:
        acquired = try_acquire_cross_process_lock(self.abs_path, self.label)
        if not acquired.get("ok"):
            raise RuntimeError(acquired.get("error") or f"lock busy: {acquired.get('holder')}")
        if self.heartbeat:
            self._thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
            self._thread.start()
        return acquired

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(_HEARTBEAT_INTERVAL_SEC):
            refresh_lock_heartbeat(self.abs_path, label=self.label)

    def __exit__(self, *_args) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        release_cross_process_lock(self.abs_path)
