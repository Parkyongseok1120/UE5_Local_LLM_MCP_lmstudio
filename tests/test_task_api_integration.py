from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from task_api import task_cancel, task_start, task_status  # noqa: E402
from wrapper_job_manager import job_path, read_job  # noqa: E402


def _wait_for_job_file(workspace: Path, job_id: str, *, timeout_sec: float = 5.0) -> None:
    path = job_path(workspace, job_id)
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if path.is_file() and read_job(workspace, job_id) is not None:
            return
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for job file: {path}")


def test_task_start_and_status_phase_fields(tmp_path: Path) -> None:
    started = task_start(tmp_path, request="Fix compile error in Demo.cpp")
    assert started["ok"] is True
    assert started["phase"] == "planning"
    assert "userMessage" in started
    assert started["cancellable"] is True
    task_id = started["taskSessionId"]
    status = task_status(tmp_path, task_id)
    assert status["phase"] == "planning"
    assert (tmp_path / ".agent" / "tasks" / task_id / "logs" / "task.log").is_file()


def test_task_cancel_stops_background_job(tmp_path: Path) -> None:
    started = task_start(
        tmp_path,
        request="Compile fix loop",
        start_background_job=True,
    )
    task_id = started["taskSessionId"]
    job_id = started.get("activeJobId") or started.get("state", {}).get("activeJobId")
    assert job_id
    _wait_for_job_file(tmp_path, job_id)
    cancelled = task_cancel(tmp_path, task_id)
    assert cancelled["status"] == "cancelled"
    assert cancelled["phase"] == "cancelled"
    job = read_job(tmp_path, job_id)
    assert job is not None
    assert job.get("status") == "cancelled"


def test_task_state_persisted(tmp_path: Path) -> None:
    started = task_start(tmp_path, request="Read-only plan")
    task_id = started["taskSessionId"]
    state_path = tmp_path / ".agent" / "tasks" / task_id / "state.json"
    assert state_path.is_file()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["authToken"]
