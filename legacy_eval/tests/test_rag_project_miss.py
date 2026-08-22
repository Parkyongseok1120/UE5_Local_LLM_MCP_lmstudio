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


def test_repeat_history_is_scoped_by_normalized_explicit_projects(
    tmp_path: Path,
) -> None:
    reset_query_history()
    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"explicit-projects")
    common = {
        "tool": "unreal_rag_search",
        "active_project": "C:/Games/Active.uproject",
        "query": "SharedFeatureName",
        "mode": "review",
        "scope": "project",
        "detail_level": "compact",
        "top_k": 4,
        "hybrid": False,
        "index_path": index,
        "session_id": "same-session",
    }
    first = deliver_rag_result(
        **common,
        projects=["ProjectA"],
        rows=[{"project": "ProjectA", "path": "Source/A.cpp"}],
    )
    assert first["suppressed"] is False

    project_b = deliver_rag_result(
        **common,
        projects=["ProjectB"],
        rows=None,
    )
    assert project_b["suppressed"] is False
    assert project_b["semanticQueryKey"] != first["semanticQueryKey"]

    key_ab = semantic_query_key(
        tool="unreal_rag_search",
        active_project="C:/Games/Active.uproject",
        query="SharedFeatureName",
        mode="review",
        scope="project",
        index_path=index,
        session_id="same-session",
        projects=["ProjectB", "ProjectA", "ProjectA"],
    )
    key_ba = semantic_query_key(
        tool="unreal_rag_search",
        active_project="C:/Games/Active.uproject",
        query="SharedFeatureName",
        mode="review",
        scope="project",
        index_path=index,
        session_id="same-session",
        projects=["ProjectA", "ProjectB"],
    )
    assert key_ab == key_ba


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


def test_repeat_history_survives_mcp_process_memory_reset(monkeypatch, tmp_path: Path) -> None:
    import read_query_history as history

    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "agent-state"))
    reset_query_history()
    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"persistent")
    first = deliver_rag_result(
        tool="unreal_rag_search",
        active_project="C:/Games/Alpha.uproject",
        query="FGomokuMatchConfig",
        mode="review",
        scope="project",
        detail_level="compact",
        top_k=4,
        hybrid=False,
        index_path=index,
        session_id="stable-chat-session",
        rows=[{"path": "Source/Alpha/GomokuGameMode.h"}],
    )
    assert first["ok"] is True

    # Model an MCP host restart: process globals disappear while the state root remains.
    history._HISTORY.clear()
    history._HISTORY_ORDER.clear()
    history._SEMANTIC_INDEX.clear()
    history._TOPIC_INDEX.clear()
    history._CONTINUATION_TOKENS.clear()

    second = deliver_rag_result(
        tool="unreal_rag_search",
        active_project="C:/Games/Alpha.uproject",
        query="FGomokuMatchConfig",
        mode="review",
        scope="project",
        detail_level="compact",
        top_k=4,
        hybrid=False,
        index_path=index,
        session_id="stable-chat-session",
        rows=None,
    )
    assert second["suppressed"] is True
    assert second["repeat"]["repeatDetected"] is True


def test_direct_source_handoff_is_project_neutral() -> None:
    from unreal_rag_mcp import _direct_source_handoff

    handoff = _direct_source_handoff("inspect FGomokuMatchConfig in the current project")
    assert handoff["requiredNextTool"] == "search_files"
    assert handoff["requiredNextToolArgs"] == {
        "query": "FGomokuMatchConfig",
        "path": "project://Source",
        "maxResults": 40,
    }
    assert handoff["nextActionIsTool"] is True


def test_direct_source_handoff_preserves_explicit_project_selector() -> None:
    from direct_model_mode import normalize_direct_payload
    from unreal_rag_mcp import _direct_source_handoff

    handoff = _direct_source_handoff(
        "inspect FGomokuMatchConfig",
        ["C:/Games/Beta/Beta.uproject", "BetaAlias"],
    )
    assert handoff["requiredNextToolArgs"]["project"] == (
        "C:/Games/Beta/Beta.uproject"
    )
    assert handoff["nextActionArgs"]["project"] == (
        "C:/Games/Beta/Beta.uproject"
    )
    assert handoff["projectSelectors"] == [
        "C:/Games/Beta/Beta.uproject",
        "BetaAlias",
    ]
    normalized = normalize_direct_payload(handoff)
    assert normalized["suggestion"]["args"]["project"] == (
        "C:/Games/Beta/Beta.uproject"
    )
    assert normalized["projectSelectors"] == handoff["projectSelectors"]


def test_direct_search_runs_same_query_once_for_each_explicit_project(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import unreal_rag_mcp as mcp

    monkeypatch.delenv("MCP_EXECUTION_MODE", raising=False)
    monkeypatch.delenv("AGENT_STATE_ROOT", raising=False)
    monkeypatch.delenv("RAG_QUERY_HISTORY_PATH", raising=False)
    reset_query_history()
    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"selector-regression")
    server = mcp.McpServer(index)
    server.workspace = tmp_path
    sent: list[dict] = []
    server.send = sent.append
    monkeypatch.setattr(
        mcp,
        "load_shared_config",
        lambda: {"activeProject": "C:/Games/Active/Active.uproject"},
    )
    monkeypatch.setattr(mcp, "active_project_names", lambda: ["Active"])
    monkeypatch.setattr(
        "index_staleness.project_source_stale_status",
        lambda **_kwargs: {
            "ok": True,
            "stale": False,
            "analysisCanProceed": True,
            "directSourcePreferred": False,
            "refreshRecommended": False,
        },
    )
    searched: list[str] = []

    def fake_search(
        query: str,
        _top_k: int,
        arguments: dict,
        _hybrid: bool,
        *,
        stale_status: dict,
    ) -> tuple[list[dict], str, str, str, dict]:
        del query, stale_status
        project = arguments["project"][0]
        searched.append(project)
        return (
            [{"chunk_id": project, "project": project, "text": project}],
            f"evidence for {project}",
            "project",
            "compact",
            {
                "staleProjectRowsSuppressed": 0,
                "sourceDerivedProjectEvidenceSuppressed": False,
            },
        )

    server._run_search_with_diagnostics = fake_search
    common = {
        "query": "same feature query",
        "scope": "project",
        "mode": "review",
        "sessionId": "selector-session",
    }
    server.handle_search(101, {**common, "project": ["ProjectA"]})
    first = sent[-1]["result"]["structuredContent"]
    server.handle_search(102, {**common, "project": ["ProjectB"]})
    second = sent[-1]["result"]["structuredContent"]

    assert searched == ["ProjectA", "ProjectB"]
    assert first["projects"] == ["ProjectA"]
    assert second["projects"] == ["ProjectB"]
    assert first["semanticQueryKey"] != second["semanticQueryKey"]

    server.handle_search(103, {**common, "project": ["ProjectA"]})
    duplicate = sent[-1]["result"]["structuredContent"]
    assert searched == ["ProjectA", "ProjectB"]
    assert duplicate["status"] == "no_new_information"


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
