from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from control_state_registry import CONTROL_STATE_REGISTRY, require_control_event  # noqa: E402


def test_declared_events_have_exactly_one_registered_handler_branch() -> None:
    assert CONTROL_STATE_REGISTRY.events == frozenset(
        CONTROL_STATE_REGISTRY.event_handlers
    )
    assert len(set(CONTROL_STATE_REGISTRY.event_handlers.values())) == len(
        CONTROL_STATE_REGISTRY.event_handlers
    )


def test_unknown_control_event_is_rejected_instead_of_becoming_a_noop() -> None:
    with pytest.raises(ValueError, match="undeclared canonical control event"):
        require_control_event("TASK_CANCELLED")


def test_proxy_registry_is_generated_from_the_canonical_lifecycle() -> None:
    generated = json.loads(
        (
            ROOT
            / "lmstudio-context-compactor-plugin"
            / "src"
            / "control-state-registry.generated.js"
        ).read_text(encoding="utf-8").split("module.exports =", 1)[1].strip().removesuffix(";")
    )
    assert generated["events"] == sorted(CONTROL_STATE_REGISTRY.events)
    assert generated["synthesisLifecycle"] == sorted(
        CONTROL_STATE_REGISTRY.synthesis_lifecycle
    )
    assert generated["proxyLifecycleStates"] == sorted(
        CONTROL_STATE_REGISTRY.proxy_lifecycle_states
    )
    assert CONTROL_STATE_REGISTRY.synthesis_lifecycle.issubset(
        CONTROL_STATE_REGISTRY.proxy_lifecycle_states
    )


def test_each_recovery_transition_has_an_explicit_exit() -> None:
    for status, transition in CONTROL_STATE_REGISTRY.recovery_transitions.items():
        assert transition.get("satisfactionEvent"), status
        assert transition.get("nextStatus"), status
        assert transition.get("nextLifecycle"), status


def test_every_recoverable_lifecycle_state_has_a_path_to_complete() -> None:
    for state in (
        CONTROL_STATE_REGISTRY.lifecycle_states
        - {"TERMINAL_BLOCKED", "CANCELLED"}
    ):
        path = CONTROL_STATE_REGISTRY.lifecycle_path_to_complete(state)
        assert path, state
        assert path[0] == state
        assert path[-1] == "COMPLETE"


def test_delivery_recovery_and_general_user_input_are_distinct_states() -> None:
    general_events = {
        transition["event"]
        for transition in CONTROL_STATE_REGISTRY.lifecycle_transitions[
            "AWAITING_USER_INPUT"
        ]
    }
    delivery_events = {
        transition["event"]
        for transition in CONTROL_STATE_REGISTRY.lifecycle_transitions[
            "DELIVERY_RECOVERY_AWAITING_USER"
        ]
    }
    assert "OPERATOR_CONFIRMED_VISIBLE" not in general_events
    assert "OPERATOR_AUTHORIZED_REEMIT" not in general_events
    assert "USER_INPUT_COMMITTED" not in delivery_events


def test_lifecycle_graph_uses_only_declared_events_and_states() -> None:
    assert set(CONTROL_STATE_REGISTRY.lifecycle_transitions) == set(
        CONTROL_STATE_REGISTRY.lifecycle_states
    )
    for state, transitions in CONTROL_STATE_REGISTRY.lifecycle_transitions.items():
        if state in CONTROL_STATE_REGISTRY.terminal_lifecycle_states:
            assert transitions == ()
        for transition in transitions:
            assert transition["event"] in CONTROL_STATE_REGISTRY.lifecycle_events
            assert transition["next"] in CONTROL_STATE_REGISTRY.lifecycle_states


def test_capacity_invariants_align_producers_ledgers_and_projections() -> None:
    capacities = CONTROL_STATE_REGISTRY.capacity_policies
    frontier = capacities["inspectionFrontier"]
    audit = capacities["repositoryAuditPage"]
    slices = capacities["slicePlan"]
    outcomes = capacities["routedAnalysisOutcomeLedger"]
    projection = capacities["controlProjection"]

    assert frontier["producerMaximumPerResult"] <= frontier["durableMaximum"]
    assert audit["sourceEvidenceRetention"] <= frontier["durableMaximum"]
    assert audit["maximumInventoryFiles"] <= frontier["durableMaximum"]
    assert slices["maximumSlices"] == slices["checkpointCompletedSliceCapacity"]
    assert outcomes["maximumEntries"] == 32
    assert outcomes["overflowDisposition"] == "evict_oldest"
    assert projection["maximumModelVisibleCharacters"] == 32000
    assert "requiredTool" in projection["requiredHeaderFields"]
