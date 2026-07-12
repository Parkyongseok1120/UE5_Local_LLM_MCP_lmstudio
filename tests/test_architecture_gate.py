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
    assert isinstance(gate.get("assumptions"), list)


def test_domain_profile_mixed_domain():
    profile = build_domain_profile("Add replication to gas ability component", mode="auto")
    assert profile.primary in {"component", "replication", "gas", "generic"}
    assert "component" in profile.scores or "replication" in profile.scores


def test_architecture_decision_fingerprint_stable():
    q = ["Which owner?", "Which lifetime?"]
    assert question_fingerprint(q) == question_fingerprint(list(reversed(q)))
    decision = build_architecture_decision(ambiguity_gate={"ambiguityScore": 0.8, "recommendedAction": "ask_user_once", "clarificationQuestions": q})
    assert decision.question_fingerprint
    assert decision.risk_score >= 0.7
