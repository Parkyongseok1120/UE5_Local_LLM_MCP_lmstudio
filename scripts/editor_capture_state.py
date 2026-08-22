#!/usr/bin/env python
"""Persist project-and-kind completion facts for Editor export generations."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from atomic_io import atomic_write_text
from workspace_paths import filesystem_path_identity

STATE_NAME = "editor_capture_state.json"
SCHEMA_VERSION = 1


def _descriptor(value: Path) -> Path | None:
    candidate = value.expanduser().resolve()
    if candidate.is_file() and candidate.suffix.casefold() == ".uproject":
        return candidate
    if candidate.is_dir():
        descriptors = sorted(candidate.glob("*.uproject"))
        if len(descriptors) == 1:
            return descriptors[0].resolve()
    return None


def _identity(project: Path) -> str:
    return filesystem_path_identity(project.resolve(), strip_project_uri=False)


def _key(identity: str) -> str:
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schemaVersion": SCHEMA_VERSION, "projects": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schemaVersion": SCHEMA_VERSION, "projects": {}}
    if not isinstance(payload, dict) or payload.get("schemaVersion") != SCHEMA_VERSION:
        return {"schemaVersion": SCHEMA_VERSION, "projects": {}}
    if not isinstance(payload.get("projects"), dict):
        payload["projects"] = {}
    return payload


def record_completed_capture(index_dir: Path, manifest: dict[str, Any]) -> None:
    project = _descriptor(Path(str(manifest.get("projectFile") or "")))
    exports = manifest.get("exports")
    if project is None or not isinstance(exports, list):
        raise RuntimeError("Completed Editor manifest has no exact project or export list")
    identity = _identity(project)
    state_path = index_dir.resolve() / STATE_NAME
    state = _load(state_path)
    projects = state["projects"]
    previous = projects.get(_key(identity))
    kinds = dict(previous.get("kinds") or {}) if isinstance(previous, dict) else {}
    for entry in exports:
        if not isinstance(entry, dict):
            raise RuntimeError("Completed Editor manifest export entry is invalid")
        kind = str(entry.get("kind") or "").strip()
        if not kind:
            raise RuntimeError("Completed Editor manifest export kind is missing")
        kinds[kind] = {
            "kind": kind,
            "runId": str(manifest.get("runId") or ""),
            "capturedAt": float(manifest["capturedAt"]),
            "file": str(entry.get("file") or ""),
            "sha256": str(entry.get("sha256") or ""),
            "sizeBytes": int(entry.get("sizeBytes") or 0),
            "rowCount": int(entry.get("rowCount") or 0),
        }
    projects[_key(identity)] = {
        "projectFile": str(project),
        "projectRoot": str(project.parent),
        "kinds": kinds,
    }
    atomic_write_text(state_path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def completed_capture(
    index_dir: Path,
    project_file_or_root: Path | None,
    kind: str,
) -> dict[str, Any] | None:
    if project_file_or_root is None:
        return None
    project = _descriptor(project_file_or_root)
    if project is None:
        return None
    identity = _identity(project)
    project_state = _load(index_dir.resolve() / STATE_NAME)["projects"].get(_key(identity))
    if not isinstance(project_state, dict):
        return None
    stored = filesystem_path_identity(
        project_state.get("projectFile") or "", strip_project_uri=False
    )
    if stored != identity:
        return None
    kinds = project_state.get("kinds")
    selected = kinds.get(kind) if isinstance(kinds, dict) else None
    if not isinstance(selected, dict) and kind in {
        "skeletal_mesh", "anim_blueprint", "anim_montage", "sequencer"
    }:
        selected = kinds.get("animation") if isinstance(kinds, dict) else None
    if not isinstance(selected, dict):
        return None
    captured = selected.get("capturedAt")
    return selected if isinstance(captured, (int, float)) and captured > 0 else None


__all__ = ["STATE_NAME", "completed_capture", "record_completed_capture"]
