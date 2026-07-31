from __future__ import annotations

import importlib
import json
import sys
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
        "MCP_HOST_PID",
        "AGENT_STATE_ROOT",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    import mcp_boot_instance
    import mcp_connection

    importlib.reload(mcp_boot_instance)
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


def test_default_owner_uses_host_boot_instance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bridge = tmp_path / "mcp-bridge-pair.id"
    bridge.write_text("install-wide-bridge-id-bbbb", encoding="utf-8")
    plain = tmp_path / "mcp-client-instance.id"
    plain.write_text("permanent-should-be-ignored", encoding="utf-8")
    mcp = _reload_mcp(
        monkeypatch,
        AGENT_STATE_ROOT=str(tmp_path),
        MCP_BRIDGE_PAIR_ID="install-wide-bridge-id-bbbb",
        MCP_HOST_PID="424242",
        MCP_CLIENT_INSTANCE_LEASE_SEC="not-a-number",
    )
    assert mcp._parse_lease_sec() == 120
    owner = mcp.get_mcp_connection_id()
    instance = mcp.get_mcp_client_instance_id()
    assert owner == f"install-wide-bridge-id-bbbb:{instance}"
    assert instance.startswith("mcp-boot-424242-")
    boot_path = tmp_path / "runtime" / "boot-424242.json"
    assert boot_path.is_file()
    boot = json.loads(boot_path.read_text(encoding="utf-8"))
    assert boot["clientInstanceId"] == instance
    assert boot["hostPid"] == 424242
    assert plain.read_text(encoding="utf-8").strip() == "permanent-should-be-ignored"


def test_distinct_host_pids_get_distinct_boot_instances(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bridge = tmp_path / "mcp-bridge-pair.id"
    bridge.write_text("install-wide-bridge-id-dddd", encoding="utf-8")
    first = _reload_mcp(
        monkeypatch,
        AGENT_STATE_ROOT=str(tmp_path),
        MCP_BRIDGE_PAIR_ID="install-wide-bridge-id-dddd",
        MCP_HOST_PID="111111",
    )
    id_a = first.get_mcp_client_instance_id()
    second = _reload_mcp(
        monkeypatch,
        AGENT_STATE_ROOT=str(tmp_path),
        MCP_BRIDGE_PAIR_ID="install-wide-bridge-id-dddd",
        MCP_HOST_PID="222222",
    )
    id_b = second.get_mcp_client_instance_id()
    assert id_a != id_b
    assert id_a.startswith("mcp-boot-111111-")
    assert id_b.startswith("mcp-boot-222222-")


def test_same_host_pid_reuses_boot_instance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    first = _reload_mcp(
        monkeypatch,
        AGENT_STATE_ROOT=str(tmp_path),
        MCP_BRIDGE_PAIR_ID="install-wide-bridge-id-eeee",
        MCP_HOST_PID="333333",
    )
    id_a = first.get_mcp_client_instance_id()
    second = _reload_mcp(
        monkeypatch,
        AGENT_STATE_ROOT=str(tmp_path),
        MCP_BRIDGE_PAIR_ID="install-wide-bridge-id-eeee",
        MCP_HOST_PID="333333",
    )
    id_b = second.get_mcp_client_instance_id()
    assert id_a == id_b


def test_empty_bridge_file_is_atomically_repaired(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bridge = tmp_path / "mcp-bridge-pair.id"
    bridge.write_text("   \n", encoding="utf-8")
    mcp = _reload_mcp(monkeypatch, AGENT_STATE_ROOT=str(tmp_path), MCP_HOST_PID="555555")
    pair = mcp.get_mcp_bridge_pair_id()
    assert mcp._valid_bridge_id(pair)
    assert bridge.read_text(encoding="utf-8").strip() == pair
