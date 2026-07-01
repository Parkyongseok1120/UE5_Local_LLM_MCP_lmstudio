#!/usr/bin/env python
"""Format exported Blueprint graph metadata for RAG text and validation."""

from __future__ import annotations

from typing import Any


def format_pin_link(link: dict[str, Any]) -> str:
    graph = str(link.get("graph") or "")
    source_node = str(link.get("from_node") or link.get("node") or "?")
    source_pin = str(link.get("from_pin") or link.get("pin") or "")
    target_node = str(link.get("to_node") or "?")
    target_pin = str(link.get("to_pin") or "")
    left = f"{source_node}.{source_pin}" if source_pin else source_node
    right = f"{target_node}.{target_pin}" if target_pin else target_node
    if graph:
        return f"[{graph}] {left} -> {right}"
    return f"{left} -> {right}"


def iter_graph_nodes(row: dict[str, Any]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for graph in row.get("graphs") or []:
        if not isinstance(graph, dict):
            continue
        graph_name = str(graph.get("name") or "")
        for node in graph.get("nodes") or []:
            if isinstance(node, dict):
                item = dict(node)
                item["_graph_name"] = graph_name
                nodes.append(item)
    return nodes


def iter_pin_links(row: dict[str, Any]) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    flat = row.get("graph_links") or []
    if isinstance(flat, list):
        links.extend(item for item in flat if isinstance(item, dict))
    for node in iter_graph_nodes(row):
        graph_name = str(node.get("_graph_name") or "")
        node_name = str(node.get("name") or node.get("title") or "")
        for pin in node.get("pins") or []:
            if not isinstance(pin, dict):
                continue
            for link in pin.get("links") or []:
                if not isinstance(link, dict):
                    continue
                links.append(
                    {
                        "graph": graph_name,
                        "from_node": node_name,
                        "from_pin": pin.get("name") or "",
                        "to_node": link.get("node") or link.get("to_node") or "",
                        "to_pin": link.get("pin") or link.get("to_pin") or "",
                    }
                )
    return links


def append_blueprint_graph_text_parts(row: dict[str, Any], text_parts: list[str]) -> None:
    graph_links = row.get("graph_links") or iter_pin_links(row)
    if graph_links:
        text_parts.append("graph_links:")
        for link in list(graph_links)[:160]:
            if isinstance(link, dict):
                text_parts.append(f"- {format_pin_link(link)}")

    graphs = row.get("graphs") or []
    if graphs:
        text_parts.append("graphs:")
        for graph in graphs[:16]:
            if not isinstance(graph, dict):
                continue
            graph_name = graph.get("name") or "?"
            text_parts.append(f"- graph: {graph_name} nodes={graph.get('node_count', '?')}")
            for node in (graph.get("nodes") or [])[:40]:
                if not isinstance(node, dict):
                    continue
                node_line = f"  - node: {node.get('name') or '?'} ({node.get('class') or '?'}) title={node.get('title') or ''}"
                text_parts.append(node_line)
                for pin in (node.get("pins") or [])[:16]:
                    if not isinstance(pin, dict):
                        continue
                    pin_line = f"    pin: {pin.get('name') or '?'} dir={pin.get('direction') or ''} type={pin.get('type') or ''}"
                    links = pin.get("links") or []
                    if links:
                        pin_line += " links=" + ", ".join(
                            f"{item.get('node','?')}.{item.get('pin','')}" for item in links[:8] if isinstance(item, dict)
                        )
                    text_parts.append(pin_line)


def blueprint_row_search_text(row: dict[str, Any]) -> str:
    parts = [str(row.get("asset_path") or ""), str(row.get("generated_class") or "")]
    append_blueprint_graph_text_parts(row, parts)
    for key in ("variables", "functions", "interfaces", "parent_class"):
        value = row.get(key)
        if value:
            parts.append(f"{key}: {value}")
    return "\n".join(parts).lower()
