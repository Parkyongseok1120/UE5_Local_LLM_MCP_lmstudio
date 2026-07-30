#!/usr/bin/env python
"""Task-scoped orchestration API backing unreal_task_* MCP tools."""

from __future__ import annotations

import json
import hashlib
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from atomic_io import atomic_write_text
from state_root import ensure_state_root_layout, resolve_agent_state_root, task_state_dir
from task_phase import task_phase_from_state

TERMINAL_TASK_STATUSES = frozenset({"completed", "cancelled", "failed", "cancellation_uncertain"})
APPROVABLE_TASK_STATUSES = frozenset({"pending_approval", "awaiting_approval"})


class TaskStateReadError(RuntimeError):
    """Raised when a persisted task record exists but cannot be trusted."""


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def required_gate_set_hash(
    *,
    task_session_id: str,
    plan_id: str,
    plan_revision: str,
    active_slice_id: str,
    project_file: str,
    required_gates: list[str],
) -> str:
    return _canonical_hash(
        {
            "taskSessionId": task_session_id,
            "planId": plan_id,
            "planRevision": plan_revision,
            "activeSliceId": active_slice_id,
            "projectFile": project_file,
            "requiredBeforeWrite": required_gates,
        }
    )


TASK_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _validate_task_session_id(task_session_id: str) -> str:
    value = str(task_session_id or "").strip()
    if ".." in value or "/" in value or "\\" in value:
        raise ValueError("taskSessionId must not contain path separators or traversal")
    if not TASK_SESSION_ID_RE.fullmatch(value):
        raise ValueError("taskSessionId must match [A-Za-z0-9_-]{8,64}")
    return value


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
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TaskStateReadError(f"Task state is unreadable: {path.name}") from exc
    if not isinstance(payload, dict):
        raise TaskStateReadError(f"Task state must be a JSON object: {path.name}")
    persisted_id = str(payload.get("taskSessionId") or "").strip()
    if persisted_id != task_session_id:
        raise TaskStateReadError(
            f"Task state identity mismatch: expected {task_session_id}, found {persisted_id or 'missing'}"
        )
    return payload


def _write_state(workspace: Path, task_session_id: str, state: dict[str, Any]) -> None:
    root = task_root(workspace, task_session_id)
    root.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        _state_path(workspace, task_session_id),
        json.dumps(state, ensure_ascii=False, indent=2),
    )


