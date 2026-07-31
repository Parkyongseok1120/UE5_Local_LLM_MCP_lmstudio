#!/usr/bin/env python
"""Per-process MCP connection identity for route ownership."""

from __future__ import annotations

import os
import uuid

_CONNECTION_ID: str | None = None


def get_mcp_connection_id() -> str:
    """Return a stable id for this MCP server process/connection."""
    global _CONNECTION_ID
    if _CONNECTION_ID:
        return _CONNECTION_ID
    env_value = str(os.environ.get("MCP_CONNECTION_ID") or "").strip()
    if env_value:
        _CONNECTION_ID = env_value
        return _CONNECTION_ID
    _CONNECTION_ID = f"mcp-{os.getpid()}-{uuid.uuid4().hex[:12]}"
    return _CONNECTION_ID


def task_owns_active_tool_route(state: dict | None) -> bool:
    """True when a running write-enabled task may own tools/list filtering.

    Read-only / plan_only tasks never own global tool exposure.
    Tasks from another MCP connection (or legacy tasks without a connection id)
    also do not own exposure, so a previous chat cannot permanently shrink tools.
    """
    if not isinstance(state, dict):
        return False
    if str(state.get("status") or "") != "running":
        return False
    write_gate = state.get("writeGate") if isinstance(state.get("writeGate"), dict) else {}
    writes_allowed = (
        write_gate.get("writesAllowed") is True
        or state.get("writesAllowed") is True
    )
    if not writes_allowed:
        return False
    task_connection = str(state.get("mcpConnectionId") or "").strip()
    if not task_connection:
        return False
    return task_connection == get_mcp_connection_id()
