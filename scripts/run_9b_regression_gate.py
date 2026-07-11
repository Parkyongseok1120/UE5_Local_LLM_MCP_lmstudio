#!/usr/bin/env python
"""9B regression gate: pytest smoke + component autofix + optional live eval."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent


def run(cmd: list[str]) -> int:
    proc = subprocess.run(cmd, cwd=ROOT)
    return int(proc.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run 9B domain expansion regression gate.")
    parser.add_argument("--live", action="store_true", help="Include LM Studio live eval when server is reachable.")
    parser.add_argument("--component-repeat", type=int, default=5)
    args = parser.parse_args()

    py = sys.executable
    steps: list[tuple[str, list[str]]] = [
        ("pytest_focus", [py, "-m", "pytest", "tests/test_include_resolver.py", "tests/test_rag_staleness_search.py", "tests/test_agent_orchestrator.py", "tests/test_domain_planner.py", "-q"]),
        ("cinematic_smoke", [py, str(SCRIPTS / "smoke_cinematic_analysis.py")]),
        (
            "component_autofix",
            [
                py,
                str(SCRIPTS / "eval_pass_at_k.py"),
                "--autofix-only",
                "--config",
                str(ROOT / "config" / "rag_eval_real_project_holdout_cases.local.json"),
                "--case-ids",
                "local_component_registration_missing_include",
            ],
        ),
        ("node_check", ["node", "--check", str(ROOT / "lmstudio-unreal-agent-mcp" / "src" / "server.js")]),
    ]
    if args.live:
        for repeat_idx in range(max(1, int(args.component_repeat))):
            steps.append(
                (
                    f"component_live_repeat_{repeat_idx + 1}",
                    [
                        py,
                        str(SCRIPTS / "eval_pass_at_k.py"),
                        "--live",
                        "--require-live",
                        "--config",
                        str(ROOT / "config" / "rag_eval_real_project_holdout_cases.local.json"),
                        "--case-ids",
                        "local_component_registration_missing_include",
                        "--max-attempts",
                        "1",
                    ],
                )
            )
        steps.append(
            (
                "full_36_live",
                [
                    py,
                    str(SCRIPTS / "eval_pass_at_k.py"),
                    "--live",
                    "--require-live",
                    "--config",
                    str(ROOT / "config" / "rag_eval_real_project_holdout_cases.local.json"),
                ],
            )
        )

    report = {"steps": {}, "ok": True}
    for name, cmd in steps:
        code = run(cmd)
        report["steps"][name] = {"exitCode": code, "command": cmd}
        if code != 0:
            report["ok"] = False

    out = ROOT / "Reports" / "eval" / "9b_regression_gate.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
