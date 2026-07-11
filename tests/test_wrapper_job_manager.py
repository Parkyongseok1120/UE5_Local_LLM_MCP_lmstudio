from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from wrapper_job_manager import (  # noqa: E402
    cancel_job,
    compact_job_status,
    jobs_root,
    start_job,
    transition_job_status,
    write_job,
)


def test_transition_rejects_terminal_overwrite() -> None:
    job = {"jobId": "abc", "status": "completed", "revision": 1}
    assert transition_job_status(job, "running") is False
    assert job["status"] == "completed"


def test_compact_job_status_shape() -> None:
    job = {
        "jobId": "abc",
        "status": "running",
        "revision": 3,
        "progressSequence": 2,
        "progress": [{"seq": 1, "message": "step"}, {"seq": 2, "message": "step2"}],
        "currentAttempt": "attempt_1",
    }
    compact = compact_job_status(job)
    assert compact["userMessage"]
    assert compact["cancellable"] is True
    assert compact["hasNewOutput"] is True
    assert compact["stateRevision"] == 3
    assert compact["progressSequence"] == 2


def test_progress_delta_uses_sequence_cursor() -> None:
    job = {
        "jobId": "abc",
        "status": "running",
        "revision": 5,
        "progressSequence": 3,
        "progress": [
            {"seq": 1, "message": "a"},
            {"seq": 2, "message": "b"},
            {"seq": 3, "message": "c"},
        ],
    }
    delta = compact_job_status(job, since_progress_sequence=1)["progressDelta"]
    assert [entry["message"] for entry in delta] == ["b", "c"]


def test_stale_revision_reject(tmp_path: Path) -> None:
    workspace = tmp_path
    jobs_root(workspace)
    job = {"jobId": "revtest", "status": "running", "revision": 0, "progress": []}
    assert write_job(workspace, job) is True
    job["revision"] = 1
    assert write_job(workspace, job, expected_revision=99) is False


def test_cancel_before_complete(tmp_path: Path) -> None:
    workspace = tmp_path
    jobs_root(workspace)
    job = {
        "jobId": "cancelme",
        "status": "running",
        "revision": 1,
        "progress": [],
    }
    write_job(workspace, job)
    result = cancel_job(workspace, "cancelme")
    assert result["ok"] is True
    assert result["job"]["status"] == "cancelled"


def test_cancel_preserves_cancelled(tmp_path: Path) -> None:
    workspace = tmp_path
    jobs_root(workspace)
    job = {
        "jobId": "deadbeef",
        "status": "running",
        "revision": 1,
        "progress": [],
    }
    write_job(workspace, job)
    result = cancel_job(workspace, "deadbeef")
    assert result["ok"] is True
    assert result["job"]["status"] == "cancelled"
