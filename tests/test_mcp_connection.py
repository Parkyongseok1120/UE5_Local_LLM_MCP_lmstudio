from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from mcp_connection import get_mcp_connection_id, task_owns_active_tool_route  # noqa: E402


def test_task_owns_active_tool_route_requires_write_and_same_connection() -> None:
    connection = get_mcp_connection_id()
    assert task_owns_active_tool_route(
        {
            "status": "running",
            "writesAllowed": True,
            "writeGate": {"writesAllowed": True},
            "mcpConnectionId": connection,
        }
    )
    assert not task_owns_active_tool_route(
        {
            "status": "running",
            "writesAllowed": False,
            "writeGate": {"writesAllowed": False},
            "mcpConnectionId": connection,
        }
    )
    assert not task_owns_active_tool_route(
        {
            "status": "running",
            "writesAllowed": True,
            "writeGate": {"writesAllowed": True},
            "mcpConnectionId": "other-connection",
        }
    )
    assert not task_owns_active_tool_route(
        {
            "status": "running",
            "writesAllowed": True,
            "writeGate": {"writesAllowed": True},
        }
    )
