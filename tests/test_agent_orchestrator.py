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
    assert classify_task("Fix the current build errors until it builds", "auto") == "compile_fix"
    assert classify_task(
        "Complete the existing implementation until it compiles successfully. "
        "No new features, just fix existing code to compile.",
        "auto",
    ) == "compile_fix"


def test_feature_prompt_with_build_acceptance_remains_edit_work():
    prompt = (
        "Finish the remaining prototype features that are only declared or untested. "
        "Support a complete multiplayer match, run a real Unreal build and all relevant "
        "automation tests, fixing any failures you find."
    )
    assert classify_task(prompt, "auto") == "edit"
    roadmap_prompt = (
        "Take O-Mock all the way through the original gameplay roadmap, stages 0 through 13. "
        "This is a real implementation and verification pass. Implement the roadmap in coherent, "
        "buildable slices: Stage 0: audit; Stage 1: deterministic rules; Stage 9: correct network "
        "authority; Stage 10: lobby; Stage 11: minigame; Stage 13: bots. If a test or build fails, "
        "diagnose the observed failure, fix the actual cause, and rerun it."
    )
    assert classify_task(roadmap_prompt, "auto") == "edit"
    assert classify_task(
        "현재 생성된 C++ 구현을 실제 컴파일 성공까지 직접 완성해줘",
        "auto",
    ) == "compile_fix"


def test_classify_answer_only():
    assert classify_task("What is UActorComponent?", "api_lookup") == "answer_only"


def test_classify_inspect_review():
    assert classify_task("Review project architecture inventory", "review") == "inspect_only"


def test_classify_cinematic_system_analysis_korean():
    assert classify_task("현재 프로젝트의 시네마틱 시스템 분석", "auto") == "cpp_analysis"


def test_classify_cinematic_structure_explain():
    assert classify_task("시네마틱 시스템 구조와 작동 방식 설명", "auto") in {"inspect_only", "cpp_analysis"}


def test_classify_cinematic_runtime_bug():
    assert classify_task("시네마틱 종료 후 위치가 되돌아가는 버그 분석", "auto") == "runtime_debug"


def test_classify_cinematic_implement_is_edit():
    assert classify_task("시네마틱 시스템에 Stop 기능 구현", "auto") == "edit"

def test_improve_request_is_edit():
    assert classify_task("Improve this code", "auto") == "edit"
    assert classify_task("\ucf54\ub4dc \uac1c\uc120 \ud574\uc918", "auto") == "edit"


def test_runtime_debug_mode_keeps_explicit_fix_intent():
    assert classify_task("StaminaComponent runtime bug: fix it", "runtime_debug") == "edit"


def test_runtime_debug_fix_preserves_causal_workflow_and_write_gates(monkeypatch):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    plan = build_agent_plan("StaminaComponent runtime bug in PIE: fix it", "runtime_debug")
    assert plan.task_kind == "edit"
    assert plan.write_gate["writesAllowed"] is True
    assert plan.orchestration["strategy"] == "runtime_causal_loop"
    assert plan.orchestration["runtimeVerificationRequired"] is True
    assert {
        "unreal_runtime_debug_session",
        "unreal_code_sketch_claim_validate",
    }.issubset(plan.orchestration["requiredBeforeWrite"])
    assert "unreal_runtime_debug_session" in plan.tool_policy
    roles = plan.orchestration["roleContract"]
    assert roles["planner"]["mayWrite"] is False
    assert roles["implementer"]["startsAfter"] == plan.orchestration["requiredBeforeWrite"]
    assert roles["verifier"]["mustUseFreshPostWriteEvidence"] is True
    assert roles["verifier"]["mustNotAcceptImplementerSelfReport"] is True
    assert "same_observer_runtime_verification" in roles["verifier"]["requiredEvidence"]
    assert any("same reproductionFingerprint" in item for item in plan.stop_conditions)
    assert classify_task("Fix runtime crash in StaminaComponent", "auto") == "edit"


def test_long_feature_spec_does_not_create_runtime_debug_gate_from_scattered_words(monkeypatch):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    request = (
        "Implement the full authoritative Gomoku roadmap. Correct broken networking "
        "code, keep match state in GameMode and GameState, add an ordered event log, "
        "run Automation when builds fail, and inspect tests that do not assert behavior."
    )

    plan = build_agent_plan(request, "agent_edit")

    assert plan.task_kind == "edit"
    assert plan.orchestration["strategy"] != "runtime_causal_loop"
    assert "unreal_runtime_debug_session" not in plan.orchestration["requiredBeforeWrite"]


