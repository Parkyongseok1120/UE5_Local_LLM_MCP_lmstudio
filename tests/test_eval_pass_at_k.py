#!/usr/bin/env python
"""Tests for Pass@K eval helpers."""

from __future__ import annotations

import sys
import json
import subprocess
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
SCRIPTS = WORKSPACE / "scripts"
sys.path.insert(0, str(SCRIPTS))

from eval_pass_at_k import (  # noqa: E402
    build_metrics_only_results,
    calculate_kpi_metrics,
    changed_files_from_diff,
    compose_eval_request,
    count_wrapper_attempts,
    infer_eval_tier,
    patch_target_metrics,
    should_abort_consecutive_failures,
)
from error_taxonomy import route_error_action  # noqa: E402
from lmstudio_unreal_wrapper import align_route_to_eval_mode  # noqa: E402
from eval_e2e_compile import split_ubt_target_spec  # noqa: E402


def test_should_abort_consecutive_failures_requires_all_tail_failures():
    results = [
        {"id": "a", "pass": False},
        {"id": "b", "pass": False},
        {"id": "c", "pass": True},
        {"id": "d", "pass": False},
        {"id": "e", "pass": False},
    ]
    assert should_abort_consecutive_failures(results, 0) is False
    assert should_abort_consecutive_failures(results, 5) is False
    assert should_abort_consecutive_failures(results[:2], 2) is True
    assert should_abort_consecutive_failures(results, 3) is False
    assert should_abort_consecutive_failures(results[-2:], 2) is True


def test_count_wrapper_attempts_handles_missing_dir(tmp_path):
    assert count_wrapper_attempts(tmp_path / "missing") == 0


def test_count_wrapper_attempts_counts_attempt_directories_only(tmp_path):
    run_dir = tmp_path / "wrapper_run"
    run_dir.mkdir()
    (run_dir / "attempt_1").mkdir()
    (run_dir / "attempt_2").mkdir()
    (run_dir / "attempt_notes.txt").write_text("not a directory", encoding="utf-8")
    (run_dir / "other").mkdir()

    assert count_wrapper_attempts(run_dir) == 2


def test_split_ubt_target_spec_accepts_bare_and_full_targets():
    assert split_ubt_target_spec("CompileFixEditor") == ("CompileFixEditor", "Win64", "Development")
    assert split_ubt_target_spec("HoldoutFixtureEditor Win64 Development") == (
        "HoldoutFixtureEditor",
        "Win64",
        "Development",
    )


def test_metrics_only_results_aggregate_retry_state_fixture():
    cases = [
        {"id": "missing_gameplaytags_dep"},
        {"id": "cpp_header_signature_mismatch"},
        {"id": "missing_generated_h"},
    ]

    results = build_metrics_only_results(cases, WORKSPACE / "tests" / "fixtures" / "retry_state_eval")
    metrics = calculate_kpi_metrics(results)

    assert all(row["mode"] == "metrics-only" for row in results)
    assert metrics["sameErrorRepeatedCount"] == 1
    assert metrics["noOpEditCount"] == 1
    assert metrics["repeatedErrorCaseIds"] == ["missing_gameplaytags_dep"]
    assert metrics["noOpCaseIds"] == ["cpp_header_signature_mismatch"]


def test_calculate_kpi_metrics_includes_tier_breakdown():
    results = [
        {
            "id": "module_case",
            "category": "GameplayTags dependency issue",
            "mode": "module_fix",
            "evalTier": "module_fix",
            "pass": True,
            "passAt1": True,
            "attempts": 1,
        },
        {
            "id": "multifile_case",
            "category": "simple multi-file compile refactor",
            "mode": "multifile_refactor",
            "evalTier": "multifile_refactor",
            "pass": True,
            "passAt1": False,
            "attempts": 3,
            "wrongFileEdit": True,
        },
    ]

    metrics = calculate_kpi_metrics(results)

    assert metrics["overall"]["cases"] == 2
    assert metrics["overall"]["pass_at_1"] == 1
    assert metrics["tiers"]["module_fix"]["pass_at_1_rate"] == 1.0
    assert metrics["tiers"]["multifile_refactor"]["cases"] == 1
    assert metrics["tiers"]["multifile_refactor"]["max_attempts_used"] == 3
    assert metrics["tiers"]["multifile_refactor"]["wrong_file_edits"] == 1


def test_infer_eval_tier_falls_back_from_mode_and_id():
    assert infer_eval_tier({"id": "case_multifile_api_move", "mode": "compile_fix"}) == "multifile_refactor"
    assert infer_eval_tier({"category": "UMG dependency issue", "mode": "module_fix"}) == "module_fix"


