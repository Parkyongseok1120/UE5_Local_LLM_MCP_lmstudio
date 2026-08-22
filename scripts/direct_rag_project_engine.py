#!/usr/bin/env python
"""Resolve exact project descriptors and their Unreal major/minor version."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from unreal_engine_discovery import engine_association_version

_VERSION = re.compile(r"(?<!\d)(\d+)\.(\d+)")


def normalize_engine_version(value: object) -> str:
    match = _VERSION.search(str(value or "").strip())
    return f"{int(match.group(1))}.{int(match.group(2))}" if match else ""


def engine_root_version(root: Path) -> str:
    build_version = root / "Engine" / "Build" / "Build.version"
    try:
        payload = json.loads(build_version.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if isinstance(payload, dict):
        major = payload.get("MajorVersion")
        minor = payload.get("MinorVersion")
        if isinstance(major, int) and isinstance(minor, int):
            return f"{major}.{minor}"
    return normalize_engine_version(root.name.replace("_", "."))


def project_engine_version(project: Path, workspace: Path) -> dict[str, Any]:
    try:
        payload = json.loads(project.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "errorCode": "PROJECT_DESCRIPTOR_INVALID",
            "error": f"Could not read project descriptor {project}: {exc}",
        }
    if not isinstance(payload, dict) or not isinstance(payload.get("EngineAssociation", ""), str):
        return {
            "ok": False,
            "errorCode": "PROJECT_DESCRIPTOR_INVALID",
            "error": f"Project EngineAssociation must be a string: {project}",
        }
    association = str(payload.get("EngineAssociation") or "").strip()
    version = engine_association_version(association)
    resolved_engine_root = ""
    if not version and not association:
        from workspace_paths import resolve_engine_version

        version = normalize_engine_version(resolve_engine_version(workspace))
    if not version:
        from workspace_paths import resolve_engine_root_for_association

        resolution = resolve_engine_root_for_association(association, workspace)
        if resolution.get("ok") is not True:
            return {
                "ok": False,
                "errorCode": str(resolution.get("errorCode") or "ENGINE_ASSOCIATION_UNRESOLVED"),
                "error": str(resolution.get("error") or "The project engine could not be resolved."),
            }
        resolved_engine_root = str(resolution.get("engineRoot") or "")
        version = engine_root_version(Path(resolved_engine_root))
    if not version:
        return {
            "ok": False,
            "errorCode": "PROJECT_ENGINE_VERSION_UNRESOLVED",
            "error": f"Could not determine the Unreal version for {project}.",
        }
    return {
        "ok": True,
        "project": str(project.resolve()),
        "engineAssociation": association,
        "engineVersion": version,
        "engineRoot": resolved_engine_root or None,
    }


__all__ = [
    "normalize_engine_version",
    "engine_root_version",
    "project_engine_version",
]
