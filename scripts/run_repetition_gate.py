#!/usr/bin/env python
"""Run repetition gate for flaky-prone deterministic suites."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

try:
    from scripts.ci.ci_suites import SUITES
    from scripts.ci.run_ci_suite import SuiteValidationError, resolve_test_files
except ModuleNotFoundError:  # Direct execution: python scripts/run_repetition_gate.py ...
    from ci.ci_suites import SUITES
    from ci.run_ci_suite import SuiteValidationError, resolve_test_files

ROOT = Path(__file__).resolve().parents[1]
REPEAT = 10


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "suite",
        choices=tuple(name for name in SUITES if name.endswith("_repetition")),
        default="direct_repetition",
        nargs="?",
        help="Named repetition suite from scripts/ci/ci_suites.py",
    )
    parser.add_argument("--repeat", type=int, default=REPEAT)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")
    try:
        suites = list(resolve_test_files((args.suite,)))
    except SuiteValidationError as exc:
        parser.error(str(exc))
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
