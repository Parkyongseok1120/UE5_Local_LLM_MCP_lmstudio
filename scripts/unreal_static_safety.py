#!/usr/bin/env python
"""General runtime API and C++ safety validators."""

from __future__ import annotations

import re
from pathlib import Path

from cpp_parse_utils import (
    find_balanced_parens,
    mask_comments_and_strings,
)
from unreal_static_model import (
    CPP_HEADER_SUFFIXES,
    Finding,
)
from unreal_static_scan import (
    iter_function_blocks,
    line_number,
)

GENGINE_WORLD_ACCESS_RE = re.compile(r"\bGEngine\s*->\s*(GetWorld|GetGameInstance)\s*\(")

DISABLE_GRAVITY_RE = re.compile(r"(?:->|\.)\s*DisableGravity\s*\(")

GET_CURRENT_DELTA_TIME_RE = re.compile(r"(?<!->)(?<!\.)\bGetCurrentDeltaTime\s*\(")

PAWN_DECL_RE = re.compile(
    r"\b(?:const\s+)?APawn\s*\*\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
)

WORLD_GET_URL_RE = re.compile(
    r"\b(?:GetWorld\s*\(\s*\)|(?:[A-Za-z_]\w*)?World)\s*->\s*GetURL\s*\(",
    re.IGNORECASE,
)

SPAWN_ACTOR_TRANSFORM_POINTER_RE = re.compile(
    r"\bSpawnActor\s*(?:<[^;{}]+?>)?\s*\([^;{}]*?,\s*&\s*[A-Za-z_]\w*Transform\b",
    re.DOTALL,
)

def validate_gengine_world_context(path: Path, text: str, root: Path) -> list[Finding]:
    """Flag world/game-instance resolution through GEngine.

    UEngine::GetWorld() returns nullptr by design and UEngine has no
    GetGameInstance(); both patterns grab the wrong world (or null) in PIE,
    editor, and multi-world scenarios. World access must flow from an owning
    object (subsystem/actor GetWorld()) or an explicit world-context parameter.
    """
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    for match in GENGINE_WORLD_ACCESS_RE.finditer(text):
        accessor = match.group(1)
        findings.append(
            Finding(
                "error",
                rel,
                line_number(text, match.start()),
                "GENGINE_WORLD_CONTEXT",
                (
                    f"GEngine->{accessor}() does not resolve a usable world context (null or wrong world in "
                    "PIE/editor/multi-world). Use the owning object's world instead: subsystem/actor GetWorld(), "
                    "an explicit UWorld*/world-context parameter, or "
                    "UWorld::GetGameInstance()/UWorld::GetSubsystem on a known world."
                ),
            )
        )
    return findings

def validate_known_bad_api_patterns(path: Path, text: str, root: Path) -> list[Finding]:
    """Warn on high-confidence Unreal API mistakes seen in live project use.

    These stay advisory because static text matching cannot always recover the
    receiver type or selected SpawnActor overload. GEngine world access remains
    a separate blocking rule because that pattern is unambiguously unusable.
    """
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    masked = mask_comments_and_strings(text)
    for match in DISABLE_GRAVITY_RE.finditer(text):
        findings.append(
            Finding(
                "warning",
                rel,
                line_number(text, match.start()),
                "INVENTED_MOVEMENT_API",
                (
                    "UCharacterMovementComponent has no DisableGravity(). Use GravityScale = 0.0f "
                    "or an intentional movement mode such as MOVE_Flying."
                ),
            )
        )
    for match in WORLD_GET_URL_RE.finditer(text):
        findings.append(
            Finding(
                "warning",
                rel,
                line_number(text, match.start()),
                "INVENTED_WORLD_API",
                (
                    "UWorld has no GetURL(). Use GetMapName() or "
                    "UGameplayStatics::GetCurrentLevelName(), then OpenLevel/ServerTravel as appropriate."
                ),
            )
        )
    for match in SPAWN_ACTOR_TRANSFORM_POINTER_RE.finditer(text):
        findings.append(
            Finding(
                "warning",
                rel,
                line_number(text, match.start()),
                "SPAWNACTOR_TRANSFORM_POINTER",
                (
                    "A Transform pointer selects a different/legacy SpawnActor overload and is easy to misuse. "
                    "Prefer the typed overload with SpawnTransform by const reference/value and explicit Params."
                ),
            )
        )
    for match in GET_CURRENT_DELTA_TIME_RE.finditer(masked):
        declaration = re.search(
            r"\b(?:float|double|auto)\s+GetCurrentDeltaTime\s*\([^;{}]*\)\s*(?:const\s*)?(?:;|\{)",
            masked,
        )
        if declaration:
            break
        findings.append(
            Finding(
                "error",
                rel,
                line_number(text, match.start()),
                "INVENTED_DELTA_TIME_API",
                "GetCurrentDeltaTime() is not an Unreal Actor/Component API. Use the TickComponent DeltaTime parameter or GetWorld()->GetDeltaSeconds().",
            )
        )
    pawn_names = {match.group("name") for match in PAWN_DECL_RE.finditer(masked)}
    for pawn_name in pawn_names:
        pawn_call = re.compile(rf"\b{re.escape(pawn_name)}\s*->\s*GetCharacterMovement\s*\(")
        for match in pawn_call.finditer(masked):
            findings.append(
                Finding(
                    "error",
                    rel,
                    line_number(text, match.start()),
                    "PAWN_CHARACTER_MOVEMENT_API",
                    (
                        f"{pawn_name} is statically typed as APawn, which has no GetCharacterMovement(). "
                        "Cast to ACharacter after checking the result, or use a movement component available on the pawn."
                    ),
                )
            )
    return findings

