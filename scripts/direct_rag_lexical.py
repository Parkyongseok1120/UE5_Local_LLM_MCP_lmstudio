#!/usr/bin/env python
"""Bounded factual lexical/hybrid retrieval for Direct RAG."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from direct_rag_sql import fetch_fts_rows, tokenize
from direct_rag_symbol_query import symbol_candidates
from rag_types import SearchOptions
from retrieval_profiles import apply_retrieval_layer_bonus

MODE_QUERY_TERMS: dict[str, tuple[str, ...]] = {
    "module_fix": ("Build.cs", "module", "include", "dependency"),
    "reflection_fix": ("UHT", "generated.h", "UCLASS", "UPROPERTY", "UFUNCTION"),
    "compile_fix": ("compiler", "declaration", "definition", "header", "source"),
    "runtime_debug": ("runtime", "callstack", "log", "ensure", "crash"),
    "shader": ("shader", "usf", "ush", "RenderCore", "RHI"),
    "material_analysis": ("material", "expression", "parameter", "graph"),
    "blueprint_analysis": ("blueprint", "graph", "node", "pin", "function"),
}


def _effective_query(query: str, mode: str) -> str:
    terms = list(MODE_QUERY_TERMS.get(mode, ()))
    present = {item.casefold() for item in tokenize(query)}
    additions = [item for item in terms if item.casefold() not in present]
    return " ".join([query, *additions]).strip()


def _rank_rows(rows: list[dict[str, Any]], query: str, mode: str) -> list[dict[str, Any]]:
    query_fold = query.casefold()
    terms = {term.casefold() for term in tokenize(query)}
    ranked: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        rank = float(item.get("score") or 0.0)
        symbol = str(item.get("symbol_name") or "").casefold()
        title = str(item.get("title") or "").casefold()
        locator = str(item.get("locator") or "").casefold()
        if symbol == query_fold:
            rank -= 100.0
        elif symbol and (symbol in query_fold or query_fold in symbol):
            rank -= 30.0
        rank -= 4.0 * sum(term in title for term in terms)
        rank -= 2.0 * sum(term in locator for term in terms)
        item["rank_score"] = rank
        item["resolved_mode"] = mode
        ranked.append(item)
    ranked.sort(key=lambda row: (float(row.get("rank_score") or 0.0), str(row.get("chunk_id") or "")))
    return apply_retrieval_layer_bonus(ranked, mode)


def lexical_search(
    index: Path,
    query: str,
    top_k: int,
    options: SearchOptions | None = None,
    *,
    expected_generation: str | None = None,
) -> list[dict[str, Any]]:
    options = options or SearchOptions()
    mode = options.mode if options.mode != "auto" else "implementation"
    candidate_limit = max(top_k, options.candidate_limit, top_k * 20)
    rows = fetch_fts_rows(
        index,
        _effective_query(query, mode),
        options,
        candidate_limit,
        expected_generation=expected_generation,
    )
    return _rank_rows(rows, query, mode)[:top_k]


def hybrid_search(
    index: Path,
    query: str,
    top_k: int,
    options: SearchOptions | None = None,
    *,
    expected_generation: str | None = None,
) -> list[dict[str, Any]]:
    options = options or SearchOptions()
    lexical = lexical_search(
        index,
        query,
        top_k,
        options,
        expected_generation=expected_generation,
    )
    symbols = symbol_candidates(
        index,
        query,
        limit=max(top_k * 4, 32),
        projects=options.projects,
        project_roots=options.project_roots,
        expected_generation=expected_generation,
    )
    merged = {str(row.get("chunk_id") or ""): dict(row) for row in lexical}
    for row in symbols:
        key = str(row.get("chunk_id") or "")
        if not key:
            continue
        existing = merged.get(key)
        if existing is None:
            merged[key] = dict(row)
        else:
            existing["semantic_score"] = max(
                float(existing.get("semantic_score") or 0.0),
                float(row.get("semantic_score") or 0.0),
            )
            existing["rank_score"] = min(
                float(existing.get("rank_score") or 0.0),
                float(row.get("rank_score") or 0.0),
            )
    ranked = list(merged.values())
    ranked.sort(
        key=lambda row: (
            -float(row.get("semantic_score") or 0.0),
            float(row.get("rank_score") or 0.0),
            float(row.get("score") or 0.0),
        )
    )
    return ranked[:top_k]


__all__ = ["hybrid_search", "lexical_search"]
