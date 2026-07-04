#!/usr/bin/env python
"""Helpers for persistent Unreal symbol graph data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def default_graph_path(root: Path | None = None) -> Path:
    base = root or Path(__file__).resolve().parent.parent
    return base / "data" / "symbol_graph" / "symbol_graph.json"


def load_symbol_graph(root: Path | None = None) -> dict[str, Any]:
    path = default_graph_path(root)
    if not path.is_file():
        return {"version": 1, "symbols": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "symbols": []}
    return data if isinstance(data, dict) else {"version": 1, "symbols": []}


def lookup_symbol(name: str, graph: dict[str, Any], limit: int = 20) -> list[dict[str, Any]]:
    needle = str(name or "").lower()
    if not needle:
        return []
    rows = graph.get("symbols") if isinstance(graph, dict) else []
    matches: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol_name") or "")
        if symbol.lower() == needle:
            matches.insert(0, row)
        elif needle in symbol.lower():
            matches.append(row)
        if len(matches) >= limit:
            break
    return matches[:limit]


def owner_build_cs_for_file(file_path: str, graph: dict[str, Any]) -> str:
    target = str(file_path or "").replace("\\", "/").lower()
    if not target:
        return ""
    rows = graph.get("symbols") if isinstance(graph, dict) else []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        row_path = str(row.get("file_path") or "").replace("\\", "/").lower()
        if row_path == target:
            return str(row.get("owner_build_cs") or "")
    return ""
