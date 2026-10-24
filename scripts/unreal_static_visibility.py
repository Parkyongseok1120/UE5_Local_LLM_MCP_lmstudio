#!/usr/bin/env python
"""What a translation unit can see: own-header-first, complete types and plugin type declarations."""

from __future__ import annotations

import re
from pathlib import Path

from cpp_parse_utils import (
    mask_comments_and_strings,
)
from unreal_static_include import (
    ENGINE_INCLUDE_PREFIXES,
    module_name_for_source_path,
)
from unreal_static_model import (
    CPP_HEADER_SUFFIXES,
    CPP_SOURCE_SUFFIXES,
    Finding,
)
from unreal_static_scan import (
    include_lines,
    line_number,
)

QUOTED_INCLUDE_RE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.MULTILINE)


def _resolve_project_include(include: str, path: Path, include_index: dict[str, list[str]]) -> list[Path]:
    normalized = include.replace("\\", "/")
    if (path.parent / normalized).is_file():
        return [path.parent / normalized]
    return [Path(item) for item in include_index.get(normalized, [])]


def visible_includes(path: Path, text: str, include_index: dict[str, list[str]], *, depth: int = 4) -> set[str]:
    """Quoted includes of ``text`` plus those reached through project headers (bounded)."""
    seen_files: set[Path] = set()
    found: set[str] = set()
    pending = [(path, text, 0)]
    while pending:
        current, content, level = pending.pop()
        for include in QUOTED_INCLUDE_RE.findall(content):
            normalized = include.replace("\\", "/")
            found.add(normalized)
            if level >= depth:
                continue
            for header in _resolve_project_include(normalized, current, include_index)[:1]:
                key = header.resolve()
                if key not in seen_files:
                    seen_files.add(key)
                    pending.append((header, read_text_safe(header), level + 1))
    return found


def read_text_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


def _includes_any(visible: set[str], wanted: str) -> bool:
    return any(item == wanted or item.endswith("/" + wanted) for item in visible)


def validate_first_include_is_own_header(path: Path, text: str, root: Path, include_index: dict[str, list[str]],
                                         build_text: str) -> list[Finding]:
    """UBT (IWYU) requires X.cpp to include X.h first when the module has X.h."""
    if path.suffix.lower() not in CPP_SOURCE_SUFFIXES:
        return []
    if re.search(r"IWYUSupport\s*=\s*IWYUSupport\.None|bEnforceIWYU\s*=\s*false|PCHUsageMode\.NoPCHs", build_text):
        return []
    own = path.stem + ".h"
    module = module_name_for_source_path(path)
    candidates = [Path(item) for item in include_index.get(own, [])]
    if not any(module_name_for_source_path(candidate) == module for candidate in candidates):
        return []
    includes = include_lines(_without_comments(text))
    if not includes:
        return []
    line, first = includes[0]
    if Path(first.replace("\\", "/")).name.casefold() == own.casefold():
        return []
    return [Finding(
        "error", str(path.relative_to(root)).replace("\\", "/"), line, "FIRST_INCLUDE_NOT_OWN_HEADER",
        f'UnrealBuildTool requires "{own}" to be the first include of {path.name} ("Expected {own} to be first header '
        f'included"); move #include "{own}" above "{first}".',
        metadata={"ruleId": "FIRST_INCLUDE_NOT_OWN_HEADER", "analysisKind": "lexical", "confidence": "high",
                  "limitations": ["skipped when the Build.cs disables IWYU enforcement"]})]


def _without_comments(text: str) -> str:
    """Comments blanked (line numbers kept) so a commented-out include is not the first include."""
    text = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group()), text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


# Engine types whose complete definition a .cpp needs, matched as uses that require it.
CPP_COMPLETE_TYPE_INCLUDES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"\bGetCharacterMovement\s*\(\s*\)\s*->"), "GetCharacterMovement()->", "GameFramework/CharacterMovementComponent.h"),
    (re.compile(r"\bF(?:Point|Radial)?DamageEvent\s*(?:\(|\{|\s+[A-Za-z_]\w*\s*[;({=])"), "FDamageEvent", "Engine/DamageEvents.h"),
    (re.compile(r"\b(?:SpawnActor(?:Deferred)?|Cast|CastChecked|NewObject)\s*<\s*ACharacter\s*>"), "ACharacter", "GameFramework/Character.h"),
)


