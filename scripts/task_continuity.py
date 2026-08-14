#!/usr/bin/env python
"""Pure state helpers for long-running task leases and checkpoints."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

DEFAULT_LEASE_SECONDS = 1800
MIN_LEASE_SECONDS = 60
MAX_LEASE_SECONDS = 86400
MAX_CHECKPOINT_HISTORY = 256
MAX_CHECKPOINT_FILES = 4096
MAX_CHECKPOINT_SLICES = 1024


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def parse_utc(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def clamp_lease_seconds(value: Any) -> int:
    try:
        seconds = int(value or DEFAULT_LEASE_SECONDS)
    except (TypeError, ValueError):
        seconds = DEFAULT_LEASE_SECONDS
    return max(MIN_LEASE_SECONDS, min(MAX_LEASE_SECONDS, seconds))


def canonical_hash(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def initialize_continuity(
    *,
    task_session_id: str,
    plan_id: str,
    plan_revision: str,
    active_slice_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or utc_now()
    ttl = clamp_lease_seconds(lease_seconds)
    identity = {
        "taskSessionId": task_session_id,
        "planId": plan_id,
        "planRevision": plan_revision,
        "activeSliceId": active_slice_id,
    }
    return {
        "version": 1,
        "planIdentity": identity,
        "planIdentityHash": canonical_hash(identity),
        "lease": {
            "epoch": 1,
            "status": "active",
            "ttlSeconds": ttl,
            "acquiredAt": iso_utc(current),
            "heartbeatAt": iso_utc(current),
            "expiresAt": iso_utc(current + timedelta(seconds=ttl)),
            "renewalReason": "task_start",
        },
        "checkpoint": {
            "sequence": 0,
            "status": "not_recorded",
            "activeSliceId": active_slice_id,
            "modifiedFiles": [],
            "fileSnapshots": [],
            "gitChangedFiles": [],
            "gitDiscoveryEnabled": True,
            "discoveryWarnings": [],
            "requiredNextAction": "",
            "recordedAt": "",
            "targetHash": "",
            "checkpointStateHash": "",
            "checkpointHash": "",
        },
        "checkpointHistory": [],
        "checkpointHistoryOverflowCount": 0,
        "recovery": {
            "status": "not_required",
            "conflicts": [],
            "recoveredAt": "",
        },
    }


def lease_health(
    continuity: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    payload = continuity if isinstance(continuity, dict) else {}
    lease = payload.get("lease") if isinstance(payload.get("lease"), dict) else {}
    current = now or utc_now()
    expiry = parse_utc(lease.get("expiresAt"))
    if not lease:
        return {
            "configured": False,
            "active": True,
            "expired": False,
            "reason": "legacy_task_without_lease",
        }
    if expiry is None:
        return {
            "configured": True,
            "active": False,
            "expired": True,
            "reason": "invalid_expiry",
            "epoch": int(lease.get("epoch") or 0),
        }
    expired = expiry <= current
    return {
        "configured": True,
        "active": not expired and str(lease.get("status") or "") == "active",
        "expired": expired,
        "reason": "expired" if expired else str(lease.get("status") or "active"),
        "epoch": int(lease.get("epoch") or 0),
        "heartbeatAt": str(lease.get("heartbeatAt") or ""),
        "expiresAt": iso_utc(expiry),
        "remainingSeconds": max(0, int((expiry - current).total_seconds())),
    }


def renew_lease(
    continuity: dict[str, Any],
    *,
    reason: str,
    lease_seconds: int | None = None,
    advance_epoch: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    updated = dict(continuity or {})
    prior = (
        dict(updated.get("lease") or {})
        if isinstance(updated.get("lease"), dict)
        else {}
    )
    current = now or utc_now()
    ttl = clamp_lease_seconds(
        lease_seconds if lease_seconds is not None else prior.get("ttlSeconds")
    )
    epoch = max(1, int(prior.get("epoch") or 1))
    if advance_epoch:
        epoch += 1
    updated["lease"] = {
        **prior,
        "epoch": epoch,
        "status": "active",
        "ttlSeconds": ttl,
        "heartbeatAt": iso_utc(current),
        "expiresAt": iso_utc(current + timedelta(seconds=ttl)),
        "renewalReason": str(reason or "heartbeat")[:120],
    }
    if not updated["lease"].get("acquiredAt"):
        updated["lease"]["acquiredAt"] = iso_utc(current)
    return updated


def record_checkpoint(
    continuity: dict[str, Any],
    *,
    phase: str,
    active_slice_id: str,
    completed_slices: list[str],
    pending_slices: list[str],
    modified_files: list[str],
    file_snapshots: list[dict[str, Any]],
    required_next_action: str,
    git_changed_files: list[str] | None = None,
    git_discovery_enabled: bool = True,
    discovery_warnings: list[str] | None = None,
    validation: dict[str, Any] | None = None,
    mutation_generation: int = 0,
    note: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    updated = dict(continuity or {})
    prior = (
        dict(updated.get("checkpoint") or {})
        if isinstance(updated.get("checkpoint"), dict)
        else {}
    )
    current = now or utc_now()
    unique_completed = list(
        dict.fromkeys(
            [
                *(
                    str(item)
                    for item in (prior.get("completedSlices") or [])
                    if str(item).strip()
                ),
                *(str(item) for item in completed_slices if str(item).strip()),
            ]
        )
    )
    unique_pending = list(
        dict.fromkeys(str(item) for item in pending_slices if str(item).strip())
    )
    unique_modified = list(
        dict.fromkeys(str(item) for item in modified_files if str(item).strip())
    )
    unique_git_changed = list(
        dict.fromkeys(
            str(item) for item in (git_changed_files or []) if str(item).strip()
        )
    )
    if len(unique_completed) > MAX_CHECKPOINT_SLICES:
        raise ValueError(
            "completed slice set exceeds checkpoint limit "
            f"({len(unique_completed)} > {MAX_CHECKPOINT_SLICES})"
        )
    if len(unique_pending) > MAX_CHECKPOINT_SLICES:
        raise ValueError(
            "pending slice set exceeds checkpoint limit "
            f"({len(unique_pending)} > {MAX_CHECKPOINT_SLICES})"
        )
    if len(unique_modified) > MAX_CHECKPOINT_FILES:
        raise ValueError(
            "modified file set exceeds checkpoint limit "
            f"({len(unique_modified)} > {MAX_CHECKPOINT_FILES})"
        )
    if len(file_snapshots) != len(unique_modified):
        raise ValueError(
            "checkpoint snapshots must cover the complete modified file set "
            f"({len(file_snapshots)} != {len(unique_modified)})"
        )
    if len(unique_git_changed) > MAX_CHECKPOINT_FILES:
        raise ValueError(
            "Git changed file set exceeds checkpoint limit "
            f"({len(unique_git_changed)} > {MAX_CHECKPOINT_FILES})"
        )
    normalized_snapshots = [
        dict(item) for item in file_snapshots if isinstance(item, dict)
    ]
    normalized_snapshots.sort(key=lambda item: str(item.get("relativePath") or ""))
    checkpoint = {
        "sequence": int(prior.get("sequence") or 0) + 1,
        "status": "recorded",
        "phase": str(phase or "working").strip(),
        "activeSliceId": str(active_slice_id or "").strip(),
        "completedSlices": unique_completed,
        "pendingSlices": unique_pending,
        "modifiedFiles": unique_modified,
        "fileSnapshots": normalized_snapshots,
        "gitChangedFiles": unique_git_changed,
        "gitDiscoveryEnabled": bool(git_discovery_enabled),
        "discoveryWarnings": list(
            dict.fromkeys(
                str(item)
                for item in (discovery_warnings or [])
                if str(item).strip()
            )
        ),
        "requiredNextAction": str(required_next_action or "").strip(),
        "validation": dict(validation or {}),
        "mutationGeneration": max(0, int(mutation_generation or 0)),
        "note": str(note or "")[:1000],
        "recordedAt": iso_utc(current),
    }
    checkpoint["targetHash"] = canonical_hash(normalized_snapshots)
    checkpoint["checkpointStateHash"] = canonical_hash(
        {
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
    )
    checkpoint["checkpointHash"] = canonical_hash(checkpoint)
    history = [
        dict(item)
        for item in (updated.get("checkpointHistory") or [])
        if isinstance(item, dict)
    ]
    if prior.get("sequence"):
        history.append(prior)
    overflow = max(0, len(history) - MAX_CHECKPOINT_HISTORY)
    updated["checkpoint"] = checkpoint
    updated["checkpointHistory"] = history[-MAX_CHECKPOINT_HISTORY:]
    if overflow:
        updated["checkpointHistoryOverflowCount"] = int(
            updated.get("checkpointHistoryOverflowCount") or 0
        ) + overflow
    updated["recovery"] = {
        "status": "checkpoint_current",
        "conflicts": [],
        "recoveredAt": "",
    }
    return renew_lease(updated, reason="checkpoint", now=current)


def mark_recovery(
    continuity: dict[str, Any],
    *,
    conflicts: list[dict[str, Any]],
    accepted_current_files: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    updated = dict(continuity or {})
    current = now or utc_now()
    if conflicts and not accepted_current_files:
        updated["recovery"] = {
            "status": "blocked_by_checkpoint_conflict",
            "conflicts": conflicts,
            "recoveredAt": "",
        }
        return updated
    updated["recovery"] = {
        "status": "recovered_with_rebase" if conflicts else "recovered",
        "conflicts": [],
        "recoveredAt": iso_utc(current),
    }
    return renew_lease(
        updated,
        reason="recovery_rebase" if conflicts else "recovery",
        advance_epoch=True,
        now=current,
    )


def recovery_conflicts(continuity: dict[str, Any] | None) -> list[dict[str, Any]]:
    payload = continuity if isinstance(continuity, dict) else {}
    recovery = (
        payload.get("recovery")
        if isinstance(payload.get("recovery"), dict)
        else {}
    )
    return [
        dict(item)
        for item in (recovery.get("conflicts") or [])
        if isinstance(item, dict)
    ]
