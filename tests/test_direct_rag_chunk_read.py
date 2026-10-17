"""unreal_rag_search chunkId: page the full cached text of one indexed chunk."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import direct_rag_chunk_read as chunk_read  # noqa: E402
import direct_rag_contract as contract  # noqa: E402
import direct_rag_search as search  # noqa: E402
from direct_rag_result import to_mcp_tool_result  # noqa: E402

LONG_TEXT = "".join(f"line {i:04d}: \"quoted\" \\ value\n" for i in range(600))


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    index = tmp_path / "rag.sqlite"
    with sqlite3.connect(index) as db:
        db.execute("create table chunks (chunk_id text primary key, source text, title text, locator text,"
                   " project text, chunk_index integer, text text)")
        db.execute("insert into chunks values ('doc1:0', 'engine_docs', 'Actor', 'Actor.cpp:1', '', 0, ?)", (LONG_TEXT,))
        db.execute("insert into chunks values ('doc2:0', 'engine_docs', 'Tiny', 'Tiny', '', 0, 'short text')")
    monkeypatch.setattr(chunk_read, "resolve_request_index", lambda *a, **k: {"ok": True, "index": str(index)})
    return SimpleNamespace(index=index, workspace=tmp_path)


def call(runtime, **arguments):
    return to_mcp_tool_result(search.rag_search(runtime, arguments), tool_name="unreal_rag_search")["structuredContent"]


def test_pages_reassemble_the_exact_chunk_text_and_end_with_no_next_offset(runtime):
    pieces, offset, pages = [], 0, 0
    while offset is not None:
        page = call(runtime, chunkId="doc1:0", offset=offset, maxChars=2000)
        assert page["ok"] is True and page["evidenceSource"] == "cached_index_text"
        assert page["totalChars"] == len(LONG_TEXT) and page["offset"] == offset
        assert page["returnedChars"] == len(page["content"]) > 0
        pieces.append(page["content"])
        offset = page["nextOffset"]
        pages += 1
        assert pages < 200
    assert "".join(pieces) == LONG_TEXT
    assert pages > 3
    assert page["hasMore"] is False


def test_small_chunk_returns_in_one_page_with_metadata(runtime):
    page = call(runtime, chunkId="doc2:0")
    assert page["content"] == "short text" and page["nextOffset"] is None and page["hasMore"] is False
    assert page["title"] == "Tiny" and page["locator"] == "Tiny" and len(page["chunkSha256"]) == 64


def test_unknown_chunk_is_not_found_in_scope_never_declared_absent(runtime):
    result = call(runtime, chunkId="nope:9")
    assert result["ok"] is False and result["errorCode"] == "CHUNK_NOT_FOUND"
    assert result["absenceVerified"] is False and result["scope"] == "selected_index_generation"


@pytest.mark.parametrize("arguments, fragment", [
    ({"chunkId": "doc1:0", "query": "ActualDamage"}, "not both"),
    ({"chunkId": "doc1:0", "offset": -1}, "offset"),
    ({"chunkId": "doc1:0", "offset": 10**9}, "beyond"),
    ({"offset": 5, "query": "x"}, "only together with chunkId"),
])
def test_invalid_combinations_are_rejected_with_a_reason(runtime, arguments, fragment):
    result = call(runtime, **arguments)
    assert result["ok"] is False and fragment in result["message"]


def test_schema_exposes_chunk_paging_and_keeps_query_for_search(runtime):
    [tool] = [t for t in contract.direct_rag_tool_definitions() if t["name"] == "unreal_rag_search"]
    properties = tool["inputSchema"]["properties"]
    assert {"chunkId", "offset", "query", "maxChars"} <= set(properties)
    assert contract.validate_tool_arguments(tool, {"chunkId": "doc1:0", "offset": 0}) is None
    assert contract.validate_tool_arguments(tool, {"offset": 1.5}) is not None


def test_clipped_search_points_to_chunk_paging():
    assert "chunkId" in chunk_read.CHUNK_NEXT_ACTION and "offset" in chunk_read.CHUNK_NEXT_ACTION
