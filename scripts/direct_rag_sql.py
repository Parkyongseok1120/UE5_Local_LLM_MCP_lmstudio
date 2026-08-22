#!/usr/bin/env python
"""Small SQLite primitives for factual Direct RAG queries."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from rag_types import SearchOptions
from direct_rag_readonly_db import connect_readonly

TERM_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+|[\uac00-\ud7a3]+")
META_COLUMNS = (
    "project",
    "project_root",
    "relative_path",
    "extension",
    "layer",
    "doc_type",
    "genre",
    "symbol_name",
    "symbol_kind",
    "module_name",
    "error_code",
    "error_file",
    "path_only",
)
ENGINE_PROJECT_IDENTITIES = frozenset({"", "engine", "__engine__"})


def tokenize(value: str) -> list[str]:
    return [term for term in TERM_RE.findall(str(value or "")) if len(term) > 1]


def normalize_values(
    values: Iterable[str] | None,
    *,
    split_commas: bool = True,
) -> list[str]:
    result: list[str] = []
    for value in values or []:
        parts = str(value).split(",") if split_commas else [str(value)]
        for part in parts:
            item = part.strip()
            if item and item not in result:
                result.append(item)
    return result


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    rows = connection.execute(f"pragma table_info({table})").fetchall()
    return {str(row[1]) for row in rows}


def _select_columns(available: set[str], *, score: str) -> list[str]:
    columns = [
        "chunks.chunk_id",
        "chunks.source",
        "chunks.title",
        "chunks.locator",
        "chunks.chunk_index",
        "chunks.text",
        f"{score} as score",
    ]
    columns.extend(
        f"chunks.{column}" if column in available else f"'' as {column}"
        for column in META_COLUMNS
    )
    return columns


def _add_filter(
    clauses: list[str],
    parameters: list[Any],
    column: str,
    values: Iterable[str] | None,
    available: set[str],
) -> None:
    normalized = normalize_values(values, split_commas=column != "project_root")
    if column == "extension":
        normalized = [value if value.startswith(".") else f".{value}" for value in normalized]
    if not normalized:
        return
    if column in {"project", "project_root"} and column not in available:
        clauses.append("1=0")
        return
    if column not in available:
        return
    if column == "project":
        engine_requested = any(value.casefold() in ENGINE_PROJECT_IDENTITIES for value in normalized)
        named = [value for value in normalized if value.casefold() not in ENGINE_PROJECT_IDENTITIES]
        choices: list[str] = []
        if engine_requested:
            choices.append("lower(coalesce(chunks.project, '')) in ('', 'engine', '__engine__')")
        if named:
            placeholders = ",".join("?" for _ in named)
            choices.append(f"chunks.project in ({placeholders})")
            parameters.extend(named)
        clauses.append("(" + " or ".join(choices) + ")")
        return
    placeholders = ",".join("?" for _ in normalized)
    clauses.append(f"chunks.{column} in ({placeholders})")
    parameters.extend(normalized)


def _matches_required(row: dict[str, Any], required_terms: list[str]) -> bool:
    if not required_terms:
        return True
    text = " ".join(
        str(row.get(name) or "")
        for name in (
            "title",
            "locator",
            "relative_path",
            "symbol_name",
            "module_name",
            "error_code",
            "error_file",
            "text",
        )
    ).casefold()
    return all(term.casefold() in text for term in required_terms)


def fetch_fts_rows(
    index: Path,
    query: str,
    options: SearchOptions,
    limit: int,
    *,
    expected_generation: str | None = None,
) -> list[dict[str, Any]]:
    """Return filtered FTS rows without mode routing, sidecars, or instructions."""

    terms = tokenize(query)[:32]
    if not terms:
        return []
    fts_query = " OR ".join(f'"{term}"' for term in terms)
    connection = connect_readonly(index, expected_generation=expected_generation)
    connection.row_factory = sqlite3.Row
    try:
        available = _table_columns(connection, "chunks")
        clauses = ["chunks_fts match ?"]
        parameters: list[Any] = [fts_query]
        for column, values in (
            ("source", options.sources),
            ("project", options.projects),
            ("project_root", options.project_roots),
            ("layer", options.layers),
            ("doc_type", options.doc_types),
            ("genre", options.genres),
            ("extension", options.extensions),
        ):
            _add_filter(clauses, parameters, column, values, available)
        parameters.append(max(1, int(limit)))
        rows = connection.execute(
            f"""
            select {", ".join(_select_columns(available, score="bm25(chunks_fts)"))}
            from chunks_fts
            join chunks on chunks_fts.rowid = chunks.rowid
            where {" and ".join(clauses)}
            order by score
            limit ?
            """,
            parameters,
        ).fetchall()
        required = normalize_values(options.required_terms)
        return [dict(row) for row in rows if _matches_required(dict(row), required)]
    finally:
        connection.close()


def fetch_like_rows(
    index: Path,
    terms: Iterable[str],
    *,
    projects: Iterable[str] | None,
    project_roots: Iterable[str] | None = None,
    limit: int,
    expected_generation: str | None = None,
) -> list[dict[str, Any]]:
    """Return source/symbol rows for lexical symbol lookup."""

    normalized = normalize_values(terms)[:16]
    if not normalized:
        return []
    connection = connect_readonly(index, expected_generation=expected_generation)
    connection.row_factory = sqlite3.Row
    try:
        available = _table_columns(connection, "chunks")
        fields = ["title", "locator", "text"]
        if "symbol_name" in available:
            fields.insert(0, "symbol_name")
        groups: list[str] = []
        parameters: list[Any] = []
        for term in normalized:
            pattern = f"%{term.casefold()}%"
            groups.append("(" + " or ".join(f"lower(chunks.{field}) like ?" for field in fields) + ")")
            parameters.extend(pattern for _ in fields)
        clauses = ["(" + " or ".join(groups) + ")"]
        _add_filter(clauses, parameters, "project", projects, available)
        _add_filter(clauses, parameters, "project_root", project_roots, available)
        parameters.append(max(1, int(limit)))
        symbol_order = (
            "case when coalesce(chunks.symbol_name, '') != '' then 0 else 1 end,"
            if "symbol_name" in available
            else ""
        )
        return [
            dict(row)
            for row in connection.execute(
                f"""
                select {", ".join(_select_columns(available, score="0.0"))}
                from chunks
                where {" and ".join(clauses)}
                order by {symbol_order} length(chunks.title)
                limit ?
                """,
                parameters,
            ).fetchall()
        ]
    finally:
        connection.close()


def project_root_candidates(
    index: Path,
    projects: Iterable[str],
    *,
    expected_generation: str | None = None,
) -> dict[str, list[str]]:
    """Return rooted index identities used to reject ambiguous name selectors."""

    normalized = normalize_values(projects)
    result = {project: [] for project in normalized}
    if not normalized or not index.is_file():
        return result
    connection = connect_readonly(index, expected_generation=expected_generation)
    try:
        available = _table_columns(connection, "chunks")
        if "project" not in available or "project_root" not in available:
            return result
        placeholders = ",".join("?" for _ in normalized)
        rows = connection.execute(
            f"""
            select project, project_root
            from chunks
            where project in ({placeholders}) and project_root != ''
            group by project, project_root
            order by project, project_root
            """,
            normalized,
        ).fetchall()
        for project, root in rows:
            key = str(project)
            value = str(root)
            if key in result and value and value not in result[key]:
                result[key].append(value)
        return result
    except sqlite3.Error:
        return result
    finally:
        connection.close()


__all__ = [
    "META_COLUMNS",
    "fetch_fts_rows",
    "fetch_like_rows",
    "normalize_values",
    "project_root_candidates",
    "tokenize",
]
