"""Tests for code_sketch routing in the agent orchestrator (sketch quality rail)."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from agent_orchestrator import build_agent_plan, classify_task  # noqa: E402
from rag_search import resolve_mode  # noqa: E402


def test_classify_sketch_korean():
    assert classify_task("시퀀서 위치 유지 코드 시안 보여줘", "auto") == "code_sketch"


def test_classify_sketch_english():
    assert classify_task("show me example code for a tick component", "auto") == "code_sketch"


def test_classify_sketch_explicit_mode():
    assert classify_task("anything", "code_sketch") == "code_sketch"


def test_compile_error_beats_sketch():
    # A concrete compile error should still route to compile_fix, not code_sketch.
    assert classify_task("C1083 컴파일 오류 나는 코드 시안", "auto") == "compile_fix"


def test_resolve_mode_sketch_hint():
    assert resolve_mode("예시 코드 보여줘", "auto") == "code_sketch"


def test_sketch_plan_is_read_only():
    plan = build_agent_plan("컴포넌트 예시 코드 시안 보여줘", "auto")
    payload = plan.to_dict()
    assert plan.task_kind == "code_sketch"
    assert plan.edit_strategy == "no_edit"
    assert plan.evidence.writes_allowed is False
    assert payload["writeGate"]["writesAllowed"] is False


def test_sketch_plan_suggests_symbol_and_claim_validation():
    plan = build_agent_plan("UMovieSceneSequencePlayer 예시 코드 시안", "auto")
    payload = plan.to_dict()
    tools = [call["tool"] for call in payload["suggestedToolCalls"]]
    assert "unreal_rag_search" in tools
    assert "unreal_code_sketch_claim_validate" in tools
    # gates should require symbol lookup + sketch claim validation
    assert "unreal_code_sketch_claim_validate" in payload["evidencePlan"]["gates"]


def test_sketch_tool_policy_has_no_write_steps():
    plan = build_agent_plan("서브시스템 코드 시안 보여줘", "auto")
    payload = plan.to_dict()
    policy = payload["toolPolicy"]
    assert "replace_in_file" not in policy
    assert "write_file" not in policy
    assert "build_unreal_project" not in policy
