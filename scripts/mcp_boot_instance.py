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
_WINDOWS_PROCESS_SNAPSHOT: dict[int, dict[str, Any]] | None = None


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _utc_iso(dt: datetime | None = None) -> str:
    return (dt or _utc_now()).isoformat()


def _valid_id(value: str) -> bool:
    return bool(value and _ID_RE.match(value))


def _windows_process_snapshot() -> dict[int, dict[str, Any]]:
    """Return one bounded Windows process snapshot for all identity lookups."""

    global _WINDOWS_PROCESS_SNAPSHOT
    if sys.platform != "win32":
        return {}
    if _WINDOWS_PROCESS_SNAPSHOT is not None:
        return _WINDOWS_PROCESS_SNAPSHOT
    snapshot: dict[int, dict[str, Any]] = {}
    try:
        from process_probe import ProbeTimeout, run_probe

        script = " ".join(
            (
                "Get-CimInstance Win32_Process | ForEach-Object {",
                "[pscustomobject]@{",
                "ProcessId=[int]$_.ProcessId;",
                "ParentProcessId=[int]$_.ParentProcessId;",
                "Name=[string]$_.Name;",
                "StartedAt=if ($_.CreationDate) {$_.CreationDate.ToUniversalTime().ToString('o')} else {''}",
                "}",
                "} | ConvertTo-Json -Compress",
            )
        )
        result = run_probe(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            timeout_sec=2.5,
        )
        if not isinstance(result, ProbeTimeout) and result.returncode == 0:
            parsed = json.loads(str(result.stdout or "null"))
            for row in parsed if isinstance(parsed, list) else [parsed]:
                if not isinstance(row, dict):
                    continue
                pid = int(row.get("ProcessId") or 0)
                if pid > 0:
                    snapshot[pid] = {
                        "parentPid": int(row.get("ParentProcessId") or 0),
                        "name": str(row.get("Name") or ""),
                        "startedAt": str(row.get("StartedAt") or ""),
                    }
    except Exception:
        # Empty snapshot intentionally triggers the direct-parent fallback.
        pass
    _WINDOWS_PROCESS_SNAPSHOT = snapshot
    return snapshot


def _pid_alive(pid: int) -> str:
    if pid <= 0:
        return "dead"
    try:
        if sys.platform == "win32":
            snapshot = _windows_process_snapshot()
            if snapshot:
                return "alive" if pid in snapshot else "dead"
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
            return str(_windows_process_snapshot().get(pid, {}).get("name") or "")
        if Path(f"/proc/{pid}/comm").is_file():
            return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
        from process_probe import ProbeTimeout, run_probe

        result = run_probe(["ps", "-o", "comm=", "-p", str(pid)])
        if isinstance(result, ProbeTimeout) or result.returncode != 0:
            return ""
        return str((result.stdout or "").strip())
    except Exception:
        return ""


def _process_start_ticks(pid: int) -> int | None:
    """Return Linux starttime ticks for pid, or None when unavailable."""

    if pid <= 0 or sys.platform == "win32":
        return None
    try:
        if Path(f"/proc/{pid}/stat").is_file():
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            close = stat.rfind(")")
            fields = stat[close + 1 :].split() if close >= 0 else stat.split()
            return int(fields[19])
    except Exception:
        return None
    return None


def _process_started_at(pid: int) -> str:
    """Return a stable start identity string for pid."""

    if pid <= 0:
        return ""
    try:
        if sys.platform == "win32":
            return str(_windows_process_snapshot().get(pid, {}).get("startedAt") or "")
        ticks = _process_start_ticks(pid)
        if ticks is not None:
            return f"ticks:{ticks}"
        from process_probe import ProbeTimeout, run_probe

        result = run_probe(["ps", "-o", "lstart=", "-p", str(pid)])
        if isinstance(result, ProbeTimeout) or result.returncode != 0:
            return ""
        return str((result.stdout or "").strip())
    except Exception:
        return ""


def _parent_pid(pid: int) -> int:
    if pid <= 0:
        return 0
    try:
        if sys.platform == "win32":
            return int(_windows_process_snapshot().get(pid, {}).get("parentPid") or 0)
        if Path(f"/proc/{pid}/stat").is_file():
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            close = stat.rfind(")")
            fields = stat[close + 1 :].split() if close >= 0 else stat.split()
            return int(fields[1])
        from process_probe import ProbeTimeout, run_probe

        result = run_probe(["ps", "-o", "ppid=", "-p", str(pid)])
        if isinstance(result, ProbeTimeout) or result.returncode != 0:
            return 0
        return int(str((result.stdout or "").strip() or "0"))
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


def _host_identity_matches(payload: dict[str, Any], *, host_pid: int) -> bool:
    if int(payload.get("hostPid") or 0) != host_pid:
        return False
    current_ticks = _process_start_ticks(host_pid)
    payload_ticks = payload.get("hostStartTicks")
    if current_ticks is not None and payload_ticks is not None:
        try:
            if int(payload_ticks) != int(current_ticks):
                return False
        except (TypeError, ValueError):
            return False
    else:
        current_started = _process_started_at(host_pid)
        payload_started = str(payload.get("hostStartedAt") or "").strip()
        if current_started and payload_started and current_started != payload_started:
            return False
    current_exe = _process_name(host_pid)
    payload_exe = str(payload.get("hostExecutable") or "").strip()
    if current_exe and payload_exe and current_exe.lower() != payload_exe.lower():
        return False
    return True


def resolve_or_create_boot_instance_id(state_root: Path) -> str:
    """Return a client instance id unique to the current host process identity."""

    explicit = str(os.environ.get("MCP_CLIENT_INSTANCE_ID") or "").strip()
    if _valid_id(explicit):
        return explicit

    host_pid = resolve_mcp_host_pid()
    host_pid_explicit = str(os.environ.get("MCP_HOST_PID") or "").strip().isdigit()
    runtime = Path(state_root) / "runtime"
    path = runtime / f"boot-{host_pid}.json"
    current_started = _process_started_at(host_pid)
    current_ticks = _process_start_ticks(host_pid)
    current_exe = _process_name(host_pid)

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
                    alive = _pid_alive(host_pid)
                    # Keep ID for the same host identity; do not rotate solely on TTL.
                    # TTL is only a hint for cleanup of abandoned files.
                    reusable = (
                        _valid_id(existing)
                        and _host_identity_matches(payload, host_pid=host_pid)
                        and (host_pid_explicit or alive == "alive")
                    )
                    if reusable:
                        payload["renewedAt"] = _utc_iso()
                        payload["expiresAt"] = _utc_iso(_utc_now() + timedelta(hours=12))
                        if current_started and not str(payload.get("hostStartedAt") or "").strip():
                            payload["hostStartedAt"] = current_started
                        if current_ticks is not None and payload.get("hostStartTicks") is None:
                            payload["hostStartTicks"] = current_ticks
                        if current_exe and not str(payload.get("hostExecutable") or "").strip():
                            payload["hostExecutable"] = current_exe
                        _atomic_write_json(path, payload)
                        return existing
            value = f"mcp-boot-{host_pid}-{uuid.uuid4().hex[:16]}"
            payload = {
                "clientInstanceId": value,
                "hostPid": host_pid,
                "hostStartedAt": current_started,
                "hostStartTicks": current_ticks,
                "hostExecutable": current_exe,
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
