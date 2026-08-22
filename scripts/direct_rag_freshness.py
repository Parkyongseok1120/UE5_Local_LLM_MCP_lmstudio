#!/usr/bin/env python
"""Conservative project/index freshness facts for Direct RAG."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from direct_rag_freshness_rows import project_row_facts
from direct_rag_generation_identity import read_consistent_index_manifest
from workspace_paths import filesystem_path_identity, resolve_active_project_path

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_TTL_SECONDS = 60.0
_SOURCE_EXTENSIONS = frozenset({".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".cs"})
_SKIP_DIRS = frozenset({"binaries", "deriveddatacache", "intermediate", "saved", ".git"})


def invalidate_freshness_cache() -> None:
    _CACHE.clear()


def _selectors(value: Any) -> list[str]:
    raw = [value] if isinstance(value, str) else list(value) if isinstance(value, (list, tuple, set)) else []
    return list(dict.fromkeys(str(item).strip() for item in raw if str(item).strip()))


def _selected_project(
    projects: Any,
    workspace: Path | None,
) -> tuple[Path | None, list[str]]:
    selectors = _selectors(projects)
    active = resolve_active_project_path(workspace)
    if not selectors:
        return active, []
    for value in selectors:
        candidate = Path(value).expanduser()
        if candidate.is_file() and candidate.suffix.casefold() == ".uproject":
            return candidate.resolve(), selectors
        if candidate.is_dir():
            descriptors = sorted(candidate.glob("*.uproject"))
            if len(descriptors) == 1:
                return descriptors[0].resolve(), selectors
    if active:
        active_ids = {
            filesystem_path_identity(active),
            filesystem_path_identity(active.stem),
        }
        if any(filesystem_path_identity(item) in active_ids for item in selectors):
            return active, selectors
    return None, selectors


def _index_fingerprint(index: Path, expected_generation: str | None) -> str:
    canonical = filesystem_path_identity(index.resolve(), strip_project_uri=False)
    generation = str(
        read_consistent_index_manifest(
            index,
            expected_generation=expected_generation,
        ).get("generationId")
        or "legacy"
    ).strip()
    try:
        stat = index.stat()
        return f"{canonical}|{generation}|{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        return f"{canonical}|{generation}|missing"


def _newest_source(project: Path) -> float | None:
    newest: float | None = None
    roots = [project.parent / "Source", project.parent / "Plugins"]
    for root in roots:
        if not root.is_dir():
            continue
        for directory, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name.casefold() not in _SKIP_DIRS]
            base = Path(directory)
            for name in filenames:
                path = base / name
                if path.suffix.casefold() not in _SOURCE_EXTENSIONS:
                    continue
                try:
                    modified = path.stat().st_mtime
                except OSError:
                    continue
                newest = modified if newest is None else max(newest, modified)
    return newest


def project_freshness(
    index: Path,
    *,
    search_mode: str = "auto",
    projects: Any = None,
    workspace: Path | None = None,
    expected_generation: str | None = None,
) -> dict[str, Any]:
    selected, selectors = _selected_project(projects, workspace)
    fingerprint = _index_fingerprint(index, expected_generation)
    cache_key = "|".join([str(selected or ""), "\x1f".join(selectors), search_mode, fingerprint])
    cached = _CACHE.get(cache_key)
    if cached and time.time() - cached[0] < _TTL_SECONDS:
        return dict(cached[1])
    if selectors and selected is None:
        payload = {
            "ok": True,
            "reason": "explicit_project_freshness_unresolved",
            "projectSelectors": selectors,
            "freshnessScope": "explicit",
            "indexUsable": index.is_file(),
            "directSourcePreferred": True,
            "refreshRecommended": False,
            "refreshRequired": False,
            "indexFingerprint": fingerprint,
        }
    elif selected is None:
        payload = {
            "ok": True,
            "reason": "no_active_project",
            "freshnessScope": "active",
            "indexUsable": index.is_file(),
            "directSourcePreferred": False,
            "refreshRecommended": False,
            "refreshRequired": not index.is_file(),
            "indexFingerprint": fingerprint,
        }
    else:
        has_rows, has_symbols, has_architecture = project_row_facts(
            index,
            selected,
            expected_generation=expected_generation,
        )
        newest = _newest_source(selected)
        try:
            index_mtime = index.stat().st_mtime
        except OSError:
            index_mtime = 0.0
        source_newer = newest is not None and newest > index_mtime
        usable = index.is_file() and index_mtime > 0
        stale = source_newer or not has_rows
        payload = {
            "ok": True,
            "project": str(selected),
            "projectSelectors": selectors,
            "freshnessScope": "explicit" if selectors else "active",
            "indexUsable": usable,
            "stale": stale,
            "reason": "project_source_newer_than_index" if source_newer else "project_rows_missing" if not has_rows else "up_to_date",
            "projectSourceFresh": has_rows and not source_newer,
            "projectSymbolsFresh": has_symbols and not source_newer,
            "architectureFresh": has_architecture and not source_newer,
            "directSourcePreferred": source_newer or not has_symbols,
            "refreshRecommended": usable and stale,
            "refreshRequired": not usable,
            "indexFingerprint": fingerprint,
        }
    _CACHE[cache_key] = (time.time(), dict(payload))
    return payload


__all__ = ["invalidate_freshness_cache", "project_freshness"]
