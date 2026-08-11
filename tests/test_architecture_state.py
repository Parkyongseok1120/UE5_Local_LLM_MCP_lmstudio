from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from architecture_state import (  # noqa: E402
    ARCHITECTURE_STATES,
    _TRANSITIONS,
    ArchitectureTransitionError,
    architecture_state_for_result,
    initial_architecture_state,
    load_architecture_state,
    reduce_architecture_state,
    save_architecture_state,
)


def test_transition_table_is_closed_and_every_declared_transition_reduces() -> None:
    assert set(_TRANSITIONS) == set(ARCHITECTURE_STATES)
    for source, events in _TRANSITIONS.items():
        assert events, source
        for event, target in events.items():
            assert target in ARCHITECTURE_STATES
            result = reduce_architecture_state(
                {"version": 1, "current": source, "transitionHistory": []},
                event,
            )
            assert result["current"] == target
            assert result["transitionHistory"][-1]["from"] == source


def test_transition_history_is_bounded_to_64_entries() -> None:
    state = {"version": 1, "current": "FailedClosed", "transitionHistory": []}
    for _ in range(80):
        state = reduce_architecture_state(state, "FAIL_CLOSED")
    assert len(state["transitionHistory"]) == 64


def test_exact_repair_revalidates_then_becomes_validated() -> None:
    state = reduce_architecture_state(initial_architecture_state(), "EVIDENCE_READY")
    state = reduce_architecture_state(state, "PROPOSAL_SUBMITTED")
    state = reduce_architecture_state(state, "EXACT_REPAIR_REQUIRED")
    assert state["current"] == "ExactRepair"

    state = reduce_architecture_state(state, "PROPOSAL_SUBMITTED")
    state = reduce_architecture_state(state, "VALIDATION_PASSED")

    assert state["current"] == "Validated"
    assert [row["to"] for row in state["transitionHistory"]][-2:] == [
        "Revalidation",
        "Validated",
    ]


def test_illegal_transition_is_rejected_by_server_reducer() -> None:
    with pytest.raises(ArchitectureTransitionError):
        reduce_architecture_state(initial_architecture_state(), "VALIDATION_PASSED")


def test_core_contradiction_routes_to_full_replan() -> None:
    result = architecture_state_for_result(
        initial_architecture_state(),
        {
            "ok": False,
            "projectRoot": "Demo",
            "proposalValidation": {
                "ok": False,
                "repairStrategy": "full_replan",
                "implementationGate": {"writesAllowed": False},
            },
        },
        proposal_supplied=True,
    )
    assert result["current"] == "FullReplan"


def test_source_change_forces_evidence_refill_even_after_validation() -> None:
    validated = architecture_state_for_result(
        initial_architecture_state(),
        {
            "ok": True,
            "projectRoot": "Demo",
            "proposalValidation": {
                "ok": True,
                "implementationGate": {"writesAllowed": True},
            },
        },
        proposal_supplied=True,
    )
    changed = architecture_state_for_result(
        validated,
        {
            "ok": False,
            "projectRoot": "Demo",
            "errorCode": "ARCHITECTURE_PROPOSAL_SOURCE_CHANGED",
        },
        proposal_supplied=True,
    )
    assert validated["current"] == "Validated"
    assert changed["current"] == "EvidenceRefill"


def test_architecture_state_is_durable_per_session_and_project(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    state = reduce_architecture_state(initial_architecture_state(), "EVIDENCE_READY")
    save_architecture_state("session-a", "Project-A", state)

    assert load_architecture_state("session-a", "Project-A")["current"] == "InitialProposal"
    assert load_architecture_state("session-a", "Project-B")["current"] == "Discovery"
