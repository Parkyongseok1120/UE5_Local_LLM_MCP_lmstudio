"""Resolve the exact project descriptor and its Unreal Engine binding."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any


def resolve_project_file(
    workspace: Path,
    explicit: str | Path | None,
    *,
    active_project_resolver: Callable[[Path], Path | None],
) -> Path | None:
    if explicit is not None and str(explicit).strip():
        candidate = Path(str(explicit)).expanduser()
        if not candidate.is_absolute():
            candidate = workspace / candidate
    else:
        candidate = active_project_resolver(workspace)
    if candidate is None:
        return None
    resolved = candidate.resolve()
    if resolved.suffix.casefold() != ".uproject" or not resolved.is_file():
        return None
    return resolved


def project_engine_association(uproject: Path) -> tuple[str, str]:
    try:
        descriptor = json.loads(uproject.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return "", f"Could not read project descriptor {uproject}: {exc}"
    if not isinstance(descriptor, dict):
        return "", f"Project descriptor is not a JSON object: {uproject}"
    association = descriptor.get("EngineAssociation")
    if association is None:
        return "", ""
    if not isinstance(association, str):
        return "", f"Project EngineAssociation must be a string: {uproject}"
    return association.strip(), ""


def resolve_project_engine_root(
    uproject: Path,
    workspace: Path,
    *,
    engine_resolver: Callable[[object, Path], dict[str, Any]],
) -> dict[str, Any]:
    association, descriptor_error = project_engine_association(uproject)
    if descriptor_error:
        return {
            "ok": False,
            "engineRoot": "",
            "source": "",
            "requestedEngineAssociation": "",
            "errorCode": "PROJECT_DESCRIPTOR_INVALID",
            "error": descriptor_error,
        }
    return dict(engine_resolver(association, workspace))


__all__ = [
    "project_engine_association",
    "resolve_project_engine_root",
    "resolve_project_file",
]
