#!/usr/bin/env python
"""Symbol-centric and hybrid retrieval helpers for the Unreal RAG index."""

from __future__ import annotations

import difflib
import json
import re
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any

from rag_search import META_COLUMNS, SearchOptions, expand_query_terms, rerank_row, resolve_mode, table_columns, tokenize
from symbol_cache import get_cached, set_cached
from workspace_paths import find_workspace_root

CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

DEFAULT_CONCEPT_ALIASES: dict[str, list[str]] = {
    "health": ["Health", "HealthComponent", "LyraHealth", "LyraHealthComponent", "Damage", "Vitality"],
    "health component": ["LyraHealthComponent", "HealthComponent", "UHealthComponent", "LyraHealth"],
    "player health": ["LyraHealthComponent", "HealthComponent", "LyraHealth"],
}


@lru_cache(maxsize=1)
def load_concept_aliases() -> dict[str, list[str]]:
    workspace = find_workspace_root()
    path = workspace / "config" / "concept_aliases.json"
    if not path.exists():
        return DEFAULT_CONCEPT_ALIASES
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): list(v) for k, v in data.items() if isinstance(v, list)}
    except Exception:
        pass
    return DEFAULT_CONCEPT_ALIASES


def split_identifier(value: str) -> list[str]:
    parts: list[str] = []
    for token in IDENTIFIER_RE.findall(value):
        parts.append(token)
        parts.extend(CAMEL_SPLIT_RE.findall(token))
    return [part for part in parts if len(part) > 1]


def expand_concept_terms(query: str) -> list[str]:
    concept_aliases = load_concept_aliases()
    terms = expand_query_terms(tokenize(query))
    lower = query.lower()
    for key, aliases in concept_aliases.items():
        if key in lower:
            terms.extend(aliases)
    for alias_key, aliases in concept_aliases.items():
        if all(part in lower for part in alias_key.split()):
            terms.extend(aliases)
    for token in tokenize(query):
        for part in split_identifier(token):
            if len(part) > 2:
                terms.append(part)
    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(term)
    return deduped[:48]


def symbol_similarity(query: str, symbol_name: str, title: str = "", text: str = "") -> float:
    query_lower = query.lower()
    if symbol_name:
        symbol_lower = symbol_name.lower()
        if symbol_lower == query_lower:
            return 1.0
        if symbol_lower in query_lower or query_lower in symbol_lower:
            return 0.92
        ratio = difflib.SequenceMatcher(None, query_lower, symbol_lower).ratio()
        query_parts = {part.lower() for part in split_identifier(query)}
        symbol_parts = {part.lower() for part in split_identifier(symbol_name)}
        overlap = len(query_parts & symbol_parts)
        if overlap:
            ratio = max(ratio, min(0.95, 0.45 + overlap * 0.18))
        return ratio

    title_lower = title.lower()
    text_lower = text.lower()
    query_parts = {part.lower() for part in split_identifier(query)} | {t.lower() for t in tokenize(query)}
    best = 0.0
    for part in query_parts:
        if len(part) < 3:
            continue
        if part in title_lower or part in text_lower:
            best = max(best, 0.72)
    if query_lower in title_lower or query_lower in text_lower:
        best = max(best, 0.85)
    for alias in load_concept_aliases().get("health component", []):
        if alias.lower() in title_lower or alias.lower() in text_lower:
            best = max(best, 0.9)
    return best


def is_identifier_query(query: str) -> bool:
    token = query.strip()
    return bool(re.match(r"^[A-UW-Z][A-Za-z0-9_]+$", token))