def test_nearby_runtime_symptom_still_requires_causal_gate(monkeypatch):
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")

    plan = build_agent_plan(
        "Fix the GameMode runtime issue where PIE restores the wrong turn state.",
        "agent_edit",
    )

    assert plan.orchestration["strategy"] == "runtime_causal_loop"
    assert "unreal_runtime_debug_session" in plan.orchestration["requiredBeforeWrite"]


def test_negated_refactor_does_not_escalate_local_fix():
    plan = build_agent_plan(
        "No cross-file refactoring; fix only the StaminaComponent bug.",
        "implementation",
    )

    assert plan.task_kind == "edit"
    assert plan.write_gate["writesAllowed"] is True


def test_bug_hunt_without_fix_is_inspect_only():
    cases = [
        "지금 버그있는거 찾기만하고 수정은 하지마.",
        "find bugs only, do not fix",
        "버그만 찾아줘",
        "수정하지 말고 버그 찾아줘",
    ]
    for prompt in cases:
        plan = build_agent_plan(prompt, "auto")
        assert classify_task(prompt, "auto") == "inspect_only", prompt
        assert plan.task_kind == "inspect_only", prompt
        assert plan.evidence.writes_allowed is False, prompt
        assert plan.write_gate["writesAllowed"] is False, prompt
        assert "replace_in_file" not in plan.tool_policy, prompt


def test_invented_refactor_plan_is_suppressed_by_latest_user_bug_hunt():
    from agent_orchestrator import resolve_plan_request

    invented = (
        "Refactor enemy combat feedback into a clean architecture:\n"
        "- Introduce UEnemyPoiseComponent (SuperArmor/Poise/Groggy) with GameplayTags.\n"
        "- Introduce UEnemyHitReactionComponent (hit montages, knockback, hit flash).\n"
        "- Introduce UCombatFeedbackSubsystem.\n"
        "- Thin down AEnemyCharacter::TakeDamage() to delegate to these components/subsystem.\n"
        "Keep changes focused and backward-compatible; do not remove existing behavior."
    )
    latest = "지금 버그있는거 찾기만하고 수정은 하지마."
    resolved = resolve_plan_request(invented, latest)
    assert resolved["modelRequestSuppressed"] is True
    assert resolved["usedLatestUserMessage"] is True
    assert resolved["request"] == latest
    plan = build_agent_plan(invented, "auto", latest_user_message=latest)
    assert plan.task_kind == "inspect_only"
    assert plan.write_gate["writesAllowed"] is False
    assert any("overridden" in note.lower() or "invented" in note.lower() for note in plan.notes)


def test_invented_implementation_plan_without_latest_user_fails_closed():
    invented = (
        "Refactor enemy combat feedback into a clean architecture:\n"
        "- Introduce UEnemyPoiseComponent\n"
        "- Introduce UEnemyHitReactionComponent\n"
        "Thin down TakeDamage and keep changes focused and backward-compatible."
    )
    plan = build_agent_plan(invented, "auto")
    assert plan.task_kind == "inspect_only"
    assert plan.write_gate["writesAllowed"] is False


def test_explicit_fix_still_edit_when_not_negated():
    assert classify_task("StaminaComponent 버그 수정해줘", "auto") == "edit"
    assert classify_task("fix the stamina bug", "auto") == "edit"



def test_cinematic_analysis_plan_source_first():
    plan = build_agent_plan("현재 프로젝트의 시네마틱 시스템 분석", "auto")
    payload = plan.to_dict()
    assert plan.task_kind == "cpp_analysis"
    assert plan.evidence.writes_allowed is False
    assert payload["writeGate"]["writesAllowed"] is False
    policy = payload["toolPolicy"]
    assert policy.index("search_files") < policy.index("unreal_rag_search")
    tools = [c["tool"] for c in payload["suggestedToolCalls"]]
    assert "search_files" in tools
    assert "read_file" in tools or any("read_file" in str(c) for c in payload["suggestedToolCalls"])


