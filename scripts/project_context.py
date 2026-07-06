#!/usr/bin/env python
"""Resolve activeProject context for browse paths, RAG filters, and MCP tool hints."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from workspace_paths import (
    default_editor_export_dir,
    find_workspace_root,
    resolve_active_project_path,
    resolve_active_project_root,
)


_PROJECT_CONTEXT_CACHE: dict[str, Any] | None = None


def _cache_ttl_seconds() -> float:
    raw = os.environ.get("PROJECT_CONTEXT_TTL_SECONDS", "60")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 60.0


def clear_project_context_cache() -> None:
    global _PROJECT_CONTEXT_CACHE
    _PROJECT_CONTEXT_CACHE = None


def _resolve_workspace_root() -> Path:
    env_root = os.environ.get("WORKSPACE_ROOT", "").strip()
    if env_root:
        candidate = Path(env_root).expanduser()
        try:
            return candidate.resolve()
        except OSError:
            return candidate
    docs = Path.home() / "Documents"
    if docs.is_dir():
        return docs.resolve()
    return find_workspace_root()


def _read_uproject_modules(uproject: Path) -> list[str]:
    try:
        data = json.loads(uproject.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    modules = data.get("Modules") if isinstance(data, dict) else None
    if not isinstance(modules, list):
        return []
    names: list[str] = []
    for item in modules:
        if isinstance(item, dict):
            name = str(item.get("Name") or "").strip()
            if name:
                names.append(name)
    return names


def _list_source_modules(project_dir: Path) -> list[str]:
    source_root = project_dir / "Source"
    if not source_root.is_dir():
        return []
    return sorted(
        child.name
        for child in source_root.iterdir()
        if child.is_dir() and not child.name.startswith(".")
    )


def _relative_browse_path(workspace_root: Path, target: Path) -> str:
    try:
        rel = os.path.relpath(str(target.resolve()), str(workspace_root.resolve()))
    except ValueError:
        return ""
    if rel.startswith(".."):
        return ""
    return rel.replace("\\", "/")


def _project_under_workspace(project_dir: Path, workspace_root: Path) -> bool:
    try:
        project_dir.resolve().relative_to(workspace_root.resolve())
        return True
    except ValueError:
        return False


def _mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _project_context_cache_key(uproject: Path, project_dir: Path, workspace_root: Path) -> tuple[str, str, str, int, int]:
    try:
        uproject_key = str(uproject.resolve())
    except OSError:
        uproject_key = str(uproject)
    try:
        project_key = str(project_dir.resolve())
    except OSError:
        project_key = str(project_dir)
    try:
        workspace_key = str(workspace_root.resolve())
    except OSError:
        workspace_key = str(workspace_root)
    return (
        uproject_key,
        project_key,
        workspace_key,
        _mtime_ns(uproject),
        _mtime_ns(project_dir / "Source"),
    )


def _cached_project_context(cache_key: tuple[str, str, str, int, int]) -> dict[str, Any] | None:
    ttl = _cache_ttl_seconds()
    if ttl <= 0 or not _PROJECT_CONTEXT_CACHE:
        return None
    if _PROJECT_CONTEXT_CACHE.get("key") != cache_key:
        return None
    if time.monotonic() >= float(_PROJECT_CONTEXT_CACHE.get("expiresAt") or 0.0):
        return None
    value = _PROJECT_CONTEXT_CACHE.get("value")
    return dict(value) if isinstance(value, dict) else None


def _store_project_context(cache_key: tuple[str, str, str, int, int], ctx: dict[str, Any]) -> None:
    global _PROJECT_CONTEXT_CACHE
    ttl = _cache_ttl_seconds()
    if ttl <= 0:
        _PROJECT_CONTEXT_CACHE = None
        return
    _PROJECT_CONTEXT_CACHE = {
        "key": cache_key,
        "expiresAt": time.monotonic() + ttl,
        "value": dict(ctx),
    }


def resolve_active_project_context(start: Path | None = None) -> dict[str, Any]:
    uproject = resolve_active_project_path(start)
    workspace_root = _resolve_workspace_root()
    if not uproject:
        return {
            "ok": False,
            "error": "activeProject is not set. Call unreal_set_active_project first.",
            "workspaceRoot": str(workspace_root),
            "browseAvailable": False,
            "suggestedToolCalls": [{"tool": "unreal_set_active_project", "args": {}}],
        }

    project_dir = resolve_active_project_root(start) or uproject.parent
    cache_key = _project_context_cache_key(uproject, project_dir, workspace_root)
    cached = _cached_project_context(cache_key)
    if cached:
        return cached

    project_name = uproject.stem
    modules = _read_uproject_modules(uproject)
    source_modules = _list_source_modules(project_dir)
    primary_module = modules[0] if modules else (source_modules[0] if source_modules else project_name)

    source_root = project_dir / "Source" / primary_module
    if not source_root.is_dir() and source_modules:
        source_root = project_dir / "Source" / source_modules[0]
    if not source_root.is_dir():
        source_root = project_dir / "Source"

    content_root = project_dir / "Content"
    export_dir = default_editor_export_dir(start)
    browse_available = _project_under_workspace(project_dir, workspace_root)

    ctx: dict[str, Any] = {
        "ok": True,
        "uprojectPath": str(uproject),
        "projectName": project_name,
        "projectDir": str(project_dir),
        "modules": modules,
        "primaryModule": primary_module,
        "sourceRoot": str(source_root.resolve()) if source_root.exists() else str(project_dir / "Source"),
        "sourceModules": source_modules,
        "contentRoot": str(content_root),
        "exportDir": str(export_dir),
        "workspaceRoot": str(workspace_root),
        "browseAvailable": browse_available,
    }

    if browse_available:
        ctx["sourceBrowsePath"] = _relative_browse_path(workspace_root, Path(ctx["sourceRoot"]))
        ctx["contentBrowsePath"] = _relative_browse_path(workspace_root, content_root)
    else:
        ctx["sourceBrowsePath"] = ""
        ctx["contentBrowsePath"] = ""
        ctx["browseNote"] = (
            "Project is outside WORKSPACE_ROOT; search_files/list_directory may be unavailable. "
            "Use RAG, metadata lookup, or absolute Python reads."
        )

    _store_project_context(cache_key, ctx)
    return ctx


def active_project_name(start: Path | None = None) -> str:
    ctx = resolve_active_project_context(start)
    return str(ctx.get("projectName") or "") if ctx.get("ok") else ""


def project_context_or_error(start: Path | None = None) -> dict[str, Any]:
    return resolve_active_project_context(start)
