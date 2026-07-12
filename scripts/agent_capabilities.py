#!/usr/bin/env python
"""Resolve unreal-agent MCP capabilities from install config (not RAG process env)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from workspace_paths import DEFAULT_LMSTUDIO_ROOT, load_shared_config


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _lmstudio_home() -> Path:
    override = os.environ.get("LMSTUDIO_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return DEFAULT_LMSTUDIO_ROOT


def _mcp_config_path() -> Path:
    override = os.environ.get("LMSTUDIO_MCP_CONFIG", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (_lmstudio_home() / "mcp.json").resolve()


def _read_mcp_config() -> dict[str, Any]:
    path = _mcp_config_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def resolve_agent_write_enabled() -> bool:
    """True when unreal-agent MCP server has write mode enabled in mcp.json."""
    config = _read_mcp_config()
    servers = config.get("mcpServers")
    if isinstance(servers, dict):
        agent = servers.get("unreal-agent")
        if isinstance(agent, dict):
            env = agent.get("env")
            if isinstance(env, dict) and "ALLOW_WRITE" in env:
                return _truthy(env.get("ALLOW_WRITE"))

    shared = load_shared_config()
    caps = shared.get("agentCapabilities")
    if isinstance(caps, dict) and "allowWrite" in caps:
        return _truthy(caps.get("allowWrite"))

    return False
