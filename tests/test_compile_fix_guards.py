#!/usr/bin/env python
"""Tests for compile-fix wrapper guardrails."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lmstudio_unreal_wrapper import (  # noqa: E402
    answer_claims_build_cs_edit,
    bundle_includes_build_cs,
    declaration_definition_evidence,
    edit_scope_blockers,
    focused_current_source_evidence,
    focused_source_pair_context,
    hallucination_blockers,
    module_fix_retry_feedback,
    no_change_blockers,
    preserve_specific_route,
    request_forbids_build_cs_first,
    request_requests_build_cs_fix,
    route_forbidden_action_blockers,
    summarize_project_state,
    unresolved_build_cs_modules,
    validate_unreal_readiness,
)


def test_gameplaytags_missing_module_is_static_error():
    fixture = ROOT / "tests" / "fixtures" / "compile_fix" / "missing_gameplaytags_dep"

    findings = validate_unreal_readiness(fixture, None)

    assert any(
        finding.code == "POSSIBLE_MISSING_MODULE"
        and finding.severity == "error"
        and "GameplayTags" in finding.message
        for finding in findings
    )


def test_no_change_rejected_when_gameplaytags_still_missing():
    fixture = ROOT / "tests" / "fixtures" / "compile_fix" / "missing_gameplaytags_dep"
    request = (fixture / "request.txt").read_text(encoding="utf-8-sig")

    issues = no_change_blockers(request, fixture, [])

    assert any("GameplayTags" in issue and "Build.cs" in issue for issue in issues)


def test_header_only_edit_rejected_for_cpp_signature_request():
    fixture = ROOT / "tests" / "fixtures" / "compile_fix" / "cpp_header_signature_mismatch"
    request = (fixture / "request.txt").read_text(encoding="utf-8-sig")
    before = {
        "Source/CompileFixSig/Public/HackComponent.h": "void DoWork(int Value);\n",
        "Source/CompileFixSig/Private/HackComponent.cpp": "void UHackComponent::DoWork()\n{\n}\n",
    }
    after = {
        "Source/CompileFixSig/Public/HackComponent.h": "void DoWork();\n",
        "Source/CompileFixSig/Private/HackComponent.cpp": "void UHackComponent::DoWork()\n{\n}\n",
    }

    issues = edit_scope_blockers(request, before, after, fixture)

    assert any(".cpp definition" in issue for issue in issues)


def test_no_change_rejected_when_signature_finding_remains():
    fixture = ROOT / "tests" / "fixtures" / "compile_fix" / "cpp_header_signature_mismatch"
    request = (fixture / "request.txt").read_text(encoding="utf-8-sig")
    findings = validate_unreal_readiness(fixture, None)

    issues = no_change_blockers(request, fixture, findings)

    assert any(finding.code == "CPP_FUNCTION_SIGNATURE_MISMATCH" for finding in findings)
    assert any(".cpp/header mismatch" in issue for issue in issues)


def test_module_fix_project_state_includes_full_build_cs():
    fixture = ROOT / "tests" / "fixtures" / "compile_fix" / "missing_gameplaytags_dep"
    state = summarize_project_state(fixture, mode="module_fix")

    assert "CompileFixTags.Build.cs" in state
    assert "full file" in state
    assert "PublicDependencyModuleNames" in state
    assert '"Core"' in state


def test_compile_fix_project_state_suppresses_full_build_cs():
    fixture = ROOT / "tests" / "fixtures" / "compile_fix" / "missing_gameplaytags_dep"
    state = summarize_project_state(fixture, mode="compile_fix")

    assert "Full *.Build.cs content below" not in state
    assert "CompileFixTags.Build.cs" in state
    assert "PublicDependencyModuleNames" in state


def test_project_state_includes_plain_header_member_declarations(tmp_path):
    fixture = tmp_path
    header = fixture / "Source" / "HoldoutFixture" / "Public" / "HoldoutMissingDefinitionComponent.h"
    cpp = fixture / "Source" / "HoldoutFixture" / "Private" / "HoldoutMissingDefinitionComponent.cpp"
    header.parent.mkdir(parents=True)
    cpp.parent.mkdir(parents=True)
    header.write_text(
        "\n".join(
            [
                "#pragma once",
                "class HOLDOUTFIXTURE_API UHoldoutMissingDefinitionComponent : public UActorComponent",
                "{",
                "public:",
                "    void StartDash();",
                "    virtual void BeginPlay() override;",
                "};",
            ]
        ),
        encoding="utf-8",
    )
    cpp.write_text(
        "void UHoldoutMissingDefinitionComponent::BeginPlay()\n{\n    StartDash();\n}\n",
        encoding="utf-8",
    )

    state = summarize_project_state(fixture, mode="compile_fix")

    assert "void StartDash();" in state
    assert "virtual void BeginPlay() override;" in state


def test_focused_source_pair_context_prefers_current_header_cpp_pair():
    fixture = ROOT / "tests" / "fixtures" / "compile_fix" / "cpp_header_signature_mismatch"

    context = focused_source_pair_context(
        fixture,
        "CPP_FUNCTION_SIGNATURE_MISMATCH Source/CompileFixSig/Private/HackComponent.cpp UHackComponent::DoWork",
    )

    assert "Focused current source evidence" in context
    assert "Source/CompileFixSig/Private/HackComponent.cpp" in context
    assert "Source/CompileFixSig/Public/HackComponent.h" in context
    assert "HackComponent" in context


def test_lnk_evidence_fallback_extracts_missing_declared_function(tmp_path):
    fixture = tmp_path
    header = fixture / "Source" / "HoldoutFixture" / "Public" / "HoldoutMissingDefinitionComponent.h"
    cpp = fixture / "Source" / "HoldoutFixture" / "Private" / "HoldoutMissingDefinitionComponent.cpp"
    header.parent.mkdir(parents=True)
    cpp.parent.mkdir(parents=True)
    header.write_text(
        "\n".join(
            [
                "#pragma once",
                "class HOLDOUTFIXTURE_API UHoldoutMissingDefinitionComponent : public UActorComponent",
                "{",
                "public:",
                "    void StartDash();",
                "    virtual void BeginPlay() override;",
                "};",
            ]
        ),
        encoding="utf-8",
    )
    cpp.write_text(
        "\n".join(
            [
                "#include \"HoldoutMissingDefinitionComponent.h\"",
                "void UHoldoutMissingDefinitionComponent::BeginPlay()",
                "{",
                "    Super::BeginPlay();",
                "    StartDash();",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    route = {"errorSubkind": "LNK_MISSING_CPP_DEFINITION"}
    context = focused_current_source_evidence(
        fixture,
        "Fix the Unreal linker error caused by a missing cpp definition for a declared function.",
        route,
    )

    assert "Current declaration/definition evidence" in context
    assert "UHoldoutMissingDefinitionComponent::StartDash()" in context
    assert "Do not copy function names from generic RAG examples" in context


def test_declaration_definition_evidence_does_not_need_path_or_symbol(tmp_path):
    fixture = tmp_path
    header = fixture / "Source" / "Demo" / "Public" / "DashComponent.h"
    cpp = fixture / "Source" / "Demo" / "Private" / "DashComponent.cpp"
    header.parent.mkdir(parents=True)
    cpp.parent.mkdir(parents=True)
    header.write_text(
        "class DEMO_API UDashComponent : public UActorComponent\n{\npublic:\n    void StartDash();\n};\n",
        encoding="utf-8",
    )
    cpp.write_text("void UDashComponent::BeginPlay()\n{\n    StartDash();\n}\n", encoding="utf-8")

    context = declaration_definition_evidence(
        fixture,
        "LNK2019 unresolved external missing cpp definition",
        {"errorSubkind": "LNK_MISSING_CPP_DEFINITION"},
    )

    assert "UDashComponent::StartDash()" in context


def test_hallucination_rejects_build_cs_claim_without_file():
    fixture = ROOT / "tests" / "fixtures" / "compile_fix" / "missing_gameplaytags_dep"
    request = (fixture / "request.txt").read_text(encoding="utf-8-sig")
    bundle = {
        "answer": "Added GameplayTags to PublicDependencyModuleNames in Build.cs.",
        "files": [],
        "patches": [],
    }

    issues = hallucination_blockers(request, bundle["answer"], bundle, fixture)

    assert issues
    assert "Build.cs" in issues[0]


def test_negative_build_cs_answer_does_not_claim_build_cs_edit():
    assert not answer_claims_build_cs_edit(
        "Guarded the source include without modifying Build.cs; Build.cs remains unchanged."
    )
    assert not answer_claims_build_cs_edit(
        "Wrapped the editor-only code while respecting the constraint not to add UnrealEd to Build.cs."
    )


def test_negative_build_cs_instruction_does_not_require_build_cs_patch(tmp_path):
    fixture = tmp_path
    source = fixture / "Source" / "Demo" / "Private" / "DashComponent.cpp"
    source.parent.mkdir(parents=True)
    source.write_text("void UDashComponent::StartDash()\n{\n}\n", encoding="utf-8")
    request = (
        "Fix the Unreal linker error caused by a missing cpp definition. "
        "Do not use a Build.cs-first fix without module evidence."
    )
    bundle = {
        "answer": "Added missing cpp definition for StartDash.",
        "files": [
            {
                "path": "Source/Demo/Private/DashComponent.cpp",
                "content": "void UDashComponent::StartDash()\n{\n}\n",
            }
        ],
        "patches": [],
    }

    assert request_forbids_build_cs_first(request)
    assert not request_requests_build_cs_fix(request)
    assert hallucination_blockers(request, bundle["answer"], bundle, fixture) == []


def test_route_forbidden_action_rejects_unrealed_build_cs_runtime_fix():
    route = {"forbiddenActions": ["adding UnrealEd to runtime module as default fix"]}
    bundle = {
        "answer": "Guarded source and added conditional UnrealEd.",
        "files": [
            {
                "path": "Source/Demo/Demo.Build.cs",
                "content": (
                    "using UnrealBuildTool;\n"
                    "public class Demo : ModuleRules\n"
                    "{\n"
                    "  public Demo(ReadOnlyTargetRules Target) : base(Target)\n"
                    "  {\n"
                    "    if (Target.bBuildEditor) { PrivateDependencyModuleNames.Add(\"UnrealEd\"); }\n"
                    "  }\n"
                    "}\n"
                ),
            }
        ],
        "patches": [],
    }

    issues = route_forbidden_action_blockers(route, bundle)

    assert issues
    assert "UnrealEd" in issues[0]


def test_missing_definition_fix_rejects_removed_existing_call_site(tmp_path):
    fixture = tmp_path
    before = {
        "Source/Demo/Public/DashComponent.h": "\n".join(
            [
                "class DEMO_API UDashComponent : public UActorComponent",
                "{",
                "public:",
                "    void StartDash();",
                "    virtual void BeginPlay() override;",
                "};",
            ]
        ),
        "Source/Demo/Private/DashComponent.cpp": "\n".join(
            [
                "void UDashComponent::BeginPlay()",
                "{",
                "    StartDash();",
                "}",
            ]
        ),
    }
    after = {
        **before,
        "Source/Demo/Private/DashComponent.cpp": "\n".join(
            [
                "void UDashComponent::BeginPlay()",
                "{",
                "}",
                "",
                "void UDashComponent::StartDash()",
                "{",
                "}",
            ]
        ),
    }

    issues = edit_scope_blockers(
        "Fix LNK2019 missing cpp definition for the declared function.",
        before,
        after,
        fixture,
    )

    assert any("removed existing call site" in issue for issue in issues)


def test_bundle_includes_build_cs_detects_path():
    bundle = {"files": [{"path": "Source/Mod/Mod.Build.cs", "content": ""}], "patches": []}
    assert bundle_includes_build_cs(bundle)


def test_answer_claims_build_cs_edit():
    assert answer_claims_build_cs_edit("Updated PublicDependencyModuleNames in Build.cs to add GameplayTags.")
    assert not answer_claims_build_cs_edit("No changes needed.")


def test_hallucination_allows_non_build_cs_patch_when_modules_present(tmp_path):
    fixture = ROOT / "tests" / "fixtures" / "compile_fix" / "missing_gameplaytags_dep"
    project = tmp_path / "CompileFixTags"
    for path in fixture.rglob("*"):
        if path.is_file():
            rel = path.relative_to(fixture)
            dest = project / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    golden = fixture / "golden" / "Source" / "CompileFixTags" / "CompileFixTags.Build.cs"
    build_cs = project / "Source" / "CompileFixTags" / "CompileFixTags.Build.cs"
    build_cs.write_text(golden.read_text(encoding="utf-8"), encoding="utf-8")

    request = (fixture / "request.txt").read_text(encoding="utf-8-sig")
    assert unresolved_build_cs_modules(request, project) == []

    bundle = {
        "answer": "Adjusted header include order only.",
        "files": [{"path": "Source/CompileFixTags/Public/TaggedActorComponent.h", "content": "x"}],
        "patches": [],
    }
    assert hallucination_blockers(request, bundle["answer"], bundle, project) == []


def test_module_fix_retry_feedback_switches_after_build_cs_present(tmp_path):
    fixture = ROOT / "tests" / "fixtures" / "compile_fix" / "missing_gameplaytags_dep"
    project = tmp_path / "CompileFixTags"
    for path in fixture.rglob("*"):
        if path.is_file():
            rel = path.relative_to(fixture)
            dest = project / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    request = (fixture / "request.txt").read_text(encoding="utf-8-sig")
    assert "patch the owner" in module_fix_retry_feedback(request, project).lower()
    golden = fixture / "golden" / "Source" / "CompileFixTags" / "CompileFixTags.Build.cs"
    build_cs = project / "Source" / "CompileFixTags" / "CompileFixTags.Build.cs"
    build_cs.write_text(golden.read_text(encoding="utf-8"), encoding="utf-8")
    assert "already include" in module_fix_retry_feedback(request, project)


def test_preserve_specific_route_updates_after_build_cs_link_error():
    previous = {"broadMode": "module_fix", "errorSubkind": "C1083_MISSING_INCLUDE"}
    current = {"broadMode": "link_fix", "errorSubkind": "LINK_GENERIC"}
    route, preserved = preserve_specific_route(
        current,
        previous,
        attempt=1,
        changed_paths=["Source/HoldoutFixture/HoldoutFixture.Build.cs"],
    )
    assert route["broadMode"] == "link_fix"
    assert preserved is False


def test_preserve_specific_route_uses_current_route_on_retry_when_specific():
    previous = {"broadMode": "module_fix", "errorSubkind": "C1083_MISSING_INCLUDE"}
    current = {"broadMode": "compile_fix", "errorSubkind": "HEADER_CPP_SIGNATURE_MISMATCH"}
    route, preserved = preserve_specific_route(current, previous, attempt=2)
    assert route["errorSubkind"] == "HEADER_CPP_SIGNATURE_MISMATCH"
    assert preserved is False


def test_multifile_surface_allows_cpp_followup_after_partial_header(tmp_path):
    from wrapper_guards import PENDING_CPP_FOLLOWUP, multifile_surface_blockers

    before = {
        "Source/Demo/Public/DemoComp.h": "void Foo();\n",
        "Source/Demo/Private/DemoComp.cpp": "void UDemoComp::Foo(){}\n",
    }
    after = {
        **before,
        "Source/Demo/Private/DemoComp.cpp": "void UDemoComp::Foo(int32 Value){}\n",
    }
    issues = multifile_surface_blockers(
        "multifile refactor signature across header and cpp",
        before,
        after,
        tmp_path,
        mode="multifile_refactor",
        pending_surfaces={PENDING_CPP_FOLLOWUP},
    )
    assert issues == []


def test_route_forbidden_allows_build_cs_when_request_targets_module():
    route = {
        "forbiddenActions": ["Build.cs-first fix without module evidence"],
        "broadMode": "compile_fix",
    }
    bundle = {
        "files": [
            {
                "path": "Source/Mod/Mod.Build.cs",
                "content": 'PublicDependencyModuleNames.Add("GameplayTags");',
            }
        ],
        "patches": [],
    }
    request = "Add GameplayTags to PublicDependencyModuleNames in Build.cs"
    issues = route_forbidden_action_blockers(route, bundle, request=request, mode="compile_fix")
    assert issues == []


def test_multifile_pattern_hint_includes_callback_pattern():
    from wrapper_evidence import multifile_pattern_hint

    hint = multifile_pattern_hint("Expand callback registration parameter list to match typedef")
    assert "callback" in hint.lower() or "typedef" in hint.lower()

