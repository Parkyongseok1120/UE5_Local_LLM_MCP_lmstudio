#!/usr/bin/env python
"""Resolve explicit engine provenance for a portable Direct RAG build."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def resolve_build_binding(
    workspace: Path,
    project: Path | None,
    engine_version: str | None,
    engine_association: str | None,
) -> dict[str, Any]:
    if (engine_version is None) != (engine_association is None):
        return {
            "ok": False,
            "errorCode": "RAG_ENGINE_BINDING_INCOMPLETE",
            "error": "--engine-version and --engine-association must be supplied together.",
        }
    if project is None:
        return {
            "ok": True,
            "engineVersion": engine_version,
            "engineAssociation": engine_association,
        }
    if engine_version is not None:
        return {
            "ok": False,
            "errorCode": "RAG_ENGINE_BINDING_CONFLICT",
            "error": "Use either --project or the explicit engine binding pair, not both.",
        }
    descriptor = project.expanduser()
    if not descriptor.is_absolute():
        descriptor = workspace / descriptor
    descriptor = descriptor.resolve()
    if not descriptor.is_file() or descriptor.suffix.casefold() != ".uproject":
        return {
            "ok": False,
            "errorCode": "PROJECT_SELECTOR_NOT_FOUND",
            "error": f"Build project is not one exact existing .uproject: {descriptor}",
        }
    from direct_rag_project_engine import project_engine_version

    binding = project_engine_version(descriptor, workspace)
    return {**binding, "projectFile": str(descriptor)}


__all__ = ["resolve_build_binding"]
