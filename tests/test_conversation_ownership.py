from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from mcp_connection import task_connection_matches  # noqa: E402
from task_api import (  # noqa: E402
    authorize_active_task_tool,
    task_list_active,
    task_retry_job_cancel,
    task_root,
    task_start,
)


def test_conversation_ids_isolate_task_ownership(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("MCP_BRIDGE_PAIR_ID", "bridge-conversation-test")
    monkeypatch.setenv("MCP_CLIENT_INSTANCE_ID", "client-conversation-test")
    monkeypatch.delenv("MCP_SESSION_ID", raising=False)
    monkeypatch.delenv("MCP_CONVERSATION_ID", raising=False)
    monkeypatch.delenv("MCP_CONNECTION_ID", raising=False)

    chat_a = task_start(
        tmp_path,
        request="chat a",
        conversation_id="conv-aaaa",
        start_background_job=False,
    )
    chat_b = task_start(
        tmp_path,
        request="chat b",
        conversation_id="conv-bbbb",
        start_background_job=False,
    )
    assert chat_a["ok"] is True
    assert chat_b["ok"] is True
    assert chat_a["state"]["conversationId"] == "conv-aaaa"
    assert chat_b["state"]["conversationId"] == "conv-bbbb"
    assert chat_a["state"]["mcpConnectionId"] != chat_b["state"]["mcpConnectionId"]
    cap_a = chat_a["taskAuthorization"]["ownerCapability"]
    cap_b = chat_b["taskAuthorization"]["ownerCapability"]
    assert cap_a and cap_b and cap_a != cap_b
    assert "ownerCapability" not in chat_a["state"]

    listed_a = task_list_active(tmp_path, owner_capability=cap_a)
    listed_b = task_list_active(tmp_path, owner_capability=cap_b)
    own_a = [t for t in listed_a["tasks"] if t.get("connectionMatches")]
    own_b = [t for t in listed_b["tasks"] if t.get("connectionMatches")]
    assert len(own_a) == 1
    assert own_a[0]["taskSessionId"] == chat_a["taskSessionId"]
    assert len(own_b) == 1
    assert own_b[0]["taskSessionId"] == chat_b["taskSessionId"]

    foreign_a = [t for t in listed_a["tasks"] if not t.get("connectionMatches")]
    assert foreign_a
    assert all("conversationId" not in t for t in foreign_a)
    assert all(t.get("mcpConnectionId") == "" for t in foreign_a)
    assert all("ownerCapability" not in t for t in listed_a["tasks"])

    # conversationId alone never proves ownership.
    listed_spoof = task_list_active(tmp_path, conversation_id="conv-aaaa")
    assert all(
        t.get("connectionMatches") is False
        for t in listed_spoof["tasks"]
        if t.get("status") == "running"
    )
    state_a = json.loads(
        (task_root(tmp_path, chat_a["taskSessionId"]) / "state.json").read_text(encoding="utf-8")
    )
    assert task_connection_matches(state_a, conversation_id="conv-aaaa") is False
    assert task_connection_matches(state_a, owner_capability=cap_a) is True

    # Without capability, conversation-scoped tasks do not match.
    listed_none = task_list_active(tmp_path)
    assert all(
        t.get("connectionMatches") is False
        for t in listed_none["tasks"]
        if t.get("status") == "running"
    )


def test_node_route_requires_capability_for_scoped_tasks(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Edit Source/Demo/Foo.cpp",
        conversation_id="conv-route-01",
        start_background_job=False,
    )
    active_tool = started["toolRoute"]["activeTools"][0]
    denied = authorize_active_task_tool(
        tmp_path,
        tool_name=active_tool,
        arguments={},
    )
    assert denied["ok"] is False
    assert denied["errorCode"] == "TASK_ROUTE_OWNERSHIP_REQUIRED"

    wrong = authorize_active_task_tool(
        tmp_path,
        tool_name=active_tool,
        arguments={"ownerCapability": "0" * 64},
    )
    assert wrong["ok"] is False
    assert wrong["errorCode"] == "TASK_ROUTE_CAPABILITY_MISMATCH"
    assert "ownerCapability" in str(wrong.get("nextAction") or "")

    allowed = authorize_active_task_tool(
        tmp_path,
        tool_name=active_tool,
        arguments={"taskAuthorization": started["taskAuthorization"]},
    )
    assert allowed["ok"] is True


def test_multi_chat_tools_list_exposes_route_union(tmp_path: Path, monkeypatch) -> None:
    from task_api import list_tools_route_context

    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("MCP_BRIDGE_PAIR_ID", "bridge-union-test")
    monkeypatch.setenv("MCP_CLIENT_INSTANCE_ID", "client-union-test")
    chat_a = task_start(
        tmp_path,
        request="Edit Source/Demo/Foo.cpp",
        conversation_id="conv-union-a",
        start_background_job=False,
    )
    chat_b = task_start(
        tmp_path,
        request="Edit Source/Demo/Bar.cpp",
        conversation_id="conv-union-b",
        start_background_job=False,
    )
    assert chat_a["ok"] and chat_b["ok"]
    context = list_tools_route_context(tmp_path)
    assert context["status"] == "ambiguous_or_corrupt"
    assert context["errorCode"] == "MULTIPLE_HEALTHY_ROUTE_TASKS"
    assert context.get("catalogMode") == "route_union"
    tools = set((context.get("state") or {}).get("toolRoute", {}).get("activeTools") or [])
    assert tools
    assert tools >= set(chat_a["toolRoute"]["activeTools"]) | set(
        chat_b["toolRoute"]["activeTools"]
    )


def test_corrupt_task_blocks_route_union_catalog(tmp_path: Path, monkeypatch) -> None:
    from task_api import list_tools_route_context, task_root

    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Edit Source/Demo/Foo.cpp",
        conversation_id="conv-healthy",
        start_background_job=False,
    )
    assert started["ok"] is True
    corrupt_dir = tmp_path / "state" / "tasks" / "corrupt_task_block"
    corrupt_dir.mkdir(parents=True)
    (corrupt_dir / "workspace-root.txt").write_text(
        str(tmp_path.resolve()), encoding="utf-8"
    )
    (corrupt_dir / "state.json").write_text("{", encoding="utf-8")
    context = list_tools_route_context(tmp_path)
    assert context["status"] == "ambiguous_or_corrupt"
    assert context["errorCode"] == "TASK_STATE_CORRUPT"
    assert context.get("catalogMode") != "route_union"
    # Healthy task still exists on disk.
    assert (task_root(tmp_path, started["taskSessionId"]) / "state.json").is_file()


