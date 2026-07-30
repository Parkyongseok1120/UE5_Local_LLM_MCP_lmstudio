#!/usr/bin/env python
"""Tests for MCP_ESSENTIAL_TOOLS filtering on unreal-rag MCP."""

from __future__ import annotations

import importlib.util
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
    "unreal_code_sketch_claim_validate",
    "unreal_review_claim_validate",
    "unreal_diagram_validate",
    "unreal_project_status",
}

AGENT_ESSENTIAL = {
    "get_workspace_info",
    "get_active_project",
    "list_directory",
    "read_file",
    "read_file_range",
    "read_symbol",
    "replace_in_file",
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
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    names = {tool["name"] for tool in server.all_tool_definitions()}
    assert names == RAG_ESSENTIAL
    assert "clangd_goto_definition" not in names


def test_essential_tools_enabled_filters_rag_tools(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.delenv("MCP_EXTENDED_TOOLS", raising=False)
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
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
