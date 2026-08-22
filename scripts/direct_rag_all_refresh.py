#!/usr/bin/env python
"""Coordinate a source refresh with an optional exact-project Editor snapshot."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

SourceScope = Literal["project_source", "all"]


def _snapshot_editor_exports(
    project: Path,
    export_dir: Path,
    content_path: str | None,
    staging_parent: Path,
    *,
    launch_editor: bool,
) -> tuple[dict[str, Any] | None, Path | None, dict[str, Any] | None]:
    from direct_rag_editor_snapshot import create_editor_export_snapshot
    from direct_rag_refresh_lock import DirectRagRefreshBusyError, index_refresh_lock
    from editor_export_runner import run_editor_export

    try:
        with index_refresh_lock(export_dir):
            export_result = (
                run_editor_export(
                    export_dir=export_dir,
                    content_path=content_path,
                    scope="all",
                    uproject=project,
                )
                if launch_editor
                else None
            )
            if export_result is not None and export_result.get("ok") is not True:
                return export_result, None, export_result
            snapshot = create_editor_export_snapshot(
                export_dir,
                staging_parent,
                project,
                expected_scope="all",
            )
            return export_result, snapshot, None
    except DirectRagRefreshBusyError as exc:
        error = {"ok": False, "errorCode": "EDITOR_EXPORT_BUSY", "error": str(exc)}
        return None, None, error
    except Exception as exc:
        error = {
            "ok": False,
            "errorCode": "EDITOR_EXPORT_SNAPSHOT_FAILED",
            "error": str(exc),
        }
        return None, None, error


def refresh_source_scope(
    *,
    scope: SourceScope,
    workspace: Path,
    project: Path,
    index_dir: Path,
    allow_editor_launch: bool,
    progress: Callable[[str], None],
) -> dict[str, Any]:
    from active_project_sync import sync_active_project
    from direct_rag_editor_snapshot import discard_editor_export_snapshot
    from editor_export_paths import editor_export_dir_for_project
    from workspace_paths import load_shared_config

    editor_export: dict[str, Any] | None = None
    snapshot: Path | None = None
    if scope == "all":
        config = load_shared_config()
        export_dir = editor_export_dir_for_project(project, use_shared_config=False)
        if allow_editor_launch:
            progress("editor_metadata: exporting the captured project before staged rebuild")
        editor_export, snapshot, snapshot_error = _snapshot_editor_exports(
            project,
            export_dir,
            str(config.get("editorExportContentPath") or "") or None,
            index_dir.parent,
            launch_editor=allow_editor_launch,
        )
        if snapshot_error is not None:
            return {
                "ok": False,
                "editorMetadataSetup": {
                    "ok": False,
                    "exportResult": editor_export,
                    "transactionError": snapshot_error,
                    "stageCommitted": False,
                },
            }

    progress("project_source: collecting symbols and rebuilding index (may take several minutes)")
    try:
        source_result = sync_active_project(
            workspace=workspace,
            project=project,
            index_dir=index_dir,
            progress=progress,
            editor_export_dir=snapshot,
        )
    finally:
        discard_editor_export_snapshot(snapshot)

    result: dict[str, Any] = {
        "ok": source_result.get("ok") is True,
        "projectSourceSync": source_result,
    }
    if scope == "all":
        result["editorMetadataSetup"] = {
            "ok": source_result.get("ok") is True,
            "exportResult": editor_export,
            "stageCommitted": source_result.get("stageCommitted") is True,
        }
    return result


__all__ = ["refresh_source_scope"]
