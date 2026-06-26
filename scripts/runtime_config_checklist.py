#!/usr/bin/env python
"""Deterministic runtime/config readiness checks for Unreal projects."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

BIND_ACTION_RE = re.compile(r'BindAction\s*\(\s*"([^"]+)"', re.MULTILINE)
BIND_AXIS_RE = re.compile(r'BindAxis\s*\(\s*"([^"]+)"', re.MULTILINE)
ACTION_MAPPING_RE = re.compile(r"\+ActionMappings=\(ActionName=([^,\)]+)", re.IGNORECASE)
AXIS_MAPPING_RE = re.compile(r"\+AxisMappings=\(AxisName=([^,\)]+)", re.IGNORECASE)
GAME_MODE_RE = re.compile(
    r"(?:GlobalDefaultGameMode|GameModeMap.*GameMode)=([^\s\r\n]+)",
    re.IGNORECASE,
)
ENHANCED_INPUT_CPP_RE = re.compile(r"EnhancedInputComponent|UEnhancedInput", re.IGNORECASE)
LEGACY_BIND_RE = re.compile(r"BindAction\s*\(|BindAxis\s*\(", re.MULTILINE)


def _read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp949", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except (OSError, UnicodeDecodeError):
            continue
    return ""


def _collect_cpp_bindings(source_root: Path) -> tuple[set[str], set[str]]:
    actions: set[str] = set()
    axes: set[str] = set()
    if not source_root.is_dir():
        return actions, axes
    for path in source_root.rglob("*.cpp"):
        if "Intermediate" in path.parts or "Binaries" in path.parts:
            continue
        text = _read_text(path)
        actions.update(BIND_ACTION_RE.findall(text))
        axes.update(BIND_AXIS_RE.findall(text))
    return actions, axes


def _ini_mappings(config_dir: Path) -> tuple[set[str], set[str], str]:
    input_ini = config_dir / "DefaultInput.ini"
    if not input_ini.is_file():
        return set(), set(), ""
    text = _read_text(input_ini)
    actions = {m.strip() for m in ACTION_MAPPING_RE.findall(text)}
    axes = {m.strip() for m in AXIS_MAPPING_RE.findall(text)}
    return actions, axes, text


def check_runtime_config(project_root: Path | str) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    if root.suffix.lower() == ".uproject":
        root = root.parent

    issues: list[str] = []
    warnings: list[str] = []
    passed: list[str] = []
    details: dict[str, Any] = {}

    config_dir = root / "Config"
    game_ini = config_dir / "DefaultGame.ini"
    engine_ini = config_dir / "DefaultEngine.ini"

    cpp_actions, cpp_axes = _collect_cpp_bindings(root / "Source")
    ini_actions, ini_axes, input_text = _ini_mappings(config_dir)
    details["cppActions"] = sorted(cpp_actions)
    details["cppAxes"] = sorted(cpp_axes)
    details["iniActions"] = sorted(ini_actions)
    details["iniAxes"] = sorted(ini_axes)

    game_mode_found = False
    for ini_path in (game_ini, engine_ini):
        if ini_path.is_file():
            gm = GAME_MODE_RE.search(_read_text(ini_path))
            if gm:
                game_mode_found = True
                passed.append(f"GameMode configured in {ini_path.name}: {gm.group(1).strip()}")
                break
    if not game_mode_found:
        issues.append(
            "No GlobalDefaultGameMode/GameModeMap in DefaultGame.ini or DefaultEngine.ini - "
            "custom GameMode BeginPlay (e.g. SpawnEntities) may not run in PIE."
        )

    missing_actions = cpp_actions - ini_actions
    missing_axes = cpp_axes - ini_axes
    if cpp_actions or cpp_axes:
        if missing_actions:
            issues.append(f"BindAction names missing in DefaultInput.ini: {sorted(missing_actions)}")
        if missing_axes:
            issues.append(f"BindAxis names missing in DefaultInput.ini: {sorted(missing_axes)}")
        if not missing_actions and not missing_axes and (cpp_actions or cpp_axes):
            passed.append("C++ BindAction/BindAxis names match DefaultInput.ini mappings.")

    uses_enhanced_default = "EnhancedInput.EnhancedInputComponent" in input_text or (
        engine_ini.is_file() and "EnhancedInput.EnhancedInputComponent" in _read_text(engine_ini)
    )
    legacy_cpp = False
    for path in (root / "Source").rglob("*.cpp"):
        if LEGACY_BIND_RE.search(_read_text(path)):
            legacy_cpp = True
            break
    enhanced_cpp = False
    for path in (root / "Source").rglob("*"):
        if path.suffix.lower() in {".cpp", ".h"} and ENHANCED_INPUT_CPP_RE.search(_read_text(path)):
            enhanced_cpp = True
            break

    if uses_enhanced_default and legacy_cpp and not enhanced_cpp:
        issues.append(
            "DefaultInput uses EnhancedInputComponent but C++ uses legacy BindAction/BindAxis - pick one system."
        )
    elif legacy_cpp and not uses_enhanced_default:
        passed.append("Legacy input: ini and BindAction/BindAxis are consistent.")
    elif enhanced_cpp and uses_enhanced_default:
        passed.append("Enhanced Input: ini default matches EnhancedInput includes.")

    if not (root / "Content").is_dir():
        warnings.append("Content/ folder missing — C++-only prototype (no assets).")

    ok = len(issues) == 0
    return {
        "ok": ok,
        "projectRoot": str(root),
        "issues": issues,
        "warnings": warnings,
        "passed": passed,
        "details": details,
    }


def main() -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Check Unreal project runtime/config readiness.")
    parser.add_argument("--project-root", required=True, help=".uproject path or project root")
    args = parser.parse_args()
    result = check_runtime_config(args.project_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
