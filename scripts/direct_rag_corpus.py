#!/usr/bin/env python
"""Read factual engine/project corpus coverage from one generation manifest."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from direct_rag_generation_identity import read_consistent_index_manifest


def corpus_capabilities(
    index: Path,
    *,
    expected_generation: str | None = None,
) -> dict[str, Any]:
    payload = read_consistent_index_manifest(
        index,
        expected_generation=expected_generation,
    )
    raw = payload.get("corpusCapabilities")
    if not isinstance(raw, dict):
        return {
            "known": False,
            "engineEvidence": None,
            "projectEvidence": None,
        }
    return {
        "known": True,
        "engineEvidence": raw.get("engineEvidence") is True,
        "engineEvidenceChunks": int(raw.get("engineEvidenceChunks") or 0),
        "projectEvidence": raw.get("projectEvidence") is True,
        "projectEvidenceChunks": int(raw.get("projectEvidenceChunks") or 0),
    }


def engine_corpus_error(
    index: Path,
    scope: str,
    *,
    expected_generation: str | None = None,
) -> dict[str, Any] | None:
    capabilities = corpus_capabilities(
        index,
        expected_generation=expected_generation,
    )
    if scope not in {"engine", "mixed"} or capabilities.get("engineEvidence") is not False:
        return None
    return {
        "errorCode": "RAG_ENGINE_CORPUS_UNAVAILABLE",
        "error": "This engine-bound RAG shard contains project evidence but no engine corpus.",
        "corpusCapabilities": capabilities,
    }


__all__ = ["corpus_capabilities", "engine_corpus_error"]
