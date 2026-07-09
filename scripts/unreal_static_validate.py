#!/usr/bin/env python
"""Static Unreal compile-readiness validators for project source trees."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from parse_build_cs import declared_modules_from_text, public_modules_from_text
from ue_cpp_signatures import (
    FUNCTION_POINTER_TARGET_RE,
    INTERFACE_VIRTUAL_METHOD_RE,
    TYPEDEF_FUNCTION_POINTER_RE,
    clean_method_ret,
    collect_callback_drifts,
    collect_interface_specs,
    find_implementer_method_decl,
    find_method_decl_in_header,
    normalize_signature_params,
    parse_interface_virtual_methods,
)

SOURCE_ONLY_SUFFIXES = {".cpp", ".c", ".cc", ".h", ".hpp"}
IGNORED_PROJECT_DIRS = {
    ".git",
    ".vs",
    "Binaries",
    "DerivedDataCache",
    "golden",
    "Intermediate",
    "Saved",
}


@dataclass
class Finding:
    severity: str
    path: str
    line: int
    code: str
    message: str


def read_text(path: Path, default: str = "") -> str:
    if not path.exists():
        return default
    for encoding in ("utf-8-sig", "utf-8", "cp949", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return default


def should_ignore_project_path(path: Path) -> bool:
    return any(part in IGNORED_PROJECT_DIRS for part in path.parts)


def iter_source_files(root: Path) -> list[Path]:
    suffixes = {".h", ".hpp", ".cpp", ".c", ".cc", ".cs"}
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        if should_ignore_project_path(path):
            continue
        files.append(path)
    return files


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def include_lines(text: str) -> list[tuple[int, str]]:
    results: list[tuple[int, str]] = []
    for index, line in enumerate(text.splitlines(), start=1):
        match = re.match(r'\s*#\s*include\s+[<"]([^>"]+)[>"]', line)
        if match:
            results.append((index, match.group(1)))
    return results


REFLECTED_TYPE_RE = re.compile(r"\b(UCLASS|USTRUCT|UENUM|UINTERFACE)\s*\(")
REFLECTION_RE = re.compile(r"\b(UCLASS|USTRUCT|UENUM|UINTERFACE|GENERATED_BODY)\s*\(")
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
UE_DECLARATION_MACROS = {
    "UCLASS",
    "USTRUCT",
    "UENUM",
    "UINTERFACE",
    "UFUNCTION",
    "UPROPERTY",
    "GENERATED_BODY",
    "GENERATED_UCLASS_BODY",
}
RAW_UOBJECT_MEMBER_RE = re.compile(
    r"\b(?:UObject|AActor|APawn|AController|UActorComponent|USceneComponent|UDataAsset|"
    r"UTexture(?:2D)?|UMaterial(?:Interface|Instance)?|USoundBase|UAnimMontage)\s*\*\s*"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:=\s*[^;]+)?;"
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
UNREAL_LIFECYCLE_OVERRIDE_ALLOWLIST = {
    "AActor": {
        "BeginDestroy",
        "BeginPlay",
        "Destroyed",
        "EndPlay",
        "OnConstruction",
        "PostActorCreated",
        "PostInitializeComponents",
        "PostLoad",
        "ShouldTickIfViewportsOnly",
        "Tick",
    },
    "UActorComponent": {
        "Activate",
        "BeginDestroy",
        "BeginPlay",
        "Deactivate",
        "EndPlay",
        "InitializeComponent",
        "OnComponentCreated",
        "OnComponentDestroyed",
        "OnRegister",
        "OnUnregister",
        "TickComponent",
        "UninitializeComponent",
    },
    "UWorldSubsystem": {
        "Deinitialize",
        "DoesSupportWorldType",
        "Initialize",
        "OnWorldBeginPlay",
        "OnWorldComponentsUpdated",
        "OnWorldEndPlay",
        "PostInitialize",
        "PreDeinitialize",
        "ShouldCreateSubsystem",
    },
    "UGameInstanceSubsystem": {
        "Deinitialize",
        "Initialize",
        "ShouldCreateSubsystem",
    },
    "UEngineSubsystem": {
        "Deinitialize",
        "Initialize",
        "ShouldCreateSubsystem",
    },
    "ULocalPlayerSubsystem": {
        "Deinitialize",
        "Initialize",
        "PlayerControllerChanged",
        "ShouldCreateSubsystem",
    },
    "UObject": {
        "BeginDestroy",
        "PostInitProperties",
        "PostLoad",
    },
}
UNREAL_LIFECYCLE_OVERRIDE_CANDIDATES = (
    set().union(*UNREAL_LIFECYCLE_OVERRIDE_ALLOWLIST.values())
    | {
        "OnLevelRemovedFromWorld",
        "OnWorldCleanup",
        "OnWorldDestroyed",
        "WorldDestroyed",
    }
)
UNREAL_LIFECYCLE_ALTERNATIVES = {
    "AActor": "EndPlay(...) or Destroyed()",
    "UActorComponent": "EndPlay(...) or OnComponentDestroyed(...)",
    "UWorldSubsystem": "OnWorldEndPlay(UWorld&) or PreDeinitialize()",
    "UGameInstanceSubsystem": "Deinitialize()",
    "UEngineSubsystem": "Deinitialize()",
    "ULocalPlayerSubsystem": "Deinitialize() or PlayerControllerChanged(...)",
    "UObject": "BeginDestroy()",
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


def validate_generated_h(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    includes = include_lines(text)
    generated = [(line, value) for line, value in includes if value.endswith(".generated.h")]
    if REFLECTION_RE.search(text) and not generated:
        findings.append(
            Finding(
                "error",
                str(path.relative_to(root)),
                1,
                "GENERATED_H_MISSING",
                f'Reflected Unreal header must include "{path.stem}.generated.h" as its last include.',
            )
        )
    if len(generated) > 1:
        findings.append(
            Finding(
                "error",
                str(path.relative_to(root)),
                generated[1][0],
                "GENERATED_H_DUPLICATE",
                "generated.h include appears more than once.",
            )
        )
    if generated:
        last_include_line = max(line for line, _ in includes)
        if generated[0][0] != last_include_line:
            findings.append(
                Finding(
                    "error",
                    str(path.relative_to(root)),
                    generated[0][0],
                    "GENERATED_H_NOT_LAST",
                    "generated.h must be the last include in the header.",
                )
            )
        uclass_idx = _uclass_line_index(text)
        for line_no, _ in generated:
            if line_no > uclass_idx:
                findings.append(
                    Finding(
                        "error",
                        str(path.relative_to(root)),
                        line_no,
                        "GENERATED_H_AFTER_TYPE",
                        "generated.h must appear in the include block before UCLASS/USTRUCT, not after the type body.",
                    )
                )
                break
    return findings


def _uclass_line_index(text: str) -> int:
    for index, line in enumerate(text.splitlines(), start=1):
        if re.search(r"\bU(CLASS|STRUCT|ENUM)\b", line):
            return index
    return len(text.splitlines()) + 1


def validate_delegate_broadcast_consistency(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    if path.suffix.lower() not in {".cpp", ".cc", ".c"}:
        return findings
    for match in re.finditer(r"\.Broadcast\s*\(\s*\)", text):
        line_no = text[: match.start()].count("\n") + 1
        findings.append(
            Finding(
                "error",
                str(path.relative_to(root)),
                line_no,
                "DELEGATE_BROADCAST_SIGNATURE_MISMATCH",
                "Delegate Broadcast() is missing required payload arguments.",
            )
        )
    return findings


def validate_reflected_namespace(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    brace_depth = 0
    namespace_depths: list[int] = []
    pending_namespace = False
    for index, line in enumerate(text.splitlines(), start=1):
        namespace_match = re.search(r"\bnamespace\s+[A-Za-z_][A-Za-z0-9_:]*\b", line)
        if namespace_match and "{" in line:
            namespace_depths.append(brace_depth + 1)
            pending_namespace = False
        elif namespace_match and ";" not in line:
            pending_namespace = True
        elif pending_namespace and "{" in line:
            namespace_depths.append(brace_depth + 1)
            pending_namespace = False
        match = REFLECTED_TYPE_RE.search(line)
        if match and namespace_depths:
            findings.append(
                Finding(
                    "error",
                    str(path.relative_to(root)),
                    index,
                    "REFLECTED_TYPE_IN_NAMESPACE",
                    f"{match.group(1)} reflected types should not be wrapped in a new C++ namespace.",
                )
            )
        brace_depth += line.count("{") - line.count("}")
        while namespace_depths and brace_depth < namespace_depths[-1]:
            namespace_depths.pop()
    return findings


def validate_blueprint_native_event_declarations(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if "BlueprintNativeEvent" not in line:
            continue

        declaration_parts: list[str] = []
        cursor = index + 1
        while cursor < len(lines) and len(declaration_parts) < 6:
            candidate = lines[cursor].strip()
            cursor += 1
            if not candidate or candidate.startswith("UPROPERTY") or candidate.startswith("UFUNCTION"):
                continue
            declaration_parts.append(candidate)
            if ";" in candidate:
                break
        declaration = " ".join(declaration_parts)
        name_match = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", declaration)
        function_name = name_match.group(1) if name_match else ""

        if "= 0" in declaration or re.search(r"\bvirtual\b.*=\s*0\s*;", declaration):
            findings.append(
                Finding(
                    "error",
                    str(path.relative_to(root)),
                    cursor,
                    "BLUEPRINT_NATIVE_EVENT_PURE_VIRTUAL",
                    "BlueprintNativeEvent UFUNCTION declarations should not be made pure virtual; implement the generated _Implementation method instead.",
                )
            )

        if function_name:
            duplicate_re = re.compile(
                rf"\bvirtual\b[^\n;]*\b{re.escape(function_name)}\s*\([^;]*\)\s*(?:const\s*)?=\s*0\s*;"
            )
            for duplicate in duplicate_re.finditer(text):
                duplicate_line = line_number(text, duplicate.start())
                if duplicate_line > index + 1:
                    findings.append(
                        Finding(
                            "error",
                            str(path.relative_to(root)),
                            duplicate_line,
                            "BLUEPRINT_NATIVE_EVENT_DUPLICATE_VIRTUAL",
                            f"{function_name} duplicates a BlueprintNativeEvent as a pure virtual function; use {function_name}_Implementation in implementers.",
                        )
                    )
                    break
    return findings


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


def validate_raw_uobject_members(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    lines = text.splitlines()
    for index, line in enumerate(lines, start=1):
        if "(" in line:
            continue
        match = RAW_UOBJECT_MEMBER_RE.search(line)
        if not match:
            continue
        nearby = "\n".join(lines[max(0, index - 5) : index])
        if "UPROPERTY" in nearby or "TObjectPtr" in line:
            continue
        findings.append(
            Finding(
                "warning",
                str(path.relative_to(root)),
                index,
                "RAW_UOBJECT_MEMBER_WITHOUT_UPROPERTY",
                f'Raw UObject member "{match.group("name")}" is not visibly tracked by UPROPERTY/TObjectPtr and may be unsafe for garbage collection.',
            )
        )
    return findings


def validate_private_blueprint_access(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    lines = text.splitlines()
    access = "private"
    index = 0
    while index < len(lines):
        line = lines[index]
        type_match = re.match(r"\s*(class|struct)\s+(?:[A-Z0-9_]+_API\s+)?[A-Za-z_][A-Za-z0-9_]*\b", line)
        if type_match:
            access = "private" if type_match.group(1) == "class" else "public"
        access_match = re.match(r"\s*(public|protected|private)\s*:", line)
        if access_match:
            access = access_match.group(1)
        if "UPROPERTY" in line:
            start = index
            block = line
            while ")" not in block and index + 1 < len(lines):
                index += 1
                block += "\n" + lines[index]
            if (
                access == "private"
                and re.search(r"\bBlueprintRead(?:Only|Write)\b", block)
                and "AllowPrivateAccess" not in block
            ):
                findings.append(
                    Finding(
                        "error",
                        str(path.relative_to(root)),
                        start + 1,
                        "PRIVATE_BLUEPRINT_ACCESS",
                        'private BlueprintReadOnly/BlueprintReadWrite UPROPERTY requires meta=(AllowPrivateAccess="true").',
                    )
                )
        index += 1
    return findings


def has_include(text: str, include_path: str) -> bool:
    return any(value == include_path or value.endswith("/" + include_path) for _, value in include_lines(text))


def class_base_names(text: str) -> dict[str, str]:
    bases: dict[str, str] = {}
    pattern = re.compile(
        r"\bclass\s+(?:[A-Z0-9_]+_API\s+)?(?P<class>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*public\s+(?P<base>[A-Za-z_][A-Za-z0-9_]*)"
    )
    for match in pattern.finditer(text):
        bases[match.group("class")] = match.group("base")
    return bases


def class_bases(root: Path) -> dict[str, str]:
    bases: dict[str, str] = {}
    for path in iter_source_files(root):
        if path.suffix.lower() not in {".h", ".hpp"}:
            continue
        bases.update(class_base_names(read_text(path)))
    return bases


def validate_required_includes(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    if path.suffix.lower() in {".h", ".hpp"}:
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
    if path.suffix.lower() in {".cpp", ".c", ".cc"}:
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


def validate_unreal_lifecycle_overrides(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    class_re = re.compile(
        r"\bclass\s+(?:[A-Z0-9_]+_API\s+)?(?P<class>[A-Za-z_][A-Za-z0-9_]*)"
        r"\s*:\s*public\s+(?P<base>[A-Za-z_][A-Za-z0-9_]*)[^;{]*\{",
        flags=re.MULTILINE,
    )
    override_re = re.compile(
        r"(?m)^[^\n;{}]*\b(?P<func>[A-Za-z_][A-Za-z0-9_]*)\s*\([^;\n{}]*\)\s*(?:const\s*)?(?:final\s*)?override\b"
    )
    for class_match in class_re.finditer(text):
        base_name = class_match.group("base")
        allowed = UNREAL_LIFECYCLE_OVERRIDE_ALLOWLIST.get(base_name)
        if not allowed:
            continue
        open_index = text.find("{", class_match.end() - 1)
        if open_index < 0:
            continue
        close_index = find_matching_brace(text, open_index)
        if close_index < 0:
            continue
        body = text[open_index + 1 : close_index]
        for override_match in override_re.finditer(body):
            function_name = override_match.group("func")
            if function_name not in UNREAL_LIFECYCLE_OVERRIDE_CANDIDATES:
                continue
            if function_name in allowed:
                continue
            class_name = class_match.group("class")
            alternative = UNREAL_LIFECYCLE_ALTERNATIVES.get(
                base_name,
                "the lifecycle hook declared by the direct base class",
            )
            findings.append(
                Finding(
                    "error",
                    rel,
                    line_number(text, open_index + 1 + override_match.start()),
                    "INVALID_UNREAL_LIFECYCLE_OVERRIDE",
                    f"{class_name} derives from {base_name}; {function_name} is not a valid lifecycle override for that base. Use {alternative} or verify the exact UE API before editing.",
                )
            )
    return findings


def iter_cpp_definition_blocks(text: str) -> list[tuple[str, str, int, int, str]]:
    blocks: list[tuple[str, str, int, int, str]] = []
    pattern = re.compile(
        r"(?P<class>[A-Za-z_][A-Za-z0-9_]*)::(?P<func>~?[A-Za-z_][A-Za-z0-9_]*)\s*\([^;{}]*\)\s*(?::[^{;]*)?\{",
        flags=re.MULTILINE,
    )
    for match in pattern.finditer(text):
        open_index = match.end() - 1
        close_index = find_matching_brace(text, open_index)
        if close_index == -1:
            continue
        blocks.append(
            (
                match.group("class"),
                match.group("func"),
                match.start(),
                close_index,
                text[open_index + 1 : close_index],
            )
        )
    return blocks


def block_for_offset(blocks: list[tuple[str, str, int, int, str]], offset: int) -> tuple[str, str, int, int, str] | None:
    for block in blocks:
        if block[2] <= offset <= block[3]:
            return block
    return None


def validate_constructor_lifecycle_usage(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    blocks = iter_cpp_definition_blocks(text)
    for token, code, message in (
        (
            "CreateDefaultSubobject<",
            "CREATE_DEFAULT_SUBOBJECT_OUTSIDE_CONSTRUCTOR",
            "CreateDefaultSubobject should only be used in the owning class constructor.",
        ),
        (
            "ConstructorHelpers::",
            "CONSTRUCTOR_HELPERS_OUTSIDE_CONSTRUCTOR",
            "ConstructorHelpers asset lookup should be limited to constructors.",
        ),
    ):
        for match in re.finditer(re.escape(token), text):
            block = block_for_offset(blocks, match.start())
            if not block or block[0] != block[1]:
                findings.append(Finding("error", rel, line_number(text, match.start()), code, message))
    for class_name, func_name, offset, _, body in blocks:
        if class_name == func_name and re.search(r"\bSpawnActor\s*<", body):
            findings.append(
                Finding(
                    "error",
                    rel,
                    line_number(text, offset),
                    "SPAWN_ACTOR_IN_CONSTRUCTOR",
                    "Do not spawn actors from constructors; move spawning to BeginPlay or an explicit runtime factory.",
                )
            )
    return findings


def validate_newobject_outer(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    for match in re.finditer(r"^\s*#\s*define\s+NewObject\b.*$", text, re.MULTILINE):
        findings.append(
            Finding(
                "warning",
                rel,
                line_number(text, match.start()),
                "NEWOBJECT_MACRO_SHADOW",
                "A local NewObject macro shadows the UObject factory API; remove the macro and include UObject/UObjectGlobals.h.",
            )
        )
    for match in re.finditer(r"\bNewObject\s*<[^>]+>\s*\(\s*\)", text):
        findings.append(
            Finding(
                "warning",
                rel,
                line_number(text, match.start()),
                "NEWOBJECT_WITHOUT_OUTER",
                "NewObject without an explicit Outer is easy to garbage-collect incorrectly; pass an owning UObject and store retained objects in UPROPERTY.",
            )
        )
    return findings


def validate_component_timer_manager(path: Path, text: str, root: Path, bases: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    for class_name, _, offset, _, body in iter_cpp_definition_blocks(text):
        if bases.get(class_name) != "UActorComponent":
            continue
        for match in re.finditer(r"\bGetWorldTimerManager\s*\(", body):
            findings.append(
                Finding(
                    "error",
                    rel,
                    line_number(text, offset + match.start()),
                    "COMPONENT_GET_WORLD_TIMER_MANAGER",
                    "UActorComponent should use GetWorld()->GetTimerManager() after validating GetWorld(), not GetWorldTimerManager().",
                )
            )
    return findings


def find_build_cs_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*.Build.cs") if path.is_file() and not should_ignore_project_path(path)]


def build_cs_text(root: Path) -> str:
    parts = []
    for path in find_build_cs_files(root):
        parts.append(read_text(path))
    return "\n".join(parts)


def declared_build_modules(build_text_value: str) -> set[str]:
    return declared_modules_from_text(build_text_value)


def public_build_modules(build_text_value: str) -> set[str]:
    return public_modules_from_text(build_text_value)


def load_include_owner_map(path: Path) -> dict[str, list[str]]:
    cache_key = str(path.resolve()) if path else ""
    cache = getattr(load_include_owner_map, "_cache", {})
    if cache_key in cache:
        return cache[cache_key]
    owners: dict[str, list[str]] = {}
    if not path or not path.exists():
        return owners
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            metadata = item.get("metadata") or {}
            if metadata.get("symbol_kind") != "include_owner":
                continue
            include_path = str(metadata.get("include_path") or metadata.get("symbol_name") or "")
            owner_modules = [str(value) for value in metadata.get("owner_modules") or [] if value]
            if not include_path or not owner_modules:
                continue
            keys = {
                include_path,
                include_path.replace("\\", "/"),
                Path(include_path).name,
            }
            for key in keys:
                owners.setdefault(key, [])
                for module_name in owner_modules:
                    if module_name not in owners[key]:
                        owners[key].append(module_name)
    cache[cache_key] = owners
    setattr(load_include_owner_map, "_cache", cache)
    return owners


def module_name_from_build_file(path: Path) -> str:
    name = path.name
    if name.endswith(".Build.cs"):
        return name[: -len(".Build.cs")]
    return path.stem


def local_module_names(root: Path) -> set[str]:
    return {module_name_from_build_file(path) for path in find_build_cs_files(root)}


def include_visibility(path: Path) -> str:
    parts = {part.lower() for part in path.parts}
    if path.suffix.lower() in {".h", ".hpp"} and "private" not in parts:
        return "public"
    return "private"


def find_include_owner(include_path: str, owner_map: dict[str, list[str]]) -> list[str]:
    normalized = include_path.replace("\\", "/")
    candidates = [
        normalized,
        Path(normalized).name,
    ]
    for candidate in candidates:
        if candidate in owner_map:
            return owner_map[candidate]
    return []


def validate_enhanced_input(path: Path, text: str, root: Path, build_text: str) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    for match in re.finditer(r"\b(?:PlayerInputComponent|InputComponent)\s*->\s*BindAction\s*\(", text):
        findings.append(
            Finding(
                "error",
                rel,
                line_number(text, match.start()),
                "DIRECT_BIND_ACTION",
                "Enhanced Input code must cast to UEnhancedInputComponent and bind with ETriggerEvent.",
            )
        )
    uses_enhanced = any(
        token in text
        for token in (
            "UEnhancedInputComponent",
            "UEnhancedInputLocalPlayerSubsystem",
            "UInputAction",
            "UInputMappingContext",
            "ETriggerEvent",
        )
    )
    if uses_enhanced and "EnhancedInput" not in build_text:
        findings.append(
            Finding(
                "error",
                rel,
                1,
                "MISSING_ENHANCED_INPUT_MODULE",
                'Enhanced Input types require "EnhancedInput" in the module Build.cs dependencies.',
            )
        )
    if uses_enhanced:
        for match in re.finditer(r"->\s*BindAction\s*\(", text):
            statement_end = text.find(";", match.start())
            statement = text[match.start() : statement_end if statement_end != -1 else match.end() + 200]
            if "ETriggerEvent::" not in statement:
                findings.append(
                    Finding(
                        "error",
                        rel,
                        line_number(text, match.start()),
                        "ENHANCED_BIND_WITHOUT_TRIGGER_EVENT",
                        "Enhanced Input BindAction must use an ETriggerEvent argument.",
                    )
                )
    if uses_enhanced and path.suffix.lower() == ".cpp" and "EnhancedInputComponent.h" not in text:
        findings.append(
            Finding(
                "warning",
                rel,
                1,
                "MISSING_ENHANCED_INPUT_INCLUDE",
                'Code uses Enhanced Input; check that "EnhancedInputComponent.h" and related headers are included where needed.',
            )
        )
    return findings


def class_headers(root: Path) -> dict[str, str]:
    headers: dict[str, str] = {}
    for path in iter_source_files(root):
        if path.suffix.lower() not in {".h", ".hpp"}:
            continue
        text = read_text(path)
        for match in re.finditer(r"\bclass\s+(?:[A-Z0-9_]+_API\s+)?([A-Za-z_][A-Za-z0-9_]*)\b", text):
            headers.setdefault(match.group(1), text)
    return headers


def _normalize_signature_params(params: str) -> str:
    return normalize_signature_params(params)


def _header_has_matching_signature(header: str, func_name: str, params: str) -> bool:
    wanted = normalize_signature_params(params)
    declaration_re = re.compile(rf"\b{re.escape(func_name)}\s*\((?P<params>[^)]*)\)")
    for declaration in declaration_re.finditer(header):
        if normalize_signature_params(declaration.group("params")) == wanted:
            return True
    return False


def _normalize_return_type(ret: str) -> str:
    value = re.sub(r"\s+", " ", str(ret or "").strip())
    return value.replace(" const", "").strip()


def validate_cpp_declarations(path: Path, text: str, root: Path, headers: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    definition_re = re.compile(
        r"^(?P<ret>[\w:<>,~*&\s]+?)\s+(?P<class>[A-Za-z_][A-Za-z0-9_]*)::"
        r"(?P<func>~?[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<params>[^)]*)\)",
        flags=re.MULTILINE,
    )
    for match in definition_re.finditer(text):
        class_name = match.group("class")
        func_name = match.group("func")
        params = match.group("params")
        cpp_ret = _normalize_return_type(match.group("ret"))
        header = headers.get(class_name)
        if not header:
            continue
        bare_func = func_name.lstrip("~")
        if bare_func == class_name:
            continue
        if func_name.endswith(("_Implementation", "_Validate")):
            base_name = re.sub(r"_(?:Implementation|Validate)$", "", func_name)
            if header and "BlueprintImplementableEvent" in header and func_name.endswith("_Implementation"):
                if re.search(rf"UFUNCTION\s*\([^)]*BlueprintImplementableEvent", header):
                    findings.append(
                        Finding(
                            "error",
                            rel,
                            line_number(text, match.start()),
                            "BLUEPRINT_IMPLEMENTABLE_EVENT_INVALID_IMPL",
                            (
                                f"{class_name}::{func_name} must not be defined in .cpp for "
                                "BlueprintImplementableEvent; remove the invalid _Implementation body."
                            ),
                        )
                    )
                    continue
            if re.search(rf"\b{re.escape(base_name)}\s*\(", header):
                continue
        if _header_has_matching_signature(header, func_name, params):
            decl_match = find_method_decl_in_header(header, func_name)
            if decl_match:
                header_ret = _normalize_return_type(decl_match.group("ret"))
                if header_ret and cpp_ret and header_ret != cpp_ret:
                    findings.append(
                        Finding(
                            "warning",
                            rel,
                            line_number(text, match.start()),
                            "CPP_RETURN_TYPE_MISMATCH",
                            (
                                f"{class_name}::{func_name} return type in .cpp ({cpp_ret}) does not match "
                                f"the header declaration ({header_ret})."
                            ),
                        )
                    )
            continue
        if re.search(rf"\b{re.escape(func_name)}\s*\(", header):
            findings.append(
                Finding(
                    "warning",
                    rel,
                    line_number(text, match.start()),
                    "CPP_FUNCTION_SIGNATURE_MISMATCH",
                    f"{class_name}::{func_name} is implemented in .cpp with parameters that do not match the matching header declaration.",
                )
            )
            continue
        findings.append(
            Finding(
                "warning",
                rel,
                line_number(text, match.start()),
                "CPP_FUNCTION_NOT_DECLARED_IN_HEADER",
                f"{class_name}::{func_name} is implemented in .cpp but was not found in the matching header.",
            )
        )
    return findings


def collect_blueprint_native_event_declarations(root: Path) -> list[tuple[str, str, Path, int]]:
    declarations: list[tuple[str, str, Path, int]] = []
    for path in iter_source_files(root):
        if path.suffix.lower() not in {".h", ".hpp"}:
            continue
        text = read_text(path)
        current_class = ""
        lines = text.splitlines()
        index = 0
        while index < len(lines):
            class_match = re.search(
                r"\bclass\s+(?:[A-Z0-9_]+_API\s+)?([A-Za-z_][A-Za-z0-9_]*)\b",
                lines[index],
            )
            if class_match:
                current_class = class_match.group(1)
            if "BlueprintNativeEvent" in lines[index]:
                declaration_parts: list[str] = []
                cursor = index + 1
                while cursor < len(lines) and len(declaration_parts) < 8:
                    candidate = lines[cursor].strip()
                    cursor += 1
                    if not candidate or candidate.startswith("UFUNCTION") or candidate.startswith("UPROPERTY"):
                        continue
                    declaration_parts.append(candidate)
                    if ";" in candidate:
                        break
                declaration = " ".join(declaration_parts)
                name_match = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", declaration)
                if current_class and name_match:
                    declarations.append((current_class, name_match.group(1), path, index + 1))
                index = cursor
                continue
            index += 1
    return declarations


def validate_blueprint_native_event_implementations(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    cpp_paths = [path for path in iter_source_files(root) if path.suffix.lower() in {".cpp", ".c", ".cc"}]
    if not cpp_paths:
        return findings
    cpp_text = "\n".join(read_text(path) for path in cpp_paths)
    for class_name, event_name, path, line in collect_blueprint_native_event_declarations(root):
        rel = str(path.relative_to(root))
        header_text = read_text(path)
        manual_decl = re.search(
            rf"\b(?:virtual\s+)?void\s+{re.escape(event_name)}_Implementation\s*\([^;]*\)\s*(?:override\s*)?;",
            header_text,
        )
        if manual_decl:
            findings.append(
                Finding(
                    "error",
                    rel,
                    line_number(header_text, manual_decl.start()),
                    "BLUEPRINT_NATIVE_EVENT_MANUAL_IMPL_DECL",
                    (
                        f"Do not declare {event_name}_Implementation in the header for BlueprintNativeEvent; "
                        "UHT generates it. Implement only in .cpp."
                    ),
                )
            )
        implementation = rf"\b{re.escape(class_name)}::{re.escape(event_name)}_Implementation\s*\("
        if not re.search(implementation, cpp_text):
            findings.append(
                Finding(
                    "error",
                    rel,
                    line,
                    "BLUEPRINT_NATIVE_EVENT_IMPL_MISSING",
                    (
                        f"{class_name}::{event_name} is BlueprintNativeEvent and needs "
                        f"{event_name}_Implementation in the matching .cpp file."
                    ),
                )
            )
    return findings


def validate_cpp_definitions_missing(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    headers = class_headers(root)
    cpp_paths = [path for path in iter_source_files(root) if path.suffix.lower() in {".cpp", ".c", ".cc"}]
    if not cpp_paths:
        return findings
    all_cpp = "\n".join(read_text(path) for path in cpp_paths)
    method_decl_re = re.compile(
        r"^[ \t]*(?:virtual[ \t]+)?[\w:<>,~*& \t]+[ \t]+(?P<func>~?[A-Za-z_][A-Za-z0-9_]*)"
        r"\s*\([^;{}]*\)\s*(?:const\s*)?(?:override\s*)?;",
        re.MULTILINE,
    )
    for class_name, header_text in headers.items():
        if not class_name.startswith("U"):
            continue
        header_path = None
        for path in iter_source_files(root):
            if path.suffix.lower() in {".h", ".hpp"} and class_name in read_text(path):
                header_path = path
                break
        for match in method_decl_re.finditer(header_text):
            func_name = match.group("func")
            if func_name.startswith("~") or func_name in UE_DECLARATION_MACROS:
                continue
            window = header_text[max(0, match.start() - 240) : match.start()]
            if "BlueprintImplementableEvent" in window:
                continue
            impl_name = func_name
            if "BlueprintNativeEvent" in window:
                impl_name = f"{func_name}_Implementation"
            impl = rf"\b{re.escape(class_name)}::{re.escape(impl_name)}\s*\("
            if re.search(impl, all_cpp):
                continue
            if header_path is None:
                continue
            findings.append(
                Finding(
                    "error",
                    str(header_path.relative_to(root)),
                    line_number(header_text, match.start()),
                    "CPP_DEFINITION_MISSING",
                    f"{class_name}::{impl_name} is declared in the header but has no matching .cpp definition.",
                )
            )
    return findings


def collect_rpc_declarations(root: Path) -> list[tuple[str, str, Path, int]]:
    declarations: list[tuple[str, str, Path, int]] = []
    for path in iter_source_files(root):
        if path.suffix.lower() not in {".h", ".hpp"}:
            continue
        text = read_text(path)
        current_class = ""
        lines = text.splitlines()
        index = 0
        while index < len(lines):
            class_match = re.search(
                r"\bclass\s+(?:[A-Z0-9_]+_API\s+)?([A-Za-z_][A-Za-z0-9_]*)\b",
                lines[index],
            )
            if class_match:
                current_class = class_match.group(1)
            if "UFUNCTION" in lines[index] and re.search(r"\b(Server|Client|NetMulticast)\b", lines[index]):
                declaration_parts: list[str] = []
                cursor = index + 1
                while cursor < len(lines) and len(declaration_parts) < 8:
                    candidate = lines[cursor].strip()
                    cursor += 1
                    if not candidate or candidate.startswith("UFUNCTION") or candidate.startswith("UPROPERTY"):
                        continue
                    declaration_parts.append(candidate)
                    if ";" in candidate:
                        break
                declaration = " ".join(declaration_parts)
                name_match = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", declaration)
                if current_class and name_match:
                    declarations.append((current_class, name_match.group(1), path, index + 1))
                index = cursor
                continue
            index += 1
    return declarations


def validate_rpc_implementations(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    cpp_paths = [path for path in iter_source_files(root) if path.suffix.lower() in {".cpp", ".c", ".cc"}]
    if not cpp_paths:
        return findings
    cpp_text = "\n".join(read_text(path) for path in cpp_paths)
    for class_name, function_name, path, line in collect_rpc_declarations(root):
        implementation = rf"\b{re.escape(class_name)}::{re.escape(function_name)}_Implementation\s*\("
        if re.search(implementation, cpp_text):
            continue
        findings.append(
            Finding(
                "error",
                str(path.relative_to(root)),
                line,
                "RPC_IMPLEMENTATION_MISSING",
                f"{class_name}::{function_name} is an RPC and needs a matching {function_name}_Implementation definition in .cpp.",
            )
        )
    return findings


def validate_build_modules(root: Path, source_text: str, build_text_value: str) -> list[Finding]:
    findings: list[Finding] = []
    module_rules = {
        "GameplayTags": ("FGameplayTag", "FGameplayTagContainer", "UGameplayTagsManager"),
        "UMG": ("UUserWidget", "UWidget", "UButton", "UTextBlock"),
        "AIModule": ("AAIController", "UBehaviorTree", "UBlackboardComponent"),
        "Niagara": ("UNiagaraComponent", "UNiagaraSystem", "UNiagaraFunctionLibrary"),
        "NavigationSystem": ("NavigationSystem.h", "UNavigationSystemV1", "ANavigationData"),
        "InputCore": ("InputCoreTypes.h", "FKey", "EKeys::"),
        "Slate": ("SlateBrush.h", "FSlateBrush", "SWidget", "SButton"),
        "MovieScene": ("MovieScene.h", "UMovieScene", "FMovieScene"),
        "LevelSequence": ("LevelSequence.h", "ULevelSequence", "ALevelSequenceActor"),
    }
    build_files = find_build_cs_files(root)
    rel = str(build_files[0].relative_to(root)) if build_files else "Source/*.Build.cs"
    declared_modules = declared_build_modules(build_text_value)
    for module_name, tokens in module_rules.items():
        if module_name in declared_modules:
            continue
        if any(token in source_text for token in tokens):
            severity = "error" if module_name == "GameplayTags" else "warning"
            findings.append(
                Finding(
                    severity,
                    rel,
                    1,
                    "POSSIBLE_MISSING_MODULE",
                    f"Code appears to use {module_name} types; verify Build.cs dependencies.",
                )
            )
    return findings


def validate_include_owner_modules(
    root: Path,
    build_text_value: str,
    owner_map: dict[str, list[str]],
) -> list[Finding]:
    findings: list[Finding] = []
    if not owner_map:
        return findings
    declared = declared_build_modules(build_text_value)
    public_declared = public_build_modules(build_text_value)
    local_modules = local_module_names(root)
    for path in iter_source_files(root):
        if path.suffix.lower() not in {".h", ".hpp", ".cpp", ".c", ".cc"}:
            continue
        text = read_text(path)
        visibility = include_visibility(path)
        for line, include_path in include_lines(text):
            owner_modules = find_include_owner(include_path, owner_map)
            if not owner_modules:
                continue
            candidate_modules = [
                module_name
                for module_name in owner_modules
                if module_name not in local_modules and module_name not in {"Core", "CoreUObject"}
            ]
            if not candidate_modules:
                continue
            missing = [module_name for module_name in candidate_modules if module_name not in declared]
            if missing:
                dependency_kind = "PublicDependencyModuleNames" if visibility == "public" else "PrivateDependencyModuleNames"
                findings.append(
                    Finding(
                        "warning",
                        str(path.relative_to(root)),
                        line,
                        "MISSING_INCLUDE_OWNER_MODULE",
                        f'Include "{include_path}" belongs to module(s) {", ".join(missing)}; add to {dependency_kind}.',
                    )
                )
                continue
            if visibility == "public":
                private_only = [module_name for module_name in candidate_modules if module_name not in public_declared]
                if private_only:
                    findings.append(
                        Finding(
                            "warning",
                            str(path.relative_to(root)),
                            line,
                            "PUBLIC_HEADER_PRIVATE_MODULE",
                            f'Public header includes "{include_path}" from {", ".join(private_only)}; prefer PublicDependencyModuleNames.',
                        )
                    )
    return findings


def find_matching_brace(text: str, open_index: int) -> int:
    depth = 0
    index = open_index
    in_string: str | None = None
    escape = False
    while index < len(text):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == in_string:
                in_string = None
        else:
            if char in {'"', "'"}:
                in_string = char
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return index
        index += 1
    return -1


def iter_function_blocks(text: str) -> list[tuple[str, int, str]]:
    blocks: list[tuple[str, int, str]] = []
    pattern = re.compile(
        r"(?P<header>(?:[\w:<>,~*&]+\s+)+(?P<name>[A-Za-z_][A-Za-z0-9_:~]*)\s*\([^;{}]*\)\s*(?:const\s*)?)\{",
        flags=re.MULTILINE,
    )
    for match in pattern.finditer(text):
        header = re.sub(r"\s+", " ", match.group("header")).strip()
        name = match.group("name")
        if name in {"if", "for", "while", "switch", "catch"}:
            continue
        open_index = match.end() - 1
        close_index = find_matching_brace(text, open_index)
        if close_index == -1:
            continue
        blocks.append((header, match.start(), text[open_index + 1 : close_index]))
    return blocks


ACTION_STAGE_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("current state check", ("Can", "Is", "Has", "State", "Status", "Current", "bIs", "IsValid")),
    ("resource cost check", ("Cost", "Resource", "Stamina", "Mana", "Ammo", "Energy", "Cooldown", "CanAfford")),
    ("asset/montage/target check", ("Target", "Montage", "Asset", "Anim", "Action", "IsValid")),
    ("feasibility check", ("Feasible", "Validate", "Allowed", "Eligible", "Trace", "Can")),
    ("success confirmation", ("Success", "Succeeded", "Result", "bSuccess", "return true")),
    ("resource consume", ("Consume", "Spend", "Commit", "ApplyCost", "Deduct", "Remove")),
    ("state change", ("Set", "State", "Status", "Current", "Enter", "Exit", "bIs")),
    ("event broadcast", ("Broadcast", "Delegate", ".On", "OnAction", "OnRequest")),
]


def first_stage_index(body: str, tokens: tuple[str, ...]) -> int:
    lowered = body.lower()
    indexes = []
    for token in tokens:
        index = lowered.find(token.lower())
        if index != -1:
            indexes.append(index)
    return min(indexes) if indexes else -1


def likely_action_request(header: str, body: str) -> bool:
    value = f"{header}\n{body}"
    if not re.search(r"\b(Request|Try|Attempt|Start|Begin|Perform|Execute|Use|Commit)[A-Za-z0-9_]*", header):
        return False
    return bool(
        re.search(
            r"\b(Action|Interact|Ability|Attack|Use|Cast|Montage|Target|Resource|Cost|Consume|Broadcast)\b",
            value,
        )
    )


def validate_action_request_order(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for header, offset, body in iter_function_blocks(text):
        if not likely_action_request(header, body):
            continue
        stage_indexes = [(name, first_stage_index(body, tokens)) for name, tokens in ACTION_STAGE_PATTERNS]
        present = [(name, index) for name, index in stage_indexes if index != -1]
        missing = [name for name, index in stage_indexes if index == -1]
        if len(present) < 5:
            findings.append(
                Finding(
                    "warning",
                    str(path.relative_to(root)),
                    line_number(text, offset),
                    "ACTION_REQUEST_ORDER_INCOMPLETE",
                    "Likely action request function is missing visible stages: " + ", ".join(missing[:5]) + ".",
                )
            )
            continue
        present_indexes = [index for _, index in present]
        if present_indexes != sorted(present_indexes):
            findings.append(
                Finding(
                    "warning",
                    str(path.relative_to(root)),
                    line_number(text, offset),
                    "ACTION_REQUEST_ORDER_MISMATCH",
                    "Likely action request stages appear out of the required validation/consume/state/broadcast order.",
                )
            )
    return findings


def validate_component_subsystem_patterns(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    name_lower = path.name.lower()
    is_subsystem = "subsystem" in name_lower or any(
        token in text for token in ("UWorldSubsystem", "UGameInstanceSubsystem", "UEngineSubsystem")
    )
    is_component = "component" in name_lower or "UActorComponent" in text

    if is_subsystem:
        if "CreateDefaultSubobject" in text:
            findings.append(
                Finding(
                    "error",
                    rel,
                    0,
                    "SUBSYSTEM_CREATE_SUBOBJECT",
                    "Subsystems must not use CreateDefaultSubobject; subsystems are not Actors.",
                )
            )
        class_stem = path.stem
        ctor_match = re.search(
            rf"\b{re.escape(class_stem)}::{re.escape(class_stem)}\s*\([^)]*\)\s*\{{",
            text,
        )
        if ctor_match:
            ctor_end = text.find("}", ctor_match.end())
            ctor_body = text[ctor_match.end() : ctor_end if ctor_end != -1 else ctor_match.end()]
            for match in re.finditer(r"\b(?:GetWorld|GEngine|SpawnActor)\s*\(", ctor_body):
                findings.append(
                    Finding(
                        "error",
                        rel,
                        line_number(text, ctor_match.start() + match.start()),
                        "SUBSYSTEM_WORLD_SPAWN_IN_CTOR",
                        "Avoid GetWorld/GEngine/SpawnActor in subsystem constructors.",
                    )
                )
        if "PrimaryComponentTick" in text or "TickComponent" in text:
            findings.append(
                Finding(
                    "warning",
                    rel,
                    0,
                    "SUBSYSTEM_TICK_PATTERN",
                    "Subsystems should prefer timers/delegates over component-style Tick patterns.",
                )
            )

    if is_component and path.suffix.lower() in {".cpp", ".cc"}:
        class_name = path.stem
        ctor_pattern = rf"\b{re.escape(class_name)}\s*::\s*{re.escape(class_name)}\s*\("
        if re.search(ctor_pattern, text):
            for match in re.finditer(r"\bGetWorld\s*\(\s*\)", text):
                findings.append(
                    Finding(
                        "warning",
                        rel,
                        line_number(text, match.start()),
                        "COMPONENT_CTOR_GETWORLD",
                        "Avoid GetWorld() in component constructors; use BeginPlay when world access is required.",
                    )
                )
    return findings


def validate_typo_includes(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    if path.suffix.lower() not in {".h", ".hpp", ".cpp", ".c", ".cc", ".inl"}:
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


def build_source_include_index(root: Path) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    source = root / "Source"
    if not source.is_dir():
        return index
    for path in source.rglob("*"):
        if path.suffix.lower() not in {".h", ".hpp"}:
            continue
        if should_ignore_project_path(path):
            continue
        parts = path.parts
        rel = path.name
        if "Public" in parts:
            rel = "/".join(parts[parts.index("Public") + 1 :])
        elif "Private" in parts:
            rel = "/".join(parts[parts.index("Private") + 1 :])
        rel = rel.replace("\\", "/")
        index.setdefault(rel, []).append(str(path))
        index.setdefault(path.name, []).append(str(path))
    return index


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


def validate_duplicate_source_basenames(root: Path) -> list[Finding]:
    counts: dict[str, list[str]] = {}
    source = root / "Source"
    if not source.is_dir():
        return []
    for path in source.rglob("*"):
        if path.suffix.lower() not in {".h", ".hpp", ".cpp", ".c", ".cc"}:
            continue
        if should_ignore_project_path(path):
            continue
        key = path.name.lower()
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


def validate_include_paths_exist(path: Path, text: str, root: Path, include_index: dict[str, list[str]]) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root)).replace("\\", "/")
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
        if normalized.endswith(".h") or normalized.endswith(".hpp"):
            findings.append(
                Finding(
                    "error",
                    rel,
                    line,
                    "INCLUDE_PATH_NOT_FOUND",
                    f'Include "{include_path}" was not found under the project Source/ tree.',
                )
            )
    return findings


def validate_interface_implementer_drift(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    interface_specs = {
        name: [(m.func, m.params_normalized, m.ret) for m in methods]
        for name, methods in collect_interface_specs(root).items()
    }
    if not interface_specs:
        return findings

    for path in iter_source_files(root):
        if path.suffix.lower() not in {".h", ".hpp"}:
            continue
        text = read_text(path)
        rel = str(path.relative_to(root)).replace("\\", "/")
        for interface_name, methods in interface_specs.items():
            if interface_name not in text:
                continue
            if re.search(rf"\bclass\s+{re.escape(interface_name)}\b", text):
                continue
            if f": public {interface_name}" not in text and f", public {interface_name}" not in text:
                continue
            for func_name, iface_params, iface_ret in methods:
                impl_match = find_method_decl_in_header(text, func_name)
                if not impl_match:
                    findings.append(
                        Finding(
                            "warning",
                            rel,
                            line_number(text, 0),
                            "INTERFACE_IMPLEMENTER_SIGNATURE_MISMATCH",
                            f"{interface_name} requires {func_name}({iface_params or 'void'}) but implementer declaration was not found.",
                        )
                    )
                    continue
                impl_params = normalize_signature_params(impl_match.group("params"))
                impl_ret, _ = clean_method_ret(impl_match.group("ret"))
                if impl_params != iface_params or impl_ret.replace("const", "").strip() != iface_ret.replace("const", "").strip():
                    findings.append(
                        Finding(
                            "warning",
                            rel,
                            line_number(text, impl_match.start()),
                            "INTERFACE_IMPLEMENTER_SIGNATURE_MISMATCH",
                            f"{func_name} implementer signature does not match {interface_name} ({iface_ret} {func_name} vs {impl_ret} {func_name}).",
                        )
                    )
    return findings


def validate_callback_function_pointer_drift(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for drift in collect_callback_drifts(root):
        rel = str(drift.cpp_path.relative_to(root)).replace("\\", "/")
        findings.append(
            Finding(
                "warning",
                rel,
                1,
                "CALLBACK_FUNCTION_POINTER_MISMATCH",
                (
                    f"{drift.class_name}::{drift.func_name} params ({drift.method_params or 'void'}) do not match "
                    f"callback typedef {drift.typedef_alias} ({drift.typedef_params or 'void'})."
                ),
            )
        )
    return findings


STALE_MULTIFILE_METHOD_NAMES = frozenset({"DoAll", "HandleAll", "RunAll"})


def validate_multifile_callsite_drift(root: Path) -> list[Finding]:
    """Detect consolidated method names still used after header method split."""
    findings: list[Finding] = []
    headers = class_headers(root)
    for class_name, header_text in headers.items():
        if not class_name.startswith("U"):
            continue
        declared = set(re.findall(r"\bvoid\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\)\s*;", header_text))
        if len(declared) < 2:
            continue
        stale_in_project = STALE_MULTIFILE_METHOD_NAMES - declared
        if not stale_in_project:
            continue
        for path in iter_source_files(root):
            if path.suffix.lower() not in {".cpp", ".c", ".cc", ".h", ".hpp"}:
                continue
            text = read_text(path)
            rel = str(path.relative_to(root)).replace("\\", "/")
            for stale in stale_in_project:
                def_match = re.search(
                    rf"\bvoid\s+{re.escape(class_name)}::{re.escape(stale)}\s*\(",
                    text,
                )
                if def_match:
                    findings.append(
                        Finding(
                            "warning",
                            rel,
                            line_number(text, def_match.start()),
                            "MULTIFILE_CALLSITE_DRIFT",
                            (
                                f"{class_name}::{stale} is defined in cpp but header declares "
                                f"{', '.join(sorted(declared))} instead."
                            ),
                        )
                    )
                for call_match in re.finditer(rf"->{re.escape(stale)}\s*\(", text):
                    findings.append(
                        Finding(
                            "warning",
                            rel,
                            line_number(text, call_match.start()),
                            "MULTIFILE_CALLSITE_DRIFT",
                            (
                                f"Callsite uses {stale}() but {class_name} header declares "
                                f"{', '.join(sorted(declared))}."
                            ),
                        )
                    )
    return findings


def validate_unreal_readiness(
    root: Path,
    module_graph_path: Path | None = None,
    *,
    lightweight: bool = False,
    skip_include_path_checks: bool = False,
) -> list[Finding]:
    if lightweight:
        return validate_unreal_readiness_lightweight(root)
    findings: list[Finding] = []
    build_text_value = build_cs_text(root)
    include_owner_map = load_include_owner_map(module_graph_path) if module_graph_path else {}
    include_index = build_source_include_index(root)
    headers = class_headers(root)
    bases = class_bases(root)
    all_source_text = []
    for path in iter_source_files(root):
        text = read_text(path)
        all_source_text.append(text)
        if path.suffix.lower() in {".h", ".hpp", ".cpp", ".c", ".cc"}:
            findings.extend(validate_typo_includes(path, text, root))
            findings.extend(validate_component_subsystem_patterns(path, text, root))
        if path.suffix.lower() in {".h", ".hpp"}:
            findings.extend(validate_generated_h(path, text, root))
            findings.extend(validate_reflected_namespace(path, text, root))
            findings.extend(validate_unreal_lifecycle_overrides(path, text, root))
            findings.extend(validate_blueprint_native_event_declarations(path, text, root))
            findings.extend(validate_private_blueprint_access(path, text, root))
            findings.extend(validate_raw_uobject_members(path, text, root))
            findings.extend(validate_required_includes(path, text, root))
        if path.suffix.lower() in {".h", ".hpp", ".cpp", ".c", ".cc"}:
            findings.extend(validate_editor_only_runtime_includes(path, text, root))
            findings.extend(validate_enhanced_input(path, text, root, build_text_value))
            findings.extend(validate_action_request_order(path, text, root))
            if not skip_include_path_checks:
                findings.extend(validate_include_paths_exist(path, text, root, include_index))
        if path.suffix.lower() in {".cpp", ".c", ".cc"}:
            findings.extend(validate_required_includes(path, text, root))
            findings.extend(validate_constructor_lifecycle_usage(path, text, root))
            findings.extend(validate_newobject_outer(path, text, root))
            findings.extend(validate_component_timer_manager(path, text, root, bases))
            findings.extend(validate_cpp_declarations(path, text, root, headers))
            findings.extend(validate_delegate_broadcast_consistency(path, text, root))
    findings.extend(validate_build_modules(root, "\n".join(all_source_text), build_text_value))
    findings.extend(validate_include_owner_modules(root, build_text_value, include_owner_map))
    findings.extend(validate_duplicate_source_basenames(root))
    findings.extend(validate_rpc_implementations(root))
    findings.extend(validate_blueprint_native_event_implementations(root))
    findings.extend(validate_cpp_definitions_missing(root))
    findings.extend(validate_interface_implementer_drift(root))
    findings.extend(validate_callback_function_pointer_drift(root))
    findings.extend(validate_multifile_callsite_drift(root))
    return findings


def validate_unreal_readiness_lightweight(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    headers = class_headers(root)
    for path in iter_source_files(root):
        if path.suffix.lower() not in SOURCE_ONLY_SUFFIXES:
            continue
        text = read_text(path)
        findings.extend(validate_typo_includes(path, text, root))
        if path.suffix.lower() in {".h", ".hpp"}:
            findings.extend(validate_generated_h(path, text, root))
            findings.extend(validate_reflected_namespace(path, text, root))
            findings.extend(validate_unreal_lifecycle_overrides(path, text, root))
            findings.extend(validate_blueprint_native_event_declarations(path, text, root))
        if path.suffix.lower() in {".cpp", ".c", ".cc"}:
            findings.extend(validate_cpp_declarations(path, text, root, headers))
    return findings


def format_findings(findings: list[Finding]) -> str:
    if not findings:
        return "No static Unreal compile-readiness issues found."
    output_lines = ["Static Unreal compile-readiness findings:"]
    for finding in findings:
        location = f"{finding.path}:{finding.line}" if finding.line else finding.path
        output_lines.append(f"- [{finding.severity}] {finding.code} {location}: {finding.message}")
    return "\n".join(output_lines)


def has_static_errors(findings: list[Finding]) -> bool:
    return any(finding.severity == "error" for finding in findings)


ACTIONABLE_DRIFT_CODES = frozenset(
    {
        "CPP_RETURN_TYPE_MISMATCH",
        "CALLBACK_FUNCTION_POINTER_MISMATCH",
        "CPP_FUNCTION_SIGNATURE_MISMATCH",
        "INTERFACE_IMPLEMENTER_SIGNATURE_MISMATCH",
        "DELEGATE_BROADCAST_SIGNATURE_MISMATCH",
        "MULTIFILE_CALLSITE_DRIFT",
    }
)


def has_actionable_static_findings(findings: list[Finding], *, mode: str = "") -> bool:
    if has_static_errors(findings):
        return True
    if str(mode or "") != "multifile_refactor":
        return False
    return any(str(finding.code) in ACTIONABLE_DRIFT_CODES for finding in findings)


def should_block_llm_apply_static_gate(findings: list[Finding], *, mode: str = "") -> bool:
    return has_actionable_static_findings(findings, mode=mode)


BLOCKING_STATIC_ERROR_CODES = {
    "DUPLICATE_SOURCE_BASENAME",
    "BLUEPRINT_NATIVE_EVENT_IMPL_MISSING",
    "BLUEPRINT_NATIVE_EVENT_MANUAL_IMPL_DECL",
    "BLUEPRINT_IMPLEMENTABLE_EVENT_INVALID_IMPL",
}


def has_blocking_static_errors(findings: list[Finding]) -> bool:
    for finding in findings:
        if finding.severity != "error":
            continue
        if finding.code.startswith("GENERATED_H"):
            return True
        if finding.code in BLOCKING_STATIC_ERROR_CODES:
            return True
        if finding.code == "INCLUDE_PATH_NOT_FOUND":
            return True
    return False


def can_run_autofix_ubt(findings: list[Finding], *, autofix_written: bool = False) -> bool:
    if has_blocking_static_errors(findings):
        return False
    if autofix_written:
        drift_codes = {
            "CPP_RETURN_TYPE_MISMATCH",
            "CALLBACK_FUNCTION_POINTER_MISMATCH",
            "BLUEPRINT_NATIVE_EVENT_IMPL_MISSING",
            "BLUEPRINT_NATIVE_EVENT_MANUAL_IMPL_DECL",
            "CPP_DEFINITION_MISSING",
        }
        if any(str(finding.code) in drift_codes for finding in findings):
            return False
        return True
    if not findings:
        return True
    if not has_static_errors(findings):
        return True
    return False

