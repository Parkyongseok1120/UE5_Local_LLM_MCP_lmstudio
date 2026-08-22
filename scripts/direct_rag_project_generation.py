#!/usr/bin/env python
"""Resolve one project's engine binding against its target RAG generation."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def resolve_project_generation(
    project: Path,
    index_dir: Path,
    workspace: Path,
) -> dict[str, Any]:
    from direct_rag_manifest_binding import resolve_generation_engine_binding
    from direct_rag_project_engine import project_engine_version

    engine = project_engine_version(project, workspace)
    if engine.get("ok") is not True:
        return engine
    binding = resolve_generation_engine_binding(
        index_dir,
        engine_version=str(engine["engineVersion"]),
        engine_association=str(engine.get("engineAssociation") or ""),
    )
    return {**binding, "engine": engine} if binding.get("ok") is not True else engine


__all__ = ["resolve_project_generation"]
