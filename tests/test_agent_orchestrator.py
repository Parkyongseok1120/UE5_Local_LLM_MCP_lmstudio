"""Tests for agent orchestrator (Phase 14)."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from agent_orchestrator import (  # noqa: E402
    build_agent_plan,
    classify_task,
    verify_edit_allowed,
)


def test_classify_compile_fix():
    assert classify_task("Fix C1083 missing include in MyActor.h", "auto") == "compile_fix"


def test_classify_answer_only():
    assert classify_task("What is UActorComponent?", "api_lookup") == "answer_only"


def test_classify_inspect_review():
    assert classify_task("Review project architecture inventory", "review") == "inspect_only"


def test_refactor_r0_no_edit():
    plan = build_agent_plan("Discover impact for UMySubsystem refactor R0", "refactor_r0")
    assert plan.task_kind == "refactor"
    assert plan.edit_strategy == "no_edit"
    assert plan.evidence.writes_allowed is False


def test_compile_fix_patch_strategy():
    plan = build_agent_plan("Fix LNK2019 unresolved external", "compile_fix")
    assert plan.edit_strategy == "exact_patch"
    assert "compile_fix" in plan.evidence.rag_modes


def test_verify_edit_blocked_on_inspect():
    plan = build_agent_plan("Review findings only", "review")
    result = verify_edit_allowed(plan, files_count=1, patches_count=0)
    assert result["ok"] is False


def test_tool_policy_nonempty():
    plan = build_agent_plan("Implement dodge component", "agent_edit")
    assert len(plan.tool_policy) >= 3
