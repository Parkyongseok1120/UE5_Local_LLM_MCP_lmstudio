"""Inspect and launch Unreal Editor processes for one exact project export."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from editor_export_markers import clear_export_markers
from portable_path_identity import (
    ascii_windows_fold,
    canonical_absolute_path_identity,
    is_windows_host_platform,
)


def resolve_editor_executable(engine_root: Path, host_platform: str | None = None) -> Path:
    host = host_platform or sys.platform
    if host == "win32":
        binary_dir = engine_root / "Engine" / "Binaries" / "Win64"
        candidates = (binary_dir / "UnrealEditor-Cmd.exe", binary_dir / "UnrealEditor.exe")
    elif host == "darwin":
        binary_dir = engine_root / "Engine" / "Binaries" / "Mac"
        candidates = (
            binary_dir / "UnrealEditor-Cmd",
            binary_dir / "UnrealEditor",
            binary_dir / "UnrealEditor-Cmd.app" / "Contents" / "MacOS" / "UnrealEditor-Cmd",
            binary_dir / "UnrealEditor.app" / "Contents" / "MacOS" / "UnrealEditor",
        )
    else:
        binary_dir = engine_root / "Engine" / "Binaries" / "Linux"
        candidates = (binary_dir / "UnrealEditor-Cmd", binary_dir / "UnrealEditor")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Unreal Editor executable not found under: {binary_dir}")


def project_editor_running(
    uproject: Path,
    host_platform: str | None = None,
    *,
    run_process: Callable[..., Any] = subprocess.run,
) -> bool:
    host = sys.platform if host_platform is None else host_platform
    windows = is_windows_host_platform(host)
    project_text = canonical_absolute_path_identity(uproject, host)
    if windows:
        command = (
            "Get-CimInstance Win32_Process -Filter \"Name='UnrealEditor.exe' OR "
            "Name='UnrealEditor-Cmd.exe'\" | Select-Object -ExpandProperty CommandLine"
        )
        argv = ["powershell", "-NoProfile", "-Command", command]
    else:
        argv = ["ps", "-ax", "-o", "command="]
    try:
        proc = run_process(argv, capture_output=True, text=True, check=False, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return False
    raw_output = (proc.stdout or "").replace("\\", "/")
    output = ascii_windows_fold(raw_output) if windows else raw_output
    return project_text in output and "unrealeditor" in ascii_windows_fold(raw_output)


def run_headless_export(
    *,
    uproject: Path,
    engine_root: Path,
    job: dict[str, Any],
    timeout_sec: int,
    workspace: Path,
    wait_for_markers: Callable[..., dict[str, Any]],
    run_process: Callable[..., Any] = subprocess.run,
    log_path: Path | None = None,
) -> dict[str, Any]:
    headless_script = workspace / "tools" / "ue_export" / "headless_export_job.py"
    if not headless_script.is_file():
        return {"ok": False, "error": f"Missing headless export script: {headless_script}"}
    export_dir = Path(str(job["exportDir"]))
    clear_export_markers(export_dir)
    try:
        editor_exe = resolve_editor_executable(engine_root)
    except FileNotFoundError as exc:
        return {"ok": False, "errorCode": "EDITOR_EXECUTABLE_NOT_FOUND", "error": str(exc)}
    env = os.environ.copy()
    env["LMSTUDIO_EXPORT_JOB"] = str(job["jobPath"])
    command = [
        str(editor_exe),
        str(uproject.resolve()),
        f"-ExecutePythonScript={headless_script.resolve()}",
        "-stdout",
        "-FullStdOutLogOutput",
        "-unattended",
        "-nosplash",
        "-NullRHI",
        "-log",
    ]
    actual_log = log_path or workspace / "data" / "editor_export_jobs" / f"{job['jobId']}.log"
    actual_log.parent.mkdir(parents=True, exist_ok=True)
    try:
        with actual_log.open("w", encoding="utf-8") as log_handle:
            proc = run_process(
                command,
                cwd=str(uproject.parent),
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout_sec,
                check=False,
            )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "errorCode": "EDITOR_EXPORT_PROCESS_TIMEOUT",
            "error": f"Unreal Editor export timed out after {timeout_sec}s.",
            "logPath": str(actual_log),
            "mode": "headless",
        }
    except OSError as exc:
        return {
            "ok": False,
            "errorCode": "EDITOR_EXPORT_PROCESS_FAILED",
            "error": f"Could not run Unreal Editor export: {exc}",
            "logPath": str(actual_log),
            "mode": "headless",
        }
    marker = wait_for_markers(
        export_dir,
        timeout_sec=30,
        poll_sec=1.0,
        expected_run_id=str(job["jobId"]),
    )
    marker.update(
        {
            "exitCode": proc.returncode,
            "logPath": str(actual_log),
            "mode": marker.get("mode") or "headless",
        }
    )
    if proc.returncode != 0:
        marker["ok"] = False
        marker["errorCode"] = marker.get("errorCode") or "EDITOR_EXPORT_PROCESS_EXITED"
        marker["error"] = marker.get("error") or f"Editor exited with code {proc.returncode}"
    return marker


__all__ = ["project_editor_running", "resolve_editor_executable", "run_headless_export"]
