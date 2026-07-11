#!/usr/bin/env python
"""Domain-specific static validators for component/subsystem/replication/GAS/animation."""

from __future__ import annotations

import re
from pathlib import Path

from unreal_static_validate import Finding, line_number


def validate_component_preflight(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    if path.suffix.lower() not in {".h", ".hpp", ".cpp", ".c", ".cc"}:
        return findings

    if path.suffix.lower() in {".cpp", ".c", ".cc"}:
        for match in re.finditer(r"\bCreateDefaultSubobject\s*<", text):
            before = text[: match.start()]
            if "::" not in before.splitlines()[-1]:
                findings.append(
                    Finding(
                        "warning",
                        rel,
                        line_number(text, match.start()),
                        "COMPONENT_CREATE_DEFAULT_SUBOBJECT_WRONG_LOCATION",
                        "CreateDefaultSubobject should be called from the owner class constructor, not a free function.",
                    )
                )
                break

    if path.suffix.lower() in {".h", ".hpp"} and "CreateDefaultSubobject" in text:
        findings.append(
            Finding(
                "warning",
                rel,
                1,
                "COMPONENT_CREATE_DEFAULT_SUBOBJECT_WRONG_LOCATION",
                "CreateDefaultSubobject belongs in the owner .cpp constructor, not the header.",
            )
        )

    member_pattern = re.compile(
        r"CreateDefaultSubobject\s*<\s*([A-Za-z_][A-Za-z0-9_]*)\s*>",
    )
    for match in member_pattern.finditer(text):
        symbol = match.group(1)
        if not re.search(rf"\bTObjectPtr\s*<\s*{re.escape(symbol)}\s*>|\b{re.escape(symbol)}\s*\*", text):
            findings.append(
                Finding(
                    "warning",
                    rel,
                    line_number(text, match.start()),
                    "COMPONENT_MEMBER_DECLARATION_MISSING",
                    f"Register {symbol} with a matching UPROPERTY member declaration in the owner header.",
                )
            )
            break
    return findings


def validate_subsystem_lifecycle(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    if path.suffix.lower() not in {".cpp", ".c", ".cc", ".h", ".hpp"}:
        return findings
    if "Subsystem" not in path.name and "Subsystem" not in text:
        return findings

    if re.search(r"\b(?:UGameInstanceSubsystem|UWorldSubsystem|UEngineSubsystem|ULocalPlayerSubsystem)\b", text):
        if "CreateDefaultSubobject" in text:
            findings.append(
                Finding(
                    "error",
                    rel,
                    line_number(text, text.index("CreateDefaultSubobject")),
                    "SUBSYSTEM_CREATE_DEFAULT_SUBOBJECT_FORBIDDEN",
                    "Subsystems must not call CreateDefaultSubobject; use Initialize/Deinitialize instead.",
                )
            )
        if path.suffix.lower() in {".cpp", ".c", ".cc"} and re.search(r"\bGetWorld\s*\(", text):
            ctor_match = re.search(r"::[A-Za-z0-9_]+\s*\([^)]*\)\s*\{", text)
            if ctor_match and text.find("GetWorld", ctor_match.start(), ctor_match.end() + 200) >= 0:
                findings.append(
                    Finding(
                        "warning",
                        rel,
                        line_number(text, ctor_match.start()),
                        "SUBSYSTEM_GETWORLD_IN_CTOR",
                        "Avoid GetWorld() in subsystem constructors; defer to Initialize().",
                    )
                )
    return findings


def validate_replication_contract(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    if path.suffix.lower() not in {".h", ".hpp", ".cpp", ".c", ".cc"}:
        return findings

    if re.search(r"\b(?:Server|Client|NetMulticast)\s*,\s*(?:Reliable|Unreliable)\b", text):
        if "_Implementation" in text and "check(Role" not in text and "HasAuthority" not in text:
            findings.append(
                Finding(
                    "warning",
                    rel,
                    line_number(text, text.index("_Implementation")),
                    "REPLICATION_RPC_AUTHORITY_CHECK_MISSING",
                    "RPC _Implementation should validate authority/role before mutating replicated state.",
                )
            )

    if "UPROPERTY" in text and "ReplicatedUsing" in text:
        prop = re.search(r"UPROPERTY[^;\n]*ReplicatedUsing\s*=\s*([A-Za-z0-9_]+)", text)
        onrep = re.search(r"\bvoid\s+OnRep_[A-Za-z0-9_]+\s*\(", text)
        if prop and not onrep:
            findings.append(
                Finding(
                    "warning",
                    rel,
                    line_number(text, prop.start()),
                    "REPLICATION_ONREP_HANDLER_MISSING",
                    "ReplicatedUsing property should have a matching OnRep handler.",
                )
            )
    return findings


def validate_gas_footprint(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    if "AbilitySystemComponent" in text and "GiveAbility" in text and "InitAbilityActorInfo" not in text:
        findings.append(
            Finding(
                "warning",
                rel,
                line_number(text, text.index("GiveAbility")),
                "GAS_ABILITY_BEFORE_ASC_INIT",
                "Grant abilities only after InitAbilityActorInfo on the AbilitySystemComponent.",
            )
        )
    if "UAttributeSet" in text and "GetLifetimeReplicatedProps" not in text and path.suffix.lower() in {".cpp", ".cc"}:
        findings.append(
            Finding(
                "warning",
                rel,
                1,
                "GAS_ATTRIBUTE_REPLICATION_MISSING",
                "Replicated attribute sets should override GetLifetimeReplicatedProps.",
            )
        )
    return findings


def validate_animation_notify_lifecycle(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    if "AnimNotify" not in text and "AnimNotifyState" not in text:
        return findings
    if "AnimNotifyState" in text:
        if "NotifyBegin" not in text or "NotifyEnd" not in text:
            findings.append(
                Finding(
                    "warning",
                    rel,
                    1,
                    "ANIM_NOTIFYSTATE_LIFECYCLE_INCOMPLETE",
                    "AnimNotifyState should implement NotifyBegin and NotifyEnd.",
                )
            )
    elif "Received_Notify" not in text:
        findings.append(
            Finding(
                "warning",
                rel,
                1,
                "ANIM_NOTIFY_RECEIVED_MISSING",
                "AnimNotify should override Received_Notify.",
            )
        )
    return findings
