#!/usr/bin/env python
"""Tests for MCP tool registry dispatch in unreal-rag MCP."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _load_rag_mcp_module():
    path = SCRIPTS / "unreal_rag_mcp.py"
    spec = importlib.util.spec_from_file_location("unreal_rag_mcp", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _tool_descriptions(server) -> list[dict]:
    return server._all_tool_definitions_unfiltered()


def test_essential_tool_names_exist_in_tool_descriptions(tmp_path):
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    names = {tool["name"] for tool in _tool_descriptions(server)}
    missing = mod.ESSENTIAL_TOOL_NAMES - names
    assert not missing, f"ESSENTIAL tools missing from tool descriptions: {sorted(missing)}"


def test_registry_tool_names_are_subset_of_all_tools(tmp_path):
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    all_names = {tool["name"] for tool in _tool_descriptions(server)}
    registry_names = mod._MCP_TOOL_REGISTRY.names()
    assert registry_names.issubset(all_names)
    assert "unreal_rag_refresh" in registry_names
    assert "unreal_rag_search" in registry_names
    assert "unreal_get_active_project" in registry_names
    assert len(registry_names) >= 10
    assert "unreal_code_sketch_claim_validate" in registry_names
    assert "unreal_node_plan_validate" in registry_names
    assert "unreal_render_report" in registry_names


def test_no_duplicate_tool_names(tmp_path):
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    names = [tool["name"] for tool in _tool_descriptions(server)]
    assert len(names) == len(set(names)), f"Duplicate tool names: {[n for n in names if names.count(n) > 1]}"
    registry = mod.build_mcp_tool_registry()
    assert len(registry.names()) == len(set(registry.names()))
def test_public_schemas_cover_handler_consumed_arguments(tmp_path):
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    definitions = {tool["name"]: tool for tool in _tool_descriptions(server)}

    expected = {
        "unreal_set_active_project": {"prepare", "force"},
        "unreal_compile_loop_status": {"sinceProgressSequence", "verbose"},
        "unreal_agent_session": {
            "scope", "detailLevel", "continuationToken", "sessionId", "includeRawMatches",
        },
        "unreal_render_report": {"diagramMode", "allowOverwrite"},
        "unreal_task_start": {"startBackgroundJob"},
        "unreal_architecture_decision_approve": {"approvalToken"},
    }

    for name, required_properties in expected.items():
        schema = definitions[name]["inputSchema"]
        properties = set(schema["properties"])
        assert required_properties.issubset(properties), (name, required_properties - properties)

    approval_schema = definitions["unreal_architecture_decision_approve"]["inputSchema"]
    assert "approvalToken" in approval_schema["required"]

    registry_render = mod._MCP_TOOL_REGISTRY.get("unreal_render_report")
