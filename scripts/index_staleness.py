#!/usr/bin/env python
"""Lightweight project-source staleness checks for RAG search responses."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from on_active_project_changed import project_index_sync_capabilities
from workspace_paths import resolve_active_project_path, resolve_index_dir


_STALE_CACHE: dict[str, Any] = {"checkedAt": 0.0, "payload": {}}
_STALE_TTL_SECONDS = 60.0


def _index_mtime_fingerprint(index_dir: Path) -> str:
    sqlite = index_dir / "rag.sqlite"
    if not sqlite.is_file():
        return "missing"
    try:
        stat = sqlite.stat()
        return f"{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        return "unreadable"


def project_source_stale_status(
    force: bool = False,
    *,
    search_mode: str = "auto",
) -> dict[str, Any]:
    """Return capability-split staleness for active project vs RAG index."""
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
            "indexUsable": True,
            "stalenessSeverity": "none",
            "analysisCanProceed": True,
            "directSourcePreferred": False,
            "refreshRecommended": False,
            "refreshRequired": False,
            "recommendedTool": None,
            "recommendedCommand": None,
            "indexFingerprint": None,
        }
        _STALE_CACHE["checkedAt"] = now
        _STALE_CACHE["payload"] = payload
        return payload

    index_dir = resolve_index_dir()
    caps = project_index_sync_capabilities(active, index_dir)
    mode = (search_mode or "auto").strip().lower()

    # Blueprint/asset graph claims need fresh editor metadata; C++ review does not.
    editor_blocks_claim = (
        not caps.get("editorMetadataFresh")
        and mode in {"blueprint_analysis", "blueprint_verification", "material_analysis", "material_porting"}
    )
    if editor_blocks_claim and caps.get("stalenessSeverity") != "blocking":
        caps = dict(caps)
        caps["stalenessSeverity"] = "claim_blocking"
        caps["analysisCanProceed"] = True
        caps["directSourcePreferred"] = True

    payload = {
        "ok": True,
        "project": str(active),
        "indexDir": str(index_dir),
        "indexFingerprint": _index_mtime_fingerprint(index_dir),
        "recommendedTool": None,
        "recommendedCommand": ".\\rag.ps1 sync-active-project" if caps.get("refreshRecommended") else None,
        **caps,
    }
    _STALE_CACHE["checkedAt"] = now
    _STALE_CACHE["payload"] = payload
    return payload


def invalidate_stale_cache() -> None:
    _STALE_CACHE["checkedAt"] = 0.0
    _STALE_CACHE["payload"] = {}
