#!/usr/bin/env python
"""Canonical control-plane state root shared by RAG and agent MCP servers."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_RELATIVE = Path(".lmstudio") / "state" / "unreal-agent"


def resolve_shared_config_path() -> Path:
    raw = os.environ.get("SHARED_UNREAL_CONFIG", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / ".lmstudio" / "config" / "unreal-workspace.json").resolve()


def resolve_agent_state_root(workspace: Path | None = None) -> Path:
    del workspace  # workspace does not affect control-plane state root
    override = os.environ.get("AGENT_STATE_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    config_path = resolve_shared_config_path()
    config_dir = config_path.parent
    # Installed layouts use <app>/config/unreal-workspace.json.  Tests,
    # portable launches, and explicit overrides may instead point directly to
    # <dir>/unreal-workspace.json; walking two parents from that form can land
    # at /state on POSIX and fail with a permission error.
    app_root = config_dir.parent if config_dir.name.casefold() == "config" else config_dir
    return (app_root / "state" / "unreal-agent").resolve()


def ensure_state_root_layout(state_root: Path | None = None) -> Path:
    root = state_root or resolve_agent_state_root()
    for sub in ("locks", "transactions", "tasks", "jobs", "backups", "reports"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def task_state_dir(task_session_id: str, state_root: Path | None = None) -> Path:
    safe = str(task_session_id or "").strip()
    if not safe or ".." in safe or "/" in safe or "\\" in safe:
        raise ValueError("invalid taskSessionId")
    root = ensure_state_root_layout(state_root)
    tasks_root = (root / "tasks").resolve()
    target = (tasks_root / safe).resolve()
    if target != tasks_root and tasks_root not in target.parents:
        raise ValueError("taskSessionId resolves outside state tasks root")
    return target


def jobs_sqlite_path(state_root: Path | None = None) -> Path:
    root = ensure_state_root_layout(state_root)
    return root / "jobs" / "jobs.sqlite"
