#!/usr/bin/env python
"""Tests for active-project auto setup skip/run logic."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from install_editor_graph_plugin import (
    PLUGIN_NAME,
    host_unreal_platform,
    install_plugin,
    plugin_binary_path,
    plugin_needs_setup,
)
from on_active_project_changed import (
    active_project_check_status,
    auto_setup_enabled,
    ensure_active_project_ready,
    project_index_needs_sync,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_auto_setup_enabled_defaults_true() -> None:
    assert auto_setup_enabled({}) is True
    assert auto_setup_enabled({"autoSetupOnProjectSwitch": True}) is True
    assert auto_setup_enabled({"autoSetupOnProjectSwitch": False}) is False


def test_project_index_needs_sync_when_profile_missing(tmp_path: Path) -> None:
    project = tmp_path / "Demo.uproject"
    project.write_text('{"FileVersion": 3, "EngineAssociation": "5.8"}', encoding="utf-8")
    index_dir = tmp_path / "data"
    index_dir.mkdir()

    needed, reason = project_index_needs_sync(project, index_dir)
    assert needed is True
    assert reason == "missing_project_profile"


def test_project_index_needs_sync_when_architecture_missing(tmp_path: Path) -> None:
    project = tmp_path / "Demo.uproject"
    project.write_text('{"FileVersion": 3}', encoding="utf-8")
    index_dir = tmp_path / "data"
    _write_jsonl(
        index_dir / "raw_project_profiles.jsonl",
        [{"metadata": {"project": "Demo", "project_root": str(tmp_path)}}],
    )

    needed, reason = project_index_needs_sync(project, index_dir)
    assert needed is True
    assert reason == "missing_project_architecture"


def test_project_index_needs_sync_when_symbols_missing(tmp_path: Path) -> None:
    project = tmp_path / "Demo.uproject"
    project.write_text('{"FileVersion": 3, "EngineAssociation": "5.8"}', encoding="utf-8")
    index_dir = tmp_path / "data"
    _write_jsonl(
        index_dir / "raw_project_profiles.jsonl",
        [{"metadata": {"project": "Demo", "project_root": str(tmp_path)}}],
    )
    _write_jsonl(
        index_dir / "raw_project_architecture.jsonl",
        [{"metadata": {"project": "Demo", "project_root": str(tmp_path)}}],
    )

    needed, reason = project_index_needs_sync(project, index_dir)
    assert needed is True
    assert reason == "missing_project_symbols"


def test_project_index_needs_sync_when_symbols_are_for_other_project(tmp_path: Path) -> None:
    project = tmp_path / "Demo.uproject"
    project.write_text('{"FileVersion": 3}', encoding="utf-8")
    index_dir = tmp_path / "data"
    _write_jsonl(
        index_dir / "raw_project_profiles.jsonl",
        [{"metadata": {"project": "Demo", "project_root": str(tmp_path)}}],
    )
    _write_jsonl(
        index_dir / "raw_project_architecture.jsonl",
        [{"metadata": {"project": "Demo", "project_root": str(tmp_path)}}],
    )
    _write_jsonl(
        index_dir / "raw_project_symbols.jsonl",
        [{"metadata": {"project": "OtherGame", "symbol": "UFoo"}}],
    )

    needed, reason = project_index_needs_sync(project, index_dir)
    assert needed is True
    assert reason == "missing_project_symbols"


def test_project_index_skips_when_profile_symbols_and_index_fresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "Demo.uproject"
    project.write_text('{"FileVersion": 3, "EngineAssociation": "5.8"}', encoding="utf-8")
    index_dir = tmp_path / "data"
    _write_jsonl(
        index_dir / "raw_project_profiles.jsonl",
        [{"metadata": {"project": "Demo", "project_root": str(tmp_path)}}],
    )
    _write_jsonl(
        index_dir / "raw_project_architecture.jsonl",
        [{"metadata": {"project": "Demo", "project_root": str(tmp_path)}}],
    )
    symbols_path = index_dir / "raw_project_symbols.jsonl"
    _write_jsonl(symbols_path, [{"metadata": {"project": "Demo", "symbol": "UFoo"}}])

    monkeypatch.setattr(
        "on_active_project_changed._project_has_uassets",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "on_active_project_changed.manifest_stale",
        lambda *_args, **_kwargs: (False, "up-to-date"),
    )

    needed, reason = project_index_needs_sync(project, index_dir)
    assert needed is False
    assert reason == "up_to_date"


def test_project_index_needs_sync_when_asset_registry_missing_for_content_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "Demo.uproject"
    project.write_text('{"FileVersion": 3}', encoding="utf-8")
    index_dir = tmp_path / "data"
    _write_jsonl(
        index_dir / "raw_project_profiles.jsonl",
        [{"metadata": {"project": "Demo", "project_root": str(tmp_path)}}],
    )
    _write_jsonl(
        index_dir / "raw_project_architecture.jsonl",
        [{"metadata": {"project": "Demo", "project_root": str(tmp_path)}}],
    )
    _write_jsonl(
        index_dir / "raw_project_symbols.jsonl",
        [{"metadata": {"project": "Demo", "symbol": "UFoo"}}],
    )
    monkeypatch.setattr("on_active_project_changed._project_has_uassets", lambda *_args, **_kwargs: True)

    needed, reason = project_index_needs_sync(project, index_dir)
    assert needed is True
    assert reason == "missing_project_asset_registry"


def test_plugin_needs_setup_when_missing(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    plugin_src = workspace / "tools" / "ue_plugins" / PLUGIN_NAME
    plugin_src.mkdir(parents=True)
    (plugin_src / f"{PLUGIN_NAME}.uplugin").write_text('{"FileVersion": 3, "VersionName": "1"}', encoding="utf-8")
    (plugin_src / "Source.cpp").write_text("plugin", encoding="utf-8")

    project = tmp_path / "Game.uproject"
    project.write_text('{"FileVersion": 3, "Plugins": []}', encoding="utf-8")

    needed, reason = plugin_needs_setup(project, workspace)
    assert needed is True
    assert reason == "plugin_missing"


def test_plugin_binary_path_uses_native_host_layout(tmp_path: Path) -> None:
    project = tmp_path / "Demo.uproject"
    assert host_unreal_platform("darwin") == "Mac"
    assert host_unreal_platform("linux") == "Linux"
    assert plugin_binary_path(project, "darwin").name.endswith(".dylib")
    assert "Mac" in plugin_binary_path(project, "darwin").parts
    assert plugin_binary_path(project, "linux").name.endswith(".so")
    assert "Linux" in plugin_binary_path(project, "linux").parts


def test_plugin_skips_when_installed_enabled_and_compiled(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    plugin_src = workspace / "tools" / "ue_plugins" / PLUGIN_NAME
    plugin_src.mkdir(parents=True)
    (plugin_src / f"{PLUGIN_NAME}.uplugin").write_text('{"FileVersion": 3, "VersionName": "1"}', encoding="utf-8")
    (plugin_src / "Source.cpp").write_text("plugin", encoding="utf-8")

    project = tmp_path / "Game.uproject"
    project.write_text(
        json.dumps(
            {
                "FileVersion": 3,
                "Plugins": [{"Name": PLUGIN_NAME, "Enabled": True, "TargetAllowList": ["Editor"]}],
            }
        ),
        encoding="utf-8",
    )

    install_plugin(project=project, workspace=workspace, enable=True, update=True)
    binary = project.parent / "Plugins" / PLUGIN_NAME / "Binaries" / "Win64" / f"UnrealEditor-{PLUGIN_NAME}.dll"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b"compiled")

    needed, reason = plugin_needs_setup(project, workspace)
    assert needed is False
    assert reason == "ready"


def test_ensure_active_project_ready_skips_when_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "Demo.uproject"
    project.write_text('{"FileVersion": 3}', encoding="utf-8")

    monkeypatch.setattr("on_active_project_changed.find_workspace_root", lambda: tmp_path)
    monkeypatch.setattr("on_active_project_changed.load_shared_config", lambda: {"autoSetupOnProjectSwitch": False})
    monkeypatch.setattr("on_active_project_changed.resolve_project", lambda _p: project)
    monkeypatch.setattr("on_active_project_changed.resolve_index_dir", lambda: tmp_path / "data")

    payload = ensure_active_project_ready(project)
    assert payload["skipped"] is True
    assert payload["reason"] == "autoSetupOnProjectSwitch_disabled"


def test_ensure_active_project_ready_fast_path_for_unchanged_ready_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "Demo.uproject"
    project.write_text('{"FileVersion": 3}', encoding="utf-8")

    monkeypatch.setattr("on_active_project_changed.find_workspace_root", lambda: tmp_path)
    monkeypatch.setattr("on_active_project_changed.load_shared_config", lambda: {"autoSetupOnProjectSwitch": True})
    monkeypatch.setattr("on_active_project_changed.resolve_project", lambda _p: project)
    monkeypatch.setattr("on_active_project_changed.resolve_index_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(
        "on_active_project_changed.active_project_check_status",
        lambda *_args, **_kwargs: {"ready": True, "pluginNeeded": False, "syncNeeded": False},
    )

    payload = ensure_active_project_ready(project, previous_project=project)
    assert payload["skipped"] is True
    assert payload["reason"] == "already_ready_for_unchanged_project"


def test_active_project_check_status_reports_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "Demo.uproject"
    project.write_text('{"FileVersion": 3}', encoding="utf-8")
    workspace = tmp_path / "workspace"
    index_dir = tmp_path / "data"

    monkeypatch.setattr("on_active_project_changed.plugin_needs_setup", lambda *_a, **_k: (False, "ready"))
    monkeypatch.setattr("on_active_project_changed.project_index_needs_sync", lambda *_a, **_k: (False, "up_to_date"))

    status = active_project_check_status(project, workspace, index_dir)
    assert status["ready"] is True
    assert status["pluginNeeded"] is False
    assert status["syncNeeded"] is False