def validate_complete_type_includes(path: Path, text: str, root: Path, include_index: dict[str, list[str]]) -> list[Finding]:
    """Uses that need an engine type's complete definition, with no include of it in this file or the
    project headers it includes (C2027 / C2664 on an incomplete type)."""
    if path.suffix.lower() not in CPP_SOURCE_SUFFIXES:
        return []
    masked = mask_comments_and_strings(text)
    visible: set[str] | None = None
    findings: list[Finding] = []
    for pattern, token, required in CPP_COMPLETE_TYPE_INCLUDES:
        match = pattern.search(masked)
        if not match:
            continue
        visible = visible if visible is not None else visible_includes(path, text, include_index)
        if _includes_any(visible, required):
            continue
        findings.append(Finding(
            "warning", str(path.relative_to(root)), line_number(text, match.start()), "MISSING_CPP_SYMBOL_INCLUDE",
            f'Code uses {token}, which needs the complete type; include "{required}" in this .cpp file.',
            metadata={"ruleId": "MISSING_CPP_SYMBOL_INCLUDE", "analysisKind": "lexical", "confidence": "medium",
                      "limitations": ["engine headers may include it transitively"]}))
    return findings


# Plugin types never reach a header through Engine/Core includes: they need the plugin's header or a
# forward declaration. Values are the header that defines each type.
PLUGIN_TYPE_HEADERS = {
    "UInputMappingContext": "InputMappingContext.h",
    "UInputAction": "InputAction.h",
    "UEnhancedInputComponent": "EnhancedInputComponent.h",
    "UEnhancedInputLocalPlayerSubsystem": "EnhancedInputSubsystems.h",
    "FInputActionValue": "InputActionValue.h",
    "FInputActionInstance": "InputAction.h",
    "ETriggerEvent": "InputTriggers.h",
    "EInputActionValueType": "InputActionValue.h",
}
ENHANCED_INPUT_HEADERS = frozenset({
    "EnhancedInputComponent.h", "EnhancedInputSubsystems.h", "EnhancedInputSubsystemInterface.h", "InputAction.h",
    "InputActionValue.h", "InputMappingContext.h", "InputModifiers.h", "InputTriggers.h", "EnhancedPlayerInput.h",
    "EnhancedActionKeyMapping.h", "EnhancedInputLibrary.h", "PlayerMappableInputConfig.h", "InputMappingQuery.h",
})


def validate_plugin_type_declarations(path: Path, text: str, root: Path, include_index: dict[str, list[str]]) -> list[Finding]:
    """A header that names an Enhanced Input type without including its header or forward-declaring it."""
    if path.suffix.lower() not in CPP_HEADER_SUFFIXES:
        return []
    masked = mask_comments_and_strings(text)
    visible = visible_includes(path, text, include_index)
    if any(Path(item).name in ENHANCED_INPUT_HEADERS for item in visible):
        return []
    # Includes we cannot see through (other plugins, unknown headers) may provide the type.
    opaque = [item for item in visible
              if not item.endswith(".generated.h") and not _resolve_project_include(item, path, include_index)
              and not any(item.startswith(prefix) for prefix in ENGINE_INCLUDE_PREFIXES if prefix != "EnhancedInput/")
              and item not in {"CoreMinimal.h", "TimerManager.h", "InputCoreTypes.h", "EngineMinimal.h"}]
    findings: list[Finding] = []
    for type_name, header in PLUGIN_TYPE_HEADERS.items():
        use = re.search(r"\b" + type_name + r"\b", masked)
        if not use:
            continue
        if re.search(r"\b(?:class|struct|enum\s+class|enum)\s+" + type_name + r"\b", masked):
            continue
        findings.append(Finding(
            "warning" if opaque else "error", str(path.relative_to(root)).replace("\\", "/"), line_number(text, use.start()),
            "PLUGIN_TYPE_NOT_DECLARED",
            f"{type_name} is an Enhanced Input plugin type and is not declared in this header (C2065/C4430). "
            f'Forward-declare it ("class {type_name};" / "struct {type_name};") or include "{header}".',
            metadata={"ruleId": "PLUGIN_TYPE_NOT_DECLARED", "analysisKind": "lexical",
                      "confidence": "medium" if opaque else "high",
                      "limitations": [f"unresolved include(s) may provide it: {', '.join(opaque[:3])}"] if opaque else []}))
    return findings


__all__ = [
    "CPP_COMPLETE_TYPE_INCLUDES",
    "ENHANCED_INPUT_HEADERS",
    "PLUGIN_TYPE_HEADERS",
    "QUOTED_INCLUDE_RE",
    "validate_complete_type_includes",
    "validate_first_include_is_own_header",
    "validate_plugin_type_declarations",
    "visible_includes",
]
