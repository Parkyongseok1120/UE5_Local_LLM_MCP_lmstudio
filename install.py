#!/usr/bin/env python3
"""Cross-platform integrated installer for evidence-first coding and optional Unreal adapters."""

from __future__ import annotations

import argparse
import copy
import contextlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from control_runtime_identity import build_runtime_manifest

INSTALL_MANIFEST = json.loads((ROOT / "installer" / "manifest.json").read_text(encoding="utf-8"))
PRODUCT_VERSION = str(INSTALL_MANIFEST["productVersion"])
SKILL_NAME = "evidence-first-code-audit"
SKILL_SOURCE = ROOT / "skills" / SKILL_NAME
PRESET_SOURCE = SKILL_SOURCE / "assets" / "lmstudio-evidence-first.preset.json"
UNSAFE_AUTO_APPROVALS = {
    "lmstudio/js-code-sandbox:run_javascript",
    "lmstudio/js-code-sandbox:*",
    "mcp/unreal-agent:*",
    "mcp/unreal-rag:*",
}
PROFILE_DEFAULTS = {
    name: set(components)
    for name, components in INSTALL_MANIFEST["profiles"].items()
    if name != "custom"
}
ALL_COMPONENTS = set(INSTALL_MANIFEST["components"])
LMSTUDIO_STACK_COMPONENTS = frozenset({"lmstudio", "unreal", "context_compactor"})
PORTABLE_RULE_FILENAME = "evidence-first-code-audit.md"
CLINE_SETTINGS_RELATIVE_PATH = Path(".cline") / "data" / "settings" / "cline_mcp_settings.json"
LMSTUDIO_CONTEXT_POLICY_ENV = {
    "MCP_CONTEXT_COMPACTOR_ADVISORY",
    "MCP_REQUIRE_CONTEXT_COMPACTOR_ACTIVE",
    "MCP_CONTEXT_COMPACTOR_REQUIRED_FRONTENDS",
    "MCP_CONTEXT_COMPACTOR_MAX_AGE_SECONDS",
}
BOOTSTRAP_LOCK_TOKEN_ENV = "EVIDENCE_FIRST_BOOTSTRAP_LOCK_TOKEN"
CONTEXT_COMPACTOR_PLUGIN_ID = "codex/unreal-context-compactor"
CONTEXT_COMPACTOR_PLUGIN_NAME = "unreal-context-compactor"

INVALID_LOCK_GRACE_SECONDS = 300
MAX_INSTALLER_JSON_BYTES = 16 * 1024 * 1024


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _reject_filesystem_root(path: Path, label: str) -> None:
    resolved = path.expanduser().resolve()
    if resolved.parent == resolved:
        raise ValueError(f"{label} must not be a filesystem root: {resolved}")


def _default_portable_rule_path(args: argparse.Namespace) -> Path:
    """Return a neutral, managed location when no agent-specific path was supplied."""
    return args.state_home / "portable-rules" / PORTABLE_RULE_FILENAME


def _default_cline_settings_path() -> Path:
    """Return Cline's conventional per-user MCP settings location."""
    return Path.home() / CLINE_SETTINGS_RELATIVE_PATH


def _engine_root_is_valid(root: Path) -> bool:
    engine = root / "Engine"
    if not engine.is_dir():
        return False
    candidates = [
        engine / "Source",
        engine / "Build" / "BatchFiles" / "Build.bat",
        engine / "Build" / "BatchFiles" / "Mac" / "Build.sh",
        engine / "Build" / "BatchFiles" / "Linux" / "Build.sh",
        engine / "Binaries" / "DotNET" / "UnrealBuildTool" / "UnrealBuildTool.dll",
    ]
    return any(path.exists() for path in candidates)


def _launcher_manifest_engine_locations() -> list[Path]:
    manifests: list[Path] = []
    if sys.platform == "win32":
        program_data = os.environ.get("PROGRAMDATA", "").strip()
        if program_data:
            manifests.append(Path(program_data) / "Epic" / "UnrealEngineLauncher" / "LauncherInstalled.dat")
    elif sys.platform == "darwin":
        manifests.append(
            Path.home()
            / "Library"
            / "Application Support"
            / "Epic"
            / "UnrealEngineLauncher"
            / "LauncherInstalled.dat"
        )
    locations: list[Path] = []
    for manifest in manifests:
        try:
            if manifest.stat().st_size > 2 * 1024 * 1024:
                continue
            payload = json.loads(manifest.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, json.JSONDecodeError, UnicodeError):
            continue
        rows = payload.get("InstallationList") if isinstance(payload, dict) else None
        for row in rows if isinstance(rows, list) else []:
            location = str(row.get("InstallLocation") or "").strip() if isinstance(row, dict) else ""
            if location:
                locations.append(Path(location).expanduser())
    return locations


def _engine_sort_key(path: Path) -> tuple[tuple[int, ...], str]:
    match = re.search(r"UE[_ -]?(\d+(?:\.\d+)*)", path.name, flags=re.IGNORECASE)
    version = tuple(int(part) for part in match.group(1).split(".")) if match else ()
    return version, path.name.casefold()


def _common_engine_locations() -> list[Path]:
    explicit = os.environ.get("UNREAL_ENGINE_ROOT", "").strip()
    locations: list[Path] = [Path(explicit).expanduser()] if explicit else []
    if sys.platform == "win32":
        for name in ("ProgramFiles", "ProgramFiles(x86)"):
            value = os.environ.get(name, "").strip()
            if value:
                locations.append(Path(value) / "Epic Games")
    elif sys.platform == "darwin":
        locations.extend((Path("/Users/Shared/Epic Games"), Path("/Applications/Epic Games")))
    else:
        locations.extend(
            (
                Path.home() / "UnrealEngine",
                Path.home() / "Epic Games",
                Path("/opt/UnrealEngine"),
                Path("/opt/Epic Games"),
            )
        )
    return locations


def _detect_engine_root(engine_association: str = "") -> Path | None:
    candidates: list[Path] = []
    for location in [*_launcher_manifest_engine_locations(), *_common_engine_locations()]:
        if _engine_root_is_valid(location):
            candidates.append(location)
        try:
            if location.is_dir():
                candidates.extend(
                    path
                    for path in location.glob("UE_*")
                    if _engine_root_is_valid(path)
                )
        except OSError:
            continue
    unique: dict[str, Path] = {}
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        unique[str(resolved).casefold()] = resolved
    ordered = sorted(unique.values(), key=_engine_sort_key, reverse=True)
    requested = _engine_association_folder(engine_association)
    if engine_association.strip():
        # Registered/source-build identifiers are opaque. Selecting a newest
        # installed launcher engine for one would silently build/index the
        # wrong project, so only a numeric association may be auto-discovered.
        if not requested:
            return None
        exact = next((path for path in ordered if path.name.casefold() == requested.casefold()), None)
        if exact:
            return exact
        return None
    return ordered[0] if ordered else None


def _engine_association_folder(value: str) -> str:
    """Return an exact launcher folder name for a numeric association only."""

    match = re.fullmatch(r"(?:UE_)?(\d+(?:\.\d+)+)", str(value or "").strip(), re.IGNORECASE)
    return f"UE_{match.group(1)}" if match else ""


def _engine_root_matches_numeric_association(root: Path, association: str) -> bool:
    """Check a project binding without guessing from a custom source-build ID."""

    requested = _engine_association_folder(association)
    if not requested:
        return True
    requested_version = requested.removeprefix("UE_")
    return (
        _engine_version_from_root(root) == requested_version
        or root.name.casefold() == requested.casefold()
    )


def _configured_engine_root_for_association(
    shared: dict[str, Any],
    association: str,
) -> Path | None:
    mappings = shared.get("engineRootsByAssociation")
    if not association or not isinstance(mappings, dict):
        return None
    candidate = Path(str(mappings.get(association) or "")).expanduser()
    return candidate.resolve() if _engine_root_is_valid(candidate) else None


def _engine_minor_version(value: str) -> str:
    """Return a numeric Unreal major.minor version, or an empty string."""

    match = re.search(r"(?<!\d)(\d+)\.(\d+)(?:\.\d+)?", str(value or ""))
    if not match:
        return ""
    return f"{int(match.group(1))}.{int(match.group(2))}"


def _engine_version_from_root(engine_root: Path | None) -> str:
    """Read the selected engine's major.minor without changing selection policy."""

    if not engine_root:
        return ""
    build_version = engine_root / "Engine" / "Build" / "Build.version"
    try:
        payload = _load_json(build_version, {})
    except (OSError, ValueError, json.JSONDecodeError):
        payload = {}
    if isinstance(payload, dict):
        major = payload.get("MajorVersion")
        minor = payload.get("MinorVersion")
        if isinstance(major, int) and isinstance(minor, int):
            return f"{major}.{minor}"
    return _engine_minor_version(engine_root.name)


def _shared_index_selection_is_managed(shared: dict[str, Any]) -> bool:
    """Keep nonstandard user-selected index paths intact across reinstalls."""

    namespace = str(shared.get("indexNamespace") or "").strip()
    index_path = str(shared.get("indexPath") or "").strip().replace("\\", "/")
    if not namespace and not index_path:
        return True
    if namespace and re.fullmatch(r"unreal\d+", namespace):
        return not index_path or index_path == f"data/{namespace}/rag.sqlite"
    return bool(re.fullmatch(r"data/unreal\d+/rag\.sqlite", index_path))


