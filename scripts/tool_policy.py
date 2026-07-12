#!/usr/bin/env python
"""Load tool orchestration policy from config/tool_orchestration.json."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

ToolKind = Literal["tool", "optional_tool", "terminal_action", "checkpoint"]

RAG_MCP_TOOLS = frozenset(
    {
        "unreal_get_active_project",
        "unreal_set_active_project",
        "unreal_rag_health",
        "unreal_agent_plan",
        "unreal_rag_search",
        "unreal_symbol_lookup",
        "unreal_agent_session",
        "unreal_rag_capabilities",
        "unreal_code_sketch_claim_validate",
        "unreal_rag_refresh",
        "unreal_start_rag_refresh",
        "unreal_rag_refresh_status",
        "unreal_start_compile_loop",
        "unreal_compile_loop_status",
        "unreal_cancel_compile_loop",
        "unreal_refactor_manager_plan",
        "unreal_refactor_impact_scan",
        "unreal_refactor_plan_validate",
        "unreal_diagram_validate",
        "unreal_render_report",
        "unreal_project_status",
        "unreal_task_start",
        "unreal_task_status",
        "unreal_task_approve",
        "unreal_task_cancel",
        "unreal_task_resume",
        "unreal_project_prepare",
        "unreal_job_log_read",
        "unreal_architecture_decision_status",
        "unreal_architecture_decision_approve",
        "unreal_architecture_decision_revoke",
    }
)

TERMINAL_ACTIONS = frozenset({"answer_with_evidence", "stop_with_plan", "report_blocked"})
CHECKPOINTS = frozenset({"static_validate", "ubt_build", "architecture_approval"})


@lru_cache(maxsize=1)
def load_tool_orchestration() -> dict[str, Any]:
    path = Path(__file__).resolve().parent.parent / "config" / "tool_orchestration.json"
    if not path.is_file():
        return {"tasks": {}}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def normalize_tool_entry(entry: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(entry, dict):
        kind = str(entry.get("kind") or "tool")
        name = str(entry.get("name") or "")
        return {"kind": kind, "name": name}
    text = str(entry or "").strip()
    if text in TERMINAL_ACTIONS:
        return {"kind": "terminal_action", "name": text}
    if text in CHECKPOINTS:
        return {"kind": "checkpoint", "name": text}
    if text.startswith("optional:"):
        return {"kind": "optional_tool", "name": text.split(":", 1)[1]}
    return {"kind": "tool", "name": text}


def typed_sequence_for_task(task_kind: str) -> list[dict[str, Any]]:
    cfg = load_tool_orchestration()
    task = (cfg.get("tasks") or {}).get(task_kind) or {}
    seq = task.get("sequence") or []
    typed = task.get("typedSequence")
    if isinstance(typed, list) and typed:
        return [normalize_tool_entry(item) for item in typed]
    return [normalize_tool_entry(item) for item in seq]


def tool_sequence_for_task(task_kind: str) -> list[str]:
    return [
        item["name"]
        for item in typed_sequence_for_task(task_kind)
        if item.get("name") and item.get("kind") not in {"terminal_action", "checkpoint"}
    ]


def writes_allowed_for_task(task_kind: str) -> bool:
    cfg = load_tool_orchestration()
    task = (cfg.get("tasks") or {}).get(task_kind) or {}
    return bool(task.get("writesAllowed", False))


def exposure_inventory() -> dict[str, Any]:
    from plan_consistency import AGENT_EXTENDED_REFACTOR, RAG_ESSENTIAL_TOOLS, RAG_EXTENDED_ONLY

    return {
        "ragMcpTools": sorted(RAG_MCP_TOOLS),
        "essentialProfile": sorted(RAG_ESSENTIAL_TOOLS),
        "extendedProfile": sorted(RAG_ESSENTIAL_TOOLS | RAG_EXTENDED_ONLY),
        "agentExtendedRefactor": sorted(AGENT_EXTENDED_REFACTOR),
        "terminalActions": sorted(TERMINAL_ACTIONS),
        "checkpoints": sorted(CHECKPOINTS),
    }
