#!/usr/bin/env python
"""Select one immutable RAG index generation for a project's Unreal version."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from direct_rag_manifest_binding import (
    read_manifest_engine_binding,
    read_manifest_generation_identity,
)
from direct_rag_index_ownership import resolve_common_project_owner
from direct_rag_generation_identity import RagGenerationTransitionError
from direct_rag_project_engine import project_engine_version
from direct_rag_project_selectors import classify_project_selectors
from direct_rag_named_index import resolve_named_index
from direct_rag_request_binding import resolve_request_engine_binding
from direct_rag_shard_selection import (
    candidate_indexes,
    matching_engine_indexes,
    select_existing_index,
)
from direct_rag_unbuilt_shard import resolve_missing_engine_index


def _manifest_binding(index: Path) -> tuple[str, str]:
    return read_manifest_engine_binding(index.parent)


def _manifest_version(index: Path) -> str:
    return _manifest_binding(index)[0]


def _resolve_request_index(
    base_index: Path,
    workspace: Path,
    *,
    project_selector: Any = None,
    use_active: bool = True,
    allow_unbuilt: bool = False,
) -> dict[str, Any]:
    """Return the only index whose manifest matches all exact selected projects."""

    base = base_index.expanduser().resolve()
    classified = classify_project_selectors(
        project_selector,
        workspace,
        use_active=use_active,
    )
    if classified.get("ok") is not True:
        return classified
    descriptors = list(classified["descriptors"])
    names = list(classified["names"])
    named = None
    if names:
        named = resolve_named_index(candidate_indexes(base), names, workspace)
        if named is not None:
            if named.get("ok") is not True:
                return named
            descriptors.extend(Path(value) for value in named.get("projectDescriptors") or [])
    if not descriptors:
        identity = read_manifest_generation_identity(base.parent)
        return {
            "ok": True,
            "index": str(base),
            "projectEngineVersion": None,
            "indexEngineVersion": identity["engineVersion"] or None,
            "indexGenerationId": identity["generationId"] or None,
        }

    binding = resolve_request_engine_binding(
        descriptors,
        workspace,
        project_engine_version,
    )
    if binding.get("ok") is not True:
        return binding
    facts = list(binding["facts"])
    requested = str(binding["requestedVersion"])
    requested_association = str(binding["requestedAssociation"])
    custom_association = str(binding["customAssociation"])
    reported_association = binding.get("reportedAssociation")
    candidates = candidate_indexes(base)
    matches = matching_engine_indexes(candidates, requested, requested_association)
    owned_index: Path | None = None
    if matches:
        ownership = resolve_common_project_owner(matches, descriptors, facts)
        missing_allowed = (
            named is None
            and len(descriptors) == 1
            and ownership.get("errorCode") == "PROJECT_SELECTOR_NOT_INDEXED"
        )
        if ownership.get("ok") is not True and not missing_allowed:
            return ownership
        if ownership.get("ok") is True:
            owned_index = Path(str(ownership["index"])).resolve()
    named_index = Path(str(named["index"])).resolve() if named is not None else None
    choice = select_existing_index(
        base,
        matches,
        owned_index=owned_index,
        named_index=named_index,
        requested_version=requested,
        reported_association=reported_association,
    )
    if choice.get("ok") is not True:
        return choice
    selected = choice.get("index")
    if selected is None:
        current = _manifest_version(base)
        return resolve_missing_engine_index(
            base,
            workspace,
            requested_version=requested,
            reported_association=reported_association,
            custom_association=custom_association,
            current_version=current,
            descriptors=descriptors,
            allow_unbuilt=allow_unbuilt,
        )
    selected = Path(selected)
    identity = read_manifest_generation_identity(selected.parent)
    return {
        "ok": True,
        "index": str(selected),
        "projectEngineVersion": requested,
        "projectEngineAssociation": reported_association,
        "indexEngineVersion": identity["engineVersion"],
        "indexGenerationId": identity["generationId"] or None,
        "projects": [str(path) for path in descriptors],
        "usedSiblingIndex": selected != base,
    }


def resolve_request_index(
    base_index: Path,
    workspace: Path,
    *,
    project_selector: Any = None,
    use_active: bool = True,
    allow_unbuilt: bool = False,
) -> dict[str, Any]:
    try:
        return _resolve_request_index(
            base_index,
            workspace,
            project_selector=project_selector,
            use_active=use_active,
            allow_unbuilt=allow_unbuilt,
        )
    except RagGenerationTransitionError as exc:
        return {
            "ok": False,
            "errorCode": "RAG_GENERATION_TRANSITION",
            "error": str(exc),
            "retryAllowed": True,
        }


__all__ = ["resolve_request_index"]
