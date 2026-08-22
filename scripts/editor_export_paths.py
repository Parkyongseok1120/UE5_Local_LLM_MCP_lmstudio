#!/usr/bin/env python
"""Resolve bounded Unreal Editor metadata export locations."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from active_project_paths import resolve_active_project_root
from portable_path_identity import filesystem_path_identity
from workspace_config import load_shared_config


def _project_root(project: Path) -> Path:
    resolved = project.expanduser().resolve()
    return resolved.parent if resolved.suffix.casefold() == ".uproject" else resolved


def _normalize_for_project(
    configured: str | Path | None,
    project_root: Path | None,
    *,
    host_platform: str | None = None,
) -> Path:
    default = (
        (project_root / "Saved" / "LmStudioMetadataExports").resolve()
        if project_root
        else default_editor_export_dir()
    )
    raw = str(configured or "").strip()
    if not raw:
        return default
    path = Path(os.path.expandvars(raw)).expanduser()
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    if project_root:
        try:
            if resolved == project_root.resolve():
                return default
        except OSError:
            pass
        if (
            filesystem_path_identity(resolved.name, host_platform, strip_project_uri=False)
            == filesystem_path_identity(project_root.name, host_platform, strip_project_uri=False)
            and resolved.parent == project_root.parent
        ):
            return default
        normalized = filesystem_path_identity(
            resolved.as_posix(), host_platform, strip_project_uri=False
        )
        suffix = filesystem_path_identity(
            "Saved/LmStudioMetadataExports", host_platform, strip_project_uri=False
        )
        if normalized.endswith(f"/{suffix}"):
            try:
                resolved.relative_to(project_root.resolve())
            except ValueError:
                return default
    return resolved if str(resolved) else default


def default_editor_export_dir(start: Path | None = None) -> Path:
    root = resolve_active_project_root(start)
    if root:
        return (root / "Saved" / "LmStudioMetadataExports").resolve()
    local_app = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app:
        base = Path(local_app)
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        xdg_data = os.environ.get("XDG_DATA_HOME", "").strip()
        base = Path(xdg_data) if xdg_data else Path.home() / ".local" / "share"
    return (base / "LmStudio" / "UnrealMetadataExports").resolve()


def normalize_editor_export_dir(
    configured: str | Path | None,
    start: Path | None = None,
    *,
    host_platform: str | None = None,
) -> Path:
    return _normalize_for_project(
        configured,
        resolve_active_project_root(start),
        host_platform=host_platform,
    )


def editor_export_dir_for_project(
    project: Path,
    configured: str | Path | None = None,
    *,
    host_platform: str | None = None,
    use_shared_config: bool = True,
) -> Path:
    """Resolve exports against the exact captured project, never mutable active state."""

    raw = configured
    if raw is None and use_shared_config:
        raw = str(load_shared_config().get("editorExportDir") or "").strip()
    return _normalize_for_project(
        raw,
        _project_root(project),
        host_platform=host_platform,
    )


def editor_export_dir(start: Path | None = None) -> Path | None:
    raw = str(load_shared_config().get("editorExportDir") or "").strip()
    return normalize_editor_export_dir(raw, start) if raw else default_editor_export_dir(start)


def auto_editor_export_enabled(start: Path | None = None) -> bool:
    del start
    value = load_shared_config().get("autoEditorExport", True)
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def editor_export_content_path(start: Path | None = None) -> str:
    del start
    raw = str(load_shared_config().get("editorExportContentPath") or "/Game").strip()
    return raw or "/Game"
