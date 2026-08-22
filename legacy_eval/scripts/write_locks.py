#!/usr/bin/env python
"""Archived Python cross-process locks from the pre-Direct runtime."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from process_probe import (
    ProcessAlive,
    probe_process_alive,
    probe_process_start_identity,
)
from state_root import ensure_state_root_layout, resolve_agent_state_root
from workspace_paths import canonical_absolute_path_identity

_OWNER = f"{os.getpid()}:{uuid.uuid4().hex}"
_PROCESS_IDENTITY = probe_process_start_identity(os.getpid())
_HEARTBEAT_INTERVAL_SEC = 60.0
_PENDING_GUARD = threading.Lock()
_PENDING_PATHS: dict[str, int] = {}


def _canonical_lock_key(abs_path: Path, host_platform: str | None = None) -> str:
    return canonical_absolute_path_identity(abs_path, host_platform)


def lock_file_path(abs_path: Path, state_root: Path | None = None) -> Path:
    root = ensure_state_root_layout(state_root or resolve_agent_state_root())
    digest = hashlib.sha256(_canonical_lock_key(abs_path).encode("utf-8")).hexdigest()
    return root / "locks" / f"{digest}.lock"


def _read_lock_owner(lock_path: Path) -> str:
    try:
        return lock_path.read_text(encoding="utf-8").splitlines()[0].strip()
    except OSError:
        return ""


def _read_lock_process_identity(lock_path: Path) -> str:
    try:
        for line in lock_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("processIdentity:"):
                return line.partition(":")[2].strip()
    except OSError:
        pass
    return ""


def _stale_reclaim_guard_path(lock_path: Path) -> Path:
    return lock_path.parent / "stale-reclaim.sqlite3"


def _open_reclaim_database(lock_path: Path) -> sqlite3.Connection:
    database_path = _stale_reclaim_guard_path(lock_path)
    connection = sqlite3.connect(database_path, timeout=2.0, isolation_level=None)
    connection.execute("PRAGMA busy_timeout = 2000")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS reclaim_guards (
            lock_path TEXT PRIMARY KEY,
            owner TEXT NOT NULL,
            pid INTEGER NOT NULL,
            process_identity TEXT NOT NULL DEFAULT '',
            acquired_at TEXT NOT NULL
        )
        """
    )
    columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(reclaim_guards)")
    }
    if "process_identity" not in columns:
        try:
            connection.execute(
                "ALTER TABLE reclaim_guards ADD COLUMN "
                "process_identity TEXT NOT NULL DEFAULT ''"
            )
        except sqlite3.OperationalError:
            refreshed = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(reclaim_guards)")
            }
            if "process_identity" not in refreshed:
                raise
    return connection


def _try_acquire_stale_reclaim_guard(lock_path: Path) -> tuple[Path | None, str]:
    connection: sqlite3.Connection | None = None
    lock_key = lock_path.name
    try:
        connection = _open_reclaim_database(lock_path)
        observed = connection.execute(
            "SELECT owner, pid, process_identity FROM reclaim_guards WHERE lock_path = ?",
            (lock_key,),
        ).fetchone()
        observed_reclaimable = False
        if observed is not None and str(observed[0]) != _OWNER:
            observed_pid = int(observed[1])
            observed_identity = str(observed[2] or "")
            current_identity = (
                probe_process_start_identity(observed_pid)
                if observed_identity
                else ""
            )
            observed_reclaimable = bool(
                _process_alive(observed_pid) == "dead"
                or (
                    observed_identity
                    and current_identity
                    and current_identity != observed_identity
                )
            )
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT owner, pid, process_identity FROM reclaim_guards WHERE lock_path = ?",
            (lock_key,),
        ).fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO reclaim_guards("
                "lock_path, owner, pid, process_identity, acquired_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    lock_key,
                    _OWNER,
                    os.getpid(),
                    _PROCESS_IDENTITY,
                    datetime.now(tz=timezone.utc).isoformat(),
                ),
            )
        elif str(row[0]) == _OWNER:
            pass
        elif (
            observed is not None
            and tuple(row) == tuple(observed)
            and observed_reclaimable
        ):
            connection.execute(
                "UPDATE reclaim_guards SET owner = ?, pid = ?, process_identity = ?, "
                "acquired_at = ? "
                "WHERE lock_path = ? AND owner = ?",
                (
                    _OWNER,
                    os.getpid(),
                    _PROCESS_IDENTITY,
                    datetime.now(tz=timezone.utc).isoformat(),
                    lock_key,
                    str(row[0]),
                ),
            )
        else:
            connection.execute("ROLLBACK")
            return None, "stale_reclaim_in_progress"
        connection.execute("COMMIT")
        return lock_path, ""
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        if connection is not None:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
        return None, str(exc)
    finally:
        if connection is not None:
            connection.close()


def _release_stale_reclaim_guard(guard_path: Path) -> None:
    for attempt in range(3):
        connection: sqlite3.Connection | None = None
        try:
            connection = _open_reclaim_database(guard_path)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM reclaim_guards WHERE lock_path = ? AND owner = ?",
                (guard_path.name, _OWNER),
            )
            connection.execute("COMMIT")
            return
        except (OSError, sqlite3.Error):
            if connection is not None:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            if attempt < 2:
                time.sleep(0.05)
        finally:
            if connection is not None:
                connection.close()


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
    recorded_identity = _read_lock_process_identity(lock_path)
    current_identity = (
        probe_process_start_identity(pid) if recorded_identity else ""
    )
    if (
        recorded_identity
        and current_identity
        and recorded_identity != current_identity
    ):
        return True
    if alive == "unknown":
        return False
    return False


def _write_lock_payload(lock_path: Path, label: str) -> None:
    payload = (
        f"{_OWNER}\n{label}\n{datetime.now(tz=timezone.utc).isoformat()}\n"
        f"processIdentity:{_PROCESS_IDENTITY}\n"
    )
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
    payload = (
        f"{_OWNER}\n{label}\n{datetime.now(tz=timezone.utc).isoformat()}\n"
        f"processIdentity:{_PROCESS_IDENTITY}\n"
    )
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
                guard_path, guard_error = _try_acquire_stale_reclaim_guard(lock_path)
                if guard_path is None:
                    return {
                        "ok": False,
                        "holder": guard_error or _read_lock_owner(lock_path),
                        "scope": "cross_process",
                    }
                try:
                    # Re-check only while exclusively owning stale recovery.
                    if not _is_stale_lock(lock_path):
                        return {
                            "ok": False,
                            "holder": _read_lock_owner(lock_path),
                            "scope": "cross_process",
                        }
                    lock_path.unlink(missing_ok=True)
                    fd = os.open(
                        str(lock_path),
                        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    )
                    try:
                        os.write(fd, payload.encode("utf-8"))
                    finally:
                        os.close(fd)
                    acquired = True
                    return {
                        "ok": True,
                        "lockPath": str(lock_path),
                        "staleReclaimed": True,
                    }
                except OSError as exc:
                    return {
                        "ok": False,
                        "holder": _read_lock_owner(lock_path),
                        "scope": "cross_process",
                        "error": str(exc),
                    }
                finally:
                    _release_stale_reclaim_guard(guard_path)
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
