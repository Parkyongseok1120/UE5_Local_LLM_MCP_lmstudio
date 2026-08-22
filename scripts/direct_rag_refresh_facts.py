#!/usr/bin/env python
"""Factual result projection shared by Direct RAG refresh entry points."""

from __future__ import annotations

from typing import Any


REFRESH_FACT_FIELDS = frozenset(
    {
        "ok",
        "error",
        "errorCode",
        "scope",
        "project",
        "projectSourceSync",
        "editorMetadataSetup",
        "editorLaunchAllowed",
        "cacheInvalidated",
    }
)
DIRECTIVE_FIELDS = frozenset(
    {
        "actions",
        "agentWorkflow",
        "allowedPatchTargets",
        "assemblyInstructions",
        "chatAction",
        "chatMessage",
        "forbiddenActions",
        "nextAction",
        "nextActions",
        "requiredReads",
        "requiredNextAction",
        "requiredNextTool",
        "softSteering",
    }
)


def _factual_value(value: Any, depth: int = 0) -> Any:
    if depth > 10:
        return "[depth limited]"
    if isinstance(value, list):
        return [_factual_value(item, depth + 1) for item in value[:256]]
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    for key, item in value.items():
        folded = str(key).casefold()
        if key in DIRECTIVE_FIELDS or folded.startswith(("task", "route", "synthesis")):
            continue
        result[key] = _factual_value(item, depth + 1)
    return result


def project_refresh_facts(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep bounded refresh facts and recursively remove tool-order authority."""

    selected = {key: value for key, value in payload.items() if key in REFRESH_FACT_FIELDS}
    return _factual_value(selected)


__all__ = ["project_refresh_facts"]
