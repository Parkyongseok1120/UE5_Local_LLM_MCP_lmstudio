#!/usr/bin/env python
"""Plan consistency validation and Essential/Extended tool exposure checks."""

from __future__ import annotations

import os
from typing import Any

WRITE_TOOLS = frozenset(
    {
        "write_file",
        "replace_in_file",
        "build_unreal_project",
        "unreal_start_compile_loop",
    }
)

RAG_ESSENTIAL_TOOLS = frozenset(
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
    }
)

RAG_EXTENDED_ONLY = frozenset(
    {
        "unreal_rag_refresh",
        "unreal_start_rag_refresh",
        "unreal_rag_refresh_status",
        "unreal_start_compile_loop",
        "unreal_compile_loop_status",
        "unreal_cancel_compile_loop",
        "unreal_refactor_manager_plan",
        "unreal_refactor_impact_scan",
        "unreal_refactor_plan_validate",
    }
)

AGENT_EXTENDED_REFACTOR = frozenset(
    {
        "refactor_impact_scan",
        "refactor_plan_validate",
    }
)


def essential_tools_enabled() -> bool:
    value = os.environ.get("MCP_ESSENTIAL_TOOLS", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def exposed_rag_tools() -> frozenset[str]:
    if essential_tools_enabled():
        return RAG_ESSENTIAL_TOOLS
    return RAG_ESSENTIAL_TOOLS | RAG_EXTENDED_ONLY


def apply_ambiguity_write_policy(
    *,
    ambiguity_gate: dict[str, Any],
    strategy: str,
    evidence_writes_allowed: bool,
) -> tuple[str, bool, dict[str, Any]]:
    """Return updated strategy, writes_allowed, and write_gate extras."""
    action = str(ambiguity_gate.get("recommendedAction") or "")
    extras: dict[str, Any] = {}
    writes = evidence_writes_allowed
    edit_strategy = strategy

    if action == "bounded_assumption":
        extras["assumptions"] = list(ambiguity_gate.get("assumptions") or [])
        return edit_strategy, writes, extras

    if action in {"plan_only", "ask_user_once", "human_approval"}:
        edit_strategy = "no_edit"
        writes = False

    if action == "ask_user_once":
        extras["requiresUserClarification"] = True
        extras["clarificationQuestions"] = list(ambiguity_gate.get("clarificationQuestions") or [])[:3]

    if action in {"human_approval", "plan_only"} and float(ambiguity_gate.get("ambiguityScore") or 0) >= 0.6:
        extras["requiresHumanApproval"] = True

    return edit_strategy, writes, extras


def sanitize_tools_for_exposure(
    tool_policy: list[str],
    suggested_calls: list[dict[str, Any]],
    *,
    refactor_manager_embedded: bool = False,
) -> tuple[list[str], list[dict[str, Any]], list[str]]:
    """Filter hidden tools in Essential mode; return notes."""
    notes: list[str] = []
    if not essential_tools_enabled():
        return tool_policy, suggested_calls, notes

    allowed = exposed_rag_tools()
    filtered_policy = [tool for tool in tool_policy if tool in allowed or not tool.startswith("unreal_")]
    hidden_policy = [tool for tool in tool_policy if tool.startswith("unreal_") and tool not in allowed]
    if hidden_policy:
        notes.append(
            "Essential mode: refactor/refresh tools omitted from toolPolicy; "
            "use embedded refactorManager in plan response."
        )

    filtered_calls: list[dict[str, Any]] = []
    for call in suggested_calls:
        tool = str(call.get("tool") or "")
        if tool in RAG_EXTENDED_ONLY:
            if refactor_manager_embedded and tool in {
                "unreal_refactor_manager_plan",
                "unreal_refactor_impact_scan",
            }:
                continue
            notes.append(f"Essential mode: suppressed suggested call to hidden tool {tool}.")
            continue
        if tool.startswith("unreal_") and tool not in allowed:
            continue
        filtered_calls.append(call)

    return filtered_policy, filtered_calls, notes


def validate_plan_consistency(plan: Any) -> list[str]:
    """Return consistency errors; empty if plan is coherent."""
    issues: list[str] = []
    task_kind = getattr(plan, "task_kind", "")
    edit_strategy = getattr(plan, "edit_strategy", "")
    evidence = getattr(plan, "evidence", None)
    write_gate = getattr(plan, "write_gate", {}) or {}
    ambiguity_gate = getattr(plan, "ambiguity_gate", {}) or {}
    tool_policy = list(getattr(plan, "tool_policy", []) or [])
    suggested = list(getattr(plan, "suggested_tool_calls", []) or [])
    plan_slices = list(getattr(plan, "plan_slices", []) or [])

    writes_allowed = bool(write_gate.get("writesAllowed"))
    evidence_writes = bool(getattr(evidence, "writes_allowed", False)) if evidence else False

    if edit_strategy == "no_edit" and writes_allowed:
        issues.append("editStrategy=no_edit but writeGate.writesAllowed=true")
    if evidence_writes and not writes_allowed and edit_strategy != "no_edit":
        issues.append("evidence.writesAllowed=true but writeGate.writesAllowed=false without no_edit")
    if task_kind in {"inspect_only", "answer_only", "code_sketch", "runtime_debug"} and writes_allowed:
        issues.append(f"taskKind={task_kind} must not allow writes")
    if ambiguity_gate.get("recommendedAction") == "ask_user_once" and writes_allowed:
        issues.append("ask_user_once must block writes")
    if ambiguity_gate.get("recommendedAction") == "plan_only" and writes_allowed:
        issues.append("plan_only must block writes")

    action = str(ambiguity_gate.get("recommendedAction") or "")
    if action in {"ask_user_once", "plan_only"}:
        for call in suggested:
            if str(call.get("tool") or "") in WRITE_TOOLS:
                issues.append(f"write tool {call.get('tool')} suggested under {action}")

    max_files = int(write_gate.get("maxFilesPerEdit") or 0)
    if max_files > 0:
        for slice_ in plan_slices:
            files = slice_.get("files") or []
            if len(files) > max_files:
                issues.append(f"plan slice {slice_.get('slice_id')} exceeds maxFilesPerEdit")

    if essential_tools_enabled():
        allowed = exposed_rag_tools()
        for tool in tool_policy:
            if tool in RAG_EXTENDED_ONLY:
                issues.append(f"hidden tool {tool} in toolPolicy under Essential mode")
        for call in suggested:
            if str(call.get("tool") or "") in RAG_EXTENDED_ONLY:
                issues.append(f"hidden tool {call.get('tool')} in suggestedToolCalls under Essential mode")

    if write_gate.get("requiresHumanApproval") and writes_allowed:
        issues.append("requiresHumanApproval=true but writesAllowed=true")

    return issues


def apply_consistency_fallback(plan: Any, issues: list[str]) -> None:
    """Mutate plan to conservative no-write state on consistency failure."""
    if not issues:
        return
    plan.edit_strategy = "no_edit"
    if plan.evidence:
        plan.evidence.writes_allowed = False
    plan.write_gate = dict(plan.write_gate or {})
    plan.write_gate["writesAllowed"] = False
    plan.write_gate["planConsistencyFallback"] = True
    plan.write_gate["consistencyIssues"] = issues[:8]
    plan.notes.append("Plan consistency fallback: writes disabled due to: " + "; ".join(issues[:3]))
