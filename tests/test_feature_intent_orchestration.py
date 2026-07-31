from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from agent_orchestrator import build_agent_plan  # noqa: E402

GATE = "unreal_feature_intent_resolve"


def test_ambiguous_edit_inserts_feature_intent_gate_before_write_tools(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    plan = build_agent_plan(
        "Implement the best state architecture across multiple modules, maybe "
        "persistent or replicated",
    )
    payload = plan.to_dict()

    assert payload["featureIntent"]["requiresResolution"] is True
    assert payload["featureIntent"]["candidateCount"] == 3
    assert GATE in payload["orchestration"]["requiredBeforeWrite"]
    assert GATE in payload["toolPolicy"]
    write_indices = [
        payload["toolPolicy"].index(tool)
        for tool in ("replace_in_file", "write_file")
        if tool in payload["toolPolicy"]
    ]
    assert write_indices
    assert payload["toolPolicy"].index(GATE) < min(write_indices)
    assert all(
        "dimensions" not in candidate
        and "acceptanceCriteria" not in candidate
        for candidate in payload["featureIntent"]["candidates"]
    )


def test_low_ambiguity_bounded_edit_keeps_legacy_gate_set(monkeypatch) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    plan = build_agent_plan(
        "Implement null guard in Source/Demo/Thing.cpp; local transient behavior, "
        "no replication, fail closed, no UI changes."
    )

    assert plan.feature_intent["requiresResolution"] is False
    assert GATE not in plan.orchestration["requiredBeforeWrite"]
    assert GATE not in plan.tool_policy


def test_precise_existing_owner_edit_does_not_add_feature_gate(monkeypatch) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    plan = build_agent_plan(
        "Add a cooldown timer to the existing player component without "
        "replication or persistence"
    )

    assert plan.feature_intent["ambiguity"]["ambiguityScore"] < 0.45
    assert plan.feature_intent["requiresResolution"] is False
    assert GATE not in plan.orchestration["requiredBeforeWrite"]
    assert GATE not in plan.tool_policy


def test_non_write_analysis_never_requires_feature_intent_gate(monkeypatch) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    plan = build_agent_plan("Analyze the current subsystem architecture, no edits")

    assert GATE not in plan.orchestration["requiredBeforeWrite"]
    assert not plan.feature_intent
