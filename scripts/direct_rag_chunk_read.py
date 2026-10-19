#!/usr/bin/env python
"""Page the full text of one indexed RAG chunk (``unreal_rag_search chunkId=``).

Search evidence is clipped to a per-row budget. When the clipped text is not
enough, the caller can fetch the one chunk by id and page through it with
``offset`` and ``maxChars``. The page is read from the same generation-checked,
read-only index that produced the id; it is cached index text, not a fresh read
of the project source.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from direct_rag_generation_identity import connect_consistent_readonly
from direct_rag_index_registry import resolve_request_index
from direct_rag_limits import response_char_limit
from direct_rag_result import CapabilityResult, configured_result_limit, failure, render_model_json

MAX_CHUNK_ID_CHARS = 512
_METADATA_RESERVE = 96


CHUNK_NEXT_ACTION = (
    "Evidence text was clipped. To read one match in full, call unreal_rag_search with "
    "chunkId=<matches[].chunkId> and page with offset/nextOffset."
)


def chunk_argument_error(arguments: dict[str, Any]) -> str | None:
    if "chunkId" not in arguments:
        return "offset applies only together with chunkId."
    if str(arguments.get("query") or "").strip():
        return "Use chunkId or query, not both."
    chunk_id = arguments.get("chunkId")
    if not isinstance(chunk_id, str) or not chunk_id.strip() or len(chunk_id) > MAX_CHUNK_ID_CHARS:
        return f"chunkId must be a non-empty string of at most {MAX_CHUNK_ID_CHARS} characters."
    offset = arguments.get("offset", 0)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        return "offset must be a non-negative integer."
    return None


def read_chunk_page(runtime: Any, arguments: dict[str, Any]) -> CapabilityResult:
    problem = chunk_argument_error(arguments)
    if problem:
        return failure("INVALID_TOOL_ARGUMENTS", problem, retry_allowed=True)
    chunk_id = str(arguments["chunkId"]).strip()
    offset = int(arguments.get("offset", 0))
    limit = response_char_limit("compact", arguments.get("maxChars"), configured_result_limit())
    resolution = resolve_request_index(
        runtime.index,
        getattr(runtime, "workspace", Path.cwd()),
        project_selector=arguments.get("project"),
        use_active=arguments.get("use_active_project", True) is not False,
    )
    if resolution.get("ok") is not True:
        return failure(str(resolution.get("errorCode") or "RAG_ENGINE_INDEX_MISMATCH"),
                       str(resolution.get("error") or "No compatible Unreal RAG index is available."),
                       retry_allowed=resolution.get("retryAllowed") is True, retry_mode="same_arguments")
    index = Path(str(resolution["index"]))
    if not index.is_file():
        return failure("RAG_INDEX_MISSING", f"RAG index does not exist: {index}")
    expected = str(resolution.get("indexGenerationId") or "").strip() or None
    connection = connect_consistent_readonly(index, expected_generation=expected)
    try:
        row = connection.execute(
            "select chunk_id, source, title, locator, project, chunk_index, text from chunks where chunk_id = ?",
            (chunk_id,)).fetchone()
    except sqlite3.Error as exc:
        return failure("RAG_INDEX_UNREADABLE", f"The index could not be read: {exc}")
    finally:
        connection.close()
    if row is None:
        # Only this index generation was searched; the id may belong to another one.
        return failure("CHUNK_NOT_FOUND", "No chunk with this id exists in the selected index generation.",
                       retry_allowed=False, chunkId=chunk_id, absenceVerified=False,
                       scope="selected_index_generation")
    text = str(row[6] or "")
    total = len(text)
    if offset > total:
        return failure("INVALID_TOOL_ARGUMENTS", f"offset {offset} is beyond the chunk length {total}.",
                       retry_allowed=True, chunkId=chunk_id, totalChars=total)
    payload: dict[str, Any] = {
        "ok": True, "chunkId": str(row[0]), "source": str(row[1] or ""), "title": str(row[2] or ""),
        "locator": str(row[3] or ""), "project": str(row[4] or ""), "chunkIndex": int(row[5] or 0),
        "evidenceSource": "cached_index_text", "indexPath": str(index),
        "chunkSha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "totalChars": total, "offset": offset, "content": "", "nextOffset": None, "hasMore": False,
    }
    # Largest page whose serialized payload fits the response cap (escaping can inflate it).
    room = max(0, limit - len(render_model_json(payload)) - _METADATA_RESERVE)
    length = min(total - offset, room)
    while True:
        page = text[offset:offset + length]
        payload.update(content=page, returnedChars=len(page),
                       nextOffset=offset + len(page) if offset + len(page) < total else None,
                       hasMore=offset + len(page) < total)
        if len(render_model_json(payload)) + _METADATA_RESERVE <= limit or length == 0:
            break
        length = max(0, int(length * 0.9) - 1)
    if payload["hasMore"] and not payload["content"]:
        return failure("OUTPUT_LIMIT_EXCEEDED", "maxChars is too small to return any text of this chunk.",
                       retry_allowed=True, chunkId=chunk_id, totalChars=total)
    return CapabilityResult(payload, char_limit=limit)


__all__ = ["CHUNK_NEXT_ACTION", "chunk_argument_error", "read_chunk_page"]
