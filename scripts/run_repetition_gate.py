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
    "tests/test_python_direct_rag_server.py",
    "tests/test_direct_mcp_subprocess_e2e.py",
    "tests/test_cross_language_tool_contract.py",
    "tests/test_build_rag_index_atomic.py",
    "tests/test_atomic_io.py",
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
