#!/usr/bin/env python
"""Ceiling-tier Pass@K fixtures and config validation."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from eval_e2e_compile import DEFAULT_UBT  # noqa: E402
from lmstudio_unreal_wrapper import (  # noqa: E402
    declared_build_modules,
    has_static_errors,
    validate_unreal_readiness,
)

CEILING_CONFIG = ROOT / "config" / "rag_eval_pass_at_k_ceiling_cases.json"

LOG_ONLY_GIVEAWAYS = (
    "publicdependencymodulenames",
    "gameplaytags를 추가",
    "generated.h를 마지막",
    "build.cs에",
    "add gameplaytags",
)


def _load_ceiling_cases() -> list[dict]:
    payload = json.loads(CEILING_CONFIG.read_text(encoding="utf-8-sig"))
    return list(payload.get("cases") or [])


@pytest.mark.parametrize("case", _load_ceiling_cases(), ids=lambda c: c["id"])
def test_ceiling_request_log_only_has_no_solution_hints(case: dict):
    fixture_dir = ROOT / case["fixtureDir"]
    request_path = fixture_dir / str(case.get("requestFile") or "request.txt")
    text = request_path.read_text(encoding="utf-8-sig").lower()
    assert "ubt compile failed" in text or "log excerpt" in text
    for giveaway in LOG_ONLY_GIVEAWAYS:
        assert giveaway not in text, f"{case['id']} request leaks hint: {giveaway}"


CEILING_MODULE_DEPS = {
    "missing_gameplaytags_dep": "GameplayTags",
    "missing_enhancedinput_dep": "EnhancedInput",
    "missing_aimodule_dep": "AIModule",
    "missing_umg_dep": "UMG",
    "missing_niagara_dep": "Niagara",
    "missing_navigation_system_dep": "NavigationSystem",
    "missing_inputcore_dep": "InputCore",
    "missing_slate_dep": "Slate",
    "missing_movie_scene_dep": "MovieScene",
    "missing_levelsequence_dep": "LevelSequence",
}

MODULE_FIX_STATIC_CODES = {
    "missing_gameplaytags_dep": {"POSSIBLE_MISSING_MODULE"},
    "missing_enhancedinput_dep": {"MISSING_ENHANCED_INPUT_MODULE"},
    "missing_aimodule_dep": {"POSSIBLE_MISSING_MODULE"},
    "missing_umg_dep": {"POSSIBLE_MISSING_MODULE"},
    "missing_niagara_dep": {"POSSIBLE_MISSING_MODULE"},
}

NON_MODULE_STATIC_CODES = {
    "missing_generated_h_log_only": {"GENERATED_H_MISSING"},
    "editor_only_include_runtime": {"EDITOR_ONLY_INCLUDE_IN_RUNTIME_MODULE"},
}

UBT_ONLY_CEILING_CASES = {"cpp_header_signature_log_only"}


def _broken_build_cs(fixture_dir: Path) -> Path:
    matches = [path for path in fixture_dir.rglob("*.Build.cs") if "golden" not in path.parts]
    assert len(matches) == 1, f"expected one broken Build.cs under {fixture_dir}"
    return matches[0]


def _golden_build_cs(fixture_dir: Path) -> Path:
    matches = list((fixture_dir / "golden").rglob("*.Build.cs"))
    assert len(matches) == 1, f"expected one golden Build.cs under {fixture_dir}/golden"
    return matches[0]


def _module_fix_cases() -> list[dict]:
    return [case for case in _load_ceiling_cases() if case.get("mode") == "module_fix"]


@pytest.mark.parametrize("case", _module_fix_cases(), ids=lambda c: c["id"])
def test_ceiling_broken_build_cs_missing_module(case: dict):
    fixture_dir = ROOT / case["fixtureDir"]
    module_name = CEILING_MODULE_DEPS[case["id"]]
    broken = _broken_build_cs(fixture_dir).read_text(encoding="utf-8-sig")
    golden = _golden_build_cs(fixture_dir).read_text(encoding="utf-8-sig")
    assert module_name not in declared_build_modules(broken)
    assert module_name in declared_build_modules(golden)


@pytest.mark.parametrize("case", _load_ceiling_cases(), ids=lambda c: c["id"])
def test_ceiling_broken_fixture_has_static_or_compile_surface(case: dict):
    fixture_dir = ROOT / case["fixtureDir"]
    if case["id"] in UBT_ONLY_CEILING_CASES:
        assert (fixture_dir / "golden").is_dir(), f"{case['id']}: golden/ required for UBT-only ceiling case"
        return
    findings = validate_unreal_readiness(fixture_dir, None)
    codes = {f.code for f in findings}
    expected = MODULE_FIX_STATIC_CODES.get(case["id"]) or NON_MODULE_STATIC_CODES.get(case["id"])
    if expected:
        assert expected & codes, f"{case['id']}: expected one of {expected}, got {codes}"
    elif case.get("mode") == "module_fix" and not (has_static_errors(findings) or findings):
        pytest.skip(f"{case['id']}: no static surface without module graph (Build.cs gap asserted separately)")
    else:
        assert has_static_errors(findings) or findings, f"{case['id']}: no static findings"


@pytest.mark.parametrize("case", _load_ceiling_cases(), ids=lambda c: c["id"])
def test_ceiling_golden_fixture_exists(case: dict):
    golden = ROOT / case["fixtureDir"] / "golden"
    assert golden.is_dir(), f"missing golden/: {case['id']}"
    assert any(golden.rglob("*")), f"empty golden/: {case['id']}"


@pytest.mark.skipif(not DEFAULT_UBT.is_file(), reason="UBT not installed")
@pytest.mark.slow
def test_ceiling_dry_run_golden_ubt():
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "eval_pass_at_k.py"),
            "--dry-run",
            "--config",
            str(CEILING_CONFIG),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")


def test_ceiling_config_tier_and_case_count():
    payload = json.loads(CEILING_CONFIG.read_text(encoding="utf-8-sig"))
    assert payload.get("tier") == "ceiling"
    cases = list(payload.get("cases") or [])
    assert len(cases) == 13
    module_cases = [case for case in cases if case.get("mode") == "module_fix"]
    assert len(module_cases) == 10
    assert {case["id"] for case in module_cases} == set(CEILING_MODULE_DEPS)
    assert {case["id"] for case in cases if case.get("mode") != "module_fix"} == {
        "missing_generated_h_log_only",
        "cpp_header_signature_log_only",
        "editor_only_include_runtime",
    }
    assert payload.get("defaults", {}).get("reportPassAt1") is True
