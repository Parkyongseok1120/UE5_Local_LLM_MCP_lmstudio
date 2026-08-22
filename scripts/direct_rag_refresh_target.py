#!/usr/bin/env python
"""Resolve the engine-bound index directory for one exact project refresh."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from direct_rag_index_registry import resolve_request_index


def resolve_project_refresh_target(
    configured: Path,
    workspace: Path,
    project: Path,
) -> dict[str, Any]:
    path = configured.expanduser().resolve()
    base_index = path if path.name.casefold() == "rag.sqlite" else path / "rag.sqlite"
    resolution = resolve_request_index(
        base_index,
        workspace.expanduser().resolve(),
        project_selector=str(project.expanduser().resolve()),
        use_active=False,
        allow_unbuilt=True,
    )
    if resolution.get("ok") is not True:
        return resolution
    selected = Path(str(resolution["index"])).expanduser().resolve()
    return {
        **resolution,
        "index": str(selected),
        "indexDir": str(selected.parent),
    }


__all__ = ["resolve_project_refresh_target"]
