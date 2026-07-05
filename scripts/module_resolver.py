#!/usr/bin/env python
"""Lightweight Unreal Build.cs dependency resolver suggestions."""

from __future__ import annotations

import json
import re
from typing import Any


MODULE_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("GameplayTags", ("GameplayTagContainer.h", "FGameplayTag", "FGameplayTagContainer", "UGameplayTagsManager")),
    (
        "EnhancedInput",
        (
            "InputAction.h",
            "InputMappingContext.h",
            "EnhancedInputComponent.h",
            "EnhancedInputSubsystems.h",
            "UInputAction",
            "UInputMappingContext",
            "UEnhancedInputComponent",
        ),
    ),
    ("UMG", ("UserWidget.h", "UUserWidget")),
    ("Niagara", ("NiagaraComponent.h", "UNiagaraComponent", "UNiagaraSystem")),
    ("AIModule", ("AIController.h", "BehaviorTree", "UBehaviorTree", "UBlackboardComponent")),
    ("NavigationSystem", ("NavigationSystem.h", "UNavigationSystemV1")),
    ("Slate", ("SWidget", "SlateBasics.h")),
    ("SlateCore", ("SlateCore.h",)),
    ("MovieScene", ("MovieScene.h", "UMovieScene")),
    ("LevelSequence", ("LevelSequence.h", "ULevelSequence")),
    ("Projects", ("Interfaces/IPluginManager.h", "IPluginManager")),
    ("InputCore", ("InputCoreTypes.h", "EKeys")),
]


def _contains_token(text: str, token: str) -> bool:
    if token.endswith(".h"):
        return token.lower() in text.lower()
    return re.search(rf"\b{re.escape(token)}\b", text) is not None


def resolve_modules_from_text(text: str) -> list[str]:
    """Return Unreal module names inferred from headers/types in text."""
    found: list[str] = []
    for module, tokens in MODULE_PATTERNS:
        if any(_contains_token(text, token) for token in tokens):
            found.append(module)
    return found


def resolve_modules_from_error(message: str) -> list[str]:
    """Return module suggestions from compiler/linker error text."""
    return resolve_modules_from_text(message)


def build_cs_has_module(build_cs_text: str, module: str) -> bool:
    """Return true if a Build.cs dependency list already contains module."""
    return re.search(rf'"{re.escape(module)}"', build_cs_text) is not None


def _find_dependency_block(build_cs_text: str, public: bool) -> tuple[int, int, str]:
    names = (
        ("PublicDependencyModuleNames", "PublicDependencyModuleNames.AddRange")
        if public
        else ("PrivateDependencyModuleNames", "PrivateDependencyModuleNames.AddRange")
    )
    for name in names:
        idx = build_cs_text.find(name)
        if idx < 0:
            continue
        open_idx = build_cs_text.find("{", idx)
        close_idx = build_cs_text.find("}", open_idx)
        if open_idx >= 0 and close_idx >= 0:
            return open_idx, close_idx, name
    return -1, -1, ""


def suggest_build_cs_dependency_patch(build_cs_text: str, module: str, public: bool = False) -> dict[str, Any]:
    """Return a deterministic suggestion for adding a module dependency.

    This module never edits files. Callers can decide whether to apply the
    returned replacement after reading the real Build.cs.
    """
    module = str(module or "").strip()
    if not module:
        return {"ok": False, "reason": "empty module", "module": module}
    if build_cs_has_module(build_cs_text, module):
        return {"ok": True, "alreadyPresent": True, "module": module, "public": public}

    open_idx, close_idx, block_name = _find_dependency_block(build_cs_text, public)
    if open_idx < 0:
        target = "PublicDependencyModuleNames" if public else "PrivateDependencyModuleNames"
        return {
            "ok": False,
            "reason": f"{target} AddRange block not found",
            "module": module,
            "public": public,
        }

    block = build_cs_text[open_idx : close_idx + 1]
    existing = re.findall(r'"([^"]+)"', block)
    modules = sorted({*existing, module})
    indent_match = re.search(r"\n([ \t]+)\"", block)
    indent = indent_match.group(1) if indent_match else "                "
    replacement = "{\n" + ",\n".join(f'{indent}"{item}"' for item in modules) + "\n            }"
    return {
        "ok": True,
        "alreadyPresent": False,
        "module": module,
        "public": public,
        "blockName": block_name,
        "oldText": block,
        "newText": replacement,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Suggest Unreal Build.cs module dependencies from text.")
    parser.add_argument("text", nargs="*", help="Compiler error or code text.")
    args = parser.parse_args()
    print(json.dumps(resolve_modules_from_error(" ".join(args.text)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
