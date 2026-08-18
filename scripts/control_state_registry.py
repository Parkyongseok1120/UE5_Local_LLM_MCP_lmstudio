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
    return ControlStateRegistry(
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
    )


CONTROL_STATE_REGISTRY = load_control_state_registry()


def require_control_event(event_kind: str) -> str:
    normalized = str(event_kind or "").strip().upper()
    if normalized not in CONTROL_STATE_REGISTRY.events:
        raise ValueError(f"undeclared canonical control event: {normalized or '<empty>'}")
    return normalized
