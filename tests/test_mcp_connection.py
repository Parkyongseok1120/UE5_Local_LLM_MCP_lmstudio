from __future__ import annotations

import importlib
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _reload_mcp(monkeypatch: pytest.MonkeyPatch, **env: str | None):
    for key in (
        "MCP_SESSION_ID",
        "MCP_CONVERSATION_ID",
        "MCP_CONNECTION_ID",
        "MCP_BRIDGE_PAIR_ID",
        "MCP_CLIENT_INSTANCE_ID",
        "MCP_CLIENT_INSTANCE_LEASE_SEC",
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
    assert not mcp.task_owns_active_tool_route(
        {
            "status": "running",
            "mode": "plan_only",
            "writesAllowed": False,
            "writeGate": {"writesAllowed": False},
            "mcpConnectionId": connection,
        }
    )


def test_session_id_scopes_owner_with_bridge_and_instance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bridge = tmp_path / "mcp-bridge-pair.id"
    bridge.write_text("install-wide-bridge-id-aaaa", encoding="utf-8")
    mcp = _reload_mcp(
        monkeypatch,
        AGENT_STATE_ROOT=str(tmp_path),
        MCP_BRIDGE_PAIR_ID="install-wide-bridge-id-aaaa",
        MCP_CLIENT_INSTANCE_ID="client-run-aaaaaaa",
        MCP_CONNECTION_ID="should-not-win",
        MCP_SESSION_ID="chat-session-xyz",
    )
    owner = mcp.get_mcp_connection_id()
    assert owner == "install-wide-bridge-id-aaaa:client-run-aaaaaaa:chat-session-xyz"


def test_default_owner_uses_ephemeral_lease_not_permanent_plain_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bridge = tmp_path / "mcp-bridge-pair.id"
    bridge.write_text("install-wide-bridge-id-bbbb", encoding="utf-8")
    plain = tmp_path / "mcp-client-instance.id"
    plain.write_text("permanent-should-be-rotated", encoding="utf-8")
    mcp = _reload_mcp(
        monkeypatch,
        AGENT_STATE_ROOT=str(tmp_path),
        MCP_BRIDGE_PAIR_ID="install-wide-bridge-id-bbbb",
        MCP_CLIENT_INSTANCE_LEASE_SEC="2",
    )
    owner = mcp.get_mcp_connection_id()
    instance = mcp.get_mcp_client_instance_id()
    assert owner == f"install-wide-bridge-id-bbbb:{instance}"
    assert "permanent-should-be-rotated" not in owner
    lease_path = tmp_path / "mcp-client-instance.lease.json"
    assert lease_path.is_file()
    lease = json.loads(lease_path.read_text(encoding="utf-8"))
    assert lease["clientInstanceId"] == instance
    assert "expiresAt" in lease
    assert "generation" in lease
    # Plain permanent file is removed/migrated away from ownership.
    assert not plain.exists() or plain.read_text(encoding="utf-8").strip() != instance


def test_expired_lease_without_holders_rotates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bridge = tmp_path / "mcp-bridge-pair.id"
    bridge.write_text("install-wide-bridge-id-cccc", encoding="utf-8")
    lease_path = tmp_path / "mcp-client-instance.lease.json"
    lease_path.write_text(
        json.dumps(
            {
                "clientInstanceId": "mcp-client-oldgeneration000",
                "ownerPid": 1,
                "holderPids": [1],
                "createdAt": "2020-01-01T00:00:00+00:00",
                "expiresAt": "2020-01-01T00:00:01+00:00",
                "generation": 3,
            }
        ),
        encoding="utf-8",
    )
    mcp = _reload_mcp(
        monkeypatch,
        AGENT_STATE_ROOT=str(tmp_path),
        MCP_BRIDGE_PAIR_ID="install-wide-bridge-id-cccc",
    )
    instance = mcp.get_mcp_client_instance_id()
    assert instance != "mcp-client-oldgeneration000"
    lease = json.loads(lease_path.read_text(encoding="utf-8"))
    assert lease["generation"] >= 4


def test_empty_bridge_file_is_atomically_repaired(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bridge = tmp_path / "mcp-bridge-pair.id"
    bridge.write_text("   \n", encoding="utf-8")
    mcp = _reload_mcp(monkeypatch, AGENT_STATE_ROOT=str(tmp_path))
    pair = mcp.get_mcp_bridge_pair_id()
    assert mcp._valid_bridge_id(pair)
    assert bridge.read_text(encoding="utf-8").strip() == pair
