#!/usr/bin/env python
"""Read freshness facts for one exact project root/name composite."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from direct_rag_readonly_db import connect_readonly
from workspace_paths import filesystem_path_identity


def project_row_facts(
    index: Path,
    project: Path,
    *,
    expected_generation: str | None = None,
) -> tuple[bool, bool, bool]:
    connection: sqlite3.Connection | None = None
    try:
        connection = connect_readonly(
            index,
            expected_generation=expected_generation,
        )
        connection.row_factory = sqlite3.Row
        columns = {str(row[1]) for row in connection.execute("pragma table_info(chunks)")}
        if "project" not in columns:
            return False, False, False
        source_expr = "source" if "source" in columns else "'' as source"
        layer_expr = "layer" if "layer" in columns else "'' as layer"
        doc_type_expr = "doc_type" if "doc_type" in columns else "'' as doc_type"
        project_name = project.stem.casefold()
        if "project_root" in columns:
            root = filesystem_path_identity(project.parent, strip_project_uri=False)
            rows = connection.execute(
                f"""
                select {source_expr}, {layer_expr}, {doc_type_expr}
                from chunks
                where project_root = ? and lower(project) = ?
                """,
                (root, project_name),
            ).fetchall()
        else:
            rows = connection.execute(
                f"""
                select {source_expr}, {layer_expr}, {doc_type_expr}
                from chunks
                where lower(project) = ?
                """,
                (project_name,),
            ).fetchall()
    except sqlite3.Error:
        return False, False, False
    finally:
        if connection is not None:
            connection.close()
    has_rows = bool(rows)
    symbols = any(
        str(row["source"] or "").casefold() == "unreal_symbol"
        or str(row["doc_type"] or "").casefold() == "project_symbol"
        for row in rows
    )
    architecture = any(
        str(row["source"] or "").casefold() == "project_architecture"
        or str(row["layer"] or "").casefold() == "project_architecture"
        for row in rows
    )
    return has_rows, symbols, architecture


__all__ = ["project_row_facts"]
