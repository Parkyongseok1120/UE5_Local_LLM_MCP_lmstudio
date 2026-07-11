from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from job_store import read_job_record, validate_job_id, write_job_record  # noqa: E402
from state_root import ensure_state_root_layout  # noqa: E402
from wrapper_job_manager import cancel_job, read_job, start_job  # noqa: E402


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


def test_cancel_job_persists_cancel_requested(monkeypatch, isolated_state: Path) -> None:
    monkeypatch.setattr("wrapper_job_manager.subprocess.run", lambda *a, **k: type("R", (), {"returncode": 0})())
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
