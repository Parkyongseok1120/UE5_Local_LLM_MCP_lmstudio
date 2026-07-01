#!/usr/bin/env python
"""Sync Editor export JSONL into RAG raw inputs and optionally rebuild the index."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from editor_metadata_status import METADATA_FILES, editor_metadata_status
from ingest_editor_exports import discover_exports
from workspace_paths import (
    auto_editor_export_enabled,
    default_editor_export_dir,
    editor_export_dir,
    find_workspace_root,
    load_shared_config,
    resolve_active_project_path,
    resolve_engine_root,
    resolve_index_dir,
)

PRIORITY_KINDS = ("material", "blueprint", "animation")


def _resolve_export_dir(explicit: str | None) -> Path:
    from editor_export_runner import resolve_export_dir

    if explicit and str(explicit).strip():
        return resolve_export_dir(explicit)
    return resolve_export_dir(None)


def _resolve_project_name(explicit: str | None) -> str:
    if explicit and str(explicit).strip():
        return str(explicit).strip()
    active = str(load_shared_config().get("activeProject") or "").strip()
    if active:
        return Path(active).stem
    return "Project"


def _export_dir_summary(export_dir: Path | None) -> dict[str, Any]:
    if not export_dir or not export_dir.is_dir():
        return {"configured": False, "path": str(export_dir or ""), "files": [], "newestMtime": None}
    files = discover_exports(export_dir)
    newest: float | None = None
    file_rows = []
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


def _raw_newest_mtime(index_dir: Path, kinds: tuple[str, ...] = PRIORITY_KINDS) -> float | None:
    newest: float | None = None
    for kind in kinds:
        filename = METADATA_FILES.get(kind)
        if not filename:
            continue
        path = index_dir / filename
        if not path.is_file():
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        newest = mtime if newest is None else max(newest, mtime)
    return newest


def _needs_export_or_sync(
    status: dict[str, Any],
    export_summary: dict[str, Any],
    raw_mtime: float | None,
    *,
    force: bool,
) -> bool:
    if force:
        return True
    if status.get("needsEditorExport"):
        return True
    if not export_summary.get("files"):
        return True
    export_mtime = export_summary.get("newestMtime")
    if raw_mtime is None:
        return True
    if export_mtime and export_mtime < (status.get("latestProjectUAssetMtime") or 0):
        return True
    return False


def sync_editor_metadata(
    *,
    export_dir: str | Path | None = None,
    index_dir: str | Path | None = None,
    project_name: str | None = None,
    rebuild_index: bool = True,
    force_ingest: bool = False,
    auto_export: bool = False,
    content_path: str | None = None,
    export_scope: str | None = None,
    export_mode: str = "auto",
) -> dict[str, Any]:
    workspace = find_workspace_root()
    idx = resolve_index_dir() if not index_dir else Path(index_dir)
    if not idx.is_absolute():
        idx = workspace / idx

    resolved_export = _resolve_export_dir(str(export_dir) if export_dir else None)
    project = _resolve_project_name(project_name)
    status = editor_metadata_status(idx, None, 24.0)
    export_summary = _export_dir_summary(resolved_export)
    raw_mtime = _raw_newest_mtime(idx)
    export_mtime = export_summary.get("newestMtime")

    export_result: dict[str, Any] | None = None
    needs_work = _needs_export_or_sync(status, export_summary, raw_mtime, force=force_ingest)
    if auto_export and needs_work:
        from editor_export_runner import run_editor_export

        export_result = run_editor_export(
            export_dir=resolved_export,
            content_path=content_path,
            scope=export_scope,  # type: ignore[arg-type]
            mode=export_mode,  # type: ignore[arg-type]
        )
        if export_result.get("ok"):
            resolved_export = Path(str(export_result.get("exportDir") or resolved_export or ""))
            export_summary = _export_dir_summary(resolved_export)
            export_mtime = export_summary.get("newestMtime")
        status = editor_metadata_status(idx, None, 24.0)

    should_ingest = force_ingest
    ingest_reason = "forced" if force_ingest else ""
    if not should_ingest and resolved_export and export_summary["files"]:
        if raw_mtime is None:
            should_ingest = True
            ingest_reason = "no_raw_metadata"
        elif export_mtime and export_mtime > raw_mtime:
            should_ingest = True
            ingest_reason = "export_dir_newer_than_index"
        elif status.get("needsEditorExport"):
            should_ingest = True
            ingest_reason = "metadata_status_needs_export_or_ingest"
        elif export_result and export_result.get("ok"):
            should_ingest = True
            ingest_reason = "fresh_export"

    actions: list[str] = []
    ingest_result: dict[str, Any] | None = None
    rebuild_result: dict[str, Any] | None = None

    if auto_export and export_result and not export_result.get("ok"):
        actions.append(f"Automatic Editor export failed: {export_result.get('error') or 'unknown error'}")
        if export_result.get("logPath"):
            actions.append(f"Inspect export log: {export_result.get('logPath')}")

    if not resolved_export:
        actions.append("Set editorExportDir in unreal-workspace.json or pass exportDir.")
    elif not export_summary["files"]:
        actions.append(f"No JSONL exports found under {resolved_export}.")
        if not auto_export:
            actions.append("Run .\\rag.ps1 export-editor-metadata for automatic export + sync.")
    elif should_ingest:
        out_dir = str(idx.relative_to(workspace) if idx.is_relative_to(workspace) else idx)
        cmd = [
            sys.executable,
            str(workspace / "scripts" / "ingest_editor_exports.py"),
            "--export-dir",
            str(resolved_export),
            "--out-dir",
            out_dir,
            "--project-name",
            project,
        ]
        proc = subprocess.run(cmd, cwd=str(workspace), capture_output=True, text=True, check=False)
        ingest_result = {
            "ok": proc.returncode == 0,
            "reason": ingest_reason,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        }
        if proc.returncode != 0:
            actions.append("Ingest failed; inspect stderr and rerun after fixing export files.")
        else:
            actions.append("Ingest completed.")
            if rebuild_index:
                build_cmd = [
                    sys.executable,
                    str(workspace / "scripts" / "incremental_build.py"),
                    "--out-dir",
                    out_dir,
                ]
                build_proc = subprocess.run(
                    build_cmd, cwd=str(workspace), capture_output=True, text=True, check=False
                )
                rebuild_result = {
                    "ok": build_proc.returncode == 0,
                    "stdout": build_proc.stdout.strip(),
                    "stderr": build_proc.stderr.strip(),
                }
                if build_proc.returncode == 0:
                    actions.append("Incremental index rebuild completed.")
                else:
                    actions.append("Incremental rebuild failed; run .\\rag.ps1 build-incremental manually.")
    else:
        actions.append("Export dir is not newer than indexed raw metadata; ingest skipped.")
        if status.get("needsEditorExport"):
            actions.append("Metadata still stale vs project uassets; run export-editor-metadata.")

    refreshed_status = editor_metadata_status(idx, None, 24.0)
    ok = bool(
        (export_result is None or export_result.get("ok"))
        and (
            (ingest_result and ingest_result.get("ok"))
            or (not should_ingest and not refreshed_status.get("needsEditorExport"))
        )
    )
    return {
        "ok": ok,
        "projectName": project,
        "indexDir": str(idx),
        "exportDir": export_summary,
        "exportResult": export_result,
        "ingestReason": ingest_reason or None,
        "ingest": ingest_result,
        "rebuild": rebuild_result,
        "metadataStatusBefore": status,
        "metadataStatusAfter": refreshed_status,
        "nextActions": actions,
        "agentWorkflow": [
            "1. unreal_editor_metadata_status",
            "2. unreal_run_editor_export or unreal_sync_editor_metadata with autoExport=true",
            "3. unreal_asset_graph_lookup for target asset",
            "4. unreal_material_claim_validate or unreal_blueprint_claim_validate for concrete claims",
        ],
    }


def refresh_editor_metadata(
    *,
    export_dir: str | Path | None = None,
    index_dir: str | Path | None = None,
    project_name: str | None = None,
    rebuild_index: bool = True,
    content_path: str | None = None,
    export_scope: str | None = None,
    export_mode: str = "auto",
    force: bool = False,
) -> dict[str, Any]:
    return sync_editor_metadata(
        export_dir=export_dir,
        index_dir=index_dir,
        project_name=project_name,
        rebuild_index=rebuild_index,
        force_ingest=force,
        auto_export=True,
        content_path=content_path,
        export_scope=export_scope,
        export_mode=export_mode,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync Editor metadata exports into RAG index.")
    parser.add_argument("--export-dir", default="")
    parser.add_argument("--index-dir", default="")
    parser.add_argument("--project-name", default="")
    parser.add_argument("--no-rebuild", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--auto-export", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--content-path", default="")
    parser.add_argument("--scope", default="")
    parser.add_argument("--mode", default="auto", choices=["auto", "headless", "request"])
    parser.add_argument("--refresh", action="store_true", help="Export from Editor, ingest, and rebuild.")
    args = parser.parse_args()

    common = {
        "export_dir": args.export_dir or None,
        "index_dir": args.index_dir or None,
        "project_name": args.project_name or None,
        "rebuild_index": not args.no_rebuild,
        "content_path": args.content_path or None,
        "export_scope": args.scope or None,
        "export_mode": args.mode,
    }
    if args.refresh:
        payload = refresh_editor_metadata(**common, force=args.force)
    else:
        payload = sync_editor_metadata(
            **common,
            force_ingest=args.force,
            auto_export=args.auto_export,
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
