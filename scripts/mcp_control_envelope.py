"""Stable MCP control envelope consumed by UI and context compaction."""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any


def _action_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("tool") or "")
    return str(value or "")


def _looks_like_tool_action(value: Any) -> bool:
    return bool(
        re.fullmatch(
            r"(?:unreal_|get_|set_|open_|read_|write_|replace_|apply_|delete_|build_|run_|search_|list_|detect_|record_|cancel_|quarantine_|static_|refactor_|propose_)"
            r"[a-z0-9_]*(?::[a-z0-9_-]+)?",
            _action_name(value).strip(),
            flags=re.IGNORECASE,
        )
    )


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


def attach_control_envelope(
    payload: dict[str, Any],
    *,
    tool_name: str = "",
    status: str = "",
) -> dict[str, Any]:
    result = dict(payload)
    existing = result.get("control") if isinstance(result.get("control"), dict) else {}
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
        next_is_tool = _looks_like_tool_action(next_action)
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
    status = str(control.get("status") or ("Completed" if ok else "Blocked"))
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
    next_action = str(control.get("nextAction") or "")
    if next_action:
        lines.append(
            f"nextAction={next_action} (tool={str(bool(control.get('nextActionIsTool'))).lower()})"
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
            "taskId",
            "phase",
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
