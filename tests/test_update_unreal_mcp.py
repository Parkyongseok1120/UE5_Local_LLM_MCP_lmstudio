"""The Unreal updater refreshes both runtimes without rewriting authority or project binding."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("update_unreal_mcp_test", ROOT / "scripts/update_unreal_mcp.py")
updater = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(updater)


@pytest.fixture
def installed(tmp_path: Path):
    source = tmp_path / "New Release"
    agent = source / "lmstudio-unreal-agent-mcp/src/direct-server.js"
    agent.parent.mkdir(parents=True)
    agent.write_text("// agent\n", encoding="utf-8")
    (source / "lmstudio-unreal-agent-mcp/package-lock.json").write_text("{}\n", encoding="utf-8")
    rag = source / "scripts/unreal_rag_direct.py"
    rag.parent.mkdir(parents=True)
    rag.write_text("# rag\n", encoding="utf-8")
    manifest = source / "config/stable_tool_manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "agentEssential": ["read_file", "workspace_status"],
        "ragEssential": ["unreal_rag_health"],
    }), encoding="utf-8")
    config = tmp_path / "mcp.json"
    config.write_text(json.dumps({"mcpServers": {
        "keep": {"command": "other", "args": ["other.js"]},
        "unreal-agent": {"command": sys.executable, "args": [str(agent)], "env": {
            "WORKSPACE_ROOT": str(tmp_path / "Games"), "ALLOW_WRITE": "1",
            "ALLOW_COMMANDS": "1", "ALLOW_UNREAL_BUILD": "1", "CUSTOM_SETTING": "keep",
        }},
        "unreal-rag": {"command": sys.executable, "args": [str(rag), "--index", str(tmp_path / "rag.sqlite")],
                       "env": {"SHARED_UNREAL_CONFIG": str(tmp_path / "shared.json")}},
    }}), encoding="utf-8")
    return source, config


def test_dry_run_preserves_complete_config_and_permissions(installed, monkeypatch):
    source, config = installed
    before = config.read_bytes()
    monkeypatch.setattr(updater, "_run", lambda *a, **k: pytest.fail("dry run must not execute"))
    monkeypatch.setattr(updater, "_mcp_tools", lambda *a, **k: pytest.fail("dry run must not start MCP"))
    report = updater.update(config, source_root=source, dry_run=True)
    assert report["ok"] and report["dependencies"] == "would_refresh_locked_node_dependencies"
    assert report["permissions"] == {
        "ALLOW_WRITE": "1", "ALLOW_COMMANDS": "1", "ALLOW_UNREAL_BUILD": "1",
    }
    assert report["configurationChanged"] is False and report["permissionsChanged"] is False
    assert config.read_bytes() == before


def test_skip_dependencies_smokes_agent_and_rag_without_rewriting_config(installed, monkeypatch):
    source, config = installed
    before = config.read_bytes()
    commands = []
    catalogs = iter([{"read_file", "workspace_status"}, {"unreal_rag_health"}])

    def fake_run(command, **_):
        commands.append(command)
        output = "v20.20.2\n" if command[1] == "--version" else "Python 3.12\n"
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    monkeypatch.setattr(updater, "_run", fake_run)
    monkeypatch.setattr(updater, "_mcp_tools", lambda *a, **k: next(catalogs))
    report = updater.update(config, source_root=source, skip_deps=True)
    assert report["ok"] and report["restartRequired"]
    assert report["verification"] == {
        "unrealAgentInitialized": True, "unrealAgentTools": 2,
        "unrealRagInitialized": True, "unrealRagTools": 1,
        "modelConnection": "not_verified", "engineExecution": "not_requested",
    }
    assert len(commands) == 2
    assert config.read_bytes() == before


def test_if_present_skips_absent_install_and_rejects_partial_or_foreign_binding(installed):
    source, config = installed
    original = config.read_text(encoding="utf-8")
    empty = config.parent / "empty.json"
    empty.write_text(json.dumps({"mcpServers": {"keep": {"command": "x"}}}), encoding="utf-8")
    report = updater.update(empty, source_root=source, if_present=True)
    assert report["installed"] is False and report["restartRequired"] is False
    no_servers = config.parent / "no-servers.json"
    no_servers.write_text("{}", encoding="utf-8")
    report = updater.update(no_servers, source_root=source, if_present=True)
    assert report["installed"] is False and report["restartRequired"] is False

    partial = json.loads(config.read_text(encoding="utf-8"))
    del partial["mcpServers"]["unreal-rag"]
    config.write_text(json.dumps(partial), encoding="utf-8")
    with pytest.raises(ValueError, match="incomplete"):
        updater.update(config, source_root=source, if_present=True)

    config.write_text(original, encoding="utf-8")
    other = source.parent / "Other"
    (other / "lmstudio-unreal-agent-mcp/src").mkdir(parents=True)
    (other / "lmstudio-unreal-agent-mcp/src/direct-server.js").write_text("// other\n", encoding="utf-8")
    (other / "lmstudio-unreal-agent-mcp/package-lock.json").write_text("{}\n", encoding="utf-8")
    (other / "scripts").mkdir()
    (other / "scripts/unreal_rag_direct.py").write_text("# other\n", encoding="utf-8")
    (other / "config").mkdir()
    (other / "config/stable_tool_manifest.json").write_text(json.dumps({"agentEssential": [], "ragEssential": []}))
    with pytest.raises(ValueError, match="different source tree"):
        updater.update(config, source_root=other, dry_run=True)


def test_unsafe_process_environment_is_not_forwarded(monkeypatch):
    monkeypatch.setenv("NODE_OPTIONS", "--require hostile.js")
    monkeypatch.setenv("PYTHONPATH", "hostile")
    env = updater._process_env({"NODE_OPTIONS": "--require worse.js", "PYTHONPATH": "worse", "ALLOW_WRITE": "1"})
    assert "NODE_OPTIONS" not in env and "PYTHONPATH" not in env
    assert env["ALLOW_WRITE"] == "1"
