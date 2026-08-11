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


def repeated_gate_input_preflight(
    state: dict[str, Any],
    *,
    gate: str,
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    attempts = state.get("failedGateAttempts")
    attempts = attempts if isinstance(attempts, dict) else {}
    previous = attempts.get(gate)
    previous = previous if isinstance(previous, dict) else {}
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
        "inputHash": canonical_gate_input_hash(input_payload),
    }


def canonical_gate_blocker_identity(
    gate: str,
    evidence: dict[str, Any],
) -> dict[str, str]:
    first_blocker = (
        evidence.get("firstBlocker")
        if isinstance(evidence.get("firstBlocker"), dict)
        else {}
    )
    return {
        "gate": str(gate or "").strip(),
        "validationErrorCode": str(evidence.get("errorCode") or ""),
        "nextAction": str(evidence.get("nextAction") or gate),
        "blockerErrorCode": str(first_blocker.get("errorCode") or ""),
        "symbol": str(first_blocker.get("symbol") or ""),
        "receiverType": str(first_blocker.get("receiverType") or ""),
        "verdict": str(first_blocker.get("verdict") or ""),
        "coverageStatus": str(first_blocker.get("coverageStatus") or ""),
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

    blocker = canonical_gate_blocker_identity(gate, evidence)
    fingerprint = _canonical_hash(blocker)
    attempts = (
        dict(state.get("failedGateAttempts") or {})
        if isinstance(state.get("failedGateAttempts"), dict)
        else {}
    )
    previous = attempts.get(gate) if isinstance(attempts.get(gate), dict) else {}
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
        "inputHash": canonical_gate_input_hash(input_payload),
        "evidenceHash": _canonical_hash(evidence),
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
