#!/usr/bin/env python
"""Symbol lookup capability for the independent Direct RAG server."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from direct_rag_result import CapabilityResult, failure
from direct_rag_generation_boundary import generation_transition_boundary
from direct_rag_evidence import compact_match_refs, factual_rows, format_evidence_rows
from direct_rag_freshness import project_freshness
from direct_rag_limits import detail_limits, next_detail, resolve_detail
from direct_rag_index_registry import resolve_request_index
from direct_rag_selection import (
    exact_project_roots,
    indexed_project_filters,
    project_selectors,
)
from direct_rag_symbol_query import symbol_lookup
from target_resolver import resolve_symbol_target
from workspace_paths import active_project_names, resolve_active_project_path


class SymbolRuntime(Protocol):
    index: Path
    workspace: Path


def _bounded_top_k(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return max(1, min(16, int(value)))
    except (TypeError, ValueError):
        return None


def _selected_project_root(
    selectors: list[str],
    selected_roots: list[str],
    workspace: Path | None,
) -> Path | None:
    if len(selected_roots) == 1 and selected_roots[0] != "__unresolved_project_root__":
        return Path(selected_roots[0])
    if selectors:
        if len(selectors) != 1:
            return None
        candidate = Path(selectors[0]).expanduser()
        if candidate.is_file() and candidate.suffix.casefold() == ".uproject":
            return candidate.resolve().parent
        if candidate.is_dir():
            return candidate.resolve()
        active_names = {name.casefold() for name in active_project_names()}
        if selectors[0].casefold() not in active_names:
            return None
    active = resolve_active_project_path(workspace)
    if active is None:
        return None
    return active.parent if active.suffix.casefold() == ".uproject" else active


def _source_derived(row: dict[str, Any], selected: list[str]) -> bool:
    project = str(row.get("project") or "").strip()
    if not project:
        return False
    if selected and project.casefold() not in {item.casefold() for item in selected}:
        return False
    return str(row.get("source") or "").casefold() in {
        "unreal_symbol",
        "unreal_project_text",
        "project_architecture",
        "project_profile",
    }


@generation_transition_boundary
def symbol_lookup_capability(
    runtime: SymbolRuntime,
    arguments: dict[str, Any],
) -> CapabilityResult:
    query = str(arguments.get("query") or "").strip()
    if not query:
        return failure(
            "INVALID_TOOL_ARGUMENTS",
            "query must be a non-empty string.",
            retry_allowed=True,
        )
    workspace = getattr(runtime, "workspace", Path.cwd())
    index_resolution = resolve_request_index(
        runtime.index,
        workspace,
        project_selector=arguments.get("project"),
        use_active=True,
    )
    if index_resolution.get("ok") is not True:
        return failure(
            str(index_resolution.get("errorCode") or "RAG_ENGINE_INDEX_MISMATCH"),
            str(index_resolution.get("error") or "No compatible Unreal RAG index is available."),
            retry_allowed=index_resolution.get("retryAllowed") is True,
            retry_mode="same_arguments",
            **(
                {"projectRoots": index_resolution["projectRoots"]}
                if index_resolution.get("projectRoots") is not None
                else {}
            ),
            engineIndex={
                key: value
                for key, value in index_resolution.items()
                if key not in {"ok", "errorCode", "error"}
            },
        )
    index = Path(str(index_resolution["index"]))
    expected_generation = str(index_resolution.get("indexGenerationId") or "").strip() or None
    if not index.is_file():
        return failure(
            "RAG_INDEX_MISSING",
            f"RAG index does not exist: {index}",
        )
    requested_top_k = _bounded_top_k(arguments.get("top_k", 8))
    if requested_top_k is None:
        return failure(
            "INVALID_TOOL_ARGUMENTS",
            "top_k must be an integer from 1 through 16.",
            retry_allowed=True,
        )
    detail = resolve_detail(str(arguments.get("detailLevel") or "compact"))
    limits = detail_limits(detail)
    top_k = min(requested_top_k, int(limits["top_k"]))
    selectors = project_selectors(arguments.get("project"))
    indexed_selectors = indexed_project_filters(selectors)
    selected_projects = indexed_selectors or active_project_names()
    active_project = resolve_active_project_path(workspace)
    selected_roots, ambiguous = exact_project_roots(
        index,
        selectors,
        active_project=active_project,
        use_active=True,
        expected_generation=expected_generation,
    )
    if ambiguous:
        return failure(
            "PROJECT_SELECTOR_AMBIGUOUS",
            "The supplied project name matches multiple indexed roots. Use an exact .uproject path.",
            retry_allowed=True,
            projectRoots=ambiguous,
        )
    local_rows = symbol_lookup(
        index,
        query,
        top_k=top_k,
        symbol_kind=str(arguments.get("symbol_kind") or ""),
        projects=selected_projects,
        project_roots=selected_roots,
        expected_generation=expected_generation,
    ) if selected_projects else []
    engine_rows = symbol_lookup(
        index,
        query,
        top_k=top_k,
        symbol_kind=str(arguments.get("symbol_kind") or ""),
        projects=["__engine__"],
        expected_generation=expected_generation,
    )
    combined = [*local_rows, *engine_rows]
    combined.sort(key=lambda row: float(row.get("rank_score") or 0.0))
    seen: set[str] = set()
    unique_rows: list[dict[str, Any]] = []
    for row in combined:
        chunk_id = str(row.get("chunk_id") or "")
        if chunk_id in seen:
            continue
        seen.add(chunk_id)
        unique_rows.append(row)
    rows = factual_rows(unique_rows)[:top_k]
    freshness = project_freshness(
        index,
        search_mode="api_lookup",
        projects=selectors,
        workspace=workspace,
        expected_generation=expected_generation,
    )
    suppressed = 0
    if (
        freshness.get("directSourcePreferred")
        and freshness.get("projectSymbolsFresh") is False
    ):
        kept = [row for row in rows if not _source_derived(row, selected_projects)]
        suppressed = len(rows) - len(kept)
        rows = kept

    project_root = _selected_project_root(selectors, selected_roots, workspace)
    target_resolution = resolve_symbol_target(
        query,
        rows,
        access="read",
        project_root=project_root,
        expected_base_type=str(arguments.get("expectedBaseType") or ""),
        directory_domain=str(arguments.get("directoryDomain") or ""),
    )
    context, truncated = format_evidence_rows(
        rows,
        max_chars=int(limits["assembly_chars"]),
        max_chars_per_row=int(limits["row_chars"]),
    )
    payload: dict[str, Any] = {
        "ok": True,
        "query": query,
        "projects": selectors,
        "matchCount": len(rows),
        "matches": compact_match_refs(rows),
        "evidence": context,
        "targetResolution": target_resolution,
        "detailLevel": detail,
        "indexStaleness": freshness,
        "staleProjectRowsSuppressed": suppressed,
        "indexPath": str(index),
        "engineVersion": index_resolution.get("indexEngineVersion"),
    }
    if truncated:
        payload["nextDetailLevel"] = next_detail(detail)
    if suppressed:
        payload["freshnessAdvisory"] = {
            "status": "cached_project_symbols_excluded",
            "message": (
                f"{suppressed} cached project symbol row(s) were excluded because "
                "their source freshness could not be established."
            ),
        }
    return CapabilityResult(
        payload,
        char_limit=int(limits["max_tool_chars"]),
    )


def capability_handlers() -> dict[str, Any]:
    return {"unreal_symbol_lookup": symbol_lookup_capability}


__all__ = ["capability_handlers", "symbol_lookup_capability"]
