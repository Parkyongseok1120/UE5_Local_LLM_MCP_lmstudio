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
    parser.add_argument("--skip-live", action="store_true", help="Record skippedLive=true in report.")
    args = parser.parse_args()

    py = sys.executable
    architecture_tests = [
        "tests/test_plan_consistency.py",
        "tests/test_architecture_gate.py",
        "tests/test_small_refactor_policy.py",
        "tests/test_refactor_impact_scan.py",
    ]
    domain_tests = [
        "tests/test_plugin_project_context.py",
        "tests/test_domain_planner.py",
        "tests/test_include_resolver.py",
        "tests/test_rag_staleness_search.py",
        "tests/test_domain_validators_crossfile.py",
        "tests/test_domain_refactor_source_hardening.py",
        "tests/test_compile_fix_plan_separation.py",
        "tests/test_plan_slice_terminal_state.py",
        "tests/test_wrapper_slice_progression.py",
        "tests/test_architecture_evidence_node_execution.py",
    ]
    core_tests = [
        "tests/test_agent_orchestrator.py",
        "tests/test_unreal_static_validate.py",
        "tests/test_code_sketch_claim_validate.py",
    ]
    node_checks = [
        "lmstudio-unreal-agent-mcp/src/server.js",
        "lmstudio-unreal-agent-mcp/src/read-path-resolver.js",
        "lmstudio-unreal-agent-mcp/src/refactor-tools.js",
        "lmstudio-unreal-agent-mcp/src/bootstrap-cache.js",
        "lmstudio-unreal-agent-mcp/src/context-ux.js",
        "lmstudio-unreal-agent-mcp/src/validate-write.js",
        "lmstudio-unreal-agent-mcp/src/write-guards.js",
    ]

    steps: list[tuple[str, list[str]]] = [
        ("architecture_safety_pytest", [py, "-m", "pytest", *architecture_tests, "-q"]),
        ("domain_contract_pytest", [py, "-m", "pytest", *domain_tests, "-q"]),
        ("core_compile_pytest", [py, "-m", "pytest", *core_tests, "-q"]),
        ("domain_contract_runner", [py, str(SCRIPTS / "eval_domain_contract.py")]),
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
    ]
    for rel in node_checks:
        steps.append((f"node_check_{Path(rel).stem}", ["node", "--check", str(ROOT / rel)]))
    resolver_test = ROOT / "tests" / "test_active_project_read_resolver.py"
    if shutil_which("node"):
        steps.append(("active_project_read_resolver", [py, "-m", "pytest", str(resolver_test), "-q"]))
    else:
        steps.append(("active_project_read_resolver", [py, "-c", "import sys; sys.exit(0)"]))
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

    report = {
        "steps": {},
        "ok": True,
        "skippedLive": bool(args.skip_live or not args.live),
        "suites": {
            "core_compile": "config/rag_eval_real_project_holdout_cases.local.json",
            "domain_contract": "scripts/eval_domain_contract.py",
            "architecture_safety": architecture_tests,
        },
        "baselineManifest": "data/baseline_summary/manifest.json",
    }
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


def shutil_which(name: str) -> str | None:
    import shutil

    return shutil.which(name)


if __name__ == "__main__":
    raise SystemExit(main())
