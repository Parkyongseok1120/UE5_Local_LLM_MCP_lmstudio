#!/usr/bin/env python
"""Prove that every exact project selector belongs to one RAG shard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from direct_rag_generation_identity import RagGenerationTransitionError
from direct_rag_manifest_binding import (
    engine_bindings_match,
    read_manifest_generation_identity,
)
from direct_rag_sql import project_root_candidates
from workspace_paths import filesystem_path_identity


def _owned_by(index: Path, descriptor: Path, binding: dict[str, Any]) -> bool:
    try:
        identity = read_manifest_generation_identity(index.parent)
        candidates = project_root_candidates(
            index,
            [descriptor.stem],
            expected_generation=identity["generationId"] or None,
        )
    except RagGenerationTransitionError:
        raise
    except Exception:
        return False
    expected = filesystem_path_identity(descriptor.parent, strip_project_uri=False)
    roots = {
        filesystem_path_identity(root, strip_project_uri=False)
        for root in candidates.get(descriptor.stem, [])
    }
    return expected in roots and engine_bindings_match(
        str(binding.get("engineVersion") or ""),
        str(binding.get("engineAssociation") or ""),
        identity["engineVersion"],
        identity["engineAssociation"],
    )


def resolve_common_project_owner(
    candidates: list[Path],
    descriptors: list[Path],
    bindings: list[dict[str, Any]],
) -> dict[str, Any]:
    owners: list[set[Path]] = []
    for descriptor, binding in zip(descriptors, bindings, strict=True):
        matches = {
            index.resolve()
            for index in candidates
            if index.is_file() and _owned_by(index, descriptor, binding)
        }
        if not matches:
            return {
                "ok": False,
                "errorCode": "PROJECT_SELECTOR_NOT_INDEXED",
                "error": f"The exact project has no current row in an engine-compatible RAG shard: {descriptor}",
                "project": str(descriptor),
            }
        owners.append(matches)
    common = set.intersection(*owners) if owners else set()
    if len(common) == 1:
        return {"ok": True, "index": str(next(iter(common)))}
    all_owners = set().union(*owners) if owners else set()
    if not common:
        manifest_bindings = {
            tuple(read_manifest_generation_identity(index.parent)[key] for key in (
                "engineVersion",
                "engineAssociation",
            ))
            for index in all_owners
        }
        return {
            "ok": False,
            "errorCode": (
                "RAG_MULTI_ENGINE_QUERY_UNSUPPORTED"
                if len(manifest_bindings) > 1
                else "RAG_MULTI_INDEX_QUERY_UNSUPPORTED"
            ),
            "error": "The selected projects do not share one exact RAG index shard.",
            "candidateIndexes": sorted(str(index) for index in all_owners),
        }
    return {
        "ok": False,
        "errorCode": "PROJECT_SELECTOR_AMBIGUOUS",
        "error": "The selected projects are duplicated across multiple RAG shards.",
        "candidateIndexes": sorted(str(index) for index in common),
    }


__all__ = ["resolve_common_project_owner"]
