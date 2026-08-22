#!/usr/bin/env python
"""Unreal lifecycle, timer, action-order, and teardown validators."""

from __future__ import annotations

import re
from pathlib import Path

from cpp_parse_utils import (
    find_balanced_parens,
)
from unreal_static_model import (
    CPP_IMPLEMENTATION_SUFFIXES,
    CPP_SOURCE_SUFFIXES,
    Finding,
)
from unreal_static_scan import (
    _split_top_level_args,
    find_matching_brace,
    iter_class_method_blocks,
    iter_function_blocks,
    line_number,
    normalize_timer_handle,
)

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

LIFECYCLE_METHODS_REQUIRING_SUPER = frozenset(
    {"BeginPlay", "EndPlay", "Tick", "Initialize", "Deinitialize"}
)

def validate_missing_super_lifecycle_call(path: Path, text: str, root: Path) -> list[Finding]:
    """Warn (never block) when a UE lifecycle override's body never calls the matching
    Super:: method. This is a common LLM omission that silently breaks base-class
    setup/teardown (e.g. AActor::BeginPlay, UWorldSubsystem::Deinitialize)."""
    findings: list[Finding] = []
    if path.suffix.lower() not in CPP_SOURCE_SUFFIXES:
        return findings
    for header, start, body in iter_function_blocks(text):
        name_match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)::([A-Za-z_][A-Za-z0-9_]*)\s*\(", header)
        if not name_match:
            continue
        class_name, method_name = name_match.group(1), name_match.group(2)
        if method_name not in LIFECYCLE_METHODS_REQUIRING_SUPER:
            continue
        if re.search(rf"\bSuper::{re.escape(method_name)}\s*\(", body):
            continue
        findings.append(
            Finding(
                "warning",
                str(path.relative_to(root)),
                line_number(text, start),
                "MISSING_SUPER_LIFECYCLE_CALL",
                f"{class_name}::{method_name} overrides a UE lifecycle hook but never calls "
                f"Super::{method_name}(...); this can break base-class setup/teardown.",
            )
        )
    return findings

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

    if is_component and path.suffix.lower() in CPP_IMPLEMENTATION_SUFFIXES:
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

def _set_timer_is_looping(call_args: str) -> bool:
    args = _split_top_level_args(call_args)
    if not args:
        return False
    joined = " ".join(args)
    if re.search(r"\bbLoop\s*=\s*false\b", joined, re.IGNORECASE):
        return False
    if re.search(r"\bbLoop\s*=\s*true\b", joined, re.IGNORECASE):
        return True
    for arg in reversed(args):
        lowered = arg.strip().lower()
        if lowered in {"true", "false"}:
            return lowered == "true"
    return False

def _extract_set_timer_calls(body: str) -> list[tuple[int, str, bool]]:
    calls: list[tuple[int, str, bool]] = []
    for match in re.finditer(r"\bSetTimer(?:Delegate)?\s*\(", body):
        open_index = match.end() - 1
        close_index = find_balanced_parens(body, open_index)
        if close_index == -1:
            continue
        args_text = body[open_index + 1 : close_index]
        args = _split_top_level_args(args_text)
        if not args:
            continue
        handle = normalize_timer_handle(args[0])
        if not handle or handle == "this":
            continue
        calls.append((match.start(), handle, _set_timer_is_looping(args_text)))
    return calls

def _extract_clear_timer_calls(body: str) -> tuple[set[str], set[str]]:
    cleared: set[str] = set()
    clear_all_objects: set[str] = set()
    for match in re.finditer(r"\bClearTimer\s*\(\s*", body):
        open_index = match.end() - 1
        close_index = find_balanced_parens(body, open_index)
        if close_index == -1:
            continue
        expr = body[open_index + 1 : close_index].strip()
        cleared.add(normalize_timer_handle(expr))
    for match in re.finditer(r"\bClearAllTimersForObject\s*\(\s*", body):
        open_index = match.end() - 1
        close_index = find_balanced_parens(body, open_index)
        if close_index == -1:
            continue
        expr = body[open_index + 1 : close_index].strip()
        clear_all_objects.add(normalize_timer_handle(expr))
    return cleared, clear_all_objects

TIMER_LOOP_SET_RE = re.compile(r"\bSetTimer(?:Delegate)?\s*\(")

TIMER_CLEAR_RE = re.compile(r"\b(?:ClearTimer|ClearAllTimersForObject)\s*\(")

CLEAR_ALL_TIMERS_RE = re.compile(r"\bClearAllTimersForObject\s*\(")

TIMER_SET_HANDLE_RE = re.compile(r"\bSetTimer(?:Delegate)?\s*\(\s*(?P<handle>[A-Za-z_][A-Za-z0-9_]*)")

TIMER_CLEAR_HANDLE_RE = re.compile(r"\bClearTimer\s*\(\s*(?P<handle>[A-Za-z_][A-Za-z0-9_]*)")

TEARDOWN_METHOD_RE = re.compile(
    r"\b(?:EndPlay|Deinitialize|PreDeinitialize|OnWorldEndPlay|OnUnregister|"
    r"UninitializeComponent|OnComponentDestroyed|Destroyed|BeginDestroy|Shutdown)\b"
)

def validate_timer_set_without_clear(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    class_methods = iter_class_method_blocks(text)
    if not class_methods:
        return findings
    by_class: dict[str, list[tuple[str, int, str]]] = {}
    for class_name, method_name, start, body in class_methods:
        by_class.setdefault(class_name, []).append((method_name, start, body))
    for class_name, methods in by_class.items():
        set_handles: set[str] = set()
        cleared_handles: set[str] = set()
        clear_all_this = False
        first_set_start: int | None = None
        for method_name, start, body in methods:
            for call_start, handle, looping in _extract_set_timer_calls(body):
                if not looping:
                    continue
                if first_set_start is None:
                    first_set_start = start
                set_handles.add(handle)
        for method_name, start, body in methods:
            if TEARDOWN_METHOD_RE.search(method_name):
                cleared, clear_all_objects = _extract_clear_timer_calls(body)
                cleared_handles.update(cleared)
                if "this" in clear_all_objects:
                    clear_all_this = True
        if not set_handles:
            continue
        if clear_all_this:
            continue
        missing_handles = set_handles - cleared_handles
        if missing_handles:
            findings.append(
                Finding(
                    "warning",
                    rel,
                    line_number(text, first_set_start or methods[0][1]),
                    "TIMER_SET_WITHOUT_CLEAR",
                    f"{class_name} sets repeating timer(s) without matching ClearTimer in teardown"
                    f" (missing: {', '.join(sorted(missing_handles))}).",
                )
            )
    return findings

def validate_interrupt_param_ignored(path: Path, text: str, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(path.relative_to(root))
    for header, start, body in iter_function_blocks(text):
        if not re.search(r"\bb(?:Interrupted|WasCancelled)\b", header):
            continue
        for param in ("bInterrupted", "bWasCancelled"):
            if param not in header:
                continue
            if re.search(rf"\b{param}\b", body):
                continue
            name_match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)::([A-Za-z_][A-Za-z0-9_]*)\s*\(", header)
            method = name_match.group(2) if name_match else "callback"
            findings.append(
                Finding(
                    "warning",
                    rel,
                    line_number(text, start),
                    "INTERRUPT_PARAM_IGNORED",
                    f'{method} receives "{param}" but never references it in the body.',
                )
            )
    return findings

def validate_actor_ctor_getworld(path: Path, text: str, root: Path, bases: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    if path.suffix.lower() not in CPP_SOURCE_SUFFIXES:
        return findings
    rel = str(path.relative_to(root))
    for class_name, func_name, _, _, body in iter_cpp_definition_blocks(text):
        if class_name != func_name:
            continue
        base = bases.get(class_name, "")
        if base != "AActor" and not base.startswith("A"):
            continue
        if "GetWorld()" not in body:
            continue
        findings.append(
            Finding(
                "warning",
                rel,
                0,
                "ACTOR_CTOR_GETWORLD",
                f"Avoid GetWorld() in {class_name} constructor; defer world access to BeginPlay.",
            )
        )
    return findings

__all__ = [
    'UNREAL_LIFECYCLE_OVERRIDE_ALLOWLIST',
    'UNREAL_LIFECYCLE_OVERRIDE_CANDIDATES',
    'UNREAL_LIFECYCLE_ALTERNATIVES',
    'validate_unreal_lifecycle_overrides',
    'iter_cpp_definition_blocks',
    'block_for_offset',
    'validate_constructor_lifecycle_usage',
    'validate_newobject_outer',
    'validate_component_timer_manager',
    'LIFECYCLE_METHODS_REQUIRING_SUPER',
    'validate_missing_super_lifecycle_call',
    'ACTION_STAGE_PATTERNS',
    'first_stage_index',
    'likely_action_request',
    'validate_action_request_order',
    'validate_component_subsystem_patterns',
    '_set_timer_is_looping',
    '_extract_set_timer_calls',
    '_extract_clear_timer_calls',
    'TIMER_LOOP_SET_RE',
    'TIMER_CLEAR_RE',
    'CLEAR_ALL_TIMERS_RE',
    'TIMER_SET_HANDLE_RE',
    'TIMER_CLEAR_HANDLE_RE',
    'TEARDOWN_METHOD_RE',
    'validate_timer_set_without_clear',
    'validate_interrupt_param_ignored',
    'validate_actor_ctor_getworld',
]
