#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from architecture_decision import build_architecture_decision, question_fingerprint  # noqa: E402
from domain_planner import architecture_ambiguity_gate, build_domain_profile  # noqa: E402


def test_high_ambiguity_ask_user_once():
    gate = architecture_ambiguity_gate(
        "Maybe either unclear subsystem ownership is ambiguous"
    )
    assert gate["recommendedAction"] == "ask_user_once"
    assert gate["clarificationQuestions"]
    assert gate["architectureRequired"] is True


def test_human_approval_at_high_score():
    gate = architecture_ambiguity_gate(
        "Unclear ambiguous ownership lifetime authority across whole project maybe either subsystem replication"
    )
    assert gate["recommendedAction"] in {"ask_user_once", "human_approval"}
    assert gate["ambiguityScore"] >= 0.7


def test_bounded_assumption_records_assumptions():
    gate = architecture_ambiguity_gate("Add world subsystem for level state")
    assert gate["recommendedAction"] == "bounded_assumption"


def test_explicit_gameframework_authority_contract_does_not_ask_again():
    gate = architecture_ambiguity_gate(
        "Keep authoritative match state in GameState/GameMode. The server must validate moves, "
        "replicate validated state to clients, and put client requests on a client-owned actor "
        "such as PlayerController or Pawn."
    )
    assert gate["recommendedAction"] == "bounded_assumption"
    assert gate["ambiguityScore"] <= 0.45
    assert gate["clarificationQuestions"] == []
    assert any("GameMode/GameState" in item for item in gate["assumptions"])
    assert isinstance(gate.get("assumptions"), list)


def test_domain_profile_mixed_domain():
    profile = build_domain_profile("Add replication to gas ability component", mode="auto")
    assert profile.primary in {"component", "replication", "gas", "generic"}
    assert "component" in profile.scores or "replication" in profile.scores


def test_architecture_quality_constraint_does_not_become_redesign_gate():
    profile = build_domain_profile(
        "Implement AGomokuGameMode and AGomokuBoardActor; keep architecture clean "
        "and extensible for later multiplayer."
    )

    assert profile.primary != "architecture"
    assert profile.architecture_required is False


def test_efficient_architecture_wording_does_not_become_redesign_gate():
    profile = build_domain_profile(
        "Implement hotseat Gomoku across several classes. Use efficient architecture "
        "with one BoardActor and instanced stone meshes."
    )

    assert profile.primary != "architecture"
    assert profile.architecture_required is False


def test_explicit_architecture_design_wording_still_requires_gate():
    profile = build_domain_profile(
        "Design the architecture and ownership boundaries for the match state."
    )

    assert profile.primary == "architecture"
    assert profile.architecture_required is True


def test_architecture_decision_fingerprint_stable():
    q = ["Which owner?", "Which lifetime?"]
    assert question_fingerprint(q) == question_fingerprint(list(reversed(q)))
    decision = build_architecture_decision(ambiguity_gate={"ambiguityScore": 0.8, "recommendedAction": "ask_user_once", "clarificationQuestions": q})
    assert decision.question_fingerprint
    assert decision.risk_score >= 0.7
