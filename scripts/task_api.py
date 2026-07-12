#!/usr/bin/env python
"""Task-scoped orchestration API backing unreal_task_* MCP tools."""

from __future__ import annotations

import json
import os
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from task_phase import task_phase_from_state

TERMINAL_TASK_STATUSES = frozenset({"completed", "cancelled", "failed", "cancellation_uncertain"})
APPROVABLE_TASK_STATUSES = frozenset({"pending_approval", "awaiting_approval"})


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


TASK_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _validate_task_session_id(task_session_id: str) -> str:
    value = str(task_session_id or "").strip()
    if ".." in value or "/" in value or "\\" in value:
        raise ValueError("taskSessionId must not contain path separators or traversal")
    if not TASK_SESSION_ID_RE.fullmatch(value):
        raise ValueError("taskSessionId must match [A-Za-z0-9_-]{8,64}")
    return value


from state_root import ensure_state_root_layout, resolve_agent_state_root, task_state_dir


def task_root(workspace: Path, task_session_id: str) -> Path:
    safe_id = _validate_task_session_id(task_session_id)
    state_root = ensure_state_root_layout(resolve_agent_state_root(workspace))
    return task_state_dir(safe_id, state_root)


def _state_path(workspace: Path, task_session_id: str) -> Path:
    return task_root(workspace, task_session_id) / "state.json"


def _log_path(workspace: Path, task_session_id: str) -> Path:
    return task_root(workspace, task_session_id) / "logs" / "task.log"


def _read_state(workspace: Path, task_session_id: str) -> dict[str, Any] | None:
    path = _state_path(workspace, task_session_id)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_state(workspace: Path, task_session_id: str, state: dict[str, Any]) -> None:
    root = task_root(workspace, task_session_id)
    root.mkdir(parents=True, exist_ok=True)
    temp = root / f"state.json.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(_state_path(workspace, task_session_id))


@contextmanager
def _task_lock(workspace: Path, task_session_id: str) -> Iterator[None]:
    from write_locks import release_cross_process_lock, try_acquire_cross_process_lock

    state_path = _state_path(workspace, task_session_id)
    acquired = try_acquire_cross_process_lock(state_path, label="task_state")
    if not acquired.get("ok"):
        raise RuntimeError(acquired.get("error") or f"task lock busy: {acquired.get('holder')}")
    try:
        yield
    finally:
        release_cross_process_lock(state_path)


def _mutate_task_state(
    workspace: Path,
    task_session_id: str,
    mutator: Callable[[dict[str, Any]], dict[str, Any] | None],
) -> dict[str, Any]:
    with _task_lock(workspace, task_session_id):
        state = _read_state(workspace, task_session_id)
        if not state:
            return {"ok": False, "error": f"Unknown task: {task_session_id}"}
        updated = mutator(state)
        if updated is None:
            return {"ok": False, "error": "Task mutation rejected", "taskSessionId": task_session_id}
        _write_state(workspace, task_session_id, updated)
        return _task_response(workspace, updated)


def _append_log(workspace: Path, task_session_id: str, message: str, level: str = "info") -> None:
    log_file = _log_path(workspace, task_session_id)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{_utc_now()}] [{level}] {message}\n"
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(line)


def _active_job(workspace: Path, state: dict[str, Any]) -> dict[str, Any] | None:
    job_id = str(state.get("activeJobId") or "").strip()
    if not job_id:
        return None
    from wrapper_job_manager import compact_job_status, read_job

    job = read_job(workspace, job_id)
    if not job:
        return None
    return compact_job_status(job)


def _public_state(state: dict[str, Any]) -> dict[str, Any]:
    public = dict(state)
    public.pop("authToken", None)
    return public


def _task_response(workspace: Path, state: dict[str, Any]) -> dict[str, Any]:
    job = _active_job(workspace, state)
    ux = task_phase_from_state(state, job)
    return {
        "ok": True,
        "taskSessionId": state.get("taskSessionId"),
        "status": state.get("status"),
        **ux,
        "state": _public_state(state),
        "job": job,
    }


