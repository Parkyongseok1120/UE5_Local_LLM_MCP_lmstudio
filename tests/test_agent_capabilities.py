from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from agent_capabilities import resolve_agent_write_enabled  # noqa: E402


def test_resolve_agent_write_enabled_from_mcp_json(tmp_path: Path, monkeypatch) -> None:
    lm_home = tmp_path / "lmstudio"
    lm_home.mkdir()
    mcp_path = lm_home / "mcp.json"
    mcp_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "unreal-agent": {
                        "env": {"ALLOW_WRITE": "1"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LMSTUDIO_HOME", str(lm_home))
    monkeypatch.delenv("SHARED_UNREAL_CONFIG", raising=False)
    assert resolve_agent_write_enabled() is True


def test_resolve_agent_write_disabled_from_mcp_json(tmp_path: Path, monkeypatch) -> None:
    lm_home = tmp_path / "lmstudio"
    lm_home.mkdir()
    mcp_path = lm_home / "mcp.json"
    mcp_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "unreal-agent": {
                        "env": {"ALLOW_WRITE": "0"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LMSTUDIO_HOME", str(lm_home))
    monkeypatch.delenv("SHARED_UNREAL_CONFIG", raising=False)
    assert resolve_agent_write_enabled() is False


def test_resolve_agent_write_falls_back_to_shared_config(tmp_path: Path, monkeypatch) -> None:
    lm_home = tmp_path / "lmstudio"
    lm_home.mkdir()
    shared = tmp_path / "unreal-workspace.json"
    shared.write_text(
        json.dumps({"agentCapabilities": {"allowWrite": True}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("LMSTUDIO_HOME", str(lm_home))
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(shared))
    assert resolve_agent_write_enabled() is True


def test_resolve_agent_write_missing_config(tmp_path: Path, monkeypatch) -> None:
    lm_home = tmp_path / "lmstudio"
    lm_home.mkdir()
    monkeypatch.setenv("LMSTUDIO_HOME", str(lm_home))
    monkeypatch.delenv("SHARED_UNREAL_CONFIG", raising=False)
    assert resolve_agent_write_enabled() is False
