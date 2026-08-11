"""Stable MCP control envelope consumed by UI and context compaction."""

from __future__ import annotations

import hashlib
import json
from typing import Any


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
    next_action = _action_name(
        result.get("nextAction")
        or result.get("requiredNextTool")
        or result.get("requiredNextAction")
        or ""
    )
    if "nextActionIsTool" in result:
        next_is_tool = bool(result.get("nextActionIsTool"))
    else:
        next_is_tool = bool(result.get("requiredNextTool"))
    resolved_status = str(status or architecture.get("current") or "")
    if not resolved_status:
        if result.get("ok") is False or result.get("writeGateClosed") is True:
            resolved_status = "Blocked"
        elif next_action:
            resolved_status = "NeedsAction"
        else:
            resolved_status = "Completed"
    retry_policy = "none"
    if result.get("doNotRetryUnchanged") or result.get("retryable") is False:
        retry_policy = "forbidden"
    elif result.get("retryable") is True:
        retry_policy = "once"
    control = {
        "version": 1,
        "taskId": str(task_auth.get("taskSessionId") or result.get("taskSessionId") or ""),
        "phase": str(existing.get("phase") or tool_name or result.get("phase") or "Unknown"),
        "status": resolved_status,
        "nextAction": next_action,
        "nextActionIsTool": next_is_tool,
        "retryPolicy": retry_policy,
        "blockerFingerprint": _fingerprint(result),
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
