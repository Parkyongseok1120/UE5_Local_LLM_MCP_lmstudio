#!/usr/bin/env python
"""Run the bounded collectors for one exact Direct RAG project generation."""

from __future__ import annotations

import shutil
import sys
import tempfile
from direct_rag_operation import collector_cleanup_safe, run_collector
from collections.abc import Callable
from pathlib import Path
from typing import Any


def run_script(workspace: Path, script: str, *args: str) -> dict[str, Any]:
    cmd = [sys.executable, str(workspace / "scripts" / script), *args]
    scratch = None
    for flag in ("--out-dir", "--out", "--jsonl"):
        if flag in args:
            destination = Path(args[args.index(flag) + 1])
            scratch = destination if flag == "--out-dir" else destination.parent
            break
    return run_collector(cmd, cwd=workspace, scratch=scratch)


def collector_commands(project: Path, stage: Path) -> tuple[tuple[str, ...], ...]:
    project_root = project.parent.resolve()
    source_root = project_root / "Source"
    if not source_root.is_dir():
        source_root = project_root
    return (
        (
            "collect_unreal_projects.py",
            "--out", str(stage / "raw_projects.jsonl"),
            "--root", str(project),
        ),
        (
            "collect_unreal_project_profile.py",
            "--root", str(project),
            "--out", str(stage / "raw_project_profiles.jsonl"),
        ),
        (
            "collect_project_architecture.py",
            "--project", str(project),
            "--out-dir", str(stage / "project_architecture"),
            "--jsonl", str(stage / "raw_project_architecture.jsonl"),
        ),
        (
            "collect_unreal_symbols.py",
            "--root", str(source_root),
            "--out", str(stage / "raw_project_symbols.jsonl"),
            "--tier", "full",
            "--scope", "project",
            "--project-name", project.stem,
            "--project-root", str(project_root),
        ),
    )


def run_project_collectors(
    workspace: Path,
    project: Path,
    stage: Path,
    emit: Callable[[str], None],
) -> tuple[list[dict[str, Any]], str | None]:
    from direct_rag_project_merge import merge_project_collection, stamp_project_collection
    from direct_rag_source_snapshot import source_snapshot

    steps: list[dict[str, Any]] = []
    stage.mkdir(parents=True, exist_ok=True)
    collection = Path(
        tempfile.mkdtemp(prefix=".project-collection-", dir=str(stage))
    ).resolve()
    try:
        before = source_snapshot(project, max_seconds=10.0)
        for script, *arguments in collector_commands(project, collection):
            emit(f"{script} (staged)")
            step = run_script(workspace, script, *arguments)
            steps.append({"name": script, **step})
            if step.get("ok") is not True:
                return steps, script
        after = source_snapshot(project, max_seconds=10.0)
        if before.get("status") == after.get("status") == "complete":
            if before["fingerprint"] != after["fingerprint"]:
                steps.append({"name": "project_source_snapshot", "ok": False,
                              "outputTail": "Project inputs changed during collection; no generation published."})
                return steps, "project_source_snapshot"
            stamp_project_collection(collection, after["fingerprint"])
        merge_project_collection(stage, collection, project.resolve())
        return steps, None
    finally:
        if collector_cleanup_safe(collection):
            shutil.rmtree(collection, ignore_errors=True)


def ingest_editor_snapshot(
    workspace: Path,
    project: Path,
    stage: Path,
    snapshot: Path,
    emit: Callable[[str], None],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    from editor_metadata_status import METADATA_FILES
    from ingest_editor_exports import discover_exports

    exports = discover_exports(snapshot, project_file=project, require_manifest=True)
    expected = tuple(
        dict.fromkeys(
            METADATA_FILES[kind]
            for _path, kind in exports
            if kind in METADATA_FILES
        )
    )
    if not exports or not expected:
        return {
            "ok": False,
            "errorCode": "EDITOR_EXPORTS_NOT_FOUND",
            "error": f"No exact-project Editor exports found under {snapshot}.",
        }, ()
    emit("ingest_editor_exports.py (staged)")
    result = run_script(
        workspace,
        "ingest_editor_exports.py",
        "--export-dir", str(snapshot),
        "--out-dir", str(stage),
        "--project-name", project.stem,
        "--project-root", str(project.parent.resolve()),
        "--project-file", str(project.resolve()),
        "--require-manifest",
    )
    return result, expected


def build_staged_index(
    workspace: Path,
    stage: Path,
    emit: Callable[[str], None],
    engine_version: str,
    engine_association: str,
) -> dict[str, Any]:
    from active_project_paths import indexing_tier

    emit("direct_rag_build_generation.py (staged)")
    return run_script(
        workspace,
        "direct_rag_build_generation.py",
        "--out-dir", str(stage),
        "--workspace", str(workspace),
        "--engine-version", engine_version,
        "--engine-association", engine_association,
        "--indexing-tier", indexing_tier(workspace),
    )


__all__ = [
    "build_staged_index",
    "collector_commands",
    "ingest_editor_snapshot",
    "run_script",
    "run_project_collectors",
]
