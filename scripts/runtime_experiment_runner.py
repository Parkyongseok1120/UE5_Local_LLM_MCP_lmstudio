#!/usr/bin/env python
"""Build and execute bounded Unreal Automation/trace/soak experiment plans."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from automation_report_parser import parse_automation_report
from unreal_insights_analyzer import (
    DEFAULT_MAX_TIMER_COUNT,
    DEFAULT_TIMEOUT_SECONDS as DEFAULT_INSIGHTS_TIMEOUT_SECONDS,
    build_unreal_insights_analysis_plan,
    discover_unreal_insights,
    run_unreal_insights_analysis,
)

MAX_SOAK_ITERATIONS = 100
DEFAULT_REPORT_PATH = "Saved/Automation/RuntimeExperiments"
DEFAULT_DEDICATED_MIN_DURATION_SECONDS = 60
MAX_DIAGNOSTIC_FILE_BYTES = 16 * 1024 * 1024
MAX_DIAGNOSTIC_FILES = 128
_TRACE_EXTENSION = ".utrace"
_STRUCTURED_ERROR_RE = re.compile(
    r"\bLog[A-Za-z0-9_]+:\s*(?P<verbosity>Error|Fatal):\s*(?P<message>.*)$",
    re.IGNORECASE,
)
_CRASH_PATTERNS = (
    re.compile(r"\bFatal error\s*:", re.IGNORECASE),
    re.compile(r"\bLowLevelFatalError\b", re.IGNORECASE),
    re.compile(r"\bUnhandled Exception\b", re.IGNORECASE),
    re.compile(r"\bEXCEPTION_(?:ACCESS_VIOLATION|STACK_OVERFLOW|ILLEGAL_INSTRUCTION)\b", re.IGNORECASE),
    re.compile(r"\bAssertion failed\s*:", re.IGNORECASE),
    re.compile(r"\bCritical error\s*:", re.IGNORECASE),
    re.compile(r"\bSignal\s+(?:6|11)\s+caught\b", re.IGNORECASE),
    re.compile(r"\bLog[A-Za-z0-9_]+:\s*Fatal:\s*", re.IGNORECASE),
)
_ERROR_PATTERNS = (
    re.compile(r"\bAutomation Test Failed\b", re.IGNORECASE),
    re.compile(r"\bEnsure condition failed\s*:", re.IGNORECASE),
    re.compile(r"\bResult\s*[:=]\s*(?:Fail|Failed|Failure)\b", re.IGNORECASE),
)
_BENIGN_ERROR_MESSAGE_RE = re.compile(
    r"^\s*(?:"
    r"0(?:\s+errors?)?\b|"
    r"none\b|"
    r"no\s+(?:fatal\s+)?errors?\b|"
    r"errors?\s*[:=]\s*0\b"
    r")",
    re.IGNORECASE,
)
_BENIGN_ZERO_SUMMARY_RE = re.compile(
    r"^(?:(?:\[[^\]]+\]\s*)|(?:Log[A-Za-z0-9_]+:\s*(?:Display|Log|Verbose):\s*))*"
    r"(?:no|zero|0)\s+(?:fatal\s+)?(?:errors?|crashes?)\b",
    re.IGNORECASE,
)
_BENIGN_ZERO_COUNT_RE = re.compile(
    r"^(?:(?:\[[^\]]+\]\s*)|(?:Log[A-Za-z0-9_]+:\s*(?:Display|Log|Verbose):\s*))*"
    r"(?:fatal error|crash|assertion failed) count\s*:\s*0\s*$",
    re.IGNORECASE,
)


def _bounded_int(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
    label: str,
    issues: list[str],
) -> int:
    try:
        parsed = int(value if value is not None else default)
    except (TypeError, ValueError):
        issues.append(f"{label} must be an integer")
        parsed = default
    return max(minimum, min(maximum, parsed))


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
    automation_report_path: str = DEFAULT_REPORT_PATH,
    dedicated_min_duration_seconds: int = DEFAULT_DEDICATED_MIN_DURATION_SECONDS,
    require_trace: bool | None = None,
    unreal_insights_cmd: str = "",
    insights_timeout_seconds: int = DEFAULT_INSIGHTS_TIMEOUT_SECONDS,
    insights_max_timer_count: int = DEFAULT_MAX_TIMER_COUNT,
) -> dict[str, Any]:
    executable = str(editor_cmd or "").strip()
    project = str(project_file or "").strip()
    automation = str(automation_filter or "").strip()
    report_path = str(automation_report_path or "").strip()
    trace_path = str(trace_output or "").strip()
    issues: list[str] = []
    if not executable:
        issues.append("editor_cmd is required")
    if not project.lower().endswith(".uproject"):
        issues.append("project_file must be a .uproject path")
    if not automation:
        issues.append("automation_filter is required")
    if any(character in automation for character in (";", ",", "\r", "\n")):
        issues.append(
            "automation_filter cannot contain command separators, commas, or newlines"
        )
    if not report_path:
        issues.append("automation_report_path is required")
    if map_name and (
        str(map_name).strip().startswith("-")
        or any(character in str(map_name) for character in ("\r", "\n"))
    ):
        issues.append("map_name cannot be an option or contain newlines")

    iterations = _bounded_int(
        soak_iterations,
        default=1,
        minimum=1,
        maximum=MAX_SOAK_ITERATIONS,
        label="soak_iterations",
        issues=issues,
    )
    timeout = _bounded_int(
        timeout_seconds,
        default=1800,
        minimum=60,
        maximum=86400,
        label="timeout_seconds",
        issues=issues,
    )
    dedicated_minimum = _bounded_int(
        dedicated_min_duration_seconds,
        default=DEFAULT_DEDICATED_MIN_DURATION_SECONDS,
        minimum=1,
        maximum=86400,
        label="dedicated_min_duration_seconds",
        issues=issues,
    )
    insights_timeout = _bounded_int(
        insights_timeout_seconds,
        default=DEFAULT_INSIGHTS_TIMEOUT_SECONDS,
        minimum=30,
        maximum=3600,
        label="insights_timeout_seconds",
        issues=issues,
    )
    insights_timer_limit = _bounded_int(
        insights_max_timer_count,
        default=DEFAULT_MAX_TIMER_COUNT,
        minimum=1,
        maximum=100_000,
        label="insights_max_timer_count",
        issues=issues,
    )
    channels = list(
        dict.fromkeys(str(item).strip() for item in (trace_channels or []) if str(item).strip())
    )
    if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", channel) for channel in channels):
        issues.append("trace_channels contain unsupported characters")
    trace_required = (
        bool(channels or trace_path) if require_trace is None else bool(require_trace)
    )
    insights_executable = discover_unreal_insights(
        editor_cmd=executable,
        configured=unreal_insights_cmd,
    )
    if trace_required and not trace_path:
        issues.append("trace_output is required when trace evidence is required")
    if trace_required and not insights_executable:
        issues.append(
            "UnrealInsights executable is required when trace evidence is required"
        )
    if trace_path and Path(trace_path).suffix.casefold() != _TRACE_EXTENSION:
        issues.append("trace_output must use the .utrace extension")
    if dedicated_server and dedicated_minimum > timeout:
        issues.append(
            "dedicated_min_duration_seconds cannot exceed the per-iteration timeout"
        )

    argv = [executable, project]
    if map_name:
        argv.append(str(map_name).strip())
    argv.append("-Unattended")
    if not trace_required:
        argv.append("-NullRHI")
    argv.extend(
        [
            "-NoSplash",
            "-NoSound",
            f"-ExecCmds=Automation RunTest {automation}; Quit",
            "-TestExit=Automation Test Queue Empty",
            f"-ReportExportPath={report_path}",
            "-stdout",
            "-FullStdOutLogOutput",
        ]
    )
    if dedicated_server:
        argv.extend(["-server", "-log"])
    if channels:
        argv.append(f"-trace={','.join(channels)}")
    if trace_path:
        argv.append(f"-tracefile={trace_path}")
    return {
        "ok": not issues,
        "issues": issues,
        "argv": argv,
        "automationFilter": automation,
        "soakIterations": iterations,
        "timeoutSeconds": timeout,
        "dedicatedServer": bool(dedicated_server),
        "dedicatedMinDurationSec": dedicated_minimum if dedicated_server else 0,
        "traceRequired": trace_required,
        "unrealInsightsCmd": insights_executable,
        "insightsTimeoutSeconds": insights_timeout,
        "insightsMaxTimerCount": insights_timer_limit,
        "observerArtifacts": {
            "automationReport": report_path,
            "traceOutput": trace_path,
            "stdout": True,
        },
        "proofBoundary": (
            "The plan is executable evidence, not RuntimeVerified proof until all requested "
            "iterations run and their fresh Automation reports, diagnostics, duration, and "
            "required trace artifacts are evaluated."
        ),
    }


def _resolve_artifact_path(value: str, *, project_file: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    project_parent = Path(project_file).expanduser().resolve().parent
    return (project_parent / path).resolve()


def _replace_argument(argv: list[str], prefix: str, replacement: str) -> list[str]:
    replaced = False
    result: list[str] = []
    for item in argv:
        if item.casefold().startswith(prefix.casefold()):
            result.append(f"{prefix}{replacement}")
            replaced = True
        else:
            result.append(item)
    if not replaced:
        result.append(f"{prefix}{replacement}")
    return result


def _fresh_iteration_paths(
    plan: dict[str, Any],
    *,
    iteration: int,
    run_token: str,
) -> tuple[Path, Path | None]:
    argv = [str(item) for item in plan.get("argv") or []]
    project_file = argv[1] if len(argv) > 1 else ""
    artifacts = dict(plan.get("observerArtifacts") or {})
    report_base = _resolve_artifact_path(
        str(artifacts.get("automationReport") or DEFAULT_REPORT_PATH),
        project_file=project_file,
    )
    report_path = report_base / f"run-{run_token}-iteration-{iteration:03d}"
    trace_path: Path | None = None
    trace_base_value = str(artifacts.get("traceOutput") or "")
    if trace_base_value:
        trace_base = _resolve_artifact_path(
            trace_base_value,
            project_file=project_file,
        )
        trace_path = trace_base.with_name(
            f"{trace_base.stem}-{run_token}-iteration-{iteration:03d}{_TRACE_EXTENSION}"
        )
    return report_path, trace_path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decoded_output(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _trace_evidence(path: Path | None, *, required: bool) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "required": required,
        "path": str(path) if path is not None else "",
        "exists": False,
        "sizeBytes": 0,
        "sha256": "",
        "ok": not required,
        "issues": [],
    }
    if path is None:
        if required:
            evidence["issues"].append("trace evidence is required but no trace path was configured")
        return evidence
    try:
        if not path.is_file():
            if required:
                evidence["issues"].append(f"required trace artifact does not exist: {path}")
            return evidence
        size = path.stat().st_size
        evidence["exists"] = True
        evidence["sizeBytes"] = size
        if size <= 0:
            if required:
                evidence["issues"].append(f"required trace artifact is empty: {path}")
            return evidence
        evidence["sha256"] = _sha256_file(path)
        evidence["ok"] = True
        return evidence
    except OSError as exc:
        evidence["issues"].append(f"trace artifact could not be inspected: {exc}")
        evidence["ok"] = False
        return evidence


def _diagnostic_text_files(report_path: Path) -> tuple[list[Path], list[str]]:
    if not report_path.is_dir():
        return [], []
    paths = sorted(
        (
            item
            for item in report_path.rglob("*")
            if item.is_file() and item.suffix.casefold() in {".log", ".txt"}
        ),
        key=lambda item: str(item).casefold(),
    )
    if len(paths) > MAX_DIAGNOSTIC_FILES:
        return paths[:MAX_DIAGNOSTIC_FILES], [
            f"too many diagnostic files to validate completely: {len(paths)}"
        ]
    return paths, []


def _is_benign_marker_line(line: str) -> bool:
    lowered = line.casefold()
    expected_marker = "expected" in lowered and any(
        marker in lowered
        for marker in (
            "error",
            "fatal error",
            "assertion failed",
            "critical error",
            "unhandled exception",
        )
    )
    expected_confirmed = expected_marker and any(
        outcome in lowered for outcome in (" was found", " matched", " occurred")
    ) and not any(
        failure in lowered
        for failure in ("not found", "did not", "wasn't", "failed to", "missing")
    )
    return (
        bool(_BENIGN_ZERO_SUMMARY_RE.search(line))
        or expected_confirmed
        or bool(_BENIGN_ZERO_COUNT_RE.search(line))
    )


def _scan_diagnostic_sources(
    sources: Iterable[tuple[str, str]],
) -> dict[str, Any]:
    hits: list[dict[str, str]] = []
    error_count = 0
    crash_count = 0
    for source, content in sources:
        for line_number, raw_line in enumerate(content.splitlines(), start=1):
            line = raw_line.strip()
            if not line or _is_benign_marker_line(line):
                continue
            crash = any(pattern.search(line) for pattern in _CRASH_PATTERNS)
            structured_error = _STRUCTURED_ERROR_RE.search(line)
            error = crash or any(pattern.search(line) for pattern in _ERROR_PATTERNS)
            if structured_error and not _BENIGN_ERROR_MESSAGE_RE.search(
                structured_error.group("message")
            ):
                error = True
                if structured_error.group("verbosity").casefold() == "fatal":
                    crash = True
            if not error:
                continue
            error_count += 1
            if crash:
                crash_count += 1
            if len(hits) < 100:
                hits.append(
                    {
                        "source": source,
                        "line": str(line_number),
                        "kind": "crash" if crash else "error",
                        "message": line[:1000],
                    }
                )
    return {
        "ok": error_count == 0,
        "errorCount": error_count,
        "crashCount": crash_count,
        "hits": hits,
    }


def _collect_diagnostics(
    *,
    stdout: str,
    stderr: str,
    report_path: Path,
    report_messages: list[dict[str, str]],
) -> dict[str, Any]:
    sources: list[tuple[str, str]] = [("stdout", stdout), ("stderr", stderr)]
    issues: list[str] = []
    for index, message in enumerate(report_messages, start=1):
        severity = str(message.get("severity") or "")
        text = str(message.get("message") or "")
        sources.append((f"automation-report-event-{index}", f"LogReport: {severity}: {text}"))
    text_files, file_issues = _diagnostic_text_files(report_path)
    issues.extend(file_issues)
    for path in text_files:
        try:
            size = path.stat().st_size
            if size > MAX_DIAGNOSTIC_FILE_BYTES:
                issues.append(
                    f"diagnostic file is too large to validate completely: {path} ({size} bytes)"
                )
                continue
            sources.append((str(path), path.read_text(encoding="utf-8", errors="replace")))
        except OSError as exc:
            issues.append(f"diagnostic file could not be read: {path}: {exc}")
    scanned = _scan_diagnostic_sources(sources)
    scanned["issues"] = issues
    scanned["ok"] = bool(scanned["ok"] and not issues)
    return scanned


def run_unreal_experiment_plan(
    plan: dict[str, Any],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    insights_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    insights_platform_name: str = os.name,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    if not plan.get("ok"):
        return {"ok": False, "error": "experiment plan is invalid", "plan": plan}
    argv = [str(item) for item in plan.get("argv") or []]
    if len(argv) < 2:
        return {"ok": False, "error": "experiment plan argv is incomplete", "plan": plan}
    try:
        iterations = int(plan.get("soakIterations") or 1)
        timeout = int(plan.get("timeoutSeconds") or 1800)
        dedicated_minimum = int(plan.get("dedicatedMinDurationSec") or 0)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "error": "experiment plan contains non-integer execution bounds",
            "plan": plan,
        }
    if not 1 <= iterations <= MAX_SOAK_ITERATIONS:
        return {
            "ok": False,
            "error": f"soakIterations must be between 1 and {MAX_SOAK_ITERATIONS}",
            "plan": plan,
        }
    if not 60 <= timeout <= 86400:
        return {
            "ok": False,
            "error": "timeoutSeconds must be between 60 and 86400",
            "plan": plan,
        }
    if not str(plan.get("automationFilter") or "").strip():
        return {
            "ok": False,
            "error": "automationFilter is required by the execution plan",
            "plan": plan,
        }
    trace_required = bool(plan.get("traceRequired"))
    dedicated_server = bool(plan.get("dedicatedServer"))
    if dedicated_server and not 1 <= dedicated_minimum <= timeout:
        return {
            "ok": False,
            "error": "dedicatedMinDurationSec must be positive and cannot exceed timeoutSeconds",
            "plan": plan,
        }
    runs: list[dict[str, Any]] = []
    started = clock()
    run_token = f"{os.getpid()}-{time.time_ns()}"
    for iteration in range(1, iterations + 1):
        report_path, trace_path = _fresh_iteration_paths(
            plan,
            iteration=iteration,
            run_token=run_token,
        )
        iteration_argv = _replace_argument(
            argv,
            "-ReportExportPath=",
            str(report_path),
        )
        if trace_path is not None:
            iteration_argv = _replace_argument(
                iteration_argv,
                "-tracefile=",
                str(trace_path),
            )
        artifact_setup_error = ""
        try:
            report_path.mkdir(parents=True, exist_ok=False)
            if trace_path is not None:
                trace_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            artifact_setup_error = str(exc)

        run_started = clock()
        stdout = ""
        stderr = ""
        if artifact_setup_error:
            return_code: int | None = None
            timed_out = False
            infrastructure_error = (
                f"fresh artifact directory could not be prepared: {artifact_setup_error}"
            )
        else:
            infrastructure_error = ""
            try:
                completed = runner(
                    iteration_argv,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    check=False,
                )
                return_code = int(completed.returncode)
                timed_out = False
                stdout = _decoded_output(completed.stdout)
                stderr = _decoded_output(completed.stderr)
            except subprocess.TimeoutExpired as exc:
                return_code = None
                timed_out = True
                stdout = _decoded_output(exc.stdout)
                stderr = _decoded_output(exc.stderr)
            except OSError as exc:
                return_code = None
                timed_out = False
                infrastructure_error = str(exc)
        run_duration = round(clock() - run_started, 3)
        report = parse_automation_report(
            report_path,
            requested_filter=str(plan.get("automationFilter") or ""),
        )
        trace = _trace_evidence(trace_path, required=trace_required)
        insights_analysis: dict[str, Any] = {
            "ok": not trace_required,
            "required": trace_required,
            "issues": [],
            "runs": [],
            "metrics": {},
        }
        if trace_required:
            if trace.get("ok") and trace_path is not None:
                insights_plan = build_unreal_insights_analysis_plan(
                    unreal_insights_cmd=str(plan.get("unrealInsightsCmd") or ""),
                    trace_file=trace_path,
                    output_dir=report_path / "InsightsAnalysis",
                    timeout_seconds=int(
                        plan.get("insightsTimeoutSeconds")
                        or DEFAULT_INSIGHTS_TIMEOUT_SECONDS
                    ),
                    max_timer_count=int(
                        plan.get("insightsMaxTimerCount")
                        or DEFAULT_MAX_TIMER_COUNT
                    ),
                )
                insights_analysis = run_unreal_insights_analysis(
                    insights_plan,
                    runner=insights_runner,
                    platform_name=insights_platform_name,
                )
                insights_analysis["required"] = True
            else:
                insights_analysis["issues"] = [
                    "UnrealInsights analysis was not run because required trace validation failed"
                ]
        diagnostics = _collect_diagnostics(
            stdout=stdout,
            stderr=stderr,
            report_path=report_path,
            report_messages=list(report.get("messages") or []),
        )
        issues: list[str] = []
        if return_code != 0:
            issues.append(f"Unreal process returned non-zero status: {return_code}")
        if timed_out:
            issues.append(f"Unreal process exceeded timeout: {timeout} seconds")
        if infrastructure_error:
            issues.append(f"Unreal process could not execute: {infrastructure_error}")
        issues.extend(str(item) for item in report.get("issues") or [])
        issues.extend(str(item) for item in trace.get("issues") or [])
        issues.extend(str(item) for item in insights_analysis.get("issues") or [])
        issues.extend(str(item) for item in diagnostics.get("issues") or [])
        if diagnostics.get("errorCount"):
            issues.append(
                f"runtime diagnostics contain {diagnostics['errorCount']} error marker(s)"
            )
        if diagnostics.get("crashCount"):
            issues.append(
                f"runtime diagnostics contain {diagnostics['crashCount']} crash marker(s)"
            )
        if dedicated_server and run_duration < dedicated_minimum:
            issues.append(
                "dedicated-server iteration ended before the required minimum duration: "
                f"{run_duration} < {dedicated_minimum} seconds"
            )
        run = {
            "iteration": iteration,
            "argv": iteration_argv,
            "returnCode": return_code,
            "timedOut": timed_out,
            "infrastructureError": infrastructure_error,
            "stdoutTail": stdout[-8000:],
            "stderrTail": stderr[-8000:],
            "durationSec": run_duration,
            "automationReport": report,
            "trace": trace,
            "insightsAnalysis": insights_analysis,
            "diagnostics": diagnostics,
            "issues": issues,
            "ok": not issues,
        }
        runs.append(run)
        if issues:
            break

    passed = len(runs) == iterations and all(item["ok"] for item in runs)
    duration = round(clock() - started, 3)
    error_count = sum(
        max(
            int(item.get("diagnostics", {}).get("errorCount") or 0),
            int(item.get("automationReport", {}).get("failedCount") or 0),
            int(item.get("automationReport", {}).get("testErrorCount") or 0),
        )
        + (1 if item.get("infrastructureError") else 0)
        + (1 if item.get("returnCode") not in {0, None} else 0)
        for item in runs
    )
    crash_count = sum(
        int(item.get("diagnostics", {}).get("crashCount") or 0) for item in runs
    )
    timeout_count = sum(1 for item in runs if item.get("timedOut"))
    actual_artifacts = [
        {
            "iteration": item["iteration"],
            "automationReport": item["automationReport"].get("reportPath", ""),
            "trace": item["trace"],
            "insightsAnalysis": item["insightsAnalysis"],
        }
        for item in runs
    ]
    return {
        "ok": passed,
        "requestedIterations": iterations,
        "completedIterations": len(runs),
        "runs": runs,
        "durationSec": duration,
        "proofLevel": "RuntimeObserved" if passed else "NeedsRuntimeProof",
        "artifacts": actual_artifacts,
        "oracleEvidence": {
            "kind": "automation",
            "location": (
                str(actual_artifacts[0]["automationReport"])
                if actual_artifacts
                else ""
            ),
            "observation": (
                "all requested iterations produced passing Automation reports and "
                "satisfied artifact/runtime policies"
                if passed
                else "one or more requested iterations failed artifact or runtime validation"
            ),
            "sampleCount": sum(
                int(item.get("automationReport", {}).get("matchedTestCount") or 0)
                for item in runs
            ),
            "soakIterations": len(runs),
            "durationSec": duration,
            "errorCount": error_count,
            "crashCount": crash_count,
            "timeoutCount": timeout_count,
            "traceHashes": [
                item["trace"]["sha256"]
                for item in runs
                if item.get("trace", {}).get("sha256")
            ],
            "traceMetrics": [
                item["insightsAnalysis"].get("metrics", {})
                for item in runs
                if item.get("insightsAnalysis", {}).get("ok")
            ],
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
