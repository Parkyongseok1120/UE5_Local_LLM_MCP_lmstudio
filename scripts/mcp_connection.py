#!/usr/bin/env python
"""MCP bridge pair + client-session ownership for dual Python/Node servers."""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

_OWNER_ID: str | None = None
_BRIDGE_PAIR_ID: str | None = None
_LOCAL_SESSION_ID: str | None = None

_BRIDGE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")


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
    if root is None:
        return None
    return root / "mcp-bridge-pair.id"


def _legacy_bridge_connection_path() -> Path | None:
    root = _resolve_state_root()
    if root is None:
        return None
    return root / "mcp-bridge-connection.id"


def _valid_bridge_id(value: str) -> bool:
    return bool(value and _BRIDGE_ID_RE.match(value))


def _atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".tmp-{uuid.uuid4().hex[:8]}")
    temp.write_text(value, encoding="utf-8")
    temp.replace(path)


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
        try:
            if path.is_file():
                existing = path.read_text(encoding="utf-8").strip()
                if _valid_bridge_id(existing):
                    _BRIDGE_PAIR_ID = existing
                    return _BRIDGE_PAIR_ID
            elif legacy is not None and legacy.is_file():
                existing = legacy.read_text(encoding="utf-8").strip()
                if _valid_bridge_id(existing):
                    _atomic_write_text(path, existing)
                    _BRIDGE_PAIR_ID = existing
                    return _BRIDGE_PAIR_ID
            value = f"mcp-bridge-{uuid.uuid4().hex}"
            # Repair empty/invalid files with atomic replace.
            if path.is_file() or (legacy is not None and legacy.is_file()):
                _atomic_write_text(path, value)
                _BRIDGE_PAIR_ID = value
                return _BRIDGE_PAIR_ID
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            try:
                fd = os.open(str(path), flags, 0o644)
            except FileExistsError:
                existing = path.read_text(encoding="utf-8").strip()
                if _valid_bridge_id(existing):
                    _BRIDGE_PAIR_ID = existing
                    return _BRIDGE_PAIR_ID
                _atomic_write_text(path, value)
                _BRIDGE_PAIR_ID = value
                return _BRIDGE_PAIR_ID
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(value)
            _BRIDGE_PAIR_ID = value
            return _BRIDGE_PAIR_ID
        except OSError:
            pass
    _BRIDGE_PAIR_ID = f"mcp-bridge-local-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    return _BRIDGE_PAIR_ID


def get_mcp_client_session_id() -> str:
    """Chat/client session id. Prefer host-provided MCP_SESSION_ID."""
    global _LOCAL_SESSION_ID
    session = str(os.environ.get("MCP_SESSION_ID") or "").strip()
    if session:
        return session
    if _LOCAL_SESSION_ID:
        return _LOCAL_SESSION_ID
    _LOCAL_SESSION_ID = f"local-{os.getpid()}-{uuid.uuid4().hex[:12]}"
    return _LOCAL_SESSION_ID


def get_mcp_connection_id() -> str:
    """Return the ownership id for task route filtering.

    Preference:
    1. MCP_SESSION_ID → bridgePairId:clientSessionId (chat-scoped ownership)
    2. MCP_CONNECTION_ID (explicit test/advanced override only)
    3. process-local owner (Python/Node diverge; safe multi-chat default)
    """
    global _OWNER_ID
    if _OWNER_ID:
        return _OWNER_ID
    session = str(os.environ.get("MCP_SESSION_ID") or "").strip()
    if session:
        _OWNER_ID = f"{get_mcp_bridge_pair_id()}:{session}"
        return _OWNER_ID
    explicit = str(os.environ.get("MCP_CONNECTION_ID") or "").strip()
    if explicit:
        # Tests and advanced single-session setups may still pin this.
        _OWNER_ID = explicit
        return _OWNER_ID
    # Do NOT fall back to the install-wide bridge file for ownership.
    _OWNER_ID = f"mcp-local-{os.getpid()}-{uuid.uuid4().hex[:12]}"
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
