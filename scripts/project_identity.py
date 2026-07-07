#!/usr/bin/env python
"""Stable project identity for cache namespacing and switch invalidation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def _read_uproject_modules(uproject: Path) -> list[str]:
    try:
        data = json.loads(uproject.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    modules = data.get("Modules") if isinstance(data, dict) else None
    if not isinstance(modules, list):
        return []
    names: list[str] = []
    for item in modules:
        if isinstance(item, dict):
            name = str(item.get("Name") or "").strip()
            if name:
                names.append(name)
    return names


def resolve_uproject(project: str | Path | None) -> Path | None:
    if not project:
        return None
    path = Path(str(project)).expanduser().resolve()
    if path.suffix.lower() == ".uproject" and path.is_file():
        return path
    if path.is_dir():
        candidates = sorted(path.glob("*.uproject"))
        if len(candidates) == 1:
            return candidates[0].resolve()
    return None


def project_identity(project: str | Path | None, *, engine_version: str = "") -> dict[str, Any]:
    """Return a stable identity dict for the given .uproject path."""
    uproject = resolve_uproject(project)
    if not uproject:
        return {
            "ok": False,
            "projectId": "",
            "projectName": "",
            "uprojectPath": str(project or ""),
            "projectRoot": "",
            "engineVersion": engine_version,
            "modules": [],
        }

    project_root = uproject.parent.resolve()
    modules = sorted(_read_uproject_modules(uproject))
    stem = uproject.stem
    engine = str(engine_version or "").strip()
    digest_input = "|".join(
        [
            str(uproject.resolve()).lower(),
            stem,
            engine,
            ",".join(modules),
        ]
    )
    project_id = hashlib.sha1(digest_input.encode("utf-8")).hexdigest()[:16]
    return {
        "ok": True,
        "projectId": project_id,
        "projectName": stem,
        "uprojectPath": str(uproject),
        "projectRoot": str(project_root),
        "engineVersion": engine,
        "modules": modules,
    }
