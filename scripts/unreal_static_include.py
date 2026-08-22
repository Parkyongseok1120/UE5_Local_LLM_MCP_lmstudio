#!/usr/bin/env python
"""C++ include visibility, existence, and required-include validators."""

from __future__ import annotations

import re
from pathlib import Path

from cpp_parse_utils import (
    mask_comments_and_strings,
)
from unreal_static_build import (
    find_include_owner,
)
from unreal_static_model import (
    CPP_HEADER_SUFFIXES,
    CPP_SOURCE_SUFFIXES,
    SOURCE_ONLY_SUFFIXES,
    Finding,
)
from unreal_static_scan import (
    class_base_names,
    has_include,
    include_lines,
    line_number,
    should_ignore_project_path,
)
from workspace_paths import filesystem_path_identity

EDITOR_ONLY_INCLUDES = (
    "UnrealEd.h",
    "UEditorEngine.h",
    "Editor.h",
    "EditorUtilityWidget.h",
    "EditorUtilitySubsystem.h",
    "Kismet2/",
    "AssetToolsModule.h",
    "LevelEditor.h",
)

BASE_CLASS_INCLUDES = {
    "AActor": "GameFramework/Actor.h",
    "APawn": "GameFramework/Pawn.h",
    "ACharacter": "GameFramework/Character.h",
    "APlayerController": "GameFramework/PlayerController.h",
    "AGameModeBase": "GameFramework/GameModeBase.h",
    "AController": "GameFramework/Controller.h",
    "UObject": "UObject/Object.h",
    "UActorComponent": "Components/ActorComponent.h",
    "USceneComponent": "Components/SceneComponent.h",
    "UDataAsset": "Engine/DataAsset.h",
    "USaveGame": "GameFramework/SaveGame.h",
    "UUserWidget": "Blueprint/UserWidget.h",
    "UGameInstanceSubsystem": "Subsystems/GameInstanceSubsystem.h",
    "UWorldSubsystem": "Subsystems/WorldSubsystem.h",
    "UEngineSubsystem": "Subsystems/EngineSubsystem.h",
    "UInterface": "UObject/Interface.h",
}

CPP_SYMBOL_INCLUDES = {
    "UGameplayStatics::": "Kismet/GameplayStatics.h",
    "ConstructorHelpers::": "UObject/ConstructorHelpers.h",
    "DrawDebug": "DrawDebugHelpers.h",
    "DOREPLIFETIME": "Net/UnrealNetwork.h",
    "FObjectInitializer": "UObject/ObjectMacros.h",
    "UBoxComponent": "Components/BoxComponent.h",
    "USphereComponent": "Components/SphereComponent.h",
}

def module_name_for_source_path(path: Path) -> str:
    parts = list(path.parts)
    lowered = [part.lower() for part in parts]
    if "source" not in lowered:
        return ""
    source_index = lowered.index("source")
    if source_index + 1 >= len(parts):
        return ""
    return parts[source_index + 1]

