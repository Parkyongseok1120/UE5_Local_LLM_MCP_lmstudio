"""Automated replay of the four-turn regression transcript from the audit."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from agent_orchestrator import build_agent_plan  # noqa: E402
from semantic_ambiguity import resolve_lexical_semantic_ambiguity  # noqa: E402
from target_resolver import resolve_symbol_target  # noqa: E402


def _load_rag_mcp():
    spec = importlib.util.spec_from_file_location(
        "unreal_rag_mcp_transcript_replay",
        SCRIPTS / "unreal_rag_mcp.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_previous_four_turn_transcript_replays_without_guessing_or_repetition(
    monkeypatch, tmp_path: Path
):
    status_text = "지금 프로젝트 어디야"
    selection_text = "그럼 프로젝트 Project_MJS로 지정"
    source_text = (
        "Player Animinstance C++ 클래스를 살펴보고 현재 어떻게 동작하는지 분석해봐"
    )
    ambiguity_text = (
        "이동 애니메이션이 바뀔 때 보정하고 싶고 엑셀레이터 기능도 있으면 함"
    )

    # Turn 1: status is a taskless, read-only control request.
    status_plan = build_agent_plan(status_text, "auto").to_dict()
    assert status_plan["taskKind"] == "project_control"
    assert status_plan["requestIntent"]["operation"] == "status"
    assert status_plan["requestIntent"]["mutability"] == "none"
    assert status_plan["suggestedToolCalls"] == []

    # Turn 2: selecting the already-active exact name is a side-effect-free no-op,
    # never an inspect_only source task.
    project_file = tmp_path / "Project MJS" / "Project_MJS.uproject"
    project_file.parent.mkdir(parents=True)
    project_file.write_text("{}", encoding="utf-8")
    rag_mcp = _load_rag_mcp()
    monkeypatch.setattr(
        rag_mcp,
        "load_shared_config",
        lambda: {"activeProject": str(project_file)},
    )
    monkeypatch.setattr(
        rag_mcp,
        "resolve_active_project_context",
        lambda: {"ok": True, "activeProject": str(project_file)},
    )
    monkeypatch.setattr(rag_mcp, "active_project_names", lambda: ["Project_MJS"])
    selection = rag_mcp._project_control_response(selection_text, tmp_path)
    assert selection["taskKind"] == "project_control"
    assert selection["switchResult"] == "already_active"
    assert selection["changed"] is False
    assert selection["taskSessionStarted"] is False
    assert selection["nextActionIsTool"] is False

    # Turn 3: deterministic symbol resolution runs before fallback text search,
    # and the unique high-confidence Unreal class is reached without duplicate calls.
    import project_context

    monkeypatch.setattr(
        project_context,
        "resolve_active_project_context",
        lambda: {
            "ok": True,
            "projectName": "Project_MJS",
            "projectFile": str(project_file),
            "projectRoot": str(project_file.parent),
            "sourceRoot": str(project_file.parent / "Source" / "Project_MJS"),
            "sourceBrowsePath": "project://Source/Project_MJS",
            "suggestedToolCalls": [],
        },
    )
    source_plan = build_agent_plan(source_text, "auto").to_dict()
    tool_names = [row["tool"] for row in source_plan["suggestedToolCalls"]]
    assert source_plan["taskKind"] == "cpp_analysis"
    assert tool_names[0] == "unreal_symbol_lookup"
    assert tool_names.count("unreal_symbol_lookup") == 1
    assert tool_names.count("read_file") == 1
    target = resolve_symbol_target(
        "Player Animinstance C++ 클래스",
        [
            {
                "symbol_name": "UCPlayerCharacterAnimInstance",
                "qualified_name": "Project_MJS::UCPlayerCharacterAnimInstance",
                "file_path": (
                    "Source/Project_MJS/Animation/"
                    "UCPlayerCharacterAnimInstance.h"
                ),
            },
            {
                "symbol_name": "UCEnemyCharacterAnimInstance",
                "file_path": "Source/Project_MJS/Animation/UCEnemyCharacterAnimInstance.h",
            },
        ],
    )
    assert target["status"] == "resolved"
    assert target["selected"]["symbol"] == "UCPlayerCharacterAnimInstance"
    assert target["exact"] is False

    # Turn 4: source evidence may rank animation smoothing, but cannot silently
    # turn the user's lexical ambiguity into a sprint/dash feature decision.
    semantic = resolve_lexical_semantic_ambiguity(
        ambiguity_text,
        evidence_rows=[
            {"symbol_name": "bIsAccelerating"},
            {"symbol_name": "GroundSpeed"},
        ],
        write_intent=False,
    )
    assert semantic is not None
    assert semantic["selectedInterpretation"] is None
    assert semantic["semanticInterpretations"][0]["id"] == (
        "animation_acceleration_smoothing"
    )
    sprint = next(
        row
        for row in semantic["semanticInterpretations"]
        if row["id"] == "sprint_or_boost_gameplay"
    )
    assert sprint["supportingEvidence"] == []
    assert {"input binding", "speed policy", "stamina policy"}.issubset(
        sprint["unsupportedAssumptions"]
    )
