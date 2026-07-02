#!/usr/bin/env python
"""Tests for resolve_active_project_context()."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from project_context import resolve_active_project_context  # noqa: E402


def test_project_context_demo_game(demo_game_project, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(demo_game_project["projectDir"].parent))
    ctx = resolve_active_project_context()
    assert ctx["ok"] is True
    assert ctx["projectName"] == "DemoGame"
    assert ctx["primaryModule"] == "DemoGame"
    assert ctx["sourceRoot"].endswith("Source\\DemoGame") or ctx["sourceRoot"].endswith("Source/DemoGame")
    assert ctx["exportDir"].endswith("Saved\\LmStudioMetadataExports") or ctx["exportDir"].endswith(
        "Saved/LmStudioMetadataExports"
    )


def test_project_context_lyra_style_module_name(lyra_style_project, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(lyra_style_project["projectDir"].parent))
    ctx = resolve_active_project_context()
    assert ctx["ok"] is True
    assert ctx["projectName"] == "LyraStyleGame"
    assert ctx["primaryModule"] == "LyraGame"
    assert "LyraGame" in ctx["sourceModules"]


def test_project_context_outside_workspace(tmp_path, shared_config_path, monkeypatch):
    workspace = tmp_path / "Workspace"
    workspace.mkdir()
    project_dir = tmp_path / "OutsideProject" / "Other"
    project_dir.mkdir(parents=True)
    uproject = project_dir / "Other.uproject"
    uproject.write_text(
        json.dumps({"Modules": [{"Name": "Other", "Type": "Runtime"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (project_dir / "Source" / "Other").mkdir(parents=True, exist_ok=True)
    shared_config_path.write_text(json.dumps({"activeProject": str(uproject)}), encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace))
    ctx = resolve_active_project_context()
    assert ctx["ok"] is True
    assert ctx["browseAvailable"] is False
    assert ctx["sourceBrowsePath"] == ""


def test_project_context_missing_active_project(shared_config_path):
    shared_config_path.write_text("{}", encoding="utf-8")
    ctx = resolve_active_project_context()
    assert ctx["ok"] is False
    assert ctx["suggestedToolCalls"][0]["tool"] == "unreal_set_active_project"
