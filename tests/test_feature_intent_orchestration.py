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


def test_detailed_gomoku_request_keeps_write_route_without_approval_gates(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    plan = build_agent_plan(
        "Implement AGomokuGameMode, AGomokuGameState, and AGomokuBoardActor for "
        "a 15x15 local hotseat game. Use WGomokuHUD or a simple UI widget, keep "
        "architecture clean and extensible for later multiplayer, and provide "
        "all required Unreal C++ files."
    )

    assert plan.write_gate["writesAllowed"] is True
    assert plan.edit_strategy != "no_edit"
    assert plan.domain_profile["architectureRequired"] is False
    assert plan.feature_intent["requiresResolution"] is False
    assert GATE not in plan.orchestration["requiredBeforeWrite"]
    assert "unreal_architecture_reasoning" not in plan.orchestration["requiredBeforeWrite"]
    assert plan.orchestration["requiredBeforeWrite"] == [
        "unreal_code_sketch_claim_validate"
    ]


def test_summarized_local_gomoku_behavior_does_not_invent_an_intent_gate(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    plan = build_agent_plan(
        "Implement Gomoku local 2-player hotseat game with board actor, mouse "
        "click placement, turn system, win detection, restart button, personal "
        "timer, and timeout auto-place logic."
    )

    assert plan.feature_intent["requiresResolution"] is False
    assert GATE not in plan.orchestration["requiredBeforeWrite"]
    assert plan.orchestration["requiredBeforeWrite"] == [
        "unreal_code_sketch_claim_validate"
    ]


def test_non_write_analysis_never_requires_feature_intent_gate(monkeypatch) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    plan = build_agent_plan("Analyze the current subsystem architecture, no edits")

    assert GATE not in plan.orchestration["requiredBeforeWrite"]
    assert not plan.feature_intent


def test_earliest_unfinished_feature_request_persists_completion_audit_gate(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    plan = build_agent_plan(
        "현재 프로젝트의 구현 상태를 먼저 확인하고 아직 완료되지 않은 가장 앞 단계의 "
        "핵심 기능 하나를 실제로 완성해줘"
    )
    payload = plan.to_dict()

    assert payload["featureCompletionAudit"] == {
        "version": 1,
        "required": True,
        "status": "pending",
    }
    assert payload["featureIntent"]["requiresFeatureCompletionAudit"] is True
    assert payload["featureIntent"]["requiresResolution"] is True
    assert GATE in payload["orchestration"]["requiredBeforeWrite"]


def test_gui_acceptance_prompt_routes_to_edit_with_completion_frontier(
    monkeypatch,
) -> None:
    """Keep the exact Korean GUI acceptance prompt on the write path."""

    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    plan = build_agent_plan(
        "현재 O-Mock 프로젝트의 구현 상태를 먼저 확인하고, 오목 규칙과 로컬 "
        "플레이부터 시작하는 개발 순서에서 아직 완료되지 않은 가장 앞 단계의 핵심 "
        "기능 하나를 실제로 완성해줘. 문서나 계획만 만드는 데 그치지 말고 기능 "
        "구현을 우선해. 기존 동작과 현재 상태 소유권은 깨지 말고, 필요한 자동화 "
        "테스트와 Unreal 빌드까지 실행해서 결과를 알려줘."
    )
    payload = plan.to_dict()

    assert payload["taskKind"] == "edit"
    assert payload["writeGate"]["writesAllowed"] is True
    assert payload["editStrategy"] != "no_edit"
    assert payload["featureCompletionAudit"] == {
        "version": 1,
        "required": True,
        "status": "pending",
    }
    assert payload["featureIntent"]["requiresFeatureCompletionAudit"] is True
    assert payload["featureIntent"]["requiresResolution"] is True
    assert GATE in payload["orchestration"]["requiredBeforeWrite"]