def _task_status_from_job_terminal(terminal: str) -> str:
    if terminal == "completed":
        return "completed"
    if terminal == "cancelled":
        return "cancelled"
    if terminal == "cancellation_uncertain":
        return "cancellation_uncertain"
    return "failed"


def _reflect_job_terminal_state(
    workspace: Path,
    task_session_id: str,
    state: dict[str, Any],
) -> dict[str, Any]:
    job = _active_job(workspace, state)
    if not job:
        return state
    terminal = str(job.get("status") or "")
    if terminal not in {"completed", "failed", "timed_out", "cancelled", "cancellation_uncertain"}:
        return state
    if state.get("terminalLogged"):
        return state
    state["status"] = _task_status_from_job_terminal(terminal)
    if terminal == "cancellation_uncertain" and job.get("orphanProcessSuspected"):
        state["orphanProcessSuspected"] = True
    state["updatedAt"] = _utc_now()
    _append_log(workspace, task_session_id, f"Job {job.get('jobId')} finished: {terminal}")
    state["terminalLogged"] = True
    return state


def bind_active_job(workspace: Path, task_session_id: str, job_id: str) -> dict[str, Any]:
    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        state["activeJobId"] = job_id
        state["updatedAt"] = _utc_now()
        _append_log(workspace, task_session_id, f"Bound active job {job_id}")
        return state

    return _mutate_task_state(workspace, task_session_id, mutate)


def task_start(
    workspace: Path,
    *,
    request: str,
    mode: str = "agent_edit",
    project_file: str = "",
    plan_id: str = "",
    start_background_job: bool = False,
    on_progress: Callable[[dict[str, Any], str], None] | None = None,
) -> dict[str, Any]:
    task_session_id = uuid.uuid4().hex[:16]
    auth_token = uuid.uuid4().hex
    state = {
        "taskSessionId": task_session_id,
        "status": "running",
        "request": request,
        "mode": mode,
        "projectFile": project_file,
        "planId": plan_id or uuid.uuid4().hex[:12],
        "planRevision": "1",
        "activeSliceId": "",
        "activeJobId": "",
        "authToken": auth_token,
        "createdAt": _utc_now(),
        "updatedAt": _utc_now(),
        "toolDiscoveryCandidates": [
            "unreal_rag_search",
            "read_file",
            "replace_in_file",
            "build_unreal_project",
        ],
    }
    (task_root(workspace, task_session_id) / "logs").mkdir(parents=True, exist_ok=True)
    with _task_lock(workspace, task_session_id):
        _write_state(workspace, task_session_id, state)
    _append_log(workspace, task_session_id, f"Task started: {request[:200]}")

    if start_background_job and request.strip():
        from wrapper_job_manager import (
            append_progress as job_append_progress,
            create_job,
            launch_job,
            read_job,
            save_job,
            transition_job_status,
        )

        job_args: dict[str, Any] = {
            "request": request,
            "mode": mode,
            "project_file": project_file,
        }

        def _progress(job: dict[str, Any], message: str) -> None:
            _append_log(workspace, task_session_id, message)
            if on_progress:
                on_progress(job, message)

        job = create_job(workspace, job_args)
        job_id = str(job.get("jobId") or "")

        def bind_job(current: dict[str, Any]) -> dict[str, Any] | None:
            current["activeJobId"] = job_id
            current["updatedAt"] = _utc_now()
            return current

        bound = _mutate_task_state(workspace, task_session_id, bind_job)
        if not bound.get("ok"):
            latest = read_job(workspace, job_id) or job
            transition_job_status(latest, "cancelled")
            latest["taskBindFailed"] = True
            job_append_progress(latest, "Task bind failed before worker launch.")
            save_job(workspace, latest)
        else:
            launch_job(workspace, job_id, on_progress=_progress)
            state = bound["state"]
            state["activeJobId"] = job_id

    payload = _task_response(workspace, state)
    payload["authToken"] = auth_token
    return payload


