#!/usr/bin/env python
"""Granular Unreal compile/UHT/link error taxonomy."""

from __future__ import annotations

import re
from typing import Any

# Granular subkinds mapped to broad RAG modes
SUBKIND_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    ("GENERATED_H_MISSING", "reflection_fix", re.compile(r"generated\.h.*not found|include.*\.generated\.h", re.I)),
    ("GENERATED_H_NOT_LAST", "reflection_fix", re.compile(r"generated\.h.*must be the last include", re.I)),
    ("GENERATED_H_DUPLICATE", "reflection_fix", re.compile(r"duplicate.*generated\.h", re.I)),
    ("UHT_REFLECTED_TYPE_IN_NAMESPACE", "reflection_fix", re.compile(r"reflected types cannot be declared in a namespace", re.I)),
    ("UHT_MISSING_BODY_MACRO", "reflection_fix", re.compile(r"GENERATED_BODY|missing.*UCLASS|USTRUCT", re.I)),
    ("BLUEPRINT_NATIVE_EVENT_IMPL_MISSING", "reflection_fix", re.compile(r"_Implementation.*not found|native event", re.I)),
    ("BLUEPRINT_NATIVE_EVENT_DUPLICATE_VIRTUAL", "reflection_fix", re.compile(r"duplicate.*virtual.*_Implementation", re.I)),
    ("MISSING_INCLUDE_OWNER_MODULE", "module_fix", re.compile(r"cannot open include|C1083", re.I)),
    ("PUBLIC_HEADER_PRIVATE_MODULE", "module_fix", re.compile(r"PublicDependencyModuleNames|private header.*public", re.I)),
    ("C1083_MISSING_INCLUDE", "module_fix", re.compile(r"C1083.*cannot open include", re.I)),
    ("LNK_MISSING_CPP_DEFINITION", "link_fix", re.compile(r"LNK2019|unresolved external", re.I)),
    ("LNK_MISSING_MODULE", "link_fix", re.compile(r"LNK.*module|undefined reference", re.I)),
    ("RPC_IMPLEMENTATION_MISSING", "compile_fix", re.compile(r"RPC|Server_|Client_|NetMulticast", re.I)),
    ("ENHANCED_INPUT_BINDING_ERROR", "compile_fix", re.compile(r"EnhancedInput|ETriggerEvent|BindAction", re.I)),
    ("EDITOR_ONLY_INCLUDE_IN_RUNTIME_MODULE", "module_fix", re.compile(r"UnrealEd|Editor.*runtime module", re.I)),
    ("RAW_UOBJECT_MEMBER_WITHOUT_UPROPERTY", "reflection_fix", re.compile(r"UPROPERTY.*UObject|raw pointer.*UObject", re.I)),
    ("CONSTRUCTOR_LIFECYCLE_MISUSE", "compile_fix", re.compile(r"constructor.*CreateDefaultSubobject|FObjectInitializer", re.I)),
]

BROAD_MODES = frozenset({"module_fix", "reflection_fix", "compile_fix", "runtime_debug", "link_fix"})


def classify_error_subkind(message: str, error_code: str = "") -> tuple[str, str]:
    """Return (error_subkind, broad_mode)."""
    combined = f"{error_code} {message}"
    for subkind, broad, pattern in SUBKIND_PATTERNS:
        if pattern.search(combined):
            return subkind, broad
    # Fallback broad classification
    value = combined.lower()
    if "generated.h" in value or "unrealheadertool" in value or "uht" in value:
        return "UHT_GENERIC", "reflection_fix"
    if "cannot open include" in value or "c1083" in value or "build.cs" in value:
        return "INCLUDE_GENERIC", "module_fix"
    if "lnk" in value or "unresolved external" in value:
        return "LINK_GENERIC", "link_fix"
    if "ensure" in value or "assert" in value or "crash" in value:
        return "RUNTIME_GENERIC", "runtime_debug"
    return "COMPILE_GENERIC", "compile_fix"


def enrich_error_metadata(metadata: dict[str, Any], message: str, error_code: str = "") -> dict[str, Any]:
    subkind, broad = classify_error_subkind(message, error_code or str(metadata.get("error_code") or ""))
    out = dict(metadata)
    out["error_subkind"] = subkind
    out["error_kind"] = broad if broad in BROAD_MODES else metadata.get("error_kind", broad)
    return out


