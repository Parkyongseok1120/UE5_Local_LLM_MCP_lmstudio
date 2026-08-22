#!/usr/bin/env python
"""Exact project selection policy for Direct RAG queries."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from direct_rag_project_selectors import exact_project_descriptor
from direct_rag_sql import project_root_candidates
from project_routing import resolve_project_filters
from rag_types import SearchOptions
from workspace_paths import (
    active_project_names,
    filesystem_path_identity,
    resolve_active_project_path,
)


def project_selectors(value: Any) -> list[str]:
    candidates = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    result: list[str] = []
    for item in candidates:
        selector = str(item or "").strip()
        if selector and selector not in result:
            result.append(selector)
    return result


def indexed_project_filters(selectors: list[str]) -> list[str]:
    """Map exact path selectors to the project names stored in the index."""

    result: list[str] = []
    for selector in selectors:
        descriptor = exact_project_descriptor(selector)
        identities = [descriptor.stem] if descriptor else [selector]
        for identity in identities:
            if identity and identity not in result:
                result.append(identity)
    return result


def indexed_project_root_filters(selectors: list[str]) -> list[str]:
    """Return canonical roots only for selectors that identify one exact project."""

    roots: list[str] = []
    for selector in selectors:
        descriptor = exact_project_descriptor(selector)
        if descriptor is None:
            continue
        identity = filesystem_path_identity(
            descriptor.parent,
            strip_project_uri=False,
        )
        if identity and identity not in roots:
            roots.append(identity)
    return roots


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def exact_project_roots(
    index: Path,
    selectors: list[str],
    *,
    active_project: Path | None,
    use_active: bool,
    expected_generation: str | None = None,
) -> tuple[list[str], list[str]]:
    """Resolve roots and report duplicate-name candidates instead of merging clones."""

    roots = indexed_project_root_filters(selectors)
    ambiguous: list[str] = []
    name_selectors = [
        selector for selector in selectors if exact_project_descriptor(selector) is None
    ]
    if name_selectors:
        identities = indexed_project_filters(name_selectors)
        candidates = project_root_candidates(
            index,
            identities,
            expected_generation=expected_generation,
        )
        for identity in identities:
            matches = candidates.get(identity, [])
            if len(matches) > 1:
                ambiguous.extend(match for match in matches if match not in ambiguous)
            elif len(matches) == 1 and matches[0] not in roots:
                roots.append(matches[0])
        if not roots and not ambiguous:
            # A current-schema index cannot safely bind an unrooted project row.
            roots.append("__unresolved_project_root__")
    elif not selectors and use_active and active_project is not None:
        roots = indexed_project_root_filters([str(active_project)])
    return roots, ambiguous


def search_options(
    index: Path,
    query: str,
    top_k: int,
    arguments: dict[str, Any],
    *,
    workspace: Path | None = None,
    expected_generation: str | None = None,
) -> tuple[SearchOptions, str, list[str], list[str], list[str]]:
    explicit = project_selectors(arguments.get("project"))
    indexed_explicit = indexed_project_filters(explicit)
    active_project = resolve_active_project_path(workspace)
    active_names = (
        [active_project.stem]
        if active_project is not None
        and active_project.is_file()
        and active_project.suffix.casefold() == ".uproject"
        else active_project_names()
    )
    active_path = str(active_project) if active_project is not None else None
    mode = str(arguments.get("mode") or "auto")
    scope = str(arguments.get("scope") or "auto")
    use_active = arguments.get("use_active_project", True) is not False
    projects, resolved_scope = resolve_project_filters(
        query,
        mode,
        indexed_explicit,
        active_names,
        scope=scope,
        use_active_project=use_active,
        active_project_path=active_path,
    )
    query_projects = ["__engine__"] if resolved_scope == "engine" else projects
    roots: list[str] = []
    ambiguous: list[str] = []
    if resolved_scope in {"project", "mixed"}:
        roots, ambiguous = exact_project_roots(
            index,
            explicit,
            active_project=active_project,
            use_active=use_active,
            expected_generation=expected_generation,
        )
        if not projects:
            query_projects = ["__unbound_project__"]
            roots = ["__unresolved_project_root__"]
    return (
        SearchOptions(
            mode=mode,
            sources=_string_list(arguments.get("source")),
            projects=query_projects,
            project_roots=roots,
            layers=_string_list(arguments.get("layer")),
            doc_types=_string_list(arguments.get("doc_type")),
            genres=_string_list(arguments.get("genre")),
            extensions=_string_list(arguments.get("extension")),
            required_terms=_string_list(arguments.get("required_term")),
            candidate_limit=max(120, top_k * 20),
            evidence_only=True,
        ),
        resolved_scope,
        explicit,
        projects or active_names if resolved_scope in {"project", "mixed"} else [],
        ambiguous,
    )


__all__ = [
    "exact_project_roots",
    "indexed_project_filters",
    "indexed_project_root_filters",
    "project_selectors",
    "search_options",
]
