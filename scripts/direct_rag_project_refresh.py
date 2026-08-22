#!/usr/bin/env python
"""Collect and promote one exact project's source-backed RAG generation."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from direct_rag_refresh_transaction import (
    BUILD_OUTPUTS,
    COLLECTOR_OUTPUTS,
    RETIRED_MANAGED_FILES,
    commit_refresh_stage,
    discard_refresh_stage,
    prepare_refresh_stage,
    rebase_stage_manifest,
)
from direct_rag_project_collection import (
    build_staged_index,
    ingest_editor_snapshot,
    run_project_collectors,
)


def refresh_project_source_generation(
    *,
    project: Path,
    index_dir: Path,
    workspace: Path,
    progress: Callable[[str], None] | None = None,
    editor_export_dir: Path | None = None,
) -> dict[str, Any]:
    """Build in a same-volume stage; caller owns the index refresh lock."""

    active = project.expanduser().resolve()
    if not active.is_file() or active.suffix.casefold() != ".uproject":
        return {"ok": False, "error": "activeProject is not set or missing"}
    idx = index_dir.expanduser().resolve()
    ws = workspace.expanduser().resolve()
    from direct_rag_project_generation import resolve_project_generation

    engine = resolve_project_generation(active, idx, ws)
    if engine.get("ok") is not True:
        return {
            "ok": False,
            "errorCode": str(engine.get("errorCode") or "PROJECT_ENGINE_VERSION_UNRESOLVED"),
            "error": str(engine.get("error") or "The project engine version could not be resolved."),
            "project": str(active),
            "indexDir": str(idx),
            "stageCommitted": False,
            "steps": [],
        }
    steps: list[dict[str, Any]] = []
    stage: Path | None = None
    expected_editor_raw: tuple[str, ...] = ()

    def emit(message: str) -> None:
        if progress is not None:
            progress(message)

    try:
        stage = prepare_refresh_stage(idx)
        collector_steps, failed_script = run_project_collectors(ws, active, stage, emit)
        steps.extend(collector_steps)
        if failed_script is not None:
            return {
                "ok": False,
                "errorCode": "PROJECT_SOURCE_COLLECT_FAILED",
                "error": f"{failed_script} failed; prior raw inputs and index were preserved.",
                "failedStep": failed_script,
                "project": str(active),
                "indexDir": str(idx),
                "stageCommitted": False,
                "steps": steps,
            }

        from active_project_paths import indexing_tier
        from direct_rag_engine_collection import ensure_engine_inputs
        from direct_rag_engine_tier import engine_tier_prune_files
        from direct_rag_project_collection import run_script

        engine_steps, failed_engine_step = ensure_engine_inputs(
            workspace=ws,
            project=active,
            stage=stage,
            engine_binding=engine,
            run_script=run_script,
            emit=emit,
        )
        steps.extend(engine_steps)
        if failed_engine_step is not None:
            return {
                "ok": False,
                "errorCode": "ENGINE_EVIDENCE_COLLECT_FAILED",
                "error": f"{failed_engine_step} failed; no incomplete engine shard was published.",
                "failedStep": failed_engine_step,
                "project": str(active),
                "indexDir": str(idx),
                "stageCommitted": False,
                "steps": steps,
            }
        if editor_export_dir is not None:
            editor_step, expected_editor_raw = ingest_editor_snapshot(
                ws, active, stage, editor_export_dir, emit
            )
            steps.append({"name": "ingest_editor_exports.py", **editor_step})
            if editor_step.get("ok") is not True:
                return {
                    "ok": False,
                    "errorCode": "EDITOR_METADATA_INGEST_FAILED",
                    "error": "Staged Editor ingest failed; prior generation was preserved.",
                    "project": str(active),
                    "indexDir": str(idx),
                    "stageCommitted": False,
                    "steps": steps,
                }
        else:
            steps.append({
                "name": "sync_editor_metadata",
                "ok": True,
                "skipped": True,
                "reason": "project_source_scope",
            })
        build_step = build_staged_index(
            ws,
            stage,
            emit,
            str(engine["engineVersion"]),
            str(engine.get("engineAssociation") or ""),
        )
        steps.append({"name": "direct_rag_build_generation.py", **build_step})
        if build_step.get("ok") is not True:
            return {
                "ok": False,
                "errorCode": "PROJECT_SOURCE_INDEX_BUILD_FAILED",
                "error": "Staged index build failed; prior raw inputs and index were preserved.",
                "failedStep": "direct_rag_build_generation.py",
                "project": str(active),
                "indexDir": str(idx),
                "stageCommitted": False,
                "steps": steps,
            }

        rebase_stage_manifest(stage, idx)
        required = tuple(
            dict.fromkeys((*COLLECTOR_OUTPUTS, *expected_editor_raw, *BUILD_OUTPUTS))
        )
        commit_refresh_stage(
            stage,
            idx,
            required_files=required,
            prune_files=tuple(
                dict.fromkeys(
                    (*RETIRED_MANAGED_FILES, *engine_tier_prune_files(indexing_tier(ws)))
                )
            ),
        )
        return {
            "ok": True,
            "project": str(active),
            "indexDir": str(idx),
            "stageCommitted": True,
            "steps": steps,
        }
    except Exception as exc:
        return {
            "ok": False,
            "errorCode": "PROJECT_SOURCE_REFRESH_TRANSACTION_FAILED",
            "error": f"Refresh transaction failed; prior index rollback was attempted: {exc}",
            "project": str(active),
            "indexDir": str(idx),
            "stageCommitted": False,
            "steps": steps,
        }
    finally:
        discard_refresh_stage(stage)


__all__ = ["refresh_project_source_generation"]
