"""Stable MCP control envelope consumed by UI and context compaction."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any


CONTROL_VERSION = 2
CONTROL_DISPOSITIONS = frozenset(
    {
        "continue",
        "require_tool",
        "rediscover",
        "checkpoint",
        "await_user",
        "workflow_stop",
        "complete",
    }
)
_DISCOVERY_TOOL_NAMES = frozenset(
    {
        "unreal_rag_search",
        "unreal_symbol_lookup",
        "list_directory",
        "search_files",
        "read_file",
        "read_file_range",
        "read_symbol",
        "read_unreal_logs",
    }
)


def _action_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("tool") or "")
    return str(value or "")


def _fingerprint(payload: dict[str, Any]) -> str:
    existing = str(payload.get("blockerFingerprint") or "").strip()
    if existing:
        return existing
    material = {
        "errorCode": payload.get("errorCode"),
        "blockers": payload.get("blockers") or payload.get("firstBlocker"),
        "missing": payload.get("missingFields") or payload.get("missingJsonPaths"),
    }
    if not any(value not in (None, "", [], {}) for value in material.values()):
        return ""
    encoded = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


def _clean_tool_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(
        dict.fromkeys(
            str(item or "").strip()
            for item in value
            if str(item or "").strip()
        )
    )[:32]


def _task_context(payload: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    task_auth = (
        payload.get("taskAuthorization")
        if isinstance(payload.get("taskAuthorization"), dict)
        else {}
    )
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    route = (
        payload.get("toolRoute")
        if isinstance(payload.get("toolRoute"), dict)
        else state.get("toolRoute")
        if isinstance(state.get("toolRoute"), dict)
        else {}
    )
    task_session_id = str(
        task_auth.get("taskSessionId")
        or payload.get("taskSessionId")
        or state.get("taskSessionId")
        or existing.get("taskSessionId")
        or ""
    ).strip()
    raw_epoch = (
        payload.get("controlEpoch")
        or state.get("controlEpoch")
        or existing.get("epoch")
        or 0
    )
    try:
        epoch = max(0, int(raw_epoch))
    except (TypeError, ValueError):
        epoch = 0
    return {
        "taskSessionId": task_session_id,
        "route": route,
        "state": state,
        "epoch": epoch,
    }


def _required_tool(
    payload: dict[str, Any],
    existing: dict[str, Any],
) -> dict[str, Any] | None:
    raw = payload.get("requiredNextTool")
    if isinstance(raw, dict):
        name = str(raw.get("name") or raw.get("tool") or "").strip()
        args = raw.get("args") if isinstance(raw.get("args"), dict) else {}
    else:
        name = str(raw or "").strip()
        args = (
            payload.get("requiredNextToolArgs")
            if isinstance(payload.get("requiredNextToolArgs"), dict)
            else {}
        )
    if not name and payload.get("nextActionIsTool") is True:
        name = str(payload.get("nextAction") or "").strip()
        args = (
            payload.get("nextActionArgs")
            if isinstance(payload.get("nextActionArgs"), dict)
            else {}
        )
    if not name:
        existing_required = existing.get("requiredTool")
        if not isinstance(existing_required, dict):
            return None
        name = str(existing_required.get("name") or "").strip()
        args = (
            existing_required.get("args")
            if isinstance(existing_required.get("args"), dict)
            else {}
        )
        if not name:
            return None
    if ":" in name:
        name, action = name.split(":", 1)
        if action and "action" not in args:
            args = {**args, "action": action}
    return {"name": name[:160], "args": args}


def _task_disposition(
    payload: dict[str, Any],
    existing: dict[str, Any],
    required_tool: dict[str, Any] | None,
) -> str:
    error_code = str(payload.get("errorCode") or "").strip().upper()
    status = str(payload.get("status") or "").strip().casefold()
    phase = str(payload.get("phase") or "").strip().casefold()
    if error_code == "REPEATED_GATE_BLOCKER":
        return "rediscover"
    if status in {"completed", "complete"} or phase == "complete":
        return "complete"
    if payload.get("taskRouteTerminal") is True:
        return "complete" if status in {"completed", "complete"} else "workflow_stop"
    if payload.get("stopCurrentWorkflow") is True:
        return "workflow_stop"
    if status in {"pending_approval", "awaiting_approval", "await_user"}:
        return "await_user"
    if error_code in {
        "FEATURE_INTENT_BLOCKING_QUESTIONS",
        "FEATURE_FRONTIER_USER_CONTRACT_REQUIRED",
    }:
        return "await_user"
    if required_tool:
        return (
            "checkpoint"
            if required_tool["name"] == "unreal_task_checkpoint"
            else "require_tool"
        )
    if payload.get("ok") is False and payload.get("retryable") is False:
        return "workflow_stop"
    explicit = str(existing.get("disposition") or "").strip().casefold()
    if explicit in CONTROL_DISPOSITIONS:
        return explicit
    return "continue"


def _task_control_envelope(
    payload: dict[str, Any],
    existing: dict[str, Any],
) -> dict[str, Any]:
    context = _task_context(payload, existing)
    route = context["route"]
    required_tool = _required_tool(payload, existing)
    disposition = _task_disposition(payload, existing, required_tool)
    if disposition in {"rediscover", "workflow_stop", "complete", "await_user", "continue"}:
        required_tool = None

    allowed_tools = _clean_tool_names(route.get("activeTools"))
    if disposition == "rediscover":
        allowed_tools = [name for name in allowed_tools if name in _DISCOVERY_TOOL_NAMES]
    elif required_tool:
        # A required action is an exact projection. The wider route remains in
        # toolRoute for legacy clients, but the v2 consumer must see one schema.
        allowed_tools = [required_tool["name"]]
    elif disposition in {"workflow_stop", "complete", "await_user"}:
        allowed_tools = []

    retry_value = "allowed"
    if payload.get("doNotRetryUnchanged") is True or payload.get("retryable") is False:
        retry_value = "forbidden"
    elif payload.get("retryable") is True:
        retry_value = "once"
    existing_blocker = (
        existing.get("blocker")
        if isinstance(existing.get("blocker"), dict)
        else {}
    )
    error_code = str(payload.get("errorCode") or "").strip()
    has_blocker = bool(
        error_code
        or existing_blocker
        or disposition in {"rediscover", "workflow_stop"}
    )
    blocker_fingerprint = (
        _fingerprint(payload)
        if has_blocker
        else ""
    ) or str(existing_blocker.get("fingerprint") or "")
    blocker = (
        {"code": error_code or "SERVER_BLOCKED", "fingerprint": blocker_fingerprint}
        if has_blocker
        else None
    )
    control = {
        "version": CONTROL_VERSION,
        "epoch": context["epoch"],
        "taskSessionId": context["taskSessionId"],
        "routeHash": str(route.get("routeHash") or existing.get("routeHash") or ""),
        "phase": str(route.get("phase") or existing.get("phase") or payload.get("phase") or "unknown"),
        "disposition": disposition,
        "requiredTool": required_tool,
        "allowedTools": allowed_tools,
        "retryPolicy": {"sameSemanticInput": retry_value},
        "blocker": blocker,
    }
    return {
        key: value
        for key, value in control.items()
        if value not in (None, "")
    }


def attach_control_envelope(
    payload: dict[str, Any],
    *,
    tool_name: str = "",
    status: str = "",
) -> dict[str, Any]:
    result = dict(payload)
    existing = result.get("control") if isinstance(result.get("control"), dict) else {}
    task_context = _task_context(result, existing)
    if int(existing.get("version") or 0) >= CONTROL_VERSION or (
        task_context["taskSessionId"]
        and (
            task_context["route"]
            or task_context["state"]
            or "controlEpoch" in result
            or result.get("taskRouteTerminal") is True
        )
    ):
        result["control"] = _task_control_envelope(result, existing)
        return result
    architecture = (
        result.get("architectureState")
        if isinstance(result.get("architectureState"), dict)
        else {}
    )
    task_auth = (
        result.get("taskAuthorization")
        if isinstance(result.get("taskAuthorization"), dict)
        else {}
    )
    has_direct_action = any(
        key in result
        for key in ("nextAction", "requiredNextTool", "requiredNextAction")
    )
    next_action = _action_name(
        (
            result.get("nextAction")
            or result.get("requiredNextTool")
            or result.get("requiredNextAction")
            or ""
        )
        if has_direct_action
        else existing.get("nextAction") or ""
    )
    if "nextActionIsTool" in result:
        next_is_tool = bool(result.get("nextActionIsTool"))
    elif "requiredNextTool" in result:
        next_is_tool = bool(result.get("requiredNextTool"))
    elif has_direct_action:
        # A snake_case action is not proof that an MCP tool exists. Informational
        # handoffs such as ``read_project_source_or_answer`` deliberately look
        # imperative, and prefix inference used to turn them into impossible
        # exact-tool gates in the context compactor. Executable handoffs must be
        # declared explicitly through requiredNextTool or nextActionIsTool.
        next_is_tool = False
    else:
        next_is_tool = existing.get("nextActionIsTool") is True
    resolved_status = str(
        status or architecture.get("current") or existing.get("status") or ""
    )
    if not resolved_status:
        if result.get("ok") is False or result.get("writeGateClosed") is True:
            resolved_status = "Blocked"
        elif next_action:
            resolved_status = "NeedsAction"
        else:
            resolved_status = "Completed"
    if "doNotRetryUnchanged" in result or "retryable" in result:
        retry_policy = "none"
        if result.get("doNotRetryUnchanged") or result.get("retryable") is False:
            retry_policy = "forbidden"
        elif result.get("retryable") is True:
            retry_policy = "once"
    else:
        retry_policy = str(existing.get("retryPolicy") or "none")
    fingerprint = _fingerprint(result) or str(existing.get("blockerFingerprint") or "")
    control = {
        "version": 1,
        "taskId": str(
            task_auth.get("taskSessionId")
            or result.get("taskSessionId")
            or existing.get("taskId")
            or ""
        ),
        "phase": str(existing.get("phase") or tool_name or result.get("phase") or "Unknown"),
        "status": resolved_status,
        "nextAction": next_action,
        "nextActionIsTool": next_is_tool,
        "retryPolicy": retry_policy,
        "blockerFingerprint": fingerprint,
        "continuationToken": str(
            existing.get("continuationToken")
            or result.get("proposalRevision")
            or ""
        ),
    }
    result["control"] = {
        key: value for key, value in control.items() if value not in (None, "")
    }
    return result


def concise_control_text(payload: dict[str, Any]) -> str:
    """One small text projection; structuredContent remains authoritative."""

    control = payload.get("control") if isinstance(payload.get("control"), dict) else {}
    ok = payload.get("ok") is not False
    phase = str(control.get("phase") or payload.get("tool") or "tool")
    status = str(
        control.get("disposition")
        or control.get("status")
        or ("Completed" if ok else "Blocked")
    )
    error_code = str(payload.get("errorCode") or "")
    headline = f"{'OK' if ok else 'FAILED'} [{phase}] {status}"
    if error_code:
        headline += f" ({error_code})"
    summary = str(
        payload.get("verdictSummary")
        or payload.get("summary")
        or payload.get("userMessage")
        or payload.get("message")
        or payload.get("error")
        or ""
    ).strip()
    lines = [headline]
    if summary:
        lines.append(summary[:800])
    required = control.get("requiredTool") if isinstance(control.get("requiredTool"), dict) else {}
    next_action = str(required.get("name") or control.get("nextAction") or "")
    if next_action:
        lines.append(
            f"nextAction={next_action} (tool=true)"
        )
    lines.append("Detailed result is available in structuredContent.control and structuredContent data.")
    return "\n".join(lines)


def model_visible_control_text(
    payload: dict[str, Any],
    *,
    frontend: str | None = None,
    max_chars: int = 32_000,
) -> str:
    """Return a bounded model-facing projection compatible with the frontend.

    LM Studio 0.4.20 retains MCP ``content`` in the conversation sent back to
    the model but drops top-level ``structuredContent`` there. Keep the latter
    authoritative for capable clients while mirroring sanitized JSON into text
    only for LM Studio.
    """

    resolved_frontend = str(
        frontend if frontend is not None else os.environ.get("MCP_FRONTEND", "")
    ).strip().lower()
    if resolved_frontend != "lmstudio":
        return concise_control_text(payload)

    from mcp_tool_compact import compact_structured_payload

    budget = max(2_000, min(int(max_chars or 32_000), 32_000))
    compact = compact_structured_payload(payload, max_bytes=budget)
    rendered = json.dumps(compact, ensure_ascii=False, indent=2)
    if len(rendered) <= budget:
        return rendered

    fallback = {
        "ok": payload.get("ok"),
        "control": {
            key: (
                value[:500]
                if isinstance(value, str) and len(value) > 500
                else value
            )
            for key, value in (
                payload.get("control")
                if isinstance(payload.get("control"), dict)
                else {}
            ).items()
        },
        "errorCode": payload.get("errorCode"),
        "summary": payload.get("summary") or payload.get("message") or payload.get("error"),
        "retryable": payload.get("retryable"),
        "doNotRetry": payload.get("doNotRetry"),
        "doNotRetryTools": payload.get("doNotRetryTools"),
        "stopCurrentWorkflow": payload.get("stopCurrentWorkflow"),
        "stopCurrentPhase": payload.get("stopCurrentPhase"),
        "phaseBoundary": payload.get("phaseBoundary"),
        "agentInstruction": payload.get("agentInstruction"),
        "requiredNextTool": payload.get("requiredNextTool"),
        "requiredNextToolArgs": payload.get("requiredNextToolArgs"),
        "nextAction": payload.get("nextAction"),
        "nextActionArgs": payload.get("nextActionArgs"),
        "nextSteps": list(payload.get("nextSteps") or [])[:5],
        "suggestedToolCalls": list(payload.get("suggestedToolCalls") or [])[:3],
        "_textFallbackTruncated": True,
    }
    fallback_text = json.dumps(
        {key: value for key, value in fallback.items() if value not in (None, "", [])},
        ensure_ascii=False,
        indent=2,
    )
    if len(fallback_text) <= budget:
        return fallback_text

    control = fallback.get("control") if isinstance(fallback.get("control"), dict) else {}
    minimal_control = {
        key: (
            value[:200]
            if isinstance(value, str)
            else value
        )
        for key in (
            "version",
            "epoch",
            "taskSessionId",
            "routeHash",
            "taskId",
            "phase",
            "disposition",
            "requiredTool",
            "allowedTools",
            "status",
            "nextAction",
            "nextActionIsTool",
            "retryPolicy",
            "blockerFingerprint",
            "continuationToken",
        )
        if (value := control.get(key)) not in (None, "")
    }
    minimal = {
        "ok": payload.get("ok"),
        "errorCode": str(payload.get("errorCode") or "")[:200],
        "control": minimal_control,
        "retryable": payload.get("retryable"),
        "doNotRetry": payload.get("doNotRetry"),
        "doNotRetryTools": payload.get("doNotRetryTools"),
        "stopCurrentWorkflow": payload.get("stopCurrentWorkflow"),
        "stopCurrentPhase": payload.get("stopCurrentPhase"),
        "phaseBoundary": payload.get("phaseBoundary"),
        "agentInstruction": str(payload.get("agentInstruction") or "")[:600],
        "_textFallbackTruncated": True,
    }
    return json.dumps(
        {key: value for key, value in minimal.items() if value not in (None, "", {})},
        ensure_ascii=False,
        indent=2,
    )
