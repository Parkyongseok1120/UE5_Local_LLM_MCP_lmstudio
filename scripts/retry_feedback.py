#!/usr/bin/env python3
"""Retry and static-validation feedback assembly for compile-fix loops."""

from __future__ import annotations

import re
from typing import Any

from unreal_static_validate import Finding

STATIC_SIGNATURE_RETRY_HINT = (
    "Static validation still reports CPP_FUNCTION_SIGNATURE_MISMATCH.\n"
    "The previous patch did not resolve the declaration/definition mismatch.\n"
    "Read the exact header declaration and cpp definition again.\n"
    "Patch the smallest signature difference. Do not edit Build.cs unless module evidence exists."
)
DELEGATE_BROADCAST_RETRY_HINT = (
    "The delegate Broadcast call still does not match the declared payload.\n"
    "Patch the exact .Broadcast(...) callsite in the matching cpp file.\n"
    "Copy oldText from the current project state summary; do not invent alternate member names."
)
LNK_MISSING_DEFINITION_RETRY_HINT = (
    "The unresolved external / missing cpp definition remains.\n"
    "Add or correct the missing implementation in the matching cpp file.\n"
    "Do not edit Build.cs unless module evidence exists."
)
BLUEPRINT_NATIVE_EVENT_RETRY_HINT = (
    "BlueprintNativeEvent fixes belong in the matching .cpp as Class::Event_Implementation().\n"
    "Do not add manual _Implementation declarations to the header."
)
CPP_RETURN_TYPE_RETRY_HINT = (
    "Static validation reports return-type drift between header and .cpp.\n"
    "Update the header UPROPERTY/UFUNCTION declaration and matching .cpp definition together."
)
MULTIFILE_CALLSITE_RETRY_HINT = (
    "Static validation reports multifile callsite drift.\n"
    "Update declaration, definition, and consumer callsites in one coherent patch."
)
GENERATED_H_RETRY_HINT = (
    "Static validation reports generated.h ordering or inclusion issues.\n"
    "Keep .generated.h last among includes and ensure it is present for UCLASS headers."
)
POSSIBLE_MISSING_MODULE_HINT = (
    "Static validation suspects a missing Build.cs module dependency.\n"
    "Inspect *.Build.cs and add the module to PublicDependencyModuleNames when public headers need it."
)
COMPONENT_REGISTRATION_INCLUDE_HINT = (
    "Static validation reports a missing project component include at a complete-type use site.\n"
    "Add the exact project-relative #include to the referencing file shown in the finding.\n"
    "Do not edit Build.cs when the symbol owner module matches the consumer module."
)


def static_validation_retry_feedback(
    findings: list[Finding] | None,
    route: dict[str, Any],
    *,
    build_output: str = "",
) -> str:
    codes = {str(finding.code) for finding in (findings or [])}
    if "INTERFACE_IMPLEMENTER_SIGNATURE_MISMATCH" in codes:
        return (
            "Static validation reports interface implementer signature drift.\n"
            "Read the interface header and update implementer header/cpp together in one patch."
        )
    if "CALLBACK_FUNCTION_POINTER_MISMATCH" in codes:
        return (
            "Static validation reports callback/function-pointer signature drift.\n"
            "Update the handler header, cpp definition, and registration assignment together."
        )
    if "CPP_RETURN_TYPE_MISMATCH" in codes:
        return CPP_RETURN_TYPE_RETRY_HINT
    if "CPP_FUNCTION_SIGNATURE_MISMATCH" in codes:
        return STATIC_SIGNATURE_RETRY_HINT
    if "DELEGATE_BROADCAST_SIGNATURE_MISMATCH" in codes:
        return DELEGATE_BROADCAST_RETRY_HINT
    if codes & {
        "BLUEPRINT_NATIVE_EVENT_IMPL_MISSING",
        "BLUEPRINT_NATIVE_EVENT_MANUAL_IMPL_DECL",
        "BLUEPRINT_IMPLEMENTABLE_EVENT_INVALID_IMPL",
    }:
        return BLUEPRINT_NATIVE_EVENT_RETRY_HINT
    if "MULTIFILE_CALLSITE_DRIFT" in codes:
        return MULTIFILE_CALLSITE_RETRY_HINT
    if codes & {"GENERATED_H_NOT_LAST", "GENERATED_H_AFTER_TYPE", "GENERATED_H_MISSING", "GENERATED_H_DUPLICATE"}:
        return GENERATED_H_RETRY_HINT
    if "POSSIBLE_MISSING_MODULE" in codes:
        return POSSIBLE_MISSING_MODULE_HINT
    for finding in findings or []:
        if finding.code == "COMPONENT_REGISTRATION_INCLUDE_MISSING":
            return finding.message
    if route.get("errorSubkind") == "COMPONENT_REGISTRATION_MISSING_INCLUDE":
        return COMPONENT_REGISTRATION_INCLUDE_HINT
    if "CPP_DEFINITION_MISSING" in codes and route.get("errorSubkind") != "C1083_MISSING_INCLUDE":
        return LNK_MISSING_DEFINITION_RETRY_HINT
    if route.get("errorSubkind") == "DELEGATE_BROADCAST_SIGNATURE_MISMATCH":
        return DELEGATE_BROADCAST_RETRY_HINT
    if route.get("errorSubkind") == "LNK_MISSING_CPP_DEFINITION":
        if re.search(r"\bLNK2019\b|unresolved external|missing cpp definition", build_output or "", re.I):
            return LNK_MISSING_DEFINITION_RETRY_HINT
    return ""


def retry_feedback_block(recommendation: dict[str, Any]) -> str:
    lines: list[str] = []
    if recommendation.get("sameErrorRepeated"):
        lines.append(
            "The previous attempt repeated the same error. Do not repeat the same patch. "
            "Reclassify the root cause and read the required files from errorRoute before editing."
        )
    repeat_count = int(recommendation.get("sameErrorRepeatCount") or 0)
    if repeat_count >= 2:
        lines.append(
            f"Same error repeated {repeat_count} times. Change patch target and gather broader evidence before editing."
        )
    if recommendation.get("noOpEdit"):
        lines.append("The previous attempt produced no effective file change. Do not submit the same patch again.")
    for hint in recommendation.get("requiredPromptHints") or []:
        if hint not in lines:
            lines.append(str(hint))
    return "\n".join(lines)
