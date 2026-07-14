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


def test_classify_cinematic_system_analysis_korean():
    assert classify_task("현재 프로젝트의 시네마틱 시스템 분석", "auto") == "cpp_analysis"


def test_classify_cinematic_structure_explain():
    assert classify_task("시네마틱 시스템 구조와 작동 방식 설명", "auto") in {"inspect_only", "cpp_analysis"}


def test_classify_cinematic_runtime_bug():
    assert classify_task("시네마틱 종료 후 위치가 되돌아가는 버그 분석", "auto") == "runtime_debug"


def test_classify_cinematic_implement_is_edit():
    assert classify_task("시네마틱 시스템에 Stop 기능 구현", "auto") == "edit"


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
