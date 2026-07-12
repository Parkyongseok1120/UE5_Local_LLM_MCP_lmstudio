#!/usr/bin/env python
"""Runtime tool exposure policy — manifest is the source of truth for stable profiles."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from plan_consistency import RAG_EXTENDED_ONLY

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "config" / "stable_tool_manifest.json"

RAG_EXTENDED_PROFILE_TOOLS = frozenset(
    RAG_EXTENDED_ONLY
    | {
        "unreal_refactor_manager_plan",
        "unreal_material_porting_plan_validate",
        "unreal_editor_metadata_status",
        "unreal_run_editor_export",
        "unreal_sync_editor_metadata",
        "unreal_asset_graph_lookup",
        "unreal_blueprint_claim_validate",
        "unreal_material_claim_validate",
        "unreal_node_plan_validate",
        "unreal_render_report",
        "unreal_rag_rebuild_status",
        "clangd_document_symbols",
        "clangd_find_references",
        "clangd_goto_definition",
        "unreal_generate_compile_loop",
        "unreal_genre_scope_validate",
        "unreal_open_project_picker",
        "unreal_project_architecture",
        "unreal_project_graph_query",
        "unreal_review_claim_validate",
        "unreal_runtime_config_check",
    }
)


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def essential_tools_enabled() -> bool:
    return env_flag("MCP_ESSENTIAL_TOOLS")


def extended_tools_enabled() -> bool:
    return env_flag("MCP_EXTENDED_TOOLS")


def control_plane_tools_enabled() -> bool:
    return env_flag("ALLOW_CONTROL_PLANE_TOOLS")


@lru_cache(maxsize=1)
def load_stable_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8-sig"))


def rag_essential_tool_names() -> frozenset[str]:
    return frozenset(load_stable_manifest().get("ragEssential") or [])


def rag_hidden_tool_names() -> frozenset[str]:
    return frozenset(load_stable_manifest().get("ragHiddenUntilControlPlane") or [])


def agent_essential_tool_names() -> frozenset[str]:
    return frozenset(load_stable_manifest().get("agentEssential") or [])


def agent_hidden_tool_names() -> frozenset[str]:
    return frozenset(load_stable_manifest().get("agentHiddenUntilControlPlane") or [])


def callable_rag_tool_names(all_registered: Iterable[str]) -> frozenset[str]:
    registered = frozenset(all_registered)
    essential = rag_essential_tool_names()
    hidden = rag_hidden_tool_names()
    visible = set(registered)
    if not control_plane_tools_enabled():
        visible -= hidden
    if extended_tools_enabled():
        return frozenset(visible)
    allowed = set(essential)
    if control_plane_tools_enabled():
        allowed |= hidden
    return frozenset(name for name in visible if name in allowed)


AGENT_EXTENDED_PROFILE_TOOLS = frozenset(
    {
        "set_active_project",
        "detect_unreal_project",
        "list_unreal_projects",
        "open_active_project_picker",
        "run_command",
        "refactor_impact_scan",
        "refactor_plan_validate",
        "propose_file_deletions",
        "delete_file",
        "record_bootstrap_step",
    }
)


def callable_agent_tool_names(all_registered: Iterable[str]) -> frozenset[str]:
    registered = frozenset(all_registered)
    essential = agent_essential_tool_names()
    hidden = agent_hidden_tool_names()
    visible = set(registered)
    if not control_plane_tools_enabled():
        visible -= hidden
    if extended_tools_enabled():
        return frozenset(visible)
    allowed = set(essential)
    if control_plane_tools_enabled():
        allowed |= hidden
    return frozenset(name for name in visible if name in allowed)


def tool_not_callable_payload(tool_name: str) -> dict:
    message = f"Tool '{tool_name}' is not callable in the current MCP exposure profile."
    return {
        "ok": False,
        "errorCode": "TOOL_NOT_CALLABLE",
        "error": message,
        "phase": "failed",
        "userMessage": message,
        "agentInstruction": "Use tools/list to see callable tools for this MCP profile.",
        "retryable": False,
    }
