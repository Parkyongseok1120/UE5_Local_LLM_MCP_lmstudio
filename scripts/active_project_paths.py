#!/usr/bin/env python
"""Resolve the project selected in the shared Unreal workspace config."""

from __future__ import annotations

from pathlib import Path

from workspace_config import find_workspace_root, load_shared_config


def resolve_active_project_path(start: Path | None = None) -> Path | None:
    active = str(load_shared_config().get("activeProject") or "").strip()
    if not active:
        return None
    path = Path(active).expanduser()
    if not path.is_absolute():
        path = find_workspace_root(start) / path
    if path.exists():
        return path.resolve()
    return None


def resolve_active_project_root(start: Path | None = None) -> Path | None:
    active = resolve_active_project_path(start)
    if not active:
        return None
    if active.suffix.lower() == ".uproject":
        return active.parent.resolve()
    return active.resolve()


def resolve_active_project_source_root(start: Path | None = None) -> Path | None:
    root = resolve_active_project_root(start)
    if not root:
        return None
    source = root / "Source"
    if source.is_dir():
        return source.resolve()
    # Plugin-only projects still use the project root as their scan boundary.
    return root.resolve()


def indexing_tier(start: Path | None = None) -> str:
    del start
    tier = str(load_shared_config().get("indexingTier") or "standard").strip().lower()
    return tier if tier in {"lite", "standard", "full"} else "standard"
