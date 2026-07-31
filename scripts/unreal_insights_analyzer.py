#!/usr/bin/env python
"""Run UnrealInsights headlessly and validate exported timer statistics."""

from __future__ import annotations

import csv
import hashlib
import math
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

DEFAULT_MAX_TIMER_COUNT = 5000
MAX_TIMER_COUNT = 100_000
DEFAULT_TIMEOUT_SECONDS = 600
MAX_TIMEOUT_SECONDS = 3600
_EXPORTS = (
    ("GameThread", "GameThread*"),
    ("RenderThread", "RenderThread*"),
    ("RHIThread", "RHIThread*"),
    ("AllTimers", ""),
)
_GROUP_PATTERNS = {
    "gc": re.compile(r"(?:collect\s*garbage|garbage\s*collect|garbagecollector|\bgc\b)", re.I),
    "asyncLoad": re.compile(
        r"(?:async.*load|load.*async|asyncpackage|loadpackage|streaming.*load)",
        re.I,
    ),
    "allocation": re.compile(
        r"(?:malloc|realloc|memory.*alloc|allocat(?:e|ion)|\bfree\b)",
        re.I,
    ),
}
_EXPORT_ERROR_RE = re.compile(
    r"(?:Fatal error\s*:|Assertion failed\s*:|Failed to export timing statistics|"
    r"Unable to access TimingProfilerProvider|Unknown Cmd Param|"
    r"LogTraceInsights:\s*Error:)",
    re.I,
)
_ANALYSIS_DIAGNOSTIC_RE = re.compile(
    r"\bLog[A-Za-z0-9_]+:\s*(?:Warning|Error):\s*.*$",
    re.I,
)
_COMMAND_OBSERVED_RE = re.compile(
    r"(?:TimingInsights\.ExportTimerStatistics|Exported timing statistics to file)",
    re.I,
)


def discover_unreal_insights(
    *,
    editor_cmd: str,
    configured: str = "",
) -> str:
    """Resolve UnrealInsights beside UnrealEditor or from PATH."""

    explicit = str(configured or "").strip()
    if explicit:
        return explicit
    from_path = shutil.which("UnrealInsights") or shutil.which("UnrealInsights.exe")
    if from_path:
        return from_path
    editor = Path(str(editor_cmd or "").strip()).expanduser()
    if not editor.name:
        return ""
    executable_name = "UnrealInsights.exe" if editor.suffix.casefold() == ".exe" else "UnrealInsights"
    sibling = editor.with_name(executable_name)
    if sibling.is_file():
        return str(sibling)
    parts = [part.casefold() for part in editor.parts]
    if "mac" in parts:
        try:
            mac_index = parts.index("mac")
            binaries = Path(*editor.parts[: mac_index + 1])
            app_executable = (
                binaries
                / "UnrealInsights.app"
                / "Contents"
                / "MacOS"
                / "UnrealInsights"
            )
            if app_executable.is_file():
                return str(app_executable)
        except (ValueError, OSError):
            pass
    return ""


def _unsafe_command_value(value: object) -> bool:
    return any(character in str(value) for character in ('"', ";", "\r", "\n"))


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


def _windows_raw_command_line(
    *,
    executable: str,
    trace_path: Path,
    csv_path: Path,
    threads: str,
    max_timer_count: int,
) -> str:
    values = (executable, trace_path, csv_path, threads)
    if any(_unsafe_command_value(value) for value in values):
        raise ValueError(
            "Windows Insights command values cannot contain quotes, semicolons, or newlines"
        )
    export_path = csv_path
    if any(character.isspace() for character in str(export_path)):
        if os.name != "nt":
            raise ValueError(
                "Windows UnrealInsights timer export requires a whitespace-free output path"
            )
        try:
            import ctypes

            buffer = ctypes.create_unicode_buffer(32768)
            result = ctypes.windll.kernel32.GetShortPathNameW(  # type: ignore[attr-defined]
                str(export_path.parent),
                buffer,
                len(buffer),
            )
            if result <= 0:
                raise OSError("GetShortPathNameW failed")
            export_path = Path(buffer.value) / export_path.name
        except (AttributeError, OSError) as exc:
            raise ValueError(
                "Windows UnrealInsights timer export could not obtain a "
                "whitespace-free output path"
            ) from exc
        if any(character.isspace() for character in str(export_path)):
            raise ValueError(
                "Windows UnrealInsights short output path still contains whitespace"
            )
    if threads and not re.fullmatch(r"[A-Za-z0-9_.*?-]+", threads):
        raise ValueError("Windows Insights thread filter contains unsupported characters")
    inner = (
        f"TimingInsights.ExportTimerStatistics {export_path} "
        f"-maxTimerCount={max_timer_count} "
        "-sortBy=TotalInclusiveTime -sortOrder=Descending"
    )
    if threads:
        inner += f" -threads={threads}"
    return (
        f'"{executable}" -OpenTraceFile="{trace_path}" '
        "-unattended -autoquit -noui -nullrhi -log -stdout "
        "-FullStdOutLogOutput -UTF8Output "
        f'-ExecOnAnalysisCompleteCmd="{inner}"'
    )


