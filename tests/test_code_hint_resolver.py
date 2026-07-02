#!/usr/bin/env python
"""Tests for code_hint_resolver Source/**/Domain discovery."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from code_hint_resolver import find_domain_dirs, resolve_code_domain_hint  # noqa: E402
from project_context import resolve_active_project_context  # noqa: E402


def test_find_domain_dirs_under_other_module(tmp_path):
    project_dir = tmp_path / "OtherGame"
    cinematic = project_dir / "Source" / "OtherModule" / "Cinematic"
    cinematic.mkdir(parents=True)
    matches = find_domain_dirs(project_dir, "Cinematic")
    assert len(matches) == 1
    assert matches[0]["moduleName"] == "OtherModule"
    assert matches[0]["domainFolder"] == "Cinematic"


def test_resolve_combat_domain_demo_game(demo_game_project, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(demo_game_project["projectDir"].parent))
    ctx = resolve_active_project_context()
    payload = resolve_code_domain_hint("Combat C++ subsystem", ctx)
    assert payload["ok"] is True
    assert payload["domainFolder"] == "Combat"
    assert payload["projectName"] == "DemoGame"
    assert payload["suggestedToolCalls"]
    assert payload["suggestedToolCalls"][-1]["tool"] == "unreal_rag_search"


def test_resolve_cinematic_lyra_style(lyra_style_project, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(lyra_style_project["projectDir"].parent))
    ctx = resolve_active_project_context()
    payload = resolve_code_domain_hint("C++ cinematic", ctx)
    assert payload["ok"] is True
    assert payload["domainFolder"] == "Cinematic"
    assert payload["domainSourcePaths"][0]["moduleName"] == "LyraGame"
