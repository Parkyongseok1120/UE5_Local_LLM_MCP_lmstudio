#!/usr/bin/env python3
"""Subkind policy registry for routing, guards, autofix, and retry hints."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

GuardFn = Callable[..., list[str]]
EvidenceFn = Callable[..., str]


@dataclass(frozen=True)
class SubkindPolicy:
    broad_mode: str = "compile_fix"
    eval_tier: str = ""
    guard_names: tuple[str, ...] = ()
    evidence_names: tuple[str, ...] = ()
    autofix_names: tuple[str, ...] = ()
    retry_hint_codes: tuple[str, ...] = ()
    model_only: bool = False


SUBKIND_POLICY: dict[str, SubkindPolicy] = {
    "BLUEPRINT_NATIVE_EVENT_IMPL_MISSING": SubkindPolicy(
        broad_mode="reflection_fix",
        eval_tier="uht_reflection",
        guard_names=("blueprint_native_event_impl_fix_context", "blueprint_native_event_dual_surface"),
        autofix_names=("blueprint_native_event_impl",),
        retry_hint_codes=("BLUEPRINT_NATIVE_EVENT_IMPL_MISSING",),
    ),
    "BLUEPRINT_NATIVE_EVENT_MANUAL_IMPL_DECL": SubkindPolicy(
        broad_mode="reflection_fix",
        eval_tier="uht_reflection",
        guard_names=("blueprint_native_event_impl_fix_context", "blueprint_native_event_dual_surface"),
        autofix_names=("blueprint_native_event_impl",),
        retry_hint_codes=("BLUEPRINT_NATIVE_EVENT_MANUAL_IMPL_DECL",),
    ),
    "BLUEPRINT_IMPLEMENTABLE_EVENT_INVALID_IMPL": SubkindPolicy(
        broad_mode="reflection_fix",
        eval_tier="uht_reflection",
        autofix_names=("blueprint_implementable_event_strip",),
        retry_hint_codes=("BLUEPRINT_IMPLEMENTABLE_EVENT_INVALID_IMPL",),
    ),
    "CPP_RETURN_TYPE_MISMATCH": SubkindPolicy(
        broad_mode="multifile_refactor",
        eval_tier="multifile_refactor",
        autofix_names=("cpp_return_type_sync",),
        retry_hint_codes=("CPP_RETURN_TYPE_MISMATCH",),
    ),
    "CALLBACK_FUNCTION_POINTER_MISMATCH": SubkindPolicy(
        broad_mode="multifile_refactor",
        eval_tier="multifile_refactor",
        autofix_names=("callback_expand",),
        retry_hint_codes=("CALLBACK_FUNCTION_POINTER_MISMATCH",),
    ),
    "DELEGATE_BROADCAST_SIGNATURE_MISMATCH": SubkindPolicy(
        broad_mode="compile_fix",
        eval_tier="single_file_compile_fix",
        guard_names=("delegate_broadcast_callsite_fix_context",),
        autofix_names=("delegate_broadcast",),
        retry_hint_codes=("DELEGATE_BROADCAST_SIGNATURE_MISMATCH",),
    ),
    "LNK_MISSING_CPP_DEFINITION": SubkindPolicy(
        broad_mode="compile_fix",
        eval_tier="single_file_compile_fix",
        autofix_names=("blueprint_native_event_impl",),
        retry_hint_codes=("CPP_DEFINITION_MISSING",),
    ),
    "COMPONENT_REGISTRATION_MISSING_INCLUDE": SubkindPolicy(
        broad_mode="compile_fix",
        eval_tier="single_file_compile_fix",
        autofix_names=("component_include",),
        retry_hint_codes=("COMPONENT_REGISTRATION_INCLUDE_MISSING",),
    ),
    "EDITOR_ONLY_INCLUDE_IN_RUNTIME_MODULE": SubkindPolicy(
        broad_mode="editor_runtime_fix",
        eval_tier="editor_runtime_boundary",
        autofix_names=("editor_runtime_guard",),
        retry_hint_codes=("EDITOR_ONLY_INCLUDE_IN_RUNTIME_MODULE",),
    ),
    "INTERFACE_IMPLEMENTER_MISMATCH": SubkindPolicy(
        broad_mode="multifile_refactor",
        eval_tier="multifile_refactor",
        model_only=True,
    ),
    "PUBLIC_HEADER_PRIVATE_MODULE": SubkindPolicy(
        broad_mode="module_fix",
        eval_tier="module_fix",
        autofix_names=("build_module",),
        retry_hint_codes=("POSSIBLE_MISSING_MODULE",),
    ),
    "RPC_IMPLEMENTATION_MISSING": SubkindPolicy(
        broad_mode="reflection_fix",
        eval_tier="uht_reflection",
        model_only=True,
    ),
}


def policy_for_subkind(subkind: str) -> SubkindPolicy | None:
    return SUBKIND_POLICY.get(str(subkind or "").strip())


def effective_mode_for_case(case: dict[str, Any]) -> str:
    mode = str(case.get("mode") or "compile_fix")
    tier = str(case.get("evalTier") or "").strip()
    subkind = str(case.get("expectedErrorSubkind") or "")
    policy = policy_for_subkind(subkind)
    if tier == "multifile_refactor" and mode == "compile_fix":
        return "multifile_refactor"
    if tier == "editor_runtime_boundary" and mode == "compile_fix":
        return "editor_runtime_fix"
    if policy and policy.broad_mode and mode == "compile_fix" and policy.eval_tier == tier:
        return policy.broad_mode
    return mode


def validate_policy_coverage(case: dict[str, Any]) -> list[str]:
    subkind = str(case.get("expectedErrorSubkind") or "").strip()
    if not subkind:
        return []
    policy = policy_for_subkind(subkind)
    if policy is None:
        # Subkinds without a SUBKIND_POLICY entry are routed by error_taxonomy
        # directly (e.g. generic compile_fix); absence is not a coverage gap.
        return []
    if policy.model_only:
        return []
    if not policy.autofix_names and not policy.guard_names:
        return [f"policy for {subkind} has no autofix or guard coverage"]
    return []
