"""Sanitize server-owned task state before it crosses the MCP boundary."""

from __future__ import annotations

from typing import Any


_PUBLIC_TASK_AUTHORIZATION_FIELDS = ("taskSessionId", "ownerCapability")


def compact_task_authorization(value: Any) -> Any:
    """Return the stable model-facing ownership handle for an auth object."""

    if not isinstance(value, dict):
        return value
    return {
        field: value[field]
        for field in _PUBLIC_TASK_AUTHORIZATION_FIELDS
        if str(value.get(field) or "").strip()
    }


def sanitize_model_payload(value: Any) -> Any:
    """Recursively remove rotating authorization fields from public payloads.

    Internal task APIs keep the complete route authorization.  Only dictionaries
    stored under the protocol key ``taskAuthorization`` are compacted, so plan
    revisions and route diagnostics outside the secret ownership object retain
    their normal meaning.
    """

    if isinstance(value, list):
        return [sanitize_model_payload(item) for item in value]
    if not isinstance(value, dict):
        return value
    sanitized: dict[str, Any] = {}
    for key, item in value.items():
        if key == "expiryTransition":
            continue
        if key == "taskAuthorization":
            sanitized[key] = compact_task_authorization(item)
        else:
            sanitized[key] = sanitize_model_payload(item)
    return sanitized
