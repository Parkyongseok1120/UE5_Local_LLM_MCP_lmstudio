#!/usr/bin/env python
"""Disk cache for symbol candidate queries."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

DEFAULT_TTL_SECONDS = 3600


def cache_dir(workspace: Path) -> Path:
    path = workspace / "data" / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_key(query: str, projects: list[str] | None, limit: int) -> str:
    payload = json.dumps(
        {"query": query, "projects": sorted(projects or []), "limit": limit},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_cached(workspace: Path, query: str, projects: list[str] | None, limit: int, ttl: int = DEFAULT_TTL_SECONDS) -> list[dict[str, Any]] | None:
    path = cache_dir(workspace) / f"{cache_key(query, projects, limit)}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - float(payload.get("cachedAt", 0)) > ttl:
            return None
        rows = payload.get("rows")
        return rows if isinstance(rows, list) else None
    except Exception:
        return None


def set_cached(workspace: Path, query: str, projects: list[str] | None, limit: int, rows: list[dict[str, Any]]) -> None:
    path = cache_dir(workspace) / f"{cache_key(query, projects, limit)}.json"
    path.write_text(
        json.dumps({"cachedAt": time.time(), "rows": rows}, ensure_ascii=False),
        encoding="utf-8",
    )
