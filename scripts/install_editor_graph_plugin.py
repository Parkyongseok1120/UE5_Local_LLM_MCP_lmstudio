#!/usr/bin/env python
"""Install the LM Studio Unreal editor graph exporter plugin into a project."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from workspace_paths import find_workspace_root, resolve_active_project_path, resolve_engine_root, resolve_ubt_path

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
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def plugin_binary_path(project: Path) -> Path:
    project_root = project.parent.resolve()
    return project_root / "Plugins" / PLUGIN_NAME / "Binaries" / "Win64" / f"UnrealEditor-{PLUGIN_NAME}.dll"


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
) -> dict[str, Any]:
    source = workspace / "tools" / "ue_plugins" / PLUGIN_NAME
    if not source.is_dir():
        raise SystemExit(f"Plugin source not found: {source}")

    project_root = project.parent.resolve()
    plugins_dir = project_root / "Plugins"
    destination = plugins_dir / PLUGIN_NAME
    source_hash = _plugin_tree_hash(source)
    destination_hash = _plugin_tree_hash(destination)
    already_existed = destination.exists()
    out_of_date = bool(already_existed and source_hash and destination_hash and source_hash != destination_hash)
    should_copy = not already_existed or force or (update and out_of_date)

    if dry_run:
        copied = False
    else:
        plugins_dir.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if force or (update and out_of_date):
                shutil.rmtree(destination)
                shutil.copytree(source, destination)
                copied = True
            else:
                copied = False
        else:
            shutil.copytree(source, destination)
            copied = True
    installed_hash = source_hash if copied or (dry_run and should_copy) else destination_hash

    enabled_changed = False
    if enable:
        enabled_changed = enable_plugin(project, dry_run=dry_run)

    return {
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


def _tail(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def _project_engine_association(project: Path) -> str:
    data = _load_json(project)
    return str(data.get("EngineAssociation") or "").strip()


def _program_files_epic_roots() -> list[Path]:
    roots: list[Path] = []
    for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
        value = os.environ.get(env_name, "").strip()
        if value:
            roots.append(Path(value) / "Epic Games")
    return roots


def _engine_roots_from_association(association: str) -> list[Path]:
    if not association:
        return []
    candidates: list[Path] = []
    folder_names: list[str] = []
    if association.startswith("UE_"):
        folder_names.append(association)
    elif association[0].isdigit():
        folder_names.append(f"UE_{association}")
    for epic_root in _program_files_epic_roots():
        for folder in folder_names:
            candidates.append(epic_root / folder)
    return candidates


def _ubt_path_for_engine_root(engine_root: Path) -> Path:
    return engine_root / "Engine" / "Binaries" / "DotNET" / "UnrealBuildTool" / "UnrealBuildTool.exe"


def _candidate_ubt_paths(project: Path, workspace: Path) -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()

    def add_root(root: Path) -> None:
        key = str(root).lower()
        if key not in seen:
            roots.append(root)
            seen.add(key)

    for root in _engine_roots_from_association(_project_engine_association(project)):
        add_root(root)
    env_root = os.environ.get("UNREAL_ENGINE_ROOT", "").strip()
    if env_root:
        add_root(Path(env_root).expanduser())
    add_root(resolve_engine_root(workspace))

    paths: list[Path] = []
    for root in roots:
        exe = _ubt_path_for_engine_root(root.resolve())
        paths.append(exe)
        paths.append(exe.with_suffix(".dll"))
    configured = resolve_ubt_path(workspace)
    paths.append(configured)
    paths.append(configured.with_suffix(".dll"))
    return list(dict.fromkeys(path.resolve() for path in paths))


def _ubt_invocation(project: Path, workspace: Path) -> tuple[list[str], Path | None]:
    for candidate in _candidate_ubt_paths(project, workspace):
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
    platform: str = "Win64",
    configuration: str = "Development",
    timeout_sec: int = 1800,
) -> dict[str, Any]:
    project_root = project.parent.resolve()
    binary = project_root / "Plugins" / PLUGIN_NAME / "Binaries" / "Win64" / f"UnrealEditor-{PLUGIN_NAME}.dll"
    needs_build = bool(
        install_payload.get("copied")
        or install_payload.get("pluginWouldCopy")
        or install_payload.get("uprojectChanged")
        or not binary.is_file()
    )
    target = target.strip() or f"{project.stem}Editor"

    invocation, ubt_path = _ubt_invocation(project, workspace)
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
        return {
            "requested": True,
            "skipped": False,
            "ok": False,
            "error": f"UnrealBuildTool not found under {resolve_ubt_path(workspace).parent}",
            "checkedUbtPaths": [str(path) for path in _candidate_ubt_paths(project, workspace)],
            "binary": str(binary),
        }

    proc = subprocess.run(
        command,
        cwd=str(project_root),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout_sec,
        check=False,
    )
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Install LM Studio Blueprint graph exporter plugin.")
    parser.add_argument("--project", default="", help="Path to .uproject. Defaults to shared activeProject.")
    parser.add_argument("--workspace", default="", help="Workspace root. Defaults to auto-detected repo root.")
    parser.add_argument("--no-enable", action="store_true", help="Copy plugin without editing the .uproject Plugins list.")
    parser.add_argument("--force", action="store_true", help="Replace an existing project plugin copy.")
    parser.add_argument("--update", action="store_true", help="Replace the project plugin copy only when repo plugin files differ.")
    parser.add_argument("--build", action="store_true", help="Run UnrealBuildTool when the plugin needs compiling.")
    parser.add_argument("--target", default="", help="UBT target. Defaults to <ProjectName>Editor.")
    parser.add_argument("--platform", default="Win64")
    parser.add_argument("--configuration", default="Development")
    parser.add_argument("--timeout-sec", type=int, default=1800)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    workspace = Path(args.workspace).expanduser().resolve() if args.workspace else find_workspace_root()
    project = resolve_project(args.project)
    payload = install_plugin(
        project=project,
        workspace=workspace,
        enable=not args.no_enable,
        dry_run=args.dry_run,
        force=args.force,
        update=args.update,
    )
    payload["build"] = {"requested": False}
    if args.build:
        payload["build"] = maybe_build_plugin(
            project=project,
            workspace=workspace,
            install_payload=payload,
            dry_run=args.dry_run,
            target=args.target,
            platform=args.platform,
            configuration=args.configuration,
            timeout_sec=args.timeout_sec,
        )
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
