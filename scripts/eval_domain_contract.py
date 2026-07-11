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
    configs = sorted((ROOT / "config").glob("rag_eval_*_domain.local.json"))
    return configs


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
    ]
    pytest_result = run_pytest(domain_tests)
    report["steps"]["domain_pytest"] = pytest_result
    if not pytest_result["ok"]:
        report["ok"] = False

    if not args.pytest_only:
        for config in load_domain_configs():
            step_name = f"config_smoke_{config.stem}"
            try:
                data = json.loads(config.read_text(encoding="utf-8-sig"))
                case_count = len(data.get("cases") or [])
                report["steps"][step_name] = {"ok": case_count > 0, "caseCount": case_count}
                if case_count <= 0:
                    report["ok"] = False
            except (OSError, json.JSONDecodeError) as exc:
                report["steps"][step_name] = {"ok": False, "error": str(exc)}
                report["ok"] = False

    out = ROOT / "Reports" / "eval" / "domain_contract_kpi.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