def mode_from_error_kind(error_kind: str) -> str:
    if error_kind in BROAD_MODES:
        return error_kind if error_kind != "link_fix" else "compile_fix"
    return "compile_fix"


def route_error_action(message: str, error_code: str = "") -> dict[str, Any]:
    """Return deterministic routing hints for the first actionable build error."""
    subkind, broad = classify_error_subkind(message, error_code)
    route: dict[str, Any] = {
        "errorSubkind": subkind,
        "broadMode": broad,
        "requiredReads": [],
        "preferredRagModes": [],
        "allowedPatchTargets": [],
        "forbiddenActions": [],
        "notes": [],
    }

    def set_route(
        *,
        broad_mode: str | None = None,
        reads: list[str],
        rag: list[str],
        targets: list[str],
        forbidden: list[str] | None = None,
        notes: list[str] | None = None,
    ) -> dict[str, Any]:
        route["broadMode"] = broad_mode or route["broadMode"]
        route["requiredReads"] = reads
        route["preferredRagModes"] = rag
        route["allowedPatchTargets"] = targets
        route["forbiddenActions"] = forbidden or []
        route["notes"] = notes or []
        return route

    if subkind.startswith("GENERATED_H") or subkind.startswith("UHT_") or subkind == "UHT_GENERIC":
        return set_route(
            broad_mode="reflection_fix",
            reads=["failing header", "owner Build.cs if module ambiguity exists"],
            rag=["reflection_fix", "compile_fix"],
            targets=["failing header"],
            forbidden=["unrelated cpp rewrite", "broad refactor"],
            notes=["Fix the first UHT/reflection surface before editing implementation files."],
        )

    if subkind in {"C1083_MISSING_INCLUDE", "MISSING_INCLUDE_OWNER_MODULE", "INCLUDE_GENERIC"}:
        return set_route(
            broad_mode="module_fix",
            reads=["failing source/header", "owner Build.cs", "include owner/module graph"],
            rag=["module_fix", "compile_fix"],
            targets=["owner Build.cs", "failing source/header only if include is actually missing"],
            forbidden=["editing unrelated files", "explaining dependency without Build.cs patch"],
            notes=["Use module_resolver or symbol graph before adding dependencies."],
        )

    if subkind == "LNK_MISSING_CPP_DEFINITION":
        return set_route(
            broad_mode="compile_fix",
            reads=["header declaration", "cpp definition file", "owner module Build.cs only if module issue is suspected"],
            rag=["compile_fix"],
            targets=["matching cpp/header"],
            forbidden=["Build.cs-first fix without module evidence"],
            notes=["Prefer adding the missing definition or correcting signature mismatch."],
        )

    if subkind == "ENHANCED_INPUT_BINDING_ERROR":
        return set_route(
            broad_mode="module_fix",
            reads=["failing file", "owner Build.cs"],
            rag=["module_fix", "compile_fix"],
            targets=["owner Build.cs", "failing file"],
            forbidden=[],
            notes=["Enhanced Input errors often require EnhancedInput module evidence."],
        )

    if subkind == "EDITOR_ONLY_INCLUDE_IN_RUNTIME_MODULE":
        return set_route(
            broad_mode="module_fix",
            reads=["failing file", "module Build.cs"],
            rag=["module_fix", "compile_fix"],
            targets=["failing file", "module boundary files"],
            forbidden=["adding UnrealEd to runtime module as default fix"],
            notes=["Prefer moving editor-only code behind editor modules or guards."],
        )

    if broad == "link_fix":
        return set_route(
            broad_mode="link_fix",
            reads=["referenced declaration", "candidate cpp definitions", "owner Build.cs if module evidence exists"],
            rag=["compile_fix"],
            targets=["matching cpp/header", "owner Build.cs only with module evidence"],
            forbidden=["blind Build.cs dependency changes"],
        )

    return set_route(
        broad_mode=broad if broad in BROAD_MODES else "compile_fix",
        reads=["failing file or build log excerpt"],
        rag=[mode_from_error_kind(broad)],
        targets=["failing file"],
        forbidden=["broad refactor"],
        notes=["Classify a more specific subkind if the next build repeats this error."],
    )
