#!/usr/bin/env python
"""Parse Unreal Engine ModuleRules Build.cs dependency declarations."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

DEPENDENCY_KINDS = (
    "PublicDependencyModuleNames",
    "PrivateDependencyModuleNames",
    "PublicIncludePathModuleNames",
    "PrivateIncludePathModuleNames",
    "DynamicallyLoadedModuleNames",
)

QUOTED_RE = re.compile(r'"([^"]+)"')

# PublicDependencyModuleNames.Add("X") or .AddRange(...)
ADD_CALL_RE = re.compile(
    r"(?P<kind>PublicDependencyModuleNames|PrivateDependencyModuleNames|"
    r"PublicIncludePathModuleNames|PrivateIncludePathModuleNames|DynamicallyLoadedModuleNames)"
    r"\s*\.\s*(?P<method>AddRange|Add)\s*\(\s*(?P<args>.*?)\s*\)\s*;",
    re.DOTALL,
)

EDITOR_CONDITION_RE = re.compile(
    r"if\s*\(\s*Target\.bBuildEditor\s*\)\s*\{(?P<body>.*?)\}",
    re.DOTALL,
)


def _extract_modules_from_args(args: str) -> list[str]:
    args = args.strip()
    if not args:
        return []
    # Add("Module") single string
    single = QUOTED_RE.findall(args)
    if single and "new" not in args.lower() and "[" not in args:
        return list(dict.fromkeys(single))
    # AddRange(new string[] { ... }) or AddRange(new[] { ... })
    return list(dict.fromkeys(QUOTED_RE.findall(args)))


def parse_build_cs_text(text: str, module_name: str = "") -> dict[str, Any]:
    """Parse Build.cs content into dependencies and conditional blocks."""
    dependencies: dict[str, list[str]] = {kind: [] for kind in DEPENDENCY_KINDS}
    conditional: list[dict[str, Any]] = []
    editor_spans = [(m.start(), m.end()) for m in EDITOR_CONDITION_RE.finditer(text)]

    for match in ADD_CALL_RE.finditer(text):
        if any(start <= match.start() < end for start, end in editor_spans):
            continue
        kind = match.group("kind")
        modules = _extract_modules_from_args(match.group("args"))
        for mod in modules:
            if mod not in dependencies[kind]:
                dependencies[kind].append(mod)

    for match in EDITOR_CONDITION_RE.finditer(text):
        body = match.group("body")
        block_deps: dict[str, list[str]] = {kind: [] for kind in DEPENDENCY_KINDS}
        for inner in ADD_CALL_RE.finditer(body):
            kind = inner.group("kind")
            for mod in _extract_modules_from_args(inner.group("args")):
                if mod not in block_deps[kind]:
                    block_deps[kind].append(mod)
        if any(block_deps[k] for k in DEPENDENCY_KINDS):
            conditional.append(
                {
                    "condition": "Target.bBuildEditor",
                    "editor_only": True,
                    "dependencies": block_deps,
                }
            )

    # Drop empty kinds from main dependencies dict for cleaner output
    dependencies = {k: v for k, v in dependencies.items() if v}

    return {
        "module_name": module_name,
        "dependencies": dependencies,
        "conditional_dependencies": conditional,
    }


def parse_build_cs_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
    module_name = path.name.removesuffix(".Build.cs") if path.name.endswith(".Build.cs") else path.stem
    return parse_build_cs_text(text, module_name)


def parse_build_deps(path: Path) -> dict[str, list[str]]:
    """Backward-compatible: return flat dependencies dict only."""
    return parse_build_cs_file(path).get("dependencies") or {}


def public_modules_from_text(text: str) -> set[str]:
    parsed = parse_build_cs_text(text)
    public: set[str] = set(parsed.get("dependencies", {}).get("PublicDependencyModuleNames", []))
    for block in parsed.get("conditional_dependencies") or []:
        public.update(block.get("dependencies", {}).get("PublicDependencyModuleNames", []))
    return public


def declared_modules_from_text(text: str) -> set[str]:
    parsed = parse_build_cs_text(text)
    found: set[str] = set()
    for deps in parsed.get("dependencies", {}).values():
        found.update(deps)
    for block in parsed.get("conditional_dependencies") or []:
        for deps in block.get("dependencies", {}).values():
            found.update(deps)
    return found


def format_dependency_lines(parsed: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for key, values in sorted((parsed.get("dependencies") or {}).items()):
        lines.append(f"{key}: {', '.join(values) if values else '(empty)'}")
    for block in parsed.get("conditional_dependencies") or []:
        cond = block.get("condition", "conditional")
        for key, values in sorted((block.get("dependencies") or {}).items()):
            if values:
                lines.append(f"[{cond}] {key}: {', '.join(values)}")
    return lines