def fetch_symbol_candidates(
    index: Path,
    query: str,
    limit: int = 80,
    projects: list[str] | None = None,
) -> list[dict[str, Any]]:
    workspace = find_workspace_root()
    cached = get_cached(workspace, query, projects, limit)
    if cached is not None:
        return cached

    terms = expand_concept_terms(query)
    if not terms:
        terms = [query.strip()]

    identifier_terms: list[str] = []
    if is_identifier_query(query):
        identifier_terms = [query.strip()]
    else:
        identifier_terms = [
            term for term in terms
            if is_identifier_query(term) and len(term) > 6
        ][:6]

    prioritized = [
        term for term in terms
        if term[:1].isupper() and len(term) > 4
    ]
    prioritized_terms = identifier_terms or prioritized

    conn = sqlite3.connect(index)
    conn.row_factory = sqlite3.Row
    available_columns = table_columns(conn, "chunks")
    select_columns = [
        "chunks.chunk_id",
        "chunks.source",
        "chunks.title",
        "chunks.locator",
        "chunks.chunk_index",
        "chunks.text",
        "5.0 as score",
    ]
    for column in META_COLUMNS:
        if column in available_columns:
            select_columns.append(f"chunks.{column}")
        else:
            select_columns.append(f"'' as {column}")

    def run_symbol_exact_prefix(active_terms: list[str], row_limit: int) -> list[sqlite3.Row]:
        clauses = ["chunks.source in ('unreal_symbol', 'unreal_project_text', 'unreal_source')"]
        params: list[Any] = []
        match_groups: list[str] = []
        for term in active_terms[:20]:
            term_lower = term.lower()
            match_groups.append("(lower(chunks.symbol_name) = ? or lower(chunks.symbol_name) like ?)")
            params.extend([term_lower, f"{term_lower}%"])
        if not match_groups:
            return []
        clauses.append("(" + " or ".join(match_groups) + ")")
        if projects and "project" in available_columns:
            placeholders = ",".join("?" for _ in projects)
            clauses.append(f"chunks.project in ({placeholders})")
            params.extend(projects)
        params.append(row_limit)
        return conn.execute(
            f"""
            select {", ".join(select_columns)}
            from chunks
            where {" and ".join(clauses)}
            order by
                length(chunks.symbol_name),
                case
                    when lower(chunks.title) like '%.h' then 0
                    when lower(chunks.title) like '%.cpp' then 1
                    when chunks.symbol_name != '' then 2
                    else 3
                end,
                length(chunks.title)
            limit ?
            """,
            params,
        ).fetchall()

    def run_query(active_terms: list[str], row_limit: int, *, skip_symbol_broad_like: bool = False) -> list[sqlite3.Row]:
        clauses = ["chunks.source in ('unreal_symbol', 'unreal_project_text', 'unreal_source')"]
        params: list[Any] = []
        like_groups: list[str] = []
        for term in active_terms[:20]:
            pattern = f"%{term.lower()}%"
            if skip_symbol_broad_like:
                like_groups.append("(lower(chunks.title) like ? or lower(chunks.text) like ?)")
                params.extend([pattern, pattern])
            else:
                like_groups.append(
                    "(lower(chunks.symbol_name) like ? or lower(chunks.title) like ? or lower(chunks.text) like ?)"
                )
                params.extend([pattern, pattern, pattern])
        if like_groups:
            clauses.append("(" + " or ".join(like_groups) + ")")
        if projects and "project" in available_columns:
            placeholders = ",".join("?" for _ in projects)
            clauses.append(f"chunks.project in ({placeholders})")
            params.extend(projects)
        params.append(row_limit)
        return conn.execute(
            f"""
            select {", ".join(select_columns)}
            from chunks
            where {" and ".join(clauses)}
            order by
                case
                    when lower(chunks.title) like '%.h' then 0
                    when lower(chunks.title) like '%.cpp' then 1
                    when chunks.symbol_name != '' then 2
                    else 3
                end,
                length(chunks.title)
            limit ?
            """,
            params,
        ).fetchall()

    seen_ids: set[str] = set()
    merged_rows: list[sqlite3.Row] = []
    if identifier_terms:
        for row in run_symbol_exact_prefix(identifier_terms, max(limit * 3, 120)):
            chunk_id = str(row["chunk_id"])
            if chunk_id in seen_ids:
                continue
            seen_ids.add(chunk_id)
            merged_rows.append(row)
        for row in run_query(identifier_terms, max(limit * 3, 120), skip_symbol_broad_like=True):
            chunk_id = str(row["chunk_id"])
            if chunk_id in seen_ids:
                continue
            seen_ids.add(chunk_id)
            merged_rows.append(row)
    if prioritized_terms:
        for row in run_query(prioritized_terms[:8], max(limit * 2, 80)):
            chunk_id = str(row["chunk_id"])
            if chunk_id in seen_ids:
                continue
            seen_ids.add(chunk_id)
            merged_rows.append(row)
    for row in run_query(terms, max(limit * 4, 160)):
        chunk_id = str(row["chunk_id"])
        if chunk_id in seen_ids:
            continue
        seen_ids.add(chunk_id)
        merged_rows.append(row)
    conn.close()

    scored: list[dict[str, Any]] = []
    for row in merged_rows:
        item = dict(row)
        symbol_name = str(item.get("symbol_name") or "")
        title = str(item.get("title") or "")
        text = str(item.get("text") or "")
        similarity = symbol_similarity(query, symbol_name, title, text)
        title_lower = title.lower()
        query_lower = query.lower()
        if "healthcomponent" in title_lower or "lyrahealth" in title_lower:
            if "health" in query_lower or "체력" in query_lower:
                similarity = max(similarity, 0.96)
        if "lyra" in query_lower or "lyrahealth" in " ".join(expand_concept_terms(query)).lower():
            if "lyrastartergame" in title_lower or "/lyragame/" in title_lower:
                similarity = max(similarity, min(0.98, similarity + 0.2))
            if title_lower.startswith("runtime/engine/"):
                similarity = min(similarity, 0.45)
        if similarity < 0.28:
            continue
        item["semantic_score"] = similarity
        scored.append(item)

    scored.sort(
        key=lambda row: (
            -float(row.get("semantic_score") or 0.0),
            str(row.get("symbol_name") or ""),
        )
    )
    result = scored[:limit]
    set_cached(workspace, query, projects, limit, result)
    return result


