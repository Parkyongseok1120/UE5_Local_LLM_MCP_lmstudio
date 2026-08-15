#!/usr/bin/env python
"""Tests for active-project auto setup skip/run logic."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from install_editor_graph_plugin import (
    PLUGIN_NAME,
    host_unreal_platform,
    install_and_build_plugin,
    install_plugin,
    maybe_build_plugin,
    plugin_binary_path,
    plugin_needs_setup,
)
from install_editor_graph_plugin import main as install_editor_graph_plugin_main
from on_active_project_changed import (
    active_project_check_status,
    auto_setup_enabled,
    ensure_active_project_ready,
    ensure_editor_plugin,
    project_index_needs_sync,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _plugin_workspace(tmp_path: Path, source_text: str = "new") -> Path:
    workspace = tmp_path / "workspace"
    plugin = workspace / "tools" / "ue_plugins" / PLUGIN_NAME
    plugin.mkdir(parents=True)
    (plugin / f"{PLUGIN_NAME}.uplugin").write_text(
        json.dumps({"FileVersion": 3, "VersionName": source_text}),
        encoding="utf-8",
    )
    source = plugin / "Source"
    source.mkdir()
    (source / "GraphExporter.cpp").write_text(source_text, encoding="utf-8")
    return workspace


def _engine_with_ubt(root: Path) -> tuple[Path, Path]:
    """Create the minimal engine layout used by plugin-build resolution tests."""

    root.joinpath("Engine", "Build").mkdir(parents=True)
    ubt = root / "Engine" / "Binaries" / "DotNET" / "UnrealBuildTool" / "UnrealBuildTool.exe"
    ubt.parent.mkdir(parents=True)
    ubt.write_bytes(b"fixture")
    return root, ubt


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
    binary = plugin_binary_path(project)
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b"compiled")

    needed, reason = plugin_needs_setup(project, workspace)
    assert needed is False
    assert reason == "ready"


def test_plugin_build_failure_rolls_back_new_plugin_and_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _plugin_workspace(tmp_path)
    project = tmp_path / "Game.uproject"
    original = b'{"FileVersion":3,"Plugins":[]}\r\n'
    project.write_bytes(original)

    monkeypatch.setattr(
        "install_editor_graph_plugin.maybe_build_plugin",
        lambda **_kwargs: {
            "requested": True,
            "skipped": False,
            "ok": False,
            "errorCode": "UBT_FAILED",
        },
    )

    result = install_and_build_plugin(project=project, workspace=workspace, update=True)

    assert result["ok"] is False
    assert result["build"]["errorCode"] == "UBT_FAILED"
    assert result["rollback"]["ok"] is True
    assert result["rollback"]["restoredPlugin"] is True
    assert result["rollback"]["restoredUproject"] is True
    assert project.read_bytes() == original
    assert not (tmp_path / "Plugins").exists()


def test_plugin_build_failure_restores_existing_plugin_tree_and_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _plugin_workspace(tmp_path, "new-plugin")
    project = tmp_path / "Game.uproject"
    original = b'{"FileVersion":3,"Plugins":[{"Name":"LmStudioGraphExporter","Enabled":false}]}\n'
    project.write_bytes(original)
    destination = tmp_path / "Plugins" / PLUGIN_NAME
    destination.mkdir(parents=True)
    (destination / f"{PLUGIN_NAME}.uplugin").write_text(
        json.dumps({"FileVersion": 3, "VersionName": "old-plugin"}),
        encoding="utf-8",
    )
    old_source = destination / "Source"
    old_source.mkdir()
    (old_source / "Old.cpp").write_text("old-plugin", encoding="utf-8")
    old_binary = destination / "Binaries" / "Win64" / f"UnrealEditor-{PLUGIN_NAME}.dll"
    old_binary.parent.mkdir(parents=True)
    old_binary.write_bytes(b"old-binary")

    def fail_build_with_backup(**_kwargs: object) -> dict[str, object]:
        assert not list((tmp_path / "Plugins").glob(f".{PLUGIN_NAME}.backup-*"))
        assert list(tmp_path.glob(f".{PLUGIN_NAME}.backup-*"))
        return {"requested": True, "skipped": False, "ok": False}

    monkeypatch.setattr("install_editor_graph_plugin.maybe_build_plugin", fail_build_with_backup)

    result = install_and_build_plugin(project=project, workspace=workspace, update=True)

    assert result["ok"] is False
    assert result["rollback"]["ok"] is True
    assert project.read_bytes() == original
    assert (old_source / "Old.cpp").read_text(encoding="utf-8") == "old-plugin"
    assert old_binary.read_bytes() == b"old-binary"
    assert not (destination / "Source" / "GraphExporter.cpp").exists()
    assert not list((tmp_path / "Plugins").glob(f".{PLUGIN_NAME}.*"))
    assert not list(tmp_path.glob(f".{PLUGIN_NAME}.*"))


def test_atomic_plugin_copy_keeps_existing_tree_when_staging_copy_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _plugin_workspace(tmp_path, "new-plugin")
    project = tmp_path / "Game.uproject"
    project.write_text('{"FileVersion": 3}', encoding="utf-8")
    destination = tmp_path / "Plugins" / PLUGIN_NAME
    destination.mkdir(parents=True)
    (destination / f"{PLUGIN_NAME}.uplugin").write_text(
        json.dumps({"FileVersion": 3, "VersionName": "old-plugin"}),
        encoding="utf-8",
    )
    (destination / "Old.cpp").write_text("old-plugin", encoding="utf-8")

    original_copytree = __import__("shutil").copytree

    def fail_staging_copy(source: object, target: object, *args: object, **kwargs: object) -> object:
        if Path(target).name.startswith(f".{PLUGIN_NAME}.staging-"):
            raise OSError("injected staging failure")
        return original_copytree(source, target, *args, **kwargs)

    monkeypatch.setattr("install_editor_graph_plugin.shutil.copytree", fail_staging_copy)

    with pytest.raises(OSError, match="injected staging failure"):
        install_plugin(project=project, workspace=workspace, update=True)

    assert (destination / "Old.cpp").read_text(encoding="utf-8") == "old-plugin"
    assert not list((tmp_path / "Plugins").glob(f".{PLUGIN_NAME}.staging-*"))
    assert not list(tmp_path.glob(f".{PLUGIN_NAME}.staging-*"))


def test_direct_install_rolls_back_plugin_when_descriptor_is_invalid(tmp_path: Path) -> None:
    workspace = _plugin_workspace(tmp_path)
    project = tmp_path / "Game.uproject"
    original = b"{ not valid json }\n"
    project.write_bytes(original)

    with pytest.raises(SystemExit):
        install_plugin(project=project, workspace=workspace, update=True)

    assert project.read_bytes() == original
    assert not (tmp_path / "Plugins").exists()


def test_build_failure_does_not_overwrite_concurrent_uproject_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _plugin_workspace(tmp_path)
    project = tmp_path / "Game.uproject"
    project.write_text('{"FileVersion": 3, "Plugins": []}', encoding="utf-8")
    external = b'{"FileVersion":3,"ExternalChange":true}\n'

    def fail_after_external_change(**_kwargs: object) -> dict[str, object]:
        project.write_bytes(external)
        return {"requested": True, "skipped": False, "ok": False, "errorCode": "UBT_FAILED"}

    monkeypatch.setattr("install_editor_graph_plugin.maybe_build_plugin", fail_after_external_change)

    result = install_and_build_plugin(project=project, workspace=workspace, update=True)

    assert result["ok"] is False
    assert result["rollback"]["ok"] is False
    assert result["rollback"]["restoredPlugin"] is True
    assert result["rollback"]["restoredUproject"] is False
    assert project.read_bytes() == external
    assert not (tmp_path / "Plugins").exists()


def test_maybe_build_plugin_returns_structured_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "Game.uproject"
    project.write_text('{"FileVersion": 3}', encoding="utf-8")
    fake_ubt = tmp_path / "UnrealBuildTool.exe"
    fake_ubt.write_bytes(b"fixture")

    monkeypatch.setattr(
        "install_editor_graph_plugin._ubt_invocation",
        lambda *_args, **_kwargs: ([str(fake_ubt)], fake_ubt),
    )

    def timeout(*_args: object, **_kwargs: object) -> object:
        raise subprocess.TimeoutExpired("UnrealBuildTool", 1, output=b"partial output")

    monkeypatch.setattr("install_editor_graph_plugin.subprocess.run", timeout)

    result = maybe_build_plugin(
        project=project,
        workspace=tmp_path,
        install_payload={"copied": True},
        timeout_sec=1,
    )

    assert result["ok"] is False
    assert result["errorCode"] == "BUILD_TIMEOUT"
    assert result["outputTail"] == "partial output"


def test_maybe_build_plugin_returns_structured_os_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "Game.uproject"
    project.write_text('{"FileVersion": 3}', encoding="utf-8")
    fake_ubt = tmp_path / "UnrealBuildTool.exe"
    fake_ubt.write_bytes(b"fixture")
    monkeypatch.setattr(
        "install_editor_graph_plugin._ubt_invocation",
        lambda *_args, **_kwargs: ([str(fake_ubt)], fake_ubt),
    )

    def execution_error(*_args: object, **_kwargs: object) -> object:
        raise OSError("injected exec error")

    monkeypatch.setattr(
        "install_editor_graph_plugin.subprocess.run",
        execution_error,
    )

    result = maybe_build_plugin(
        project=project,
        workspace=tmp_path,
        install_payload={"copied": True},
    )

    assert result["ok"] is False
    assert result["errorCode"] == "UBT_EXEC_FAILED"
    assert result["error"] == "injected exec error"


def test_plugin_build_fails_closed_for_custom_association_and_uses_exact_mapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _plugin_workspace(tmp_path)
    workspace.joinpath("config").mkdir()
    fallback_root, fallback_ubt = _engine_with_ubt(tmp_path / "UE_5.10")
    source_root, source_ubt = _engine_with_ubt(tmp_path / "SourceBuild")
    association = "{SOURCE-BUILD-GUID}"
    project = tmp_path / "SourceGame.uproject"
    project.write_text(
        json.dumps({"FileVersion": 3, "EngineAssociation": association}),
        encoding="utf-8",
    )
    workspace.joinpath("config", "workspace.json").write_text(
        json.dumps({"defaultEngineRoot": str(fallback_root)}),
        encoding="utf-8",
    )
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(tmp_path / "missing-shared.json"))
    monkeypatch.delenv("UNREAL_ENGINE_ROOT", raising=False)

    unresolved = maybe_build_plugin(
        project=project,
        workspace=workspace,
        install_payload={"copied": True},
        dry_run=True,
    )
    assert unresolved["ok"] is False
    assert unresolved["errorCode"] == "ENGINE_ASSOCIATION_UNRESOLVED"
    assert unresolved["checkedUbtPaths"] == []
    assert str(fallback_ubt) not in str(unresolved)

    workspace.joinpath("config", "workspace.json").write_text(
        json.dumps(
            {
                "defaultEngineRoot": str(fallback_root),
                "engineRootsByAssociation": {association: str(source_root)},
            }
        ),
        encoding="utf-8",
    )
    mapped = maybe_build_plugin(
        project=project,
        workspace=workspace,
        install_payload={"copied": True},
        dry_run=True,
    )
    assert mapped["ok"] is True
    assert mapped["ubtPath"] == str(source_ubt.resolve())
    assert mapped["command"][0] == str(source_ubt.resolve())


def test_cli_build_failure_reports_rollback_and_nonzero_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "Game.uproject"
    project.write_text('{"FileVersion": 3}', encoding="utf-8")
    monkeypatch.setattr(
        "install_editor_graph_plugin.install_and_build_plugin",
        lambda **_kwargs: {
            "ok": False,
            "install": {"ok": False, "project": str(project)},
            "build": {"requested": True, "ok": False, "errorCode": "BUILD_TIMEOUT"},
            "rollback": {"attempted": True, "ok": True},
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "install_editor_graph_plugin.py",
            "--project",
            str(project),
            "--workspace",
            str(tmp_path),
            "--build",
        ],
    )

    exit_code = install_editor_graph_plugin_main()

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["rollback"]["ok"] is True
    assert payload["build"]["errorCode"] == "BUILD_TIMEOUT"


def test_successful_plugin_build_commits_staged_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _plugin_workspace(tmp_path, "new-plugin")
    project = tmp_path / "Game.uproject"
    project.write_text('{"FileVersion": 3, "Plugins": []}', encoding="utf-8")

    monkeypatch.setattr(
        "install_editor_graph_plugin.maybe_build_plugin",
        lambda **_kwargs: {"requested": True, "skipped": False, "ok": True},
    )

    result = install_and_build_plugin(project=project, workspace=workspace, update=True)

    destination = tmp_path / "Plugins" / PLUGIN_NAME
    assert result["ok"] is True
    assert result["commit"]["cleanupPending"] is False
    assert (destination / "Source" / "GraphExporter.cpp").read_text(encoding="utf-8") == "new-plugin"
    assert json.loads(project.read_text(encoding="utf-8"))["Plugins"] == [
        {"Name": PLUGIN_NAME, "Enabled": True, "TargetAllowList": ["Editor"]}
    ]
    assert not list((tmp_path / "Plugins").glob(f".{PLUGIN_NAME}.*"))
    assert not list(tmp_path.glob(f".{PLUGIN_NAME}.*"))


def test_ensure_editor_plugin_keeps_compile_failure_as_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "Game.uproject"
    project.write_text('{"FileVersion": 3}', encoding="utf-8")

    monkeypatch.setattr("on_active_project_changed.plugin_needs_setup", lambda *_args, **_kwargs: (True, "plugin_missing"))
    monkeypatch.setattr(
        "on_active_project_changed.install_and_build_plugin",
        lambda **_kwargs: {
            "ok": False,
            "install": {"ok": True, "copied": True, "pluginAlreadyExisted": False},
            "build": {"requested": True, "skipped": False, "ok": False, "errorCode": "UBT_FAILED"},
            "rollback": {"attempted": True, "ok": True},
        },
    )

    result = ensure_editor_plugin(project, tmp_path)

    assert result["ok"] is False
    assert result["rollback"]["ok"] is True
    assert "rolled back" in result["warning"]


def test_active_project_ready_reports_plugin_failure_in_top_level_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "Game.uproject"
    project.write_text('{"FileVersion": 3}', encoding="utf-8")
    monkeypatch.setattr("on_active_project_changed.find_workspace_root", lambda: tmp_path)
    monkeypatch.setattr("on_active_project_changed.load_shared_config", lambda: {"autoSetupOnProjectSwitch": True})
    monkeypatch.setattr("on_active_project_changed.resolve_project", lambda _value: project)
    monkeypatch.setattr("on_active_project_changed.resolve_index_dir", lambda: tmp_path / "data")
    monkeypatch.setattr("on_active_project_changed.plugin_needs_setup", lambda *_args, **_kwargs: (True, "plugin_missing"))
    monkeypatch.setattr("on_active_project_changed.project_index_needs_sync", lambda *_args, **_kwargs: (False, "up_to_date"))
    monkeypatch.setattr("on_active_project_changed.active_project_check_status", lambda *_args, **_kwargs: {"ready": False})
    monkeypatch.setattr(
        "on_active_project_changed.ensure_editor_plugin",
        lambda *_args, **_kwargs: {"ok": False, "warning": "compile failed"},
    )

    result = ensure_active_project_ready(project)

    assert result["ok"] is False
    assert result["plugin"]["warning"] == "compile failed"
    assert result["sync"]["skipped"] is True


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
