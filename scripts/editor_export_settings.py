"""Read bounded Editor export settings from shared configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from workspace_config import load_shared_config

ExportScope = Literal["all", "materials", "blueprints"]
ExportMode = Literal["auto", "headless", "request"]


def editor_export_content_path(start: Path | None = None) -> str:
    del start
    raw = str(load_shared_config().get("editorExportContentPath") or "/Game").strip()
    return raw or "/Game"


def editor_export_maps_path(start: Path | None = None) -> str:
    config = load_shared_config()
    raw = str(config.get("editorExportMapsPath") or "").strip()
    return raw or editor_export_content_path(start)


def editor_export_scope(start: Path | None = None) -> ExportScope:
    del start
    raw = str(load_shared_config().get("editorExportScope") or "all").strip().lower()
    if raw in {"material", "materials"}:
        return "materials"
    if raw in {"blueprint", "blueprints", "bp"}:
        return "blueprints"
    return "all"


def editor_export_timeout_sec(start: Path | None = None) -> int:
    del start
    try:
        value = int(load_shared_config().get("editorExportTimeoutSec") or 1800)
    except (TypeError, ValueError):
        value = 1800
    return max(120, min(value, 7200))


__all__ = [
    "ExportMode",
    "ExportScope",
    "editor_export_content_path",
    "editor_export_maps_path",
    "editor_export_scope",
    "editor_export_timeout_sec",
]
