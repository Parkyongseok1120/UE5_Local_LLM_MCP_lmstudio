#!/usr/bin/env python
"""Smoke tests for Unreal58-RAG path helpers and index health."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parents[1]
SCRIPTS = WORKSPACE / "scripts"
sys.path.insert(0, str(SCRIPTS))

from rag_index_ops import index_health, rebuild_status  # noqa: E402
from workspace_paths import (  # noqa: E402
    _discover_engine_roots,
    _engine_location_candidates,
    active_project_names,
    normalize_locator,
    resolve_engine_root,
    resolve_index_dir,
    resolve_index_path,
    resolve_ubt_path,
)


def test_normalize_locator_rewrites_legacy_prefix(tmp_path, monkeypatch):
    workspace = tmp_path / "Unreal58-RAG"
    workspace.mkdir()
    config_dir = workspace / "config"
    config_dir.mkdir()
    (config_dir / "workspace.json").write_text(
        json.dumps({"rootPath": str(workspace)}),
        encoding="utf-8",
    )
    physical = tmp_path / "physical-clone"
    physical.mkdir()
    monkeypatch.setenv("UNREAL58_ROOT", str(workspace))
    legacy = str(physical / "data" / "foo.txt")
    normalized = normalize_locator(legacy, physical)
    assert normalized == str(workspace / "data" / "foo.txt")


def test_normalize_locator_does_not_merge_i_dot_sibling_roots(tmp_path, monkeypatch):
    physical = tmp_path / "\u0130Project"
    canonical = tmp_path / "Canonical"
    sibling = tmp_path / "I\u0307Project"
    for root in (physical, canonical, sibling):
        root.mkdir(exist_ok=True)
    if physical.samefile(sibling):
        pytest.skip("host filesystem aliases the two Unicode spellings")
    monkeypatch.setattr("workspace_paths.canonical_workspace_root", lambda _start=None: canonical)
    candidate = sibling / "Source" / "Thing.cpp"
    for host_platform in ("linux", "darwin", "win32"):
        assert normalize_locator(
            str(candidate),
            physical,
            host_platform=host_platform,
        ) == str(candidate)


def test_normalize_locator_folds_ascii_root_alias_only_on_windows(tmp_path, monkeypatch):
    physical = tmp_path / "ProjectRoot"
    canonical = tmp_path / "Canonical"
    physical.mkdir()
    canonical.mkdir()
    monkeypatch.setattr("workspace_paths.canonical_workspace_root", lambda _start=None: canonical)
    candidate = str(physical / "Source" / "Thing.cpp").replace("ProjectRoot", "PROJECTROOT")
    assert normalize_locator(candidate, physical, host_platform="win32") == str(
        canonical / "Source" / "Thing.cpp"
    )
    assert normalize_locator(candidate, physical, host_platform="linux") == candidate


def test_active_project_names_from_shared_config(tmp_path, monkeypatch):
    shared = tmp_path / "unreal-workspace.json"
    project = tmp_path / "LyraStarterGame.uproject"
    project.write_text("{}", encoding="utf-8")
    shared.write_text(
        json.dumps({"activeProject": str(project)}),
        encoding="utf-8",
    )
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(shared))
    names = active_project_names()
    assert "LyraStarterGame" in names


def test_index_health_if_present():
    index = resolve_index_path(WORKSPACE)
    if not index.exists():
        return
    health = index_health(index)
    assert health["indexExists"] is True
    if not health.get("indexReadable", True):
        assert health.get("indexError")
        return
    assert health["chunkCount"] > 0


def test_rebuild_status_if_present():
    index = resolve_index_path(WORKSPACE)
    if not index.exists():
        return
    status = rebuild_status(index)
    assert "needsRebuild" in status
    assert "rawInputs" in status
    if not status.get("indexReadable", True):
        assert status["needsRebuild"] is True
        assert status["reason"] == "index-unreadable"


def test_default_index_path_is_native_when_workspace_config_is_absent(tmp_path):
    workspace = tmp_path / "UE5_Local_LLM_MCP_lmstudio"
    workspace.mkdir()

    assert resolve_index_path(workspace) == (
        workspace / "data" / "unreal58" / "rag.sqlite"
    ).resolve()


def test_index_path_normalizes_foreign_relative_separators(tmp_path):
    workspace = tmp_path / "UE5_Local_LLM_MCP_lmstudio"
    config_dir = workspace / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "workspace.json").write_text(
        json.dumps({
            "rootPath": str(workspace),
            "indexNamespace": "unreal59",
            "indexPath": r"data\unreal59\rag.sqlite",
        }),
        encoding="utf-8",
    )

    assert resolve_index_path(workspace) == (
        workspace / "data" / "unreal59" / "rag.sqlite"
    ).resolve()


def test_shared_index_config_drives_unconfigured_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "UE5_Local_LLM_MCP_lmstudio"
    workspace.mkdir()
    shared = tmp_path / "unreal-workspace.json"
    shared.write_text(
        json.dumps(
            {
                "engineVersion": "5.10",
                "indexNamespace": "unreal510",
                "indexPath": r"data\unreal510\rag.sqlite",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(shared))

    assert resolve_index_path(workspace) == (
        workspace / "data" / "unreal510" / "rag.sqlite"
    ).resolve()
    assert resolve_index_dir(workspace) == (workspace / "data" / "unreal510").resolve()


def test_workspace_index_config_overrides_shared_selection(tmp_path, monkeypatch):
    workspace = tmp_path / "UE5_Local_LLM_MCP_lmstudio"
    config_dir = workspace / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "workspace.json").write_text(
        json.dumps({"engineVersion": "5.9", "indexNamespace": "unreal59"}),
        encoding="utf-8",
    )
    shared = tmp_path / "unreal-workspace.json"
    shared.write_text(
        json.dumps({"engineVersion": "5.10", "indexNamespace": "unreal510"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(shared))

    assert resolve_index_path(workspace) == (
        workspace / "data" / "unreal59" / "rag.sqlite"
    ).resolve()


def test_stale_configured_root_falls_back_to_discovered_workspace(tmp_path):
    workspace = tmp_path / "UE5_Local_LLM_MCP_lmstudio"
    config_dir = workspace / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "workspace.json").write_text(
        json.dumps({
            "rootPath": str(tmp_path / "missing-other-host-root"),
            "indexPath": "data/unreal58/rag.sqlite",
        }),
        encoding="utf-8",
    )

    assert resolve_index_path(workspace) == (
        workspace / "data" / "unreal58" / "rag.sqlite"
    ).resolve()


def test_index_health_handles_missing_chunks_table(tmp_path):
    import sqlite3

    index = tmp_path / "rag.sqlite"
    sqlite3.connect(index).close()
    health = index_health(index)
    assert health["indexExists"] is True
    assert health["indexReadable"] is False
    assert health["okForChat"] is False
    assert health["chatAction"] == "stop_and_report_rag_rebuild_required"
    assert "no such table" in health["indexError"].lower()
    assert health["executionStatus"] == "succeeded"
    assert health["indexStatus"] == "unavailable"
    assert health["projectBindingStatus"] == "unknown"
    assert health["errorCode"] == "RAG_INDEX_UNREADABLE"
    assert health["nextRequiredAction"] == "run_rag_build_or_doctor"

    status = rebuild_status(index)
    assert status["needsRebuild"] is True
    assert status["reason"] == "index-unreadable"
    assert status["chatAction"] == "stop_and_report_rag_rebuild_required"
    assert status["recommendedDoctorCommand"] == ".\\rag.ps1 doctor"


def test_index_health_is_total_when_index_path_is_a_directory(tmp_path):
    health = index_health(tmp_path)
    assert health["executionStatus"] == "succeeded"
    assert health["indexStatus"] == "unavailable"
    assert health["indexReadable"] is False
    assert health["errorCode"] == "RAG_INDEX_UNREADABLE"
    assert health["nextRequiredAction"] == "run_rag_build_or_doctor"
    assert health["okForChat"] is False


@pytest.mark.parametrize(
    ("configured_project", "expected_status", "expected_code", "expected_action"),
    [
        ("", "unbound", "RAG_PROJECT_UNBOUND", "stop_and_select_active_project"),
        ("missing.uproject", "stale", "RAG_PROJECT_BINDING_STALE", "stop_and_reselect_active_project"),
    ],
)
def test_rag_health_fails_closed_for_unbound_or_stale_project(
    tmp_path,
    monkeypatch,
    configured_project,
    expected_status,
    expected_code,
    expected_action,
):
    import unreal_rag_mcp

    index = tmp_path / "rag.sqlite"
    import sqlite3
    conn = sqlite3.connect(index)
    conn.execute("create table chunks (source text, layer text)")
    conn.execute("insert into chunks values ('project', 'source')")
    conn.commit()
    conn.close()
    configured = str(tmp_path / configured_project) if configured_project else ""
    monkeypatch.setattr(unreal_rag_mcp, "load_shared_config", lambda: {"activeProject": configured})
    monkeypatch.setattr(unreal_rag_mcp, "active_project_names", lambda: [])
    monkeypatch.setattr(unreal_rag_mcp, "embedding_status", lambda _index: {})
    captured = {}

    class FakeServer:
        def __init__(self):
            self.index = index

        def tool_result(self, _message_id, text, **_kwargs):
            captured.update(json.loads(text))

    unreal_rag_mcp._handle_unreal_rag_health(FakeServer(), 1, {})

    assert captured["executionStatus"] == "succeeded"
    assert captured["indexStatus"] == "ready"
    assert captured["projectBindingStatus"] == expected_status
    assert captured["okForChat"] is False
    assert captured["chatAction"] == expected_action
    assert captured["errorCode"] == expected_code
    assert captured["nextRequiredAction"] == "select_active_project"


def test_rag_health_accepts_only_an_existing_uproject_binding(tmp_path, monkeypatch):
    import sqlite3
    import unreal_rag_mcp

    index = tmp_path / "rag.sqlite"
    conn = sqlite3.connect(index)
    conn.execute("create table chunks (source text, layer text)")
    conn.execute("insert into chunks values ('project', 'source')")
    conn.commit()
    conn.close()
    project = tmp_path / "Demo.uproject"
    project.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(unreal_rag_mcp, "load_shared_config", lambda: {"activeProject": str(project)})
    monkeypatch.setattr(unreal_rag_mcp, "active_project_names", lambda: ["Demo"])
    monkeypatch.setattr(unreal_rag_mcp, "embedding_status", lambda _index: {})
    captured = {}

    class FakeServer:
        def __init__(self):
            self.index = index

        def tool_result(self, _message_id, text, **_kwargs):
            captured.update(json.loads(text))

    unreal_rag_mcp._handle_unreal_rag_health(FakeServer(), 1, {})

    assert captured["indexStatus"] == "ready"
    assert captured["projectBindingStatus"] == "bound"
    assert captured["okForChat"] is True
    assert captured["chatAction"] == "continue"
    assert captured["errorCode"] == ""
    assert captured["nextRequiredAction"] == "continue"


def test_rag_health_is_total_when_embedding_sidecar_is_corrupt(tmp_path, monkeypatch):
    import sqlite3
    import unreal_rag_mcp

    index = tmp_path / "rag.sqlite"
    conn = sqlite3.connect(index)
    conn.execute("create table chunks (source text, layer text)")
    conn.execute("insert into chunks values ('project', 'source')")
    conn.commit()
    conn.close()
    index.with_suffix(".embeddings.sqlite").write_text("not sqlite", encoding="utf-8")
    project = tmp_path / "Demo.uproject"
    project.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(unreal_rag_mcp, "load_shared_config", lambda: {"activeProject": str(project)})
    monkeypatch.setattr(unreal_rag_mcp, "active_project_names", lambda: ["Demo"])
    captured = {}

    class FakeServer:
        def __init__(self):
            self.index = index

        def tool_result(self, _message_id, text, **_kwargs):
            captured.update(json.loads(text))

    unreal_rag_mcp._handle_unreal_rag_health(FakeServer(), 1, {})

    assert captured["indexStatus"] == "ready"
    assert captured["projectBindingStatus"] == "bound"
    assert captured["okForChat"] is True
    assert captured["embeddings"]["status"] == "unavailable"
    assert captured["embeddings"]["errorCode"] == "RAG_EMBEDDING_STATUS_UNAVAILABLE"


def test_engine_root_has_no_hardcoded_unreal_install_fallback(tmp_path, monkeypatch):
    import workspace_paths

    workspace = tmp_path / "UE5_Local_LLM_MCP_lmstudio"
    config_dir = workspace / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "workspace.json").write_text(
        json.dumps({"rootPath": "", "defaultEngineRoot": ""}),
        encoding="utf-8",
    )
    shared = tmp_path / "unreal-workspace.json"
    shared.write_text(json.dumps({"defaultEngineRoot": ""}), encoding="utf-8")
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(shared))
    monkeypatch.delenv("UNREAL_ENGINE_ROOT", raising=False)
    monkeypatch.delenv("UNREAL_UBT_PATH", raising=False)
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.setattr(workspace_paths, "_discover_engine_roots", lambda: [])

    assert str(resolve_engine_root(workspace)) in {"", "."}
    expected_ubt = "UnrealBuildTool.exe" if sys.platform == "win32" else "UnrealBuildTool.dll"
    assert resolve_ubt_path(workspace) == Path(expected_ubt)


def test_engine_discovery_supports_mac_and_linux_common_layouts(tmp_path):
    assert Path("/Users/Shared/Epic Games") in _engine_location_candidates("darwin", {}, tmp_path)

    direct = tmp_path / "UnrealEngine"
    (direct / "Engine" / "Source").mkdir(parents=True)
    assert direct.resolve() in _discover_engine_roots("linux", {}, tmp_path)


def test_engine_discovery_orders_semantic_versions(tmp_path):
    parent = tmp_path / "Epic Games"
    for name in ("UE_5.9", "UE_5.10"):
        (parent / name / "Engine" / "Source").mkdir(parents=True)
    assert [path.name for path in _discover_engine_roots("linux", {}, tmp_path)] == [
        "UE_5.10",
        "UE_5.9",
    ]


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX-distinct Unicode engine roots")
def test_engine_discovery_windows_policy_does_not_unicode_casefold_i_dot_roots(tmp_path):
    parent = tmp_path / "Epic Games"
    engine_roots = [parent / name for name in ("UE_5.8-\u0130", "UE_5.8-I\u0307")]
    for engine_root in engine_roots:
        (engine_root / "Engine" / "Source").mkdir(parents=True, exist_ok=True)
    if engine_roots[0].samefile(engine_roots[1]):
        pytest.skip("host filesystem aliases the two Unicode spellings")
    roots = _discover_engine_roots(
        "win32",
        {"ProgramFiles": str(tmp_path), "ProgramFiles(x86)": ""},
        tmp_path,
    )
    assert {path.name for path in roots} == {"UE_5.8-\u0130", "UE_5.8-I\u0307"}