def test_refactor_r0_no_edit(monkeypatch):
    monkeypatch.delenv("MCP_ESSENTIAL_TOOLS", raising=False)
    plan = build_agent_plan("Discover impact for UMySubsystem refactor R0", "refactor_r0")
    payload = plan.to_dict()
    assert plan.task_kind == "refactor"
    assert plan.edit_strategy == "no_edit"
    assert plan.evidence.writes_allowed is False
    assert payload["refactorManager"]["managerMode"] == "refactor_manager"
    assert "unreal_refactor_manager_plan" in payload["evidencePlan"]["gates"]
    assert "unreal_semantic_refactor_guard" in payload["evidencePlan"]["gates"]
    assert "unreal_semantic_refactor_guard" in payload["orchestration"]["requiredBeforeWrite"]
    assert payload["suggestedToolCalls"][1]["tool"] == "unreal_refactor_manager_plan"


def test_korean_implementation_plan_is_read_only() -> None:
    plan = build_agent_plan("Project_MJS 스태미나 시스템 구현 계획 세워", "auto")
    payload = plan.to_dict()

    assert plan.task_kind == "inspect_only"
    assert plan.edit_strategy == "no_edit"
    assert payload["writeGate"]["writesAllowed"] is False
    assert "write_file" not in payload["toolPolicy"]
    assert "replace_in_file" not in payload["toolPolicy"]


def test_plan_then_implement_keeps_edit_intent() -> None:
    plan = build_agent_plan("스태미나 시스템 계획 세우고 구현해줘", "auto")

    assert plan.task_kind == "edit"
    assert plan.write_gate["writesAllowed"] is True


def test_medium_refactor_requires_approval_gate_before_writes():
    plan = build_agent_plan("Refactor combat system API across inventory and ability subsystem", "refactor_r2")
    payload = plan.to_dict()

    assert plan.task_kind == "refactor"
    assert plan.edit_strategy == "no_edit"
    assert payload["writeGate"]["requiresHumanApproval"] is True
    assert payload["writeGate"]["writesAllowed"] is False
    assert "human_approval_gate" in payload["evidencePlan"]["gates"]
    assert payload["refactorManager"]["nextAction"] in {
        "collect_impact_scan_inputs",
        "collect_missing_impact_roles",
        "refresh_symbol_graph",
        "resolve_incomplete_impact_evidence",
        "request_human_approval",
    }
    assert any("Medium/large refactors require impact plan" in note for note in payload["notes"])
    assert any("write_file only for brand-new files" in item for item in payload["checkpoints"])
    assert any("do not fall back to write_file" in item for item in payload["checkpoints"])
    assert any("run_javascript" in item and "project file I/O" in item for item in payload["checkpoints"])


def test_compile_fix_patch_strategy():
    plan = build_agent_plan("Fix LNK2019 unresolved external", "compile_fix")
    assert plan.edit_strategy == "exact_patch"
    assert "compile_fix" in plan.evidence.rag_modes
    assert "unreal_code_sketch_claim_validate" in plan.orchestration["requiredBeforeWrite"]
    assert plan.tool_policy.index("unreal_code_sketch_claim_validate") < plan.tool_policy.index(
        "replace_in_file"
    )


def test_multifile_refactor_mode_is_compile_fix_track():
    plan = build_agent_plan("Fix C3668 interface signature drift across header and cpp", "multifile_refactor")

    assert plan.task_kind == "compile_fix"
    assert plan.edit_strategy == "exact_patch"


def test_compile_fix_link_route_includes_soft_steering_checkpoints():
    plan = build_agent_plan("Fix LNK2019 unresolved external symbol UHoldoutComponent::StartDash", "compile_fix")
    payload = plan.to_dict()

    assert payload["errorRoute"]["errorSubkind"] == "LNK_MISSING_CPP_DEFINITION"
    assert any("Route soft steering:" in item for item in payload["checkpoints"])
    assert any("Route soft warning:" in item for item in payload["checkpoints"])


def test_compile_fix_signature_route_includes_required_reads():
    plan = build_agent_plan("CPP_FUNCTION_SIGNATURE_MISMATCH header/cpp signature mismatch", "compile_fix")
    payload = plan.to_dict()

    assert payload["errorRoute"]["errorSubkind"] == "HEADER_CPP_SIGNATURE_MISMATCH"
    assert any("Route required read: header declaration" in item for item in payload["checkpoints"])
    assert any("Route forbidden action: Build.cs-first fix without module evidence" in item for item in payload["checkpoints"])


