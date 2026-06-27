#!/usr/bin/env python
"""Run UE agent eval harness and write Reports/."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
REPORTS = ROOT / "Reports"


def run_cmd(label: str, cmd: list[str]) -> dict:
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    return {
        "label": label,
        "cmd": cmd,
        "exitCode": proc.returncode,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-2000:],
        "pass": proc.returncode == 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="UE agent eval harness")
    parser.add_argument("--out-dir", default="")
    args = parser.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    out_dir = Path(args.out_dir) if args.out_dir else REPORTS / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    python = sys.executable
    results = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "steps": [],
    }

    steps = [
        ("retrieval_unreal_programming", [python, str(SCRIPTS / "evaluate_rag_queries.py"), "--query-set", "config/rag_eval_unreal_programming_queries.json"]),
        ("retrieval_routing", [python, str(SCRIPTS / "evaluate_rag_queries.py"), "--query-set", "config/rag_eval_project_routing_queries.json"]),
        ("eval_reasoning", [python, str(SCRIPTS / "eval_reasoning.py")]),
        ("eval_e2e_compile", [python, str(SCRIPTS / "eval_e2e_compile.py")]),
        ("eval_pass_at_k_dry", [python, str(SCRIPTS / "eval_pass_at_k.py"), "--dry-run"]),
        ("test_parse_build_cs", [python, "-m", "pytest", "tests/test_parse_build_cs.py", "-q"]),
        ("test_project_routing", [python, "-m", "pytest", "tests/test_project_routing.py", "-q"]),
        ("test_error_taxonomy", [python, "-m", "pytest", "tests/test_error_taxonomy.py", "-q"]),
        ("test_apply_patch", [python, "-m", "pytest", "tests/test_apply_patch.py", "-q"]),
    ]

    for label, cmd in steps:
        row = run_cmd(label, cmd)
        results["steps"].append(row)
        (out_dir / f"{label}.json").write_text(json.dumps(row, indent=2), encoding="utf-8")

    results["passCount"] = sum(1 for s in results["steps"] if s["pass"])
    results["total"] = len(results["steps"])
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    md_lines = [
        f"# Eval harness {stamp}",
        f"Pass: {results['passCount']}/{results['total']}",
        "",
    ]
    for step in results["steps"]:
        md_lines.append(f"- {step['label']}: {'PASS' if step['pass'] else 'FAIL'}")
    (out_dir / "summary.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    print(f"Wrote {summary_path}")
    return 0 if results["passCount"] == results["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
