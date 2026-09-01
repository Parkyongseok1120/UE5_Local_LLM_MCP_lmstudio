from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from direct_rag_result import to_mcp_tool_result  # noqa: E402
from direct_rag_request_bounds import (  # noqa: E402
    MAX_PROJECT_SELECTORS,
    MAX_QUERY_CHARS,
)


@pytest.mark.parametrize(
    ("module_name", "handler_name", "tool_name"),
    [
        ("direct_rag_search", "rag_search", "unreal_rag_search"),
        ("direct_rag_symbol", "symbol_lookup_capability", "unreal_symbol_lookup"),
    ],
)
def test_configured_floor_rejects_schema_valid_max_query_before_index_resolution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    module_name: str,
    handler_name: str,
    tool_name: str,
) -> None:
    module = __import__(module_name)
    monkeypatch.setenv("MCP_TOOL_RESULT_MAX_CHARS", "2000")

    def unexpected_resolution(*_args, **_kwargs):
        raise AssertionError("request bounds must run before index resolution")

    monkeypatch.setattr(module, "resolve_request_index", unexpected_resolution)
    result = getattr(module, handler_name)(
        SimpleNamespace(index=tmp_path / "missing.sqlite", workspace=tmp_path),
        {"query": "q" * MAX_QUERY_CHARS, "detailLevel": "compact"},
    )
    rendered = to_mcp_tool_result(result, tool_name=tool_name)

    assert rendered["structuredContent"]["errorCode"] == "INVALID_TOOL_ARGUMENTS"
    assert rendered["structuredContent"]["retry"]["allowed"] is True
    assert rendered["structuredContent"].get("errorCode") != "OUTPUT_LIMIT_EXCEEDED"


@pytest.mark.parametrize(
    ("module_name", "handler_name", "tool_name"),
    [
        ("direct_rag_search", "rag_search", "unreal_rag_search"),
        ("direct_rag_symbol", "symbol_lookup_capability", "unreal_symbol_lookup"),
    ],
)
def test_configured_floor_rejects_aggregate_exact_project_metadata_without_clipping(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    module_name: str,
    handler_name: str,
    tool_name: str,
) -> None:
    module = __import__(module_name)
    monkeypatch.setenv("MCP_TOOL_RESULT_MAX_CHARS", "2000")
    selectors = [
        f"C:/Projects/Owner{number:02d}/LongProjectName/LongProjectName.uproject"
        for number in range(MAX_PROJECT_SELECTORS)
    ]

    def unexpected_resolution(*_args, **_kwargs):
        raise AssertionError("aggregate bounds must run before index resolution")

    monkeypatch.setattr(module, "resolve_request_index", unexpected_resolution)
    result = getattr(module, handler_name)(
        SimpleNamespace(index=tmp_path / "missing.sqlite", workspace=tmp_path),
        {"query": "Actor", "project": selectors, "detailLevel": "compact"},
    )
    rendered = to_mcp_tool_result(result, tool_name=tool_name)

    assert rendered["structuredContent"]["errorCode"] == "INVALID_TOOL_ARGUMENTS"
    assert rendered["structuredContent"].get("errorCode") != "OUTPUT_LIMIT_EXCEEDED"


def test_compact_search_fits_dense_evidence_inside_the_serialized_envelope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import direct_rag_search

    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"index")
    rows = [
        {
            "chunk_id": f"fact:{number}",
            "source": "unreal_project_text",
            "title": "DenseActor.cpp" + ("t" * 500),
            "locator": "Source/Demo/DenseActor.cpp:" + ("9" * 500),
            "project": "Demo" + ("p" * 500),
            "symbol_name": "ADenseActor" + ("s" * 500),
        }
        for number in range(6)
    ]
    page = SimpleNamespace(
        rows=rows,
        context="evidence\n" + ("x\n" * 5_000),
        resolved_scope="project",
        detail_level="compact",
        freshness={"indexUsable": True},
        explicit_projects=["Demo"],
        selected_projects=["Demo"],
        stale_rows_suppressed=0,
        truncated=False,
    )
    monkeypatch.setattr(direct_rag_search, "deliver", lambda **_kwargs: {})
    monkeypatch.setattr(direct_rag_search, "retrieve", lambda *_args, **_kwargs: page)

    result = direct_rag_search.rag_search(
        SimpleNamespace(index=index),
        {"query": "DenseActor"},
    )
    rendered = to_mcp_tool_result(result, tool_name="unreal_rag_search")

    assert rendered["structuredContent"]["ok"] is True
    assert rendered["structuredContent"]["evidenceEnvelopeTruncated"] is True
    assert rendered["structuredContent"]["nextDetailLevel"] == "medium"
    assert len(rendered["content"][0]["text"]) <= 10_000
    assert rendered["structuredContent"].get("errorCode") != "OUTPUT_LIMIT_EXCEEDED"


