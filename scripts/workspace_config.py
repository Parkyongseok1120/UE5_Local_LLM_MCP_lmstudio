#!/usr/bin/env python
"""Workspace discovery and the shared per-user Unreal configuration."""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_LMSTUDIO_ROOT = Path.home() / ".lmstudio"
DEFAULT_ENGINE_VERSION = "5.8"
DEFAULT_INDEX_NAMESPACE = "unreal58"
FALLBACK_INDEX_REL = Path("data/unreal58/rag.sqlite")
RUNTIME_INDEX_PATH_ENV = "UNREAL_RAG_INDEX_PATH"
DEFAULT_SHARED_CONFIG: dict = {
    "activeProject": None,
    "projectSearchRoots": [],
    "defaultEngineRoot": "",
    "engineRootsByAssociation": {},
    "autoEditorExport": True,
    "installEditorGraphPlugin": False,
}
WORKSPACE_DIR_NAMES = (
    "UE5_Local_LLM_MCP_lmstudio",
    "Unreal58-RAG",
    "UnrealEngine57Dev_RAG",
)


def index_namespace_from_version(version: str) -> str:
    """Map an engine version to its portable index namespace."""

    text = str(version or "").strip()
    if not text:
        return DEFAULT_INDEX_NAMESPACE
    digits = "".join(char for char in text if char.isdigit())
    return f"unreal{digits}" if digits else DEFAULT_INDEX_NAMESPACE


def engine_version_to_namespace(engine_version: str) -> str:
    return index_namespace_from_version(engine_version)


def find_workspace_root(start: Path | None = None) -> Path:
    env_root = os.environ.get("UNREAL58_ROOT", "").strip()
    if env_root:
        candidate = Path(env_root).expanduser().resolve()
        if candidate.exists():
            return candidate
    origin = Path(__file__).resolve().parent.parent if start is None else start
    resolved = origin.resolve()
    for candidate in [resolved, *resolved.parents]:
        if candidate.name in WORKSPACE_DIR_NAMES:
            return candidate
        if (candidate / "config" / "workspace.json").exists():
            return candidate
    return resolved


def canonical_workspace_root(start: Path | None = None) -> Path:
    root = find_workspace_root(start)
    for config_path in (
        root / "config" / "workspace.local.json",
        root / "config" / "workspace.json",
    ):
        if not config_path.exists():
            continue
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            configured = str(data.get("rootPath") or "").strip()
            if configured:
                native_root = configured.replace("\\", os.sep).replace("/", os.sep)
                candidate = Path(native_root).expanduser()
                if candidate.is_absolute() and candidate.exists():
                    return candidate.resolve()
        except Exception:
            pass
    return root


def shared_config_path() -> Path:
    env_path = os.environ.get("SHARED_UNREAL_CONFIG", "").strip()
    if env_path:
        return Path(env_path).resolve()
    return (DEFAULT_LMSTUDIO_ROOT / "config" / "unreal-workspace.json").resolve()


def load_shared_config() -> dict:
    path = shared_config_path()
    if not path.exists():
        return dict(DEFAULT_SHARED_CONFIG)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {**DEFAULT_SHARED_CONFIG, "_configError": f"{path}: {exc}"}
    return data if isinstance(data, dict) else dict(DEFAULT_SHARED_CONFIG)


def save_shared_config(config: dict) -> Path:
    from atomic_io import atomic_write_text

    path = shared_config_path()
    atomic_write_text(path, json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    return path


def active_project_names() -> list[str]:
    active = load_shared_config().get("activeProject")
    if not active:
        return []
    path = Path(str(active))
    names = {path.stem}
    if path.parent.name:
        names.add(path.parent.name)
    return sorted(names)


def load_workspace_config(start: Path | None = None) -> dict:
    root = find_workspace_root(start)
    path = root / "config" / "workspace.json"
    local_path = root / "config" / "workspace.local.json"
    defaults: dict = {
        "rootPath": str(canonical_workspace_root(root)),
        "engineVersion": DEFAULT_ENGINE_VERSION,
        "indexNamespace": DEFAULT_INDEX_NAMESPACE,
        "indexPath": FALLBACK_INDEX_REL.as_posix(),
        "defaultEngineRoot": "",
        "engineRootsByAssociation": {},
        "knowledgeRoots": {
            "guidelines": "RAG_Project_Guidelines",
            "gameDesign": "Game_Design_Docs",
            "projectSnapshots": "data/unreal_projects/text_snapshot",
        },
    }
    if not path.exists() and not local_path.exists():
        return defaults
    merged = dict(defaults)
    for source in (path, local_path):
        if not source.exists():
            continue
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if isinstance(data, dict):
            merged.update(data)
    if not str(merged.get("indexNamespace") or "").strip():
        merged["indexNamespace"] = index_namespace_from_version(
            str(merged.get("engineVersion") or DEFAULT_ENGINE_VERSION)
        )
    return merged