def _analysis_diagnostics(text: str) -> dict[str, Any]:
    hits = [
        line.strip()
        for line in text.splitlines()
        if _ANALYSIS_DIAGNOSTIC_RE.search(line)
    ]
    return {
        "count": len(hits),
        "items": hits[:100],
        "truncated": len(hits) > 100,
        "nonBlocking": True,
    }


def build_unreal_insights_analysis_plan(
    *,
    unreal_insights_cmd: str,
    trace_file: str | Path,
    output_dir: str | Path,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_timer_count: int = DEFAULT_MAX_TIMER_COUNT,
) -> dict[str, Any]:
    executable = str(unreal_insights_cmd or "").strip()
    trace_path = Path(trace_file).expanduser().resolve()
    export_dir = Path(output_dir).expanduser().resolve()
    issues: list[str] = []
    try:
        timeout = int(timeout_seconds)
        timer_limit = int(max_timer_count)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_SECONDS
        timer_limit = DEFAULT_MAX_TIMER_COUNT
        issues.append("Insights timeout and timer count must be integers")
    if not executable:
        issues.append("UnrealInsights executable is required")
    elif _unsafe_command_value(executable):
        issues.append(
            "UnrealInsights executable cannot contain quotes, semicolons, or newlines"
        )
    if trace_path.suffix.casefold() != ".utrace":
        issues.append("trace_file must use the .utrace extension")
    if not 30 <= timeout <= MAX_TIMEOUT_SECONDS:
        issues.append(
            f"Insights timeout must be between 30 and {MAX_TIMEOUT_SECONDS} seconds"
        )
    if not 1 <= timer_limit <= MAX_TIMER_COUNT:
        issues.append(f"max_timer_count must be between 1 and {MAX_TIMER_COUNT}")
    if _unsafe_command_value(trace_path) or _unsafe_command_value(export_dir):
        issues.append(
            "trace and export paths cannot contain quotes, semicolons, or newlines"
        )

    exports: list[dict[str, Any]] = []
    for metric_name, threads in _EXPORTS:
        csv_path = export_dir / f"{metric_name}.csv"
        command = (
            f'TimingInsights.ExportTimerStatistics "{csv_path}" '
            f"-maxTimerCount={timer_limit} "
            "-sortBy=TotalInclusiveTime -sortOrder=Descending"
        )
        if threads:
            command += f' -threads="{threads}"'
        if _unsafe_command_value(threads):
            issues.append(
                f"Insights thread filter contains unsafe command characters: {threads}"
            )
        argv = [
            executable,
            f"-OpenTraceFile={trace_path}",
            "-unattended",
            "-autoquit",
            "-noui",
            "-nullrhi",
            "-log",
            f"-ExecOnAnalysisCompleteCmd={command}",
        ]
        exports.append(
            {
                "name": metric_name,
                "threads": threads,
                "csvPath": str(csv_path),
                "argv": argv,
            }
        )
    return {
        "ok": not issues,
        "issues": issues,
        "traceFile": str(trace_path),
        "outputDir": str(export_dir),
        "timeoutSeconds": timeout,
        "maxTimerCount": timer_limit,
        "exports": exports,
        "proofBoundary": (
            "UnrealInsights parses the binary .utrace. This adapter validates its fresh "
            "timer-statistics CSV exports; it does not parse the binary format itself."
        ),
    }


def _normal_column(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _finite_nonnegative_float(value: object) -> float:
    number = float(str(value).strip())
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"expected a finite non-negative number, got {value!r}")
    return number