def test_compact_symbol_lookup_fits_dense_evidence_inside_the_serialized_envelope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import direct_rag_symbol

    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"index")
    rows = [
        {
            "chunk_id": f"symbol:{number}",
            "source": "unreal_symbol",
            "title": "DenseActor.h" + ("t" * 500),
            "locator": "Source/Demo/DenseActor.h:" + ("9" * 500),
            "project": "__engine__",
            "symbol_name": "ADenseActor" + ("s" * 500),
            "symbol_kind": "class",
            "rank_score": float(number),
            "text": "symbol evidence\n" + ("x\n" * 2_000),
        }
        for number in range(6)
    ]
    monkeypatch.setattr(
        direct_rag_symbol,
        "resolve_request_index",
        lambda *_args, **_kwargs: {
            "ok": True,
            "index": str(index),
            "indexEngineVersion": "5.8",
        },
    )
    monkeypatch.setattr(direct_rag_symbol, "active_project_names", lambda: [])
    monkeypatch.setattr(direct_rag_symbol, "exact_project_roots", lambda *_args, **_kwargs: ([], []))
    monkeypatch.setattr(direct_rag_symbol, "symbol_lookup", lambda *_args, **_kwargs: rows)
    monkeypatch.setattr(direct_rag_symbol, "project_freshness", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(direct_rag_symbol, "resolve_symbol_target", lambda *_args, **_kwargs: {})

    result = direct_rag_symbol.symbol_lookup_capability(
        SimpleNamespace(index=index, workspace=tmp_path),
        {"query": "ADenseActor", "detailLevel": "compact"},
    )
    rendered = to_mcp_tool_result(result, tool_name="unreal_symbol_lookup")

    assert rendered["structuredContent"]["ok"] is True
    assert rendered["structuredContent"]["evidenceEnvelopeTruncated"] is True
    assert len(rendered["content"][0]["text"]) <= 10_000
    assert rendered["structuredContent"].get("errorCode") != "OUTPUT_LIMIT_EXCEEDED"


def test_evidence_formatter_and_match_refs_honor_their_character_budgets() -> None:
    from direct_rag_evidence import compact_match_refs, format_evidence_rows

    rows = [
        {
            "chunk_id": f"fact:{number}",
            "source": "unreal_project_text",
            "title": "Title" + ("t" * 700),
            "locator": "Source/Demo/File.cpp:" + ("9" * 700),
            "text": "line\n" + ("x" * 4_000),
        }
        for number in range(8)
    ]
    evidence, truncated = format_evidence_rows(
        rows,
        max_chars=1_000,
        max_chars_per_row=700,
    )
    tiny_evidence, tiny_truncated = format_evidence_rows(
        rows,
        max_chars=24,
        max_chars_per_row=12,
    )
    row_clipped_evidence, row_clipped_truncated = format_evidence_rows(
        [{"chunk_id": "fact:row", "text": "x" * 1_000}],
        max_chars=2_000,
        max_chars_per_row=64,
    )
    refs = compact_match_refs(rows, max_chars=1_200)

    assert truncated is True
    assert len(evidence) <= 1_000
    assert tiny_truncated is True
    assert len(tiny_evidence) <= 24
    assert row_clipped_truncated is True
    assert "[row truncated]" in row_clipped_evidence
    assert len(json.dumps(refs, ensure_ascii=False, separators=(",", ":"))) <= 1_200
    assert len(refs) < len(rows)


def test_mixed_retrieval_interleaves_sources_and_obeys_effective_top_k(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import direct_rag_retrieval
    from rag_types import SearchOptions

    options = SearchOptions(mode="review", projects=["Demo"], evidence_only=True)
    monkeypatch.setattr(
        direct_rag_retrieval,
        "search_options",
        lambda *_args, **_kwargs: (options, "mixed", ["Demo"], ["Demo"], []),
    )
    monkeypatch.setattr(
        direct_rag_retrieval,
        "project_freshness",
        lambda *_args, **_kwargs: {"directSourcePreferred": False},
    )

    def fake_search(_index, _query, top_k, search_options, **_kwargs):
        project = "__engine__" if search_options.projects == ["__engine__"] else "Demo"
        return [
            {
                "chunk_id": f"{project}:{number}",
                "source": "unreal_project_text" if project == "Demo" else "unreal_engine_text",
                "project": project,
                "text": f"{project} evidence {number}",
            }
            for number in range(top_k)
        ]

    monkeypatch.setattr(direct_rag_retrieval, "lexical_search", fake_search)
    page = direct_rag_retrieval.retrieve(
        tmp_path / "rag.sqlite",
        "DenseActor",
        16,
        {"scope": "mixed", "mode": "review", "detailLevel": "compact"},
    )

    assert len(page.rows) == 6
    assert [row["project"] for row in page.rows] == [
        "Demo",
        "__engine__",
        "Demo",
        "__engine__",
        "Demo",
        "__engine__",
    ]
