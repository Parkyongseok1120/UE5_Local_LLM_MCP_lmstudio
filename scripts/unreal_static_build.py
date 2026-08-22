#!/usr/bin/env python
"""Build.cs ownership and module dependency validators."""

from __future__ import annotations

import json
import re
from pathlib import Path

from parse_build_cs import declared_modules_from_text, public_modules_from_text
from unreal_static_model import (
    CPP_HEADER_SUFFIXES,
    CPP_IMPLEMENTATION_SUFFIXES,
    SOURCE_ONLY_SUFFIXES,
    Finding,
)
from unreal_static_scan import (
    _source_module_root,
    include_lines,
    iter_source_files,
    line_number,
    read_text,
    should_ignore_project_path,
)


def find_build_cs_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.Build.cs") if path.is_file() and not should_ignore_project_path(path))

def build_cs_text(root: Path) -> str:
    parts = []
    for path in find_build_cs_files(root):
        parts.append(read_text(path))
    return "\n".join(parts)

def build_cs_texts_by_module_root(root: Path) -> dict[Path, tuple[Path, str]]:
    return {
        path.parent.resolve(): (path.resolve(), read_text(path))
        for path in find_build_cs_files(root)
    }

def owning_build_cs_text(
    path: Path,
    root: Path,
    module_build_texts: dict[Path, tuple[Path, str]],
    fallback: str = "",
) -> tuple[Path | None, str]:
    module_root = _source_module_root(path.resolve(), root.resolve())
    if module_root is None:
        return None, fallback
    item = module_build_texts.get(module_root.resolve())
    return item if item is not None else (None, "")

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
            owner_modules = [
                str(value)
                for value in metadata.get("owner_modules") or []
                if value and str(value).casefold() != "source"
            ]
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
    if path.suffix.lower() in CPP_HEADER_SUFFIXES and "private" not in parts:
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
                "Do not call InputComponent->BindAction. For legacy input use InputComponent->BindKey / "
                "BindAxis; for Enhanced Input cast to UEnhancedInputComponent and BindAction with ETriggerEvent.",
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
    if (
        uses_enhanced
        and path.suffix.lower() in CPP_IMPLEMENTATION_SUFFIXES
        and "EnhancedInputComponent.h" not in text
    ):
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

def validate_build_modules(
    root: Path,
    source_text: str = "",
    build_text_value: str = "",
    *,
    scope_paths: list[Path] | None = None,
    scope_texts: dict[Path, str] | None = None,
) -> list[Finding]:
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

    def append_missing_modules(module_source: str, module_build: str, rel: str) -> None:
        declared_modules = declared_build_modules(module_build)
        for module_name, tokens in module_rules.items():
            if module_name in declared_modules or not any(token in module_source for token in tokens):
                continue
            severity = "error" if module_name == "GameplayTags" else "warning"
            findings.append(
                Finding(
                    severity,
                    rel,
                    1,
                    "POSSIBLE_MISSING_MODULE",
                    f"Code in this module appears to use {module_name} types; add or verify the dependency in this Build.cs.",
                )
            )

    build_files = find_build_cs_files(root)
    if not build_files:
        append_missing_modules(source_text, build_text_value, "Source/*.Build.cs")
        return findings

    all_source_paths = [
        path.resolve()
        for path in iter_source_files(root)
        if path.suffix.lower() in SOURCE_ONLY_SUFFIXES
    ]
    scoped_paths = [Path(path).resolve() for path in scope_paths] if scope_paths is not None else None
    scoped_text_by_path = {
        Path(path).resolve(): text for path, text in (scope_texts or {}).items()
    }

    for build_file in build_files:
        build_file = build_file.resolve()
        module_root = build_file.parent
        module_paths = [
            path for path in all_source_paths if _source_module_root(path, root) == module_root
        ]
        if scoped_paths is not None:
            scoped_module_paths = [
                path
                for path in scoped_paths
                if path.suffix.lower() in SOURCE_ONLY_SUFFIXES
                and _source_module_root(path, root) == module_root
            ]
            build_file_is_scoped = build_file in scoped_paths
            if not scoped_module_paths and not build_file_is_scoped:
                continue
            if not build_file_is_scoped:
                module_paths = scoped_module_paths

        module_source = "\n".join(
            scoped_text_by_path[path] if path in scoped_text_by_path else read_text(path)
            for path in module_paths
        )
        append_missing_modules(
            module_source,
            read_text(build_file),
            str(build_file.relative_to(root.resolve())),
        )
    return findings

def validate_include_owner_modules(
    root: Path,
    build_text_value: str,
    owner_map: dict[str, list[str]],
    *,
    scope_paths: list[Path] | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    if not owner_map:
        return findings
    module_build_texts = build_cs_texts_by_module_root(root)
    local_modules = local_module_names(root)
    source_paths = scope_paths if scope_paths is not None else iter_source_files(root)
    for path in source_paths:
        path = Path(path).resolve()
        if path.suffix.lower() not in SOURCE_ONLY_SUFFIXES:
            continue
        owner_build_file, owner_build_text = owning_build_cs_text(
            path,
            root,
            module_build_texts,
            fallback=build_text_value if not module_build_texts else "",
        )
        declared = declared_build_modules(owner_build_text)
        public_declared = public_build_modules(owner_build_text)
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
                owner_hint = (
                    str(owner_build_file.relative_to(root.resolve()))
                    if owner_build_file is not None
                    else "the owning module Build.cs"
                )
                findings.append(
                    Finding(
                        "warning",
                        str(path.relative_to(root)),
                        line,
                        "MISSING_INCLUDE_OWNER_MODULE",
                        f'Include "{include_path}" belongs to module(s) {", ".join(missing)}; '
                        f"add to {dependency_kind} in {owner_hint}.",
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

__all__ = [
    'find_build_cs_files',
    'build_cs_text',
    'build_cs_texts_by_module_root',
    'owning_build_cs_text',
    'declared_build_modules',
    'public_build_modules',
    'load_include_owner_map',
    'module_name_from_build_file',
    'local_module_names',
    'include_visibility',
    'find_include_owner',
    'validate_enhanced_input',
    'validate_build_modules',
    'validate_include_owner_modules',
]
