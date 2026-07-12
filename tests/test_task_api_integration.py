from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from task_api import task_cancel, task_root, task_start, task_status  # noqa: E402
from wrapper_job_manager import create_job, job_path, launch_job, read_job, write_job  # noqa: E402


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
