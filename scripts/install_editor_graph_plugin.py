#!/usr/bin/env python
"""Install the LM Studio Unreal editor graph exporter plugin into a project."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from workspace_paths import (
    find_workspace_root,
    resolve_active_project_path,
    resolve_engine_root_for_association,
    resolve_ubt_path,
)

PLUGIN_NAME = "LmStudioGraphExporter"
PLUGIN_HASH_EXCLUDES = {
    ".git",
    ".vs",
    "Binaries",
    "DerivedDataCache",
    "Intermediate",
    "Saved",
}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"Expected a JSON object in {path}")
    return data


def _write_json(path: Path, data: dict[str, Any]) -> None:
    _write_bytes_atomically(
        path,
        (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )


def _write_bytes_atomically(path: Path, content: bytes) -> None:
    """Replace a descriptor only after a complete same-directory write."""

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _remove_directory_or_link(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _plugin_sidecar_path(plugins_dir: Path, label: str) -> Path:
    """Return a sibling path so renames never cross a filesystem boundary."""

    while True:
        candidate = plugins_dir / f".{PLUGIN_NAME}.{label}-{uuid4().hex}"
        if not _path_exists(candidate):
            return candidate


@dataclass
class _PluginInstallTransaction:
    """Private state retained only while a plugin build is pending."""

    project: Path
    plugins_dir: Path
    destination: Path
    plugins_dir_existed: bool
    destination_existed: bool
    uproject_before: bytes
    uproject_post_hash: str
    destination_hash: str = ""
    backup: Path | None = None
    destination_changed: bool = False
    destination_installed: bool = False
    active: bool = True


def _begin_plugin_install_transaction(project: Path) -> _PluginInstallTransaction:
    project_root = project.parent.resolve()
    plugins_dir = project_root / "Plugins"
    destination = plugins_dir / PLUGIN_NAME
    original = project.read_bytes()
    return _PluginInstallTransaction(
        project=project,
        plugins_dir=plugins_dir,
        destination=destination,
        plugins_dir_existed=plugins_dir.is_dir(),
        destination_existed=_path_exists(destination),
        uproject_before=original,
        uproject_post_hash=_sha256_bytes(original),
    )


def _copy_plugin_tree_atomically(
    source: Path,
    destination: Path,
    transaction: _PluginInstallTransaction | None = None,
) -> None:
    """Install a complete plugin tree without deleting the old one first."""

    plugins_dir = destination.parent
    sidecar_dir = plugins_dir.parent
    plugins_dir.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise OSError(f"Refusing to replace symlinked plugin destination: {destination}")

    # Keep the previous descriptor outside <Project>/Plugins while UBT runs.
    # Unreal recursively discovers .uplugin files there, so a backup inside
    # that directory can be treated as a duplicate plugin during the build.
    staging = _plugin_sidecar_path(sidecar_dir, "staging")
    backup: Path | None = None
    staged_hash = ""
    moved_existing = False
    installed = False
    try:
        shutil.copytree(source, staging)
        staged_hash = _plugin_tree_hash(staging)
        if _path_exists(destination):
            backup = _plugin_sidecar_path(sidecar_dir, "backup")
            os.replace(destination, backup)
            moved_existing = True
            if transaction is not None:
                transaction.destination_changed = True
                transaction.backup = backup
        os.replace(staging, destination)
        installed = True
    except (Exception, SystemExit, KeyboardInterrupt):
        restored_original = False
        try:
            if installed and _path_exists(destination):
                _remove_directory_or_link(destination)
            if moved_existing and backup and _path_exists(backup) and not _path_exists(destination):
                os.replace(backup, destination)
                restored_original = True
        finally:
            if _path_exists(staging):
                _remove_directory_or_link(staging)
            if transaction is not None and restored_original:
                transaction.destination_changed = False
                transaction.destination_installed = False
                transaction.destination_hash = ""
                transaction.backup = None
        raise

    if transaction is not None:
        transaction.destination_changed = True
        transaction.destination_installed = True
        transaction.backup = backup
        transaction.destination_hash = staged_hash
        return

    if backup and _path_exists(backup):
        # A failed cleanup is recoverable: retain the generated backup rather
        # than turning a completed replacement into a destructive rollback.
        try:
            _remove_directory_or_link(backup)
        except OSError:
            pass


def _rollback_plugin_install(transaction: _PluginInstallTransaction) -> dict[str, Any]:
    """Restore only files that still match this invocation's post-state."""

    if not transaction.active:
        return {"attempted": False, "ok": True, "reason": "transaction_already_finished"}

    issues: list[str] = []
    restored_plugin = False
    restored_uproject = False
    try:
        if transaction.destination_changed:
            if transaction.destination_installed:
                if transaction.destination.is_symlink() or not transaction.destination.is_dir():
                    issues.append("plugin destination changed or disappeared before rollback")
                elif _plugin_tree_hash(transaction.destination) != transaction.destination_hash:
                    issues.append("plugin destination changed after installation; refusing to overwrite it")
                else:
                    _remove_directory_or_link(transaction.destination)
            elif _path_exists(transaction.destination):
                issues.append("plugin destination changed during installation; refusing to overwrite it")

            if not issues:
                if transaction.destination_existed:
                    if transaction.backup and _path_exists(transaction.backup):
                        if _path_exists(transaction.destination):
                            issues.append("plugin destination appeared before original backup could be restored")
                        else:
                            os.replace(transaction.backup, transaction.destination)
                            restored_plugin = True
                    else:
                        issues.append("original plugin backup is unavailable")
                else:
                    restored_plugin = True

        if _file_sha256(transaction.project) != transaction.uproject_post_hash:
            issues.append(".uproject changed after installation; refusing to overwrite it")
        else:
            _write_bytes_atomically(transaction.project, transaction.uproject_before)
            restored_uproject = True
    except OSError as exc:
        issues.append(f"rollback filesystem error: {exc}")
    finally:
        transaction.active = False

    if not transaction.plugins_dir_existed:
        try:
            transaction.plugins_dir.rmdir()
        except OSError:
            # It may contain user-created files or a retained backup. Leave it
            # intact rather than deleting anything outside this transaction.
            pass

    return {
        "attempted": True,
        "ok": not issues,
        "restoredPlugin": restored_plugin,
        "restoredUproject": restored_uproject,
        "backupPath": str(transaction.backup) if transaction.backup and _path_exists(transaction.backup) else "",
        "issues": issues,
    }


