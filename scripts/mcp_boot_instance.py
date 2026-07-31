#!/usr/bin/env python
"""Resolve an ephemeral MCP client-instance id scoped to the host process tree."""

from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _utc_iso(dt: datetime | None = None) -> str:
    return (dt or _utc_now()).isoformat()


def _valid_id(value: str) -> bool:
    return bool(value and _ID_RE.match(value))


def _pid_alive(pid: int) -> str:
    if pid <= 0:
        return "dead"
    try:
        from process_probe import probe_process_alive

        return str(probe_process_alive(pid) or "unknown")
    except Exception:
        try:
            os.kill(pid, 0)
            return "alive"
        except ProcessLookupError:
            return "dead"
        except OSError:
            return "unknown"


def _process_name(pid: int) -> str:
    if pid <= 0:
        return ""
    try:
        if sys.platform == "win32":
            from process_probe import ProbeTimeout, run_probe

            result = run_probe(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    (
                        f"$p = Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\"; "
                        "if ($null -eq $p) { exit 1 }; Write-Output $p.Name"
                    ),
                ],
            )
            if isinstance(result, ProbeTimeout) or result.returncode != 0:
                return ""
            return str((result.stdout or "").strip())
        path = Path(f"/proc/{pid}/comm")
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _parent_pid(pid: int) -> int:
    if pid <= 0:
        return 0
    try:
        if sys.platform == "win32":
            from process_probe import ProbeTimeout, run_probe

            result = run_probe(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    (
                        f"$p = Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\"; "
                        "if ($null -eq $p) { exit 1 }; Write-Output $p.ParentProcessId"
                    ),
                ],
            )
            if isinstance(result, ProbeTimeout) or result.returncode != 0:
                return 0
            return int(str((result.stdout or "").strip() or "0"))
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
        return int(stat[3])
    except Exception:
        return 0


def resolve_mcp_host_pid() -> int:
    raw = str(os.environ.get("MCP_HOST_PID") or "").strip()
    if raw.isdigit():
        return int(raw)
    markers = ("lm studio", "lmstudio", "cline", "cursor", "code")
    pid = os.getpid()
    seen: set[int] = set()
    for _ in range(8):
        if pid in seen or pid <= 0:
            break
        seen.add(pid)
        parent = _parent_pid(pid)
        if parent <= 0 or parent in seen:
            break
        name = _process_name(parent).lower()
        if any(marker in name for marker in markers):
            return parent
        pid = parent
    return os.getppid() if os.getppid() > 0 else os.getpid()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".tmp-{uuid.uuid4().hex[:8]}")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def resolve_or_create_boot_instance_id(state_root: Path) -> str:
    """Return a client instance id unique to the current host process tree."""

    explicit = str(os.environ.get("MCP_CLIENT_INSTANCE_ID") or "").strip()
    if _valid_id(explicit):
        return explicit

    host_pid = resolve_mcp_host_pid()
    host_pid_explicit = str(os.environ.get("MCP_HOST_PID") or "").strip().isdigit()
    runtime = Path(state_root) / "runtime"
    path = runtime / f"boot-{host_pid}.json"

    from write_locks import release_cross_process_lock, try_acquire_cross_process_lock

    deadline = time.time() + 5.0
    while time.time() < deadline:
        acquired = try_acquire_cross_process_lock(path, label="mcp_boot_instance", state_root=state_root)
        if not acquired.get("ok"):
            time.sleep(0.05)
            continue
        try:
            if path.is_file():
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError, TypeError):
                    payload = None
                if isinstance(payload, dict):
                    existing = str(payload.get("clientInstanceId") or "").strip()
                    payload_host = int(payload.get("hostPid") or 0)
                    expires_raw = str(payload.get("expiresAt") or "").strip()
                    expired = False
                    if expires_raw:
                        try:
                            expires_at = datetime.fromisoformat(expires_raw)
                            expired = expires_at <= _utc_now()
                        except ValueError:
                            expired = True
                    alive = _pid_alive(host_pid)
                    # Explicit MCP_HOST_PID is a launcher-scoped key: reuse until expiry.
                    # Inferred host PIDs require the host process to still be alive.
                    reusable = (
                        _valid_id(existing)
                        and payload_host == host_pid
                        and not expired
                        and (host_pid_explicit or alive == "alive")
                    )
                    if reusable:
                        payload["renewedAt"] = _utc_iso()
                        payload["expiresAt"] = _utc_iso(_utc_now() + timedelta(hours=12))
                        _atomic_write_json(path, payload)
                        return existing
            value = f"mcp-boot-{host_pid}-{uuid.uuid4().hex[:16]}"
            payload = {
                "clientInstanceId": value,
                "hostPid": host_pid,
                "createdAt": _utc_iso(),
                "renewedAt": _utc_iso(),
                "expiresAt": _utc_iso(_utc_now() + timedelta(hours=12)),
            }
            _atomic_write_json(path, payload)
            final = json.loads(path.read_text(encoding="utf-8"))
            resolved = str(final.get("clientInstanceId") or "").strip()
            return resolved if _valid_id(resolved) else value
        finally:
            release_cross_process_lock(path, state_root=state_root)
    return f"mcp-boot-local-{os.getpid()}-{uuid.uuid4().hex[:12]}"
