from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _reload_mcp(monkeypatch: pytest.MonkeyPatch, **env: str | None):
    for key in (
        "MCP_SESSION_ID",
        "MCP_CONNECTION_ID",
        "MCP_BRIDGE_PAIR_ID",
        "MCP_CLIENT_INSTANCE_ID",
        "AGENT_STATE_ROOT",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    import mcp_connection

    return importlib.reload(mcp_connection)


def test_task_owns_active_tool_route_requires_running_same_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mcp = _reload_mcp(monkeypatch, MCP_CONNECTION_ID="pytest-connection-owner")
    connection = mcp.get_mcp_connection_id()
    assert mcp.task_owns_active_tool_route(
        {
            "status": "running",
            "mode": "agent_edit",
            "writesAllowed": True,
            "writeGate": {"writesAllowed": True},
            "mcpConnectionId": connection,
        }
    )
    # Write permission is separate from route ownership.
    assert mcp.task_owns_active_tool_route(
        {
            "status": "running",
            "mode": "agent_edit",
            "writesAllowed": False,
            "writeGate": {"writesAllowed": False},
            "mcpConnectionId": connection,
        }
    )
    assert not mcp.task_owns_active_tool_route(
        {
            "status": "running",
            "mode": "plan_only",
            "writesAllowed": False,
            "writeGate": {"writesAllowed": False},
            "mcpConnectionId": connection,
        }
    )
    assert not mcp.task_owns_active_tool_route(
        {
            "status": "running",
            "mode": "agent_edit",
            "writesAllowed": True,
            "writeGate": {"writesAllowed": True},
            "mcpConnectionId": "other-connection",
        }
    )
    assert not mcp.task_owns_active_tool_route(
        {
            "status": "running",
            "mode": "agent_edit",
            "writesAllowed": True,
            "writeGate": {"writesAllowed": True},
        }
    )


def test_session_id_preferred_over_connection_and_bridge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bridge = tmp_path / "mcp-bridge-pair.id"
    bridge.write_text("install-wide-bridge-id-aaaa", encoding="utf-8")
    mcp = _reload_mcp(
        monkeypatch,
        AGENT_STATE_ROOT=str(tmp_path),
        MCP_BRIDGE_PAIR_ID="install-wide-bridge-id-aaaa",
        MCP_CONNECTION_ID="should-not-win",
        MCP_SESSION_ID="chat-session-xyz",
    )
    owner = mcp.get_mcp_connection_id()
    assert owner == "install-wide-bridge-id-aaaa:chat-session-xyz"
    assert owner != "should-not-win"
    assert owner != "install-wide-bridge-id-aaaa"


def test_default_owner_is_bridge_plus_shared_client_instance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bridge = tmp_path / "mcp-bridge-pair.id"
    bridge.write_text("install-wide-bridge-id-bbbb", encoding="utf-8")
    mcp = _reload_mcp(
        monkeypatch,
        AGENT_STATE_ROOT=str(tmp_path),
        MCP_BRIDGE_PAIR_ID="install-wide-bridge-id-bbbb",
    )
    owner = mcp.get_mcp_connection_id()
    instance = mcp.get_mcp_client_instance_id()
    assert owner == f"install-wide-bridge-id-bbbb:{instance}"
    assert owner != "install-wide-bridge-id-bbbb"
    # Simulated second MCP process reload shares the same client instance file.
    mcp2 = _reload_mcp(
        monkeypatch,
        AGENT_STATE_ROOT=str(tmp_path),
        MCP_BRIDGE_PAIR_ID="install-wide-bridge-id-bbbb",
    )
    assert mcp2.get_mcp_connection_id() == owner


def test_empty_bridge_file_is_atomically_repaired(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bridge = tmp_path / "mcp-bridge-pair.id"
    bridge.write_text("   \n", encoding="utf-8")
    mcp = _reload_mcp(monkeypatch, AGENT_STATE_ROOT=str(tmp_path))
    pair = mcp.get_mcp_bridge_pair_id()
    assert mcp._valid_bridge_id(pair)
    assert bridge.read_text(encoding="utf-8").strip() == pair


def test_legacy_bridge_file_migrates_to_pair_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    legacy = tmp_path / "mcp-bridge-connection.id"
    legacy.write_text("legacy-bridge-id-ccccccc", encoding="utf-8")
    mcp = _reload_mcp(monkeypatch, AGENT_STATE_ROOT=str(tmp_path))
    pair = mcp.get_mcp_bridge_pair_id()
    assert pair == "legacy-bridge-id-ccccccc"
    assert (tmp_path / "mcp-bridge-pair.id").read_text(encoding="utf-8").strip() == pair
