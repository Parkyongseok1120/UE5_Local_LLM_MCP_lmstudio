#!/usr/bin/env python
"""Factual symbol retrieval for the independent Direct RAG server."""

from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any

from direct_rag_sql import fetch_like_rows, tokenize

IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _identifier_terms(value: str) -> list[str]:
    terms: list[str] = []
    for token in IDENTIFIER_RE.findall(value):
        for part in (token, *CAMEL_SPLIT_RE.split(token)):
            if len(part) > 1 and part.casefold() not in {item.casefold() for item in terms}:
                terms.append(part)
    for token in tokenize(value):
        if token.casefold() not in {item.casefold() for item in terms}:
            terms.append(token)
    return terms[:24]


def _similarity(query: str, row: dict[str, Any]) -> float:
    query_fold = query.casefold()
    symbol = str(row.get("symbol_name") or "")
    symbol_fold = symbol.casefold()
    if symbol_fold == query_fold:
        return 1.0
    if symbol_fold and (symbol_fold in query_fold or query_fold in symbol_fold):
        return 0.94
    ratio = difflib.SequenceMatcher(None, query_fold, symbol_fold).ratio() if symbol else 0.0
    query_parts = {part.casefold() for part in _identifier_terms(query)}
    symbol_parts = {part.casefold() for part in _identifier_terms(symbol)}
    overlap = len(query_parts & symbol_parts)
    if overlap:
        ratio = max(ratio, min(0.92, 0.45 + 0.16 * overlap))
    haystack = " ".join(
        str(row.get(name) or "") for name in ("title", "locator", "text")
    ).casefold()
    if query_fold and query_fold in haystack:
        ratio = max(ratio, 0.82)
    elif any(len(part) > 2 and part in haystack for part in query_parts):
        ratio = max(ratio, 0.64)
    return ratio


def symbol_candidates(
    index: Path,
    query: str,
    *,
    limit: int,
    projects: list[str] | None = None,
    project_roots: list[str] | None = None,
    expected_generation: str | None = None,
) -> list[dict[str, Any]]:
    terms = _identifier_terms(query) or [query]
    rows = fetch_like_rows(
        index,
        [query, *terms],
        projects=projects,
        project_roots=project_roots,
        limit=max(limit * 5, 80),
        expected_generation=expected_generation,
    )
    scored: list[dict[str, Any]] = []
    for row in rows:
        score = _similarity(query, row)
        if score < 0.25:
            continue
        item = dict(row)
        item["semantic_score"] = score
        item["rank_score"] = -score * 100.0
        item["resolved_mode"] = "api_lookup"
        scored.append(item)
    scored.sort(
        key=lambda row: (
            -float(row.get("semantic_score") or 0.0),
            str(row.get("symbol_name") or "").casefold(),
            str(row.get("locator") or "").casefold(),
        )
    )
    return scored[:limit]


def symbol_lookup(
    index: Path,
    query: str,
    *,
    top_k: int = 8,
    symbol_kind: str = "",
    projects: list[str] | None = None,
    project_roots: list[str] | None = None,
    expected_generation: str | None = None,
) -> list[dict[str, Any]]:
    rows = symbol_candidates(
        index,
        query,
        limit=max(top_k * 4, 32),
        projects=projects,
        project_roots=project_roots,
        expected_generation=expected_generation,
    )
    if symbol_kind:
        expected = symbol_kind.casefold()
        rows = [row for row in rows if str(row.get("symbol_kind") or "").casefold() == expected]
    return rows[:top_k]


__all__ = ["symbol_candidates", "symbol_lookup"]
