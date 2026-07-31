from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from task_api import task_cancel, task_record_gate, task_root, task_start, task_status  # noqa: E402
from task_phase import task_phase_from_state  # noqa: E402
from wrapper_job_manager import create_job, job_path, read_job, write_job  # noqa: E402


def _wait_for_job_file(workspace: Path, job_id: str, *, timeout_sec: float = 10.0) -> None:
    path = job_path(workspace, job_id)
    deadline = time.time() + timeout_sec
    last_error = ""
    while time.time() < deadline:
        try:
            if read_job(workspace, job_id) is not None:
                return
        except OSError as exc:
            last_error = str(exc)
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for job record: {path} ({last_error})")


def test_task_start_and_status_phase_fields(tmp_path: Path) -> None:
    started = task_start(tmp_path, request="Fix compile error in Demo.cpp")
    assert started["ok"] is True
    assert started["phase"] == "planning"
    assert "userMessage" in started
    assert started["cancellable"] is True
    task_id = started["taskSessionId"]
    status = task_status(tmp_path, task_id)
    assert status["phase"] == "planning"
    assert (task_root(tmp_path, task_id) / "logs" / "task.log").is_file()


def test_task_cancel_stops_background_job(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    launched: list[str] = []

    def fake_launch_job(workspace, job_id, on_progress=None):
        launched.append(job_id)
        write_job(
            workspace,
            {
                "jobId": job_id,
                "status": "cancelled",
                "revision": 2,
                "progress": [],
                "pid": 999999,
            },
        )
        return {"jobId": job_id, "status": "cancelled"}

    monkeypatch.setattr("wrapper_job_manager._process_alive", lambda _pid: "dead")
    monkeypatch.setattr("wrapper_job_manager.launch_job", fake_launch_job)
    started = task_start(
        tmp_path,
        request="Compile fix loop",
        start_background_job=True,
    )
    task_id = started["taskSessionId"]
    job_id = str(started.get("activeJobId") or started.get("state", {}).get("activeJobId"))
    assert job_id
    assert launched == [job_id]
    _wait_for_job_file(tmp_path, job_id)
    cancelled = task_cancel(tmp_path, task_id)
    assert cancelled["status"] == "cancelled"
    assert cancelled["phase"] == "cancelled"
    job = read_job(tmp_path, job_id)
    assert job is not None
    assert job.get("status") == "cancelled"


def test_task_start_binds_job_before_launch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    order: list[str] = []
    real_create = create_job

    def tracked_create(workspace, arguments):
        order.append("create")
        return real_create(workspace, arguments)

    def tracked_launch(workspace, job_id, on_progress=None):
        order.append("launch")
        return {"jobId": job_id, "status": "queued"}

    monkeypatch.setattr("wrapper_job_manager.create_job", tracked_create)
    monkeypatch.setattr("wrapper_job_manager.launch_job", tracked_launch)
    started = task_start(tmp_path, request="bind order", start_background_job=True)
    assert order == ["create", "launch"]
    assert started.get("activeJobId") or started.get("state", {}).get("activeJobId")


def test_task_state_persisted(tmp_path: Path) -> None:
    started = task_start(tmp_path, request="Read-only plan")
    task_id = started["taskSessionId"]
    state_path = task_root(tmp_path, task_id) / "state.json"
    assert state_path.is_file()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["authToken"]
    status = task_status(tmp_path, task_id)
    assert "authToken" not in status
    assert "authToken" not in status.get("state", {})


def test_corrupt_task_state_fails_closed_without_overwriting_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(tmp_path, request="Preserve corrupt state")
    task_id = started["taskSessionId"]
    state_path = task_root(tmp_path, task_id) / "state.json"
    corrupt_payload = '{"taskSessionId":'
    state_path.write_text(corrupt_payload, encoding="utf-8")

    status = task_status(tmp_path, task_id)

    assert status["ok"] is False
    assert status["errorCode"] == "TASK_STATE_CORRUPT"
    assert "start a new task" in status["recovery"]
    assert state_path.read_text(encoding="utf-8") == corrupt_payload


def test_task_state_identity_mismatch_fails_closed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(tmp_path, request="Validate state identity")
    task_id = started["taskSessionId"]
    state_path = task_root(tmp_path, task_id) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["taskSessionId"] = "different_task_id"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    status = task_status(tmp_path, task_id)

    assert status["ok"] is False
    assert status["errorCode"] == "TASK_STATE_CORRUPT"
    assert "identity mismatch" in status["error"]


def test_task_start_records_background_launch_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))

    def fail_launch(*_args, **_kwargs):
        raise RuntimeError("thread unavailable")

    monkeypatch.setattr("wrapper_job_manager.launch_job", fail_launch)

    result = task_start(
        tmp_path,
        request="Launch background worker",
        start_background_job=True,
    )

    assert result["ok"] is False
    assert result["errorCode"] == "JOB_LAUNCH_FAILED"
    assert result["status"] == "failed"
    task_id = result["taskSessionId"]
    persisted = json.loads(
        (task_root(tmp_path, task_id) / "state.json").read_text(encoding="utf-8")
    )
    assert persisted["status"] == "failed"
    assert persisted["launchError"] == "thread unavailable"
    job = read_job(tmp_path, persisted["activeJobId"])
    assert job is not None
    assert job["status"] == "failed"
    assert job["launchFailed"] is True


