#!/usr/bin/env python
"""Static eight-tool contract for the default Direct RAG server.

This module is deliberately data-only.  It must stay independent from the
legacy Strict server, task lifecycle, route policy, and capability handlers.
"""

from __future__ import annotations

from typing import Any

from direct_rag_request_bounds import (
    MAX_QUERY_CHARS, MAX_REPEAT_RECEIPT_CHARS, MAX_SYMBOL_HINT_CHARS,
    bounded_filter_schema, bounded_string_schema, max_chars_schema, project_selector_schema,
)
from direct_rag_chunk_read import MAX_CHUNK_ID_CHARS
from rag_modes import MODE_ENUM

DIRECT_RAG_TOOL_NAMES: tuple[str, ...] = (
    "unreal_get_active_project",
    "unreal_set_active_project",
    "unreal_rag_search",
    "unreal_symbol_lookup",
    "unreal_rag_health",
    "unreal_rag_rebuild_status",
    "unreal_rag_refresh",
    "unreal_rag_capabilities",
)

# Domain classification remains with the catalog; transport receives a predicate.
DIRECT_RAG_MUTATING_TOOL_NAMES = frozenset({"unreal_set_active_project", "unreal_rag_refresh"})


def _schema(
    properties: dict[str, Any],
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = list(required)
    schema["additionalProperties"] = False
    return schema


def direct_rag_tool_definitions() -> list[dict[str, Any]]:
    """Return a fresh, process-stable Direct catalog."""

    project_selector = project_selector_schema()
    detail_level = {
        "type": "string",
        "enum": ["compact", "medium", "large", "full"],
        "default": "compact",
        "description": "Raise after truncation.",
    }
    definitions = [
        {
            "name": "unreal_get_active_project",
            "description": "Shared active .uproject, paths.",
            "inputSchema": _schema({}),
        },
        {
            "name": "unreal_set_active_project",
            "description": "Set or clear the shared active .uproject.",
            "inputSchema": _schema(
                {
                    "projectPath": {"type": "string"},
                    "clear": {"type": "boolean", "default": False},
                }
            ),
        },
        {
            "name": "unreal_rag_search",
            "description": "Ranked index evidence; not a preflight: a known file's direct read is authoritative. chunkId pages a match.",
            "inputSchema": _schema(
                {
                    "query": bounded_string_schema(MAX_QUERY_CHARS, min_length=1),
                    "chunkId": bounded_string_schema(MAX_CHUNK_ID_CHARS),
                    "offset": {"type": "integer"},
                    "top_k": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 16,
                        "default": 6,
                    },
                    "mode": {
                        "type": "string",
                        "enum": list(MODE_ENUM),
                        "default": "auto",
                        "description": "review for code review; auto when unsure.",
                    },
                    "hybrid": {"type": "boolean", "default": False},
                    "source": bounded_filter_schema(),
                    "project": project_selector,
                    "layer": bounded_filter_schema(),
                    "doc_type": bounded_filter_schema(),
                    "genre": bounded_filter_schema(),
                    "extension": bounded_filter_schema(),
                    "required_term": bounded_filter_schema(),
                    "scope": {
                        "type": "string",
                        "enum": ["auto", "engine", "project", "mixed"],
                        "default": "auto",
                        "description": "project needs an exact project selector.",
                    },
                    "use_active_project": {"type": "boolean", "default": True},
                    "detailLevel": detail_level,
                    "maxChars": max_chars_schema(),
                    "repeatReceipt": {
                        **bounded_string_schema(MAX_REPEAT_RECEIPT_CHARS),
                        "description": "Echo for unchanged reply.",
                    },
                },
            ),
        },
        {
            "name": "unreal_symbol_lookup",
            "description": (
                "Unreal symbol declarations; sourceMode=engine_local reads engine headers without an index. "
                "Gaps never prove absence."
            ),
            "inputSchema": _schema(
                {
                    "query": bounded_string_schema(MAX_QUERY_CHARS, min_length=1),
                    "top_k": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 16,
                        "default": 8,
                    },
                    "symbol_kind": bounded_string_schema(MAX_SYMBOL_HINT_CHARS),
                    "project": project_selector,
                    "expectedBaseType": bounded_string_schema(MAX_SYMBOL_HINT_CHARS),
                    "sourceMode": {"type": "string", "enum": ["index", "engine_local"], "default": "index"},
                    "owner": bounded_string_schema(128),
                    "directoryDomain": bounded_string_schema(MAX_SYMBOL_HINT_CHARS),
                    "detailLevel": detail_level,
                    "maxChars": max_chars_schema(),
                },
                ("query",),
            ),
        },
        {
            "name": "unreal_rag_health",
            "description": "Index readability and counts.",
            "inputSchema": _schema({}),
        },
        {
            "name": "unreal_rag_rebuild_status",
            "description": "Whether inputs postdate the index.",
            "inputSchema": _schema({}),
        },
        {
            "name": "unreal_rag_refresh",
            "description": (
                "Rebuild stale active-project index; Editor needs allowEditorLaunch."
            ),
            "inputSchema": _schema(
                {
                    "scope": {
                        "type": "string",
                        "enum": ["project_source", "editor_metadata", "all"],
                        "default": "project_source",
                    },
                    "force": {"type": "boolean", "default": False},
                    "allowEditorLaunch": {
                        "type": "boolean",
                        "default": False,
                        "description": "Allow an Unreal Editor subprocess.",
                    },
                }
            ),
        },
        {
            "name": "unreal_rag_capabilities",
            "description": "Server and index status.",
            "inputSchema": _schema({}),
        },
    ]
    assert tuple(item["name"] for item in definitions) == DIRECT_RAG_TOOL_NAMES
    return definitions


