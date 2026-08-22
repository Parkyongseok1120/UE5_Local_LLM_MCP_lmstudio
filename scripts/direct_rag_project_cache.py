#!/usr/bin/env python
"""Minimal cache invalidation used only by Direct active-project switching."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from direct_rag_freshness import invalidate_freshness_cache
from project_context import clear_project_context_cache


def invalidate_direct_project_switch(
    previous_project: str | Path | None,
    new_project: str | Path | None,
) -> dict[str, Any]:
    clear_project_context_cache()
    invalidate_freshness_cache()
    return {
        "ok": True,
        "previousProject": str(previous_project or "") or None,
        "newProject": str(new_project or "") or None,
        "cleared": ["project_context", "direct_rag_freshness"],
        "cacheRefreshRequired": False,
        "indexRefreshRecommended": True,
        "note": "Cached index rows were not rewritten; run unreal_rag_refresh when current project evidence is needed.",
    }


__all__ = ["invalidate_direct_project_switch"]