def _sync_installer_index_settings(shared: dict[str, Any], engine_root: Path | None) -> None:
    """Write a standard index selection for the engine already selected by install()."""

    version = _engine_version_from_root(engine_root)
    if not version or not _shared_index_selection_is_managed(shared):
        return
    namespace = f"unreal{''.join(char for char in version if char.isdigit())}"
    shared["engineVersion"] = version
    shared["indexNamespace"] = namespace
    shared["indexPath"] = f"data/{namespace}/rag.sqlite"
    shared["embeddingsPath"] = f"data/{namespace}/embeddings"


def _default_editor_export_path(project: Path) -> Path:
    return project.parent / "Saved" / "LmStudioMetadataExports"


def _editor_export_path_is_default_like(value: Any) -> bool:
    raw = str(value or "").replace("\\", "/").rstrip("/").casefold()
    return not raw or raw.endswith("/saved/lmstudiometadataexports")


def _project_picker_initial_directory(args: argparse.Namespace) -> Path:
    config_path = args.lmstudio_home.expanduser() / "config" / "unreal-workspace.json"
    try:
        existing = _load_json(config_path, {})
    except (OSError, ValueError, json.JSONDecodeError):
        existing = {}
    active = Path(str(existing.get("activeProject") or "")).expanduser() if isinstance(existing, dict) else None
    if active and active.is_file():
        return active.parent
    for root in args.workspace_root:
        candidate = root.expanduser()
        if candidate.is_dir():
            return candidate
    return Path.home()


def _picker_title(kind: str) -> str:
    if kind == "uproject":
        return "Select Unreal project (.uproject) to index"
    if kind == "engine":
        return "Select Unreal Engine root folder"
    return "Select folder to scan for Unreal projects"


def _applescript_quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _pick_with_osascript(kind: str, initial_directory: Path) -> str | None:
    """Use macOS choose file/folder so the dialog activates from Terminal/iTerm."""
    start = initial_directory if initial_directory.is_dir() else Path.home()
    prompt = _applescript_quote(_picker_title(kind))
    default = _applescript_quote(str(start))
    if kind == "uproject":
        script = (
            "try\n"
            f'  set theFile to choose file with prompt "{prompt}" '
            f'default location (POSIX file "{default}")\n'
            "  return POSIX path of theFile\n"
            "on error number -128\n"
            '  return ""\n'
            "end try"
        )
    else:
        script = (
            "try\n"
            f'  set theFolder to choose folder with prompt "{prompt}" '
            f'default location (POSIX file "{default}")\n'
            "  return POSIX path of theFolder\n"
            "on error number -128\n"
            '  return ""\n'
            "end try"
        )
    completed = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "osascript picker failed").strip()
        raise RuntimeError(detail)
    selected = completed.stdout.strip()
    return selected or None


