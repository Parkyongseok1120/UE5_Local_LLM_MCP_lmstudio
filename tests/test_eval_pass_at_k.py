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

from eval_pass_at_k import build_metrics_only_results, calculate_kpi_metrics, count_wrapper_attempts  # noqa: E402


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


def test_metrics_only_cli_does_not_require_ubt(tmp_path):
    config = {
        "defaults": {"maxAttempts": 4, "minPassRate": 1.0},
        "cases": [
            {"id": "missing_gameplaytags_dep"},
            {"id": "cpp_header_signature_mismatch"},
        ],
    }
    config_path = tmp_path / "metrics_only_config.json"
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


def test_holdout_config_metrics_only_cli_loads_without_ubt(tmp_path):
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "eval_pass_at_k.py"),
            "--metrics-only",
            "--config",
            "config/rag_eval_real_project_holdout_cases.json",
            "--ubt-path",
            str(tmp_path / "missing" / "UnrealBuildTool.exe"),
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
    assert "Pass@K summary: 12/12" in proc.stdout
    assert "holdout_gameplaytags_missing_module" in proc.stdout


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
