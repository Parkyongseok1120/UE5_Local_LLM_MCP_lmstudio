#!/usr/bin/env python
"""Index health, rebuild, refresh, and inventory capabilities for Direct RAG."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Protocol

from direct_rag_contract import DIRECT_RAG_TOOL_NAMES
from direct_rag_corpus import corpus_capabilities
from direct_rag_index_registry import resolve_request_index
from direct_rag_result import CapabilityResult, failure, success
from rag_embeddings import embedding_status
from rag_index_ops import index_health, rebuild_status
from workspace_paths import active_project_names, load_shared_config, resolve_active_project_path


def _resolve_active_index(
    runtime: IndexRuntime,
    *,
    allow_unbuilt: bool = False,
) -> dict[str, Any]:
    return resolve_request_index(
        runtime.index,
        runtime.workspace,
        use_active=True,
        allow_unbuilt=allow_unbuilt,
    )


def _index_failure(resolution: dict[str, Any]) -> CapabilityResult:
    return failure(
        str(resolution.get("errorCode") or "RAG_ENGINE_INDEX_MISMATCH"),
        str(resolution.get("error") or "No compatible Unreal RAG index is available."),
        engineIndex={
            key: value
            for key, value in resolution.items()
            if key not in {"ok", "errorCode", "error"}
        },
    )


class IndexRuntime(Protocol):
    index: Path
    workspace: Path

    def notify(self, message: str, level: str = "info") -> None: ...


_DIRECTIVE_FIELDS = frozenset(
    {
        "chatAction",
        "chatMessage",
        "collectHints",
        "nextRequiredAction",
    }
)
_REBUILD_FACT_FIELDS = frozenset(
    {
        "buildManifest",
        "chunkCount",
        "chunksJsonl",
        "executionStatus",
        "indexError",
        "indexExists",
        "indexFile",
        "indexPath",
        "indexReadable",
        "indexReasonCode",
        "indexStatus",
        "lastBuiltAt",
        "layerBreakdown",
        "needsRebuild",
        "rawInputs",
        "reason",
        "sourceBreakdown",
    }
)

def _clean_diagnostics(payload: dict[str, Any]) -> dict[str, Any]:
    clean = {
        key: value
        for key, value in payload.items()
        if key not in _DIRECTIVE_FIELDS
    }
    reason_code = str(clean.pop("errorCode", "") or "").strip()
    if reason_code:
        clean["indexReasonCode"] = reason_code
    return clean


def rag_health(
    runtime: IndexRuntime,
    arguments: dict[str, Any],
) -> CapabilityResult:
    del arguments
    resolution = _resolve_active_index(runtime, allow_unbuilt=True)
    selected_index = Path(str(resolution.get("index") or runtime.index))
    health = _clean_diagnostics(index_health(selected_index))
    configured = str(load_shared_config().get("activeProject") or "").strip()
    resolved_project = resolve_active_project_path(runtime.workspace)
    active = str(resolved_project) if resolved_project is not None else configured
    if not configured:
        binding = "unbound"
    else:
        binding = (
            "bound"
            if resolved_project is not None
            and resolved_project.is_file()
            and resolved_project.suffix.casefold() == ".uproject"
            else "stale"
        )
    health.update(
        {
            "activeProject": active or None,
            "activeProjectNames": active_project_names(),
            "projectBindingStatus": binding,
            "indexPath": str(selected_index),
            "engineCompatibility": {
                "ok": resolution.get("ok") is True,
                **{
                    key: value
                    for key, value in resolution.items()
                    if key not in {"ok", "index"}
                },
            },
        }
    )
    health["corpusCapabilities"] = corpus_capabilities(selected_index)
    try:
        health["embeddings"] = {"status": "ready", **embedding_status(selected_index)}
    except (OSError, sqlite3.Error, ValueError) as exc:
        health["embeddings"] = {
            "status": "unavailable",
            "errorCode": "RAG_EMBEDDING_STATUS_UNAVAILABLE",
            "message": str(exc),
        }
    return success(**{key: value for key, value in health.items() if key != "ok"})


def rag_rebuild_status(
    runtime: IndexRuntime,
    arguments: dict[str, Any],
) -> CapabilityResult:
    del arguments
    resolution = _resolve_active_index(runtime)
    if resolution.get("ok") is not True:
        return _index_failure(resolution)
    status = _clean_diagnostics(rebuild_status(Path(str(resolution["index"]))))
    facts = {
        key: value
        for key, value in status.items()
        if key in _REBUILD_FACT_FIELDS
    }
    return success(**facts)


def rag_refresh(
    runtime: IndexRuntime,
    arguments: dict[str, Any],
) -> CapabilityResult:
    from rag_refresh import project_refresh_facts, refresh_active_project

    scope = str(arguments.get("scope") or "project_source")
    allow_editor_launch = arguments.get("allowEditorLaunch") is True
    resolution = _resolve_active_index(runtime, allow_unbuilt=True)
    if resolution.get("ok") is not True:
        return _index_failure(resolution)
    selected_index = Path(str(resolution["index"]))
    runtime.notify(f"unreal_rag_refresh started (scope={scope})")
    payload = refresh_active_project(
        scope=scope,  # type: ignore[arg-type]
        workspace=runtime.workspace,
        index_path=selected_index,
        force=arguments.get("force") is True,
        allow_editor_launch=allow_editor_launch,
        progress=lambda message: runtime.notify(f"unreal_rag_refresh: {message}"),
    )
    payload = project_refresh_facts(payload)
    runtime.notify("unreal_rag_refresh finished")
    if payload.get("ok") is not True:
        return failure(
            str(payload.get("errorCode") or "RAG_REFRESH_FAILED"),
            str(payload.get("error") or "The active-project RAG refresh failed."),
            retry_allowed=False,
            refresh={key: value for key, value in payload.items() if key != "ok"},
        )
    return success(**{key: value for key, value in payload.items() if key != "ok"})


def rag_capabilities(
    runtime: IndexRuntime,
    arguments: dict[str, Any],
) -> CapabilityResult:
    del arguments
    resolution = _resolve_active_index(runtime)
    selected_index = Path(str(resolution.get("index") or runtime.index))
    status = rebuild_status(selected_index)
    corpus = corpus_capabilities(selected_index) if selected_index.is_file() else {
        "known": False,
        "engineEvidence": None,
        "projectEvidence": None,
    }
    return success(
        server="unreal-rag-direct",
        role="Local Unreal evidence retrieval and active-project binding",
        toolCount=len(DIRECT_RAG_TOOL_NAMES),
        tools=list(DIRECT_RAG_TOOL_NAMES),
        indexPath=str(selected_index),
        engineCompatibility={
            "ok": resolution.get("ok") is True,
            **{
                key: value
                for key, value in resolution.items()
                if key not in {"ok", "index"}
            },
        },
        indexHealthy=(
            int(status.get("chunkCount") or 0) > 0
            and status.get("needsRebuild") is not True
        ),
        corpusCapabilities=corpus,
        engineCorpusReady=corpus.get("engineEvidence"),
        boundaries={
            "provided": ["evidence retrieval", "active-project binding", "index refresh"],
            "excluded": [
                "workflow lifecycle",
                "planning and tool ordering",
                "file mutation",
                "build execution",
                "background jobs",
            ],
        },
    )


def capability_handlers() -> dict[str, Any]:
    return {
        "unreal_rag_health": rag_health,
        "unreal_rag_rebuild_status": rag_rebuild_status,
        "unreal_rag_refresh": rag_refresh,
        "unreal_rag_capabilities": rag_capabilities,
    }


__all__ = [
    "capability_handlers",
    "rag_capabilities",
    "rag_health",
    "rag_rebuild_status",
    "rag_refresh",
]
