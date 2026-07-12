#!/usr/bin/env python
"""Cross-process file locks compatible with lmstudio-unreal-agent-mcp write-locks.js."""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from state_root import ensure_state_root_layout, resolve_agent_state_root

_OWNER = f"{os.getpid()}:{uuid.uuid4().hex}"
_STALE_LOCK_AGE_SEC = 300.0


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


def _process_alive(pid: int) -> bool:
    from process_probe import ProbeTimeout, run_probe

    if pid <= 0:
        return False
    if os.name == "nt":
        result = run_probe(["tasklist", "/FI", f"PID eq {pid}"])
        if isinstance(result, ProbeTimeout):
            return False
        return str(pid) in (result.stdout or "")
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


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
    if not _process_alive(pid):
        return True
    try:
        age = datetime.now(tz=timezone.utc).timestamp() - lock_path.stat().st_mtime
        return age > _STALE_LOCK_AGE_SEC
    except OSError:
        return True


def try_acquire_cross_process_lock(abs_path: Path, label: str = "write", state_root: Path | None = None) -> dict:
    lock_path = lock_file_path(abs_path, state_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = f"{_OWNER}\n{label}\n{datetime.now(tz=timezone.utc).isoformat()}\n"
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, payload.encode("utf-8"))
        finally:
            os.close(fd)
        return {"ok": True, "lockPath": str(lock_path)}
    except FileExistsError:
        if _is_stale_lock(lock_path):
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                return {"ok": False, "holder": _read_lock_owner(lock_path), "scope": "cross_process"}
            return try_acquire_cross_process_lock(abs_path, label, state_root)
        return {"ok": False, "holder": _read_lock_owner(lock_path), "scope": "cross_process"}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}


def release_cross_process_lock(abs_path: Path, state_root: Path | None = None) -> None:
    lock_path = lock_file_path(abs_path, state_root)
    try:
        owner = _read_lock_owner(lock_path)
        if owner.startswith(_OWNER):
            lock_path.unlink(missing_ok=True)
    except OSError:
        pass
