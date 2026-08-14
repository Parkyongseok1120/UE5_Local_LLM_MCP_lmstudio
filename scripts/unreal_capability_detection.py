#!/usr/bin/env python
"""Evidence-based Unreal/project capability detection without version guessing."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from unreal_source_extensions import UNREAL_CPP_SUFFIXES

AUTOMATION_PATTERNS = (
    re.compile(
        r'\bIMPLEMENT_CUSTOM_[A-Z0-9_]*AUTOMATION_TEST\s*'
        r'\([^,]+,\s*[^,]+,\s*(?:TEXT\s*\(\s*)?"([^"]+)"\s*\)?'
    ),
    re.compile(
        r'\bIMPLEMENT_(?!CUSTOM_)[A-Z0-9_]*AUTOMATION_TEST\s*'
        r'\([^,]+,\s*(?:TEXT\s*\(\s*)?"([^"]+)"\s*\)?'
    ),
    re.compile(
        r'\b(?:BEGIN_DEFINE_SPEC|DEFINE_SPEC)\s*'
        r'\([^,]+,\s*(?:TEXT\s*\(\s*)?"([^"]+)"\s*\)?'
    ),
)
CQTEST_PATTERNS = (
    re.compile(
        r'\bTEST\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*'
        r'(?:TEXT\s*\(\s*)?"([^"]+)"\s*\)?'
    ),
    re.compile(
        r'\bTEST_CLASS(?:_WITH_(?:ASSERTS|BASE|FLAGS|BASE_AND_FLAGS))?\s*'
        r'\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*'
        r'(?:TEXT\s*\(\s*)?"([^"]+)"\s*\)?'
    ),
)
AUTOMATION_SOURCE_EXTENSIONS = UNREAL_CPP_SUFFIXES

FEATURE_PROBES: dict[str, tuple[str, ...]] = {
    "enhancedInput": ("EnhancedInput",),
    "gameplayAbilities": ("GameplayAbilities", "GameplayTags", "GameplayTasks"),
    "niagara": ("Niagara",),
    "commonUI": ("CommonUI",),
    "worldPartition": ("WorldPartition",),
}


def _read_descriptor(project_file: Path) -> tuple[dict[str, Any], list[str]]:
    try:
        payload = json.loads(project_file.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {}, [f"project descriptor unreadable: {exc}"]
    if not isinstance(payload, dict):
        return {}, ["project descriptor root must be a JSON object"]
    return payload, []


def _engine_version(engine_root: Path) -> tuple[str, str]:
    build_version = engine_root / "Engine" / "Build" / "Build.version"
    try:
        payload = json.loads(build_version.read_text(encoding="utf-8-sig"))
        return f"{int(payload['MajorVersion'])}.{int(payload['MinorVersion'])}", str(build_version)
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return "", ""


def _first_file(candidates: Iterable[Path]) -> Path | None:
    return next((path.resolve() for path in candidates if path.is_file()), None)


def _build_tool(engine_root: Path, host_platform: str) -> Path | None:
    batch = engine_root / "Engine" / "Build" / "BatchFiles"
    ubt = engine_root / "Engine" / "Binaries" / "DotNET" / "UnrealBuildTool"
    if host_platform == "win32":
        candidates = (ubt / "UnrealBuildTool.exe", batch / "Build.bat", ubt / "UnrealBuildTool.dll")
    elif host_platform == "darwin":
        candidates = (batch / "Mac" / "Build.sh", batch / "Build.sh", ubt / "UnrealBuildTool.dll")
    else:
        candidates = (batch / "Linux" / "Build.sh", batch / "Build.sh", ubt / "UnrealBuildTool.dll")
    return _first_file(candidates)


def _editor_cmd(engine_root: Path, host_platform: str) -> Path | None:
    binaries = engine_root / "Engine" / "Binaries"
    if host_platform == "win32":
        candidates = (binaries / "Win64" / "UnrealEditor-Cmd.exe",)
    elif host_platform == "darwin":
        candidates = (
            binaries / "Mac" / "UnrealEditor-Cmd",
            binaries / "Mac" / "UnrealEditor.app" / "Contents" / "MacOS" / "UnrealEditor",
        )
    else:
        candidates = (binaries / "Linux" / "UnrealEditor-Cmd", binaries / "Linux" / "UnrealEditor")
    return _first_file(candidates)


def _plugin_names(
    descriptor: dict[str, Any],
    project_root: Path,
    engine_root: Path | None,
) -> set[str]:
    names = {
        str(item.get("Name") or "")
        for item in descriptor.get("Plugins") or []
        if isinstance(item, dict) and item.get("Enabled") is not False
    }
    bases = [project_root / "Plugins"]
    if engine_root is not None:
        bases.append(engine_root / "Engine" / "Plugins")
    for base in bases:
        if not base.is_dir():
            continue
        try:
            for plugin in base.rglob("*.uplugin"):
                names.add(plugin.stem)
        except OSError:
            continue
    return {item for item in names if item}


def _module_names(descriptor: dict[str, Any], project_root: Path) -> set[str]:
    names = {
        str(item.get("Name") or "")
        for item in descriptor.get("Modules") or []
        if isinstance(item, dict)
    }
    for source in (project_root / "Source", project_root / "Plugins"):
        if not source.is_dir():
            continue
        try:
            names.update(path.stem.replace(".Build", "") for path in source.rglob("*.Build.cs"))
        except OSError:
            pass
    return {item for item in names if item}


def _cpp_code_offsets(text: str) -> bytearray:
    code = bytearray(len(text))
    index = 0
    while index < len(text):
        if text.startswith("//", index):
            newline = text.find("\n", index + 2)
            index = len(text) if newline < 0 else newline
            continue
        if text.startswith("/*", index):
            close = text.find("*/", index + 2)
            index = len(text) if close < 0 else close + 2
            continue
        if text.startswith('R"', index):
            open_paren = text.find("(", index + 2)
            if 0 <= open_paren - (index + 2) <= 16:
                delimiter = text[index + 2 : open_paren]
                close = text.find(f'){delimiter}"', open_paren + 1)
                if close >= 0:
                    index = close + len(delimiter) + 2
                    continue
        if text[index] in {'"', "'"}:
            quote = text[index]
            index += 1
            while index < len(text):
                if text[index] == "\\":
                    index += 2
                    continue
                if text[index] == quote:
                    index += 1
                    break
                index += 1
            continue
        code[index] = 1
        index += 1
    return code


def _macro_starts_in_code(text: str, code_offsets: bytearray, index: int) -> bool:
    if not code_offsets[index]:
        return False
    logical_line_start = text.rfind("\n", 0, index) + 1
    while logical_line_start > 0:
        previous_line_end = logical_line_start - 1
        previous_line_start = text.rfind("\n", 0, previous_line_end) + 1
        if re.search(r"\\\s*$", text[previous_line_start:previous_line_end]) is None:
            break
        logical_line_start = previous_line_start
    return re.match(r"\s*#\s*define\b", text[logical_line_start:index]) is None


def _cqtest_registered_root(test_name: str, test_directory: str) -> str:
    name = str(test_name or "").strip().strip(".")
    directory = str(test_directory or "").strip().strip(".")
    return f"{directory}.{name}" if directory and name else ""


def _automation_tests(project_root: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    roots = [project_root / "Source", project_root / "Plugins"]
    for root in roots:
        if not root.is_dir():
            continue
        try:
            paths = sorted(
                (
                    path
                    for path in root.rglob("*")
                    if path.is_file()
                    and path.suffix.casefold() in AUTOMATION_SOURCE_EXTENSIONS
                ),
                key=lambda path: path.as_posix().casefold(),
            )
        except OSError:
            continue
        for path in paths:
            try:
                text = path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            code_offsets = _cpp_code_offsets(text)
            for pattern in AUTOMATION_PATTERNS:
                for match in pattern.finditer(text):
                    if not _macro_starts_in_code(text, code_offsets, match.start()):
                        continue
                    rows.append(
                        {
                            "name": match.group(1),
                            "path": path.relative_to(project_root).as_posix(),
                        }
                    )
            for pattern in CQTEST_PATTERNS:
                for match in pattern.finditer(text):
                    if not _macro_starts_in_code(text, code_offsets, match.start()):
                        continue
                    registered_root = _cqtest_registered_root(
                        match.group(1),
                        match.group(2),
                    )
                    if registered_root:
                        rows.append(
                            {
                                "name": registered_root,
                                "path": path.relative_to(project_root).as_posix(),
                            }
                        )
    unique: dict[str, dict[str, str]] = {}
    for row in rows:
        unique.setdefault(row["name"].casefold(), row)
    return list(unique.values())


def _feature_probe(
    feature: str,
    markers: tuple[str, ...],
    *,
    plugins: set[str],
    project_root: Path,
    engine_root: Path | None,
) -> dict[str, Any]:
    plugin_hits = sorted(
        plugin for plugin in plugins if any(marker.casefold() in plugin.casefold() for marker in markers)
    )
    file_hits: list[str] = []
    search_roots = [project_root / "Source", project_root / "Plugins"]
    if engine_root is not None:
        search_roots.append(engine_root / "Engine" / "Plugins")
    for marker in markers:
        for base in search_roots:
            if not base.is_dir():
                continue
            candidates = (
                base / marker,
                base / "Runtime" / marker,
                base / "Experimental" / marker,
                base / "FX" / marker,
                base / "Editor" / marker,
                base / "Marketplace" / marker,
            )
            hit = next((item for item in candidates if item.exists()), None)
            if hit:
                file_hits.append(str(hit.resolve()))
                break
    available = bool(plugin_hits or file_hits)
    return {
        "available": available,
        "evidence": {
            "plugins": plugin_hits[:16],
            "paths": file_hits[:16],
        },
        "detection": "descriptor_or_filesystem",
        "feature": feature,
    }


def detect_unreal_capabilities(
    project_file: str | Path,
    *,
    engine_root: str | Path | None = None,
    host_platform: str | None = None,
) -> dict[str, Any]:
    host = str(host_platform or sys.platform)
    project = Path(project_file).expanduser().resolve()
    descriptor, issues = _read_descriptor(project) if project.is_file() else ({}, ["project file missing"])
    root = project.parent
    explicit_engine = str(engine_root or "").strip()
    engine_candidate = (
        Path(explicit_engine).expanduser().resolve()
        if explicit_engine
        else None
    )
    engine = (
        engine_candidate
        if engine_candidate is not None and (engine_candidate / "Engine").is_dir()
        else None
    )
    if explicit_engine and engine is None:
        issues.append("engine root is missing or does not contain Engine/")
    version, version_evidence = _engine_version(engine) if engine is not None else ("", "")
    build_tool = _build_tool(engine, host) if engine is not None else None
    editor = _editor_cmd(engine, host) if engine is not None else None
    plugins = _plugin_names(descriptor, root, engine)
    modules = _module_names(descriptor, root)
    automation = _automation_tests(root)
    features = {
        name: _feature_probe(
            name,
            markers,
            plugins=plugins,
            project_root=root,
            engine_root=engine,
        )
        for name, markers in FEATURE_PROBES.items()
    }
    graph_plugin = next((item for item in plugins if item.casefold() == "lmstudiographexporter"), "")
    return {
        "ok": not issues,
        "issues": issues,
        "hostPlatform": host,
        "projectFile": str(project) if project.is_file() else "",
        "engineRoot": str(engine) if engine is not None else "",
        "engineVersion": version,
        "engineVersionEvidence": version_evidence,
        "engineAssociation": str(descriptor.get("EngineAssociation") or ""),
        "project": {
            "sourceAvailable": (root / "Source").is_dir(),
            "modules": sorted(modules),
            "plugins": sorted(plugins),
        },
        "execution": {
            "buildAvailable": build_tool is not None,
            "buildTool": str(build_tool or ""),
            "editorCommandletAvailable": editor is not None,
            "editorCmd": str(editor or ""),
            "automationDeclared": bool(automation),
            "automationTests": automation[:128],
        },
        "assetIntrospection": {
            "pythonExportSourceAvailable": True,
            "cppGraphExporterInstalled": bool(graph_plugin),
            "cppGraphExporterPlugin": graph_plugin,
        },
        "features": features,
        "proofBoundary": (
            "Capabilities are inferred from the current descriptor, installed files, executables, and source declarations. "
            "A version number alone never marks a feature available."
        ),
    }


__all__ = ["detect_unreal_capabilities"]
