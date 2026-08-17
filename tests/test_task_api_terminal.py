from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from task_api import (  # noqa: E402
    task_approve,
    task_cancel,
    task_root,
    task_resume,
    task_start,
    task_status,
)
from wrapper_job_manager import write_job  # noqa: E402


def test_task_approve_rejects_terminal_task(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    workspace = tmp_path
    started = task_start(workspace, request="demo", start_background_job=False)
    task_id = str(started["taskSessionId"])
    state_path = task_root(workspace, task_id) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "completed"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = task_approve(workspace, task_id, note="too late")
    assert result["ok"] is False


def test_task_cancel_preserves_cancellation_uncertain(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    workspace = tmp_path
    started = task_start(workspace, request="demo", start_background_job=False)
    task_id = str(started["taskSessionId"])
    job_id = uuid.uuid4().hex[:12]
    write_job(workspace, {"jobId": job_id, "status": "running", "revision": 1, "progress": []})
    state_path = task_root(workspace, task_id) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["activeJobId"] = job_id
    state_path.write_text(json.dumps(state), encoding="utf-8")

    with patch("wrapper_job_manager.cancel_job") as cancel_job:
        cancel_job.return_value = {
            "ok": True,
            "cancellationState": "cancellation_uncertain",
            "orphanProcessSuspected": True,
            "processTreeKilled": False,
        }
        result = task_cancel(workspace, task_id)

    assert result["ok"] is True
    assert result["status"] == "cancellation_uncertain"
    assert result["cancellationState"] == "cancellation_uncertain"
    assert result["orphanProcessSuspected"] is True
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "cancellation_uncertain"
    assert persisted.get("orphanProcessSuspected") is True


def test_task_status_maps_cancellation_uncertain_not_failed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    workspace = tmp_path
    started = task_start(workspace, request="demo", start_background_job=False)
    task_id = str(started["taskSessionId"])
    job_id = uuid.uuid4().hex[:12]
    write_job(
        workspace,
        {
            "jobId": job_id,
            "status": "cancellation_uncertain",
            "revision": 1,
            "progress": [],
            "orphanProcessSuspected": True,
        },
    )
    state_path = task_root(workspace, task_id) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["activeJobId"] = job_id
    state["status"] = "running"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = task_status(workspace, task_id)
    assert result["ok"] is True
    assert result["status"] == "cancellation_uncertain"
    assert result["state"]["status"] == "cancellation_uncertain"


def test_task_resume_restores_confirmed_cancel_and_discards_expired_gates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    plan = {
        "taskKind": "edit",
        "writeGate": {"writesAllowed": True},
        "orchestration": {
            "requiredBeforeWrite": ["unreal_code_sketch_claim_validate"]
        },
    }
    started = task_start(tmp_path, request="demo", plan_payload=plan)
    task_id = str(started["taskSessionId"])
    state_path = task_root(tmp_path, task_id) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["completedGates"] = {
        "unreal_code_sketch_claim_validate": {
            "status": "completed",
            "gateSetHash": state["requiredGateSetHash"],
            "expiresAt": "2000-01-01T00:00:00+00:00",
        }
    }
    state["pendingGates"] = []
    state_path.write_text(json.dumps(state), encoding="utf-8")

    cancelled = task_cancel(tmp_path, task_id)
    resumed = task_resume(tmp_path, task_id)

    assert cancelled["status"] == "cancelled"
    assert cancelled["taskRouteTerminal"] is True
    assert cancelled["toolRoute"] == {}
    assert cancelled["routeAuthorization"] == {"routeHash": "", "routePhase": ""}
    assert "toolRoute" not in cancelled["state"]
    assert cancelled["resumeAction"] == "unreal_task_resume"
    assert "nextAction" not in cancelled
    assert resumed["ok"] is True
    assert resumed["status"] == "running"
    assert resumed["state"]["completedGates"] == {}
    assert resumed["state"]["pendingGates"] == [
        "unreal_code_sketch_claim_validate"
    ]
    assert resumed["writeReadiness"]["ready"] is False
    assert resumed["nextAction"] == "unreal_code_sketch_claim_validate"


def test_task_resume_rejects_unconfirmed_or_completed_task(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(tmp_path, request="demo")
    task_id = str(started["taskSessionId"])
    state_path = task_root(tmp_path, task_id) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))

    state["status"] = "cancellation_uncertain"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    uncertain = task_resume(tmp_path, task_id)
    assert uncertain["ok"] is False

    state["status"] = "completed"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    completed = task_resume(tmp_path, task_id)
    assert completed["ok"] is False


def test_task_resume_consumes_structured_user_input_contract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(tmp_path, request="inspect selected source")
    task_id = str(started["taskSessionId"])
    state_path = task_root(tmp_path, task_id) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    resume_token = "a" * 64
    required_input = {
        "kind": "provide_path",
        "prompt": "Provide the source root to inspect.",
        "schema": {
            "type": "object",
            "required": ["path"],
            "properties": {"path": {"type": "string"}},
        },
        "resumeToken": resume_token,
    }
    state["controlState"] = {
        "version": 2,
        "disposition": "await_user",
        "requiredUserInput": required_input,
    }
    state["requiredUserInput"] = required_input
    state_path.write_text(json.dumps(state), encoding="utf-8")
    authorization = {
        "taskSessionId": task_id,
        "ownerCapability": state["ownerCapability"],
    }

    stale = task_resume(
        tmp_path,
        task_id,
        task_authorization=authorization,
        user_response={"path": "Source/Demo"},
        resume_token="stale-token",
    )
    assert stale["ok"] is False
    assert stale["errorCode"] == "TASK_RESUME_TOKEN_MISMATCH"

    resumed = task_resume(
        tmp_path,
        task_id,
        task_authorization=authorization,
        user_response={"path": "Source/Demo"},
        resume_token=resume_token,
    )
    assert resumed["ok"] is True
    assert resumed["control"]["requiredTool"]["name"] == "unreal_agent_plan"
    assert resumed["control"]["allowedTools"] == ["unreal_agent_plan"]
    assert resumed["state"]["userInputHistory"][-1]["kind"] == "provide_path"
    assert "requiredUserInput" not in resumed["state"]


def test_plan_only_rejects_start_background_job(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    result = task_start(
        tmp_path,
        request="plan only",
        mode="plan_only",
        start_background_job=True,
    )
    assert result["ok"] is False
    assert result["errorCode"] == "INVALID_ARGUMENT"
