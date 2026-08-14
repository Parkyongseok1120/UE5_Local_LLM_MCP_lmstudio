#!/usr/bin/env python
"""Tests for automated Editor metadata export orchestration."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from editor_export_runner import (  # noqa: E402
    build_export_job,
    project_editor_running,
    submit_export_request,
    wait_for_export_markers,
    resolve_editor_executable,
    REQUEST_NAME,
    DONE_NAME,
)
from workspace_paths import default_editor_export_dir, normalize_editor_export_dir  # noqa: E402


def test_build_export_job_writes_job_file(tmp_path):
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    tools_dir = ROOT / "tools" / "ue_export"
    job = build_export_job(
        export_dir=export_dir,
        tools_dir=tools_dir,
        content_path="/Game/Env",
        maps_path="/Game/Maps",
        scope="materials",
        workspace=ROOT,
    )
    job_path = Path(job["jobPath"])
    assert job_path.is_file()
    payload = json.loads(job_path.read_text(encoding="utf-8"))
    assert payload["contentPath"] == "/Game/Env"
    assert payload["scope"] == "materials"


def test_request_watcher_flow_via_markers(tmp_path, monkeypatch):
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    tools_dir = ROOT / "tools" / "ue_export"
    job = build_export_job(
        export_dir=export_dir,
        tools_dir=tools_dir,
        content_path="/Game",
        maps_path="/Game",
        scope="all",
        workspace=ROOT,
    )
    submit_export_request(job)
    assert (export_dir / REQUEST_NAME).is_file()

    (export_dir / DONE_NAME).write_text(
        json.dumps({"ok": True, "mode": "request_watcher", "exportDir": str(export_dir)}, ensure_ascii=False),
        encoding="utf-8",
    )

    payload = wait_for_export_markers(export_dir, timeout_sec=1, poll_sec=0.1)
    assert payload["ok"] is True


def test_default_editor_export_dir_uses_project_saved_folder(monkeypatch, tmp_path):
    project_root = tmp_path / "DemoGame"
    project_root.mkdir()
    uproject = project_root / "DemoGame.uproject"
    uproject.write_text("{}", encoding="utf-8")
    cfg_path = tmp_path / "unreal-workspace.json"
    cfg_path.write_text(json.dumps({"activeProject": str(uproject)}), encoding="utf-8")
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(cfg_path))

    export_dir = default_editor_export_dir()
    assert export_dir == project_root / "Saved" / "LmStudioMetadataExports"


def test_normalize_editor_export_dir_rejects_project_root(monkeypatch, tmp_path):
    project_root = tmp_path / "DemoGame"
    project_root.mkdir()
    uproject = project_root / "DemoGame.uproject"
    uproject.write_text("{}", encoding="utf-8")
    cfg_path = tmp_path / "unreal-workspace.json"
    cfg_path.write_text(json.dumps({"activeProject": str(uproject)}), encoding="utf-8")
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(cfg_path))

    normalized = normalize_editor_export_dir(str(project_root))
    assert normalized == project_root / "Saved" / "LmStudioMetadataExports"


def test_normalize_editor_export_dir_replaces_stale_other_project_default(monkeypatch, tmp_path):
    project_root = tmp_path / "NewGame"
    project_root.mkdir()
    uproject = project_root / "NewGame.uproject"
    uproject.write_text("{}", encoding="utf-8")
    cfg_path = tmp_path / "unreal-workspace.json"
    cfg_path.write_text(json.dumps({"activeProject": str(uproject)}), encoding="utf-8")
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(cfg_path))

    stale = tmp_path / "OldGame" / "Saved" / "LmStudioMetadataExports"
    assert normalize_editor_export_dir(stale) == project_root / "Saved" / "LmStudioMetadataExports"


@pytest.mark.skipif(sys.platform == "win32", reason="requires two POSIX-distinct Unicode directories")
def test_normalize_editor_export_dir_keeps_i_dot_sibling_project(monkeypatch, tmp_path):
    project_root = tmp_path / "\u0130Project"
    sibling = tmp_path / "I\u0307Project"
    project_root.mkdir()
    sibling.mkdir(exist_ok=True)
    if project_root.samefile(sibling):
        pytest.skip("host filesystem aliases the two Unicode spellings")
    uproject = project_root / "Demo.uproject"
    uproject.write_text("{}", encoding="utf-8")
    cfg_path = tmp_path / "unreal-workspace.json"
    cfg_path.write_text(json.dumps({"activeProject": str(uproject)}), encoding="utf-8")
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(cfg_path))

    for host_platform in ("linux", "darwin", "win32"):
        assert normalize_editor_export_dir(
            sibling,
            host_platform=host_platform,
        ) == sibling


def test_project_editor_running_uses_host_aware_ascii_path_identity(monkeypatch, tmp_path):
    project = tmp_path / "Source" / "\u0130" / "Demo.uproject"
    project.parent.mkdir(parents=True)
    project.write_text("{}", encoding="utf-8")

    def process_output(command_line: str) -> None:
        monkeypatch.setattr(
            "editor_export_runner.subprocess.run",
            lambda *_args, **_kwargs: SimpleNamespace(stdout=command_line),
        )

    ascii_alias = str(project).replace("Source", "SOURCE")
    process_output(f'UnrealEditor-Cmd.exe "{ascii_alias}"')
    assert project_editor_running(project, "win32") is True
    assert project_editor_running(project, "linux") is False

    unicode_twin = str(project).replace("\u0130", "I\u0307")
    process_output(f'UnrealEditor-Cmd.exe "{unicode_twin}"')
    for host_platform in ("linux", "darwin", "win32"):
        assert project_editor_running(project, host_platform) is False


def test_resolve_editor_executable_supports_mac_and_linux_layouts(tmp_path):
    for host, folder in (("darwin", "Mac"), ("linux", "Linux")):
        engine = tmp_path / host
        executable = engine / "Engine" / "Binaries" / folder / "UnrealEditor-Cmd"
        executable.parent.mkdir(parents=True)
        executable.write_text("", encoding="utf-8")
        assert resolve_editor_executable(engine, host) == executable

    mac_bundle = tmp_path / "mac-bundle"
    bundled_executable = (
        mac_bundle
        / "Engine"
        / "Binaries"
        / "Mac"
        / "UnrealEditor.app"
        / "Contents"
        / "MacOS"
        / "UnrealEditor"
    )
    bundled_executable.parent.mkdir(parents=True)
    bundled_executable.write_text("", encoding="utf-8")
    assert resolve_editor_executable(mac_bundle, "darwin") == bundled_executable