def test_changed_files_from_diff_ignores_wrapper_artifacts():
    diff = """--- a/Source/HoldoutFixture/HoldoutFixture.Build.cs
+++ b/Source/HoldoutFixture/HoldoutFixture.Build.cs
--- a/wrapper_run/attempt_1/model_response.json
+++ b/wrapper_run/attempt_1/model_response.json
--- a/Source/HoldoutFixture/Private/HoldoutDashComponent.cpp
+++ b/Source/HoldoutFixture/Private/HoldoutDashComponent.cpp
"""

    assert changed_files_from_diff(diff) == [
        "Source/HoldoutFixture/HoldoutFixture.Build.cs",
        "Source/HoldoutFixture/Private/HoldoutDashComponent.cpp",
    ]


def test_patch_target_metrics_detect_build_cs_false_positive(tmp_path):
    run_dir = tmp_path / "wrapper_run"
    run_dir.mkdir()
    (run_dir / "final_diff.patch").write_text(
        """--- a/Source/HoldoutFixture/HoldoutFixture.Build.cs
+++ b/Source/HoldoutFixture/HoldoutFixture.Build.cs
@@ -1 +1 @@
-PublicDependencyModuleNames.AddRange(new string[] { "Core" });
+PublicDependencyModuleNames.AddRange(new string[] { "Core", "GameplayTags" });
""",
        encoding="utf-8",
    )
    case = {
        "id": "signature_mismatch",
        "category": "header/cpp signature mismatch",
        "mode": "compile_fix",
        "expectedPatchTargets": ["matching cpp/header"],
        "forbiddenPatchTargets": ["Build.cs-first fix without module evidence"],
    }

    metrics = patch_target_metrics(case, run_dir)

    assert metrics["buildCsTouched"] is True
    assert metrics["buildCsFalsePositive"] is True
    assert metrics["wrongFileEdit"] is True
    assert metrics["forbiddenPatchTargetHits"] == ["Build.cs-first fix without module evidence"]


def test_patch_target_metrics_accepts_expected_build_cs_edit(tmp_path):
    run_dir = tmp_path / "wrapper_run"
    run_dir.mkdir()
    (run_dir / "final_diff.patch").write_text(
        """--- a/Source/HoldoutFixture/HoldoutFixture.Build.cs
+++ b/Source/HoldoutFixture/HoldoutFixture.Build.cs
@@ -1 +1 @@
-PublicDependencyModuleNames.AddRange(new string[] { "Core" });
+PublicDependencyModuleNames.AddRange(new string[] { "Core", "GameplayTags" });
""",
        encoding="utf-8",
    )
    case = {
        "id": "gameplaytags_missing_module",
        "category": "GameplayTags dependency issue",
        "mode": "module_fix",
        "expectedPatchTargets": ["owner Build.cs"],
        "forbiddenPatchTargets": ["unrelated gameplay class"],
        "expectedModules": ["GameplayTags"],
    }

    metrics = patch_target_metrics(case, run_dir)

    assert metrics["expectedPatchTargetMatched"] is True
    assert metrics["buildCsTouched"] is True
    assert metrics["buildCsFalsePositive"] is False
    assert metrics["wrongFileEdit"] is False


def test_metrics_only_can_aggregate_patch_metrics_from_artifact_dir(tmp_path):
    artifact_run = tmp_path / "artifacts" / "case_module" / "wrapper_run"
    artifact_run.mkdir(parents=True)
    (artifact_run / "final_diff.patch").write_text(
        """--- a/Source/HoldoutFixture/HoldoutFixture.Build.cs
+++ b/Source/HoldoutFixture/HoldoutFixture.Build.cs
@@ -1 +1 @@
-PublicDependencyModuleNames.AddRange(new string[] { "Core" });
+PublicDependencyModuleNames.AddRange(new string[] { "Core", "GameplayTags" });
""",
        encoding="utf-8",
    )
    cases = [
        {
            "id": "case_module",
            "category": "GameplayTags dependency issue",
            "mode": "module_fix",
            "expectedPatchTargets": ["owner Build.cs"],
            "expectedModules": ["GameplayTags"],
        }
    ]

    results = build_metrics_only_results(cases, artifact_dir=tmp_path / "artifacts")
    metrics = calculate_kpi_metrics(results)

    assert results[0]["changedSourceFiles"] == ["Source/HoldoutFixture/HoldoutFixture.Build.cs"]
    assert metrics["expectedPatchCoverageRate"] == 1.0
    assert metrics["buildCsTouchedCount"] == 1
    assert metrics["buildCsFalsePositiveCount"] == 0


