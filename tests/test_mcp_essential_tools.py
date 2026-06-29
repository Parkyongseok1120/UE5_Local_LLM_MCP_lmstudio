#!/usr/bin/env python
"""Tests for MCP_ESSENTIAL_TOOLS filtering on unreal-rag MCP."""

from __future__ import annotations

import importlib.util
import os
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
}

AGENT_ESSENTIAL = {
    "get_workspace_info",
    "get_active_project",
    "list_directory",
    "read_file",
    "read_file_range",
    "replace_in_file",
    "write_file",
    "search_files",
    "build_unreal_project",
    "read_unreal_logs",
}


def _load_rag_mcp_module():
    path = SCRIPTS / "unreal_rag_mcp.py"
    spec = importlib.util.spec_from_file_location("unreal_rag_mcp", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_essential_tools_disabled_exposes_many_tools(monkeypatch, tmp_path):
    monkeypatch.delenv("MCP_ESSENTIAL_TOOLS", raising=False)
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    names = {tool["name"] for tool in server.all_tool_definitions()}
    assert "clangd_goto_definition" in names
    assert len(names) > len(RAG_ESSENTIAL)


def test_essential_tools_enabled_filters_rag_tools(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    names = {tool["name"] for tool in server.all_tool_definitions()}
    assert names == RAG_ESSENTIAL


def test_unreal_agent_plan_description_mentions_chat_first(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    mod = _load_rag_mcp_module()
    server = mod.McpServer(tmp_path / "missing.sqlite")
    plan = next(t for t in server.all_tool_definitions() if t["name"] == "unreal_agent_plan")
    assert "FIRST" in plan["description"]
    assert "toolPolicy" in plan["description"]


def test_agent_essential_tool_names_documented():
    """Keep Python test set aligned with server.js ESSENTIAL_AGENT_TOOL_NAMES."""
    server_js = (ROOT / "lmstudio-unreal-agent-mcp" / "src" / "server.js").read_text(encoding="utf-8")
    for name in AGENT_ESSENTIAL:
        assert f'"{name}"' in server_js
