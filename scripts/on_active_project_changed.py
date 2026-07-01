#!/usr/bin/env python
"""Auto-install editor plugin and sync RAG inputs when the active project changes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from active_project_sync import sync_active_project
from editor_metadata_status import editor_metadata_status
from incremental_build import manifest_stale
from install_editor_graph_plugin import (
    install_plugin,
    maybe_build_plugin,
    plugin_needs_setup,
    resolve_project,
)
from workspace_paths import (
    auto_editor_export_enabled,
    find_workspace_root,
    load_shared_config,
    resolve_index_dir,
)


def auto_setup_enabled(config: dict[str, Any] | None = None) -> bool:
    cfg = config if config is not None else load_shared_config()
    return cfg.get("autoSetupOnProjectSwitch") is not False


def _load_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _row_metadata(row: dict[str, Any]) -> dict[str, Any]:
    meta = row.get("metadata")
    return meta if isinstance(meta, dict) else row


def _project_rows(rows: list[dict[str, Any]], project_name: str, project_root: Path | None = None) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    root_text = str(project_root.resolve()) if project_root else ""
    for row in rows:
        meta = _row_metadata(row)
        project = str(meta.get("project") or "")
        project_path = str(meta.get("project_root") or meta.get("project_path") or "")
        if project == project_name:
            matched.append(meta)
            continue
        if root_text and root_text in project_path:
            matched.append(meta)
    return matched


def _newest_mtime(root: Path, patterns: tuple[str, ...]) -> float | None:
    latest: float | None = None
    if not root.is_dir():
        return None
    for pattern in patterns:
        for path in root.rglob(pattern):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            latest = mtime if latest is None else max(latest, mtime)
    return latest


def _project_has_uassets(project_root: Path) -> bool:
    content = project_root / "Content"
    if not content.is_dir():
        return False
    for _ in content.rglob("*.uasset"):
        return True
    return False


def _project_editor_metadata_needs_sync(project: Path, index_dir: Path, project_name: str) -> tuple[bool, str]:
    project_root = project.parent.resolve()
    registry_rows = _project_rows(_load_jsonl_rows(index_dir / "raw_asset_registry.jsonl"), project_name)
    has_uassets = _project_has_uassets(project_root)

    if has_uassets and not registry_rows:
        return True, "missing_project_asset_registry"

    if not has_uassets:
        return False, "no_content_assets"

    status = editor_metadata_status(index_dir, project)
    if status.get("needsEditorExport"):
        return True, "stale_editor_metadata"
    return False, "editor_metadata_fresh"


def project_index_needs_sync(project: Path, index_dir: Path) -> tuple[bool, str]:
    project_name = project.stem
    project_root = project.parent.resolve()

    profiles = index_dir / "raw_project_profiles.jsonl"
    if not _project_rows(_load_jsonl_rows(profiles), project_name, project_root):
        return True, "missing_project_profile"

    architecture = index_dir / "raw_project_architecture.jsonl"
    if not _project_rows(_load_jsonl_rows(architecture), project_name, project_root):
        return True, "missing_project_architecture"

    symbols_path = index_dir / "raw_project_symbols.jsonl"
    if not _project_rows(_load_jsonl_rows(symbols_path), project_name):
        return True, "missing_project_symbols"

    source_root = project_root / "Source"
    if source_root.is_dir() and symbols_path.is_file():
        newest_source = _newest_mtime(source_root, ("*.cpp", "*.h", "*.hpp", "*.cs"))
        if newest_source is not None:
            try:
                symbols_mtime = symbols_path.stat().st_mtime
            except OSError:
                symbols_mtime = None
            if symbols_mtime is not None and newest_source > symbols_mtime:
                return True, "source_newer_than_symbols"

    editor_needed, editor_reason = _project_editor_metadata_needs_sync(project, index_dir, project_name)
    if editor_needed:
        return True, editor_reason

    manifest_path = index_dir / "build_manifest.json"
    sqlite_path = index_dir / "rag.sqlite"
    stale, reason = manifest_stale(index_dir, manifest_path, sqlite_path)
    if stale:
        return True, f"index_stale:{reason}"

    return False, "up_to_date"


def active_project_check_status(project: Path, workspace: Path | None = None, index_dir: Path | None = None) -> dict[str, Any]:
    workspace = workspace or find_workspace_root()
    index_dir = index_dir or resolve_index_dir()
    plugin_needed, plugin_reason = plugin_needs_setup(project, workspace)
    sync_needed, sync_reason = project_index_needs_sync(project, index_dir)
    return {
        "project": str(project),
        "indexDir": str(index_dir),
        "autoSetupOnProjectSwitch": auto_setup_enabled(),
        "pluginNeeded": plugin_needed,
        "pluginReason": plugin_reason,
        "syncNeeded": sync_needed,
        "syncReason": sync_reason,
        "ready": not plugin_needed and not sync_needed,
    }


def ensure_editor_plugin(
    project: Path,
    workspace: Path,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    needs, reason = plugin_needs_setup(project, workspace)
    if not needs:
        return {
            "skipped": True,
            "reason": reason,
            "ok": True,
        }

    install_payload = install_plugin(
        project=project,
        workspace=workspace,
        enable=True,
        dry_run=dry_run,
        update=True,
    )
    build_payload = maybe_build_plugin(
        project=project,
        workspace=workspace,
        install_payload=install_payload,
        dry_run=dry_run,
    )
    copied_or_present = bool(install_payload.get("copied")) or bool(install_payload.get("pluginAlreadyExisted"))
    build_ok = bool(build_payload.get("ok", True)) or bool(build_payload.get("skipped"))
    ok = bool(install_payload.get("ok")) and (build_ok or copied_or_present)
    result: dict[str, Any] = {
        "skipped": False,
        "reason": reason,
        "ok": ok,
        "install": install_payload,
        "build": build_payload,
    }
    if not build_ok and copied_or_present:
        result["warning"] = "Plugin copied but compile step failed or was skipped; Editor export may be unavailable until UBT build succeeds."
    return result


def run_active_project_sync(workspace: Path, project: Path | None = None) -> dict[str, Any]:
    return sync_active_project(workspace=workspace, project=project)


def ensure_active_project_ready(
    project: str | Path | None = None,
    *,
    previous_project: str | Path | None = None,
    dry_run: bool = False,
    force: bool = False,
    skip_plugin: bool = False,
    skip_sync: bool = False,
) -> dict[str, Any]:
    workspace = find_workspace_root()
    config = load_shared_config()
    if not force and not auto_setup_enabled(config):
        return {
            "ok": True,
            "skipped": True,
            "reason": "autoSetupOnProjectSwitch_disabled",
        }

    resolved = resolve_project(str(project or ""))
    index_dir = resolve_index_dir()

    if previous_project and not force:
        try:
            if Path(previous_project).resolve() == resolved.resolve():
                status = active_project_check_status(resolved, workspace, index_dir)
                if status["ready"]:
                    return {
                        "ok": True,
                        "skipped": True,
                        "reason": "already_ready_for_unchanged_project",
                        "project": str(resolved),
                        "check": status,
                    }
        except OSError:
            pass

    plugin_check = plugin_needs_setup(resolved, workspace)
    index_check = project_index_needs_sync(resolved, index_dir)

    payload: dict[str, Any] = {
        "ok": True,
        "project": str(resolved),
        "indexDir": str(index_dir),
        "autoEditorExport": auto_editor_export_enabled(config),
        "check": active_project_check_status(resolved, workspace, index_dir),
        "plugin": {
            "needed": plugin_check[0],
            "reason": plugin_check[1],
            "skipped": True,
        },
        "sync": {
            "needed": index_check[0],
            "reason": index_check[1],
            "skipped": True,
        },
    }

    if not skip_plugin and (force or plugin_check[0]):
        payload["plugin"] = ensure_editor_plugin(resolved, workspace, dry_run=dry_run)
        payload["plugin"]["needed"] = True
        if not payload["plugin"].get("ok", True):
            payload["plugin"]["warning"] = payload["plugin"].get(
                "warning",
                "Plugin install/build did not fully succeed; continuing with project sync when needed.",
            )
    elif not plugin_check[0]:
        payload["plugin"]["skipped"] = True
        payload["plugin"]["reason"] = plugin_check[1]

    if not skip_sync and (force or index_check[0]):
        if dry_run:
            payload["sync"] = {
                "skipped": False,
                "needed": True,
                "reason": index_check[1],
                "ok": True,
                "dryRun": True,
            }
        else:
            sync_result = run_active_project_sync(workspace, resolved)
            payload["sync"] = {
                "skipped": False,
                "needed": True,
                "reason": index_check[1],
                **sync_result,
            }
            if not sync_result.get("ok"):
                payload["ok"] = False
    elif not index_check[0]:
        payload["sync"]["skipped"] = True
        payload["sync"]["reason"] = index_check[1]

    payload["skipped"] = bool(
        payload["plugin"].get("skipped")
        and payload["sync"].get("skipped")
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Ensure plugin + index are ready after active project changes.")
    parser.add_argument("--project", default="", help="Path to .uproject. Defaults to shared activeProject.")
    parser.add_argument("--previous-project", default="", help="Previous active project path for unchanged-project fast path.")
    parser.add_argument("--check-only", action="store_true", help="Only report whether plugin/sync work is needed.")
    parser.add_argument("--dry-run", action="store_true", help="Report actions without writing or syncing.")
    parser.add_argument("--force", action="store_true", help="Run plugin install and sync even when checks pass.")
    parser.add_argument("--skip-plugin", action="store_true")
    parser.add_argument("--skip-sync", action="store_true")
    args = parser.parse_args()

    if args.check_only:
        project = resolve_project(args.project)
        payload = active_project_check_status(project)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    payload = ensure_active_project_ready(
        args.project or None,
        previous_project=args.previous_project or None,
        dry_run=args.dry_run,
        force=args.force,
        skip_plugin=args.skip_plugin,
        skip_sync=args.skip_sync,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
