#!/usr/bin/env python
"""Resolve exact project descriptors and their Unreal major/minor version."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from engine_identity import infer_unreal_version_from_folder, read_unreal_build_identity
from unreal_engine_discovery import engine_association_version

_VERSION = re.compile(r"(?<!\d)(\d+)\.(\d+)")


def normalize_engine_version(value: object) -> str:
    match = _VERSION.search(str(value or "").strip())
    return f"{int(match.group(1))}.{int(match.group(2))}" if match else ""


def engine_root_version_with_source(root: Path) -> tuple[str, str]:
    """``(major.minor, source)`` for an engine root; ``("", "")`` when unknown.

    A strict Build.version observation wins. A folder-name guess is returned
    only as ``folder_name_inferred`` so callers never treat it as verified.
    """
    identity = read_unreal_build_identity(root)
    if identity["status"] == "observed":
        return f"{identity['major']}.{identity['minor']}", identity["source"]
    guess = infer_unreal_version_from_folder(root)
    if guess.get("version"):
        return f"{guess['major']}.{guess['minor']}", guess["source"]
    return "", ""


def engine_root_version(root: Path) -> str:
    # String-only consumers cannot retain provenance; expose only observed
    # versions here. Discovery/UI callers can use the labelled tuple instead.
    version, source = engine_root_version_with_source(root)
    return version if source == "build_version_file" else ""


def project_engine_version(project: Path, workspace: Path, *, engine_root: Path | None = None) -> dict[str, Any]:
    """Bind a project to its engine version.

    ``engine_root`` is an explicit engine chosen by the caller (for example an
    installer). It only supplies an engine for a project whose EngineAssociation
    is empty; a declared association is never overridden.
    """
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
    version_source = "project_association" if version else ""
    resolved_engine_root = ""
    if not version:
        # An empty or non-numeric association is resolved to a real engine and
        # its version is read from that engine. No default version is assumed.
        from workspace_paths import resolve_engine_root_for_association

        explicit = {"explicit_engine_root": engine_root} if engine_root is not None and not association else {}
        resolution = resolve_engine_root_for_association(association, workspace, **explicit)
        if resolution.get("ok") is not True:
            return {
                "ok": False,
                "errorCode": str(resolution.get("errorCode") or "ENGINE_ASSOCIATION_UNRESOLVED"),
                "error": str(resolution.get("error") or "The project engine could not be resolved."),
                "engineVersionStatus": "unknown",
            }
        resolved_engine_root = str(resolution.get("engineRoot") or "")
        version, version_source = engine_root_version_with_source(Path(resolved_engine_root))
    if not version:
        return {
            "ok": False,
            "errorCode": "PROJECT_ENGINE_VERSION_UNRESOLVED",
            "error": f"Could not determine the Unreal version for {project}.",
            "engineVersionStatus": "unknown",
            "engineRoot": resolved_engine_root or None,
        }
    return {
        "ok": True,
        "project": str(project.resolve()),
        "engineAssociation": association,
        "engineVersion": version,
        "engineVersionSource": version_source,
        "engineVersionStatus": "unknown" if version_source == "folder_name_inferred" else "declared_or_observed",
        "engineRoot": resolved_engine_root or None,
    }


__all__ = [
    "normalize_engine_version",
    "engine_root_version",
    "engine_root_version_with_source",
    "project_engine_version",
]