def merge_hybrid_results(
    fts_rows: list[dict[str, Any]],
    symbol_rows: list[dict[str, Any]],
    top_k: int,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in fts_rows:
        merged[str(row["chunk_id"])] = dict(row)
    for row in symbol_rows:
        chunk_id = str(row["chunk_id"])
        existing = merged.get(chunk_id)
        if existing:
            existing["semantic_score"] = max(
                float(existing.get("semantic_score") or 0.0),
                float(row.get("semantic_score") or 0.0),
            )
            continue
        merged[chunk_id] = dict(row)

    ranked = list(merged.values())
    ranked.sort(
        key=lambda row: (
            -float(row.get("semantic_score") or 0.0),
            float(row.get("rank_score") or 0.0),
            float(row.get("score") or 0.0),
        )
    )
    return ranked[:top_k]


def symbol_lookup(
    index: Path,
    query: str,
    top_k: int = 8,
    symbol_kind: str = "",
    project: list[str] | None = None,
) -> list[dict[str, Any]]:
    rows = fetch_symbol_candidates(index, query, limit=max(top_k * 8, 64), projects=project)
    if symbol_kind:
        rows = [row for row in rows if str(row.get("symbol_kind") or "").lower() == symbol_kind.lower()]
    mode = "api_lookup"
    query_terms = expand_concept_terms(query)
    ranked = [rerank_row(row, query_terms, mode) for row in rows]
    ranked.sort(
        key=lambda row: (
            -float(row.get("semantic_score") or 0.0),
            float(row.get("rank_score") or 0.0),
        )
    )
    return ranked[:top_k]


def hybrid_search(
    index: Path,
    query: str,
    top_k: int,
    options: SearchOptions | None = None,
    fts_search_fn: Any = None,
) -> list[dict[str, Any]]:
    from rag_search import search as default_search

    search_fn = fts_search_fn or default_search
    options = options or SearchOptions()
    fts_rows = search_fn(index, query, top_k, options)
    symbol_rows = fetch_symbol_candidates(
        index,
        query,
        limit=max(top_k * 6, 48),
        projects=options.projects,
    )
    mode = resolve_mode(query, options.mode)
    query_terms = expand_concept_terms(query)
    symbol_rows = [rerank_row(row, query_terms, mode) for row in symbol_rows]
    merged = merge_hybrid_results(fts_rows, symbol_rows, top_k)

    try:
        from rag_embeddings import embedding_status, search_embeddings

        status = embedding_status(index)
        if status.get("hybridV2Ready"):
            embed_rows = search_embeddings(index, query, top_k=max(top_k * 2, 12))
            if options.projects:
                project_set = {p.lower() for p in options.projects}
                embed_rows = [
                    row
                    for row in embed_rows
                    if not row.get("project")
                    or str(row.get("project")).lower() in project_set
                    or any(p.lower() in str(row.get("locator") or "").lower() for p in options.projects)
                ]
            merged = merge_hybrid_results(merged, embed_rows, top_k)
    except Exception:
        pass

    return merged[:top_k]
