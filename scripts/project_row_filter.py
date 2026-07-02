#!/usr/bin/env python
"""Strict project-name filtering for indexed metadata rows."""

from __future__ import annotations

from typing import Any

from project_context import active_project_name as context_active_project_name
from workspace_paths import load_shared_config


def row_project_name(row: dict[str, Any]) -> str:
    meta = row.get("metadata")
    if isinstance(meta, dict):
        project = str(meta.get("project") or "").strip()
        if project:
            return project
    return str(row.get("project") or "").strip()


def resolve_filter_project_name(project_name: str | None = None) -> str:
    if project_name and str(project_name).strip():
        return str(project_name).strip()
    name = context_active_project_name()
    if name:
        return name
    active = str(load_shared_config().get("activeProject") or "").strip()
    if not active:
        return ""
    from pathlib import Path

    path = Path(active)
    return path.stem if path.suffix.lower() == ".uproject" else path.name


def filter_rows_by_project(
    rows: list[dict[str, Any]],
    project_name: str | None = None,
) -> list[dict[str, Any]]:
    active = resolve_filter_project_name(project_name)
    if not active:
        return rows
    return [row for row in rows if row_project_name(row) == active]
