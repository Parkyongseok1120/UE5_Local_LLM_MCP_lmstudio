#!/usr/bin/env python
"""Format exported material graph metadata for RAG text and validation."""

from __future__ import annotations

from typing import Any


def format_graph_edge(edge: dict[str, Any]) -> str:
    source = str(edge.get("from") or "?")
    target = str(edge.get("to") or "?")
    target_input = str(edge.get("to_input") or edge.get("input") or "")
    if target_input:
        return f"{source} -> {target}.{target_input}"
    return f"{source} -> {target}"


def append_material_graph_text_parts(row: dict[str, Any], text_parts: list[str]) -> None:
    root_outputs = row.get("root_outputs") or []
    if root_outputs:
        text_parts.append("root_outputs:")
        for item in root_outputs[:32]:
            if isinstance(item, dict):
                text_parts.append(
                    f"- {item.get('output', '?')} <= {item.get('expression', '?')}"
                )
            else:
                text_parts.append(f"- {item}")

    graph_edges = row.get("graph_edges") or []
    if graph_edges:
        text_parts.append("graph_edges:")
        for edge in graph_edges[:160]:
            if isinstance(edge, dict):
                text_parts.append(f"- {format_graph_edge(edge)}")

    expressions = row.get("expressions") or []
    if expressions:
        text_parts.append("expressions:")
        for expression in expressions[:80]:
            if not isinstance(expression, dict):
                continue
            name = expression.get("name") or "?"
            cls = expression.get("class") or "?"
            line = f"- {name} ({cls})"
            details = expression.get("details") or {}
            if isinstance(details, dict) and details:
                detail_bits = [f"{key}={value}" for key, value in list(details.items())[:8]]
                line += " details: " + ", ".join(detail_bits)
            wires = expression.get("input_wires") or {}
            if isinstance(wires, dict) and wires:
                wire_bits = [f"{key}<={value}" for key, value in list(wires.items())[:16]]
                line += " wires: " + ", ".join(wire_bits)
            text_parts.append(line)


def material_row_search_text(row: dict[str, Any]) -> str:
    parts = [str(row.get("asset_path") or ""), str(row.get("name") or "")]
    append_material_graph_text_parts(row, parts)
    for key in (
        "parent_material",
        "graph_source",
        "scalar_parameters",
        "vector_parameters",
        "texture_parameters",
        "static_switch_parameters",
    ):
        value = row.get(key)
        if value:
            parts.append(f"{key}: {value}")
    return "\n".join(parts).lower()
