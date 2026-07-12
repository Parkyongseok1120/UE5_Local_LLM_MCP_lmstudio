#!/usr/bin/env python
"""Domain contract eval runner (separate KPI from core 36-case holdout)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from domain_eval_normalize import evaluate_domain_case, normalize_eval_case  # noqa: E402


def run_pytest(test_paths: list[str]) -> dict[str, Any]:
    cmd = [sys.executable, "-m", "pytest", *test_paths, "-q"]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    return {
        "ok": proc.returncode == 0,
        "exitCode": proc.returncode,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-2000:],
        "command": cmd,
    }


def load_domain_configs() -> list[Path]:
    return sorted((ROOT / "config").glob("rag_eval_*_domain.local.json"))


def evaluate_config(config: Path) -> dict[str, Any]:
    data = json.loads(config.read_text(encoding="utf-8-sig"))
    defaults = dict(data.get("defaults") or {})
    cases = list(data.get("cases") or [])
    results: list[dict[str, Any]] = []
    ok = True
    for raw_case in cases:
        if not isinstance(raw_case, dict):
            ok = False
            results.append({"ok": False, "error": "case must be object"})
            continue
        case = normalize_eval_case(defaults, raw_case)
        if case.get("request"):
            outcome = evaluate_domain_case(case)
            outcome["caseId"] = raw_case.get("id") or raw_case.get("fixtureId") or ""
            results.append(outcome)
            ok = ok and bool(outcome.get("ok"))
        else:
            results.append({"ok": True, "caseId": raw_case.get("id"), "skipped": "fixture-only case"})
    return {"ok": ok, "caseCount": len(cases), "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run domain contract eval matrix.")
    parser.add_argument("--pytest-only", action="store_true")
    args = parser.parse_args()

    report: dict[str, Any] = {
        "suite": "domain_contract",
        "configs": [str(path.name) for path in load_domain_configs()],
        "steps": {},
        "ok": True,
    }

    domain_tests = [
        "tests/test_domain_planner.py",
        "tests/test_plan_consistency.py",
        "tests/test_architecture_gate.py",
        "tests/test_plugin_project_context.py",
        "tests/test_small_refactor_policy.py",
        "tests/test_refactor_impact_scan.py",
        "tests/test_compile_fix_plan_separation.py",
        "tests/test_plan_slice_terminal_state.py",
        "tests/test_wrapper_slice_progression.py",
        "tests/test_architecture_evidence_node_execution.py",
    ]
    pytest_result = run_pytest(domain_tests)
    report["steps"]["domain_pytest"] = pytest_result
    if not pytest_result["ok"]:
        report["ok"] = False

    if not args.pytest_only:
        configs = load_domain_configs()
        if not configs:
            report["environmentBlocked"] = True
            report["environmentBlockedReason"] = (
                "no rag_eval_*_domain.local.json holdout configs present"
            )
            report["steps"]["structural_eval"] = {"ok": True, "skipped": True}
        for config in configs:
            step_name = f"config_eval_{config.stem}"
            try:
                outcome = evaluate_config(config)
                report["steps"][step_name] = outcome
                if not outcome.get("ok"):
                    report["ok"] = False
            except (OSError, json.JSONDecodeError, TypeError) as exc:
                report["steps"][step_name] = {"ok": False, "error": str(exc)}
                report["ok"] = False

    out = ROOT / "Reports" / "eval" / "domain_contract_kpi.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
