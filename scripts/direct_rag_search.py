#!/usr/bin/env python
"""Search capability for the independent Direct RAG server."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from direct_rag_delivery import deliver
from direct_rag_generation_boundary import generation_transition_boundary
from direct_rag_result import CapabilityResult, configured_result_limit, failure
from direct_rag_evidence import (
    compact_match_refs,
    evidence_metadata_fits,
    fit_evidence_payload,
)
from direct_rag_limits import detail_limits, next_detail
from direct_rag_request_bounds import rag_request_bound_error
from direct_rag_index_registry import resolve_request_index
from direct_rag_retrieval import retrieve
from direct_rag_selection import project_selectors
from rag_modes import MODE_ENUM
from workspace_paths import resolve_active_project_path


class SearchRuntime(Protocol):
    index: Path
    workspace: Path


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return max(minimum, min(maximum, parsed))


def _freshness_advisory(
    page: Any,
) -> dict[str, Any] | None:
    if page.resolved_scope == "project_miss":
        return {
            "status": "project_match_unavailable",
            "message": (
                "No indexed match was available for the exact project selector. "
                "Current project source remains the authoritative implementation evidence."
            ),
        }
    if page.stale_rows_suppressed:
        return {
            "status": "cached_project_source_excluded",
            "message": (
                f"{page.stale_rows_suppressed} cached project-source row(s) were excluded "
                "because their freshness could not be established."
            ),
        }
    if page.freshness.get("refreshRecommended"):
        return {
            "status": "refresh_available",
            "message": str(
                page.freshness.get("reason")
                or "The selected project's cached index inputs may be stale."
            ),
        }
    return None


@generation_transition_boundary
def rag_search(
    runtime: SearchRuntime,
    arguments: dict[str, Any],
) -> CapabilityResult:
    request_limits = detail_limits(str(arguments.get("detailLevel") or "compact"))
    request_limit = min(
        int(request_limits["max_tool_chars"]),
        configured_result_limit(),
    )
    bound_error = rag_request_bound_error(
        arguments,
        capability="search",
        transport_limit=request_limit,
    )
    if bound_error:
        return failure(
            "INVALID_TOOL_ARGUMENTS",
            bound_error,
            retry_allowed=True,
        )
    query = str(arguments.get("query") or "").strip()
    if not query:
        return failure(
            "INVALID_TOOL_ARGUMENTS",
            "query must be a non-empty string.",
            retry_allowed=True,
        )
    index_resolution = resolve_request_index(
        runtime.index,
        getattr(runtime, "workspace", Path.cwd()),
        project_selector=arguments.get("project"),
        use_active=arguments.get("use_active_project", True) is not False,
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
    from direct_rag_corpus import engine_corpus_error

    corpus_error = engine_corpus_error(
        index,
        str(arguments.get("scope") or "auto"),
        expected_generation=expected_generation,
    )
    if corpus_error is not None:
        return failure(
            str(corpus_error["errorCode"]),
            str(corpus_error["error"]),
            corpusCapabilities=corpus_error["corpusCapabilities"],
        )
    top_k = _bounded_int(arguments.get("top_k", 6), 6, 1, 16)
    if top_k is None:
        return failure(
            "INVALID_TOOL_ARGUMENTS",
            "top_k must be an integer from 1 through 16.",
            retry_allowed=True,
        )
    mode = str(arguments.get("mode") or "auto")
    if mode not in MODE_ENUM:
        return failure(
            "INVALID_TOOL_ARGUMENTS",
            f"Unsupported mode: {mode}",
            retry_allowed=True,
        )

    detail = str(arguments.get("detailLevel") or "compact")
    scope = str(arguments.get("scope") or "auto")
    explicit = project_selectors(arguments.get("project"))
    active_project = resolve_active_project_path(getattr(runtime, "workspace", None))
    active = str(active_project or "")
    repeat_receipt = str(arguments.get("repeatReceipt") or "").strip()
    preflight = deliver(
        tool="unreal_rag_search",
        active_project=active,
        query=query,
        mode=mode,
        scope=scope,
        detail_level=detail,
        top_k=top_k,
        hybrid=arguments.get("hybrid") is True,
        index_path=index,
        rows=None,
        repeat_receipt=repeat_receipt,
        projects=explicit,
    )
    if preflight.get("suppressed"):
        duplicate_payload = {
            "ok": True,
            "duplicate": True,
            "status": "no_new_information",
            "message": (
                "The supplied repeat receipt matches this query and current index state; "
                "the prior evidence is unchanged."
            ),
            "projects": explicit,
            "repeatReceipt": repeat_receipt,
        }
        duplicate_limit = min(2_000, configured_result_limit())
        if not evidence_metadata_fits(
            duplicate_payload,
            max_chars=duplicate_limit,
            reserve_chars=0,
        ):
            return failure(
                "INVALID_TOOL_ARGUMENTS",
                "Project selectors exceed the duplicate response metadata budget.",
                retry_allowed=True,
            )
        return CapabilityResult(duplicate_payload, char_limit=duplicate_limit)

    page = retrieve(
        index,
        query,
        top_k,
        arguments,
        workspace=getattr(runtime, "workspace", None),
        expected_generation=expected_generation,
    )
    if page.resolved_scope == "project_ambiguous":
        return failure(
            "PROJECT_SELECTOR_AMBIGUOUS",
            "The supplied project name matches multiple indexed roots. Use an exact .uproject path.",
            retry_allowed=True,
            projectRoots=page.selected_projects,
        )
    limits = detail_limits(page.detail_level)
    limit = min(int(limits["max_tool_chars"]), configured_result_limit())
    match_refs = compact_match_refs(
        page.rows,
        max_chars=min(int(limits["match_chars"]), max(512, limit // 3)),
    )
    match_metadata_truncated = len(match_refs) < len(page.rows)
    payload: dict[str, Any] = {
        "ok": True,
        "query": query,
        "scope": page.resolved_scope,
        "projects": page.explicit_projects,
        "selectedProjects": page.selected_projects,
        "matchCount": len(page.rows),
        "matches": match_refs,
        "evidence": page.context,
        "detailLevel": page.detail_level,
        "repeatReceipt": "0" * 32,
        "indexStaleness": page.freshness,
        "staleProjectRowsSuppressed": page.stale_rows_suppressed,
        "indexPath": str(index),
        "engineVersion": index_resolution.get("indexEngineVersion"),
    }
    if match_metadata_truncated:
        payload["matchMetadataTruncated"] = True
    if page.truncated or match_metadata_truncated:
        payload["nextDetailLevel"] = next_detail(page.detail_level)
    advisory = _freshness_advisory(page)
    if advisory:
        payload["freshnessAdvisory"] = advisory
    if not evidence_metadata_fits(payload, max_chars=limit):
        return failure(
            "INVALID_TOOL_ARGUMENTS",
            (
                "Query, project selectors, or requested match metadata exceed the selected "
                "detail transport budget; shorten them or lower top_k."
            ),
            retry_allowed=True,
        )
    delivery = deliver(
        tool="unreal_rag_search",
        active_project=active,
        query=query,
        mode=mode,
        scope=scope,
        detail_level=page.detail_level,
        top_k=top_k,
        hybrid=arguments.get("hybrid") is True,
        index_path=index,
        rows=page.rows,
        repeat_receipt=repeat_receipt,
        projects=explicit,
    )
    payload["repeatReceipt"] = delivery.get("repeatReceipt")
    payload, envelope_truncated = fit_evidence_payload(payload, max_chars=limit)
    if envelope_truncated:
        payload["evidenceEnvelopeTruncated"] = True
        next_level = next_detail(page.detail_level)
        if next_level:
            payload["nextDetailLevel"] = next_level
        payload, _ = fit_evidence_payload(payload, max_chars=limit)
    return CapabilityResult(
        payload,
        char_limit=limit,
        rollback_delivery_key=str(delivery.get("deliveryVariantKey") or ""),
    )


def capability_handlers() -> dict[str, Any]:
    return {"unreal_rag_search": rag_search}


__all__ = ["capability_handlers", "rag_search"]