def test_compile_fix_includes_c1083_error_route_and_module_hints():
    plan = build_agent_plan(
        "fatal error C1083: Cannot open include file: 'GameplayTagContainer.h': No such file or directory",
        "compile_fix",
    )
    payload = plan.to_dict()

    assert payload["errorRoute"]["broadMode"] == "module_fix"
    assert "module_fix" in payload["evidencePlan"]["rag_modes"]
    assert any("Route required read: owner Build.cs" in item for item in payload["checkpoints"])
    assert any(hint["module"] == "GameplayTags" for hint in payload["moduleHints"])


def test_compile_fix_includes_reflection_error_route():
    plan = build_agent_plan("BadActor.generated.h must be the last include before UCLASS", "reflection_fix")
    payload = plan.to_dict()

    assert payload["errorRoute"]["broadMode"] == "reflection_fix"
    assert payload["evidencePlan"]["rag_modes"][0] == "reflection_fix"
    assert any("Route forbidden action: broad refactor" in item for item in payload["checkpoints"])


def test_symbol_graph_hint_missing_graph_does_not_fail(monkeypatch):
    import agent_orchestrator

    monkeypatch.setattr(agent_orchestrator, "load_symbol_graph", None, raising=False)
    plan = build_agent_plan("Fix ADemoActor C1083 compile error", "compile_fix")

    assert plan.to_dict().get("symbolGraphHints", []) == []


def test_verify_edit_blocked_on_inspect():
    plan = build_agent_plan("Review findings only", "review")
    result = verify_edit_allowed(plan, files_count=1, patches_count=0)
    assert result["ok"] is False


def test_tool_policy_nonempty():
    plan = build_agent_plan("Implement dodge component", "agent_edit")
    assert len(plan.tool_policy) >= 3


def test_single_surface_codegen_uses_guarded_orchestration():
    plan = build_agent_plan(
        "Implement dodge component in the existing component file",
        "agent_edit",
        file_count_hint=1,
    )
    payload = plan.to_dict()
    assert payload["orchestration"]["riskTier"] == "medium"
    assert payload["orchestration"]["strategy"] == "guarded"
    assert "unreal_code_sketch_claim_validate" in payload["toolPolicy"]
    assert payload["toolPolicy"].index("unreal_code_sketch_claim_validate") < payload["toolPolicy"].index("replace_in_file")
    assert payload["toolPolicy"].index("static_validate_project") < payload["toolPolicy"].index("build_unreal_project")


def test_multifile_codegen_is_staged_without_forcing_architecture_gate():
    plan = build_agent_plan(
        "Implement a feature across the controller, game state, and actor",
        "agent_edit",
        file_count_hint=3,
    )
    payload = plan.to_dict()
    assert payload["orchestration"]["riskTier"] == "high"
    assert payload["orchestration"]["strategy"] == "staged_guarded"
    assert "unreal_architecture_reasoning" not in payload["orchestration"]["requiredBeforeWrite"]
    assert "unreal_code_sketch_claim_validate" in payload["orchestration"]["requiredBeforeWrite"]


def test_explicit_architecture_design_still_requires_architecture_gate():
    plan = build_agent_plan(
        "Implement a redesign of the architecture and ownership boundaries for the match state",
        "agent_edit",
        file_count_hint=3,
    )
    payload = plan.to_dict()
    assert payload["orchestration"]["strategy"] == "architecture_first"
    assert "unreal_architecture_reasoning" in payload["orchestration"]["requiredBeforeWrite"]


def test_orchestration_reports_active_profile_without_claiming_model_switching():
    route = build_agent_plan("Fix C1083 missing include", "compile_fix").to_dict()["orchestration"]
    assert route["profile"]
    assert "does not" in route["routingBoundary"].lower()


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
    for mode in ("shader", "material_analysis", "material_porting", "blueprint_analysis", "blueprint_verification"):
        plan = build_agent_plan("Analyze graph and parameters", mode)
        assert plan.task_kind == "inspect_only"
        assert plan.edit_strategy == "no_edit"
        assert plan.write_gate["writesAllowed"] is False
        assert mode in plan.evidence.rag_modes


