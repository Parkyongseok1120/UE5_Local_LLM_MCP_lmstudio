#!/usr/bin/env python
"""Archived workflow-era project-source staleness checks."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from on_active_project_changed import project_index_sync_capabilities
from workspace_paths import resolve_active_project_path, resolve_index_dir


_STALE_CACHE: dict[str, dict[str, Any]] = {}
_STALE_TTL_SECONDS = 60.0


def _staleness_cache_key(
    active: Path,
    index_dir: Path,
    search_mode: str,
    projects: Any = None,
) -> str:
    from read_query_history import index_fingerprint, normalize_project_selectors

    return "|".join(
        [
            str(active.resolve()),
            str(index_dir.resolve()),
            index_fingerprint(index_dir / "rag.sqlite"),
            (search_mode or "auto").strip().lower(),
            "\x1f".join(normalize_project_selectors(projects)),
        ]
    )


def _explicit_project_path(
    active: Path | None,
    projects: Any = None,
) -> Path | None:
    from read_query_history import normalize_project_selectors
    from workspace_paths import filesystem_path_identity

    selectors = normalize_project_selectors(projects)
    if not selectors:
        return active
    raw = (
        [projects]
        if isinstance(projects, str)
        else list(projects)
        if isinstance(projects, (list, tuple, set, frozenset))
        else []
    )
    for item in raw:
        candidate = Path(str(item)).expanduser()
        if candidate.is_file() and candidate.suffix.casefold() == ".uproject":
            return candidate.resolve()
        if candidate.is_dir():
            descriptors = sorted(candidate.glob("*.uproject"))
            if len(descriptors) == 1:
                return descriptors[0].resolve()
    if active:
        active_identities = {
            filesystem_path_identity(active),
            filesystem_path_identity(active.stem),
            filesystem_path_identity(active.parent.name),
        }
        if any(selector in active_identities for selector in selectors):
            return active
    return None


def invalidate_stale_cache(project: Path | str | None = None) -> None:
    if project is None:
        _STALE_CACHE.clear()
        return
    prefix = str(Path(project).resolve()) + "|"
    for key in list(_STALE_CACHE):
        if key.startswith(prefix):
            _STALE_CACHE.pop(key, None)


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
    projects: Any = None,
) -> dict[str, Any]:
    """Return staleness for the explicit query project or active project."""
    from read_query_history import normalize_project_selectors

    now = time.time()
    active = resolve_active_project_path()
    project_selectors = normalize_project_selectors(projects)
    selected = _explicit_project_path(active, projects)
    index_dir = resolve_index_dir()
    cache_key = _staleness_cache_key(
        selected or Path("_none_"),
        index_dir,
        search_mode,
        project_selectors,
    )
    cached_entry = _STALE_CACHE.get(cache_key)
    if not force and cached_entry:
        manifest_fp = _index_mtime_fingerprint(index_dir)
        age = now - float(cached_entry.get("checkedAt") or 0.0)
        payload = cached_entry.get("payload")
        if isinstance(payload, dict) and cached_entry.get("manifestFp") == manifest_fp and age < _STALE_TTL_SECONDS:
            return payload
        if isinstance(payload, dict) and cached_entry.get("manifestFp") == manifest_fp and age < 300.0:
            return payload

    if project_selectors and not selected:
        payload = {
            "ok": True,
            "stale": False,
            "reason": "explicit_project_freshness_unresolved",
            "projectSelectors": list(project_selectors),
            "freshnessScope": "explicit",
            "indexUsable": True,
            "stalenessSeverity": "none",
            "analysisCanProceed": True,
            "directSourcePreferred": True,
            "refreshRecommended": False,
            "refreshRequired": False,
            "refreshTargetsSelectedProject": False,
            "recommendedTool": None,
            "recommendedCommand": None,
            "indexFingerprint": _index_mtime_fingerprint(index_dir),
        }
        _STALE_CACHE[cache_key] = {
            "checkedAt": now,
            "payload": payload,
            "manifestFp": _index_mtime_fingerprint(index_dir),
        }
        return payload

    if not selected:
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
            "refreshTargetsSelectedProject": True,
            "recommendedTool": None,
            "recommendedCommand": None,
            "indexFingerprint": None,
        }
        _STALE_CACHE[cache_key] = {"checkedAt": now, "payload": payload}
        return payload

    caps = project_index_sync_capabilities(selected, index_dir)
    manifest_fp = _index_mtime_fingerprint(index_dir)
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
        "project": str(selected),
        "projectSelectors": list(project_selectors),
        "freshnessScope": "explicit" if project_selectors else "active",
        "indexDir": str(index_dir),
        "indexFingerprint": _index_mtime_fingerprint(index_dir),
        "recommendedTool": None,
        "refreshTargetsSelectedProject": bool(
            not project_selectors
            or (active and selected.resolve() == active.resolve())
        ),
        "recommendedCommand": (
            ".\\rag.ps1 sync-active-project"
            if caps.get("refreshRecommended")
            and (
                not project_selectors
                or (active and selected.resolve() == active.resolve())
            )
            else None
        ),
        **caps,
    }
    _STALE_CACHE[cache_key] = {"checkedAt": now, "payload": payload, "manifestFp": manifest_fp}
    return payload


def invalidate_stale_cache_legacy() -> None:
    invalidate_stale_cache(None)
