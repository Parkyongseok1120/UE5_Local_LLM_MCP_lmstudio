#!/usr/bin/env python
"""Build and execute bounded Unreal Automation/trace/soak experiment plans."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any, Callable

MAX_SOAK_ITERATIONS = 100


def build_unreal_experiment_plan(
    *,
    editor_cmd: str,
    project_file: str,
    automation_filter: str,
    trace_channels: list[str] | None = None,
    trace_output: str = "",
    soak_iterations: int = 1,
    map_name: str = "",
    dedicated_server: bool = False,
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    executable = str(editor_cmd or "").strip()
    project = str(project_file or "").strip()
    automation = str(automation_filter or "").strip()
    issues: list[str] = []
    if not executable:
        issues.append("editor_cmd is required")
    if not project.lower().endswith(".uproject"):
        issues.append("project_file must be a .uproject path")
    if not automation:
        issues.append("automation_filter is required")
    iterations = max(1, min(MAX_SOAK_ITERATIONS, int(soak_iterations or 1)))
    channels = list(
        dict.fromkeys(str(item).strip() for item in (trace_channels or []) if str(item).strip())
    )
    argv = [executable, project]
    if map_name:
        argv.append(str(map_name).strip())
    argv.extend(
        [
        "-Unattended",
        "-NullRHI",
        "-NoSplash",
        "-NoSound",
        f"-ExecCmds=Automation RunTest {automation}; Quit",
        "-TestExit=Automation Test Queue Empty",
        "-ReportExportPath=Saved/Automation/RuntimeExperiments",
        "-stdout",
        "-FullStdOutLogOutput",
        ]
    )
    if dedicated_server:
        argv.extend(["-server", "-log"])
    if channels:
        argv.append(f"-trace={','.join(channels)}")
    if trace_output:
        argv.append(f"-tracefile={trace_output}")
    return {
        "ok": not issues,
        "issues": issues,
        "argv": argv,
        "automationFilter": automation,
        "soakIterations": iterations,
        "timeoutSeconds": max(60, min(86400, int(timeout_seconds or 1800))),
        "observerArtifacts": {
            "automationReport": "Saved/Automation/RuntimeExperiments",
            "traceOutput": trace_output,
            "stdout": True,
        },
        "proofBoundary": (
            "The plan is executable evidence, not RuntimeVerified proof until all requested "
            "iterations run and their artifacts are evaluated."
        ),
    }


def run_unreal_experiment_plan(
    plan: dict[str, Any],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    if not plan.get("ok"):
        return {"ok": False, "error": "experiment plan is invalid", "plan": plan}
    argv = [str(item) for item in plan.get("argv") or []]
    iterations = int(plan.get("soakIterations") or 1)
    timeout = int(plan.get("timeoutSeconds") or 1800)
    runs: list[dict[str, Any]] = []
    started = time.monotonic()
    for iteration in range(1, iterations + 1):
        run_started = time.monotonic()
        try:
            completed = runner(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            run = {
                "iteration": iteration,
                "returnCode": int(completed.returncode),
                "timedOut": False,
                "stdoutTail": str(completed.stdout or "")[-8000:],
                "stderrTail": str(completed.stderr or "")[-8000:],
                "durationSec": round(time.monotonic() - run_started, 3),
            }
        except subprocess.TimeoutExpired as exc:
            run = {
                "iteration": iteration,
                "returnCode": None,
                "timedOut": True,
                "stdoutTail": str(exc.stdout or "")[-8000:],
                "stderrTail": str(exc.stderr or "")[-8000:],
                "durationSec": round(time.monotonic() - run_started, 3),
            }
        except OSError as exc:
            run = {
                "iteration": iteration,
                "returnCode": None,
                "timedOut": False,
                "infrastructureError": str(exc),
                "stdoutTail": "",
                "stderrTail": "",
                "durationSec": round(time.monotonic() - run_started, 3),
            }
        runs.append(run)
        if run["timedOut"] or run["returnCode"] != 0:
            break
    passed = len(runs) == iterations and all(
        not item["timedOut"] and item["returnCode"] == 0 for item in runs
    )
    duration = round(time.monotonic() - started, 3)
    error_count = sum(
        1
        for item in runs
        if item.get("returnCode") not in {0, None}
        or item.get("infrastructureError")
    )
    timeout_count = sum(1 for item in runs if item.get("timedOut"))
    return {
        "ok": passed,
        "requestedIterations": iterations,
        "completedIterations": len(runs),
        "runs": runs,
        "durationSec": duration,
        "proofLevel": "RuntimeObserved" if passed else "NeedsRuntimeProof",
        "artifacts": dict(plan.get("observerArtifacts") or {}),
        "oracleEvidence": {
            "kind": "automation",
            "location": str(
                (plan.get("observerArtifacts") or {}).get("automationReport") or ""
            ),
            "observation": (
                "all requested iterations passed"
                if passed
                else "one or more requested iterations failed"
            ),
            "sampleCount": len(runs),
            "soakIterations": len(runs),
            "durationSec": duration,
            "errorCount": error_count,
            "crashCount": 0,
            "timeoutCount": timeout_count,
        },
    }


def existing_runtime_paths(plan: dict[str, Any]) -> dict[str, bool]:
    argv = [str(item) for item in plan.get("argv") or []]
    executable = Path(argv[0]).expanduser() if argv else Path()
    project = Path(argv[1]).expanduser() if len(argv) > 1 else Path()
    return {
        "editorCmdExists": bool(argv) and executable.is_file(),
        "projectFileExists": len(argv) > 1 and project.is_file(),
    }
