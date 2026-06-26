#!/usr/bin/env python
"""Background wrapper jobs for non-blocking MCP compile loops."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def jobs_root(workspace: Path) -> Path:
    path = workspace / "data" / "mcp_wrapper_jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def job_path(workspace: Path, job_id: str) -> Path:
    return jobs_root(workspace) / f"{job_id}.json"


def read_job(workspace: Path, job_id: str) -> dict[str, Any] | None:
    path = job_path(workspace, job_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_job(workspace: Path, job: dict[str, Any]) -> None:
    path = job_path(workspace, str(job["jobId"]))
    path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")


def append_progress(job: dict[str, Any], message: str, level: str = "info") -> None:
    job.setdefault("progress", []).append(
        {"at": _utc_now(), "level": level, "message": message}
    )
    job["updatedAt"] = _utc_now()


def _tail_file(path: Path, max_chars: int = 12000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _sync_from_run_metadata(job: dict[str, Any]) -> None:
    run_dir = job.get("runDir")
    if not run_dir:
        return
    metadata_path = Path(run_dir) / "run_metadata.json"
    if not metadata_path.exists():
        return
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        job["runMetadata"] = metadata
    except Exception:
        return

    attempts = []
    run_path = Path(run_dir)
    for attempt_dir in sorted(run_path.glob("attempt_*")):
        if not attempt_dir.is_dir():
            continue
        attempts.append(
            {
                "name": attempt_dir.name,
                "hasStaticValidation": (attempt_dir / "static_validation.txt").exists(),
                "hasUbtLog": (attempt_dir / "ubt.log").exists(),
                "hasStructuredErrors": (attempt_dir / "structured_errors.json").exists(),
            }
        )
    if attempts:
        job["attempts"] = attempts
        job["currentAttempt"] = attempts[-1]["name"]


def list_jobs(workspace: Path, limit: int = 20) -> list[dict[str, Any]]:
    root = jobs_root(workspace)
    files = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    jobs: list[dict[str, Any]] = []
    for path in files[:limit]:
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
            jobs.append(
                {
                    "jobId": job.get("jobId"),
                    "status": job.get("status"),
                    "createdAt": job.get("createdAt"),
                    "updatedAt": job.get("updatedAt"),
                    "runDir": job.get("runDir"),
                    "returncode": job.get("returncode"),
                }
            )
        except Exception:
            continue
    return jobs


def build_wrapper_command(workspace: Path, run_dir: Path, arguments: dict[str, Any]) -> list[str]:
    script = workspace / "scripts" / "lmstudio_unreal_wrapper.py"
    command = [
        sys.executable,
        str(script),
        "--request",
        str(arguments.get("request") or ""),
        "--index",
        str((workspace / "data" / "unreal58" / "rag.sqlite").resolve()),
        "--module-graph",
        str((workspace / "data" / "unreal58" / "raw_module_graph.jsonl").resolve()),
        "--project-name",
        str(arguments.get("project_name") or "ScratchPrototype"),
        "--mode",
        str(arguments.get("mode") or "agent_edit"),
        "--max-attempts",
        str(max(1, min(6, int(arguments.get("max_attempts") or 4)))),
        "--run-dir",
        str(run_dir.resolve()),
    ]
    project_file = str(arguments.get("project_file") or "").strip()
    target = str(arguments.get("target") or "").strip()
    if project_file:
        command.extend(["--project-file", project_file])
    if target:
        command.extend(["--target", target])
    if arguments.get("skip_build"):
        command.append("--skip-build")
    if arguments.get("dry_run"):
        command.append("--dry-run")
    return command


def start_job(
    workspace: Path,
    arguments: dict[str, Any],
    on_progress: Callable[[dict[str, Any], str], None] | None = None,
) -> dict[str, Any]:
    request_text = str(arguments.get("request") or "").strip()
    if not request_text:
        raise ValueError("Missing required argument: request")

    job_id = uuid.uuid4().hex[:12]
    run_dir = jobs_root(workspace) / job_id
    run_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = run_dir / "job.stdout.log"
    stderr_path = run_dir / "job.stderr.log"

    job: dict[str, Any] = {
        "jobId": job_id,
        "status": "starting",
        "createdAt": _utc_now(),
        "updatedAt": _utc_now(),
        "runDir": str(run_dir.resolve()),
        "arguments": arguments,
        "progress": [],
        "returncode": None,
        "stdoutPath": str(stdout_path),
        "stderrPath": str(stderr_path),
    }
    append_progress(job, "Job created.")
    write_job(workspace, job)

    command = build_wrapper_command(workspace, run_dir, arguments)

    def worker() -> None:
        current = read_job(workspace, job_id) or job
        current["status"] = "running"
        append_progress(current, "Wrapper subprocess started.")
        write_job(workspace, current)
        if on_progress:
            on_progress(current, "Wrapper subprocess started.")

        with stdout_path.open("w", encoding="utf-8") as stdout_file, stderr_path.open(
            "w", encoding="utf-8"
        ) as stderr_file:
            process = subprocess.Popen(
                command,
                cwd=str(workspace),
                stdout=stdout_file,
                stderr=stderr_file,
                text=True,
            )
            current["pid"] = process.pid
            write_job(workspace, current)

            stop_polling = threading.Event()

            def metadata_poller() -> None:
                while not stop_polling.is_set():
                    polled = read_job(workspace, job_id)
                    if not polled:
                        return
                    before = polled.get("currentAttempt")
                    _sync_from_run_metadata(polled)
                    after = polled.get("currentAttempt")
                    if after and after != before:
                        append_progress(polled, f"In progress: {after}")
                        if on_progress:
                            on_progress(polled, f"In progress: {after}")
                    polled["updatedAt"] = _utc_now()
                    write_job(workspace, polled)
                    stop_polling.wait(5)

            poller = threading.Thread(target=metadata_poller, name=f"wrapper-poller-{job_id}", daemon=True)
            poller.start()
            returncode = process.wait()
            stop_polling.set()
            poller.join(timeout=2)

        current = read_job(workspace, job_id) or job
        _sync_from_run_metadata(current)
        current["returncode"] = returncode
        current["status"] = "completed" if returncode == 0 else "failed"
        current["stdoutTail"] = _tail_file(stdout_path)
        current["stderrTail"] = _tail_file(stderr_path)
        append_progress(
            current,
            f"Wrapper finished with exit code {returncode}.",
            level="error" if returncode != 0 else "info",
        )
        write_job(workspace, current)
        if on_progress:
            on_progress(current, f"Wrapper finished with exit code {returncode}.")

    thread = threading.Thread(target=worker, name=f"wrapper-job-{job_id}", daemon=True)
    thread.start()
    job["status"] = "queued"
    write_job(workspace, job)
    return job


def job_status(workspace: Path, job_id: str) -> dict[str, Any]:
    job = read_job(workspace, job_id)
    if not job:
        return {"ok": False, "error": f"Unknown job: {job_id}"}
    _sync_from_run_metadata(job)
    job["updatedAt"] = _utc_now()
    write_job(workspace, job)
    return {"ok": True, "job": job}
