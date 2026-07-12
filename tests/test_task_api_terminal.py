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
