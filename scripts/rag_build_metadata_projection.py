"""Bound metadata persisted beside searchable RAG chunk text."""

from __future__ import annotations

import json
from typing import Any

MAX_STORED_METADATA_BYTES = 8 * 1024
MAX_METADATA_SCALAR_CHARS = 1024
MAX_METADATA_LIST_ITEMS = 32
CHUNK_METADATA_POLICY_VERSION = 1

_ESSENTIAL_KEYS = (
    "project", "project_root", "project_file", "root", "relative_path",
    "path", "source_path", "extension", "scope", "symbol_name",
    "symbol_kind", "module_name", "error_code", "error_file", "error_kind",
    "asset_path", "asset_type", "parent_class", "generated_class", "section",
    "setting", "value", "ordinal", "editor_export_mtime", "editor_export_kind",
)


def _scalar(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return value[:MAX_METADATA_SCALAR_CHARS] if isinstance(value, str) else None


def _value(value: Any) -> Any:
    scalar = _scalar(value)
    if scalar is not None or value is None:
        return scalar
    if not isinstance(value, (list, tuple)):
        return None
    bounded: list[Any] = []
    for item in value[:MAX_METADATA_LIST_ITEMS]:
        item_scalar = _scalar(item)
        if item_scalar is None and item is not None:
            return None
        bounded.append(item_scalar)
    return bounded


def _encoded_size(value: dict[str, Any]) -> int:
    rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return len(rendered.encode("utf-8"))


def compact_metadata(
    metadata: dict[str, Any],
    routing_fields: dict[str, str],
) -> dict[str, Any]:
    """Keep routing/provenance scalars; never duplicate exported graphs per chunk."""

    source = dict(metadata)
    source.update({key: value for key, value in routing_fields.items() if value})
    ordered = [*_ESSENTIAL_KEYS]
    ordered.extend(key for key in sorted(source) if key not in _ESSENTIAL_KEYS)
    result: dict[str, Any] = {}
    for key in ordered:
        if key not in source:
            continue
        raw = source[key]
        value = _value(raw)
        if value is None and raw is not None:
            if isinstance(raw, (dict, list, tuple, set)):
                count_key = f"{key}_count"
                candidate = {**result, count_key: len(raw)}
                if _encoded_size(candidate) <= MAX_STORED_METADATA_BYTES:
                    result[count_key] = len(raw)
            continue
        candidate = {**result, key: value}
        if _encoded_size(candidate) <= MAX_STORED_METADATA_BYTES:
            result[key] = value
    return result


def chunk_metadata_policy() -> dict[str, Any]:
    return {
        "version": CHUNK_METADATA_POLICY_VERSION,
        "maxBytes": MAX_STORED_METADATA_BYTES,
        "nestedValues": "count_only",
    }


__all__ = ["chunk_metadata_policy", "compact_metadata"]
