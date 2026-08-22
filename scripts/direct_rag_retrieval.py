#!/usr/bin/env python
"""Project-scoped retrieval mechanics shared by Direct search capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from direct_rag_evidence import factual_rows, format_evidence_rows
from direct_rag_freshness import project_freshness
from direct_rag_lexical import hybrid_search, lexical_search
from direct_rag_limits import detail_limits, resolve_detail
from direct_rag_selection import search_options
from rag_types import SearchOptions
from workspace_paths import (
    ascii_windows_fold,
    filesystem_path_identity,
)

_ENGINE_PROJECTS = frozenset({"", "engine", "__engine__"})
_PROJECT_LAYERS = frozenset(
    {"unreal_symbol", "project_architecture", "project_profile", "project_text"}
)
_PROJECT_SOURCES = frozenset(
    {"unreal_symbol", "project_architecture", "project_profile", "unreal_project_text"}
)


@dataclass(frozen=True)
class RetrievalPage:
    rows: list[dict[str, Any]]
    context: str
    resolved_scope: str
    detail_level: str
    freshness: dict[str, Any]
    explicit_projects: list[str]
    selected_projects: list[str]
    stale_rows_suppressed: int
    truncated: bool


def _is_project_source_row(
    row: dict[str, Any],
    selected_projects: list[str],
) -> bool:
    project = str(row.get("project") or "").strip()
    if not project or ascii_windows_fold(project) in _ENGINE_PROJECTS:
        return False
    selected = {
        filesystem_path_identity(item)
        for item in selected_projects
        if str(item).strip()
    }
    if selected and filesystem_path_identity(project) not in selected:
        return False
    layer = str(row.get("layer") or "").strip().casefold()
    source = str(row.get("source") or "").strip().casefold()
    doc_type = str(row.get("doc_type") or "").strip().casefold()
    return (
        layer in _PROJECT_LAYERS
        or source in _PROJECT_SOURCES
        or doc_type
        in {"project_symbol", "project_architecture", "project_profile", "project_text"}
    )


def _mark_other_projects(
    rows: list[dict[str, Any]],
    selected_projects: list[str],
) -> list[dict[str, Any]]:
    selected = {
        filesystem_path_identity(item)
        for item in selected_projects
        if str(item).strip()
    }
    marked: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        project = str(item.get("project") or "").strip()
        if (
            project
            and ascii_windows_fold(project) not in _ENGINE_PROJECTS
            and selected
            and filesystem_path_identity(project) not in selected
        ):
            item["otherProject"] = True
        marked.append(item)
    return marked


def retrieve(
    index: Path,
    query: str,
    top_k: int,
    arguments: dict[str, Any],
    *,
    workspace: Path | None = None,
    expected_generation: str | None = None,
) -> RetrievalPage:
    detail = resolve_detail(str(arguments.get("detailLevel") or "compact"))
    limits = detail_limits(detail)
    top_k = min(top_k, int(limits["top_k"]))
    options, scope, explicit, selected, ambiguous = search_options(
        index,
        query,
        top_k,
        arguments,
        workspace=workspace,
        expected_generation=expected_generation,
    )
    freshness = (
        {
            "ok": True,
            "reason": "engine_scope",
            "freshnessScope": "engine",
            "indexUsable": index.is_file(),
            "directSourcePreferred": False,
            "refreshRecommended": False,
            "refreshRequired": not index.is_file(),
        }
        if scope == "engine"
        else project_freshness(
            index,
            search_mode=str(arguments.get("mode") or "auto"),
            projects=explicit,
            workspace=workspace,
            expected_generation=expected_generation,
        )
    )
    suppress_project_source = bool(
        freshness.get("directSourcePreferred")
        and (
            freshness.get("projectSymbolsFresh") is False
            or freshness.get("architectureFresh") is False
        )
    )
    suppressed = 0

    if ambiguous:
        return RetrievalPage(
            rows=[],
            context="The project name matches multiple indexed project roots; use an exact .uproject path.",
            resolved_scope="project_ambiguous",
            detail_level=detail,
            freshness=freshness,
            explicit_projects=explicit,
            selected_projects=ambiguous,
            stale_rows_suppressed=0,
            truncated=False,
        )

    def keep_fresh(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        nonlocal suppressed
        if not suppress_project_source:
            return rows
        kept = [row for row in rows if not _is_project_source_row(row, selected)]
        suppressed += len(rows) - len(kept)
        return kept

    run = hybrid_search if arguments.get("hybrid") is True else lexical_search
    if scope == "mixed" and options.projects:
        local_rows = factual_rows(
            keep_fresh(
                run(
                    index,
                    query,
                    top_k,
                    options,
                    expected_generation=expected_generation,
                )
            )
        )
        engine_options = SearchOptions(
            mode=options.mode,
            sources=options.sources,
            projects=["__engine__"],
            project_roots=[],
            layers=options.layers,
            doc_types=options.doc_types,
            genres=options.genres,
            extensions=options.extensions,
            required_terms=options.required_terms,
            candidate_limit=options.candidate_limit,
            evidence_only=True,
        )
        engine_rows = factual_rows(
            run(
                index,
                query,
                top_k,
                engine_options,
                expected_generation=expected_generation,
            )
        )
        seen = {str(row.get("chunk_id") or "") for row in local_rows}
        rows = list(local_rows)
        rows.extend(
            row
            for row in engine_rows
            if str(row.get("chunk_id") or "") not in seen
        )
        context, truncated = format_evidence_rows(
            rows,
            max_chars=int(limits["assembly_chars"]),
            max_chars_per_row=int(limits["row_chars"]),
        )
    else:
        rows = factual_rows(
            keep_fresh(
                run(
                    index,
                    query,
                    top_k,
                    options,
                    expected_generation=expected_generation,
                )
            )
        )
        if scope != "engine" and not rows and options.projects:
            scope = "project_miss"
            context = "No matching evidence was found for the exact project selector(s)."
            truncated = False
        else:
            context, truncated = format_evidence_rows(
                rows,
                max_chars=int(limits["assembly_chars"]),
                max_chars_per_row=int(limits["row_chars"]),
            )
    return RetrievalPage(
        rows=_mark_other_projects(rows, selected),
        context=context,
        resolved_scope=scope,
        detail_level=detail,
        freshness=freshness,
        explicit_projects=explicit,
        selected_projects=selected,
        stale_rows_suppressed=suppressed,
        truncated=truncated,
    )


__all__ = [
    "RetrievalPage",
    "retrieve",
]
