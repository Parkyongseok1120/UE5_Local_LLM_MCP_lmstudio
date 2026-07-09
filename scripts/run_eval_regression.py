#!/usr/bin/env python
"""Regression gate: run Tier-A eval bundle, compare to last green, write Reports/eval/."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
EVAL_DIR = ROOT / "Reports" / "eval"
HISTORY_DIR = EVAL_DIR / "history"
DELTA_DIR = EVAL_DIR / "deltas"
FAILURES_DIR = EVAL_DIR / "failures"

# Default per-step timeout in seconds. Override via --step-timeout.
DEFAULT_STEP_TIMEOUT = 600


def load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def run_cmd(label: str, cmd: list[str], *, ci: bool = False, step_timeout: int = DEFAULT_STEP_TIMEOUT) -> dict:
    if ci:
        if label in {"eval_pass_at_k_dry", "eval_e2e_compile"}:
            return {
                "label": label,
                "exitCode": 0,
                "pass": True,
                "skipped": True,
                "reason": "UBT-dependent step skipped in CI",
                "stdoutTail": "",
                "stderrTail": "",
            }
        if label in {"retrieval_unreal_programming", "retrieval_sequencer", "bench_mcp"}:
            return {
                "label": label,
                "exitCode": 0,
                "pass": True,
                "skipped": True,
                "reason": "RAG index/performance-dependent step skipped in CI",
                "stdoutTail": "",
                "stderrTail": "",
            }
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=step_timeout,
        )
        return {
            "label": label,
            "exitCode": proc.returncode,
            "pass": proc.returncode == 0,
            "stdoutTail": (proc.stdout or "")[-3000:],
            "stderrTail": (proc.stderr or "")[-1500:],
        }
    except subprocess.TimeoutExpired:
        return {
            "label": label,
            "exitCode": -1,
            "pass": False,
            "timedOut": True,
            "reason": f"step timed out after {step_timeout}s",
            "stdoutTail": "",
            "stderrTail": "",
        }


def collect_kpi_metrics() -> dict:
    baseline = ROOT / "data" / "baseline"
    metrics: dict = {}
    reasoning = load_json(baseline / "reasoning-kpi.json")
    pass_at_k = load_json(baseline / "pass-at-k-kpi.json")
    project_review = load_json(baseline / "project-review-kpi.json")
    mcp_bench = load_json(baseline / "mcp-bench-latest.json")
    if reasoning:
        metrics["reasoningScore"] = float(reasoning.get("score") or 0)
    if pass_at_k:
        metrics["passAtKRate"] = float(pass_at_k.get("passRate") or 0)
        metrics["passAtKMode"] = pass_at_k.get("mode")
    if project_review:
        metrics["projectReviewRecall"] = float(project_review.get("aggregateRecall") or 0)
    if mcp_bench:
        metrics["mcpBench"] = mcp_bench.get("results") or mcp_bench
    return metrics


def compare_reports(current: dict, baseline: dict | None, *, ignored_missing_labels: set[str] | None = None) -> dict:
    if not baseline:
        return {"hasBaseline": False, "regressions": [], "improvements": []}
    regressions: list[str] = []
    improvements: list[str] = []
    ignored_missing_labels = ignored_missing_labels or set()

    cur_steps = {s["label"]: s for s in current.get("steps") or []}
    base_steps = {s["label"]: s for s in baseline.get("steps") or []}

    for label, prev in base_steps.items():
        if label in cur_steps or label in ignored_missing_labels:
            continue
        if prev.get("pass"):
            regressions.append(f"step {label} missing from current run")

    for label, row in cur_steps.items():
        prev = base_steps.get(label)
        if prev and prev.get("pass") and not row.get("pass"):
            regressions.append(f"step {label} regressed")
        if prev and not prev.get("pass") and row.get("pass"):
            improvements.append(f"step {label} improved")
        if not prev and row.get("pass"):
            improvements.append(f"step {label} added")

    cur_metrics = current.get("metrics") or {}
    base_metrics = baseline.get("metrics") or {}
    pak = cur_metrics.get("passAtKRate")
    base_pak = base_metrics.get("passAtKRate")
    if pak is not None and base_pak is not None and pak < base_pak - 0.10:
        regressions.append(f"Pass@K rate dropped {base_pak:.0%} -> {pak:.0%}")

    return {"hasBaseline": True, "regressions": regressions, "improvements": improvements}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run eval regression gate.")
    parser.add_argument("--compare", default="", help="Baseline latest.json path")
    parser.add_argument("--skip-pytest", action="store_true")
    parser.add_argument("--ci", action="store_true", help="Skip index/UBT-dependent steps when prerequisites are missing")
    parser.add_argument("--live", action="store_true", help="Include Tier-B live steps")
    parser.add_argument("--step-timeout", type=int, default=DEFAULT_STEP_TIMEOUT,
                        help=f"Per-step subprocess timeout in seconds (default: {DEFAULT_STEP_TIMEOUT})")
    args = parser.parse_args()

    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    DELTA_DIR.mkdir(parents=True, exist_ok=True)
    FAILURES_DIR.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    python = sys.executable

    steps_spec = [
        ("retrieval_unreal_programming", [python, str(SCRIPTS / "evaluate_rag_queries.py"), "--query-set", "config/rag_eval_unreal_programming_queries.json"]),
        ("retrieval_sequencer", [python, str(SCRIPTS / "evaluate_rag_queries.py"), "--query-set", "config/rag_eval_sequencer_queries.json"]),
        ("eval_reasoning", [python, str(SCRIPTS / "eval_reasoning.py")]),
        ("eval_e2e_compile", [python, str(SCRIPTS / "eval_e2e_compile.py")]),
        ("eval_pass_at_k_dry", [python, str(SCRIPTS / "eval_pass_at_k.py"), "--dry-run"]),
        ("eval_agent_harness", [python, str(SCRIPTS / "eval_agent_harness.py")]),
        ("bench_mcp", [python, str(SCRIPTS / "bench_mcp.py")]),
        ("report_tier_kpi", [python, str(SCRIPTS / "report_tier_kpi.py")]),
    ]
    pytest_steps = [
        ("test_agent_orchestrator", [python, "-m", "pytest", "tests/test_agent_orchestrator.py", "-q"]),
        ("test_apply_patch", [python, "-m", "pytest", "tests/test_apply_patch.py", "-q"]),
        ("test_retry_state", [python, "-m", "pytest", "tests/test_retry_state.py", "-q"]),
        ("test_unreal_static_validate", [python, "-m", "pytest", "tests/test_unreal_static_validate.py", "-q"]),
        ("test_validate_project_sources", [python, "-m", "pytest", "tests/test_validate_project_sources.py", "-q"]),
        ("test_error_taxonomy_routing", [python, "-m", "pytest", "tests/test_error_taxonomy_routing.py", "-q"]),
        ("test_retrieval_guideline_sidecar", [python, "-m", "pytest", "tests/test_retrieval_guideline_sidecar.py", "-q"]),
        ("test_multifile_refactor_autofix", [python, "-m", "pytest", "tests/test_multifile_refactor_autofix.py", "-q"]),
        ("test_interface_implementer_validate", [python, "-m", "pytest", "tests/test_interface_implementer_validate.py", "-q"]),
        ("test_compile_fix_guards", [python, "-m", "pytest", "tests/test_compile_fix_guards.py", "-q"]),
        ("test_wrapper_phase2a_helpers", [python, "-m", "pytest", "tests/test_wrapper_phase2a_helpers.py", "-q"]),
        ("test_multifile_surface_blockers", [python, "-m", "pytest", "tests/test_multifile_surface_blockers.py", "-q"]),
        ("test_delegate_broadcast_autofix", [python, "-m", "pytest", "tests/test_delegate_broadcast_autofix.py", "-q"]),
        ("test_ubt_target_sanitize", [python, "-m", "pytest", "tests/test_ubt_target_sanitize.py", "-q"]),
        ("test_wrapper_evidence_paths", [python, "-m", "pytest", "tests/test_wrapper_evidence_paths.py", "-q"]),
        ("test_apply_patch_single_line", [python, "-m", "pytest", "tests/test_apply_patch_single_line.py", "-q"]),
        ("test_validate_holdout_cases", [python, "-m", "pytest", "tests/test_validate_holdout_cases.py", "-q"]),
        ("test_wrapper_retry_feedback", [python, "-m", "pytest", "tests/test_wrapper_retry_feedback.py", "-q"]),
        ("test_apply_bundle_transaction", [python, "-m", "pytest", "tests/test_apply_bundle_transaction.py", "-q"]),
        ("test_blueprint_native_event_guards", [python, "-m", "pytest", "tests/test_blueprint_native_event_guards.py", "-q"]),
        ("test_can_run_autofix_ubt", [python, "-m", "pytest", "tests/test_can_run_autofix_ubt.py", "-q"]),
        ("test_should_block_static_gate", [python, "-m", "pytest", "tests/test_should_block_static_gate.py", "-q"]),
        ("test_autofix_holdout_matrix", [python, "-m", "pytest", "tests/test_autofix_holdout_matrix.py", "-q"]),
    ]
    if not args.skip_pytest:
        steps_spec.extend(pytest_steps)
    if args.live:
        steps_spec.extend([
            ("eval_soulslike_live", [python, str(SCRIPTS / "eval_soulslike_live.py"), "--no-dry-run", "--require-live"]),
            ("eval_project_review_live", [python, str(SCRIPTS / "eval_project_review.py"), "--live", "--require-live"]),
            ("eval_pass_at_k_live", [python, str(SCRIPTS / "eval_pass_at_k.py"), "--live", "--require-live"]),
        ])

    report: dict = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "tier": "B-live" if args.live else "A-static",
        "steps": [],
    }
    for label, cmd in steps_spec:
        row = run_cmd(label, cmd, ci=args.ci, step_timeout=args.step_timeout)
        report["steps"].append(row)
        if not row["pass"]:
            fail_dir = FAILURES_DIR / label / stamp
            fail_dir.mkdir(parents=True, exist_ok=True)
            (fail_dir / "stdout.txt").write_text(row.get("stdoutTail") or "", encoding="utf-8")
            (fail_dir / "stderr.txt").write_text(row.get("stderrTail") or "", encoding="utf-8")
            if row.get("timedOut"):
                (fail_dir / "timeout.txt").write_text(row.get("reason") or "", encoding="utf-8")

    report["passCount"] = sum(1 for s in report["steps"] if s["pass"])
    report["total"] = len(report["steps"])
    report["metrics"] = collect_kpi_metrics()

    baseline_path = Path(args.compare) if args.compare else EVAL_DIR / "latest.json"
    baseline = load_json(baseline_path) if baseline_path.is_file() else None
    ignored_missing = {label for label, _ in pytest_steps} if args.skip_pytest else set()
    report["delta"] = compare_reports(report, baseline, ignored_missing_labels=ignored_missing)

    latest_json = EVAL_DIR / "latest.json"
    latest_md = EVAL_DIR / "latest.md"
    history_json = HISTORY_DIR / f"{stamp}.json"
    delta_json = DELTA_DIR / f"{stamp}.json"

    for path, payload in [
        (latest_json, report),
        (history_json, report),
        (delta_json, report["delta"]),
    ]:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    md = [
        f"# Eval regression {stamp}",
        f"Tier: {report['tier']}",
        f"Pass: {report['passCount']}/{report['total']}",
        "",
        "## Steps",
    ]
    for step in report["steps"]:
        suffix = " (SKIP)" if step.get("skipped") else ""
        md.append(f"- {step['label']}: {'PASS' if step['pass'] else 'FAIL'}{suffix}")
    if report["metrics"]:
        md.extend(["", "## Metrics", json.dumps(report["metrics"], indent=2)])
    if report["delta"].get("regressions"):
        md.extend(["", "## Regressions"] + [f"- {r}" for r in report["delta"]["regressions"]])
    latest_md.write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"Wrote {latest_json}")
    print(f"Wrote {latest_md}")
    if report["delta"].get("regressions"):
        print("REGRESSIONS:", "; ".join(report["delta"]["regressions"]), file=sys.stderr)
    ok = report["passCount"] == report["total"] and not report["delta"].get("regressions")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
