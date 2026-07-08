#!/usr/bin/env python
"""Invalidate caches and session state when the active Unreal project changes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from project_context import clear_project_context_cache
from project_identity import project_identity, resolve_uproject
from symbol_cache import invalidate_project_caches


def clear_wrapper_snapshot_cache() -> None:
    try:
        from wrapper_evidence import clear_all_project_snapshot_caches, clear_refactor_surface_cache

        clear_all_project_snapshot_caches()
        clear_refactor_surface_cache()
    except Exception:
        pass


def on_project_switch_invalidate(
    previous_project: str | Path | None,
    new_project: str | Path | None,
    *,
    workspace: Path | None = None,
) -> dict[str, Any]:
    """Clear project-scoped in-process caches after activeProject changes."""
    prev = resolve_uproject(previous_project)
    new = resolve_uproject(new_project)
    prev_identity = project_identity(prev) if prev else {"ok": False, "projectName": ""}
    new_identity = project_identity(new) if new else {"ok": False, "projectName": ""}

    cleared: list[str] = []
    clear_project_context_cache()
    cleared.append("project_context")

    clear_wrapper_snapshot_cache()
    cleared.append("wrapper_snapshot_cache")
    cleared.append("wrapper_refactor_surface_cache")

    try:
        from index_staleness import invalidate_stale_cache

        invalidate_stale_cache()
        cleared.append("index_staleness_cache")
    except Exception:
        pass

    if workspace is not None and prev_identity.get("ok"):
        removed = invalidate_project_caches(
            workspace,
            list(prev_identity.get("modules") or []),
            str(prev_identity.get("projectName") or ""),
        )
        if removed:
            cleared.append(f"symbol_cache({removed})")

    return {
        "ok": True,
        "previousProject": prev_identity,
        "newProject": new_identity,
        "cleared": cleared,
        "note": "Global RAG index chunks are not deleted; run unreal_rag_refresh or sync-active-project to reindex.",
    }
