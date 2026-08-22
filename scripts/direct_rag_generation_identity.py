#!/usr/bin/env python
"""Reject mixed manifest/SQLite generations during companion promotion."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class RagGenerationTransitionError(RuntimeError):
    pass


def _manifest_payload(index: Path) -> dict[str, Any]:
    try:
        payload = json.loads(
            (index.parent / "build_manifest.json").read_text(encoding="utf-8-sig")
        )
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _database_generation(connection: sqlite3.Connection) -> str:
    try:
        row = connection.execute(
            "select value from index_meta where key = 'generation_id'"
        ).fetchone()
    except sqlite3.Error:
        return ""
    return str(row[0] or "").strip() if row else ""


def _connect_consistent_pair(
    index: Path,
    *,
    attempts: int = 40,
    delay_seconds: float = 0.05,
    expected_generation: str | None = None,
) -> tuple[sqlite3.Connection, dict[str, Any]]:
    target = index.expanduser().resolve()
    uri = target.as_uri() + "?mode=ro"
    last = "generation identity did not stabilize"
    for attempt in range(max(1, attempts)):
        before = _manifest_payload(target)
        manifest_before = str(before.get("generationId") or "").strip()
        connection = sqlite3.connect(uri, uri=True)
        actual = _database_generation(connection)
        after = _manifest_payload(target)
        manifest_after = str(after.get("generationId") or "").strip()
        request_generation = str(expected_generation or "").strip()
        observed = {manifest_before, actual, manifest_after}
        if request_generation and observed != {request_generation}:
            connection.close()
            raise RagGenerationTransitionError(
                "RAG generation changed during one request "
                f"(expected={request_generation}, manifest={manifest_after or manifest_before or 'missing'}, "
                f"sqlite={actual or 'missing'}): {target}"
            )
        if not manifest_before and not actual and not manifest_after:
            return connection, after
        if manifest_before and manifest_before == actual == manifest_after:
            return connection, after
        connection.close()
        last = f"manifest={manifest_after or manifest_before or 'missing'}, sqlite={actual or 'missing'}"
        if attempt + 1 < attempts:
            time.sleep(delay_seconds)
    raise RagGenerationTransitionError(
        f"RAG generation transition is incomplete ({last}): {target}"
    )


def connect_consistent_readonly(
    index: Path,
    *,
    attempts: int = 40,
    delay_seconds: float = 0.05,
    expected_generation: str | None = None,
) -> sqlite3.Connection:
    connection, _payload = _connect_consistent_pair(
        index,
        attempts=attempts,
        delay_seconds=delay_seconds,
        expected_generation=expected_generation,
    )
    return connection


def read_consistent_index_manifest(
    index: Path,
    *,
    expected_generation: str | None = None,
) -> dict[str, Any]:
    index = index.expanduser().resolve()
    payload = _manifest_payload(index)
    if not index.is_file():
        return payload
    connection, confirmed = _connect_consistent_pair(
        index,
        expected_generation=expected_generation,
    )
    connection.close()
    return confirmed


def read_consistent_manifest(
    index_dir: Path,
    *,
    expected_generation: str | None = None,
) -> dict[str, Any]:
    return read_consistent_index_manifest(
        index_dir / "rag.sqlite",
        expected_generation=expected_generation,
    )


__all__ = [
    "RagGenerationTransitionError",
    "connect_consistent_readonly",
    "read_consistent_index_manifest",
    "read_consistent_manifest",
]
