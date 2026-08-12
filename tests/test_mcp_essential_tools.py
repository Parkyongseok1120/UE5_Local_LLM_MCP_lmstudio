#!/usr/bin/env python
"""Tests for MCP_ESSENTIAL_TOOLS filtering on unreal-rag MCP."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

RAG_ESSENTIAL = {
    "unreal_get_active_project",
    "unreal_set_active_project",
    "unreal_rag_health",
    "unreal_agent_plan",
    "unreal_rag_search",
    "unreal_symbol_lookup",
    "unreal_agent_session",
    "unreal_rag_capabilities",
    "unreal_architecture_reasoning",
    "unreal_feature_intent_resolve",
    "unreal_runtime_config_check",
    "unreal_runtime_debug_session",
    "unreal_runtime_verify",
    "unreal_code_sketch_claim_validate",
    "unreal_semantic_refactor_guard",
    "unreal_review_claim_validate",
    "unreal_diagram_validate",
    "unreal_project_status",
    "unreal_task_status",
    "unreal_task_list_active",
    "unreal_task_recover_active",
    "unreal_task_cancel_active",
    "unreal_task_quarantine_corrupt",
    "unreal_task_retry_job_cancel",
    "unreal_task_checkpoint",
    "unreal_task_define_slices",
    "unreal_task_resume",
    "unreal_task_cancel",
}

AGENT_ESSENTIAL = {
    "get_workspace_info",
    "get_active_project",
    "list_directory",
    "read_file",
    "read_file_range",
    "read_symbol",
    "replace_in_file",
    "apply_edit_bundle",
    "write_file",
    "search_files",
    "static_validate_project",
    "build_unreal_project",
    "run_unreal_automation_tests",
    "read_unreal_logs",
    "write_session_handoff",
    "record_bootstrap_step",
}


def _load_rag_mcp_module():
    path = SCRIPTS / "unreal_rag_mcp.py"
    spec = importlib.util.spec_from_file_location("unreal_rag_mcp", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_default_profile_is_fail_closed(monkeypatch, tmp_path):
    monkeypatch.delenv("MCP_ESSENTIAL_TOOLS", raising=False)
    monkeypatch.delenv("MCP_EXTENDED_TOOLS", raising=False)
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    names = {tool["name"] for tool in server.all_tool_definitions()}
    assert names == RAG_ESSENTIAL
    assert {
        "unreal_task_status",
        "unreal_task_checkpoint",
        "unreal_task_recover_active",
        "unreal_task_cancel",
    }.issubset(names)
    assert "clangd_goto_definition" not in names


def test_essential_tools_enabled_filters_rag_tools(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.delenv("MCP_EXTENDED_TOOLS", raising=False)
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    names = {tool["name"] for tool in server.all_tool_definitions()}
    assert names == set(mod.ESSENTIAL_TOOL_NAMES)
    assert names == RAG_ESSENTIAL
    assert "unreal_rag_refresh" not in names


def test_feature_intent_schema_is_compact_for_local_tool_calling(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    tool = next(
        item
        for item in server.all_tool_definitions()
        if item["name"] == "unreal_feature_intent_resolve"
    )
    schema = tool["inputSchema"]
    properties = schema["properties"]

    assert schema["required"] == ["taskAuthorization"]
    assert set(properties) == {
        "selectedIntentId",
        "selectionRationale",
        "blockingQuestionAnswers",
        "slices",
        "activeSliceId",
        "targetFiles",
        "taskAuthorization",
    }
    assert properties["slices"]["maxItems"] == 24
    assert properties["slices"]["items"]["additionalProperties"] is False
    assert properties["targetFiles"]["maxItems"] <= 2
    assert properties["taskAuthorization"]["additionalProperties"] is False


def test_task_status_binds_schema_valid_recovery_arguments_only_for_verified_owner(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    mod = _load_rag_mcp_module()
    authorization = {
        "taskSessionId": "task-1",
        "authToken": "token",
        "ownerCapability": "capability",
        "planId": "plan-1",
        "planRevision": "1",
        "activeSliceId": "slice-1",
        "routeHash": "route-1",
        "routePhase": "executor",
    }
    payload = {
        "ok": True,
        "taskSessionId": "task-1",
        "nextAction": "unreal_task_checkpoint:recover",
        "nextActionIsTool": True,
    }

    anonymous = mod._bind_task_status_next_action_args(payload, None)
    assert "nextActionArgs" not in anonymous

    owned = mod._bind_task_status_next_action_args(payload, authorization)
    assert owned["nextActionArgs"] == {
        "action": "recover",
        "taskAuthorization": {
            "taskSessionId": "task-1",
            "ownerCapability": "capability",
        },
    }

    resume = mod._bind_task_status_next_action_args(
        {**payload, "nextAction": "unreal_task_resume"},
        authorization,
    )
    assert resume["nextActionArgs"] == {"taskSessionId": "task-1"}


def test_checkpoint_uses_compact_server_resolved_authorization(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    tool = next(
        item
        for item in server.all_tool_definitions()
        if item["name"] == "unreal_task_checkpoint"
    )

    checkpoint_auth = tool["inputSchema"]["properties"]["taskAuthorization"]
    assert set(checkpoint_auth["properties"]) == {
        "taskSessionId",
        "ownerCapability",
    }
    assert checkpoint_auth["required"] == ["taskSessionId", "ownerCapability"]
    assert checkpoint_auth["additionalProperties"] is False
    feature_tool = next(
        item
        for item in server.all_tool_definitions()
        if item["name"] == "unreal_feature_intent_resolve"
    )
    assert set(
        feature_tool["inputSchema"]["properties"]["taskAuthorization"][
            "properties"
        ]
    ) == {"taskSessionId", "ownerCapability"}
    validation = tool["inputSchema"]["properties"]["validation"]
    assert validation["additionalProperties"] is False
    assert set(validation["properties"]) == {
        "status",
        "summary",
        "artifacts",
        "errors",
    }


def test_rag_evidence_tools_advertise_compact_route_ownership(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    tools = {item["name"]: item for item in server.all_tool_definitions()}

    for name in (
        "unreal_rag_search",
        "unreal_symbol_lookup",
        "unreal_feature_intent_resolve",
        "unreal_architecture_reasoning",
        "unreal_code_sketch_claim_validate",
    ):
        auth = tools[name]["inputSchema"]["properties"]["taskAuthorization"]
        assert auth["required"] == ["taskSessionId", "ownerCapability"]

    evidence_auth = tools["unreal_rag_search"]["inputSchema"]["properties"][
        "taskAuthorization"
    ]
    assert set(evidence_auth["properties"]) == {"taskSessionId", "ownerCapability"}

    gate_auth = tools["unreal_feature_intent_resolve"]["inputSchema"]["properties"][
        "taskAuthorization"
    ]
    assert set(gate_auth["properties"]) == {"taskSessionId", "ownerCapability"}

    assert mod._has_complete_task_authorization(
        {"taskSessionId": "t", "ownerCapability": "cap"}
    ) is False
    assert mod._has_complete_task_authorization(
        {
            "taskSessionId": "t",
            "authToken": "token",
            "ownerCapability": "cap",
            "planId": "p",
            "planRevision": 1,
            "activeSliceId": "slice",
            "routeHash": "route",
            "routePhase": "executor",
        }
    ) is True


def test_route_authorization_refresh_replaces_stale_full_arguments():
    mod = _load_rag_mcp_module()
    arguments = {
        "taskAuthorization": {
            "taskSessionId": "task",
            "authToken": "stale",
            "ownerCapability": "owner",
            "planId": "plan",
            "planRevision": "1",
            "activeSliceId": "slice",
            "routeHash": "stale-route",
            "routePhase": "planner",
        }
    }
    current = {
        **arguments["taskAuthorization"],
        "authToken": "current",
        "routeHash": "current-route",
    }

    mod._refresh_argument_task_authorization(
        arguments, {"ok": True, "taskAuthorization": current}
    )

    assert arguments["taskAuthorization"] == current


RAG_EXTENDED_ONLY = {
    "unreal_rag_refresh",
    "unreal_start_rag_refresh",
    "unreal_rag_refresh_status",
    "unreal_start_compile_loop",
    "unreal_compile_loop_status",
    "unreal_cancel_compile_loop",
}


def test_hidden_control_plane_tools_require_flag(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.delenv("ALLOW_CONTROL_PLANE_TOOLS", raising=False)
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    names = {tool["name"] for tool in server.all_tool_definitions()}
    assert "unreal_task_start" not in names
    monkeypatch.setenv("ALLOW_CONTROL_PLANE_TOOLS", "1")
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    names = {tool["name"] for tool in server.all_tool_definitions()}
    assert "unreal_task_start" in names


def test_initial_active_project_lookup_bypasses_foreign_task_ownership(
    monkeypatch,
    tmp_path,
) -> None:
    """Bootstrap project discovery must stay usable before task authorization exists."""

    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    project_dir = tmp_path / "O_Mock"
    project_dir.mkdir()
    project_file = project_dir / "O_Mock.uproject"
    project_file.write_text("{}", encoding="utf-8")
    shared_config = tmp_path / "unreal-workspace.json"
    shared_config.write_text(
        json.dumps({"activeProject": str(project_file)}),
        encoding="utf-8",
    )
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(shared_config))

    mod = _load_rag_mcp_module()
    from task_api import authorize_active_task_tool, task_start

    started = task_start(
        tmp_path,
        request="Edit Source/O_Mock/Private/BoardActor.cpp",
        mode="agent_edit",
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True},
            "orchestration": {"requiredBeforeWrite": []},
        },
    )
    denied = authorize_active_task_tool(
        tmp_path,
        tool_name="unreal_rag_search",
        arguments={},
    )
    assert denied["ok"] is False
    assert denied["errorCode"] == "TASK_ROUTE_OWNERSHIP_REQUIRED"

    server = mod.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    sent: list[dict] = []
    server.send = sent.append
    server.handle_tool_call(
        500,
        {"name": "unreal_get_active_project", "arguments": {}},
    )

    result = sent[-1]["result"]
    assert result.get("isError") is not True
    payload = result["structuredContent"]
    assert Path(payload["activeProject"]).resolve() == project_file.resolve()
    assert payload["projectContext"]["ok"] is True
    assert payload.get("errorCode") != "TASK_ROUTE_OWNERSHIP_REQUIRED"
    assert payload["requiredNextTool"] == "unreal_agent_plan"
    assert payload["control"]["nextAction"] == "unreal_agent_plan"
    assert payload["control"]["nextActionIsTool"] is True
    assert started["status"] == "running"


def test_active_project_lookup_without_selection_does_not_force_planner(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    shared_config = tmp_path / "unreal-workspace.json"
    shared_config.write_text(
        json.dumps({"activeProject": None, "projectSearchRoots": []}),
        encoding="utf-8",
    )
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(shared_config))
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        499,
        {"name": "unreal_get_active_project", "arguments": {}},
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["projectContext"]["ok"] is False
    assert payload["control"]["nextActionIsTool"] is False
    assert "requiredNextTool" not in payload
    assert payload["suggestedToolCalls"]


def test_active_task_control_surface_is_listed_and_callable_without_flag(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.delenv("ALLOW_CONTROL_PLANE_TOOLS", raising=False)
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    mod = _load_rag_mcp_module()
    from task_api import task_start

    started = task_start(
        tmp_path,
        request="Inspect code",
        mode="read_only",
        plan_payload={
            "taskKind": "inspect",
            "writeGate": {"writesAllowed": False},
            "orchestration": {"requiredBeforeWrite": []},
        },
    )
    server = mod.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    controls = {
        "unreal_task_status",
        "unreal_task_checkpoint",
        "unreal_task_cancel",
    }
    assert controls <= {
        tool["name"] for tool in server.all_tool_definitions()
    }
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        501,
        {
            "name": "unreal_task_status",
            "arguments": {"taskSessionId": started["taskSessionId"]},
        },
    )
    checkpoint_payload = sent[-1]["result"]["structuredContent"]
    assert checkpoint_payload["ok"] is True, checkpoint_payload
    server.handle_tool_call(
        502,
        {
            "name": "unreal_task_checkpoint",
            "arguments": {
                "action": "status",
                "taskAuthorization": started["taskAuthorization"],
            },
        },
    )
    checkpoint_payload = sent[-1]["result"]["structuredContent"]
    assert checkpoint_payload["ok"] is True, checkpoint_payload
    server.handle_tool_call(
        503,
        {
            "name": "unreal_task_cancel",
            "arguments": {"taskSessionId": started["taskSessionId"]},
        },
    )
    assert sent[-1]["result"]["structuredContent"]["ok"] is True


def test_completed_task_status_and_recovery_controls_are_not_profile_blocked(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.delenv("ALLOW_CONTROL_PLANE_TOOLS", raising=False)
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    mod = _load_rag_mcp_module()
    from task_api import task_start

    started = task_start(
        tmp_path,
        request="Inspect completed task recovery",
        mode="plan_only",
        plan_payload={
            "taskKind": "inspect_only",
            "writeGate": {"writesAllowed": False},
            "orchestration": {"requiredBeforeWrite": []},
        },
    )
    assert started["status"] == "completed"

    server = mod.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        504,
        {
            "name": "unreal_task_status",
            "arguments": {"taskSessionId": started["taskSessionId"]},
        },
    )
    status_payload = sent[-1]["result"]["structuredContent"]
    assert status_payload["ok"] is True, status_payload
    assert status_payload["status"] == "completed"

    server.handle_tool_call(
        505,
        {"name": "unreal_task_recover_active", "arguments": {}},
    )
    recovery_payload = sent[-1]["result"]["structuredContent"]
    assert recovery_payload.get("errorCode") != "TOOL_NOT_CALLABLE"


def test_autonomy_blocked_route_lists_and_dispatches_bounded_replan(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    mod = _load_rag_mcp_module()
    from task_api import task_root, task_start

    started = task_start(
        tmp_path,
        request="Edit Source/Demo/Foo.cpp",
        mode="agent_edit",
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {"requiredBeforeWrite": []},
            "executablePlanSlices": [
                {"sliceId": "task", "files": ["Source/Demo/Foo.cpp"]}
            ],
        },
    )
    state_path = task_root(tmp_path, started["taskSessionId"]) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["autonomySupervisor"]["retryState"]["totalNoProgress"] = 5
    state["autonomySupervisor"]["blockers"] = [
        {"code": "retry_budget_exhausted", "message": "blocked"}
    ]
    state["autonomySupervisor"]["nextAction"] = "replan_autonomous_strategy"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    server = mod.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    assert "unreal_agent_plan" in {
        tool["name"] for tool in server.all_tool_definitions()
    }
    sent: list[dict] = []
    server.send = sent.append
    server.handle_tool_call(
        551,
        {
            "name": "unreal_agent_plan",
            "arguments": {
                "request": "Create a different read-only inspection strategy"
            },
        },
    )
    payload = sent[-1]["result"]["structuredContent"]
    assert payload["taskAuthorization"]["taskSessionId"] == started["taskSessionId"]
    from task_api import task_status

    assert task_status(tmp_path, started["taskSessionId"])["state"]["planRevision"] == "2"
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["autonomySupervisor"]["blockers"] == []
    assert (
        persisted["autonomySupervisor"]["retryState"]["totalNoProgress"]
        == 5
    )

    server.handle_tool_call(
        552,
        {
            "name": "unreal_agent_plan",
            "arguments": {"request": "Attempt unbounded repeated replan"},
        },
    )
    denied = sent[-1]["result"]["structuredContent"]
    assert denied["ok"] is False
    assert denied["errorCode"] == "REPLAN_BUDGET_EXHAUSTED"
    assert denied["checkpointRecordRequired"] is True
    assert denied["taskAuthorization"]["taskSessionId"] == started["taskSessionId"]
    assert denied["nextActionArgs"]["action"] == "record"
    assert denied["nextActionArgs"]["taskAuthorization"] == denied["taskAuthorization"]
    assert "Do not call unreal_agent_plan again" in denied["agentInstruction"]


def test_blocked_routes_keep_catalog_but_reject_replan_at_call_time(
    monkeypatch,
    tmp_path,
) -> None:
    """Blocked routes keep controls/current phase visible and fail at CallTool."""
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    mod = _load_rag_mcp_module()
    from task_api import task_root, task_start
    from tool_exposure import load_stable_manifest

    essential = set(load_stable_manifest()["ragEssential"])
    always = set(load_stable_manifest()["ragAlwaysDiscoverable"])
    started = task_start(
        tmp_path,
        request="Edit Source/Demo/Foo.cpp",
        mode="agent_edit",
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {"requiredBeforeWrite": []},
            "executablePlanSlices": [
                {"sliceId": "task", "files": ["Source/Demo/Foo.cpp"]}
            ],
        },
    )
    state_path = task_root(tmp_path, started["taskSessionId"]) / "state.json"
    server = mod.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    sent: list[dict] = []
    server.send = sent.append

    def catalog_names() -> set[str]:
        return {tool["name"] for tool in server.all_tool_definitions()}

    state = json.loads(state_path.read_text(encoding="utf-8"))
    phase_catalog = essential & (
        always | set(state["toolRoute"]["activeTools"])
    )
    state["continuity"]["lease"]["expiresAt"] = "2000-01-01T00:00:00+00:00"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    assert catalog_names() == phase_catalog
    assert "unreal_agent_plan" in catalog_names()
    server.handle_tool_call(
        561,
        {
            "name": "unreal_agent_plan",
            "arguments": {"request": "Must not bypass expired lease"},
        },
    )
    expired = sent[-1]["result"]["structuredContent"]
    assert expired["ok"] is False
    assert expired["errorCode"] == "TASK_ROUTE_BLOCKED"
    assert expired["errorCode"] != "TOOL_NOT_CALLABLE"

    state["continuity"]["lease"]["expiresAt"] = "2999-01-01T00:00:00+00:00"
    state["continuity"]["recovery"]["conflicts"] = [
        {"relativePath": "Source/Demo/Foo.cpp", "reason": "content_changed"}
    ]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    assert catalog_names() == phase_catalog
    server.handle_tool_call(
        562,
        {
            "name": "unreal_agent_plan",
            "arguments": {"request": "Must not bypass checkpoint conflict"},
        },
    )
    conflict = sent[-1]["result"]["structuredContent"]
    assert conflict["ok"] is False
    assert conflict["errorCode"] == "TASK_ROUTE_BLOCKED"
    assert conflict["errorCode"] != "TOOL_NOT_CALLABLE"

    state["status"] = "completed"
    state["continuity"]["recovery"]["conflicts"] = []
    state_path.write_text(json.dumps(state), encoding="utf-8")
    task_start(
        tmp_path,
        request="First Source/Demo/A.cpp",
        mode="agent_edit",
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {"requiredBeforeWrite": []},
            "executablePlanSlices": [
                {"sliceId": "task", "files": ["Source/Demo/A.cpp"]}
            ],
        },
    )
    task_start(
        tmp_path,
        request="Second Source/Demo/B.cpp",
        mode="agent_edit",
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {"requiredBeforeWrite": []},
            "executablePlanSlices": [
                {"sliceId": "task", "files": ["Source/Demo/B.cpp"]}
            ],
        },
    )
    assert catalog_names() == phase_catalog
    server.handle_tool_call(
        563,
        {
            "name": "unreal_agent_plan",
            "arguments": {"request": "Must not bypass ambiguous ownership"},
        },
    )
    multi = sent[-1]["result"]["structuredContent"]
    assert multi["ok"] is False
    assert multi["errorCode"] == "MULTIPLE_HEALTHY_ROUTE_TASKS"
    assert multi["errorCode"] != "TOOL_NOT_CALLABLE"


def test_clean_startup_advertises_manifest_rag_essential(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.delenv("MCP_EXTENDED_TOOLS", raising=False)
    monkeypatch.delenv("ALLOW_CONTROL_PLANE_TOOLS", raising=False)
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    mod = _load_rag_mcp_module()
    from tool_exposure import load_stable_manifest

    server = mod.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    names = {tool["name"] for tool in server.all_tool_definitions()}
    assert names == set(load_stable_manifest()["ragEssential"])
    diag = server.tool_catalog_diagnostics()
    assert diag["profile"] == "essential"
    assert diag["advertisedCount"] == len(names)
    assert diag["routeContextStatus"] == "none"


def test_active_route_exposes_only_phase_rag_tools_and_controls(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    mod = _load_rag_mcp_module()
    from task_api import task_start
    from tool_exposure import load_stable_manifest

    essential = set(load_stable_manifest()["ragEssential"])
    started = task_start(
        tmp_path,
        request="Edit Source/Demo/Foo.cpp",
        mode="agent_edit",
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {"requiredBeforeWrite": []},
            "executablePlanSlices": [
                {"sliceId": "task", "files": ["Source/Demo/Foo.cpp"]}
            ],
        },
    )
    server = mod.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    expected = essential & (
        set(load_stable_manifest()["ragAlwaysDiscoverable"])
        | set(started["state"]["toolRoute"]["activeTools"])
    )
    assert {tool["name"] for tool in server.all_tool_definitions()} == expected
    assert expected < essential
    diag = server.tool_catalog_diagnostics()
    assert diag["routeContextStatus"] == "active"
    assert diag["advertisedCount"] == len(expected)


def test_corrupt_route_keeps_catalog_and_recovery_controls(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    mod = _load_rag_mcp_module()
    from task_api import task_root, task_start
    from tool_exposure import load_stable_manifest

    always = set(load_stable_manifest()["ragAlwaysDiscoverable"])
    started = task_start(
        tmp_path,
        request="Edit Source/Demo/Foo.cpp",
        mode="agent_edit",
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {"requiredBeforeWrite": []},
            "executablePlanSlices": [
                {"sliceId": "task", "files": ["Source/Demo/Foo.cpp"]}
            ],
        },
    )
    state_path = task_root(tmp_path, started["taskSessionId"]) / "state.json"
    state_path.write_text("{not-json", encoding="utf-8")
    server = mod.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    sent: list[dict] = []
    server.send = sent.append
    names = {tool["name"] for tool in server.all_tool_definitions()}
    assert names == always
    assert "unreal_task_quarantine_corrupt" in names
    diag = server.tool_catalog_diagnostics()
    assert diag["routeContextStatus"] == "ambiguous_or_corrupt"
    assert diag["routeErrorCode"] == "TASK_STATE_CORRUPT"
    server.handle_tool_call(
        570,
        {"name": "unreal_agent_plan", "arguments": {"request": "blocked by corrupt"}},
    )
    denied = sent[-1]["result"]["structuredContent"]
    assert denied["ok"] is False
    assert denied["errorCode"] == "TASK_STATE_CORRUPT"
    assert denied["errorCode"] != "TOOL_NOT_CALLABLE"


def test_hidden_control_plane_remains_hidden(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.delenv("ALLOW_CONTROL_PLANE_TOOLS", raising=False)
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    mod = _load_rag_mcp_module()
    from tool_exposure import load_stable_manifest

    hidden = set(load_stable_manifest().get("ragHiddenUntilControlPlane") or [])
    server = mod.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    names = {tool["name"] for tool in server.all_tool_definitions()}
    assert hidden
    assert names.isdisjoint(hidden)


def test_extended_tools_enabled_exposes_refresh_and_compile_loop(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_EXTENDED_TOOLS", "1")
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    names = {tool["name"] for tool in server.all_tool_definitions()}
    assert "unreal_start_rag_refresh" in names
    assert "unreal_start_compile_loop" in names


def test_unreal_agent_plan_description_mentions_chat_first(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    plan = next(t for t in server.all_tool_definitions() if t["name"] == "unreal_agent_plan")
    assert "FIRST" in plan["description"]
    assert "toolPolicy" in plan["description"]
    assert "server-issued taskAuthorization" in plan["description"]


def test_route_authorization_recovery_is_not_converted_to_hard_stop(monkeypatch):
    mod = _load_rag_mcp_module()
    payload = mod._route_authorization_failure_payload(
        {
            "ok": False,
            "errorCode": "TASK_PHASE_TOOL_BUDGET_EXHAUSTED",
            "nextActions": ["unreal_task_checkpoint"],
        },
        "read_file",
    )
    assert payload["retryable"] is False
    assert payload["stopCurrentWorkflow"] is False
    assert payload["recoveryActionRequired"] is True
    assert "paste-ready" in payload["agentInstruction"]


def test_terminal_route_integrity_failure_still_stops(monkeypatch):
    mod = _load_rag_mcp_module()
    payload = mod._route_authorization_failure_payload(
        {"ok": False, "errorCode": "TASK_STATE_CORRUPT"},
        "read_file",
    )
    assert payload["retryable"] is False
    assert payload["stopCurrentWorkflow"] is False
    assert payload["recoveryActionRequired"] is True
    assert payload["nextAction"] == "unreal_task_quarantine_corrupt"
    assert payload["nextActionIsTool"] is True


def test_agent_write_plan_fails_closed_without_fresh_context_proxy(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("MCP_FRONTEND", "lmstudio")
    monkeypatch.setenv("MCP_REQUIRE_CONTEXT_COMPACTOR_ACTIVE", "1")
    monkeypatch.setenv("LMS_CONTEXT_COMPACTOR_STATE_DIR", str(tmp_path / "missing-compactor-state"))
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "agent-state"))
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        16,
        {
            "name": "unreal_agent_plan",
            "arguments": {
                "request": "Create Source/Demo/NewActor.h and implement the actor class",
                "mode": "codegen",
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["ok"] is False
    assert payload["errorCode"] == "CONTEXT_COMPACTOR_NOT_ACTIVE"
    assert payload["stopCurrentWorkflow"] is True
    assert payload["failureLayer"] == "chat_model_routing_policy"
    assert payload["agentAuthority"] == "unchanged"
    assert payload["doNotFallbackToManualCode"] is True
    assert "SAFE_MODE" in payload["notCausedBy"]
    assert "MACOS_PRIVACY_PERMISSION" in payload["notCausedBy"]
    tasks_root = tmp_path / "agent-state" / "tasks"
    if tasks_root.exists():
        assert not list(tasks_root.iterdir())


def test_agent_write_plan_allows_direct_model_under_advisory_policy(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("MCP_FRONTEND", "lmstudio")
    monkeypatch.setenv("MCP_CONTEXT_COMPACTOR_ADVISORY", "1")
    monkeypatch.setenv("MCP_REQUIRE_CONTEXT_COMPACTOR_ACTIVE", "0")
    monkeypatch.setenv(
        "LMS_CONTEXT_COMPACTOR_STATE_DIR",
        str(tmp_path / "missing-compactor-state"),
    )
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "agent-state"))
    project = tmp_path / "Demo" / "Demo.uproject"
    project.parent.mkdir(parents=True)
    project.write_text("{}", encoding="utf-8")
    shared = tmp_path / "unreal-workspace.json"
    shared.write_text(
        json.dumps({"activeProject": str(project)}),
        encoding="utf-8",
    )
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(shared))
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        17,
        {
            "name": "unreal_agent_plan",
            "arguments": {
                "request": "Create Source/Demo/NewActor.h and implement the actor class",
                "mode": "codegen",
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["taskAuthorization"]["taskSessionId"]
    assert payload["taskAuthorization"]["ownerCapability"]
    assert payload["nextAction"] in payload["toolRoute"]["pendingGates"]
    assert payload["nextActionIsTool"] is True
    assert payload["nextActionArgs"] == {
        "taskAuthorization": payload["taskAuthorization"]
    }
    assert payload["executionContract"]["maxFilesPerSlice"] == 2
    assert payload["executionContract"]["splitBeforeFirstGate"] is True
    assert payload["executionContract"]["existingFileMutationTool"] == "replace_in_file"
    assert payload["executionContract"]["maxChangedLinesPerMutation"] == 60
    assert payload["executionContract"]["maxCombinedPatchChars"] == 8000
    assert payload["executionContract"]["fullExistingFileContentInBundleAllowed"] is False
    assert "ownerCapability" in payload["agentInstruction"]
    if payload["nextAction"] == "unreal_feature_intent_resolve":
        assert "make one model-facing call" in payload["agentInstruction"]
        assert "never call unreal_task_define_slices separately" in payload["agentInstruction"]
    assert "Never send a full existing file" in payload["agentInstruction"]
    routing = payload["contextCompactorRouting"]
    assert routing["policy"] == "advisory"
    assert routing["active"] is False
    assert routing["blocksWrites"] is False
    assert routing["directModelAllowed"] is True


def test_validated_architecture_slices_bind_server_side_before_feature_intent(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("MCP_REQUIRE_CONTEXT_COMPACTOR_ACTIVE", "0")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "agent-state"))
    project = tmp_path / "Demo" / "Demo.uproject"
    project.parent.mkdir(parents=True)
    project.write_text("{}", encoding="utf-8")
    shared = tmp_path / "unreal-workspace.json"
    shared.write_text(json.dumps({"activeProject": str(project)}), encoding="utf-8")
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(shared))
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    server.set_pending_architecture_handoff(
        project_root=str(project.parent),
        proposal={
            "implementationSlices": [
                {
                    "sliceId": "history-core",
                    "files": [
                        "Source/Demo/MoveHistory.h",
                        "Source/Demo/MoveHistory.cpp",
                    ],
                },
                {
                    "sliceId": "history-integration-tests",
                    "files": [
                        "Source/Demo/GameState.cpp",
                        "Source/Demo/Tests/MoveHistory.spec.cpp",
                    ],
                },
            ]
        },
        session_id="architecture-chat",
        proposal_revision="proposal-rev-1",
        source_snapshot_fingerprint="source-snapshot-1",
    )
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        1700,
        {
            "name": "unreal_agent_plan",
            "arguments": {
                "request": "Create the local move history and undo system with tests",
                "mode": "codegen",
                "sessionId": "architecture-chat",
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["architectureHandoff"] == {
        "serverOwned": True,
        "proposalRevision": "proposal-rev-1",
        "sourceSnapshotFingerprint": "source-snapshot-1",
        "sliceCount": 2,
        "activeSliceId": "history-core",
    }
    assert payload["toolRoute"]["selectedSlice"]["sliceId"] == "history-core"
    assert payload["toolRoute"]["selectedSlice"]["files"] == [
        "Source/Demo/MoveHistory.h",
        "Source/Demo/MoveHistory.cpp",
    ]
    from task_api import task_status

    state = task_status(
        tmp_path,
        payload["taskAuthorization"]["taskSessionId"],
    )["state"]
    assert [row["sliceId"] for row in state["planScope"]["slices"]] == [
        "history-core",
        "history-integration-tests",
    ]
    assert state["sliceProgress"]["pendingSlices"] == [
        "history-integration-tests"
    ]


def test_validated_architecture_handler_hands_its_slice_to_same_chat_planner(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("MCP_REQUIRE_CONTEXT_COMPACTOR_ACTIVE", "0")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "agent-state"))
    project = tmp_path / "Demo" / "Demo.uproject"
    source = project.parent / "Source" / "Demo"
    source.mkdir(parents=True)
    project.write_text("{}", encoding="utf-8")
    (source / "Worker.cpp").write_text("void Run() {}\n", encoding="utf-8")
    shared = tmp_path / "unreal-workspace.json"
    shared.write_text(json.dumps({"activeProject": str(project)}), encoding="utf-8")
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(shared))
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        17001,
        {
            "name": "unreal_architecture_reasoning",
            "arguments": {
                "projectRoot": str(project.parent),
                "sessionId": "same-chat",
                "proposal": {
                    "decision": "add one bounded local helper implementation",
                    "scope": {
                        "networked": False,
                        "runtime": "standalone",
                        "validationLevel": "Draft",
                    },
                    "invariants": [
                        {"id": "I1", "statement": "existing worker behavior stays stable"},
                    ],
                    "impactedSurfaces": ["Source/Demo/NewHelper.cpp"],
                    "validationPlan": ["compile"],
                    "implementationFiles": ["Source/Demo/NewHelper.cpp"],
                    "implementationSlices": [
                        {
                            "sliceId": "new-helper",
                            "files": ["Source/Demo/NewHelper.cpp"],
                            "dependsOn": [],
                            "invariantIds": ["I1"],
                            "validation": ["compile"],
                        }
                    ],
                },
            },
        },
    )

    architecture = sent[-1]["result"]["structuredContent"]
    assert architecture["proposalValidation"]["ok"] is True
    assert architecture["architectureState"]["current"] == "Validated"

    server.handle_tool_call(
        17002,
        {
            "name": "unreal_agent_plan",
            "arguments": {
                "request": "Create Source/Demo/NewHelper.cpp for the bounded local helper",
                "mode": "codegen",
                "sessionId": "same-chat",
            },
        },
    )

    planner = sent[-1]["result"]["structuredContent"]
    assert planner["architectureHandoff"]["serverOwned"] is True
    assert planner["architectureHandoff"]["sliceCount"] == 1
    selected_slice = planner["toolRoute"]["selectedSlice"]
    assert selected_slice["sliceId"] == "new-helper"
    assert selected_slice["files"] == ["Source/Demo/NewHelper.cpp"]

    server.handle_tool_call(
        17003,
        {
            "name": "unreal_feature_intent_resolve",
            "arguments": {
                "taskAuthorization": planner["taskAuthorization"],
            },
        },
    )

    intent = sent[-1]["result"]["structuredContent"]
    assert intent["ok"] is True, intent
    assert intent["internalPhases"] == [
        "SelectIntent",
        "ResolveSlice",
        "CaptureSnapshot",
        "BindIntent",
    ]
    assert intent["sliceResolution"]["serverOwned"] is True
    assert intent["sliceResolution"]["activeSliceId"] == "new-helper"


def test_architecture_handoff_is_not_reused_by_another_chat(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    project = tmp_path / "Demo"
    project.mkdir()
    server.set_pending_architecture_handoff(
        project_root=str(project),
        proposal={
            "implementationSlices": [
                {"sliceId": "chat-a-only", "files": ["Source/Demo/A.cpp"]},
            ]
        },
        session_id="chat-a",
    )

    assert server.consume_pending_architecture_handoff(
        project,
        session_id="chat-b",
    ) == {}
    assert server.consume_pending_architecture_handoff(
        project,
        session_id="chat-a",
    ) == {}


def test_pure_continuation_reuses_active_task_without_replanning(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "agent-state"))
    project = tmp_path / "Demo" / "Demo.uproject"
    project.parent.mkdir(parents=True)
    project.write_text("{}", encoding="utf-8")
    shared = tmp_path / "unreal-workspace.json"
    shared.write_text(json.dumps({"activeProject": str(project)}), encoding="utf-8")
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(shared))
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        1701,
        {
            "name": "unreal_agent_plan",
            "arguments": {
                "request": "Implement Source/Demo/StateSubsystem.cpp",
                "mode": "agent_edit",
            },
        },
    )
    planned = sent[-1]["result"]["structuredContent"]
    original_task = planned["taskAuthorization"]["taskSessionId"]
    from task_api import task_status

    original_revision = task_status(tmp_path, original_task)["state"]["planRevision"]

    server.handle_tool_call(
        1702,
        {
            "name": "unreal_agent_plan",
            "arguments": {"request": "계속 진행해"},
        },
    )
    continued = sent[-1]["result"]["structuredContent"]

    assert continued["ok"] is True
    assert continued["continuationPreserved"] is True
    assert continued["taskAuthorization"]["taskSessionId"] == original_task
    assert task_status(tmp_path, original_task)["state"]["planRevision"] == original_revision
    assert continued["request"] == "Implement Source/Demo/StateSubsystem.cpp"
    assert continued["taskKind"] == "edit"


def test_compile_fix_plan_reproduces_build_before_requesting_fix_sketch(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "agent-state"))
    project = tmp_path / "Demo" / "Demo.uproject"
    project.parent.mkdir(parents=True)
    project.write_text("{}", encoding="utf-8")
    shared = tmp_path / "unreal-workspace.json"
    shared.write_text(
        json.dumps({"activeProject": str(project)}),
        encoding="utf-8",
    )
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(shared))
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        171,
        {
            "name": "unreal_agent_plan",
            "arguments": {
                "request": "Fix the current Unreal build errors until the project builds",
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["taskKind"] == "compile_fix"
    assert payload["nextAction"] == "build_unreal_project"
    assert payload["nextActionArgs"]["taskAuthorization"] == payload["taskAuthorization"]
    # build_unreal_project is provided by unreal-agent, not this RAG catalog.
    # Keep this bridge contract deliberately minimal so it remains valid for
    # every advertised agent build schema.
    assert set(payload["nextActionArgs"]) == {"taskAuthorization"}
    assert "build_unreal_project" in payload["toolRoute"]["activeTools"]
    assert "requiredFirstTool" not in payload["toolRoute"]
    assert "static_validate_project" in payload["toolRoute"]["activeTools"]
    assert "read_file" in payload["toolRoute"]["activeTools"]
    assert "unreal_code_sketch_claim_validate" in payload["toolRoute"]["pendingGates"]
    assert "Reproduce the current build first" in payload["agentInstruction"]


def test_strict_compactor_request_does_not_block_non_lmstudio_frontend(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("MCP_FRONTEND", "cline")
    monkeypatch.setenv("MCP_REQUIRE_CONTEXT_COMPACTOR_ACTIVE", "1")
    monkeypatch.setenv("MCP_CONTEXT_COMPACTOR_REQUIRED_FRONTENDS", "lmstudio,cline")
    monkeypatch.setenv(
        "LMS_CONTEXT_COMPACTOR_STATE_DIR",
        str(tmp_path / "missing-compactor-state"),
    )
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "agent-state"))
    project = tmp_path / "Demo" / "Demo.uproject"
    project.parent.mkdir(parents=True)
    project.write_text("{}", encoding="utf-8")
    shared = tmp_path / "unreal-workspace.json"
    shared.write_text(json.dumps({"activeProject": str(project)}), encoding="utf-8")
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(shared))
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        18,
        {
            "name": "unreal_agent_plan",
            "arguments": {"request": "Create Source/Demo/NewActor.h", "mode": "codegen"},
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["taskAuthorization"]["taskSessionId"]
    routing = payload["contextCompactorRouting"]
    assert routing["policy"] == "not_applicable"
    assert routing["frontend"] == "cline"
    assert routing["strictRequested"] is True
    assert routing["strictScopeMatched"] is False
    assert routing["blocksWrites"] is False
    assert routing["active"] is None
    assert routing["status"]["telemetryChecked"] is False


def test_code_sketch_tool_exposes_project_generation_contract(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    tool = next(item for item in server.all_tool_definitions() if item["name"] == "unreal_code_sketch_claim_validate")
    properties = tool["inputSchema"]["properties"]
    assert {"projectRoot", "targetFiles", "changeKind", "validationPlan", "architectureProposal", "architectureSymbols"}.issubset(properties)

    project = tmp_path / "Project"
    target = project / "Source" / "Demo" / "Private" / "Worker.cpp"
    target.parent.mkdir(parents=True)
    target.write_text("void Run() {}\n", encoding="utf-8")
    sent: list[dict] = []
    server.send = sent.append
    server.handle_tool_call(
        17,
        {
            "name": "unreal_code_sketch_claim_validate",
            "arguments": {
                "sketch": "AActor* Actor = nullptr;",
                "projectRoot": str(project),
                "targetFiles": ["Source/Demo/Private/Worker.cpp"],
                "changeKind": "modify_existing",
            },
        },
    )
    payload = sent[-1]["result"]["structuredContent"]
    contract = payload["generationContract"]
    assert contract["mode"] == "project_specific"
    assert contract["targets"][0]["exists"] is True
    assert contract["writeGate"]["requiresReadBeforeWrite"] is True
    assert payload["graphStatus"]["status"] == "ready"
    assert payload["graphStatus"]["graphSource"] in {
        "rebuilt",
        "memory_verified",
        "persistent_verified",
    }


def test_active_task_advertises_compact_code_sketch_schema(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    project = tmp_path / "Demo"
    source = project / "Source" / "Demo"
    source.mkdir(parents=True)
    (project / "Demo.uproject").write_text("{}", encoding="utf-8")
    (source / "DemoPlayerController.h").write_text(
        "class ADemoPlayerController {};\n", encoding="utf-8"
    )
    (source / "DemoPlayerController.cpp").write_text(
        '#include "DemoPlayerController.h"\n', encoding="utf-8"
    )
    shared = tmp_path / "unreal-workspace.json"
    shared.write_text(
        json.dumps({"activeProject": str(project / "Demo.uproject")}),
        encoding="utf-8",
    )
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(shared))

    from task_api import task_start

    task_start(
        tmp_path,
        request="Add an authoritative move request RPC to the player controller",
        project_file=str(project / "Demo.uproject"),
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {
                "requiredBeforeWrite": ["unreal_code_sketch_claim_validate"],
            },
            "executablePlanSlices": [
                {
                    "sliceId": "rep_contract",
                    "files": [
                        "Source/Demo/DemoPlayerController.h",
                        "Source/Demo/DemoPlayerController.cpp",
                    ],
                }
            ],
        },
    )

    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    tool = next(
        item
        for item in server.all_tool_definitions()
        if item["name"] == "unreal_code_sketch_claim_validate"
    )

    assert set(tool["inputSchema"]["properties"]) == {
        "sketch",
        "validationPlan",
        "taskAuthorization",
    }
    assert tool["inputSchema"]["required"] == ["sketch", "taskAuthorization"]


def test_active_task_code_sketch_derives_generation_contract(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    project = tmp_path / "Demo"
    source = project / "Source" / "Demo"
    source.mkdir(parents=True)
    (project / "Demo.uproject").write_text("{}", encoding="utf-8")
    (source / "DemoPlayerController.h").write_text(
        "class ADemoPlayerController {};\n", encoding="utf-8"
    )
    (source / "DemoPlayerController.cpp").write_text(
        '#include "DemoPlayerController.h"\n', encoding="utf-8"
    )

    from task_api import task_start

    started = task_start(
        tmp_path,
        request="Add an authoritative move request RPC to the player controller",
        project_file=str(project / "Demo.uproject"),
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {
                "requiredBeforeWrite": ["unreal_code_sketch_claim_validate"],
            },
            "executablePlanSlices": [
                {
                    "sliceId": "rep_contract",
                    "files": [
                        "Source/Demo/DemoPlayerController.h",
                        "Source/Demo/DemoPlayerController.cpp",
                    ],
                }
            ],
        },
    )
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    sent: list[dict] = []
    server.send = sent.append
    captured_contract_args: dict = {}
    original_build_generation_contract = mod.build_generation_contract

    def capture_generation_contract(request, **kwargs):
        captured_contract_args["request"] = request
        captured_contract_args.update(kwargs)
        return original_build_generation_contract(request, **kwargs)

    monkeypatch.setattr(mod, "build_generation_contract", capture_generation_contract)

    server.handle_tool_call(
        172,
        {
            "name": "unreal_code_sketch_claim_validate",
            "arguments": {
                "sketch": (
                    "UFUNCTION(Server, Reliable)\n"
                    "void ServerRequestMove(int32 X, int32 Y);"
                ),
                "validationPlan": ["UnrealHeaderTool", "build", "RPC ownership review"],
                "taskAuthorization": started["taskAuthorization"],
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    contract = payload["generationContract"]
    assert contract["mode"] == "project_specific"
    assert contract["changeKind"] == "multifile"
    assert [target["path"] for target in contract["targets"]] == [
        "Source/Demo/DemoPlayerController.h",
        "Source/Demo/DemoPlayerController.cpp",
    ]
    assert all(target["exists"] is True for target in contract["targets"])
    assert captured_contract_args["request"] == (
        "Add an authoritative move request RPC to the player controller"
    )
    assert Path(captured_contract_args["project_root"]) == project
    assert captured_contract_args["target_files"] == [
        "Source/Demo/DemoPlayerController.h",
        "Source/Demo/DemoPlayerController.cpp",
    ]


def test_failed_prewrite_gate_explicitly_forbids_checkpoint_completion(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    result = mod._record_prewrite_gate(
        server,
        gate_name="unreal_code_sketch_claim_validate",
        arguments={"taskAuthorization": {"taskSessionId": "task"}},
        evidence={"ok": False, "errorCode": "SKETCH_TOO_LARGE"},
        gate_passed=False,
    )
    assert result["errorCode"] == "GATE_VALIDATION_FAILED"
    assert result["validationErrorCode"] == "SKETCH_TOO_LARGE"
    assert "Do not use a checkpoint" in result["agentInstruction"]


def test_code_sketch_rebuilds_graph_and_accepts_project_local_symbol(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    project = tmp_path / "Project"
    target = project / "Source" / "Demo" / "Public" / "GomokuRuleEngine.h"
    target.parent.mkdir(parents=True)
    target.write_text("class UGomokuRuleEngine {};\n", encoding="utf-8")
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        18,
        {
            "name": "unreal_code_sketch_claim_validate",
            "arguments": {
                "sketch": "UGomokuRuleEngine* Rules = nullptr;",
                "projectRoot": str(project),
                "targetFiles": ["Source/Demo/Public/GomokuRuleEngine.h"],
                "changeKind": "modify_existing",
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["ok"] is True, payload
    assert payload["graphStatus"]["status"] == "ready"
    assert payload["graphStatus"]["graphSource"] == "rebuilt"
    assert payload["projectGraphAvailable"] is True
    assert payload["unverifiedCount"] == 0


def test_new_cpp_sketch_uses_existing_paired_header_declarations(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    project = tmp_path / "Project"
    source = project / "Source" / "Demo"
    source.mkdir(parents=True)
    header = source / "GomokuGameState.h"
    header.write_text(
        """
