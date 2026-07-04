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
    no_change_blockers,
    request_forbids_build_cs_first,
    request_requests_build_cs_fix,
    summarize_project_state,
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
