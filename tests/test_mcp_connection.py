from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from mcp_connection import (  # noqa: E402
    get_mcp_connection_id,
    task_owns_active_tool_route,
)


def test_task_owns_active_tool_route_requires_running_same_connection() -> None:
    connection = get_mcp_connection_id()
    assert task_owns_active_tool_route(
        {
            "status": "running",
            "mode": "agent_edit",
            "writesAllowed": True,
            "writeGate": {"writesAllowed": True},
            "mcpConnectionId": connection,
        }
    )
    # Write permission is separate from route ownership.
    assert task_owns_active_tool_route(
        {
            "status": "running",
            "mode": "agent_edit",
            "writesAllowed": False,
            "writeGate": {"writesAllowed": False},
            "mcpConnectionId": connection,
        }
    )
    assert not task_owns_active_tool_route(
        {
            "status": "running",
            "mode": "plan_only",
            "writesAllowed": False,
            "writeGate": {"writesAllowed": False},
            "mcpConnectionId": connection,
        }
    )
    assert not task_owns_active_tool_route(
        {
            "status": "running",
            "mode": "agent_edit",
            "writesAllowed": True,
            "writeGate": {"writesAllowed": True},
            "mcpConnectionId": "other-connection",
        }
    )
    assert not task_owns_active_tool_route(
        {
            "status": "running",
            "mode": "agent_edit",
            "writesAllowed": True,
            "writeGate": {"writesAllowed": True},
        }
    )
