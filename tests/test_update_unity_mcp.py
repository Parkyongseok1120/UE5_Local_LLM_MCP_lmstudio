"""The Unity MCP updater checks an existing binding without editing project state."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("update_unity_mcp_test", ROOT / "scripts/update_unity_mcp.py")
updater = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(updater)


@pytest.fixture
def installed(tmp_path):
    source = tmp_path / "New Release"
    server = source / "lmstudio-unity-mcp/src/server.js"
    server.parent.mkdir(parents=True)
    server.write_text("// test server\n", encoding="utf-8")
    checker = source / "scripts/check_unity_install.js"
    checker.parent.mkdir(parents=True)
    checker.write_text("// test checker\n", encoding="utf-8")
    (source / "unity-editor-bridge").mkdir()
    project = tmp_path / "Unity Project"
    for folder in ["Assets", "Packages", "ProjectSettings"]:
        (project / folder).mkdir(parents=True)
    manifest = project / "Packages/manifest.json"
    manifest.write_text(json.dumps({"dependencies": {
        "keep": "1", "com.evidencefirst.unity-bridge": "file:" + (source / "unity-editor-bridge").as_posix(),
    }}), encoding="utf-8")
    (project / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.0f1", encoding="utf-8")
    config = tmp_path / "mcp.json"
    config.write_text(json.dumps({"mcpServers": {
        "keep": {"command": "other", "args": ["other.js"]},
        "unity-tools": {"command": sys.executable, "args": [str(server)], "env": {
            "UNITY_PROJECT_ROOT": str(project), "ALLOW_WRITE": "1", "ALLOW_COMMANDS": "0",
            "UNITY_DOTNET": "dotnet", "UNITY_SYMBOL_WORKER": "worker.dll",
        }},
    }}), encoding="utf-8")
    return source, project, config, manifest


def test_dry_run_inspects_binding_without_running_or_writing(installed, monkeypatch):
    source, _, config, manifest = installed
    before = config.read_bytes(), manifest.read_bytes()
    monkeypatch.setattr(updater, "_run", lambda *a, **k: pytest.fail("dry run must not execute tools"))
    report = updater.update(config, source_root=source, dry_run=True)
    assert report["ok"] and report["verification"] == "not_run"
    assert report["dependencies"] == "would_refresh"
    assert not report["configurationChanged"] and not report["projectChanged"]
    assert (config.read_bytes(), manifest.read_bytes()) == before


def test_skip_deps_validates_stdio_without_rewriting_permissions_or_other_servers(installed, monkeypatch):
    source, _, config, manifest = installed
    before = config.read_bytes(), manifest.read_bytes()
    commands = []

    def fake_run(command, **_):
        commands.append(command)
        output = "v20.20.2" if command[1] == "--version" else json.dumps({"initialized": True, "connection": "disconnected"})
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    monkeypatch.setattr(updater, "_run", fake_run)
    report = updater.update(config, source_root=source, skip_deps=True)
    assert report["ok"] and report["restartRequired"]
    assert report["verification"]["initialized"]
    assert len(commands) == 2
    assert commands[1][1] == str(source / "scripts/check_unity_install.js")
    assert (config.read_bytes(), manifest.read_bytes()) == before


def test_process_output_is_decoded_as_utf8_without_windows_codepage_failures(tmp_path, monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="done ✓", stderr="")

    monkeypatch.setattr(updater.subprocess, "run", fake_run)
    result = updater._run([sys.executable, "--version"], cwd=tmp_path, timeout=15)
    assert result.stdout == "done ✓"
    assert captured["text"] is True
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"


def test_update_rejects_different_source_or_bridge_before_external_action(installed, monkeypatch):
    source, _, config, manifest = installed
    monkeypatch.setattr(updater, "_run", lambda *a, **k: pytest.fail("invalid binding must fail in preflight"))
    other = source.parent / "Other Release"
    (other / "lmstudio-unity-mcp/src").mkdir(parents=True)
    (other / "lmstudio-unity-mcp/src/server.js").write_text("// other\n")
    (other / "scripts").mkdir()
    (other / "scripts/check_unity_install.js").write_text("// other\n")
    with pytest.raises(ValueError, match="different source tree"):
        updater.update(config, source_root=other)
    changed = json.loads(manifest.read_text(encoding="utf-8"))
    changed["dependencies"]["com.evidencefirst.unity-bridge"] = "file:/unrelated"
    manifest.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="Bridge binding differs"):
        updater.update(config, source_root=source)


def test_real_cli_checks_current_mcp_without_changing_config(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable")
    project = tmp_path / "Unity Project"
    for folder in ["Assets", "Packages", "ProjectSettings"]:
        (project / folder).mkdir(parents=True)
    manifest = project / "Packages/manifest.json"
    manifest.write_text(json.dumps({"dependencies": {
        "com.evidencefirst.unity-bridge": "file:" + (ROOT / "unity-editor-bridge").resolve().as_posix(),
    }}), encoding="utf-8")
    (project / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.0f1", encoding="utf-8")
    config = tmp_path / "mcp.json"
    config.write_text(json.dumps({"mcpServers": {"unity-tools": {
        "command": node,
        "args": [str(ROOT / "lmstudio-unity-mcp/src/server.js")],
        "env": {"UNITY_PROJECT_ROOT": str(project), "UNITY_DOTNET": "configured",
                "UNITY_SYMBOL_WORKER": "configured", "ALLOW_WRITE": "0", "ALLOW_COMMANDS": "0"},
    }}}), encoding="utf-8")
    before = config.read_bytes(), manifest.read_bytes()
    result = subprocess.run([sys.executable, str(ROOT / "scripts/update_unity_mcp.py"),
                             "--mcp-config", str(config), "--skip-deps"],
                            cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] and report["verification"]["initialized"]
    assert report["verification"]["connection"] == "disconnected"
    assert (config.read_bytes(), manifest.read_bytes()) == before
