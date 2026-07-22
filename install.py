#!/usr/bin/env python3
"""Cross-platform integrated installer for evidence-first coding and optional Unreal adapters."""

from __future__ import annotations

import argparse
import json
import os
import platform
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
INSTALL_MANIFEST = json.loads((ROOT / "installer" / "manifest.json").read_text(encoding="utf-8"))
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
PORTABLE_RULE_FILENAME = "evidence-first-code-audit.md"
CLINE_SETTINGS_RELATIVE_PATH = Path(".cline") / "data" / "settings" / "cline_mcp_settings.json"


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


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
    for location in _common_engine_locations():
        if _engine_root_is_valid(location):
            candidates.append(location)
        if location.is_dir():
            candidates.extend(path for path in location.glob("UE_5.*") if _engine_root_is_valid(path))
    unique = {str(path.resolve()).casefold(): path.resolve() for path in candidates}
    ordered = sorted(unique.values(), key=lambda path: path.name, reverse=True)
    requested = engine_association.strip()
    if requested and requested[0:1].isdigit():
        requested = f"UE_{requested}"
    if requested:
        exact = next((path for path in ordered if path.name.casefold() == requested.casefold()), None)
        if exact:
            return exact
    return ordered[0] if ordered else None


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


def _pick_indexing_target(kind: str, initial_directory: Path) -> Path | None:
    """Open the native file picker through tkinter without asking for a typed path."""
    root: Any | None = None
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except tk.TclError:
            pass
        if kind == "uproject":
            selected = filedialog.askopenfilename(
                parent=root,
                title="Select Unreal project (.uproject) to index",
                initialdir=str(initial_directory),
                filetypes=(("Unreal Project", "*.uproject"),),
            )
        else:
            selected = filedialog.askdirectory(
                parent=root,
                title="Select folder to scan for Unreal projects",
                initialdir=str(initial_directory),
                mustexist=True,
            )
    except Exception as exc:  # GUI backends fail differently across supported desktop platforms.
        print(f"  Project picker unavailable: {exc}")
        return None
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass

    if not selected:
        return None
    path = Path(selected).expanduser().resolve()
    if kind == "uproject" and (not path.is_file() or path.suffix.lower() != ".uproject"):
        print(f"  Ignoring invalid Unreal project selection: {path}")
        return None
    if kind == "folder" and not path.is_dir():
        print(f"  Ignoring invalid folder selection: {path}")
        return None
    return path


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


def _json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _default_platform() -> str:
    system = platform.system().lower()
    if system == "windows":
        return "Win64"
    if system == "darwin":
        return "Mac"
    return "Linux"


@dataclass
class InstallLock:
    state_home: Path
    dry_run: bool = False
    path: Path = field(init=False)
    acquired: bool = False

    def __post_init__(self) -> None:
        self.path = self.state_home / "install.lock"

    def acquire(self) -> None:
        if self.dry_run:
            return
        self.state_home.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise RuntimeError(
                f"another installer is active (or a stale lock remains): {self.path}"
            ) from exc
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "createdAt": time.time()}, handle)
        self.acquired = True

    def release(self) -> None:
        if self.acquired:
            try:
                self.path.unlink()
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
            os.replace(temporary, target)
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
            if old.exists():
                shutil.rmtree(old) if old.is_dir() else old.unlink()
        except Exception:
            if moved_old and old.exists() and not target.exists():
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
            if target.exists():
                shutil.rmtree(target) if target.is_dir() else target.unlink()
            if action["existed"]:
                backup = Path(action["backup"])
                if backup.is_dir():
                    shutil.copytree(backup, target)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup, target)

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
            os.replace(temporary, journal_path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return journal_path


def rollback_last_install(state_home: Path, *, dry_run: bool = False) -> dict[str, Any]:
    journal_path = state_home / "install-journal.json"
    journal = _load_json(journal_path, None)
    if not isinstance(journal, dict):
        raise FileNotFoundError(f"install journal not found: {journal_path}")
    allowed = [Path(value).resolve() for value in journal.get("allowedRoots") or []]
    restored = 0
    for action in reversed(journal.get("actions") or []):
        target = Path(action["target"]).resolve()
        if not any(_is_within(target, root) or target == root for root in allowed):
            raise ValueError(f"journal target escaped approved roots: {target}")
        print(f"{'[dry-run] ' if dry_run else ''}rollback: {target}")
        if dry_run:
            continue
        if target.exists():
            shutil.rmtree(target) if target.is_dir() else target.unlink()
        if action.get("existed"):
            backup = Path(action["backup"])
            if backup.is_dir():
                shutil.copytree(backup, target)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, target)
        restored += 1
    if not dry_run:
        journal_path.unlink()
    return {"ok": True, "restored": restored, "journal": str(journal_path)}