def validate_editor_only_runtime_includes(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    module_name = module_name_for_source_path(path)
    if "editor" in module_name.lower():
        return findings
    for line, include_path in include_lines(text):
        if any(marker.lower() in include_path.lower() for marker in EDITOR_ONLY_INCLUDES):
            findings.append(
                Finding(
                    "error",
                    str(path.relative_to(root)),
                    line,
                    "EDITOR_ONLY_INCLUDE_IN_RUNTIME_MODULE",
                    f'Runtime module source includes editor-only header "{include_path}". Move it to an Editor module or guard/remove the dependency.',
                )
            )
    return findings

def validate_required_includes(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    if path.suffix.lower() in CPP_HEADER_SUFFIXES:
        for class_name, base_name in class_base_names(text).items():
            required = BASE_CLASS_INCLUDES.get(base_name)
            if required and not has_include(text, required):
                findings.append(
                    Finding(
                        "warning",
                        rel,
                        line_number(text, text.find(class_name)),
                        "MISSING_BASE_CLASS_INCLUDE",
                        f'{class_name} derives from {base_name}; include "{required}" directly before the generated header.',
                    )
                )
        if "FTimerHandle" in text and not has_include(text, "TimerManager.h"):
            findings.append(
                Finding(
                    "warning",
                    rel,
                    line_number(text, text.find("FTimerHandle")),
                    "MISSING_TIMER_MANAGER_INCLUDE",
                    'FTimerHandle in a header usually requires "TimerManager.h".',
                )
            )
        if ("FGameplayTag" in text or "FGameplayTagContainer" in text) and not has_include(text, "GameplayTagContainer.h"):
            findings.append(
                Finding(
                    "warning",
                    rel,
                    line_number(text, max(text.find("FGameplayTag"), text.find("FGameplayTagContainer"))),
                    "MISSING_GAMEPLAY_TAG_INCLUDE",
                    'Gameplay tag value types require "GameplayTagContainer.h" in the header that exposes them.',
                )
            )
    if path.suffix.lower() in CPP_SOURCE_SUFFIXES:
        for token, include_path in CPP_SYMBOL_INCLUDES.items():
            token_index = text.find(token)
            if token_index != -1 and not has_include(text, include_path):
                findings.append(
                    Finding(
                        "warning",
                        rel,
                        line_number(text, token_index),
                        "MISSING_CPP_SYMBOL_INCLUDE",
                        f'Code uses {token}; include "{include_path}" in this .cpp file.',
                    )
                )
    return findings

def validate_component_registration_includes(path: Path, text: str, root: Path) -> list[Finding]:
    """Error-level missing include for CreateDefaultSubobject/NewObject complete types."""
    from include_resolver import (
        format_include_feedback,
        infer_usage_kind,
        resolve_project_symbol_include,
    )

    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    if path.suffix.lower() not in SOURCE_ONLY_SUFFIXES:
        return findings

    patterns = (
        re.compile(r"CreateDefaultSubobject\s*<\s*([A-Za-z_][A-Za-z0-9_]*)\s*>"),
        re.compile(r"NewObject\s*<\s*([A-Za-z_][A-Za-z0-9_]*)\s*>"),
    )
    masked = mask_comments_and_strings(text)
    seen: set[tuple[str, str]] = set()
    for pattern in patterns:
        for match in pattern.finditer(masked):
            symbol = match.group(1)
            if not symbol.startswith("U"):
                continue
            include_key = (symbol, rel)
            if include_key in seen:
                continue
            seen.add(include_key)
            usage = infer_usage_kind(text, symbol, match.start())
            resolution = resolve_project_symbol_include(root, symbol, path, usage)
            if not resolution:
                fallback = CPP_SYMBOL_INCLUDES.get(symbol)
                if fallback and not has_include(text, fallback):
                    findings.append(
                        Finding(
                            "error",
                            rel,
                            line_number(text, match.start()),
                            "COMPONENT_REGISTRATION_INCLUDE_MISSING",
                            (
                                f"Missing include for component {symbol}.\n"
                                f'Add: #include "{fallback}"\n'
                                f"To: {rel}\n"
                                "Do not modify Build.cs for engine component includes."
                            ),
                        )
                    )
                continue
            if (root / resolution.declaring_file).resolve() == path.resolve():
                continue
            if has_include(text, resolution.preferred_include):
                continue
            if f'"{symbol}.h"' in text:
                continue
            findings.append(
                Finding(
                    "error",
                    rel,
                    line_number(text, match.start()),
                    "COMPONENT_REGISTRATION_INCLUDE_MISSING",
                    format_include_feedback(resolution),
                )
            )
    return findings

def validate_typo_includes(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    if path.suffix.lower() not in SOURCE_ONLY_SUFFIXES:
        return findings
    rel = str(path.relative_to(root))
    for match in re.finditer(r'#include\s+"([^"]+)"', text):
        include_path = match.group(1)
        if include_path.startswith("Game/Framework/"):
            corrected = include_path.replace("Game/Framework/", "GameFramework/", 1)
            findings.append(
                Finding(
                    "error",
                    rel,
                    line_number(text, match.start()),
                    "BAD_INCLUDE_PATH",
                    f'Invalid include "{include_path}". Use "{corrected}" instead.',
                )
            )
    return findings

ENGINE_INCLUDE_PREFIXES = (
    "Core/",
    "CoreUObject/",
    "Engine/",
    "Subsystems/",
    "GameFramework/",
    "Components/",
    "UObject/",
    "Input/",
    "EnhancedInput/",
    "Kismet/",
    "Blueprint/",
    "Editor/",
    "UnrealEd/",
    "Materials/",
    "RHI/",
    "RenderCore/",
    "PhysicsCore/",
    "Navigation/",
    "AI/",
    "GameplayTags/",
    "GameplayTasks/",
    "Net/",
    "Sockets/",
    "HAL/",
    "Misc/",
    "Logging/",
    "Stats/",
    "Async/",
    "Serialization/",
)

def validate_duplicate_source_basenames(
    root: Path,
    host_platform: str | None = None,
) -> list[Finding]:
    counts: dict[str, list[str]] = {}
    source = root / "Source"
    if not source.is_dir():
        return []
    for path in source.rglob("*"):
        if path.suffix.lower() not in SOURCE_ONLY_SUFFIXES:
            continue
        if should_ignore_project_path(path):
            continue
        key = filesystem_path_identity(
            path.name,
            host_platform,
            strip_project_uri=False,
        )
        rel = str(path.relative_to(root)).replace("\\", "/")
        counts.setdefault(key, []).append(rel)
    findings: list[Finding] = []
    for basename, paths in sorted(counts.items()):
        if len(paths) < 2:
            continue
        findings.append(
            Finding(
                "error",
                paths[0],
                1,
                "DUPLICATE_SOURCE_BASENAME",
                f'Duplicate source basename "{basename}" under Source/: {", ".join(paths)}',
            )
        )
    return findings

def validate_include_paths_exist(
    path: Path,
    text: str,
    root: Path,
    include_index: dict[str, list[str]],
    *,
    write_mode: bool = False,
    include_owner_map: dict[str, list[str]] | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root)).replace("\\", "/")
    owner_map = include_owner_map or {}
    for line, include_path in include_lines(text):
        normalized = include_path.replace("\\", "/")
        if normalized.startswith("Game/Framework/"):
            continue
        if any(normalized.startswith(prefix) for prefix in ENGINE_INCLUDE_PREFIXES):
            continue
        if normalized in {"CoreMinimal.h", "Generated.h"} or normalized.endswith(".generated.h"):
            continue
        candidates = include_index.get(normalized, [])
        if candidates:
            continue
        if write_mode and find_include_owner(normalized, owner_map):
            continue
        if Path(normalized).suffix.lower() not in CPP_HEADER_SUFFIXES:
            continue
        local_basename_candidates = include_index.get(Path(normalized).name, [])
        if not local_basename_candidates:
            # No local header with this basename means the include is most likely
            # supplied by Unreal Engine or another module. UBT is authoritative.
            continue
        severity = "error"
        if write_mode and "/" not in normalized and "\\" not in normalized:
            severity = "warning"
        known_locations = ", ".join(
            sorted({str(Path(candidate).relative_to(root)).replace("\\", "/") for candidate in local_basename_candidates})[:3]
        )
        findings.append(
            Finding(
                severity,
                rel,
                line,
                "INCLUDE_PATH_NOT_FOUND",
                (
                    f'Include "{include_path}" does not resolve to a local project/plugin header. '
                    f"Known header location(s): {known_locations}."
                ),
            )
        )
    return findings

__all__ = [
    'EDITOR_ONLY_INCLUDES',
    'BASE_CLASS_INCLUDES',
    'CPP_SYMBOL_INCLUDES',
    'module_name_for_source_path',
    'validate_editor_only_runtime_includes',
    'validate_required_includes',
    'validate_component_registration_includes',
    'validate_typo_includes',
    'ENGINE_INCLUDE_PREFIXES',
    'validate_duplicate_source_basenames',
    'validate_include_paths_exist',
]
