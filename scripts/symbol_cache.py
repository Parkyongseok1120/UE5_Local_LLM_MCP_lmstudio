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


def invalidate_project_caches(workspace: Path, project_names: list[str], project_stem: str = "") -> int:
    """Remove disk cache entries keyed to the given project name(s). Returns files removed."""
    names = {name.strip() for name in project_names if str(name).strip()}
    if project_stem:
        names.add(project_stem.strip())
    if not names:
        return 0
    removed = 0
    root = cache_dir(workspace)
    for path in root.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        # Cache files do not store project names directly; key includes sorted projects.
        # Best-effort: delete entries whose filename key was built with those project filters.
        for name in names:
            probe_key = cache_key(f"__invalidate__{name}", [name], 64)
            if path.stem == probe_key:
                path.unlink(missing_ok=True)
                removed += 1
                break
    # Conservative fallback: when we cannot match keys, leave disk cache until TTL expiry.
    return removed
