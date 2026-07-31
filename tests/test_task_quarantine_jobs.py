from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from task_api import task_quarantine_corrupt  # noqa: E402
from wrapper_job_manager import create_job, read_job, write_job  # noqa: E402


def test_quarantine_cancels_linked_jobs_before_move(tmp_path: Path, monkeypatch) -> None:
    state_root = tmp_path / "state"
    monkeypatch.setenv("AGENT_STATE_ROOT", str(state_root))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    task_id = "corrupt01_task_aaaa"
    task_dir = state_root / "tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "workspace-root.txt").write_text(str(workspace.resolve()), encoding="utf-8")
    (task_dir / "state.json").write_text("{", encoding="utf-8")

    job = create_job(
        workspace,
        {
            "request": "edit files",
            "mode": "agent_edit",
            "taskSessionId": task_id,
        },
    )
    job_id = str(job["jobId"])
    job["status"] = "running"
    job["pid"] = None
    write_job(workspace, job)

    payload = task_quarantine_corrupt(workspace)
    assert payload["ok"] is True
    assert payload["routeReleased"] is True
    assert not task_dir.exists()
    quarantined = list((state_root / "quarantine" / "tasks").glob(f"{task_id}-*"))
    assert len(quarantined) == 1
    final_job = read_job(workspace, job_id)
    assert final_job is not None
    assert final_job["status"] in {"cancelled", "cancellation_uncertain"}
    assert final_job["status"] == "cancelled"


def test_quarantine_refuses_when_job_cancel_uncertain(tmp_path: Path, monkeypatch) -> None:
    state_root = tmp_path / "state"
    monkeypatch.setenv("AGENT_STATE_ROOT", str(state_root))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    task_id = "corrupt02_task_bbbb"
    task_dir = state_root / "tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "workspace-root.txt").write_text(str(workspace.resolve()), encoding="utf-8")
    (task_dir / "state.json").write_text("{", encoding="utf-8")

    job = create_job(
        workspace,
        {
            "request": "edit files",
            "mode": "agent_edit",
            "taskSessionId": task_id,
        },
    )
    job_id = str(job["jobId"])

    def fake_cancel(workspace_arg, job_arg):
        assert job_arg == job_id
        return {
            "ok": True,
            "cancellationState": "cancellation_uncertain",
            "orphanProcessSuspected": True,
            "job": {"jobId": job_id, "status": "cancellation_uncertain"},
        }

    monkeypatch.setattr("wrapper_job_manager.cancel_job", fake_cancel)
    payload = task_quarantine_corrupt(workspace)
    assert payload["ok"] is False
    assert payload["errorCode"] == "TASK_CANCELLATION_UNCERTAIN"
    assert payload.get("routeReleased") is False
    assert payload.get("orphanProcessSuspected") is True
    assert task_dir.is_dir()
    assert (task_dir / "state.json").read_text(encoding="utf-8") == "{"
