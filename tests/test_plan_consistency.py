#!/usr/bin/env python
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from agent_orchestrator import AgentPlan, EvidencePlan, build_agent_plan  # noqa: E402
from plan_consistency import (  # noqa: E402
    apply_ambiguity_write_policy,
    essential_tools_enabled,
    sanitize_tools_for_exposure,
    validate_plan_consistency,
)


def test_ask_user_once_blocks_writes():
    strategy, writes, extras = apply_ambiguity_write_policy(
        ambiguity_gate={"recommendedAction": "ask_user_once", "clarificationQuestions": ["Which owner?"]},
        strategy="exact_patch",
        evidence_writes_allowed=True,
    )
    assert strategy == "no_edit"
    assert writes is False
    assert extras.get("requiresUserClarification") is True


def test_plan_only_blocks_writes():
    strategy, writes, _ = apply_ambiguity_write_policy(
        ambiguity_gate={"recommendedAction": "plan_only", "ambiguityScore": 0.6},
        strategy="exact_patch",
        evidence_writes_allowed=True,
    )
    assert strategy == "no_edit"
    assert writes is False


def test_validate_plan_consistency_catches_contradiction():
    plan = AgentPlan(
        request="rename helper",
        task_kind="refactor",
        evidence=EvidencePlan(task_kind="refactor", writes_allowed=True),
        edit_strategy="no_edit",
        tool_policy=["unreal_rag_search"],
        suggested_tool_calls=[],
        project_context={"ok": True},
        write_gate={"writesAllowed": True, "maxFilesPerEdit": 2},
        checkpoints=[],
        stop_conditions=[],
        retry_policy=[],
        notes=[],
    )
    issues = validate_plan_consistency(plan)
    assert any("no_edit but writeGate.writesAllowed=true" in item for item in issues)


def test_essential_mode_filters_hidden_refactor_tools(monkeypatch):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    policy, calls, notes = sanitize_tools_for_exposure(
        ["unreal_rag_search", "unreal_refactor_manager_plan"],
        [{"tool": "unreal_refactor_manager_plan", "args": {}}],
        refactor_manager_embedded=True,
    )
    assert "unreal_refactor_manager_plan" not in policy
    assert calls == []
    assert notes


def test_build_agent_plan_ambiguity_blocks_writes(monkeypatch):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    plan = build_agent_plan(
        "Maybe either world or game instance subsystem ownership is unclear across modules",
        mode="auto",
    )
    assert plan.ambiguity_gate.get("recommendedAction") in {"ask_user_once", "plan_only", "human_approval"}
    assert plan.write_gate.get("writesAllowed") is False
    assert validate_plan_consistency(plan) == []


def test_essential_tools_enabled_default_false(monkeypatch):
    monkeypatch.delenv("MCP_ESSENTIAL_TOOLS", raising=False)
    assert essential_tools_enabled() is False
