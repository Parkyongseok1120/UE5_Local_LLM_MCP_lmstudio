#!/usr/bin/env python
"""Bounded capability and MCP result types for Direct RAG."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

DEFAULT_DIRECT_RESULT_CHARS = 32_000
MAX_DIRECT_RESULT_CHARS = 80_000
_CONTROL_KEYS = frozenset(
    {
        "agentInstruction",
        "agentWorkflow",
        "allowedPatchTargets",
        "allowedTools",
        "assemblyInstructions",
        "authorizationBound",
        "chatAction",
        "chatMessage",
        "control",
        "controlEpoch",
        "doNotRepeatSearch",
        "doNotRetry",
        "doNotRetryTools",
        "nextAction",
        "nextActionArgs",
        "nextActionIsTool",
        "nextActions",
        "ownerCapability",
        "pendingGates",
        "requiredNextAction",
        "requiredNextTool",
        "requiredNextToolArgs",
        "requiredReads",
        "routeHash",
        "stopCurrentWorkflow",
        "softSteering",
        "taskAuthorization",
        "toolRoute",
        "forbiddenActions",
    }
)


def configured_result_limit() -> int:
    raw = str(os.environ.get("MCP_TOOL_RESULT_MAX_CHARS") or "").strip()
    if not raw:
        return DEFAULT_DIRECT_RESULT_CHARS
    try:
        return max(2_000, min(int(raw), MAX_DIRECT_RESULT_CHARS))
    except ValueError:
        return DEFAULT_DIRECT_RESULT_CHARS


@dataclass(frozen=True)
class CapabilityResult:
    payload: dict[str, Any]
    is_error: bool = False
    char_limit: int | None = None
    rollback_delivery_key: str = ""


def _strip_control(value: Any, depth: int = 0) -> Any:
    if depth > 12:
        return "[depth limited]"
    if isinstance(value, list):
        return [_strip_control(item, depth + 1) for item in value[:1_000]]
    if not isinstance(value, dict):
        if isinstance(value, str) and len(value) > 256_000:
            return value[:255_980] + "\n...[truncated]"
        return value
    clean: dict[str, Any] = {}
    for key, item in value.items():
        folded = str(key).casefold()
        if key in _CONTROL_KEYS or folded.startswith("task"):
            continue
        if folded.startswith("synthesis") or folded.startswith("route"):
            continue
        clean[key] = _strip_control(item, depth + 1)
    return clean


def _normalize(result: CapabilityResult) -> dict[str, Any]:
    source = _strip_control(dict(result.payload))
    ok = not result.is_error and source.get("ok") is not False
    source["ok"] = ok
    source.pop("isError", None)
    if ok:
        return source
    source["errorCode"] = str(source.get("errorCode") or "TOOL_FAILED")[:120]
    source["message"] = str(
        source.get("message") or source.pop("error", "The tool call failed.")
    )[:4_000]
    source.pop("error", None)
    retry = source.get("retry") if isinstance(source.get("retry"), dict) else {}
    allowed = retry.get("allowed") is True
    source["retry"] = {
        "allowed": allowed,
        "mode": str(retry.get("mode") or "different_arguments") if allowed else "none",
    }
    return source


def _bounded(payload: dict[str, Any], limit: int) -> tuple[dict[str, Any], str]:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(rendered) <= limit:
        return payload, rendered
    if payload.get("ok") is not False:
        compact = {
            "ok": False,
            "errorCode": "OUTPUT_LIMIT_EXCEEDED",
            "message": (
                "The result exceeded the transport limit. Request a smaller detail level "
                "or result count."
            ),
            "retry": {"allowed": True, "mode": "different_arguments"},
        }
        return compact, json.dumps(compact, ensure_ascii=False, indent=2)
    compact = {
        "ok": False,
        "errorCode": str(payload.get("errorCode") or "TOOL_FAILED"),
        "message": str(payload.get("message") or "The tool call failed.")[:800],
        "retry": payload.get("retry") or {"allowed": False, "mode": "none"},
    }
    return compact, json.dumps(compact, ensure_ascii=False, indent=2)


def success(**payload: Any) -> CapabilityResult:
    return CapabilityResult({"ok": True, **payload})


def failure(
    error_code: str,
    message: str,
    *,
    retry_allowed: bool = False,
    retry_mode: str = "different_arguments",
    **payload: Any,
) -> CapabilityResult:
    return CapabilityResult(
        {
            "ok": False,
            "errorCode": str(error_code or "TOOL_FAILED")[:120],
            "message": str(message or "The tool call failed.")[:4_000],
            "retry": {
                "allowed": bool(retry_allowed),
                "mode": retry_mode if retry_allowed else "none",
            },
            **payload,
        },
        is_error=True,
        char_limit=4_096,
    )


def to_mcp_tool_result(
    result: CapabilityResult,
    *,
    tool_name: str,
) -> dict[str, Any]:
    """Normalize once and keep text/structured content exactly consistent."""

    del tool_name
    normalized = _normalize(result)
    limit = min(result.char_limit or configured_result_limit(), configured_result_limit())
    bounded, rendered = _bounded(normalized, limit)
    if result.payload.get("ok") is not False and bounded.get("ok") is False:
        delivery_key = str(result.rollback_delivery_key or "").strip()
        if delivery_key:
            from direct_rag_history import forget

            forget(delivery_key)
    is_error = bool(result.is_error or bounded.get("ok") is False)
    return {
        "content": [{"type": "text", "text": rendered}],
        "structuredContent": bounded,
        "isError": is_error,
    }


def serialized_size(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


__all__ = [
    "CapabilityResult",
    "configured_result_limit",
    "failure",
    "serialized_size",
    "success",
    "to_mcp_tool_result",
]
