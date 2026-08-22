#!/usr/bin/env python
"""Thin orchestration and CLI for exact-project Unreal Editor exports."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from active_project_paths import resolve_active_project_path
from editor_export_location import resolve_export_dir
from editor_export_markers import (
    DONE_NAME,
    ERROR_NAME,
    REQUEST_NAME,
    build_export_job,
    clear_export_markers,
    submit_export_request,
    wait_for_export_markers,
)
from editor_export_mode import choose_export_mode, execute_export_mode
from editor_export_process import (
    project_editor_running as _project_editor_running,
    resolve_editor_executable,
    run_headless_export as _run_headless_export,
)
from editor_export_project import (
    project_engine_association,
    resolve_project_engine_root as _resolve_project_engine_root,
    resolve_project_file,
)
from editor_export_settings import (
    ExportMode,
    ExportScope,
    editor_export_content_path,
    editor_export_maps_path,
    editor_export_scope,
    editor_export_timeout_sec,
)
from workspace_paths import find_workspace_root, resolve_engine_root_for_association

_clear_markers = clear_export_markers


def project_editor_running(
    uproject: Path,
    host_platform: str | None = None,
) -> bool:
    """Compatibility seam around the focused process inspector."""

    return _project_editor_running(
        uproject,
        host_platform,
        run_process=subprocess.run,
    )


def resolve_project_engine_root(
    uproject: Path,
    workspace: Path,
) -> dict[str, Any]:
    """Resolve only the engine association declared by the exact descriptor."""

    return _resolve_project_engine_root(
        uproject,
        workspace,
        engine_resolver=resolve_engine_root_for_association,
    )


def run_headless_export(
    *,
    uproject: Path,
    engine_root: Path,
    job: dict[str, Any],
    timeout_sec: int,
    log_path: Path | None = None,
) -> dict[str, Any]:
    """Launch one bounded headless export while retaining the public API."""

    return _run_headless_export(
        uproject=uproject,
        engine_root=engine_root,
        job=job,
        timeout_sec=timeout_sec,
        workspace=find_workspace_root(),
        wait_for_markers=wait_for_export_markers,
        run_process=subprocess.run,
        log_path=log_path,
    )


def run_editor_export(
    *,
    export_dir: str | Path | None = None,
    content_path: str | None = None,
    maps_path: str | None = None,
    scope: ExportScope | None = None,
    mode: ExportMode = "auto",
    uproject: str | Path | None = None,
    timeout_sec: int | None = None,
) -> dict[str, Any]:
    workspace = find_workspace_root()
    explicit_project = uproject is not None and bool(str(uproject).strip())
    active = resolve_project_file(
        workspace,
        uproject,
        active_project_resolver=resolve_active_project_path,
    )
    if active is None:
        return {
            "ok": False,
            "error": "No exact .uproject found. Run rag.ps1 set-project or pass --project.",
        }
    association, descriptor_error = project_engine_association(active)
    if descriptor_error:
        return {
            "ok": False,
            "errorCode": "PROJECT_DESCRIPTOR_INVALID",
            "error": descriptor_error,
            "project": str(active),
            "engineAssociation": "",
        }

    resolved_export = resolve_export_dir(
        export_dir,
        project_file=active if explicit_project else None,
    )
    resolved_content = content_path or editor_export_content_path()
    resolved_maps = maps_path or editor_export_maps_path()
    resolved_scope = scope or editor_export_scope()
    resolved_timeout = timeout_sec or editor_export_timeout_sec()
    job = build_export_job(
        export_dir=resolved_export,
        tools_dir=workspace / "tools" / "ue_export",
        content_path=resolved_content,
        maps_path=resolved_maps,
        scope=resolved_scope,
        workspace=workspace,
        project_file=active,
    )

    editor_open = project_editor_running(active)
    chosen_mode = choose_export_mode(mode, editor_open)
    result, engine_root, engine_resolution = execute_export_mode(
        chosen_mode=chosen_mode,
        active_project=active,
        workspace=workspace,
        export_dir=resolved_export,
        job=job,
        timeout_sec=resolved_timeout,
        resolve_engine=resolve_project_engine_root,
        run_headless=run_headless_export,
        submit_request=submit_export_request,
        wait_for_markers=wait_for_export_markers,
    )
    result.update(
        {
            "exportDir": str(resolved_export),
            "contentPath": resolved_content,
            "mapsPath": resolved_maps,
            "scope": resolved_scope,
            "project": str(active),
            "engineRoot": str(engine_root or ""),
            "engineAssociation": association,
            "engineResolutionSource": str(engine_resolution.get("source") or ""),
            "chosenMode": chosen_mode,
            "editorWasRunning": editor_open,
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Unreal Editor metadata export automatically.")
    parser.add_argument("--export-dir", default="")
    parser.add_argument("--content-path", default="")
    parser.add_argument("--maps-path", default="")
    parser.add_argument("--scope", default="", choices=["", "all", "materials", "blueprints"])
    parser.add_argument("--mode", default="auto", choices=["auto", "headless", "request"])
    parser.add_argument("--project", default="")
    parser.add_argument("--timeout-sec", type=int, default=0)
    args = parser.parse_args()
    payload = run_editor_export(
        export_dir=args.export_dir or None,
        content_path=args.content_path or None,
        maps_path=args.maps_path or None,
        scope=args.scope or None,
        mode=args.mode,
        uproject=args.project or None,
        timeout_sec=args.timeout_sec or None,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
