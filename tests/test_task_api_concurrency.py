from __future__ import annotations

import json
import sys
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from task_api import task_cancel, task_root, task_start, task_status  # noqa: E402
from wrapper_job_manager import write_job  # noqa: E402


def test_alive_pid_lock_not_stale_by_age(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    from write_locks import _is_stale_lock, lock_file_path

    target = tmp_path / "sample.txt"
    target.write_text("x", encoding="utf-8")
    lock_path = lock_file_path(target)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(f"{__import__('os').getpid()}:abc\nwrite\n", encoding="utf-8")
    old = lock_path.stat().st_mtime
    lock_path.touch()
    import os

    os.utime(lock_path, (old - 600, old - 600))
    monkeypatch.setattr("write_locks._process_alive", lambda _pid: "alive")
    assert _is_stale_lock(lock_path) is False


def test_probe_unknown_lock_not_stale(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    from write_locks import _is_stale_lock, lock_file_path

    target = tmp_path / "sample.txt"
    target.write_text("x", encoding="utf-8")
    lock_path = lock_file_path(target)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("424242:abc\nwrite\n", encoding="utf-8")
    monkeypatch.setattr("write_locks._process_alive", lambda _pid: "unknown")
    assert _is_stale_lock(lock_path) is False


def test_concurrent_cancel_and_status_preserves_terminal_state(tmp_path: Path, monkeypatch) -> None:
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

    cancel_started = threading.Event()
    cancel_done = threading.Event()

    def slow_cancel(*_args, **_kwargs):
        cancel_started.set()
        time.sleep(0.2)
        cancel_done.set()
        return {
            "ok": True,
            "cancellationState": "cancellation_uncertain",
            "orphanProcessSuspected": True,
        }

    results: list[dict] = []

    def run_cancel() -> None:
        with patch("wrapper_job_manager.cancel_job", side_effect=slow_cancel):
            results.append(task_cancel(workspace, task_id))

    def run_status() -> None:
        cancel_started.wait(timeout=2)
        time.sleep(0.05)
        results.append(task_status(workspace, task_id))

    t_cancel = threading.Thread(target=run_cancel)
    t_status = threading.Thread(target=run_status)
    t_cancel.start()
    t_status.start()
    t_cancel.join(timeout=5)
    t_status.join(timeout=5)
    cancel_done.wait(timeout=2)

    final = json.loads(state_path.read_text(encoding="utf-8"))
    assert final["status"] == "cancellation_uncertain"
