#!/usr/bin/env python
"""Fail-closed bridge to the Node project-discovery SSOT."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any


_CACHE_TTL_SECONDS = 60.0
_CACHE_LIMIT = 64
_CACHE_LOCK = threading.Lock()
_CACHE: dict[
    tuple[str, str, str, str, int, int, str, str, str, str],
    tuple[float, dict[str, Any]],
] = {}


def _mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _agent_config_path(workspace: Path) -> Path:
    configured = str(os.environ.get("AGENT_MCP_CONFIG") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (
        workspace
        / "lmstudio-unreal-agent-mcp"
        / "config"
        / "agent-mcp.json"
    ).resolve()


def _shared_config_path() -> Path | None:
    configured = str(os.environ.get("SHARED_UNREAL_CONFIG") or "").strip()
    return Path(configured).expanduser().resolve() if configured else None


def clear_project_name_resolution_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def resolve_project_name(
    workspace: Path,
    target: str,
    *,
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    """Resolve one exact project name within configured, bounded search roots.

    Project discovery and matching remain owned by ``unreal-detect.js``.  This
    bridge only transports JSON and caches recent immutable results so repeated
    control turns do not rescan the configured roots.
    """

    root = workspace.expanduser().resolve()
    config_path = _agent_config_path(root)
    shared_path = _shared_config_path()
    normalized_target = str(target or "").strip()
    node = str(os.environ.get("NODE_BINARY") or "").strip() or shutil.which("node")
    try:
        process_cwd = os.getcwd()
    except OSError:
        process_cwd = ""
    key = (
        str(root),
        str(config_path),
        str(shared_path or ""),
        normalized_target,
        _mtime_ns(config_path),
        _mtime_ns(shared_path) if shared_path else 0,
        str(os.environ.get("PROJECT_SEARCH_ROOTS") or ""),
        str(os.environ.get("PROJECT_SEARCH_MAX_DEPTH") or ""),
        str(node or ""),
        process_cwd,
    )
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and now - cached[0] <= _CACHE_TTL_SECONDS:
            return {**cached[1], "cacheHit": True}

    cli_path = (
        root
        / "lmstudio-unreal-agent-mcp"
        / "src"
        / "resolve-project-name-cli.js"
    )
    if not node or not cli_path.is_file():
        return {
            "ok": False,
            "status": "await_user",
            "errorCode": "PROJECT_NAME_RESOLVER_UNAVAILABLE",
            "error": "The bounded Node project-name resolver is unavailable.",
            "suggestions": [],
            "cacheHit": False,
        }

    request = {
        "workspaceRoot": str(root),
        "target": normalized_target,
    }
    if config_path.is_file():
        request["configPath"] = str(config_path)
    try:
        completed = subprocess.run(
            [node, str(cli_path)],
            input=json.dumps(request, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1.0, float(timeout_seconds)),
            check=False,
        )
        stdout_lines = [line for line in completed.stdout.splitlines() if line.strip()]
        payload = json.loads(stdout_lines[-1]) if stdout_lines else {}
        if not isinstance(payload, dict):
            raise ValueError("resolver output was not a JSON object")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError) as exc:
        payload = {
            "ok": False,
            "errorCode": "PROJECT_NAME_RESOLUTION_FAILED",
            "error": str(exc),
            "suggestions": [],
        }

    payload.setdefault("ok", False)
    payload.setdefault("suggestions", [])
    if not payload.get("ok"):
        payload.setdefault("status", "await_user")
    payload["cacheHit"] = False
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_LIMIT:
            oldest = min(_CACHE, key=lambda candidate: _CACHE[candidate][0])
            _CACHE.pop(oldest, None)
        _CACHE[key] = (now, dict(payload))
    return payload
