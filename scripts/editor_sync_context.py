#!/usr/bin/env python
"""Resolve one exact-project context for Direct Editor metadata sync."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from editor_export_paths import editor_export_dir_for_project
from workspace_paths import (
    find_workspace_root,
    resolve_active_project_path,
    resolve_index_dir,
)


@dataclass(frozen=True)
class EditorSyncContext:
    workspace: Path
    index_dir: Path
    project_file: Path
    project_root: Path
    project_name: str
    export_dir: Path

    def response_identity(self) -> dict[str, str]:
        return {
            "projectName": self.project_name,
            "projectFile": str(self.project_file),
            "projectRoot": str(self.project_root),
            "indexDir": str(self.index_dir),
        }


class EditorSyncContextError(RuntimeError):
    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.details = details or {}

    def as_payload(self) -> dict[str, Any]:
        return {
            **self.details,
            "ok": False,
            "errorCode": self.error_code,
            "error": str(self),
            "stageCommitted": False,
        }


def _exact_project(workspace: Path, project_file: str | Path | None) -> Path:
    selected = (
        Path(project_file).expanduser()
        if project_file is not None and str(project_file).strip()
        else resolve_active_project_path(workspace)
    )
    if selected is None:
        raise EditorSyncContextError(
            "PROJECT_SELECTOR_REQUIRED",
            "Editor metadata sync requires one exact existing .uproject path.",
        )
    if not selected.is_absolute():
        selected = workspace / selected
    selected = selected.resolve()
    if not selected.is_file() or selected.suffix.casefold() != ".uproject":
        raise EditorSyncContextError(
            "PROJECT_SELECTOR_REQUIRED",
            f"Editor metadata sync requires one exact existing .uproject path: {selected}",
            details={"projectFile": str(selected)},
        )
    return selected


def resolve_editor_sync_context(
    *,
    workspace: Path | None = None,
    export_dir: str | Path | None = None,
    index_dir: str | Path | None = None,
    project_name: str | None = None,
    project_file: str | Path | None = None,
) -> EditorSyncContext:
    ws = (workspace or find_workspace_root()).expanduser().resolve()
    project = _exact_project(ws, project_file)
    requested_name = str(project_name or "").strip()
    if requested_name and requested_name.casefold() != project.stem.casefold():
        raise EditorSyncContextError(
            "PROJECT_SELECTOR_MISMATCH",
            "project_name does not identify the selected .uproject descriptor.",
            details={
                "projectName": requested_name,
                "projectFile": str(project),
                "projectRoot": str(project.parent),
            },
        )

    configured_index = Path(index_dir).expanduser() if index_dir else resolve_index_dir(ws)
    if not configured_index.is_absolute():
        configured_index = ws / configured_index
    from direct_rag_refresh_target import resolve_project_refresh_target

    target = resolve_project_refresh_target(configured_index, ws, project)
    if target.get("ok") is not True:
        raise EditorSyncContextError(
            str(target.get("errorCode") or "PROJECT_INDEX_RESOLUTION_FAILED"),
            str(target.get("error") or "Could not resolve the exact project's RAG index."),
            details={
                **target,
                "projectFile": str(project),
                "projectRoot": str(project.parent),
                "indexDir": str(configured_index),
            },
        )
    selected_index = Path(str(target["indexDir"])).expanduser().resolve()
    selected_export = editor_export_dir_for_project(
        project,
        configured=export_dir,
        use_shared_config=False,
    )
    return EditorSyncContext(
        workspace=ws,
        index_dir=selected_index,
        project_file=project,
        project_root=project.parent,
        project_name=project.stem,
        export_dir=selected_export,
    )


__all__ = [
    "EditorSyncContext",
    "EditorSyncContextError",
    "resolve_editor_sync_context",
]
