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


def test_plan_includes_small_model_execution_contract():
    plan = build_agent_plan("Fix C1083 missing include in MyActor.h", "compile_fix")
    payload = plan.to_dict()
    assert payload["writeGate"]["writesAllowed"] is True
    assert payload["writeGate"]["mustReadBeforeWrite"] is True
    assert payload["writeGate"]["mustBuildAfterWrite"] is True
    assert payload["checkpoints"]
    assert payload["stopConditions"]
    assert payload["retryPolicy"]


def test_runtime_debug_write_gate_blocks_edits():
    plan = build_agent_plan("Read PIE crash logs and diagnose input mapping", "runtime_debug")
    assert plan.write_gate["writesAllowed"] is False
    result = verify_edit_allowed(plan, files_count=0, patches_count=1)
    assert result["ok"] is False
    assert any("Write gate" in issue for issue in result["issues"])


def test_shader_material_blueprint_analysis_blocks_edits():
    for mode in ("shader", "material_analysis", "blueprint_analysis"):
        plan = build_agent_plan("Analyze graph and parameters", mode)
        assert plan.task_kind == "inspect_only"
        assert plan.edit_strategy == "no_edit"
        assert plan.write_gate["writesAllowed"] is False
        assert mode in plan.evidence.rag_modes


def test_verify_edit_limit_from_profile():
    plan = build_agent_plan("Implement dodge component", "agent_edit")
    max_files = int(plan.write_gate["maxFilesPerEdit"])
    assert max_files > 0
    result = verify_edit_allowed(plan, files_count=max_files + 1, patches_count=0)
    assert result["ok"] is False
    assert any("maxFilesPerEdit" in issue for issue in result["issues"])
