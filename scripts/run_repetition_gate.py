#!/usr/bin/env python
"""Run repetition gate for flaky-prone deterministic suites."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPEAT = 10
SUITES = [
    "tests/test_plan_slice_terminal_state.py",
    "tests/test_wrapper_slice_progression.py",
    "tests/test_architecture_evidence_node_execution.py",
    "tests/test_validation_context_cache.py",
    "tests/test_mermaid_validate.py",
    "tests/test_fault_injection_plan_state.py",
    "tests/test_cross_language_tool_contract.py",
    "tests/test_rag_staleness_search.py",
    "tests/test_compile_fix_plan_separation.py",
    "tests/test_job_cancel_deterministic.py",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=REPEAT)
    parser.add_argument("--suite", action="append", default=[], help="Run only these suite paths (repeatable)")
    args = parser.parse_args()
    suites = list(args.suite) if args.suite else SUITES
    report = {"repeat": args.repeat, "suites": suites, "runs": {}, "ok": True}
    py = sys.executable
    for suite in suites:
        for idx in range(args.repeat):
            key = f"{suite}#{idx + 1}"
            proc = subprocess.run([py, "-m", "pytest", suite, "-q"], cwd=ROOT)
            report["runs"][key] = proc.returncode
            if proc.returncode != 0:
                report["ok"] = False
    out = ROOT / "Reports" / "eval" / "repetition_gate.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
