#!/usr/bin/env python
"""Tests for compile-fix wrapper guardrails."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lmstudio_unreal_wrapper import (  # noqa: E402
    Finding,
    edit_scope_blockers,
    no_change_blockers,
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
    findings = [
        Finding(
            "warning",
            "Source/CompileFixSig/Private/HackComponent.cpp",
            3,
            "CPP_FUNCTION_NOT_DECLARED_IN_HEADER",
            "UHackComponent::DoWork is implemented in .cpp but was not found in the matching header.",
        )
    ]

    issues = no_change_blockers(request, fixture, findings)

    assert any(".cpp/header mismatch" in issue for issue in issues)
