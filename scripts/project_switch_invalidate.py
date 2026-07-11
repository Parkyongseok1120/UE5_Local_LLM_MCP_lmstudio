#!/usr/bin/env python
"""Invalidate caches and session state when the active Unreal project changes."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from project_context import clear_project_context_cache
from project_identity import project_identity, resolve_uproject
from symbol_cache import invalidate_project_caches


def cache_generation_path(workspace: Path) -> Path:
    return workspace / "data" / "project_cache_generation.json"


def read_cache_generation(workspace: Path) -> int:
    path = cache_generation_path(workspace)
    if not path.is_file():
        return 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return int(payload.get("generation") or 0)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 0


def write_cache_generation(workspace: Path) -> int:
    from atomic_io import atomic_write_text

    path = cache_generation_path(workspace)
    generation = max(read_cache_generation(workspace) + 1, int(time.time() * 1000))
    atomic_write_text(
        path,
        json.dumps({"generation": generation}, ensure_ascii=False, indent=2) + "\n",
    )
    return generation


def publish_project_switch_generation(workspace: Path) -> int:
    """Increment generation once when activeProject actually changes."""
    return write_cache_generation(workspace)


def clear_local_project_caches(
    workspace: Path | None,
    *,
    previous_project: str | Path | None = None,
    new_project: str | Path | None = None,
) -> dict[str, Any]:
    """Clear in-process caches only; never writes generation file."""
    prev = resolve_uproject(previous_project)
    new = resolve_uproject(new_project)
    prev_identity = project_identity(prev) if prev else {"ok": False, "projectName": ""}
    new_identity = project_identity(new) if new else {"ok": False, "projectName": ""}

    cleared: list[str] = []
    partial_clear: list[str] = []
    errors: list[str] = []
    critical_ok = True

    clear_project_context_cache()
    cleared.append("project_context")

    try:
        clear_wrapper_snapshot_cache()
        cleared.append("wrapper_snapshot_cache")
        cleared.append("wrapper_refactor_surface_cache")
    except Exception as exc:
        critical_ok = False
        errors.append(f"wrapper_snapshot_cache: {exc}")

    try:
        from domain_validation_context import clear_domain_validation_cache
        from index_staleness import invalidate_stale_cache
        from read_query_history import reset_query_history_for_index

        clear_domain_validation_cache()
        cleared.append("domain_validation_context")
        invalidate_stale_cache()
        cleared.append("index_staleness_cache")
        if new:
            from workspace_paths import resolve_index_dir

            reset_query_history_for_index(resolve_index_dir() / "rag.sqlite")
            cleared.append("rag_query_history_for_index")
    except Exception as exc:
        critical_ok = False
        errors.append(f"domain_or_index_cache: {exc}")
        partial_clear.extend(["domain_validation_context", "index_staleness_cache", "rag_query_history_for_index"])

    if workspace is not None:
        try:
            from rag_search import close_index_connections

            close_index_connections()
            cleared.append("rag_sqlite_connections")
        except Exception as exc:
            critical_ok = False
            errors.append(f"rag_sqlite_connections: {exc}")
            partial_clear.append("rag_sqlite_connections")

    if workspace is not None and prev_identity.get("ok"):
        try:
            removed = invalidate_project_caches(
                workspace,
                list(prev_identity.get("modules") or []),
                str(prev_identity.get("projectName") or ""),
            )
            if removed:
                cleared.append(f"symbol_cache({removed})")
        except Exception as exc:
            critical_ok = False
            errors.append(f"symbol_cache: {exc}")
            partial_clear.append("symbol_cache")

    return {
        "ok": critical_ok,
        "previousProject": prev_identity,
        "newProject": new_identity,
        "cleared": cleared,
        "partialClear": partial_clear,
        "errors": errors,
        "cacheRefreshRequired": not critical_ok,
    }


def clear_wrapper_snapshot_cache() -> None:
    from wrapper_evidence import clear_all_project_snapshot_caches, clear_refactor_surface_cache

    clear_all_project_snapshot_caches()
    clear_refactor_surface_cache()


def on_project_switch_invalidate(
    previous_project: str | Path | None,
    new_project: str | Path | None,
    *,
    workspace: Path | None = None,
) -> dict[str, Any]:
    """Publish generation and clear caches after an activeProject change."""
    payload = clear_local_project_caches(
        workspace,
        previous_project=previous_project,
        new_project=new_project,
    )
    generation = publish_project_switch_generation(workspace) if workspace is not None else None
    payload["cacheGeneration"] = generation
    payload["note"] = (
        "Global RAG index chunks are not deleted; run unreal_rag_refresh or sync-active-project to reindex."
    )
    return payload
