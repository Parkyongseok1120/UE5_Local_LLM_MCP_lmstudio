#!/usr/bin/env python
"""Task-scoped orchestration API backing unreal_task_* MCP tools."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from task_phase import task_phase_from_state


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def task_root(workspace: Path, task_session_id: str) -> Path:
    return workspace / ".agent" / "tasks" / task_session_id


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
    temp = root / "state.json.tmp"
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(_state_path(workspace, task_session_id))


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


def bind_active_job(workspace: Path, task_session_id: str, job_id: str) -> dict[str, Any]:
    state = _read_state(workspace, task_session_id)
    if not state:
        return {"ok": False, "error": f"Unknown task: {task_session_id}"}
    state["activeJobId"] = job_id
    state["updatedAt"] = _utc_now()
    _write_state(workspace, task_session_id, state)
    _append_log(workspace, task_session_id, f"Bound active job {job_id}")
    return _task_response(workspace, state)


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
    _write_state(workspace, task_session_id, state)
    (task_root(workspace, task_session_id) / "logs").mkdir(parents=True, exist_ok=True)
    _append_log(workspace, task_session_id, f"Task started: {request[:200]}")

    if start_background_job and request.strip():
        from wrapper_job_manager import start_job

        job_args: dict[str, Any] = {
            "request": request,
            "mode": mode,
            "project_file": project_file,
        }

        def _progress(job: dict[str, Any], message: str) -> None:
            _append_log(workspace, task_session_id, message)
            if on_progress:
                on_progress(job, message)

        job = start_job(workspace, job_args, on_progress=_progress)
        state["activeJobId"] = job.get("jobId") or ""
        state["updatedAt"] = _utc_now()
        _write_state(workspace, task_session_id, state)

    payload = _task_response(workspace, state)
    payload["authToken"] = auth_token
    return payload


def task_status(workspace: Path, task_session_id: str) -> dict[str, Any]:
    state = _read_state(workspace, task_session_id)
    if not state:
        return {"ok": False, "error": f"Unknown task: {task_session_id}"}

    job = _active_job(workspace, state)
    if job and str(job.get("status") or "") in {"completed", "failed", "timed_out", "cancelled"}:
        terminal = str(job.get("status") or "")
        if terminal == "completed":
            state["status"] = "completed"
        elif terminal == "cancelled":
            state["status"] = "cancelled"
        else:
            state["status"] = "failed"
        state["updatedAt"] = _utc_now()
        _write_state(workspace, task_session_id, state)
        _append_log(workspace, task_session_id, f"Job {job.get('jobId')} finished: {terminal}")

    return _task_response(workspace, state)


def task_approve(workspace: Path, task_session_id: str, *, note: str = "") -> dict[str, Any]:
    state = _read_state(workspace, task_session_id)
    if not state:
        return {"ok": False, "error": f"Unknown task: {task_session_id}"}
    state["status"] = "running"
    state["approvalNote"] = note
    state["updatedAt"] = _utc_now()
    _write_state(workspace, task_session_id, state)
    _append_log(workspace, task_session_id, f"Approved: {note[:200]}")
    return _task_response(workspace, state)


def task_cancel(workspace: Path, task_session_id: str) -> dict[str, Any]:
    state = _read_state(workspace, task_session_id)
    if not state:
        return {"ok": False, "error": f"Unknown task: {task_session_id}"}

    job_id = str(state.get("activeJobId") or "").strip()
    if job_id:
        from wrapper_job_manager import cancel_job

        cancel_result = cancel_job(workspace, job_id)
        _append_log(
            workspace,
            task_session_id,
            f"Cancelled job {job_id}: {cancel_result.get('ok')}",
        )

    state["status"] = "cancelled"
    state["updatedAt"] = _utc_now()
    _write_state(workspace, task_session_id, state)
    _append_log(workspace, task_session_id, "Task cancelled")
    return _task_response(workspace, state)


def task_resume(workspace: Path, task_session_id: str) -> dict[str, Any]:
    state = _read_state(workspace, task_session_id)
    if not state:
        return {"ok": False, "error": f"Unknown task: {task_session_id}"}
    if state.get("status") in {"cancelled", "failed"}:
        state["status"] = "running"
    state["updatedAt"] = _utc_now()
    _write_state(workspace, task_session_id, state)
    _append_log(workspace, task_session_id, "Task resumed")
    return _task_response(workspace, state)