def test_task_authorization_schema_accepts_owner_capability() -> None:
    import unreal_rag_mcp as rag

    schema = rag._task_authorization_schema()
    assert "ownerCapability" in schema["properties"]
    assert "ownerCapability" in schema["required"]
    assert "conversationId" in schema["properties"]
    assert schema.get("additionalProperties") is False


def test_capability_disables_legacy_connection_fallback(tmp_path: Path, monkeypatch) -> None:
    from mcp_connection import build_mcp_connection_id, task_connection_matches
    from task_api import active_task_route_context, authorize_active_task_tool

    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("MCP_BRIDGE_PAIR_ID", "bridge-legacy-cap")
    monkeypatch.setenv("MCP_CLIENT_INSTANCE_ID", "client-legacy-cap")
    scoped = task_start(
        tmp_path,
        request="scoped",
        conversation_id="conv-scoped-1",
        start_background_job=False,
    )
    assert scoped["ok"] is True
    cap = scoped["taskAuthorization"]["ownerCapability"]
    scoped_state = json.loads(
        (task_root(tmp_path, scoped["taskSessionId"]) / "state.json").read_text(encoding="utf-8")
    )

    legacy_id = "task_legacyfall01"
    legacy_dir = tmp_path / "state" / "tasks" / legacy_id
    legacy_dir.mkdir(parents=True)
    legacy_state = {
        "taskSessionId": legacy_id,
        "status": "running",
        "mode": "agent_edit",
        "workspaceRoot": str(tmp_path.resolve()),
        "routeScope": {
            "workspaceRoot": str(tmp_path.resolve()),
            "projectFile": "",
        },
        "mcpConnectionId": build_mcp_connection_id(),
        "toolRoute": dict(scoped["toolRoute"]),
        "writesAllowed": True,
        "writeGate": {"writesAllowed": True},
    }
    (legacy_dir / "state.json").write_text(json.dumps(legacy_state), encoding="utf-8")
    (legacy_dir / "workspace-root.txt").write_text(
        str(tmp_path.resolve()), encoding="utf-8"
    )

    assert task_connection_matches(legacy_state) is True
    assert task_connection_matches(legacy_state, owner_capability=cap) is False
    assert task_connection_matches(scoped_state, owner_capability=cap) is True

    # Without capability, both healthy routes are visible → multi.
    listed = active_task_route_context(tmp_path)
    assert listed["status"] == "ambiguous_or_corrupt"
    assert listed["errorCode"] == "MULTIPLE_HEALTHY_ROUTE_TASKS"

    # With capability, only the scoped task matches → authorize succeeds.
    allowed = authorize_active_task_tool(
        tmp_path,
        tool_name="read_file",
        arguments={"ownerCapability": cap},
    )
    assert allowed["ok"] is True
    assert allowed["taskSessionId"] == scoped["taskSessionId"]


