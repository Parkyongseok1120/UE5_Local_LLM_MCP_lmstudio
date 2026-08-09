#!/usr/bin/env python
"""Validate Unreal API names in a drafted code sketch against the RAG symbol index.

Small local models frequently emit plausible-but-nonexistent Unreal APIs when
asked for a "code sketch / 시안" in plain chat. This validator extracts the
Unreal-style symbols from a draft, checks each one against the local symbol
index (positive existence check) and a curated denylist (negative check), and
returns a per-symbol verdict so the model can downgrade or remove unverified
APIs before presenting compile-ready code.

Verdicts:
- ``known_bad``: symbol matches the invented-API / wrong-lifecycle denylist.
- ``verified``: an exact symbol match exists in the index or project graph.
- ``weak``: only a graph prefix or owner-less match exists; treat as needs-confirmation.
- ``unverified``: no index evidence found; must not be presented as real API.

This tool never writes files and never runs a build. It is evidence only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

# Unreal C++ types carry a conventional U/A/F/S/I prefix. Broad suffix-only
# matching (``*Actor``, ``*Component``) also matched ordinary member names such
# as ``BoardActor`` and turned assignments into nonexistent API claims.
# The character immediately after the Unreal prefix pair must be alphanumeric.
# This keeps enum values such as ``IE_Pressed`` out of the type-symbol gate.
SYMBOL_RES = (re.compile(r"\b[AUFSI][A-Z][A-Za-z0-9][A-Za-z0-9_]+\b"),)
# Method/member calls the model asserts exist, e.g. Player->SetRestoreState(...).
MEMBER_CALL_RE = re.compile(r"(?:->|\.)\s*([A-Za-z_][A-Za-z0-9_]{2,})\s*\(")
MEMBER_CALL_CLAIM_RE = re.compile(
    r"\b(?P<receiver>[A-Za-z_][A-Za-z0-9_]*)\s*(?P<operator>->|\.)\s*"
    r"(?P<member>[A-Za-z_][A-Za-z0-9_]{2,})\s*\("
)
STATIC_CALL_CLAIM_RE = re.compile(
    r"\b(?P<receiver>[AUFSI][A-Z][A-Za-z0-9_]*)\s*::\s*"
    r"(?P<member>[A-Za-z_][A-Za-z0-9_]{2,})\s*\("
)
# A qualified out-of-class function definition contains the same ``Type::Name(``
# token sequence as a static call.  Mask these definition spans so a newly
# implemented project method is not falsely treated as an API invocation.
QUALIFIED_FUNCTION_DEFINITION_RE = re.compile(
    r"(?m)^[ \t]*(?:(?:[A-Za-z_][A-Za-z0-9_:<>,~]*|[*&]+)[ \t]+)*"
    r"(?P<receiver>[AUFSI][A-Z][A-Za-z0-9_]*)\s*::\s*"
    r"(?P<member>~?[A-Za-z_][A-Za-z0-9_]*)\s*\([^;{}]*\)\s*"
    r"(?:(?:const|override|final|noexcept(?:\s*\([^)]*\))?|&&?)[ \t]*)*"
    r"(?:\r?\n[ \t]*)?\{"
)
VARIABLE_TYPE_RE = re.compile(
    r"\b(?P<type>(?:[AUFSI][A-Z][A-Za-z0-9_:]*|bool|u?int(?:8|16|32|64)|float|double))"
    r"(?:\s*[*&]\s*|\s+)"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b"
)
TEMPLATE_VARIABLE_TYPE_RE = re.compile(
    r"\b(?P<wrapper>TObjectPtr|TWeakObjectPtr|TSoftObjectPtr|TSubclassOf|TSubobjectPtr)\s*<\s*"
    r"(?:(?:class|struct)\s+)?"
    r"(?P<type>[AUFSI][A-Z][A-Za-z0-9_:]*)\s*>\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b"
)
CONTAINER_VARIABLE_TYPE_RE = re.compile(
    r"\b(?P<type>TArray|TMap|TSet)\s*<[^;{}\n]+>\s*"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b"
)
LOCAL_TYPE_DECL_RE = re.compile(
    r"\b(?:class|struct|enum(?:\s+class)?)\s+(?:[A-Z0-9_]+_API\s+)?([AUFSI][A-Z][A-Za-z0-9_]{2,})\b"
)
LOCAL_INHERITANCE_SKETCH_RE = re.compile(
    r"(?m)^\s*([AUFSI][A-Z][A-Za-z0-9_]{2,})\s*:\s*(?:public\s+)?"
    r"[AUFSI][A-Z][A-Za-z0-9_]{2,}\s*$"
)
LOCAL_DELEGATE_DECL_RE = re.compile(
    r"\bDECLARE_(?:DYNAMIC_)?MULTICAST_DELEGATE(?:_[A-Za-z]+Params?)?\s*\(\s*([A-Za-z_]\w*)"
)

# UnrealBuildTool exposes these C# collections in ``*.Build.cs`` files. Their
# collection methods are not Unreal C++ APIs and therefore cannot be resolved
# through the engine/project symbol index used by this validator. Keep them out
# of API-claim classification; Build.cs syntax and module names are validated by
# the dedicated Build.cs parser and the Unreal build itself.
UNREAL_BUILD_TOOL_COLLECTION_RECEIVERS = {
    "AdditionalBundleResources",
    "CircularlyReferencedDependentModules",
    "DynamicallyLoadedModuleNames",
    "ExternalDependencies",
    "InternalIncludePaths",
    "PrivateDefinitions",
    "PrivateDependencyModuleNames",
    "PrivateIncludePathModuleNames",
    "PrivateIncludePaths",
    "PublicAdditionalLibraries",
    "PublicAdditionalShadowFiles",
    "PublicDefinitions",
    "PublicDelayLoadDLLs",
    "PublicDependencyModuleNames",
    "PublicFrameworks",
    "PublicIncludePathModuleNames",
    "PublicIncludePaths",
    "PublicSystemIncludePaths",
    "PublicSystemLibraries",
    "PublicWeakFrameworks",
    "RuntimeDependencies",
}

# Identifiers that are ubiquitous UE building blocks; skipping them keeps the
# report focused on the risky, request-specific symbols.
COMMON_SAFE = {
    "UObject", "AActor", "UActorComponent", "USceneComponent", "UClass",
    "FString", "FName", "FText", "FVector", "FVector2D", "FRotator", "FTransform",
    "FCollisionQueryParams", "SCENE_QUERY_STAT",
    "FMath", "FQuat", "FAttachmentTransformRules", "FObjectInitializer", "INDEX_NONE",
    "UStaticMeshComponent", "UInstancedStaticMeshComponent",
    "UWorld", "APawn", "ACharacter", "APlayerController", "AGameModeBase", "AGameStateBase",
    "UWorldSubsystem", "UGameInstanceSubsystem", "UEngineSubsystem",
    "UCLASS", "USTRUCT", "UENUM", "UFUNCTION", "UPROPERTY", "UINTERFACE",
}

# This is an input safety boundary, not a symbol-count policy.  Large feature
# drafts must be split into the active implementation slice before validation.
MAX_SKETCH_CHARS = 12_000
EXACT_LOOKUP_BATCH_SIZE = 400


def _mask_comments_and_literals(text: str) -> str:
    """Replace comments and quoted literal contents while preserving newlines.

    The symbol validator judges executable/declarative code claims. Natural-
    language comments and string payloads frequently mention proposed class
    names and must not become fail-closed API claims.
    """

    source = text or ""
    output = list(source)
    index = 0
    state = "code"
    while index < len(source):
        current = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""

        if state == "code":
            if current == "/" and following == "/":
                output[index] = output[index + 1] = " "
                index += 2
                state = "line_comment"
                continue
            if current == "/" and following == "*":
                output[index] = output[index + 1] = " "
                index += 2
                state = "block_comment"
                continue
            if current == '"':
                output[index] = " "
                index += 1
                state = "double_quote"
                continue
            if current == "'":
                output[index] = " "
                index += 1
                state = "single_quote"
                continue
            index += 1
            continue

        if current in "\r\n":
            if state == "line_comment":
                state = "code"
            index += 1
            continue

        output[index] = " "
        if state == "block_comment":
            if current == "*" and following == "/":
                output[index + 1] = " "
                index += 2
                state = "code"
                continue
        elif state in {"double_quote", "single_quote"}:
            quote = '"' if state == "double_quote" else "'"
            if current == "\\" and following:
                output[index + 1] = " "
                index += 2
                continue
            if current == quote:
                state = "code"
        index += 1
    return "".join(output)


def extract_symbols(text: str) -> list[str]:
    text = _mask_comments_and_literals(text)
    found: list[str] = []
    for pattern in SYMBOL_RES:
        for match in pattern.finditer(text or ""):
            sym = match.group(0)
            if sym not in found:
                found.append(sym)
    return found


def extract_member_calls(text: str) -> list[str]:
    text = _mask_comments_and_literals(text)
    found: list[str] = []
    for match in MEMBER_CALL_RE.finditer(text or ""):
        name = match.group(1)
        if name and name not in found:
            found.append(name)
    return found


SAFE_TEMPLATE_WRAPPER_MEMBERS = {
    "TObjectPtr": {"Get"},
    "TWeakObjectPtr": {"Get", "IsValid", "Reset", "IsStale", "IsExplicitlyNull"},
    "TSoftObjectPtr": {"Get", "IsValid", "IsNull", "IsPending", "LoadSynchronous", "Reset"},
    "TSubclassOf": {"Get"},
}
# ``AddDynamic`` and its siblings are Unreal delegate helper macros expanded at
# the call site, not ordinary methods that appear as exact symbols in the
# engine index.  Nested delegate properties such as
# ``Button->OnClicked.AddDynamic(...)`` also leave the lightweight receiver
# inference with only ``OnClicked``.  Treat only these dynamic-delegate macros
# as syntax-level primitives; the compiler/static validator still checks that
# the receiver is actually a compatible delegate and that the bound function
# has the required signature.  Do not include ``AddLambda`` here: dynamic
# multicast delegates intentionally do not support it.
SAFE_DYNAMIC_DELEGATE_MACRO_MEMBERS = {
    "AddDynamic",
    "AddUniqueDynamic",
    "Broadcast",
    "IsAlreadyBound",
    "RemoveDynamic",
}
# These names are the stable core operations shared by Unreal's standard
# containers. A concise implementation sketch often references an existing
# class field without repeating its TArray/TMap/TSet declaration, and the
# declaration context can legitimately omit that field while a header slice is
# still being drafted. Treating these receiver-less calls as engine API claims
# creates a pointless symbol-lookup loop (``Add`` -> ``Num`` -> ``Reset``).
# C++ static validation remains responsible for proving that the receiver is
# declared and supports the operation.
SAFE_UNTYPED_CONTAINER_MEMBERS = {
    "Add",
    "Contains",
    "Find",
    "FindOrAdd",
    "IsValidIndex",
    "Num",
    "Remove",
    "Reset",
}
SAFE_RECEIVER_MEMBER_CLAIMS = {
    # Stable engine helpers whose declarations live in broad engine headers.
    # Exact-name RAG lookup can otherwise return several owner-less rows and
    # incorrectly downgrade these routine calls to weak.
    "FHitResult": {"GetActor"},
    "FCollisionQueryParams": {"AddIgnoredActor"},
    "FMath": {"Abs", "Clamp", "Max", "Min", "RoundToInt"},
    "FTransform": {"SetLocation", "SetRotation", "SetScale3D"},
    "FVector": {"GetSafeNormal"},
    "TArray": {"Add", "Find", "IsValidIndex", "Num", "Reset"},
    "TMap": {"Add", "Contains", "Find", "FindOrAdd", "Num", "Remove", "Reset"},
    "TSet": {"Add", "Contains", "Num", "Remove", "Reset"},
    # These are inherited component APIs.  The lightweight project graph often
    # indexes the declaring base class only, which previously downgraded valid
    # calls on UStaticMeshComponent/UInstancedStaticMeshComponent receivers to
    # weak owner-less matches and forced a pointless lookup/retry cycle.
    "USceneComponent": {"AttachToComponent", "SetVisibility"},
    "UStaticMeshComponent": {
        "AttachToComponent",
        "SetCollisionEnabled",
        "SetVisibility",
    },
    "UInstancedStaticMeshComponent": {
        "AddInstance",
        "AttachToComponent",
        "ClearInstances",
        "GetInstanceCount",
        "GetInstanceTransform",
        "GetStaticMesh",
        "SetCollisionEnabled",
        "SetVisibility",
    },
    "UInputComponent": {"BindAction", "BindAxis"},
    "APlayerController": {
        "DeprojectScreenPositionToWorld",
        "GetHitResultAtScreenPosition",
    },
    "UWorld": {"IsGameWorld", "LineTraceSingleByChannel"},
    # Verified against UE 5.8 GameplayStatics.h. Reflected-function rows in the
    # local RAG index do not currently retain qualified owners, so these exact
    # static calls would otherwise be downgraded to weak despite header proof.
    "UGameplayStatics": {
        "DeprojectScreenToWorld",
        "GetGameState",
        "GetPlayerController",
    },
}

# Common inherited component fields are not declared in a local .cpp sketch,
# so their receiver type cannot be learned by the declaration regex alone.
KNOWN_RECEIVER_NAME_TYPES = {
    "InputComponent": "UInputComponent",
    "RootComponent": "USceneComponent",
}


def extract_member_call_claims(
    text: str,
    declaration_context: str = "",
) -> list[dict[str, str]]:
    text = _mask_comments_and_literals(text)
    context = _mask_comments_and_literals(declaration_context)
    variable_types: dict[str, str] = {}
    wrapper_types: dict[str, str] = {}
    # Paired declarations establish field types for a .cpp sketch. Local
    # declarations in the sketch intentionally win when names shadow fields.
    for source in (context, text):
        for match in VARIABLE_TYPE_RE.finditer(source or ""):
            variable_types[match.group("name")] = match.group("type").split("::")[-1]
        for match in TEMPLATE_VARIABLE_TYPE_RE.finditer(source or ""):
            variable_types[match.group("name")] = match.group("type").split("::")[-1]
            wrapper_types[match.group("name")] = match.group("wrapper")
        for match in CONTAINER_VARIABLE_TYPE_RE.finditer(source or ""):
            variable_types[match.group("name")] = match.group("type")

    claims: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for match in MEMBER_CALL_CLAIM_RE.finditer(text or ""):
        receiver = match.group("receiver")
        member = match.group("member")
        if receiver in UNREAL_BUILD_TOOL_COLLECTION_RECEIVERS:
            continue
        if member in SAFE_DYNAMIC_DELEGATE_MACRO_MEMBERS:
            continue
        wrapper_type = wrapper_types.get(receiver, "")
        if (
            match.group("operator") == "."
            and member in SAFE_TEMPLATE_WRAPPER_MEMBERS.get(wrapper_type, set())
        ):
            continue
        receiver_type = variable_types.get(
            receiver,
            KNOWN_RECEIVER_NAME_TYPES.get(receiver, ""),
        )
        if not receiver_type and member in SAFE_UNTYPED_CONTAINER_MEMBERS:
            continue
        if member in SAFE_RECEIVER_MEMBER_CLAIMS.get(receiver_type, set()):
            continue
        key = (receiver, receiver_type, member)
        if key not in seen:
            seen.add(key)
            claims.append(
                {
                    "receiver": receiver,
                    "receiverType": receiver_type,
                    "member": member,
                    "callKind": "member",
                }
            )
    definition_claim_starts = {
        match.start("receiver")
        for match in QUALIFIED_FUNCTION_DEFINITION_RE.finditer(text or "")
    }
    for match in STATIC_CALL_CLAIM_RE.finditer(text or ""):
        if match.start("receiver") in definition_claim_starts:
            continue
        receiver_type = match.group("receiver").split("::")[-1]
        member = match.group("member")
        if member in SAFE_RECEIVER_MEMBER_CLAIMS.get(receiver_type, set()):
            continue
        key = (receiver_type, receiver_type, member)
        if key not in seen:
            seen.add(key)
            claims.append(
                {
                    "receiver": receiver_type,
                    "receiverType": receiver_type,
                    "member": member,
                    "callKind": "static",
                }
            )
    return claims


def _call_argument_count(text: str, open_paren: int) -> int | None:
    if open_paren < 0 or open_paren >= len(text) or text[open_paren] != "(":
        return None
    paren = bracket = brace = angle = 0
    has_token = False
    commas = 0
    for char in text[open_paren + 1 :]:
        if char == "(" :
            paren += 1
        elif char == ")":
            if paren:
                paren -= 1
            elif not bracket and not brace:
                return commas + 1 if has_token else 0
        elif char == "[":
            bracket += 1
        elif char == "]" and bracket:
            bracket -= 1
        elif char == "{":
            brace += 1
        elif char == "}" and brace:
            brace -= 1
        elif char == "<":
            angle += 1
        elif char == ">" and angle:
            angle -= 1
        elif char == "," and not paren and not bracket and not brace and not angle:
            commas += 1
        elif not char.isspace():
            has_token = True
    return None


def _call_argument_text(text: str, open_paren: int) -> str | None:
    if open_paren < 0 or open_paren >= len(text) or text[open_paren] != "(":
        return None
    depth = 0
    for index in range(open_paren + 1, len(text)):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            if depth:
                depth -= 1
            else:
                return text[open_paren + 1 : index]
    return None


def _split_cpp_arguments(value: str) -> list[str]:
    parts: list[str] = []
    start = 0
    paren = bracket = brace = angle = 0
    for index, char in enumerate(value):
        if char == "(":
            paren += 1
        elif char == ")" and paren:
            paren -= 1
        elif char == "[":
            bracket += 1
        elif char == "]" and bracket:
            bracket -= 1
        elif char == "{":
            brace += 1
        elif char == "}" and brace:
            brace -= 1
        elif char == "<":
            angle += 1
        elif char == ">" and angle:
            angle -= 1
        elif char == "," and not paren and not bracket and not brace and not angle:
            parts.append(value[start:index].strip())
            start = index + 1
    tail = value[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _normalized_cpp_type(value: str) -> str:
    cleaned = re.sub(r"\s*=.*$", "", str(value or "")).strip()
    cleaned = re.sub(r"\b(?:const|volatile|class|struct)\b", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # A declaration parameter ends in its identifier; preserve pointer/reference
    # tokens while dropping only that final name.
    cleaned = re.sub(r"\s+[A-Za-z_][A-Za-z0-9_]*(?:\s*\[[^]]*\])?$", "", cleaned)
    cleaned = re.sub(r"\s*([*&])\s*", r"\1", cleaned)
    return cleaned.strip()


def _project_signature_contract(row: dict[str, Any], symbol: str) -> dict[str, Any] | None:
    path = Path(str(row.get("file_path") or ""))
    line_number = int(row.get("line_start") or 0)
    if not path.is_file() or line_number < 1:
        return None
    try:
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return None
    if line_number > len(lines):
        return None
    source_line = lines[line_number - 1]
    match = re.match(
        rf"^\s*(?P<return>.*?)\b(?:(?:[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)::)?"
        rf"{re.escape(symbol)}\s*\((?P<params>[^)]*)\)",
        source_line,
    )
    if not match:
        return None
    return_type = re.sub(
        r"^(?:(?:virtual|static|inline|constexpr|FORCEINLINE)\s+)+",
        "",
        match.group("return").strip(),
    )
    params = _split_cpp_arguments(match.group("params"))
    if len(params) == 1 and params[0] == "void":
        params = []
    required = sum(1 for param in params if "=" not in param)
    return {
        "returnType": return_type,
        "requiredArgumentCount": required,
        "maximumArgumentCount": len(params),
        "parameterTypes": [_normalized_cpp_type(param) for param in params],
        "source": "project_source_signature",
        "locator": f"{path}:{line_number}",
    }


def _project_contract_rows(
    graph: dict[str, Any] | None,
    claims: list[dict[str, str]],
) -> dict[str, list[dict[str, Any]]]:
    wanted = {
        f"{str(claim.get('receiverType') or '').casefold()}::{str(claim.get('member') or '').casefold()}"
        for claim in claims
        if claim.get("receiverType") and claim.get("member")
    }
    rows: dict[str, list[dict[str, Any]]] = {}
    for raw in (graph or {}).get("symbols") or []:
        if not isinstance(raw, dict) or raw.get("symbol_kind") != "function":
            continue
        symbol = str(raw.get("symbol_name") or "")
        owner = _row_qualified_owner(raw, symbol)
        key = f"{owner.casefold()}::{symbol.casefold()}"
        if key not in wanted:
            continue
        contract = _project_signature_contract(raw, symbol)
        if not contract:
            continue
        decorated = dict(raw)
        decorated["signatures"] = [contract]
        decorated["evidence_source"] = "project_source_signature"
        rows.setdefault(key, []).append(decorated)
    return rows


def _call_contract_issues(
    text: str,
    declaration_context: str,
    rows_by_claim: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    masked = _mask_comments_and_literals(text)
    context = _mask_comments_and_literals(declaration_context)
    variable_types: dict[str, str] = {}
    for source in (context, masked):
        for match in VARIABLE_TYPE_RE.finditer(source or ""):
            variable_types[match.group("name")] = match.group("type").split("::")[-1]
        for match in TEMPLATE_VARIABLE_TYPE_RE.finditer(source or ""):
            variable_types[match.group("name")] = match.group("type").split("::")[-1]

    definition_starts = {
        match.start("receiver")
        for match in QUALIFIED_FUNCTION_DEFINITION_RE.finditer(masked)
    }
    occurrences: list[tuple[re.Match[str], str, str]] = []
    for match in MEMBER_CALL_CLAIM_RE.finditer(masked):
        owner = variable_types.get(
            match.group("receiver"),
            KNOWN_RECEIVER_NAME_TYPES.get(match.group("receiver"), ""),
        )
        if owner:
            occurrences.append((match, owner, match.group("member")))
    for match in STATIC_CALL_CLAIM_RE.finditer(masked):
        if match.start("receiver") not in definition_starts:
            occurrences.append((match, match.group("receiver"), match.group("member")))

    issues: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    definitions = list(QUALIFIED_FUNCTION_DEFINITION_RE.finditer(masked))

    def infer_expression_type(expression: str, position: int) -> str:
        value = expression.strip()
        if value in variable_types:
            return variable_types[value]
        if value == "this":
            enclosing = [item for item in definitions if item.start() <= position]
            return enclosing[-1].group("receiver") if enclosing else ""
        constructed = re.match(r"^(?P<type>[AUFSI][A-Za-z0-9_]*)\s*[({]", value)
        if constructed:
            return constructed.group("type")
        if re.match(r"^[+-]?\d+$", value):
            return "int32"
        if value in {"true", "false"}:
            return "bool"
        return ""

    def base_type(value: str) -> str:
        return _normalized_cpp_type(value).replace("*", "").replace("&", "").strip()
    assignment = re.compile(
        r"(?P<target>[A-Za-z_][A-Za-z0-9_:]*)\s*(?P<pointer>\*)?\s*"
        r"[A-Za-z_][A-Za-z0-9_]*\s*=\s*$"
    )
    for match, owner, symbol in occurrences:
        key = f"{owner.casefold()}::{symbol.casefold()}"
        rows = rows_by_claim.get(key, [])
        contracts = [
            contract
            for row in rows
            for contract in (row.get("signatures") or [])
            if isinstance(contract, dict)
        ]
        if not contracts:
            continue
        argument_count = _call_argument_count(masked, match.end() - 1)
        sources = {
            str(contract.get("source") or "engine_header_exact")
            for contract in contracts
        }
        source_prefix = "PROJECT" if "project_source_signature" in sources else "ENGINE"
        if argument_count is not None and not any(
            int(contract.get("requiredArgumentCount", 0))
            <= argument_count
            <= int(contract.get("maximumArgumentCount", 0))
            for contract in contracts
        ):
            accepted = sorted(
                {
                    (int(contract.get("requiredArgumentCount", 0)), int(contract.get("maximumArgumentCount", 0)))
                    for contract in contracts
                }
            )
            issue_key = (key, "ARGUMENT_COUNT_MISMATCH", str(argument_count))
            if issue_key not in seen:
                seen.add(issue_key)
                issues.append(
                    {
                        "symbol": symbol,
                        "receiverType": owner,
                        "verdict": "known_bad",
                        "errorCode": f"{source_prefix}_ARGUMENT_COUNT_MISMATCH",
                        "evidence": [],
                        "note": (
                            f"{owner}::{symbol} is called with {argument_count} argument(s), "
                            f"but the version-matched header accepts {accepted}."
                        ),
                    }
                )

        argument_text = _call_argument_text(masked, match.end() - 1)
        arguments = _split_cpp_arguments(argument_text or "") if argument_text is not None else []
        actual_types = [infer_expression_type(argument, match.start()) for argument in arguments]
        typed_contracts: list[list[str]] = []
        for contract in contracts:
            raw_types = contract.get("parameterTypes") or contract.get("parameters") or []
            if isinstance(raw_types, list):
                typed_contracts.append([base_type(str(item)) for item in raw_types])
        comparable = bool(actual_types) and any(actual_types) and typed_contracts
        if comparable:
            compatible_contract = any(
                len(expected) == len(actual_types)
                and all(
                    not actual or not wanted or base_type(actual) == wanted
                    for actual, wanted in zip(actual_types, expected)
                )
                for expected in typed_contracts
            )
            if not compatible_contract:
                mismatch_pairs = [
                    f"arg{index + 1}={actual or 'unknown'} expected {expected}"
                    for expected_types in typed_contracts[:1]
                    for index, (actual, expected) in enumerate(zip(actual_types, expected_types))
                    if actual and expected and base_type(actual) != expected
                ]
                if mismatch_pairs:
                    issue_key = (key, "PARAMETER_TYPE_MISMATCH", "|".join(actual_types))
                    if issue_key not in seen:
                        seen.add(issue_key)
                        issues.append(
                            {
                                "symbol": symbol,
                                "receiverType": owner,
                                "verdict": "known_bad",
                                "errorCode": f"{source_prefix}_PARAMETER_TYPE_MISMATCH",
                                "evidence": [],
                                "note": (
                                    f"{owner}::{symbol} argument types do not match the "
                                    f"source declaration: {', '.join(mismatch_pairs)}."
                                ),
                            }
                        )

        prefix = masked[max(0, match.start() - 160) : match.start()]
        assigned = assignment.search(prefix)
        if not assigned:
            continue
        target_type = assigned.group("target")
        target_pointer = bool(assigned.group("pointer"))
        return_contracts = {
            (
                _normalized_cpp_type(str(contract.get("returnType") or "")).replace("*", ""),
                "*" in _normalized_cpp_type(str(contract.get("returnType") or "")),
            )
            for contract in contracts
            if str(contract.get("returnType") or "").strip()
        }
        incompatible = return_contracts and all(
            (
                returned_pointer != target_pointer
                or (
                    returned != target_type
                    and (not returned or not target_type or returned[0] != target_type[0])
                )
            )
            for returned, returned_pointer in return_contracts
        )
        if incompatible:
            displayed_returns = sorted(
                f"{name}{'*' if pointer else ''}"
                for name, pointer in return_contracts
            )
            issue_key = (key, "RETURN_TYPE_MISMATCH", target_type)
            if issue_key not in seen:
                seen.add(issue_key)
                issues.append(
                    {
                        "symbol": symbol,
                        "receiverType": owner,
                        "verdict": "known_bad",
                        "errorCode": f"{source_prefix}_RETURN_TYPE_MISMATCH",
                        "evidence": [],
                        "note": (
                            f"{owner}::{symbol} returns "
                            f"{displayed_returns}, "
                            f"which cannot be assigned directly to unrelated "
                            f"{target_type}{'*' if target_pointer else ''}."
                        ),
                    }
                )
    return issues


def _project_declaration_contract_issues(
    text: str,
    graph: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    class_macros: dict[str, set[str]] = {}
    bases: dict[str, str] = {}
    for row in (graph or {}).get("symbols") or []:
        if not isinstance(row, dict) or row.get("symbol_kind") not in {"class", "struct"}:
            continue
        name = str(row.get("symbol_name") or "")
        macro = str(row.get("api_macro") or "")
        base = str(row.get("base_class") or "")
        if name and macro:
            class_macros.setdefault(name.casefold(), set()).add(macro)
        if name and base:
            bases[name.casefold()] = base

    for match in re.finditer(
        r"\b(?:class|struct)\s+(?P<macro>[A-Z][A-Z0-9_]*_API)\s+"
        r"(?P<name>[AUFSI][A-Za-z0-9_]*)\b",
        _mask_comments_and_literals(text),
    ):
        expected = class_macros.get(match.group("name").casefold(), set())
        if expected and match.group("macro") not in expected:
            issues.append(
                {
                    "symbol": match.group("macro"),
                    "verdict": "known_bad",
                    "errorCode": "PROJECT_API_MACRO_MISMATCH",
                    "evidence": [],
                    "note": (
                        f"{match.group('name')} is exported with {sorted(expected)}, not "
                        f"{match.group('macro')}."
                    ),
                    "replacement": sorted(expected)[0],
                }
            )

    assignment_re = re.compile(
        r"\b(?P<target>[AUFSI][A-Za-z0-9_]*)\s*\*\s*[A-Za-z_]\w*\s*=\s*"
        r"[^;\n]*?GetGameState\s*<\s*(?P<returned>[AUFSI][A-Za-z0-9_]*)\s*>\s*\(\s*\)"
    )

    def derives_from(child: str, parent: str) -> bool:
        current = child
        seen: set[str] = set()
        while current and current.casefold() not in seen:
            if current == parent:
                return True
            seen.add(current.casefold())
            current = bases.get(current.casefold(), "")
        return False

    for match in assignment_re.finditer(_mask_comments_and_literals(text)):
        target = match.group("target")
        returned = match.group("returned")
        if target != returned and not derives_from(returned, target):
            issues.append(
                {
                    "symbol": "GetGameState",
                    "verdict": "known_bad",
                    "errorCode": "TEMPLATE_RETURN_TYPE_MISMATCH",
                    "evidence": [],
                    "note": (
                        f"GetGameState<{returned}>() yields {returned}*, which cannot be "
                        f"assigned directly to unrelated {target}*."
                    ),
                }
            )
    return issues


def extract_local_declarations(text: str) -> set[str]:
    """Return symbols introduced by the sketch itself, not claimed as engine APIs."""
    text = _mask_comments_and_literals(text)
    declared = {match.group(1) for match in LOCAL_TYPE_DECL_RE.finditer(text or "")}
    # Architecture sketches commonly use ``ANewType : AActor`` instead of a
    # complete C++ class declaration. Treat only this anchored inheritance form
    # as a proposed local declaration; engine/member claims remain fail-closed.
    declared.update(
        match.group(1) for match in LOCAL_INHERITANCE_SKETCH_RE.finditer(text or "")
    )
    declared.update(match.group(1) for match in LOCAL_DELEGATE_DECL_RE.finditer(text or ""))
    return declared



def _resolve_index(index: str | Path | None) -> Path:
    if index:
        return Path(index).resolve()
    from workspace_paths import resolve_index_path

    return resolve_index_path()


def _lookup(index: Path, symbol: str, top_k: int = 5) -> list[dict[str, Any]]:
    """Legacy semantic lookup retained for explicit, non-gating API searches.

    Sketch validation deliberately does not call this path: a cache miss can
    require several broad LIKE scans of a large RAG database, and fuzzy rows do
    not provide enough evidence to pass the fail-closed sketch gate anyway.
    """
    from rag_semantic import symbol_lookup

    try:
        return symbol_lookup(index, symbol, top_k=top_k)
    except Exception:
        return []


def _lookup_many_exact(
    index: Path,
    symbols: list[str],
    *,
    top_k: int = 5,
) -> tuple[dict[str, list[dict[str, Any]]], str, int]:
    """Resolve exact symbol names with bounded, indexed, read-only SQL batches."""

    ordered = list(dict.fromkeys(symbol for symbol in symbols if symbol))
    if not ordered or not index.is_file():
        return {}, "", 0

    rows_by_symbol: dict[str, list[dict[str, Any]]] = {}
    query_count = 0
    try:
        connection = sqlite3.connect(
            f"{index.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=1.0,
        )
        connection.row_factory = sqlite3.Row
        try:
            columns = {
                str(row[1])
                for row in connection.execute("pragma table_info(chunks)").fetchall()
            }
            required = {"symbol_name", "symbol_kind", "title", "locator"}
            if not required.issubset(columns):
                missing = ", ".join(sorted(required - columns))
                return {}, f"RAG chunks schema is missing required columns: {missing}", 0
            optional = [
                column
                for column in ("source", "project", "module_name", "metadata_json")
                if column in columns
            ]
            select_columns = ["symbol_name", "symbol_kind", "title", "locator", *optional]
            for start in range(0, len(ordered), EXACT_LOOKUP_BATCH_SIZE):
                batch = ordered[start : start + EXACT_LOOKUP_BATCH_SIZE]
                placeholders = ",".join("?" for _ in batch)
                query_count += 1
                matches = connection.execute(
                    f"select {', '.join(select_columns)} from chunks "
                    f"where symbol_name in ({placeholders}) "
                    "order by symbol_name, title, locator",
                    batch,
                ).fetchall()
                for match in matches:
                    item = dict(match)
                    metadata: dict[str, Any] = {}
                    raw_metadata = item.pop("metadata_json", "")
                    if raw_metadata:
                        try:
                            decoded = json.loads(str(raw_metadata))
                            metadata = decoded if isinstance(decoded, dict) else {}
                        except json.JSONDecodeError:
                            metadata = {}
                    qualified = str(
                        metadata.get("qualified_name")
                        or metadata.get("qualifiedName")
                        or ""
                    )
                    if qualified:
                        item["qualified_name"] = qualified
                    item["evidence_source"] = "rag_index_exact"
                    key = str(item.get("symbol_name") or "").casefold()
                    bucket = rows_by_symbol.setdefault(key, [])
                    if len(bucket) < top_k:
                        bucket.append(item)
        finally:
            connection.close()
    except (OSError, sqlite3.Error, ValueError) as exc:
        return {}, f"{type(exc).__name__}: {exc}", query_count
    return rows_by_symbol, "", query_count


def _graph_symbol_index(graph: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(graph, dict):
        return index
    for raw in graph.get("symbols") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("symbol_name") or "").casefold()
        if name:
            index.setdefault(name, []).append(raw)
    return index


def _graph_lookup(
    graph: dict[str, Any] | None,
    symbol: str,
    top_k: int = 5,
    *,
    symbol_index: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(graph, dict):
        return []
    target = symbol.casefold()
    exact: list[dict[str, Any]] = []
    prefix: list[dict[str, Any]] = []
    active_index = symbol_index if symbol_index is not None else _graph_symbol_index(graph)
    candidate_rows: list[dict[str, Any]] = list(active_index.get(target, []))
    if len(candidate_rows) < top_k:
        for folded, rows in active_index.items():
            if folded != target and (folded.startswith(target) or target.startswith(folded)):
                candidate_rows.extend(rows)
    for raw in candidate_rows:
        name = str(raw.get("symbol_name") or "")
        folded = name.casefold()
        if folded == target:
            bucket = exact
        elif folded.startswith(target) or target.startswith(folded):
            bucket = prefix
        else:
            continue
        row = dict(raw)
        row.setdefault("title", row.get("qualified_name") or name)
        path = str(row.get("file_path") or "")
        line = int(row.get("line_start") or 1)
        row.setdefault("locator", f"{path}:{line}" if path else "")
        row["evidence_source"] = "project_symbol_graph"
        bucket.append(row)
    return (exact + prefix)[:top_k]


def _receiver_owner_chain(graph: dict[str, Any] | None, receiver_type: str) -> set[str]:
    """Return the receiver and project-local base types allowed to own a member."""
    receiver = receiver_type.split("::")[-1].strip()
    if not receiver:
        return set()
    bases: dict[str, str] = {}
    for row in (graph or {}).get("symbols") or []:
        if not isinstance(row, dict) or row.get("symbol_kind") not in {"class", "struct"}:
            continue
        name = str(row.get("symbol_name") or "").split("::")[-1]
        base = str(row.get("base_class") or "").split("::")[-1]
        if name:
            bases[name.casefold()] = base
    owners = {receiver.casefold()}
    current = receiver
    while current and len(owners) < 32:
        base = bases.get(current.casefold(), "")
        folded = base.casefold()
        if not base or folded in owners:
            break
        owners.add(folded)
        current = base
    return owners


def _reflected_project_type_evidence(
    graph: dict[str, Any] | None,
    receiver_type: str,
) -> list[dict[str, Any]]:
    """Return direct graph proof that a receiver is a reflected project type.

    ``StaticClass`` is emitted by UnrealHeaderTool for reflected classes, so it
    will not appear as an ordinary function row in the lightweight source
    graph.  Accept it only when the graph itself proves the exact receiver is a
    reflected class.  This deliberately does not bless arbitrary
    ``Foo::StaticClass()`` calls.
    """

    target = receiver_type.split("::")[-1].strip().casefold()
    if not target:
        return []
    evidence: list[dict[str, Any]] = []
    for row in (graph or {}).get("symbols") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("symbol_name") or "").casefold() != target:
            continue
        if row.get("symbol_kind") not in {"class", "interface"}:
            continue
        if row.get("is_reflected") is not True:
            continue
        path = str(row.get("file_path") or "")
        line = int(row.get("line_start") or 1)
        evidence.append(
            {
                "symbol_name": str(row.get("symbol_name") or receiver_type),
                "symbol_kind": str(row.get("symbol_kind") or "class"),
                "qualified_name": str(row.get("qualified_name") or ""),
                "title": f"reflected project type {receiver_type}",
                "locator": f"{path}:{line}" if path else "",
                "source": "project_symbol_graph",
            }
        )
    return evidence[:3]


def _row_qualified_owner(row: dict[str, Any], symbol: str) -> str:
    qualified = str(row.get("qualified_name") or "").strip()
    if "::" in qualified:
        owner, member = qualified.rsplit("::", 1)
        if member.casefold() == symbol.casefold():
            return owner.split("::")[-1]
    return ""


def _classify_symbol(
    symbol: str,
    rows: list[dict[str, Any]],
    *,
    receiver_type: str = "",
    acceptable_owners: set[str] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    target = symbol.lower()
    exact: list[dict[str, Any]] = []
    prefix: list[dict[str, Any]] = []
    for row in rows:
        name = str(row.get("symbol_name") or "").lower()
        if not name:
            continue
        if name == target:
            exact.append(row)
        elif name.startswith(target) or target.startswith(name):
            prefix.append(row)
    evidence = [
        {
            "symbol_name": r.get("symbol_name"),
            "symbol_kind": r.get("symbol_kind"),
            "qualified_name": r.get("qualified_name"),
            "title": r.get("title"),
            "locator": r.get("locator"),
            "source": r.get("evidence_source") or "rag_index",
            **({"signatures": r.get("signatures")} if r.get("signatures") else {}),
        }
        for r in (exact or prefix)[:3]
    ]
    if exact:
        if receiver_type:
            expected_owners = acceptable_owners or {receiver_type.split("::")[-1].casefold()}
            qualified_owners = {
                _row_qualified_owner(row, symbol).casefold()
                for row in exact
                if _row_qualified_owner(row, symbol)
            }
            if expected_owners & qualified_owners:
                return "verified", evidence
            if qualified_owners:
                return "unverified", evidence
            return "weak", evidence
        return "verified", evidence
    if prefix:
        return "weak", evidence
    return "unverified", []


def validate_sketch(
    sketch: str,
    index: str | Path | None = None,
    *,
    top_k: int = 5,
    graph: dict[str, Any] | None = None,
    declaration_context: str = "",
    engine_root: str | Path | None = None,
) -> dict[str, Any]:
    from unreal_api_denylist import check_denylist

    sketch_chars = len(sketch or "")
    if sketch_chars > MAX_SKETCH_CHARS:
        return {
            "ok": False,
            "errorCode": "SKETCH_TOO_LARGE",
            "error": (
                f"Sketch is {sketch_chars} characters; the validation limit is "
                f"{MAX_SKETCH_CHARS}."
            ),
            "retryable": True,
            "verdictSummary": "Sketch was not inspected because it exceeds the active-slice size limit.",
            "indexPath": str(index or ""),
            "indexExists": False,
            "projectGraphAvailable": False,
            "projectGraphSymbolCount": 0,
            "symbolCount": 0,
            "localDeclarationCount": 0,
            "verifiedCount": 0,
            "knownBadCount": 0,
            "unverifiedCount": 0,
            "weakCount": 0,
            "results": [],
            "sketchCharCount": sketch_chars,
            "maxSketchChars": MAX_SKETCH_CHARS,
            "indexLookupMode": "not_started",
            "indexLookupSymbolCount": 0,
            "indexLookupQueryCount": 0,
            "guidance": (
                "Split the draft to the current target-file slice and validate that smaller sketch. "
                "Do not checkpoint or claim the code-sketch gate complete."
            ),
            "agentInstruction": (
                "Split to the active slice, keep targetFiles exact, and call "
                "unreal_code_sketch_claim_validate once with the smaller draft."
            ),
        }

    index_path = _resolve_index(index)
    index_exists = index_path.exists()
    graph_available = isinstance(graph, dict) and isinstance(graph.get("symbols"), list)
    graph_index = _graph_symbol_index(graph)

    analysis_text = _mask_comments_and_literals(sketch)
    denylist_hits = check_denylist(analysis_text)
    denied_terms = {hit["term"] for hit in denylist_hits}

    candidates = extract_symbols(analysis_text)
    member_claims = extract_member_call_claims(
        analysis_text,
        declaration_context=declaration_context,
    )
    # The target files are loaded as declaration_context by the MCP wrapper.
    # Existing project-local classes, structs, enums, and delegate macros are
    # valid ownership evidence too; requiring them to appear again in the
    # patch sketch created an impossible lookup loop for delegate typedefs
    # that lightweight RAG indexes do not record.
    local_declarations = extract_local_declarations(analysis_text)
    local_declarations.update(extract_local_declarations(declaration_context))
    lookup_symbols: list[str] = []
    for symbol in [*candidates, *(claim["member"] for claim in member_claims)]:
        if symbol in COMMON_SAFE or symbol in local_declarations:
            continue
        if symbol.casefold() in denied_terms:
            continue
        if symbol not in lookup_symbols:
            lookup_symbols.append(symbol)
    exact_rows, index_lookup_error, index_lookup_queries = _lookup_many_exact(
        index_path,
        lookup_symbols,
        top_k=top_k,
    ) if index_exists else ({}, "", 0)
    from engine_header_evidence import lookup_engine_header_evidence

    engine_claims: list[dict[str, str]] = [
        {
            "symbol": symbol,
            "receiverType": "",
            # A full SDK declaration scan is the final evidence tier, not a
            # generic fuzzy search for every invented token. Enable it only
            # when the project graph/index already proves that the engine
            # symbol occurs somewhere but lacks its declaration header.
            "allowDeclarationScan": bool(
                exact_rows.get(symbol.casefold())
                or _graph_lookup(graph, symbol, top_k=1, symbol_index=graph_index)
            ),
        }
        for symbol in candidates
        if symbol in lookup_symbols
    ]
    engine_claims.extend(
        {
            "symbol": claim["member"],
            "receiverType": claim.get("receiverType", ""),
        }
        for claim in member_claims
        if claim["member"] in lookup_symbols and claim.get("receiverType")
    )
    resolved_engine_root = engine_root or os.environ.get("UNREAL_ENGINE_ROOT", "")
    engine_lookup = lookup_engine_header_evidence(resolved_engine_root, engine_claims)
    engine_rows_by_claim = engine_lookup.get("results") or {}
    project_rows_by_claim = _project_contract_rows(graph, member_claims)
    contract_rows_by_claim: dict[str, list[dict[str, Any]]] = {
        str(key): list(value)
        for key, value in engine_rows_by_claim.items()
        if isinstance(value, list)
    }
    for key, value in project_rows_by_claim.items():
        contract_rows_by_claim.setdefault(key, []).extend(value)

    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    for hit in denylist_hits:
        term = hit["term"]
        if term in seen:
            continue
        seen.add(term)
        results.append(
            {
                "symbol": term,
                "verdict": "known_bad",
                "evidence": [],
                "note": hit["message"],
                "replacement": hit.get("replacement") or "",
            }
        )

    def _consider(
        symbol: str,
        *,
        is_member: bool,
        receiver_type: str = "",
        receiver: str = "",
        call_kind: str = "",
    ) -> None:
        key = f"{receiver_type.casefold()}::{symbol.casefold()}" if is_member else symbol.casefold()
        if key in seen or symbol.casefold() in denied_terms:
            return
        if symbol in COMMON_SAFE:
            return
        if symbol in local_declarations:
            return
        seen.add(key)
        if is_member and call_kind == "static" and symbol == "StaticClass":
            reflected_evidence = _reflected_project_type_evidence(graph, receiver_type)
            if reflected_evidence:
                results.append(
                    {
                        "symbol": symbol,
                        "receiver": receiver,
                        "receiverType": receiver_type,
                        "verdict": "verified",
                        "evidence": [],
                        "note": (
                            f"{receiver_type} is a reflected project class; StaticClass is "
                            "generated by UnrealHeaderTool."
                        ),
                    }
                )
                return
        graph_rows = _graph_lookup(
            graph,
            symbol,
            top_k=top_k,
            symbol_index=graph_index,
        )
        rag_rows = exact_rows.get(symbol.casefold(), [])
        engine_key = (
            f"{receiver_type.casefold()}::{symbol.casefold()}"
            if is_member and receiver_type
            else symbol.casefold()
        )
        engine_rows = list(engine_rows_by_claim.get(engine_key, []))
        rows = graph_rows + engine_rows + [
            row for row in rag_rows
            if not any(
                str(existing.get("symbol_name") or "").casefold() == str(row.get("symbol_name") or "").casefold()
                and str(existing.get("qualified_name") or "").casefold()
                == str(row.get("qualified_name") or "").casefold()
                for existing in [*graph_rows, *engine_rows]
            )
        ]
        verdict, evidence = _classify_symbol(
            symbol,
            rows,
            receiver_type=receiver_type if is_member else "",
            acceptable_owners=_receiver_owner_chain(graph, receiver_type) if is_member else None,
        )
        note = ""
        if verdict == "unverified":
            note = (
                "No exact symbol evidence was found in the project graph, index, or bounded engine-header "
                "lookup. This is an index/source coverage miss, not proof that the API is absent; keep it "
                "UNKNOWN until the version-matched header or compiler proves the contract."
            )
        elif verdict == "weak":
            note = "Only a prefix or owner-less match found; confirm the exact receiver and signature before use."
        if is_member and receiver_type and verdict == "unverified" and evidence:
            note = (
                f"{symbol} exists, but no exact {receiver_type}::{symbol} owner match was found. "
                "Do not treat a method on another receiver as valid."
            )
        elif is_member and not receiver_type and verdict == "verified":
            verdict = "weak"
            note = (
                f"Receiver type for {receiver or 'member call'}->{symbol} could not be inferred; "
                "confirm the receiver class and exact signature."
            )
        elif is_member and verdict != "verified":
            note = note or "Member/method call not confirmed on the receiver type; verify the exact signature."
        coverage_status = (
            "engine_header_verified"
            if any(row.get("evidence_source") == "engine_header_exact" for row in rows)
            and verdict == "verified"
            else "indexed_verified"
            if verdict == "verified"
            else "engine_root_unavailable"
            if engine_lookup.get("status") == "engine_root_unavailable"
            else "index_source_coverage_missing"
        )
        verified_engine_evidence = [
            item for item in evidence if item.get("source") == "engine_header_exact"
        ]
        results.append(
            {
                "symbol": symbol,
                **({"receiver": receiver, "receiverType": receiver_type} if is_member else {}),
                "verdict": verdict,
                "coverageStatus": coverage_status,
                "evidence": verified_engine_evidence if verdict == "verified" else evidence,
                "note": note,
            }
        )

    for symbol in candidates:
        _consider(symbol, is_member=False)
    for claim in member_claims:
        _consider(
            claim["member"],
            is_member=True,
            receiver_type=claim["receiverType"],
            receiver=claim["receiver"],
            call_kind=claim.get("callKind", ""),
        )

    results.extend(
        _call_contract_issues(
            analysis_text,
            declaration_context,
            contract_rows_by_claim,
        )
    )
    results.extend(_project_declaration_contract_issues(analysis_text, graph))

    combined_declarations = _mask_comments_and_literals(
        f"{declaration_context}\n{sketch}"
    )
    game_state_class = re.search(
        r"\bclass\s+(?:\w+_API\s+)?(?P<name>A\w+)\s*:\s*public\s+A(?:GameStateBase|GameState)\b",
        combined_declarations,
    )
    # Ownership blockers must describe the proposed sketch, not stale declarations
    # from the file that the sketch is about to replace.  Using the combined
    # declaration context here made removal of an existing GameState RPC
    # impossible: the pre-write gate kept rediscovering the old declaration even
    # after the draft removed it.
    sketch_declarations = _mask_comments_and_literals(sketch)
    if game_state_class and re.search(
        r"\bUFUNCTION\s*\([^)]*\bServer\b[^)]*\)", sketch_declarations
    ):
        results.append(
            {
                "symbol": "ServerRpcOnGameState",
                "verdict": "known_bad",
                "evidence": [],
                "note": (
                    f"{game_state_class.group('name')} is not client-owned; a client-to-server "
                    "request RPC declared on GameState cannot be invoked by ordinary clients."
                ),
                "replacement": (
                    "Declare the request RPC on the owning PlayerController, Pawn, or another "
                    "client-owned Actor, then perform the authoritative GameState mutation on the server."
                ),
            }
        )

    known_bad = sum(1 for r in results if r["verdict"] == "known_bad")
    unverified = sum(1 for r in results if r["verdict"] == "unverified")
    weak = sum(1 for r in results if r["verdict"] == "weak")
    verified = sum(1 for r in results if r["verdict"] == "verified")
    known_bad_terms = [
        str(result["symbol"])
        for result in results
        if result["verdict"] == "known_bad"
    ]
    known_bad_suffix = f" ({', '.join(known_bad_terms[:4])})" if known_bad_terms else ""
    verdict_summary = (
        f"{verified} verified, {weak} weak, {known_bad} known_bad"
        f"{known_bad_suffix}, "
        f"{unverified} unverified"
    )
    verdict_order = {"known_bad": 0, "unverified": 1, "weak": 2, "verified": 3}
    results.sort(key=lambda item: (verdict_order.get(str(item.get("verdict")), 9), str(item.get("symbol"))))

    payload = {
        "ok": known_bad == 0 and unverified == 0 and weak == 0 and not index_lookup_error,
        "verdictSummary": verdict_summary,
        "indexPath": str(index_path),
        "indexExists": index_exists,
        "projectGraphAvailable": graph_available,
        "projectGraphSymbolCount": sum(len(rows) for rows in graph_index.values()),
        "symbolCount": len(results),
        "localDeclarationCount": len(local_declarations),
        "verifiedCount": verified,
        "knownBadCount": known_bad,
        "unverifiedCount": unverified,
        "weakCount": weak,
        "results": results,
        "sketchCharCount": sketch_chars,
        "maxSketchChars": MAX_SKETCH_CHARS,
        "indexLookupMode": "exact_batch" if index_exists else "index_unavailable",
        "indexLookupSymbolCount": len(lookup_symbols),
        "indexLookupQueryCount": index_lookup_queries,
        "engineHeaderLookup": {
            "status": str(engine_lookup.get("status") or "not_started"),
            "engineRoot": str(engine_lookup.get("engineRoot") or ""),
            "catalogFileCount": int(engine_lookup.get("catalogFileCount") or 0),
            "inspectedFileCount": int(engine_lookup.get("inspectedFileCount") or 0),
            "verifiedClaimCount": sum(
                1
                for result in results
                if result.get("coverageStatus") == "engine_header_verified"
            ),
        },
        "guidance": (
            "Replace every known_bad item using its replacement, downgrade unverified or weak "
            "symbols to UNKNOWN, then validate receiver types and exact signatures before presenting it. "
            "Keep proof level at Proposed."
        ),
    }
    if index_lookup_error:
        payload.update(
            {
                "ok": False,
                "errorCode": "SKETCH_INDEX_LOOKUP_FAILED",
                "error": index_lookup_error,
                "retryable": True,
                "agentInstruction": (
                    "Check unreal_rag_health once; do not repeat the same sketch validation "
                    "until the index is readable."
                ),
            }
        )
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate Unreal API names in a code sketch.")
    parser.add_argument("--sketch", default="", help="Sketch text to validate")
    parser.add_argument("--sketch-file", default="", help="File containing sketch text")
    parser.add_argument("--index", default="", help="Path to rag.sqlite (defaults to workspace index)")
    parser.add_argument("--top-k", type=int, default=5)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sketch = args.sketch
    if args.sketch_file:
        sketch = Path(args.sketch_file).read_text(encoding="utf-8-sig")
    if not sketch.strip():
        print("No sketch text provided. Use --sketch or --sketch-file.", file=sys.stderr)
        return 2
    payload = validate_sketch(sketch, args.index or None, top_k=args.top_k)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
