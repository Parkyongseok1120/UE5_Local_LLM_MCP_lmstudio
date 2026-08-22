#!/usr/bin/env python
"""Exact project and row identities for Editor metadata chunks."""

from __future__ import annotations

import hashlib
from typing import Any

from workspace_paths import filesystem_path_identity


def project_owner_identity(project: str, project_root: str = "") -> str:
    root = filesystem_path_identity(project_root, strip_project_uri=False)
    project_name = str(project or "").strip().casefold()
    return f"root:{root}|project:{project_name}" if root else f"project:{project_name}"


def row_identity(
    source: str,
    row: dict[str, Any],
    path: str,
    title: str,
    row_ordinal: int | None,
) -> str:
    if source != "unreal_project_settings":
        return title
    ordinal = row.get("ordinal")
    if not isinstance(ordinal, int) or ordinal < 0:
        ordinal = row_ordinal if row_ordinal is not None else 0
    return "|".join(
        (
            path,
            str(row.get("section") or ""),
            str(row.get("setting") or ""),
            str(ordinal),
        )
    )


def chunk_id_for_row(
    source: str,
    row: dict[str, Any],
    project: str,
    project_root: str,
    path: str,
    title: str,
    row_ordinal: int | None,
) -> str:
    identity = row_identity(source, row, path, title, row_ordinal)
    payload = f"{source}|{project_root}|{project}|{path}|{identity}"
    return hashlib.sha1(payload.encode()).hexdigest()


def chunk_asset_key(chunk: dict[str, Any]) -> str:
    metadata = chunk.get("metadata")
    if not isinstance(metadata, dict):
        return str(chunk.get("id") or chunk.get("path") or chunk.get("title") or "")
    owner = project_owner_identity(
        str(metadata.get("project") or ""),
        str(metadata.get("project_root") or metadata.get("projectRoot") or ""),
    )
    asset_path = str(metadata.get("asset_path") or "").strip()
    if asset_path:
        return f"{owner}|asset:{asset_path.casefold()}"
    row_key = str(chunk.get("id") or chunk.get("path") or chunk.get("title") or "")
    return f"{owner}|row:{row_key}"


def chunk_belongs_to_project(
    chunk: dict[str, Any],
    project: str,
    project_root: str = "",
) -> bool:
    metadata = chunk.get("metadata")
    if not isinstance(metadata, dict):
        return False
    actual_project = str(
        metadata.get("project") or chunk.get("project") or ""
    ).strip().casefold()
    if actual_project != str(project or "").strip().casefold():
        return False
    expected_root = filesystem_path_identity(project_root, strip_project_uri=False)
    if not expected_root:
        return True
    actual_root = filesystem_path_identity(
        metadata.get("project_root") or metadata.get("projectRoot") or "",
        strip_project_uri=False,
    )
    return bool(actual_root) and actual_root == expected_root


__all__ = [
    "chunk_asset_key",
    "chunk_belongs_to_project",
    "chunk_id_for_row",
    "project_owner_identity",
    "row_identity",
]
