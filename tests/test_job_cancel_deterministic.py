from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from job_store import read_job_record, validate_job_id, write_job_record  # noqa: E402
from state_root import ensure_state_root_layout  # noqa: E402
from wrapper_job_manager import cancel_job, create_job, launch_job, read_job, start_job  # noqa: E402


class _FakePopen:
    def __init__(self, *args, **kwargs):
        self.pid = 4242
        self.args = args
        self.kwargs = kwargs

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0


@pytest.fixture()
def isolated_state(tmp_path: Path, monkeypatch):
    state_root = tmp_path / "state"
    monkeypatch.setenv("AGENT_STATE_ROOT", str(state_root))
    ensure_state_root_layout(state_root)
    return tmp_path


def test_job_store_revision_conflict(isolated_state: Path) -> None:
    job_id = uuid.uuid4().hex[:12]
    validate_job_id(job_id)
    payload = {"jobId": job_id, "status": "queued", "revision": 0, "progressSequence": 0}
    assert write_job_record(payload, workspace=isolated_state)
    stale = dict(payload)
    stale["status"] = "running"
    current = read_job_record(job_id, workspace=isolated_state)
    assert current is not None
    assert write_job_record(stale, expected_revision=int(current["revision"]), workspace=isolated_state) is True
    assert write_job_record(stale, expected_revision=0, workspace=isolated_state) is False
    current = read_job_record(job_id, workspace=isolated_state)
    assert current is not None
    assert int(current["revision"]) == 2


def test_cancel_blocks_stale_running_merge(isolated_state: Path) -> None:
    job_id = uuid.uuid4().hex[:12]
    write_job_record(
        {
            "jobId": job_id,
            "status": "cancel_requested",
            "revision": 2,
            "progressSequence": 1,
            "progress": [],
        },
        workspace=isolated_state,
    )
    stale_running = {
        "jobId": job_id,
        "status": "running",
        "revision": 1,
        "pid": 1234,
        "progressSequence": 0,
    }
    from wrapper_job_manager import save_job  # noqa: E402

    assert save_job(isolated_state, stale_running) is False
    persisted = read_job_record(job_id, workspace=isolated_state)
    assert persisted is not None
    assert persisted["status"] == "cancel_requested"


def test_cancel_job_skips_kill_when_pid_identity_mismatch(monkeypatch, isolated_state: Path) -> None:
    job_id = uuid.uuid4().hex[:12]
    command = [sys.executable, "rag_refresh.py"]
    from process_identity import command_fingerprint  # noqa: E402

    job = {
        "jobId": job_id,
        "status": "running",
        "revision": 1,
        "progressSequence": 0,
        "pid": 4242,
        "command": command,
        "commandFingerprint": command_fingerprint(command),
        "pidStartedAt": "2026-01-01T00:00:00+00:00",
        "progress": [],
    }
    write_job_record(job, workspace=isolated_state)
    monkeypatch.setattr("wrapper_job_manager._pid_matches_job", lambda _job: False)
    monkeypatch.setattr("wrapper_job_manager._process_alive", lambda _pid: "alive")
    result = cancel_job(isolated_state, job_id)
    assert result["ok"] is True
    assert result["orphanProcessSuspected"] is True
    assert result["cancellationState"] == "cancellation_uncertain"
    persisted = read_job(isolated_state, job_id)
    assert persisted is not None
    assert persisted["status"] == "cancellation_uncertain"
    assert persisted.get("orphanProcessSuspected") is True


def test_cancel_job_cancelled_when_pid_dead_and_identity_mismatch(monkeypatch, isolated_state: Path) -> None:
    job_id = uuid.uuid4().hex[:12]
    job = {
        "jobId": job_id,
        "status": "running",
        "revision": 1,
        "progressSequence": 0,
        "pid": 4242,
        "progress": [],
    }
    write_job_record(job, workspace=isolated_state)
    monkeypatch.setattr("wrapper_job_manager._pid_matches_job", lambda _job: False)
    monkeypatch.setattr("wrapper_job_manager._process_alive", lambda _pid: "dead")
    result = cancel_job(isolated_state, job_id)
    assert result["ok"] is True
    assert result["orphanProcessSuspected"] is False
    persisted = read_job(isolated_state, job_id)
    assert persisted is not None
    assert persisted["status"] == "cancelled"


def test_cancel_job_persists_cancel_requested(monkeypatch, isolated_state: Path) -> None:
    def _fake_run(*args, **kwargs):
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("wrapper_job_manager.subprocess.run", _fake_run)
    monkeypatch.setattr("process_identity.subprocess.run", _fake_run)
    job_id = uuid.uuid4().hex[:12]
    job = {
        "jobId": job_id,
        "status": "running",
        "revision": 1,
        "progressSequence": 0,
        "pid": 999999,
        "progress": [],
    }
    write_job_record(job, workspace=isolated_state)
    result = cancel_job(isolated_state, job_id)
    assert result["ok"] is True
    persisted = read_job(isolated_state, job_id)
    assert persisted is not None
    assert persisted["status"] in {"cancelled", "cancellation_uncertain", "cancel_requested"}


def test_spawn_persist_failure_kills_tree_and_marks_failed(monkeypatch, isolated_state: Path) -> None:
    job = create_job(isolated_state, {"request": "spawn-fail-test"})
    job_id = str(job["jobId"])
    kill_calls: list[int] = []
    monkeypatch.setattr("wrapper_job_manager._kill_process_tree", lambda pid: kill_calls.append(pid) or True)
    monkeypatch.setattr("wrapper_job_manager._confirm_process_dead", lambda _pid: "dead")
    monkeypatch.setattr("wrapper_job_manager.subprocess.Popen", _FakePopen)

    original = __import__("job_store").transition_job_record

    def fake_transition(job_id_arg, status, mutator, workspace=None):
        if status == "running":
            return False
        return original(job_id_arg, status, mutator, workspace=workspace)

    monkeypatch.setattr("job_store.transition_job_record", fake_transition)
    launch_job(isolated_state, job_id)
    deadline = time.time() + 5
    persisted = read_job(isolated_state, job_id)
    while time.time() < deadline:
        persisted = read_job(isolated_state, job_id)
        if persisted and persisted.get("status") in {"failed", "cancellation_uncertain"}:
            break
        time.sleep(0.05)
    assert persisted is not None
    assert persisted["status"] == "failed"
    assert persisted.get("spawnPersistFailed") is True
    assert kill_calls


def test_reconcile_stale_starting_job_without_pid(isolated_state: Path) -> None:
    from reconcile_jobs import reconcile_stale_jobs

    job_id = uuid.uuid4().hex[:12]
    write_job_record(
        {
            "jobId": job_id,
            "status": "starting",
            "revision": 1,
            "progressSequence": 0,
            "progress": [],
        },
        workspace=isolated_state,
    )
    summary = reconcile_stale_jobs(isolated_state)
    assert summary["terminalized"] >= 1
    persisted = read_job(isolated_state, job_id)
    assert persisted is not None
    assert persisted["status"] == "failed"
    assert persisted.get("reconciledStaleStarting") is True
