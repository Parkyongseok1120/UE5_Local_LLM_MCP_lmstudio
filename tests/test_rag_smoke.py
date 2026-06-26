#!/usr/bin/env python
"""Smoke tests for Unreal58-RAG path helpers and index health."""

from __future__ import annotations

import json
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
SCRIPTS = WORKSPACE / "scripts"
sys.path.insert(0, str(SCRIPTS))

from rag_index_ops import index_health, rebuild_status  # noqa: E402
from workspace_paths import (  # noqa: E402
    active_project_names,
    normalize_locator,
    resolve_index_path,
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
    import sqlite3

    index = resolve_index_path(WORKSPACE)
    if not index.exists():
        return
    try:
        health = index_health(index)
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower():
            return
        raise
    assert health["indexExists"] is True
    assert health["chunkCount"] > 0


def test_rebuild_status_if_present():
    import sqlite3

    index = resolve_index_path(WORKSPACE)
    if not index.exists():
        return
    try:
        status = rebuild_status(index)
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower():
            return
        raise
    assert "needsRebuild" in status
    assert "rawInputs" in status
