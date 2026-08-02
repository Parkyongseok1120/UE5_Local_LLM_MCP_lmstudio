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
    assert payload["taskAuthorization"]["planRevision"] == "2"
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
    """Advertised Essential catalog stays stable; route errors block at CallTool."""
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    mod = _load_rag_mcp_module()
    from task_api import task_root, task_start
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
    state_path = task_root(tmp_path, started["taskSessionId"]) / "state.json"
    server = mod.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    sent: list[dict] = []
    server.send = sent.append

    def catalog_names() -> set[str]:
        return {tool["name"] for tool in server.all_tool_definitions()}

    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["continuity"]["lease"]["expiresAt"] = "2000-01-01T00:00:00+00:00"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    assert catalog_names() == essential
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
    assert catalog_names() == essential
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
    assert catalog_names() == essential
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


def test_active_route_keeps_rag_catalog_stable(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    mod = _load_rag_mcp_module()
    from task_api import task_start
    from tool_exposure import load_stable_manifest

    essential = set(load_stable_manifest()["ragEssential"])
    task_start(
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
    assert {tool["name"] for tool in server.all_tool_definitions()} == essential
    diag = server.tool_catalog_diagnostics()
    assert diag["routeContextStatus"] == "active"
    assert diag["advertisedCount"] == len(essential)


def test_corrupt_route_keeps_catalog_and_recovery_controls(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    mod = _load_rag_mcp_module()
    from task_api import task_root, task_start
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
    state_path = task_root(tmp_path, started["taskSessionId"]) / "state.json"
    state_path.write_text("{not-json", encoding="utf-8")
    server = mod.McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    sent: list[dict] = []
    server.send = sent.append
    names = {tool["name"] for tool in server.all_tool_definitions()}
    assert names == essential
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
    assert payload["stopCurrentWorkflow"] is True


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
    assert payload["executionContract"]["maxFilesPerSlice"] == 2
    assert payload["executionContract"]["splitBeforeFirstGate"] is True
    assert payload["executionContract"]["existingFileMutationTool"] == "replace_in_file"
    assert payload["executionContract"]["maxChangedLinesPerMutation"] == 60
    assert payload["executionContract"]["maxCombinedPatchChars"] == 8000
    assert payload["executionContract"]["fullExistingFileContentInBundleAllowed"] is False
    assert "ownerCapability" in payload["agentInstruction"]
    assert "Never send a full existing file" in payload["agentInstruction"]
    routing = payload["contextCompactorRouting"]
    assert routing["policy"] == "advisory"
    assert routing["active"] is False
    assert routing["blocksWrites"] is False
    assert routing["directModelAllowed"] is True


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
    assert payload["nextActionArgs"]["responseMode"] == "compact"
    assert payload["nextActionArgs"]["taskAuthorization"] == payload["taskAuthorization"]
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
    assert payload["gateCompletion"]["taskAuthorization"]["routePhase"] == "executor"
    text_result = sent[-1]["result"]["content"][0]["text"]
    assert "nextTaskAuthorization=" in text_result
    assert payload["gateCompletion"]["taskAuthorization"]["routeHash"] in text_result
    assert '"phase":"executor"' in text_result
    assert "do not synthesize routePhase or routeHash" in text_result


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
    assert payload["nextAction"] == "unreal_symbol_lookup"
    assert payload["nextActionArgs"] == {
        "query": "UDefinitelyMissingApi",
        "top_k": 8,
        "detailLevel": "compact",
    }
    assert payload["doNotRetryUnchanged"] is True
    assert text_result.startswith("GATE_FAILED: writes remain closed")
    assert "blockingSymbols=UDefinitelyMissingApi:unverified" in text_result
    assert "firstBlocker=UDefinitelyMissingApi:unverified" in text_result
    assert 'nextAction=unreal_symbol_lookup {"query":"UDefinitelyMissingApi"' in text_result
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
    assert payload["nextActionArgs"]["allowedTargetFiles"] == [
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
    assert completion["nextAction"] == "unreal_symbol_lookup"
    assert completion["nextActionArgs"]["query"] == "InitializeDefinitelyMissingBoard"
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
    assert experiment["ok"] is True
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
