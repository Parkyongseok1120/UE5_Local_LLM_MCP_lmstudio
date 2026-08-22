#!/usr/bin/env python
"""Merge Editor metadata by exact project-composite row identity."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from editor_metadata_identity import chunk_asset_key, chunk_belongs_to_project
from editor_metadata_jsonl import ingest_export, load_raw_chunks, write_raw_chunks


def merge_chunks(
    existing: dict[str, dict[str, Any]],
    incoming: list[dict[str, Any]],
    project: str,
    project_root: str = "",
    *,
    replace_project: bool = True,
) -> tuple[list[dict[str, Any]], int]:
    selected_keys = {
        key
        for key, chunk in existing.items()
        if chunk_belongs_to_project(chunk, project, project_root)
    }
    kept = (
        {key: chunk for key, chunk in existing.items() if key not in selected_keys}
        if replace_project
        else dict(existing)
    )
    replaced = 0
    for chunk in incoming:
        key = chunk_asset_key(chunk)
        if not key:
            raise ValueError("Projected Editor metadata chunk has no stable identity")
        if key in existing:
            replaced += 1
        kept[key] = chunk
    return list(kept.values()), replaced


def merge_export_into_raw(
    export_path: Path,
    source_key: str,
    project: str,
    out_path: Path,
    *,
    project_root: str = "",
    replace_project: bool = True,
) -> tuple[int, int]:
    incoming = ingest_export(export_path, source_key, project, project_root)
    existing = load_raw_chunks(out_path)
    merged, replaced = merge_chunks(
        existing,
        incoming,
        project,
        project_root,
        replace_project=replace_project,
    )
    write_raw_chunks(out_path, merged)
    return len(incoming), replaced


__all__ = ["merge_chunks", "merge_export_into_raw"]