def test_asset_metadata_modes_use_metadata_tool_policy(monkeypatch):
    monkeypatch.delenv("MCP_ESSENTIAL_TOOLS", raising=False)
    plan = build_agent_plan("Analyze M_Blackhole_Core material graph wires", "material_analysis")
    assert "unreal_editor_metadata_status" in plan.tool_policy
    assert "unreal_run_editor_export" in plan.tool_policy
    assert "unreal_asset_graph_lookup" in plan.tool_policy


def test_code_sketch_verify_edit_blocked():
    plan = build_agent_plan("Sketch a HealthComponent API", "codegen")
    assert plan.task_kind == "code_sketch"
    result = verify_edit_allowed(plan, files_count=1, patches_count=0)
    assert result["ok"] is False
    assert any("code_sketch" in issue for issue in result["issues"])


def test_edit_plan_suggests_search_files_before_write(monkeypatch):
    monkeypatch.setattr(
        "project_context.resolve_active_project_context",
        lambda: {
            "ok": True,
            "sourceBrowsePath": "Project/Source/Game",
            "projectName": "Game",
        },
    )
    plan = build_agent_plan("Add UHealthComponent under SharedComponent", "agent_edit")
    tools = [call["tool"] for call in plan.suggested_tool_calls]
    assert "search_files" in tools


def test_inventory_plan_source_first():
    plan = build_agent_plan("inventory what's missing Stamina system", "review")
    payload = plan.to_dict()
    assert plan.task_kind == "inspect_only"
    policy = payload["toolPolicy"]
    assert "search_files" in policy
    assert policy.index("search_files") < policy.index("unreal_rag_search")
    assert "direct_source_evidence" in payload["evidencePlan"]["gates"]
    tools = [c["tool"] for c in payload["suggestedToolCalls"]]
    assert tools.count("search_files") >= 1
    search_queries = [c["args"].get("query") for c in payload["suggestedToolCalls"] if c["tool"] == "search_files"]
    assert any(q and "Stamina" in str(q) for q in search_queries)
    assert any("Guideline/engine RAG" in item for item in payload["checkpoints"])


def test_korean_gap_inventory_source_first():
    plan = build_agent_plan("HP Stemina 시스템에 추가해야할 것들이 있을텐데 뭐뭐 있니", "review")
    payload = plan.to_dict()
    assert plan.task_kind == "inspect_only"
    policy = payload["toolPolicy"]
    assert policy.index("search_files") < policy.index("unreal_rag_search")


def test_inspect_policy_not_tied_to_fixed_project_name(monkeypatch):
    for name, browse in (
        ("AlphaGame", "AlphaGame/Source"),
        ("BetaSample", "BetaSample/Source"),
    ):
        monkeypatch.setattr(
            "project_context.resolve_active_project_context",
            lambda name=name, browse=browse: {
                "ok": True,
                "projectName": name,
                "sourceBrowsePath": browse,
            },
        )
        plan = build_agent_plan(f"Review inventory for missing FooComponent in {name}", "review")
        policy = plan.to_dict()["toolPolicy"]
        assert policy.index("search_files") < policy.index("unreal_rag_search")
        queries = [
            c["args"].get("query")
            for c in plan.suggested_tool_calls
            if c["tool"] == "search_files"
        ]
        assert any("Foo" in str(q) for q in queries)


def test_edit_codegen_refactor_policy_unchanged():
    from tool_policy import gates_for_task, tool_sequence_for_task

    seq = tool_sequence_for_task("edit")
    assert seq[0] == "unreal_agent_session"
    assert "search_files" not in seq
    assert "direct_source_evidence" not in gates_for_task("edit")
    assert "search_files" not in tool_sequence_for_task("codegen")
    assert "search_files" not in tool_sequence_for_task("refactor")


def test_verify_edit_limit_from_profile():
    plan = build_agent_plan("Implement dodge component", "agent_edit")
    max_files = int(plan.write_gate["maxFilesPerEdit"])
    assert max_files > 0
    result = verify_edit_allowed(plan, files_count=max_files + 1, patches_count=0)
    assert result["ok"] is False
    assert any("maxFilesPerEdit" in issue for issue in result["issues"])
