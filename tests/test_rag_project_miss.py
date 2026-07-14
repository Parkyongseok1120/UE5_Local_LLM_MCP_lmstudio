"""Tests for active-project RAG miss signaling and zero-result repeat guards."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from rag_context import assemble_context  # noqa: E402
from read_query_history import (  # noqa: E402
    check_repeat_query,
    query_fingerprint,
    record_query_delivery,
    reset_query_history,
    semantic_query_key,
)
from rag_delivery import deliver_rag_result  # noqa: E402
from tool_policy import load_tool_orchestration, tool_sequence_for_task  # noqa: E402


def test_zero_result_repeat_is_blocked(tmp_path: Path) -> None:
    reset_query_history()
    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"zero")
    fp = query_fingerprint(
        tool="unreal_rag_search",
        active_project="C:/Games/Alpha.uproject",
        query="MissingFeatureToken",
        mode="review",
        scope="project",
        detail_level="compact",
        top_k=4,
        hybrid=False,
        index_path=index,
    )
    record_query_delivery(fp, detail_level="compact", match_count=0, active_project="C:/Games/Alpha.uproject")
    repeat = check_repeat_query(fp)
    assert repeat["repeatDetected"] is True
    assert repeat["requiredNextAction"] == "search_files_then_read_file"


def test_zero_result_history_scoped_by_active_project(tmp_path: Path) -> None:
    reset_query_history()
    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"ab")
    key_a = semantic_query_key(
        tool="unreal_rag_search",
        active_project="C:/Games/Alpha.uproject",
        query="SharedFeatureName",
        mode="review",
        scope="project",
        index_path=index,
    )
    key_b = semantic_query_key(
        tool="unreal_rag_search",
        active_project="C:/Games/Beta.uproject",
        query="SharedFeatureName",
        mode="review",
        scope="project",
        index_path=index,
    )
    assert key_a != key_b
    fp_a = query_fingerprint(
        tool="unreal_rag_search",
        active_project="C:/Games/Alpha.uproject",
        query="SharedFeatureName",
        mode="review",
        scope="project",
        detail_level="compact",
        top_k=4,
        hybrid=False,
        index_path=index,
    )
    record_query_delivery(
        fp_a,
        detail_level="compact",
        match_count=0,
        active_project="C:/Games/Alpha.uproject",
        semantic_key=key_a,
    )
    fp_b = query_fingerprint(
        tool="unreal_rag_search",
        active_project="C:/Games/Beta.uproject",
        query="SharedFeatureName",
        mode="review",
        scope="project",
        detail_level="compact",
        top_k=4,
        hybrid=False,
        index_path=index,
    )
    assert check_repeat_query(fp_b, semantic_key=key_b)["repeatDetected"] is False


def test_deliver_rag_result_records_terminal_absence(tmp_path: Path) -> None:
    reset_query_history()
    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"d")
    first = deliver_rag_result(
        tool="unreal_rag_search",
        active_project="C:/Games/Alpha.uproject",
        query="AbsentThing",
        mode="review",
        scope="project_miss",
        detail_level="compact",
        top_k=4,
        hybrid=False,
        index_path=index,
        rows=[],
    )
    assert first["ok"] is True
    assert first["deliveredTerminalAbsence"] is True
    second = deliver_rag_result(
        tool="unreal_rag_search",
        active_project="C:/Games/Alpha.uproject",
        query="AbsentThing",
        mode="review",
        scope="project_miss",
        detail_level="compact",
        top_k=4,
        hybrid=False,
        index_path=index,
        rows=None,
    )
    assert second["suppressed"] is True
    assert second["ok"] is False


def test_empty_assembly_mentions_search_files() -> None:
    text = assemble_context([], "query", "review")
    assert "search_files" in text
    assert "Source" in text


def test_run_search_project_miss_skips_engine_fallback(monkeypatch, tmp_path: Path) -> None:
    import unreal_rag_mcp as mcp

    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"idx")
    server = mcp.McpServer.__new__(mcp.McpServer)
    server.index = index
    server.workspace = tmp_path

    options = SimpleNamespace(
        mode="review",
        sources=[],
        projects=["AlphaGame"],
        layers=[],
        doc_types=[],
        genres=[],
        extensions=[],
        required_terms=[],
        candidate_limit=32,
    )
    monkeypatch.setattr(
        server,
        "search_options_from_args",
        lambda *_a, **_k: (options, "project"),
    )
    monkeypatch.setattr(mcp, "search", lambda *_a, **_k: [])
    monkeypatch.setattr(
        mcp,
        "search_hybrid",
        lambda *_a, **_k: [{"chunk_id": "guideline", "text": "stamina"}],
    )
    monkeypatch.setattr(mcp, "active_project_names", lambda: ["AlphaGame"])

    rows, context, scope, detail = server.run_search(
        "missing feature inventory",
        4,
        {"mode": "review", "detailLevel": "compact"},
        False,
    )
    assert rows == []
    assert scope == "project_miss"
    assert "search_files" in context
    assert detail == "compact"


def test_inspect_only_orchestration_source_first() -> None:
    load_tool_orchestration.cache_clear()
    seq = tool_sequence_for_task("inspect_only")
    assert seq.index("search_files") < seq.index("unreal_rag_search")
    review = tool_sequence_for_task("project_review")
    assert review.index("search_files") < review.index("unreal_rag_search")