STATIC_MUTABLE_CONTAINER_RE = re.compile(
    r"^\s*(?:inline\s+)?static\s+(?!const\b|constexpr\b)(?:inline\s+)?(TMap|TArray|TSet|TMultiMap)\s*<"
)

def validate_static_mutable_container_members(path: Path, text: str, root: Path) -> list[Finding]:
    """Flag static mutable container members in headers (global registry smell).

    A static TMap/TArray held by a class (e.g. a command dispatcher registry
    re-populated on every subsystem Initialize) shares state across worlds and
    PIE sessions; captured lambdas then act on the wrong world. Prefer instance
    state owned by the subsystem, keyed per world when needed.
    """
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    for index, line in enumerate(text.splitlines(), start=1):
        match = STATIC_MUTABLE_CONTAINER_RE.match(line)
        if not match:
            continue
        findings.append(
            Finding(
                "warning",
                rel,
                index,
                "STATIC_MUTABLE_CONTAINER_MEMBER",
                (
                    f"static mutable {match.group(1)} member is process-global state shared across worlds and PIE "
                    "sessions. Own the container as instance state (e.g. inside the UWorldSubsystem that registers "
                    "into it) so registrations and captured lambdas stay scoped to one world lifetime."
                ),
            )
        )
    return findings

def validate_bool_member_parameter_types(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    if path.suffix.lower() not in CPP_HEADER_SUFFIXES:
        return findings
    masked = mask_comments_and_strings(text)
    bool_members = set(re.findall(r"\bbool\s+(b[A-Z][A-Za-z0-9_]*)\b", masked))
    if not bool_members:
        return findings
    numeric_type = r"(?:signed\s+|unsigned\s+)?(?:float|double|short|long|int|int8|int16|int32|int64|uint8|uint16|uint32|uint64)"
    declaration = re.compile(rf"\b(?P<type>{numeric_type})\s+(?P<name>b[A-Z][A-Za-z0-9_]*)\b")
    seen: set[int] = set()
    for call in re.finditer(r"\b[A-Za-z_][A-Za-z0-9_]*\s*\(", masked):
        open_index = masked.find("(", call.start())
        close_index = find_balanced_parens(masked, open_index)
        if close_index < 0:
            continue
        for match in declaration.finditer(masked, open_index + 1, close_index):
            parameter_name = match.group("name")
            member_name = f"b{parameter_name[3:]}" if parameter_name.startswith("bIn") else parameter_name
            if member_name not in bool_members or match.start() in seen:
                continue
            seen.add(match.start())
            findings.append(
                Finding(
                    "warning",
                    str(path.relative_to(root)),
                    line_number(text, match.start()),
                    "BOOL_MEMBER_PARAMETER_TYPE_MISMATCH",
                    (
                        f"Parameter {parameter_name} is declared as {match.group('type')} but corresponds to bool member "
                        f"{member_name}; declare the parameter as bool in both header and .cpp to avoid MSVC C4800."
                    ),
                )
            )
    return findings

UNCHECKED_CAST_RE = re.compile(
    r"\bCast\s*<[^>]+>\s*\([^)]+\)\s*->",
    re.MULTILINE,
)

SYNC_LOAD_RE = re.compile(
    r"\b(?:LoadObject|StaticLoadObject)\s*[<(]|(?:->|\.)LoadSynchronous\s*\("
)

HOT_PATH_RE = re.compile(r"\b(?:Tick(?:Component)?|NativeTick)\s*\(")

RUNTIME_CALLBACK_RE = re.compile(r"\b(?:BeginPlay|OnRep_\w+)\s*\(")

HARDCODED_GAME_PATH_RE = re.compile(r'"(?:/Game/[^"]+|/Engine/[^"]+)"')

FVECTOR_FLOAT_PRECISION_RE = re.compile(
    r"\bFVector\s*\([^)]*(?:\d+(?:\.\d+)?f|\(\s*float\s*\))[^)]*\)"
)

def validate_unchecked_cast_result(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    for match in UNCHECKED_CAST_RE.finditer(text):
        window = text[max(0, match.start() - 120) : match.start()]
        if re.search(r"\bif\s*\(\s*(?:IsValid\s*\(|Cast<|\w+\s*!=\s*nullptr)", window):
            continue
        findings.append(
            Finding(
                "warning",
                rel,
                line_number(text, match.start()),
                "UNCHECKED_CAST_RESULT",
                "Cast<> result is dereferenced without a visible null/IsValid check.",
            )
        )
    return findings

def validate_sync_load_in_gameplay(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    for header, start, body in iter_function_blocks(text):
        if not SYNC_LOAD_RE.search(body):
            continue
        is_hot = bool(HOT_PATH_RE.search(header))
        is_runtime = bool(RUNTIME_CALLBACK_RE.search(header))
        if not is_hot and not is_runtime:
            continue
        label = "hot path" if is_hot else "runtime callback"
        for match in SYNC_LOAD_RE.finditer(body):
            findings.append(
                Finding(
                    "warning",
                    rel,
                    line_number(text, start + match.start()),
                    "SYNC_LOAD_IN_GAMEPLAY",
                    f"Synchronous load in {label}; prefer async/streaming or preloaded assets.",
                )
            )
            break
    return findings

def validate_hardcoded_asset_path(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    for match in HARDCODED_GAME_PATH_RE.finditer(text):
        window = text[max(0, match.start() - 200) : match.start()]
        if "ConstructorHelpers" in window:
            continue
        findings.append(
            Finding(
                "warning",
                rel,
                line_number(text, match.start()),
                "HARDCODED_ASSET_PATH",
                "Hardcoded /Game/ or /Engine/ asset path; prefer soft references or ConstructorHelpers in ctor.",
            )
        )
    return findings

def validate_fvector_float_precision(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    for match in FVECTOR_FLOAT_PRECISION_RE.finditer(text):
        findings.append(
            Finding(
                "info",
                rel,
                line_number(text, match.start()),
                "FVECTOR_FLOAT_PRECISION",
                "FVector initialized with float literals/casts; UE5 FVector is double-precision.",
            )
        )
    return findings

__all__ = [
    'GENGINE_WORLD_ACCESS_RE',
    'DISABLE_GRAVITY_RE',
    'GET_CURRENT_DELTA_TIME_RE',
    'PAWN_DECL_RE',
    'WORLD_GET_URL_RE',
    'SPAWN_ACTOR_TRANSFORM_POINTER_RE',
    'validate_gengine_world_context',
    'validate_known_bad_api_patterns',
    'STATIC_MUTABLE_CONTAINER_RE',
    'validate_static_mutable_container_members',
    'validate_bool_member_parameter_types',
    'UNCHECKED_CAST_RE',
    'SYNC_LOAD_RE',
    'HOT_PATH_RE',
    'RUNTIME_CALLBACK_RE',
    'HARDCODED_GAME_PATH_RE',
    'FVECTOR_FLOAT_PRECISION_RE',
    'validate_unchecked_cast_result',
    'validate_sync_load_in_gameplay',
    'validate_hardcoded_asset_path',
    'validate_fvector_float_precision',
]