def _task_state_error(task_session_id: str, exc: TaskStateReadError) -> dict[str, Any]:
    return {
        "ok": False,
        "error": str(exc),
        "errorCode": "TASK_STATE_CORRUPT",
        "taskSessionId": task_session_id,
        "recovery": "Preserve the corrupt state file for diagnosis and start a new task.",
    }


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
        try:
            state = _read_state(workspace, task_session_id)
        except TaskStateReadError as exc:
            return _task_state_error(task_session_id, exc)
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
    plan_payload: dict[str, Any] | None = None,
    start_background_job: bool = False,
    on_progress: Callable[[dict[str, Any], str], None] | None = None,
) -> dict[str, Any]:
    if plan_payload is None:
        from agent_orchestrator import build_agent_plan

        planner_mode = "planning" if mode in {"read_only", "plan_only"} else "auto"
        plan_payload = build_agent_plan(request, planner_mode).to_dict()

    write_gate = dict(plan_payload.get("writeGate") or {})
    if mode in {"read_only", "plan_only"}:
        write_gate["writesAllowed"] = False
    writes_allowed = write_gate.get("writesAllowed") is True

    slices = list(plan_payload.get("executablePlanSlices") or plan_payload.get("planSlices") or [])
    active_slice_id = "task"
    for item in slices:
        if not isinstance(item, dict):
            continue
        candidate = str(item.get("sliceId") or item.get("slice_id") or "").strip()
        if candidate:
            active_slice_id = candidate
            break

    task_session_id = uuid.uuid4().hex[:16]
    auth_token = uuid.uuid4().hex
    required_before_write = list(
        dict.fromkeys(
            str(item).strip()
            for item in ((plan_payload.get("orchestration") or {}).get("requiredBeforeWrite") or [])
            if str(item).strip()
        )
    )
    resolved_plan_id = plan_id or str(plan_payload.get("planId") or uuid.uuid4().hex[:12])
    resolved_plan_revision = str(plan_payload.get("planRevision") or "1")
    gate_set_hash = required_gate_set_hash(
        task_session_id=task_session_id,
        plan_id=resolved_plan_id,
        plan_revision=resolved_plan_revision,
        active_slice_id=active_slice_id,
        project_file=project_file,
        required_gates=required_before_write,
    )
    write_gate["requiredBeforeWrite"] = required_before_write
    write_gate["pendingBeforeWrite"] = list(required_before_write)
    state = {
        "taskSessionId": task_session_id,
        "status": "running",
        "request": request,
        "mode": mode,
        "projectFile": project_file,
        "planId": resolved_plan_id,
        "planRevision": resolved_plan_revision,
        "activeSliceId": active_slice_id,
        "activeJobId": "",
        "authToken": auth_token,
        "writeGate": write_gate,
        "writesAllowed": writes_allowed,
        "requiredBeforeWrite": required_before_write,
        "requiredGateSetHash": gate_set_hash,
        "completedGates": {},
        "pendingGates": list(required_before_write),
        "maxFilesPerEdit": int(write_gate.get("maxFilesPerEdit") or 2),
        "taskKind": str(plan_payload.get("taskKind") or ""),
        "editStrategy": str(plan_payload.get("editStrategy") or ""),
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

        try:
            job = create_job(workspace, job_args)
        except Exception as exc:
            create_error = str(exc)

            def mark_create_failed(current: dict[str, Any]) -> dict[str, Any]:
                current["status"] = "failed"
                current["jobCreateError"] = create_error
                current["updatedAt"] = _utc_now()
                _append_log(
                    workspace,
                    task_session_id,
                    f"Background job creation failed: {create_error}",
                    level="error",
                )
                return current

            failed = _mutate_task_state(
                workspace,
                task_session_id,
                mark_create_failed,
            )
            if not failed.get("ok"):
                failed["backgroundJobError"] = create_error
                failed["authToken"] = auth_token
                return failed
            failed.update(
                {
                    "ok": False,
                    "error": f"Background job creation failed: {create_error}",
                    "errorCode": "JOB_CREATE_FAILED",
                    "authToken": auth_token,
                }
            )
            return failed
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
            bound["error"] = bound.get("error") or "Task bind failed before worker launch."
            bound["errorCode"] = bound.get("errorCode") or "JOB_BIND_FAILED"
            bound["backgroundJobId"] = job_id
            bound["authToken"] = auth_token
            return bound
        else:
            try:
                launch_job(workspace, job_id, on_progress=_progress)
            except Exception as exc:
                launch_error = str(exc)
                latest = read_job(workspace, job_id) or job
                if not transition_job_status(latest, "failed"):
                    latest["status"] = "failed"
                latest["launchFailed"] = True
                job_append_progress(
                    latest,
                    f"Background worker launch failed: {launch_error}",
                    level="error",
                )
                save_job(workspace, latest)

                def mark_launch_failed(current: dict[str, Any]) -> dict[str, Any]:
                    current["status"] = "failed"
                    current["launchError"] = launch_error
                    current["updatedAt"] = _utc_now()
                    _append_log(
                        workspace,
                        task_session_id,
                        f"Background worker launch failed: {launch_error}",
                        level="error",
                    )
                    return current

                failed = _mutate_task_state(
                    workspace,
                    task_session_id,
                    mark_launch_failed,
                )
                if not failed.get("ok"):
                    failed["backgroundJobError"] = launch_error
                    failed["backgroundJobId"] = job_id
                    failed["authToken"] = auth_token
                    return failed
                failed.update(
                    {
                        "ok": False,
                        "error": f"Background worker launch failed: {launch_error}",
                        "errorCode": "JOB_LAUNCH_FAILED",
                        "authToken": auth_token,
                    }
                )
                return failed
            state = bound["state"]
            state["activeJobId"] = job_id

    payload = _task_response(workspace, state)
    payload["authToken"] = auth_token
    return payload


def task_record_gate(
    workspace: Path,
    *,
    gate_name: str,
    task_authorization: dict[str, Any],
    input_payload: dict[str, Any],
    evidence: dict[str, Any],
    target_snapshots: list[dict[str, Any]] | None = None,
    ttl_seconds: int = 1800,
) -> dict[str, Any]:
    """Record one successful pre-write gate against its exact plan and evidence."""

    gate = str(gate_name or "").strip()
    authorization = task_authorization if isinstance(task_authorization, dict) else {}
    task_session_id = str(
        authorization.get("taskSessionId") or authorization.get("task_session_id") or ""
    ).strip()
    if not gate or not task_session_id:
        return {"ok": False, "error": "gate_name and taskAuthorization.taskSessionId are required"}

    now = datetime.now(tz=timezone.utc)
    expires = datetime.fromtimestamp(now.timestamp() + max(60, int(ttl_seconds)), tz=timezone.utc)
    record_result: dict[str, Any] = {}

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal record_result
        mismatches = _task_authorization_mismatches(state, authorization)
        if mismatches:
            record_result = {
                "ok": False,
                "error": f"Task authorization mismatch: {', '.join(mismatches)}",
                "errorCode": "TASK_AUTH_MISMATCH",
            }
            return None
        if str(state.get("status") or "") != "running":
            record_result = {
                "ok": False,
                "error": "Task is not running",
                "errorCode": "TASK_NOT_WRITABLE",
            }
            return None
        required = [str(item) for item in state.get("requiredBeforeWrite") or []]
        if gate not in required:
            record_result = {
                "ok": False,
                "error": f"{gate} is not required by this plan",
                "errorCode": "GATE_NOT_REQUIRED",
            }
            return None
        gate_set_hash = str(state.get("requiredGateSetHash") or "")
        record = {
            "gate": gate,
            "status": "completed",
            "completedAt": now.isoformat(),
            "expiresAt": expires.isoformat(),
            "gateSetHash": gate_set_hash,
            "inputHash": _canonical_hash(input_payload),
            "evidenceHash": _canonical_hash(evidence),
            "targetSnapshots": list(target_snapshots or []),
        }
        completed = dict(state.get("completedGates") or {})
        completed[gate] = record
        pending = [item for item in required if item not in completed]
        state["completedGates"] = completed
        state["pendingGates"] = pending
        write_gate = dict(state.get("writeGate") or {})
        write_gate["completedBeforeWrite"] = sorted(completed)
        write_gate["pendingBeforeWrite"] = pending
        state["writeGate"] = write_gate
        state["updatedAt"] = _utc_now()
        _append_log(workspace, task_session_id, f"Completed pre-write gate {gate}")
        record_result = {
            "ok": True,
            "gate": gate,
            "pendingGates": pending,
            "record": record,
            **task_phase_from_state(state),
        }
        return state

    result = _mutate_task_state(workspace, task_session_id, mutate)
    if record_result:
        return record_result
    return result


def _task_authorization_mismatches(
    state: dict[str, Any],
    authorization: dict[str, Any],
) -> list[str]:
    expected_auth = {
        "authToken": str(state.get("authToken") or ""),
        "planId": str(state.get("planId") or ""),
        "planRevision": str(state.get("planRevision") or ""),
        "activeSliceId": str(state.get("activeSliceId") or ""),
    }
    supplied_auth = {
        "authToken": str(authorization.get("authToken") or authorization.get("auth_token") or ""),
        "planId": str(authorization.get("planId") or authorization.get("plan_id") or ""),
        "planRevision": str(
            authorization.get("planRevision") or authorization.get("plan_revision") or ""
        ),
        "activeSliceId": str(
            authorization.get("activeSliceId") or authorization.get("active_slice_id") or ""
        ),
    }
    return [
        key
        for key, expected in expected_auth.items()
        if not supplied_auth[key] or supplied_auth[key] != expected
    ]


def task_set_runtime_session(
    workspace: Path,
    *,
    task_authorization: dict[str, Any],
    runtime_session: dict[str, Any],
) -> dict[str, Any]:
    authorization = task_authorization if isinstance(task_authorization, dict) else {}
    task_session_id = str(
        authorization.get("taskSessionId") or authorization.get("task_session_id") or ""
    ).strip()
    if not task_session_id:
        return {"ok": False, "error": "taskAuthorization.taskSessionId is required"}
    mutation_result: dict[str, Any] = {}

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal mutation_result
        mismatches = _task_authorization_mismatches(state, authorization)
        if mismatches:
            mutation_result = {
                "ok": False,
                "error": f"Task authorization mismatch: {', '.join(mismatches)}",
                "errorCode": "TASK_AUTH_MISMATCH",
            }
            return None
        if str(state.get("status") or "") != "running":
            mutation_result = {
                "ok": False,
                "error": "Task is not running",
                "errorCode": "TASK_NOT_WRITABLE",
            }
            return None
        state["runtimeDebugSession"] = dict(runtime_session)
        state["updatedAt"] = _utc_now()
        _append_log(
            workspace,
            task_session_id,
            f"Runtime debug session {runtime_session.get('sessionId')} -> {runtime_session.get('status')}",
        )
        mutation_result = {
            "ok": True,
            "taskSessionId": task_session_id,
            "runtimeDebugSession": dict(runtime_session),
        }
        return state

    result = _mutate_task_state(workspace, task_session_id, mutate)
    if mutation_result:
        return mutation_result
    return result


def task_status(workspace: Path, task_session_id: str) -> dict[str, Any]:
    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        return _reflect_job_terminal_state(workspace, task_session_id, state)

    try:
        return _mutate_task_state(workspace, task_session_id, mutate)
    except RuntimeError as exc:
        if "task lock busy" not in str(exc):
            raise
        try:
            state = _read_state(workspace, task_session_id)
        except TaskStateReadError as state_exc:
            return _task_state_error(task_session_id, state_exc)
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
    resume_error: dict[str, Any] = {}

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal resume_error
        status = str(state.get("status") or "")
        if status != "cancelled":
            resume_error = {
                "ok": False,
                "error": (
                    "Resume rejected: only a confirmed cancelled task can resume; "
                    "failed, completed, and uncertain cancellations require a new task."
                ),
                "taskSessionId": task_session_id,
            }
            return None
        job = _active_job(workspace, state)
        linked_job_id = str(state.get("activeJobId") or "").strip()
        job_status = str((job or {}).get("status") or "")
        if linked_job_id and not job:
            resume_error = {
                "ok": False,
                "error": "Resume rejected: linked job status is unavailable.",
                "taskSessionId": task_session_id,
                "jobId": linked_job_id,
            }
            return None
        if job_status and job_status not in {"completed", "cancelled"}:
            resume_error = {
                "ok": False,
                "error": f"Resume rejected: linked job status is not resumable ({job_status}).",
                "taskSessionId": task_session_id,
                "jobId": linked_job_id,
            }
            return None

        now = datetime.now(tz=timezone.utc)
        gate_set_hash = str(state.get("requiredGateSetHash") or "")
        required = [str(item) for item in state.get("requiredBeforeWrite") or []]
        current_completed: dict[str, Any] = {}
        for gate, record in dict(state.get("completedGates") or {}).items():
            if (
                gate not in required
                or not isinstance(record, dict)
                or record.get("status") != "completed"
                or str(record.get("gateSetHash") or "") != gate_set_hash
            ):
                continue
            try:
                expiry = datetime.fromisoformat(
                    str(record.get("expiresAt") or "").replace("Z", "+00:00")
                )
            except ValueError:
                continue
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry > now:
                current_completed[gate] = record

        pending = [gate for gate in required if gate not in current_completed]
        state["completedGates"] = current_completed
        state["pendingGates"] = pending
        write_gate = dict(state.get("writeGate") or {})
        write_gate["completedBeforeWrite"] = sorted(current_completed)
        write_gate["pendingBeforeWrite"] = pending
        state["writeGate"] = write_gate
        state["activeJobId"] = ""
        state.pop("terminalLogged", None)
        state["updatedAt"] = _utc_now()
        state["status"] = "running"
        _append_log(workspace, task_session_id, "Task resumed")
        return state

    result = _mutate_task_state(workspace, task_session_id, mutate)
    if resume_error:
        return resume_error
    return result