def _prompt_yes_no(question: str, default: bool) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    answer = input(f"{question} {suffix} ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes", "1", "true"}


def _interactive_profile() -> str:
    print("Install profile:")
    print("  1. SAFE (recommended: portable Codex + LM Studio, no project adapter)")
    print("  2. STANDARD (SAFE + read-only Unreal adapter)")
    print("  3. FULL (STANDARD + LM Studio context compactor; still read-only)")
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
                if _prompt_yes_no(f"Install {component}?", component in {"codex", "lmstudio"}):
                    components.add(component)
    else:
        components = set(PROFILE_DEFAULTS[profile])

    if interactive:
        if "unreal" in components:
            if _prompt_yes_no("Install LM Studio context compactor?", profile == "full"):
                components.add("context_compactor")
            else:
                components.discard("context_compactor")
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
        components.discard("context_compactor")
    if args.skip_context_compactor:
        components.discard("context_compactor")
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
    if args.enable_agent_mode and not args.accept_agent_risk:
        raise ValueError("agent mode requires explicit --accept-agent-risk")
    if interactive:
        _confirm_interactive_install(profile, components, args)
    return profile, components


def _merge_mcp_entry(config: dict[str, Any], name: str, entry: dict[str, Any]) -> None:
    servers = config.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError("mcpServers must be a JSON object")
    servers[name] = entry


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
) -> dict[str, dict[str, Any]]:
    allow = "1" if args.enable_agent_mode else "0"
    state_root = args.lmstudio_home / "state" / "unreal-agent"
    rag_entry = {
        "command": str(python_exe),
        "args": [
            str(ROOT / "scripts" / "unreal_rag_mcp.py"),
            "--index",
            str(ROOT / "data" / "unreal58" / "rag.sqlite"),
        ],
        "timeout": 420000,
        "env": {
            "SHARED_UNREAL_CONFIG": str(shared_config),
            "AGENT_STATE_ROOT": str(state_root),
            "UNREAL58_ROOT": str(ROOT),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "MCP_ESSENTIAL_TOOLS": "1",
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
            "ALLOW_WRITE": allow,
            "ALLOW_COMMANDS": allow,
            "ALLOW_UNREAL_BUILD": allow,
            "MAX_READ_BYTES": "524288",
            "MAX_OUTPUT_BYTES": "262144",
            "COMMAND_TIMEOUT_MS": "600000",
            "MCP_ESSENTIAL_TOOLS": "1",
            "MCP_REQUIRE_PLAN_AUTH": "1",
            "VALIDATE_ON_WRITE": allow,
        },
    }
    if args.engine_root:
        rag_entry["env"]["UNREAL_ENGINE_ROOT"] = str(args.engine_root)
        agent_entry["env"]["UNREAL_ENGINE_ROOT"] = str(args.engine_root)
    return {"unreal-rag": rag_entry, "unreal-agent": agent_entry}


def _run(command: list[str], *, cwd: Path, dry_run: bool) -> None:
    print(("[dry-run] " if dry_run else "") + "run: " + " ".join(command))
    if dry_run:
        return
    subprocess.run(command, cwd=str(cwd), check=True)


