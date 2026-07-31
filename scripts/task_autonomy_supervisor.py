#!/usr/bin/env python
"""Pure supervision helpers for long-running autonomous task progress."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

DEFAULT_SAME_ACTION_RETRY_LIMIT = 3
DEFAULT_SAME_ERROR_RETRY_LIMIT = 3
DEFAULT_TOTAL_NO_PROGRESS_LIMIT = 12
MAX_SUPERVISOR_HISTORY = 256


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _bounded_int(value: Any, default: int, *, maximum: int = 100) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(maximum, parsed))


def _retry_budgets(config: dict[str, Any] | None = None) -> dict[str, int]:
    payload = config if isinstance(config, dict) else {}
    return {
        "sameActionNoProgress": _bounded_int(
            payload.get("sameActionNoProgress"),
            DEFAULT_SAME_ACTION_RETRY_LIMIT,
        ),
        "sameErrorNoProgress": _bounded_int(
            payload.get("sameErrorNoProgress"),
            DEFAULT_SAME_ERROR_RETRY_LIMIT,
        ),
        "totalNoProgress": _bounded_int(
            payload.get("totalNoProgress"),
            DEFAULT_TOTAL_NO_PROGRESS_LIMIT,
            maximum=1000,
        ),
    }


def _validation_artifact_hashes(value: Any) -> list[dict[str, str]]:
    if not value:
        return []
    if isinstance(value, dict):
        return [
            {"name": str(key), "hash": _canonical_hash(item)}
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        ]
    if isinstance(value, list):
        return [
            {"name": str(index), "hash": _canonical_hash(item)}
            for index, item in enumerate(value)
        ]
    return [{"name": "value", "hash": _canonical_hash(value)}]


def _checkpoint_state_hash(checkpoint: dict[str, Any]) -> str:
    supplied = str(checkpoint.get("checkpointStateHash") or "").strip()
    if supplied:
        return supplied
    stable = {
        key: value
        for key, value in checkpoint.items()
        if key
        not in {
            "checkpointHash",
            "checkpointStateHash",
            "note",
            "recordedAt",
            "sequence",
            "validation",
        }
    }
    return _canonical_hash(stable)


def _target_hash(checkpoint: dict[str, Any]) -> str:
    supplied = str(checkpoint.get("targetHash") or "").strip()
    if supplied:
        return supplied
    snapshots = [
        {
            "relativePath": str(item.get("relativePath") or ""),
            "exists": bool(item.get("exists")),
            "fileHash": str(item.get("fileHash") or ""),
        }
        for item in (checkpoint.get("fileSnapshots") or [])
        if isinstance(item, dict)
    ]
    return _canonical_hash(snapshots) if snapshots else ""


def progress_observation(state: dict[str, Any]) -> dict[str, Any]:
    continuity = (
        state.get("continuity")
        if isinstance(state.get("continuity"), dict)
        else {}
    )
    checkpoint = (
        continuity.get("checkpoint")
        if isinstance(continuity.get("checkpoint"), dict)
        else {}
    )
    completed_gates = sorted(
        str(name)
        for name, record in dict(state.get("completedGates") or {}).items()
        if isinstance(record, dict) and record.get("status") == "completed"
    )
    validation_artifacts = _validation_artifact_hashes(checkpoint.get("validation"))
    substantive = {
        "activeSliceId": str(
            checkpoint.get("activeSliceId") or state.get("activeSliceId") or ""
        ),
        "completedSlices": sorted(
            str(item) for item in (checkpoint.get("completedSlices") or [])
        ),
        "completedGates": completed_gates,
        "validationArtifacts": validation_artifacts,
        "targetHash": _target_hash(checkpoint),
        "checkpointStateHash": _checkpoint_state_hash(checkpoint) if checkpoint else "",
    }
    sequence = int(checkpoint.get("sequence") or 0)
    return {
        **substantive,
        "checkpointSequence": sequence,
        "substantiveFingerprint": _canonical_hash(substantive),
        "progressFingerprint": _canonical_hash(
            {**substantive, "checkpointSequence": sequence}
        ),
    }


def _error_signature(error: str) -> tuple[str, str]:
    normalized = re.sub(r"\s+", " ", str(error or "").strip()).casefold()
    if not normalized:
        return "", ""
    return _canonical_hash(normalized), normalized[:500]


def initialize_autonomy_supervisor(
    state: dict[str, Any],
    *,
    retry_budgets: dict[str, Any] | None = None,
) -> dict[str, Any]:
    observation = progress_observation(state)
    return {
        "version": 1,
        "status": "active",
        "strategyEpoch": 1,
        "retryBudgets": _retry_budgets(retry_budgets),
        "retryState": {
            "sameActionNoProgress": 0,
            "sameErrorNoProgress": 0,
            "totalNoProgress": 0,
        },
        "lastAction": "task_start",
        "lastErrorHash": "",
        "lastError": "",
        "lastObservation": observation,
        "validation": {
            "status": "not_recorded",
            "artifacts": [],
            "targetHash": "",
            "checkpointStateHash": "",
            "invalidatedAt": "",
            "invalidationReason": "",
        },
        "blockers": [],
        "nextAction": "",
        "history": [
            {
                "recordedAt": _utc_now(),
                "strategyEpoch": 1,
                "action": "task_start",
                "progressed": True,
                **observation,
            }
        ],
    }


def _validation_state(
    prior: dict[str, Any],
    observation: dict[str, Any],
) -> dict[str, Any]:
    artifacts = list(observation.get("validationArtifacts") or [])
    target_hash = str(observation.get("targetHash") or "")
    checkpoint_hash = str(observation.get("checkpointStateHash") or "")
    if artifacts:
        return {
            "status": "current",
            "artifacts": artifacts,
            "targetHash": target_hash,
            "checkpointStateHash": checkpoint_hash,
            "invalidatedAt": "",
            "invalidationReason": "",
        }
    if str(prior.get("status") or "") != "current":
        return dict(prior or {})
    reason = ""
    if str(prior.get("targetHash") or "") != target_hash:
        reason = "target_hash_changed"
    elif str(prior.get("checkpointStateHash") or "") != checkpoint_hash:
        reason = "checkpoint_hash_changed"
    if not reason:
        return dict(prior)
    return {
        "status": "invalidated",
        "artifacts": [],
        "targetHash": target_hash,
        "checkpointStateHash": checkpoint_hash,
        "invalidatedAt": _utc_now(),
        "invalidationReason": reason,
    }


def observe_autonomy(
    supervisor: dict[str, Any] | None,
    state: dict[str, Any],
    *,
    action: str,
    error: str = "",
    count_retry: bool = True,
) -> dict[str, Any]:
    newly_initialized = not isinstance(supervisor, dict) or not supervisor
    if newly_initialized:
        supervisor = initialize_autonomy_supervisor(state)
    updated = dict(supervisor)
    observation = progress_observation(state)
    prior_observation = (
        updated.get("lastObservation")
        if isinstance(updated.get("lastObservation"), dict)
        else {}
    )
    progressed = newly_initialized or (
        str(prior_observation.get("substantiveFingerprint") or "")
        != str(observation.get("substantiveFingerprint") or "")
    )
    normalized_action = re.sub(r"\s+", " ", str(action or "").strip())[:500]
    error_hash, normalized_error = _error_signature(error)
    retry_state = dict(updated.get("retryState") or {})
    same_action = (
        bool(normalized_action)
        and normalized_action == str(updated.get("lastAction") or "")
        and not progressed
    )
    same_error = (
        bool(error_hash)
        and error_hash == str(updated.get("lastErrorHash") or "")
        and not progressed
    )
    if progressed:
        retry_state = {
            "sameActionNoProgress": 0,
            "sameErrorNoProgress": 0,
            "totalNoProgress": 0,
        }
    elif count_retry and (normalized_action or error_hash):
        retry_state["sameActionNoProgress"] = (
            int(retry_state.get("sameActionNoProgress") or 0) + 1
            if same_action
            else 0
        )
        retry_state["sameErrorNoProgress"] = (
            int(retry_state.get("sameErrorNoProgress") or 0) + 1
            if same_error
            else 0
        )
        retry_state["totalNoProgress"] = (
            int(retry_state.get("totalNoProgress") or 0) + 1
        )

    budgets = _retry_budgets(updated.get("retryBudgets"))
    blockers: list[dict[str, Any]] = []
    if int(retry_state.get("sameActionNoProgress") or 0) >= budgets[
        "sameActionNoProgress"
    ]:
        blockers.append(
            {
                "code": "repeated_action_no_progress",
                "message": "The same action repeated without substantive progress.",
                "count": int(retry_state.get("sameActionNoProgress") or 0),
                "limit": budgets["sameActionNoProgress"],
            }
        )
    if int(retry_state.get("sameErrorNoProgress") or 0) >= budgets[
        "sameErrorNoProgress"
    ]:
        blockers.append(
            {
                "code": "repeated_error_no_progress",
                "message": "The same error repeated without substantive progress.",
                "count": int(retry_state.get("sameErrorNoProgress") or 0),
                "limit": budgets["sameErrorNoProgress"],
            }
        )
    if int(retry_state.get("totalNoProgress") or 0) >= budgets["totalNoProgress"]:
        blockers.append(
            {
                "code": "retry_budget_exhausted",
                "message": "The no-progress retry budget is exhausted.",
                "count": int(retry_state.get("totalNoProgress") or 0),
                "limit": budgets["totalNoProgress"],
            }
        )

    validation = _validation_state(
        dict(updated.get("validation") or {}),
        observation,
    )
    history = [
        dict(item)
        for item in (updated.get("history") or [])
        if isinstance(item, dict)
    ]
    history.append(
        {
            "recordedAt": _utc_now(),
            "strategyEpoch": int(updated.get("strategyEpoch") or 1),
            "action": normalized_action,
            "errorHash": error_hash,
            "progressed": progressed,
            **observation,
        }
    )
    if len(history) > MAX_SUPERVISOR_HISTORY:
        history = history[-MAX_SUPERVISOR_HISTORY:]
        updated["historyOverflowCount"] = int(
            updated.get("historyOverflowCount") or 0
        ) + 1

    updated.update(
        {
            "status": "blocked" if blockers else "active",
            "retryBudgets": budgets,
            "retryState": retry_state,
            "lastAction": normalized_action,
            "lastErrorHash": error_hash,
            "lastError": normalized_error,
            "lastObservation": observation,
            "validation": validation,
            "blockers": blockers,
            "nextAction": "replan_autonomous_strategy" if blockers else "",
            "history": history,
        }
    )
    return updated


def advance_strategy_epoch(
    supervisor: dict[str, Any] | None,
    state: dict[str, Any],
    *,
    reason: str,
) -> dict[str, Any]:
    current = (
        dict(supervisor)
        if isinstance(supervisor, dict) and supervisor
        else initialize_autonomy_supervisor(state)
    )
    current["strategyEpoch"] = max(1, int(current.get("strategyEpoch") or 1)) + 1
    current["status"] = "active"
    current["retryState"] = {
        "sameActionNoProgress": 0,
        "sameErrorNoProgress": 0,
        "totalNoProgress": 0,
    }
    current["lastAction"] = "strategy_rebase"
    current["lastErrorHash"] = ""
    current["lastError"] = ""
    current["blockers"] = []
    current["nextAction"] = ""
    current["validation"] = {
        "status": "invalidated",
        "artifacts": [],
        "targetHash": str(
            progress_observation(state).get("targetHash") or ""
        ),
        "checkpointStateHash": str(
            progress_observation(state).get("checkpointStateHash") or ""
        ),
        "invalidatedAt": _utc_now(),
        "invalidationReason": str(reason or "strategy_rebase")[:200],
    }
    history = [
        dict(item)
        for item in (current.get("history") or [])
        if isinstance(item, dict)
    ]
    history.append(
        {
            "recordedAt": _utc_now(),
            "strategyEpoch": current["strategyEpoch"],
            "action": "strategy_rebase",
            "reason": str(reason or "")[:500],
            "progressed": True,
            **progress_observation(state),
        }
    )
    current["history"] = history[-MAX_SUPERVISOR_HISTORY:]
    current["lastObservation"] = progress_observation(state)
    return current


def autonomy_blockers(supervisor: dict[str, Any] | None) -> list[dict[str, Any]]:
    payload = supervisor if isinstance(supervisor, dict) else {}
    return [
        dict(item)
        for item in (payload.get("blockers") or [])
        if isinstance(item, dict)
    ]


def invalidate_supervisor_validation(
    supervisor: dict[str, Any] | None,
    *,
    reason: str,
) -> dict[str, Any]:
    if not isinstance(supervisor, dict):
        return {}
    updated = dict(supervisor)
    prior = (
        dict(updated.get("validation") or {})
        if isinstance(updated.get("validation"), dict)
        else {}
    )
    updated["validation"] = {
        **prior,
        "status": "invalidated",
        "artifacts": [],
        "invalidatedAt": _utc_now(),
        "invalidationReason": str(reason or "checkpoint_changed")[:200],
    }
    return updated