def task_status(workspace: Path, task_session_id: str) -> dict[str, Any]:
    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        return _reflect_job_terminal_state(workspace, task_session_id, state)

    try:
        return _mutate_task_state(workspace, task_session_id, mutate)
    except RuntimeError as exc:
        if "task lock busy" not in str(exc):
            raise
        state = _read_state(workspace, task_session_id)
        if not state:
            return {"ok": False, "error": f"Unknown task: {task_session_id}"}
        return _task_response(workspace, state)


def task_approve(workspace: Path, task_session_id: str, *, note: str = "") -> dict[str, Any]:
    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        status = str(state.get("status") or "")
        if status in TERMINAL_TASK_STATUSES:
            return None
        if status not in APPROVABLE_TASK_STATUSES and status != "running":
            return None
        state["status"] = "running"
        state["approvalNote"] = note
        state["updatedAt"] = _utc_now()
        _append_log(workspace, task_session_id, f"Approved: {note[:200]}")
        return state

    result = _mutate_task_state(workspace, task_session_id, mutate)
    if result.get("ok") is False and "Unknown task" not in str(result.get("error") or ""):
        result["error"] = "Approve rejected: task is not awaiting approval or is already terminal."
    return result


def task_cancel(workspace: Path, task_session_id: str) -> dict[str, Any]:
    cancel_error: dict[str, Any] | None = None
    cancel_meta: dict[str, Any] = {}

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal cancel_error, cancel_meta
        if str(state.get("status") or "") in TERMINAL_TASK_STATUSES:
            cancel_error = {
                "ok": False,
                "error": "Cancel rejected: task is already terminal.",
                "taskSessionId": task_session_id,
            }
            return None
        job_id = str(state.get("activeJobId") or "").strip()
        if job_id:
            from wrapper_job_manager import cancel_job

            cancel_result = cancel_job(workspace, job_id)
            _append_log(
                workspace,
                task_session_id,
                f"Cancelled job {job_id}: {cancel_result.get('ok')}",
            )
            if not cancel_result.get("ok"):
                cancel_error = {
                    "ok": False,
                    "error": cancel_result.get("error") or "cancel_job failed",
                    "taskSessionId": task_session_id,
                    "jobId": job_id,
                }
                return None
            cancel_state = str(cancel_result.get("cancellationState") or "")
            cancel_meta = {
                "cancellationState": cancel_state,
                "orphanProcessSuspected": bool(cancel_result.get("orphanProcessSuspected")),
            }
            if cancel_state == "cancellation_uncertain":
                state["status"] = "cancellation_uncertain"
                if cancel_meta["orphanProcessSuspected"]:
                    state["orphanProcessSuspected"] = True
            elif cancel_state in {"failed", "timed_out"}:
                state["status"] = "failed"
            elif cancel_state == "completed":
                state["status"] = "completed"
            else:
                state["status"] = "cancelled"
        else:
            state["status"] = "cancelled"
            cancel_meta = {"cancellationState": "cancelled", "orphanProcessSuspected": False}
        state["updatedAt"] = _utc_now()
        _append_log(workspace, task_session_id, f"Task {state['status']}")
        return state

    result = _mutate_task_state(workspace, task_session_id, mutate)
    if cancel_error:
        return cancel_error
    if result.get("ok") is False:
        if "Unknown task" in str(result.get("error") or ""):
            return result
        return {
            "ok": False,
            "error": "Cancel rejected: task is already terminal.",
            "taskSessionId": task_session_id,
        }
    result.update(cancel_meta)
    return result


def task_resume(workspace: Path, task_session_id: str) -> dict[str, Any]:
    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        if str(state.get("status") or "") in TERMINAL_TASK_STATUSES:
            return None
        state["updatedAt"] = _utc_now()
        _append_log(workspace, task_session_id, "Task resumed")
        return state

    result = _mutate_task_state(workspace, task_session_id, mutate)
    if result.get("ok") is False and "Unknown task" not in str(result.get("error") or ""):
        result["error"] = "Resume rejected: start a new task instead of resuming a terminal session."
        result["taskSessionId"] = task_session_id
    return result
