"""Tests for agent orchestrator (Phase 14)."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from agent_orchestrator import (  # noqa: E402
    build_agent_plan,
    build_suggested_tool_calls,
    classify_task,
    is_continuation_request,
    normalize_project_name,
    parse_project_control_intent,
    project_control_project_path_hint,
    verify_edit_allowed,
)


def test_continuation_classifier_only_accepts_pure_short_commands():
    for request in (
        "계속해",
        "계속 진행해",
        "이어가",
        "ㅇㅇ",
        "응",
        "continue",
        "go on",
        "resume",
    ):
        assert is_continuation_request(request) is True
    for request in (
        "continue implementing Source/Demo/Foo.cpp",
        "계속 진행하면서 새 subsystem도 추가해",
        "resume the cancelled build with a new plan",
    ):
        assert is_continuation_request(request) is False


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


def test_korean_do_not_stop_at_planning_is_implementation_work():
    prompt = (
        "현재 구현 상태를 먼저 확인하고 아직 완료되지 않은 핵심 기능을 실제로 완성해줘. "
        "문서나 계획만 만드는 데 그치지 말고 기능 구현을 우선해."
    )
    assert classify_task(prompt, "auto") == "edit"


def test_implementation_status_inventory_is_not_write_intent():
    assert classify_task(
        "Read GameMode and PlayerController to assess current implementation status "
        "and identify the most critical missing feature.",
        "auto",
    ) == "inspect_only"


def test_classify_answer_only():
    assert classify_task("What is UActorComponent?", "api_lookup") == "answer_only"


def test_project_control_classification_is_narrow_and_never_write_enabled():
    for request in (
        "What is the active project path?",
        "Switch active project to C:/Unreal Projects/Example/Example.uproject",
        "현재 활성 프로젝트 상태를 알려줘",
        "현재 프로젝트를 확인해줘",
        "프로젝트를 선택해줘",
    ):
        assert classify_task(request, "auto") == "project_control", request

    # A mixed request still needs ordinary analysis/implementation planning.
    assert classify_task(
        "Switch active project and analyze the current source architecture",
        "auto",
    ) != "project_control"

    plan = build_agent_plan("현재 활성 프로젝트 상태를 알려줘", "auto")
    assert plan.task_kind == "project_control"
    assert plan.edit_strategy == "no_edit"
    assert plan.write_gate["writesAllowed"] is False
    assert plan.evidence.rag_modes == []
    assert plan.orchestration["taskSessionRequired"] is False


def test_project_control_keeps_only_explicit_cross_platform_uproject_path():
    windows_path = r"C:\Unreal Projects\Example\Example.uproject"
    unix_path = "/Volumes/Work/Example Project/Example.uproject"
    assert project_control_project_path_hint(
        f'Switch active project to "{windows_path}"'
    ) == windows_path
    assert project_control_project_path_hint(
        f"Set active project to '{unix_path}'"
    ) == unix_path
    assert project_control_project_path_hint("Select Example by name") == ""


def test_project_control_intent_parser_distinguishes_query_negation_and_mixed_work():
    project_name = "Project" + "_MJS"
    cases = (
        ("지금 프로젝트 어디야", "status", "query", "none", "", True, False),
        ("현재 작업 프로젝트 뭐야", "status", "query", "none", "", True, False),
        (f"그럼 프로젝트 {project_name}로 지정", "select", "command", "name", project_name, True, False),
        (f"{project_name}를 프로젝트로 지정해", "select", "command", "name", project_name, True, False),
        (f"{project_name}로 프로젝트 바꿔", "select", "command", "name", project_name, True, False),
        (f"{project_name}로 지정돼 있어?", "status", "query", "name", project_name, True, False),
        (f"{project_name}로 지정하지 마", "noop", "command", "name", project_name, True, True),
    )
    for request, operation, speech_act, target_kind, target, pure, negated in cases:
        parsed = parse_project_control_intent(request)
        assert parsed.matched is True, request
        assert parsed.operation == operation, request
        assert parsed.speech_act == speech_act, request
        assert parsed.target_kind == target_kind, request
        assert parsed.target == target, request
        assert parsed.pure_control is pure, request
        assert parsed.negated is negated, request

    english_cases = (
        ("What is the active project path?", "status", "query", "none", "", False),
        ("Set Project_MJS as the active project", "select", "command", "name", project_name, False),
        ("Is Project_MJS the current project?", "status", "query", "name", project_name, False),
        ("Do not set Project_MJS as the active project", "noop", "command", "name", project_name, True),
        ("Don't switch the active project to Project_MJS", "noop", "command", "name", project_name, True),
    )
    for request, operation, speech_act, target_kind, target, negated in english_cases:
        parsed = parse_project_control_intent(request)
        assert parsed.matched is True, request
        assert parsed.operation == operation, request
        assert parsed.speech_act == speech_act, request
        assert parsed.target_kind == target_kind, request
        assert parsed.target == target, request
        assert parsed.pure_control is True, request
        assert parsed.negated is negated, request

    mixed = parse_project_control_intent(
        f"프로젝트 {project_name}로 바꾸고 Player AnimInstance 분석해"
    )
    assert mixed.matched is True
    assert mixed.operation == "select"
    assert mixed.pure_control is False
    assert mixed.remaining_request == "Player AnimInstance 분석해"
    english_mixed = parse_project_control_intent(
        f"Switch the active project to {project_name}, then analyze Player AnimInstance"
    )
    assert english_mixed.operation == "select"
    assert english_mixed.target == project_name
    assert english_mixed.pure_control is False
    assert english_mixed.remaining_request == "analyze Player AnimInstance"
    assert classify_task(
        f"{project_name} 프로젝트의 VFX 시스템 분석해", "auto"
    ) != "project_control"


def test_project_control_parser_rejects_descriptions_hypotheticals_and_embedded_control():
    non_commands = (
        "프로젝트 Project_MJS로 지정된 VFX 시스템 분석해",
        "프로젝트 Project_MJS로 바꾸는 방법 알려줘",
        "프로젝트 Project_MJS로 바꾸면 뭐가 달라져?",
        "Should I switch the active project to Project_MJS?",
        "Explain how to switch active project to Project_MJS",
        "use project settings to fix input",
        "VFX 시스템 분석하고 프로젝트 Project_MJS로 바꿔",
        "코드 분석해. 현재 프로젝트 어디야?",
    )
    for request in non_commands:
        parsed = parse_project_control_intent(request)
        assert parsed.matched is False, request
        assert classify_task(request, "auto") != "project_control", request


def test_project_control_parser_preserves_negated_mixed_work_and_korean_word_boundary():
    english = parse_project_control_intent(
        "Do not switch the active project to Project_MJS, then analyze VFX"
    )
    assert english.operation == "noop"
    assert english.negated is True
    assert english.target == "Project_MJS"
    assert english.pure_control is False
    assert english.remaining_request == "analyze VFX"

    korean = parse_project_control_intent(
        "Project_MJS로 지정하지 말고 VFX 시스템 분석해"
    )
    assert korean.operation == "noop"
    assert korean.negated is True
    assert korean.target == "Project_MJS"
    assert korean.pure_control is False
    assert korean.remaining_request == "VFX 시스템 분석해"

    switch_and_fix = parse_project_control_intent(
        "프로젝트 Project_MJS로 바꾸고 고쳐줘"
    )
    assert switch_and_fix.operation == "select"
    assert switch_and_fix.remaining_request == "고쳐줘"


def test_project_control_parser_treats_quoted_paths_as_atomic_targets():
    windows_path = "C:/Foo and Bar, Inc/Game.uproject"
    english = parse_project_control_intent(
        f'Switch active project to "{windows_path}" and analyze VFX'
    )
    assert english.operation == "select"
    assert english.target_kind == "path"
    assert english.target == windows_path
    assert english.remaining_request == "analyze VFX"

    korean_path = r"C:\Foo, Bar\Game.uproject"
    korean = parse_project_control_intent(
        f'프로젝트 "{korean_path}"로 바꾸고 고쳐줘'
    )
    assert korean.operation == "select"
    assert korean.target_kind == "path"
    assert korean.target == korean_path
    assert korean.remaining_request == "고쳐줘"


def test_normalize_project_name_is_unicode_and_separator_stable():
    assert normalize_project_name("  My_Project-Name.uproject ") == "myprojectname"
    assert normalize_project_name("Ｍｙ　Ｐｒｏｊｅｃｔ") == "myproject"


def test_plan_issues_minimal_request_intent_and_material_semantic_gate():
    read_plan = build_agent_plan("Player Animinstance C++ 클래스 분석해", "auto")
    assert read_plan.request_intent["version"] == 1
    assert read_plan.request_intent["domain"] == "source"
    assert read_plan.request_intent["operation"] == "analyze"
    assert read_plan.request_intent["mutability"] == "none"
    calls = build_suggested_tool_calls(
        "Player Animinstance C++ 클래스 분석해",
        "cpp_analysis",
        "auto",
        {
            "ok": True,
            "projectName": "Portable",
            "projectDir": str(SCRIPTS.parent),
            "workspaceRoot": str(SCRIPTS.parent),
            "sourceBrowsePath": "project://Source",
            "browseAvailable": True,
        },
    )
    assert calls[0]["tool"] == "unreal_symbol_lookup"

    write_plan = build_agent_plan("엑셀레이터 기능을 구현해", "auto")
    assert write_plan.semantic_ambiguity["material"] is True
    assert write_plan.semantic_ambiguity["selectedInterpretation"] is None
    assert write_plan.write_gate["writesAllowed"] is False
    assert write_plan.write_gate["requiresUserClarification"] is True
    assert write_plan.request_intent["ambiguity"]["status"] == "unresolved"


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


def test_latest_user_write_goal_cannot_be_replaced_by_model_read_subtask():
    from agent_orchestrator import resolve_plan_request

    latest = "Implement the first incomplete local-play feature and run tests and build."
    restatement = (
        "Read GameMode and PlayerController to assess implementation status and "
        "identify one missing feature."
    )
    resolved = resolve_plan_request(restatement, latest)

    assert resolved["modelRequestSuppressed"] is True
    assert resolved["usedLatestUserMessage"] is True
    assert resolved["request"] == latest
    assert build_agent_plan(
        restatement,
        "auto",
        latest_user_message=latest,
    ).task_kind == "edit"


def test_raw_continuation_does_not_replace_the_active_planner_objective():
    from agent_orchestrator import resolve_plan_request

    objective = "Implement local move history and undo, then build and test it."
    resolved = resolve_plan_request(objective, "계속해")

    assert resolved["request"] == objective
    assert resolved["usedLatestUserMessage"] is False
    assert resolved["modelRequestSuppressed"] is False


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


def test_known_project_context_is_not_relooked_up_for_source_inspection():
    calls = build_suggested_tool_calls(
        "Analyze the current component source",
        "cpp_analysis",
        "auto",
        {
            "ok": True,
            "sourceBrowsePath": "project://Example/Source",
        },
    )
    assert calls
    assert all(call["tool"] != "unreal_get_active_project" for call in calls)
    first_search = next(call for call in calls if call["tool"] == "search_files")
    assert first_search["args"]["path"] == "project://Example/Source"


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


def test_write_plan_uses_server_owned_conditional_post_build_contract():
    payload = build_agent_plan(
        "Implement a portable health component and run relevant automation tests",
        "agent_edit",
        file_count_hint=1,
    ).to_dict()

    contract = payload["orchestration"]["completionContract"]
    assert contract["decisionOwner"] == "latest authoritative server task control"
    assert contract["whenAutomationRequired"] == [
        "build_unreal_project",
        "run_unreal_automation_tests",
        "complete",
    ]
    assert contract["whenAutomationNotRequiredOrDisabled"] == [
        "build_unreal_project",
        "complete",
    ]
    assert (
        "automation_if_declared_or_required_by_server_control"
        in payload["orchestration"]["validationStages"]
    )
    assert not any(
        condition.startswith("Stop only when build_unreal_project")
        for condition in payload["stopConditions"]
    )
    assert any(
        "run_unreal_automation_tests" in condition
        and "authoritative server task control" in condition
        for condition in payload["stopConditions"]
    )


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

def test_task_lifecycle_mode_is_intent_driven_for_every_nonwriting_plan() -> None:
    from agent_orchestrator import resolve_task_lifecycle_mode

    for task_kind in (
        "inspect_only",
        "cpp_analysis",
        "code_sketch",
        "runtime_debug",
        "answer_only",
        "refactor",
        "future_nonwriting_kind",
    ):
        assert resolve_task_lifecycle_mode(
            {
                "taskKind": task_kind,
                "writeGate": {"writesAllowed": False},
            },
            "Collect the required project evidence and report the result",
        ) == "read_only"

    assert resolve_task_lifecycle_mode(
        {"taskKind": "edit", "writeGate": {"writesAllowed": True}},
        "Implement the requested change",
    ) == "agent_edit"
    assert resolve_task_lifecycle_mode(
        {"taskKind": "inspect_only", "writeGate": {"writesAllowed": False}},
        "Create an implementation plan only; do not edit files",
    ) == "plan_only"
    assert resolve_task_lifecycle_mode(
        {"taskKind": "project_control", "writeGate": {"writesAllowed": False}},
        "Show the active project",
    ) == "plan_only"
