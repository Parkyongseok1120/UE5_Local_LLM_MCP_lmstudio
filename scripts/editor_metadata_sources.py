#!/usr/bin/env python
"""Locate exact-project Editor metadata rows and relevant source mtimes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from editor_metadata_catalog import (
    AGGREGATE_ANIMATION_ASSET_TYPES,
    KIND_ASSET_TYPES,
    METADATA_FILES,
)
from editor_metadata_provenance import load_project_metadata_rows


def latest_project_asset_mtime(
    project_root: Path,
    asset_paths: set[str] | None = None,
) -> float | None:
    content = project_root / "Content"
    if not content.is_dir():
        return None
    latest: float | None = None
    for path in content.rglob("*.uasset"):
        if asset_paths is not None:
            try:
                rel = path.relative_to(content).with_suffix("")
            except ValueError:
                continue
            if "/Game/" + str(rel).replace("\\", "/") not in asset_paths:
                continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        latest = mtime if latest is None else max(latest, mtime)
    return latest


def asset_paths_for_kind(
    index_dir: Path,
    kind: str,
    project: Path | None,
) -> set[str] | None:
    wanted = KIND_ASSET_TYPES.get(kind)
    if not wanted:
        return None
    paths: set[str] = set()
    for meta in load_project_metadata_rows(
        index_dir / "raw_asset_registry.jsonl",
        project,
    ):
        asset_type = str(meta.get("asset_type") or "")
        asset_path = str(meta.get("asset_path") or "")
        if asset_type in wanted and asset_path.startswith("/Game/"):
            paths.add(asset_path)
    return paths


def metadata_file_info(
    index_dir: Path,
    kind: str,
    project: Path | None,
) -> tuple[Path, list[dict[str, Any]], bool]:
    path = index_dir / METADATA_FILES[kind]
    rows = load_project_metadata_rows(path, project)
    if rows:
        return path, rows, False
    aggregate_type = AGGREGATE_ANIMATION_ASSET_TYPES.get(kind)
    if aggregate_type:
        aggregate = index_dir / "raw_animation_metadata.jsonl"
        aggregate_rows = [
            row
            for row in load_project_metadata_rows(aggregate, project)
            if str(row.get("asset_type") or "") == aggregate_type
        ]
        if aggregate_rows:
            return aggregate, aggregate_rows, True
    return path, [], False


def latest_config_mtime(project_root: Path) -> float | None:
    config_dir = project_root / "Config"
    if not config_dir.is_dir():
        return None
    latest: float | None = None
    for path in config_dir.rglob("*.ini"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        latest = mtime if latest is None else max(latest, mtime)
    return latest


__all__ = [
    "asset_paths_for_kind",
    "latest_config_mtime",
    "latest_project_asset_mtime",
    "metadata_file_info",
]
