#!/usr/bin/env python
"""Shared public/runtime input bounds for Direct RAG retrieval calls."""

from __future__ import annotations

import json
from typing import Any


MAX_QUERY_CHARS = 4_096
MAX_PROJECT_SELECTOR_CHARS = 4_096
MAX_PROJECT_SELECTORS = 16
MAX_FILTER_ITEMS = 64
MAX_FILTER_VALUE_CHARS = 512
MAX_SYMBOL_HINT_CHARS = 512
MAX_REPEAT_RECEIPT_CHARS = 256
MIN_RESPONSE_METADATA_RESERVE_CHARS = 768

SEARCH_STRING_LIMITS = {
    "query": MAX_QUERY_CHARS,
    "repeatReceipt": MAX_REPEAT_RECEIPT_CHARS,
}
SEARCH_LIST_LIMITS = {
    field: (MAX_FILTER_ITEMS, MAX_FILTER_VALUE_CHARS)
    for field in (
        "source",
        "layer",
        "doc_type",
        "genre",
        "extension",
        "required_term",
    )
}
SYMBOL_STRING_LIMITS = {
    "query": MAX_QUERY_CHARS,
    "symbol_kind": MAX_SYMBOL_HINT_CHARS,
    "expectedBaseType": MAX_SYMBOL_HINT_CHARS,
    "directoryDomain": MAX_SYMBOL_HINT_CHARS,
}


def bounded_string_schema(max_chars: int, *, min_length: int = 0) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "string", "maxLength": max_chars}
    if min_length:
        schema["minLength"] = min_length
    return schema


def project_selector_schema() -> dict[str, Any]:
    item = bounded_string_schema(MAX_PROJECT_SELECTOR_CHARS, min_length=1)
    return {
        "oneOf": [
            dict(item),
            {
                "type": "array",
                "items": dict(item),
                "minItems": 1,
                "maxItems": MAX_PROJECT_SELECTORS,
            },
        ],
        "description": (
            "Exact project name/path selector(s); when supplied, the active project is not substituted. "
            "Combined exact identities must fit the selected detail transport budget and are never clipped."
        ),
    }


def bounded_filter_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "items": bounded_string_schema(MAX_FILTER_VALUE_CHARS),
        "maxItems": MAX_FILTER_ITEMS,
    }


def _bounded_string_error(value: Any, field: str, max_chars: int) -> str | None:
    if not isinstance(value, str):
        return f"{field} must be a string."
    if len(value) > max_chars:
        return f"{field} exceeds the {max_chars}-character limit."
    return None


def _project_selector_error(value: Any) -> str | None:
    selectors = [value] if isinstance(value, str) else value if isinstance(value, list) else None
    if selectors is None:
        return "project must be one string or an array of strings."
    if not selectors:
        return "project must contain at least one selector."
    if len(selectors) > MAX_PROJECT_SELECTORS:
        return f"project exceeds the {MAX_PROJECT_SELECTORS}-selector limit."
    for selector in selectors:
        error = _bounded_string_error(
            selector,
            "project selector",
            MAX_PROJECT_SELECTOR_CHARS,
        )
        if error:
            return error
        if not selector.strip():
            return "project selector must be non-empty."
    return None


def _project_selector_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    return list(value) if isinstance(value, list) else []


def _transport_identity_error(
    arguments: dict[str, Any],
    *,
    capability: str,
    transport_limit: int,
) -> str | None:
    selectors = _project_selector_values(arguments.get("project"))
    query = str(arguments.get("query") or "")
    if capability == "search":
        projection = {
            "query": query,
            "projects": selectors,
            "selectedProjects": selectors,
            "indexStaleness": {"projectSelectors": selectors},
            "repeatReceipt": str(arguments.get("repeatReceipt") or ""),
        }
    else:
        projection = {
            "query": query,
            "projects": selectors,
            "indexStaleness": {"projectSelectors": selectors},
        }
    rendered = json.dumps(projection, ensure_ascii=False, indent=2)
    if len(rendered) + MIN_RESPONSE_METADATA_RESERVE_CHARS <= transport_limit:
        return None
    return (
        "Combined query and exact project selectors exceed the selected detail transport "
        "budget; shorten the query, use fewer selectors, or request a larger detail level."
    )


def rag_request_bound_error(
    arguments: Any,
    *,
    capability: str,
    transport_limit: int | None = None,
) -> str | None:
    """Match public schema limits when handlers are invoked without MCP dispatch."""

    if not isinstance(arguments, dict):
        return "Tool arguments must be a JSON object."
    if capability == "search":
        string_limits = SEARCH_STRING_LIMITS
        list_limits = SEARCH_LIST_LIMITS
    elif capability == "symbol":
        string_limits = SYMBOL_STRING_LIMITS
        list_limits = {}
    else:
        raise ValueError(f"Unsupported Direct RAG bounds capability: {capability}")

    for field, max_chars in string_limits.items():
        if field not in arguments:
            continue
        error = _bounded_string_error(arguments[field], field, max_chars)
        if error:
            return error
    if "project" in arguments:
        error = _project_selector_error(arguments["project"])
        if error:
            return error
    for field, (max_items, max_chars) in list_limits.items():
        if field not in arguments:
            continue
        values = arguments[field]
        if not isinstance(values, list):
            return f"{field} must be an array of strings."
        if len(values) > max_items:
            return f"{field} exceeds the {max_items}-item limit."
        for value in values:
            error = _bounded_string_error(value, f"{field} item", max_chars)
            if error:
                return error
    if transport_limit is not None:
        return _transport_identity_error(
            arguments,
            capability=capability,
            transport_limit=max(1, int(transport_limit)),
        )
    return None


__all__ = [
    "MAX_FILTER_ITEMS",
    "MAX_FILTER_VALUE_CHARS",
    "MAX_PROJECT_SELECTOR_CHARS",
    "MAX_PROJECT_SELECTORS",
    "MAX_QUERY_CHARS",
    "MAX_REPEAT_RECEIPT_CHARS",
    "MAX_SYMBOL_HINT_CHARS",
    "MIN_RESPONSE_METADATA_RESERVE_CHARS",
    "bounded_filter_schema",
    "bounded_string_schema",
    "project_selector_schema",
    "rag_request_bound_error",
]