def _pick_with_tkinter(kind: str, initial_directory: Path) -> str | None:
    """Fallback picker for Windows/Linux, and macOS when osascript is unavailable."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass
    try:
        root.lift()
        root.focus_force()
        root.update()
    except tk.TclError:
        pass
    title = _picker_title(kind)
    initial = str(initial_directory if initial_directory.is_dir() else Path.home())
    try:
        if kind == "uproject":
            return filedialog.askopenfilename(
                parent=root,
                title=title,
                initialdir=initial,
                filetypes=(("Unreal Project", "*.uproject"),),
            ) or None
        return filedialog.askdirectory(
            parent=root,
            title=title,
            initialdir=initial,
            mustexist=True,
        ) or None
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def _normalize_picked_path(kind: str, selected: str | None) -> Path | None:
    if not selected:
        return None
    path = Path(selected).expanduser().resolve()
    if kind == "uproject" and (not path.is_file() or path.suffix.lower() != ".uproject"):
        print(f"  Ignoring invalid Unreal project selection: {path}")
        return None
    if kind in {"folder", "engine"} and not path.is_dir():
        print(f"  Ignoring invalid folder selection: {path}")
        return None
    return path


def _pick_indexing_target(kind: str, initial_directory: Path) -> Path | None:
    """Open a native file/folder picker without asking for a typed path."""
    print(f"  Opening picker: {_picker_title(kind)}")
    errors: list[str] = []

    # Terminal-launched Python on macOS often fails to surface tkinter dialogs.
    # Prefer AppleScript choose file/folder so Finder presents the panel.
    if sys.platform == "darwin":
        try:
            return _normalize_picked_path(kind, _pick_with_osascript(kind, initial_directory))
        except Exception as exc:
            errors.append(f"osascript: {exc}")

    try:
        return _normalize_picked_path(kind, _pick_with_tkinter(kind, initial_directory))
    except Exception as exc:
        errors.append(f"tkinter: {exc}")
        detail = "; ".join(errors) if errors else str(exc)
        print(f"  Project picker unavailable: {detail}")
        return None


def _interactive_project_indexing(args: argparse.Namespace) -> None:
    print("\nProject indexing setup:")
    if not _prompt_yes_no("Select .uproject files or folders to index?", True):
        print("  Using configured/default project search roots.")
        return

    initial_directory = _project_picker_initial_directory(args)
    replaced_default_roots = False
    while True:
        print("  1. Select .uproject file (sets the active project)")
        print("  2. Select folder (adds a project search root)")
        choice = input("Select [1]: ").strip() or "1"
        kind = "folder" if choice == "2" else "uproject"
        selected = _pick_indexing_target(kind, initial_directory)
        if selected is None:
            print("  Selection cancelled.")
        else:
            search_root = selected if kind == "folder" else selected.parent
            if getattr(args, "_workspace_root_defaulted", False) and not replaced_default_roots:
                args.workspace_root = []
                replaced_default_roots = True
            if search_root not in args.workspace_root:
                args.workspace_root.append(search_root)
            if kind == "uproject":
                args.active_project = selected
            initial_directory = search_root
            print(f"  Added: {selected}")
        if not _prompt_yes_no("Add another project or folder?", False):
            break


def _engine_picker_initial_directory() -> Path:
    for candidate in _common_engine_locations():
        candidate = candidate.expanduser()
        if candidate.is_dir():
            return candidate
    return Path.home()


def _interactive_engine_selection(args: argparse.Namespace) -> None:
    if args.engine_root:
        args._engine_selection = "explicit"
        return

    explicit = os.environ.get("UNREAL_ENGINE_ROOT", "").strip()
    if explicit:
        configured = Path(explicit).expanduser()
        if not _engine_root_is_valid(configured):
            raise ValueError(
                f"UNREAL_ENGINE_ROOT does not contain a usable Unreal Engine layout: {configured}"
            )
        args.engine_root = configured
        args._engine_selection = "explicit"
        print(f"  Using UNREAL_ENGINE_ROOT: {configured}")
        return

    print("\nUnreal Engine setup:")
    print("  1. Epic Games Launcher engine (auto-detect)")
    print("  2. Custom/source engine folder (select folder)")
    choice = input("Select [1]: ").strip() or "1"
    if choice != "2":
        args._engine_selection = "launcher"
        print("  Using Epic Games Launcher auto-detection.")
        return

    selected = _pick_indexing_target("engine", _engine_picker_initial_directory())
    if selected is None:
        raise RuntimeError("custom Unreal Engine folder selection cancelled")
    if not _engine_root_is_valid(selected):
        raise ValueError(
            f"selected folder does not contain a usable Unreal Engine layout: {selected}"
        )
    args.engine_root = selected
    args._engine_selection = "custom"
    print(f"  Engine root: {selected}")


def _json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    size = path.stat().st_size
    if size > MAX_INSTALLER_JSON_BYTES:
        raise ValueError(
            f"JSON file exceeds the {MAX_INSTALLER_JSON_BYTES}-byte installer safety limit: {path}"
        )
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _default_platform() -> str:
    system = platform.system().lower()
    if system == "windows":
        return "Win64"
    if system == "darwin":
        return "Mac"
    if system == "linux":
        return "Linux"
    raise RuntimeError(f"unsupported host platform: {platform.system()}")


def _host_cpu_arch() -> str:
    """Return 'arm64' or 'x64'. On Apple Silicon prefer hardware arch even under Rosetta."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin":
        try:
            probe = subprocess.run(
                ["sysctl", "-n", "hw.optional.arm64"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if probe.returncode == 0 and probe.stdout.strip() == "1":
                return "arm64"
        except (OSError, subprocess.TimeoutExpired):
            pass
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    if machine in {"x86_64", "amd64", "x64"}:
        return "x64"
    return machine or "unknown"


def _assert_host_component_support(components: set[str]) -> None:
    """Block LM Studio stack installs on Intel macOS; allow Codex/Cline-only custom installs."""
    if platform.system().lower() != "darwin":
        return
    arch = _host_cpu_arch()
    needs_lmstudio = bool(components & LMSTUDIO_STACK_COMPONENTS)
    if arch == "x64" and needs_lmstudio:
        raise RuntimeError(
            "Intel macOS (x86_64) cannot install LM Studio-based components. "
            "LM Studio does not support Intel Mac. Remove lmstudio/unreal/context_compactor "
            "and use a custom Codex / portable_rule / Cline-only install, "
            "or run on Apple Silicon macOS / Windows / Ubuntu Linux."
        )
    if arch == "arm64" and needs_lmstudio:
        # Soft notice: physical FULL install is verified; signing/notarization is still not claimed.
        print(
            "NOTE: Apple Silicon macOS LM Studio FULL install is verified on physical hardware; "
            "installer signing/notarization is not claimed.",
            file=sys.stderr,
        )


def _process_is_alive(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _sync_directory(path: Path) -> None:
    """Best-effort rename durability on POSIX; directory fsync is unavailable on Windows."""
    if os.name == "nt":
        return
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY)
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    """Durably replace one generated file without exposing a partial manifest."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


@dataclass
class InstallLock:
    state_home: Path
    dry_run: bool = False
    lock_name: str = "install.lock"
    owner_token: str = ""
    path: Path = field(init=False)
    acquired: bool = False

    def __post_init__(self) -> None:
        if Path(self.lock_name).name != self.lock_name:
            raise ValueError(f"invalid installer lock name: {self.lock_name}")
        self.path = self.state_home / self.lock_name

    def _clear_stale_lock(self) -> bool:
        try:
            stat_result = self.path.stat()
            payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return True
        except (OSError, json.JSONDecodeError, UnicodeError):
            payload = {}
            try:
                stat_result = self.path.stat()
            except FileNotFoundError:
                return True
            except OSError:
                return False
        pid = payload.get("pid") if isinstance(payload, dict) else None
        if isinstance(pid, int) and _process_is_alive(pid):
            return False
        if not isinstance(pid, int) and time.time() - stat_result.st_mtime < INVALID_LOCK_GRACE_SECONDS:
            # Another process may have created the exclusive lock but not yet
            # flushed its JSON owner record. Never steal that fresh lock window.
            return False
        try:
            self.path.unlink(missing_ok=True)
            _sync_directory(self.path.parent)
            return True
        except OSError:
            return False

    def acquire(self) -> None:
        if self.dry_run:
            return
        self.state_home.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError as exc:
                try:
                    payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
                except (OSError, json.JSONDecodeError, UnicodeError):
                    payload = {}
                if (
                    self.owner_token
                    and isinstance(payload, dict)
                    and payload.get("ownerToken") == self.owner_token
                ):
                    self.acquired = True
                    return
                if self._clear_stale_lock():
                    continue
                raise RuntimeError(
                    f"another installer is active (or a stale lock remains): {self.path}"
                ) from exc
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                payload = {"pid": os.getpid(), "createdAt": time.time()}
                if self.owner_token:
                    payload["ownerToken"] = self.owner_token
                json.dump(payload, handle)
                handle.flush()
                os.fsync(handle.fileno())
            _sync_directory(self.path.parent)
            self.acquired = True
            return
        raise RuntimeError(
            f"another installer is active (or a stale lock remains): {self.path}"
        )

    def release(self) -> None:
        if self.acquired:
            try:
                try:
                    payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
                except (OSError, json.JSONDecodeError, UnicodeError):
                    payload = {}
                owns_lock = (
                    isinstance(payload, dict)
                    and (
                        (self.owner_token and payload.get("ownerToken") == self.owner_token)
                        or (not self.owner_token and payload.get("pid") == os.getpid())
                    )
                )
                if owns_lock:
                    self.path.unlink(missing_ok=True)
                    _sync_directory(self.path.parent)
            finally:
                self.acquired = False


@dataclass
class Transaction:
    state_home: Path
    allowed_roots: list[Path]
    dry_run: bool = False
    actions: list[dict[str, Any]] = field(default_factory=list)
    backup_root: Path = field(init=False)

    def __post_init__(self) -> None:
        stamp = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        self.backup_root = self.state_home / "backups" / stamp

    def _assert_allowed(self, target: Path) -> Path:
        resolved = target.expanduser().resolve()
        if not any(_is_within(resolved, root) or resolved == root.resolve() for root in self.allowed_roots):
            raise ValueError(f"refusing to write outside approved roots: {resolved}")
        return resolved

    def _backup(self, target: Path) -> tuple[bool, Path | None]:
        existed = target.exists()
        if not existed:
            return False, None
        backup = self.backup_root / f"{len(self.actions):03d}-{target.name}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        if target.is_dir():
            shutil.copytree(target, backup)
        else:
            shutil.copy2(target, backup)
        return True, backup

    def write_file(self, target: Path, content: bytes) -> None:
        target = self._assert_allowed(target)
        if self.dry_run:
            print(f"[dry-run] write file: {target}")
            return
        existed, backup = self._backup(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            _sync_directory(target.parent)
        finally:
            if temporary.exists():
                temporary.unlink()
        self.actions.append(
            {"kind": "file", "target": str(target), "existed": existed, "backup": str(backup or "")}
        )

    def replace_directory(self, source: Path, target: Path) -> None:
        target = self._assert_allowed(target)
        source = source.resolve()
        if source == target or source in target.parents:
            raise ValueError(f"destination must not equal or be nested under source: {target}")
        if self.dry_run:
            print(f"[dry-run] replace directory: {target} <- {source}")
            return
        existed, backup = self._backup(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging_parent = Path(tempfile.mkdtemp(prefix=f".{target.name}-staging-", dir=target.parent))
        staging = staging_parent / target.name
        old = target.parent / f".{target.name}-old-{uuid.uuid4().hex}"
        moved_old = False
        installed_new = False
        try:
            shutil.copytree(
                source,
                staging,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
            )
            if target.exists():
                target.replace(old)
                moved_old = True
            staging.replace(target)
            installed_new = True
            if old.exists():
                shutil.rmtree(old) if old.is_dir() else old.unlink()
            _sync_directory(target.parent)
        except Exception:
            if moved_old and old.exists():
                if installed_new and target.exists():
                    shutil.rmtree(target) if target.is_dir() else target.unlink()
                old.replace(target)
            raise
        finally:
            if staging_parent.exists():
                shutil.rmtree(staging_parent)
        self.actions.append(
            {"kind": "dir", "target": str(target), "existed": existed, "backup": str(backup or "")}
        )

    def rollback_actions(self) -> None:
        if self.dry_run:
            return
        for action in reversed(self.actions):
            target = self._assert_allowed(Path(action["target"]))
            backup = Path(action["backup"]) if action["existed"] else None
            if backup is not None and not backup.exists():
                raise RuntimeError(
                    f"cannot roll back {target}: managed backup is missing: {backup}"
                )
            if target.exists():
                shutil.rmtree(target) if target.is_dir() else target.unlink()
            if action["existed"]:
                assert backup is not None
                if backup.is_dir():
                    shutil.copytree(backup, target)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup, target)
            _sync_directory(target.parent)

    def commit(self, metadata: dict[str, Any]) -> Path | None:
        if self.dry_run:
            return None
        self.state_home.mkdir(parents=True, exist_ok=True)
        journal = {
            "schemaVersion": 1,
            "installedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "allowedRoots": [str(root.resolve()) for root in self.allowed_roots],
            "backupRoot": str(self.backup_root),
            "actions": self.actions,
            **metadata,
        }
        journal_path = self.state_home / "install-journal.json"
        fd, temporary_name = tempfile.mkstemp(prefix=".install-journal.", dir=self.state_home)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(_json_bytes(journal))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, journal_path)
            _sync_directory(journal_path.parent)
        finally:
            if temporary.exists():
                temporary.unlink()
        return journal_path


def rollback_last_install(state_home: Path, *, dry_run: bool = False) -> dict[str, Any]:
    _reject_filesystem_root(state_home, "state home")
    journal_path = state_home / "install-journal.json"
    journal = _load_json(journal_path, None)
    if not isinstance(journal, dict):
        raise FileNotFoundError(f"install journal not found: {journal_path}")
    allowed = [Path(value).resolve() for value in journal.get("allowedRoots") or []]
    for root in allowed:
        _reject_filesystem_root(root, "journal allowed root")
    backup_root = Path(str(journal.get("backupRoot") or "")).resolve()
    if not _is_within(backup_root, state_home) or backup_root == state_home.resolve():
        raise ValueError(f"journal backup root escaped state home: {backup_root}")
    prepared: list[tuple[dict[str, Any], Path, Path | None]] = []
    for action in reversed(journal.get("actions") or []):
        if not isinstance(action, dict) or "target" not in action:
            raise ValueError("install journal contains an invalid action")
        target = Path(action["target"]).resolve()
        _reject_filesystem_root(target, "journal target")
        if not any(_is_within(target, root) or target == root for root in allowed):
            raise ValueError(f"journal target escaped approved roots: {target}")
        backup = None
        if action.get("existed"):
            backup = Path(str(action.get("backup") or "")).resolve()
            if not _is_within(backup, backup_root) or not backup.exists():
                raise RuntimeError(
                    f"cannot roll back {target}: managed backup is missing or outside backup root: {backup}"
                )
        prepared.append((action, target, backup))
    restored = 0
    for action, target, backup in prepared:
        print(f"{'[dry-run] ' if dry_run else ''}rollback: {target}")
        if dry_run:
            continue
        if target.exists():
            shutil.rmtree(target) if target.is_dir() else target.unlink()
        if action.get("existed"):
            assert backup is not None
            if backup.is_dir():
                shutil.copytree(backup, target)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, target)
        _sync_directory(target.parent)
        restored += 1
    if not dry_run:
        journal_path.unlink()
        _sync_directory(journal_path.parent)
    return {"ok": True, "restored": restored, "journal": str(journal_path)}


def _prompt_yes_no(question: str, default: bool) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    answer = input(f"{question} {suffix} ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes", "1", "true"}


def _interactive_profile() -> str:
    print("Install profile:")
    print("  1. SAFE (recommended: portable Codex + LM Studio + required context compactor)")
    print("  2. STANDARD (SAFE + read-only Unreal adapter; context compactor required)")
    print("  3. FULL (same required components as STANDARD; kept for compatibility)")
    print("  4. CUSTOM")
    choice = input("Select [1]: ").strip() or "1"
    return {"1": "safe", "2": "standard", "3": "full", "4": "custom"}.get(choice, "safe")


def _interactive_agent_authority() -> bool:
    print("\nUnreal adapter authority:")
    print("  1. SAFE (recommended: analysis only; no writes, commands, or builds)")
    print("  2. AGENT (allows project writes, commands, and Unreal builds)")
    choice = input("Select [1]: ").strip() or "1"
    return choice == "2"


def _interactive_rag_indexing(args: argparse.Namespace) -> None:
    """Let interactive users opt into an index build independently of the install profile."""
    if args.build_rag:
        print(f"\nRAG indexing: build ({args.index_tier}, selected by command-line option)")
        return

    print("\nRAG indexing (independent of install profile):")
    print("  1. SKIP (default: configure the adapter only)")
    print("  2. LITE (project text + asset paths; fastest)")
    print("  3. STANDARD (recommended: project/engine symbols + module graph)")
    print("  4. FULL (STANDARD + complete Engine\\Source text; large and slow)")
    choice = input("Select [1]: ").strip() or "1"
    selected = {"2": "lite", "3": "standard", "4": "full"}.get(choice)
    if selected:
        args.build_rag = True
        args.index_tier = selected


def _confirm_interactive_install(
    profile: str, components: set[str], args: argparse.Namespace
) -> None:
    authority = "AGENT (writes / commands / builds enabled)" if args.enable_agent_mode else "SAFE (read-only)"
    print("\nInstall summary:")
    print(f"  Profile    : {profile.upper()}")
    print(f"  Components : {', '.join(sorted(components)) or 'none'}")
    print(f"  Authority  : {authority}")
    if args.build_rag:
        print(f"  RAG index  : build ({args.index_tier})")
    else:
        print("  RAG index  : do not build")
    if "unreal" in components:
        print(f"  Search roots: {', '.join(str(path) for path in args.workspace_root)}")
        if args.active_project:
            print(f"  Active project: {args.active_project}")
        if args.engine_root:
            print(f"  Engine root: {args.engine_root}")
    if not _prompt_yes_no("Continue with this installation?", True):
        raise RuntimeError("installation cancelled by user")


def _resolve_components(args: argparse.Namespace) -> tuple[str, set[str]]:
    interactive = not args.yes and sys.stdin.isatty()
    if not args.workspace_root:
        args.workspace_root = [Path.home() / "Documents"]
        args._workspace_root_defaulted = True
    else:
        args._workspace_root_defaulted = False
    profile = args.profile or (_interactive_profile() if interactive else "safe")
    if profile == "custom":
        components = {
            item.strip() for item in str(args.components or "").split(",") if item.strip()
        }
        if interactive and not components:
            for component in sorted(ALL_COMPONENTS):
                if _prompt_yes_no(
                    f"Install {component}?",
                    component in {"codex", "lmstudio", "context_compactor"},
                ):
                    components.add(component)
    else:
        components = set(PROFILE_DEFAULTS[profile])

    if interactive:
        print(
            "\nLM Studio context compactor is required and will be installed/activated "
            "whenever LM Studio or Unreal components are selected."
        )
        if _prompt_yes_no("Install a rule into another coding agent?", False):
            components.add("portable_rule")
            if not args.rule_path:
                args.rule_path = [_default_portable_rule_path(args)]
                print(f"  Portable rule path: {args.rule_path[0]}")
        if _prompt_yes_no("Patch Cline MCP settings at its default location?", False):
            components.add("cline")
            if not args.cline_settings:
                args.cline_settings = _default_cline_settings_path()
                print(f"  Cline MCP settings: {args.cline_settings}")
        if "unreal" in components:
            _interactive_project_indexing(args)
            _interactive_engine_selection(args)
            _interactive_rag_indexing(args)
        if "unreal" in components and not args.enable_agent_mode:
            requested_agent_mode = _interactive_agent_authority()
            if requested_agent_mode:
                accepted = _prompt_yes_no(
                    "Enable AGENT authority for this trusted project?", False
                )
                args.enable_agent_mode = accepted
                args.accept_agent_risk = accepted
                if not accepted:
                    print("AGENT authority was not confirmed; continuing in SAFE read-only mode.")

    if args.no_codex:
        components.discard("codex")
    if args.no_lmstudio:
        components.discard("lmstudio")
    if args.no_unreal:
        components.discard("unreal")
    _enforce_required_context_compactor(components, args)
    if args.rule_path:
        components.add("portable_rule")
    elif "portable_rule" in components:
        args.rule_path = [_default_portable_rule_path(args)]
    if args.cline_settings:
        components.add("cline")
    elif "cline" in components:
        args.cline_settings = _default_cline_settings_path()
    unknown = components - ALL_COMPONENTS
    if unknown:
        raise ValueError(f"unknown components: {sorted(unknown)}")
    if profile == "safe" and args.enable_agent_mode:
        raise ValueError("SAFE profile cannot enable agent mode")
    if args.enable_agent_mode and "unreal" not in components:
        raise ValueError("--enable-agent-mode requires the unreal component")
    if args.build_rag and "unreal" not in components:
        raise ValueError("--build-rag requires the unreal component")
    if args.enable_agent_mode and not args.accept_agent_risk:
        raise ValueError("agent mode requires explicit --accept-agent-risk")
    _assert_host_component_support(components)
    if interactive:
        _confirm_interactive_install(profile, components, args)
    return profile, components


def _enforce_required_context_compactor(components: set[str], args: argparse.Namespace) -> None:
    """Force context_compactor whenever LM Studio integration is present."""
    needs_compactor = "lmstudio" in components or "unreal" in components
    if not needs_compactor:
        components.discard("context_compactor")
        return
    allow_skip = bool(getattr(args, "allow_skip_context_compactor", False))
    if args.skip_context_compactor and not allow_skip:
        raise ValueError(
            "Context compactor is required for LM Studio installs. "
            "--skip-context-compactor is blocked unless you also pass "
            "--allow-skip-context-compactor (unsupported emergency bypass)."
        )
    if args.skip_context_compactor and allow_skip:
        components.discard("context_compactor")
        print(
            "WARNING: skipping required LM Studio context compactor "
            "(unsupported emergency bypass)."
        )
        return
    components.add("context_compactor")


def _merge_mcp_entry(config: dict[str, Any], name: str, entry: dict[str, Any]) -> None:
    servers = config.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError("mcpServers must be a JSON object")
    servers[name] = entry


def _mcp_entry_for_frontend(entry: dict[str, Any], frontend: str) -> dict[str, Any]:
    """Clone an MCP entry without leaking LM Studio-only routing policy."""
    normalized_frontend = str(frontend or "unknown").strip().lower() or "unknown"
    cloned = copy.deepcopy(entry)
    env = dict(cloned.get("env") or {})
    env["MCP_FRONTEND"] = normalized_frontend
    if normalized_frontend != "lmstudio":
        for key in LMSTUDIO_CONTEXT_POLICY_ENV:
            env.pop(key, None)
    cloned["env"] = env
    return cloned


def _evidence_mcp_entry(python_exe: Path, installed_skill: Path) -> dict[str, Any]:
    return {
        "command": str(python_exe),
        "args": [str(installed_skill / "scripts" / "evidence_first_mcp.py")],
        "timeout": 120000,
        "env": {
            "EVIDENCE_FIRST_SAFE_MODE": "1",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        },
    }


def _unreal_entries(
    args: argparse.Namespace,
    python_exe: Path,
    node_exe: Path,
    shared_config: Path,
    agent_config: Path,
    context_compactor_advisory: bool = False,
    runtime_git_commit: str = "",
    engine_association: str = "",
) -> dict[str, dict[str, Any]]:
    allow = "1" if args.enable_agent_mode else "0"
    state_root = args.lmstudio_home / "state" / "unreal-agent"
    runtime_manifest = args.lmstudio_home / "config" / "control-runtime.json"
    rag_entry = {
        "command": str(python_exe),
        # Do not pin an engine namespace here. unreal_rag_mcp resolves the
        # workspace/shared index configuration unless a user explicitly starts
        # it with --index.
        "args": [str(ROOT / "scripts" / "unreal_rag_mcp.py")],
        "timeout": 420000,
        "env": {
            "SHARED_UNREAL_CONFIG": str(shared_config),
            "AGENT_STATE_ROOT": str(state_root),
            "UNREAL58_ROOT": str(ROOT),
            "MCP_FRONTEND": "lmstudio",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "MCP_ESSENTIAL_TOOLS": "1",
            "CONTROL_RUNTIME_MANIFEST": str(runtime_manifest),
            "CONTROL_RUNTIME_COMPONENT": "rag",
            "CONTROL_RUNTIME_REQUIRED": "1",
        },
    }
    agent_entry = {
        "command": str(node_exe),
        "args": [str(ROOT / "lmstudio-unreal-agent-mcp" / "src" / "server.js")],
        "timeout": 720000,
        "env": {
            "WORKSPACE_ROOT": str(args.workspace_root[0]),
            "AGENT_MCP_CONFIG": str(agent_config),
            "SHARED_UNREAL_CONFIG": str(shared_config),
            "AGENT_STATE_ROOT": str(state_root),
            "UNREAL58_ROOT": str(ROOT),
            "MCP_FRONTEND": "lmstudio",
            "PYTHON_EXE": str(python_exe),
            "ALLOW_WRITE": allow,
            "ALLOW_COMMANDS": allow,
            "ALLOW_UNREAL_BUILD": allow,
            "MAX_READ_BYTES": "524288",
            "MAX_OUTPUT_BYTES": "262144",
            "COMMAND_TIMEOUT_MS": "600000",
            "MCP_ESSENTIAL_TOOLS": "1",
            "MCP_REQUIRE_PLAN_AUTH": "1",
            "VALIDATE_ON_WRITE": allow,
            "CONTROL_RUNTIME_MANIFEST": str(runtime_manifest),
            "CONTROL_RUNTIME_COMPONENT": "agent",
            "CONTROL_RUNTIME_REQUIRED": "1",
        },
    }
    # Installed MCP components may not retain the repository's .git directory.
    # Carry the manifest commit into both processes so runtime-identity compares
    # the same immutable release identity instead of silently losing that check.
    if str(runtime_git_commit).strip():
        rag_entry["env"]["CONTROL_RUNTIME_GIT_COMMIT"] = str(runtime_git_commit).strip()
        agent_entry["env"]["CONTROL_RUNTIME_GIT_COMMIT"] = str(runtime_git_commit).strip()
        rag_entry["env"]["CONTROL_RUNTIME_EXPECTED_GIT_COMMIT"] = str(runtime_git_commit).strip()
        agent_entry["env"]["CONTROL_RUNTIME_EXPECTED_GIT_COMMIT"] = str(runtime_git_commit).strip()
    if context_compactor_advisory:
        # Compactor is required for LM Studio installs. Advisory telemetry stays on;
        # strict write-blocking remains opt-in via MCP_REQUIRE_CONTEXT_COMPACTOR_ACTIVE=1.
        rag_entry["env"]["MCP_CONTEXT_COMPACTOR_ADVISORY"] = "1"
        rag_entry["env"]["MCP_REQUIRE_CONTEXT_COMPACTOR_ACTIVE"] = "0"
        rag_entry["env"]["MCP_CONTEXT_COMPACTOR_REQUIRED_FRONTENDS"] = "lmstudio"
        rag_entry["env"]["MCP_CONTEXT_COMPACTOR_MAX_AGE_SECONDS"] = "300"
    if args.engine_root:
        rag_entry["env"]["UNREAL_ENGINE_ROOT"] = str(args.engine_root)
        agent_entry["env"]["UNREAL_ENGINE_ROOT"] = str(args.engine_root)
        # Distinguish this installer-managed default from a user's intentional
        # shell override. An empty value means it was installed without an
        # active project binding and must not retarget a later bound project.
        pin_association = str(engine_association or "").strip()
        rag_entry["env"]["UNREAL_ENGINE_ROOT_ASSOCIATION"] = pin_association
        agent_entry["env"]["UNREAL_ENGINE_ROOT_ASSOCIATION"] = pin_association
    return {"unreal-rag": rag_entry, "unreal-agent": agent_entry}


def _display_command(command: list[str]) -> str:
    return subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)


def _native_subprocess_command(command: list[str]) -> list[str] | str:
    if os.name == "nt" and Path(command[0]).suffix.lower() in {".cmd", ".bat"}:
        # Do not wrap through cmd.exe argv list: Python's Windows CreateProcess
        # requotes the /C payload and breaks "Program Files" .cmd paths.
        # shell=True with a single command line preserves npm.cmd resolution.
        return subprocess.list2cmdline(command)
    return command


def _run(
    command: list[str],
    *,
    cwd: Path,
    dry_run: bool,
    timeout: float | None = None,
) -> None:
    # Keep progress on stderr so installer stdout remains machine-parseable JSON.
    print(
        ("[dry-run] " if dry_run else "") + "run: " + _display_command(command),
        file=sys.stderr,
    )
    if dry_run:
        return
    native = _native_subprocess_command(command)
    try:
        subprocess.run(
            native,
            cwd=str(cwd),
            check=True,
            timeout=timeout,
            shell=isinstance(native, str),
            stdout=sys.stderr,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"command timed out after {timeout:g}s: {_display_command(command)}"
        ) from exc


def _powershell_file_command(
    executable: str,
    script: Path,
    script_args: list[str],
) -> list[str]:
    command = [executable, "-NoProfile"]
    if platform.system() == "Windows":
        command.extend(["-ExecutionPolicy", "Bypass"])
    command.extend(["-File", str(script), *script_args])
    return command


def _default_lmstudio_home() -> Path:
    return Path(os.environ.get("LMSTUDIO_HOME", Path.home() / ".lmstudio")).expanduser()


def _context_compactor_install_path(lmstudio_home: Path) -> Path:
    return (
        lmstudio_home
        / "extensions"
        / "plugins"
        / "codex"
        / CONTEXT_COMPACTOR_PLUGIN_NAME
        / "manifest.json"
    )


def _agent_debug_log(
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict[str, Any],
) -> None:
    # #region agent log
    if os.environ.get("LMS_CONTEXT_COMPACTOR_DEBUG_INGEST") != "1":
        return
    try:
        payload = {
            "sessionId": "49b048",
            "runId": "compactor-force",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        log_path = Path(os.environ.get("LMS_CONTEXT_COMPACTOR_DEBUG_LOG") or (ROOT / "debug-49b048.log"))
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # #endregion


def _resolve_lms_cli(lmstudio_home: Path) -> str | None:
    """Locate the LM Studio `lms` CLI on Windows, macOS, and Linux.

    Prefer the CLI next to the target LM Studio home so managed installs do not
    accidentally use a different host-wide `lms` that writes into another home.
    """
    env_override = str(os.environ.get("LMSTUDIO_CLI") or "").strip()
    if env_override and Path(env_override).exists():
        # #region agent log
        _agent_debug_log(
            "H3",
            "install.py:_resolve_lms_cli",
            "resolved lms via LMSTUDIO_CLI",
            {"lms": env_override, "home": str(lmstudio_home)},
        )
        # #endregion
        return env_override
    home = lmstudio_home.expanduser()
    candidates: list[Path] = []
    if os.name == "nt":
        candidates.extend(
            [
                home / "bin" / "lms.exe",
                home / "bin" / "lms.cmd",
                home / "bin" / "lms",
                Path(os.environ.get("LOCALAPPDATA", "")) / "LM Studio" / "lms.exe",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "LM Studio" / "lms.exe",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "LM Studio" / "resources" / "app" / "lms.exe",
            ]
        )
    else:
        candidates.append(home / "bin" / "lms")
    if sys.platform == "darwin":
        app_roots = [
            Path("/Applications/LM Studio.app"),
            Path.home() / "Applications" / "LM Studio.app",
        ]
        for app in app_roots:
            candidates.extend(
                [
                    app / "Contents" / "Resources" / "app" / "lms",
                    app / "Contents" / "Resources" / "app" / "bin" / "lms",
                    app / "Contents" / "MacOS" / "lms",
                    app / "Contents" / "Resources" / "lms",
                    app / "Contents" / "Resources" / "bin" / "lms",
                ]
            )
    if sys.platform.startswith("linux"):
        candidates.extend(
            [
                Path.home() / ".local" / "bin" / "lms",
                Path("/usr/local/bin/lms"),
                Path("/usr/bin/lms"),
            ]
        )
    for candidate in candidates:
        if candidate.is_file():
            resolved = str(candidate.resolve())
            # #region agent log
            _agent_debug_log(
                "H3",
                "install.py:_resolve_lms_cli",
                "resolved lms via home/app candidate",
                {"lms": resolved, "home": str(home)},
            )
            # #endregion
            return resolved
    found = shutil.which("lms")
    if found:
        # #region agent log
        _agent_debug_log(
            "H3",
            "install.py:_resolve_lms_cli",
            "resolved lms via PATH fallback",
            {"lms": found, "home": str(home)},
        )
        # #endregion
        return found
    return None


def _copy_context_compactor_tree(source_dir: Path, destination_dir: Path) -> None:
    """Copy a plugin tree into the managed LM Studio extensions path."""
    destination_dir.parent.mkdir(parents=True, exist_ok=True)
    if destination_dir.exists():
        shutil.rmtree(destination_dir)
    shutil.copytree(
        source_dir,
        destination_dir,
        ignore=shutil.ignore_patterns(
            ".git",
            ".pytest_cache",
            "__pycache__",
            "*.pyc",
            ".DS_Store",
        ),
    )
    _sync_directory(destination_dir)


def _ensure_context_compactor_on_disk(
    *,
    plugin_src: Path,
    lmstudio_home: Path,
) -> dict[str, Any]:
    """Guarantee the plugin exists under the managed LM Studio home.

    `lms` always installs into the host default LM Studio home. When the
    installer targets a different home (tests, custom LMSTUDIO_HOME), sync from
    the default install or materialize from the repository source.
    """
    target_manifest = _context_compactor_install_path(lmstudio_home)
    target_dir = target_manifest.parent
    detail: dict[str, Any] = {
        "target": str(target_manifest),
        "source": None,
        "copied": False,
    }
    if target_manifest.is_file():
        detail["source"] = "already-present"
        return detail

    default_home = _default_lmstudio_home().resolve()
    managed_home = lmstudio_home.expanduser().resolve()
    default_manifest = _context_compactor_install_path(default_home)
    if managed_home != default_home and default_manifest.is_file():
        _copy_context_compactor_tree(default_manifest.parent, target_dir)
        detail["source"] = "default-lmstudio-home"
        detail["copied"] = True
        # #region agent log
        _agent_debug_log(
            "H3",
            "install.py:_ensure_context_compactor_on_disk",
            "synced plugin from default LM Studio home",
            {"from": str(default_manifest.parent), "to": str(target_dir)},
        )
        # #endregion
        return detail

    if (plugin_src / "manifest.json").is_file():
        _copy_context_compactor_tree(plugin_src, target_dir)
        detail["source"] = "repository-source"
        detail["copied"] = True
        # #region agent log
        _agent_debug_log(
            "H3",
            "install.py:_ensure_context_compactor_on_disk",
            "materialized plugin from repository source",
            {"from": str(plugin_src), "to": str(target_dir)},
        )
        # #endregion
        return detail

    detail["source"] = "missing"
    return detail


def _activate_context_compactor_in_settings(
    lmstudio_home: Path,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    """Pin the installed plugin and enable development plugins in LM Studio settings."""
    settings_path = lmstudio_home / "settings.json"
    result: dict[str, Any] = {
        "settingsPath": str(settings_path),
        "pinned": False,
        "allowDevelopmentPlugins": False,
        "changed": False,
    }
    if dry_run:
        result["dryRun"] = True
        return result
    settings = _load_json(settings_path, {}) if settings_path.exists() else {}
    if not isinstance(settings, dict):
        settings = {}
    chat = settings.get("chat")
    if not isinstance(chat, dict):
        chat = {}
        settings["chat"] = chat
    pinned = chat.get("pinnedPlugins")
    if not isinstance(pinned, list):
        pinned = []
    if CONTEXT_COMPACTOR_PLUGIN_ID not in pinned:
        pinned = [*pinned, CONTEXT_COMPACTOR_PLUGIN_ID]
        chat["pinnedPlugins"] = pinned
        result["changed"] = True
    result["pinned"] = CONTEXT_COMPACTOR_PLUGIN_ID in pinned
    developer = settings.get("developer")
    if not isinstance(developer, dict):
        developer = {}
        settings["developer"] = developer
    if developer.get("allowDevelopmentPlugins") is not True:
        developer["allowDevelopmentPlugins"] = True
        result["changed"] = True
    result["allowDevelopmentPlugins"] = bool(developer.get("allowDevelopmentPlugins"))
    if result["changed"]:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _sync_directory(settings_path.parent)
    return result


def _install_context_compactor(
    args: argparse.Namespace,
    external_actions_started: list[str] | None = None,
) -> dict[str, Any]:
    plugin = ROOT / "lmstudio-context-compactor-plugin"
    if not plugin.is_dir():
        raise FileNotFoundError(f"context compactor source missing: {plugin}")
    npm = str(getattr(args, "runtime_npm", None) or "") or shutil.which("npm")
    lms = _resolve_lms_cli(args.lmstudio_home)
    if not npm:
        raise FileNotFoundError(
            "context compactor requires npm on this host. "
            "Re-run without --skip-runtime-bootstrap so Node.js/npm can be bootstrapped."
        )
    if not lms:
        if args.dry_run:
            lms = "lms"
        else:
            raise FileNotFoundError(
                "context compactor requires the LM Studio lms CLI on this host "
                f"(os={platform.system()}). Install/start LM Studio once so `lms` exists "
                f"under {args.lmstudio_home / 'bin'}, or set LMSTUDIO_CLI to the lms binary, then retry."
            )
    if not args.dry_run:
        try:
            subprocess.run(
                [lms, "--version"],
                capture_output=True,
                text=True,
                check=True,
                timeout=15,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"LM Studio lms CLI is not executable: {lms}") from exc
    if not args.skip_deps:
        if not args.dry_run and external_actions_started is not None:
            external_actions_started.append("context-compactor-npm-dependencies")
        _run(
            [npm, "ci", "--no-audit", "--no-fund"],
            cwd=plugin,
            dry_run=args.dry_run,
            timeout=600,
        )
        _run([npm, "test"], cwd=plugin, dry_run=args.dry_run, timeout=600)
    if not args.dry_run and external_actions_started is not None:
        external_actions_started.append("context-compactor-plugin-install")
    _run([lms, "dev", "--install", "-y"], cwd=plugin, dry_run=args.dry_run, timeout=120)
    installed_manifest = _context_compactor_install_path(args.lmstudio_home)
    ensure_detail: dict[str, Any] = {"source": "dry-run", "copied": False}
    if not args.dry_run:
        ensure_detail = _ensure_context_compactor_on_disk(
            plugin_src=plugin,
            lmstudio_home=args.lmstudio_home,
        )
        # #region agent log
        _agent_debug_log(
            "H3",
            "install.py:_install_context_compactor",
            "post-install ensure result",
            {
                "lms": lms,
                "manifestExists": installed_manifest.is_file(),
                "ensure": ensure_detail,
                "home": str(args.lmstudio_home),
            },
        )
        # #endregion
        if not installed_manifest.is_file():
            raise RuntimeError(
                "context compactor install finished but the plugin was not found at "
                f"{installed_manifest}. Confirm LM Studio plugin install succeeded on this OS."
            )
        runtime_manifest_path = installed_manifest.parent / "control-runtime.json"
        _atomic_write_bytes(
            runtime_manifest_path,
            _json_bytes(build_runtime_manifest(ROOT, require_clean_source=True)),
        )
    activation = _activate_context_compactor_in_settings(
        args.lmstudio_home,
        dry_run=args.dry_run,
    )
    return {
        "lms": lms,
        "installedManifest": str(installed_manifest),
        "installed": True if args.dry_run else installed_manifest.is_file(),
        "ensure": ensure_detail,
        "activation": activation,
        "pluginId": CONTEXT_COMPACTOR_PLUGIN_ID,
    }


def _live_server_status(url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/models", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return {"reachable": True, "models": [row.get("id") for row in payload.get("data") or []]}
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return {"reachable": False, "error": str(exc)}


def install(
    args: argparse.Namespace,
    *,
    resolved_components: tuple[str, set[str]] | None = None,
) -> dict[str, Any]:
    profile, components = resolved_components or _resolve_components(args)
    # Real installs re-exec under Python 3.12 in main(); unit tests may call install()
    # directly on older interpreters with --skip-runtime-bootstrap.
    python_exe = Path(getattr(args, "runtime_python", None) or sys.executable).resolve()
    if not (SKILL_SOURCE / "SKILL.md").is_file():
        raise FileNotFoundError(f"skill source missing: {SKILL_SOURCE}")

    args.codex_home = args.codex_home.expanduser().resolve()
    args.lmstudio_home = args.lmstudio_home.expanduser().resolve()
    args.state_home = args.state_home.expanduser().resolve()
    args.workspace_root = [path.expanduser().resolve() for path in args.workspace_root]
    configured_engine = os.environ.get("UNREAL_ENGINE_ROOT", "").strip()
    if "unreal" in components and not args.engine_root and configured_engine:
        environment_engine = Path(configured_engine).expanduser().resolve()
        if not _engine_root_is_valid(environment_engine):
            raise ValueError(
                f"UNREAL_ENGINE_ROOT does not contain a usable Unreal Engine layout: {environment_engine}"
            )
        args.engine_root = environment_engine
    if args.active_project:
        args.active_project = args.active_project.expanduser().resolve()
        if not args.active_project.is_file() or args.active_project.suffix.lower() != ".uproject":
            raise ValueError(f"active project must be an existing .uproject file: {args.active_project}")
        if args.active_project.parent not in args.workspace_root:
            args.workspace_root.append(args.active_project.parent)
    if args.engine_root:
        args.engine_root = args.engine_root.expanduser().resolve()
        if not _engine_root_is_valid(args.engine_root):
            raise ValueError(f"engine root does not contain a usable Unreal Engine layout: {args.engine_root}")
    args.rule_path = [path.expanduser().resolve() for path in args.rule_path]
    if args.cline_settings:
        args.cline_settings = args.cline_settings.expanduser().resolve()

    allowed_roots = [args.codex_home, args.lmstudio_home, args.state_home, ROOT]
    allowed_roots.extend(path.parent for path in args.rule_path)
    if args.cline_settings:
        allowed_roots.append(args.cline_settings.parent)
    for root in allowed_roots:
        _reject_filesystem_root(root, "managed install root")
    tx = Transaction(args.state_home, allowed_roots, dry_run=args.dry_run)
    lock = InstallLock(args.state_home, dry_run=args.dry_run)
    installed_skill = args.codex_home / "skills" / SKILL_NAME
    report: dict[str, Any] = {
        "ok": False,
        "profile": profile,
        "components": sorted(components),
        "safeMode": not args.enable_agent_mode,
        "agentMode": args.enable_agent_mode,
        "dryRun": args.dry_run,
        "platform": platform.system(),
        "safetyNormalizations": [],
        "portableRulePaths": [],
        "clineSettingsPath": str(args.cline_settings) if args.cline_settings else None,
        "activeProject": str(args.active_project) if args.active_project else None,
        "projectSearchRoots": [str(path) for path in args.workspace_root],
        "engineRoot": str(args.engine_root) if args.engine_root else None,
    }
    external_actions_started: list[str] = []
    lock.acquire()
    try:
        if "codex" in components or "lmstudio" in components or "cline" in components:
            tx.replace_directory(SKILL_SOURCE, installed_skill)

        mcp_config: dict[str, Any] | None = None
        mcp_path = args.lmstudio_home / "mcp.json"
        if "lmstudio" in components or "unreal" in components:
            mcp_config = _load_json(mcp_path, {"mcpServers": {}})
            if not isinstance(mcp_config, dict):
                raise ValueError("LM Studio mcp.json must contain a JSON object")

        if not args.enable_agent_mode and mcp_config is not None:
            existing_servers = mcp_config.get("mcpServers")
            existing_agent = existing_servers.get("unreal-agent") if isinstance(existing_servers, dict) else None
            existing_env = existing_agent.get("env") if isinstance(existing_agent, dict) else None
            if isinstance(existing_env, dict):
                for key in ("ALLOW_WRITE", "ALLOW_COMMANDS", "ALLOW_UNREAL_BUILD", "VALIDATE_ON_WRITE"):
                    if str(existing_env.get(key, "0")).strip().lower() not in {"", "0", "false", "no", "off"}:
                        report["safetyNormalizations"].append(f"unreal-agent.env.{key}")
                    existing_env[key] = "0"

        evidence_entry = _evidence_mcp_entry(python_exe, installed_skill)
        if "lmstudio" in components:
            preset_target = args.lmstudio_home / "config-presets" / "evidence-first-code-audit.preset.json"
            tx.write_file(preset_target, PRESET_SOURCE.read_bytes())
            assert mcp_config is not None
            _merge_mcp_entry(mcp_config, "evidence-first", evidence_entry)

        if "unreal" in components:
            node = str(getattr(args, "runtime_node", None) or "") or shutil.which("node")
            if not node:
                raise FileNotFoundError("Node.js 20+ is required for the Unreal adapter")
            node_exe = Path(node).resolve()
            version = subprocess.run(
                [str(node_exe), "--version"], capture_output=True, text=True, check=True
            ).stdout.strip().lstrip("v")
            if int(version.split(".")[0]) < 20:
                raise RuntimeError(f"Node.js 20+ required, found {version}")
            agent_root = ROOT / "lmstudio-unreal-agent-mcp"
            if not (agent_root / "src" / "server.js").is_file():
                raise FileNotFoundError("Unreal agent MCP source is missing")
            if not args.skip_deps:
                npm = str(getattr(args, "runtime_npm", None) or "") or shutil.which("npm")
                if not npm:
                    raise FileNotFoundError("npm is required for the Unreal adapter")
                if not args.dry_run:
                    external_actions_started.append("unreal-agent-npm-dependencies")
                _run(
                    [npm, "ci", "--no-audit", "--no-fund"],
                    cwd=agent_root,
                    dry_run=args.dry_run,
                    timeout=600,
                )

            shared_path = args.lmstudio_home / "config" / "unreal-workspace.json"
            agent_path = args.lmstudio_home / "config" / "unreal-agent.json"
            runtime_manifest_path = args.lmstudio_home / "config" / "control-runtime.json"
            shared = _load_json(shared_path, {})
            if not isinstance(shared, dict):
                raise ValueError("unreal-workspace.json must contain a JSON object")
            if args.active_project:
                shared["activeProject"] = str(args.active_project)
                if _editor_export_path_is_default_like(shared.get("editorExportDir")):
                    shared["editorExportDir"] = str(_default_editor_export_path(args.active_project))
            else:
                shared.setdefault("activeProject", None)
            shared["projectSearchRoots"] = [str(path) for path in args.workspace_root]
            existing_engine = Path(str(shared.get("defaultEngineRoot") or "")).expanduser()
            association = ""
            if args.active_project:
                try:
                    project_data = _load_json(args.active_project, {})
                    association = str(project_data.get("EngineAssociation") or "")
                except (OSError, ValueError, json.JSONDecodeError):
                    association = ""
            detected_engine = args.engine_root
            selection = getattr(args, "_engine_selection", "")
            if association:
                # A project's EngineAssociation is an exact binding. Resolve a
                # custom/source-build value only through an explicit root or
                # exact persisted map; never let a prior default/latest engine
                # retarget a different project during installation.
                if detected_engine is not None:
                    detected_engine = detected_engine.resolve()
                    if not _engine_root_is_valid(detected_engine):
                        raise ValueError(
                            f"--engine-root does not contain a usable Unreal Engine layout: {detected_engine}"
                        )
                    if not _engine_root_matches_numeric_association(detected_engine, association):
                        raise ValueError(
                            "--engine-root does not match the active project's "
                            f"EngineAssociation {association!r}"
                        )
                elif selection != "launcher":
                    detected_engine = _configured_engine_root_for_association(shared, association)
                    if detected_engine is None and _engine_association_folder(association):
                        if _engine_root_is_valid(existing_engine) and _engine_root_matches_numeric_association(
                            existing_engine, association
                        ):
                            detected_engine = existing_engine.resolve()
                if detected_engine is None:
                    detected_engine = _detect_engine_root(association)
                if detected_engine is None:
                    raise ValueError(
                        "ENGINE_ASSOCIATION_UNRESOLVED: active project uses "
                        f"EngineAssociation {association!r}. Select its engine with --engine-root "
                        "or configure engineRootsByAssociation for this source/custom build."
                    )
                if not _engine_root_matches_numeric_association(detected_engine, association):
                    raise ValueError(
                        "Resolved engine does not match the active project's "
                        f"EngineAssociation {association!r}: {detected_engine}"
                    )
                if not _engine_association_folder(association):
                    mappings = shared.get("engineRootsByAssociation")
                    shared["engineRootsByAssociation"] = {
                        **(mappings if isinstance(mappings, dict) else {}),
                        association: str(detected_engine),
                    }
            else:
                if (
                    selection != "launcher"
                    and detected_engine is None
                    and _engine_root_is_valid(existing_engine)
                ):
                    detected_engine = existing_engine.resolve()
                if detected_engine is None:
                    detected_engine = _detect_engine_root("")
            args.engine_root = detected_engine
            shared["defaultEngineRoot"] = str(detected_engine) if detected_engine else ""
            _sync_installer_index_settings(shared, detected_engine)
            shared["defaultPlatform"] = _default_platform()
            shared.setdefault("defaultConfiguration", "Development")
            shared["indexingTier"] = args.index_tier
            tx.write_file(shared_path, _json_bytes(shared))
            report["activeProject"] = shared.get("activeProject")
            report["projectSearchRoots"] = list(shared.get("projectSearchRoots") or [])
            report["engineRoot"] = shared.get("defaultEngineRoot") or None
            agent_payload = {
                "projectSearchRoots": [str(path) for path in args.workspace_root],
                "defaultEngineRoot": str(shared.get("defaultEngineRoot") or ""),
                "defaultPlatform": _default_platform(),
                "defaultConfiguration": "Development",
                "activeProject": shared.get("activeProject"),
            }
            tx.write_file(agent_path, _json_bytes(agent_payload))
            runtime_manifest = build_runtime_manifest(ROOT, require_clean_source=True)
            tx.write_file(runtime_manifest_path, _json_bytes(runtime_manifest))
            report["controlRuntimeManifest"] = str(runtime_manifest_path)
            runtime_git_commit = str(
                runtime_manifest.get("expectedSourceGitCommit") or ""
            ).strip()
            assert mcp_config is not None
            for name, entry in _unreal_entries(
                args,
                python_exe,
                node_exe,
                shared_path,
                agent_path,
                context_compactor_advisory=("context_compactor" in components),
                runtime_git_commit=runtime_git_commit,
                engine_association=association,
            ).items():
                _merge_mcp_entry(mcp_config, name, entry)

        settings_path = args.lmstudio_home / "settings.json"
        if not args.enable_agent_mode and settings_path.exists() and ("lmstudio" in components or "unreal" in components):
            settings = _load_json(settings_path, {})
            chat = settings.get("chat") if isinstance(settings, dict) else None
            patterns = chat.get("skipToolConfirmationPatterns") if isinstance(chat, dict) else None
            if isinstance(patterns, list):
                removed = [pattern for pattern in patterns if pattern in UNSAFE_AUTO_APPROVALS]
                if removed:
                    report["safetyNormalizations"].extend(
                        f"settings.chat.skipToolConfirmationPatterns:{pattern}" for pattern in removed
                    )
                    chat["skipToolConfirmationPatterns"] = [
                        pattern for pattern in patterns if pattern not in UNSAFE_AUTO_APPROVALS
                    ]
                    tx.write_file(settings_path, _json_bytes(settings))

        if mcp_config is not None:
            tx.write_file(mcp_path, _json_bytes(mcp_config))

        if "portable_rule" in components:
            if not args.rule_path:
                raise ValueError("portable_rule component requires at least one --rule-path")
            rule = (SKILL_SOURCE / "references" / "portable-rule.md").read_bytes()
            for path in args.rule_path:
                tx.write_file(path, rule)
            report["portableRulePaths"] = [str(path) for path in args.rule_path]

        if "cline" in components:
            if not args.cline_settings:
                raise ValueError("cline component requires --cline-settings")
            cline = _load_json(args.cline_settings, {"mcpServers": {}})
            if not isinstance(cline, dict):
                raise ValueError("Cline settings must contain a JSON object")
            _merge_mcp_entry(cline, "evidence-first", evidence_entry)
            if "unreal" in components and mcp_config:
                for name in ("unreal-rag", "unreal-agent"):
                    _merge_mcp_entry(
                        cline,
                        name,
                        _mcp_entry_for_frontend(
                            mcp_config["mcpServers"][name],
                            "cline",
                        ),
                    )
            tx.write_file(args.cline_settings, _json_bytes(cline))

        if "context_compactor" in components:
            report["contextCompactor"] = _install_context_compactor(
                args, external_actions_started
            )

        if args.build_rag:
            pwsh = str(getattr(args, "runtime_pwsh", None) or "") or shutil.which("pwsh")
            if not pwsh:
                if args.dry_run:
                    pwsh = "pwsh"
                else:
                    raise FileNotFoundError(
                        "--build-rag requires PowerShell 7 (pwsh). Re-run the platform launcher without "
                        "--skip-runtime-bootstrap so the installer can download it."
                    )
            if not args.dry_run:
                external_actions_started.append("rag-index-build")
            _run(
                _powershell_file_command(
                    pwsh,
                    ROOT / "scripts" / "run_index_pipeline.ps1",
                    [
                        "-WorkspaceRoot",
                        str(ROOT),
                        "-Tier",
                        args.index_tier,
                        "-PythonExe",
                        str(python_exe),
                        "-NonInteractive",
                    ],
                ),
                cwd=ROOT,
                dry_run=args.dry_run,
            )

        if not args.dry_run and "lmstudio" in components:
            smoke = SKILL_SOURCE / "scripts" / "smoke_evidence_first_mcp.py"
            completed = subprocess.run(
                [str(python_exe), str(smoke), "--server", str(installed_skill / "scripts" / "evidence_first_mcp.py")],
                capture_output=True,
                text=True,
                timeout=30,
            )
            report["mcpSmoke"] = json.loads(completed.stdout) if completed.stdout.strip() else {}
            if completed.returncode != 0 or not report["mcpSmoke"].get("ok"):
                raise RuntimeError(completed.stderr or "evidence-first MCP smoke failed")

        report["lmStudioServer"] = _live_server_status(args.lmstudio_url)
        report["indexTier"] = args.index_tier if "unreal" in components else None
        report["externalActions"] = external_actions_started if not args.dry_run else [
            action
            for action, enabled in (
                ("context-compactor-plugin-install", "context_compactor" in components),
                ("rag-index-build", args.build_rag),
            )
            if enabled
        ]
        report["rollbackScope"] = (
            "managed configuration/files only; external npm/lms installs and generated indexes are not rolled back"
        )
        report["knownIntegrationsSafe"] = not args.enable_agent_mode
        report["restartRequired"] = "lmstudio" in components or "unreal" in components
        report["ok"] = True
        journal = tx.commit(report)
        report["journal"] = str(journal or "")
        return report
    except Exception as install_error:
        try:
            tx.rollback_actions()
        except Exception as rollback_error:
            raise RuntimeError(
                f"installation failed ({install_error}); managed rollback also failed ({rollback_error}). "
                f"Inspect the backups under {tx.backup_root} before retrying."
            ) from install_error
        if external_actions_started:
            raise RuntimeError(
                f"installation failed after external actions started ({install_error}). "
                "Managed files were rolled back, but these actions may have left external state: "
                f"{', '.join(external_actions_started)}."
            ) from install_error
        raise
    finally:
        lock.release()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {PRODUCT_VERSION}")
    parser.add_argument("--profile", choices=["safe", "standard", "full", "custom"])
    parser.add_argument("--components", help="Comma-separated components for CUSTOM profile.")
    parser.add_argument("--yes", action="store_true", help="Use profile defaults without prompts.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--rollback", action="store_true", help="Restore the last managed install.")
    parser.add_argument("--enable-agent-mode", action="store_true")
    parser.add_argument(
        "--accept-agent-risk",
        action="store_true",
        help="Required with --enable-agent-mode; acknowledges write/command/build authority.",
    )
    parser.add_argument("--index-tier", choices=["lite", "standard", "full"], default="standard")
    parser.add_argument("--build-rag", action="store_true")
    parser.add_argument("--skip-deps", action="store_true")
    parser.add_argument(
        "--skip-runtime-bootstrap",
        action="store_true",
        help="Do not download/install Python 3.12, Node.js/npm, or PowerShell (pwsh); use PATH tools only.",
    )
    parser.add_argument(
        "--skip-context-compactor",
        action="store_true",
        help="Blocked unless paired with --allow-skip-context-compactor (unsupported).",
    )
    parser.add_argument(
        "--allow-skip-context-compactor",
        action="store_true",
        help="Unsupported emergency bypass for --skip-context-compactor.",
    )
    parser.add_argument("--no-codex", action="store_true")
    parser.add_argument("--no-lmstudio", action="store_true")
    parser.add_argument("--no-unreal", action="store_true")
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")))
    parser.add_argument("--lmstudio-home", type=Path, default=Path(os.environ.get("LMSTUDIO_HOME", Path.home() / ".lmstudio")))
    parser.add_argument("--state-home", type=Path, default=Path.home() / ".evidence-first")
    parser.add_argument("--workspace-root", type=Path, action="append", default=[])
    parser.add_argument("--active-project", type=Path)
    parser.add_argument(
        "--engine-root",
        type=Path,
        help="Unreal Engine root. Otherwise uses UNREAL_ENGINE_ROOT, saved config, or host common locations.",
    )
    parser.add_argument(
        "--rule-path",
        type=Path,
        action="append",
        default=[],
        help="Target rule file (defaults to STATE_HOME/portable-rules when portable_rule is selected).",
    )
    parser.add_argument(
        "--cline-settings",
        type=Path,
        help="Cline MCP settings file (defaults to ~/.cline/data/settings/cline_mcp_settings.json).",
    )
    parser.add_argument("--lmstudio-url", default="http://localhost:1234/v1")
    return parser


def _runtime_requirements(
    components: set[str],
    *,
    build_rag: bool,
) -> tuple[bool, bool]:
    need_node = bool({"unreal", "context_compactor"} & components)
    need_pwsh = bool(build_rag)
    return need_node, need_pwsh


def _bootstrap_runtime_phase(
    args: argparse.Namespace,
    *,
    need_node: bool,
    need_pwsh: bool,
    reexec: bool,
) -> dict[str, str]:
    from installer.bootstrap_runtimes import ensure_runtimes

    bootstrap_lock_token = os.environ.get(BOOTSTRAP_LOCK_TOKEN_ENV, "") or uuid.uuid4().hex
    os.environ[BOOTSTRAP_LOCK_TOKEN_ENV] = bootstrap_lock_token
    bootstrap_lock = InstallLock(
        args.state_home.expanduser().resolve(),
        dry_run=args.dry_run,
        lock_name="runtime-bootstrap.lock",
        owner_token=bootstrap_lock_token,
    )
    bootstrap_lock.acquire()
    try:
        # stdout is the installer's machine-readable JSON contract.
        # Keep bootstrap progress visible without corrupting that stream.
        with contextlib.redirect_stdout(sys.stderr):
            return ensure_runtimes(
                state_home=args.state_home.expanduser(),
                script_path=Path(__file__).resolve(),
                argv=sys.argv[1:],
                dry_run=args.dry_run,
                need_node=need_node,
                need_pwsh=need_pwsh,
                reexec=reexec,
            )
    finally:
        bootstrap_lock.release()


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        _reject_filesystem_root(args.state_home, "state home")
        _reject_filesystem_root(args.codex_home, "Codex home")
        _reject_filesystem_root(args.lmstudio_home, "LM Studio home")
        if args.rollback:
            result = rollback_last_install(args.state_home.expanduser().resolve(), dry_run=args.dry_run)
        else:
            initial_runtimes: dict[str, str] = {}
            if not args.skip_runtime_bootstrap:
                # Establish the supported Python first. If this re-execs, it does
                # so before interactive choices, avoiding duplicate prompts or
                # loss of picker selections.
                initial_runtimes = _bootstrap_runtime_phase(
                    args,
                    need_node=False,
                    need_pwsh=False,
                    reexec=True,
                )
                args.runtime_python = Path(initial_runtimes["python"])

            resolved_components = _resolve_components(args)
            if not args.skip_runtime_bootstrap:
                need_node, need_pwsh = _runtime_requirements(
                    resolved_components[1],
                    build_rag=args.build_rag,
                )
                runtimes = initial_runtimes
                if need_node or need_pwsh:
                    runtimes = _bootstrap_runtime_phase(
                        args,
                        need_node=need_node,
                        need_pwsh=need_pwsh,
                        reexec=False,
                    )
                args.runtime_python = Path(runtimes["python"])
                if runtimes.get("node"):
                    args.runtime_node = Path(runtimes["node"])
                if runtimes.get("npm"):
                    args.runtime_npm = Path(runtimes["npm"])
                if runtimes.get("pwsh"):
                    args.runtime_pwsh = Path(runtimes["pwsh"])
            result = install(args, resolved_components=resolved_components)
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "error": str(exc), "errorType": type(exc).__name__},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
