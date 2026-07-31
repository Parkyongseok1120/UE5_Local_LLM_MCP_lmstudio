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
    # queued + never spawned → PID-less cancel is allowed
    assert read_job(workspace, job_id)["status"] == "queued"

    payload = task_quarantine_corrupt(workspace)
    assert payload["ok"] is True
    assert payload["routeReleased"] is True
    assert not task_dir.exists()
    quarantined = list((state_root / "quarantine" / "tasks").glob(f"{task_id}-*"))
    assert len(quarantined) == 1
    final_job = read_job(workspace, job_id)
    assert final_job is not None
    assert final_job["status"] == "cancelled"
    assert final_job.get("processTerminationConfirmed") is True


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


def test_quarantine_refuses_existing_uncertain_terminal_job(
    tmp_path: Path, monkeypatch
) -> None:
    state_root = tmp_path / "state"
    monkeypatch.setenv("AGENT_STATE_ROOT", str(state_root))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    task_id = "corrupt03_task_cccc"
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
    from job_store import transition_job_record

    job_id = str(job["jobId"])
    assert transition_job_record(
        job_id, "cancel_requested", lambda draft: None, workspace=workspace
    )
    assert transition_job_record(
        job_id,
        "cancellation_uncertain",
        lambda draft: draft.update(
            {"orphanProcessSuspected": True, "pid": 424242, "pgid": 424242}
        ),
        workspace=workspace,
    )
    assert read_job(workspace, job_id)["status"] == "cancellation_uncertain"

    monkeypatch.setattr(
        "wrapper_job_manager._process_alive",
        lambda _pid: "alive",
    )
    monkeypatch.setattr(
        "wrapper_job_manager._pid_matches_job",
        lambda _job: True,
    )
    monkeypatch.setattr(
        "wrapper_job_manager._kill_process_tree",
        lambda _pid: False,
    )

    payload = task_quarantine_corrupt(workspace)
    assert payload["ok"] is False
    assert payload["errorCode"] == "TASK_CANCELLATION_UNCERTAIN"
    assert payload.get("routeReleased") is False
    assert task_dir.is_dir()


def test_quarantine_refuses_when_job_discovery_fails(
    tmp_path: Path, monkeypatch
) -> None:
    state_root = tmp_path / "state"
    monkeypatch.setenv("AGENT_STATE_ROOT", str(state_root))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    task_id = "corrupt04_task_dddd"
    task_dir = state_root / "tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "workspace-root.txt").write_text(str(workspace.resolve()), encoding="utf-8")
    (task_dir / "state.json").write_text("{", encoding="utf-8")

    def boom(*_args, **_kwargs):
        raise RuntimeError("jobs.sqlite unavailable")

    monkeypatch.setattr("job_store.find_jobs_by_task_session_id", boom)
    payload = task_quarantine_corrupt(workspace)
    assert payload["ok"] is False
    assert payload["errorCode"] == "TASK_JOB_DISCOVERY_UNCERTAIN"
    assert payload.get("routeReleased") is False
    assert task_dir.is_dir()


def test_quarantine_refuses_when_active_job_record_missing(
    tmp_path: Path, monkeypatch
) -> None:
    state_root = tmp_path / "state"
    monkeypatch.setenv("AGENT_STATE_ROOT", str(state_root))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    task_id = "corrupt05_task_eeee"
    task_dir = state_root / "tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "workspace-root.txt").write_text(str(workspace.resolve()), encoding="utf-8")
    (task_dir / "state.json").write_text(
        '{"activeJobId":"deadbeefcafe","status":',
        encoding="utf-8",
    )
    payload = task_quarantine_corrupt(workspace)
    assert payload["ok"] is False
    assert payload["errorCode"] == "TASK_ACTIVE_JOB_RECORD_MISSING"
    assert payload.get("routeReleased") is False
    assert payload.get("orphanProcessSuspected") is True
    assert task_dir.is_dir()


def test_quarantine_refuses_cancelled_without_termination_proof(
    tmp_path: Path, monkeypatch
) -> None:
    state_root = tmp_path / "state"
    monkeypatch.setenv("AGENT_STATE_ROOT", str(state_root))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    task_id = "corrupt06_task_ffff"
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
    from job_store import transition_job_record

    job_id = str(job["jobId"])
    assert transition_job_record(
        job_id,
        "starting",
        lambda draft: draft.update({"subprocessSpawned": True, "pidStartedAt": "now"}),
        workspace=workspace,
    )
    assert transition_job_record(
        job_id,
        "cancel_requested",
        lambda draft: None,
        workspace=workspace,
    )
    assert transition_job_record(
        job_id,
        "cancelled",
        lambda draft: draft.update(
            {
                "subprocessSpawned": True,
                "pid": 424242,
                "pidStartedAt": "now",
                "processTerminationConfirmed": False,
            }
        ),
        workspace=workspace,
    )
    payload = task_quarantine_corrupt(workspace)
    assert payload["ok"] is False
    assert payload["errorCode"] == "TASK_CANCELLATION_UNCERTAIN"
    assert payload.get("routeReleased") is False
    assert task_dir.is_dir()
