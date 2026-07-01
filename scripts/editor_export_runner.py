#!/usr/bin/env python
"""Launch Unreal Editor metadata export (headless or live-editor request watcher)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from workspace_paths import (
    default_editor_export_dir,
    editor_export_dir,
    find_workspace_root,
    load_shared_config,
    normalize_editor_export_dir,
    resolve_active_project_path,
    resolve_engine_root,
    save_shared_config,
)

ExportScope = Literal["all", "materials", "blueprints"]
ExportMode = Literal["auto", "headless", "request"]

REQUEST_NAME = "lmstudio_export_request.json"
DONE_NAME = "lmstudio_export_done.json"
ERROR_NAME = "lmstudio_export_error.json"


def editor_export_content_path(start: Path | None = None) -> str:
    config = load_shared_config()
    raw = str(config.get("editorExportContentPath") or "/Game").strip()
    return raw or "/Game"


def editor_export_maps_path(start: Path | None = None) -> str:
    config = load_shared_config()
    raw = str(config.get("editorExportMapsPath") or "").strip()
    return raw or editor_export_content_path(start)


def editor_export_scope(start: Path | None = None) -> ExportScope:
    config = load_shared_config()
    raw = str(config.get("editorExportScope") or "all").strip().lower()
    if raw in {"material", "materials"}:
        return "materials"
    if raw in {"blueprint", "blueprints", "bp"}:
        return "blueprints"
    return "all"


def editor_export_timeout_sec(start: Path | None = None) -> int:
    config = load_shared_config()
    try:
        value = int(config.get("editorExportTimeoutSec") or 1800)
    except (TypeError, ValueError):
        value = 1800
    return max(120, min(value, 7200))


def resolve_export_dir(explicit: str | Path | None = None) -> Path:
    if explicit and str(explicit).strip():
        path = normalize_editor_export_dir(explicit)
    else:
        path = editor_export_dir() or default_editor_export_dir()
    path.mkdir(parents=True, exist_ok=True)
    _maybe_persist_export_dir(path)
    return path


def _maybe_persist_export_dir(path: Path) -> None:
    config = load_shared_config()
    current = str(config.get("editorExportDir") or "").strip()
    normalized = str(path)
    if current == normalized:
        return
    try:
        if current and Path(os.path.expandvars(current.replace("/", "\\"))).expanduser().resolve() == path.resolve():
            return
    except OSError:
        pass
    config["editorExportDir"] = normalized
    save_shared_config(config)


def resolve_editor_executable(engine_root: Path) -> Path:
    win64 = engine_root / "Engine" / "Binaries" / "Win64"
    for name in ("UnrealEditor-Cmd.exe", "UnrealEditor.exe"):
        candidate = win64 / name
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Unreal Editor executable not found under: {win64}")


def project_editor_running(uproject: Path) -> bool:
    project_text = str(uproject.resolve()).lower()
    command = (
        "Get-CimInstance Win32_Process -Filter \"Name='UnrealEditor.exe' OR Name='UnrealEditor-Cmd.exe'\" "
        "| Select-Object -ExpandProperty CommandLine"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    output = (proc.stdout or "").lower()
    return project_text in output


def _clear_markers(export_dir: Path) -> None:
    for name in (DONE_NAME, ERROR_NAME, REQUEST_NAME):
        path = export_dir / name
        if path.exists():
            path.unlink()


def build_export_job(
    *,
    export_dir: Path,
    tools_dir: Path,
    content_path: str,
    maps_path: str,
    scope: ExportScope,
    workspace: Path,
) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    job_dir = workspace / "data" / "editor_export_jobs"
    job_dir.mkdir(parents=True, exist_ok=True)
    job_path = job_dir / f"{job_id}.json"
    job = {
        "jobId": job_id,
        "exportDir": str(export_dir),
        "contentPath": content_path,
        "mapsPath": maps_path,
        "scope": scope,
        "toolsDir": str(tools_dir),
        "donePath": str(export_dir / DONE_NAME),
        "errorPath": str(export_dir / ERROR_NAME),
        "requestPath": str(export_dir / REQUEST_NAME),
        "jobPath": str(job_path),
    }
    job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    return job


def wait_for_export_markers(
    export_dir: Path,
    *,
    timeout_sec: int = 1800,
    poll_sec: float = 2.0,
) -> dict[str, Any]:
    done_path = export_dir / DONE_NAME
    error_path = export_dir / ERROR_NAME
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if error_path.is_file():
            payload = json.loads(error_path.read_text(encoding="utf-8"))
            payload["ok"] = False
            return payload
        if done_path.is_file():
            payload = json.loads(done_path.read_text(encoding="utf-8"))
            payload["ok"] = bool(payload.get("ok", True))
            return payload
        time.sleep(poll_sec)
    return {"ok": False, "error": f"Timed out after {timeout_sec}s waiting for export completion."}


def submit_export_request(job: dict[str, Any]) -> None:
    export_dir = Path(str(job["exportDir"]))
    _clear_markers(export_dir)
    request = {
        "contentPath": job.get("contentPath") or "/Game",
        "mapsPath": job.get("mapsPath") or job.get("contentPath") or "/Game",
        "scope": job.get("scope") or "all",
        "requestedAt": time.time(),
    }
    (export_dir / REQUEST_NAME).write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")


def run_headless_export(
    *,
    uproject: Path,
    engine_root: Path,
    job: dict[str, Any],
    timeout_sec: int,
    log_path: Path | None = None,
) -> dict[str, Any]:
    workspace = find_workspace_root()
    tools_dir = workspace / "tools" / "ue_export"
    headless_script = tools_dir / "headless_export_job.py"
    if not headless_script.is_file():
        return {"ok": False, "error": f"Missing headless export script: {headless_script}"}

    export_dir = Path(str(job["exportDir"]))
    _clear_markers(export_dir)

    editor_exe = resolve_editor_executable(engine_root)
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

    if log_path is None:
        log_path = workspace / "data" / "editor_export_jobs" / f"{job['jobId']}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open("w", encoding="utf-8") as log_handle:
        proc = subprocess.run(
            command,
            cwd=str(uproject.parent),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_sec,
            check=False,
        )

    marker = wait_for_export_markers(export_dir, timeout_sec=30, poll_sec=1.0)
    marker["exitCode"] = proc.returncode
    marker["logPath"] = str(log_path)
    marker["mode"] = marker.get("mode") or "headless"
    if proc.returncode != 0 and marker.get("ok"):
        marker["ok"] = False
        marker["error"] = marker.get("error") or f"Editor exited with code {proc.returncode}"
    return marker


def run_editor_export(
    *,
    export_dir: str | Path | None = None,
    content_path: str | None = None,
    maps_path: str | None = None,
    scope: ExportScope | None = None,
    mode: ExportMode = "auto",
    uproject: str | Path | None = None,
    timeout_sec: int | None = None,
) -> dict[str, Any]:
    workspace = find_workspace_root()
    active = Path(str(uproject)) if uproject else resolve_active_project_path()
    if not active or not active.is_file():
        return {"ok": False, "error": "No active .uproject found. Run pick-project or set activeProject."}

    resolved_export = resolve_export_dir(export_dir)
    resolved_content = content_path or editor_export_content_path()
    resolved_maps = maps_path or editor_export_maps_path()
    resolved_scope = scope or editor_export_scope()
    resolved_timeout = timeout_sec or editor_export_timeout_sec()
    tools_dir = workspace / "tools" / "ue_export"
    engine_root = resolve_engine_root()

    job = build_export_job(
        export_dir=resolved_export,
        tools_dir=tools_dir,
        content_path=resolved_content,
        maps_path=resolved_maps,
        scope=resolved_scope,
        workspace=workspace,
    )

    editor_open = project_editor_running(active)
    chosen_mode = mode
    if mode == "auto":
        chosen_mode = "request" if editor_open else "headless"

    result: dict[str, Any]
    if chosen_mode == "request":
        submit_export_request(job)
        result = wait_for_export_markers(resolved_export, timeout_sec=min(120, resolved_timeout), poll_sec=2.0)
        if not result.get("ok"):
            result["fallback"] = "headless"
            headless = run_headless_export(
                uproject=active,
                engine_root=engine_root,
                job=job,
                timeout_sec=resolved_timeout,
            )
            result = headless
    else:
        result = run_headless_export(
            uproject=active,
            engine_root=engine_root,
            job=job,
            timeout_sec=resolved_timeout,
        )

    result.update(
        {
            "exportDir": str(resolved_export),
            "contentPath": resolved_content,
            "mapsPath": resolved_maps,
            "scope": resolved_scope,
            "project": str(active),
            "engineRoot": str(engine_root),
            "chosenMode": chosen_mode,
            "editorWasRunning": editor_open,
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Unreal Editor metadata export automatically.")
    parser.add_argument("--export-dir", default="")
    parser.add_argument("--content-path", default="")
    parser.add_argument("--maps-path", default="")
    parser.add_argument("--scope", default="", choices=["", "all", "materials", "blueprints"])
    parser.add_argument("--mode", default="auto", choices=["auto", "headless", "request"])
    parser.add_argument("--project", default="")
    parser.add_argument("--timeout-sec", type=int, default=0)
    args = parser.parse_args()

    payload = run_editor_export(
        export_dir=args.export_dir or None,
        content_path=args.content_path or None,
        maps_path=args.maps_path or None,
        scope=args.scope or None,  # type: ignore[arg-type]
        mode=args.mode,  # type: ignore[arg-type]
        uproject=args.project or None,
        timeout_sec=args.timeout_sec or None,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
