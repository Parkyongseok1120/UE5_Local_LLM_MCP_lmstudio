#!/usr/bin/env python
"""Classify query scope for active project vs engine-wide retrieval."""

from __future__ import annotations

import os
import re
from pathlib import Path

PROJECT_STRONG_HINTS = (
    "my project",
    "current project",
    "this project",
    "our project",
    "active project",
    "agent_edit",
    "agent edit",
    "refactor",
    "compile error",
    "build error",
    "saved/logs",
    "saved\\logs",
    "source/",
    "source\\",
    ".uproject",
    ".cpp:",
    ".h:",
)
ENGINE_STRONG_HINTS = (
    "how to use",
    "how do i",
    "uactorcomponent",
    "uworldsubsystem",
    "ugameinstancesubsystem",
    "generated_body",
    "generated.h",
    "build.cs",
    "publicdependencymodulenames",
    "unreal api",
    "official",
    "engine source",
    "uht",
    "unrealheadertool",
    "blueprintnativeevent",
    "enhanced input",
    "gameplaytags",
)
AGENT_MODES = {
    "agent_edit",
    "compile_fix",
    "module_fix",
    "reflection_fix",
    "runtime_debug",
    "refactor_r0",
    "refactor_r1",
    "refactor_r2",
    "refactor_r3",
    "refactor_r4",
    "prototype_component",
    "prototype_subsystem",
}
API_LOOKUP_MODES = {"api_lookup", "codegen"}


def _legacy_filter() -> bool:
    return os.environ.get("UNREAL_RAG_LEGACY_PROJECT_FILTER", "").strip() == "1"


def _routing_enabled() -> bool:
    if _legacy_filter():
        return False
    return os.environ.get("UNREAL_RAG_PROJECT_ROUTING", "v1").strip().lower() in {"1", "true", "v1", "yes"}


def classify_query_scope(
    query: str,
    mode: str = "auto",
    explicit_projects: list[str] | None = None,
    active_project_path: str | None = None,
) -> str:
    """Return engine | project | mixed."""
    if explicit_projects:
        return "project"
    if not _routing_enabled():
        return "project" if active_project_path else "engine"

    q = (query or "").lower()
    mode_l = (mode or "auto").lower()

    project_score = 0
    engine_score = 0

    for hint in PROJECT_STRONG_HINTS:
        if hint in q:
            project_score += 2
    for hint in ENGINE_STRONG_HINTS:
        if hint in q:
            engine_score += 2

    if mode_l in AGENT_MODES:
        project_score += 3
    if mode_l in API_LOOKUP_MODES:
        engine_score += 3

    if re.search(r"[a-z]:\\|/source/|\.uproject", q, re.I):
        project_score += 2
    if re.search(r"\bU[A-Z][A-Za-z0-9_]+\b", query):
        engine_score += 1

    if active_project_path:
        proj_name = Path(active_project_path).stem.lower()
        if proj_name and proj_name in q:
            project_score += 3

    if project_score >= 3 and engine_score >= 2:
        return "mixed"
    if project_score > engine_score:
        return "project"
    if engine_score > project_score:
        return "engine"
    return "mixed" if active_project_path else "engine"


def resolve_project_filters(
    query: str,
    mode: str,
    explicit_projects: list[str],
    active_project_names: list[str],
    scope: str = "auto",
    use_active_project: bool = True,
    active_project_path: str | None = None,
) -> tuple[list[str], str]:
    """Return (project filter list, resolved scope)."""
    if explicit_projects:
        return explicit_projects, "project"
    if not use_active_project:
        return [], "engine"

    resolved = scope
    if scope == "auto":
        resolved = classify_query_scope(query, mode, [], active_project_path)

    if resolved == "engine":
        return [], resolved
    if resolved in {"project", "mixed"} and active_project_names:
        return active_project_names, resolved
    return [], resolved
