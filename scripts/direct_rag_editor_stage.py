#!/usr/bin/env python
"""Transactionally ingest exact-project Unreal Editor exports."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from direct_rag_build_generation import build_generation
from direct_rag_refresh_lock import DirectRagRefreshBusyError, index_refresh_lock
from direct_rag_refresh_transaction import (
    BUILD_OUTPUTS,
    RETIRED_MANAGED_FILES,
    commit_refresh_stage,
    discard_refresh_stage,
    prepare_refresh_stage,
    rebase_stage_manifest,
    recover_interrupted_refresh,
)
from editor_metadata_status import METADATA_FILES
from ingest_editor_exports import discover_exports


def transactional_editor_ingest(
    *,
    workspace: Path,
    index_dir: Path,
    export_dir: Path,
    project: Path,
    rebuild_index: bool,
    reason: str,
) -> dict[str, Any]:
    exact_project = project.expanduser().resolve()
    if not exact_project.is_file() or exact_project.suffix.casefold() != ".uproject":
        return {
            "ok": False,
            "errorCode": "PROJECT_SELECTOR_REQUIRED",
            "error": "Editor metadata ingest requires one exact existing .uproject path.",
            "stageCommitted": False,
        }
    try:
        exports = discover_exports(
            export_dir,
            project_file=exact_project,
            require_manifest=True,
        )
    except (RuntimeError, ValueError) as exc:
        return {
            "ok": False,
            "errorCode": "EDITOR_EXPORT_CONTRACT_INVALID",
            "error": str(exc),
            "stageCommitted": False,
        }
    expected_raw = tuple(
        dict.fromkeys(METADATA_FILES[kind] for _path, kind in exports if kind in METADATA_FILES)
    )
    if not exports or not expected_raw:
        return {
            "ok": False,
            "errorCode": "EDITOR_EXPORTS_NOT_FOUND",
            "error": f"No supported Editor export JSONL files were found under: {export_dir}",
            "stageCommitted": False,
        }

    ws = workspace.expanduser().resolve()
    idx = index_dir.expanduser().resolve()
    from direct_rag_project_engine import project_engine_version

    engine = project_engine_version(exact_project, ws)
    if engine.get("ok") is not True:
        return {**engine, "stageCommitted": False}
    from direct_rag_manifest_binding import resolve_generation_engine_binding

    binding = resolve_generation_engine_binding(
        idx,
        engine_version=str(engine["engineVersion"]),
        engine_association=str(engine.get("engineAssociation") or ""),
    )
    if binding.get("ok") is not True:
        return {**binding, "stageCommitted": False}
    stage: Path | None = None
    try:
        with index_refresh_lock(idx):
            recovery = recover_interrupted_refresh(idx)
            stage = prepare_refresh_stage(idx)
            from direct_rag_project_collection import run_script

            ingest = run_script(
                ws,
                "ingest_editor_exports.py",
                "--export-dir", str(export_dir),
                "--out-dir", str(stage),
                "--project-name", exact_project.stem,
                "--project-root", str(exact_project.parent),
                "--project-file", str(exact_project),
                "--require-manifest",
            )
            if ingest.get("ok") is not True:
                return {
                    "ok": False,
                    "errorCode": "EDITOR_METADATA_INGEST_FAILED",
                    "error": "Staged Editor metadata ingest failed; the prior index was preserved.",
                    "ingest": {"reason": reason, **ingest},
                    "rebuild": None,
                    "stageCommitted": False,
                }

            rebuild: dict[str, Any] | None = None
            required = expected_raw
            if rebuild_index:
                rebuild = build_generation(
                    stage,
                    ws,
                    engine_version=str(engine["engineVersion"]),
                    engine_association=str(engine.get("engineAssociation") or ""),
                )
                if rebuild.get("ok") is not True:
                    return {
                        "ok": False,
                        "errorCode": "EDITOR_METADATA_INDEX_BUILD_FAILED",
                        "error": "Staged Editor metadata build failed; the prior index was preserved.",
                        "ingest": {"reason": reason, **ingest},
                        "rebuild": rebuild,
                        "stageCommitted": False,
                    }
                rebase_stage_manifest(stage, idx)
                required = tuple(dict.fromkeys((*expected_raw, *BUILD_OUTPUTS)))

            commit_refresh_stage(
                stage,
                idx,
                required_files=required,
                prune_files=RETIRED_MANAGED_FILES,
            )
            result = {
                "ok": True,
                "ingest": {"reason": reason, **ingest},
                "rebuild": rebuild,
                "stageCommitted": True,
            }
            if recovery.get("recovered"):
                result["recovery"] = recovery
            return result
    except DirectRagRefreshBusyError as exc:
        return {
            "ok": False,
            "errorCode": "RAG_REFRESH_BUSY",
            "error": str(exc),
            "stageCommitted": False,
        }
    except Exception as exc:
        return {
            "ok": False,
            "errorCode": "EDITOR_METADATA_REFRESH_TRANSACTION_FAILED",
            "error": f"Editor metadata transaction failed; prior index rollback was attempted: {exc}",
            "stageCommitted": False,
        }
    finally:
        discard_refresh_stage(stage)


__all__ = ["transactional_editor_ingest"]
