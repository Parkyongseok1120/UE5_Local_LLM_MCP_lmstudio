#!/usr/bin/env python
"""Read exact-project Editor export provenance from merged raw JSONL rows."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from workspace_paths import filesystem_path_identity

EXPORT_MTIME_FIELD = "editor_export_mtime"
EXPORT_KIND_FIELD = "editor_export_kind"


@dataclass(frozen=True)
class EditorRowProvenance:
    row_count: int
    known: bool
    oldest_mtime: float | None
    newest_mtime: float | None
    export_kinds: tuple[str, ...]


def _metadata_payload(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    metadata = row.get("metadata")
    return metadata if isinstance(metadata, dict) else row


def _project_selector(project_file_or_root: Path | None) -> tuple[str, str]:
    if project_file_or_root is None:
        return "", ""
    selected = project_file_or_root.expanduser().resolve()
    descriptor = selected.suffix.casefold() == ".uproject"
    root = selected.parent if descriptor else selected
    return (
        filesystem_path_identity(root, strip_project_uri=False),
        selected.stem.casefold() if descriptor else "",
    )


def load_project_metadata_rows(
    path: Path,
    project_file_or_root: Path | None = None,
) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    expected_root, expected_project = _project_selector(project_file_or_root)
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            metadata = _metadata_payload(json.loads(line))
        except json.JSONDecodeError:
            continue
        if metadata is None:
            continue
        if expected_root:
            actual = filesystem_path_identity(
                metadata.get("project_root") or metadata.get("projectRoot") or "",
                strip_project_uri=False,
            )
            if not actual or actual != expected_root:
                continue
        if expected_project:
            actual_project = str(metadata.get("project") or "").strip().casefold()
            if actual_project != expected_project:
                continue
        rows.append(metadata)
    return rows


def _valid_mtime(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0.0 else None


def summarize_editor_row_provenance(
    rows: Iterable[dict[str, Any]],
) -> EditorRowProvenance:
    materialized = list(rows)
    mtimes = [_valid_mtime(row.get(EXPORT_MTIME_FIELD)) for row in materialized]
    known = bool(materialized) and all(value is not None for value in mtimes)
    valid_mtimes = [value for value in mtimes if value is not None]
    kinds = tuple(
        sorted(
            {
                str(row.get(EXPORT_KIND_FIELD) or "").strip()
                for row in materialized
                if str(row.get(EXPORT_KIND_FIELD) or "").strip()
            }
        )
    )
    return EditorRowProvenance(
        row_count=len(materialized),
        known=known,
        oldest_mtime=min(valid_mtimes) if known else None,
        newest_mtime=max(valid_mtimes) if known else None,
        export_kinds=kinds,
    )


def project_file_provenance(
    path: Path,
    project_file_or_root: Path | None = None,
    *,
    asset_type: str = "",
) -> EditorRowProvenance:
    rows = load_project_metadata_rows(path, project_file_or_root)
    if asset_type:
        rows = [row for row in rows if str(row.get("asset_type") or "") == asset_type]
    return summarize_editor_row_provenance(rows)


__all__ = [
    "EXPORT_KIND_FIELD",
    "EXPORT_MTIME_FIELD",
    "EditorRowProvenance",
    "load_project_metadata_rows",
    "project_file_provenance",
    "summarize_editor_row_provenance",
]
