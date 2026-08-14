#!/usr/bin/env python
"""Pure failed-gate history transition helpers.

Task persistence and authorization remain owned by task_api.  This module owns
only the canonical semantic-blocker identity and its bounded retry transition,
so adding gate policy does not keep expanding the lifecycle facade.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from task_autonomy_supervisor import observe_autonomy


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_gate_input_hash(input_payload: dict[str, Any]) -> str:
    return _canonical_hash(input_payload)


def _scope_generation(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def failed_gate_attempt_for_current_scope(
    state: dict[str, Any],
    gate: str,
) -> dict[str, Any]:
    """Return a failed gate record only when all transition owners still match."""

    attempts = state.get("failedGateAttempts")
    attempts = attempts if isinstance(attempts, dict) else {}
    attempt = attempts.get(gate)
    attempt = attempt if isinstance(attempt, dict) else {}
    if not attempt:
        return {}
    scope_fields = {
        "gateSetHash",
        "planRevision",
        "activeSliceId",
        "mutationGeneration",
    }
    matches = bool(
        scope_fields.issubset(attempt)
        and
        str(attempt.get("gateSetHash") or "")
        == str(state.get("requiredGateSetHash") or "")
        and str(attempt.get("planRevision") or "")
        == str(state.get("planRevision") or "")
        and str(attempt.get("activeSliceId") or "")
        == str(state.get("activeSliceId") or "")
        and _scope_generation(attempt.get("mutationGeneration"))
        == _scope_generation(state.get("mutationGeneration"))
    )
    return attempt if matches else {}


def repeated_gate_input_preflight(
    state: dict[str, Any],
    *,
    gate: str,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    previous = failed_gate_attempt_for_current_scope(state, gate)
    exact_repeat = bool(
        int(previous.get("attemptCount") or 0) >= 2
        and str(previous.get("inputHash") or "")
        == canonical_gate_input_hash(input_payload)
    )
    return {
        "blocked": exact_repeat,
        "gate": gate,
        "attemptCount": int(previous.get("attemptCount") or 0),
        "blockerFingerprint": str(previous.get("fingerprint") or ""),
        "validationErrorCode": str(previous.get("validationErrorCode") or ""),
        "nextAction": str(previous.get("nextAction") or gate),
        "nextActionIsTool": previous.get("nextActionIsTool") is True,
        "nextActionArgs": (
            dict(previous.get("nextActionArgs") or {})
            if isinstance(previous.get("nextActionArgs"), dict)
            else {}
        ),
        "recoveryContract": (
            dict(previous.get("recoveryContract") or {})
            if isinstance(previous.get("recoveryContract"), dict)
            else {}
        ),
        "inputHash": canonical_gate_input_hash(input_payload),
    }


def completed_gate_input_preflight(
    state: dict[str, Any],
    *,
    gate: str,
    input_payload: dict[str, Any],
    current_target_snapshot_hash: str,
) -> dict[str, Any]:
    """Return an idempotent redirect only for the exact completed gate scope."""

    completed = state.get("completedGates")
    completed = completed if isinstance(completed, dict) else {}
    record = completed.get(gate)
    record = record if isinstance(record, dict) else {}
    matches = bool(
        record.get("status") == "completed"
        and str(record.get("gateSetHash") or "")
        == str(state.get("requiredGateSetHash") or "")
        and str(record.get("inputHash") or "")
        == canonical_gate_input_hash(input_payload)
        and str(record.get("planRevision") or "")
        == str(state.get("planRevision") or "")
        and str(record.get("activeSliceId") or "")
        == str(state.get("activeSliceId") or "")
        and int(record.get("mutationGeneration") or 0)
        == int(state.get("mutationGeneration") or 0)
        and bool(current_target_snapshot_hash)
        and str(record.get("targetSnapshotHash") or "")
        == str(current_target_snapshot_hash)
    )
    return {
        "alreadyCompleted": matches,
        "gate": gate,
        "inputHash": canonical_gate_input_hash(input_payload),
        "targetSnapshotHash": str(current_target_snapshot_hash or ""),
        "record": record if matches else {},
    }


def canonical_gate_blocker_identity(
    gate: str,
    evidence: dict[str, Any],
    input_payload: dict[str, Any] | None = None,
) -> dict[str, str]:
    first_blocker = (
        evidence.get("firstBlocker")
        if isinstance(evidence.get("firstBlocker"), dict)
        else {}
    )
    generation = (
        evidence.get("generationContract")
        if isinstance(evidence.get("generationContract"), dict)
        else {}
    )
    write_gate = (
        generation.get("writeGate")
        if isinstance(generation.get("writeGate"), dict)
        else evidence.get("writeGate")
        if isinstance(evidence.get("writeGate"), dict)
        else {}
    )
    issues = generation.get("issues") if isinstance(generation.get("issues"), list) else []
    material_delta = (
        generation.get("materialDelta")
        if isinstance(generation.get("materialDelta"), dict)
        else {}
    )
    payload = input_payload if isinstance(input_payload, dict) else {}
    validation_error_code = str(evidence.get("errorCode") or "")
    if validation_error_code == "FEATURE_FRONTIER_UNPROVEN":
        completion = (
            evidence.get("completionFrontier")
            if isinstance(evidence.get("completionFrontier"), dict)
            else {}
        )
        issues = [
            " ".join(str(item or "").split()).casefold()
            for item in (completion.get("issues") or [])
            if str(item or "").strip()
        ]
        # Feature-frontier retries may legitimately omit model-facing slice
        # fields once task ownership has supplied them.  Those shape changes do
        # not change the rejected frontier.  Bind equivalence to the server's
        # source ledger plus the normalized frontier and validation findings;
        # a materially different frontier or new source evidence still resets
        # the bounded retry count.
        return {
            "gate": str(gate or "").strip(),
            "validationErrorCode": validation_error_code,
            "nextAction": str(evidence.get("nextAction") or gate),
            "directSourceEvidenceFingerprint": str(
                payload.get("_serverDirectSourceEvidenceFingerprint") or ""
            ),
            "completionFrontierHash": str(
                payload.get("_serverCompletionFrontierHash") or ""
            ),
            "validationIssuesHash": _canonical_hash(issues),
        }
    targets = [
        str(item or "").replace("\\", "/").strip("/").casefold()
        for item in (payload.get("targetFiles") or [])
        if str(item or "").strip()
    ]
    semantic_delta = {
        "status": str(material_delta.get("status") or ""),
        "definitionDeltas": list(material_delta.get("definitionDeltas") or [])[:16],
        "novelCodeLines": list(material_delta.get("novelCodeLines") or [])[:8],
        "explicitDiff": bool(material_delta.get("explicitDiff")),
    }
    return {
        "gate": str(gate or "").strip(),
        "validationErrorCode": validation_error_code,
        "nextAction": str(evidence.get("nextAction") or gate),
        "blockerErrorCode": str(first_blocker.get("errorCode") or ""),
        "symbol": str(first_blocker.get("symbol") or ""),
        "receiverType": str(first_blocker.get("receiverType") or ""),
        "verdict": str(first_blocker.get("verdict") or ""),
        "coverageStatus": str(first_blocker.get("coverageStatus") or ""),
        "writeGateReason": " ".join(str(write_gate.get("reason") or "").split()).casefold(),
        "firstContractIssue": " ".join(str(issues[0] if issues else "").split()).casefold(),
        "targetFilesHash": _canonical_hash(sorted(dict.fromkeys(targets))),
        "semanticDeltaHash": _canonical_hash(semantic_delta),
    }


def apply_failed_gate_attempt(
    state: dict[str, Any],
    *,
    gate: str,
    input_payload: dict[str, Any],
    evidence: dict[str, Any],
    updated_at: str,
) -> dict[str, Any]:
    """Mutate one authenticated task state with a failed semantic gate."""

    blocker = canonical_gate_blocker_identity(gate, evidence, input_payload)
    fingerprint = _canonical_hash(blocker)
    attempts = (
        dict(state.get("failedGateAttempts") or {})
        if isinstance(state.get("failedGateAttempts"), dict)
        else {}
    )
    previous = failed_gate_attempt_for_current_scope(state, gate)
    attempt_count = (
        int(previous.get("attemptCount") or 0) + 1
        if str(previous.get("fingerprint") or "") == fingerprint
        else 1
    )
    repeated = attempt_count >= 2
    attempts[gate] = {
        "fingerprint": fingerprint,
        "attemptCount": attempt_count,
        "validationErrorCode": blocker["validationErrorCode"],
        "nextAction": blocker["nextAction"],
        "nextActionIsTool": evidence.get("nextActionIsTool") is True,
        "nextActionArgs": (
            dict(evidence.get("nextActionArgs") or {})
            if isinstance(evidence.get("nextActionArgs"), dict)
            else {}
        ),
        "recoveryContract": (
            dict(evidence.get("featureFrontierRecovery") or {})
            if isinstance(evidence.get("featureFrontierRecovery"), dict)
            else {}
        ),
        "inputHash": canonical_gate_input_hash(input_payload),
        "evidenceHash": _canonical_hash(evidence),
        "gateSetHash": str(state.get("requiredGateSetHash") or ""),
        "planRevision": str(state.get("planRevision") or ""),
        "activeSliceId": str(state.get("activeSliceId") or ""),
        "mutationGeneration": _scope_generation(state.get("mutationGeneration")),
        "updatedAt": updated_at,
    }
    state["failedGateAttempts"] = attempts
    state["autonomySupervisor"] = observe_autonomy(
        state.get("autonomySupervisor"),
        state,
        action=f"gate:{gate}:failed",
        error=f"{gate}:{blocker['validationErrorCode']}:{fingerprint}",
        count_retry=True,
    )
    state["updatedAt"] = updated_at
    return {
        "ok": False,
        "gate": gate,
        "errorCode": (
            "REPEATED_GATE_BLOCKER" if repeated else "GATE_VALIDATION_FAILED"
        ),
        "validationErrorCode": blocker["validationErrorCode"],
        "blockerFingerprint": fingerprint,
        "equivalentAttemptCount": attempt_count,
        "repeatedBlocker": repeated,
        "retryable": not repeated,
        "autonomySupervisor": {
            "status": str(
                (state.get("autonomySupervisor") or {}).get("status") or "active"
            ),
            "retryState": dict(
                (state.get("autonomySupervisor") or {}).get("retryState") or {}
            ),
            "blockers": list(
                (state.get("autonomySupervisor") or {}).get("blockers") or []
            ),
        },
    }
