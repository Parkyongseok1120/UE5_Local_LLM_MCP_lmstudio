#!/usr/bin/env python
"""Resolve engine-versioned RAG index configuration and filesystem paths."""

from __future__ import annotations

import json
import os
from pathlib import Path

from workspace_config import (
    DEFAULT_ENGINE_VERSION,
    RUNTIME_INDEX_PATH_ENV,
    canonical_workspace_root,
    find_workspace_root,
    index_namespace_from_version,
    load_shared_config,
)

INDEX_CONFIG_KEYS = ("engineVersion", "indexNamespace", "indexPath")


def _read_workspace_index_settings_at_root(root: Path) -> dict[str, str]:
    """Return explicit index settings from a workspace and its local overlay."""

    values: dict[str, str] = {}
    for path in (root / "config" / "workspace.json", root / "config" / "workspace.local.json"):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        for key in INDEX_CONFIG_KEYS:
            value = str(data.get(key) or "").strip()
            if value:
                values[key] = value
    return values


def _read_workspace_index_settings(start: Path | None = None) -> dict[str, str]:
    return _read_workspace_index_settings_at_root(find_workspace_root(start))


def _index_settings_at_root(root: Path) -> dict[str, str]:
    workspace_values = _read_workspace_index_settings_at_root(root)
    if workspace_values:
        return workspace_values
    shared = load_shared_config()
    return {
        key: value
        for key in INDEX_CONFIG_KEYS
        if (value := str(shared.get(key) or "").strip())
    }


def _index_settings(start: Path | None = None) -> dict[str, str]:
    return _index_settings_at_root(find_workspace_root(start))


def _resolve_configured_index_path(root: Path, value: str) -> Path:
    native_index_path = value.replace("\\", os.sep).replace("/", os.sep)
    candidate = Path(native_index_path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (root / candidate).resolve()


def _runtime_index_path_override() -> Path | None:
    raw = str(os.environ.get(RUNTIME_INDEX_PATH_ENV) or "").strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise ValueError(f"{RUNTIME_INDEX_PATH_ENV} must be an absolute path")
    return candidate.resolve()


def resolve_index_path_in_workspace(workspace: Path | str) -> Path:
    """Resolve an index inside an explicit packaging workspace boundary."""

    root = Path(workspace).expanduser().resolve()
    settings = _index_settings_at_root(root)
    index_path = settings.get("indexPath", "")
    if index_path:
        return _resolve_configured_index_path(root, index_path)
    namespace = settings.get("indexNamespace", "")
    if not namespace:
        namespace = index_namespace_from_version(settings.get("engineVersion", ""))
    return (root / "data" / namespace / "rag.sqlite").resolve()


def resolve_engine_version(start: Path | None = None) -> str:
    version = _index_settings(start).get("engineVersion", "")
    if version:
        return version
    root = find_workspace_root(start)
    engine_roots: list[str] = []
    for path in (root / "config" / "workspace.json", root / "config" / "workspace.local.json"):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            engine_roots.append(str(data.get("defaultEngineRoot") or "").strip())
    engine_roots.append(str(load_shared_config().get("defaultEngineRoot") or "").strip())
    for engine_root in engine_roots:
        if engine_root:
            folder = Path(engine_root).name
            if folder.upper().startswith("UE_"):
                return folder[3:].replace("_", ".")
    return DEFAULT_ENGINE_VERSION


def resolve_index_namespace(start: Path | None = None) -> str:
    runtime_index = _runtime_index_path_override()
    if runtime_index is not None and runtime_index.parent.name:
        return runtime_index.parent.name
    settings = _index_settings(start)
    namespace = settings.get("indexNamespace", "")
    if namespace:
        return namespace
    index_path = settings.get("indexPath", "").replace("\\", "/")
    if index_path:
        parts = [part for part in Path(index_path).parts if part]
        for index, part in enumerate(parts):
            if part == "data" and index + 1 < len(parts):
                return parts[index + 1]
    return index_namespace_from_version(resolve_engine_version(start))


def resolve_index_dir(start: Path | None = None) -> Path:
    runtime_index = _runtime_index_path_override()
    if runtime_index is not None:
        return runtime_index.parent
    root = canonical_workspace_root(start)
    index_path = _index_settings(start).get("indexPath", "")
    if index_path:
        return _resolve_configured_index_path(root, index_path).parent
    return (root / "data" / resolve_index_namespace(start)).resolve()


def resolve_index_path(start: Path | None = None) -> Path:
    runtime_index = _runtime_index_path_override()
    if runtime_index is not None:
        return runtime_index
    root = canonical_workspace_root(start)
    index_path = _index_settings(start).get("indexPath", "")
    if index_path:
        return _resolve_configured_index_path(root, index_path)
    return (root / "data" / resolve_index_namespace(start) / "rag.sqlite").resolve()
