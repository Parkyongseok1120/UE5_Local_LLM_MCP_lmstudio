#!/usr/bin/env python
"""Strict _filter_project isolation tests."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from asset_graph_lookup import _filter_project  # noqa: E402


def test_filter_project_blocks_empty_project_rows():
    rows = [
        {"metadata": {"project": "", "asset_path": "/Game/A"}},
        {"metadata": {"project": "DemoGame", "asset_path": "/Game/B"}},
        {"metadata": {"project": "OtherGame", "asset_path": "/Game/C"}},
    ]
    filtered = _filter_project(rows, "DemoGame")
    assert len(filtered) == 1
    assert filtered[0]["metadata"]["asset_path"] == "/Game/B"


def test_filter_project_without_active_returns_all(monkeypatch):
    monkeypatch.setattr("project_row_filter.resolve_filter_project_name", lambda _name=None: "")
    rows = [{"metadata": {"project": "", "asset_path": "/Game/A"}}]
    assert _filter_project(rows, None) == rows