def validate_tool_arguments(
    tool: dict[str, Any],
    arguments: Any,
) -> str | None:
    """Perform small transport-level checks before capability dispatch."""

    if not isinstance(arguments, dict):
        return "Tool arguments must be a JSON object."
    schema = tool.get("inputSchema") if isinstance(tool, dict) else {}
    properties = schema.get("properties") if isinstance(schema, dict) else {}
    unknown = sorted(set(arguments) - set(properties or {}))
    if unknown:
        return f"Unknown argument(s): {', '.join(unknown)}"
    missing = [
        key
        for key in schema.get("required", [])
        if key not in arguments
        or arguments[key] is None
        or (isinstance(arguments[key], str) and not arguments[key].strip())
    ]
    if missing:
        return f"Missing required argument(s): {', '.join(missing)}"
    for key, value in arguments.items():
        field_schema = properties.get(key) if isinstance(properties, dict) else None
        if isinstance(field_schema, dict) and not _value_matches_schema(value, field_schema):
            return f"Argument '{key}' does not match its declared type or allowed values."
    return None


def _value_matches_schema(value: Any, schema: dict[str, Any]) -> bool:
    alternatives = schema.get("oneOf")
    if isinstance(alternatives, list):
        return any(
            _value_matches_schema(value, candidate)
            for candidate in alternatives
            if isinstance(candidate, dict)
        )
    expected = schema.get("type")
    if expected == "string":
        if not isinstance(value, str):
            return False
        if len(value) < int(schema.get("minLength") or 0):
            return False
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            return False
    elif expected == "boolean":
        if not isinstance(value, bool):
            return False
    elif expected == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return False
        if "minimum" in schema and value < int(schema["minimum"]):
            return False
        if "maximum" in schema and value > int(schema["maximum"]):
            return False
    elif expected == "array":
        if not isinstance(value, list):
            return False
        if len(value) < int(schema.get("minItems") or 0):
            return False
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            return False
        item_schema = schema.get("items")
        if isinstance(item_schema, dict) and any(
            not _value_matches_schema(item, item_schema) for item in value
        ):
            return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    return True


__all__ = [
    "DIRECT_RAG_TOOL_NAMES",
    "DIRECT_RAG_MUTATING_TOOL_NAMES",
    "direct_rag_tool_definitions",
    "validate_tool_arguments",
]
