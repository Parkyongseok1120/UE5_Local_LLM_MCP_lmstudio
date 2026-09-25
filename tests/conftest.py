#!/usr/bin/env python
"""Shared pytest fixtures for multi-project tests (no local UE project disk dependency)."""

from __future__ import annotations

import json
import platform
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

def plant_compactor_sdk_fixture(plugin_dir: Path) -> None:
    """Fake LMS dependency tree with the real, version-checked patch command.

    The actual upstream act loop is exercised separately by the Node suite.
    Installer fixtures must still execute and validate their post-install patch.
    """
    scripts = plugin_dir / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "lmstudio-context-compactor-plugin/scripts/patch-lmstudio-sdk.cjs", scripts)
    (plugin_dir / "package.json").write_text(json.dumps({"scripts": {
        "patch:sdk": "node scripts/patch-lmstudio-sdk.cjs"}}), encoding="utf-8")
    sdk = plugin_dir / "node_modules/@lmstudio/sdk"
    (sdk / "dist").mkdir(parents=True, exist_ok=True)
    (sdk / "package.json").write_text('{"version":"1.5.0"}', encoding="utf-8")
    source = "handlePredictionEnd: endPacket => {\n                const predictionResult = makePredictionResult({"
    for name in ["index.cjs", "index.mjs"]:
        (sdk / "dist" / name).write_bytes(source.encode("utf-8"))
sys.path.insert(0, str(ROOT / "scripts"))

AGENT_MCP_ROOT = ROOT / "lmstudio-unreal-agent-mcp"
AGENT_MCP_SDK = AGENT_MCP_ROOT / "node_modules" / "@modelcontextprotocol" / "sdk"


def gui_installer_command(installer: Path) -> list[str]:
    """GUI-profile fixture tests simulate a supported Mac, not Intel GUI support.

    Native Intel refusal/headless behavior is covered separately, without this helper.
    No production installer switch or environment bypass is introduced.
    """
    if sys.platform == "darwin":
        return [sys.executable, "-c", "import platform,runpy,sys; platform.machine=lambda:'arm64'; p=sys.argv.pop(1); sys.argv[0]=p; runpy.run_path(p,run_name='__main__')", str(installer)]
    return [sys.executable, str(installer)]


def powershell_prefix() -> list[str]:
    """Return the host-native PowerShell 7 command prefix for fixture tests."""
    candidates = ("powershell", "pwsh") if sys.platform == "win32" else ("pwsh", "powershell")
    executable = next((shutil.which(name) for name in candidates if shutil.which(name)), None)
    if not executable:
        pytest.skip("PowerShell 7/pwsh is unavailable")
    command = [executable, "-NoProfile"]
    if sys.platform == "win32":
        command.extend(["-ExecutionPolicy", "Bypass"])
    return command


@pytest.fixture(autouse=True)
def isolate_lmstudio_runtime_state(tmp_path, monkeypatch):
    """Prevent tests from reading or mutating the user's live LM Studio state."""

    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "lmstudio-state"))
    monkeypatch.setenv(
        "SHARED_UNREAL_CONFIG",
        str(tmp_path / "lmstudio-config" / "unreal-workspace.json"),
    )


def require_agent_mcp_deps() -> None:
    if not AGENT_MCP_SDK.is_dir():
        pytest.skip("agent MCP npm deps missing; run: cd lmstudio-unreal-agent-mcp && npm ci")


def _write_uproject(path: Path, modules: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "FileVersion": 3,
        "EngineAssociation": "5.8",
        "Category": "",
        "Description": "",
        "Modules": [{"Name": name, "Type": "Runtime", "LoadingPhase": "Default"} for name in modules],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _write_source_tree(project_dir: Path, module: str, domains: list[str]) -> None:
    for domain in domains:
        target = project_dir / "Source" / module / domain
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{domain}Subsystem.h").write_text(
            f"#pragma once\nclass U{domain}Subsystem {{}};\n",
            encoding="utf-8",
        )


@pytest.fixture
def shared_config_path(tmp_path, monkeypatch):
    cfg = tmp_path / "unreal-workspace.json"
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(cfg))
    return cfg


@pytest.fixture
def demo_game_project(tmp_path, shared_config_path):
    project_dir = tmp_path / "DemoGame"
    uproject = _write_uproject(project_dir / "DemoGame.uproject", ["DemoGame"])
    _write_source_tree(project_dir, "DemoGame", ["Combat"])
    content = project_dir / "Content" / "Shaders" / "MF_Test"
    content.mkdir(parents=True, exist_ok=True)
    shared_config_path.write_text(
        json.dumps({"activeProject": str(uproject)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "projectDir": project_dir,
        "uproject": uproject,
        "module": "DemoGame",
        "domain": "Combat",
        "folder": "MF_Test",
    }


@pytest.fixture
def lyra_style_project(tmp_path, shared_config_path):
    project_dir = tmp_path / "LyraStyleGame"
    uproject = _write_uproject(project_dir / "LyraStyleGame.uproject", ["LyraGame"])
    _write_source_tree(project_dir, "LyraGame", ["Cinematic"])
    shared_config_path.write_text(
        json.dumps({"activeProject": str(uproject)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "projectDir": project_dir,
        "uproject": uproject,
        "module": "LyraGame",
        "projectName": "LyraStyleGame",
        "domain": "Cinematic",
    }


def pytest_configure(config):
    config.addinivalue_line("markers", "smoke: optional local-disk regression tests that require a real UE project on disk")
