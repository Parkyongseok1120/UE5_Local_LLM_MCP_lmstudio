"""Server-owned architecture finite-state machine and durable session state."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from atomic_io import atomic_write_text
from state_root import resolve_agent_state_root


ARCHITECTURE_STATES = frozenset(
    {
        "Discovery",
        "InitialProposal",
        "FullReplan",
        "EvidenceRefill",
        "ExactRepair",
        "Revalidation",
        "Validated",
        "FailedClosed",
    }
)

_TRANSITIONS: dict[str, dict[str, str]] = {
    "Discovery": {
        "EVIDENCE_READY": "InitialProposal",
        "EVIDENCE_MISSING": "EvidenceRefill",
        "PROPOSAL_SUBMITTED": "Revalidation",
        "FAIL_CLOSED": "FailedClosed",
    },
    "InitialProposal": {
        "PROPOSAL_SUBMITTED": "Revalidation",
        "EVIDENCE_MISSING": "EvidenceRefill",
        "FULL_REPLAN_REQUIRED": "FullReplan",
        "EXACT_REPAIR_REQUIRED": "ExactRepair",
        "VALIDATION_PASSED": "Validated",
        "FAIL_CLOSED": "FailedClosed",
    },
    "FullReplan": {
        "PROPOSAL_SUBMITTED": "Revalidation",
        "EVIDENCE_MISSING": "EvidenceRefill",
        "FULL_REPLAN_REQUIRED": "FullReplan",
        "FAIL_CLOSED": "FailedClosed",
    },
    "EvidenceRefill": {
        "EVIDENCE_READY": "InitialProposal",
        "PROPOSAL_SUBMITTED": "Revalidation",
        "EVIDENCE_MISSING": "EvidenceRefill",
        "FAIL_CLOSED": "FailedClosed",
    },
    "ExactRepair": {
        "PROPOSAL_SUBMITTED": "Revalidation",
        "EXACT_REPAIR_REQUIRED": "ExactRepair",
        "FULL_REPLAN_REQUIRED": "FullReplan",
        "EVIDENCE_MISSING": "EvidenceRefill",
        "FAIL_CLOSED": "FailedClosed",
    },
    "Revalidation": {
        "VALIDATION_PASSED": "Validated",
        "EXACT_REPAIR_REQUIRED": "ExactRepair",
        "FULL_REPLAN_REQUIRED": "FullReplan",
        "EVIDENCE_MISSING": "EvidenceRefill",
        "FAIL_CLOSED": "FailedClosed",
    },
    "Validated": {
        "PROPOSAL_SUBMITTED": "Revalidation",
        "EVIDENCE_MISSING": "EvidenceRefill",
        "FAIL_CLOSED": "FailedClosed",
    },
    "FailedClosed": {
        "EVIDENCE_MISSING": "EvidenceRefill",
        "PROPOSAL_SUBMITTED": "Revalidation",
        "FAIL_CLOSED": "FailedClosed",
    },
}


class ArchitectureTransitionError(ValueError):
    pass


def initial_architecture_state() -> dict[str, Any]:
    return {"version": 1, "current": "Discovery", "transitionHistory": []}


def _failed_closed_architecture_state(reason: str) -> dict[str, Any]:
    """Return the fail-closed state used for persisted-state integrity failures."""

    return {
        "version": 1,
        "current": "FailedClosed",
        "transitionHistory": [],
        "integrityError": str(reason or "persisted architecture state is invalid"),
    }


def reduce_architecture_state(
    state: dict[str, Any] | None,
    event: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current_state = dict(state or initial_architecture_state())
    current = str(current_state.get("current") or "Discovery")
    normalized_event = str(event or "").strip().upper()
    if current not in ARCHITECTURE_STATES:
        raise ArchitectureTransitionError(f"unknown architecture state: {current}")
    target = (_TRANSITIONS.get(current) or {}).get(normalized_event)
    if not target:
        raise ArchitectureTransitionError(
            f"illegal architecture transition: {current} + {normalized_event or '<empty>'}"
        )
    history = list(current_state.get("transitionHistory") or [])
    history.append(
        {
            "from": current,
            "event": normalized_event,
            "to": target,
            "at": datetime.now(tz=timezone.utc).isoformat(),
            **({"metadata": dict(metadata)} if metadata else {}),
        }
    )
    return {
        "version": 1,
        "current": target,
        "transitionHistory": history[-64:],
    }


def _state_path(session_id: str, project_root: str) -> Path:
    identity = json.dumps(
        {"sessionId": str(session_id or ""), "projectRoot": str(project_root or "")},
        ensure_ascii=False,
        sort_keys=True,
    )
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return resolve_agent_state_root() / "architecture-states" / f"{key}.json"


def load_architecture_state(session_id: str, project_root: str) -> dict[str, Any]:
    if not str(session_id or "").strip():
        return initial_architecture_state()
    path = _state_path(session_id, project_root)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return initial_architecture_state()
    except (OSError, UnicodeError):
        return _failed_closed_architecture_state(
            "persisted architecture state exists but is unreadable"
        )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return _failed_closed_architecture_state(
            "persisted architecture state contains invalid JSON"
        )
    if not isinstance(payload, dict):
        return _failed_closed_architecture_state(
            "persisted architecture state must be a JSON object"
        )
    if str(payload.get("current") or "") not in ARCHITECTURE_STATES:
        return _failed_closed_architecture_state(
            "persisted architecture state is invalid"
        )
    if not isinstance(payload.get("transitionHistory"), list):
        return _failed_closed_architecture_state(
            "persisted architecture transition history is invalid"
        )
    return payload


def save_architecture_state(
    session_id: str,
    project_root: str,
    state: dict[str, Any],
) -> None:
    if not str(session_id or "").strip():
        return
    atomic_write_text(
        _state_path(session_id, project_root),
        json.dumps(state, ensure_ascii=False, separators=(",", ":")),
    )


def architecture_state_for_result(
    previous: dict[str, Any] | None,
    payload: dict[str, Any],
    *,
    proposal_supplied: bool,
) -> dict[str, Any]:
    """Reduce one architecture response without asking the model to track phases."""

    state = dict(previous or initial_architecture_state())
    error_code = str(payload.get("errorCode") or "")
    graph = payload.get("graphEvidence") if isinstance(payload.get("graphEvidence"), dict) else {}
    validation = (
        payload.get("proposalValidation")
        if isinstance(payload.get("proposalValidation"), dict)
        else {}
    )
    repair_strategy = str(validation.get("repairStrategy") or "")
    if not repair_strategy:
        submission = payload.get("repairSubmission")
        if isinstance(submission, dict) and submission.get("mode") == "fullProposal":
            repair_strategy = "full_replan"

    evidence_missing = bool(
        error_code in {
            "ARCHITECTURE_PROPOSAL_SOURCE_CHANGED",
            "PROJECT_GRAPH_UNAVAILABLE",
            "ARCHITECTURE_EVIDENCE_INCOMPLETE",
        }
        or payload.get("projectRoot") in (None, "")
        or graph.get("complete") is False
    )
    if evidence_missing:
        return reduce_architecture_state(
            state, "EVIDENCE_MISSING", metadata={"errorCode": error_code}
        )

    if error_code == "ARCHITECTURE_PROPOSAL_BASE_MISSING":
        fresh = initial_architecture_state()
        return reduce_architecture_state(fresh, "EVIDENCE_READY")

    current = str(state.get("current") or "Discovery")
    if proposal_supplied and current != "Revalidation":
        state = reduce_architecture_state(state, "PROPOSAL_SUBMITTED")

    if validation:
        if validation.get("ok") is True:
            gate = validation.get("implementationGate") or {}
            if gate.get("writesAllowed") is True:
                return reduce_architecture_state(state, "VALIDATION_PASSED")
            design_contract = (
                validation.get("designContract")
                if isinstance(validation.get("designContract"), dict)
                else {}
            )
            if design_contract.get("validationLevel") == "Draft":
                return reduce_architecture_state(
                    state,
                    "EXACT_REPAIR_REQUIRED",
                    metadata={"reason": "bind_architecture_contract"},
                )
            return reduce_architecture_state(
                state,
                "FAIL_CLOSED",
                metadata={"reason": str(gate.get("nextAction") or "implementation_gate_closed")},
            )
        event = (
            "FULL_REPLAN_REQUIRED"
            if repair_strategy == "full_replan"
            else "EXACT_REPAIR_REQUIRED"
        )
        return reduce_architecture_state(state, event)

    if error_code in {
        "ARCHITECTURE_PROPOSAL_REVISION_CONFLICT",
        "ARCHITECTURE_PROPOSAL_REPAIR_PATH_MISMATCH",
        "ARCHITECTURE_PROPOSAL_UNCHANGED",
    }:
        event = (
            "FULL_REPLAN_REQUIRED"
            if repair_strategy == "full_replan"
            else "EXACT_REPAIR_REQUIRED"
        )
        return reduce_architecture_state(state, event)
    if payload.get("ok") is False:
        return reduce_architecture_state(
            state, "FAIL_CLOSED", metadata={"errorCode": error_code}
        )
    if str(state.get("current") or "") in {"Discovery", "EvidenceRefill"}:
        return reduce_architecture_state(state, "EVIDENCE_READY")
    return state
