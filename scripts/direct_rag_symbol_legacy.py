#!/usr/bin/env python
"""Recognize legacy project-symbol rows only from exact filesystem evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from workspace_paths import filesystem_path_identity


def _row_root(row: dict[str, Any]) -> str:
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    return filesystem_path_identity(
        metadata.get("project_root") or metadata.get("projectRoot") or "",
        strip_project_uri=False,
    )


def _absolute_path_within(value: Any, root: Path) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        return False
    try:
        candidate.resolve().relative_to(root)
    except (OSError, ValueError):
        return False
    return True


def legacy_symbol_belongs_to_project(row: dict[str, Any], project: Path) -> bool:
    """Accept an old row only when both collector root and source path agree."""

    if _row_root(row):
        return False
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        return False
    descriptor = project.expanduser().resolve()
    if (
        str(metadata.get("project") or "").strip().casefold()
        != descriptor.stem.casefold()
        or str(row.get("source") or "").strip() != "unreal_symbol"
        or str(metadata.get("scope") or "").strip().casefold() != "project"
    ):
        return False
    root = descriptor.parent.resolve()
    return _absolute_path_within(metadata.get("root"), root) and _absolute_path_within(
        row.get("path"), root
    )


__all__ = ["legacy_symbol_belongs_to_project"]
