#!/usr/bin/env python3
"""Targeted autofixes for holdout blind-spot cases."""

from __future__ import annotations

import re
from pathlib import Path

from unreal_static_validate import (
    Finding,
    build_cs_text,
    declared_build_modules,
    iter_source_files,
    public_build_modules,
    read_text,
    validate_build_modules,
)

MODULE_TOKEN_MAP: dict[str, tuple[str, ...]] = {
    "GameplayTags": ("FGameplayTag", "FGameplayTagContainer", "GameplayTagContainer.h"),
    "UMG": ("UUserWidget", "UWidget", "Blueprint/UserWidget.h"),
    "Niagara": ("UNiagaraComponent", "UNiagaraSystem", "NiagaraComponent.h"),
    "AIModule": ("AAIController", "AIController.h"),
    "NavigationSystem": ("NavigationSystem.h", "UNavigationSystemV1"),
    "LevelSequence": ("LevelSequence.h", "ULevelSequence"),
    "EnhancedInput": ("EnhancedInput", "ETriggerEvent", "BindAction"),
    "Projects": ("IPluginManager", "PluginManager.h"),
}


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _matching_cpp_for_header(header: Path) -> Path | None:
    if header.suffix.lower() not in {".h", ".hpp"}:
        return None
    cpp_same = header.with_suffix(".cpp")
    if cpp_same.is_file():
        return cpp_same
    module_private = header.parent.parent / "Private" / f"{header.stem}.cpp"
    return module_private if module_private.is_file() else None


def apply_blueprint_native_event_impl_autofix(root: Path, findings: list[Finding]) -> list[Path]:
    written: list[Path] = []
    targets = {
        (root / finding.path).resolve()
        for finding in findings
        if finding.code in {"BLUEPRINT_NATIVE_EVENT_IMPL_MISSING", "BLUEPRINT_NATIVE_EVENT_MANUAL_IMPL_DECL"}
    }
    for header_path in sorted(targets, key=lambda p: str(p)):
        if not header_path.is_file():
            continue
        header_text = read_text(header_path)
        if finding_manual := re.search(
            r"^\s*(?:virtual\s+)?void\s+(\w+)_Implementation\s*\([^;]*\)\s*(?:override\s*)?;\s*$",
            header_text,
            re.MULTILINE,
        ):
            cleaned_lines = [
                line
                for line in header_text.splitlines()
                if not re.search(rf"\b{finding_manual.group(1)}_Implementation\s*\(", line)
            ]
            cleaned = "\n".join(cleaned_lines) + ("\n" if header_text.endswith("\n") else "")
            if cleaned != header_text:
                write_file(header_path, cleaned)
                written.append(header_path)
                header_text = cleaned
        for match in re.finditer(
            r"UFUNCTION\s*\([^)]*BlueprintNativeEvent[^)]*\)[^\n;]*\n\s*(?:virtual\s+)?void\s+(\w+)\s*\(",
            header_text,
        ):
            event_name = match.group(1)
            cpp_path = _matching_cpp_for_header(header_path)
            if not cpp_path:
                continue
            cpp_text = read_text(cpp_path)
            impl = f"{event_name}_Implementation"
            if re.search(rf"\b\w+::{re.escape(impl)}\s*\(", cpp_text):
                continue
            class_match = re.search(r"UCLASS[^;]*\n\s*class\s+\w+_API\s+(\w+)", header_text)
            class_name = class_match.group(1) if class_match else header_path.stem
            addition = f"\nvoid {class_name}::{impl}()\n{{\n}}\n"
            updated = cpp_text.rstrip() + addition
            write_file(cpp_path, updated)
            written.append(cpp_path)
    return written


def apply_blueprint_implementable_event_strip_autofix(root: Path, findings: list[Finding]) -> list[Path]:
    written: list[Path] = []
    for finding in findings:
        if finding.code != "BLUEPRINT_IMPLEMENTABLE_EVENT_INVALID_IMPL":
            continue
        cpp_path = (root / finding.path).resolve()
        if not cpp_path.is_file():
            continue
        text = read_text(cpp_path)
        updated = re.sub(
            r"^[\t ]*void\s+\w+::\w+_Implementation\s*\([^)]*\)\s*\{[^}]*\}\s*\n?",
            "",
            text,
            flags=re.MULTILINE,
        )
        if updated != text:
            write_file(cpp_path, updated)
            written.append(cpp_path)
    return written


def apply_build_module_autofix(root: Path, findings: list[Finding]) -> list[Path]:
    written: list[Path] = []
    build_paths = [path for path in iter_source_files(root) if path.name.endswith(".Build.cs")]
    if not build_paths:
        return written
    build_path = build_paths[0]
    build_text = read_text(build_path)
    declared = declared_build_modules(build_text)
    source_text = "\n".join(read_text(path) for path in iter_source_files(root))
    candidates: list[str] = []
    for module, tokens in MODULE_TOKEN_MAP.items():
        if module in declared:
            continue
        if sum(1 for token in tokens if token in source_text) >= 1:
            candidates.append(module)
    if len(candidates) != 1:
        return written
    module = candidates[0]
    needle = "PublicDependencyModuleNames.AddRange(new string[] {"
    if needle not in build_text:
        return written
    insert = f'        "{module}",\n'
    updated = build_text.replace(
        needle + '\n            "Core",',
        needle + '\n            "Core",\n' + insert,
        1,
    )
    if updated == build_text:
        updated = build_text.replace(
            'PrivateDependencyModuleNames.AddRange(new string[] {',
            'PrivateDependencyModuleNames.AddRange(new string[] {\n' + insert,
            1,
        )
    if updated != build_text:
        write_file(build_path, updated)
        written.append(build_path)
    return written


def apply_component_include_autofix(root: Path, findings: list[Finding]) -> list[Path]:
    written: list[Path] = []
    component_map = {
        "UBoxComponent": "Components/BoxComponent.h",
        "USphereComponent": "Components/SphereComponent.h",
    }
    for path in iter_source_files(root):
        if path.suffix.lower() not in {".cpp", ".c", ".cc", ".h", ".hpp"}:
            continue
        text = read_text(path)
        updated = text
        for symbol, include_path in component_map.items():
            if symbol not in text:
                continue
            if f'"{include_path}"' in text or f"<{include_path}>" in text:
                continue
            if f'"{symbol}.h"' in text:
                updated = updated.replace(f'#include "{symbol}.h"', f'#include "{include_path}"')
            elif "CreateDefaultSubobject<" + symbol in text:
                lines = updated.splitlines()
                insert_at = 0
                for index, line in enumerate(lines):
                    if line.strip().startswith("#include "):
                        insert_at = index + 1
                lines.insert(insert_at, f'#include "{include_path}"')
                updated = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
        if updated != text:
            write_file(path, updated)
            written.append(path)
    return written


def apply_include_path_autofix(root: Path, findings: list[Finding]) -> list[Path]:
    written: list[Path] = []
    for finding in findings:
        if finding.code != "INCLUDE_PATH_NOT_FOUND":
            continue
        path = (root / finding.path).resolve()
        if not path.is_file():
            continue
        text = read_text(path)
        updated = text
        for bare in ("BoxComponent.h", "SphereComponent.h"):
            if bare not in text:
                continue
            updated = updated.replace(f'#include "{bare}"', f'#include "Components/{bare}"')
            updated = updated.replace(f"#include <{bare}>", f'#include "Components/{bare}"')
        if updated != text:
            write_file(path, updated)
            written.append(path)
    return written