def test_legacy_only_arbitrary_capability_fails_closed(tmp_path: Path, monkeypatch) -> None:
    from mcp_connection import build_mcp_connection_id, task_connection_matches
    from task_api import (
        active_task_route_context,
        authorize_active_task_tool,
    )

    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("MCP_BRIDGE_PAIR_ID", "bridge-legacy-only")
    monkeypatch.setenv("MCP_CLIENT_INSTANCE_ID", "client-legacy-only")

    started = task_start(
        tmp_path,
        request="legacy only",
        start_background_job=False,
    )
    assert started["ok"] is True
    legacy_id = str(started["taskSessionId"])
    state_path = task_root(tmp_path, legacy_id) / "state.json"
    legacy_state = json.loads(state_path.read_text(encoding="utf-8"))
    # Convert the started task into a connection-owned legacy task.
    legacy_state.pop("conversationId", None)
    legacy_state.pop("ownerCapability", None)
    legacy_state["mcpConnectionId"] = build_mcp_connection_id()
    state_path.write_text(json.dumps(legacy_state), encoding="utf-8")

    assert task_connection_matches(legacy_state) is True
    assert task_connection_matches(legacy_state, owner_capability="a" * 64) is False

    owned = active_task_route_context(
        tmp_path,
        require_owner_capability=True,
    )
    assert owned["status"] == "active"
    assert owned["state"]["taskSessionId"] == legacy_id

    # Capability was stripped from state; drop it from auth too for legacy path.
    auth = dict(started["taskAuthorization"])
    auth.pop("ownerCapability", None)
    auth.pop("conversationId", None)
    without_cap = authorize_active_task_tool(
        tmp_path,
        tool_name="read_file",
        arguments={"taskAuthorization": auth},
    )
    assert without_cap["ok"] is True
    assert without_cap["taskSessionId"] == legacy_id

    denied = authorize_active_task_tool(
        tmp_path,
        tool_name="read_file",
        arguments={"ownerCapability": "b" * 64},
    )
    assert denied["ok"] is False
    assert denied.get("legacy") is not True
    assert denied["errorCode"] == "TASK_ROUTE_CAPABILITY_MISMATCH"
    assert "ownerCapability" in str(denied.get("nextAction") or "")

    context = active_task_route_context(
        tmp_path,
        owner_capability="c" * 64,
        require_owner_capability=True,
    )
    assert context["status"] == "ambiguous_or_corrupt"
    assert context["errorCode"] == "TASK_ROUTE_CAPABILITY_MISMATCH"


