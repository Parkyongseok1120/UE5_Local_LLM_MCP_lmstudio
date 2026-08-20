"""Typed executable view of the canonical control-state registry."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REGISTRY_PATH = Path(__file__).resolve().parents[1] / "config" / "control_state_machine.json"


@dataclass(frozen=True)
class ControlStateRegistry:
    version: int
    events: frozenset[str]
    event_handlers: dict[str, str]
    synthesis_lifecycle: frozenset[str]
    proxy_lifecycle_states: frozenset[str]
    recovery_transitions: dict[str, dict[str, Any]]
    running_liveness_alternatives: tuple[str, ...]
    lifecycle_states: frozenset[str]
    terminal_lifecycle_states: frozenset[str]
    lifecycle_events: frozenset[str]
    lifecycle_transitions: dict[str, tuple[dict[str, str], ...]]

    def lifecycle_path_to_complete(self, start: str) -> tuple[str, ...]:
        """Return a shortest declared path to successful completion."""
        normalized = str(start or "").strip().upper()
        if normalized not in self.lifecycle_states:
            raise ValueError(f"undeclared lifecycle state: {normalized or '<empty>'}")
        queue: list[tuple[str, tuple[str, ...]]] = [(normalized, (normalized,))]
        visited: set[str] = set()
        while queue:
            current, path = queue.pop(0)
            if current == "COMPLETE":
                return path
            if current in visited:
                continue
            visited.add(current)
            for transition in self.lifecycle_transitions.get(current, ()):
                next_state = transition["next"]
                if next_state not in visited:
                    queue.append((next_state, (*path, next_state)))
        return ()

    def lifecycle_path_to_terminal(self, start: str) -> tuple[str, ...]:
        normalized = str(start or "").strip().upper()
        if normalized not in self.lifecycle_states:
            raise ValueError(f"undeclared lifecycle state: {normalized or '<empty>'}")
        queue: list[tuple[str, tuple[str, ...]]] = [(normalized, (normalized,))]
        visited: set[str] = set()
        while queue:
            current, path = queue.pop(0)
            if current in self.terminal_lifecycle_states:
                return path
            if current in visited:
                continue
            visited.add(current)
            for transition in self.lifecycle_transitions.get(current, ()):
                next_state = transition["next"]
                if next_state not in visited:
                    queue.append((next_state, (*path, next_state)))
        return ()


def load_control_state_registry(path: Path = REGISTRY_PATH) -> ControlStateRegistry:
    raw = json.loads(path.read_text(encoding="utf-8"))
    events = frozenset(str(item) for item in raw.get("events") or [])
    handlers = {
        str(key): str(value)
        for key, value in (raw.get("eventHandlers") or {}).items()
    }
    if not events or events != frozenset(handlers):
        missing = sorted(events - frozenset(handlers))
        undeclared = sorted(frozenset(handlers) - events)
        raise RuntimeError(
            f"control event registry drift: missingHandlers={missing}, undeclaredHandlers={undeclared}"
        )
    synthesis = frozenset(str(item) for item in raw.get("synthesisLifecycle") or [])
    proxy = frozenset(str(item) for item in raw.get("proxyLifecycleStates") or [])
    if not synthesis or not synthesis.issubset(proxy):
        raise RuntimeError("every canonical synthesis lifecycle state must be accepted by the proxy")
    lifecycle_states = frozenset(str(item) for item in raw.get("lifecycleStates") or [])
    terminal_lifecycle_states = frozenset(
        str(item) for item in raw.get("terminalLifecycleStates") or []
    )
    lifecycle_events = frozenset(str(item) for item in raw.get("lifecycleEvents") or [])
    raw_transitions = raw.get("lifecycleTransitions") or {}
    if not lifecycle_states or not terminal_lifecycle_states.issubset(lifecycle_states):
        raise RuntimeError("terminal lifecycle states must be declared lifecycle states")
    if frozenset(raw_transitions) != lifecycle_states:
        raise RuntimeError("every lifecycle state must have one explicit transition list")
    lifecycle_transitions: dict[str, tuple[dict[str, str], ...]] = {}
    for state_name, values in raw_transitions.items():
        transitions: list[dict[str, str]] = []
        for value in values if isinstance(values, list) else []:
            event = str((value or {}).get("event") or "")
            next_state = str((value or {}).get("next") or "")
            if event not in lifecycle_events or next_state not in lifecycle_states:
                raise RuntimeError(
                    f"invalid lifecycle transition: {state_name} --{event}--> {next_state}"
                )
            transitions.append({"event": event, "next": next_state})
        lifecycle_transitions[str(state_name)] = tuple(transitions)
    registry = ControlStateRegistry(
        version=int(raw.get("version") or 0),
        events=events,
        event_handlers=handlers,
        synthesis_lifecycle=synthesis,
        proxy_lifecycle_states=proxy,
        recovery_transitions={
            str(key): dict(value)
            for key, value in (raw.get("recoveryTransitions") or {}).items()
            if isinstance(value, dict)
        },
        running_liveness_alternatives=tuple(
            str(item) for item in raw.get("runningLivenessAlternatives") or []
        ),
        lifecycle_states=lifecycle_states,
        terminal_lifecycle_states=terminal_lifecycle_states,
        lifecycle_events=lifecycle_events,
        lifecycle_transitions=lifecycle_transitions,
    )
    terminal_unreachable = sorted(
        state_name
        for state_name in lifecycle_states - terminal_lifecycle_states
        if not registry.lifecycle_path_to_terminal(state_name)
    )
    if terminal_unreachable:
        raise RuntimeError(
            "recoverable lifecycle states without terminal path: "
            f"{terminal_unreachable}"
        )
    completion_unreachable = sorted(
        state_name
        for state_name in lifecycle_states - {"TERMINAL_BLOCKED", "CANCELLED"}
        if not registry.lifecycle_path_to_complete(state_name)
    )
    if completion_unreachable:
        raise RuntimeError(
            "recoverable lifecycle states without COMPLETE path: "
            f"{completion_unreachable}"
        )
    return registry


CONTROL_STATE_REGISTRY = load_control_state_registry()


def require_control_event(event_kind: str) -> str:
    normalized = str(event_kind or "").strip().upper()
    if normalized not in CONTROL_STATE_REGISTRY.events:
        raise ValueError(f"undeclared canonical control event: {normalized or '<empty>'}")
    return normalized
