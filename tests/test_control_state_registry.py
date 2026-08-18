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
