#!/usr/bin/env python
"""JSONL parsing and atomic storage for Editor metadata collection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from atomic_io import atomic_write_text
from editor_metadata_identity import chunk_asset_key
from editor_metadata_projection import row_to_chunk, source_for_row


def parse_export_spec(spec: str) -> tuple[Path, str]:
    path_text, separator, kind = spec.rpartition(":")
    if not separator or not path_text or not kind:
        raise ValueError(f"Invalid export spec, expected path:type: {spec}")
    return Path(path_text), kind


def _read_jsonl_objects(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8-sig", errors="strict").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL object at {path}:{line_number}: {exc.msg}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"Expected JSON object at {path}:{line_number}")
        rows.append(row)
    return rows


def load_raw_chunks(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    chunks: dict[str, dict[str, Any]] = {}
    for chunk in _read_jsonl_objects(path):
        key = chunk_asset_key(chunk)
        if not key:
            raise ValueError(f"Editor metadata chunk has no stable identity: {path}")
        chunks[key] = chunk
    return chunks


def ingest_export(
    export_path: Path,
    source_key: str,
    project: str,
    project_root: str = "",
) -> list[dict[str, Any]]:
    export_mtime = export_path.stat().st_mtime
    return [
        row_to_chunk(
            source_for_row(source_key, row),
            row,
            project,
            project_root,
            export_mtime=export_mtime,
            export_kind=source_key,
            row_ordinal=row_ordinal,
        )
        for row_ordinal, row in enumerate(_read_jsonl_objects(export_path))
    ]


def write_raw_chunks(path: Path, chunks: Iterable[dict[str, Any]]) -> None:
    content = "".join(json.dumps(chunk, ensure_ascii=False) + "\n" for chunk in chunks)
    atomic_write_text(path, content)


__all__ = [
    "ingest_export",
    "load_raw_chunks",
    "parse_export_spec",
    "write_raw_chunks",
]
