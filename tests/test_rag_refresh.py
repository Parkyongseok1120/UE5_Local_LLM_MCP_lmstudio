#!/usr/bin/env python
"""Tests for manual RAG refresh entry points."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rag_refresh  # noqa: E402


def test_refresh_active_project_without_active_project(monkeypatch):
    import workspace_paths

    monkeypatch.setattr(workspace_paths, "resolve_active_project_path", lambda: None)

    payload = rag_refresh.refresh_active_project()

    assert payload["ok"] is False
    assert "No activeProject" in payload["error"]


def test_refresh_active_project_invalidates_caches(monkeypatch, tmp_path):
    import active_project_sync
    import on_active_project_changed
    import project_context
    import project_switch_invalidate
    import index_staleness
    import workspace_paths

    uproject = tmp_path / "Demo.uproject"
    uproject.write_text("{}", encoding="utf-8")
    calls = {"sync": 0, "setup": 0, "context": 0, "wrapper": 0, "staleness": 0}

    monkeypatch.setattr(workspace_paths, "resolve_active_project_path", lambda: uproject)
    monkeypatch.setattr(workspace_paths, "find_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(
        active_project_sync,
        "sync_active_project",
        lambda **kwargs: calls.__setitem__("sync", calls["sync"] + 1) or {"ok": True},
    )
    monkeypatch.setattr(
        on_active_project_changed,
        "ensure_active_project_ready",
        lambda *args, **kwargs: calls.__setitem__("setup", calls["setup"] + 1) or {"ok": True},
    )
    monkeypatch.setattr(
        project_context,
        "clear_project_context_cache",
        lambda: calls.__setitem__("context", calls["context"] + 1),
    )
    monkeypatch.setattr(
        project_switch_invalidate,
        "clear_wrapper_snapshot_cache",
        lambda: calls.__setitem__("wrapper", calls["wrapper"] + 1),
    )
    monkeypatch.setattr(
        index_staleness,
        "invalidate_stale_cache",
        lambda: calls.__setitem__("staleness", calls["staleness"] + 1),
    )

    payload = rag_refresh.refresh_active_project(scope="editor_metadata", workspace=tmp_path, project=uproject)

    assert payload["ok"] is True
    assert payload["scope"] == "editor_metadata"
    assert calls["setup"] == 1
    assert calls["context"] == 1
    assert calls["wrapper"] == 1
    assert calls["staleness"] == 1
    assert calls["sync"] == 0