def parse_timer_statistics_csv(csv_path: str | Path) -> dict[str, Any]:
    path = Path(csv_path)
    issues: list[str] = []
    try:
        size = path.stat().st_size
        if size <= 0:
            raise ValueError("CSV export is empty")
        text = path.read_text(encoding="utf-8-sig")
        if not text.strip():
            raise ValueError("CSV export contains no text")
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(text.splitlines(), dialect=dialect)
        if not reader.fieldnames:
            raise ValueError("CSV export has no header")
        columns = {_normal_column(name): name for name in reader.fieldnames if name}
        required = {"name", "count", "incl"}
        missing = sorted(required - columns.keys())
        if missing:
            raise ValueError(
                "CSV export is missing required timer columns: " + ", ".join(missing)
            )
        rows: list[dict[str, Any]] = []
        for line_number, raw_row in enumerate(reader, start=2):
            if not any(str(value or "").strip() for value in raw_row.values()):
                continue
            name = str(raw_row.get(columns["name"]) or "").strip()
            if not name:
                raise ValueError(f"timer name is empty at CSV line {line_number}")
            count = _finite_nonnegative_float(raw_row.get(columns["count"], ""))
            inclusive = _finite_nonnegative_float(raw_row.get(columns["incl"], ""))
            exclusive = 0.0
            if "excl" in columns:
                exclusive = _finite_nonnegative_float(
                    raw_row.get(columns["excl"], "")
                )
            rows.append(
                {
                    "name": name,
                    "count": int(count),
                    "inclusiveTimeSec": inclusive,
                    "exclusiveTimeSec": exclusive,
                }
            )
        if not rows:
            raise ValueError("CSV export contains no timer statistic rows")
        return {
            "ok": True,
            "issues": [],
            "path": str(path),
            "sizeBytes": size,
            "sha256": _sha256_file(path),
            "rowCount": len(rows),
            "rows": rows,
        }
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        issues.append(f"timer statistics CSV is missing or malformed: {path}: {exc}")
        return {
            "ok": False,
            "issues": issues,
            "path": str(path),
            "sizeBytes": 0,
            "sha256": "",
            "rowCount": 0,
            "rows": [],
        }


def _metric_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    top = max(rows, key=lambda row: float(row["inclusiveTimeSec"]))
    return {
        "available": bool(rows),
        "timerCount": len(rows),
        "instanceCount": sum(int(row["count"]) for row in rows),
        "maxInclusiveTimeSec": float(top["inclusiveTimeSec"]),
        "topTimer": str(top["name"]),
    }