def _commit_plugin_install(transaction: _PluginInstallTransaction) -> dict[str, Any]:
    """Discard the old plugin only after the requested UBT build succeeded."""

    if not transaction.active:
        return {"ok": True, "cleanupPending": False}
    cleanup_pending = False
    if transaction.backup and _path_exists(transaction.backup):
        try:
            _remove_directory_or_link(transaction.backup)
        except OSError:
            cleanup_pending = True
    transaction.active = False
    return {
        "ok": True,
        "cleanupPending": cleanup_pending,
        "backupPath": str(transaction.backup) if cleanup_pending and transaction.backup else "",
    }


def _plugin_version(path: Path) -> str:
    descriptor = path / f"{PLUGIN_NAME}.uplugin"
    if not descriptor.is_file():
        return ""
    data = _load_json(descriptor)
    return str(data.get("VersionName") or data.get("Version") or "").strip()


def _plugin_tree_hash(path: Path) -> str:
    if not path.is_dir():
        return ""
    digest = hashlib.sha256()
    files = []
    for child in path.rglob("*"):
        if not child.is_file():
            continue
        rel = child.relative_to(path)
        if any(part in PLUGIN_HASH_EXCLUDES for part in rel.parts):
            continue
        files.append(rel)
    for rel in sorted(files, key=lambda item: str(item).lower()):
        digest.update(str(rel).replace("\\", "/").encode("utf-8"))
        digest.update(b"\0")
        digest.update((path / rel).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def resolve_project(explicit: str) -> Path:
    if explicit.strip():
        project = Path(explicit).expanduser()
    else:
        active = resolve_active_project_path()
        if not active:
            raise SystemExit("No project passed and no activeProject is configured.")
        project = active
    project = project.resolve()
    if project.suffix.lower() != ".uproject" or not project.is_file():
        raise SystemExit(f"Expected an existing .uproject file: {project}")
    return project


def is_plugin_enabled(uproject: Path) -> bool:
    data = _load_json(uproject)
    plugins = data.get("Plugins")
    if not isinstance(plugins, list):
        return False
    for item in plugins:
        if isinstance(item, dict) and item.get("Name") == PLUGIN_NAME:
            return item.get("Enabled") is True
    return False


def host_unreal_platform(host_platform: str | None = None) -> str:
    host = host_platform or sys.platform
    if host == "win32":
        return "Win64"
    if host == "darwin":
        return "Mac"
    return "Linux"


def plugin_binary_path(project: Path, host_platform: str | None = None) -> Path:
    project_root = project.parent.resolve()
    platform = host_unreal_platform(host_platform)
    extension = ".dll" if platform == "Win64" else ".dylib" if platform == "Mac" else ".so"
    return project_root / "Plugins" / PLUGIN_NAME / "Binaries" / platform / f"UnrealEditor-{PLUGIN_NAME}{extension}"


def plugin_needs_setup(project: Path, workspace: Path) -> tuple[bool, str]:
    source = workspace / "tools" / "ue_plugins" / PLUGIN_NAME
    if not source.is_dir():
        return True, "plugin_source_missing"

    destination = project.parent.resolve() / "Plugins" / PLUGIN_NAME
    if not destination.is_dir():
        return True, "plugin_missing"

    source_hash = _plugin_tree_hash(source)
    destination_hash = _plugin_tree_hash(destination)
    if source_hash and destination_hash and source_hash != destination_hash:
        return True, "plugin_out_of_date"

    if not plugin_binary_path(project).is_file():
        return True, "plugin_not_compiled"

    if not is_plugin_enabled(project):
        return True, "plugin_not_enabled"

    return False, "ready"


def enable_plugin(uproject: Path, *, dry_run: bool = False) -> bool:
    data = _load_json(uproject)
    plugins = data.get("Plugins")
    if not isinstance(plugins, list):
        plugins = []
        data["Plugins"] = plugins

    changed = False
    for item in plugins:
        if isinstance(item, dict) and item.get("Name") == PLUGIN_NAME:
            if item.get("Enabled") is not True:
                item["Enabled"] = True
                changed = True
            if item.get("TargetAllowList") != ["Editor"]:
                item["TargetAllowList"] = ["Editor"]
                changed = True
            break
    else:
        plugins.append({"Name": PLUGIN_NAME, "Enabled": True, "TargetAllowList": ["Editor"]})
        changed = True

    if changed and not dry_run:
        _write_json(uproject, data)
    return changed


def install_plugin(
    *,
    project: Path,
    workspace: Path,
    enable: bool = True,
    dry_run: bool = False,
    force: bool = False,
    update: bool = False,
    transaction: _PluginInstallTransaction | None = None,
) -> dict[str, Any]:
    source = workspace / "tools" / "ue_plugins" / PLUGIN_NAME
    if not source.is_dir():
        raise SystemExit(f"Plugin source not found: {source}")

    owns_transaction = transaction is None and not dry_run
    if owns_transaction:
        transaction = _begin_plugin_install_transaction(project)

    project_root = project.parent.resolve()
    plugins_dir = project_root / "Plugins"
    destination = plugins_dir / PLUGIN_NAME
    source_hash = _plugin_tree_hash(source)
    destination_hash = _plugin_tree_hash(destination)
    already_existed = destination.exists()
    out_of_date = bool(already_existed and source_hash and destination_hash and source_hash != destination_hash)
    should_copy = not already_existed or force or (update and out_of_date)

    try:
        if dry_run:
            copied = False
        else:
            if should_copy:
                _copy_plugin_tree_atomically(source, destination, transaction)
                copied = True
            else:
                copied = False
        installed_hash = source_hash if copied or (dry_run and should_copy) else destination_hash

        enabled_changed = False
        if enable:
            enabled_changed = enable_plugin(project, dry_run=dry_run)
        if transaction is not None:
            transaction.uproject_post_hash = _file_sha256(project)

        payload = {
            "ok": True,
            "project": str(project),
            "pluginSource": str(source),
            "pluginDestination": str(destination),
            "copied": copied,
            "pluginAlreadyExisted": already_existed,
            "pluginOutOfDate": out_of_date,
            "pluginUpdateRequested": update,
            "pluginWouldCopy": should_copy,
            "sourceVersion": _plugin_version(source),
            "destinationVersion": _plugin_version(destination),
            "sourceHash": source_hash,
            "destinationHashBefore": destination_hash,
            "installedHash": installed_hash,
            "enabled": enable,
            "uprojectChanged": enabled_changed,
        }
    except (Exception, SystemExit, KeyboardInterrupt):
        if owns_transaction and transaction is not None:
            _rollback_plugin_install(transaction)
        raise

    if owns_transaction and transaction is not None:
        _commit_plugin_install(transaction)
    return payload


def _tail(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def _project_engine_association(project: Path) -> str:
    data = _load_json(project)
    return str(data.get("EngineAssociation") or "").strip()


def _ubt_path_for_engine_root(engine_root: Path) -> Path:
    return engine_root / "Engine" / "Binaries" / "DotNET" / "UnrealBuildTool" / "UnrealBuildTool.exe"


def _project_engine_resolution(project: Path, workspace: Path) -> dict[str, str | bool]:
    """Bind plugin compilation to the project's exact EngineAssociation."""

    return resolve_engine_root_for_association(
        _project_engine_association(project),
        workspace,
    )


def _candidate_ubt_paths(
    project: Path,
    workspace: Path,
    engine_resolution: dict[str, str | bool] | None = None,
) -> list[Path]:
    association = _project_engine_association(project)
    resolution = engine_resolution or _project_engine_resolution(project, workspace)
    roots: list[Path] = []
    seen: set[str] = set()

    def add_root(root: Path) -> None:
        key = str(root).lower()
        if key not in seen:
            roots.append(root)
            seen.add(key)

    resolved_root = str(resolution.get("engineRoot") or "").strip()
    if resolved_root:
        add_root(Path(resolved_root))

    # A project without EngineAssociation may use the legacy explicit UBT
    # setting.  A non-empty association must never fall through to that
    # setting or a default engine after exact resolution failed.
    include_legacy_ubt = not association

    paths: list[Path] = []
    for root in roots:
        exe = _ubt_path_for_engine_root(root.resolve())
        paths.append(exe)
        paths.append(exe.with_suffix(".dll"))
    if include_legacy_ubt:
        configured = resolve_ubt_path(workspace)
        paths.append(configured)
        paths.append(configured.with_suffix(".dll"))
    return list(dict.fromkeys(path.resolve() for path in paths))


def _ubt_invocation(
    project: Path,
    workspace: Path,
    engine_resolution: dict[str, str | bool] | None = None,
) -> tuple[list[str], Path | None]:
    for candidate in _candidate_ubt_paths(project, workspace, engine_resolution):
        if candidate.is_file():
            if candidate.suffix.lower() == ".dll":
                return ["dotnet", str(candidate)], candidate
            return [str(candidate)], candidate
    return [], None


def maybe_build_plugin(
    *,
    project: Path,
    workspace: Path,
    install_payload: dict[str, Any],
    dry_run: bool = False,
    target: str = "",
    platform: str = "",
    configuration: str = "Development",
    timeout_sec: int = 1800,
) -> dict[str, Any]:
    project_root = project.parent.resolve()
    platform = platform.strip() or host_unreal_platform()
    binary = plugin_binary_path(project)
    needs_build = bool(
        install_payload.get("copied")
        or install_payload.get("pluginWouldCopy")
        or install_payload.get("uprojectChanged")
        or not binary.is_file()
    )
    target = target.strip() or f"{project.stem}Editor"

    association = _project_engine_association(project)
    engine_resolution = _project_engine_resolution(project, workspace)
    invocation, ubt_path = _ubt_invocation(project, workspace, engine_resolution)
    command = [
        *invocation,
        target,
        platform,
        configuration,
        f"-Project={project}",
        "-WaitMutex",
    ]

    if not needs_build:
        return {
            "requested": True,
            "skipped": True,
            "ok": True,
            "reason": "Plugin already present, enabled, and compiled.",
            "binary": str(binary),
        }
    if association and not bool(engine_resolution.get("ok")):
        return {
            "requested": True,
            "skipped": False,
            "ok": False,
            "errorCode": str(engine_resolution.get("errorCode") or "ENGINE_ASSOCIATION_UNRESOLVED"),
            "error": str(
                engine_resolution.get("error")
                or "The project's EngineAssociation could not be resolved."
            ),
            "checkedUbtPaths": [],
            "binary": str(binary),
        }
    if dry_run:
        return {
            "requested": True,
            "skipped": False,
            "ok": True,
            "dryRun": True,
            "command": command if invocation else ["<UnrealBuildTool not found>", *command[len(invocation) :]],
            "ubtPath": str(ubt_path) if ubt_path else "",
            "binary": str(binary),
        }
    if not invocation:
        checked_paths = _candidate_ubt_paths(project, workspace, engine_resolution)
        resolved_root = str(engine_resolution.get("engineRoot") or "").strip()
        return {
            "requested": True,
            "skipped": False,
            "ok": False,
            "errorCode": "UBT_NOT_FOUND",
            "error": (
                f"UnrealBuildTool not found under {Path(resolved_root) / 'Engine' / 'Binaries'}"
                if resolved_root
                else "UnrealBuildTool could not be resolved for the active project."
            ),
            "checkedUbtPaths": [str(path) for path in checked_paths],
            "binary": str(binary),
        }

    try:
        proc = subprocess.run(
            command,
            cwd=str(project_root),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return {
            "requested": True,
            "skipped": False,
            "ok": False,
            "errorCode": "BUILD_TIMEOUT",
            "error": f"UnrealBuildTool timed out after {timeout_sec} seconds",
            "command": command,
            "ubtPath": str(ubt_path) if ubt_path else "",
            "binary": str(binary),
            "outputTail": _tail(str(output)),
        }
    except OSError as exc:
        return {
            "requested": True,
            "skipped": False,
            "ok": False,
            "errorCode": "UBT_EXEC_FAILED",
            "error": str(exc),
            "command": command,
            "ubtPath": str(ubt_path) if ubt_path else "",
            "binary": str(binary),
        }
    output = proc.stdout or ""
    return {
        "requested": True,
        "skipped": False,
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "command": command,
        "ubtPath": str(ubt_path) if ubt_path else "",
        "binary": str(binary),
        "outputTail": _tail(output),
    }


def install_and_build_plugin(
    *,
    project: Path,
    workspace: Path,
    enable: bool = True,
    dry_run: bool = False,
    force: bool = False,
    update: bool = False,
    target: str = "",
    platform: str = "",
    configuration: str = "Development",
    timeout_sec: int = 1800,
) -> dict[str, Any]:
    """Install, build, and restore the exact prior project state on failure."""

    transaction = _begin_plugin_install_transaction(project)
    try:
        install_payload = install_plugin(
            project=project,
            workspace=workspace,
            enable=enable,
            dry_run=dry_run,
            force=force,
            update=update,
            transaction=transaction,
        )
    except KeyboardInterrupt:
        _rollback_plugin_install(transaction)
        raise
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - rollback must cover unexpected installer failures
        rollback = _rollback_plugin_install(transaction)
        return {
            "ok": False,
            "install": {
                "ok": False,
                "project": str(project),
                "pluginDestination": str(project.parent.resolve() / "Plugins" / PLUGIN_NAME),
                "errorCode": "PLUGIN_INSTALL_FAILED",
                "error": str(exc),
            },
            "build": {
                "requested": True,
                "skipped": True,
                "ok": False,
                "errorCode": "PLUGIN_INSTALL_FAILED",
                "error": "Plugin installation failed before UnrealBuildTool could run.",
            },
            "rollback": rollback,
        }

    try:
        build_payload = maybe_build_plugin(
            project=project,
            workspace=workspace,
            install_payload=install_payload,
            dry_run=dry_run,
            target=target,
            platform=platform,
            configuration=configuration,
            timeout_sec=timeout_sec,
        )
    except KeyboardInterrupt:
        _rollback_plugin_install(transaction)
        raise
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - preserve the project if the build wrapper fails
        build_payload = {
            "requested": True,
            "skipped": False,
            "ok": False,
            "errorCode": "UBT_EXEC_FAILED",
            "error": str(exc),
        }

    if build_payload.get("ok"):
        return {
            "ok": True,
            "install": install_payload,
            "build": build_payload,
            "commit": _commit_plugin_install(transaction),
        }

    return {
        "ok": False,
        "install": install_payload,
        "build": build_payload,
        "rollback": _rollback_plugin_install(transaction),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Install LM Studio Blueprint graph exporter plugin.")
    parser.add_argument("--project", default="", help="Path to .uproject. Defaults to shared activeProject.")
    parser.add_argument("--workspace", default="", help="Workspace root. Defaults to auto-detected repo root.")
    parser.add_argument("--no-enable", action="store_true", help="Copy plugin without editing the .uproject Plugins list.")
    parser.add_argument("--force", action="store_true", help="Replace an existing project plugin copy.")
    parser.add_argument("--update", action="store_true", help="Replace the project plugin copy only when repo plugin files differ.")
    parser.add_argument("--build", action="store_true", help="Run UnrealBuildTool when the plugin needs compiling.")
    parser.add_argument("--target", default="", help="UBT target. Defaults to <ProjectName>Editor.")
    parser.add_argument("--platform", default="", help="UBT platform (defaults to the host: Win64, Mac, or Linux).")
    parser.add_argument("--configuration", default="Development")
    parser.add_argument("--timeout-sec", type=int, default=1800)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    workspace = Path(args.workspace).expanduser().resolve() if args.workspace else find_workspace_root()
    project = resolve_project(args.project)
    if args.build:
        result = install_and_build_plugin(
            project=project,
            workspace=workspace,
            enable=not args.no_enable,
            dry_run=args.dry_run,
            force=args.force,
            update=args.update,
            target=args.target,
            platform=args.platform,
            configuration=args.configuration,
            timeout_sec=args.timeout_sec,
        )
        payload = result["install"]
        payload["ok"] = bool(result.get("ok"))
        payload["build"] = result["build"]
        if result.get("rollback"):
            payload["rollback"] = result["rollback"]
        if result.get("commit", {}).get("cleanupPending"):
            payload["commit"] = result["commit"]
    else:
        payload = install_plugin(
            project=project,
            workspace=workspace,
            enable=not args.no_enable,
            dry_run=args.dry_run,
            force=args.force,
            update=args.update,
        )
        payload["build"] = {"requested": False}
    payload["next"] = [
        "Close Unreal Editor if it is open before installing or rebuilding.",
        "Run .\\rag.ps1 export-editor-metadata to produce Blueprint node and pin metadata.",
    ]
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload["build"].get("requested") and not payload["build"].get("ok", False):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
