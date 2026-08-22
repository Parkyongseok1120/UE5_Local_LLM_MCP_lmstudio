#!/usr/bin/env python
"""Active-project read/switch capabilities for the Direct RAG server."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from direct_rag_result import CapabilityResult, failure, success
from project_context import resolve_active_project_context
from project_controller import switch_active_project
from workspace_paths import (
    active_project_names,
    load_shared_config,
    resolve_active_project_path,
    shared_config_path,
)


class ProjectRuntime(Protocol):
    workspace: Path


def get_active_project(
    runtime: ProjectRuntime,
    arguments: dict[str, Any],
) -> CapabilityResult:
    del arguments
    configured = str(load_shared_config().get("activeProject") or "").strip()
    resolved = resolve_active_project_path(runtime.workspace)
    context = resolve_active_project_context(runtime.workspace)
    if not configured:
        binding = "unbound"
    else:
        binding = (
            "bound"
            if resolved is not None
            and resolved.is_file()
            and resolved.suffix.casefold() == ".uproject"
            else "stale"
        )
    clean_context = {
        key: value
        for key, value in context.items()
        if key not in {"suggestedToolCalls", "error"}
    }
    return success(
        activeProject=str(resolved) if resolved is not None else configured or None,
        activeProjectNames=active_project_names(),
        projectBindingStatus=binding,
        sharedConfigPath=str(shared_config_path()),
        projectContext=clean_context,
    )


def set_active_project(
    runtime: ProjectRuntime,
    arguments: dict[str, Any],
) -> CapabilityResult:
    clear = arguments.get("clear") is True
    raw_path = str(arguments.get("projectPath") or "").strip()
    if clear and raw_path:
        return failure(
            "INVALID_TOOL_ARGUMENTS",
            "projectPath and clear=true are mutually exclusive.",
            retry_allowed=True,
        )
    if not clear and not raw_path:
        return failure(
            "INVALID_TOOL_ARGUMENTS",
            "Provide one absolute projectPath or set clear=true.",
            retry_allowed=True,
        )
    if raw_path and not Path(raw_path).expanduser().is_absolute():
        return failure(
            "PROJECT_PATH_NOT_ABSOLUTE",
            "projectPath must be an absolute path to one existing .uproject file.",
            retry_allowed=True,
        )

    payload = switch_active_project(
        runtime.workspace,
        project_path=raw_path or None,
        clear=clear,
    )
    if payload.get("ok") is not True:
        return failure(
            str(payload.get("errorCode") or "PROJECT_SWITCH_FAILED"),
            str(payload.get("error") or "The active project could not be changed."),
            retry_allowed=True,
            switchResult=str(payload.get("switchResult") or "failed"),
            activeProject=payload.get("activeProject"),
        )
    return success(
        **{
            key: value
            for key, value in payload.items()
            if key not in {"ok", "autoSetup"}
        },
        activeProjectNames=active_project_names(),
    )


def capability_handlers() -> dict[str, Any]:
    return {
        "unreal_get_active_project": get_active_project,
        "unreal_set_active_project": set_active_project,
    }


__all__ = ["capability_handlers", "get_active_project", "set_active_project"]
