"""Per-project collection freshness; publishing a DB does not recollect retained rows."""
from __future__ import annotations
import time
from pathlib import Path
from typing import Any
from direct_rag_freshness_rows import project_collection_fingerprint, project_row_facts
from direct_rag_generation_identity import read_consistent_index_manifest
from direct_rag_project_selectors import exact_project_descriptor
from direct_rag_source_snapshot import source_snapshot
from workspace_paths import filesystem_path_identity, resolve_active_project_path
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_TTL_SECONDS = 60.0


def invalidate_freshness_cache() -> None:
    _CACHE.clear()


def _selectors(value: Any) -> list[str]:
    raw = [value] if isinstance(value, str) else list(value) if isinstance(value, (list, tuple, set)) else []
    return list(dict.fromkeys(str(item).strip() for item in raw if str(item).strip()))


def _selected_projects(projects: Any, workspace: Path | None) -> tuple[list[Path], list[str], list[str]]:
    selectors, active = _selectors(projects), resolve_active_project_path(workspace)
    if not selectors:
        return ([active] if active else []), [], []
    selected, unresolved = [], []
    for value in selectors:
        descriptor = exact_project_descriptor(value)
        if descriptor is None and active and filesystem_path_identity(value) == filesystem_path_identity(active.stem):
            descriptor = active
        if descriptor is None:
            unresolved.append(value)
        elif descriptor not in selected:
            selected.append(descriptor)
    return selected, selectors, unresolved


def _index_fingerprint(index: Path, expected_generation: str | None) -> str:
    canonical = filesystem_path_identity(index.resolve(), strip_project_uri=False)
    generation = str(read_consistent_index_manifest(index, expected_generation=expected_generation).get("generationId") or "legacy")
    try:
        stat = index.stat()
        return f"{canonical}|{generation}|{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        return f"{canonical}|{generation}|missing"


def _project_state(index: Path, project: Path, snapshot: dict, generation: str | None) -> dict:
    rows, symbols, architecture = project_row_facts(index, project, expected_generation=generation)
    collected = project_collection_fingerprint(index, project, expected_generation=generation)
    current = snapshot.get("fingerprint")
    known = bool(collected and current and snapshot.get("status") == "complete")
    fresh, stale = bool(known and current == collected and rows), bool((known and current != collected) or not rows)
    return {
        "project": str(project),
        "projectRoot": filesystem_path_identity(project.parent, strip_project_uri=False),
        "projectName": project.stem, "status": "fresh" if fresh else "stale" if stale else "unknown",
        "reason": "up_to_date" if fresh else "project_source_changed" if known else "project_rows_missing" if not rows else "collection_provenance_unknown",
        "projectSourceFresh": True if fresh else False if stale else None,
        "projectSymbolsFresh": (fresh and symbols) if known or not symbols else None,
        "architectureFresh": (fresh and architecture) if known or not architecture else None,
        "snapshotCoverage": snapshot.get("status"),
    }


def project_freshness(index: Path, *, search_mode: str = "auto", projects: Any = None,
                      workspace: Path | None = None, expected_generation: str | None = None) -> dict[str, Any]:
    selected, selectors, unresolved = _selected_projects(projects, workspace)
    fingerprint = _index_fingerprint(index, expected_generation)
    # A cache hit must observe current inputs first, including additions/deletions.
    snapshots = [source_snapshot(project) for project in selected]
    key = "|".join([str(selected), str(selectors), search_mode, fingerprint,
                    str([item.get("fingerprint") or item.get("reason") for item in snapshots])])
    cached = _CACHE.get(key)
    if cached and time.monotonic() - cached[0] < _TTL_SECONDS:
        return dict(cached[1])
    usable = index.is_file()
    base = {"ok": True, "projectSelectors": selectors, "freshnessScope": "explicit" if selectors else "active",
            "indexUsable": usable, "indexFingerprint": fingerprint, "refreshRequired": not usable}
    if not selected and not selectors:
        payload = {**base, "reason": "no_active_project", "directSourcePreferred": False, "refreshRecommended": False}
    else:
        states = [_project_state(index, project, snapshot, expected_generation) for project, snapshot in zip(selected, snapshots)]
        complete = bool(states) and not unresolved
        fresh = complete and all(state["status"] == "fresh" for state in states)
        stale = any(state["status"] == "stale" for state in states)
        def aggregate(field: str) -> bool | None:
            values = [state[field] for state in states]
            if any(value is False for value in values):
                return False
            return True if complete and values and all(value is True for value in values) else None
        payload = {
            **base, "project": str(selected[0]) if len(selected) == 1 else None,
            "projectStates": states, "unresolvedSelectors": unresolved, "stale": stale,
            "freshnessStatus": "fresh" if fresh else "stale" if stale else "unknown",
            "reason": "up_to_date" if fresh else "explicit_project_freshness_unresolved" if unresolved else states[0]["reason"] if len(states) == 1 else "project_freshness_mixed",
            "projectSourceFresh": aggregate("projectSourceFresh"), "projectSymbolsFresh": aggregate("projectSymbolsFresh"),
            "architectureFresh": aggregate("architectureFresh"), "directSourcePreferred": not fresh,
            "refreshRecommended": usable and not fresh,
        }
    now = time.monotonic()
    for old, (timestamp, _) in list(_CACHE.items()):
        if now - timestamp >= _TTL_SECONDS:
            _CACHE.pop(old, None)
    if len(_CACHE) >= 128:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = (now, dict(payload))
    return payload


def cached_project_row_current(row: dict[str, Any], freshness: dict[str, Any]) -> bool:
    states = freshness.get("projectStates")
    if not isinstance(states, list):
        return not (freshness.get("directSourcePreferred") and (
            freshness.get("projectSymbolsFresh") is False or freshness.get("architectureFresh") is False))
    root = filesystem_path_identity(row.get("project_root") or "", strip_project_uri=False)
    name = str(row.get("project") or "").casefold()
    return any(state["projectRoot"] == root and state["projectName"].casefold() == name
               and state["status"] == "fresh" for state in states)


__all__ = ["invalidate_freshness_cache", "project_freshness", "cached_project_row_current"]