def test_metrics_only_cli_does_not_require_ubt(tmp_path):
    config = {
        "defaults": {"maxAttempts": 4, "minPassRate": 1.0},
        "cases": [
            {"id": "missing_gameplaytags_dep"},
            {"id": "cpp_header_signature_mismatch"},
        ],
    }
    config_path = tmp_path / "metrics_only_config.json"
    output_path = tmp_path / "metrics_only_kpi.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "eval_pass_at_k.py"),
            "--metrics-only",
            "--config",
            str(config_path),
            "--retry-state-fixture",
            str(WORKSPACE / "tests" / "fixtures" / "retry_state_eval"),
            "--ubt-path",
            str(tmp_path / "does-not-exist" / "UnrealBuildTool.exe"),
            "--output",
            str(output_path),
        ],
        cwd=str(WORKSPACE),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=20,
    )

    assert proc.returncode == 0
    assert "metrics-only" in proc.stdout
    assert "Wrote" in proc.stdout
    assert output_path.is_file()


def test_holdout_config_metrics_only_cli_loads_without_ubt(tmp_path):
    output_path = tmp_path / "holdout_metrics_only_kpi.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "eval_pass_at_k.py"),
            "--metrics-only",
            "--config",
            "config/rag_eval_real_project_holdout_cases.json",
            "--ubt-path",
            str(tmp_path / "missing" / "UnrealBuildTool.exe"),
            "--output",
            str(output_path),
        ],
        cwd=str(WORKSPACE),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=20,
    )

    assert proc.returncode == 0
    assert "Pass@K summary: 36/36" in proc.stdout
    assert "holdout_gameplaytags_missing_module" in proc.stdout
    assert output_path.is_file()


def test_fixture_only_holdout_case_reports_not_live_applicable(tmp_path):
    case = {
        "id": "holdout_fixture_only",
        "mode": "module_fix",
        "errorLog": "fatal error C1083: Cannot open include file: 'EnhancedInputComponent.h'",
    }

    result = __import__("eval_pass_at_k").run_case(
        case,
        dry_run=False,
        ubt_path=tmp_path / "UnrealBuildTool.exe",
        ubt_timeout=1,
        max_attempts=1,
        url="http://localhost:1234/v1",
        model="",
        wrapper_timeout=1,
    )

    assert result["pass"] is False
    assert result["mode"] == "live"
    assert "not live-applicable" in result["error"]


def test_compose_eval_request_includes_error_log():
    case = {
        "errorLog": "fatal error C1083: Cannot open include file: 'EnhancedInputComponent.h'",
    }
    text = compose_eval_request(case, "Patch the owner Build.cs.")

    assert text.startswith("fatal error C1083")
    assert "Patch the owner Build.cs." in text


def test_compose_eval_request_dedups_bootstrap_shaped_request():
    error_log = "fatal error C1083: Cannot open include file: 'EnhancedInputComponent.h'"
    case = {"errorLog": error_log}
    body = f"{error_log}\n\nPatch the owner Build.cs."
    text = compose_eval_request(case, body)

    assert text.count("fatal error C1083") == 1
    assert "Patch the owner Build.cs." in text


def test_copy_fixture_skips_build_artifacts(tmp_path):
    from eval_pass_at_k import copy_fixture

    fixture_dir = tmp_path / "fixture"
    work_dir = tmp_path / "work"
    fixture_dir.mkdir()
    (fixture_dir / "Intermediate").mkdir()
    (fixture_dir / "Intermediate" / "Build.txt").write_text("stale", encoding="utf-8")
    (fixture_dir / "Binaries").mkdir()
    (fixture_dir / "HoldoutFixture.uproject").write_text("{}", encoding="utf-8")

    copy_fixture(fixture_dir, work_dir)

    assert (work_dir / "HoldoutFixture.uproject").is_file()
    assert not (work_dir / "Intermediate").exists()
    assert not (work_dir / "Binaries").exists()


def test_module_fix_route_prefers_c1083_when_error_log_present():
    request = compose_eval_request(
        {
            "errorLog": "fatal error C1083: Cannot open include file: 'EnhancedInputComponent.h'",
        },
        "Fix the missing EnhancedInput module dependency.",
    )
    route = align_route_to_eval_mode(route_error_action(request), "module_fix", request)

    assert route["broadMode"] == "module_fix"
    assert route["errorSubkind"] == "C1083_MISSING_INCLUDE"
