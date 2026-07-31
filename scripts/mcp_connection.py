#!/usr/bin/env python
"""MCP bridge pair + ephemeral client-instance ownership for dual Python/Node servers."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_OWNER_ID: str | None = None
_BRIDGE_PAIR_ID: str | None = None
_CLIENT_INSTANCE_ID: str | None = None
_LOCAL_SESSION_ID: str | None = None

_BRIDGE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")
_CLIENT_INSTANCE_LEASE_SEC = max(30, int(os.environ.get("MCP_CLIENT_INSTANCE_LEASE_SEC") or "120"))
_LEGACY_PLAIN_INSTANCE = "mcp-client-instance.id"
_LEASE_FILE = "mcp-client-instance.lease.json"


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _utc_iso(dt: datetime | None = None) -> str:
    return (dt or _utc_now()).isoformat()


def _resolve_state_root() -> Path | None:
    override = str(os.environ.get("AGENT_STATE_ROOT") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    try:
        from state_root import resolve_agent_state_root

        return Path(resolve_agent_state_root()).resolve()
    except Exception:
        return None


def _bridge_connection_path() -> Path | None:
    root = _resolve_state_root()
    return (root / "mcp-bridge-pair.id") if root else None


def _legacy_bridge_connection_path() -> Path | None:
    root = _resolve_state_root()
    return (root / "mcp-bridge-connection.id") if root else None


def _client_lease_path() -> Path | None:
    root = _resolve_state_root()
    return (root / _LEASE_FILE) if root else None


def _legacy_client_instance_path() -> Path | None:
    root = _resolve_state_root()
    return (root / _LEGACY_PLAIN_INSTANCE) if root else None


def _valid_bridge_id(value: str) -> bool:
    return bool(value and _BRIDGE_ID_RE.match(value))


def _atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".tmp-{uuid.uuid4().hex[:8]}")
    temp.write_text(value, encoding="utf-8")
    temp.replace(path)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


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


def _locked_mutate(path: Path, *, label: str, mutator):
    from write_locks import release_cross_process_lock, try_acquire_cross_process_lock

    root = _resolve_state_root()
    deadline = time.time() + 5.0
    while time.time() < deadline:
        acquired = try_acquire_cross_process_lock(path, label=label, state_root=root)
        if not acquired.get("ok"):
            time.sleep(0.05)
            continue
        try:
            return mutator()
        finally:
            release_cross_process_lock(path, state_root=root)
    return None


def _locked_shared_id(
    path: Path,
    *,
    label: str,
    prefix: str,
    legacy: Path | None = None,
) -> str | None:
    def mutate() -> str | None:
        if path.is_file():
            existing = path.read_text(encoding="utf-8").strip()
            if _valid_bridge_id(existing):
                return existing
        elif legacy is not None and legacy.is_file():
            existing = legacy.read_text(encoding="utf-8").strip()
            if _valid_bridge_id(existing):
                _atomic_write_text(path, existing)
                return path.read_text(encoding="utf-8").strip()
        value = f"{prefix}{uuid.uuid4().hex}"
        _atomic_write_text(path, value)
        final = path.read_text(encoding="utf-8").strip()
        return final if _valid_bridge_id(final) else value

    return _locked_mutate(path, label=label, mutator=mutate)


def get_mcp_bridge_pair_id() -> str:
    """Install/process-pair id shared by Python and Node (not chat ownership)."""
    global _BRIDGE_PAIR_ID
    if _BRIDGE_PAIR_ID:
        return _BRIDGE_PAIR_ID
    for key in ("MCP_BRIDGE_PAIR_ID",):
        env_value = str(os.environ.get(key) or "").strip()
        if _valid_bridge_id(env_value):
            _BRIDGE_PAIR_ID = env_value
            return _BRIDGE_PAIR_ID
    path = _bridge_connection_path()
    legacy = _legacy_bridge_connection_path()
    if path is not None:
        if path.is_file():
            try:
                existing = path.read_text(encoding="utf-8").strip()
                if _valid_bridge_id(existing):
                    _BRIDGE_PAIR_ID = existing
                    return _BRIDGE_PAIR_ID
            except OSError:
                pass
        repaired = _locked_shared_id(
            path,
            label="mcp_bridge_pair",
            prefix="mcp-bridge-",
            legacy=legacy,
        )
        if repaired:
            _BRIDGE_PAIR_ID = repaired
            return _BRIDGE_PAIR_ID
    _BRIDGE_PAIR_ID = f"mcp-bridge-local-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    return _BRIDGE_PAIR_ID


def _lease_holders_alive(holders: list[Any]) -> bool:
    for raw in holders:
        try:
            pid = int(raw)
        except (TypeError, ValueError):
            continue
        if _pid_alive(pid) == "alive":
            return True
    return False


def _read_lease(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return raw if isinstance(raw, dict) else None


def _renew_or_rotate_client_lease(path: Path) -> str | None:
    """Ephemeral client-run lease. Rotates when expired and no holder PIDs are alive."""

    def mutate() -> str | None:
        now = _utc_now()
        legacy = _legacy_client_instance_path()
        current = _read_lease(path) if path.is_file() else None
        generation = 1
        reuse_id = ""
        if current:
            generation = max(1, int(current.get("generation") or 1))
            candidate = str(current.get("clientInstanceId") or "").strip()
            expires_raw = str(current.get("expiresAt") or "").strip()
            try:
                expires_at = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
            except ValueError:
                expires_at = now - timedelta(seconds=1)
            holders = list(current.get("holderPids") or [])
            if _valid_bridge_id(candidate) and (
                expires_at > now or _lease_holders_alive(holders)
            ):
                reuse_id = candidate
            else:
                generation += 1
        elif legacy is not None and legacy.is_file():
            # Migrate permanent plain-text id into a fresh lease generation (do not keep forever).
            try:
                legacy.unlink(missing_ok=True)
            except OSError:
                pass
            generation = 1

        client_id = reuse_id or f"mcp-client-{uuid.uuid4().hex}"
        holders = []
        if current and reuse_id:
            for raw in current.get("holderPids") or []:
                try:
                    pid = int(raw)
                except (TypeError, ValueError):
                    continue
                if _pid_alive(pid) == "alive" and pid not in holders:
                    holders.append(pid)
        if os.getpid() not in holders:
            holders.append(os.getpid())
        payload = {
            "clientInstanceId": client_id,
            "ownerPid": os.getpid(),
            "holderPids": holders[-8:],
            "createdAt": str((current or {}).get("createdAt") or _utc_iso(now)),
            "expiresAt": _utc_iso(now + timedelta(seconds=_CLIENT_INSTANCE_LEASE_SEC)),
            "generation": generation,
            "renewedAt": _utc_iso(now),
        }
        _atomic_write_json(path, payload)
        final = _read_lease(path) or payload
        value = str(final.get("clientInstanceId") or "").strip()
        return value if _valid_bridge_id(value) else client_id

    return _locked_mutate(path, label="mcp_client_instance_lease", mutator=mutate)


def get_mcp_client_instance_id() -> str:
    """Ephemeral client-run id shared by both MCP servers for one host execution."""
    global _CLIENT_INSTANCE_ID, _OWNER_ID
    env_value = str(os.environ.get("MCP_CLIENT_INSTANCE_ID") or "").strip()
    if _valid_bridge_id(env_value):
        _CLIENT_INSTANCE_ID = env_value
        return _CLIENT_INSTANCE_ID
    path = _client_lease_path()
    if path is not None:
        repaired = _renew_or_rotate_client_lease(path)
        if repaired:
            if _CLIENT_INSTANCE_ID and _CLIENT_INSTANCE_ID != repaired:
                _OWNER_ID = None
            _CLIENT_INSTANCE_ID = repaired
            return _CLIENT_INSTANCE_ID
    if not _CLIENT_INSTANCE_ID:
        _CLIENT_INSTANCE_ID = f"mcp-client-local-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    return _CLIENT_INSTANCE_ID


def get_mcp_conversation_id() -> str:
    """Chat/conversation scope. Prefer host-provided session or conversation id."""
    for key in ("MCP_SESSION_ID", "MCP_CONVERSATION_ID"):
        value = str(os.environ.get(key) or "").strip()
        if value:
            return value
    return ""


def get_mcp_client_session_id() -> str:
    """Chat/client session id. Prefer host-provided MCP_SESSION_ID."""
    global _LOCAL_SESSION_ID
    session = get_mcp_conversation_id()
    if session:
        return session
    if _LOCAL_SESSION_ID:
        return _LOCAL_SESSION_ID
    _LOCAL_SESSION_ID = f"local-{os.getpid()}-{uuid.uuid4().hex[:12]}"
    return _LOCAL_SESSION_ID


def get_mcp_connection_id() -> str:
    """Return the ownership id for task route filtering.

    Preference:
    1. MCP_SESSION_ID / MCP_CONVERSATION_ID → bridge:instance:conversation
    2. MCP_CONNECTION_ID (explicit test/advanced override only)
    3. bridge:instance from ephemeral lease (host-run pairing; not a permanent install id)
    """
    global _OWNER_ID
    if _OWNER_ID:
        return _OWNER_ID
    conversation = get_mcp_conversation_id()
    if conversation:
        _OWNER_ID = (
            f"{get_mcp_bridge_pair_id()}:{get_mcp_client_instance_id()}:{conversation}"
        )
        return _OWNER_ID
    explicit = str(os.environ.get("MCP_CONNECTION_ID") or "").strip()
    if explicit:
        _OWNER_ID = explicit
        return _OWNER_ID
    # Ephemeral host-run lease pairs Python/Node without a forever install-wide owner file.
    _OWNER_ID = f"{get_mcp_bridge_pair_id()}:{get_mcp_client_instance_id()}"
    return _OWNER_ID


def task_connection_matches(state: dict | None) -> bool:
    if not isinstance(state, dict):
        return False
    task_connection = str(state.get("mcpConnectionId") or "").strip()
    if not task_connection:
        return False
    return task_connection == get_mcp_connection_id()


def task_owns_active_tool_route(state: dict | None) -> bool:
    """True when a running task may own tools/list filtering."""
    if not isinstance(state, dict):
        return False
    if str(state.get("status") or "") != "running":
        return False
    mode = str(state.get("mode") or "").strip().lower()
    if mode in {"plan_only", "detached"}:
        return False
    return task_connection_matches(state)


def task_is_foreign_healthy(state: dict | None) -> bool:
    """True when another owner id holds a still-healthy running task."""
    if not isinstance(state, dict):
        return False
    if str(state.get("status") or "") != "running":
        return False
    if task_connection_matches(state):
        return False
    if not str(state.get("mcpConnectionId") or "").strip():
        return False
    continuity = state.get("continuity") if isinstance(state.get("continuity"), dict) else {}
    lease = continuity.get("lease") if isinstance(continuity.get("lease"), dict) else {}
    if lease:
        from task_continuity import lease_health

        if lease_health(continuity).get("active") is not True:
            return False
    recovery = continuity.get("recovery") if isinstance(continuity.get("recovery"), dict) else {}
    if recovery.get("conflicts"):
        return False
    supervisor = (
        state.get("autonomySupervisor")
        if isinstance(state.get("autonomySupervisor"), dict)
        else {}
    )
    if supervisor.get("blockers"):
        return False
    return True