def test_plan_only_running_task_ignored_for_capability_claimants(
    tmp_path: Path, monkeypatch
) -> None:
    from mcp_connection import build_mcp_connection_id
    from task_api import active_task_route_context, authorize_active_task_tool

    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("MCP_BRIDGE_PAIR_ID", "bridge-plan-only-claim")
    monkeypatch.setenv("MCP_CLIENT_INSTANCE_ID", "client-plan-only-claim")
    task_id = "task_planonly01"
    task_dir = tmp_path / "state" / "tasks" / task_id
    task_dir.mkdir(parents=True)
    state = {
        "taskSessionId": task_id,
        "status": "running",
        "mode": "plan_only",
        "workspaceRoot": str(tmp_path.resolve()),
        "routeScope": {
            "workspaceRoot": str(tmp_path.resolve()),
            "projectFile": "",
        },
        "conversationId": "conv-plan",
        "ownerCapability": "d" * 64,
        "mcpConnectionId": f"{build_mcp_connection_id()}:conv-plan",
        "toolRoute": {
            "status": "active",
            "routeHash": "route-plan",
            "phase": "planner",
            "activeTools": ["unreal_agent_plan"],
            "maxToolCallsPerPhase": 2,
        },
    }
    (task_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (task_dir / "workspace-root.txt").write_text(
        str(tmp_path.resolve()), encoding="utf-8"
    )
    # plan_only must not become a scoped claimant under CallTool authorize.
    context = active_task_route_context(
        tmp_path,
        owner_capability="e" * 64,
        require_owner_capability=True,
    )
    assert context["status"] == "none"
    authorized = authorize_active_task_tool(
        tmp_path,
        tool_name="read_file",
        arguments={"ownerCapability": "e" * 64},
    )
    assert authorized.get("legacy") is True
    assert authorized["ok"] is True


def test_state_root_unavailable_list_and_authorize(tmp_path: Path, monkeypatch) -> None:
    from task_api import authorize_active_task_tool, task_list_active

    bad_root = tmp_path / "not-a-dir"
    bad_root.write_text("file", encoding="utf-8")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(bad_root))

    listed = task_list_active(tmp_path)
    assert listed["ok"] is False
    assert listed["errorCode"] == "TASK_STATE_ROOT_UNAVAILABLE"
    assert listed["nextAction"] == "check_agent_state_root"

    denied = authorize_active_task_tool(tmp_path, tool_name="read_file", arguments={})
    assert denied["ok"] is False
    assert denied["errorCode"] == "TASK_STATE_ROOT_UNAVAILABLE"
    assert denied.get("legacy") is not True
    assert denied["nextAction"] == "check_agent_state_root"


def test_foreign_recover_returns_redacted_summary(tmp_path: Path, monkeypatch) -> None:
    from task_api import task_recover_active

    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="secret request text",
        conversation_id="conv-recover-a",
        start_background_job=False,
    )
    foreign = task_recover_active(
        tmp_path,
        task_session_id=started["taskSessionId"],
        conversation_id="conv-other",
        owner_capability="0" * 64,
    )
    assert foreign.get("foreign") is True
    assert foreign.get("leaseRenewed") is False
    assert "state" not in foreign
    discovered = foreign.get("discoveredTask") or {}
    assert "conversationId" not in discovered
    assert discovered.get("mcpConnectionId", "") == ""
    assert discovered.get("request", "") == ""
    assert "secret request" not in json.dumps(foreign)


def test_retry_cancel_syncs_uncertain_task(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="uncertain sync",
        conversation_id="conv-sync-01",
        start_background_job=False,
    )
    task_id = str(started["taskSessionId"])
    capability = started["taskAuthorization"]["ownerCapability"]
    state_path = task_root(tmp_path, task_id) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "cancellation_uncertain"
    state["terminalLogged"] = True
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = task_retry_job_cancel(
        tmp_path,
        task_session_id=task_id,
        owner_capability=capability,
    )
    assert result["ok"] is True
    assert result["nextAction"] == "unreal_agent_plan"
    synced = json.loads(state_path.read_text(encoding="utf-8"))
    assert synced["status"] == "cancelled"
    assert synced.get("orphanProcessSuspected") is False


def test_corrupt_task_owner_requires_force(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    task_id = "corrupt_owner_task01"
    task_dir = tmp_path / "state" / "tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "state.json").write_text("{", encoding="utf-8")
    (task_dir / "workspace-root.txt").write_text(str(tmp_path.resolve()), encoding="utf-8")

    denied = task_retry_job_cancel(tmp_path, task_session_id=task_id)
    assert denied["ok"] is False
    assert denied["errorCode"] == "TASK_OWNER_UNVERIFIABLE"
