#!/usr/bin/env python
"""Validate one completed, exact-project Unreal Editor export run."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from workspace_paths import filesystem_path_identity

MANIFEST_NAME = "export_manifest.json"
SCHEMA_VERSION = 1
SUPPORTED_KINDS = frozenset({
    "blueprint",
    "material",
    "texture",
    "mesh",
    "world_look",
    "animation",
    "structured",
    "fmod",
    "asset_registry",
    "project_settings",
    "level",
})


class EditorExportContractError(RuntimeError):
    """The export directory is not one completed exact-project capture."""


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validated_row_count(path: Path) -> int:
    count = 0
    for line_no, line in enumerate(
        path.read_text(encoding="utf-8-sig", errors="strict").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EditorExportContractError(
                f"Editor export is not complete JSONL: {path.name}:{line_no}: {exc}"
            ) from exc
        if not isinstance(row, dict):
            raise EditorExportContractError(
                f"Editor export row must be an object: {path.name}:{line_no}"
            )
        count += 1
    return count


def completed_export_files(
    export_dir: Path,
    expected_project: Path,
    *,
    expected_scope: str = "",
) -> tuple[dict[str, Any], list[tuple[Path, str]]]:
    """Return manifest-listed files after proving project, hash, and row counts."""

    root = export_dir.expanduser().resolve()
    project = expected_project.expanduser().resolve()
    manifest_path = root / MANIFEST_NAME
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EditorExportContractError(
            f"Missing or invalid completed Editor export manifest: {manifest_path}"
        ) from exc
    if not isinstance(payload, dict):
        raise EditorExportContractError("Editor export manifest must be a JSON object")
    if payload.get("schemaVersion") != SCHEMA_VERSION or payload.get("complete") is not True:
        raise EditorExportContractError("Editor export manifest is not a completed schema-v1 run")
    if not str(payload.get("runId") or "").strip():
        raise EditorExportContractError("Editor export manifest has no runId")
    if expected_scope and str(payload.get("scope") or "").casefold() != expected_scope.casefold():
        raise EditorExportContractError(
            f"Editor export scope is not the requested {expected_scope!r} capture"
        )
    captured_at = payload.get("capturedAt")
    if not isinstance(captured_at, (int, float)) or captured_at <= 0:
        raise EditorExportContractError("Editor export manifest has no valid capturedAt")
    actual_identity = filesystem_path_identity(
        payload.get("projectFile") or "", strip_project_uri=False
    )
    expected_identity = filesystem_path_identity(project, strip_project_uri=False)
    if not actual_identity or actual_identity != expected_identity:
        raise EditorExportContractError(
            "Editor export project identity does not match the selected .uproject"
        )
    entries = payload.get("exports")
    if not isinstance(entries, list) or not entries:
        raise EditorExportContractError("Editor export manifest contains no authoritative kinds")
    exports: list[tuple[Path, str]] = []
    seen_files: set[str] = set()
    seen_kinds: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise EditorExportContractError("Editor export manifest entry must be an object")
        name = str(entry.get("file") or "").strip()
        kind = str(entry.get("kind") or "").strip()
        if kind not in SUPPORTED_KINDS or not name or Path(name).name != name:
            raise EditorExportContractError("Editor export manifest has an unsafe file or kind")
        if name.casefold() in seen_files or kind in seen_kinds:
            raise EditorExportContractError("Editor export manifest contains duplicate files or kinds")
        seen_files.add(name.casefold())
        seen_kinds.add(kind)
        path = (root / name).resolve()
        if path.parent != root or not path.is_file():
            raise EditorExportContractError(f"Manifest-listed Editor export is missing: {name}")
        size = path.stat().st_size
        if entry.get("sizeBytes") != size or entry.get("sha256") != _file_sha256(path):
            raise EditorExportContractError(f"Editor export changed after completion: {name}")
        rows = _validated_row_count(path)
        if entry.get("rowCount") != rows:
            raise EditorExportContractError(f"Editor export row count changed: {name}")
        exports.append((path, kind))
    return payload, exports


__all__ = [
    "EditorExportContractError",
    "MANIFEST_NAME",
    "completed_export_files",
]
