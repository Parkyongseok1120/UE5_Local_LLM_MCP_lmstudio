"""Resolve and optionally persist one Editor metadata export directory."""

from __future__ import annotations

import os
from pathlib import Path

from editor_export_paths import (
    default_editor_export_dir,
    editor_export_dir,
    editor_export_dir_for_project,
    normalize_editor_export_dir,
)
from workspace_config import load_shared_config, save_shared_config


def resolve_export_dir(
    explicit: str | Path | None = None,
    *,
    project_file: str | Path | None = None,
) -> Path:
    project_scoped = project_file is not None and bool(str(project_file).strip())
    if project_scoped:
        path = editor_export_dir_for_project(
            Path(str(project_file)),
            explicit,
            use_shared_config=False,
        )
    elif explicit and str(explicit).strip():
        path = normalize_editor_export_dir(explicit)
    else:
        path = editor_export_dir() or default_editor_export_dir()
    path.mkdir(parents=True, exist_ok=True)
    if not project_scoped:
        _maybe_persist_export_dir(path)
    return path


def _maybe_persist_export_dir(path: Path) -> None:
    config = load_shared_config()
    current = str(config.get("editorExportDir") or "").strip()
    if current == str(path):
        return
    try:
        if current and Path(os.path.expandvars(current)).expanduser().resolve() == path.resolve():
            return
    except OSError:
        pass
    config["editorExportDir"] = str(path)
    save_shared_config(config)


__all__ = ["resolve_export_dir"]
