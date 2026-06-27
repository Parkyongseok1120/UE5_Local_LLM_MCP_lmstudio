#!/usr/bin/env python
"""Small planner/executor/verifier for Unreal agent tasks (Phase 14)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

TaskKind = Literal[
    "answer_only",
    "inspect_only",
    "edit",
    "compile_fix",
    "refactor",
    "runtime_debug",
]
EditStrategy = Literal[
    "no_edit",
    "new_file",
    "full_rewrite_small",
    "exact_patch",
    "line_range_patch",
]

COMPILE_MARKERS = (
    "c1083", "lnk2019", "uht", "generated.h", "build.cs", "compile error",
    "undefined", "unresolved", "missing module",
)
REFACTOR_MARKERS = ("refactor", "r0", "r1", "r2", "r3", "r4", "move class", "extract")
RUNTIME_MARKERS = ("pie", "runtime", "gamemode", "input mapping", "crash", "assert", "log")
REVIEW_MARKERS = ("review", "inventory", "audit", "findings", "architecture review")
API_MARKERS = ("what is", "how does", "api", "lookup", "documentation", "explain")


@dataclass
class EvidencePlan:
    task_kind: TaskKind
    rag_modes: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    files_to_read: list[str] = field(default_factory=list)
    symbols_to_scan: list[str] = field(default_factory=list)
    gates: list[str] = field(default_factory=list)
    writes_allowed: bool = False
    confidence: float = 0.5

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentPlan:
    request: str
    task_kind: TaskKind
    evidence: EvidencePlan
    edit_strategy: EditStrategy
    tool_policy: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": self.request,
            "taskKind": self.task_kind,
            "evidencePlan": self.evidence.to_dict(),
            "editStrategy": self.edit_strategy,
            "toolPolicy": self.tool_policy,
            "notes": self.notes,
        }


def classify_task(request: str, mode: str = "auto") -> TaskKind:
    text = f"{mode} {request}".lower()
    if mode in {"refactor_r0", "refactor_r1", "refactor_r2", "refactor_r3", "refactor_r4"}:
        return "refactor"
    if mode in {"compile_fix", "module_fix", "reflection_fix"}:
        return "compile_fix"
    if mode == "runtime_debug":
        return "runtime_debug"
    if mode in {"review", "planning"}:
        return "inspect_only"
    if mode == "api_lookup":
        return "answer_only"
    if any(m in text for m in COMPILE_MARKERS):
        return "compile_fix"
    if any(m in text for m in REFACTOR_MARKERS):
        return "refactor"
    if any(m in text for m in RUNTIME_MARKERS):
        return "runtime_debug"
    if any(m in text for m in REVIEW_MARKERS):
        return "inspect_only"
    if any(m in text for m in API_MARKERS) and not any(
        w in text for w in ("fix", "patch", "implement", "add class", "create")
    ):
        return "answer_only"
    if any(w in text for w in ("implement", "add ", "create ", "patch", "fix ", "write ")):
        return "edit"
    return "edit"


def choose_edit_strategy(task_kind: TaskKind, request: str, *, file_count_hint: int = 0) -> EditStrategy:
    if task_kind in {"answer_only", "inspect_only"}:
        return "no_edit"
    if task_kind == "compile_fix":
        return "exact_patch"
    if task_kind == "refactor" and "r0" in request.lower():
        return "no_edit"
    if file_count_hint == 0 and "new file" in request.lower():
        return "new_file"
    if file_count_hint == 1:
        return "full_rewrite_small"
    return "exact_patch"


def build_evidence_plan(request: str, task_kind: TaskKind, mode: str = "auto") -> EvidencePlan:
    plan = EvidencePlan(task_kind=task_kind, queries=[request.strip()])
    if task_kind == "answer_only":
        plan.rag_modes = ["api_lookup", "auto"]
        plan.gates = []
        plan.writes_allowed = False
        plan.confidence = 0.8
    elif task_kind == "inspect_only":
        plan.rag_modes = ["review", "planning"]
        plan.gates = ["unreal_project_architecture", "unreal_review_claim_validate"]
        plan.writes_allowed = False
        plan.confidence = 0.75
    elif task_kind == "compile_fix":
        plan.rag_modes = ["compile_fix", "module_fix", "reflection_fix"]
        plan.gates = ["static_validate", "ubt_build"]
        plan.writes_allowed = True
        plan.confidence = 0.7
    elif task_kind == "refactor":
        plan.rag_modes = [mode if mode.startswith("refactor_") else "refactor_r0", "planning"]
        plan.gates = ["unreal_refactor_plan_validate", "unreal_refactor_impact_scan"]
        plan.writes_allowed = mode not in {"refactor_r0", "refactor_r1", "auto"} or "r0" not in mode
        if "r0" in mode.lower() or task_kind == "refactor" and "discover" in request.lower():
            plan.writes_allowed = False
        plan.confidence = 0.65
    elif task_kind == "runtime_debug":
        plan.rag_modes = ["runtime_debug", "review"]
        plan.gates = ["unreal_runtime_config_check", "read_unreal_logs"]
        plan.writes_allowed = False
        plan.confidence = 0.7
    else:
        plan.rag_modes = [mode if mode != "auto" else "agent_edit"]
        plan.gates = ["static_validate"]
        plan.writes_allowed = True
        plan.confidence = 0.6

    _extract_symbols(request, plan)
    return plan


def _extract_symbols(request: str, plan: EvidencePlan) -> None:
    for match in re.finditer(r"\bU[A-Z][A-Za-z0-9_]+\b", request):
        plan.symbols_to_scan.append(match.group(0))
    for match in re.finditer(r"\b[A-Z][A-Za-z0-9_]+(?:Component|Subsystem|Character|Actor|GameMode)\b", request):
        sym = match.group(0)
        if sym not in plan.symbols_to_scan:
            plan.symbols_to_scan.append(sym)


def build_agent_plan(request: str, mode: str = "auto", *, file_count_hint: int = 0) -> AgentPlan:
    from load_sampling_preset import profile_agent_policy
    from tool_policy import tool_sequence_for_task

    policy = profile_agent_policy()
    task_kind = classify_task(request, mode)
    evidence = build_evidence_plan(request, task_kind, mode)
    strategy = choose_edit_strategy(task_kind, request, file_count_hint=file_count_hint)
    if not evidence.writes_allowed:
        strategy = "no_edit"
    tool_policy = tool_sequence_for_task(task_kind)
    notes: list[str] = []
    if evidence.confidence < 0.65:
        notes.append("Low confidence: prefer inspect-only before edits.")
    if strategy == "exact_patch":
        notes.append("Prefer minimal patch over full-file rewrite.")
    if policy.get("maxFilesPerEdit"):
        notes.append(f"Max files per edit: {policy['maxFilesPerEdit']}")
    if not policy.get("allowRefactorModes", True) and task_kind == "refactor":
        strategy = "no_edit"
        evidence.writes_allowed = False
        notes.append("Refactor modes disabled for active model profile.")
    return AgentPlan(
        request=request,
        task_kind=task_kind,
        evidence=evidence,
        edit_strategy=strategy,
        tool_policy=tool_policy,
        notes=notes,
    )


def orchestrator_enabled() -> bool:
    return os.environ.get("UNREAL_AGENT_ORCHESTRATE", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def format_plan_for_prompt(plan: AgentPlan) -> str:
    payload = plan.to_dict()
    return (
        "## Agent orchestrator plan (follow this order)\n"
        f"Task: {payload['taskKind']}\n"
        f"Edit strategy: {payload['editStrategy']}\n"
        f"RAG modes: {', '.join(plan.evidence.rag_modes)}\n"
        f"Gates: {', '.join(plan.evidence.gates) or 'none'}\n"
        f"Tool policy: {' -> '.join(plan.tool_policy)}\n"
        + ("Notes: " + "; ".join(plan.notes) + "\n" if plan.notes else "")
    )


def verify_edit_allowed(plan: AgentPlan, *, files_count: int, patches_count: int) -> dict[str, Any]:
    issues: list[str] = []
    if plan.edit_strategy == "no_edit" and (files_count or patches_count):
        issues.append("Plan forbids edits but bundle contains file changes.")
    if plan.task_kind == "inspect_only" and (files_count or patches_count):
        issues.append("Inspect-only task must not write files.")
    return {"ok": len(issues) == 0, "issues": issues}


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build agent orchestrator plan JSON.")
    parser.add_argument("--request", required=True)
    parser.add_argument("--mode", default="auto")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    plan = build_agent_plan(args.request, args.mode)
    if args.json:
        print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_plan_for_prompt(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