def test_task_start_records_background_job_creation_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))

    def fail_create(*_args, **_kwargs):
        raise RuntimeError("job store unavailable")

    monkeypatch.setattr("wrapper_job_manager.create_job", fail_create)

    result = task_start(
        tmp_path,
        request="Create background job",
        start_background_job=True,
    )

    assert result["ok"] is False
    assert result["errorCode"] == "JOB_CREATE_FAILED"
    assert result["status"] == "failed"
    persisted = json.loads(
        (
            task_root(tmp_path, result["taskSessionId"]) / "state.json"
        ).read_text(encoding="utf-8")
    )
    assert persisted["status"] == "failed"
    assert persisted["jobCreateError"] == "job store unavailable"
    assert persisted["activeJobId"] == ""


def test_required_prewrite_gate_is_persisted_and_completed_against_plan(tmp_path: Path) -> None:
    plan = {
        "taskKind": "edit",
        "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
        "orchestration": {
            "requiredBeforeWrite": ["unreal_code_sketch_claim_validate"],
        },
    }
    started = task_start(tmp_path, request="Implement Demo", plan_payload=plan)
    state = started["state"]
    authorization = {
        "taskSessionId": started["taskSessionId"],
        "authToken": started["authToken"],
        "planId": state["planId"],
        "planRevision": state["planRevision"],
        "activeSliceId": state["activeSliceId"],
    }

    assert state["pendingGates"] == ["unreal_code_sketch_claim_validate"]
    assert started["writeReadiness"]["ready"] is False
    assert started["writeReadiness"]["pendingGates"] == ["unreal_code_sketch_claim_validate"]
    assert started["nextAction"] == "unreal_code_sketch_claim_validate"
    completed = task_record_gate(
        tmp_path,
        gate_name="unreal_code_sketch_claim_validate",
        task_authorization=authorization,
        input_payload={"sketch": "demo"},
        evidence={"ok": True},
        target_snapshots=[],
    )

    assert completed["ok"] is True
    assert completed["pendingGates"] == []
    assert completed["writeReadiness"]["ready"] is True
    current = task_status(tmp_path, started["taskSessionId"])["state"]
    record = current["completedGates"]["unreal_code_sketch_claim_validate"]
    assert record["status"] == "completed"
    assert record["gateSetHash"] == current["requiredGateSetHash"]
    assert current["writeGate"]["pendingBeforeWrite"] == []


def test_refactor_task_injects_semantic_guard_when_plan_omits_it(
    tmp_path: Path,
) -> None:
    started = task_start(
        tmp_path,
        request="Meaning-preserving refactor",
        plan_payload={
            "taskKind": "Refactor",
            "writeGate": {"writesAllowed": True},
            "orchestration": {"requiredBeforeWrite": []},
        },
    )

    state = started["state"]
    assert state["requiredBeforeWrite"] == ["unreal_semantic_refactor_guard"]
    assert state["pendingGates"] == ["unreal_semantic_refactor_guard"]
    assert started["writeReadiness"]["ready"] is False


