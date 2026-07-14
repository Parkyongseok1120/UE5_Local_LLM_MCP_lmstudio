#!/usr/bin/env python
"""Bounded progressive tool-family discovery."""

from __future__ import annotations

from typing import Any

from plan_consistency import exposed_rag_tools
from tool_policy import TERMINAL_ACTIONS, exposure_inventory

TOOL_FAMILIES: dict[str, list[str]] = {
    "project_context": ["unreal_get_active_project", "unreal_set_active_project"],
    "source_search": ["unreal_rag_search", "search_files", "read_file"],
    "symbol_lookup": ["unreal_symbol_lookup"],
    "architecture": ["unreal_agent_plan", "unreal_project_architecture"],
    "asset_metadata": ["unreal_editor_metadata_status", "unreal_asset_graph_lookup"],
    "validation": [
        "unreal_code_sketch_claim_validate",
        "unreal_review_claim_validate",
        "unreal_diagram_validate",
    ],
    "write": ["replace_in_file", "write_file"],
    "build": ["build_unreal_project", "unreal_start_compile_loop"],
    "runtime_logs": ["unreal_compile_loop_status"],
    "version_control": [],
    "report_rendering": ["unreal_render_report"],
}

MAX_CANDIDATES = 6


def _descriptor(name: str, *, family: str) -> dict[str, Any]:
    from tool_exposure import agent_essential_tool_names, rag_essential_tool_names

    inventory = exposure_inventory()
    essential = set(inventory.get("essentialProfile") or []) | set(rag_essential_tool_names()) | set(
        agent_essential_tool_names()
    )
    extended = set(inventory.get("extendedProfile") or [])
    if name in essential:
        profile = "essential"
    elif name in extended:
        profile = "extended"
    else:
        profile = "agent"
    return {
        "name": name,
        "family": family,
        "server": "rag" if name.startswith("unreal_") else "agent",
        "profileAvailability": profile,
        "readOnly": name not in {"replace_in_file", "write_file", "build_unreal_project"},
        "terminalAction": name in TERMINAL_ACTIONS,
    }


def discover_tool_candidates(
    *,
    family: str,
    attempted: set[str] | None = None,
    failed: set[str] | None = None,
) -> list[dict[str, Any]]:
    attempted = attempted or set()
    failed = failed or set()
    allowed = exposed_rag_tools()
    names = [
        name
        for name in TOOL_FAMILIES.get(family, [])
        if name not in TERMINAL_ACTIONS and name not in attempted and name not in failed
    ]
    if family == "source_search":
        names = [name for name in names if not name.startswith("unreal_") or name in allowed]
    descriptors = [_descriptor(name, family=family) for name in names if not _descriptor(name, family=family)["terminalAction"]]
    return descriptors[:MAX_CANDIDATES]
