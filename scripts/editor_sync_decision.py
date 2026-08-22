#!/usr/bin/env python
"""Pure per-project freshness inputs for Editor metadata synchronization."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from editor_capture_state import completed_capture
from editor_metadata_catalog import AGGREGATE_ANIMATION_ASSET_TYPES, METADATA_FILES
from editor_metadata_provenance import EditorRowProvenance, project_file_provenance
from ingest_editor_exports import discover_exports
from workspace_paths import load_shared_config

PRIORITY_KINDS = (
    "material",
    "texture",
    "mesh",
    "blueprint",
    "structured",
    "animation",
    "world_look",
    "fmod",
)
def resolve_export_dir(explicit: str | None) -> Path:
    from editor_export_runner import resolve_export_dir as resolve

    return resolve(explicit if explicit and str(explicit).strip() else None)


def resolve_project_name(explicit: str | None) -> str:
    if explicit and str(explicit).strip():
        return str(explicit).strip()
    active = str(load_shared_config().get("activeProject") or "").strip()
    return Path(active).stem if active else "Project"


def export_dir_summary(
    export_dir: Path | None,
    project_file: Path | None = None,
) -> dict[str, Any]:
    if not export_dir or not export_dir.is_dir():
        return {
            "configured": False,
            "path": str(export_dir or ""),
            "files": [],
            "newestMtime": None,
        }
    try:
        files = discover_exports(
            export_dir,
            project_file=project_file,
            require_manifest=bool(
                project_file and (export_dir / "export_manifest.json").is_file()
            ),
        )
    except (RuntimeError, ValueError) as exc:
        return {
            "configured": True,
            "path": str(export_dir),
            "files": [],
            "newestMtime": None,
            "errorCode": "EDITOR_EXPORT_CONTRACT_INVALID",
            "error": str(exc),
        }
    file_rows = []
    newest: float | None = None
    for path, kind in files:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = None
        if mtime is not None:
            newest = mtime if newest is None else max(newest, mtime)
        file_rows.append({"path": str(path), "kind": kind, "mtime": mtime})
    return {
        "configured": True,
        "path": str(export_dir),
        "files": file_rows,
        "newestMtime": newest,
    }


def _raw_provenance_for_kind(
    index_dir: Path,
    kind: str,
    project_root: Path | None,
) -> EditorRowProvenance:
    filename = METADATA_FILES.get(kind)
    if not filename:
        return EditorRowProvenance(0, False, None, None, ())
    direct = project_file_provenance(index_dir / filename, project_root)
    if direct.row_count:
        return direct
    aggregate_type = AGGREGATE_ANIMATION_ASSET_TYPES.get(kind)
    aggregate = (
        project_file_provenance(
            index_dir / METADATA_FILES["animation"],
            project_root,
            asset_type=aggregate_type,
        )
        if aggregate_type
        else direct
    )
    if aggregate.row_count:
        return aggregate
    capture = completed_capture(index_dir, project_root, kind)
    if capture is None:
        return aggregate
    captured_at = float(capture["capturedAt"])
    return EditorRowProvenance(0, True, captured_at, captured_at, (kind,))


def raw_newest_mtime(
    index_dir: Path,
    kinds: tuple[str, ...] = PRIORITY_KINDS,
    project_root: Path | None = None,
) -> float | None:
    newest: float | None = None
    for kind in kinds:
        provenance = _raw_provenance_for_kind(index_dir, kind, project_root)
        if not provenance.known and not provenance.row_count:
            continue
        if not provenance.known or provenance.newest_mtime is None:
            return None
        mtime = provenance.newest_mtime
        newest = mtime if newest is None else max(newest, mtime)
    return newest


def _raw_mtime_for_kind(
    index_dir: Path,
    kind: str,
    project_root: Path | None,
) -> float | None:
    provenance = _raw_provenance_for_kind(index_dir, kind, project_root)
    return provenance.newest_mtime if provenance.known else None


def exports_newer_than_raw(
    index_dir: Path,
    export_summary: dict[str, Any],
    project_root: Path | None,
) -> bool:
    for row in export_summary.get("files") or []:
        export_mtime = row.get("mtime")
        if export_mtime is None:
            continue
        raw_mtime = _raw_mtime_for_kind(
            index_dir,
            str(row.get("kind") or ""),
            project_root,
        )
        if raw_mtime is None or float(export_mtime) > raw_mtime:
            return True
    return False


def needs_export_or_sync(
    status: dict[str, Any],
    export_summary: dict[str, Any],
    raw_mtime: float | None,
    *,
    force: bool,
) -> bool:
    return bool(
        force
        or status.get("needsEditorExport")
        or not export_summary.get("files")
        or raw_mtime is None
    )


__all__ = [
    "export_dir_summary",
    "exports_newer_than_raw",
    "needs_export_or_sync",
    "raw_newest_mtime",
    "resolve_export_dir",
    "resolve_project_name",
]