def _group_metrics(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for group, pattern in _GROUP_PATTERNS.items():
        matching = [row for row in rows if pattern.search(str(row["name"]))]
        result[group] = {
            "available": bool(matching),
            "timerCount": len(matching),
            "instanceCount": sum(int(row["count"]) for row in matching),
            "totalInclusiveTimeSec": sum(
                float(row["inclusiveTimeSec"]) for row in matching
            ),
            "timers": [str(row["name"]) for row in matching[:100]],
        }
    return result


def run_unreal_insights_analysis(
    plan: dict[str, Any],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    platform_name: str = os.name,
) -> dict[str, Any]:
    if not plan.get("ok"):
        return {
            "ok": False,
            "issues": ["UnrealInsights analysis plan is invalid", *plan.get("issues", [])],
            "plan": plan,
        }
    timeout = int(plan.get("timeoutSeconds") or DEFAULT_TIMEOUT_SECONDS)
    exports = list(plan.get("exports") or [])
    if len(exports) != len(_EXPORTS) or not 30 <= timeout <= MAX_TIMEOUT_SECONDS:
        return {
            "ok": False,
            "issues": ["UnrealInsights analysis plan has invalid execution bounds"],
            "plan": plan,
        }
    trace_path = Path(str(plan.get("traceFile") or ""))
    try:
        if not trace_path.is_file() or trace_path.stat().st_size <= 0:
            raise ValueError("trace artifact does not exist or is empty")
        trace_size = trace_path.stat().st_size
        trace_hash = _sha256_file(trace_path)
        output_dir = Path(str(plan.get("outputDir") or "")).resolve()
        expected_names = {name for name, _threads in _EXPORTS}
        export_names = {str(export.get("name") or "") for export in exports}
        if export_names != expected_names:
            raise ValueError("Insights export set is incomplete or contains unknown exports")
        for export in exports:
            csv_path = Path(str(export.get("csvPath") or "")).resolve()
            if csv_path.parent != output_dir:
                raise ValueError(
                    f"Insights CSV export escapes the fresh output directory: {csv_path}"
                )
            if csv_path.exists():
                raise ValueError(f"Insights CSV export already exists: {csv_path}")
            argv = [str(item) for item in export.get("argv") or []]
            if (
                len(argv) < 2
                or not argv[0].strip()
                or f"-OpenTraceFile={trace_path}" not in argv
                or not any(str(csv_path) in item for item in argv)
            ):
                raise ValueError(
                    f"Insights export argv is malformed for {export.get('name')}"
                )
        output_dir.mkdir(parents=True, exist_ok=False)
    except (OSError, ValueError) as exc:
        return {
            "ok": False,
            "issues": [f"UnrealInsights analysis inputs could not be prepared: {exc}"],
            "traceFile": str(trace_path),
        }

    runs: list[dict[str, Any]] = []
    issues: list[str] = []
    parsed_by_name: dict[str, dict[str, Any]] = {}
    for export in exports:
        argv = [str(item) for item in export.get("argv") or []]
        csv_path = Path(str(export.get("csvPath") or ""))
        invocation: str | list[str] = argv
        stdout = ""
        stderr = ""
        timed_out = False
        infrastructure_error = ""
        return_code: int | None = None
        try:
            runner_kwargs: dict[str, Any] = {
                "capture_output": True,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "timeout": timeout,
                "check": False,
            }
            if platform_name == "nt":
                invocation = _windows_raw_command_line(
                    executable=argv[0],
                    trace_path=trace_path,
                    csv_path=csv_path.resolve(),
                    threads=str(export.get("threads") or ""),
                    max_timer_count=int(
                        plan.get("maxTimerCount") or DEFAULT_MAX_TIMER_COUNT
                    ),
                )
                runner_kwargs["shell"] = False
            completed = runner(
                invocation,
                **runner_kwargs,
            )
            return_code = int(completed.returncode)
            stdout = _decoded_output(completed.stdout)
            stderr = _decoded_output(completed.stderr)
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = _decoded_output(exc.stdout)
            stderr = _decoded_output(exc.stderr)
        except OSError as exc:
            infrastructure_error = str(exc)

        export_issues: list[str] = []
        if return_code != 0:
            export_issues.append(
                f"UnrealInsights returned non-zero status for {export.get('name')}: "
                f"{return_code}"
            )
        if timed_out:
            export_issues.append(
                f"UnrealInsights timed out for {export.get('name')} after {timeout} seconds"
            )
        if infrastructure_error:
            export_issues.append(
                f"UnrealInsights could not execute for {export.get('name')}: "
                f"{infrastructure_error}"
            )
        diagnostics = "\n".join((stdout, stderr))
        if _EXPORT_ERROR_RE.search(diagnostics):
            export_issues.append(
                f"UnrealInsights diagnostics report an analysis/export error for "
                f"{export.get('name')}"
            )
        parsed = parse_timer_statistics_csv(csv_path)
        export_issues.extend(str(item) for item in parsed.get("issues") or [])
        log_command_observed = bool(_COMMAND_OBSERVED_RE.search(diagnostics))
        command_observed = log_command_observed or bool(parsed.get("ok"))
        run = {
            "name": str(export.get("name") or ""),
            "threads": str(export.get("threads") or ""),
            "argv": argv,
            "invocation": invocation,
            "returnCode": return_code,
            "timedOut": timed_out,
            "infrastructureError": infrastructure_error,
            "stdoutTail": stdout[-8000:],
            "stderrTail": stderr[-8000:],
            "csv": parsed,
            "commandObserved": command_observed,
            "commandObservation": (
                "log_marker"
                if log_command_observed
                else ("fresh_csv" if parsed.get("ok") else "missing")
            ),
            "analysisDiagnostics": _analysis_diagnostics(diagnostics),
            "issues": export_issues,
            "ok": not export_issues,
        }
        runs.append(run)
        if export_issues:
            issues.extend(export_issues)
            break
        parsed_by_name[run["name"]] = parsed

    metrics: dict[str, Any] = {"threads": {}, "timerGroups": {}}
    if not issues and len(runs) == len(exports):
        for thread_name in ("GameThread", "RenderThread", "RHIThread"):
            metrics["threads"][thread_name] = _metric_from_rows(
                list(parsed_by_name[thread_name]["rows"])
            )
        metrics["timerGroups"] = _group_metrics(
            list(parsed_by_name["AllTimers"]["rows"])
        )
    return {
        "ok": not issues and len(runs) == len(exports),
        "issues": issues,
        "traceFile": str(trace_path),
        "traceSizeBytes": trace_size,
        "traceSha256": trace_hash,
        "runs": runs,
        "metrics": metrics,
        "proofBoundary": str(plan.get("proofBoundary") or ""),
    }
