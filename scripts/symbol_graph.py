#!/usr/bin/env python
"""Helpers for persistent Unreal symbol graph data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


GRAPH_ONLY_BEHAVIOR_LIMIT = (
    "Graph output is source-location/navigation evidence only. It cannot by itself prove "
    "runtime behavior, wiring, data flow, ownership, or framework semantics."
)


def default_graph_path(root: Path | None = None) -> Path:
    base = root or Path(__file__).resolve().parent.parent
    return base / "data" / "symbol_graph" / "symbol_graph.json"


def _empty_graph() -> dict[str, Any]:
    return {"version": 2, "files": [], "symbols": [], "edges": []}


def load_symbol_graph(root: Path | None = None) -> dict[str, Any]:
    path = default_graph_path(root)
    if not path.is_file():
        return _empty_graph()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return _empty_graph()
    return data if isinstance(data, dict) else _empty_graph()


def lookup_symbol(name: str, graph: dict[str, Any], limit: int = 20) -> list[dict[str, Any]]:
    needle = str(name or "").strip().lower()
    if not needle or limit <= 0:
        return []
    rows = graph.get("symbols") if isinstance(graph, dict) else []
    exact: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol_name") or "")
        qualified = str(row.get("qualified_name") or "")
        names = {symbol.lower(), qualified.lower()} - {""}
        if needle in names:
            exact.append(row)
        elif any(needle in candidate for candidate in names):
            partial.append(row)
    return [*exact, *partial][:limit]


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


def source_evidence_for_symbol(row: dict[str, Any]) -> dict[str, Any]:
    """Return packet-compatible source evidence without upgrading graph proof.

    v1 artifacts have no ``sourceEvidence`` field, so construct the same
    compact location shape from their retained symbol fields.
    """
    source = row.get("sourceEvidence") if isinstance(row, dict) else None
    if isinstance(source, dict) and source.get("location"):
        evidence = dict(source)
    else:
        path = str(row.get("file_path") or "")
        line = int(row.get("line_start") or 1)
        evidence = {
            "kind": "project_source",
            "location": f"{path}:{line}" if path else "symbol_graph:unknown",
            "filePath": path,
            "lineStart": line,
            "lineEnd": int(row.get("line_end") or line),
            "fileHash": str(row.get("file_hash") or ""),
        }
    evidence["observation"] = (
        f"Graph located {row.get('symbol_kind', 'symbol')} "
        f"{row.get('qualified_name') or row.get('symbol_name') or ''} in project source."
    ).strip()
    evidence["proofBoundary"] = str(row.get("proofBoundary") or GRAPH_ONLY_BEHAVIOR_LIMIT)
    return evidence


def related_edges(
    symbol: str | dict[str, Any],
    graph: dict[str, Any],
    *,
    kinds: set[str] | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return deterministic local graph relations for navigation/impact review."""
    if isinstance(symbol, dict):
        matches = [symbol]
    else:
        matches = lookup_symbol(symbol, graph, limit=limit)
    ids = {str(row.get("id") or "") for row in matches if row.get("id")}
    if not ids:
        return []
    out: list[dict[str, Any]] = []
    for edge in graph.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        if kinds and str(edge.get("kind") or "") not in kinds:
            continue
        if str(edge.get("from") or "") not in ids and str(edge.get("to") or "") not in ids:
            continue
        out.append(edge)
    return sorted(out, key=lambda item: (str(item.get("kind")), str(item.get("id"))))[:limit]


def graph_claim_evidence(
    symbol: str,
    graph: dict[str, Any],
    *,
    claim_type: str = "existence",
    limit: int = 4,
) -> dict[str, Any]:
    """Produce conservative evidence for callers composing structured claims.

    A graph can support a source-located existence/textual-relation claim.  It
    deliberately rejects an attempt to present graph-only data as a behavioral
    or architectural conclusion; the caller must add a BehaviorPath and the
    evidence required by ``review_claim_validate``.
    """
    matches = lookup_symbol(symbol, graph, limit=limit)
    normalized = str(claim_type or "existence").strip().lower()
    graph_only_unsupported = normalized != "existence"
    if graph_only_unsupported:
        return {
            "ok": False,
            "claimType": normalized,
            "evidence": [source_evidence_for_symbol(row) for row in matches],
            "proofBoundary": GRAPH_ONLY_BEHAVIOR_LIMIT,
            "requiredNextEvidence": [
                "explicit BehaviorPath with entry, decision/dispatch, and final effect when the claim is behavioral",
                "direct source reads or static/build/test/runtime evidence appropriate to the claim",
            ],
        }
    return {
        "ok": bool(matches),
        "claimType": normalized,
        "proofLevel": "SourceVerified" if matches else "Proposed",
        "evidence": [source_evidence_for_symbol(row) for row in matches],
        "proofBoundary": GRAPH_ONLY_BEHAVIOR_LIMIT,
    }
