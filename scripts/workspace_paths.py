#!/usr/bin/env python
"""Resolve Unreal58-RAG workspace paths and normalize legacy locators."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DEFAULT_LMSTUDIO_ROOT = Path.home() / ".lmstudio"
DEFAULT_ENGINE_VERSION = "5.8"
DEFAULT_INDEX_NAMESPACE = "unreal58"
FALLBACK_INDEX_REL = Path("data/unreal58/rag.sqlite")
DEFAULT_SHARED_CONFIG: dict = {
    "activeProject": None,
    "projectSearchRoots": [],
    "defaultEngineRoot": "",
    "autoEditorExport": True,
    "installEditorGraphPlugin": False,
    "autoSetupOnProjectSwitch": True,
}

WORKSPACE_DIR_NAMES = ("UE5_Local_LLM_MCP_lmstudio", "Unreal58-RAG", "UnrealEngine57Dev_RAG")

LEGACY_LOCATOR_PREFIXES: tuple[str, ...] = ()


def find_workspace_root(start: Path | None = None) -> Path:
    env_root = os.environ.get("UNREAL58_ROOT", "").strip()
    if env_root:
        candidate = Path(env_root).expanduser().resolve()
        if candidate.exists():
            return candidate
    if start is None:
        start = Path(__file__).resolve().parent.parent
    start = start.resolve()
    for candidate in [start, *start.parents]:
        if candidate.name in WORKSPACE_DIR_NAMES:
            return candidate
        config = candidate / "config" / "workspace.json"
        if config.exists():
            return candidate
    return start


def canonical_workspace_root(start: Path | None = None) -> Path:
    root = find_workspace_root(start)
    for config_path in (root / "config" / "workspace.local.json", root / "config" / "workspace.json"):
        if not config_path.exists():
            continue
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            configured = str(data.get("rootPath") or "").strip()
            if configured:
                return Path(configured)
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
    config = load_shared_config()
    active = config.get("activeProject")
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
        "indexPath": str(FALLBACK_INDEX_REL).replace("/", "\\"),
        "defaultEngineRoot": "",
        "knowledgeRoots": {
            "guidelines": "RAG_Project_Guidelines",
            "gameDesign": "Game_Design_Docs",
            "projectSnapshots": "data/unreal_projects/text_snapshot",
        },
    }
    if not path.exists() and not local_path.exists():
        return defaults
    merged = dict(defaults)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if isinstance(data, dict):
            merged.update(data)
    try:
        local_data = json.loads(local_path.read_text(encoding="utf-8")) if local_path.exists() else {}
    except Exception:
        local_data = {}
    if isinstance(local_data, dict):
        merged.update(local_data)
    if not str(merged.get("indexNamespace") or "").strip():
        merged["indexNamespace"] = index_namespace_from_version(
            str(merged.get("engineVersion") or DEFAULT_ENGINE_VERSION)
        )
    return merged


def index_namespace_from_version(version: str) -> str:
    """Map engine semver minor to index namespace (e.g. 5.8 -> unreal58)."""
    text = str(version or "").strip()
    if not text:
        return DEFAULT_INDEX_NAMESPACE
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return DEFAULT_INDEX_NAMESPACE
    return f"unreal{digits}"


def engine_version_to_namespace(engine_version: str) -> str:
    return index_namespace_from_version(engine_version)


def resolve_engine_version(start: Path | None = None) -> str:
    config = load_workspace_config(start)
    version = str(config.get("engineVersion") or "").strip()
    if version:
        return version
    engine_root = str(config.get("defaultEngineRoot") or "").strip()
    if engine_root:
        folder = Path(engine_root).name
        if folder.upper().startswith("UE_"):
            return folder[3:].replace("_", ".")
    return DEFAULT_ENGINE_VERSION


def _engine_location_candidates(
    host_platform: str | None = None,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
) -> list[Path]:
    host = host_platform or sys.platform
    env = os.environ if environ is None else environ
    user_home = Path.home() if home is None else home
    if host == "win32":
        roots = []
        for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
            value = env.get(env_name, "").strip()
            if value:
                roots.append(Path(value) / "Epic Games")
        return roots
    if host == "darwin":
        return [Path("/Users/Shared/Epic Games"), Path("/Applications/Epic Games")]
    return [
        user_home / "UnrealEngine",
        user_home / "Epic Games",
        Path("/opt/UnrealEngine"),
        Path("/opt/Epic Games"),
    ]


def _is_engine_root(path: Path) -> bool:
    engine = path / "Engine"
    return engine.is_dir() and ((engine / "Source").is_dir() or (engine / "Build").is_dir())


def _discover_engine_roots(
    host_platform: str | None = None,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
) -> list[Path]:
    candidates: list[Path] = []
    seen: set[str] = set()
    for location in _engine_location_candidates(host_platform, environ, home):
        if not location.is_dir():
            continue
        roots = [location] if _is_engine_root(location) else []
        roots.extend(path for path in location.glob("UE_5.*") if _is_engine_root(path))
        for root in roots:
            resolved = root.resolve()
            key = str(resolved).casefold() if (host_platform or sys.platform) == "win32" else str(resolved)
            if key not in seen:
                seen.add(key)
                candidates.append(resolved)
    candidates.sort(key=lambda path: path.name, reverse=True)
    return candidates


def resolve_index_namespace(start: Path | None = None) -> str:
    config = load_workspace_config(start)
    namespace = str(config.get("indexNamespace") or "").strip()
    if namespace:
        return namespace
    index_path = str(config.get("indexPath") or "").strip().replace("\\", "/")
    if index_path:
        parts = [part for part in Path(index_path).parts if part]
        for idx, part in enumerate(parts):
            if part == "data" and idx + 1 < len(parts):
                return parts[idx + 1]
    return index_namespace_from_version(resolve_engine_version(start))


def resolve_index_dir(start: Path | None = None) -> Path:
    root = canonical_workspace_root(start)
    namespace = resolve_index_namespace(start)
    return (root / "data" / namespace).resolve()


def resolve_index_path(start: Path | None = None) -> Path:
    config = load_workspace_config(start)
    root = canonical_workspace_root(start)
    index_path = str(config.get("indexPath") or "").strip()
    if index_path:
        candidate = Path(index_path)
        if candidate.is_absolute():
            return candidate.resolve()
        return (root / candidate).resolve()
    return (resolve_index_dir(start) / "rag.sqlite").resolve()


def resolve_engine_root(start: Path | None = None) -> Path:
    env_root = os.environ.get("UNREAL_ENGINE_ROOT", "").strip()
    if env_root:
        return Path(env_root).expanduser().resolve()
    config = load_workspace_config(start)
    shared = load_shared_config()
    for source in (
        str(config.get("defaultEngineRoot") or "").strip(),
        str(shared.get("defaultEngineRoot") or "").strip(),
    ):
        if source:
            return Path(source).expanduser().resolve()
    for candidate in _discover_engine_roots():
        return candidate.resolve()
    return Path("")


def resolve_ubt_path(start: Path | None = None) -> Path:
    env_ubt = os.environ.get("UNREAL_UBT_PATH", "").strip()
    if env_ubt:
        return Path(env_ubt).expanduser().resolve()
    engine_root = resolve_engine_root(start)
    if str(engine_root) in {"", "."}:
        return Path("UnrealBuildTool.exe" if sys.platform == "win32" else "UnrealBuildTool.dll")
    ubt_root = (
        engine_root
        / "Engine"
        / "Binaries"
        / "DotNET"
        / "UnrealBuildTool"
    )
    names = ("UnrealBuildTool.exe", "UnrealBuildTool.dll") if sys.platform == "win32" else (
        "UnrealBuildTool.dll",
        "UnrealBuildTool.exe",
    )
    candidates = [ubt_root / name for name in names]
    return next((path for path in candidates if path.is_file()), candidates[0])


def resolve_engine_source_root(start: Path | None = None) -> Path:
    return resolve_engine_root(start) / "Engine" / "Source"


def resolve_active_project_path(start: Path | None = None) -> Path | None:
    config = load_shared_config()
    active = str(config.get("activeProject") or "").strip()
    if not active:
        return None
    path = Path(active).expanduser()
    if not path.is_absolute():
        path = Path(active)
    if path.exists():
        return path.resolve()
    return None


def resolve_active_project_root(start: Path | None = None) -> Path | None:
    active = resolve_active_project_path(start)
    if not active:
        return None
    if active.suffix.lower() == ".uproject":
        return active.parent.resolve()
    return active.resolve()


def resolve_active_project_source_root(start: Path | None = None) -> Path | None:
    root = resolve_active_project_root(start)
    if not root:
        return None
    source = root / "Source"
    if source.is_dir():
        return source.resolve()
    plugins = root / "Plugins"
    if plugins.is_dir():
        return root.resolve()
    return root.resolve()


def indexing_tier(start: Path | None = None) -> str:
    config = load_shared_config()
    tier = str(config.get("indexingTier") or "standard").strip().lower()
    if tier in {"lite", "standard", "full"}:
        return tier
    return "standard"


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
) -> Path:
    project_root = resolve_active_project_root(start)
    default = default_editor_export_dir(start)
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
        if resolved.name.lower() == project_root.name.lower() and resolved.parent == project_root.parent:
            return default
        normalized = resolved.as_posix().casefold()
        if normalized.endswith("/saved/lmstudiometadataexports"):
            try:
                resolved.relative_to(project_root.resolve())
            except ValueError:
                return default
    return resolved if str(resolved) else default


def editor_export_dir(start: Path | None = None) -> Path | None:
    config = load_shared_config()
    raw = str(config.get("editorExportDir") or "").strip()
    if not raw:
        return default_editor_export_dir(start)
    return normalize_editor_export_dir(raw, start)


def auto_editor_export_enabled(start: Path | None = None) -> bool:
    config = load_shared_config()
    value = config.get("autoEditorExport", True)
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def editor_export_content_path(start: Path | None = None) -> str:
    config = load_shared_config()
    raw = str(config.get("editorExportContentPath") or "/Game").strip()
    return raw or "/Game"


def normalize_locator(locator: str, workspace_root: Path | None = None) -> str:
    physical_root = (workspace_root or find_workspace_root()).resolve()
    workspace_root = canonical_workspace_root(workspace_root)
    text = str(locator or "").strip()
    if not text:
        return text

    normalized = text.replace("/", "\\")
    workspace_text = str(workspace_root)

    for legacy in LEGACY_LOCATOR_PREFIXES:
        legacy_norm = legacy.replace("/", "\\")
        if normalized.lower().startswith(legacy_norm.lower()):
            suffix = normalized[len(legacy_norm) :].lstrip("\\/")
            return str(workspace_root / Path(suffix))

    physical_text = str(physical_root)
    if normalized.lower().startswith(physical_text.lower()):
        suffix = normalized[len(physical_text) :].lstrip("\\/")
        return str(workspace_root / Path(suffix))

    if normalized.lower().startswith(workspace_text.lower()):
        return str(Path(normalized))

    return text
