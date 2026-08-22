#!/usr/bin/env python
"""Tests for automated Editor metadata export orchestration."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import editor_export_runner as runner
from editor_export_runner import (
    DONE_NAME,
    REQUEST_NAME,
    build_export_job,
    project_editor_running,
    project_engine_association,
    resolve_editor_executable,
    run_editor_export,
    submit_export_request,
    wait_for_export_markers,
)
from workspace_paths import default_editor_export_dir, normalize_editor_export_dir
from workspace_paths import resolve_active_project_path


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


def test_explicit_project_uses_its_default_export_dir_without_mutating_active_config(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    active_project = tmp_path / "ActiveA" / "ActiveA.uproject"
    explicit_project = tmp_path / "ExplicitB" / "ExplicitB.uproject"
    active_project.parent.mkdir()
    explicit_project.parent.mkdir()
    active_project.write_text("{}", encoding="utf-8")
    explicit_project.write_text(
        json.dumps({"FileVersion": 3, "EngineAssociation": "5.0"}),
        encoding="utf-8",
    )

    active_export = active_project.parent / "Saved" / "LmStudioMetadataExports"
    config_path = tmp_path / "unreal-workspace.json"
    config_text = json.dumps(
        {
            "activeProject": str(active_project),
            "editorExportDir": str(active_export),
        },
        ensure_ascii=False,
        indent=2,
    )
    config_path.write_text(config_text, encoding="utf-8")
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(config_path))

    engine_root = tmp_path / "UE_5.0"
    captured = {}
    monkeypatch.setattr(runner, "find_workspace_root", lambda: workspace)
    monkeypatch.setattr(runner, "project_editor_running", lambda _project: False)
    monkeypatch.setattr(
        runner,
        "resolve_project_engine_root",
        lambda _project, _workspace: {
            "ok": True,
            "engineRoot": str(engine_root),
            "source": "test",
            "requestedEngineAssociation": "5.0",
            "errorCode": "",
            "error": "",
        },
    )

    def headless(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "mode": "headless"}

    monkeypatch.setattr(runner, "run_headless_export", headless)

    result = run_editor_export(
        mode="headless",
        uproject=explicit_project,
        content_path="/Game",
        maps_path="/Game/Maps",
        scope="all",
        timeout_sec=120,
    )

    expected_export = explicit_project.parent / "Saved" / "LmStudioMetadataExports"
    assert result["ok"] is True
    assert Path(result["exportDir"]) == expected_export
    assert Path(captured["job"]["exportDir"]) == expected_export
    assert expected_export.is_dir()
    assert not active_export.exists()
    assert config_path.read_text(encoding="utf-8") == config_text


def test_relative_project_paths_are_workspace_relative_not_process_cwd(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace with spaces"
    project = workspace / "Projects" / "다른 프로젝트" / "Portable.uproject"
    unrelated_cwd = tmp_path / "unrelated cwd"
    project.parent.mkdir(parents=True)
    unrelated_cwd.mkdir()
    project.write_text("{}", encoding="utf-8")
    config_path = tmp_path / "unreal-workspace.json"
    config_path.write_text(
        json.dumps({"activeProject": str(project.relative_to(workspace))}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(config_path))
    monkeypatch.chdir(unrelated_cwd)

    assert resolve_active_project_path(workspace) == project.resolve()

    captured = {}
    monkeypatch.setattr(runner, "find_workspace_root", lambda: workspace)
    monkeypatch.setattr(runner, "project_editor_running", lambda _project: False)
    monkeypatch.setattr(
        runner,
        "resolve_project_engine_root",
        lambda _project, _workspace: {
            "ok": True,
            "engineRoot": str(tmp_path / "Source Engine"),
            "source": "test",
            "requestedEngineAssociation": "",
            "errorCode": "",
            "error": "",
        },
    )

    def headless(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "mode": "headless"}

    monkeypatch.setattr(runner, "run_headless_export", headless)
    result = run_editor_export(
        mode="headless",
        uproject=project.relative_to(workspace),
        content_path="/Game",
        maps_path="/Game",
        scope="all",
        timeout_sec=120,
    )

    assert result["ok"] is True
    assert Path(result["project"]) == project.resolve()
    assert captured["uproject"] == project.resolve()
    assert Path(result["exportDir"]) == (
        project.parent / "Saved" / "LmStudioMetadataExports"
    )


def test_headless_export_binds_to_explicit_project_engine_association(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = tmp_path / "SourceBuildProject" / "SourceBuildProject.uproject"
    project.parent.mkdir()
    project.write_text(
        json.dumps({"FileVersion": 3, "EngineAssociation": "{source-build-guid}"}),
        encoding="utf-8",
    )
    engine = tmp_path / "SourceBuild"
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    calls = {}

    def resolve_association(association, start):
        calls["association"] = association
        calls["workspace"] = start
        return {
            "ok": True,
            "engineRoot": str(engine),
            "source": "config.engineRootsByAssociation",
            "requestedEngineAssociation": association,
            "errorCode": "",
            "error": "",
        }

    def headless(**kwargs):
        calls["engineRoot"] = kwargs["engine_root"]
        return {"ok": True, "mode": "headless"}

    monkeypatch.setattr(runner, "find_workspace_root", lambda: workspace)
    monkeypatch.setattr(runner, "resolve_engine_root_for_association", resolve_association)
    monkeypatch.setattr(runner, "resolve_export_dir", lambda _value, **_kwargs: export_dir)
    monkeypatch.setattr(runner, "editor_export_content_path", lambda: "/Game")
    monkeypatch.setattr(runner, "editor_export_maps_path", lambda: "/Game")
    monkeypatch.setattr(runner, "editor_export_scope", lambda: "all")
    monkeypatch.setattr(runner, "editor_export_timeout_sec", lambda: 120)
    monkeypatch.setattr(runner, "build_export_job", lambda **_kwargs: {"jobId": "job"})
    monkeypatch.setattr(runner, "project_editor_running", lambda _project: False)
    monkeypatch.setattr(runner, "run_headless_export", headless)

    result = run_editor_export(mode="headless", uproject=project)

    assert result["ok"] is True
    assert calls["association"] == "{source-build-guid}"
    assert calls["workspace"] == workspace
    assert calls["engineRoot"] == engine
    assert result["engineRoot"] == str(engine)
    assert result["engineResolutionSource"] == "config.engineRootsByAssociation"


def test_headless_export_fails_closed_for_unresolved_custom_association(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = tmp_path / "Custom" / "Custom.uproject"
    project.parent.mkdir()
    project.write_text(
        json.dumps({"FileVersion": 3, "EngineAssociation": "custom-source-build"}),
        encoding="utf-8",
    )
    export_dir = tmp_path / "exports"
    export_dir.mkdir()

    monkeypatch.setattr(runner, "find_workspace_root", lambda: workspace)
    monkeypatch.setattr(
        runner,
        "resolve_engine_root_for_association",
        lambda association, start: {
            "ok": False,
            "engineRoot": "",
            "source": "",
            "requestedEngineAssociation": association,
            "errorCode": "ENGINE_ASSOCIATION_UNRESOLVED",
            "error": "custom source build is not mapped",
        },
    )
    monkeypatch.setattr(runner, "resolve_export_dir", lambda _value, **_kwargs: export_dir)
    monkeypatch.setattr(runner, "editor_export_content_path", lambda: "/Game")
    monkeypatch.setattr(runner, "editor_export_maps_path", lambda: "/Game")
    monkeypatch.setattr(runner, "editor_export_scope", lambda: "all")
    monkeypatch.setattr(runner, "editor_export_timeout_sec", lambda: 120)
    monkeypatch.setattr(runner, "build_export_job", lambda **_kwargs: {"jobId": "job"})
    monkeypatch.setattr(runner, "project_editor_running", lambda _project: False)
    monkeypatch.setattr(
        runner,
        "run_headless_export",
        lambda **_kwargs: pytest.fail("headless export must not use a default engine"),
    )

    result = run_editor_export(mode="headless", uproject=project)

    assert result["ok"] is False
    assert result["errorCode"] == "ENGINE_ASSOCIATION_UNRESOLVED"
    assert result["engineRoot"] == ""
    assert result["engineAssociation"] == "custom-source-build"


def test_project_engine_association_rejects_invalid_descriptor(tmp_path):
    project = tmp_path / "Broken.uproject"
    project.write_text("not json", encoding="utf-8")

    association, error = project_engine_association(project)

    assert association == ""
    assert "Could not read project descriptor" in error


def test_wait_for_markers_rejects_stale_done_marker_without_matching_run_id(
    tmp_path,
):
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    marker = export_dir / DONE_NAME
    marker.write_text(
        json.dumps({"ok": True, "runId": "old-run"}),
        encoding="utf-8",
    )

    payload = wait_for_export_markers(
        export_dir,
        timeout_sec=0.02,
        poll_sec=0.001,
        expected_run_id="new-run",
    )

    assert payload["ok"] is False
    assert payload["errorCode"] == "EDITOR_EXPORT_MARKER_TIMEOUT"
    assert not marker.exists()


def test_wait_for_markers_rejects_error_marker_without_run_id(tmp_path):
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    marker = export_dir / runner.ERROR_NAME
    marker.write_text(json.dumps({"ok": False, "error": "stale"}), encoding="utf-8")

    payload = wait_for_export_markers(
        export_dir,
        timeout_sec=0.02,
        poll_sec=0.001,
        expected_run_id="exact-run",
    )

    assert payload["ok"] is False
    assert payload["errorCode"] == "EDITOR_EXPORT_MARKER_TIMEOUT"
    assert not marker.exists()


def test_run_editor_export_rejects_non_uproject_file_before_job_creation(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    wrong_file = workspace / "project.json"
    wrong_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(runner, "find_workspace_root", lambda: workspace)
    monkeypatch.setattr(
        runner,
        "build_export_job",
        lambda **_kwargs: pytest.fail("invalid project must not create an export job"),
    )

    result = run_editor_export(mode="headless", uproject=wrong_file)

    assert result["ok"] is False
    assert "exact .uproject" in result["error"]


@pytest.mark.parametrize(
    ("failure", "error_code"),
    [
        (subprocess.TimeoutExpired("UnrealEditor", 3), "EDITOR_EXPORT_PROCESS_TIMEOUT"),
        (OSError("spawn denied"), "EDITOR_EXPORT_PROCESS_FAILED"),
    ],
)
def test_headless_process_failures_return_fail_closed_result(
    tmp_path,
    monkeypatch,
    failure,
    error_code,
):
    import editor_export_process

    workspace = tmp_path / "workspace"
    script = workspace / "tools" / "ue_export" / "headless_export_job.py"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    project = tmp_path / "Game" / "Game.uproject"
    project.parent.mkdir()
    project.write_text("{}", encoding="utf-8")
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    executable = tmp_path / "UnrealEditor-Cmd"
    executable.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        editor_export_process,
        "resolve_editor_executable",
        lambda _root: executable,
    )

    def fail_process(*_args, **_kwargs):
        raise failure

    result = editor_export_process.run_headless_export(
        uproject=project,
        engine_root=tmp_path / "Engine",
        job={
            "jobId": "exact-run",
            "jobPath": str(tmp_path / "job.json"),
            "exportDir": str(export_dir),
        },
        timeout_sec=3,
        workspace=workspace,
        wait_for_markers=lambda *_args, **_kwargs: pytest.fail(
            "a failed process must not accept completion markers"
        ),
        run_process=fail_process,
    )

    assert result["ok"] is False
    assert result["errorCode"] == error_code
