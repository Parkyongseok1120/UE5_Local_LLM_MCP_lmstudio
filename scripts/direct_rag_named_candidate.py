#!/usr/bin/env python
"""Match one project name against one generation-pinned RAG shard."""

from __future__ import annotations

from pathlib import Path

from direct_rag_manifest_binding import (
    engine_bindings_match,
    read_manifest_generation_identity,
)
from direct_rag_project_engine import project_engine_version
from direct_rag_project_selectors import descriptor_for_indexed_root
from direct_rag_sql import project_root_candidates

NamedMatch = tuple[Path, str, Path, str, str]


def match_named_candidate(index: Path, name: str, workspace: Path) -> list[NamedMatch]:
    identity = read_manifest_generation_identity(index.parent)
    roots = project_root_candidates(
        index,
        [name],
        expected_generation=identity["generationId"] or None,
    ).get(name, [])
    matches: list[NamedMatch] = []
    for root in roots:
        descriptor = descriptor_for_indexed_root(root, name)
        if descriptor is None:
            continue
        binding = project_engine_version(descriptor, workspace)
        if binding.get("ok") is not True or not engine_bindings_match(
            str(binding["engineVersion"]),
            str(binding.get("engineAssociation") or ""),
            identity["engineVersion"],
            identity["engineAssociation"],
        ):
            continue
        matches.append(
            (
                index.resolve(),
                root,
                descriptor,
                identity["engineVersion"],
                identity["engineAssociation"],
            )
        )
    return matches


__all__ = ["match_named_candidate"]
