"""Tests for RAG staleness capability split and query repeat guard."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from index_staleness import invalidate_stale_cache, project_source_stale_status  # noqa: E402
from read_query_history import (  # noqa: E402
    check_repeat_query,
    delivery_variant_key,
    query_fingerprint,
    record_query_delivery,
    reset_query_history,
    semantic_query_key,
)
from unreal_rag_mcp import essential_tools_enabled, extended_tools_enabled  # noqa: E402


def test_repeat_query_suppresses_second_call(tmp_path: Path) -> None:
    reset_query_history()
    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"x" * 100)
    fp = query_fingerprint(
        tool="unreal_rag_search",
        active_project="C:/Proj/A.uproject",
        query="cinematic system",
        mode="review",
        scope="auto",
        detail_level="compact",
        top_k=4,
        hybrid=False,
        index_path=index,
    )
    assert check_repeat_query(fp)["repeatDetected"] is False
    record_query_delivery(fp, detail_level="compact", match_count=3)
    repeat = check_repeat_query(fp)
    assert repeat["repeatDetected"] is True
    assert repeat["doNotRetry"] is True
    assert repeat["fullContextSuppressed"] is True


def test_semantic_key_blocks_top_k_variant_repeat(tmp_path: Path) -> None:
    reset_query_history()
    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"z" * 64)
    semantic = semantic_query_key(
        tool="unreal_rag_search",
        active_project="C:/Proj/A.uproject",
        query="replication setup",
        mode="review",
        scope="auto",
        index_path=index,
    )
    fp4 = delivery_variant_key(
        tool="unreal_rag_search",
        active_project="C:/Proj/A.uproject",
        query="replication setup",
        mode="review",
        scope="auto",
        detail_level="compact",
        top_k=4,
        hybrid=False,
        index_path=index,
    )
    fp8 = delivery_variant_key(
        tool="unreal_rag_search",
        active_project="C:/Proj/A.uproject",
        query="replication setup",
        mode="review",
        scope="auto",
        detail_level="compact",
        top_k=8,
        hybrid=False,
        index_path=index,
    )
    assert fp4 != fp8
    record_query_delivery(fp4, detail_level="compact", match_count=2, semantic_key=semantic)
    repeat = check_repeat_query(fp8, semantic_key=semantic)
    assert repeat["repeatDetected"] is True


def test_detail_escalation_blocked_after_two_steps(tmp_path: Path) -> None:
    reset_query_history()
    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"w")
    fp = query_fingerprint(
        tool="unreal_rag_search",
        active_project="C:/Proj/A.uproject",
        query="gas ability",
        mode="review",
        scope="auto",
        detail_level="compact",
        top_k=4,
        hybrid=False,
        index_path=index,
    )
    record_query_delivery(fp, detail_level="compact", match_count=1)
    blocked = check_repeat_query(
        fp,
        allow_detail_escalation=True,
        previous_detail="compact",
        current_detail="large",
    )
    assert blocked["repeatDetected"] is True


def test_detail_escalation_allowed_after_first_delivery(tmp_path: Path) -> None:
    reset_query_history()
    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"y")
    fp = query_fingerprint(
        tool="unreal_rag_search",
        active_project="C:/Proj/A.uproject",
        query="cinematic",
        mode="review",
        scope="auto",
        detail_level="compact",
        top_k=4,
        hybrid=False,
        index_path=index,
    )
    record_query_delivery(fp, detail_level="compact", match_count=1)
    allowed = check_repeat_query(
        fp,
        allow_detail_escalation=True,
        previous_detail="compact",
        current_detail="medium",
    )
    assert allowed["repeatDetected"] is False


def test_project_sync_capabilities_advisory_when_source_newer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from on_active_project_changed import project_index_sync_capabilities

    project = tmp_path / "Demo.uproject"
    project.write_text("{}", encoding="utf-8")
    index_dir = tmp_path / "idx"
    index_dir.mkdir()
    (index_dir / "rag.sqlite").write_bytes(b"data")
    (index_dir / "raw_project_profiles.jsonl").write_text(
        json.dumps({"metadata": {"project": "Demo", "project_root": str(tmp_path)}}) + "\n",
        encoding="utf-8",
    )
    (index_dir / "raw_project_architecture.jsonl").write_text(
        json.dumps({"metadata": {"project": "Demo", "project_root": str(tmp_path)}}) + "\n",
        encoding="utf-8",
    )
    sym = index_dir / "raw_project_symbols.jsonl"
    sym.write_text(json.dumps({"metadata": {"project": "Demo"}}) + "\n", encoding="utf-8")
    source = tmp_path / "Source" / "Demo" / "Private"
    source.mkdir(parents=True)
    cpp = source / "A.cpp"
    cpp.write_text("void A(){}", encoding="utf-8")
    import os
    import time

    old = sym.stat().st_mtime
    os.utime(cpp, (time.time() + 60, time.time() + 60))
    caps = project_index_sync_capabilities(project, index_dir)
    assert caps["stale"] is True
    assert caps["stalenessSeverity"] == "advisory"
    assert caps["analysisCanProceed"] is True
    assert caps["directSourcePreferred"] is True
    _ = old


def test_essential_mode_hides_refresh_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.delenv("MCP_EXTENDED_TOOLS", raising=False)
    assert essential_tools_enabled() is True
    assert extended_tools_enabled() is False


def test_stale_status_no_recommended_refresh_tool_in_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    invalidate_stale_cache()
    project = tmp_path / "Demo.uproject"
    project.write_text("{}", encoding="utf-8")
    index_dir = tmp_path / "data" / "unreal58"
    index_dir.mkdir(parents=True)
    (index_dir / "rag.sqlite").write_bytes(b"chunks")
    monkeypatch.setattr("index_staleness.resolve_active_project_path", lambda: project)
    monkeypatch.setattr("index_staleness.resolve_index_dir", lambda: index_dir)
    monkeypatch.setattr(
        "index_staleness.project_index_sync_capabilities",
        lambda *_a, **_k: {
            "stale": True,
            "reason": "source_newer_than_symbols",
            "stalenessSeverity": "advisory",
            "analysisCanProceed": True,
            "directSourcePreferred": True,
            "refreshRecommended": True,
            "refreshRequired": False,
            "indexUsable": True,
            "projectSourceFresh": False,
            "projectSymbolsFresh": False,
            "architectureFresh": True,
            "editorMetadataFresh": True,
        },
    )
    status = project_source_stale_status(force=True, search_mode="review")
    assert status["recommendedTool"] is None
    assert status["analysisCanProceed"] is True
