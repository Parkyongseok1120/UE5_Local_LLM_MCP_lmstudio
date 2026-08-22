#!/usr/bin/env python
"""Report freshness and availability of Editor-exported metadata files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from editor_capture_state import completed_capture
from editor_metadata_catalog import METADATA_FILES
from editor_metadata_provenance import summarize_editor_row_provenance
from editor_metadata_sources import (
    asset_paths_for_kind,
    latest_config_mtime,
    latest_project_asset_mtime,
    metadata_file_info,
)
from workspace_paths import (
    editor_export_dir,
    find_workspace_root,
    load_shared_config,
    resolve_index_dir,
)

def editor_metadata_status(
    index_dir: str | Path | None = None,
    project_root: str | Path | None = None,
    stale_after_hours: float = 24.0,
) -> dict[str, Any]:
    workspace = find_workspace_root()
    idx = Path(index_dir) if index_dir else resolve_index_dir()
    if not idx.is_absolute():
        idx = workspace / idx

    active_project = ""
    selected_project: Path | None = None
    if project_root:
        selected_project = Path(project_root).resolve()
        root = selected_project
    else:
        config = load_shared_config()
        active_project = str(config.get("activeProject") or "")
        selected_project = Path(active_project).resolve() if active_project else None
        root = selected_project or Path()
    if root and root.suffix.lower() == ".uproject":
        root = root.parent

    latest_asset_mtime = latest_project_asset_mtime(root) if root and root.exists() else None
    latest_config_source_mtime = latest_config_mtime(root) if root and root.exists() else None
    now = __import__("time").time()
    files: dict[str, Any] = {}
    missing: list[str] = []
    stale: list[str] = []
    for kind, filename in METADATA_FILES.items():
        selected_root = root if root and root.exists() else None
        selected_scope = selected_project or selected_root
        path, metadata_rows, aggregate_source = metadata_file_info(
            idx,
            kind,
            selected_scope,
        )
        row_count = len(metadata_rows)
        capture = completed_capture(idx, selected_project or root, kind)
        authoritative_empty = bool(capture and capture.get("rowCount") == 0)
        exists = bool(metadata_rows) or authoritative_empty
        row: dict[str, Any] = {"path": str(path), "exists": exists, "rowCount": row_count}
        relevant_mtime = latest_asset_mtime
        if kind == "project_settings":
            relevant_mtime = latest_config_source_mtime
        elif kind not in {"asset_registry", "project_settings"}:
            asset_paths = asset_paths_for_kind(idx, kind, selected_scope)
            kind_mtime = latest_project_asset_mtime(root, asset_paths) if root and root.exists() and asset_paths else None
            if kind_mtime is not None:
                relevant_mtime = kind_mtime
        if exists:
            provenance = summarize_editor_row_provenance(metadata_rows)
            capture_mtime = (
                provenance.oldest_mtime
                if metadata_rows
                else float(capture["capturedAt"])
                if capture is not None
                else None
            )
            provenance_known = provenance.known if metadata_rows else authoritative_empty
            age_hours = (
                (now - capture_mtime) / 3600.0
                if capture_mtime is not None
                else None
            )
            older_than_source = (
                bool(relevant_mtime and capture_mtime < relevant_mtime)
                if capture_mtime is not None
                else None
            )
            row.update(
                {
                    "sizeBytes": path.stat().st_size if path.is_file() else 0,
                    "fileMtime": path.stat().st_mtime if path.is_file() else None,
                    "mtime": capture_mtime,
                    "captureMtime": capture_mtime,
                    "captureProvenanceKnown": provenance_known,
                    "exportKinds": (
                        list(provenance.export_kinds)
                        if metadata_rows
                        else [str(capture.get("kind") or kind)]
                        if capture is not None
                        else []
                    ),
                    "ageHours": round(age_hours, 2) if age_hours is not None else None,
                    "rowCount": row_count,
                    "olderThanRelevantSource": older_than_source,
                }
            )
            if authoritative_empty:
                row["authoritativeEmpty"] = True
                row["captureRunId"] = str(capture.get("runId") or "") if capture else ""
            if not provenance_known:
                row["freshnessUnknown"] = True
                row["freshnessReason"] = "selected_project_row_provenance_missing"
            if aggregate_source:
                row["aggregateSource"] = "raw_animation_metadata.jsonl"
            if (
                not provenance_known
                or (age_hours is not None and age_hours > stale_after_hours)
                or older_than_source is True
            ):
                stale.append(kind)
        else:
            missing.append(kind)
        files[kind] = row

    needs_export = bool(missing or stale)
    export_dir = editor_export_dir()
    export_dir_info: dict[str, Any] = {"configured": bool(export_dir), "path": str(export_dir or "")}
    if export_dir:
        try:
            export_files = sorted(export_dir.glob("*.jsonl"))
            export_dir_info["fileCount"] = len(export_files)
            export_dir_info["newestMtime"] = max((p.stat().st_mtime for p in export_files), default=None)
        except OSError:
            export_dir_info["fileCount"] = 0
            export_dir_info["newestMtime"] = None

    return {
        "ok": not needs_export,
        "activeProject": active_project,
        "projectRoot": str(root) if root else "",
        "indexDir": str(idx),
        "latestProjectUAssetMtime": latest_asset_mtime,
        "missingKinds": missing,
        "staleKinds": stale,
        "needsEditorExport": needs_export,
        "exportDir": export_dir_info,
        "files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Report Editor metadata freshness.")
    parser.add_argument("--index-dir", default=None)
    parser.add_argument("--project-root", default="")
    parser.add_argument("--stale-after-hours", type=float, default=24.0)
    args = parser.parse_args()
    payload = editor_metadata_status(args.index_dir, args.project_root or None, args.stale_after_hours)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
