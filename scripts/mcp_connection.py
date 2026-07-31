#!/usr/bin/env python
"""Shared MCP bridge identity for Python/Node route ownership."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

_CONNECTION_ID: str | None = None


def _bridge_connection_path() -> Path | None:
    root = str(os.environ.get("AGENT_STATE_ROOT") or "").strip()
    if not root:
        return None
    return Path(root).expanduser().resolve() / "mcp-bridge-connection.id"


def _read_or_create_bridge_id() -> str | None:
    path = _bridge_connection_path()
    if path is None:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
        value = f"mcp-bridge-{uuid.uuid4().hex}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            fd = os.open(str(path), flags, 0o644)
        except FileExistsError:
            value = path.read_text(encoding="utf-8").strip()
            return value or None
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
        return value
    except OSError:
        return None


def get_mcp_connection_id() -> str:
    """Return the shared host-session bridge id for this MCP pair.

    Preference order:
    1. MCP_CONNECTION_ID / MCP_SESSION_ID env (installer or test override)
    2. AGENT_STATE_ROOT/mcp-bridge-connection.id shared by Python + Node
    3. Process-local fallback (last resort; dual MCP will disagree)
    """
    global _CONNECTION_ID
    if _CONNECTION_ID:
        return _CONNECTION_ID
    for key in ("MCP_CONNECTION_ID", "MCP_SESSION_ID"):
        env_value = str(os.environ.get(key) or "").strip()
        if env_value:
            _CONNECTION_ID = env_value
            return _CONNECTION_ID
    bridge = _read_or_create_bridge_id()
    if bridge:
        _CONNECTION_ID = bridge
        return _CONNECTION_ID
    _CONNECTION_ID = f"mcp-{os.getpid()}-{uuid.uuid4().hex[:12]}"
    return _CONNECTION_ID


def task_connection_matches(state: dict | None) -> bool:
    if not isinstance(state, dict):
        return False
    task_connection = str(state.get("mcpConnectionId") or "").strip()
    if not task_connection:
        return False
    return task_connection == get_mcp_connection_id()


def task_owns_active_tool_route(state: dict | None) -> bool:
    """True when a running task may own tools/list filtering.

    Write permission is intentionally separate from route ownership.
    plan_only / detached modes never own the global tool route.
    Legacy tasks without a connection id also do not own exposure.
    """
    if not isinstance(state, dict):
        return False
    if str(state.get("status") or "") != "running":
        return False
    mode = str(state.get("mode") or "").strip().lower()
    if mode in {"plan_only", "detached"}:
        return False
    return task_connection_matches(state)


def task_is_foreign_healthy(state: dict | None) -> bool:
    """True when another connection owns a still-healthy running task."""
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