def _install_context_compactor(args: argparse.Namespace) -> None:
    plugin = ROOT / "lmstudio-context-compactor-plugin"
    if not plugin.is_dir():
        raise FileNotFoundError(f"context compactor source missing: {plugin}")
    npm = shutil.which("npm")
    lms = shutil.which("lms")
    if not lms:
        candidate = args.lmstudio_home / "bin" / ("lms.exe" if os.name == "nt" else "lms")
        if candidate.exists():
            lms = str(candidate)
    if not npm or not lms:
        raise FileNotFoundError("context compactor requires npm and the lms CLI")
    if not args.skip_deps:
        _run([npm, "ci", "--no-audit", "--no-fund"], cwd=plugin, dry_run=args.dry_run)
        _run([npm, "test"], cwd=plugin, dry_run=args.dry_run)
    _run([lms, "dev", "--install", "-y"], cwd=plugin, dry_run=args.dry_run)


def _live_server_status(url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/models", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return {"reachable": True, "models": [row.get("id") for row in payload.get("data") or []]}
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return {"reachable": False, "error": str(exc)}


def install(args: argparse.Namespace) -> dict[str, Any]:
    profile, components = _resolve_components(args)
    python_exe = Path(sys.executable).resolve()
    if sys.version_info < (3, 10):
        raise RuntimeError("Python 3.10+ is required")
    if not (SKILL_SOURCE / "SKILL.md").is_file():
        raise FileNotFoundError(f"skill source missing: {SKILL_SOURCE}")

    args.codex_home = args.codex_home.expanduser().resolve()
    args.lmstudio_home = args.lmstudio_home.expanduser().resolve()
    args.state_home = args.state_home.expanduser().resolve()
    args.workspace_root = [path.expanduser().resolve() for path in args.workspace_root]
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
            node = shutil.which("node")
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
                npm = shutil.which("npm")
                if not npm:
                    raise FileNotFoundError("npm is required for the Unreal adapter")
                _run([npm, "ci", "--no-audit", "--no-fund"], cwd=agent_root, dry_run=args.dry_run)

            shared_path = args.lmstudio_home / "config" / "unreal-workspace.json"
            agent_path = args.lmstudio_home / "config" / "unreal-agent.json"
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
            if detected_engine is None and _engine_root_is_valid(existing_engine):
                detected_engine = existing_engine.resolve()
            if detected_engine is None:
                detected_engine = _detect_engine_root(association)
            args.engine_root = detected_engine
            shared["defaultEngineRoot"] = str(detected_engine) if detected_engine else ""
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
            assert mcp_config is not None
            for name, entry in _unreal_entries(args, python_exe, node_exe, shared_path, agent_path).items():
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
                    _merge_mcp_entry(cline, name, mcp_config["mcpServers"][name])
            tx.write_file(args.cline_settings, _json_bytes(cline))

        if "context_compactor" in components:
            _install_context_compactor(args)

        if args.build_rag:
            pwsh = shutil.which("pwsh") or shutil.which("powershell")
            if not pwsh:
                raise FileNotFoundError("--build-rag requires PowerShell (pwsh or powershell)")
            _run(
                [
                    pwsh,
                    "-NoProfile",
                    "-File",
                    str(ROOT / "scripts" / "run_index_pipeline.ps1"),
                    "-WorkspaceRoot",
                    str(ROOT),
                    "-Tier",
                    args.index_tier,
                    "-PythonExe",
                    str(python_exe),
                    "-NonInteractive",
                ],
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
        report["externalActions"] = [
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
    except Exception:
        tx.rollback_actions()
        raise
    finally:
        lock.release()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
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
    parser.add_argument("--skip-context-compactor", action="store_true")
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


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.rollback:
            result = rollback_last_install(args.state_home.expanduser().resolve(), dry_run=args.dry_run)
        else:
            result = install(args)
    except (FileNotFoundError, OSError, RuntimeError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
