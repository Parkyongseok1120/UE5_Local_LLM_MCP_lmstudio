#!/usr/bin/env python
"""Evidence-backed domain validators for Unreal component/subsystem/network/GAS/animation code."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from cpp_parse_utils import extract_macro_blocks, mask_comments_and_strings
from unreal_static_validate import Finding, line_number

if TYPE_CHECKING:
    from domain_validation_context import DomainValidationContext

CPP_SUFFIXES = {".cpp", ".c", ".cc"}
HEADER_SUFFIXES = {".h", ".hpp"}
SUBSYSTEM_BASES = {
    "UGameInstanceSubsystem",
    "UWorldSubsystem",
    "UEngineSubsystem",
    "ULocalPlayerSubsystem",
}


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _matching_brace(text: str, open_index: int) -> int:
    depth = 0
    for index in range(open_index, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _cpp_function_blocks(text: str) -> list[tuple[str, str, int, int, str]]:
    masked = mask_comments_and_strings(text)
    pattern = re.compile(
        r"(?m)^[ \t]*(?:[A-Za-z_][\w:<>,~*& \t]*?[ \t]+)?"
        r"(?P<class>[A-Za-z_]\w*)::(?P<func>~?[A-Za-z_]\w*)\s*"
        r"\([^;{}]*\)[^{;]*\{"
    )
    blocks: list[tuple[str, str, int, int, str]] = []
    for match in pattern.finditer(masked):
        open_index = masked.find("{", match.start(), match.end())
        close_index = _matching_brace(masked, open_index)
        if close_index >= 0:
            blocks.append(
                (
                    match.group("class"),
                    match.group("func"),
                    match.start(),
                    close_index + 1,
                    text[open_index + 1 : close_index],
                )
            )
    return blocks


def _block_for_offset(
    blocks: list[tuple[str, str, int, int, str]], offset: int
) -> tuple[str, str, int, int, str] | None:
    return next((block for block in blocks if block[2] <= offset < block[3]), None)


def _class_context_text(
    text: str,
    context: DomainValidationContext | None,
    class_name: str,
) -> str:
    if context and class_name:
        combined = context.class_text(class_name)
        if combined:
            return combined
    return text


def validate_component_preflight(
    path: Path,
    text: str,
    root: Path,
    context: DomainValidationContext | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    if path.suffix.lower() not in CPP_SUFFIXES:
        return findings
    rel = _rel(path, root)
    masked = mask_comments_and_strings(text)
    blocks = _cpp_function_blocks(text)
    call_re = re.compile(r"\bCreateDefaultSubobject\s*<\s*(?P<type>[A-Za-z_]\w*)\s*>")
    assignment_re = re.compile(
        r"(?P<member>[A-Za-z_]\w*)\s*=\s*(?:[A-Za-z_]\w*\s*\.\s*)?"
        r"CreateDefaultSubobject\s*<\s*(?P<type>[A-Za-z_]\w*)\s*>",
        re.DOTALL,
    )
    for match in call_re.finditer(masked):
        block = _block_for_offset(blocks, match.start())
        if block is None or block[1] != block[0]:
            findings.append(
                Finding(
                    "warning",
                    rel,
                    line_number(text, match.start()),
                    "COMPONENT_CREATE_DEFAULT_SUBOBJECT_WRONG_LOCATION",
                    "CreateDefaultSubobject must run in the owner class constructor.",
                )
            )

    for match in assignment_re.finditer(masked):
        block = _block_for_offset(blocks, match.start())
        if block is None:
            continue
        owner = block[0]
        member = match.group("member")
        component_type = match.group("type")
        owner_text = _class_context_text(text, context, owner)
        declaration = re.compile(
            rf"(?:TObjectPtr\s*<\s*{re.escape(component_type)}\s*>|"
            rf"{re.escape(component_type)}\s*\*)\s*{re.escape(member)}\b"
        )
        if context and context.header_for_class(owner) and not declaration.search(mask_comments_and_strings(owner_text)):
            findings.append(
                Finding(
                    "warning",
                    rel,
                    line_number(text, match.start()),
                    "COMPONENT_MEMBER_DECLARATION_MISSING",
                    f"{owner} constructs {member}; declare a matching {component_type} member in its header.",
                )
            )
    return findings


def validate_subsystem_lifecycle(
    path: Path,
    text: str,
    root: Path,
    context: DomainValidationContext | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    if path.suffix.lower() not in CPP_SUFFIXES:
        return findings
    rel = _rel(path, root)
    for class_name, func_name, start, _, body in _cpp_function_blocks(text):
        base = context.class_bases.get(class_name, "") if context else ""
        class_text = _class_context_text(text, context, class_name)
        is_subsystem = base in SUBSYSTEM_BASES or any(token in class_text for token in SUBSYSTEM_BASES)
        if not is_subsystem:
            continue
        masked_body = mask_comments_and_strings(body)
        if "CreateDefaultSubobject" in masked_body:
            findings.append(
                Finding(
                    "error",
                    rel,
                    line_number(text, start),
                    "SUBSYSTEM_CREATE_DEFAULT_SUBOBJECT_FORBIDDEN",
                    "Subsystems must not create default subobjects; initialize owned state in Initialize().",
                )
            )
        if func_name == class_name and re.search(r"\bGetWorld\s*\(", masked_body):
            findings.append(
                Finding(
                    "warning",
                    rel,
                    line_number(text, start),
                    "SUBSYSTEM_GETWORLD_IN_CTOR",
                    "Avoid GetWorld() in subsystem constructors; defer world access to Initialize().",
                )
            )
    return findings


def _replicated_using_targets(text: str) -> list[tuple[int, str]]:
    targets: list[tuple[int, str]] = []
    for start, _, block in extract_macro_blocks(text, "UPROPERTY"):
        match = re.search(r"\bReplicatedUsing\s*=\s*([A-Za-z_]\w*)", block)
        if match:
            targets.append((start, match.group(1)))
    return targets


def validate_replication_contract(
    path: Path,
    text: str,
    root: Path,
    context: DomainValidationContext | None = None,
) -> list[Finding]:
    """Validate exact OnRep contracts; Server RPCs do not need redundant HasAuthority checks."""
    if path.suffix.lower() not in HEADER_SUFFIXES:
        return []
    rel = _rel(path, root)
    findings: list[Finding] = []
    masked = mask_comments_and_strings(text)
    class_match = re.search(r"\bclass\s+(?:[A-Z0-9_]+_API\s+)?([A-Za-z_]\w*)[^;{]*\{", masked)
    owner = class_match.group(1) if class_match else ""
    combined = _class_context_text(text, context, owner)
    combined_masked = mask_comments_and_strings(combined)
    for offset, handler in _replicated_using_targets(text):
        declaration = re.search(rf"\bvoid\s+{re.escape(handler)}\s*\(", combined_masked)
        definition = re.search(rf"\b[A-Za-z_]\w*::{re.escape(handler)}\s*\(", combined_masked)
        if not declaration and not definition:
            findings.append(
                Finding(
                    "warning",
                    rel,
                    line_number(text, offset),
                    "REPLICATION_ONREP_HANDLER_MISSING",
                    f"ReplicatedUsing names {handler}, but no matching declaration or definition was found.",
                )
            )
    return findings


def validate_gas_footprint(
    path: Path,
    text: str,
    root: Path,
    context: DomainValidationContext | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    if path.suffix.lower() not in CPP_SUFFIXES:
        return findings
    rel = _rel(path, root)
    for class_name, _, start, _, body in _cpp_function_blocks(text):
        masked_body = mask_comments_and_strings(body)
        init = masked_body.find("InitAbilityActorInfo")
        grant = masked_body.find("GiveAbility")
        if init >= 0 and grant >= 0 and grant < init:
            findings.append(
                Finding(
                    "warning",
                    rel,
                    line_number(text, start),
                    "GAS_ABILITY_BEFORE_ASC_INIT",
                    f"{class_name} grants an ability before InitAbilityActorInfo in the same function.",
                )
            )

    masked = mask_comments_and_strings(text)
    owner_match = re.search(r"\b([A-Za-z_]\w*)::GetLifetimeReplicatedProps\s*\(", masked)
    if owner_match and context:
        owner_text = context.class_text(owner_match.group(1))
        if "UAttributeSet" in owner_text and "Replicated" not in owner_text:
            findings.append(
                Finding(
                    "info",
                    rel,
                    line_number(text, owner_match.start()),
                    "GAS_ATTRIBUTE_REPLICATION_WITHOUT_REPLICATED_FIELDS",
                    "AttributeSet overrides lifetime replication but no replicated attribute field was found.",
                )
            )
    return findings


def validate_animation_notify_lifecycle(
    path: Path,
    text: str,
    root: Path,
    context: DomainValidationContext | None = None,
) -> list[Finding]:
    """Do not hard-code notify callback names; engine/version and inheritance decide valid overrides."""
    if path.suffix.lower() not in CPP_SUFFIXES or context is None:
        return []
    rel = _rel(path, root)
    findings: list[Finding] = []
    for class_name, func_name, start, _, _ in _cpp_function_blocks(text):
        class_text = context.class_text(class_name)
        if "AnimNotify" not in class_text:
            continue
        header = context.header_for_class(class_name)
        header_text = context.text_for(header)
        if func_name in {"Notify", "Received_Notify", "NotifyBegin", "NotifyTick", "NotifyEnd"}:
            if header and not re.search(rf"\b{re.escape(func_name)}\s*\(", mask_comments_and_strings(header_text)):
                findings.append(
                    Finding(
                        "warning",
                        rel,
                        line_number(text, start),
                        "ANIMATION_CALLBACK_DECLARATION_MISSING",
                        f"{class_name}::{func_name} is defined without a matching header declaration.",
                    )
                )
    return findings


def validate_replication_ownership_conservative(
    path: Path,
    text: str,
    root: Path,
    context: DomainValidationContext | None = None,
) -> list[Finding]:
    if path.suffix.lower() not in HEADER_SUFFIXES:
        return []
    rel = _rel(path, root)
    masked = mask_comments_and_strings(text)
    if re.search(r"\bUFUNCTION\s*\(\s*Server\b", masked) and "GetOwner()" not in masked:
        return [
            Finding(
                "info",
                rel,
                1,
                "REPLICATION_OWNERSHIP_UNKNOWN",
                "Server RPC declared without visible owning-connection evidence; treat as plan-only.",
            )
        ]
    return []


def validate_rpc_caller_ownership_conservative(
    path: Path,
    text: str,
    root: Path,
    context: DomainValidationContext | None = None,
) -> list[Finding]:
    if path.suffix.lower() not in CPP_SUFFIXES:
        return []
    rel = _rel(path, root)
    findings: list[Finding] = []
    for _, func_name, start, _, body in _cpp_function_blocks(text):
        if re.search(r"->Server\w+\s*\(", body) and "GetLocalRole()" not in body:
            findings.append(
                Finding(
                    "info",
                    rel,
                    line_number(text, start),
                    "REPLICATION_RPC_CALLER_UNKNOWN",
                    "RPC callsite lacks caller-side ownership evidence; plan-only until verified.",
                )
            )
    return findings


def validate_gas_asc_lifecycle_conservative(
    path: Path,
    text: str,
    root: Path,
    context: DomainValidationContext | None = None,
) -> list[Finding]:
    if "AbilitySystemComponent" not in text or path.suffix.lower() not in CPP_SUFFIXES:
        return []
    rel = _rel(path, root)
    if "InitAbilityActorInfo" in text and "OnRep_PlayerState" not in text:
        return [
            Finding(
                "info",
                rel,
                1,
                "GAS_ASC_LIFECYCLE_INCOMPLETE",
                "InitAbilityActorInfo present without client OnRep_PlayerState path evidence.",
            )
        ]
    return []


def validate_animinstance_thread_conservative(
    path: Path,
    text: str,
    root: Path,
    context: DomainValidationContext | None = None,
) -> list[Finding]:
    if "AnimInstance" not in text or path.suffix.lower() not in CPP_SUFFIXES:
        return []
    rel = _rel(path, root)
    if re.search(r"\bGetWorld\s*\(", text) and "ThreadSafe" in text:
        return [
            Finding(
                "warning",
                rel,
                1,
                "ANIMATION_THREAD_CONTEXT_RISK",
                "Potential world mutation/read from worker-thread AnimInstance path.",
            )
        ]
    return []


def validate_animnotify_mutable_state_conservative(
    path: Path,
    text: str,
    root: Path,
    context: DomainValidationContext | None = None,
) -> list[Finding]:
    if "AnimNotify" not in text or path.suffix.lower() not in HEADER_SUFFIXES:
        return []
    rel = _rel(path, root)
    if re.search(r"\b(bool|float|int32|TArray|TMap|TObjectPtr)\s+\w+\s*;", text):
        return [
            Finding(
                "info",
                rel,
                1,
                "ANIMATION_NOTIFY_MUTABLE_STATE",
                "AnimNotify declares mutable state; prefer owner-owned runtime storage.",
            )
        ]
    return []
