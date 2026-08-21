#!/usr/bin/env python
"""Resolve Unreal58-RAG workspace paths and normalize legacy locators."""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Iterable, Mapping
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
    "autoSetupOnProjectSwitch": True,
}

WORKSPACE_DIR_NAMES = ("UE5_Local_LLM_MCP_lmstudio", "Unreal58-RAG", "UnrealEngine57Dev_RAG")

LEGACY_LOCATOR_PREFIXES: tuple[str, ...] = ()


def is_windows_host_platform(host_platform: str | None = None) -> bool:
    """Return whether *host_platform* uses Windows path matching rules."""

    host = sys.platform if host_platform is None else str(host_platform)
    return host.strip().lower() in {"win32", "windows", "nt"}


def ascii_windows_fold(value: str) -> str:
    """Fold only ASCII A-Z, avoiding Unicode lower/casefold collisions."""

    return str(value).translate(str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"))


def normalize_portable_path(
    value: object,
    *,
    trim_outer_slashes: bool = False,
    strip_project_uri: bool = True,
) -> str:
    """Normalize separators without changing Unicode spelling or case."""

    normalized = str(value or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if strip_project_uri and normalized.lower().startswith("project://"):
        normalized = normalized[len("project://") :]
    normalized = re.sub(r"/{2,}", "/", normalized)
    if trim_outer_slashes:
        normalized = normalized.strip("/")
    elif len(normalized) > 1:
        normalized = normalized.rstrip("/")
    return normalized


def filesystem_path_identity(
    value: object,
    host_platform: str | None = None,
    *,
    trim_outer_slashes: bool = False,
    strip_project_uri: bool = True,
) -> str:
    """Normalize a portable path without changing its Unicode spelling.

    Windows compatibility folds ASCII case only.  Full Unicode lower/casefold
    is not a faithful model of NTFS upcase rules and can merge distinct names
    such as U+0130 and ``I`` followed by U+0307.  POSIX identity remains exact.
    """

    normalized = normalize_portable_path(
        value,
        trim_outer_slashes=trim_outer_slashes,
        strip_project_uri=strip_project_uri,
    )
    return ascii_windows_fold(normalized) if is_windows_host_platform(host_platform) else normalized


def resolve_canonical_absolute_path(
    value: object,
    *,
    base_path: Path | str | None = None,
    realpath: bool = True,
) -> str:
    """Resolve an absolute path, using filesystem spelling when it exists."""

    raw = "" if value is None else str(value)
    if not raw:
        return ""
    base = os.getcwd() if base_path is None else str(base_path)
    resolved = os.path.abspath(raw if os.path.isabs(raw) else os.path.join(base, raw))
    if realpath:
        try:
            if os.path.exists(resolved):
                resolved = os.path.realpath(resolved)
        except OSError:
            # Missing, inaccessible, or concurrently removed paths retain a
            # lexical absolute identity so matching remains fail-closed.
            pass
    return str(resolved)


def canonical_absolute_path_identity(
    value: object,
    host_platform: str | None = None,
    *,
    base_path: Path | str | None = None,
    realpath: bool = True,
) -> str:
    """Return a host-aware absolute path identity without Unicode folding."""

    resolved = resolve_canonical_absolute_path(value, base_path=base_path, realpath=realpath)
    if not resolved or not is_windows_host_platform(host_platform):
        return resolved
    return ascii_windows_fold(resolved.replace("\\", "/"))


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
                native_root = configured.replace("\\", os.sep).replace("/", os.sep)
                candidate = Path(native_root).expanduser()
                # Ignore a stale root copied from another machine. The
                # discovered workspace is a safer recovery target than a
                # non-existent path (or a foreign drive-letter literal).
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
        # Persist portable relative paths. Path separators are normalized for
        # the current host only when the value is resolved.
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


INDEX_CONFIG_KEYS = ("engineVersion", "indexNamespace", "indexPath")


def _read_workspace_index_settings_at_root(root: Path) -> dict[str, str]:
    """Return explicit index settings from *root*'s local workspace overlays.

    The repository configuration is the portable, project-owned override.  The
    per-user shared config is intentionally considered only when neither
    workspace overlay selects an index, so installing one project cannot
    silently replace another project's checked-in/local selection.
    """

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
    """Resolve index settings with workspace config before per-user config."""

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
    """Resolve a portable configured index path relative to *root*."""

    native_index_path = value.replace("\\", os.sep).replace("/", os.sep)
    candidate = Path(native_index_path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (root / candidate).resolve()


def _runtime_index_path_override() -> Path | None:
    """Return the installer-owned absolute runtime index, when explicitly set."""

    raw = str(os.environ.get(RUNTIME_INDEX_PATH_ENV) or "").strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise ValueError(f"{RUNTIME_INDEX_PATH_ENV} must be an absolute path")
    return candidate.resolve()


def resolve_index_path_in_workspace(workspace: Path | str) -> Path:
    """Resolve an index for an explicit workspace without reading UNREAL58_ROOT.

    Packaging a supplied source tree must not accidentally bundle the index
    selected by a different running MCP server through its environment.  This
    deliberately uses the supplied root as the boundary and does not follow a
    configured ``rootPath`` outside that tree.
    """

    root = Path(workspace).expanduser().resolve()
    settings = _index_settings_at_root(root)
    index_path = settings.get("indexPath", "")
    if index_path:
        return _resolve_configured_index_path(root, index_path)
    namespace = settings.get("indexNamespace", "")
    if not namespace:
        namespace = index_namespace_from_version(settings.get("engineVersion", ""))
    return (root / "data" / namespace / "rag.sqlite").resolve()


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
    settings = _index_settings(start)
    version = settings.get("engineVersion", "")
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


_MAX_ENGINE_REGISTRATION_BYTES = 2 * 1024 * 1024
_WINDOWS_ENGINE_BUILDS_KEY = r"SOFTWARE\Epic Games\Unreal Engine\Builds"


def _application_settings_dirs(
    host_platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> list[Path]:
    """Return only Unreal's documented per-host application settings roots."""

    host = host_platform or sys.platform
    env = os.environ if environ is None else environ
    user_home = Path.home() if home is None else home
    candidates: list[Path] = []
    if host == "win32":
        program_data = str(
            env.get("PROGRAMDATA")
            or env.get("ProgramData")
            or env.get("ALLUSERSPROFILE")
            or ""
        ).strip()
        if program_data:
            candidates.append(Path(program_data) / "Epic")
    elif host == "darwin":
        candidates.append(user_home / "Library" / "Application Support" / "Epic")
    else:
        # FUnixPlatformProcess::ApplicationSettingsDir deliberately uses this
        # fixed home-relative path rather than XDG_CONFIG_HOME.
        candidates.append(user_home / ".config" / "Epic")

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        key = canonical_absolute_path_identity(resolved, host)
        if key and key not in seen:
            seen.add(key)
            unique.append(resolved)
    return unique


def _default_launcher_manifest_paths(
    host_platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> list[Path]:
    if (host_platform or sys.platform) != "win32":
        return []
    return [
        root / "UnrealEngineLauncher" / "LauncherInstalled.dat"
        for root in _application_settings_dirs(host_platform, environ, home)
    ]


def _default_install_ini_paths(
    host_platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> list[Path]:
    if (host_platform or sys.platform) not in {"darwin", "linux"}:
        return []
    return [
        root / "UnrealEngine" / "Install.ini"
        for root in _application_settings_dirs(host_platform, environ, home)
    ]


def _read_bounded_registration_text(path: Path) -> str:
    try:
        size = path.stat().st_size
        if size < 0 or size > _MAX_ENGINE_REGISTRATION_BYTES:
            return ""
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return ""


def _valid_engine_association_token(value: object) -> str:
    token = str(value or "").strip()
    if not token or len(token) > 256 or token in {".", "..", "(Default)"}:
        return ""
    if any(ord(char) < 32 for char in token):
        return ""
    if any(char in token for char in ("/", "\\", "=", "[", "]")):
        return ""
    return token


def _registered_engine_root(value: object) -> Path | None:
    raw = str(value or "").strip()
    if (
        not raw
        or len(raw) > 32767
        or any(char in raw for char in ("\0", "\r", "\n"))
    ):
        return None
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
        raw = raw[1:-1].strip()
    candidate = Path(raw)
    if not candidate.is_absolute():
        return None
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError):
        return None
    return resolved if _is_engine_root(resolved) else None


def _launcher_registration_rows(paths: Iterable[Path]) -> list[tuple[str, object, str]]:
    rows: list[tuple[str, object, str]] = []
    for manifest in paths:
        text = _read_bounded_registration_text(Path(manifest))
        if not text:
            continue
        try:
            payload = json.loads(text)
        except (ValueError, json.JSONDecodeError):
            continue
        installations = payload.get("InstallationList") if isinstance(payload, dict) else None
        for item in installations if isinstance(installations, list) else []:
            if not isinstance(item, dict):
                continue
            rows.append(
                (
                    str(item.get("AppName") or ""),
                    item.get("InstallLocation"),
                    "launcher-manifest",
                )
            )
    return rows


def _install_ini_registration_rows(paths: Iterable[Path]) -> list[tuple[str, object, str]]:
    rows: list[tuple[str, object, str]] = []
    for config_path in paths:
        text = _read_bounded_registration_text(Path(config_path))
        if not text:
            continue
        section = ""
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith((";", "#")):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1].strip()
                continue
            if section != "Installations" or "=" not in line:
                continue
            association, root = line.split("=", 1)
            rows.append((association, root, "install-ini"))
    return rows


def _windows_registry_registration_rows(
    injected: Mapping[str, object] | None,
    *,
    read_system_registry: bool,
) -> list[tuple[str, object, str]]:
    if injected is not None:
        return [(key, value, "windows-registry") for key, value in injected.items()]
    if not read_system_registry or sys.platform != "win32":
        return []
    try:
        import winreg

        rows: list[tuple[str, object, str]] = []
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            _WINDOWS_ENGINE_BUILDS_KEY,
            0,
            winreg.KEY_READ,
        ) as key:
            for index in range(512):
                try:
                    association, root, value_type = winreg.EnumValue(key, index)
                except OSError:
                    break
                if value_type in {winreg.REG_SZ, winreg.REG_EXPAND_SZ}:
                    rows.append((association, root, "windows-registry"))
        return rows
    except (ImportError, OSError):
        return []


def registered_engine_installations(
    host_platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    *,
    launcher_manifest_paths: Iterable[Path] | None = None,
    registry_installations: Mapping[str, object] | None = None,
    install_ini_paths: Iterable[Path] | None = None,
    read_system_registry: bool | None = None,
) -> list[dict[str, str]]:
    """Enumerate bounded, validated OS registrations without modifying them."""

    host = host_platform or sys.platform
    if read_system_registry is None:
        read_system_registry = (
            host_platform is None
            and environ is None
            and home is None
            and registry_installations is None
        )
    rows: list[tuple[str, object, str]] = []
    if host == "win32":
        manifests = (
            _default_launcher_manifest_paths(host, environ, home)
            if launcher_manifest_paths is None
            else list(launcher_manifest_paths)
        )
        rows.extend(_launcher_registration_rows(manifests))
        rows.extend(
            _windows_registry_registration_rows(
                registry_installations,
                read_system_registry=bool(read_system_registry),
            )
        )
    elif host in {"darwin", "linux"}:
        config_paths = (
            _default_install_ini_paths(host, environ, home)
            if install_ini_paths is None
            else list(install_ini_paths)
        )
        rows.extend(_install_ini_registration_rows(config_paths))

    installations: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw_association, raw_root, source in rows:
        association = _valid_engine_association_token(raw_association)
        root = _registered_engine_root(raw_root)
        if not association or root is None:
            continue
        key = (association, canonical_absolute_path_identity(root, host))
        if key in seen:
            continue
        seen.add(key)
        installations.append(
            {
                "association": association,
                "engineRoot": str(root),
                "source": source,
            }
        )
    installations.sort(
        key=lambda item: (
            item["association"],
            canonical_absolute_path_identity(item["engineRoot"], host),
        )
    )
    return installations


def _engine_sort_key(path: Path) -> tuple[tuple[int, ...], str]:
    version_text = engine_root_numeric_version(path)
    version = tuple(int(part) for part in version_text.split(".")) if version_text else ()
    return version, path.name.casefold()


def _discover_engine_roots(
    host_platform: str | None = None,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
    *,
    launcher_manifest_paths: Iterable[Path] | None = None,
    registry_installations: Mapping[str, object] | None = None,
    install_ini_paths: Iterable[Path] | None = None,
    read_system_registry: bool | None = None,
) -> list[Path]:
    candidates: list[Path] = []
    seen: set[str] = set()
    for registration in registered_engine_installations(
        host_platform,
        environ,
        home,
        launcher_manifest_paths=launcher_manifest_paths,
        registry_installations=registry_installations,
        install_ini_paths=install_ini_paths,
        read_system_registry=read_system_registry,
    ):
        root = Path(registration["engineRoot"])
        key = canonical_absolute_path_identity(root, host_platform)
        if key and key not in seen:
            seen.add(key)
            candidates.append(root)
    for location in _engine_location_candidates(host_platform, environ, home):
        if not location.is_dir():
            continue
        roots = [location] if _is_engine_root(location) else []
        try:
            # Do not bake a UE 5 minor range into discovery.  A workspace can
            # legitimately target UE4, an older UE5 release, or a later UE
            # release; the Engine layout is the compatibility contract here.
            roots.extend(path for path in location.glob("UE_*") if _is_engine_root(path))
        except OSError:
            continue
        for root in roots:
            resolved = root.resolve()
            key = canonical_absolute_path_identity(resolved, host_platform)
            if key not in seen:
                seen.add(key)
                candidates.append(resolved)
    candidates.sort(key=_engine_sort_key, reverse=True)
    return candidates


def discover_engine_roots(
    host_platform: str | None = None,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
    *,
    launcher_manifest_paths: Iterable[Path] | None = None,
    registry_installations: Mapping[str, object] | None = None,
    install_ini_paths: Iterable[Path] | None = None,
    read_system_registry: bool | None = None,
) -> list[Path]:
    """Return validated Unreal roots in newest-first order for the current host."""

    # Keep the no-argument seam used by callers/tests that replace the local
    # discovery implementation, while still allowing deterministic injected
    # host/environment discovery for portability checks.
    if (
        host_platform is None
        and environ is None
        and home is None
        and launcher_manifest_paths is None
        and registry_installations is None
        and install_ini_paths is None
        and read_system_registry is None
    ):
        return _discover_engine_roots()
    return _discover_engine_roots(
        host_platform,
        environ,
        home,
        launcher_manifest_paths=launcher_manifest_paths,
        registry_installations=registry_installations,
        install_ini_paths=install_ini_paths,
        read_system_registry=read_system_registry,
    )


_NUMERIC_ENGINE_ASSOCIATION_RE = re.compile(
    r"^(?:UE_)?(\d+(?:\.\d+)+)$", re.IGNORECASE
)


def engine_association_folder(association: object) -> str:
    """Return the installed folder name for a numeric EngineAssociation.

    Source-build GUIDs and other custom association identifiers deliberately
    return an empty string.  They cannot be safely inferred from an arbitrary
    installed engine folder and must instead use an explicit root, environment
    override, or an exact ``engineRootsByAssociation`` mapping.
    """

    match = _NUMERIC_ENGINE_ASSOCIATION_RE.fullmatch(str(association or "").strip())
    return f"UE_{match.group(1)}" if match else ""


def engine_association_version(association: object) -> str:
    """Return a numeric association version without imposing a UE release cap."""

    match = _NUMERIC_ENGINE_ASSOCIATION_RE.fullmatch(str(association or "").strip())
    return match.group(1) if match else ""


def engine_associations_match(left: object, right: object) -> bool:
    """Compare numeric aliases while keeping custom/source identifiers exact."""

    left_text = str(left or "").strip()
    right_text = str(right or "").strip()
    left_version = engine_association_version(left_text)
    right_version = engine_association_version(right_text)
    if left_version or right_version:
        return bool(left_version and right_version and left_version == right_version)
    return bool(left_text and left_text == right_text)


def engine_root_numeric_version(engine_root: Path) -> str:
    """Read an engine's major.minor without trusting an install-time path hint."""

    build_version = engine_root / "Engine" / "Build" / "Build.version"
    if build_version.is_file():
        try:
            payload = json.loads(build_version.read_text(encoding="utf-8-sig"))
            major = int(payload.get("MajorVersion"))
            minor = int(payload.get("MinorVersion"))
            return f"{major}.{minor}"
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    match = re.search(
        r"UE[_ -]?(\d+(?:\.\d+)*)",
        engine_root.name,
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else ""


def engine_root_matches_numeric_association(
    engine_root: Path,
    association: object,
) -> bool:
    requested = engine_association_version(association)
    actual = engine_root_numeric_version(engine_root)
    # A numeric project association is a binding, not a best-effort hint.  An
    # engine whose version cannot be established must not inherit that binding
    # merely because UNREAL_ENGINE_ROOT happens to point at it.
    return not requested or bool(actual and actual == requested)


def _engine_root_from_config_value(value: object, start: Path | None = None) -> Path:
    """Resolve a portable configured engine root against this workspace."""

    raw = str(value or "").strip()
    native = raw.replace("\\", os.sep).replace("/", os.sep)
    candidate = Path(native).expanduser()
    if not candidate.is_absolute():
        candidate = canonical_workspace_root(start) / candidate
    return candidate.resolve()


def _configured_engine_roots_by_association(start: Path | None = None) -> dict[str, str]:
    """Return exact custom-association mappings with workspace precedence."""

    roots: dict[str, str] = {}
    # Shared configuration is a machine-level fallback.  The workspace (and
    # its local overlay) must win so multiple projects can use different
    # source-build associations on one host.
    for source in (load_shared_config(), load_workspace_config(start)):
        entries = source.get("engineRootsByAssociation") if isinstance(source, dict) else None
        if not isinstance(entries, dict):
            continue
        for association, root in entries.items():
            key = str(association or "").strip()
            value = str(root or "").strip()
            if key and value:
                roots[key] = value
    return roots


def _engine_root_resolution(
    *,
    engine_root: Path,
    source: str,
    association: str,
) -> dict[str, str | bool]:
    return {
        "ok": True,
        "engineRoot": str(engine_root.resolve()),
        "source": source,
        "requestedEngineAssociation": association,
        "errorCode": "",
        "error": "",
    }


def _unresolved_engine_association(
    association: str,
    detail: str,
) -> dict[str, str | bool]:
    return {
        "ok": False,
        "engineRoot": "",
        "source": "",
        "requestedEngineAssociation": association,
        "errorCode": "ENGINE_ASSOCIATION_UNRESOLVED",
        "error": (
            f"ENGINE_ASSOCIATION_UNRESOLVED: EngineAssociation {association!r} {detail}. "
            "Set engineRoot, UNREAL_ENGINE_ROOT, or an exact engineRootsByAssociation entry."
        ),
    }


def resolve_engine_root_for_association(
    association: object,
    start: Path | None = None,
    *,
    explicit_engine_root: str | Path | None = None,
    host_platform: str | None = None,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
    launcher_manifest_paths: Iterable[Path] | None = None,
    registry_installations: Mapping[str, object] | None = None,
    install_ini_paths: Iterable[Path] | None = None,
    read_system_registry: bool | None = None,
) -> dict[str, str | bool]:
    """Resolve an engine root without silently substituting another project engine.

    Any non-empty association is a binding: explicit roots and the environment
    may intentionally override it, exact config mappings may bind custom
    source-build IDs, and numeric associations may discover only their exact
    ``UE_<version>`` folder.  ``defaultEngineRoot`` and newest-installed
    fallback are reserved for projects with no EngineAssociation.
    """

    association_text = str(association or "").strip()
    host = host_platform or sys.platform
    env = os.environ if environ is None else environ
    injected_discovery = (
        host_platform is not None
        or environ is not None
        or home is not None
        or launcher_manifest_paths is not None
        or registry_installations is not None
        or install_ini_paths is not None
    )
    should_read_system_registry = (
        not injected_discovery
        if read_system_registry is None
        else bool(read_system_registry)
    )

    def discovered_roots() -> list[Path]:
        if not injected_discovery and read_system_registry is None:
            return discover_engine_roots()
        return discover_engine_roots(
            host_platform=host,
            environ=env,
            home=home,
            launcher_manifest_paths=launcher_manifest_paths,
            registry_installations=registry_installations,
            install_ini_paths=install_ini_paths,
            read_system_registry=should_read_system_registry,
        )

    def registered_installations() -> list[dict[str, str]]:
        return registered_engine_installations(
            host_platform=host,
            environ=env,
            home=home,
            launcher_manifest_paths=launcher_manifest_paths,
            registry_installations=registry_installations,
            install_ini_paths=install_ini_paths,
            read_system_registry=should_read_system_registry,
        )

    def resolve_override(value: object, source: str) -> dict[str, str | bool] | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        root = _engine_root_from_config_value(raw, start)
        if _is_engine_root(root):
            return _engine_root_resolution(
                engine_root=root,
                source=source,
                association=association_text,
            )
        if association_text:
            return _unresolved_engine_association(
                association_text,
                f"could not use {source} ({root})",
            )
        return None

    explicit = resolve_override(explicit_engine_root, "argument")
    if explicit is not None:
        return explicit
    environment = resolve_override(env.get("UNREAL_ENGINE_ROOT", ""), "environment")
    environment_association_is_managed = "UNREAL_ENGINE_ROOT_ASSOCIATION" in env
    environment_association = str(
        env.get("UNREAL_ENGINE_ROOT_ASSOCIATION") or ""
    ).strip()
    stale_managed_environment = bool(
        association_text
        and environment_association_is_managed
        and not engine_associations_match(environment_association, association_text)
    )

    def same_resolved_root(
        left: dict[str, str | bool] | None,
        right: dict[str, str | bool] | None,
    ) -> bool:
        if not left or not right or not left.get("ok") or not right.get("ok"):
            return False
        return canonical_absolute_path_identity(left.get("engineRoot"), host) == (
            canonical_absolute_path_identity(right.get("engineRoot"), host)
        )

    if association_text:
        mapped_root = _configured_engine_roots_by_association(start).get(association_text)
        if mapped_root:
            mapped = resolve_override(mapped_root, "config.engineRootsByAssociation")
            if mapped is not None:
                # Preserve legacy source attribution when both hints already
                # identify the same root; a conflicting exact mapping wins.
                if (
                    not stale_managed_environment
                    and environment is not None
                    and same_resolved_root(environment, mapped)
                ):
                    return environment
                return mapped

        requested_folder = engine_association_folder(association_text)
        registered_matches: dict[str, dict[str, str]] = {}
        for registration in registered_installations():
            if not engine_associations_match(
                registration.get("association"),
                association_text,
            ):
                continue
            registered_root = Path(registration["engineRoot"])
            if requested_folder and not engine_root_matches_numeric_association(
                registered_root,
                association_text,
            ):
                continue
            key = canonical_absolute_path_identity(registered_root, host)
            registered_matches.setdefault(key, registration)
        if len(registered_matches) > 1:
            return _unresolved_engine_association(
                association_text,
                "has multiple conflicting registered engine roots",
            )
        if registered_matches:
            registration = next(iter(registered_matches.values()))
            registered = _engine_root_resolution(
                engine_root=Path(registration["engineRoot"]),
                source=f"registered.{registration['source']}",
                association=association_text,
            )
            if (
                not stale_managed_environment
                and environment is not None
                and same_resolved_root(environment, registered)
            ):
                return environment
            return registered

        if environment is not None and not stale_managed_environment:
            if not environment.get("ok"):
                return environment
            environment_root = Path(str(environment.get("engineRoot") or ""))
            if engine_root_matches_numeric_association(
                environment_root,
                association_text,
            ):
                return environment
            # Installers may persist UNREAL_ENGINE_ROOT for the project that
            # was active during setup.  A later numeric EngineAssociation is
            # stronger evidence; ignore the stale pin and continue discovery.

        if requested_folder:
            requested_identity = filesystem_path_identity(
                requested_folder,
                host,
                strip_project_uri=False,
            )
            for candidate in discovered_roots():
                if filesystem_path_identity(
                    candidate.name,
                    host,
                    strip_project_uri=False,
                ) == requested_identity:
                    return _engine_root_resolution(
                        engine_root=candidate,
                        source="EngineAssociation",
                        association=association_text,
                    )
            return _unresolved_engine_association(
                association_text,
                f"does not have an installed {requested_folder} engine",
            )
        return _unresolved_engine_association(
            association_text,
            "is a custom/source-build identifier without an exact mapping",
        )

    if environment is not None:
        return environment

    config = load_workspace_config(start)
    shared = load_shared_config()
    for source, value in (
        ("config.defaultEngineRoot", config.get("defaultEngineRoot")),
        ("shared.defaultEngineRoot", shared.get("defaultEngineRoot")),
    ):
        resolved = resolve_override(value, source)
        if resolved is not None:
            return resolved
    for candidate in discovered_roots():
        return _engine_root_resolution(
            engine_root=candidate,
            source="latest-installed",
            association="",
        )
    return {
        "ok": False,
        "engineRoot": "",
        "source": "",
        "requestedEngineAssociation": "",
        "errorCode": "ENGINE_ROOT_UNRESOLVED",
        "error": "Could not resolve an Unreal Engine installation.",
    }


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
        for idx, part in enumerate(parts):
            if part == "data" and idx + 1 < len(parts):
                return parts[idx + 1]
    return index_namespace_from_version(resolve_engine_version(start))


def resolve_index_dir(start: Path | None = None) -> Path:
    runtime_index = _runtime_index_path_override()
    if runtime_index is not None:
        return runtime_index.parent
    root = canonical_workspace_root(start)
    index_path = _index_settings(start).get("indexPath", "")
    if index_path:
        return _resolve_configured_index_path(root, index_path).parent
    namespace = resolve_index_namespace(start)
    return (root / "data" / namespace).resolve()


def resolve_index_path(start: Path | None = None) -> Path:
    runtime_index = _runtime_index_path_override()
    if runtime_index is not None:
        return runtime_index
    root = canonical_workspace_root(start)
    index_path = _index_settings(start).get("indexPath", "")
    if index_path:
        return _resolve_configured_index_path(root, index_path)
    return (root / "data" / resolve_index_namespace(start) / "rag.sqlite").resolve()


def resolve_engine_root(start: Path | None = None) -> Path:
    """Resolve the default engine only for an association-free operation."""

    resolution = resolve_engine_root_for_association("", start)
    root = str(resolution.get("engineRoot") or "")
    return Path(root) if root else Path("")


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
        path = find_workspace_root(start) / path
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
    *,
    host_platform: str | None = None,
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
        if (
            filesystem_path_identity(
                resolved.name,
                host_platform,
                strip_project_uri=False,
            )
            == filesystem_path_identity(
                project_root.name,
                host_platform,
                strip_project_uri=False,
            )
            and resolved.parent == project_root.parent
        ):
            return default
        normalized = filesystem_path_identity(
            resolved.as_posix(),
            host_platform,
            strip_project_uri=False,
        )
        expected_suffix = filesystem_path_identity(
            "Saved/LmStudioMetadataExports",
            host_platform,
            strip_project_uri=False,
        )
        if normalized.endswith(f"/{expected_suffix}"):
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


def _identity_relative_suffix(
    candidate: str,
    prefix: str,
    host_platform: str | None = None,
) -> str | None:
    candidate_path = normalize_portable_path(
        candidate,
        strip_project_uri=False,
    )
    prefix_path = normalize_portable_path(
        prefix,
        strip_project_uri=False,
    )
    candidate_identity = filesystem_path_identity(
        candidate_path,
        host_platform,
        strip_project_uri=False,
    )
    prefix_identity = filesystem_path_identity(
        prefix_path,
        host_platform,
        strip_project_uri=False,
    )
    if not candidate_identity or not prefix_identity:
        return None
    if candidate_identity == prefix_identity:
        return ""
    boundary = prefix_identity if prefix_identity.endswith("/") else f"{prefix_identity}/"
    if not candidate_identity.startswith(boundary):
        return None
    return candidate_path[len(prefix_path) :].lstrip("/")


def normalize_locator(
    locator: str,
    workspace_root: Path | None = None,
    *,
    host_platform: str | None = None,
) -> str:
    physical_root = (workspace_root or find_workspace_root()).resolve()
    workspace_root = canonical_workspace_root(workspace_root)
    text = str(locator or "").strip()
    if not text:
        return text

    normalized = text.replace("\\", "/")
    workspace_text = str(workspace_root).replace("\\", "/")

    for legacy in LEGACY_LOCATOR_PREFIXES:
        legacy_norm = legacy.replace("\\", "/")
        suffix = _identity_relative_suffix(normalized, legacy_norm, host_platform)
        if suffix is not None:
            return str(workspace_root / Path(suffix))

    physical_text = str(physical_root).replace("\\", "/")
    suffix = _identity_relative_suffix(normalized, physical_text, host_platform)
    if suffix is not None:
        return str(workspace_root / Path(suffix))

    if _identity_relative_suffix(normalized, workspace_text, host_platform) is not None:
        return str(Path(normalized))

    return text
