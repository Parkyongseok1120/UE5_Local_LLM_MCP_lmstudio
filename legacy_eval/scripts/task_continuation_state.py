#!/usr/bin/env python
"""Pure active-task continuation state transition."""

from __future__ import annotations

from typing import Any

from task_autonomy_supervisor import observe_autonomy
from task_continuity import renew_lease


def apply_user_continuation(
    state: dict[str, Any],
    *,
    updated_at: str,
) -> dict[str, Any] | None:
    """Preserve task intent, route, gates, and authorization epoch in place."""

    if str(state.get("status") or "") != "running":
        return {
            "ok": False,
            "errorCode": "TASK_CONTINUATION_NOT_RUNNING",
            "error": "Only a running task can consume a continuation request.",
            "taskSessionId": str(state.get("taskSessionId") or ""),
        }
    state["continuity"] = renew_lease(
        dict(state.get("continuity") or {}),
        reason="user_continuation",
        advance_epoch=False,
    )
    state["autonomySupervisor"] = observe_autonomy(
        state.get("autonomySupervisor"),
        state,
        action="user_continuation",
        count_retry=False,
    )
    state["lastContinuationAt"] = updated_at
    state["updatedAt"] = updated_at
    return None
