#!/usr/bin/env python
"""Sync RAG raw inputs and index for the shared active Unreal project."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sync_editor_metadata import refresh_editor_metadata, sync_editor_metadata
from workspace_paths import (
    auto_editor_export_enabled,
    editor_export_dir,
    find_workspace_root,
    load_shared_config,
    resolve_active_project_path,
    resolve_index_dir,
)


def _run_script(workspace: Path, script: str, *args: str) -> dict[str, Any]:
    cmd = [sys.executable, str(workspace / "scripts" / script), *args]
    proc = subprocess.run(cmd, cwd=str(workspace), text=True, capture_output=True, check=False)
    output = (proc.stdout or "") + (proc.stderr or "")
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "command": cmd,
        "outputTail": output[-2000:] if output else "",
    }


def sync_active_project(
    *,
    project: Path | None = None,
    index_dir: Path | None = None,
    workspace: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    def _progress(message: str) -> None:
        if progress is not None:
            progress(message)

    workspace = workspace or find_workspace_root()
    active = project or resolve_active_project_path()
    if not active or not active.is_file():
        return {"ok": False, "error": "activeProject is not set or missing"}

    active = active.resolve()
    project_root = active.parent.resolve()
    project_name = active.stem
    source_root = project_root / "Source"
    if not source_root.is_dir():
        source_root = project_root

    idx = (index_dir or resolve_index_dir()).resolve()
    workspace = workspace.resolve()
    try:
        data_rel_text = str(idx.relative_to(workspace)).replace("\\", "/")
    except ValueError:
        data_rel_text = str(idx).replace("\\", "/")

    def _step_name(command: list[str]) -> str:
        if len(command) < 2:
            return "unknown"
        return Path(command[1]).name

    steps: list[dict[str, Any]] = []
    ok = True

    for step in (
        _run_script(workspace, "collect_unreal_projects.py", "--out", f"{data_rel_text}/raw_projects.jsonl", "--root", str(project_root)),
        _run_script(
            workspace,
            "collect_unreal_project_profile.py",
            "--root",
            str(project_root),
            "--out",
            f"{data_rel_text}/raw_project_profiles.jsonl",
        ),
        _run_script(
            workspace,
            "collect_project_architecture.py",
            "--project",
            str(active),
            "--out-dir",
            f"{data_rel_text}/project_architecture",
            "--jsonl",
            f"{data_rel_text}/raw_project_architecture.jsonl",
        ),
    ):
        steps.append({"name": _step_name(step["command"]), **step})
        ok = ok and step["ok"]

    symbols_path = idx / "raw_project_symbols.jsonl"
    if source_root.is_dir():
        _progress("collect_unreal_symbols.py (project source scan)")
        if symbols_path.is_file():
            symbols_path.unlink()
        symbol_step = _run_script(
            workspace,
            "collect_unreal_symbols.py",
            "--root",
            str(source_root),
            "--out",
            f"{data_rel_text}/raw_project_symbols.jsonl",
            "--tier",
            "full",
            "--scope",
            "project",
            "--project-name",
            project_name,
        )
        steps.append({"name": "collect_unreal_symbols.py", **symbol_step})
        ok = ok and symbol_step["ok"]

    config = load_shared_config()
    export_path = editor_export_dir()
    export_dir_text = str(export_path) if export_path else ""
    editor_step: dict[str, Any] = {"name": "sync_editor_metadata", "ok": True, "skipped": True}
    if auto_editor_export_enabled(config) and active.is_file():
        editor_result = refresh_editor_metadata(
            export_dir=export_dir_text or None,
            index_dir=idx,
            project_name=project_name,
            rebuild_index=False,
            content_path=str(config.get("editorExportContentPath") or "") or None,
        )
        editor_step = {"name": "sync_editor_metadata", "ok": bool(editor_result.get("ok", True)), "result": editor_result}
    elif export_path and export_path.is_dir():
        editor_result = sync_editor_metadata(
            export_dir=export_path,
            index_dir=idx,
            project_name=project_name,
            rebuild_index=False,
            force_ingest=False,
            auto_export=False,
        )
        editor_step = {"name": "sync_editor_metadata", "ok": bool(editor_result.get("ok", True)), "result": editor_result}
    steps.append(editor_step)
    if not editor_step.get("skipped") and not editor_step.get("ok", True):
        ok = False

    for step_name in ("incremental_build.py", "warm_symbol_cache.py"):
        _progress(step_name)
        step = _run_script(workspace, step_name, "--out-dir", data_rel_text) if step_name == "incremental_build.py" else _run_script(workspace, step_name)
        steps.append({"name": step_name, **step})
        if step_name == "incremental_build.py" and not step["ok"]:
            ok = False

    return {
        "ok": ok,
        "project": str(active),
        "indexDir": str(idx),
        "steps": steps,
    }


def main() -> int:
    payload = sync_active_project()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
