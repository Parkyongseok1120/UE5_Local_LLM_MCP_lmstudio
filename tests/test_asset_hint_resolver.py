#!/usr/bin/env python
"""Tests for asset_hint_resolver."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from asset_hint_resolver import (  # noqa: E402
    content_abs_path_to_game_path,
    normalize_folder_token,
    resolve_asset_folder_hint,
)
from project_context import resolve_active_project_context  # noqa: E402


def test_content_abs_path_to_game_path_c_drive(demo_game_project):
    project_dir = demo_game_project["projectDir"]
    game_path = content_abs_path_to_game_path(
        str(project_dir / "Content" / "Shaders" / "MF_Test" / "M_Test.uasset"),
        project_dir,
    )
    assert game_path == "/Game/Shaders/MF_Test/M_Test.uasset"


def test_content_abs_path_to_game_path_d_drive(tmp_path):
    project_dir = tmp_path / "DemoGame"
    content = project_dir / "Content" / "Shaders" / "MF_Test"
    content.mkdir(parents=True)
    game_path = content_abs_path_to_game_path(
        "D:/Games/DemoGame/Content/Shaders/MF_Test",
        project_dir,
    )
    assert game_path == "/Game/Shaders/MF_Test"


def test_normalize_folder_token_short_name():
    assert normalize_folder_token("MF_Test") == "MF_Test"
    assert normalize_folder_token("/Game/Shaders/MF_Test") == "/Game/Shaders/MF_Test"
    assert normalize_folder_token("MF_Test folder material analysis") == "MF_Test"


def test_resolve_asset_folder_hint_uses_active_project(demo_game_project, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(demo_game_project["projectDir"].parent))
    ctx = resolve_active_project_context()
    payload = resolve_asset_folder_hint("MF_Test folder", ctx)
    assert payload["ok"] is True
    assert payload["projectName"] == "DemoGame"
    assert payload["folderSegment"] == "MF_Test"
