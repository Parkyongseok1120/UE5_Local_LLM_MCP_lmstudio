#!/usr/bin/env python
"""Serialize and atomically publish a portable/manual RAG index build."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from direct_rag_build_generation import build_generation
from direct_rag_manifest_binding import resolve_generation_engine_binding
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
from index_inputs import existing_input_paths

StaleCheck = Callable[[Path, Path, Path], tuple[bool, str]]


def build_public_index(
    *,
    workspace: Path,
    index_dir: Path,
    force: bool,
    stale_check: StaleCheck,
    engine_version: str | None = None,
    engine_association: str | None = None,
    engine_root: str = "",
) -> dict[str, Any]:
    target = index_dir.expanduser().resolve()
    manifest = target / "build_manifest.json"
    sqlite = target / "rag.sqlite"
    stage: Path | None = None
    try:
        with index_refresh_lock(target):
            recovery = recover_interrupted_refresh(target)
            binding = resolve_generation_engine_binding(
                target,
                engine_version=engine_version,
                engine_association=engine_association,
            )
            if binding.get("ok") is not True:
                return binding
            from direct_rag_raw_provenance import validate_raw_provenance

            provenance = validate_raw_provenance(
                index_dir=target,
                workspace=workspace,
                engine_version=str(binding.get("engineVersion") or ""),
                engine_association=str(binding.get("engineAssociation") or ""),
                engine_root=engine_root,
            )
            if provenance.get("ok") is not True:
                return provenance
            stale, reason = stale_check(target, manifest, sqlite)
            if not force and not stale:
                return {
                    "ok": True,
                    "skipped": True,
                    "reason": reason,
                    "recovery": recovery if recovery.get("recovered") else None,
                }
            if not existing_input_paths(target):
                return {
                    "ok": False,
                    "errorCode": "RAG_RAW_INPUTS_MISSING",
                    "error": "No raw input JSONL files were found.",
                    "reason": reason,
                }
            stage = prepare_refresh_stage(target)
            build = build_generation(
                stage,
                workspace,
                engine_version=str(binding.get("engineVersion") or ""),
                engine_association=str(binding.get("engineAssociation") or ""),
            )
            if build.get("ok") is not True:
                return {
                    "ok": False,
                    "errorCode": str(build.get("errorCode") or "RAG_INDEX_BUILD_FAILED"),
                    "error": str(build.get("error") or "The staged RAG build failed."),
                    "reason": reason,
                    "build": build,
                }
            rebase_stage_manifest(stage, target)
            commit_refresh_stage(
                stage,
                target,
                required_files=BUILD_OUTPUTS,
                prune_files=RETIRED_MANAGED_FILES,
            )
            return {
                "ok": True,
                "skipped": False,
                "reason": reason,
                "stageCommitted": True,
                "build": build,
                "recovery": recovery if recovery.get("recovered") else None,
            }
    except DirectRagRefreshBusyError as exc:
        return {
            "ok": False,
            "errorCode": "RAG_REFRESH_BUSY",
            "error": str(exc),
        }
    except Exception as exc:
        return {
            "ok": False,
            "errorCode": "RAG_PUBLIC_BUILD_TRANSACTION_FAILED",
            "error": f"Portable RAG build failed without publishing a partial generation: {exc}",
        }
    finally:
        discard_refresh_stage(stage)


__all__ = ["build_public_index"]
