#!/usr/bin/env python
"""Lightweight project-source staleness checks for RAG search responses."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from on_active_project_changed import project_index_needs_sync
from workspace_paths import resolve_active_project_path, resolve_index_dir


_STALE_CACHE: dict[str, Any] = {"checkedAt": 0.0, "payload": {}}
_STALE_TTL_SECONDS = 60.0


def project_source_stale_status(force: bool = False) -> dict[str, Any]:
    """Return whether active project sources/metadata are newer than the RAG index."""
    now = time.time()
    if not force and now - float(_STALE_CACHE.get("checkedAt") or 0.0) < _STALE_TTL_SECONDS:
        cached = _STALE_CACHE.get("payload")
        if isinstance(cached, dict):
            return cached

    active = resolve_active_project_path()
    if not active:
        payload = {
            "ok": True,
            "stale": False,
            "reason": "no_active_project",
            "recommendedTool": None,
        }
        _STALE_CACHE["checkedAt"] = now
        _STALE_CACHE["payload"] = payload
        return payload

    index_dir = resolve_index_dir()
    stale, reason = project_index_needs_sync(active, index_dir)
    payload = {
        "ok": True,
        "stale": bool(stale),
        "reason": reason,
        "project": str(active),
        "indexDir": str(index_dir),
        "recommendedTool": "unreal_rag_refresh" if stale else None,
        "recommendedCommand": ".\\rag.ps1 sync-active-project" if stale else None,
    }
    _STALE_CACHE["checkedAt"] = now
    _STALE_CACHE["payload"] = payload
    return payload


def invalidate_stale_cache() -> None:
    _STALE_CACHE["checkedAt"] = 0.0
    _STALE_CACHE["payload"] = {}
