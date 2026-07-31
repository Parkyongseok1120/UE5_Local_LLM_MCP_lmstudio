#!/usr/bin/env python
"""MCP bridge pair + host-boot client-instance ownership for dual Python/Node servers."""

from __future__ import annotations

import os
import re
import time
import uuid
from pathlib import Path

_OWNER_ID: str | None = None
_BRIDGE_PAIR_ID: str | None = None
_CLIENT_INSTANCE_ID: str | None = None
_LOCAL_SESSION_ID: str | None = None

_BRIDGE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")


def _parse_lease_sec() -> int:
    raw = str(os.environ.get("MCP_CLIENT_INSTANCE_LEASE_SEC") or "120").strip()
    try:
        return max(30, int(raw))
    except ValueError:
        return 120


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


def _valid_bridge_id(value: str) -> bool:
    return bool(value and _BRIDGE_ID_RE.match(value))


def _atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".tmp-{uuid.uuid4().hex[:8]}")
    temp.write_text(value, encoding="utf-8")
    temp.replace(path)


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


def get_mcp_client_instance_id() -> str:
    """Ephemeral id for one host app run (LM Studio/Cline), shared by Python+Node."""
    global _CLIENT_INSTANCE_ID, _OWNER_ID
    env_value = str(os.environ.get("MCP_CLIENT_INSTANCE_ID") or "").strip()
    if _valid_bridge_id(env_value):
        _CLIENT_INSTANCE_ID = env_value
        return _CLIENT_INSTANCE_ID
    root = _resolve_state_root()
    if root is not None:
        try:
            from mcp_boot_instance import resolve_or_create_boot_instance_id

            repaired = resolve_or_create_boot_instance_id(root)
            if repaired:
                if _CLIENT_INSTANCE_ID and _CLIENT_INSTANCE_ID != repaired:
                    _OWNER_ID = None
                _CLIENT_INSTANCE_ID = repaired
                return _CLIENT_INSTANCE_ID
        except Exception:
            pass
    if not _CLIENT_INSTANCE_ID:
        _CLIENT_INSTANCE_ID = f"mcp-client-local-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    return _CLIENT_INSTANCE_ID


def get_mcp_conversation_id() -> str:
    for key in ("MCP_SESSION_ID", "MCP_CONVERSATION_ID"):
        value = str(os.environ.get(key) or "").strip()
        if value:
            return value
    return ""


def get_mcp_client_session_id() -> str:
    global _LOCAL_SESSION_ID
    session = get_mcp_conversation_id()
    if session:
        return session
    if _LOCAL_SESSION_ID:
        return _LOCAL_SESSION_ID
    _LOCAL_SESSION_ID = f"local-{os.getpid()}-{uuid.uuid4().hex[:12]}"
    return _LOCAL_SESSION_ID


def get_mcp_connection_id() -> str:
    """Ownership id for task route filtering.

    Preference:
    1. MCP_SESSION_ID / MCP_CONVERSATION_ID → bridge:instance:conversation
    2. MCP_CONNECTION_ID (explicit test override)
    3. bridge:hostBootInstance (pairs Python/Node for one host run only)
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
    if not isinstance(state, dict):
        return False
    if str(state.get("status") or "") != "running":
        return False
    mode = str(state.get("mode") or "").strip().lower()
    if mode in {"plan_only", "detached"}:
        return False
    return task_connection_matches(state)


def task_is_foreign_healthy(state: dict | None) -> bool:
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


# Keep import-time side effects free of brittle int() parsing.
_ = _parse_lease_sec()
