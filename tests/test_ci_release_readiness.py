from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_eval_regression  # noqa: E402


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_dev_requirements_include_pytest_for_github_actions() -> None:
    requirements = _read("requirements-dev.txt")

    assert "pytest" in requirements.lower()


def test_github_workflows_keep_windows_safe_commands() -> None:
    ci = _read(".github/workflows/ci.yml")
    eval_regression = _read(".github/workflows/eval-regression.yml")

    assert "python -m pip install -r requirements-dev.txt" in ci
    assert "python -m pip install ruff" in ci
    assert "npm.cmd install --no-fund --no-audit" in ci
    assert "W503" not in ci
    assert "timeout-minutes: 10" in eval_regression
    assert "python -m pip install -r requirements-dev.txt" in eval_regression
    assert "--step-timeout 60" in eval_regression


def test_ci_eval_regression_skips_ubt_dependent_steps_without_running_command() -> None:
    fail_cmd = [sys.executable, "-c", "raise SystemExit(37)"]

    dry = run_eval_regression.run_cmd("eval_pass_at_k_dry", fail_cmd, ci=True, step_timeout=1)
    e2e = run_eval_regression.run_cmd("eval_e2e_compile", fail_cmd, ci=True, step_timeout=1)

    assert dry["pass"] is True
    assert dry["skipped"] is True
    assert dry["exitCode"] == 0
    assert "UBT-dependent step skipped in CI" in dry["reason"]
    assert e2e["pass"] is True
    assert e2e["skipped"] is True
    assert e2e["exitCode"] == 0


def test_ci_eval_regression_skips_rag_and_mcp_environment_dependent_steps() -> None:
    fail_cmd = [sys.executable, "-c", "raise SystemExit(38)"]

    retrieval = run_eval_regression.run_cmd("retrieval_unreal_programming", fail_cmd, ci=True, step_timeout=1)
    sequencer = run_eval_regression.run_cmd("retrieval_sequencer", fail_cmd, ci=True, step_timeout=1)
    bench = run_eval_regression.run_cmd("bench_mcp", fail_cmd, ci=True, step_timeout=1)

    assert retrieval["pass"] is True
    assert retrieval["skipped"] is True
    assert "RAG index/performance-dependent step skipped in CI" in retrieval["reason"]
    assert sequencer["pass"] is True
    assert sequencer["skipped"] is True
    assert bench["pass"] is True
    assert bench["skipped"] is True
    assert bench["exitCode"] == 0


def test_ci_eval_regression_still_runs_non_ubt_steps() -> None:
    cmd = [sys.executable, "-c", "print('ran-non-ubt-step')"]

    row = run_eval_regression.run_cmd("eval_agent_harness", cmd, ci=True, step_timeout=5)

    assert row["pass"] is True
    assert row["exitCode"] == 0
    assert "ran-non-ubt-step" in row["stdoutTail"]


def test_node_install_command_available_via_cmd_on_windows() -> None:
    proc = subprocess.run(
        ["cmd", "/c", "npm.cmd", "--version"],
        cwd=ROOT / "lmstudio-unreal-agent-mcp",
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip()


def test_eval_regression_compare_ignores_intentionally_skipped_pytest_baseline_steps() -> None:
    baseline = {
        "steps": [
            {"label": "eval_reasoning", "pass": True},
            {"label": "test_agent_orchestrator", "pass": True},
        ],
        "metrics": {},
    }
    current = {
        "steps": [
            {"label": "eval_reasoning", "pass": True},
        ],
        "metrics": {},
    }

    delta = run_eval_regression.compare_reports(
        current,
        baseline,
        ignored_missing_labels={"test_agent_orchestrator"},
    )

    assert delta["regressions"] == []


def test_eval_regression_compare_flags_unexpected_missing_green_step() -> None:
    baseline = {
        "steps": [
            {"label": "eval_reasoning", "pass": True},
            {"label": "report_tier_kpi", "pass": True},
        ],
        "metrics": {},
    }
    current = {
        "steps": [
            {"label": "eval_reasoning", "pass": True},
        ],
        "metrics": {},
    }

    delta = run_eval_regression.compare_reports(current, baseline)

    assert "step report_tier_kpi missing from current run" in delta["regressions"]


def test_agent_delete_file_requires_structured_deletion_plan() -> None:
    server_js = _read("lmstudio-unreal-agent-mcp/src/server.js")

    assert 'name: "propose_file_deletions"' in server_js
    assert 'No files were deleted' in server_js
    assert 'wait for explicit user approval' in server_js
    assert 'approvalToken does not match this deletion explanation' in server_js
    assert '["path", "completedEditsSummary", "reason", "ifNotDeleted", "ifDeleted", "approvalToken"]' in server_js