def test_non_refactor_task_does_not_inject_semantic_guard(tmp_path: Path) -> None:
    started = task_start(
        tmp_path,
        request="Small edit",
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True},
            "orchestration": {"requiredBeforeWrite": []},
        },
    )

    assert "unreal_semantic_refactor_guard" not in started["state"][
        "requiredBeforeWrite"
    ]


def test_required_prewrite_gate_rejects_mismatched_authorization(tmp_path: Path) -> None:
    plan = {
        "taskKind": "edit",
        "writeGate": {"writesAllowed": True},
        "orchestration": {"requiredBeforeWrite": ["unreal_code_sketch_claim_validate"]},
    }
    started = task_start(tmp_path, request="Implement Demo", plan_payload=plan)
    state = started["state"]
    result = task_record_gate(
        tmp_path,
        gate_name="unreal_code_sketch_claim_validate",
        task_authorization={
            "taskSessionId": started["taskSessionId"],
            "authToken": "wrong",
            "planId": state["planId"],
            "planRevision": state["planRevision"],
            "activeSliceId": state["activeSliceId"],
        },
        input_payload={},
        evidence={"ok": True},
    )

    assert result["ok"] is False
    assert result["errorCode"] == "TASK_AUTH_MISMATCH"
    assert task_status(tmp_path, started["taskSessionId"])["state"]["completedGates"] == {}


def test_write_readiness_rejects_expired_or_stale_gate_records() -> None:
    required = ["unreal_code_sketch_claim_validate"]
    base = {
        "status": "running",
        "writesAllowed": True,
        "requiredBeforeWrite": required,
        "requiredGateSetHash": "current-plan",
        "completedGates": {
            required[0]: {
                "status": "completed",
                "gateSetHash": "current-plan",
                "expiresAt": (datetime.now(tz=timezone.utc) - timedelta(seconds=1)).isoformat(),
            }
        },
    }

    expired = task_phase_from_state(base)
    assert expired["writeReadiness"]["ready"] is False
    assert expired["writeReadiness"]["pendingGates"] == required
    assert expired["writeReadiness"]["gateIssues"] == [
        {"gate": required[0], "reason": "expired"}
    ]
    assert expired["nextAction"] == required[0]

    base["completedGates"][required[0]]["expiresAt"] = (
        datetime.now(tz=timezone.utc) + timedelta(minutes=5)
    ).isoformat()
    base["completedGates"][required[0]]["gateSetHash"] = "old-plan"
    stale = task_phase_from_state(base)
    assert stale["writeReadiness"]["ready"] is False
    assert stale["writeReadiness"]["gateIssues"] == [
        {"gate": required[0], "reason": "stale_plan"}
    ]


def test_terminal_and_approval_states_never_report_write_ready() -> None:
    for status in ("completed", "cancelled", "failed", "cancellation_uncertain"):
        payload = task_phase_from_state(
            {
                "status": status,
                "writesAllowed": True,
                "requiredBeforeWrite": [],
                "completedGates": {},
            }
        )
        assert payload["writeReadiness"]["ready"] is False
        if status == "cancelled":
            assert payload["nextAction"] == "unreal_task_resume"
        elif status != "completed":
            assert payload["nextAction"] == "start_new_unreal_agent_plan"

    approval = task_phase_from_state(
        {
            "status": "pending_approval",
            "writesAllowed": True,
            "requiredBeforeWrite": [],
            "completedGates": {},
        }
    )
    assert approval["phase"] == "awaiting_approval"
    assert approval["writeReadiness"]["ready"] is False
    assert approval["nextAction"] == "unreal_task_approve"


def test_active_background_job_temporarily_closes_write_readiness() -> None:
    payload = task_phase_from_state(
        {
            "status": "running",
            "writesAllowed": True,
            "requiredBeforeWrite": [],
            "completedGates": {},
        },
        {"status": "running", "jobId": "job-1"},
    )

    assert payload["writeReadiness"]["ready"] is False
    assert "job_in_progress:running" in payload["writeReadiness"]["blockedReasons"]
    assert payload["nextAction"] == "unreal_task_status"
