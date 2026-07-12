#!/usr/bin/env python
"""Background wrapper jobs for non-blocking MCP compile loops."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

TERMINAL_STATUSES = frozenset({"completed", "failed", "timed_out", "cancelled", "cancellation_uncertain"})
VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    "created": frozenset({"queued", "cancelled", "cancel_requested"}),
    "starting": frozenset({"queued", "running", "cancelled", "cancel_requested"}),
    "queued": frozenset({"starting", "running", "cancelled", "cancel_requested"}),
    "running": frozenset({"completed", "failed", "timed_out", "cancelled", "cancel_requested", "cancellation_uncertain"}),
    "cancel_requested": frozenset({"cancelled", "cancellation_uncertain"}),
}

_JOB_LOCKS: dict[str, threading.Lock] = {}
_LOCK_GUARD = threading.Lock()


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _job_lock(job_id: str) -> threading.Lock:
    with _LOCK_GUARD:
        if job_id not in _JOB_LOCKS:
            _JOB_LOCKS[job_id] = threading.Lock()
        return _JOB_LOCKS[job_id]


def jobs_root(workspace: Path) -> Path:
    path = workspace / "data" / "mcp_wrapper_jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def job_path(workspace: Path, job_id: str) -> Path:
    return jobs_root(workspace) / f"{job_id}.json"


def _read_json_with_retry(path: Path, *, attempts: int = 8, delay_sec: float = 0.05) -> dict[str, Any] | None:
    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
            time.sleep(delay_sec)
    if last_error:
        raise last_error
    return None


def read_job(workspace: Path, job_id: str) -> dict[str, Any] | None:
    from job_store import JobStoreError, read_job_record, validate_job_id

    job_id = validate_job_id(job_id)
    try:
        record = read_job_record(job_id, workspace=workspace)
        if record:
            return record
    except JobStoreError:
        if not _legacy_json_enabled():
            raise
    if not _legacy_json_enabled():
        return None
    path = job_path(workspace, job_id)
    if not path.exists():
        return None
    try:
        return _read_json_with_retry(path)
    except (OSError, json.JSONDecodeError):
        return None


def _legacy_json_enabled() -> bool:
    return os.environ.get("MCP_JOBS_LEGACY_JSON", "").strip() == "1"


def _atomic_replace_with_retry(temp: Path, path: Path, *, attempts: int = 8, delay_sec: float = 0.05) -> None:
    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            temp.replace(path)
            return
        except OSError as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
            time.sleep(delay_sec)
    if last_error:
        raise last_error


def write_job(workspace: Path, job: dict[str, Any], *, expected_revision: int | None = None) -> bool:
    from job_store import JobStoreError, validate_job_id, write_job_record

    job_id = validate_job_id(str(job["jobId"]))
    try:
        return write_job_record(job, expected_revision=expected_revision, workspace=workspace)
    except JobStoreError:
        if not _legacy_json_enabled():
            raise
    lock = _job_lock(job_id)
    with lock:
        current = _read_legacy_job(workspace, job_id)
        if expected_revision is not None:
            if not current:
                return False
            if int(current.get("revision") or 0) != expected_revision:
                return False
            merged = {**current, **job}
            merged["revision"] = int(current.get("revision") or 0) + 1
            job = merged
        elif current:
            merged = {**current, **job}
            merged["revision"] = int(current.get("revision") or 0) + 1
            job = merged
        else:
            job = dict(job)
            job["revision"] = max(1, int(job.get("revision") or 0) or 1)
        path = job_path(workspace, job_id)
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        _atomic_replace_with_retry(temp, path)
        return True


def _read_legacy_job(workspace: Path, job_id: str) -> dict[str, Any] | None:
    path = job_path(workspace, job_id)
    if not path.exists():
        return None
    try:
        return _read_json_with_retry(path)
    except (OSError, json.JSONDecodeError):
        return None


def save_job(workspace: Path, job: dict[str, Any]) -> bool:
    job_id = str(job.get("jobId") or "")
    if not job_id:
        return False
    patch = dict(job)

    def mutator(draft: dict[str, Any]) -> None:
        from job_store import _merge_payload

        merged = _merge_payload(draft, patch)
        if merged is None:
            raise ValueError("job merge rejected")
        draft.clear()
        draft.update(merged)

    try:
        from job_store import mutate_job_record

        return mutate_job_record(job_id, mutator, workspace=workspace)
    except ValueError:
        return False
    except Exception:
        pass
    current = read_job(workspace, job_id)
    if current is None:
        return write_job(workspace, job)
    from job_store import _merge_payload

    safe = _merge_payload(current, patch)
    if safe is None:
        return False
    expected = int(current.get("revision") or 0)
    return write_job(workspace, safe, expected_revision=expected)


def command_fingerprint(command: list[str]) -> str:
    return hashlib.sha256("\0".join(str(part) for part in command).encode("utf-8")).hexdigest()


def transition_job_status(job: dict[str, Any], next_status: str) -> bool:
    current = str(job.get("status") or "created")
    if current in TERMINAL_STATUSES:
        return False
    allowed = VALID_TRANSITIONS.get(current, frozenset())
    if next_status not in allowed and not (current == "starting" and next_status == "running"):
        if current == "starting" and next_status == "queued":
            job["status"] = next_status
            return True
        return False
    job["status"] = next_status
    return True


def append_progress(job: dict[str, Any], message: str, level: str = "info") -> None:
    seq = int(job.get("progressSequence") or 0) + 1
    job["progressSequence"] = seq
    job.setdefault("progress", []).append(
        {"seq": seq, "at": _utc_now(), "level": level, "message": message}
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


def compact_job_status(job: dict[str, Any], *, since_progress_sequence: int = 0) -> dict[str, Any]:
    from task_phase import job_phase_from_status

    progress = list(job.get("progress") or [])
    if since_progress_sequence:
        delta = [entry for entry in progress if int(entry.get("seq") or 0) > since_progress_sequence]
    else:
        delta = progress[-5:]
    base = {
        "jobId": job.get("jobId"),
        "status": job.get("status"),
        "phase": job.get("currentAttempt") or job.get("status"),
        "attempt": job.get("currentAttempt"),
        "runDir": job.get("runDir"),
        "stateRevision": int(job.get("revision") or 0),
        "progressSequence": int(job.get("progressSequence") or 0),
        "progressDelta": delta,
        "hasNewOutput": bool(delta),
        "returncode": job.get("returncode"),
        "updatedAt": job.get("updatedAt"),
    }
    base.update(job_phase_from_status(job))
    return base


def list_jobs(workspace: Path, limit: int = 20) -> list[dict[str, Any]]:
    from job_store import list_job_records, prune_terminal_jobs

    prune_terminal_jobs(workspace)
    jobs = list_job_records(workspace, limit=limit)
    return [compact_job_status(job) for job in jobs]


def _prune_stale_jobs(workspace: Path, ttl_hours: int = 24) -> None:
    from job_store import prune_terminal_jobs

    prune_terminal_jobs(workspace, ttl_hours=ttl_hours)


def _process_alive(pid: int) -> str:
    from process_probe import probe_process_alive

    return probe_process_alive(pid)


def _pid_matches_job(job: dict[str, Any]) -> bool:
    from process_identity import verify_job_process

    return verify_job_process(job)


def _kill_process_tree(pid: int) -> bool:
    from process_probe import ProbeTimeout, run_probe

    if sys.platform == "win32":
        result = run_probe(["taskkill", "/PID", str(pid), "/T", "/F"])
        if isinstance(result, ProbeTimeout):
            return False
        return result.returncode == 0
    try:
        os.kill(pid, 15)
        return True
    except OSError:
        return False


def _is_cancelled(workspace: Path, job_id: str) -> bool:
    job = read_job(workspace, job_id)
    return bool(job and str(job.get("status") or "") in {"cancelled", "cancel_requested"})


def _finalize_cancelled_worker(workspace: Path, job_id: str, current: dict[str, Any], *, message: str) -> None:
    latest = read_job(workspace, job_id) or current
    if str(latest.get("status") or "") == "cancel_requested":
        transition_job_status(latest, "cancelled")
        append_progress(latest, message)
        save_job(workspace, latest)


def cancel_job(workspace: Path, job_id: str) -> dict[str, Any]:
    job = read_job(workspace, job_id)
    if not job:
        return {"ok": False, "error": f"Unknown job: {job_id}"}
    if str(job.get("status") or "") in TERMINAL_STATUSES:
        terminal = str(job.get("status") or "")
        return {
            "ok": True,
            "job": compact_job_status(job),
            "processTreeKilled": False,
            "cancellationState": terminal,
            "orphanProcessSuspected": bool(job.get("orphanProcessSuspected")),
        }
    if not transition_job_status(job, "cancel_requested"):
        job["status"] = "cancel_requested"
    append_progress(job, "Cancel requested.")
    if not save_job(workspace, job):
        return {"ok": False, "error": "Failed to persist cancel_requested", "jobId": job_id}

    fresh = read_job(workspace, job_id) or job
    pid = fresh.get("pid")
    orphan_suspected = False
    process_tree_killed = False
    next_status = "cancelled"
    if pid:
        pid_int = int(pid)
        alive = _process_alive(pid_int)
        if alive == "alive":
            if _pid_matches_job(fresh):
                process_tree_killed = _kill_process_tree(pid_int)
                orphan_suspected = not process_tree_killed
                if not process_tree_killed:
                    next_status = "cancellation_uncertain"
            else:
                orphan_suspected = True
                next_status = "cancellation_uncertain"
        elif alive == "unknown":
            orphan_suspected = True
            next_status = "cancellation_uncertain"
    if not transition_job_status(fresh, next_status):
        fresh["status"] = next_status
    append_progress(fresh, "Job cancelled by request.")
    if orphan_suspected:
        fresh["orphanProcessSuspected"] = True
    if not save_job(workspace, fresh):
        return {"ok": False, "error": "Failed to persist cancelled job state", "jobId": job_id}
    return {
        "ok": True,
        "job": compact_job_status(read_job(workspace, job_id) or fresh),
        "processTreeKilled": process_tree_killed,
        "orphanProcessSuspected": orphan_suspected,
        "cancellationState": str((read_job(workspace, job_id) or fresh).get("status") or ""),
    }


def build_wrapper_command(workspace: Path, run_dir: Path, arguments: dict[str, Any]) -> list[str]:
    from workspace_paths import resolve_index_dir

    index_dir = resolve_index_dir()
    script = workspace / "scripts" / "lmstudio_unreal_wrapper.py"
    command = [
        sys.executable,
        str(script),
        "--request",
        str(arguments.get("request") or ""),
        "--index",
        str((index_dir / "rag.sqlite").resolve()),
        "--module-graph",
        str((index_dir / "raw_module_graph.jsonl").resolve()),
        "--project-name",
        str(arguments.get("project_name") or "ScratchPrototype"),
        "--mode",
        str(arguments.get("mode") or "agent_edit"),
        "--max-attempts",
        str(max(1, min(6, int(arguments.get("max_attempts") or 4)))),
        "--max-total-model-calls",
        str(max(1, int(arguments.get("max_total_model_calls") or 40))),
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
        "status": "created",
        "revision": 0,
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
    transition_job_status(job, "queued")
    write_job(workspace, job)

    command = build_wrapper_command(workspace, run_dir, arguments)
    cmd_fp = command_fingerprint(command)

    def worker() -> None:
        current = read_job(workspace, job_id) or job
        if _is_cancelled(workspace, job_id):
            _finalize_cancelled_worker(
                workspace,
                job_id,
                current,
                message="Job cancelled before wrapper subprocess start.",
            )
            return

        timeout_sec = max(0, int(arguments.get("timeoutSec") or 0))
        timed_out = False
        process: subprocess.Popen[str] | None = None

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

            def mark_running(draft: dict[str, Any]) -> None:
                append_progress(draft, "Wrapper subprocess started.")
                draft["pid"] = process.pid
                draft["pidStartedAt"] = _utc_now()
                draft["commandFingerprint"] = cmd_fp
                draft["command"] = command

            try:
                from job_store import transition_job_record

                if not transition_job_record(job_id, "running", mark_running, workspace=workspace):
                    process.kill()
                    return
            except Exception:
                current = read_job(workspace, job_id) or job
                if _is_cancelled(workspace, job_id):
                    process.kill()
                    return
                transition_job_status(current, "running")
                mark_running(current)
                save_job(workspace, current)

            current = read_job(workspace, job_id) or job
            if on_progress:
                on_progress(current, "Wrapper subprocess started.")

            stop_polling = threading.Event()

            def metadata_poller() -> None:
                while not stop_polling.is_set():
                    if _is_cancelled(workspace, job_id):
                        return
                    polled = read_job(workspace, job_id)
                    if not polled:
                        return
                    status = str(polled.get("status") or "")
                    if status in {"cancel_requested", "cancelled", "cancellation_uncertain"}:
                        return
                    before = polled.get("currentAttempt")
                    _sync_from_run_metadata(polled)
                    after = polled.get("currentAttempt")
                    if after and after != before:
                        append_progress(polled, f"In progress: {after}")
                        if on_progress:
                            on_progress(polled, f"In progress: {after}")
                    polled["updatedAt"] = _utc_now()
                    if not save_job(workspace, polled):
                        return
                    stop_polling.wait(5)

            poller = threading.Thread(target=metadata_poller, name=f"wrapper-poller-{job_id}", daemon=True)
            poller.start()
            if timeout_sec > 0:
                try:
                    returncode = process.wait(timeout=timeout_sec)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    _kill_process_tree(process.pid)
                    returncode = process.wait(timeout=30)
            else:
                returncode = process.wait()
            stop_polling.set()
            poller.join(timeout=2)

        if _is_cancelled(workspace, job_id):
            return
        current = read_job(workspace, job_id) or job
        _sync_from_run_metadata(current)
        current["returncode"] = returncode
        if timed_out:
            transition_job_status(current, "timed_out")
            append_progress(current, f"Wrapper timed out after {timeout_sec}s.", level="error")
        else:
            transition_job_status(current, "completed" if returncode == 0 else "failed")
        current["stdoutTail"] = _tail_file(stdout_path)
        current["stderrTail"] = _tail_file(stderr_path)
        append_progress(
            current,
            f"Wrapper finished with exit code {returncode}.",
            level="error" if returncode != 0 else "info",
        )
        save_job(workspace, current)
        if on_progress:
            on_progress(current, f"Wrapper finished with exit code {returncode}.")

    thread = threading.Thread(target=worker, name=f"wrapper-job-{job_id}", daemon=True)
    thread.start()
    return compact_job_status(read_job(workspace, job_id) or job)


def job_status(
    workspace: Path,
    job_id: str,
    *,
    compact: bool = True,
    since_progress_sequence: int = 0,
    since_revision: int | None = None,
) -> dict[str, Any]:
    job = read_job(workspace, job_id)
    if not job:
        return {"ok": False, "error": f"Unknown job: {job_id}"}
    _sync_from_run_metadata(job)
    payload = (
        compact_job_status(job, since_progress_sequence=since_progress_sequence)
        if compact
        else job
    )
    if since_revision is not None:
        payload["revisionChanged"] = int(job.get("revision") or 0) > since_revision
    return {"ok": True, "job": payload}


def read_job_log_page(workspace: Path, job_id: str, *, stream: str = "stdout", offset: int = 0, limit: int = 8000) -> dict[str, Any]:
    job = read_job(workspace, job_id)
    if not job:
        return {"ok": False, "error": f"Unknown job: {job_id}"}
    path = Path(job.get("stdoutPath" if stream == "stdout" else "stderrPath") or "")
    if not path.is_file():
        return {"ok": True, "text": "", "offset": offset, "eof": True}
    text = path.read_text(encoding="utf-8", errors="replace")
    page = text[offset : offset + limit]
    return {"ok": True, "text": page, "offset": offset + len(page), "eof": offset + len(page) >= len(text)}


def start_rag_refresh_job(
    workspace: Path,
    arguments: dict[str, Any],
    on_progress: Callable[[dict[str, Any], str], None] | None = None,
) -> dict[str, Any]:
    scope = str(arguments.get("scope") or "all")
    force = bool(arguments.get("force"))
    timeout_sec = max(0, int(arguments.get("timeoutSec") or 600))

    job_id = uuid.uuid4().hex[:12]
    job: dict[str, Any] = {
        "jobId": job_id,
        "jobType": "rag_refresh",
        "status": "created",
        "revision": 0,
        "createdAt": _utc_now(),
        "updatedAt": _utc_now(),
        "arguments": {"scope": scope, "force": force, "timeoutSec": timeout_sec},
        "progress": [],
        "result": None,
    }
    append_progress(job, f"RAG refresh job created (scope={scope}).")
    transition_job_status(job, "queued")
    write_job(workspace, job)

    script = workspace / "scripts" / "rag_refresh.py"
    command = [
        sys.executable,
        str(script),
        "--scope",
        scope,
        "--workspace",
        str(workspace.resolve()),
    ]
    if force:
        command.append("--force")
    cmd_fp = command_fingerprint(command)

    def worker() -> None:
        current = read_job(workspace, job_id) or job
        if _is_cancelled(workspace, job_id):
            _finalize_cancelled_worker(
                workspace,
                job_id,
                current,
                message="Job cancelled before RAG refresh subprocess start.",
            )
            return

        process = subprocess.Popen(
            command,
            cwd=str(workspace),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        def mark_running(draft: dict[str, Any]) -> None:
            append_progress(draft, "RAG refresh subprocess started.")
            draft["pid"] = process.pid
            draft["pidStartedAt"] = _utc_now()
            draft["commandFingerprint"] = cmd_fp
            draft["command"] = command

        try:
            from job_store import transition_job_record

            if not transition_job_record(job_id, "running", mark_running, workspace=workspace):
                process.kill()
                return
        except Exception:
            current = read_job(workspace, job_id) or job
            if _is_cancelled(workspace, job_id):
                process.kill()
                return
            transition_job_status(current, "running")
            mark_running(current)
            save_job(workspace, current)

        try:
            stdout, stderr = process.communicate(timeout=timeout_sec if timeout_sec > 0 else None)
        except subprocess.TimeoutExpired:
            _kill_process_tree(process.pid)
            process.kill()
            current = read_job(workspace, job_id) or job
            if not _is_cancelled(workspace, job_id):
                transition_job_status(current, "timed_out")
                append_progress(current, f"RAG refresh timed out after {timeout_sec}s.", level="error")
            save_job(workspace, current)
            return
        if _is_cancelled(workspace, job_id):
            return
        current = read_job(workspace, job_id) or job
        payload: dict[str, Any] | None = None
        try:
            payload = json.loads(stdout.strip() or "{}")
        except json.JSONDecodeError:
            payload = {"ok": process.returncode == 0, "stdout": stdout[-4000:], "stderr": stderr[-4000:]}
        current["result"] = payload
        if process.returncode == 0 and payload.get("ok", True):
            transition_job_status(current, "completed")
            append_progress(current, "RAG refresh finished.")
        else:
            transition_job_status(current, "failed")
            append_progress(current, stderr or "RAG refresh failed.", level="error")
        save_job(workspace, current)
        if on_progress:
            on_progress(current, f"RAG refresh job {current['status']}.")

    threading.Thread(target=worker, name=f"rag-refresh-job-{job_id}", daemon=True).start()
    return compact_job_status(read_job(workspace, job_id) or job)