class UGomokuRuleEngine
{
public:
    bool IsGameWon(int32& Winner) const;
};
class AGomokuGameState
{
    TWeakObjectPtr<UGomokuRuleEngine> RuleEngineRef;
public:
    void OnStonePlaced();
};
""",
        encoding="utf-8",
    )
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        180,
        {
            "name": "unreal_code_sketch_claim_validate",
            "arguments": {
                "sketch": """
void AGomokuGameState::OnStonePlaced()
{
    int32 Winner = 0;
    if (RuleEngineRef.IsValid())
    {
        RuleEngineRef->IsGameWon(Winner);
    }
}
""",
                "projectRoot": str(project),
                "targetFiles": ["Source/Demo/GomokuGameState.cpp"],
                "changeKind": "new_file",
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["ok"] is True, payload
    assert payload["generationContract"]["targets"][0]["pairedSources"] == [
        "Source/Demo/GomokuGameState.h"
    ]
    assert payload["unverifiedCount"] == 0
    assert payload["weakCount"] == 0


def test_greenfield_sketch_gate_advances_to_executor(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    project = tmp_path / "Demo"
    source = project / "Source" / "Demo"
    source.mkdir(parents=True)
    (source / "Existing.h").write_text("class UExisting {};\n", encoding="utf-8")
    uproject = project / "Demo.uproject"
    uproject.write_text("{}", encoding="utf-8")

    from task_api import task_start

    started = task_start(
        tmp_path,
        request="Create two Gomoku gameplay classes",
        project_file=str(uproject),
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {
                "requiredBeforeWrite": ["unreal_code_sketch_claim_validate"],
            },
            "executablePlanSlices": [{"sliceId": "task", "files": []}],
        },
    )
    sent: list[dict] = []
    server.send = sent.append
    server.handle_tool_call(
        181,
        {
            "name": "unreal_code_sketch_claim_validate",
            "arguments": {
                "sketch": (
                    "AGomokuGameMode : AGameModeBase\n"
                    "AGomokuGameState : AGameStateBase"
                ),
                "request": "Create two Gomoku gameplay classes",
                "projectRoot": str(project),
                "targetFiles": [
                    "Source/Demo/GomokuGameMode.h",
                    "Source/Demo/GomokuGameState.h",
                ],
                "changeKind": "multifile",
                "taskAuthorization": started["taskAuthorization"],
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["ok"] is True, payload
    assert payload["gateCompletion"]["ok"] is True, payload
    assert payload["gateCompletion"]["toolRoute"]["phase"] == "executor"
    text_result = sent[-1]["result"]["content"][0]["text"]
    assert "nextTaskAuthorization=" in text_result
    assert payload["gateCompletion"]["taskAuthorization"]["ownerCapability"] in text_result
    assert "routeHash" not in text_result
    assert '"phase":"executor"' in text_result
    assert "server resolves current route fields" in text_result


def test_build_cs_and_header_sketch_does_not_stall_prewrite_gate(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    project = tmp_path / "O_Mock"
    source = project / "Source" / "O_Mock"
    source.mkdir(parents=True)
    (project / "O_Mock.uproject").write_text("{}", encoding="utf-8")
    (source / "O_Mock.Build.cs").write_text(
        'PublicDependencyModuleNames.AddRange(new string[] { "Core" });\n',
        encoding="utf-8",
    )
    (source / "GomokuGameMode.h").write_text(
        "class AGomokuGameMode;\n",
        encoding="utf-8",
    )

    from task_api import task_start

    started = task_start(
        tmp_path,
        request="Enable UMG/Slate and prepare GomokuGameMode",
        project_file=str(project / "O_Mock.uproject"),
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {
                "requiredBeforeWrite": ["unreal_code_sketch_claim_validate"],
            },
            "executablePlanSlices": [{"sliceId": "task", "files": []}],
        },
    )
    sent: list[dict] = []
    server.send = sent.append
    server.handle_tool_call(
        183,
        {
            "name": "unreal_code_sketch_claim_validate",
            "arguments": {
                "sketch": (
                    "// O_Mock.Build.cs: add UMG/Slate for Gomoku HUD\n"
                    "PublicDependencyModuleNames.AddRange(new string[] { \"Core\", \"UMG\" });\n"
                    "PrivateDependencyModuleNames.AddRange(new string[] { \"Slate\", \"SlateCore\" });\n"
                    "// GomokuGameMode.h: add GameStateBase and UserWidget includes.\n"
                ),
                "request": "Enable UMG/Slate and prepare GomokuGameMode",
                "projectRoot": str(project),
                "targetFiles": [
                    "Source/O_Mock/O_Mock.Build.cs",
                    "Source/O_Mock/GomokuGameMode.h",
                ],
                "changeKind": "modify_existing",
                "taskAuthorization": started["taskAuthorization"],
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["ok"] is True, payload
    assert payload["symbolCount"] == 0
    assert payload["gateCompletion"]["ok"] is True
    assert payload["gateCompletion"]["toolRoute"]["phase"] == "executor"
    assert "blockingSymbols=" not in sent[-1]["result"]["content"][0]["text"]


def test_code_sketch_text_result_names_blocking_symbols(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    project = tmp_path / "Demo"
    project.mkdir()
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        182,
        {
            "name": "unreal_code_sketch_claim_validate",
            "arguments": {
                "sketch": "UDefinitelyMissingApi* Value;",
                "projectRoot": str(project),
                "targetFiles": ["Source/Demo/NewThing.h"],
                "changeKind": "new_file",
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    text_result = sent[-1]["result"]["content"][0]["text"]
    assert payload["gatePassed"] is False
    assert payload["writeGateClosed"] is True
    assert payload["firstBlocker"]["symbol"] == "UDefinitelyMissingApi"
    assert payload["firstBlocker"]["verdict"] == "unverified"
    assert payload["nextAction"] == "unreal_project_status"
    assert payload["nextActionArgs"] == {}
    assert payload["recoveryContext"]["blockers"][0]["symbol"] == "UDefinitelyMissingApi"
    assert payload["doNotRetryUnchanged"] is True
    assert text_result.startswith("GATE_FAILED: writes remain closed")
    assert "blockingSymbols=UDefinitelyMissingApi:unverified" in text_result
    assert "firstBlocker=UDefinitelyMissingApi:unverified" in text_result
    assert "nextAction=unreal_project_status {}" in text_result
    assert "Do not rerun the unchanged sketch" in text_result
    assert "Never move responsibility to another class" in text_result


def test_code_sketch_rejects_labeled_files_outside_active_target_slice(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    project = tmp_path / "Demo"
    (project / "Source" / "Demo").mkdir(parents=True)
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        183,
        {
            "name": "unreal_code_sketch_claim_validate",
            "arguments": {
                "sketch": (
                    "// First.cpp\nvoid First() {}\n"
                    "// Second.cpp\nvoid Second() {}\n"
                ),
                "projectRoot": str(project),
                "targetFiles": ["Source/Demo/First.cpp"],
                "changeKind": "new_file",
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["gatePassed"] is False
    assert payload["firstBlocker"]["verdict"] == "contract"
    assert "Second.cpp" in payload["firstBlocker"]["note"]
    assert payload["nextAction"] == "unreal_code_sketch_claim_validate"
    assert payload["nextActionArgs"]["targetFiles"] == [
        "Source/Demo/First.cpp"
    ]
    assert payload["recoveryContext"]["allowedTargetFiles"] == [
        "Source/Demo/First.cpp"
    ]


def test_code_sketch_rejects_block_comment_file_label_outside_slice(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    project = tmp_path / "Demo"
    (project / "Source" / "Demo").mkdir(parents=True)
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        1831,
        {
            "name": "unreal_code_sketch_claim_validate",
            "arguments": {
                "sketch": (
                    "/* First.cpp - implementation */\nvoid First() {}\n"
                    "/* Second.cpp: implementation */\nvoid Second() {}\n"
                ),
                "projectRoot": str(project),
                "targetFiles": ["Source/Demo/First.cpp"],
                "changeKind": "new_file",
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["gatePassed"] is False
    assert "Second.cpp" in payload["firstBlocker"]["note"]


def test_code_sketch_rejects_reflected_classes_outside_target_pair(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    project = tmp_path / "Demo"
    source = project / "Source" / "Demo"
    source.mkdir(parents=True)
    (source / "GomokuBoardActor.h").write_text(
        "UCLASS()\nclass AGomokuBoardActor : public AActor {};\n",
        encoding="utf-8",
    )
    (source / "GomokuBoardActor.cpp").write_text(
        '#include "GomokuBoardActor.h"\n',
        encoding="utf-8",
    )
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        18311,
        {
            "name": "unreal_code_sketch_claim_validate",
            "arguments": {
                "sketch": (
                    "UCLASS()\nclass AGomokuBoardActor : public AActor {};\n"
                    "UCLASS()\nclass AGomokuPlayerController : public APlayerController {};\n"
                    "UCLASS()\nclass WGomokuHUD : public UUserWidget {};\n"
                ),
                "projectRoot": str(project),
                "targetFiles": [
                    "Source/Demo/GomokuBoardActor.h",
                    "Source/Demo/GomokuBoardActor.cpp",
                ],
                "changeKind": "multifile",
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    issues = payload["generationContract"]["issues"]
    assert payload["gatePassed"] is False
    assert any("AGomokuPlayerController" in issue for issue in issues)
    assert any("WGomokuHUD" in issue and "A/U prefix" in issue for issue in issues)


def test_code_sketch_enforces_shared_first_build_error_scope(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    mod = _load_rag_mcp_module()
    from task_api import (
        task_mark_build_recovery_evidence,
        task_record_build_recovery,
        task_start,
    )

    project = tmp_path / "Demo"
    source = project / "Source" / "Demo"
    source.mkdir(parents=True)
    first_target = "Source/Demo/FirstError.cpp"
    (source / "FirstError.cpp").write_text("void FirstError() {}\n", encoding="utf-8")
    (source / "ParallelError.cpp").write_text("void ParallelError() {}\n", encoding="utf-8")
    started = task_start(
        tmp_path,
        request="Fix the current first compiler error",
        mode="agent_edit",
        plan_payload={
            "taskKind": "compile_fix",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {
                "requiredBeforeWrite": ["unreal_code_sketch_claim_validate"]
            },
            "executablePlanSlices": [],
        },
    )
    authorization = started["taskAuthorization"]
    assert task_record_build_recovery(
        tmp_path,
        task_authorization=authorization,
        recovery={
            "targetFile": first_target,
            "requiredNextTool": "read_file_range",
            "requiredNextToolArgs": {
                "path": f"project://{first_target}",
                "startLine": 1,
                "endLine": 1,
            },
        },
    )["ok"]
    assert task_mark_build_recovery_evidence(
        tmp_path,
        task_authorization=authorization,
        target_file=first_target,
    )["ok"]

    server = mod.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    sent: list[dict] = []
    server.send = sent.append
    mod._handle_unreal_code_sketch_claim_validate(
        server,
        18315,
        {
            "sketch": "void FirstError() {}\nvoid ParallelError() {}\n",
            "projectRoot": str(project),
            "targetFiles": [
                first_target,
                "Source/Demo/ParallelError.cpp",
            ],
            "changeKind": "multifile",
            "taskAuthorization": authorization,
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["errorCode"] == "BUILD_RECOVERY_TARGET_SCOPE_MISMATCH"
    assert payload["gatePassed"] is False
    assert payload["nextActionArgs"]["targetFiles"] == [first_target]
    assert "parallel diagnostics" in payload["agentInstruction"]


def test_existing_file_gate_rejects_prose_api_summary(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    project = tmp_path / "Demo"
    target = project / "Source" / "Demo" / "Worker.cpp"
    target.parent.mkdir(parents=True)
    target.write_text("void Run() {}\n", encoding="utf-8")
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        1832,
        {
            "name": "unreal_code_sketch_claim_validate",
            "arguments": {
                "sketch": (
                    "// Worker.cpp aligned to the header\n"
                    "- Uses: TryPlaceStone(PlayerId, X, Y)\n"
                    "- Implements: StartTurn and EndTurn\n"
                ),
                "projectRoot": str(project),
                "targetFiles": ["Source/Demo/Worker.cpp"],
                "changeKind": "modify_existing",
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["gatePassed"] is False
    assert payload["firstBlocker"]["verdict"] == "contract"
    assert "concrete code snippet" in payload["firstBlocker"]["note"]
    assert payload["nextAction"] == "unreal_code_sketch_claim_validate"


def test_existing_file_gate_rejects_requested_behavior_placeholder(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    project = tmp_path / "Demo"
    target = project / "Source" / "Demo" / "Board.cpp"
    target.parent.mkdir(parents=True)
    target.write_text("void OnClick() {}\n", encoding="utf-8")
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        18321,
        {
            "name": "unreal_code_sketch_claim_validate",
            "arguments": {
                "sketch": (
                    "void OnClick()\n{\n"
                    "    const FIntPoint Cell(1, 2);\n"
                    "    // ... place stone using game state here\n"
                    "}\n"
                ),
                "projectRoot": str(project),
                "targetFiles": ["Source/Demo/Board.cpp"],
                "changeKind": "modify_existing",
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["gatePassed"] is False
    assert any(
        "implementation placeholder" in issue
        for issue in payload["generationContract"]["issues"]
    )


def test_code_sketch_rejects_guessed_reflected_type_header(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    project = tmp_path / "Demo"
    source = project / "Source" / "Demo"
    source.mkdir(parents=True)
    (source / "GomokuGameState.h").write_text(
        "UCLASS()\nclass AGomokuGameState : public AGameStateBase {};\n",
        encoding="utf-8",
    )
    (source / "Board.cpp").write_text("void OnClick() {}\n", encoding="utf-8")
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        18322,
        {
            "name": "unreal_code_sketch_claim_validate",
            "arguments": {
                "sketch": (
                    '#include "AGomokuGameState.h"\n'
                    "void OnClick() { AGomokuGameState* State = nullptr; }\n"
                ),
                "projectRoot": str(project),
                "targetFiles": ["Source/Demo/Board.cpp"],
                "changeKind": "modify_existing",
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["gatePassed"] is False
    assert any(
        "GomokuGameState.h" in issue and "AGomokuGameState.h" in issue
        for issue in payload["generationContract"]["issues"]
    )


def test_failed_task_sketch_gate_preserves_executable_recovery(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    project = tmp_path / "Demo"
    source = project / "Source" / "Demo"
    source.mkdir(parents=True)
    uproject = project / "Demo.uproject"
    uproject.write_text("{}", encoding="utf-8")

    from task_api import task_start

    started = task_start(
        tmp_path,
        request="Create a bounded gameplay class",
        project_file=str(uproject),
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {
                "requiredBeforeWrite": ["unreal_code_sketch_claim_validate"],
            },
            "executablePlanSlices": [{"sliceId": "task", "files": []}],
        },
    )
    sent: list[dict] = []
    server.send = sent.append
    server.handle_tool_call(
        184,
        {
            "name": "unreal_code_sketch_claim_validate",
            "arguments": {
                "sketch": "Board->InitializeDefinitelyMissingBoard();",
                "request": "Create a bounded gameplay class",
                "projectRoot": str(project),
                "targetFiles": ["Source/Demo/NewGameMode.cpp"],
                "changeKind": "new_file",
                "taskAuthorization": started["taskAuthorization"],
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    completion = payload["gateCompletion"]
    assert completion["errorCode"] == "GATE_VALIDATION_FAILED"
    assert completion["nextAction"] == "unreal_project_status"
    assert (
        completion["recoveryContext"]["blockers"][0]["symbol"]
        == "InitializeDefinitelyMissingBoard"
    )
    assert completion["firstBlocker"]["note"]
    assert completion["doNotRetryUnchanged"] is True
    assert completion["reuseCurrentTaskAuthorization"] is True
    assert "Do not rerun the unchanged sketch" in completion["agentInstruction"]


def test_oversized_code_sketch_skips_graph_preparation(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    project = tmp_path / "Project"
    target = project / "Source" / "Demo" / "Worker.cpp"
    target.parent.mkdir(parents=True)
    target.write_text("void Run() {}\n", encoding="utf-8")

    def forbidden_graph(*_args, **_kwargs):
        raise AssertionError("oversized sketch must not prepare a graph")

    def forbidden_generation_contract(*_args, **_kwargs):
        raise AssertionError("oversized sketch must not inspect generation targets")

    server.architecture_graph = forbidden_graph
    monkeypatch.setattr(mod, "build_generation_contract", forbidden_generation_contract)
    sent: list[dict] = []
    server.send = sent.append
    server.handle_tool_call(
        19,
        {
            "name": "unreal_code_sketch_claim_validate",
            "arguments": {
                "sketch": "UTooLarge " + ("x" * mod.MAX_SKETCH_CHARS),
                "projectRoot": str(project),
                "targetFiles": ["Source/Demo/Worker.cpp"],
            },
        },
    )

    result = sent[-1]["result"]
    payload = result["structuredContent"]
    assert payload["errorCode"] == "SKETCH_TOO_LARGE"
    assert payload["graphStatus"]["status"] == "skipped_oversized"
    assert payload["indexLookupMode"] == "not_started"
    assert payload["generationContract"]["writeGate"]["writesAllowed"] is False
    assert len(result["content"][0]["text"]) < 2_000


def test_code_sketch_surfaces_project_graph_failure_without_engine_miss_guidance(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    project = tmp_path / "Project"
    target = project / "Source" / "Demo" / "Worker.cpp"
    target.parent.mkdir(parents=True)
    target.write_text("void Run() {}\n", encoding="utf-8")

    def broken_graph(*_args, **_kwargs):
        raise OSError("graph fixture unavailable")

    server.architecture_graph = broken_graph
    sent: list[dict] = []
    server.send = sent.append
    server.handle_tool_call(
        20,
        {
            "name": "unreal_code_sketch_claim_validate",
            "arguments": {
                "sketch": "UProjectLocalType* Value = nullptr;",
                "projectRoot": str(project),
                "targetFiles": ["Source/Demo/Worker.cpp"],
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["ok"] is False
    assert payload["errorCode"] == "PROJECT_GRAPH_UNAVAILABLE"
    assert payload["graphStatus"]["status"] == "unavailable"
    assert payload["skippedGraphCount"] == 1
    assert payload["results"][0]["verdict"] == "skipped_graph"
    assert "Project-local symbols" in payload["guidance"]
    assert "engine header" not in payload["guidance"]


def test_code_sketch_architecture_proposal_blocks_incomplete_implementation(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    project = tmp_path / "Project"
    target = project / "Source" / "Demo" / "Worker.cpp"
    target.parent.mkdir(parents=True)
    target.write_text("void Run() {}\n", encoding="utf-8")
    sent: list[dict] = []
    server.send = sent.append
    server.handle_tool_call(
        19,
        {
            "name": "unreal_code_sketch_claim_validate",
            "arguments": {
                "sketch": "AActor* Actor = nullptr;",
                "projectRoot": str(project),
                "targetFiles": ["Source/Demo/Worker.cpp"],
                "architectureProposal": {"decision": "add service"},
            },
        },
    )
    payload = sent[-1]["result"]["structuredContent"]
    assert payload["architectureProposalValidation"]["ok"] is False
    assert payload["generationContract"]["writeGate"]["writesAllowed"] is False


def test_code_sketch_architecture_cycle_closes_generation_write_gate(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    project = tmp_path / "Project"
    module_a = project / "Source" / "A"
    module_b = project / "Source" / "B"
    module_a.mkdir(parents=True)
    module_b.mkdir(parents=True)
    target = module_a / "A.h"
    target.write_text('#include "../B/B.h"\n', encoding="utf-8")
    (module_b / "B.h").write_text('#include "../A/A.h"\n', encoding="utf-8")
    sent: list[dict] = []
    server.send = sent.append
    server.handle_tool_call(
        21,
        {
            "name": "unreal_code_sketch_claim_validate",
            "arguments": {
                "sketch": "void Run();",
                "projectRoot": str(project),
                "targetFiles": ["Source/A/A.h"],
                "architectureProposal": {
                    "decision": "preserve module direction",
                    "invariants": ["no dependency cycle"],
                    "impactedSurfaces": ["Source/A/A.h"],
                    "validationPlan": ["compile"],
                    "alternatives": ["extract a shared module"],
                },
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["architectureProposalValidation"]["ok"] is True
    assert payload["generationContract"]["architectureImplementationGate"]["writesAllowed"] is False
    assert payload["generationContract"]["writeGate"]["writesAllowed"] is False


def test_code_sketch_rejects_non_array_object_arguments(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    sent: list[dict] = []
    server.send = sent.append
    server.handle_tool_call(
        22,
        {
            "name": "unreal_code_sketch_claim_validate",
            "arguments": {
                "sketch": "void Run();",
                "targetFiles": {"path": "Source/A.h"},
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["ok"] is False
    assert payload["errorCode"] == "INVALID_TOOL_ARGUMENTS"


def test_semantic_refactor_guard_completes_exact_refactor_gate(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    tool = next(
        item
        for item in server.all_tool_definitions()
        if item["name"] == "unreal_semantic_refactor_guard"
    )
    assert {
        "afterRoot",
        "changedFiles",
        "diffHash",
        "invariants",
        "staticProof",
        "buildProof",
        "runtimeProof",
        "migrationCompatibilityContract",
        "taskAuthorization",
    } <= set(tool["inputSchema"]["properties"])

    project = tmp_path / "Demo"
    candidate = tmp_path / "DemoCandidate"
    target = project / "Source" / "Demo" / "Worker.cpp"
    target.parent.mkdir(parents=True)
    target.write_text("int32 Value() { return 1; }\n", encoding="utf-8")
    uproject = project / "Demo.uproject"
    uproject.write_text("{}", encoding="utf-8")
    shutil.copytree(project, candidate)
    (candidate / "Source" / "Demo" / "Worker.cpp").write_text(
        "namespace { int32 Stable() { return 1; } }\n"
        "int32 Value() { return Stable(); }\n",
        encoding="utf-8",
    )

    from semantic_refactor_guard import compare_semantic_refactor
    from task_api import task_start, task_status

    changed_files = ["Source/Demo/Worker.cpp"]
    probe = compare_semantic_refactor(
        project,
        candidate,
        changed_files=changed_files,
        diff_hash="",
        invariants=[],
        static_proof={},
        build_proof={},
    )
    diff_hash = probe["diffHash"]
    state_result = task_start(
        tmp_path,
        request="Refactor Worker implementation",
        project_file=str(uproject),
        plan_payload={
            "taskKind": "refactor",
            "writeGate": {"writesAllowed": True},
            "orchestration": {"requiredBeforeWrite": []},
        },
    )
    state = state_result["state"]
    assert state["requiredBeforeWrite"] == ["unreal_semantic_refactor_guard"]
    authorization = dict(state_result["taskAuthorization"])
    common_proof = {
        "ok": True,
        "artifactHash": "proof",
        "diffHash": diff_hash,
        "changedFiles": changed_files,
    }
    sent: list[dict] = []
    server.send = sent.append
    server.handle_tool_call(
        220,
        {
            "name": "unreal_semantic_refactor_guard",
            "arguments": {
                "action": "compare",
                "projectRoot": str(project),
                "afterRoot": str(candidate),
                "changedFiles": changed_files,
                "diffHash": diff_hash,
                "invariants": [
                    {
                        "id": "same-value",
                        "description": "Value remains one.",
                        "beforeObserver": {
                            "observer": "Value",
                            "artifactHash": "before",
                            "snapshotHash": probe["beforeSnapshot"]["snapshotHash"],
                            "value": 1,
                        },
                        "afterObserver": {
                            "observer": "Value",
                            "artifactHash": "after",
                            "snapshotHash": probe["afterSnapshot"]["snapshotHash"],
                            "value": 1,
                        },
                    }
                ],
                "staticProof": common_proof,
                "buildProof": common_proof,
                "taskAuthorization": authorization,
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["ok"] is True
    assert payload["gateCompletion"]["ok"] is True
    current = task_status(tmp_path, state_result["taskSessionId"])["state"]
    gate = current["completedGates"]["unreal_semantic_refactor_guard"]
    assert gate["targetSnapshots"][0]["path"] == changed_files[0]
    assert current["pendingGates"] == [], {
        "selectionBinding": current.get("selectionBinding"),
        "completedGates": current.get("completedGates"),
        "selectedTargetSnapshots": current.get("selectedTargetSnapshots"),
    }


def test_semantic_refactor_guard_rejects_live_target_snapshot_race(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path

    project = tmp_path / "Demo"
    candidate = tmp_path / "DemoCandidate"
    target = project / "Source" / "Demo" / "Worker.cpp"
    target.parent.mkdir(parents=True)
    target.write_text("int32 Value() { return 1; }\n", encoding="utf-8")
    shutil.copytree(project, candidate)
    (candidate / "Source" / "Demo" / "Worker.cpp").write_text(
        "namespace { int32 Stable() { return 1; } }\n"
        "int32 Value() { return Stable(); }\n",
        encoding="utf-8",
    )

    changed_files = ["Source/Demo/Worker.cpp"]
    probe = mod.compare_semantic_refactor(
        project,
        candidate,
        changed_files=changed_files,
        diff_hash="",
        invariants=[],
        static_proof={},
        build_proof={},
    )
    diff_hash = probe["diffHash"]
    original_compare = mod.compare_semantic_refactor

    def compare_then_mutate(*args, **kwargs):
        result = original_compare(*args, **kwargs)
        target.write_text("int32 Value() { return 2; }\n", encoding="utf-8")
        return result

    monkeypatch.setattr(mod, "compare_semantic_refactor", compare_then_mutate)
    common_proof = {
        "ok": True,
        "artifactHash": "proof",
        "diffHash": diff_hash,
        "changedFiles": changed_files,
    }
    sent: list[dict] = []
    server.send = sent.append
    server.handle_tool_call(
        221,
        {
            "name": "unreal_semantic_refactor_guard",
            "arguments": {
                "action": "compare",
                "projectRoot": str(project),
                "afterRoot": str(candidate),
                "changedFiles": changed_files,
                "diffHash": diff_hash,
                "invariants": [
                    {
                        "id": "same-value",
                        "description": "Value remains one.",
                        "beforeObserver": {
                            "observer": "Value",
                            "artifactHash": "before",
                            "snapshotHash": probe["beforeSnapshot"]["snapshotHash"],
                            "value": 1,
                        },
                        "afterObserver": {
                            "observer": "Value",
                            "artifactHash": "after",
                            "snapshotHash": probe["afterSnapshot"]["snapshotHash"],
                            "value": 1,
                        },
                    }
                ],
                "staticProof": common_proof,
                "buildProof": common_proof,
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["ok"] is False
    assert payload["writeGate"]["writesAllowed"] is False
    assert payload["writeGate"]["liveSnapshotBound"] is False
    assert any(
        "live target changed after semantic snapshot capture" in issue
        for issue in payload["issues"]
    )


def test_architecture_reasoning_is_available_in_extended_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_EXTENDED_TOOLS", "1")
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    assert "unreal_architecture_reasoning" in {tool["name"] for tool in server.all_tool_definitions()}

    project = tmp_path / "Project"
    source = project / "Source" / "Demo"
    source.mkdir(parents=True)
    (source / "Worker.cpp").write_text("void Run() { CurrentState = 1; }\n", encoding="utf-8")
    sent: list[dict] = []
    server.send = sent.append
    server.handle_tool_call(
        23,
        {
            "name": "unreal_architecture_reasoning",
            "arguments": {"projectRoot": str(project), "symbols": ["Run"]},
        },
    )
    payload = sent[-1]["result"]["structuredContent"]
    assert payload["ok"] is True
    assert payload["stateTransitions"]["transitions"]


def test_architecture_reasoning_is_available_in_essential_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.delenv("MCP_EXTENDED_TOOLS", raising=False)
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    assert "unreal_architecture_reasoning" in {
        tool["name"] for tool in server.all_tool_definitions()
    }


def test_architecture_reasoning_reuses_graph_until_source_changes(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    project = tmp_path / "Project"
    source = project / "Source" / "Demo"
    source.mkdir(parents=True)
    target = source / "Worker.cpp"
    target.write_text("void Run() { CurrentState = 1; }\n", encoding="utf-8")

    first_graph, first_source, _ = server.architecture_graph(str(project))
    second_graph, second_source, _ = server.architecture_graph(str(project))
    target.write_text("void Run() { CurrentState = 2; }\n", encoding="utf-8")
    third_graph, third_source, _ = server.architecture_graph(str(project))

    assert first_source == "rebuilt"
    assert second_source == "memory"
    assert second_graph is first_graph
    assert third_source == "rebuilt"
    assert third_graph is not first_graph


def test_content_verified_architecture_cache_does_not_rehash_unchanged_sources(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    mod = _load_rag_mcp_module()
    import build_symbol_graph

    freshness_calls: list[str] = []
    original_freshness_check = build_symbol_graph.graph_is_fresh_for_root

    def counted_freshness_check(graph, root):
        freshness_calls.append(str(root))
        return original_freshness_check(graph, root)

    monkeypatch.setattr(build_symbol_graph, "graph_is_fresh_for_root", counted_freshness_check)
    server = mod.McpServer(tmp_path / "missing.sqlite")
    project = tmp_path / "Project"
    source = project / "Source" / "Demo"
    source.mkdir(parents=True)
    (source / "Worker.cpp").write_text("void Run() { CurrentState = 1; }\n", encoding="utf-8")

    first_graph, first_source, _ = server.architecture_graph(
        str(project), require_content_verification=True
    )
    calls_after_first_load = len(freshness_calls)
    second_graph, second_source, _ = server.architecture_graph(
        str(project), require_content_verification=True
    )

    assert first_source in {"rebuilt", "persistent_verified"}
    assert calls_after_first_load >= 1
    assert second_source == "memory_verified"
    assert second_graph is first_graph
    assert len(freshness_calls) == calls_after_first_load


def test_architecture_reasoning_rejects_non_object_proposal(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_EXTENDED_TOOLS", "1")
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    project = tmp_path / "Project"
    project.mkdir()
    sent: list[dict] = []
    server.send = sent.append
    server.handle_tool_call(
        24,
        {
            "name": "unreal_architecture_reasoning",
            "arguments": {"projectRoot": str(project), "proposal": "not-an-object"},
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["proposalValidation"]["ok"] is False
    assert payload["proposalValidation"]["implementationGate"]["writesAllowed"] is False
    assert payload["architectureState"]["current"] == "FullReplan"
    assert payload["control"]["phase"] == "unreal_architecture_reasoning"
    assert payload["control"]["status"] == "FullReplan"


def test_architecture_reasoning_fails_closed_on_corrupt_persisted_fsm(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    project = tmp_path / "Project"
    server.set_pending_architecture_handoff(
        project_root=str(project),
        proposal={
            "implementationSlices": [
                {
                    "sliceId": "must-not-leak",
                    "files": ["Source/Demo/Worker.cpp"],
                }
            ]
        },
        proposal_revision="stale-before-integrity-check",
    )
    source = project / "Source" / "Demo"
    source.mkdir(parents=True)
    (source / "Worker.cpp").write_text("void Run() {}\n", encoding="utf-8")
    from architecture_state import _state_path

    state_path = _state_path("corrupt-fsm-chat", str(project))
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text('{"current":', encoding="utf-8")
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        29,
        {
            "name": "unreal_architecture_reasoning",
            "arguments": {
                "projectRoot": str(project),
                "sessionId": "corrupt-fsm-chat",
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["ok"] is False
    assert payload["errorCode"] == "ARCHITECTURE_STATE_INTEGRITY_FAILED"
    assert payload["retryable"] is False
    assert payload["stopCurrentWorkflow"] is True
    assert payload["architectureState"]["current"] == "FailedClosed"
    assert "invalid JSON" in payload["architectureState"]["integrityError"]
    assert payload["control"]["status"] == "FailedClosed"
    assert server.consume_pending_architecture_handoff(project) == {}


def test_architecture_reasoning_refills_missing_focus_evidence_without_replan(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    project = tmp_path / "Project"
    source = project / "Source" / "Demo"
    source.mkdir(parents=True)
    (source / "Worker.cpp").write_text("void Run() {}\n", encoding="utf-8")
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        291,
        {
            "name": "unreal_architecture_reasoning",
            "arguments": {
                "projectRoot": str(project),
                "sessionId": "focus-refill-chat",
                "symbols": ["MissingWorker"],
                "proposal": {
                    "decision": "edit the local worker",
                    "scope": {"networked": False, "runtime": "standalone"},
                    "invariants": ["preserve worker behavior"],
                    "impactedSurfaces": ["Source/Demo/Worker.cpp"],
                    "validationPlan": ["compile"],
                    "implementationFiles": ["Source/Demo/Worker.cpp"],
                },
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["ok"] is False
    assert payload["errorCode"] == "ARCHITECTURE_EVIDENCE_INCOMPLETE"
    assert payload["proposalValidation"]["repairStrategy"] == "evidence_refill"
    assert payload["requiredNextAction"] == "collect_architecture_evidence"
    assert "repairSubmission" not in payload
    assert payload["architectureState"]["current"] == "EvidenceRefill"
    assert server.consume_pending_architecture_handoff(
        project,
        session_id="focus-refill-chat",
    ) == {}


def test_architecture_reasoning_blocks_unchanged_proposal_across_server_memory(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    project = tmp_path / "Project"
    source = project / "Source" / "Demo"
    source.mkdir(parents=True)
    (source / "Worker.cpp").write_text(
        "void Run() { CurrentState = 1; }\n", encoding="utf-8"
    )
    proposal = {
        "decision": "preserve the AGomokuGameMode worker boundary",
        "invariants": ["worker behavior remains stable"],
        "impactedSurfaces": ["Source/Demo/Worker.cpp"],
        "validationPlan": ["static validation", "build", "targeted regression"],
        "alternatives": [
            {
                "name": "keep boundary",
                "rationale": "minimal change",
                "scores": {
                    "complexity": 1,
                    "maintainability": 4,
                    "performance": 4,
                    "risk": 1,
                },
            },
            {
                "name": "split boundary",
                "rationale": "new abstraction",
                "scores": {
                    "complexity": 4,
                    "maintainability": 2,
                    "performance": 3,
                    "risk": 4,
                },
            },
        ],
    }
    sent: list[dict] = []
    server.send = sent.append
    request = {
        "name": "unreal_architecture_reasoning",
        "arguments": {
            "projectRoot": str(project),
            "proposal": proposal,
            "sessionId": "stable-architecture-chat",
        },
    }

    server.handle_tool_call(25, request)
    first_payload = sent[-1]["result"]["structuredContent"]
    assert first_payload.get("errorCode") != (
        "ARCHITECTURE_PROPOSAL_UNCHANGED"
    )
    assert first_payload["proposalRevision"]

    # Clear process-local history to model an MCP host restart; durable state remains.
    import read_query_history as history

    history._HISTORY.clear()
    history._HISTORY_ORDER.clear()
    history._SEMANTIC_INDEX.clear()
    history._TOPIC_INDEX.clear()
    history._CONTINUATION_TOKENS.clear()
    server.handle_tool_call(26, request)

    repeated = sent[-1]["result"]["structuredContent"]
    assert repeated["ok"] is False
    assert repeated["errorCode"] == "ARCHITECTURE_PROPOSAL_UNCHANGED"
    assert repeated["nextActionIsTool"] is False

    patch_request = {
        "name": "unreal_architecture_reasoning",
        "arguments": {
            "projectRoot": str(project),
            "sessionId": "stable-architecture-chat",
            "baseProposalRevision": first_payload["proposalRevision"],
            "proposalPatch": {
                "decision": "preserve the AGomokuGameMode worker boundary with compact patch repair",
            },
        },
    }
    server.handle_tool_call(27, patch_request)
    patched = sent[-1]["result"]["structuredContent"]
    assert patched.get("errorCode") != "ARCHITECTURE_PROPOSAL_UNCHANGED"
    assert patched["proposalPatchApplied"] is True
    assert patched["proposalRevision"] != first_payload["proposalRevision"]

    revised_request = {
        "name": "unreal_architecture_reasoning",
        "arguments": {
            **request["arguments"],
            "proposal": {
                **proposal,
                "decision": "preserve the AGomokuGameMode worker boundary with revised lifecycle cleanup",
            },
        },
    }
    server.handle_tool_call(28, revised_request)
    revised = sent[-1]["result"]["structuredContent"]
    assert revised.get("errorCode") != "ARCHITECTURE_PROPOSAL_UNCHANGED"


def test_architecture_reasoning_applies_exact_path_repairs(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    project = tmp_path / "Project"
    source = project / "Source" / "Demo"
    source.mkdir(parents=True)
    worker = source / "Worker.cpp"
    worker.write_text("void Run() { CurrentState = 1; }\n", encoding="utf-8")
    header = source / "Worker.h"
    header.write_text("void Run();\n", encoding="utf-8")
    invariant = "worker behavior remains stable"
    proposal = {
        "decision": "preserve the worker boundary",
        "scope": {
            "networked": False,
            "runtime": "standalone",
            "risk": "high",
            "validationLevel": "Strict",
        },
        "invariants": [invariant],
        "impactedSurfaces": ["Source/Demo/Worker.cpp", "Source/Demo/Worker.h"],
        "validationPlan": ["static validation", "build", "targeted regression"],
        "validationMatrix": [{"invariant": invariant, "checks": ["targeted regression"]}],
        "alternatives": [
            {
                "name": "keep boundary",
                "rationale": "minimal change",
                "scores": {
                    "complexity": 1,
                    "maintainability": 4,
                    "performance": 4,
                    "risk": 1,
                },
            },
            {
                "name": "split boundary",
                "rationale": "new abstraction",
                "scores": {
                    "complexity": 4,
                    "maintainability": 2,
                    "performance": 3,
                    "risk": 4,
                },
            },
        ],
        "implementationFiles": ["Source/Demo/Worker.cpp", "Source/Demo/Worker.h"],
        "selectedAlternative": "keep boundary",
        "ownership": {
            "stateOwner": "Demo worker",
            "dataOwner": "Demo worker",
            "lifecycleOwner": "Demo module",
            "failurePolicy": "keep the current implementation",
            "recoveryPolicy": "revert the bounded slice",
        },
        "stateInventory": [
            {
                "state": "worker state",
                "owner": "Demo worker",
                "lifetime": "module",
                "authority": "local",
                "source": "existing",
                "cleanup": "module shutdown",
            }
        ],
        "lifecycleTransitions": [
            {
                "event": "worker update",
                "owner": "Demo worker",
                "preconditions": ["worker initialized"],
                "commitPoint": "after validation",
                "failureRecovery": "leave state unchanged",
                "cleanup": "release worker state",
            }
        ],
        "implementationSlices": [
            {
                "sliceId": "worker",
                "files": ["Source/Demo/Worker.cpp", "Source/Demo/Worker.h"],
                "invariants": [invariant],
                "validation": ["build", "targeted regression"],
            }
        ],
    }
    sent: list[dict] = []
    server.send = sent.append
    server.handle_tool_call(
        30,
        {
            "name": "unreal_architecture_reasoning",
            "arguments": {
                "projectRoot": str(project),
                "proposal": proposal,
                "sessionId": "repair-chat",
            },
        },
    )
    first = sent[-1]["result"]["structuredContent"]
    assert any(
        row["jsonPath"] == "migrationPlan"
        for row in first["proposalValidation"]["repairRequirements"]
    )
    assert first["repairSubmission"]["mode"] == "proposalRepairs"
    migration_repair = next(
        row
        for row in first["repairSubmission"]["requiredRepairs"]
        if row["jsonPath"] == "migrationPlan"
    )
    assert migration_repair["expectedType"] == "array"
    assert first["repairSubmission"]["requiredJsonPaths"] == ["migrationPlan"]

    server.handle_tool_call(
        31,
        {
            "name": "unreal_architecture_reasoning",
            "arguments": {
                "projectRoot": str(project),
                "sessionId": "repair-chat",
                "baseProposalRevision": first["proposalRevision"],
                    "proposalRepairs": [
                        {"jsonPath": "migrationPlan", "value": ["first partial value"]},
                        {"jsonPath": "migrationPlan", "value": {"wrong": "type"}},
                ],
            },
        },
    )
    rejected = sent[-1]["result"]["structuredContent"]
    assert rejected["errorCode"] == "ARCHITECTURE_PROPOSAL_REPAIR_PATH_MISMATCH"
    assert rejected["proposalRevision"] == first["proposalRevision"]
    assert rejected["duplicateJsonPaths"] == ["migrationPlan"]
    assert rejected["valueTypeErrors"] == [
        {"jsonPath": "migrationPlan", "expectedType": "array", "actualType": "dict"}
    ]

    server.handle_tool_call(
        32,
        {
            "name": "unreal_architecture_reasoning",
            "arguments": {
                "projectRoot": str(project),
                "sessionId": "repair-chat",
                "baseProposalRevision": first["proposalRevision"],
                "proposalRepairs": [
                    {
                        "jsonPath": "migrationPlan",
                        "value": ["add compatible worker behavior before moving call sites"],
                    },
                ],
            },
        },
    )
    repaired = sent[-1]["result"]["structuredContent"]
    assert repaired.get("errorCode") != "ARCHITECTURE_PROPOSAL_REPAIR_PATH_MISMATCH"
    assert repaired["proposalRepairsApplied"] is True
    assert repaired["proposalRevision"] != first["proposalRevision"]


def test_architecture_core_contradiction_requires_full_replan_and_source_rebind(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    project = tmp_path / "Project"
    source = project / "Source" / "Demo"
    source.mkdir(parents=True)
    (source / "DemoGameMode.h").write_text(
        '#include "GameFramework/GameModeBase.h"\n'
        'class ADemoGameMode : public AGameModeBase {};\n',
        encoding="utf-8",
    )
    (source / "DemoGameState.h").write_text(
        '#include "GameFramework/GameStateBase.h"\n'
        'class ADemoGameState : public AGameStateBase {};\n',
        encoding="utf-8",
    )
    (source / "DemoPlayerController.h").write_text(
        '#include "GameFramework/PlayerController.h"\n'
        'class ADemoPlayerController : public APlayerController {};\n',
        encoding="utf-8",
    )
    proposal = {
        "decision": "Use authoritative multiplayer with GameMode-owned client RPC",
        "invariants": ["Only authority commits state"],
        "impactedSurfaces": ["ADemoGameMode", "ADemoPlayerController"],
        "validationPlan": ["RPC ownership and owning connection callability"],
        "alternatives": ["GameMode RPC", "controller RPC"],
        "networking": {
            "authorityOwner": "ADemoGameMode",
            "clientInitiated": True,
            "requestPath": [
                "client input",
                "ADemoGameMode::ServerSetReady [new Server RPC]",
                "ADemoGameState::ApplyReady",
            ],
            "rpcOwner": "ADemoGameMode",
            "owningConnection": "owned by the requesting client's owning connection",
            "serverValidation": "validate authority",
            "replicatedState": ["ADemoGameState::Phase"],
        },
        "stateInventory": [{
            "state": "Phase", "owner": "ADemoGameState", "lifetime": "world",
            "authority": "server authoritative", "source": "new", "cleanup": "reset",
        }],
        "lifecycleTransitions": [{
            "event": "request", "owner": "ADemoGameMode", "preconditions": ["authority"],
            "commitPoint": "after validation", "failureRecovery": "no mutation", "cleanup": "none",
        }],
    }
    sent: list[dict] = []
    server.send = sent.append
    server.handle_tool_call(
        40,
        {
            "name": "unreal_architecture_reasoning",
            "arguments": {
                "projectRoot": str(project),
                "sessionId": "full-replan-chat",
                "proposal": proposal,
            },
        },
    )
    first = sent[-1]["result"]["structuredContent"]
    assert first["proposalValidation"]["repairStrategy"] == "full_replan"
    assert first["repairSubmission"]["mode"] == "fullProposal"
    assert first["requiredNextAction"] == "submit_full_architecture_proposal"

    cosmetically_revised = {
        **proposal,
        "decision": "Use authoritative multiplayer after independently reconsidering the design",
    }
    server.handle_tool_call(
        41,
        {
            "name": "unreal_architecture_reasoning",
            "arguments": {
                "projectRoot": str(project),
                "sessionId": "full-replan-chat",
                "proposal": cosmetically_revised,
            },
        },
    )
    unchanged_core = sent[-1]["result"]["structuredContent"]
    assert unchanged_core["errorCode"] == "ARCHITECTURE_PROPOSAL_REPLAN_CORE_UNCHANGED"
    assert unchanged_core["proposalRevision"] == first["proposalRevision"]
    assert unchanged_core["rejectedCandidateRevision"] != first["proposalRevision"]
    assert "networking.rpcOwner" in unchanged_core["requiredChangedPaths"]
    assert unchanged_core["doNotRetryUnchangedCore"] is True
    assert unchanged_core["repairSubmission"]["mode"] == "fullProposal"

    server.handle_tool_call(
        42,
        {
            "name": "unreal_architecture_reasoning",
            "arguments": {
                "projectRoot": str(project),
                "sessionId": "full-replan-chat",
                "baseProposalRevision": first["proposalRevision"],
                "proposalRepairs": [{
                    "jsonPath": "networking.rpcOwner",
                    "value": "ADemoPlayerController",
                }],
            },
        },
    )
    rejected_patch = sent[-1]["result"]["structuredContent"]
    assert rejected_patch["errorCode"] == "ARCHITECTURE_PROPOSAL_REPLAN_REQUIRED"
    assert rejected_patch["proposalRevision"] == first["proposalRevision"]

    with (source / "DemoGameState.h").open("a", encoding="utf-8") as handle:
        handle.write("// source changed after proposal\n")
    server.handle_tool_call(
        43,
        {
            "name": "unreal_architecture_reasoning",
            "arguments": {
                "projectRoot": str(project),
                "sessionId": "full-replan-chat",
                "baseProposalRevision": first["proposalRevision"],
                "proposalPatch": {"decision": "try to patch stale proposal"},
            },
        },
    )
    source_changed = sent[-1]["result"]["structuredContent"]
    assert source_changed["errorCode"] == "ARCHITECTURE_PROPOSAL_SOURCE_CHANGED"
    assert source_changed["repairSubmission"]["mode"] == "fullProposal"


def test_runtime_debug_experiment_persists_session_and_completes_gate(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    from task_api import task_start, task_status

    project = tmp_path / "Demo"
    target = project / "Source" / "Demo" / "Private" / "HealthComponent.cpp"
    target.parent.mkdir(parents=True)
    target.write_text("// before\n", encoding="utf-8")
    uproject = project / "Demo.uproject"
    uproject.write_text("{}", encoding="utf-8")
    started = task_start(
        tmp_path,
        request="Fix runtime damage bug",
        project_file=str(uproject),
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True},
            "orchestration": {"requiredBeforeWrite": ["unreal_runtime_debug_session"]},
        },
    )
    authorization = dict(started["taskAuthorization"])
    sent: list[dict] = []
    server.send = sent.append
    server.handle_tool_call(
        25,
        {
            "name": "unreal_runtime_debug_session",
            "arguments": {
                "action": "prepare",
                "taskAuthorization": authorization,
                "symptom": "health does not decrease",
                "reproductionSteps": ["start PIE", "apply damage"],
                "observer": {"id": "health-log", "signal": "health value"},
                "baselineEvidence": {
                    "kind": "log",
                    "location": "Saved/Logs/Demo.log",
                    "observation": "health remains 100",
                },
                "hypotheses": [
                    {
                        "claim": "TakeDamage never forwards to HealthComponent",
                        "falsification": "trace the same damage event into ReceiveDamage",
                    }
                ],
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["ok"] is True
    assert payload["persisted"] is True
    assert payload["gateCompletion"]["errorCode"] == "RUNTIME_EXPERIMENT_REQUIRED"
    session = payload["session"]
    authorization = dict(payload["taskAuthorization"])
    server.handle_tool_call(
        26,
        {
            "name": "unreal_runtime_debug_session",
            "arguments": {
                "action": "record_experiment",
                "taskAuthorization": authorization,
                "hypothesisId": session["selectedHypothesisId"],
                "reproductionFingerprint": session["reproductionFingerprint"],
                "observer": session["observer"],
                "experimentEvidence": {
                    "kind": "trace",
                    "location": "Saved/Profiling/damage.utrace",
                    "observation": "damage event never reaches HealthComponent",
                    "traceSummary": {"receiveDamageCalls": 0},
                },
                "experimentOutcome": "supported",
            },
        },
    )
    experiment = sent[-1]["result"]["structuredContent"]
    assert experiment["ok"] is True, json.dumps(experiment, ensure_ascii=False)
    assert experiment["session"]["status"] == "ready_for_patch_candidates"
    authorization = dict(experiment["taskAuthorization"])
    server.handle_tool_call(
        27,
        {
            "name": "unreal_runtime_debug_session",
            "arguments": {
                "action": "compare_patch_candidates",
                "taskAuthorization": authorization,
                "patchCandidates": [
                    {
                        "id": "candidate-a",
                        "changedFiles": ["Source/Demo/Private/HealthComponent.cpp"],
                        "diffHash": "diff-a",
                        "sandboxEvidence": {
                            "isolatedRoot": "sandbox/a",
                            "staticPassed": True,
                            "staticProof": {"ok": True, "artifactHash": "static-a"},
                            "buildPassed": True,
                            "buildProof": {"ok": True, "artifactHash": "build-a"},
                            "runtimeCompatible": True,
                            "invariantResults": {"health owner preserved": True},
                        },
                    },
                    {
                        "id": "candidate-b",
                        "changedFiles": ["Source/Demo/Private/HealthComponent.cpp"],
                        "diffHash": "diff-b",
                        "sandboxEvidence": {
                            "isolatedRoot": "sandbox/b",
                            "staticPassed": True,
                            "staticProof": {"ok": True, "artifactHash": "static-b"},
                            "buildPassed": True,
                            "buildProof": {"ok": True, "artifactHash": "build-b"},
                            "runtimeCompatible": True,
                            "invariantResults": {"health owner preserved": True},
                        },
                    },
                ],
                "selectedPatchCandidateId": "candidate-a",
                "patchSelectionRationale": (
                    "candidate-a keeps damage forwarding in the existing owner"
                ),
            },
        },
    )
    comparison = sent[-1]["result"]["structuredContent"]
    assert comparison["ok"] is True
    assert comparison["gateCompletion"]["ok"] is True
    executor_route = comparison["gateCompletion"]["toolRoute"]
    assert executor_route["roleSession"] == "executor"
    assert "replace_in_file" in executor_route["activeTools"]
    assert "unreal_runtime_debug_session" in executor_route["activeTools"]

    runtime_authorization = dict(
        comparison["gateCompletion"]["taskAuthorization"]
    )
    server.handle_tool_call(
        28,
        {
            "name": "unreal_runtime_debug_session",
            "arguments": {
                "action": "record_patch",
                "taskAuthorization": runtime_authorization,
                "changedFiles": [
                    "Source/Demo/Private/HealthComponent.cpp"
                ],
                "patchSummary": "Forward damage through the existing owner.",
                "selectedPatchCandidateId": "candidate-a",
                "appliedDiffHash": "diff-a",
                "buildProof": {
                    "ok": True,
                    "artifactHash": "build-live-a",
                },
            },
        },
    )
    patched = sent[-1]["result"]["structuredContent"]
    assert patched["ok"] is True
    assert patched["session"]["status"] == "awaiting_same_observer_verification"
    assert patched["toolRoute"]["roleSession"] == "verifier"
    assert "unreal_runtime_debug_session" in patched["toolRoute"]["activeTools"]

    server.handle_tool_call(
        29,
        {
            "name": "unreal_runtime_debug_session",
            "arguments": {
                "action": "verify",
                "taskAuthorization": patched["taskAuthorization"],
                "reproductionFingerprint": patched["session"][
                    "reproductionFingerprint"
                ],
                "observer": patched["session"]["observer"],
                "afterEvidence": {
                    "kind": "log",
                    "location": "Saved/Logs/Demo.log:220",
                    "observation": "health decreases after damage",
                },
                "outcome": "resolved",
            },
        },
    )
    verified = sent[-1]["result"]["structuredContent"]
    assert verified["ok"] is True
    assert verified["session"]["status"] == "runtime_verified"
    current = task_status(tmp_path, started["taskSessionId"])["state"]
    assert current["runtimeDebugSession"]["status"] == "runtime_verified"
    assert current["pendingGates"] == []


def test_review_claim_validator_accepts_legacy_strings_and_evidence_packets(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    tool = next(
        item for item in server.all_tool_definitions() if item["name"] == "unreal_review_claim_validate"
    )
    claim_items = tool["inputSchema"]["properties"]["claims"]["items"]
    variants = claim_items["oneOf"]
    assert {variant.get("type") for variant in variants} == {"string", "object"}
    packet = next(variant for variant in variants if variant.get("type") == "object")
    assert {
        "claim",
        "verdict",
        "severity",
        "proofLevel",
        "claimType",
        "evidence",
        "behaviorPath",
        "counterEvidence",
        "unknowns",
    }.issubset(set(packet["required"]))
    behavior_item = packet["properties"]["behaviorPath"]["items"]
    assert "stageStatus" in behavior_item["required"]


def test_agent_essential_tool_names_documented():
    """Keep Python test set aligned with server.js ESSENTIAL_AGENT_TOOL_NAMES."""
    server_js = (ROOT / "lmstudio-unreal-agent-mcp" / "src" / "server.js").read_text(encoding="utf-8")
    for name in AGENT_ESSENTIAL:
        assert f'"{name}"' in server_js


def test_agent_extended_delete_tools_are_documented_in_server() -> None:
    server_js = (ROOT / "lmstudio-unreal-agent-mcp" / "src" / "server.js").read_text(encoding="utf-8")

    assert '"propose_file_deletions"' in server_js
    assert '"delete_file"' in server_js
    assert 'Required before delete_file' in server_js
